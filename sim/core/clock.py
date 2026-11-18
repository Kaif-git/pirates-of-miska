"""Integer simulation clock and stable event-driven scheduler."""

from __future__ import annotations

from dataclasses import dataclass, field
import heapq
import json
from types import MappingProxyType
from typing import Any


@dataclass
class SimClock:
    """Monotonic integer tick counter; M0 defines one tick as one sim hour."""

    tick: int = 0

    def __post_init__(self) -> None:
        if type(self.tick) is not int or self.tick < 0:
            raise ValueError("Clock tick must be a nonnegative integer")

    def advance(self, ticks: int = 1) -> int:
        if type(ticks) is not int or ticks < 0:
            raise ValueError("Clock advance must be a nonnegative integer")
        self.tick += ticks
        return self.tick

    def advance_to(self, tick: int) -> int:
        if type(tick) is not int or tick < self.tick:
            raise ValueError("Clock cannot move backward")
        self.tick = tick
        return self.tick

    def snapshot(self) -> dict[str, int]:
        return {"tick": self.tick}

    @classmethod
    def restore(cls, state: dict[str, Any]) -> "SimClock":
        if not isinstance(state, dict) or set(state) != {"tick"}:
            raise ValueError("Invalid clock snapshot")
        return cls(state["tick"])


@dataclass(frozen=True)
class ScheduledEvent:
    tick: int
    priority: int
    sequence: int
    event_type: str
    payload: dict[str, Any] = field(compare=False)


class EventScheduler:
    """Stable priority queue ordered by tick, priority, then insertion order."""

    def __init__(self) -> None:
        self._heap: list[tuple[int, int, int, ScheduledEvent]] = []
        self._next_sequence = 0

    def schedule(self, tick: int, event_type: str, payload: dict[str, Any] | None = None,
                 priority: int = 0) -> ScheduledEvent:
        if type(tick) is not int or tick < 0:
            raise ValueError("Scheduled tick must be a nonnegative integer")
        if type(priority) is not int:
            raise ValueError("Event priority must be an integer")
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("Scheduled event type must be a nonempty string")
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise ValueError("Scheduled event payload must be a mapping")
        try:
            copied_payload = json.loads(json.dumps(payload, sort_keys=True, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise ValueError("Scheduled event payload must contain JSON data") from exc
        sequence = self._next_sequence
        self._next_sequence += 1
        event = ScheduledEvent(tick, priority, sequence, event_type,
                                _freeze_payload(copied_payload))
        heapq.heappush(self._heap, (tick, priority, sequence, event))
        return event

    def peek(self) -> ScheduledEvent | None:
        return self._heap[0][3] if self._heap else None

    def events(self) -> tuple[ScheduledEvent, ...]:
        return tuple(entry[3] for entry in sorted(self._heap))

    def pop_due(self, tick: int) -> list[ScheduledEvent]:
        if type(tick) is not int or tick < 0:
            raise ValueError("Current tick must be a nonnegative integer")
        due = []
        while self._heap and self._heap[0][0] <= tick:
            due.append(heapq.heappop(self._heap)[3])
        return due

    def cancel_for_entity(self, entity_id: str) -> list[ScheduledEvent]:
        """Cancel pending records that name an entity as actor or target."""
        if not isinstance(entity_id, str) or not entity_id:
            raise ValueError("Scheduled-event cancellation requires an entity ID")
        removed = []
        retained = []
        for entry in self._heap:
            event = entry[3]
            participants = (*event.payload.get("actors", ()), *event.payload.get("targets", ()))
            if any(item.get("entity_id") == entity_id for item in participants):
                removed.append(event)
            else:
                retained.append(entry)
        if removed:
            self._heap = retained
            heapq.heapify(self._heap)
        return sorted(removed, key=lambda event: (event.tick, event.priority, event.sequence))

    def snapshot(self) -> dict[str, Any]:
        entries = [
            {"tick": event.tick, "priority": event.priority,
             "sequence": event.sequence, "event_type": event.event_type,
             "payload": _thaw_payload(event.payload)}
            for _, _, _, event in sorted(self._heap)
        ]
        return {"next_sequence": self._next_sequence, "events": entries}

    @classmethod
    def restore(cls, state: dict[str, Any]) -> "EventScheduler":
        if not isinstance(state, dict) or set(state) != {"next_sequence", "events"}:
            raise ValueError("Invalid scheduler snapshot")
        sequence = state["next_sequence"]
        events = state["events"]
        if type(sequence) is not int or sequence < 0 or not isinstance(events, list):
            raise ValueError("Invalid scheduler snapshot")
        scheduler = cls()
        scheduler._next_sequence = sequence
        seen = set()
        for row in events:
            if not isinstance(row, dict) or set(row) != {
                    "tick", "priority", "sequence", "event_type", "payload"}:
                raise ValueError("Invalid scheduled event snapshot")
            row_sequence = row["sequence"]
            if type(row_sequence) is not int or row_sequence < 0 or row_sequence >= sequence or row_sequence in seen:
                raise ValueError("Invalid scheduler sequence snapshot")
            if type(row["tick"]) is not int or row["tick"] < 0:
                raise ValueError("Invalid scheduled tick snapshot")
            if type(row["priority"]) is not int:
                raise ValueError("Invalid scheduled priority snapshot")
            if not isinstance(row["event_type"], str) or not row["event_type"]:
                raise ValueError("Invalid scheduled event type snapshot")
            if not isinstance(row["payload"], dict):
                raise ValueError("Invalid scheduled payload snapshot")
            seen.add(row_sequence)
            try:
                payload = json.loads(json.dumps(row["payload"], sort_keys=True, allow_nan=False))
            except (TypeError, ValueError) as exc:
                raise ValueError("Invalid scheduled payload snapshot") from exc
            event = ScheduledEvent(row["tick"], row["priority"], row_sequence,
                                   row["event_type"], _freeze_payload(payload))
            scheduler._heap.append((event.tick, event.priority, event.sequence, event))
        if seen and max(seen) >= sequence:
            raise ValueError("Invalid scheduler next sequence")
        heapq.heapify(scheduler._heap)
        return scheduler

    def __len__(self) -> int:
        return len(self._heap)


def _freeze_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_payload(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_payload(item) for item in value)
    return value


def _thaw_payload(value: Any) -> Any:
    if isinstance(value, dict) or isinstance(value, MappingProxyType):
        return {key: _thaw_payload(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_payload(item) for item in value]
    return value
