import copy

import pytest

from sim.core.world import WorldState
from sim.systems.politics import load_politics_data, validate_politics_data
from sim.tools.batch_test import _politics_metrics


def _crews(world):
    return sorted(world.crew_ships.crews)


def test_relations_are_derived_and_trade_dependence_is_evented():
    world = WorldState.create(808)
    first, second, third = _crews(world)
    relation = world.politics.relation(world, first, second)
    assert set(relation) == {"trust", "fear", "grievance", "kinship", "trade_dependence"}
    assert relation["trust"] == 50
    cause = world.crew_ships.crews[first]["last_event_id"]
    trade = world.politics.record_trade(first, second, 23, world=world, caused_by=cause)
    assert world.politics.relation(world, second, first)["trade_dependence"] == 23
    assert world.politics.relation(world, first, third)["trade_dependence"] == 0
    assert world.event_log.get(trade).caused_by == (cause,)


def test_treaty_transition_is_thresholded_and_cannot_be_sealed_twice():
    world = WorldState.create(809)
    first, second, _ = _crews(world)
    captain_a = world.crew_ships.crews[first]["captain_id"]
    captain_b = world.crew_ships.crews[second]["captain_id"]
    cause = world.entities[captain_a]["last_event_id"]
    with pytest.raises(ValueError, match="thresholds"):
        world.politics.propose_treaty(world, first, second, "alliance", caused_by=cause)

    # Repeated event-attributed gratitude derives enough mutual trust for an alliance.
    for _ in range(6):
        world.memories.add_memory(world, captain_a, captain_b, "saved", event_id=cause,
                                  intensity_permille=1000, valence_permille=1000)
        world.memories.add_memory(world, captain_b, captain_a, "saved", event_id=cause,
                                  intensity_permille=1000, valence_permille=1000)
    assert world.politics.relation(world, first, second)["trust"] >= 70
    event_id = world.politics.propose_treaty(
        world, first, second, "alliance", caused_by=cause)
    treaty = world.politics.treaties["|".join(sorted((first, second)))]
    assert treaty["state"] == "alliance"
    assert treaty["terms"] == ["mutual_defense", "no_hostile_action"]
    with pytest.raises(ValueError, match="Invalid treaty transition"):
        world.politics.propose_treaty(world, second, first, "alliance", caused_by=event_id)
    assert len(world.event_log.by_type("TREATY_TRANSITION")) == 1


def test_treaty_obligation_breach_is_single_use_and_damages_institutional_relation():
    world = WorldState.create(810)
    first, second, _ = _crews(world)
    captain_a = world.crew_ships.crews[first]["captain_id"]
    captain_b = world.crew_ships.crews[second]["captain_id"]
    cause = world.entities[captain_a]["last_event_id"]
    for _ in range(6):
        world.memories.add_memory(world, captain_a, captain_b, "saved", event_id=cause,
                                  intensity_permille=1000, valence_permille=1000)
        world.memories.add_memory(world, captain_b, captain_a, "saved", event_id=cause,
                                  intensity_permille=1000, valence_permille=1000)
    treaty_event = world.politics.propose_treaty(
        world, first, second, "alliance", caused_by=cause)
    request = world.politics.request_aid(world, first, second, caused_by=treaty_event)
    amendment = world.politics.propose_treaty(
        world, second, first, "nonaggression", caused_by=request)
    treaty = world.politics.treaties["|".join(sorted((first, second)))]
    assert treaty["event_id"] == amendment
    assert treaty["obligations"][0]["event_id"] == request
    before = world.politics.relation(world, first, second)
    breach = world.politics.resolve_obligation(
        world, second, request, fulfilled=False, caused_by=amendment)
    after = world.politics.relation(world, first, second)
    assert world.event_log.get(breach).type == "TREATY_BREACHED"
    assert after["trust"] < before["trust"]
    assert after["grievance"] > before["grievance"]
    with pytest.raises(ValueError, match="already resolved"):
        world.politics.resolve_obligation(
            world, second, request, fulfilled=False, caused_by=amendment)
    restored = WorldState.from_snapshot(copy.deepcopy(world.to_snapshot()))
    assert restored.politics.treaties == world.politics.treaties


def test_politics_data_rejects_impossible_self_transition():
    data, _ = load_politics_data()
    invalid = copy.deepcopy(data)
    invalid["transitions"]["alliance"]["alliance"] = {
        "min_trust": 50, "max_grievance": 50}
    with pytest.raises(ValueError, match="transition"):
        validate_politics_data(invalid)


def test_seeded_faction_records_home_territory_and_snapshot_resume():
    world = WorldState.create(811)
    assert len(world.crew_ships.crews) == 3
    assert len(world.politics.factions) == 4
    for faction_id, crew in world.crew_ships.crews.items():
        faction = world.politics.factions[faction_id]
        assert faction["leader_id"] == crew["captain_id"]
        assert len(faction["territory"]) == 1
        assert world.politics.island_rulers[faction["territory"][0]] == faction_id
        assert crew["faction_id"] == faction_id
    command = world.politics.marine_command
    marine = world.politics.factions[command["faction_id"]]
    assert marine["kind"] == "marine"
    assert marine["leader_id"] == command["commander_id"]
    assert len(command["deputy_ids"]) == 2
    restored = WorldState.from_snapshot(copy.deepcopy(world.to_snapshot()))
    assert restored.politics.factions == world.politics.factions
    assert restored.politics.island_rulers == world.politics.island_rulers


def test_monthly_faction_decisions_are_deterministic_and_claim_only_adjacent_unruled_islands():
    first_world = WorldState.create(812)
    second_world = WorldState.create(812)
    for world in (first_world, second_world):
        world.politics.data["rules"]["expansion_base_basis_points"] = 10000
        for faction_id in sorted(world.politics.factions):
            world.politics._monthly_decision(world, faction_id)
    first_claims = [event.to_record() for event in first_world.event_log.by_type("TERRITORY_CLAIMED")]
    second_claims = [event.to_record() for event in second_world.event_log.by_type("TERRITORY_CLAIMED")]
    assert first_claims == second_claims
    assert first_claims
    for event in first_world.event_log.by_type("TERRITORY_CLAIMED"):
        faction_id = event.data["faction_id"]
        island_id = event.targets[0]["entity_id"]
        assert first_world.politics.island_rulers[island_id] == faction_id
        assert event.caused_by
        founded = first_world.event_log.get(
            first_world.politics.factions[faction_id]["founded_event_id"])
        home = founded.data["home_island"]
        assert first_world.geography.shortest_path(home, island_id)


def test_faction_leadership_succession_follows_causal_character_death():
    world = WorldState.create(813)
    faction_id = _crews(world)[0]
    faction = world.politics.factions[faction_id]
    former_leader = faction["leader_id"]
    cause = world.entities[former_leader]["last_event_id"]
    death = world.characters.kill_character(
        world, former_leader, cause="assassination", caused_by=cause, replace_majors=False)
    world.politics._monthly_decision(world, faction_id)
    assert faction["leader_id"] != former_leader
    succession = world.event_log.by_type("FACTION_SUCCESSION")[-1]
    assert succession.caused_by == (world.crew_ships.crews[faction_id]["last_event_id"],)
    assert death in world.event_log.get(succession.caused_by[0]).caused_by


def test_world_day_loop_runs_and_snapshots_monthly_faction_politics():
    world = WorldState.create(814)
    world.run_until(30 * 24)
    assert world.politics.last_updated_day == 30
    assert all(faction["monthly_decisions"] == 1
               for faction in world.politics.factions.values())
    assert len(world.event_log.by_type("FACTION_MONTHLY_DECISION")) == 3
    assert len(world.event_log.by_type("MARINE_COMMAND_REVIEWED")) == 1
    assert world.event_log.by_type("TREATY_TRANSITION")
    restored = WorldState.from_snapshot(copy.deepcopy(world.to_snapshot()))
    assert restored.politics.last_updated_day == 30
    assert restored.politics.factions == world.politics.factions


def test_marine_command_challenge_is_seeded_and_evented():
    first_world = WorldState.create(815)
    second_world = WorldState.create(815)
    for world in (first_world, second_world):
        world.politics.data["rules"]["marine_challenge_base_basis_points"] = 10000
        world.politics.data["rules"]["marine_challenge_success_base_basis_points"] = 20000
        marine_id = world.politics.marine_command["faction_id"]
        world.politics._monthly_decision(world, marine_id)
    first_command = first_world.politics.marine_command
    second_command = second_world.politics.marine_command
    assert first_command == second_command
    changed = first_world.event_log.by_type("MARINE_COMMANDER_CHANGED")
    assert changed
    assert first_world.politics.factions[first_command["faction_id"]]["leader_id"] \
        == first_command["commander_id"]
    challenge = first_world.event_log.get(changed[-1].caused_by[0])
    assert challenge.type == "MARINE_COMMAND_CHALLENGE"
    first_world.politics.validate_world(first_world)


def test_marine_government_faction_can_form_a_valid_treaty_with_crew_faction():
    world = WorldState.create(817)
    crew_id = _crews(world)[0]
    marine_id = world.politics.marine_command["faction_id"]
    captain_id = world.crew_ships.crews[crew_id]["captain_id"]
    cause = world.entities[captain_id]["last_event_id"]
    treaty_event = world.politics.propose_treaty(
        world, marine_id, crew_id, "nonaggression", caused_by=cause)
    assert list(world.event_log.get(treaty_event).data["factions"]) == sorted((marine_id, crew_id))
    restored = WorldState.from_snapshot(copy.deepcopy(world.to_snapshot()))
    assert restored.politics.treaties == world.politics.treaties


def test_marine_command_succession_follows_commander_death():
    world = WorldState.create(816)
    command = world.politics.marine_command
    marine_id = command["faction_id"]
    deceased = command["commander_id"]
    cause = world.entities[deceased]["last_event_id"]
    death = world.characters.kill_character(
        world, deceased, cause="combat", caused_by=cause, replace_majors=False)
    world.politics._monthly_decision(world, marine_id)
    assert command["commander_id"] != deceased
    succession = [event for event in world.event_log.by_type("FACTION_SUCCESSION")
                  if event.data.get("institution") == "marine_command"][-1]
    assert death in succession.caused_by
    world.politics.validate_world(world)


def test_heat_sets_and_raises_bounty_then_claims_only_from_finite_government_funds():
    world = WorldState.create(818)
    world.politics.data["rules"]["heat_bounty_threshold"] = 10
    world.politics.data["rules"]["heat_hunter_threshold"] = 20
    world.politics.data["rules"]["heat_fleet_threshold"] = 30
    crews = sorted(world.crew_ships.crews)
    target_crew, winner_crew = crews[:2]
    target_captain = world.crew_ships.crews[target_crew]["captain_id"]
    winner_captain = world.crew_ships.crews[winner_crew]["captain_id"]
    cause = world.crew_ships.crews[target_crew]["last_event_id"]
    initial_total = world.economy._total_silver()
    reserve = world.economy.government_silver
    world.politics._emit_heat(world, entity_id=target_captain, crew_id=target_crew,
                              amount=40, caused_by=cause, reason="government_asset_raid")
    assert world.politics.crew_bounties[target_crew]["amount"] == 650
    assert world.event_log.by_type("HEAT_THRESHOLD_CROSSED")
    paid_event_id = world.politics.on_combat_resolved(
        world, winner_crew_id=winner_crew, loser_crew_id=target_crew,
        outcome="ship_captured", winner_captain_id=winner_captain,
        loser_captain_id=target_captain, caused_by=cause)
    paid = world.event_log.get(paid_event_id)
    assert paid.type == "BOUNTY_PAID"
    assert paid.data["silver"] <= reserve
    response_spend = sum(event.data["silver"] for event in
                         world.event_log.by_type("GOVERNMENT_RESPONSE_FUNDED"))
    assert world.economy.government_silver == reserve - paid.data["silver"] - response_spend
    assert world.economy._total_silver() == initial_total
    assert world.crew_ships.crews[winner_crew]["treasury_silver"] >= paid.data["silver"]
    world.economy.ledger.assert_balanced(world.economy)
    world.to_snapshot()


def test_heat_response_allocates_finite_funds_into_garrison_and_decays():
    world = WorldState.create(819)
    world.politics.data["rules"]["heat_bounty_threshold"] = 10
    world.politics.data["rules"]["heat_hunter_threshold"] = 20
    world.politics.data["rules"]["heat_fleet_threshold"] = 30
    crew_id = sorted(world.crew_ships.crews)[0]
    captain_id = world.crew_ships.crews[crew_id]["captain_id"]
    cause = world.crew_ships.crews[crew_id]["last_event_id"]
    island_id = world.politics.factions[crew_id]["territory"][0]
    world.politics._emit_heat(world, entity_id=captain_id, crew_id=crew_id,
                              amount=35, caused_by=cause, reason="protected_route_raid")
    reserve_before = world.economy.government_silver
    response_reserve_before = world.economy.islands[island_id]["government_response_silver"]
    world.politics._allocate_government_response(world)
    assignment = world.politics.response_assignments[crew_id]
    spend = assignment["silver_spent"]
    assert assignment["level"] == "fleet"
    assert world.economy.government_silver == reserve_before - spend
    assert world.economy.islands[island_id]["government_response_silver"] \
        == response_reserve_before + spend
    assert world.economy._total_silver() == world.economy.ledger.opening_silver + \
        world.economy.ledger.silver_sources - world.economy.ledger.silver_sinks
    assert _politics_metrics(world)["heat_responses_have_threshold_ancestors"]
    world.to_snapshot()
    heat_before = world.politics.crew_heat[crew_id]
    world.politics.data["rules"]["heat_daily_decay"] = 2
    world.politics.update_day(1, world)
    assert world.politics.crew_heat[crew_id] == heat_before - 2


def test_crew_ship_loss_dissolves_and_archives_its_faction_causally():
    world = WorldState.create(820)
    crew_id = sorted(world.crew_ships.crews)[0]
    ship_id = world.crew_ships.crews[crew_id]["ship_ids"][0]
    home = world.politics.factions[crew_id]["territory"][0]
    cause = world.crew_ships.crews[crew_id]["last_event_id"]
    sink_event_id = world.encounters._sink_ship(world, ship_id, cause)
    dissolution = world.event_log.by_type("FACTION_DISSOLVED")[-1]
    assert dissolution.caused_by == (sink_event_id,)
    assert dissolution.data["faction_id"] == crew_id
    assert crew_id not in world.politics.factions
    assert crew_id in world.politics.archived_factions
    assert home not in world.politics.island_rulers
    world.to_snapshot()


def test_monthly_diplomacy_forms_alliances_and_hostile_action_causes_betrayal_and_war():
    worlds = [WorldState.create(821), WorldState.create(821)]
    for world in worlds:
        world.politics.data["rules"]["monthly_diplomacy_basis_points"] = 10000
        for _ in range(4):
            world.politics._monthly_diplomacy(world)
    assert worlds[0].event_log.to_records() == worlds[1].event_log.to_records()
    alliance_events = [event for event in worlds[0].event_log.by_type("TREATY_TRANSITION")
                       if event.data["to_state"] == "alliance"]
    assert alliance_events
    first, second = sorted(worlds[0].crew_ships.crews)[:2]
    cause = worlds[0].event_log.by_type("DIPLOMATIC_EXCHANGE")[-1].id
    combat = worlds[0].event_log.append(
        tick=worlds[0].clock.tick, event_type="ENCOUNTER_RESOLVED",
        data={"outcome": "ship_captured"}, caused_by=[cause],
        causes_tags=["test_combat"], magnitude=0.5)
    transition = worlds[0].politics._record_hostile_treaty_breach(
        worlds[0], first, second, caused_by=combat.id)
    breach = worlds[0].event_log.by_type("TREATY_BREACHED")[-1]
    assert breach.data["reason"] == "hostile_combat"
    assert transition and worlds[0].event_log.get(transition).data["to_state"] == "war"
    pair_alliances = [event for event in alliance_events
                      if list(event.data["factions"]) == sorted((first, second))]
    assert len(pair_alliances) == 1
    metrics = _politics_metrics(worlds[0])
    assert metrics["alliances_formed"] > 0
    assert metrics["wars_declared"] > 0
    assert metrics["hostile_treaty_betrayals"] == 1
    assert metrics["hostile_betrayals_causal"]
    assert not metrics["duplicate_alliance_events"]
    assert metrics["treaty_transition_chain_valid"]
    worlds[0].to_snapshot()
