import copy

from sim.tools.coherence_audit import audit_event_records, audit_faction_membership_transitions
from sim.core.world import WorldState
from sim.tools.coherence_run import _census


def _event(event_id, tick, event_type, *, data=None, actors=(), targets=(), caused_by=(),
           event_class="narrative"):
    return {
        "id": event_id,
        "tick": tick,
        "type": event_type,
        "class": event_class,
        "data": data or {},
        "actors": list(actors),
        "targets": list(targets),
        "caused_by": list(caused_by),
    }


def _part(entity_id, role):
    return {"entity_id": entity_id, "role": role}


def test_faction_membership_audit_replays_found_join_departure_and_dissolution_intervals():
    records = [
        _event("founded", 0, "FACTION_FOUNDED", data={
            "faction_id": "faction_a", "member_crew_ids": ["crew_a", "crew_b"]}),
        _event("joined", 10, "FACTION_MEMBER_JOINED", data={
            "faction_id": "faction_a", "crew_id": "crew_c"}),
        _event("departed", 20, "FACTION_MEMBER_DEPARTED", data={
            "faction_id": "faction_a", "crew_id": "crew_b"}),
        _event("dissolved", 30, "FACTION_DISSOLVED", data={
            "faction_id": "faction_a", "member_crew_ids": ["crew_a", "crew_c"]}),
    ]

    report = audit_faction_membership_transitions(records)

    assert report["errors"] == []
    assert report["active_member_crew_ids_by_faction"]["faction_a"] == []
    assert report["transitions"] == 6
    assert report["membership_intervals_by_crew"]["crew_b"][0]["left_event_id"] == "departed"
    assert report["membership_intervals_by_crew"]["crew_c"][0]["left_event_id"] == "dissolved"


def test_faction_membership_audit_flags_nonmember_departure():
    report = audit_faction_membership_transitions([
        _event("founded", 0, "FACTION_FOUNDED", data={
            "faction_id": "faction_a", "member_crew_ids": ["crew_a", "crew_b"]}),
        _event("departed", 1, "FACTION_MEMBER_DEPARTED", data={
            "faction_id": "faction_a", "crew_id": "crew_c"}),
    ])
    assert report["errors"][0]["error"] == "crew departed a faction it did not belong to"


def test_world_coalition_history_is_exposed_by_integrated_audit():
    world = WorldState.create("coalition-integrated-audit")
    report = audit_event_records([event.to_record() for event in world.event_log])
    faction_ledger = report["faction_membership_ledger"]
    merchant_id = next(fid for fid, faction in world.politics.factions.items()
                       if faction["kind"] != "marine")
    assert faction_ledger["errors"] == []
    assert faction_ledger["active_member_crew_ids_by_faction"][merchant_id] == sorted(
        world.politics.factions[merchant_id]["member_crew_ids"])
    assert report["faction_authority_ledger"]["coalition_foundings_checked"] == 1
    assert report["faction_authority_ledger"]["errors"] == []
    policy = report["event_classification_policy"]
    assert policy["ENCOUNTER_RESOLVED"]["class"] == "outcome-dependent"
    assert "material consequence" in policy["FEUD_RAID_ATTEMPTED"]["reason"]


def test_faction_authority_audit_rejects_wrong_historical_member_captains():
    world = WorldState.create("coalition-authority-audit")
    rows = [event.to_record() for event in world.event_log]
    founding = next(row for row in rows if row["type"] == "FACTION_FOUNDED"
                    and row["data"].get("member_crew_ids"))
    founding["data"]["member_captain_ids"][0] = "character_never_captained_a_member_crew"

    report = audit_event_records(rows)["faction_authority_ledger"]

    assert report["coalition_foundings_checked"] == 0
    assert report["errors"]
    assert report["errors"][0]["event_id"] == founding["id"]


def test_capture_audit_tracks_actual_event_time_owner_and_flags_stale_previous_owner():
    records = [
        _event("e1", 0, "CREW_FOUNDED", data={"crew_id": "crew_a", "ship_id": "ship_a",
                                                "owner_type": "merchant", "roster": ["person_a"]}),
        _event("merchant_a", 0, "MERCHANT_CREATED", data={"ship_id": "ship_a"}),
        _event("e2", 0, "CREW_FOUNDED", data={"crew_id": "crew_b", "ship_id": "ship_b",
                                                "owner_type": "pirate"}),
        _event("e3", 24, "ENCOUNTER_RESOLVED", data={"ships": ["ship_a", "ship_b"],
                                                        "outcome": "surrender"}),
        _event("e4", 24, "SHIP_CAPTURED", data={"previous_crew_id": "crew_a",
                                                  "new_crew_id": "crew_b"},
               targets=[_part("ship_a", "captured_ship")], caused_by=["e3"],
               event_class="structural"),
        _event("e5", 24, "SHIP_ACQUIRED", data={"method": "capture", "new_crew_id": "crew_b"},
               targets=[_part("ship_a", "acquired_ship")], caused_by=["e4"],
               event_class="structural"),
        _event("e6", 48, "SHIP_CAPTURED", data={"previous_crew_id": "crew_a",
                                                  "new_crew_id": "crew_b"},
               targets=[_part("ship_a", "captured_ship")], caused_by=["e3"],
               event_class="structural"),
    ]

    report = audit_event_records(records)

    assert report["capture_owner_chain_mismatches"] == [{
        "ship_id": "ship_a", "event_id": "e6", "tick": 48,
        "expected_previous_crew_id": "crew_b", "reported_previous_crew_id": "crew_a",
        "prior_capture_event_id": "e4",
    }]
    assert [row["event_type"] for row in report["recorded_ship_control_history"]["ship_a"]] == [
        "CREW_FOUNDED", "SHIP_CAPTURED", "SHIP_ACQUIRED", "SHIP_CAPTURED"]
    assert report["recorded_person_membership_history"]["person_a"][0]["crew_id"] == "crew_a"
    assert report["events_by_class_and_type"]["structural"]["SHIP_CAPTURED"]["count"] == 2
    assert report["merchant_loss_incidents"][0]["participant_owner_types"] == {
        "ship_a": "merchant", "ship_b": "pirate"}


def test_org_audit_distinguishes_any_post_dissolution_actor_row_from_commands():
    records = [
        _event("dissolve", 24, "FACTION_DISSOLVED", data={"faction_id": "crew_x"},
               targets=[_part("crew_x", "former_faction")], event_class="structural"),
        _event("supplies", 25, "SHIP_SUPPLIES_CONSUMED", actors=[_part("crew_x", "crew")],
               event_class="routine"),
        _event("capture", 26, "FACTION_MONTHLY_DECISION",
               actors=[_part("crew_x", "faction_command")],
               targets=[_part("ship_y", "captured_ship")], data={"previous_crew_id": "crew_y",
                                                                   "new_crew_id": "crew_x"}),
    ]

    report = audit_event_records(records)

    assert [row["event_id"] for row in report["post_dissolution_actor_actions"]] == [
        "supplies", "capture"]
    assert [row["event_id"] for row in report["post_dissolution_org_commands"]] == ["capture"]


def test_org_audit_detects_indirect_commands_without_misclassifying_crew_activity():
    records = [
        _event("dissolve", 24, "FACTION_DISSOLVED", data={"faction_id": "faction_x"},
               targets=[_part("faction_x", "former_faction")]),
        _event("crew-work", 25, "SHIP_SUPPLIES_CONSUMED",
               actors=[_part("faction_x", "crew")]),
        _event("claim", 26, "TERRITORY_CLAIMED",
               actors=[_part("captain_x", "faction_leader")],
               data={"faction_id": "faction_x"}),
    ]

    report = audit_event_records(records)

    assert [row["event_id"] for row in report["post_dissolution_actor_actions"]] == [
        "crew-work"]
    assert len(report["post_dissolution_org_commands"]) == 1
    command = report["post_dissolution_org_commands"][0]
    assert command["event_id"] == "claim"
    assert command["actor_role"] == "indirect_command"
    assert command["attribution"] == "command event faction reference"


def test_census_distinguishes_uncrewed_hulls_from_dangling_crew_links():
    world = WorldState.create("ownership-census")
    uncrewed_ship_id, _ = world.create_vessel("Unclaimed", "isle_001")
    dangling_ship_id = next(ship_id for ship_id, row in world.entities.items()
                            if row.get("kind") == "ship")
    world.entities[dangling_ship_id]["crew_id"] = "missing_crew"

    census = _census(world, 0)

    assert census["ships"]["ships_without_valid_crew"] == [dangling_ship_id]
    assert uncrewed_ship_id in census["ships"]["explicitly_uncrewed_hulls"]
    assert uncrewed_ship_id not in census["ships"]["ships_without_valid_crew"]


def test_census_flags_dissolved_crew_ship_title_controller_and_ownership_event():
    world = WorldState.create("dissolved-owner-census")
    ship_id = next(ship_id for ship_id, row in world.entities.items()
                   if row.get("kind") == "ship")
    ship = world.entities[ship_id]
    ship["legal_owner"] = {"kind": "crew", "id": "dissolved_crew"}
    ship["controller_crew_id"] = "dissolved_crew"
    ship["ownership_event_id"] = "missing_ownership_event"

    ownership = _census(world, 0)["ships"]

    assert ownership["ships_with_invalid_legal_owner"] == [{
        "ship_id": ship_id, "owner": {"kind": "crew", "id": "dissolved_crew"},
        "error": "owner crew is not active",
    }]
    assert ownership["ships_with_invalid_controller"] == [{
        "ship_id": ship_id, "controller_crew_id": "dissolved_crew",
        "error": "controller crew is not active",
    }]
    assert ownership["ships_with_invalid_ownership_event"] == [{
        "ship_id": ship_id, "ownership_event_id": "missing_ownership_event",
        "error": "missing or invalid ownership event",
    }]


def test_census_cross_checks_character_crew_links_against_rosters():
    world = WorldState.create("crew-link-census")
    crew_id, crew = next(iter(world.crew_ships.crews.items()))
    person_id = crew["roster"][0]
    person = world.entities[person_id]
    linked_crew_id = person["crew_id"]

    person["crew_id"] = None
    roster_error = _census(world, 0)["organizations"][
        "living_character_crew_link_errors"]
    assert roster_error == [{
        "character_id": person_id,
        "character_crew_id": None,
        "roster_crew_ids": [crew_id],
        "error": "crew roster contains a character with no crew link",
    }]

    person["crew_id"] = linked_crew_id
    crew["roster"].remove(person_id)
    person_error = _census(world, 0)["organizations"][
        "living_character_crew_link_errors"]
    assert person_error == [{
        "character_id": person_id,
        "character_crew_id": linked_crew_id,
        "roster_crew_ids": [],
        "error": "character crew link disagrees with active roster membership",
    }]


def test_census_rejects_dead_or_departed_people_left_in_crew_rosters():
    world = WorldState.create("terminal-person-in-roster-census")
    crew = next(iter(world.crew_ships.crews.values()))
    person_id = next((member_id for member_id in crew["roster"]
                      if member_id != crew["captain_id"]), crew["captain_id"])
    cause_id = world.entities[person_id]["last_event_id"]
    world.characters.kill_character(
        world, person_id, cause="census_fixture", caused_by=cause_id,
        replace_majors=False)
    crew["roster"].append(person_id)

    census = _census(world, 0)

    assert census["organizations"]["roster_references_to_nonliving_characters"] == [
        person_id]
    assert census["people"]["squad_headcount_conservation"][
        "population_representation_balanced"] is False


def test_census_reports_hull_less_crew_and_stranded_roster_members():
    world = WorldState.create("hull-less-census")
    crew_id, crew = next(iter(world.crew_ships.crews.items()))
    ship_id = crew["ship_ids"].pop()
    location = dict(world.entities[ship_id]["location"])
    crew["stranded_location"] = location
    for person_id in crew["roster"]:
        person = world.entities[person_id]
        person["ship_id"] = None
        person["location"] = dict(location)

    census = _census(world, 0)

    assert census["organizations"]["hull_less_crews"] == [crew_id]
    assert census["organizations"]["hull_less_crew_count"] == 1
    assert sorted(census["people"]["stranded_named_crew_members"]) == sorted(crew["roster"])


def test_census_reconciles_runtime_character_generation_and_death():
    world = WorldState.create("population-flow-census")
    start = _census(world, 0)["people"]["world_population_conservation"]
    assert start["residual"] == 0

    world.run_until(24)
    cause = world.event_log.by_type("WORLD_GENERATED")[0].id
    person_id = world.characters.create_character(
        world, tier="MINOR", cause_event_id=cause)
    after_arrival = _census(world, 1)["people"]["world_population_conservation"]
    assert after_arrival["runtime_new_characters"] == 1
    assert after_arrival["residual"] == 0
    assert after_arrival["transition_local_ledger"]["residual"] == 0
    assert after_arrival["transition_local_ledger"]["transitions"][-1]["event_type"] == (
        "CHARACTER_GENERATED")

    world.characters.kill_character(
        world, person_id, cause="population_audit", caused_by=cause,
        replace_majors=False)
    after_death = _census(world, 1)["people"]["world_population_conservation"]
    assert after_death["runtime_deaths"] == 1
    assert after_death["observed_living_population"] == start["opening_living_population"]
    assert after_death["residual"] == 0
    assert after_death["transition_local_ledger"]["residual"] == 0
    assert after_death["transition_local_ledger"]["transitions"][-1]["event_type"] == (
        "CHARACTER_DIED")

    neutral = world.event_log.append(
        tick=world.clock.tick, event_type="PRISONER_RELEASED",
        location=world.location_of(person_id)["id"],
        targets=[_part(person_id, "released_prisoner")],
        data={"character_id": person_id, "fate": "release"},
        caused_by=[cause], causes_tags=["population_ledger_test"])
    after_release = _census(world, 1)["people"]["world_population_conservation"]
    final_transition = after_release["transition_local_ledger"]["transitions"][-1]
    assert final_transition["event_id"] == neutral.id
    assert final_transition["living_population_delta"] == 0
    assert final_transition["ledger_balance_after"] == start["opening_living_population"]
    assert after_release["transition_local_ledger"]["residual"] == 0

    for event_type in ("CHARACTER_AGED", "ROOKIE_PROMOTED", "PRIZE_CREW_INSTALLED",
                       "LOD_REGION_AGGREGATED", "FACTION_SUCCESSION"):
        world.event_log.append(
            tick=world.clock.tick, event_type=event_type,
            actors=[_part(person_id, "test_actor")], data={}, caused_by=[cause],
            causes_tags=["population_ledger_test"])
    after_metadata = _census(world, 1)["people"]["world_population_conservation"]
    classified = after_metadata["transition_local_ledger"]["transitions"][-5:]
    assert [row["event_type"] for row in classified] == [
        "CHARACTER_AGED", "ROOKIE_PROMOTED", "PRIZE_CREW_INSTALLED",
        "LOD_REGION_AGGREGATED", "FACTION_SUCCESSION"]
    assert all(row["living_population_delta"] == 0 for row in classified)
    assert after_metadata["transition_local_ledger"]["residual"] == 0


def test_census_counts_lod_residents_once_across_promotion_demotion_and_death():
    world = WorldState.create("lod-representation-population-balance")
    world.run_until(24)
    world.lod._name_background_residents(365, world)
    world.lod._sync_cohorts(world)
    initial = _census(world, 1)["people"]["world_population_conservation"]
    initial_population = initial["observed_living_population"]
    assert initial["residual"] == 0

    resident_ids = sorted(world.lod.named_records)
    first_id, second_id = resident_ids[:2]
    first_record = world.lod.named_records[first_id]
    first_promotion_id = world.lod._promote(
        first_id, first_record["last_event_id"], world)
    after_promotion = _census(world, 1)["people"]["world_population_conservation"]
    assert after_promotion["observed_living_population"] == initial_population
    assert after_promotion["residual"] == 0

    second_record = world.lod.named_records[second_id]
    second_promotion_id = world.lod._promote(
        second_id, second_record["last_event_id"], world)
    world.lod.demote_character(
        world, second_id, cause_event_id=second_promotion_id)
    after_demotion = _census(world, 1)["people"]["world_population_conservation"]
    assert after_demotion["observed_living_population"] == initial_population
    assert after_demotion["residual"] == 0

    world.characters.kill_character(
        world, first_id, cause="population_audit", caused_by=first_promotion_id,
        replace_majors=False)
    after_death = _census(world, 1)["people"]["world_population_conservation"]
    assert after_death["observed_living_population"] == initial_population - 1
    assert after_death["expected_living_population"] == initial_population - 1
    assert after_death["residual"] == 0
    assert after_death["transition_local_ledger"]["residual"] == 0

    squad_balance = _census(world, 1)["people"]["squad_headcount_conservation"]
    assert squad_balance["population_representation_balanced"] is True
    assert squad_balance["population_representation_sum"] == (
        squad_balance["observed_living_population"])
    assert squad_balance["living_named_roster_partition_sum"] == (
        squad_balance["living_named_population"])
    assert squad_balance["living_named_roster_partition_residual"] == 0
    assert (squad_balance["living_named_population_in_rosters"]
            + squad_balance["living_named_population_outside_rosters"]
            == squad_balance["living_named_population"])


def test_explicit_character_departure_is_conserved_terminal_and_snapshot_safe():
    world = WorldState.create("population-explicit-departure")
    world.run_until(24)
    cause = world.event_log.by_type("WORLD_GENERATED")[0].id
    person_id = world.characters.create_character(
        world, tier="MINOR", cause_event_id=cause,
        island_id=sorted(world.geography.islands)[0])
    generated = world.entities[person_id]["last_event_id"]
    world.decisions.pending_wakes = [{
        "agent_id": person_id, "event_id": generated, "reason": "RUMOR_SHARED"}]
    pending_before = world.decisions.pending_wakes
    before = _census(world, 1)["people"]["world_population_conservation"]

    departure_id = world.characters.depart_character(
        world, person_id, destination="outside the chart", reason="family return",
        caused_by=generated)

    event = world.event_log.get(departure_id)
    assert event.type == "CHARACTER_DEPARTED"
    assert event.targets == (_part(person_id, "departed"),)
    assert event.caused_by == (generated,)
    assert person_id not in world.entities and person_id in world.archive
    assert world.archive[person_id]["death_tick"] is None
    assert world.archive[person_id]["departure_event_id"] == departure_id
    assert world.decisions.pending_wakes == []
    assert world.lifecycle_diagnostics == [{
        "code": "STALE_WAKEUP", "tick": world.clock.tick, "entity_id": person_id,
        "event_id": pending_before[0]["event_id"], "reason": "terminal_state:archived",
    }]
    after = _census(world, 1)["people"]["world_population_conservation"]
    assert after["runtime_departures"] == 1
    assert after["observed_living_population"] == before["observed_living_population"] - 1
    assert after["residual"] == 0
    assert after["transition_local_ledger"]["residual"] == 0
    records = [entry.to_record() for entry in world.event_log]
    report = audit_event_records(records)
    assert report["character_identity_ledger"]["errors"] == []
    assert report["character_identity_ledger"]["identities_by_status"]["departed"] == 1
    assert report["membership_transition_ledger"]["errors"] == []
    snapshot = world.to_snapshot()
    resumed = WorldState.from_snapshot(snapshot)
    assert resumed.to_snapshot() == snapshot
    assert resumed.decisions.pending_wakes == []

    invalid_snapshot = copy.deepcopy(snapshot)
    invalid_snapshot["archive"][person_id]["departure_event_id"] = generated
    try:
        WorldState.from_snapshot(invalid_snapshot)
    except ValueError as exc:
        assert "invalid departure event" in str(exc)
    else:
        raise AssertionError("snapshot accepted an unrelated event as departure proof")


def test_world_departure_rejects_crew_member_without_mutating_world():
    world = WorldState.create("population-departure-rostered")
    crew_id, crew = next(iter(world.crew_ships.crews.items()))
    person_id = crew["roster"][0]
    cause = world.event_log.by_type("WORLD_GENERATED")[0].id
    event_count = len(world.event_log)
    person_before = copy.deepcopy(world.entities[person_id])

    try:
        world.characters.depart_character(
            world, person_id, destination="outside the chart", reason="family return",
            caused_by=cause)
    except ValueError as exc:
        assert "must leave their crew" in str(exc)
    else:
        raise AssertionError("crew member departed the world without a roster transition")

    assert len(world.event_log) == event_count
    assert world.entities[person_id] == person_before
    assert person_id in crew["roster"]
    assert person_id not in world.archive


def test_population_disposition_audit_requires_complete_disjoint_sink_partitions():
    records = [
        _event("sink-good", 24, "SHIP_LOSS_DISPOSITION", data={
            "crew_roster_before": ["p1", "p2", "p3"],
            "survivors": ["p1", "p2"], "missing": [], "deceased": ["p3"],
            "prisoners_released": ["prisoner_a"],
        }),
        _event("sink-bad", 48, "SHIP_LOSS_DISPOSITION", data={
            "crew_roster_before": ["p4", "p5"],
            "survivors": ["p4"], "missing": ["p4"], "deceased": [],
            "prisoners_released": ["p4"],
        }),
    ]

    report = audit_event_records(records)

    assert [row["status"] for row in report["ship_loss_dispositions"]] == [
        "balanced", "invalid"]
    assert report["population_disposition_errors"] == [{
        "event_id": "sink-bad",
        "errors": [
            "overlapping survivor/missing/deceased partitions",
            "crew roster is not exhaustively dispositioned",
        ],
    }]


def test_membership_transition_replay_tracks_transfer_desertion_death_and_sink_roster():
    records = [
        _event("found-a", 0, "CREW_FOUNDED", data={
            "crew_id": "crew_a", "roster": ["p1", "p2", "p4"]}),
        _event("found-b", 0, "CREW_FOUNDED", data={
            "crew_id": "crew_b", "roster": ["p3"]}),
        _event("transfer", 24, "CREW_MEMBER_TRANSFERRED", data={
            "character_id": "p2", "from_crew_id": "crew_a", "to_crew_id": "crew_b"}),
        _event("desert", 48, "DESERTION", data={"crew_id": "crew_b"},
               actors=[_part("p2", "deserter")]),
        _event("death", 72, "CHARACTER_DIED", targets=[_part("p3", "deceased")]),
        _event("capture", 84, "PRISONER_CAPTURED", data={
            "character_id": "p4", "from_crew_id": "crew_a", "to_crew_id": None}),
        _event("sink", 96, "SHIP_LOSS_DISPOSITION", data={
            "crew_id": "crew_a", "crew_roster_before": ["p1"],
            "survivors": ["p1"], "missing": [], "deceased": [],
            "prisoners_released": []}),
    ]

    ledger = audit_event_records(records)["membership_transition_ledger"]

    assert ledger["errors"] == []
    assert ledger["active_memberships"] == {"p1": "crew_a"}
    transfers = {row["event_id"]: row for row in ledger["membership_flow"]}
    assert transfers["transfer"]["memberships_transferred"] == 1
    assert transfers["transfer"]["active_membership_delta"] == 0
    assert transfers["desert"]["memberships_left"] == 1
    assert transfers["death"]["active_memberships_after"] == 2
    assert transfers["capture"]["active_memberships_after"] == 1
    assert transfers["sink"]["active_memberships_before"] == 1
    assert transfers["sink"]["active_memberships_after"] == 1


def test_prisoner_custody_replay_tracks_transfer_ransom_execution_and_shipwreck_release():
    records = [
        _event("capture-a", 0, "PRISONER_CAPTURED", data={
            "character_id": "p1", "captor_ship_id": "ship_a",
            "from_crew_id": "crew_old", "to_crew_id": None}),
        _event("transfer-a", 24, "PRISONER_CUSTODY_TRANSFERRED", data={
            "prisoner_ids": ["p1"], "from_ship_id": "ship_a", "to_ship_id": "ship_b",
            "from_crew_id": "crew_a", "to_crew_id": "crew_b"}),
        _event("fate-a", 48, "PRISONER_FATE", data={"fate": "ransom"},
               targets=[_part("p1", "prisoner")]),
        _event("ransom-release", 48, "PRISONER_RELEASED", data={
            "character_id": "p1", "fate": "ransom"},
               targets=[_part("p1", "released_prisoner")]),
        _event("capture-b", 72, "PRIZE_CREW_INSTALLED", data={
            "former_captain_id": "p2", "captor_ship_id": "ship_c",
            "crew_id": "crew_old"}),
        _event("wreck-release", 96, "PRISONER_RELEASED", data={
            "character_id": "p2", "reason": "captor_ship_sunk"},
               targets=[_part("p2", "released_prisoner")]),
        _event("capture-c", 120, "PRISONER_CAPTURED", data={
            "character_id": "p3", "captor_ship_id": "ship_d"}),
        _event("fate-c", 144, "PRISONER_FATE", data={"fate": "execute"},
               targets=[_part("p3", "prisoner")]),
        _event("death-c", 144, "CHARACTER_DIED", data={"cause": "execution"},
               targets=[_part("p3", "deceased")]),
    ]

    ledger = audit_event_records(records)["prisoner_custody_ledger"]

    assert ledger["transition_count"] == 9
    assert ledger["prisoner_count"] == 3
    assert ledger["statuses"] == {"deceased": 1, "released": 2}
    assert ledger["prisoners"]["p1"]["captor_ship_id"] == "ship_b"
    assert ledger["prisoners"]["p1"]["status"] == "released"
    assert ledger["prisoners"]["p2"]["status"] == "released"
    assert ledger["prisoners"]["p3"]["status"] == "deceased"
    assert ledger["errors"] == []


def test_prisoner_custody_replay_rejects_wrong_captor_transfer_and_duplicate_release():
    records = [
        _event("capture", 0, "PRISONER_CAPTURED", data={
            "character_id": "p1", "captor_ship_id": "ship_a"}),
        _event("bad-transfer", 24, "PRISONER_CUSTODY_TRANSFERRED", data={
            "prisoner_ids": ["p1"], "from_ship_id": "ship_wrong", "to_ship_id": "ship_b"}),
        _event("release", 48, "PRISONER_RELEASED", data={"character_id": "p1"},
               targets=[_part("p1", "released_prisoner")]),
        _event("duplicate-release", 72, "PRISONER_RELEASED", data={"character_id": "p1"},
               targets=[_part("p1", "released_prisoner")]),
    ]

    errors = audit_event_records(records)["prisoner_custody_ledger"]["errors"]

    assert [row["error"] for row in errors] == [
        "custody transfer source vessel disagrees with current captor",
        "release references a person not in custody",
    ]


def test_membership_transition_replay_flags_unrecorded_departure_and_duplicate_join():
    records = [
        _event("found", 0, "CREW_FOUNDED", data={
            "crew_id": "crew_a", "roster": ["p1"]}),
        _event("duplicate", 24, "RECRUITED", data={
            "crew_id": "crew_a", "character_id": "p1"}),
        _event("orphan-departure", 48, "DESERTION", data={"crew_id": "crew_b"},
               actors=[_part("p1", "deserter")]),
    ]

    ledger = audit_event_records(records)["membership_transition_ledger"]

    assert [row["event_id"] for row in ledger["errors"]] == [
        "duplicate", "orphan-departure"]


def test_character_identity_audit_treats_lod_promotion_demotion_as_representation_changes():
    records = [
        _event("generated", 0, "CHARACTER_GENERATED", actors=[_part("resident", "character")]),
        _event("sampled", 24, "LOD_BACKGROUND_RECORDED",
               actors=[_part("resident", "named_resident")]),
        _event("promoted", 48, "LOD_PROMOTED",
               actors=[_part("resident", "promoted_character")]),
        _event("missing", 60, "CHARACTER_MISSING",
               actors=[_part("resident", "missing_person")]),
        _event("rescued", 72, "CHARACTER_RESCUED",
               targets=[_part("resident", "rescued_person")]),
        _event("demoted", 84, "LOD_DEMOTED",
               actors=[_part("resident", "demoted_character")]),
        _event("arrived", 108, "CHARACTER_ARRIVED",
               actors=[_part("resident", "new_crew_member")]),
        _event("death", 132, "CHARACTER_DIED", targets=[_part("resident", "deceased")]),
    ]

    ledger = audit_event_records(records)["character_identity_ledger"]

    assert ledger["transition_count"] == 7
    assert ledger["identity_count"] == 1
    assert ledger["identities_by_status"] == {"deceased": 1}
    assert ledger["errors"] == []


def test_character_identity_audit_flags_duplicate_generation_and_post_death_reappearance():
    records = [
        _event("generated", 0, "CHARACTER_GENERATED", actors=[_part("person", "character")]),
        _event("death", 24, "CHARACTER_DIED", targets=[_part("person", "deceased")]),
        _event("generated-again", 48, "CHARACTER_GENERATED",
               actors=[_part("person", "character")]),
        _event("arrived-again", 72, "CHARACTER_ARRIVED",
               actors=[_part("person", "new_crew_member")]),
    ]

    ledger = audit_event_records(records)["character_identity_ledger"]

    assert [row["event_id"] for row in ledger["errors"]] == [
        "generated-again", "arrived-again"]


def test_audit_finds_same_tick_target_concentration_and_unresolved_target_proxy():
    records = [
        _event("t1", 240, "RAIDER_TARGET_SELECTED", actors=[_part("cap_1", "raider_captain")],
               targets=[_part("ship_m", "merchant_vessel")],
               data={"estimated_cargo_value_milli": 500, "estimated_patrol_density_milli": 10}),
        _event("t2", 240, "RAIDER_TARGET_SELECTED", actors=[_part("cap_2", "raider_captain")],
               targets=[_part("ship_m", "merchant_vessel")],
               data={"estimated_cargo_value_milli": 500, "estimated_patrol_density_milli": 10}),
    ]

    report = audit_event_records(records)

    assert report["target_same_tick_concentration"] == [{
        "tick": 240, "day": 10, "target_ship_id": "ship_m", "selection_count": 2,
        "captains": ["cap_1", "cap_2"], "distinct_estimates": 1,
    }]
    assert report["target_dispositions_proxy"][0]["any_later_encounter_involving_target"] is False


def test_audit_reports_repeating_pair_interval_and_outcome_histogram():
    records = [
        _event("initial", 0, "WORLD_GENERATED", event_class="structural"),
        _event("meet1", 24, "ENCOUNTER_RESOLVED",
               data={"ships": ["ship_a", "ship_b"], "outcome": "mutual_withdrawal"}),
        _event("meet2", 192, "ENCOUNTER_RESOLVED",
               data={"ships": ["ship_b", "ship_a"], "outcome": "mutual_withdrawal"}),
        _event("meet3", 360, "ENCOUNTER_RESOLVED",
               data={"ships": ["ship_a", "ship_b"], "outcome": "surrender"}),
    ]

    report = audit_event_records(records)

    assert report["repeated_encounter_pairs"][0]["most_common_interval_days"] == 7
    assert report["repeated_encounter_pairs"][0]["most_common_interval_count"] == 2
    assert report["encounter_outcomes"] == {"mutual_withdrawal": 2, "surrender": 1}
    assert report["exported_event_rows_and_bytes"]["by_tick_origin"][
        "initialization_tick_0"]["rows"] == 1
    assert report["exported_event_rows_and_bytes"]["by_tick_origin"][
        "simulation_tick_gt_0"]["rows"] == 3


def test_audit_follows_merchant_loss_to_causal_encounter_and_owner_types():
    records = [
        _event("crew_m", 0, "CREW_FOUNDED", data={"crew_id": "crew_m", "ship_id": "ship_m",
                                                   "owner_type": "merchant"}),
        _event("merchant", 0, "MERCHANT_CREATED", data={"ship_id": "ship_m"}),
        _event("crew_p", 0, "CREW_FOUNDED", data={"crew_id": "crew_p", "ship_id": "ship_p",
                                                   "owner_type": "pirate"}),
        _event("fight", 24, "ENCOUNTER_RESOLVED",
               data={"ships": ["ship_m", "ship_p"], "outcome": "ship_captured"}),
        _event("capture", 24, "SHIP_CAPTURED", data={"previous_crew_id": "crew_m",
                                                       "new_crew_id": "crew_p"},
               targets=[_part("ship_m", "captured_ship")], caused_by=["fight"]),
    ]

    report = audit_event_records(records)

    assert len(report["merchant_loss_incidents"]) == 1
    incident = report["merchant_loss_incidents"][0]
    assert incident["cause_resolved"] is True
    assert incident["causal_encounter_id"] == "fight"
    assert incident["pirate_involved_by_event_time_owner_history"] is True
