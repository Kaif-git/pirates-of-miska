import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "engine")]

from pirates.game import World
from pirates.ship import (HUNGER_INCREASE_PER_SECOND, THIRST_INCREASE_PER_SECOND,
                          LABORS, STATIONS, BUNKS, Job, ShipLife)
from omnisim.world import tile as T
from pirates.management import crew_menu, chores_menu, time_menu, profile_rows, job_explanation


def quiet_world():
    world = World()
    for c in world.ship.crew:
        c.hunger = c.thirst = c.fatigue = c.loneliness = c.boredom = 0
    return world


def test_measured_need_rates_apply_per_second():
    world = quiet_world()
    for sailor in world.ship.crew:
        sailor.labors = {kind: 0 for kind in LABORS}
    sailor = world.ship.crew[0]

    world.advance(100)

    assert sailor.hunger == pytest.approx(HUNGER_INCREASE_PER_SECOND)
    assert sailor.thirst == pytest.approx(THIRST_INCREASE_PER_SECOND)


def disable_all(ship):
    for c in ship.crew:
        c.labors = {k: 0 for k in LABORS}


def supply_total(ship, resource):
    return ship.stores[resource] + sum(j.reserved.get(resource, 0) for j in ship.jobs) + sum(c.cargo.get(resource, 0) for c in ship.crew)


def test_ship_stations_and_bunks_reachable_through_doors():
    world = quiet_world()
    c = world.ship.crew[0]
    for key, target in STATIONS.items():
        assert world.ship.path(world, c, target) is not None, key
    for bunk in BUNKS:
        assert world.ship.path(world, c, bunk, exact=True) is not None
    assert len({c.bunk for c in world.ship.crew}) == 6
    assert not world.game_map.is_walkable(92, 25)


def test_simulation_and_visible_entities_are_not_decorative():
    world = World()
    starts = [(c.x, c.y) for c in world.ship.crew]
    assert len([e for e in world.entities() if e.get("role") in ("player", "crew")]) == 7
    assert len(world.economy.merchants()) == 3
    world.advance(60000)
    assert world.ship.hull == 100
    assert world.ship.bilge < 3
    assert world.ship.food_stock_g() > 25000
    assert all(world.ship.ammo.values())
    assert all(v < 20 for v in world.ship.dirt.values())
    assert world.ship.rigging >= 90
    assert all((c.x, c.y) != at for c, at in zip(world.ship.crew, starts))
    assert sum(c.completed for c in world.ship.crew) >= 9
    assert world.ship.crew[1].hunger < 10
    assert world.ship.crew[2].thirst < 70
    assert world.ship.crew[4].fatigue < 20
    assert any("story" in thought for c in world.ship.crew for thought in c.thoughts)


def test_galley_job_explains_finite_ingredient_to_meal_conversion():
    world = World()
    for key in ("fruit_g", "dried_fish_g", "biscuit_g"):
        world.ship.stores[key] = 0
    world.ship.generate_jobs(world)
    job = next(job for job in world.ship.jobs if job.kind == "cook")
    assert world.ship.job_cost(job) == {"grain_g": 2000, "clean_water_ml": 1000}
    assert "2.00 kg grain" in job_explanation(world, job)
    assert "3.00 kg Grain porridge" in job_explanation(world, job)


def test_cooked_food_keeps_dish_identity_partial_leftovers_and_save_state(tmp_path):
    world = World()
    ship = world.ship
    sailor = ship.crew[0]
    provisions_before = (ship.food_stock_g() + ship.stores["clean_water_ml"])
    ship.stores["grain_g"] -= 2000
    ship.stores["clean_water_ml"] -= 1000
    job = Job(ship.next_id, "cook:grain_porridge", "cook", ship.stations["galley"], state="claimed")
    ship.next_id += 1
    ship.finish(job, sailor, world)
    assert ship.food_stock_g() + ship.stores["clean_water_ml"] == provisions_before
    assert dict(ship.cooked_food_summary())["Grain porridge"] == 3000
    before = ship.food_stock_g()
    assert ship.consume_food(1250)  # 1 kg legacy remainder followed by 250 g porridge.
    assert dict(ship.cooked_food_summary())["Grain porridge"] == 2750
    assert ship.food_stock_g() == before - 1250
    path = tmp_path / "named-food.json"
    world.save(path)
    resumed = World.load(path)
    assert resumed.ship.cooked_food_summary() == ship.cooked_food_summary()
    assert resumed.ship.cooked_food_batches == ship.cooked_food_batches


def test_drink_uses_available_fractional_serving_and_tracks_daily_use(tmp_path):
    world = quiet_world()
    ship = world.ship
    sailor = ship.crew[0]
    sailor.x, sailor.y = ship.stations["water"][:2]
    sailor.thirst = 90
    ship.stores["clean_water_ml"] = 100
    for _ in range(7):
        ship.satisfy(world, sailor, "drink")
    assert ship.stores["clean_water_ml"] == 0
    assert sailor.thirst == pytest.approx(82.5)
    assert ship.water_consumed_today_ml == sailor.water_consumed_today_ml == 100
    assert ship.food_consumed_today_g == sailor.food_consumed_today_g == 0
    path = tmp_path / "daily-use.json"
    old_day = world.clock.day
    world.clock.total_ticks += 8640000
    assert world.clock.day == old_day + 1
    ship.record_consumption(world, sailor, water_ml=25)
    assert ship.water_consumed_today_ml == sailor.water_consumed_today_ml == 25
    world.save(path)
    resumed = World.load(path)
    assert resumed.ship.water_consumed_today_ml == 25
    assert resumed.ship.crew[0].water_consumed_today_ml == 25


def test_food_preference_and_recent_variety_guide_actual_food_choice():
    world = quiet_world()
    ship = world.ship
    ship.prepared_food_g = 0
    ship.stores["biscuit_g"] = 0
    ship.stores["fruit_g"] = 250
    ship.stores["dried_fish_g"] = 250
    assert ship.consume_food_details(250, preferred_food_id="fruit") == [("fruit", 250)]
    ship.stores["fruit_g"] = 250
    assert ship.consume_food_details(250, recent_food_ids=["fruit", "fruit"]) == [("dried_fish", 250)]
    sailor = ship.crew[0]
    sailor.food_preference = "dried_fish"
    old_morale = sailor.morale
    ship.apply_meal_preference(sailor, [("dried_fish", 250)])
    assert sailor.morale == old_morale + 1
    assert sailor.recent_meals[-1] == "dried_fish"


def test_cooked_batches_age_spoil_into_mass_accounted_waste_and_disposal_logs():
    from pirates.ship import COOKED_FOOD_FRESH_SECONDS

    world = quiet_world()
    ship = world.ship
    ship.cooked_food_batches = [{"id": 17, "food_id": "grain_porridge", "recipe_id": "grain_porridge",
                                 "grams": 2500, "made_at_seconds": 10}]
    ship.stores["fruit_g"] = 0
    ship.fresh_food_lots = []
    ship.next_food_batch_id = 18
    ship.seconds = 10 + COOKED_FOOD_FRESH_SECONDS - 1
    freshness = ship.cooked_food_freshness()[0]
    assert freshness["condition"] == 0
    assert freshness["spoils_in_seconds"] == 1
    mass_before = ship.prepared_food_g + ship.stores["food_waste_g"]
    world.advance(100)
    assert ship.cooked_food_batches == []
    assert ship.stores["food_waste_g"] == 2500
    assert ship.prepared_food_g + ship.stores["food_waste_g"] == mass_before
    ship.dispose_food_waste(500)
    assert ship.stores["food_waste_g"] == 2000
    assert any(row["event"] == "food_waste_disposed" for row in ship.audit)


def test_cooked_food_freshness_survives_save_and_only_advances_with_game_time(tmp_path):
    world = quiet_world()
    ship = world.ship
    ship.cooked_food_batches[0]["made_at_seconds"] = ship.seconds
    path = tmp_path / "fresh-cooked-food.json"
    world.save(path)
    loaded = World.load(path)
    assert loaded.ship.cooked_food_freshness() == ship.cooked_food_freshness()
    assert loaded.ship.stores["food_waste_g"] == 0
    assert loaded.ship.cooked_food_batches == ship.cooked_food_batches


def test_fresh_fruit_lots_age_fifo_and_convert_to_mass_accounted_waste():
    from pirates.ship import FRESH_FRUIT_SECONDS

    world = quiet_world()
    ship = world.ship
    ship.fresh_food_lots = [
        {"id": 11, "resource": "fruit_g", "grams": 1000, "received_at_seconds": 0},
        {"id": 12, "resource": "fruit_g", "grams": 1500, "received_at_seconds": 20},
    ]
    ship.next_fresh_food_lot_id = 13
    ship.seconds = FRESH_FRUIT_SECONDS
    assert ship.fresh_fruit_freshness()[0]["condition"] == 0
    before = ship.stores["fruit_g"] + ship.stores["food_waste_g"]
    world.advance(100)
    assert ship.stores["fruit_g"] == 1500
    assert ship.stores["food_waste_g"] == 1000
    assert ship.stores["fruit_g"] + ship.stores["food_waste_g"] == before
    assert ship.fresh_food_lots == [
        {"id": 12, "resource": "fruit_g", "grams": 1500, "received_at_seconds": 20}]


def test_consuming_fresh_fruit_removes_oldest_lot_first():
    world = quiet_world()
    ship = world.ship
    ship.fresh_food_lots = [
        {"id": 11, "resource": "fruit_g", "grams": 100, "received_at_seconds": 0},
        {"id": 12, "resource": "fruit_g", "grams": 250, "received_at_seconds": 20},
    ]
    ship.prepared_food_g = 0
    ship.stores["fruit_g"] = 350
    ship.stores["biscuit_g"] = 0
    ship.stores["dried_fish_g"] = 0
    ship.stores["hard_cheese_g"] = 0
    assert ship.consume_food_details(250) == [("fruit", 250)]
    assert ship.fresh_food_lots == [
        {"id": 12, "resource": "fruit_g", "grams": 100, "received_at_seconds": 20}]


def test_fruit_preservation_consumes_finite_salt_and_conserves_mass():
    world = quiet_world()
    ship = world.ship
    ship.fresh_food_lots = [{"id": 31, "resource": "fruit_g", "grams": 2500, "received_at_seconds": 0}]
    ship.stores["fruit_g"] = 2500
    ship.stores["salt_g"] = 100
    initial_mass = 2600
    job = ship.queue_fruit_preservation()
    assert ship.job_cost(job) == {"fruit_g": 1000, "salt_g": 100}
    ship.reserve_fresh_food_lots(job, 1000)
    ship.stores["fruit_g"] -= 1000
    ship.fresh_food_lots[0]["grams"] -= 1000
    ship.fresh_food_lots = [lot for lot in ship.fresh_food_lots if lot["grams"]]
    ship.stores["salt_g"] -= 100
    sailor = ship.crew[0]
    ship.finish(job, sailor, world)
    after = (ship.stores["fruit_g"] + ship.stores["salt_g"] + ship.stores["salted_fruit_g"]
             + ship.stores["food_waste_g"])
    assert after == initial_mass
    assert ship.stores["salted_fruit_g"] == 900
    assert ship.stores["food_waste_g"] == 200
    assert job.fresh_food_lots == []


def test_preservation_reservation_keeps_original_fruit_age_across_save(tmp_path):
    world = quiet_world()
    ship = world.ship
    for sailor in ship.crew:
        sailor.labors = {key: 0 for key in LABORS}
        sailor.hunger = sailor.thirst = sailor.fatigue = sailor.loneliness = sailor.boredom = 0
        sailor.sleep_debt = 0
    cook = ship.crew[1]
    cook.labors["cook"] = 1
    ship.stores["fruit_g"] = 1500
    ship.fresh_food_lots = [{"id": 21, "resource": "fruit_g", "grams": 1500, "received_at_seconds": 100}]
    ship.next_fresh_food_lot_id = 22
    ship.stores["salt_g"] = 100
    ship.seconds = 500
    job = ship.queue_fruit_preservation()
    ship.claim(world, cook)
    assert job.worker == cook.id and sum(lot["grams"] for lot in job.fresh_food_lots) == 1000
    assert sum(lot["grams"] for lot in ship.fresh_food_lots) == ship.stores["fruit_g"] == 500
    path = tmp_path / "preserving-fruit.json"
    world.save(path)
    loaded = World.load(path)
    loaded_job = next(item for item in loaded.ship.jobs if item.key == "preserve:salted_fruit")
    assert loaded_job.fresh_food_lots == job.fresh_food_lots
    assert loaded.ship.fresh_food_lots == ship.fresh_food_lots


def test_freshness_advances_identically_across_small_and_large_time_chunks():
    from pirates.ship import COOKED_FOOD_FRESH_SECONDS, FRESH_FRUIT_SECONDS

    worlds = [quiet_world(), quiet_world()]
    for world in worlds:
        for sailor in world.ship.crew:
            sailor.hunger = sailor.thirst = sailor.fatigue = sailor.loneliness = sailor.boredom = 0
            sailor.sleep_debt = 0
        ship = world.ship
        ship.stores["fruit_g"] = 1000
        ship.fresh_food_lots = [{"id": 3, "resource": "fruit_g", "grams": 1000,
                                 "received_at_seconds": ship.seconds - FRESH_FRUIT_SECONDS + 2}]
        ship.cooked_food_batches = [{"id": 4, "food_id": "bean_stew", "recipe_id": "bean_stew",
                                     "grams": 1500,
                                     "made_at_seconds": ship.seconds - COOKED_FOOD_FRESH_SECONDS + 2}]
    worlds[0].advance(300)
    for _ in range(3):
        worlds[1].advance(100)
    for world in worlds:
        assert world.ship.stores["fruit_g"] == 0
        assert world.ship.cooked_food_batches == []
        assert world.ship.stores["food_waste_g"] == 2500


def test_saved_freshness_timestamp_cannot_be_reset_on_roundtrip(tmp_path):
    from pirates.ship import FRESH_FRUIT_SECONDS

    world = quiet_world()
    ship = world.ship
    ship.seconds = FRESH_FRUIT_SECONDS - 2
    ship.fresh_food_lots = [{"id": 8, "resource": "fruit_g", "grams": 2500,
                             "received_at_seconds": 0}]
    ship.next_fresh_food_lot_id = 9
    path = tmp_path / "fruit-near-expiry.json"
    world.save(path)
    resumed = World.load(path)
    assert resumed.ship.fresh_food_lots[0]["received_at_seconds"] == 0
    resumed.advance(200)
    assert resumed.ship.stores["fruit_g"] == 0
    assert resumed.ship.stores["food_waste_g"] >= 2500


def test_legacy_save_initializes_fruit_age_once_without_inventing_history(tmp_path):
    world = quiet_world()
    world.ship.seconds = 4321
    path = tmp_path / "legacy-fruit.json"
    world.save(path)
    data = json.loads(path.read_text())
    data["ship"].pop("fresh_food_lots")
    data["ship"].pop("next_fresh_food_lot_id")
    path.write_text(json.dumps(data))
    loaded = World.load(path)
    assert loaded.ship.fresh_food_lots == [{"id": 1, "resource": "fruit_g", "grams": 2500,
                                            "received_at_seconds": 4321}]
    path2 = tmp_path / "migrated-fruit.json"
    loaded.save(path2)
    reloaded = World.load(path2)
    assert reloaded.ship.fresh_food_lots == loaded.ship.fresh_food_lots


def test_rain_exposure_uses_real_overhead_floor_and_oilskin_slows_wetting(tmp_path):
    world = quiet_world()
    ship = world.ship
    sailor = ship.crew[0]
    world.clock.total_ticks = (4 * 3600 + 4 * 60) * 100  # day 1, hour 4 is rain
    local = (sailor.x - 69, sailor.y - 12)
    assert ship.exposed_to_rain(sailor, world)
    for _ in range(10):
        ship.update_wetness(sailor, world)
    assert sailor.wetness == 40
    world.economy.gear[sailor.id] = {"coat": "oilskin"}
    sailor.wetness = 0
    for _ in range(10):
        ship.update_wetness(sailor, world)
    assert sailor.wetness == 10
    world.ship_model.place(local[0], local[1], sailor.z + 1, "floor")
    assert not ship.exposed_to_rain(sailor, world)
    for _ in range(10):
        ship.update_wetness(sailor, world)
    assert sailor.wetness == 0
    sailor.wetness = 37
    save_world = quiet_world()
    save_world.ship.crew[0].wetness = 37
    path = tmp_path / "wetness.json"
    save_world.save(path)
    assert World.load(path).ship.crew[0].wetness == 37


def test_wetness_is_bounded_and_dries_in_dry_weather():
    world = quiet_world()
    ship = world.ship
    sailor = ship.crew[0]
    world.clock.total_ticks = 0  # day 1, hour 8 is dry
    sailor.wetness = 3
    ship.update_wetness(sailor, world)
    assert sailor.wetness == 1
    sailor.wetness = 100
    assert not ship.exposed_to_rain(sailor, world)
    ship.update_wetness(sailor, world)
    assert sailor.wetness == 98


def test_severe_wetness_wears_only_clothing_and_wear_degrades_effects():
    world = quiet_world()
    economy = world.economy
    economy.gear["mara"] = {"clothing": "work_shirt", "tool": "mop"}
    economy.gear_ids["mara"] = {"clothing": "garment-501", "tool": "tool-502"}
    economy.gear_condition["mara"] = {"clothing": 100, "tool": 100}
    sailor = world.ship.sailor("mara")
    sailor.wetness = 100
    world.ship.wear_wet_garments(sailor, economy)
    assert economy.gear_condition["mara"] == {"clothing": 99, "tool": 100}
    assert economy.bonus("mara", "fatigue_multiplier") > 0.85
    assert economy.gear_ids["mara"]["clothing"] == "garment-501"


def test_bandage_is_finite_and_advances_injury_without_replacing_medicine():
    world = quiet_world()
    ship = world.ship
    sailor = ship.sailor("mara")
    injury = sailor.suffer_injury("sprain", 2, "test")
    ship.stores["bandages"] = 1
    before_medicine = ship.stores["medicine"]
    ship.apply_bandage(sailor)
    assert injury["recovery"] == 20
    assert ship.stores["bandages"] == 0
    assert ship.stores["medicine"] == before_medicine
    with pytest.raises(ValueError, match="No clean bandage"):
        ship.apply_bandage(sailor)


def test_soap_wash_consumes_measured_potable_water_and_persists_cleanliness(tmp_path):
    world = quiet_world()
    ship = world.ship
    sailor = ship.sailor("mara")
    sailor.cleanliness = 20
    ship.stores["clean_water_ml"] = 400
    ship.stores["soap"] = 1
    ship.wash_with_soap(sailor)
    assert sailor.cleanliness == 60
    assert ship.stores["clean_water_ml"] == 150
    assert ship.stores["soap"] == 0
    assert ship.stores["questionable_water_ml"] == 0
    path = tmp_path / "washed-crew.json"
    world.save(path)
    assert World.load(path).ship.sailor("mara").cleanliness == 60


def test_soap_wash_shortage_is_atomic_and_cleanliness_has_no_health_effect():
    world = quiet_world()
    ship = world.ship
    sailor = ship.sailor("mara")
    sailor.cleanliness = 10
    ship.stores["soap"] = 1
    ship.stores["clean_water_ml"] = 249
    health, stress = sailor.health, sailor.stress
    with pytest.raises(ValueError, match="250 mL potable water"):
        ship.wash_with_soap(sailor)
    assert ship.stores["soap"] == 1 and ship.stores["clean_water_ml"] == 249
    assert sailor.cleanliness == 10 and (sailor.health, sailor.stress) == (health, stress)


def test_oil_lantern_uses_finite_oil_extinguishes_and_extends_visibility(tmp_path):
    world = quiet_world()
    ship = world.ship
    ship.stores["lanterns"] = 1
    ship.stores["lamp_oil_ml"] = 500
    from pirates.ship_physics import load_by_position
    before_supply_mass = load_by_position(world)[0]["supplies"]
    baseline = int(world.game_map.visible.sum())
    ship.toggle_ship_light("lantern")
    world.refresh()
    assert world.game_map.visible.sum() > baseline
    assert ship.stores["lamp_oil_ml"] == 250 and ship.light_fuel_ml == 250
    assert load_by_position(world)[0]["supplies"] == pytest.approx(before_supply_mass)
    world.advance(1000)  # 10 game-seconds -> 1 mL burned
    assert ship.light_fuel_ml == 249 and ship.light_burn_remainder == 0
    ship.toggle_ship_light("lantern")
    assert not ship.light_on and ship.light_fuel_ml == 249
    path = tmp_path / "lantern-state.json"
    world.save(path)
    loaded = World.load(path)
    assert loaded.ship.light_type == "lantern" and not loaded.ship.light_on
    assert loaded.ship.light_fuel_ml == 249


def test_candle_consumption_burn_and_extinguish_preserve_fuel_without_fire():
    world = quiet_world()
    ship = world.ship
    ship.stores["candles"] = 2
    from pirates.ship_physics import load_by_position
    before_supply_mass = load_by_position(world)[0]["supplies"]
    before_fires = dict(world.hazards.fires)
    ship.toggle_ship_light("candle")
    assert load_by_position(world)[0]["supplies"] == pytest.approx(before_supply_mass)
    world.advance(100 * 17)
    assert ship.candle_fuel_seconds == 3583
    ship.toggle_ship_light("candle")
    assert not ship.light_on and ship.candle_fuel_seconds == 3583
    assert ship.stores["candles"] == 1
    assert load_by_position(world)[0]["supplies"] < before_supply_mass
    assert world.hazards.fires == before_fires


def test_light_sources_keep_separate_remaining_fuel_when_switching_and_reject_bad_save(tmp_path):
    world = quiet_world()
    ship = world.ship
    ship.stores["lanterns"] = 1
    ship.stores["lamp_oil_ml"] = 250
    ship.stores["candles"] = 1
    ship.toggle_ship_light("lantern")
    ship.toggle_ship_light("lantern")
    ship.toggle_ship_light("candle")
    assert ship.light_on and ship.light_type == "candle"
    assert ship.light_fuel_ml == 250 and ship.candle_fuel_seconds == 3600
    path = tmp_path / "lighting-validation.json"
    world.save(path)
    data = json.loads(path.read_text())
    data["ship"]["lighting"]["fuel_ml"] = -1
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="lighting/fuel"):
        World.load(path)


def test_no_overlap_no_walking_through_walls_or_captain():
    world = World()
    before = [(c.x, c.y) for c in world.ship.crew]
    for _ in range(300):
        world.advance(100)
        positions = [(c.x, c.y) for c in world.ship.crew]
        assert len(set(positions + [(world.player.x, world.player.y)])) == 7
        assert all(world.game_map.is_walkable(*at) for at in positions)
        assert all(abs(x - bx) + abs(y - by) <= 1 for (x, y), (bx, by) in zip(positions, before))
        before = positions


def test_menu_time_is_frozen_and_cancelled_wait_costs_nothing():
    class Fake:
        def render_list(self, *args, **kwargs):
            pass
        def choose(self, count):
            return None
        def wait_any(self):
            pass
    world = World()
    snapshot = world.ship.snapshot()
    clock = world.clock.total_ticks
    crew_menu(Fake(), world)
    chores_menu(Fake(), world)
    time_menu(Fake(), world)
    assert world.ship.snapshot() == snapshot
    assert world.clock.total_ticks == clock
    assert "Personal want:" in " ".join(profile_rows(world.ship.crew[0]))


def test_fractional_ticks_and_chunking_produce_same_state():
    a, b = World(), World()
    a.advance(37)
    assert a.ship.seconds == 0 and a.ship.remainder == 37
    a.advance(9963)
    for _ in range(100):
        b.advance(100)
    assert a.ship.snapshot() == b.ship.snapshot()
    assert (a.game_map.doors == b.game_map.doors).all()


def test_jobs_exclusive_and_supplies_reserved_once():
    world = quiet_world()
    ship = world.ship
    before = {key: supply_total(ship, key) for key in ship.stores}
    world.advance(100)
    assigned = [c.job_id for c in ship.crew if c.job_id is not None]
    assert len(assigned) == len(set(assigned))
    for key, value in before.items():
        assert supply_total(ship, key) == value
    for c in ship.crew:
        if c.job_id is not None:
            assert ship.job(c.job_id).worker == c.id


def test_labor_off_and_personal_priority():
    world = quiet_world()
    ship = world.ship
    disable_all(ship)
    world.advance(1000)
    assert all(c.job_id is None for c in ship.crew)
    c = ship.crew[0]
    c.labors["repair"] = 5
    c.labors["bail"] = 1  # equal board priority; personal preference wins
    ship.claim(world, c)
    assert ship.job(c.job_id).kind == "bail"
    ship.set_labor(c.id, "bail", 0)
    assert c.job_id is None
    with pytest.raises(ValueError):
        ship.set_labor(c.id, "repair", 6)


def test_missing_supplies_block_without_conjuring_work():
    world = quiet_world()
    ship = world.ship
    ship.stores["planks"] = 0
    world.advance(18000)
    assert ship.hull == 72
    repair = next(j for j in ship.jobs if j.kind == "repair")
    assert repair.state == "blocked" and "planks" in repair.reason
    ship.stores["planks"] = 2
    world.advance(18000)
    assert ship.hull == 100
    assert supply_total(ship, "planks") == 0


def test_need_preempts_and_resumes_claim_with_progress():
    world = quiet_world()
    ship = world.ship
    disable_all(ship)
    c = ship.crew[0]
    c.labors["repair"] = 1
    for _ in range(120):
        world.advance(100)
        job = ship.job(c.job_id)
        if job and job.progress > 0:
            break
    assert job.progress > 0 and c.cargo == {"planks": 1}
    progress = job.progress
    c.thirst = 90
    world.advance(100)
    assert job.state == "paused" and c.job_id == job.id
    assert job.progress == progress and c.cargo == {"planks": 1}
    world.advance(9000)
    assert c.thirst < 65
    assert job.state == "done"


def test_cancel_reserved_and_carried_supplies_are_not_lost_or_teleported():
    world = quiet_world()
    ship = world.ship
    disable_all(ship)
    c = ship.crew[0]
    c.labors["repair"] = 1
    before = supply_total(ship, "planks")
    ship.claim(world, c)
    first = ship.job(c.job_id)
    assert first.reserved == {"planks": 1}
    ship.cancel(first.id)
    assert first.state == "cancelled" and supply_total(ship, "planks") == before
    ship.cooldowns.clear()
    ship.generate_jobs()
    ship.claim(world, c)
    second = ship.job(c.job_id)
    for _ in range(100):
        world.advance(100)
        if c.cargo:
            break
    assert c.cargo == {"planks": 1}
    hold_amount = ship.stores["planks"]
    ship.cancel(second.id)
    assert second.state == "returning"
    assert ship.stores["planks"] == hold_amount  # bundle is still on sailor
    world.advance(3000)
    assert second.state == "cancelled"
    assert supply_total(ship, "planks") == before
    assert not c.cargo


def test_unreachable_site_reports_blockage_then_retries():
    world = quiet_world()
    ship = world.ship
    disable_all(ship)
    c = ship.crew[0]
    c.labors["repair"] = 1
    x, y = STATIONS["repair"]
    old = int(world.game_map.tiles[y, x - 1])
    world.game_map.tiles[y, x - 1] = T.WALL_WOOD
    world.game_map.tiles[y - 1, x] = T.WALL_WOOD
    world.game_map.tiles[y + 1, x] = T.WALL_WOOD
    world.advance(100)
    repair = next(j for j in ship.jobs if j.kind == "repair")
    assert repair.state == "blocked" and "route" in repair.reason
    assert not repair.reserved
    world.game_map.tiles[y, x - 1] = old
    world.advance(100)
    assert repair.worker == c.id


def test_food_water_and_sleep_are_finite_and_location_bound():
    world = quiet_world()
    ship = world.ship
    disable_all(ship)
    c = ship.crew[0]
    c.hunger = 90
    before = ship.meals
    world.advance(100)
    assert c.hunger >= 90 and ship.meals == before  # has not reached mess
    world.advance(3000)
    assert c.hunger < 25 and ship.meals == before - 1
    c.thirst = 90
    water = ship.stores["clean_water_ml"]
    world.advance(3000)
    assert c.thirst < 60 and ship.stores["clean_water_ml"] <= water - 500
    c.fatigue = 75
    c.sleep_debt = 75  # Physical fatigue alone now calls for a short rest.
    world.advance(18000)
    assert c.fatigue < 20
    assert "Slept in my own bunk." in c.thoughts


def test_wants_and_social_relationships_change_morale():
    world = quiet_world()
    ship = world.ship
    disable_all(ship)
    a, b = ship.crew[:2]
    a.x, a.y = 72, 28
    b.x, b.y = 72, 27
    a.loneliness = 80
    b.boredom = 0
    before = a.morale
    world.advance(2000)
    assert a.loneliness < 60
    assert a.relationships.get(b.id, 0) >= 1
    assert b.relationships.get(a.id, 0) >= 1
    assert a.morale > before
    a.loneliness = 0
    a.boredom = 80
    a.intent = ""
    world.advance(3000)
    assert a.boredom < 40
    assert any("quiet moment" in t for t in a.thoughts)


def test_starvation_has_health_and_morale_consequences():
    world = quiet_world()
    ship = world.ship
    disable_all(ship)
    ship.meals = 0
    ship.stores["clean_water_ml"] = 0
    c = ship.crew[0]
    c.thirst = c.hunger = 99
    world.advance(10000)
    assert c.health < 100 and c.morale < 65
    assert c.completed == 0


@pytest.mark.parametrize("elapsed", [0, 1, 20, 65, 180, 600])
def test_mid_simulation_save_resume_is_deterministic(tmp_path, elapsed):
    a = World()
    a.advance(elapsed * 100 + 37)
    path = tmp_path / "ship.json"
    a.save(path)
    b = World.load(path)
    # JSON converts RGB/coordinates tuples to lists; snapshot serialisation is canonical.
    assert json.dumps(a.ship.snapshot(), sort_keys=True) == json.dumps(b.ship.snapshot(), sort_keys=True)
    a.advance(2000)
    b.advance(2000)
    assert json.dumps(a.ship.snapshot(), sort_keys=True) == json.dumps(b.ship.snapshot(), sort_keys=True)


@pytest.mark.parametrize("corruption", ["overlap", "need", "supply", "orphan", "labor", "target", "duplicate", "nan"])
def test_bad_ship_save_rejected_without_mutating_world(tmp_path, corruption):
    world = quiet_world()
    world.advance(100)
    before = deepcopy(world.ship.snapshot())
    path = tmp_path / "bad.json"
    world.save(path)
    data = json.loads(path.read_text())
    state = data["ship"]
    if corruption == "overlap":
        state["crew"][0]["x"], state["crew"][0]["y"] = data["player"]
    elif corruption == "need":
        state["crew"][0]["hunger"] = -1
    elif corruption == "supply":
        state["stores"]["water"] = -1
    elif corruption == "orphan":
        state["crew"][0]["job_id"] = 9999
    elif corruption == "labor":
        state["crew"][0]["labors"]["repair"] = 99
    elif corruption == "target":
        state["jobs"][0]["target"] = [0, 0]
    elif corruption == "duplicate":
        state["jobs"].append(deepcopy(state["jobs"][0]))
    elif corruption == "nan":
        state["hull"] = float("nan")
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        World.load(path)
    assert world.ship.snapshot() == before


def test_legacy_v1_slot_adds_crew_without_erasing_inventory(tmp_path):
    world = World()
    world.player.x, world.player.y = 30, 23
    world.move(1, 0)
    world.pickup()
    path = tmp_path / "old.json"
    world.save(path)
    data = json.loads(path.read_text())
    data["version"] = 1
    data.pop("ship")
    path.write_text(json.dumps(data))
    restored = World.load(path)
    assert restored.player.x == 31 and len(restored.inventory) == 2
    assert len(restored.ship.crew) == 6


def test_legacy_deck_positions_displaced_by_new_fixtures_migrate(tmp_path):
    world = World()
    path = tmp_path / "old.json"
    world.save(path)
    data = json.loads(path.read_text())
    data["version"] = 1
    data["player"] = [74, 30]  # old bare deck, now a non-walkable water cask
    data.pop("ship")
    data["ground"]["74,30"] = [{"id": "test", "name": "Kept cargo", "weight_g": 1, "bulk_ml": 1, "quantity": 2}]
    path.write_text(json.dumps(data))
    restored = World.load(path)
    assert restored.game_map.is_walkable(restored.player.x, restored.player.y)
    assert any(i["name"] == "Kept cargo" for items in restored.ground_items.values() for i in items)


def test_management_orders_set_labors_and_cancel_without_time_cost():
    class Scripted:
        def __init__(self, answers):
            self.answers = iter(answers)
        def render_list(self, *args, **kwargs):
            pass
        def choose(self, count):
            return next(self.answers)
    world = World()
    before = world.clock.total_ticks
    crew_menu(Scripted([0, 1, 0, None, None]), world)
    assert world.ship.crew[0].labors["repair"] == 0
    job = sorted(world.ship.jobs, key=lambda j: (j.priority, j.id))[0]
    chores_menu(Scripted([0, 1, 4, None]), world)
    assert job.priority == 5
    target = sorted((j for j in world.ship.jobs if j.state not in ("done", "cancelled")), key=lambda j: (j.priority, j.id))[0]
    chores_menu(Scripted([0, 2, None]), world)
    assert target.state == "cancelled"
    assert world.clock.total_ticks == before


def test_observation_escape_stops_and_quit_is_forwarded(monkeypatch):
    import tcod.event
    from collections import deque
    from pirates.management import observe
    from omnisim.ui.ascii_renderer import MovementRepeater
    class Fake:
        _move_repeater = MovementRepeater()
        _pending_events = deque()
        def _finish_motion_cache(self):
            pass
        def render(self, world):
            pass
    world = World()
    renderer = Fake()
    event = tcod.event.KeyDown(scancode=tcod.event.Scancode.ESCAPE, sym=tcod.event.KeySym.ESCAPE, mod=0)
    monkeypatch.setattr(tcod.event, "wait", lambda timeout: [event])
    observe(renderer, world, seconds=60)
    assert world.ship.seconds == 1
    assert not renderer._pending_events
    event = tcod.event.Quit()
    observe(renderer, world, seconds=60)
    assert world.ship.seconds == 2
    assert list(renderer._pending_events) == [event]


def test_returning_cancelled_job_can_be_saved_and_resumed(tmp_path):
    world = quiet_world()
    disable_all(world.ship)
    c = world.ship.crew[0]
    c.labors["repair"] = 1
    for _ in range(100):
        world.advance(100)
        if c.cargo:
            break
    job = world.ship.job(c.job_id)
    before = supply_total(world.ship, "planks")
    world.ship.cancel(job.id)
    path = tmp_path / "returning.json"
    world.save(path)
    restored = World.load(path)
    restored.advance(3000)
    assert restored.ship.job(job.id).state == "cancelled"
    assert supply_total(restored.ship, "planks") == before
