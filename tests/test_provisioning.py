from copy import deepcopy

from pirates.game import World
from pirates.economy import MERCHANTS
from pirates.management import ship_rows
from pirates.provisioning import (CLEAN_WATER_L_PER_CREW_DAY,
                                  FOOD_KG_PER_CREW_DAY, forecast_provisions,
                                  readiness_rows)


def quiet_world():
    world = World()
    world.voyages.roles = {role: None for role in world.voyages.roles}
    return world


def test_forecast_budgets_match_the_measured_crew_day_rates():
    assert FOOD_KG_PER_CREW_DAY == 0.85
    assert CLEAN_WATER_L_PER_CREW_DAY == 2.2


def test_forecast_separates_ready_food_and_water_limited_cooking_without_mutation():
    world = quiet_world()
    ship = world.ship
    stores_before = dict(ship.stores)
    batches_before = deepcopy(ship.cooked_food_batches)
    forecast = forecast_provisions(world, target_days=5)
    assert forecast["ready_food_g"] == ship.edible_food_g()
    assert forecast["total_ready_food_g"] == ship.ready_food_g()
    assert forecast["projected_food_g"] >= forecast["ready_food_g"]
    assert forecast["water_safety_reserve_ml"] == len(ship.crew) * 2200
    assert forecast["projected_food_days"] >= forecast["ready_food_days"]
    assert dict(ship.stores) == stores_before
    assert ship.cooked_food_batches == batches_before


def test_dry_ingredients_do_not_hide_unavailable_cooking_or_water_shortage():
    world = quiet_world()
    ship = world.ship
    for sailor in ship.crew:
        sailor.labors["cook"] = 0
    ship.stores["clean_water_ml"] = 0
    forecast = forecast_provisions(world)
    assert not forecast["can_cook"]
    assert forecast["projected_food_g"] == forecast["ready_food_g"]
    assert any("no crew member is assigned cook labor" in reason for reason in forecast["blockers"])
    assert any("drinking reserve" in reason for reason in forecast["blockers"])


def test_blocked_galley_access_is_not_counted_as_available_cooking():
    world = quiet_world()
    ship = world.ship
    ship.stores["grain_g"] = 2000
    world.locked_doors = set(world.ship_model.navigation_geometry()["door"])
    stores_before = dict(ship.stores)

    forecast = forecast_provisions(world)

    assert not forecast["can_cook"]
    assert forecast["projected_food_g"] == forecast["ready_food_g"]
    assert any("cannot reach the galley" in reason for reason in forecast["blockers"])
    assert dict(ship.stores) == stores_before


def test_forecast_does_not_count_water_below_safety_reserve_as_recipe_water():
    world = quiet_world()
    ship = world.ship
    one_day_drinking = len(ship.crew) * 2200
    ship.stores["clean_water_ml"] = one_day_drinking
    forecast = forecast_provisions(world)
    assert forecast["water_available_for_cooking_ml"] == 0
    assert forecast["projected_food_g"] == forecast["ready_food_g"]


def test_galley_policy_pauses_preferences_reserves_and_saves(tmp_path):
    world = quiet_world()
    ship = world.ship
    for food in ("fruit_g", "dried_fish_g", "biscuit_g"):
        ship.stores[food] = 0
    ship.prepared_food_g = 0
    ship.set_galley_policy(mode="paused")
    ship.generate_jobs(world)
    assert not any(job.kind == "cook" and job.state not in ("done", "cancelled") for job in ship.jobs)

    ship.set_galley_policy(mode="preferred", preferred_recipe="bean_stew", ready_food_target_g=20000)
    ship.generate_jobs(world)
    job = next(job for job in ship.jobs if job.kind == "cook")
    assert job.key == "cook:bean_stew"
    ship.generate_jobs(world)
    assert sum(job.kind == "cook" and job.state not in ("done", "cancelled") for job in ship.jobs) == 1

    path = tmp_path / "galley-policy.json"
    world.save(path)
    resumed = World.load(path)
    assert resumed.ship.galley_policy == ship.galley_policy


def test_protected_biscuits_need_emergency_override_to_eat_normally():
    world = quiet_world()
    ship = world.ship
    for food in ("fruit_g", "dried_fish_g"):
        ship.stores[food] = 0
    ship.prepared_food_g = 0
    ship.stores["biscuit_g"] = 1000
    assert ship.edible_food_g() == 0
    assert ship.edible_food_g(emergency=True) == 1000
    assert not ship.consume_food(250)
    assert ship.consume_food(250, emergency=True)
    assert ship.stores["biscuit_g"] == 750


def test_readiness_separates_potable_questionable_and_vessel_water():
    world = quiet_world()
    ship = world.ship
    ship.stores["clean_water_ml"] = 5000
    ship.stores["questionable_water_ml"] = 2400
    vessel = ship.add_container("water_cask", 10000, 1200)
    ship.fill_container(vessel["id"], 5000)

    rows = readiness_rows(world)

    assert ship.stores["clean_water_ml"] == 0
    assert any("Water: 5.0 L potable" in row and "5.0 L in vessels" in row for row in rows)
    assert any("Questionable water: 2.40 L" in row for row in rows)
    assert any("treat questionable water" in row for row in rows)
    assert forecast_provisions(world)["water_ml"] == 5000


def test_readiness_is_read_only_and_time_frozen():
    world = quiet_world()
    world.economy.cargo = {"biscuit": 2}
    before = (dict(world.ship.stores), world.ship.seconds, world.clock.day,
              deepcopy(world.ship.galley_policy), deepcopy(world.ship.cooked_food_batches),
              deepcopy(world.ship.fresh_food_lots), deepcopy(world.ship.containers),
              world.economy.money, deepcopy(world.economy.cargo),
              deepcopy(world.economy.gear_condition))

    rows = readiness_rows(world, target_days=1.5)

    after = (dict(world.ship.stores), world.ship.seconds, world.clock.day,
             deepcopy(world.ship.galley_policy), deepcopy(world.ship.cooked_food_batches),
             deepcopy(world.ship.fresh_food_lots), deepcopy(world.ship.containers),
             world.economy.money, deepcopy(world.economy.cargo),
             deepcopy(world.economy.gear_condition))
    assert rows
    assert before == after


def test_readiness_names_galley_blocker_and_remedy():
    world = quiet_world()
    world.locked_doors = set(world.ship_model.navigation_geometry()["door"])

    rows = readiness_rows(world)

    assert any("cannot reach the galley" in row for row in rows)
    assert any(row.startswith("Next:") and "galley" in row for row in rows)


def test_readiness_names_paused_galley_remedy():
    world = quiet_world()
    world.ship.set_galley_policy(mode="paused")

    rows = readiness_rows(world)

    assert any("galley automation is paused" in row for row in rows)
    assert any("unpause the galley" in row for row in rows)


def test_readiness_adds_physical_crew_medical_ammunition_and_work_findings():
    world = quiet_world()
    ship = world.ship
    ship.hull = 40
    ship.rigging = 30
    for sailor in ship.crew:
        sailor.labors = {kind: 0 for kind in sailor.labors}
    ship.stores["medicine"] = 0
    ship.stores["bandages"] = 0
    ship.stores["shot"] = 0
    ship.ammo = {key: False for key in ship.ammo}
    world.locked_doors = set(world.ship_model.navigation_geometry()["door"])
    job = ship.add_job("readiness:repair", "repair", ship.stations["repair"], work=100)
    world.advance(100)

    rows = readiness_rows(world)
    assert any("Hull: 40/100" in row and "rigging: 30/100" in row for row in rows)
    assert any(row.startswith("Warning: hull is below") and "Next: patch hull" in row for row in rows)
    assert any("estimated sailing drive" in row.lower() for row in rows)
    assert any(row.startswith("Estimated load:") and "provisional margin" in row for row in rows)
    assert any(row.startswith("Warning: access —") for row in rows)
    assert any("Patch hull: none enabled" in row for row in rows)
    assert any("Unfinished work:" in row and "sailing does not complete or refund" in row for row in rows)
    assert any(f"order #{job.id}" in row and "blocked" in row and "Next:" in row for row in rows)
    assert any("Medical stores: 0 medicine, 0 bandages" in row for row in rows)
    assert any("combat wounds are not bridged" in row for row in rows)
    assert any("Ammunition: 0 cannon(s) loaded; 0 reserve shot" in row for row in rows)
    assert any("not departure gates" in row for row in rows)


def test_readiness_warnings_do_not_add_a_new_departure_gate():
    world = quiet_world()
    world.ship.hull = 10
    world.ship.rigging = 0
    world.ship.crew = []
    world.voyages.roles = {role: None for role in world.voyages.roles}

    rows = readiness_rows(world)
    assert any(row.startswith("Warning: hull is below") for row in rows)
    assert any("no living crew route" in row for row in rows)
    report = world.voyages.role_report(world)
    assert any("SHIP AND CREW PREPARATION" in row for row in report)
    assert any("Medical stores" in row for row in report)
    assert any("Ammunition:" in row for row in report)
    assert any("Departure gate policy" in row for row in report)

    world.voyages.confirm(world, "cinder")
    assert world.voyages.voyage["status"] == "underway"


def test_authored_departure_fixes_a_paid_water_shortage_and_sails_with_hull_warning():
    world = World.new_authored_mvp()
    assert world.economy.port == "verdant"
    world.ship.stores["clean_water_ml"] = 0
    initial = readiness_rows(world)
    assert any("Water: 0.0 L potable" in row for row in initial)

    merchant_id = "bo"
    merchant = MERCHANTS[merchant_id]
    x, y = merchant["at"]
    world.player.x, world.player.y = x + 1, y
    world.refresh()
    stock_before = world.economy.markets[merchant_id]["stock"]["spring_water_refill"]
    money_before = world.economy.money
    world.economy.trade(world, merchant_id, "spring_water_refill", 1, True)
    assert world.economy.money < money_before
    assert world.economy.markets[merchant_id]["stock"]["spring_water_refill"] == stock_before - 1

    hold_x, hold_y = world.ship.stations["hold"]
    world.player.x, world.player.y = hold_x - 1, hold_y
    world.refresh()
    world.economy.allocate(world, "spring_water_refill", 1)
    assert "spring_water_refill" not in world.economy.cargo
    assert any("Water: 5.0 L potable" in row for row in readiness_rows(world))

    # The player fixes the water shortfall but deliberately accepts the already-supported hull caution.
    world.ship.hull = 40
    review = world.voyages.role_report(world)
    assert any(row.startswith("Warning: hull is below") for row in review)
    world.voyages.confirm(world, "cinder")
    assert world.voyages.voyage["status"] == "underway"


def test_readiness_reports_spoilage_light_and_unallocated_cargo():
    world = quiet_world()
    ship = world.ship
    ship.cooked_food_batches = [{"id": 99, "food_id": "bean_stew", "recipe_id": "bean_stew",
                                 "grams": 500, "made_at_seconds": ship.seconds - (3 * 24 * 3600 - 3600)}]
    world.economy.cargo = {"biscuit": 3}

    rows = readiness_rows(world)

    assert any("Earliest spoilage:" in row and "in 1 h" in row for row in rows)
    assert any("soonest-spoiling lot" in row for row in rows)
    assert any("Light: out" in row for row in rows)
    assert any("Trade cargo: 3 unallocated unit(s)" in row for row in rows)
    assert any("allocate provisions at the hold" in row for row in rows)


def test_readiness_target_shortfall_gives_a_feasible_remedy():
    world = quiet_world()
    world.ship.stores["clean_water_ml"] = 0
    world.ship.containers = []

    rows = readiness_rows(world, target_days=2)

    assert any("Route coverage: 2.0 days" in row and "water margin -2.0 d" in row for row in rows)
    assert any("buy or fill potable water" in row for row in rows)
    voyage_rows = world.voyages.role_report(world)
    assert any("buy or fill potable water" in row for row in voyage_rows)


def test_ship_ledger_shows_readiness_without_advancing_time():
    world = quiet_world()
    before = world.ship.seconds

    rows = ship_rows(world)

    assert "SUPPLY READINESS (read-only)" in rows
    assert world.ship.seconds == before


def test_one_off_emergency_cook_logs_explicit_water_reserve_override():
    world = quiet_world()
    ship = world.ship
    ship.stores["clean_water_ml"] = 1000
    job = ship.queue_galley_once("grain_porridge", emergency_override=True)
    assert job.reserve_override
    assert any(row["event"] == "galley_reserve_override" for row in ship.audit)
    assert "Emergency galley override" in ship.events[-1]
