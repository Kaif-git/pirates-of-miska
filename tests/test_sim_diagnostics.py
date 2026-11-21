import json
from pathlib import Path

import pytest

from sim.core.world import WorldState
from sim.core.actor_bands import classify_ship_actor_band
from sim.config import load_config, validate_config
from sim.tools.batch_test import (_actor_band_bounds, _actor_band_counts,
                                  _actor_band_status, _actor_population_census, _screen_stat,
                                  _actor_population_transitions, _dream_story_funnel,
                                  _pending_prize_crew_audit, _terminal_actor_violations,
                                  _write_diagnostics,
                                  _year_diagnostics)


def test_fixed_seed_screen_reports_per_seed_counts_and_labeled_confidence_limits():
    result = _screen_stat([0, 1, 0, 3])

    assert result["n"] == 4
    assert result["values"] == [0, 1, 0, 3]
    assert result["mean"] == 1
    assert result["minimum"] == 0 and result["maximum"] == 3
    assert result["normal_approximation_95_ci_mean"][0] <= 1
    assert result["normal_approximation_95_ci_mean"][1] >= 1
    assert result["wilson_95_ci_nonzero_seed_fraction"][0] < 0.5
    assert result["wilson_95_ci_nonzero_seed_fraction"][1] > 0.5
    assert _screen_stat([0])["normal_approximation_95_ci_mean"] is None


def test_dream_story_funnel_keeps_selection_attempt_block_reason_and_resolution_distinct():
    world = WorldState.create("dream-funnel-report")
    cause = world.event_log.by_type("WORLD_GENERATED")[0]
    records = (
        ("DREAM_PLAN_CREATED", {"dream_kind": "treasure"}, "dreamer"),
        ("PLAN_STEP_STARTED", {"dream_kind": "treasure", "step_id": "sail"}, "planner"),
        ("PLAN_STEP_BLOCKED", {"dream_kind": "treasure", "step_id": "sail",
                                "reason": "vessel_not_berthed"}, "planner"),
        ("PLAN_STEP_COMPLETED", {"dream_kind": "treasure", "step_id": "find"}, "planner"),
        ("DREAM_PLAN_COMPLETED", {"dream_kind": "treasure"}, "dreamer"),
    )
    events = []
    for event_type, data, role in records:
        event = world.event_log.append(
            tick=world.clock.tick, event_type=event_type,
            actors=[{"role": role, "entity_id": "character_fixture"}],
            data=data, caused_by=[cause.id], causes_tags=["funnel_fixture"], magnitude=0.1)
        events.append(event)

    funnel = _dream_story_funnel(events)

    assert funnel["counts_by_event_type"]["DREAM_PLAN_CREATED"] == 1
    assert funnel["counts_by_event_type"]["PLAN_STEP_STARTED"] == 1
    assert funnel["counts_by_event_type"]["PLAN_STEP_BLOCKED"] == 1
    assert funnel["counts_by_event_type"]["PLAN_STEP_COMPLETED"] == 1
    assert funnel["counts_by_event_type"]["DREAM_PLAN_COMPLETED"] == 1
    assert funnel["blocked_failed_abandoned_by_reason"] == {
        "PLAN_STEP_BLOCKED|treasure|sail|vessel_not_berthed": 1}
    blocked = funnel["by_actor_action_reason"][0]
    assert blocked["actor_id"] == "character_fixture"
    assert blocked["sample_caused_by"] == [[cause.id]]


def test_pending_prize_audit_identifies_transit_hull_without_mutating_world():
    world = WorldState.create("pending-prize-transit-audit")
    ship_id = next(entity_id for entity_id, row in world.entities.items()
                   if row.get("kind") == "ship")
    ship = world.entities[ship_id]
    crew_id = ship["crew_id"]
    destination = next(island_id for island_id in sorted(world.geography.islands)
                       if island_id != ship["location"]["id"])
    world.start_voyage(ship_id, destination, caused_by=ship["created_event_id"])
    ship["captured_by_crew_id"] = crew_id
    before_events = world.event_log.to_jsonl()
    before_transit = dict(world.travel.transits[ship_id])

    audit = next(row for row in _pending_prize_crew_audit(world)
                 if row["ship_id"] == ship_id)

    assert audit["in_transit"] is True
    assert "captured_hull_in_transit" in audit["resolution_blockers"]
    assert audit["transit"] == before_transit
    assert world.event_log.to_jsonl() == before_events
    assert world.travel.transits[ship_id] == before_transit


def test_diagnostic_writer_keeps_run_identity_scope_and_actor_band_vectors(tmp_path):
    counts = {"merchant_ships": 2, "pirate_ships": 1}
    bounds = {"merchant_ships": {"minimum": 1, "target": 2, "maximum": 3},
              "pirate_ships": {"minimum": 2, "target": 2, "maximum": 4}}
    actor_bands = _actor_band_status(counts, bounds)
    yearly = [{"year": 1, "tick_range": [1, 2],
               "event_counts_by_type": {"A": 2, "B": 1},
               "events_by_class": {"routine": 3},
               "system_zero_call_over_30_days": [], "actor_bands": actor_bands,
               "story_outcomes": {}, "alive_ships_by_owner_type": {},
               "alive_characters_by_tier_and_role": {}, "births_or_recruits": 0,
               "deaths_by_cause": {}, "major_plus_with_valid_next_decision_tick": None,
               "major_plus_with_non_idle_plan": None,
               "decision_scoring": {"scored_decisions": 0,
                                    "executed_by_action_and_tier": {}, "scored_by_tier": {}},
               "lifecycle_diagnostics": [], "vendettas_created": 0,
               "vendettas_converted_to_action": 0}]

    paths = _write_diagnostics(
        7, yearly, run_id="run-fixture", metadata={"effective_config_sha256": "abc"},
        output_dir=tmp_path / "run-fixture", total_event_count=5, initial_event_count=2,
        actor_population_census=[])
    report = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))

    assert report["run_id"] == "run-fixture" and report["seed"] == 7
    assert report["metadata"]["effective_config_sha256"] == "abc"
    assert report["tick_ranges"] == [[1, 2]]
    assert report["event_count_scope"] == {
        "yearly_tick_range_exclusive_inclusive": "(start_tick, end_tick]",
        "yearly_event_count": 3,
        "initial_tick_zero_event_count": 2,
        "total_event_count": 5,
        "yearly_plus_initial_reconciles": True,
    }
    assert report["years"][0]["actor_bands"]["counts"] == counts
    assert report["years"][0]["actor_bands"]["bands"]["merchant_ships"]["pass"]
    assert not report["years"][0]["actor_bands"]["bands"]["pirate_ships"]["pass"]
    assert Path(paths["json"]).parent == tmp_path / "run-fixture"


@pytest.mark.parametrize(("faction_kind", "expected"), [
    ("actor_merchant", "merchant"), ("merchant_league", "merchant"),
    ("actor_pirate", "pirate"), ("raider", "pirate"),
    ("actor_marine", "marine"), ("patrol", "marine"),
    ("actor_privateer", "privateer"),
])
def test_shared_actor_band_classifier_recognizes_census_faction_aliases(faction_kind, expected):
    # Previous recruitment accounting omitted actor_* aliases accepted by the census.
    assert classify_ship_actor_band(
        {"owner_type": "merchant"}, faction_id="faction-1", faction_kind=faction_kind,
        merchant_registered=False) == expected


def test_shared_actor_band_classifier_does_not_count_unknown_faction_as_stale_owner():
    assert classify_ship_actor_band(
        {"owner_type": "pirate"}, faction_id="faction-unknown", faction_kind="other",
        merchant_registered=False) is None
    assert classify_ship_actor_band(
        {"owner_type": "pirate"}, faction_id=None, faction_kind="",
        merchant_registered=False) == "pirate"
    assert classify_ship_actor_band(
        {"owner_type": "pirate"}, faction_id=None, faction_kind="",
        merchant_registered=True) == "merchant"


def test_actor_recruitment_diagnostic_records_resource_block_without_spawning_assets():
    world = WorldState.create("actor-recruitment-block-reason")
    for island in world.economy.islands.values():
        for good in ("grain", "water", "rum"):
            island["stockpile_milli"][good] = 0
    world.crew_ships.actor_deadlines = {
        "merchant": 0, "pirate": 999, "privateer": 999, "marine": 999,
        "promotion": 999, "legend": 999,
    }
    ships_before = sum(row.get("kind") == "ship" for row in world.entities.values())

    world.crew_ships._recruit_actor_bands(world, 0)

    ships_after = sum(row.get("kind") == "ship" for row in world.entities.values())
    assert ships_after == ships_before
    assert world.crew_ships.actor_recruitment_diagnostics["merchant"] == {
        "opportunities": 1, "recruited": 0,
        "blocked": {"insufficient_local_provisioning_stock": 1},
    }


def test_actor_population_transition_summary_deduplicates_capture_acquisition_pair():
    world = WorldState.create("actor-transition-fixture")
    start_event = len(world.event_log)
    root = world.event_log.by_type("WORLD_GENERATED")[0]
    for event_type, data, tags in (
            ("SHIP_ACQUIRED", {"owner_type": "merchant", "method": "build"},
             ["actor_budget_recruitment"]),
            ("SHIP_CAPTURED", {"prize_crew_pending": True},
             ["ship_capture", "ownership_change"]),
            ("SHIP_ACQUIRED", {"owner_type": "pirate", "method": "capture"},
             ["ship_acquisition", "capture"]),
            ("SHIP_ACQUIRED", {"owner_type": "pirate", "method": "transfer"},
             ["ownership_change"]),
            ("SHIP_ACQUIRED", {"method": "pending_prize_crew_installed"},
             ["ship_control_assignment", "prize_crew_recovery"]),
            ("PRIZE_CREW_INSTALLED", {}, ["prize_crew", "captain_succession"]),
            ("SHIP_LOSS_DISPOSITION", {
                "ship_id": "ship-test", "crew_id": "crew-test",
                "crew_roster_before": ["p1", "p2", "p3"],
                "survivors": ["p1"], "missing": ["p2"], "deceased": ["p3"],
                "prisoners_released": [],
            }, ["ship_loss", "population_disposition"])):
        world.event_log.append(
            tick=world.clock.tick, event_type=event_type, data=data,
            caused_by=[root.id], causes_tags=tags, magnitude=0.1)

    transitions = _actor_population_transitions(world, start_event)

    assert transitions["resource_backed_recruitments_by_band"]["merchant"]["count"] == 1
    assert transitions["ship_captures"]["count"] == 1
    assert transitions["captures_pending_prize_crew"]["count"] == 1
    assert transitions["prize_crew_installations"]["count"] == 1
    assert transitions["prize_crew_control_recoveries"]["count"] == 1
    assert transitions["ship_loss_dispositions"]["events"][0]["member_partition_reconciles"]
    assert transitions["other_title_transfers"] == {"count": 1, "event_ids": [
        next(event.id for event in world.event_log.by_type("SHIP_ACQUIRED")
             if event.data.get("method") == "transfer")]}


def test_terminal_plan_failure_bookkeeping_is_not_a_post_death_actor_action():
    world = WorldState.create("terminal-plan-bookkeeping")
    person_id = next(key for key, row in world.entities.items()
                     if row.get("kind") == "character")
    root_id = world.event_log.by_type("WORLD_GENERATED")[0].id
    death = world.event_log.append(
        tick=world.clock.tick, event_type="CHARACTER_DIED",
        targets=[{"role": "deceased", "entity_id": person_id}],
        data={"cause": "fixture"}, caused_by=[root_id], causes_tags=["fixture"], magnitude=0.2)
    failed = world.event_log.append(
        tick=world.clock.tick, event_type="DREAM_PLAN_FAILED",
        actors=[{"role": "dreamer", "entity_id": person_id}],
        data={"reason": "death"}, caused_by=[death.id], causes_tags=["fixture"], magnitude=0.1)
    world.event_log.append(
        tick=world.clock.tick, event_type="AGENT_ACTION",
        actors=[{"role": "agent", "entity_id": person_id}],
        data={"action_id": "fixture"}, caused_by=[failed.id],
        causes_tags=["fixture"], magnitude=0.1)

    violations = _terminal_actor_violations(world.event_log)

    assert len(violations) == 1
    assert violations[0]["event_type"] == "AGENT_ACTION"


def test_world_records_daily_system_diagnostic_calls_without_snapshot_state():
    world = WorldState.create(0)
    world.run_until(40 * 24)

    assert world.system_diagnostics["economy"]["call_count"] == 40
    assert world.system_diagnostics["ai"]["last_fired_tick"] == 40 * 24
    snapshot = world.to_snapshot()
    assert "system_diagnostics" not in snapshot


def test_year_diagnostics_reports_system_calls_and_zero_call_system_names():
    world = WorldState.create(1)
    world.run_until(40 * 24)
    root = world.event_log.by_type("WORLD_GENERATED")[0]
    for event_type, data in (
            ("DREAM_PLAN_COMPLETED", {"dream_kind": "find_treasure"}),
            ("TREASURE_CLUE_LEARNED", {"treasure_id": "fixture"}),
            ("TREASURE_FOUND", {"treasure_id": "fixture"}),
            ("PROFITABLE_TRADE_RIVALRY", {"trade_event_ids": []}),
            ("PUBLIC_RENOWN_EARNED", {"deed_kind": "combat_victory"}),
            ("FEUD_RAID_ATTEMPTED", {"outcome": "repelled"})):
        world.event_log.append(
            tick=world.clock.tick, event_type=event_type, data=data,
            caused_by=[root.id], causes_tags=["diagnostic_fixture"], magnitude=0.1)

    diagnostics, _ = _year_diagnostics(world, 0, 40 * 24, 0, {})

    assert diagnostics["systems"]["economy"]["call_count"] == 40
    assert diagnostics["systems"]["director"]["last_fired_tick"] == 40 * 24
    assert "director" not in diagnostics["system_zero_call_over_30_days"]
    assert diagnostics["alive_ships_by_owner_type"]
    assert diagnostics["events_by_class"]["unclassified"] == 0
    assert sum(diagnostics["events_by_class"][kind]
               for kind in ("routine", "narrative", "structural")) == sum(
                    event.tick > 0 for event in world.event_log)
    assert diagnostics["actor_bands"]["counts"]["merchant_ships"] == 6
    assert diagnostics["actor_bands"]["bounds"]["merchant_ships"] == {
        "minimum": 32, "target": 60, "maximum": 80,
    }
    assert diagnostics["event_counts_by_type"]["TREASURE_FOUND"] == 1
    assert diagnostics["story_outcomes"]["dream_plans"]["DREAM_PLAN_COMPLETED"] == 1
    assert diagnostics["story_outcomes"]["public_deeds_by_kind"]["combat_victory"] >= 1
    assert diagnostics["story_outcomes"]["public_deeds_by_kind"]["profitable_trade"] > 0
    assert diagnostics["story_outcomes"]["rivalry_attempts"] == 1


def test_actor_bands_count_only_living_capable_characters_and_active_ships():
    world = WorldState.create(5)
    counts = _actor_band_counts(world)
    assert counts["merchant_ships"] == 3
    assert counts["pirate_ships"] == 1
    assert counts["privateer_ships"] == 1
    assert counts["marine_ships"] == 1
    assert counts["major_captains"] + counts["legends"] == 3
    assert _actor_band_bounds(world)["legends"] == {
        "minimum": 2, "target": 3, "maximum": 6,
    }


def test_actor_population_census_separates_hulls_crew_captain_owner_and_controller():
    world = WorldState.create("actor-census-fixture")

    census = _actor_population_census(world, label="opening")
    merchant = census["bands"]["merchant"]

    assert census["tick"] == 0 and census["label"] == "opening"
    assert census["total_physical_hulls"] == 6
    assert census["total_commandable_hulls"] == 6
    assert merchant["physical_hulls"] > 0
    assert merchant["hulls_with_active_crew"] == merchant["physical_hulls"]
    assert merchant["hulls_with_living_captain"] == merchant["physical_hulls"]
    assert merchant["legal_owner_kinds"] == {"merchant": merchant["physical_hulls"]}
    assert merchant["controllers_present"] == merchant["physical_hulls"]
    for band in ("merchant", "pirate", "marine", "privateer"):
        assert world.crew_ships._active_actor_ship_count(world, band) == \
            _actor_band_counts(world)[f"{band}_ships"]

    ship_id = next(ship_id for ship_id, row in world.entities.items()
                   if row.get("kind") == "ship")
    world.entities[ship_id]["controller_crew_id"] = "pending-controller"
    evidence = _actor_population_census(world, label="controller-mismatch")
    record = next(row for row in evidence["uncommandable_hulls"]
                  if row["ship_id"] == ship_id)
    assert record["reason"] == "onboard_crew_is_not_controller"
    assert record["onboard_crew_id"] and record["ownership_event_id"]
    assert record["crew_member_dispositions"]
    assert all(row["last_event_id"] for row in record["crew_member_dispositions"])
    assert record["control_audit"]["classification"] == "stale_controller_crew_reference"
    assert record["control_audit"]["ownership_event"]["event_id"] == \
        record["ownership_event_id"]
    world.entities[ship_id]["captured_by_crew_id"] = "pending-controller"
    captured = _actor_population_census(world, label="pending-prize-crew")
    captured_record = next(row for row in captured["uncommandable_hulls"]
                           if row["ship_id"] == ship_id)
    assert captured_record["reason"] == "captured_pending_prize_crew"
    assert captured_record["control_audit"]["classification"] == \
        "pending_prize_crew_assignment"
    assert captured["living_character_dispositions"][
        "aboard_captured_hull_pending_prize_crew"] >= 1


def test_actor_budget_configuration_rejects_inverted_ranges():
    config = load_config()
    config["world"]["actors"]["ship_bands"]["merchant"]["target_per_island_milli"] = 500
    with pytest.raises(ValueError, match="actor ship budget: merchant"):
        validate_config(config)


def test_events_have_data_driven_class_and_contextual_weather_class():
    world = WorldState.create(2)
    root = world.event_log.by_type("WORLD_GENERATED")[0]
    economy_event = world.event_log.by_type("ISLAND_ECONOMY_INITIALIZED")[0]
    assert root.event_class == "structural"
    assert economy_event.event_class == "routine"

    weather = world.event_log.append(
        tick=0, event_type="NATURAL_EVENT",
        data={"kind": "WEATHER_CHANGED", "region_id": "region_001"},
        causes_tags=["weather-test"],
    )
    assert weather.event_class == "routine"
    record = weather.to_record()
    assert record["class"] == "routine"
    restored = type(world.event_log).from_records(
        world.event_log.root_event_types, world.event_log.to_records(),
        world.config["event_rules"]["class_by_type"],
    )
    assert restored.get(weather.id).event_class == "routine"
    with pytest.raises(ValueError, match="Event class"):
        world.event_log.append(tick=0, event_type="WORLD_GENERATED",
                               causes_tags=["invalid-class-test"], event_class="")


def test_narrative_classification_requires_a_consequential_outcome():
    world = WorldState.create("consequence-aware-event-classes")
    log = world.event_log
    root = log.by_type("WORLD_GENERATED")[0]
    detection = log.append(tick=0, event_type="ENCOUNTER_DETECTED",
                           caused_by=[root.id])
    withdrawal = log.append(
        tick=0, event_type="ENCOUNTER_RESOLVED",
        data={"outcome": "mutual_withdrawal", "damage_event_ids": [],
              "outcome_event_ids": [], "gunnery_damage": [0, 0]},
        caused_by=[detection.id])
    escape = log.append(
        tick=0, event_type="ENCOUNTER_RESOLVED",
        data={"outcome": "one_side_flees", "damage_event_ids": [],
              "outcome_event_ids": [], "gunnery_damage": [0, 0]},
        caused_by=[detection.id])
    no_impact_raid = log.append(
        tick=0, event_type="FEUD_RAID_ATTEMPTED",
        data={"outcome": "repelled", "material_transfer": False},
        caused_by=[detection.id])
    consequential_raid = log.append(
        tick=0, event_type="FEUD_RAID_ATTEMPTED",
        data={"outcome": "repelled", "material_transfer": True},
        caused_by=[detection.id])
    plain_rumor = log.append(
        tick=0, event_type="RUMOR_SHARED",
        data={"distorted": False, "value": 12, "original_value": 12},
        caused_by=[detection.id])
    distorted_rumor = log.append(
        tick=0, event_type="RUMOR_SHARED",
        data={"distorted": True, "value": 13, "original_value": 12},
        caused_by=[detection.id])
    action_intent = log.append(
        tick=0, event_type="AGENT_ACTION", event_class="narrative",
        data={"action_id": "pursue_grudge"}, caused_by=[detection.id])
    selected = log.append(tick=0, event_type="RAIDER_TARGET_SELECTED",
                          caused_by=[detection.id])
    observation = log.append(tick=0, event_type="RAIDER_INTEL_OBSERVED",
                             caused_by=[detection.id])
    chart = log.append(tick=0, event_type="RAIDER_CHART_REVIEWED",
                       caused_by=[detection.id])
    pursuit = log.append(tick=0, event_type="RAIDER_PURSUIT_COMMITTED",
                         caused_by=[selected.id])
    pursuit_result = log.append(
        tick=0, event_type="RAIDER_PURSUIT_RESOLVED",
        data={"outcome": "contact"}, caused_by=[pursuit.id])

    assert detection.event_class == "routine"
    assert withdrawal.event_class == "routine"
    assert escape.event_class == "narrative"
    assert no_impact_raid.event_class == "routine"
    assert consequential_raid.event_class == "narrative"
    assert plain_rumor.event_class == "routine"
    assert distorted_rumor.event_class == "narrative"
    assert action_intent.event_class == "routine"
    assert selected.event_class == "routine"
    assert observation.event_class == "routine"
    assert chart.event_class == "routine"
    assert pursuit.event_class == "routine"
    assert pursuit_result.event_class == "routine"


def test_compaction_preserves_routine_causes_directly_used_by_narrative_events():
    world = WorldState.create("routine-cause-alias")
    root = world.event_log.by_type("WORLD_GENERATED")[0]
    contact = world.event_log.append(
        tick=0, event_type="ENCOUNTER_DETECTED", caused_by=[root.id])
    withdrawal = world.event_log.append(
        tick=0, event_type="ENCOUNTER_RESOLVED",
        data={"outcome": "mutual_withdrawal", "damage_event_ids": [],
              "outcome_event_ids": [], "gunnery_damage": [0, 0]},
        caused_by=[contact.id])
    consequence = world.event_log.append(
        tick=0, event_type="BOUNTY_SET",
        data={"amount_silver": 10}, caused_by=[withdrawal.id])

    raw_before = world.event_log.to_jsonl()
    stats = world.event_log.compaction_stats()
    compact = world.event_log.to_compacted_records()
    aliases = {event_id for row in compact if row.get("record_kind") == "routine_aggregate"
               for day in row["days"] for event_id in day.get("event_ids", [])}
    retained_story = next(row for row in compact if row.get("id") == consequence.id)

    assert contact.event_class == withdrawal.event_class == "routine"
    assert withdrawal.id in aliases
    assert retained_story["caused_by"] == [withdrawal.id]
    assert stats["raw_rows"] == len(world.event_log)
    assert stats["compact_rows"] == len(compact)
    assert stats["compact_bytes"] < stats["raw_bytes"]
    assert world.event_log.to_jsonl() == raw_before


def test_major_agents_receive_scheduled_decisions_without_external_event_wakes():
    world = WorldState.create(3)
    world.decisions.event_cursor = len(world.event_log)

    completed = world.decisions.process_scheduled_agents(world)

    assert completed
    actions = world.event_log.by_type("AGENT_ACTION")
    assert len(actions) == len(completed)
    assert all((world.entities.get(event.actors[0]["entity_id"])
                or world.archive.get(event.actors[0]["entity_id"]))["tier"]
               in {"MAJOR", "LEGEND"} for event in actions)
