import copy

import pytest

from sim.core.world import WorldState
from sim.systems.politics import load_politics_data, validate_politics_data
from sim.tools.batch_test import _politics_metrics


def _crews(world):
    return sorted(world.crew_ships.crews)


def _factions(world):
    return sorted(world.politics.factions)


def _merchant_coalition(world):
    return next(faction_id for faction_id, faction in world.politics.factions.items()
                if faction["kind"] != "marine")


def _new_aligned_pirate_crews(world, count=2):
    island_id = next(island_id for island_id in sorted(world.geography.islands)
                     if island_id not in world.politics.island_rulers)
    crew_ids = []
    for index in range(count):
        ship_id, created = world.create_vessel(f"Coalition Test Raider {index}", island_id)
        crew_id = world.crew_ships.provision_recruited_vessel(
            world, ship_id, "pirate", caused_by=created)
        crew = world.crew_ships.crews[crew_id]
        captain = world.entities[crew["captain_id"]]
        captain["traits"]["ambition"] = max(
            captain["traits"]["ambition"],
            world.politics.data["rules"]["faction_founder_min_ambition"])
        captain["stats"]["leadership"] = max(
            captain["stats"]["leadership"],
            world.politics.data["rules"]["faction_founder_min_leadership"])
        crew_ids.append(crew_id)
    return island_id, crew_ids


def test_relations_are_derived_and_trade_dependence_is_evented():
    world = WorldState.create(808)
    first, second = _factions(world)
    relation = world.politics.relation(world, first, second)
    assert set(relation) == {"trust", "fear", "grievance", "kinship", "trade_dependence"}
    assert relation["trust"] == 50
    cause = world.politics.factions[first]["last_event_id"]
    trade = world.politics.record_trade(first, second, 23, world=world, caused_by=cause)
    assert world.politics.relation(world, second, first)["trade_dependence"] == 23
    assert world.event_log.get(trade).caused_by == (cause,)


def test_treaty_transition_is_thresholded_and_cannot_be_sealed_twice():
    world = WorldState.create(809)
    first, second = _factions(world)
    captain_a = world.politics.factions[first]["leader_id"]
    captain_b = world.politics.factions[second]["leader_id"]
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
    first, second = _factions(world)
    captain_a = world.politics.factions[first]["leader_id"]
    captain_b = world.politics.factions[second]["leader_id"]
    cause = world.entities[captain_a]["last_event_id"]
    for _ in range(6):
        world.memories.add_memory(world, captain_a, captain_b, "saved", event_id=cause,
                                  intensity_permille=1000, valence_permille=1000)
        world.memories.add_memory(world, captain_b, captain_a, "saved", event_id=cause,
                                  intensity_permille=1000, valence_permille=1000)
    treaty_event = world.politics.propose_treaty(
        world, first, second, "alliance", caused_by=cause)
    request = world.politics.request_aid(world, first, second, caused_by=treaty_event)
    with pytest.raises(ValueError, match="already open"):
        world.politics.request_aid(world, first, second, caused_by=request)
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


def test_treaty_allows_only_one_open_aid_obligation_per_pair():
    world = WorldState.create("bounded-open-aid-obligation")
    first, second = _factions(world)
    leader_a = world.politics.factions[first]["leader_id"]
    leader_b = world.politics.factions[second]["leader_id"]
    cause = world.entities[leader_a]["last_event_id"]
    for _ in range(6):
        world.memories.add_memory(world, leader_a, leader_b, "saved", event_id=cause,
                                  intensity_permille=1000, valence_permille=1000)
        world.memories.add_memory(world, leader_b, leader_a, "saved", event_id=cause,
                                  intensity_permille=1000, valence_permille=1000)
    treaty_id = world.politics.propose_treaty(
        world, first, second, "alliance", caused_by=cause)
    first_request = world.politics.request_aid(
        world, first, second, caused_by=treaty_id)
    with pytest.raises(ValueError, match="already open"):
        world.politics.request_aid(world, first, second, caused_by=first_request)
    world.politics.resolve_obligation(
        world, second, first_request, fulfilled=True, caused_by=treaty_id)
    second_request = world.politics.request_aid(
        world, first, second, caused_by=first_request)
    treaty = world.politics.treaties["|".join(sorted((first, second)))]
    assert second_request != first_request
    assert sum(item["status"] == "open" for item in treaty["obligations"]) == 1


def test_public_renown_rejects_synthetic_market_rows_without_stat_changes():
    world = WorldState.create("synthetic-market-deed-rejected")
    merchant = world.economy.merchants["merchant_000001"]
    ship = world.entities[merchant["ship_id"]]
    crew = world.crew_ships.crews[ship["controller_crew_id"]]
    captain = world.entities[crew["captain_id"]]
    stats_before = dict(captain["stats"])
    fake_trade = world.event_log.append(
        tick=world.clock.tick, event_type="MARKET_TRADE", location=ship["location"]["id"],
        data={"merchant_id": merchant["id"], "side": "sell", "profit_silver": 999},
        caused_by=[merchant["last_event_id"]])

    assert world.politics.record_public_deed(
        world, captain["id"], fake_trade.id, deed_kind="profitable_trade") is None
    assert captain["reputation"] == {}
    assert captain["stats"] == stats_before
    assert not world.event_log.by_type("PUBLIC_RENOWN_EARNED")


def test_hostile_treaty_breach_can_transition_to_a_single_peaceful_settlement():
    world = WorldState.create("trade-conflict-peace-settlement")
    first, second = _factions(world)
    leader_a = world.politics.factions[first]["leader_id"]
    leader_b = world.politics.factions[second]["leader_id"]
    cause = world.entities[leader_a]["last_event_id"]
    for _ in range(6):
        world.memories.add_memory(world, leader_a, leader_b, "saved", event_id=cause,
                                  intensity_permille=1000, valence_permille=1000)
        world.memories.add_memory(world, leader_b, leader_a, "saved", event_id=cause,
                                  intensity_permille=1000, valence_permille=1000)
    alliance = world.politics.propose_treaty(
        world, first, second, "alliance", caused_by=cause)
    hostile = world.event_log.append(
        tick=world.clock.tick, event_type="ENCOUNTER_RESOLVED", location=None,
        data={"outcome": "ship_captured"}, caused_by=[alliance],
        causes_tags=["hostile_action_fixture"], magnitude=0.5)
    war = world.politics._record_hostile_treaty_breach(
        world, first, second, caused_by=hostile.id)
    assert war is not None
    assert world.event_log.get(war).data["to_state"] == "war"
    breach = world.event_log.by_type("TREATY_BREACHED")[-1]

    peace = world.politics.propose_treaty(
        world, second, first, "nonaggression", caused_by=breach.id)

    assert world.event_log.get(peace).data["from_state"] == "war"
    assert world.event_log.get(peace).data["to_state"] == "nonaggression"
    assert world.politics.treaties["|".join(sorted((first, second)))]["state"] == "nonaggression"
    with pytest.raises(ValueError, match="Invalid treaty transition"):
        world.politics.propose_treaty(world, first, second, "nonaggression", caused_by=peace)
    world.event_log.validate_causality()
    assert WorldState.from_snapshot(world.to_snapshot()).to_snapshot() == world.to_snapshot()


def test_politics_data_rejects_impossible_self_transition():
    data, _ = load_politics_data()
    invalid = copy.deepcopy(data)
    invalid["transitions"]["alliance"]["alliance"] = {
        "min_trust": 50, "max_grievance": 50}
    with pytest.raises(ValueError, match="transition"):
        validate_politics_data(invalid)


def test_seeded_faction_records_home_territory_and_snapshot_resume():
    world = WorldState.create(811)
    assert len(world.crew_ships.crews) == 6
    assert len(world.politics.factions) == 2
    merchant_id = _merchant_coalition(world)
    merchant = world.politics.factions[merchant_id]
    assert len(merchant["member_crew_ids"]) >= 2
    assert merchant_id not in world.crew_ships.crews
    assert merchant["founder_id"] == merchant["leader_id"]
    assert merchant["anchorage_island_id"] in merchant["territory"]
    assert merchant["purpose"]
    assert world.politics.island_rulers[merchant["anchorage_island_id"]] == merchant_id
    assert all(world.crew_ships.crews[crew_id]["faction_id"] == merchant_id
               for crew_id in merchant["member_crew_ids"])
    assert all(crew["faction_id"] in {None, merchant_id}
               for crew in world.crew_ships.crews.values())
    command = world.politics.marine_command
    marine = world.politics.factions[command["faction_id"]]
    assert marine["kind"] == "marine"
    assert marine["leader_id"] == command["commander_id"]
    assert len(command["deputy_ids"]) == 2
    assert world.economy.government_silver == world.politics.data["rules"][
        "government_starting_budget_silver"]
    funding = world.event_log.get(command["budget_funding_event_id"])
    assert funding.type == "GOVERNMENT_BUDGET_FUNDED"
    marine_officers = [command["commander_id"], *command["deputy_ids"]]
    assert all(member_id in world.entities
               and world.entities[member_id].get("home_faction") == command["faction_id"]
               and world.entities[member_id].get("crew_id") is None
               for member_id in marine_officers)
    restored = WorldState.from_snapshot(copy.deepcopy(world.to_snapshot()))
    assert restored.politics.factions == world.politics.factions
    assert restored.politics.island_rulers == world.politics.island_rulers


def test_faction_founding_requires_a_qualified_aligned_coalition_and_keeps_ids_distinct():
    world = WorldState.create("coalition-prerequisites")
    anchorage, crew_ids = _new_aligned_pirate_crews(world)
    one = world.crew_ships.crews[crew_ids[0]]
    founder_id = one["captain_id"]
    causes = [world.crew_ships.crews[crew_id]["last_event_id"] for crew_id in crew_ids]

    with pytest.raises(ValueError, match="at least 2 distinct crews"):
        world.politics.found_faction(
            world, member_crew_ids=crew_ids[:1], founder_id=founder_id,
            anchorage_island_id=anchorage, purpose="mutual defense",
            name="Singleton", kind="pirate_coalition", caused_by=causes)
    with pytest.raises(ValueError, match="derived from its founder"):
        world.politics.found_faction(
            world, member_crew_ids=crew_ids, founder_id=founder_id,
            anchorage_island_id=anchorage, purpose="mutual defense",
            name="Anonymous Compact", kind="pirate_coalition", caused_by=causes)

    event_id = world.politics.found_faction(
        world, member_crew_ids=crew_ids, founder_id=founder_id,
        anchorage_island_id=anchorage, purpose="mutual defense",
        name=f"{world.entities[founder_id]['display_name']}'s Pirate Compact",
        kind="pirate_coalition", caused_by=causes)
    event = world.event_log.get(event_id)
    faction_id = event.data["faction_id"]

    assert faction_id not in world.crew_ships.crews
    assert world.politics.factions[faction_id]["member_crew_ids"] == sorted(crew_ids)
    assert all(world.crew_ships.crews[crew_id]["faction_id"] == faction_id
               for crew_id in crew_ids)
    for crew_id in crew_ids:
        ship_id = world.crew_ships.crews[crew_id]["ship_ids"][0]
        ship = world.entities[ship_id]
        assert ship["legal_owner"] == {"kind": "crew", "id": crew_id}
        assert ship["controller_crew_id"] == crew_id
        assert world.crew_ships.crews[ship["crew_id"]]["faction_id"] == faction_id
    assert event.data["purpose"] == "mutual defense"
    assert event.data["anchorage_island_id"] == anchorage
    assert event.data["founder_display_name"] in event.data["name"]
    assert set(event.data["member_captain_ids"]) == {
        world.crew_ships.crews[crew_id]["captain_id"] for crew_id in crew_ids}
    restored = WorldState.from_snapshot(copy.deepcopy(world.to_snapshot()))
    assert restored.politics.factions[faction_id] == world.politics.factions[faction_id]


def test_faction_founding_rejects_unaligned_members_and_unqualified_founders():
    world = WorldState.create("coalition-invalid-prerequisites")
    anchorage, crew_ids = _new_aligned_pirate_crews(world)
    founder = world.entities[world.crew_ships.crews[crew_ids[0]]["captain_id"]]
    causes = [world.crew_ships.crews[crew_id]["last_event_id"] for crew_id in crew_ids]
    founder["traits"]["ambition"] = 0
    with pytest.raises(ValueError, match="ambition and leadership thresholds"):
        world.politics.found_faction(
            world, member_crew_ids=crew_ids, founder_id=founder["id"],
            anchorage_island_id=anchorage, purpose="mutual defense",
            name="Invalid Founder", kind="pirate_coalition", caused_by=causes)
    founder["traits"]["ambition"] = world.politics.data["rules"][
        "faction_founder_min_ambition"]
    world.crew_ships.crews[crew_ids[1]]["ideology_tags"] = ["merchant"]
    with pytest.raises(ValueError, match="share at least one alignment"):
        world.politics.found_faction(
            world, member_crew_ids=crew_ids, founder_id=founder["id"],
            anchorage_island_id=anchorage, purpose="mutual defense",
            name=f"{founder['display_name']}'s Unaligned Coalition",
            kind="pirate_coalition", caused_by=causes)


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
    faction_id = _merchant_coalition(world)
    faction = world.politics.factions[faction_id]
    former_leader = faction["leader_id"]
    cause = world.entities[former_leader]["last_event_id"]
    death = world.characters.kill_character(
        world, former_leader, cause="assassination", caused_by=cause, replace_majors=False)
    world.politics._monthly_decision(world, faction_id)
    assert faction["leader_id"] != former_leader
    succession = world.event_log.by_type("FACTION_SUCCESSION")[-1]
    assert succession.caused_by
    assert death in world.event_log.get(succession.caused_by[0]).caused_by


def test_world_day_loop_runs_and_snapshots_monthly_faction_politics():
    world = WorldState.create(814)
    world.politics.data["rules"]["monthly_diplomacy_basis_points"] = 10000
    world.run_until(30 * 24)
    assert world.politics.last_updated_day == 30
    assert all(faction["monthly_decisions"] == 1
               for faction in world.politics.factions.values())
    assert len(world.event_log.by_type("FACTION_MONTHLY_DECISION")) == 1
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
    crew_id = _merchant_coalition(world)
    marine_id = world.politics.marine_command["faction_id"]
    captain_id = world.politics.factions[crew_id]["leader_id"]
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
    faction_id = world.crew_ships.crews[crew_id]["faction_id"]
    assert faction_id is not None
    island_id = world.politics.factions[faction_id]["territory"][0]
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
    response = world.event_log.get(assignment["event_id"])
    assert response.actors[0]["entity_id"] == world.politics.marine_command["faction_id"]
    assert response.data["response"] == "fleet"
    assert response.targets[0]["entity_id"] == crew_id
    assert response.location == island_id
    assert response.data["island_id"] == island_id
    assert world.economy.islands[island_id]["government_response_silver"] >= spend
    world.to_snapshot()
    heat_before = world.politics.crew_heat[crew_id]
    world.politics.data["rules"]["heat_daily_decay"] = 2
    world.politics.update_day(1, world)
    assert world.politics.crew_heat[crew_id] == heat_before - 2


def test_marine_response_does_not_fund_a_target_without_an_actionable_faction_post():
    world = WorldState.create(821)
    island_id = next(island_id for island_id in sorted(world.geography.islands)
                     if island_id not in world.politics.island_rulers)
    ship_id, created = world.create_vessel("Unaligned Test Cutter", island_id)
    crew_id = world.crew_ships.provision_recruited_vessel(
        world, ship_id, "pirate", caused_by=created)
    captain_id = world.crew_ships.crews[crew_id]["captain_id"]
    world.politics.data["rules"]["heat_bounty_threshold"] = 1
    world.politics.data["rules"]["heat_hunter_threshold"] = 2
    world.politics.data["rules"]["heat_fleet_threshold"] = 3
    cause = world.crew_ships.crews[crew_id]["last_event_id"]
    world.politics._emit_heat(world, entity_id=captain_id, crew_id=crew_id,
                              amount=5, caused_by=cause, reason="fixture")
    reserve = world.economy.government_silver

    world.politics._allocate_government_response(world)

    assert crew_id not in world.politics.response_assignments
    assert world.economy.government_silver == reserve
    assert not world.event_log.by_type("GOVERNMENT_RESPONSE_FUNDED")


def test_crew_ship_loss_does_not_dissolve_the_surviving_coalition():
    world = WorldState.create(820)
    crew_id = sorted(world.crew_ships.crews)[0]
    faction_id = world.crew_ships.crews[crew_id]["faction_id"]
    assert faction_id is not None
    ship_id = world.crew_ships.crews[crew_id]["ship_ids"][0]
    home = world.politics.factions[faction_id]["territory"][0]
    cause = world.crew_ships.crews[crew_id]["last_event_id"]
    sink_event_id = world.encounters._sink_ship(world, ship_id, cause)
    assert not world.event_log.by_type("FACTION_DISSOLVED")
    assert faction_id in world.politics.factions
    assert faction_id not in world.politics.archived_factions
    assert world.politics.island_rulers[home] == faction_id
    assert world.crew_ships.crews[crew_id]["faction_id"] == faction_id
    assert world.event_log.get(sink_event_id).type == "SHIP_SUNK"
    world.to_snapshot()


def test_coalition_dissolution_records_remaining_members_assets_debts_and_no_successor():
    world = WorldState.create("coalition-dissolution-ledger")
    faction_id = _merchant_coalition(world)
    faction = world.politics.factions[faction_id]
    member_ids = list(faction["member_crew_ids"])
    assert len(member_ids) == 3
    first, second, survivor = member_ids
    survivor_treasury = world.economy.crew_accounts[survivor]["treasury_silver"]
    survivor_arrears = world.crew_ships.crews[survivor]["arrears_silver"]
    first_departure = world.politics.on_crew_dissolved(
        world, first, caused_by=faction["last_event_id"], reason="employment")
    assert first_departure is not None
    assert faction_id in world.politics.factions
    assert world.crew_ships.crews[second]["faction_id"] == faction_id
    second_departure = world.politics.on_crew_dissolved(
        world, second, caused_by=first_departure, reason="employment")

    assert faction_id not in world.politics.factions
    assert faction_id in world.politics.archived_factions
    dissolved = world.event_log.get(second_departure)
    assert dissolved.type == "FACTION_DISSOLVED"
    assert list(dissolved.data["member_crew_ids"]) == [survivor]
    assert list(dissolved.data["member_character_ids"]) == sorted(
        world.crew_ships.crews[survivor]["roster"])
    assert list(dissolved.data["ship_ids"]) == sorted(
        ship_id for ship_id in world.crew_ships.crews[survivor]["ship_ids"]
        if ship_id in world.entities)
    assert dissolved.data["crew_treasury_silver"] == survivor_treasury
    assert dissolved.data["crew_arrears_silver"] == survivor_arrears
    assert dissolved.data["successor_faction_id"] is None
    assert list(dissolved.data["successor_crew_ids"]) == [survivor]
    assert world.crew_ships.crews[survivor]["faction_id"] is None
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
    first, second = _factions(worlds[0])
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
