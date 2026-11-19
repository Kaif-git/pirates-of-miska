import copy
import json
import hashlib
from pathlib import Path
import subprocess
import sys

import pytest

from sim.core.events import decode_compacted_record
from sim.core.world import SNAPSHOT_SCHEMA_VERSION, WorldState
from sim.tools.batch_test import _event_log_sha256, _trade_volume_stable


ROOT = Path(__file__).resolve().parents[1]


def _world_with_pending_events():
    world = WorldState.create("snapshot-seed")
    world.rng.stream("weather").random()
    world.ids.next("ship")
    root_id = world.event_log.get("event_000001").id
    world.schedule_event(5, "NATURAL_EVENT", location="isle_001",
                         data={"kind": "storm"}, causes_tags=["weather"], magnitude=0.6)
    world.schedule_event(8, "WORLD_GENERATED", data={"child_world": True},
                         causes_tags=["test_root"])
    world.run_until(2)
    return world


def test_snapshot_resume_matches_uninterrupted_future(tmp_path):
    uninterrupted = _world_with_pending_events()
    resumed_base = _world_with_pending_events()
    snapshot_path = tmp_path / "midrun.json"
    resumed_base.save(snapshot_path)
    resumed = WorldState.load(snapshot_path)

    uninterrupted.run_until(12)
    resumed.run_until(12)

    assert uninterrupted.event_log.to_jsonl() == resumed.event_log.to_jsonl()
    assert uninterrupted.to_snapshot() == resumed.to_snapshot()
    assert resumed.ids.next("ship") == uninterrupted.ids.next("ship")
    assert resumed.rng.stream("weather").random() == uninterrupted.rng.stream("weather").random()


def test_weather_and_event_log_match_across_chunked_advancement():
    whole = WorldState.create("weather-chunking")
    chunked = WorldState.create("weather-chunking")
    whole.run_until(24 * 120)
    for day in (1, 2, 3, 9, 10, 35, 36, 119, 120):
        chunked.run_until(day * 24)

    assert whole.weather.snapshot() == chunked.weather.snapshot()
    assert whole.event_log.to_jsonl() == chunked.event_log.to_jsonl()
    assert len(whole.event_log.by_type("NATURAL_EVENT")) > 0
    assert all(event.data["calendar_season"] in {"spring", "summer", "autumn", "winter"}
               for event in whole.event_log.by_type("NATURAL_EVENT"))


def test_weather_state_round_trips_and_resumes_deterministically(tmp_path):
    uninterrupted = WorldState.create("weather-resume")
    uninterrupted.run_until(24 * 40)
    resumed = WorldState.load(uninterrupted.save(tmp_path / "weather.json"))

    uninterrupted.run_until(24 * 90)
    resumed.run_until(24 * 90)

    assert uninterrupted.weather.snapshot() == resumed.weather.snapshot()
    assert uninterrupted.event_log.to_jsonl() == resumed.event_log.to_jsonl()
    assert uninterrupted.to_snapshot() == resumed.to_snapshot()


def test_same_seed_and_same_schedule_produce_byte_identical_event_logs():
    worlds = [WorldState.create(2026), WorldState.create(2026)]
    for world in worlds:
        world.schedule_event(24, "NATURAL_EVENT", location="route_001",
                             data={"kind": "squall"}, causes_tags=["weather"])
        world.schedule_event(3, "NATURAL_EVENT", location="isle_002",
                             data={"kind": "harvest"}, causes_tags=["season"])
        world.run_until(30)

    assert worlds[0].event_log.to_jsonl().encode("utf-8") == worlds[1].event_log.to_jsonl().encode("utf-8")


def test_snapshot_schema_validation_rejects_version_and_causality_corruption():
    world = WorldState.create(1)
    snapshot = world.to_snapshot()
    assert snapshot["snapshot_schema_version"] == SNAPSHOT_SCHEMA_VERSION
    wrong_version = dict(snapshot, snapshot_schema_version=999)
    with pytest.raises(ValueError, match="version"):
        WorldState.from_snapshot(wrong_version)
    bad_event = dict(snapshot["events"][0], id="event_000002")
    with pytest.raises(ValueError, match="IDs"):
        WorldState.from_snapshot(dict(snapshot, events=[bad_event]))


def test_schema_16_snapshot_migrates_event_classes_without_changing_history():
    world = WorldState.create(17)
    world.run_until(24)
    prior = world.to_snapshot()
    prior["snapshot_schema_version"] = 16
    prior["config"]["event_rules"].pop("class_by_type", None)
    old_events = [{key: value for key, value in event.items() if key != "class"}
                  for event in prior["events"]]
    prior["events"] = old_events

    restored = WorldState.from_snapshot(prior)

    assert restored.to_snapshot()["snapshot_schema_version"] == SNAPSHOT_SCHEMA_VERSION
    assert [event.id for event in restored.event_log] == [row["id"] for row in old_events]
    assert len(restored.event_log) == len(old_events)
    assert [{key: value for key, value in event.to_record().items() if key != "class"}
            for event in restored.event_log] == old_events
    assert all(event.event_class in {"routine", "narrative", "structural"}
               for event in restored.event_log)
    assert restored.event_log.get("event_000001").event_class == "structural"


def test_schema_17_snapshot_backfills_canonical_ship_authority_after_capture():
    world = WorldState.create("schema17-ship-authority")
    world.encounters.data["rules"].update({
        "boarding_threshold_basis_points": 10000,
        "surrender_morale_threshold": 100,
        "surrender_basis_points": 10000,
        "ship_capture_basis_points": 10000,
    })
    ships = sorted(ship_id for ship_id, row in world.entities.items()
                   if row.get("kind") == "ship")[:2]
    world.entities[ships[1]]["location"] = dict(world.entities[ships[0]]["location"])
    causes = [world.crew_ships.crews[world.entities[ship_id]["crew_id"]]["last_event_id"]
              for ship_id in ships]
    result_id = world.encounters.resolve(
        world, *ships, caused_by=causes,
        stances={ships[0]: "attack", ships[1]: "attack"}, force_combat=True)
    captured_id = world.event_log.get(result_id).data["outcome_details"]["captured_ship_id"]

    prior = world.to_snapshot()
    prior["snapshot_schema_version"] = 17
    for table_name in ("entities", "archive"):
        for row in prior[table_name].values():
            if row.get("kind") == "ship":
                row.pop("legal_owner", None)
                row.pop("controller_crew_id", None)
                row.pop("ownership_event_id", None)

    restored = WorldState.from_snapshot(prior)
    captured = restored.entities[captured_id]

    acquired = next(event for event in reversed(restored.event_log.by_type("SHIP_ACQUIRED"))
                    if any(target["entity_id"] == captured_id for target in event.targets))
    assert captured["legal_owner"] == {
        "kind": acquired.data["legal_owner_kind"],
        "id": acquired.data["legal_owner_id"]}
    assert captured["controller_crew_id"] == acquired.data["controller_crew_id"]
    assert restored.ship_is_commandable(captured_id)
    assert restored.to_snapshot()["snapshot_schema_version"] == SNAPSHOT_SCHEMA_VERSION


def test_schema_18_snapshot_migrates_faction_coalition_membership_fields():
    world = WorldState.create("schema18-coalition-migration")
    expected_factions = copy.deepcopy(world.politics.factions)
    prior = world.to_snapshot()
    prior["snapshot_schema_version"] = 18
    for registry in ("factions", "archived_factions"):
        for faction in prior["politics"][registry].values():
            for field in ("member_crew_ids", "founder_id", "anchorage_island_id", "purpose"):
                faction.pop(field, None)

    restored = WorldState.from_snapshot(prior)

    assert restored.politics.factions == expected_factions
    assert restored.to_snapshot()["snapshot_schema_version"] == SNAPSHOT_SCHEMA_VERSION


def test_snapshot_preserves_explicitly_uncommanded_ship_with_onboard_crew(tmp_path):
    world = WorldState.create("explicitly-uncommanded-hull")
    ship_id = sorted(ship_id for ship_id, row in world.entities.items()
                     if row.get("kind") == "ship")[0]
    ship = world.entities[ship_id]
    crew_id = ship["crew_id"]
    acquisition = world.event_log.append(
        tick=world.clock.tick, event_type="SHIP_ACQUIRED",
        targets=[{"role": "acquired_ship", "entity_id": ship_id}],
        data={"legal_owner_kind": "crew", "legal_owner_id": crew_id,
              "controller_crew_id": None},
        caused_by=[ship["ownership_event_id"]], causes_tags=["abandoned_hull"],
        magnitude=0.1)
    world.assign_ship_title(ship_id, owner_kind="crew", owner_id=crew_id,
                            controller_crew_id=None, event_id=acquisition.id)

    restored = WorldState.load(world.save(tmp_path / "uncommanded.json"))

    assert restored.entities[ship_id]["crew_id"] == crew_id
    assert restored.entities[ship_id]["controller_crew_id"] is None
    assert restored.entities[ship_id]["captured_by_crew_id"] is None
    assert not restored.ship_is_commandable(ship_id)


def test_active_merchant_title_must_match_its_unique_vessel():
    world = WorldState.create("unique-merchant-title")
    merchant_id, merchant = sorted(world.economy.merchants.items())[0]
    ship = world.entities[merchant["ship_id"]]
    assert ship["legal_owner"] == {"kind": "merchant", "id": merchant_id}

    other_merchant_id = next(
        candidate for candidate in sorted(world.economy.merchants)
        if candidate != merchant_id)
    ship["legal_owner"] = {"kind": "merchant", "id": other_merchant_id}

    with pytest.raises(ValueError, match="unique vessel"):
        world.to_snapshot()


def test_recapture_is_a_new_causal_title_transfer_not_a_replayed_claim():
    world = WorldState.create("causal-recapture")
    ship_id = sorted(ship_id for ship_id, row in world.entities.items()
                     if row.get("kind") == "ship")[0]
    ship = world.entities[ship_id]
    merchant_id = next(mid for mid, merchant in world.economy.merchants.items()
                       if merchant["ship_id"] == ship_id)
    crew_id = ship["crew_id"]
    old_event_id = ship["ownership_event_id"]
    captured = world.event_log.append(
        tick=world.clock.tick, event_type="SHIP_ACQUIRED",
        targets=[{"role": "acquired_ship", "entity_id": ship_id}],
        data={"method": "capture", "previous_owner": ship["legal_owner"],
              "previous_controller_crew_id": ship["controller_crew_id"],
              "legal_owner_kind": "crew",
              "legal_owner_id": crew_id, "controller_crew_id": crew_id},
        caused_by=[old_event_id], causes_tags=["test_capture"], magnitude=0.1)
    world.assign_ship_title(ship_id, owner_kind="crew", owner_id=crew_id,
                            controller_crew_id=crew_id, event_id=captured.id)
    recaptured = world.event_log.append(
        tick=world.clock.tick, event_type="SHIP_ACQUIRED",
        targets=[{"role": "acquired_ship", "entity_id": ship_id}],
        data={"method": "recapture", "previous_owner": ship["legal_owner"],
              "previous_controller_crew_id": ship["controller_crew_id"],
              "legal_owner_kind": "merchant",
              "legal_owner_id": merchant_id, "controller_crew_id": crew_id},
        caused_by=[captured.id], causes_tags=["test_recapture"], magnitude=0.1)
    world.assign_ship_title(ship_id, owner_kind="merchant", owner_id=merchant_id,
                            controller_crew_id=crew_id, event_id=recaptured.id)
    with pytest.raises(ValueError, match="replay or precede"):
        world.assign_ship_title(ship_id, owner_kind="crew", owner_id=crew_id,
                                controller_crew_id=crew_id, event_id=captured.id)

    assert ship["legal_owner"] == {"kind": "merchant", "id": merchant_id}
    assert ship["ownership_event_id"] == recaptured.id
    assert world.ship_is_commandable(ship_id)
    assert [event.id for event in world.event_log.by_type("SHIP_ACQUIRED")
            if any(target["entity_id"] == ship_id for target in event.targets)] == [
                captured.id, recaptured.id]

    stale_claim = world.event_log.append(
        tick=world.clock.tick, event_type="SHIP_ACQUIRED",
        targets=[{"role": "acquired_ship", "entity_id": ship_id}],
        data={"method": "recapture", "previous_owner": {"kind": "crew", "id": crew_id},
              "previous_controller_crew_id": crew_id,
              "legal_owner_kind": "crew", "legal_owner_id": crew_id,
              "controller_crew_id": crew_id},
        caused_by=[captured.id], causes_tags=["stale_same_tick_claim"], magnitude=0.1)
    with pytest.raises(ValueError, match="stale against the current legal owner"):
        world.assign_ship_title(ship_id, owner_kind="crew", owner_id=crew_id,
                                controller_crew_id=crew_id, event_id=stale_claim.id)
    assert ship["legal_owner"] == {"kind": "merchant", "id": merchant_id}
    assert ship["ownership_event_id"] == recaptured.id


def test_saved_snapshot_is_canonical_and_loadable(tmp_path):
    world = _world_with_pending_events()
    path = world.save(tmp_path / "world.json")
    first = path.read_bytes()
    world.save(path)

    assert path.read_bytes() == first
    assert WorldState.load(path).to_snapshot() == world.to_snapshot()
    assert json.loads(first)["clock"] == {"tick": 2}


def test_run_sim_cli_and_inspect_stub_are_headless(tmp_path):
    snapshot = tmp_path / "cli-world.json"
    run = subprocess.run(
        [sys.executable, "-m", "sim.tools.run_sim", "--seed", "5", "--years", "0",
         "--output", str(snapshot), "--keep-routine"],
        cwd=ROOT, check=False, capture_output=True, text=True,
    )
    assert run.returncode == 0, run.stderr
    assert snapshot.exists()
    initial_event_count = len(WorldState.create(5).event_log)
    assert snapshot.with_suffix(".json.events.jsonl").read_text(encoding="utf-8").count("\n") == initial_event_count

    inspect = subprocess.run(
        [sys.executable, "-m", "sim.tools.inspect", str(snapshot)],
        cwd=ROOT, check=False, capture_output=True, text=True,
    )
    assert inspect.returncode == 0, inspect.stderr
    assert json.loads(inspect.stdout)["events"] == initial_event_count


def test_run_sim_default_compacts_routine_rows_without_dropping_narrative(tmp_path):
    snapshot = tmp_path / "compact-world.json"
    result = subprocess.run(
        [sys.executable, "-m", "sim.tools.run_sim", "--seed", "5", "--years", "1",
         "--output", str(snapshot)],
        cwd=ROOT, check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    world = WorldState.load(snapshot)
    raw = world.event_log.to_records()
    output_path = snapshot.with_suffix(".json.events.jsonl")
    encoded_records = [json.loads(line)
                       for line in output_path.read_text(encoding="utf-8").splitlines()]
    compact = [decode_compacted_record(row) for row in encoded_records]
    retained = [row for row in compact if row.get("record_kind") != "routine_aggregate"]
    expected = [row for row in raw if row["class"] in {"narrative", "structural"}]
    resolvable_ids = {row["id"] for row in retained}
    resolvable_ids.update(event_id for row in compact
                          if row.get("record_kind") == "routine_aggregate"
                          for day in row["days"] for event_id in day.get("event_ids", []))
    routine_aggregates = [row for row in compact
                          if row.get("record_kind") == "routine_aggregate"]

    assert len(compact) < len(raw)
    assert [row["id"] for row in retained if row["class"] in {"narrative", "structural"}] == \
        [row["id"] for row in expected]
    raw_by_id = {row["id"]: row for row in raw}
    assert all(row["data"] == raw_by_id[row["id"]]["data"]
               for row in retained if row.get("class") in {"narrative", "structural"})
    assert any("payload_zlib_b64" in row or "days_zlib_b64" in row
               for row in encoded_records)
    assert any(row.get("record_kind") == "routine_aggregate" for row in compact)
    assert sum(row["count"] for row in routine_aggregates) == sum(
        row["class"] == "routine" for row in raw)
    assert all(sum(day["count"] for day in row["days"]) == row["count"]
               for row in routine_aggregates)
    assert all(parent in resolvable_ids for row in compact
               for parent in row.get("caused_by", []))
    assert all(parent in resolvable_ids for row in compact
               if row.get("record_kind") == "routine_aggregate"
               for day in row["days"] for parent in day.get("caused_by", []))


def test_one_year_cli_run_persists_weather_calendar_and_transition_events(tmp_path):
    snapshot = tmp_path / "one-year.json"
    result = subprocess.run(
        [sys.executable, "-m", "sim.tools.run_sim", "--seed", "42", "--years", "1",
         "--output", str(snapshot)],
        cwd=ROOT, check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    world = WorldState.load(snapshot)
    assert world.clock.tick == 365 * 24
    assert world.weather.last_updated_day == 365
    weather_events = world.event_log.by_type("NATURAL_EVENT")
    assert weather_events
    assert all(event.data["kind"] == "WEATHER_CHANGED" for event in weather_events)
    assert {event.data["calendar_season"] for event in weather_events} == {
        "spring", "summer", "autumn", "winter",
    }


def test_batch_harness_reports_seeded_runs():
    result = subprocess.run(
        [sys.executable, "-m", "sim.tools.batch_test", "--seeds", "3", "--years", "0"],
        cwd=ROOT, check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    runs = json.loads(result.stdout)["runs"]
    assert [run["seed"] for run in runs] == [0, 1, 2]
    initial_event_count = len(WorldState.create(0).event_log)
    assert all(run["event_count"] == initial_event_count and run["tick"] == 0 for run in runs)
    assert all(run["annual_trade"] == [] and run["ledger_balanced"] for run in runs)


def test_batch_progress_is_jsonl_on_stderr_and_preserves_final_json_stdout():
    result = subprocess.run(
        [sys.executable, "-m", "sim.tools.batch_test", "--seeds", "1", "--years", "1",
         "--director", "off", "--progress"],
        cwd=ROOT, check=False, capture_output=True, text=True,
    )
    summary = json.loads(result.stdout)
    progress = [json.loads(line) for line in result.stderr.splitlines() if line.strip()]
    assert len(progress) == 1
    assert progress[0]["seed"] == 0
    assert progress[0]["year"] == 1
    assert progress[0]["year_seconds"] > 0
    assert progress[0]["event_count"] == summary["runs"][0]["event_count"]
    assert progress[0]["scored_decisions"] > 0


def test_batch_event_hash_matches_canonical_jsonl_stream():
    world = WorldState.create("batch-stream-hash")
    expected = hashlib.sha256(world.event_log.to_jsonl().encode("utf-8")).hexdigest()
    assert _event_log_sha256(world.event_log) == expected


def test_batch_harness_reports_annual_trade_and_balanced_ledgers():
    result = subprocess.run(
        [sys.executable, "-m", "sim.tools.batch_test", "--seeds", "1", "--years", "3"],
        cwd=ROOT, check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    run = json.loads(result.stdout)["runs"][0]
    assert run["annual_trade"][0]["year"] == 1
    assert len(run["annual_trade"]) == 3
    assert run["annual_trade"][0]["trade_count"] > 0
    assert run["annual_trade"][0]["quantity_milli"] > 0
    assert run["trade_stable"] is True
    assert run["ledger_balanced"]
    assert run["ledger_balances"]["silver_expected"] == run["ledger_balances"]["silver_actual"]
    assert run["ledger_balances"]["goods_expected"] == run["ledger_balances"]["goods_actual"]


def test_trade_stability_band_rejects_collapse_and_extreme_growth():
    stable = [{"quantity_milli": value} for value in (1000, 900, 1500, 1100)]
    collapsed = [{"quantity_milli": value} for value in (1000, 600, 0, 0)]
    exploded = [{"quantity_milli": value} for value in (1000, 2000, 11000)]
    assert _trade_volume_stable(stable) == (True, 1100)
    assert _trade_volume_stable(collapsed) == (False, None)
    assert _trade_volume_stable(exploded) == (False, 11000)
    assert _trade_volume_stable(stable[:2]) == (None, None)
