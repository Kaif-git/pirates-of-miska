import json

import pytest

from sim.core.clock import EventScheduler, SimClock
from sim.core.events import EventLog
from sim.core.ids import IdAllocator
from sim.core.rng import RandomStreams


def test_clock_round_trips_and_rejects_time_reversal():
    clock = SimClock(12)
    assert clock.advance(3) == 15
    assert SimClock.restore(clock.snapshot()).tick == 15
    with pytest.raises(ValueError):
        clock.advance_to(14)


def test_scheduler_orders_tick_priority_then_insertion_and_restores_exactly():
    scheduler = EventScheduler()
    scheduler.schedule(3, "later")
    scheduler.schedule(2, "low", {"nested": [1, 2]}, priority=2)
    scheduler.schedule(2, "first", priority=1)
    scheduler.schedule(2, "second", priority=1)
    snapshot = scheduler.snapshot()
    restored = EventScheduler.restore(json.loads(json.dumps(snapshot)))

    assert [event.event_type for event in scheduler.pop_due(2)] == ["first", "second", "low"]
    assert [event.event_type for event in restored.pop_due(2)] == ["first", "second", "low"]
    assert [event.event_type for event in restored.pop_due(3)] == ["later"]
    assert EventScheduler.restore(snapshot).snapshot() == snapshot


def test_scheduler_event_payload_is_immutable_and_snapshot_is_detached():
    scheduler = EventScheduler()
    event = scheduler.schedule(1, "work", {"items": ["a"]})
    snapshot = scheduler.snapshot()
    snapshot["events"][0]["payload"]["items"].append("b")

    assert event.payload["items"] == ("a",)
    with pytest.raises(TypeError):
        event.payload["new"] = True


def test_rng_streams_are_seeded_independent_and_checkpointable():
    first = RandomStreams(9876)
    expected_weather = [first.stream("weather").random() for _ in range(4)]

    actual = RandomStreams(9876)
    actual.stream("encounters").random()
    actual_weather = [actual.stream("weather").random() for _ in range(4)]
    assert actual_weather == expected_weather

    saved = actual.snapshot()
    uninterrupted = [actual.stream("weather").randint(0, 1000) for _ in range(8)]
    resumed = RandomStreams.restore(9876, saved)
    assert [resumed.stream("weather").randint(0, 1000) for _ in range(8)] == uninterrupted
    assert RandomStreams(9877).stream("weather").random() != expected_weather[0]


def test_ids_are_stable_per_kind_and_checkpointable():
    ids = IdAllocator()
    assert [ids.next("char"), ids.next("ship"), ids.next("char")] == [
        "char_000001", "ship_000001", "char_000002",
    ]
    restored = IdAllocator.restore(ids.snapshot())
    assert restored.next("char") == "char_000003"


def test_event_log_indexes_entities_types_locations_ticks_and_cause_graph():
    log = EventLog({"WORLD_GENERATED"})
    root = log.append(tick=0, event_type="WORLD_GENERATED", location="isle_001",
                      actors=[{"role": "creator", "entity_id": "world_000001"}],
                      data={"seed": 42}, causes_tags=["seeded_world"])
    child = log.append(tick=0, event_type="VOYAGE_STARTED", location="isle_001",
                       actors=[{"role": "captain", "entity_id": "char_000001"}],
                       targets=[{"role": "vessel", "entity_id": "ship_000001"}],
                       caused_by=[root.id], causes_tags=["ambition"], magnitude=0.5)

    assert [event.id for event in log.by_entity("char_000001")] == [child.id]
    assert [event.id for event in log.by_entity("ship_000001")] == [child.id]
    assert [event.id for event in log.by_location("isle_001")] == [root.id, child.id]
    assert [event.id for event in log.by_type("VOYAGE_STARTED")] == [child.id]
    assert [event.id for event in log.by_tick(0)] == [root.id, child.id]
    assert [event.id for event in log.in_tick_range(0, 0)] == [root.id, child.id]
    assert log.since_types(0, {"VOYAGE_STARTED"}) == (child,)
    assert log.since_types(1, ["WORLD_GENERATED", "VOYAGE_STARTED"]) == (child,)
    assert log.since_types(2, ["VOYAGE_STARTED"]) == ()
    assert log.causes(child.id) == (root,)
    assert log.consequences(root.id) == (child,)
    log.validate_causality()
    assert log.to_jsonl() == log.to_jsonl()
    assert json.loads(log.to_jsonl().splitlines()[1])["caused_by"] == [root.id]


def test_event_log_since_types_preserves_global_append_order():
    log = EventLog({"WORLD_GENERATED"})
    first = log.append(tick=0, event_type="WORLD_GENERATED", causes_tags=["first"])
    second = log.append(tick=0, event_type="ROUTE_HAZARD", caused_by=[first.id])
    third = log.append(tick=0, event_type="WORLD_GENERATED", causes_tags=["third"])
    fourth = log.append(tick=0, event_type="ROUTE_HAZARD", caused_by=[third.id])

    assert log.since_types(1, ["WORLD_GENERATED", "ROUTE_HAZARD"]) == (
        second, third, fourth)
    log.validate_causality()


def test_events_are_deeply_immutable_and_causality_errors_are_rejected():
    log = EventLog({"WORLD_GENERATED"})
    payload = {"nested": {"items": [1, 2]}}
    root = log.append(tick=0, event_type="WORLD_GENERATED", data=payload,
                      causes_tags=["initialization"])
    payload["nested"]["items"].append(3)

    assert root.to_record()["data"] == {"nested": {"items": [1, 2]}}
    with pytest.raises(TypeError):
        root.data["new"] = "blocked"
    with pytest.raises(TypeError):
        root.data["nested"]["new"] = "blocked"
    with pytest.raises(ValueError, match="Unknown cause"):
        log.append(tick=1, event_type="VOYAGE_STARTED", caused_by=["event_999999"])
    future_root = log.append(tick=10, event_type="WORLD_GENERATED",
                             causes_tags=["future_world"])
    with pytest.raises(ValueError, match="future"):
        log.append(tick=9, event_type="VOYAGE_STARTED", caused_by=[future_root.id])


def test_event_log_rejects_unexplained_roots_and_causeless_nonroots():
    log = EventLog({"WORLD_GENERATED"})
    with pytest.raises(ValueError, match="causes_tag"):
        log.append(tick=0, event_type="WORLD_GENERATED")
    with pytest.raises(ValueError, match="requires caused_by"):
        log.append(tick=0, event_type="VOYAGE_STARTED")


def test_event_log_auditor_detects_index_corruption():
    log = EventLog({"WORLD_GENERATED"})
    event = log.append(tick=0, event_type="WORLD_GENERATED", location="isle_001",
                       causes_tags=["generation"])
    log._by_location["isle_001"].clear()

    with pytest.raises(ValueError, match="indexes"):
        log.validate_causality()
