import pytest

from sim.core.world import WorldState
from sim.systems.crews_ships import load_crews_ships_data, validate_crews_ships_data


def test_crew_ship_catalog_and_starting_fleet_are_data_driven():
    data, digest = load_crews_ships_data()
    validate_crews_ships_data(data)
    assert len(digest) == 64
    world = WorldState.create("crew-ship-seed")
    assert len(world.crew_ships.crews) == 6
    assert len(world.economy.ship_supplies) == 6
    assert all(world.crew_ships._active_actor_ship_count(world, band) == 1
               for band in ("pirate", "privateer", "marine"))
    seeded = world.event_log.by_type("ACTOR_FORCE_SEEDED")
    assert {event.data["band"] for event in seeded} == {"pirate", "privateer", "marine"}
    assert all("WORLD_GENERATED" in {
        world.event_log.get(cause).type for event in seeded for cause in event.caused_by
    } for event in seeded)
    appointments = world.event_log.by_type("CAPTAIN_APPOINTED")
    assert len(appointments) == len(world.crew_ships.crews)
    for crew in world.crew_ships.crews.values():
        if crew["ideology_tags"] == ["merchant", "trade"]:
            assert len(crew["roster"]) == data["rules"]["starting_crew_size"]
        else:
            assert len(crew["roster"]) == 1
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


def test_crew_state_rejects_multi_hull_membership_under_current_authority_model():
    world = WorldState.create("crew-single-hull-authority")
    crew = next(iter(world.crew_ships.crews.values()))
    crew["ship_ids"].append(crew["ship_ids"][0])

    with pytest.raises(ValueError, match="Invalid crew record"):
        world.crew_ships.validate_state()


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
    assert crew["faction_id"] is None
    assert world.entities[crew["captain_id"]]["tier"] == "MINOR"
    assert ship["owner_type"] == "merchant"
    assert ship["crew_id"] == crew_id
    assert crew["last_event_id"] in {event.id for event in world.event_log}
    assert any(event.type == "CAPTAIN_APPOINTED"
               and event.data.get("appointment") == "recruited_merchant_captain"
               for event in world.event_log)
    assert not any(event.type == "FACTION_FOUNDED"
                   and event.data.get("faction_id") == crew_id
                   for event in world.event_log)
    world.economy.ledger.assert_balanced(world.economy)
    world.crew_ships.validate_world(world)


def test_staggered_actor_recruiter_uses_opportunities_and_real_promotion_deeds():
    world = WorldState.create("raid-smoke")
    world.run_until(30 * 24)

    assert 3 < world.crew_ships._active_actor_ship_count(world, "merchant") < 8
    promotions = world.event_log.by_type("ROOKIE_PROMOTED")
    # Promotions happen only after linked accomplishments, not appointment/acquisition.
    for promotion in promotions:
        assert len(promotion.data["deed_event_ids"]) == 2
        assert all(world.event_log.get(deed).type in {
            "ENCOUNTER_RESOLVED", "MARKET_TRADE", "VOYAGE_ARRIVED",
            "SHIP_CAPTURED", "BATTLE_RESOLVED"}
                   for deed in promotion.data["deed_event_ids"])
    assert not any(world.event_log.get(deed).type in {"CAPTAIN_APPOINTED", "SHIP_ACQUIRED"}
                   for promotion in promotions for deed in promotion.data["deed_event_ids"])
    assert all(type(deadline) is int and deadline >= 0
               for deadline in world.crew_ships.actor_deadlines.values())
    world.to_snapshot()
    restored = WorldState.from_snapshot(world.to_snapshot())
    assert restored.crew_ships.actor_deadlines == world.crew_ships.actor_deadlines
    # Two genuinely profitable sales qualify; synthetic market rows do not.
    deed_world = WorldState.create("promotion-real-deeds")
    island_id = sorted(deed_world.geography.islands)[0]
    merchant_id, created = deed_world.create_merchant(
        "Deed Merchant", island_id, silver=100, capacity_milli=20000)
    deed_world.crew_ships.provision_recruited_merchant(
        deed_world, merchant_id, caused_by=created)
    merchant = deed_world.economy.merchants[merchant_id]
    captain_id = deed_world.crew_ships.crews[
        deed_world.entities[merchant["ship_id"]]["crew_id"]]["captain_id"]
    assert deed_world.entities[captain_id]["tier"] == "MINOR"
    source_id, destination_id = "isle_001", "isle_002"
    for island in deed_world.economy.islands.values():
        island["prices_milli"]["grain"] = 1_000
    deed_world.economy.islands[destination_id]["prices_milli"]["grain"] = 100_000
    for _ in range(2):
        ship = deed_world.entities[merchant["ship_id"]]
        ship["location"] = {"kind": "island", "id": source_id}
        purchase = deed_world.economy.trade(
            deed_world, merchant_id, source_id, "grain", "buy", 5_000,
            caused_by=merchant["last_event_id"])
        ship["location"] = {"kind": "island", "id": destination_id}
        deed_world.economy.trade(
            deed_world, merchant_id, destination_id, "grain", "sell", 5_000,
            caused_by=purchase["id"])
    deed_world.crew_ships._promote_captain_rookie(deed_world, 1)
    latest_promotion = deed_world.event_log.by_type("ROOKIE_PROMOTED")[-1]
    assert len(latest_promotion.data["deed_event_ids"]) == 2
    assert all(deed_world.event_log.get(deed).type in {
        "ENCOUNTER_RESOLVED", "MARKET_TRADE", "VOYAGE_ARRIVED",
        "SHIP_CAPTURED", "BATTLE_RESOLVED"}
               for deed in latest_promotion.data["deed_event_ids"])
    world.run_until(60 * 24)
    assert world.crew_ships._active_actor_ship_count(world, "merchant") >= 3
    actor_factions = {faction_id for faction_id, faction in world.politics.factions.items()
                      if faction["kind"].startswith("actor_")}
    assert all(world.politics.factions[faction_id]["monthly_decisions"] == 0
               for faction_id in actor_factions)
    assert not any(set(key.split("|")) & actor_factions for key in world.politics.treaties)
    world.economy.ledger.assert_balanced(world.economy)
    world.to_snapshot()


def test_actor_budget_recruitment_uses_regional_display_names_not_internal_budget_labels():
    world = WorldState.create("regional-actor-names")
    bands = world.config["world"]["actors"]["ship_bands"]
    for rules in bands.values():
        rules["minimum_per_island_milli"] = 0
    bands["merchant"]["minimum_per_island_milli"] = 1000
    before_ids = set(world.economy.merchants)
    world.crew_ships.actor_deadlines["merchant"] = 30

    world.crew_ships._recruit_actor_bands(world, 30)

    assert world.crew_ships.actor_deadlines["merchant"] == 42
    new_merchants = [merchant for merchant_id, merchant in world.economy.merchants.items()
                     if merchant_id not in before_ids]
    assert len(new_merchants) == 1  # one independently scheduled, resource-backed opportunity
    assert new_merchants[0]["silver"] == world.crew_ships.data["rules"]["starting_silver"]
    for merchant in new_merchants:
        island_name = world.geography.islands[merchant["home_island"]].name
        assert merchant["name"].startswith(f"{island_name} Trading Company ")
        assert "budget" not in merchant["name"].casefold()
        ship = world.entities[merchant["ship_id"]]
        assert "budget" not in ship["name"].casefold()
        crew = world.crew_ships.crews[ship["crew_id"]]
        assert any(event.type == "CREW_FOUNDED"
                   and event.data.get("crew_id") == crew["id"]
                   and event.actors[0]["entity_id"] == crew["captain_id"]
                   for event in world.event_log)

    island_id = new_merchants[0]["home_island"]
    first_name = world.crew_ships._regional_actor_vessel_name(world, island_id, "pirate")
    ship_id, created = world.create_vessel(first_name, island_id)
    world.crew_ships.provision_recruited_vessel(world, ship_id, "pirate", caused_by=created)
    second_name = world.crew_ships._regional_actor_vessel_name(world, island_id, "pirate")
    assert first_name.startswith(f"{world.geography.islands[island_id].name} Corsair Sloop ")
    assert second_name != first_name
    assert "vessel" not in first_name.casefold()


def test_actor_recruitment_waits_for_a_real_local_provisioning_opportunity():
    world = WorldState.create("actor-recruitment-no-stock")
    band_rules = world.config["world"]["actors"]["ship_bands"]
    for rules in band_rules.values():
        rules["minimum_per_island_milli"] = 0
    band_rules["pirate"]["minimum_per_island_milli"] = 1000
    world.crew_ships.actor_deadlines.update({
        "merchant": 100, "pirate": 0, "privateer": 100,
        "marine": 100, "promotion": 100, "legend": 100,
    })
    for island in world.economy.islands.values():
        for good in world.crew_ships.data["classes"]["merchant_sloop"]["starting_supplies"]:
            island["stockpile_milli"][good] = 0
    before_crews = set(world.crew_ships.crews)
    before_ships = {ship_id for ship_id, entity in world.entities.items()
                    if entity.get("kind") == "ship"}

    world.crew_ships._recruit_actor_bands(world, 0)

    assert set(world.crew_ships.crews) == before_crews
    assert {ship_id for ship_id, entity in world.entities.items()
            if entity.get("kind") == "ship"} == before_ships
    assert world.crew_ships.actor_deadlines["pirate"] > 0


def test_opening_actor_provisions_have_world_generation_provenance_and_balance():
    world = WorldState.create("opening-actor-provenance")
    seeded = {event.targets[0]["entity_id"]: event
              for event in world.event_log.by_type("ACTOR_FORCE_SEEDED")}
    assert len(seeded) == 3
    for ship_id, event in seeded.items():
        supply_events = [row for row in world.event_log.by_type("SHIP_SUPPLIED")
                         if any(target.get("entity_id") == ship_id for target in row.targets)]
        assert supply_events
        assert any(world.event_log.get(parent).type == "WORLD_GENERATED"
                   for parent in event.caused_by)
        assert all(row.caused_by for row in supply_events)
    world.economy.ledger.assert_balanced(world.economy)
    world.to_snapshot()
    world.crew_ships.validate_world(world)


def test_pirate_dispatch_records_route_and_cargo_beliefs():
    world = WorldState.create("pirate-belief-dispatch")
    island_id = sorted(world.geography.islands)[0]
    merchant_id, merchant_created = world.create_merchant(
        "Belief Test Merchant", island_id, silver=100, capacity_milli=50000,
        cargo_milli={"grain": 10000})
    world.crew_ships.provision_recruited_merchant(
        world, merchant_id, caused_by=merchant_created)
    merchant = world.economy.merchants[merchant_id]
    pirate_ship_id, created_event = world.create_vessel("Belief Test Raider", island_id)
    pirate_crew_id = world.crew_ships.provision_recruited_vessel(
        world, pirate_ship_id, "pirate", caused_by=created_event)
    assert world.entities[pirate_ship_id]["cargo_capacity_milli"] > 0
    captain_id = world.crew_ships.crews[pirate_crew_id]["captain_id"]
    actual_cargo_value = sum(
        quantity * world.economy.goods[goods_id]["base_price_silver"]
        for goods_id, quantity in merchant["cargo_milli"].items())

    world.crew_ships._dispatch_raiders(world, 1)

    raids = world.event_log.by_type("RAIDER_TARGET_SELECTED")
    assert raids
    target_event = raids[0]
    beliefs = world.decisions.agents[captain_id]["beliefs"]
    cargo_belief = world.decisions.belief(
        captain_id, merchant["ship_id"], "estimated_cargo_value_milli")
    defense_belief = world.decisions.belief(
        captain_id, merchant["ship_id"], "estimated_vessel_strength")
    location_belief = world.decisions.belief(
        captain_id, merchant["ship_id"], "last_known_location")
    patrol_belief = world.decisions.belief(
        captain_id, target_event.location, "estimated_patrol_density_milli")
    observation = world.event_log.get(cargo_belief["source"]["event_id"])
    chart = world.event_log.get(patrol_belief["source"]["event_id"])
    assert cargo_belief["source"]["kind"] == "direct"
    assert defense_belief["source"]["event_id"] == cargo_belief["source"]["event_id"]
    assert defense_belief["confidence_permille"] == 900
    assert location_belief["source"]["event_id"] == observation.id
    assert observation.type == "RAIDER_INTEL_OBSERVED"
    assert chart.type == "RAIDER_CHART_REVIEWED"
    assert observation.actors[0]["entity_id"] == captain_id
    assert observation.targets[0]["entity_id"] == merchant["ship_id"]
    assert observation.data["observation_tick"] == world.clock.tick
    assert observation.data["last_known_location"] == location_belief["value"]
    assert -250 <= observation.data["estimation_error_permille"] <= 250
    assert abs(cargo_belief["value"] - actual_cargo_value) <= actual_cargo_value // 4
    actual_patrol = int(world.geography.routes[target_event.location].patrol_density * 1000)
    assert -250 <= chart.data["estimation_error_permille"] <= 250
    assert abs(patrol_belief["value"] - actual_patrol) <= actual_patrol // 4
    assert any(row["subject_id"] == target_event.location
               and row["attribute"] == "estimated_patrol_density_milli"
               for row in beliefs)
    assert any(row["subject_id"] == target_event.targets[0]["entity_id"]
               and row["attribute"] == "estimated_cargo_value_milli"
               for row in beliefs)
    confidence = min(target_event.data["cargo_confidence_permille"],
                      target_event.data["patrol_confidence_permille"])
    distance_factor = 72000 // (72 + target_event.data["expected_travel_hours"])
    expected_score = (target_event.data["estimated_cargo_value_milli"] * confidence // 1000 * 1000
                      // (1000 + target_event.data["estimated_patrol_density_milli"])
                      * target_event.data["capability_factor_permille"] // 1000
                      * distance_factor // 1000
                      * target_event.data["provision_availability_permille"] // 1000
                      * 1000 // (1000 + target_event.data["active_competing_commitments"] * 500))
    assert target_event.data["belief_score"] == expected_score
    assert target_event.data["active_competing_commitments"] >= 0
    assert target_event.data["provision_availability_permille"] in range(1001)
    assert target_event.data["capability_factor_permille"] in range(500, 1501)
    assert target_event.caused_by.count(observation.id) == 1
    assert target_event.data["last_known_location"] == location_belief["value"]
    assert "observed_cargo_value_milli" not in target_event.data
    assert target_event.targets[0]["entity_id"] in world.entities
    world.economy.ledger.assert_balanced(world.economy)
    world.to_snapshot()


def test_raider_intelligence_is_attributed_bounded_and_observer_specific():
    world = WorldState.create("raider-intelligence-observers")
    home_island = next(iter(sorted(world.geography.islands)))
    merchant_id, merchant_created = world.create_merchant(
        "Observed Cargo Company", home_island, silver=100, capacity_milli=50000,
        cargo_milli={"grain": 10000})
    world.crew_ships.provision_recruited_merchant(
        world, merchant_id, caused_by=merchant_created)
    merchant = world.economy.merchants[merchant_id]
    merchant_ship_id = merchant["ship_id"]
    island_id = world.location_of(merchant_ship_id)["id"]
    captain_ids = []
    for name in ("First Observer", "Second Observer"):
        ship_id, created = world.create_vessel(name, island_id)
        crew_id = world.crew_ships.provision_recruited_vessel(
            world, ship_id, "pirate", caused_by=created)
        captain_ids.append(world.crew_ships.crews[crew_id]["captain_id"])

    for captain_id in captain_ids:
        world.crew_ships._record_raider_vessel_intel(
            world, captain_id, merchant_id, merchant_ship_id, merchant)
    records = [world.decisions.belief(
        captain_id, merchant_ship_id, "estimated_cargo_value_milli")
        for captain_id in captain_ids]
    observations = [world.event_log.get(record["source"]["event_id"]) for record in records]
    actual = sum(quantity * world.economy.goods[goods_id]["base_price_silver"]
                 for goods_id, quantity in merchant["cargo_milli"].items())
    error_bound = world.decisions.ai_data["belief_rules"]["max_distortion_permille"]

    assert records[0]["source"]["kind"] == records[1]["source"]["kind"] == "direct"
    assert records[0]["source"]["event_id"] != records[1]["source"]["event_id"]
    assert observations[0].actors[0]["entity_id"] == captain_ids[0]
    assert observations[1].actors[0]["entity_id"] == captain_ids[1]
    assert all(event.type == "RAIDER_INTEL_OBSERVED" for event in observations)
    assert all(event.data["observation_tick"] == world.clock.tick for event in observations)
    assert all(abs(record["value"] - actual) <= actual * error_bound // 1000
               for record in records)
    assert records[0]["value"] != records[1]["value"]
    defense_records = [world.decisions.belief(
        captain_id, merchant_ship_id, "estimated_vessel_strength")
        for captain_id in captain_ids]
    actual_defense = world.crew_ships.combat_power(world, merchant_ship_id)["total"]
    assert all(record["source"]["kind"] == "direct"
               and record["confidence_permille"] == 900
               and abs(record["value"] - actual_defense)
               <= actual_defense * error_bound // 1000
               for record in defense_records)
    assert defense_records[0]["value"] != defense_records[1]["value"]
    assert all("actual_cargo_value_milli" not in event.data for event in observations)
    replay = WorldState.create("raider-intelligence-observers")
    assert (records[0]["value"], observations[0].data["estimation_error_permille"]) == \
        replay.crew_ships._raider_estimate(
            replay, captain_ids[0], merchant_ship_id, "cargo_value_milli", actual)

    assert world.crew_ships._fresh_raider_belief(records[0], world.clock.tick, 60)
    assert not world.crew_ships._fresh_raider_belief(
        {**records[0], "observed_tick": world.clock.tick - 61}, world.clock.tick, 60)
    assert not world.crew_ships._fresh_raider_belief(
        {**records[0], "confidence_permille": 0}, world.clock.tick, 60)
    snapshot = world.to_snapshot()
    restored = WorldState.from_snapshot(snapshot)
    for captain_id in captain_ids:
        assert restored.decisions.belief(
            captain_id, merchant_ship_id, "estimated_cargo_value_milli") == \
            world.decisions.belief(
                captain_id, merchant_ship_id, "estimated_cargo_value_milli")
    assert restored.event_log.to_jsonl() == world.event_log.to_jsonl()


def test_raiders_do_not_select_merchants_they_have_never_observed():
    world = WorldState.create("raider-no-intel-no-selection")
    merchant_islands = {
        world.location_of(merchant["ship_id"])["id"]
        for merchant in world.economy.merchants.values()
    }
    island_id = next(key for key in sorted(world.geography.islands)
                     if key not in merchant_islands)
    ship_id, created = world.create_vessel("Uninformed Raider", island_id)
    world.crew_ships.provision_recruited_vessel(world, ship_id, "pirate", caused_by=created)
    before = len(world.event_log.by_type("RAIDER_TARGET_SELECTED"))

    world.crew_ships._dispatch_raiders(world, 30)

    assert len(world.event_log.by_type("RAIDER_TARGET_SELECTED")) == before
    captain_id = world.crew_ships.crews[world.entities[ship_id]["crew_id"]]["captain_id"]
    assert world.decisions.belief(
        captain_id,
        next(iter(world.economy.merchants.values()))["ship_id"],
        "estimated_cargo_value_milli") is None
    charted_routes = {
        route.id for route in world.geography.adjacency[island_id]
    }
    captain_route_beliefs = {
        row["subject_id"] for row in world.decisions.agents[captain_id]["beliefs"]
        if row["attribute"] == "estimated_patrol_density_milli"
    }
    assert captain_route_beliefs == charted_routes


def test_raider_target_scoring_penalizes_competition_without_a_target_cap():
    world = WorldState.create("raider-target-competition")
    island_id = sorted(world.geography.islands)[0]
    merchant_id, merchant_event = world.create_merchant(
        "Competed Merchant", island_id, silver=100, capacity_milli=50000,
        cargo_milli={"grain": 20000})
    world.crew_ships.provision_recruited_merchant(
        world, merchant_id, caused_by=merchant_event)
    merchant = world.economy.merchants[merchant_id]
    raider_ids = []
    for name in ("First Competitor", "Second Competitor"):
        ship_id, created = world.create_vessel(name, island_id)
        world.crew_ships.provision_recruited_vessel(world, ship_id, "pirate", caused_by=created)
        raider_ids.append(ship_id)

    world.crew_ships._dispatch_raiders(world, 30)

    selections = [event for event in world.event_log.by_type("RAIDER_TARGET_SELECTED")
                  if event.targets and event.targets[0]["entity_id"] == merchant["ship_id"]]
    assert len(selections) == 2
    assert sorted(event.data["active_competing_commitments"] for event in selections) == [0, 1]
    assert all(event.data["expected_travel_hours"] == 0 for event in selections)
    assert all(event.data["belief_score"] > 0 for event in selections)
    assert len({event.actors[0]["entity_id"] for event in selections}) == 2


def test_raider_pursuit_persists_commitment_and_causally_resolves_contact():
    world = WorldState.create("raider-pursuit-contact")
    island_id = sorted(world.geography.islands)[0]
    merchant_id, merchant_created = world.create_merchant(
        "Pursuit Target", island_id, silver=100, capacity_milli=50000,
        cargo_milli={"grain": 1000})
    world.crew_ships.provision_recruited_merchant(
        world, merchant_id, caused_by=merchant_created)
    target_ship_id = world.economy.merchants[merchant_id]["ship_id"]
    raider_ship_id, created_id = world.create_vessel("Pursuer", island_id)
    crew_id = world.crew_ships.provision_recruited_vessel(
        world, raider_ship_id, "pirate", caused_by=created_id)
    selected = world.event_log.append(
        tick=world.clock.tick, event_type="RAIDER_TARGET_SELECTED",
        location=island_id,
        actors=[{"role": "raider_captain", "entity_id": world.crew_ships.crews[crew_id]["captain_id"]}],
        targets=[{"role": "merchant_vessel", "entity_id": target_ship_id}],
        data={"merchant_id": merchant_id, "route_id": sorted(world.geography.routes)[0]},
        caused_by=[created_id])
    route_id = sorted(world.geography.routes)[0]

    world.crew_ships._commit_raider_pursuit(
        world, world.crew_ships.crews[crew_id], raider_ship_id, merchant_id,
        target_ship_id, route_id, island_id, {"kind": "island", "id": island_id},
        selected.id)

    plan = world.crew_ships.crews[crew_id]["raider_plan"]
    assert plan["status"] == "underway"
    assert plan["target_ship_id"] == target_ship_id
    assert plan["last_known_location"] == {"kind": "island", "id": island_id}
    assert plan["expected_duration_hours"] == 0
    assert set(plan["expected_supplies_milli"]) == {"grain", "water", "rum"}
    assert plan["pursuit_budget_milli"] == {
        good: world.economy.ship_supplies[raider_ship_id][good]
        for good in ("grain", "water", "rum")}

    encounter = world.event_log.append(
        tick=world.clock.tick, event_type="ENCOUNTER_RESOLVED", location=island_id,
        actors=[{"role": "combatant", "entity_id": raider_ship_id},
                {"role": "combatant", "entity_id": target_ship_id}],
        data={"outcome": "mutual_withdrawal", "ships": [raider_ship_id, target_ship_id],
              "damage_event_ids": [], "outcome_event_ids": [], "gunnery_damage": [0, 0]},
        caused_by=[plan["commitment_event_id"]])
    assert world.crew_ships._raider_encounter_outcome(
        world, raider_ship_id, target_ship_id, encounter) == "contact"
    fleeing = world.event_log.append(
        tick=world.clock.tick, event_type="ENCOUNTER_RESOLVED", location=island_id,
        actors=[{"role": "combatant", "entity_id": raider_ship_id},
                {"role": "combatant", "entity_id": target_ship_id}],
        data={"outcome": "one_side_flees", "stances": {
            raider_ship_id: "attack", target_ship_id: "flee"}},
        caused_by=[encounter.id])
    assert world.crew_ships._raider_encounter_outcome(
        world, raider_ship_id, target_ship_id, fleeing) == "escaped"
    captured = world.event_log.append(
        tick=world.clock.tick, event_type="ENCOUNTER_RESOLVED", location=island_id,
        actors=[{"role": "combatant", "entity_id": raider_ship_id},
                {"role": "combatant", "entity_id": target_ship_id}],
        data={"outcome": "ship_captured", "stances": {}}, caused_by=[encounter.id])
    assert world.crew_ships._raider_encounter_outcome(
        world, raider_ship_id, target_ship_id, captured) == "intercepted"
    world.crew_ships.resolve_raider_plans(world, 0, [encounter.id])

    assert plan["status"] == "contact"
    resolution = world.event_log.get(plan["resolution_event_id"])
    assert resolution.type == "RAIDER_PURSUIT_RESOLVED"
    assert resolution.event_class == "routine"
    assert encounter.id in resolution.caused_by
    assert selected.id in resolution.caused_by
    restored = WorldState.from_snapshot(world.to_snapshot())
    assert restored.crew_ships.crews[crew_id]["raider_plan"] == plan


def test_raider_pursuit_aborts_without_provisions_and_expires_once():
    world = WorldState.create("raider-pursuit-abort-expiry")
    origin, destination = sorted(world.geography.islands)[:2]
    merchant_id, merchant_created = world.create_merchant(
        "Remote Pursuit Target", destination, silver=100, capacity_milli=50000,
        cargo_milli={"grain": 1000})
    world.crew_ships.provision_recruited_merchant(
        world, merchant_id, caused_by=merchant_created)
    target_ship_id = world.economy.merchants[merchant_id]["ship_id"]
    raider_ship_id, created_id = world.create_vessel("Unprovisioned Pursuer", origin)
    crew_id = world.crew_ships.provision_recruited_vessel(
        world, raider_ship_id, "pirate", caused_by=created_id)
    for good in ("grain", "water", "rum"):
        world.economy.ship_supplies[raider_ship_id][good] = 0
    world.entities[raider_ship_id]["supplies"] = world.economy.ship_supplies[raider_ship_id]
    selected = world.event_log.append(
        tick=world.clock.tick, event_type="RAIDER_TARGET_SELECTED", location=origin,
        actors=[{"role": "raider_captain", "entity_id": world.crew_ships.crews[crew_id]["captain_id"]}],
        targets=[{"role": "merchant_vessel", "entity_id": target_ship_id}],
        data={"merchant_id": merchant_id}, caused_by=[created_id])

    world.crew_ships._commit_raider_pursuit(
        world, world.crew_ships.crews[crew_id], raider_ship_id, merchant_id,
        target_ship_id, sorted(world.geography.routes)[0], destination,
        {"kind": "island", "id": destination}, selected.id)

    crew = world.crew_ships.crews[crew_id]
    plan = crew["raider_plan"]
    assert plan["status"] == "aborted"
    abort = world.event_log.get(plan["resolution_event_id"])
    assert abort.data["reason"] == "insufficient_pursuit_provisions"
    assert abort.caused_by.count(plan["commitment_event_id"]) == 1

    # A second, provisioned commitment demonstrates finite expiry and idempotence.
    for good in ("grain", "water", "rum"):
        world.economy.ship_supplies[raider_ship_id][good] = 100000
    world.entities[raider_ship_id]["supplies"] = world.economy.ship_supplies[raider_ship_id]
    selected2 = world.event_log.append(
        tick=world.clock.tick, event_type="RAIDER_TARGET_SELECTED", location=origin,
        actors=[{"role": "raider_captain", "entity_id": world.crew_ships.crews[crew_id]["captain_id"]}],
        targets=[{"role": "merchant_vessel", "entity_id": target_ship_id}],
        data={"merchant_id": merchant_id}, caused_by=[abort.id])
    world.crew_ships._commit_raider_pursuit(
        world, crew, raider_ship_id, merchant_id, target_ship_id,
        sorted(world.geography.routes)[0], destination,
        {"kind": "island", "id": destination}, selected2.id)
    plan2 = crew["raider_plan"]
    plan2["deadline_tick"] = world.clock.tick
    before = len(world.event_log.by_type("RAIDER_PURSUIT_RESOLVED"))
    world.crew_ships.resolve_raider_plans(world, 0)
    world.crew_ships.resolve_raider_plans(world, 0)
    assert plan2["status"] == "expired"
    assert len(world.event_log.by_type("RAIDER_PURSUIT_RESOLVED")) == before + 1


def test_raider_pursuit_sails_to_last_known_island_and_persists_voyage():
    world = WorldState.create("raider-pursuit-travel")
    origin = sorted(world.geography.islands)[0]
    route = min(world.geography.adjacency[origin],
                key=lambda row: (row.distance_days, row.id))
    destination = world.geography.other_end(route.id, origin)
    merchant_id, merchant_created = world.create_merchant(
        "Distant Observed Merchant", destination, silver=100, capacity_milli=50000,
        cargo_milli={"grain": 20000})
    world.crew_ships.provision_recruited_merchant(
        world, merchant_id, caused_by=merchant_created)
    merchant = world.economy.merchants[merchant_id]
    target_ship_id = merchant["ship_id"]
    raider_ship_id, raider_created = world.create_vessel("Route Pursuer", origin)
    crew_id = world.crew_ships.provision_recruited_vessel(
        world, raider_ship_id, "pirate", caused_by=raider_created)
    captain_id = world.crew_ships.crews[crew_id]["captain_id"]
    world.crew_ships._record_raider_vessel_intel(
        world, captain_id, merchant_id, target_ship_id, merchant)
    route_id = route.id
    selected = world.event_log.append(
        tick=world.clock.tick, event_type="RAIDER_TARGET_SELECTED",
        location=route_id,
        actors=[{"role": "raider_captain", "entity_id": captain_id}],
        targets=[{"role": "merchant_vessel", "entity_id": target_ship_id}],
        data={"merchant_id": merchant_id, "route_id": route_id},
        caused_by=[merchant["last_event_id"]])

    world.crew_ships._commit_raider_pursuit(
        world, world.crew_ships.crews[crew_id], raider_ship_id, merchant_id,
        target_ship_id, route_id, destination,
        {"kind": "island", "id": destination}, selected.id)

    plan = world.crew_ships.crews[crew_id]["raider_plan"]
    assert plan["status"] == "underway"
    assert plan["expected_duration_hours"] > 0
    assert world.event_log.get(plan["commitment_event_id"]).data["expected_route_ids"]
    assert plan["voyage_event_id"]
    transit = world.travel.transits[raider_ship_id]
    world.run_until(transit["arrival_tick"])

    assert raider_ship_id not in world.travel.transits
    assert world.location_of(raider_ship_id) == {"kind": "island", "id": destination}
    assert world.event_log.get(plan["voyage_event_id"]).type == "VOYAGE_STARTED"
    assert any(event.type == "VOYAGE_ARRIVED"
               and event.actors[0]["entity_id"] == raider_ship_id
               for event in world.event_log)
    # Depending on the deterministic encounter roll or target departure, the
    # plan is either still waiting on ordinary contact detection or terminal.
    assert plan["status"] in {
        "underway", "contact", "intercepted", "aborted", "escaped", "invalidated", "expired",
    }
    world.to_snapshot()


def test_raider_pursuit_invalidates_when_target_loses_its_controller():
    world = WorldState.create("raider-pursuit-invalid-target")
    island_id = sorted(world.geography.islands)[0]
    merchant_id, merchant_created = world.create_merchant(
        "Invalidated Target", island_id, silver=100, capacity_milli=50000,
        cargo_milli={"grain": 20000})
    world.crew_ships.provision_recruited_merchant(
        world, merchant_id, caused_by=merchant_created)
    merchant = world.economy.merchants[merchant_id]
    target_id = merchant["ship_id"]
    raider_id, created = world.create_vessel("Invalidation Raider", island_id)
    crew_id = world.crew_ships.provision_recruited_vessel(
        world, raider_id, "pirate", caused_by=created)
    selected = world.event_log.append(
        tick=world.clock.tick, event_type="RAIDER_TARGET_SELECTED", location=island_id,
        actors=[{"role": "raider_captain", "entity_id": world.crew_ships.crews[crew_id]["captain_id"]}],
        targets=[{"role": "merchant_vessel", "entity_id": target_id}],
        data={"merchant_id": merchant_id}, caused_by=[created])
    route_id = min(world.geography.adjacency[island_id], key=lambda route: route.id).id
    world.crew_ships._commit_raider_pursuit(
        world, world.crew_ships.crews[crew_id], raider_id, merchant_id, target_id,
        route_id, island_id, {"kind": "island", "id": island_id}, selected.id)
    plan = world.crew_ships.crews[crew_id]["raider_plan"]
    world.entities[target_id]["controller_crew_id"] = None

    world.crew_ships.resolve_raider_plans(world, 0)

    assert plan["status"] == "invalidated"
    result = world.event_log.get(plan["resolution_event_id"])
    assert result.data["reason"] == "pursuit_participant_lost_command"
    assert plan["commitment_event_id"] in result.caused_by


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
               for crew_id in starting_crew_ids
               if world.crew_ships.crews[crew_id]["ideology_tags"] == ["merchant", "trade"])
    merchant_endowments = sum(
        event.data.get("starting_silver", 0)
        for event in world.event_log.by_type("MERCHANT_CREATED")
        if event.data.get("starting_source") == "merchant_endowment"
        and event.tick > 0)
    assert world.economy._total_silver() == total_before + merchant_endowments
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
