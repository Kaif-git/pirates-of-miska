import copy

import pytest

from sim.core.world import WorldState
from sim.systems.encounters import load_encounter_data, validate_encounter_data


def _two_crewed_ships(world):
    ship_ids = sorted(key for key, entity in world.entities.items()
                      if entity.get("kind") == "ship")[:2]
    first, second = ship_ids
    world.entities[second]["location"] = dict(world.entities[first]["location"])
    return first, second


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
    surrendered_ship = next(ship for ship in surrender.data["ships"]
                            if surrender_world.entities[ship].get("captured_by_crew_id") is not None)
    assert surrender_world.entities[surrendered_ship]["captured_by_crew_id"]
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
    fate_event = prisoner_world.encounters.resolve_prisoner_fate(
        prisoner_world, prisoner_id, "execute", caused_by=captured.id)
    assert prisoner_world.archive[prisoner_id]["death_tick"] == prisoner_world.clock.tick
    assert prisoner_world.encounters.prisoners[prisoner_id]["status"] == "executed"
    assert prisoner_world.event_log.get(fate_event).type == "CHARACTER_DIED"
    prisoner_world.event_log.validate_causality()


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

    for fate, status in (("ransom", "ransomed"), ("recruit", "recruited"),
                         ("release", "released"), ("trade_to_marines", "traded")):
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
        prisoner_crew = world.entities[character_id]["crew_id"]
        captor_crew = world.entities[world.encounters.prisoners[character_id]["captor_ship_id"]]["crew_id"]
        payer_before = world.economy.crew_accounts[prisoner_crew]["treasury_silver"]
        receiver_before = world.economy.crew_accounts[captor_crew]["treasury_silver"]
        event_id = world.encounters.resolve_prisoner_fate(
            world, character_id, fate, caused_by=captured.id)
        assert world.encounters.prisoners[character_id]["status"] == status
        assert event_id in {event.id for event in world.event_log}
        if fate == "ransom":
            paid = world.event_log.get(event_id)
            assert paid.type == "RANSOM_PAID"
            assert world.economy.crew_accounts[prisoner_crew]["treasury_silver"] < payer_before
            assert world.economy.crew_accounts[captor_crew]["treasury_silver"] > receiver_before
        restored = WorldState.load(world.save(tmp_path / f"prisoner-{fate}.json"))
        assert restored.to_snapshot() == world.to_snapshot()


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
    assert captured_world.entities[captured_id]["captured_by_crew_id"] == \
        captured_world.entities[winner_id]["crew_id"]
    assert captured_world.economy._total_goods() == before_goods
    assert captured_world.encounters.reputation
    # Combat alone no longer mints an unbudgeted bounty payout; the target crew
    # must first cross the government's heat-policy threshold.
    assert not captured_world.encounters.bounty_silver
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
    sunk_world.entities[first]["hull"]["current"] = 1
    sunk_world.entities[first]["ammunition"] = 0
    before_goods = sunk_world.economy._total_goods()
    before_people = len([row for row in sunk_world.entities.values()
                         if row.get("kind") == "character"])
    result_id = sunk_world.encounters.resolve(
        sunk_world, first, second, caused_by=_encounter_causes(sunk_world, first, second),
        stances={first: "attack", second: "attack"}, force_combat=True)
    assert sunk_world.event_log.get(result_id).data["outcome"] == "ship_sunk"
    assert first not in sunk_world.entities and first in sunk_world.archive
    assert len([row for row in sunk_world.entities.values()
                if row.get("kind") == "character"]) == before_people
    assert all(sunk_world.entities[member]["crew_id"] is None
               for member in sunk_roster)
    assert sunk_world.economy._total_goods() == before_goods
    sunk_world.economy.ledger.assert_balanced(sunk_world.economy)
    sunk_world.to_snapshot()


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
    followup_trade = world.trade(merchant_id, island_id, "fish", "sell", 100,
                                 caused_by=replaced.id)
    assert world.event_log.get(followup_trade["id"]).tick == world.clock.tick
    assert world.event_log.get(followup_trade["id"]).id != acquired.id
    event_ids = [event.id for event in world.event_log]
    assert event_ids.index(acquired.id) < event_ids.index(followup_trade["id"])
    replacement_name = world.entities[replacement_id]["name"]
    world.entities[replacement_id]["name"] = "Continuity Test Vessel"
    with pytest.raises(ValueError, match="name requires successor_of"):
        world.create_vessel(world.archive[sunk_ship_id]["name"], island_id)
    successor_id, _ = world.create_vessel(
        world.archive[sunk_ship_id]["name"], island_id, successor_of=sunk_ship_id)
    assert world.entities[successor_id]["created_event_id"]
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
