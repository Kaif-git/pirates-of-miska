import copy

import pytest

from sim.core.world import WorldState
from sim.systems.encounters import load_encounter_data, validate_encounter_data
from sim.tools.coherence_audit import audit_event_records


def _two_crewed_ships(world):
    ship_ids = sorted(key for key, entity in world.entities.items()
                      if entity.get("kind") == "ship")[:2]
    first, second = ship_ids
    world.entities[second]["location"] = dict(world.entities[first]["location"])
    return first, second


def _assert_snapshot_continuation_is_deterministic(world, *, hours=24):
    snapshot = world.to_snapshot()
    uninterrupted = WorldState.from_snapshot(snapshot)
    resumed = WorldState.from_snapshot(snapshot)
    target_tick = world.clock.tick + hours

    uninterrupted.run_until(target_tick)
    resumed.run_until(target_tick)

    assert uninterrupted.event_log.to_jsonl() == resumed.event_log.to_jsonl()
    assert uninterrupted.to_snapshot() == resumed.to_snapshot()


def test_encounter_catalog_is_validated_and_weather_reduces_detection():
    data, digest = load_encounter_data()
    validate_encounter_data(data)
    assert len(digest) == 64
    invalid = copy.deepcopy(data)
    invalid["rules"]["max_gunnery_rounds"] = 0
    with pytest.raises(ValueError, match="encounter rules"):
        validate_encounter_data(invalid)
    world = WorldState.create("encounter-detection")
    first, second = _two_crewed_ships(world)
    location = {"kind": "island", "location": world.entities[first]["location"]["id"]}
    region_id = world.geography.islands[location["location"]].region_id
    world.weather.states[region_id] = "fog"
    fog_chance = world.encounters._detection_chance(world, first, location)
    world.weather.states[region_id] = "fair"
    fair_chance = world.encounters._detection_chance(world, first, location)
    assert fog_chance < fair_chance
    assert world.encounters._overlap(
        world.location_of(first), world.location_of(second)) == location


def test_encounter_resolution_records_strength_beliefs_phases_and_persistent_damage():
    world = WorldState.create("encounter-combat")
    first, second = _two_crewed_ships(world)
    first_crew = world.crew_ships.crews[world.entities[first]["crew_id"]]
    second_crew = world.crew_ships.crews[world.entities[second]["crew_id"]]
    cause_ids = [first_crew["last_event_id"], second_crew["last_event_id"]]
    before_hulls = [world.entities[ship]["hull"]["current"] for ship in (first, second)]
    before_ammo = [world.entities[ship]["ammunition"] for ship in (first, second)]
    event_id = world.encounters.resolve(
        world, first, second, caused_by=cause_ids,
        stances={first: "attack", second: "attack"}, force_combat=True,
        surprise_by=first)
    resolved = world.event_log.get(event_id)
    detected = world.event_log.get(resolved.caused_by[0])
    assert detected.type == "ENCOUNTER_DETECTED"
    assert len(detected.data["strengths_before"]) == 2
    assert len(detected.data["beliefs_before"]) == 2
    assert resolved.type == "ENCOUNTER_RESOLVED"
    assert resolved.data["outcome"] in {"combat_resolved", "captain_wounded"}
    assert any(row["phase"] == "gunnery" for row in resolved.data["phases"])
    assert resolved.data["phases"][0]["surprise_by"] == first
    assert "surprise" in resolved.causes_tags
    assert any(world.entities[ship]["hull"]["current"] < before
               for ship, before in zip((first, second), before_hulls))
    assert any(world.entities[ship]["ammunition"] < before
               for ship, before in zip((first, second), before_ammo))
    assert resolved.data["damage_event_ids"]
    assert all(world.event_log.get(child_id).caused_by == (detected.id,)
               for child_id in resolved.data["damage_event_ids"])
    memory_hooks = world.event_log.by_type("COMBAT_MEMORY_HOOK")
    assert len(memory_hooks) == 2
    assert all(hook.caused_by == (resolved.id,) for hook in memory_hooks)
    world.event_log.validate_causality()
    world.crew_ships.validate_world(world)


def test_detected_encounter_has_asymmetric_surprise_and_daily_pair_cooldown():
    world = WorldState.create("encounter-day")
    first, second = _two_crewed_ships(world)
    world.encounters.data["rules"]["base_detection_basis_points"] = 10000
    world.entities[second]["location"] = dict(world.entities[first]["location"])
    first_chance = world.encounters._detection_chance(
        world, first, {"kind": "island", "location": world.entities[first]["location"]["id"]})
    second_chance = world.encounters._detection_chance(
        world, second, {"kind": "island", "location": world.entities[first]["location"]["id"]})
    assert first_chance == second_chance == 9500
    events = world.encounters.update_day(1, world)
    assert len(events) <= 1
    assert world.encounters.last_updated_day == 1
    if events:
        resolved = world.event_log.get(events[0])
        detected = world.event_log.get(resolved.caused_by[0])
        assert detected.type == "ENCOUNTER_DETECTED"
        assert detected.data["surprise_by"] in {None, first, second}
    assert world.encounters.pair_last_encounter_day
    with pytest.raises(ValueError, match="consecutively"):
        world.encounters.update_day(1, world)


def test_encounter_system_snapshot_resumes_with_world(tmp_path):
    world = WorldState.create("encounter-resume")
    world.run_until(48)
    restored = WorldState.load(world.save(tmp_path / "encounters.json"))
    assert restored.to_snapshot() == world.to_snapshot()


def _combat_outcome(world, *, boarding=False, stances=None):
    first, second = _two_crewed_ships(world)
    crews = [world.crew_ships.crews[world.entities[ship]["crew_id"]]
             for ship in (first, second)]
    world.encounters.data["rules"]["boarding_threshold_basis_points"] = 10000 if boarding else 0
    cause = [crew["last_event_id"] for crew in crews]
    resolved_id = world.encounters.resolve(
        world, first, second, caused_by=cause,
        stances=stances or {first: "attack", second: "attack"}, force_combat=True)
    return world, first, second, world.event_log.get(resolved_id)


def test_surrender_capture_and_defection_outcomes_are_reachable():
    surrender_world = WorldState.create("outcome-surrender")
    surrender_world.encounters.data["rules"].update({
        "surrender_morale_threshold": 100, "surrender_basis_points": 10000,
        "ship_capture_basis_points": 0,
    })
    _, _, _, surrender = _combat_outcome(surrender_world, boarding=True)
    assert surrender.data["outcome"] == "surrender"
    surrendered_ship = surrender_world.event_log.by_type("SHIP_CAPTURED")[-1].targets[0]["entity_id"]
    captured = surrender_world.entities[surrendered_ship]
    new_controller = captured["controller_crew_id"]
    assert captured["legal_owner"]["kind"] == "crew"
    assert captured["legal_owner"]["id"] != captured["crew_id"]
    assert captured["controller_crew_id"] == new_controller
    assert captured["crew_id"] == new_controller
    assert surrender_world.ship_is_commandable(surrendered_ship)
    assert captured["ownership_event_id"] == next(
        event.id for event in surrender_world.event_log.by_type("SHIP_ACQUIRED")
        if event.data.get("method") == "capture")
    with pytest.raises(ValueError, match="replay or precede"):
        surrender_world.assign_ship_title(
            surrendered_ship, owner_kind="crew", owner_id=new_controller,
            controller_crew_id=new_controller,
            event_id=captured["created_event_id"])
    capture_record = surrender_world.event_log.by_type("SHIP_CAPTURED")[-1]
    assert capture_record.data["prize_crew_pending"] is False
    displaced_captain = capture_record.data["former_captain_id"]
    assert displaced_captain in surrender_world.encounters.prisoners
    assert displaced_captain not in surrender_world.crew_ships.crews[new_controller]["roster"]
    surrender_world = WorldState.from_snapshot(surrender_world.to_snapshot())
    captured = surrender_world.entities[surrendered_ship]
    displaced_crew = surrender_world.crew_ships.crews[captured["crew_id"]]
    displaced_crew["morale"] = 99
    old_captain = surrender_world.entities[displaced_captain]
    assert not surrender_world.decisions._eligible(
        surrender_world, old_captain, "crew_at_island_below_full_morale")
    assert old_captain["ship_id"] in surrender.data["ships"]
    assert surrender_world.ship_is_commandable(surrendered_ship)
    captured_merchant = next((merchant_id for merchant_id, merchant
                              in surrender_world.economy.merchants.items()
                              if merchant["ship_id"] == surrendered_ship), None)
    if captured_merchant:
        assert surrender_world.economy.best_trade_route(
            surrender_world, captured_merchant) is None
        with pytest.raises(ValueError, match="no longer owns or commands"):
            surrender_world.economy.trade(
                surrender_world, captured_merchant,
                captured["location"]["id"], "fish", "buy", 1,
                caused_by=surrender.id)
    surrender_world.to_snapshot()

    capture_world = WorldState.create("outcome-capture")
    capture_world.encounters.data["rules"].update({
        "surrender_morale_threshold": 100, "surrender_basis_points": 10000,
        "ship_capture_basis_points": 10000,
    })
    _, _, _, capture = _combat_outcome(capture_world, boarding=True)
    assert capture.data["outcome"] == "ship_captured"
    assert any(event.data.get("method") == "capture"
               for event in capture_world.event_log.by_type("SHIP_ACQUIRED"))

    defection_world = WorldState.create("outcome-defection")
    defection_world.encounters.data["rules"].update({
        "surrender_morale_threshold": 0, "defection_morale_threshold": 100,
        "surrender_basis_points": 0, "defection_basis_points": 10000,
        "captain_capture_basis_points": 0,
    })
    _, _, _, defection = _combat_outcome(defection_world, boarding=True)
    assert defection.data["outcome"] == "crew_defects"
    details = defection.data["outcome_details"]
    defector_id = details["defector_ids"][0]
    assert defector_id in defection_world.crew_ships.crews[details["to_crew_id"]]["roster"]
    assert defector_id not in defection_world.crew_ships.crews[details["from_crew_id"]]["roster"]
    assert defection_world.entities[defector_id]["crew_id"] == details["to_crew_id"]
    defection_world.to_snapshot()
    defection_world.run_until(24)
    defection_world.to_snapshot()


def test_prize_crew_ship_can_be_recaptured_by_a_later_ordinary_encounter():
    world = WorldState.create("ordinary-prize-recapture")
    world.encounters.data["rules"].update({
        "boarding_threshold_basis_points": 10000,
        "surrender_morale_threshold": 100,
        "surrender_basis_points": 10000,
        "ship_capture_basis_points": 10000,
    })
    ships = sorted(ship_id for ship_id, row in world.entities.items()
                   if row.get("kind") == "ship")
    first, second, third = ships[:3]
    world.entities[second]["location"] = dict(world.entities[first]["location"])

    def encounter_causes(ship_a, ship_b):
        return [world.crew_ships.crews[world.entities[ship_id]["crew_id"]]["last_event_id"]
                for ship_id in (ship_a, ship_b)]

    first_result_id = world.encounters.resolve(
        world, first, second, caused_by=encounter_causes(first, second),
        stances={first: "attack", second: "attack"}, force_combat=True)
    first_result = world.event_log.get(first_result_id)
    captured_id = first_result.data["outcome_details"]["captured_ship_id"]
    first_owner = dict(world.entities[captured_id]["legal_owner"])
    assert world.ship_is_commandable(captured_id)
    held_person = next(row for row in world.entities.values()
                       if row.get("kind") == "character" and row.get("crew_id") is None
                       and row.get("ship_id") is None)
    held_person.update({"ship_id": captured_id,
                        "location": dict(world.entities[captured_id]["location"])})
    world.encounters.prisoners[held_person["id"]] = {
        "character_id": held_person["id"], "captor_ship_id": captured_id,
        "capture_event_id": held_person["last_event_id"],
        "status": "held", "fate_event_id": None,
    }

    world.entities[third]["location"] = dict(world.entities[captured_id]["location"])
    prize_crew_id = world.entities[captured_id]["crew_id"]
    world.crew_ships.crews[prize_crew_id]["morale"] = 0
    world.crew_ships.crews[world.entities[third]["crew_id"]]["morale"] = 100
    world.entities[captured_id]["hull"]["current"] = world.entities[captured_id]["hull"]["max"]
    world.entities[third]["hull"]["current"] = world.entities[third]["hull"]["max"]
    second_result_id = world.encounters.resolve(
        world, captured_id, third, caused_by=encounter_causes(captured_id, third),
        stances={captured_id: "attack", third: "attack"}, force_combat=True)
    second_result = world.event_log.get(second_result_id)

    assert second_result.data["outcome"] == "ship_captured"
    assert second_result.data["outcome_details"]["captured_ship_id"] == captured_id
    capture_history = [event for event in world.event_log.by_type("SHIP_CAPTURED")
                       if event.targets[0]["entity_id"] == captured_id]
    assert len(capture_history) == 2
    latest = capture_history[-1]
    assert latest.data["previous_owner"] == first_owner
    assert latest.caused_by[0] == second_result_id or second_result_id in latest.caused_by
    assert world.entities[captured_id]["legal_owner"] == {
        "kind": "crew", "id": world.entities[third]["crew_id"]}
    assert world.ship_is_commandable(captured_id)
    custody = world.event_log.by_type("PRISONER_CUSTODY_TRANSFERRED")[-1]
    assert held_person["id"] in custody.data["prisoner_ids"]
    assert custody.data["from_ship_id"] == captured_id
    assert custody.data["to_ship_id"] == third
    assert world.encounters.prisoners[held_person["id"]]["captor_ship_id"] == third
    assert world.entities[held_person["id"]]["ship_id"] == third
    _assert_snapshot_continuation_is_deterministic(world)
    snapshot = world.to_snapshot()
    resumed = WorldState.from_snapshot(snapshot)
    assert resumed.to_snapshot() == snapshot
    assert resumed.encounters.prisoners[held_person["id"]]["status"] == "held"
    assert resumed.encounters.prisoners[held_person["id"]]["captor_ship_id"] == third
    assert resumed.entities[held_person["id"]]["ship_id"] == third


def test_capture_splits_a_victor_roster_when_surrendering_crew_has_no_successor():
    world = WorldState.create("prize-crew-split")
    world.encounters.data["rules"].update({
        "boarding_threshold_basis_points": 10000,
        "surrender_morale_threshold": 100,
        "surrender_basis_points": 10000,
        "ship_capture_basis_points": 10000,
    })
    first, second = _two_crewed_ships(world)
    winner_crew = world.crew_ships.crews[world.entities[first]["crew_id"]]
    losing_crew = world.crew_ships.crews[world.entities[second]["crew_id"]]
    winner_crew["morale"] = 100
    losing_crew["morale"] = 0
    former_captain = losing_crew["captain_id"]
    for member_id in list(losing_crew["roster"]):
        if member_id == former_captain:
            continue
        person = world.entities[member_id]
        person.update({"crew_id": None, "ship_id": None,
                       "location": dict(world.entities[second]["location"])})
        losing_crew["roster"].remove(member_id)
        world.economy.crew_accounts[losing_crew["id"]]["member_wallets"].pop(member_id, None)
        for role, assigned in list(losing_crew["roles"].items()):
            if assigned == member_id:
                del losing_crew["roles"][role]

    result_id = world.encounters.resolve(
        world, first, second, caused_by=_encounter_causes(world, first, second),
        stances={first: "attack", second: "attack"}, force_combat=True)
    result = world.event_log.get(result_id)
    assert result.data["outcome"] == "ship_captured"
    captured_id = result.data["outcome_details"]["captured_ship_id"]
    assert captured_id == second
    prize_crew_id = world.entities[captured_id]["crew_id"]
    transfer = next(event for event in reversed(world.event_log.by_type("CREW_MEMBER_TRANSFERRED"))
                    if event.data.get("reason") == "prize_crew_assignment")
    prize_captain = transfer.data["character_id"]
    assert transfer.data["from_crew_id"] == winner_crew["id"]
    assert transfer.data["to_crew_id"] == prize_crew_id
    assert prize_captain in world.crew_ships.crews[prize_crew_id]["roster"]
    assert prize_captain not in winner_crew["roster"]
    assert former_captain in world.encounters.prisoners
    assert world.ship_is_commandable(captured_id)
    world.to_snapshot()


def test_capture_recruits_a_local_prize_captain_when_neither_crew_can_split():
    world = WorldState.create("local-prize-captain")
    world.encounters.data["rules"].update({
        "boarding_threshold_basis_points": 10000,
        "surrender_morale_threshold": 100,
        "surrender_basis_points": 10000,
        "ship_capture_basis_points": 10000,
    })
    first, second = _two_crewed_ships(world)
    winner_crew = world.crew_ships.crews[world.entities[first]["crew_id"]]
    losing_crew = world.crew_ships.crews[world.entities[second]["crew_id"]]
    winner_crew["morale"] = 100
    losing_crew["morale"] = 0
    former_captain = losing_crew["captain_id"]
    for crew in (winner_crew, losing_crew):
        for member_id in list(crew["roster"]):
            if member_id == crew["captain_id"]:
                continue
            person = world.entities[member_id]
            person.update({"crew_id": None, "ship_id": None,
                           "location": dict(world.entities[second]["location"])})
            crew["roster"].remove(member_id)
            world.economy.crew_accounts[crew["id"]]["member_wallets"].pop(member_id, None)
            for role, assigned in list(crew["roles"].items()):
                if assigned == member_id:
                    del crew["roles"][role]
    island_id = world.entities[second]["location"]["id"]
    other_island = next(candidate for candidate in sorted(world.geography.islands)
                         if candidate != island_id)
    unassigned_people = [row for row in world.entities.values()
                         if row.get("kind") == "character" and row.get("crew_id") is None
                         and row.get("ship_id") is None]
    for person in unassigned_people:
        person["location"] = {"kind": "island", "id": other_island}
    local_person = unassigned_people[0]
    local_person["location"] = {"kind": "island", "id": island_id}

    result_id = world.encounters.resolve(
        world, first, second, caused_by=_encounter_causes(world, first, second),
        stances={first: "attack", second: "attack"}, force_combat=True)
    result = world.event_log.get(result_id)
    assert result.data["outcome"] == "ship_captured"
    assert result.data["outcome_details"]["captured_ship_id"] == second
    recruitment = next(event for event in reversed(world.event_log.by_type("RECRUITED"))
                       if event.data.get("reason") == "prize_crew_assignment")
    assert recruitment.data["character_id"] == local_person["id"]
    assert local_person["crew_id"] == losing_crew["id"]
    assert world.entities[second]["controller_crew_id"] == losing_crew["id"]
    assert world.ship_is_commandable(second)
    assert former_captain in world.encounters.prisoners
    world.to_snapshot()


def test_capture_without_any_prize_candidate_stays_pending_and_noncommandable():
    world = WorldState.create("pending-prize-candidate")
    world.encounters.data["rules"].update({
        "boarding_threshold_basis_points": 10000,
        "surrender_morale_threshold": 100,
        "surrender_basis_points": 10000,
        "ship_capture_basis_points": 10000,
    })
    first, second = _two_crewed_ships(world)
    winner_crew = world.crew_ships.crews[world.entities[first]["crew_id"]]
    losing_crew = world.crew_ships.crews[world.entities[second]["crew_id"]]
    winner_crew["morale"] = 100
    losing_crew["morale"] = 0
    for crew in (winner_crew, losing_crew):
        for member_id in list(crew["roster"]):
            if member_id == crew["captain_id"]:
                continue
            world.entities[member_id].update({"crew_id": None, "ship_id": None})
            crew["roster"].remove(member_id)
            world.economy.crew_accounts[crew["id"]]["member_wallets"].pop(member_id, None)
            for role, assigned in list(crew["roles"].items()):
                if assigned == member_id:
                    del crew["roles"][role]
    capture_island = world.entities[second]["location"]["id"]
    other_island = next(island_id for island_id in sorted(world.geography.islands)
                        if island_id != capture_island)
    for person in world.entities.values():
        if (person.get("kind") == "character" and person.get("crew_id") is None
                and person.get("ship_id") is None):
            person["location"] = {"kind": "island", "id": other_island}

    result_id = world.encounters.resolve(
        world, first, second, caused_by=_encounter_causes(world, first, second),
        stances={first: "attack", second: "attack"}, force_combat=True)
    result = world.event_log.get(result_id)
    assert result.data["outcome"] == "ship_captured"
    assert result.data["outcome_details"]["captured_ship_id"] == second
    capture = world.event_log.by_type("SHIP_CAPTURED")[-1]
    assert capture.data["prize_crew_pending"] is True
    assert second not in world.encounters.prisoners
    assert not world.ship_is_commandable(second)
    assert world.entities[second]["controller_crew_id"] == winner_crew["id"]
    with pytest.raises(ValueError, match="valid onboard command authority"):
        world.encounters.resolve(
            world, second, world.entities[next(ship_id for ship_id, row in world.entities.items()
                                                if row.get("kind") == "ship"
                                                and ship_id not in {first, second})]["id"],
            caused_by=[result_id], force_combat=True)
    world.to_snapshot()


def test_captain_death_wounding_and_prisoner_fates_are_reachable():
    wounded_world = WorldState.create("outcome-wounded")
    wounded_world.encounters.data["rules"].update({
        "captain_wound_basis_points": 10000, "captain_death_basis_points": 0,
        "boarding_threshold_basis_points": 0,
    })
    _, _, _, wounded = _combat_outcome(wounded_world)
    assert wounded.data["outcome"] == "captain_wounded"
    assert wounded.data["outcome_event_ids"]

    killed_world = WorldState.create("outcome-killed")
    killed_world.encounters.data["rules"].update({
        "captain_wound_basis_points": 0, "captain_death_basis_points": 10000,
        "boarding_threshold_basis_points": 0,
    })
    _, _, _, killed = _combat_outcome(killed_world)
    assert killed.data["outcome"] == "captain_killed"
    deceased_id = killed.data["outcome_details"]["captain_id"]
    assert deceased_id in killed_world.archive
    assert deceased_id not in killed_world.entities

    prisoner_world = WorldState.create("outcome-prisoner")
    prisoner_world.encounters.data["rules"].update({
        "boarding_threshold_basis_points": 10000,
        "surrender_morale_threshold": 0,
        "defection_morale_threshold": 0,
        "captain_capture_basis_points": 10000,
        "captain_wound_basis_points": 0,
        "captain_death_basis_points": 0,
    })
    _, _, _, captured = _combat_outcome(prisoner_world, boarding=True)
    assert captured.data["outcome"] == "captain_captured"
    prisoner_id = captured.data["outcome_details"]["captured_character_id"]
    prisoner = prisoner_world.encounters.prisoners[prisoner_id]
    former_crew_id = prisoner["former_crew_id"]
    captor_ship_id = prisoner["captor_ship_id"]
    assert prisoner_world.entities[prisoner_id]["crew_id"] is None
    assert prisoner_world.entities[prisoner_id]["ship_id"] == captor_ship_id
    assert prisoner_id not in prisoner_world.crew_ships.crews[former_crew_id]["roster"]
    assert prisoner_world.crew_ships.crews[former_crew_id]["captain_id"] != prisoner_id
    assert prisoner_world.event_log.by_type("PRISONER_CAPTURED")[-1].data == {
        "character_id": prisoner_id, "captor_ship_id": captor_ship_id,
        "from_crew_id": former_crew_id, "to_crew_id": None,
    }
    assert not prisoner_world.decisions._eligible(
        prisoner_world, prisoner_world.entities[prisoner_id], "living_character_with_dream")
    fate_event = prisoner_world.encounters.resolve_prisoner_fate(
        prisoner_world, prisoner_id, "execute", caused_by=captured.id)
    assert prisoner_world.archive[prisoner_id]["death_tick"] == prisoner_world.clock.tick
    assert prisoner_world.encounters.prisoners[prisoner_id]["status"] == "executed"
    assert prisoner_world.event_log.get(fate_event).type == "CHARACTER_DIED"
    prisoner_world.event_log.validate_causality()


def test_capturing_a_solo_crew_captain_replaces_the_captain_without_losing_the_crew():
    world = WorldState.create("solo-captain-captured")
    for crew_id, crew in world.crew_ships.crews.items():
        captain_id = crew["captain_id"]
        for member_id in list(crew["roster"]):
            if member_id == captain_id:
                continue
            crew["roster"].remove(member_id)
            for role, assigned in list(crew["roles"].items()):
                if assigned == member_id:
                    del crew["roles"][role]
            world.economy.crew_accounts[crew_id]["member_wallets"].pop(member_id)
            world.entities[member_id].update({"crew_id": None, "ship_id": None})

    world.encounters.data["rules"].update({
        "boarding_threshold_basis_points": 10000,
        "surrender_morale_threshold": 0,
        "defection_morale_threshold": 0,
        "captain_capture_basis_points": 10000,
        "captain_wound_basis_points": 0,
        "captain_death_basis_points": 0,
    })
    _, _, _, captured = _combat_outcome(world, boarding=True)
    prisoner_id = captured.data["outcome_details"]["captured_character_id"]
    former_crew_id = world.encounters.prisoners[prisoner_id]["former_crew_id"]
    former_crew = world.crew_ships.crews[former_crew_id]

    assert captured.data["outcome"] == "captain_captured"
    assert len(former_crew["roster"]) == 1
    assert former_crew["captain_id"] == former_crew["roster"][0]
    assert former_crew["captain_id"] != prisoner_id
    world.crew_ships.validate_world(world)


def test_sinking_mutual_withdrawal_and_parley_outcomes_are_reachable():
    one_sinks = WorldState.create("outcome-sink-one")
    first, second = _two_crewed_ships(one_sinks)
    one_sinks.entities[first]["hull"]["current"] = 1
    one_sinks.entities[first]["ammunition"] = 0
    _, _, _, sunk = _combat_outcome(one_sinks)
    assert sunk.data["outcome"] == "ship_sunk"

    both_sink = WorldState.create("outcome-sink-both")
    first, second = _two_crewed_ships(both_sink)
    both_sink.entities[first]["hull"]["current"] = 1
    both_sink.entities[second]["hull"]["current"] = 1
    _, _, _, rare = _combat_outcome(both_sink)
    assert rare.data["outcome"] == "both_sunk_rare"

    peaceful = WorldState.create("outcome-withdrawal")
    first, second = _two_crewed_ships(peaceful)
    stances = {first: "avoid", second: "flee"}
    result = peaceful.encounters.resolve(
        peaceful, first, second, caused_by=_encounter_causes(peaceful, first, second),
        stances=stances)
    assert peaceful.event_log.get(result).data["outcome"] == "mutual_withdrawal"

    parley_world = WorldState.create("outcome-parley")
    first, second = _two_crewed_ships(parley_world)
    result = parley_world.encounters.resolve(
        parley_world, first, second, caused_by=_encounter_causes(parley_world, first, second),
        stances={first: "parley", second: "avoid"})
    assert parley_world.event_log.get(result).data["outcome"] == "parley_ended_fight"


def test_escape_can_succeed_and_prisoner_fates_cover_all_recorded_paths(tmp_path):
    escaped = False
    for seed in range(24):
        world = WorldState.create(f"outcome-flee-{seed}")
        first, second = _two_crewed_ships(world)
        world.encounters.data["rules"]["escape_base_basis_points"] = 10000
        result_id = world.encounters.resolve(
            world, first, second, caused_by=_encounter_causes(world, first, second),
            stances={first: "attack", second: "flee"})
        if world.event_log.get(result_id).data["outcome"] == "one_side_flees":
            escaped = True
            break
    assert escaped

    for fate, status in (("ransom", "ransomed"), ("release", "released")):
        world = WorldState.create(f"prisoner-{fate}")
        world.encounters.data["rules"].update({
            "boarding_threshold_basis_points": 10000,
            "surrender_morale_threshold": 0,
            "defection_morale_threshold": 0,
            "captain_capture_basis_points": 10000,
            "captain_wound_basis_points": 0,
            "captain_death_basis_points": 0,
        })
        _, _, _, captured = _combat_outcome(world, boarding=True)
        character_id = captured.data["outcome_details"]["captured_character_id"]
        prisoner_crew = world.encounters.prisoners[character_id]["former_crew_id"]
        captor_crew = world.entities[world.encounters.prisoners[character_id]["captor_ship_id"]]["crew_id"]
        payer_before = world.economy.crew_accounts[prisoner_crew]["treasury_silver"]
        receiver_before = world.economy.crew_accounts[captor_crew]["treasury_silver"]
        event_id = world.encounters.resolve_prisoner_fate(
            world, character_id, fate, caused_by=captured.id)
        assert world.encounters.prisoners[character_id]["status"] == status
        assert event_id in {event.id for event in world.event_log}
        release = world.event_log.get(event_id)
        assert release.type == "PRISONER_RELEASED"
        assert world.entities[character_id]["ship_id"] is None
        assert world.entities[character_id]["crew_id"] is None
        assert world.entities[character_id]["location"] == release.data["location"]
        if fate == "ransom":
            paid = world.event_log.get(release.data["ransom_event_id"])
            assert paid.type == "RANSOM_PAID"
            assert world.economy.crew_accounts[prisoner_crew]["treasury_silver"] < payer_before
            assert world.economy.crew_accounts[captor_crew]["treasury_silver"] > receiver_before
        restored = WorldState.load(world.save(tmp_path / f"prisoner-{fate}.json"))
        assert restored.to_snapshot() == world.to_snapshot()
        _assert_snapshot_continuation_is_deterministic(world)

    for fate in ("recruit", "trade_to_marines"):
        world = WorldState.create(f"unsupported-prisoner-{fate}")
        world.encounters.data["rules"].update({
            "boarding_threshold_basis_points": 10000,
            "surrender_morale_threshold": 0,
            "defection_morale_threshold": 0,
            "captain_capture_basis_points": 10000,
            "captain_wound_basis_points": 0,
            "captain_death_basis_points": 0,
        })
        _, _, _, captured = _combat_outcome(world, boarding=True)
        character_id = captured.data["outcome_details"]["captured_character_id"]
        with pytest.raises(ValueError, match="Unknown prisoner fate"):
            world.encounters.resolve_prisoner_fate(
                world, character_id, fate, caused_by=captured.id)
        assert world.encounters.prisoners[character_id]["status"] == "held"


def test_ship_capture_preserves_merchant_and_sinking_archives_asset_with_conservation():
    captured_world = WorldState.create("consequence-capture")
    captured_world.encounters.data["rules"].update({
        "boarding_threshold_basis_points": 10000,
        "surrender_morale_threshold": 100,
        "surrender_basis_points": 10000,
        "ship_capture_basis_points": 10000,
    })
    first, second = _two_crewed_ships(captured_world)
    before_goods = captured_world.economy._total_goods()
    causes = _encounter_causes(captured_world, first, second)
    result_id = captured_world.encounters.resolve(
        captured_world, first, second, caused_by=causes,
        stances={first: "attack", second: "attack"}, force_combat=True)
    result = captured_world.event_log.get(result_id)
    assert result.data["outcome"] == "ship_captured"
    captured_id = result.data["outcome_details"]["captured_ship_id"]
    winner_id = result.data["outcome_details"]["winner_ship_id"]
    captured_ship = captured_world.entities[captured_id]
    assert captured_ship["legal_owner"] == {
        "kind": "crew", "id": captured_world.entities[winner_id]["crew_id"]}
    assert captured_ship["controller_crew_id"] == captured_ship["crew_id"]
    assert captured_world.ship_is_commandable(captured_id)
    assert captured_world.economy._total_goods() == before_goods
    assert captured_world.encounters.reputation
    # Combat alone no longer mints an unbudgeted bounty payout; the target crew
    # must first cross the government's heat-policy threshold.
    assert not captured_world.encounters.bounty_silver
    captured_world = WorldState.from_snapshot(captured_world.to_snapshot())
    location = captured_world.entities[captured_id]["location"]["id"]
    destination = next(island_id for island_id in sorted(captured_world.economy.islands)
                       if island_id != location)
    captured_world.start_voyage(captured_id, destination, caused_by=result_id)
    assert captured_id in captured_world.travel.transits
    captured_world.economy.ledger.assert_balanced(captured_world.economy)
    captured_world.to_snapshot()

    sunk_world = WorldState.create("consequence-sink")
    first, second = _two_crewed_ships(sunk_world)
    sunk_roster = list(sunk_world.crew_ships.crews[
        sunk_world.entities[first]["crew_id"]]["roster"])
    sunk_crew_id = sunk_world.entities[first]["crew_id"]
    wallet_member = sunk_roster[1]
    held_person_id = sunk_roster[0]
    sunk_world.encounters.prisoners[held_person_id] = {
        "character_id": held_person_id, "captor_ship_id": first,
        "capture_event_id": sunk_world.entities[held_person_id]["last_event_id"],
        "status": "held", "fate_event_id": None,
    }
    sunk_world.entities[first]["hull"]["current"] = 1
    sunk_world.entities[first]["ammunition"] = 0
    before_goods = sunk_world.economy._total_goods()
    before_people = len([row for row in sunk_world.entities.values()
                         if row.get("kind") == "character"])
    before_silver = sunk_world.economy._total_silver()
    result_id = sunk_world.encounters.resolve(
        sunk_world, first, second, caused_by=_encounter_causes(sunk_world, first, second),
        stances={first: "attack", second: "attack"}, force_combat=True)
    assert sunk_world.event_log.get(result_id).data["outcome"] == "ship_sunk"
    assert first not in sunk_world.entities and first in sunk_world.archive
    assert len([row for row in sunk_world.entities.values()
                if row.get("kind") == "character"]) == before_people
    assert all(sunk_world.entities[member]["crew_id"] == sunk_crew_id
               for member in sunk_roster)
    assert all(sunk_world.entities[member]["ship_id"] is None
               for member in sunk_roster)
    assert all(sunk_world.entities[member]["location"] == {
        "kind": "island", "id": sunk_world.location_of(second)["id"]}
        for member in sunk_roster)
    disposition = sunk_world.event_log.by_type("SHIP_LOSS_DISPOSITION")[-1]
    assert list(disposition.data["survivors"]) == sorted(sunk_roster)
    assert list(disposition.data["deceased"]) == []
    assert disposition.data["crew_continues_hull_less"] is True
    assert sunk_world.crew_ships.crews[sunk_crew_id]["ship_ids"] == []
    assert sunk_world.crew_ships.crews[sunk_crew_id]["stranded_location"] == {
        "kind": "island", "id": sunk_world.location_of(second)["id"]}
    assert sunk_crew_id in sunk_world.economy.crew_accounts
    assert sunk_world.crew_ships.crews[sunk_crew_id]["faction_id"] in (
        sunk_world.politics.factions | sunk_world.politics.archived_factions)
    assert list(disposition.data["prisoners_released"]) == [held_person_id]
    assert sunk_world.encounters.prisoners[held_person_id]["status"] == "released"
    assert sunk_world.encounters.prisoners[held_person_id]["fate_event_id"] != disposition.id
    assert sunk_world.entities[held_person_id]["crew_id"] == sunk_crew_id
    assert sunk_world.entities[held_person_id]["location"] == {
        "kind": "island", "id": sunk_world.location_of(second)["id"]}
    assert disposition.data["silver_transferred"] == 0
    assert disposition.data["silver_retained_by_crew"] is True
    assert sunk_world.entities[wallet_member]["wealth"]["silver"] == (
        sunk_world.economy.crew_accounts[sunk_crew_id]["member_wallets"][wallet_member])
    assert sunk_world.economy._total_silver() == before_silver
    assert sunk_world.economy._total_goods() == before_goods
    sunk_world.economy.ledger.assert_balanced(sunk_world.economy)
    with pytest.raises(ValueError, match="valid vessel command authority"):
        sunk_world.crew_ships.recruit(
            sunk_world, sunk_crew_id,
            next(character_id for character_id, row in sunk_world.entities.items()
                 if row.get("kind") == "character" and row.get("crew_id") is None),
            caused_by=disposition.id)
    sunk_world.run_until(24)
    snapshot = sunk_world.to_snapshot()
    resumed = WorldState.from_snapshot(snapshot)
    assert resumed.to_snapshot() == snapshot
    assert resumed.crew_ships.crews[sunk_crew_id]["ship_ids"] == []
    assert resumed.crew_ships.crews[sunk_crew_id]["roster"] == sunk_roster


def test_underway_sink_uses_seeded_independent_fates_and_preserves_missing_people():
    def sink_with_seed(seed):
        world = WorldState.create(seed)
        first_crew = world.crew_ships.crews[sorted(world.crew_ships.crews)[0]]
        ship_id = first_crew["ship_ids"][0]
        ship = world.entities[ship_id]
        crew_id = ship["crew_id"]
        roster = list(world.crew_ships.crews[crew_id]["roster"])
        route_id = sorted(world.geography.routes)[0]
        route = world.geography.routes[route_id]
        origin, destination = sorted((route.origin, route.destination))
        ship["location"] = {"kind": "route", "id": route_id, "from": origin,
                             "to": destination, "progress_permille": 500}
        ship["hull"]["current"] = 0
        cause_id = ship["created_event_id"]

        world.encounters._sink_ship(world, ship_id, cause_id)
        disposition = world.event_log.by_type("SHIP_LOSS_DISPOSITION")[-1]
        world.validate_locations()
        world.crew_ships.validate_world(world)
        return world, roster, disposition.data

    first, roster, data = sink_with_seed("underway-casualty-fates-0")
    _repeat, _roster, repeated = sink_with_seed("underway-casualty-fates-0")
    dispositions = data["survivors"] + data["missing"] + data["deceased"]

    assert sorted(dispositions) == sorted(roster)
    assert len(dispositions) == len(set(dispositions))
    assert data["circumstances"]["casualty_model"] == "independent_keyed_rolls_no_quota"
    assert data["circumstances"]["peril_basis_points"] == 8700
    assert data["missing"]
    assert data["deceased"]
    assert (data["survivors"], data["missing"], data["deceased"]) == (
        repeated["survivors"], repeated["missing"], repeated["deceased"])
    audit = audit_event_records([event.to_record() for event in first.event_log])
    assert audit["population_disposition_errors"] == []
    assert audit["membership_transition_ledger"]["errors"] == []
    assert audit["character_identity_ledger"]["errors"] == []
    assert audit["prisoner_custody_ledger"]["errors"] == []
    assert audit["ship_loss_dispositions"][-1]["status"] == "balanced"
    for character_id in data["missing"]:
        person = first.entities[character_id]
        assert person["missing_at_sea"]["ship_id"] == data["ship_id"]
        assert person["location"]["kind"] == "route"
        assert not first.decisions._eligible(
            first, person, "living_character_with_dream")
    for character_id in data["deceased"]:
        assert first.archive[character_id]["cause_of_death_event"]
    resumed = WorldState.from_snapshot(first.to_snapshot())
    assert resumed.to_snapshot() == first.to_snapshot()


def test_ship_loss_rejects_unresolved_roster_before_any_mutation():
    world = WorldState.create("ship-loss-roster-preflight")
    crew = world.crew_ships.crews[sorted(world.crew_ships.crews)[0]]
    ship_id = crew["ship_ids"][0]
    ship = world.entities[ship_id]
    missing_member_id = crew["roster"][0]
    ship["hull"]["current"] = 0
    world.entities.pop(missing_member_id)
    event_count = len(world.event_log)
    transit_before = dict(world.travel.transits)

    with pytest.raises(ValueError, match="unresolved crew roster member"):
        world.encounters._sink_ship(world, ship_id, ship["created_event_id"])

    assert ship_id in world.entities
    assert len(world.event_log) == event_count
    assert world.travel.transits == transit_before
    assert not world.event_log.by_type("SHIP_SUNK")
    assert not world.event_log.by_type("SHIP_LOSS_DISPOSITION")


def test_missing_person_can_be_rescued_or_confirmed_dead_without_identity_recreation():
    rescued_world = WorldState.create("underway-casualty-fates-0")
    first_crew = rescued_world.crew_ships.crews[sorted(rescued_world.crew_ships.crews)[0]]
    sunk_ship_id = first_crew["ship_ids"][0]
    sunk_ship = rescued_world.entities[sunk_ship_id]
    route_id = sorted(rescued_world.geography.routes)[0]
    route = rescued_world.geography.routes[route_id]
    origin, destination = sorted((route.origin, route.destination))
    sunk_ship["location"] = {"kind": "route", "id": route_id, "from": origin,
                              "to": destination, "progress_permille": 500}
    sunk_ship["hull"]["current"] = 0
    rescued_world.encounters._sink_ship(
        rescued_world, sunk_ship_id, sunk_ship["created_event_id"])
    missing_event = rescued_world.event_log.by_type("CHARACTER_MISSING")[0]
    person_id = missing_event.actors[0]["entity_id"]
    _assert_snapshot_continuation_is_deterministic(rescued_world)
    rescuer_id = next(ship_id for ship_id, ship in rescued_world.entities.items()
                      if ship.get("kind") == "ship")
    rescuer = rescued_world.entities[rescuer_id]
    search_location = rescued_world.entities[person_id]["missing_at_sea"]["search_location"]
    rescuer["location"] = dict(search_location)

    rescued_id = rescued_world.encounters.resolve_missing_person(
        rescued_world, person_id, "found_alive", caused_by=missing_event.id,
        rescuer_ship_id=rescuer_id)

    assert rescued_world.event_log.get(rescued_id).type == "CHARACTER_RESCUED"
    assert person_id in rescued_world.entities
    assert person_id not in rescued_world.archive
    assert rescued_world.entities[person_id].get("missing_at_sea") is None
    assert rescued_world.entities[person_id]["crew_id"] == first_crew["id"]
    assert rescued_world.entities[person_id]["location"] == search_location
    assert first_crew["roster"].count(person_id) == 1
    rescued_world.crew_ships.validate_world(rescued_world)
    assert WorldState.from_snapshot(rescued_world.to_snapshot()).to_snapshot() == (
        rescued_world.to_snapshot())

    deceased_world = WorldState.create("underway-casualty-fates-0")
    crew = deceased_world.crew_ships.crews[sorted(deceased_world.crew_ships.crews)[0]]
    ship_id = crew["ship_ids"][0]
    ship = deceased_world.entities[ship_id]
    ship["location"] = {"kind": "route", "id": route_id, "from": origin,
                        "to": destination, "progress_permille": 500}
    ship["hull"]["current"] = 0
    deceased_world.encounters._sink_ship(deceased_world, ship_id, ship["created_event_id"])
    missing = deceased_world.event_log.by_type("CHARACTER_MISSING")[0]
    doomed_id = missing.actors[0]["entity_id"]
    death_id = deceased_world.encounters.resolve_missing_person(
        deceased_world, doomed_id, "confirmed_dead", caused_by=missing.id)

    assert deceased_world.event_log.get(death_id).type == "CHARACTER_DIED"
    assert doomed_id not in deceased_world.entities
    assert deceased_world.archive[doomed_id]["death_tick"] is not None
    deceased_world.crew_ships.validate_world(deceased_world)


def test_stranded_crew_employment_transfers_people_wallets_assets_and_dissolves_faction():
    world = WorldState.create("stranded-employment-conservation")
    wreck_id, employer_ship_id = _two_crewed_ships(world)
    wreck = world.entities[wreck_id]
    employer_ship = world.entities[employer_ship_id]
    source_id = wreck["crew_id"]
    employer_id = employer_ship["crew_id"]
    stranded = world.crew_ships.crews[source_id]
    faction_id = stranded["faction_id"]
    employer = world.crew_ships.crews[employer_id]
    island_id = world.location_of(employer_ship_id)["id"]
    wreck["location"] = {"kind": "island", "id": island_id}

    # Produce wallet balances through the normal payroll path before merging.
    world.crew_ships._pay_daily_wages(world, source_id, 1)
    source_roster = list(stranded["roster"])
    source_account = world.economy.crew_accounts[source_id]
    source_treasury = source_account["treasury_silver"]
    retained_hull_id, retained_hull_created = world.create_vessel(
        "Unassigned Prize Hull", island_id)
    title_event = world.event_log.append(
        tick=world.clock.tick, event_type="SHIP_ACQUIRED", location=island_id,
        actors=[{"role": "acquiring_crew", "entity_id": source_id}],
        targets=[{"role": "acquired_ship", "entity_id": retained_hull_id}],
        data={"method": "test_prize_title", "legal_owner_kind": "crew",
              "legal_owner_id": source_id, "controller_crew_id": source_id},
        caused_by=[retained_hull_created], causes_tags=["employment_test"])
    world.assign_ship_title(
        retained_hull_id, owner_kind="crew", owner_id=source_id,
        controller_crew_id=source_id, event_id=title_event.id)
    stranded["arrears_silver"] = 13
    unpaid = world.event_log.append(
        tick=world.clock.tick, event_type="WAGES_UNPAID", location=island_id,
        targets=[{"role": "crew", "entity_id": source_id}],
        data={"crew_id": source_id, "unpaid_silver": 13},
        caused_by=[stranded["last_event_id"]], causes_tags=["employment_test"])
    stranded["last_event_id"] = unpaid.id
    employer_roster_before = list(employer["roster"])
    silver_before = world.economy._total_silver()
    goods_before = world.economy._total_goods()
    request = world.event_log.append(
        tick=world.clock.tick, event_type="CREW_EMPLOYMENT_SOUGHT", location=island_id,
        targets=[{"role": "stranded_crew", "entity_id": source_id}],
        data={"crew_id": source_id}, caused_by=[stranded["last_event_id"]],
        causes_tags=["employment_test"])

    world.encounters._sink_ship(world, wreck_id, request.id)
    disposition = world.event_log.by_type("SHIP_LOSS_DISPOSITION")[-1]
    source = world.crew_ships.crews[source_id]
    source["distress_days"] = 30
    world.crew_ships._seek_stranded_employment(world, source_id)
    latest_search = world.event_log.by_type("CREW_EMPLOYMENT_SOUGHT")[-1]

    assert source_id not in world.crew_ships.crews
    assert source_id not in world.economy.crew_accounts
    assert employer["roster"] == sorted(employer_roster_before + source_roster)
    assert len(employer["roster"]) == len(set(employer["roster"]))
    assert all(world.entities[member_id]["crew_id"] == employer_id
               and world.entities[member_id]["ship_id"] == employer_ship_id
               for member_id in source_roster)
    target_account = world.economy.crew_accounts[employer_id]
    transfer_wallets = {
        event.data["character_id"]: event.data["wallet_silver"]
        for event in world.event_log.by_type("CREW_MEMBER_TRANSFERRED")
        if event.data.get("from_crew_id") == source_id
    }
    assert all(target_account["member_wallets"][member_id] >= balance
               for member_id, balance in transfer_wallets.items())
    asset_transfer = world.event_log.by_type("CREW_ASSETS_TRANSFERRED")[-1]
    assert asset_transfer.data["treasury_silver"] <= source_treasury
    assert asset_transfer.data["ship_ids"] == (retained_hull_id,)
    assert world.entities[retained_hull_id]["legal_owner"] == {
        "kind": "crew", "id": employer_id}
    assert world.entities[retained_hull_id]["controller_crew_id"] is None
    transfer_event = world.event_log.get(
        world.entities[retained_hull_id]["ownership_event_id"])
    assert transfer_event.type == "SHIP_ACQUIRED"
    assert asset_transfer.id in transfer_event.caused_by
    assert employer["treasury_silver"] == target_account["treasury_silver"]
    assert employer["arrears_silver"] == 13
    assert asset_transfer.data["arrears_silver"] == 13
    assert world.economy._total_silver() == silver_before
    assert world.economy._total_goods() == goods_before
    assert world.event_log.by_type("CREW_EMPLOYMENT_ACCEPTED")[-1].data["from_crew_id"] == source_id
    assert latest_search.data["eligible_employer_crew_ids"] == (employer_id,)
    assert "stranded_crew_has_missing_or_inactive_member" not in (
        latest_search.data["blocked_candidate_reasons"])
    assert world.event_log.by_type("CREW_DISSOLVED")[-1].data["employed_by_crew_id"] == employer_id
    assert faction_id in world.politics.factions
    assert source_id not in world.politics.factions[faction_id]["member_crew_ids"]
    assert source_id not in world.politics.archived_factions
    assert list(disposition.data["survivors"]) == sorted(source_roster)
    world.crew_ships._pay_daily_wages(world, employer_id, 2)
    world.economy.ledger.assert_balanced(world.economy)
    world.crew_ships.validate_world(world)
    world.validate_ship_authority()
    snapshot = world.to_snapshot()
    assert WorldState.from_snapshot(snapshot).to_snapshot() == snapshot


@pytest.mark.parametrize("condition,expected_blocker", [
    ("missing_member", "stranded_crew_has_missing_or_inactive_member"),
    ("held_prisoner", "stranded_crew_has_held_prisoner"),
    ("marine_crew", "marine_organization_cannot_use_civilian_employment"),
])
def test_stranded_employment_search_reports_ineligible_sources_without_raising(
        condition, expected_blocker):
    world = WorldState.create(f"stranded-employment-blocked-{condition}")
    stranded_ship_id, employer_ship_id = _two_crewed_ships(world)
    stranded_location = world.location_of(employer_ship_id)
    world.entities[stranded_ship_id]["location"] = dict(stranded_location)
    source_crew_id = world.entities[stranded_ship_id]["crew_id"]
    world.encounters._sink_ship(
        world, stranded_ship_id, world.entities[stranded_ship_id]["created_event_id"])
    source = world.crew_ships.crews[source_crew_id]
    member_id = source["roster"][0]

    if condition == "missing_member":
        missing = world.event_log.append(
            tick=world.clock.tick, event_type="CHARACTER_MISSING",
            location=stranded_location["id"],
            actors=[{"role": "missing_person", "entity_id": member_id}],
            data={"character_id": member_id, "ship_id": stranded_ship_id,
                  "last_known_location": {"kind": "route", "id": "route_001"},
                  "search_location": stranded_location},
            caused_by=[source["last_event_id"]], causes_tags=["employment_precondition_test"])
        world.entities[member_id]["missing_at_sea"] = {
            "since_tick": world.clock.tick,
            "last_known_location": {"kind": "route", "id": "route_001"},
            "search_location": dict(stranded_location), "ship_id": stranded_ship_id,
            "cause_event_id": missing.id,
        }
        source["last_event_id"] = missing.id
        world.entities[member_id]["last_event_id"] = missing.id
    elif condition == "held_prisoner":
        held_member = next(person_id for person_id in source["roster"]
                           if person_id != source["captain_id"])
        world.encounters.prisoners[held_member] = {
            "character_id": held_member, "captor_ship_id": employer_ship_id,
            "capture_event_id": source["last_event_id"], "status": "held",
            "fate_event_id": None, "former_crew_id": source_crew_id,
        }
        world.crew_ships.on_character_captured(
            world, held_member, captor_ship_id=employer_ship_id,
            captor_crew_id=world.entities[employer_ship_id]["crew_id"],
            caused_by=source["last_event_id"])
    else:
        source["ideology_tags"] = ["marine"]

    source["distress_days"] = 30
    request_id = world.crew_ships._seek_stranded_employment(world, source_crew_id)
    request = world.event_log.get(request_id)

    assert request.type == "CREW_EMPLOYMENT_SOUGHT"
    assert request.data["eligible_employer_crew_ids"] == ()
    assert request.data["blocked_candidate_reasons"][expected_blocker] == 1
    assert source_crew_id in world.crew_ships.crews
    assert source["ship_ids"] == []
    assert member_id in source["roster"]


def test_sunk_merchant_replaces_vessel_without_losing_finite_trade_account():
    world = WorldState.create("merchant-vessel-recovery")
    island_id = sorted(world.economy.islands)[0]
    merchant_id, created_id = world.create_merchant(
        "Recovered Merchant", island_id, silver=317, capacity_milli=10000,
        cargo_milli={"fish": 2400},
    )
    merchant = world.economy.merchants[merchant_id]
    sunk_ship_id = merchant["ship_id"]
    before_silver = world.economy._total_silver()
    before_goods = world.economy._total_goods()

    sink_id = world.encounters._sink_ship(world, sunk_ship_id, created_id)

    replacement_id = merchant["ship_id"]
    assert replacement_id != sunk_ship_id
    assert sunk_ship_id in world.archive and sunk_ship_id not in world.entities
    assert world.entities[replacement_id]["cargo"] == merchant["cargo_milli"]
    assert merchant["silver"] == 317
    assert merchant["cargo_milli"]["fish"] == 2400
    assert world.economy._total_silver() == before_silver
    assert world.economy._total_goods() == before_goods
    replaced = world.event_log.get(merchant["last_event_id"])
    assert replaced.type == "MERCHANT_VESSEL_REPLACED"
    acquired = next(event for event in world.event_log.by_type("SHIP_ACQUIRED")
                    if event.data.get("successor_of") == sunk_ship_id)
    assert acquired.data["method"] == "replacement"
    assert acquired.targets[0]["entity_id"] == replacement_id
    assert acquired.caused_by == (sink_id, world.entities[replacement_id]["created_event_id"])
    assert acquired.id in replaced.caused_by
    assert world.entities[replacement_id]["created_event_id"]
    assert world.entities[replacement_id]["legal_owner"] == {
        "kind": "merchant", "id": merchant_id}
    assert world.entities[replacement_id]["controller_crew_id"] is None
    with pytest.raises(ValueError, match="no longer owns or commands"):
        world.trade(merchant_id, island_id, "fish", "sell", 100,
                    caused_by=replaced.id)
    replacement_name = world.entities[replacement_id]["name"]
    world.entities[replacement_id]["name"] = "Continuity Test Vessel"
    with pytest.raises(ValueError, match="name requires successor_of"):
        world.create_vessel(world.archive[sunk_ship_id]["name"], island_id)
    successor_id, successor_created = world.create_vessel(
        world.archive[sunk_ship_id]["name"], island_id, successor_of=sunk_ship_id)
    assert world.entities[successor_id]["created_event_id"]
    world.assign_ship_title(
        successor_id, owner_kind="crew", owner_id=next(iter(world.crew_ships.crews)),
        controller_crew_id=None, event_id=successor_created)
    world.entities[successor_id]["name"] = "Explicit Successor Vessel"
    world.entities[replacement_id]["name"] = replacement_name
    world.economy.ledger.assert_balanced(world.economy)
    world.to_snapshot()


def test_weaker_estimated_side_win_is_tagged_as_causal_upset():
    world = WorldState.create("encounter-upset")
    first, second = _two_crewed_ships(world)
    crew_a = world.crew_ships.crews[world.entities[first]["crew_id"]]
    crew_b = world.crew_ships.crews[world.entities[second]["crew_id"]]
    crew_a["morale"] = 100
    crew_b["morale"] = 0
    first_strength = world.crew_ships.combat_power(world, first)["total"]
    second_strength = world.crew_ships.combat_power(world, second)["total"]
    assert first_strength > second_strength
    evidence = crew_a["last_event_id"]
    world.decisions.set_belief(world, crew_a["captain_id"], second,
                               "estimated_strength", 1000,
                               source_event_id=evidence, confidence_permille=900)
    world.decisions.set_belief(world, crew_b["captain_id"], first,
                               "estimated_strength", 1,
                               source_event_id=crew_b["last_event_id"], confidence_permille=900)
    world.entities[first]["ammunition"] = 0
    world.entities[second]["ammunition"] = 0
    world.encounters.data["rules"]["boarding_threshold_basis_points"] = 0
    result_id = world.encounters.resolve(
        world, first, second, caused_by=_encounter_causes(world, first, second),
        stances={first: "attack", second: "attack"}, force_combat=True)
    result = world.event_log.get(result_id)
    upset = result.data["outcome_details"]["belief_upset"]
    assert list(upset["estimated_strengths"]) == [1, 1000]
    assert upset["weaker_estimated_ship_id"] == first
    assert upset["actual_winner_ship_id"] == first
    assert upset["upset"] is True
    assert "upset" in result.causes_tags
    assert result.caused_by


def test_upset_share_stays_in_configured_10_to_30_percent_band_with_trace_tags():
    outcomes = []
    for index in range(40):
        world = WorldState.create(f"upset-band-{index}")
        first, second = _two_crewed_ships(world)
        world.entities[first]["ammunition"] = 0
        world.entities[second]["ammunition"] = 0
        world.encounters.data["rules"]["boarding_threshold_basis_points"] = 0
        ships = [first, second]
        crews = [world.crew_ships.crews[world.entities[ship]["crew_id"]]
                 for ship in ships]
        strengths = [world.crew_ships.combat_power(world, ship)["total"] for ship in ships]
        actually_weaker = min(range(2), key=lambda side: (strengths[side], ships[side]))
        actually_stronger = 1 - actually_weaker
        # Deliberately inject one wrong ranking in every five fights (20% upset rate).
        estimated_weaker = (actually_stronger if index % 5 == 0 else actually_weaker)
        estimates = [1200, 1200]
        estimates[estimated_weaker] = 100
        estimates[1 - estimated_weaker] = 900
        for subject_index, subject_id in enumerate(ships):
            observer = crews[1 - subject_index]
            world.decisions.set_belief(
                world, observer["captain_id"], subject_id, "estimated_strength",
                estimates[subject_index], source_event_id=observer["last_event_id"],
                confidence_permille=900)
        result_id = world.encounters.resolve(
            world, first, second, caused_by=_encounter_causes(world, first, second),
            stances={first: "attack", second: "attack"}, force_combat=True)
        outcomes.append(world.event_log.get(result_id))
    upset_rows = [event for event in outcomes
                  if event.data["outcome_details"]["belief_upset"]["upset"]]
    assert len(upset_rows) * 100 // len(outcomes) == 20
    assert all("upset" in event.causes_tags and event.caused_by for event in upset_rows)


def test_failed_flee_attempt_weakens_ship_before_combat(monkeypatch):
    world = WorldState.create("encounter-failed-flee")
    first, second = _two_crewed_ships(world)
    ship = world.entities[second]
    before = ship["rigging"]["current"]
    morale = world.crew_ships.crews[ship["crew_id"]]["morale"]
    ship["ammunition"] = 0
    world.entities[first]["ammunition"] = 0
    monkeypatch.setattr(world.encounters, "_escape_succeeds", lambda *args: False)
    result_id = world.encounters.resolve(
        world, first, second, caused_by=_encounter_causes(world, first, second),
        stances={first: "attack", second: "flee"})
    result = world.event_log.get(result_id)
    failed = world.event_log.by_type("FLEE_FAILED")[-1]
    assert result.data["outcome"] == "combat_resolved"
    assert result.data["phases"][0]["outcome"] == "failed_flee"
    damage = world.event_log.get(result.data["damage_event_ids"][0])
    assert damage.caused_by == (failed.id,)
    assert damage.data["component"] == "rigging"
    assert damage.data["damage"] == failed.data["weakened_rigging"] == 1
    assert damage.data["after"] == before - 1
    assert damage.id in result.caused_by
    assert ship["rigging"]["current"] <= before - 1
    assert failed.data["morale_loss"] == 5
    assert world.crew_ships.crews[ship["crew_id"]]["morale"] <= morale - 5


def test_encounter_captain_death_archives_person_and_replaces_major_population():
    world = WorldState.create("encounter-captain-replacement")
    world.encounters.data["rules"].update({
        "captain_death_basis_points": 10000,
        "captain_wound_basis_points": 0,
        "boarding_threshold_basis_points": 0,
    })
    majors = sorted(entity_id for entity_id, row in world.entities.items()
                    if row.get("kind") == "character" and row.get("tier") == "MAJOR")
    for character_id in majors[:5]:
        if character_id not in world.entities:
            continue
        world.characters.kill_character(
            world, character_id, cause="test_battle", caused_by=
            world.entities[character_id]["last_event_id"], tags=["combat_casualty"])
    assert all(character_id in world.archive for character_id in majors[:5])
    rookies = world.event_log.by_type("ROOKIE_SPAWNED")
    assert len(rookies) >= 1
    assert sum(1 for row in world.entities.values()
               if row.get("kind") == "character" and row.get("tier") in {"LEGEND", "MAJOR"}) >= 8
    world.to_snapshot()


def _encounter_causes(world, first, second):
    return [world.crew_ships.crews[world.entities[ship]["crew_id"]]["last_event_id"]
            for ship in (first, second)]
