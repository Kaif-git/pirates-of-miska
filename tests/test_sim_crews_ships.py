from sim.core.world import WorldState
from sim.systems.crews_ships import load_crews_ships_data, validate_crews_ships_data


def test_crew_ship_catalog_and_starting_fleet_are_data_driven():
    data, digest = load_crews_ships_data()
    validate_crews_ships_data(data)
    assert len(digest) == 64
    world = WorldState.create("crew-ship-seed")
    assert len(world.crew_ships.crews) == 3
    assert len(world.economy.ship_supplies) == 3
    appointments = world.event_log.by_type("CAPTAIN_APPOINTED")
    assert len(appointments) == len(world.crew_ships.crews)
    for crew in world.crew_ships.crews.values():
        assert len(crew["roster"]) == data["rules"]["starting_crew_size"]
        assert crew["captain_id"] == crew["roles"]["captain"]
        ship = world.entities[crew["ship_ids"][0]]
        assert ship["class"] == "merchant_sloop"
        assert ship["hull"]["current"] == ship["hull"]["max"]
        assert ship["rigging"]["current"] == ship["rigging"]["max"]
        assert ship["crew_id"] == crew["id"]
        assert any(event.data.get("crew_id") == crew["id"]
                   for event in appointments)
    world.economy.ledger.assert_balanced(world.economy)
    world.crew_ships.validate_world(world)


def test_recruited_merchant_gets_a_provisioned_captain_and_causal_crew():
    world = WorldState.create("recruited-merchant-crew")
    island_id = sorted(world.economy.islands)[0]
    merchant_id, merchant_event = world.create_merchant(
        "New Harbor Merchant", island_id, silver=0, capacity_milli=20000)

    crew_id = world.crew_ships.provision_recruited_merchant(
        world, merchant_id, caused_by=merchant_event)
    crew = world.crew_ships.crews[crew_id]
    ship = world.entities[crew["ship_ids"][0]]

    assert crew["captain_id"] in crew["roster"]
    assert world.entities[crew["captain_id"]]["tier"] == "MINOR"
    assert ship["owner_type"] == "merchant"
    assert ship["crew_id"] == crew_id
    assert crew["last_event_id"] in {event.id for event in world.event_log}
    assert any(event.type == "CAPTAIN_APPOINTED"
               and event.data.get("appointment") == "recruited_merchant_captain"
               for event in world.event_log)
    world.economy.ledger.assert_balanced(world.economy)
    world.crew_ships.validate_world(world)


def test_monthly_actor_recruiter_uses_real_ship_crew_and_promotion_events():
    world = WorldState.create("raid-smoke")
    world.run_until(30 * 24)

    assert world.crew_ships._active_actor_ship_count(world, "merchant") == 8
    promotions = world.event_log.by_type("ROOKIE_PROMOTED")
    assert len(promotions) == 2
    for promotion in promotions:
        promoted_id = promotion.actors[0]["entity_id"]
        assert world.entities[promoted_id]["tier"] == "MAJOR"
        assert len(promotion.data["deed_event_ids"]) == 2
    assert any(event.data.get("method") == "build"
                   and event.actors[0]["entity_id"] == promoted_id
                   for event in world.event_log.by_type("SHIP_ACQUIRED"))
    world.run_until(60 * 24)
    assert world.crew_ships._active_actor_ship_count(world, "merchant") >= 12
    actor_factions = {faction_id for faction_id, faction in world.politics.factions.items()
                      if faction["kind"].startswith("actor_")}
    assert all(world.politics.factions[faction_id]["monthly_decisions"] == 0
               for faction_id in actor_factions)
    assert not any(set(key.split("|")) & actor_factions for key in world.politics.treaties)
    world.economy.ledger.assert_balanced(world.economy)
    world.to_snapshot()


def test_pirate_dispatch_records_route_and_cargo_beliefs():
    world = WorldState.create("pirate-belief-dispatch")
    merchant_id, merchant = next(iter(sorted(world.economy.merchants.items())))
    island_id = world.location_of(merchant["ship_id"])["id"]
    pirate_ship_id, created_event = world.create_vessel("Belief Test Raider", island_id)
    pirate_crew_id = world.crew_ships.provision_recruited_vessel(
        world, pirate_ship_id, "pirate", caused_by=created_event)
    assert world.entities[pirate_ship_id]["cargo_capacity_milli"] > 0
    world.decisions.ai_data["belief_rules"]["max_beliefs_per_agent"] = 512
    captain_id = world.crew_ships.crews[pirate_crew_id]["captain_id"]
    for route in world.geography.routes.values():
        world.decisions.set_belief(
            world, captain_id, route.id, "estimated_patrol_density_milli", 900,
            source_event_id=created_event, confidence_permille=500, source_kind="rumor")
    for merchant in world.economy.merchants.values():
        world.decisions.set_belief(
            world, captain_id, merchant["ship_id"], "estimated_cargo_value_milli", 25,
            source_event_id=created_event, confidence_permille=500, source_kind="rumor")

    world.crew_ships._dispatch_raiders(world, 1)

    raids = world.event_log.by_type("RAIDER_TARGET_SELECTED")
    assert raids
    target_event = raids[0]
    raider_id = world.crew_ships.crews[pirate_crew_id]["captain_id"]
    beliefs = world.decisions.agents[raider_id]["beliefs"]
    assert any(row["subject_id"] == target_event.location
               and row["attribute"] == "estimated_patrol_density_milli"
               for row in beliefs)
    assert any(row["subject_id"] == target_event.targets[0]["entity_id"]
               and row["attribute"] == "estimated_cargo_value_milli"
               for row in beliefs)
    assert target_event.data["belief_score"] == (
        target_event.data["estimated_cargo_value_milli"] * 1000
        // (1000 + target_event.data["estimated_patrol_density_milli"]))
    assert target_event.data["estimated_cargo_value_milli"] == 25
    assert target_event.data["estimated_patrol_density_milli"] == 900
    assert target_event.targets[0]["entity_id"] in world.entities
    world.economy.ledger.assert_balanced(world.economy)
    world.to_snapshot()


def test_crew_recruitment_and_provisions_exhaustion_have_causal_consequences():
    world = WorldState.create("crew-shortage")
    silver_before = world.economy._total_silver()
    crew_id, crew = next(iter(world.crew_ships.crews.items()))
    recruit_id = next(key for key, row in world.entities.items()
                      if row.get("kind") == "character" and row.get("crew_id") is None)
    recruit_event = world.crew_ships.recruit(
        world, crew_id, recruit_id, caused_by=crew["last_event_id"])
    assert recruit_id in crew["roster"]
    assert world.event_log.get(recruit_event).type == "RECRUITED"

    ship_id = crew["ship_ids"][0]
    home = world.economy.islands[world.entities[ship_id]["location"]["id"]]
    for goods_id, quantity in world.economy.ship_supplies[ship_id].items():
        home["stockpile_milli"][goods_id] += quantity
        world.economy.ship_supplies[ship_id][goods_id] = 0
    world.entities[ship_id]["supplies"] = dict(world.economy.ship_supplies[ship_id])
    world.crew_ships.data["rules"]["morale_loss_per_shortage_day"] = 80
    world.crew_ships.data["rules"]["desertion_basis_points_per_day"] = 10000
    world.run_until(24)
    assert world.event_log.by_type("SHIP_SUPPLIES_CONSUMED")
    assert world.event_log.by_type("CREW_SUPPLIES_EXHAUSTED")
    assert world.event_log.by_type("DESERTION")
    assert world.event_log.by_type("WAGES_PAID")
    assert crew["morale"] == 0
    assert len(crew["roster"]) == 8
    assert world.economy._total_silver() == silver_before
    world.economy.ledger.assert_balanced(world.economy)
    world.crew_ships.validate_world(world)


def test_crew_ships_and_supply_ledger_roundtrip_and_resume(tmp_path):
    uninterrupted = WorldState.create("crew-ship-resume")
    split = WorldState.create("crew-ship-resume")
    uninterrupted.run_until(24 * 5)
    split.run_until(24 * 5)
    restored = WorldState.load(split.save(tmp_path / "crew-ships.json"))
    uninterrupted.run_until(24 * 20)
    restored.run_until(24 * 20)
    assert restored.to_snapshot() == uninterrupted.to_snapshot()


def test_crew_payroll_moves_finite_silver_then_records_arrears():
    world = WorldState.create("crew-payroll")
    world.crew_ships.data["rules"]["daily_food_milli_per_member"] = 0
    world.crew_ships.data["rules"]["daily_water_milli_per_member"] = 0
    world.crew_ships.data["rules"]["daily_rum_milli_per_member"] = 0
    total_before = world.economy._total_silver()
    starting_crew_ids = set(world.crew_ships.crews)
    world.run_until(24 * 40)
    assert world.event_log.by_type("WAGES_PAID")
    assert world.event_log.by_type("WAGES_UNPAID")
    assert all(world.crew_ships.crews[crew_id]["arrears_silver"] > 0
               for crew_id in starting_crew_ids)
    assert world.economy._total_silver() == total_before
    world.economy.ledger.assert_balanced(world.economy)
    world.crew_ships.validate_world(world)


def test_computed_strength_changes_with_damage_crew_morale_and_supplies():
    world = WorldState.create("ship-strength")
    crew_id, crew = next(iter(world.crew_ships.crews.items()))
    ship_id = crew["ship_ids"][0]
    baseline = world.crew_ships.combat_power(world, ship_id)
    assert baseline["total"] > 0
    assert baseline["firepower"] > 0
    assert baseline["boarding"] > 0
    damage_event = world.crew_ships.damage_ship(
        world, ship_id, "hull", 35, caused_by=crew["last_event_id"])
    damaged = world.crew_ships.combat_power(world, ship_id)
    assert damaged["total"] < baseline["total"]
    assert damaged["damage_penalty"] > baseline["damage_penalty"]
    world.entities[ship_id]["hull"]["current"] = world.entities[ship_id]["hull"]["max"]
    world.entities[ship_id]["condition"] = 100
    member_id = next(key for key in crew["roster"] if key != crew["captain_id"])
    crew["roster"].remove(member_id)
    fewer_crew = world.crew_ships.combat_power(world, ship_id)
    assert fewer_crew["total"] < baseline["total"]
    crew["roster"].append(member_id)
    crew["morale"] = 5
    low_morale = world.crew_ships.combat_power(world, ship_id)
    assert low_morale["total"] < baseline["total"]
    world.economy.ship_supplies[ship_id]["grain"] = 0
    world.entities[ship_id]["supplies"] = dict(world.economy.ship_supplies[ship_id])
    no_food = world.crew_ships.combat_power(world, ship_id)
    assert no_food["total"] < low_morale["total"]
    observer = crew["captain_id"]
    true_strength = no_food["total"]
    accurate = world.crew_ships.estimated_strength(world, observer, ship_id, 0)
    stale = world.crew_ships.estimated_strength(world, observer, ship_id, 30)
    assert "true_strength" not in accurate
    assert abs(accurate["estimate"] - true_strength) <= (
        true_strength * accurate["uncertainty_permille"] // 1000 + 1)
    assert stale["uncertainty_permille"] >= accurate["uncertainty_permille"]
    assert world.event_log.get(damage_event).type == "SHIP_DAMAGED"


def test_living_character_and_ship_names_share_a_casefolded_registry():
    world = WorldState.create("ship-names")
    crew = next(iter(world.crew_ships.crews.values()))
    ship_id = crew["ship_ids"][0]
    cause = crew["last_event_id"]
    living_character = world.entities[crew["captain_id"]]
    original_ship_name = world.entities[ship_id]["name"]
    try:
        world.crew_ships.rename_ship(
            world, ship_id, living_character["display_name"].upper(), caused_by=cause)
        assert False, "ship name collided with a living character"
    except ValueError as exc:
        assert "already in use" in str(exc)
    other_ship = next(key for key, row in world.entities.items()
                      if row.get("kind") == "ship" and key != ship_id)
    try:
        world.crew_ships.rename_ship(
            world, ship_id, world.entities[other_ship]["name"], caused_by=cause)
        assert False, "ship name collided with another living ship"
    except ValueError as exc:
        assert "already in use" in str(exc)
    renamed = world.crew_ships.rename_ship(
        world, ship_id, "  The   Amber Gull  ", caused_by=cause)
    assert world.entities[ship_id]["name"] == "The Amber Gull"
    event = world.event_log.get(renamed)
    assert event.type == "SHIP_RENAMED"
    assert event.data["old_name"] == original_ship_name
    assert event.data["new_name"] == "The Amber Gull"
    world.entities[ship_id]["name"] = living_character["display_name"].swapcase()
    try:
        world.crew_ships.validate_world(world)
        assert False, "world validator accepted duplicate living names"
    except ValueError as exc:
        assert "Duplicate living display name" in str(exc)
