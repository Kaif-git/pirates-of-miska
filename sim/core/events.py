"""Immutable structured events, append-only logging, indexes, and causality checks."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from bisect import bisect_left
import base64
from heapq import merge
import json
import math
import re
from itertools import islice
from types import MappingProxyType
from typing import Any, Mapping
import zlib

_EVENT_TYPE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_EVENT_CLASSES = {"routine", "narrative", "structural"}
EVENT_CLASS_POLICY = {
    "AGENT_ACTION": {
        "class": "routine",
        "reason": "decision intent is counted through its consequential result, not the attempt row",
    },
    "ENCOUNTER_DETECTED": {
        "class": "routine",
        "reason": "contact detection is not an outcome",
    },
    "DREAM_PROGRESS": {
        "class": "routine",
        "reason": "incremental plan progress is not a completed consequential outcome",
    },
    "ENCOUNTER_RESOLVED": {
        "class": "outcome-dependent",
        "reason": "uneventful withdrawal/parley is routine; damage or disposition is narrative",
    },
    "FEUD_RAID_ATTEMPTED": {
        "class": "outcome-dependent",
        "reason": "repelled no-impact grudge pressure is routine; material consequence is narrative",
    },
    "GRUDGE_PURSUED": {
        "class": "routine",
        "reason": "grudge pressure is not a hostile consequence by itself",
    },
    "RAIDER_TARGET_SELECTED": {
        "class": "routine",
        "reason": "target selection is planning, not contact or loss",
    },
    "RAIDER_INTEL_OBSERVED": {
        "class": "routine",
        "reason": "private observation updates an estimate; it is not a pursuit outcome",
    },
    "RAIDER_CHART_REVIEWED": {
        "class": "routine",
        "reason": "consulting a route chart updates knowledge but is not a consequence",
    },
    "RAIDER_PURSUIT_COMMITTED": {
        "class": "routine",
        "reason": "a persisted pursuit plan is intent, not contact or material loss",
    },
    "RAIDER_PURSUIT_RESOLVED": {
        "class": "outcome-dependent",
        "reason": "pursuit closure reports a terminal state; encounter consequences are classified separately",
    },
    "ACTOR_FORCE_SEEDED": {
        "class": "routine",
        "reason": "opening-force provenance records initial availability, not a simulated consequence",
    },
    "INJURY_RECOVERY": {
        "class": "outcome-dependent",
        "reason": "incremental healing is routine; documented full recovery is a persistent consequence",
    },
    "PRISONER_PROVISIONS_CONSUMED": {
        "class": "routine",
        "reason": "daily captive rations are conserved logistics, not a fate outcome",
    },
    "PRISONER_REVIEWED": {
        "class": "routine",
        "reason": "bounded custody review records obligation status without forcing a fate",
    },
    "RANSOM_DEMANDED": {
        "class": "routine",
        "reason": "a demand is an unresolved offer/obligation, not a completed transfer or release",
    },
    "PRISONER_ESCAPE_ATTEMPT": {
        "class": "outcome-dependent",
        "reason": "failed escape attempts are routine; successful custody termination is narrative",
    },
    "SHIP_SUPPLIES_LOST": {
        "class": "narrative",
        "reason": "weather removes finite provisions and records the conserved goods sink",
    },
    "DISEASE_CASE": {
        "class": "narrative",
        "reason": "an outbreak causes a named person-level illness with an injury follow-up",
    },
    "MERCHANT_ACTIVITY_BLOCKED": {
        "class": "routine",
        "reason": "bounded route review records an unresolved economic blocker without inventing trade",
    },
    "MERCHANT_CLOSED": {
        "class": "structural",
        "reason": "a causal title transfer terminates the firm while its assets remain accounted",
    },
    "PUBLIC_RENOWN_EARNED": {
        "class": "narrative",
        "reason": "a verified deed receives bounded public recognition without changing ability",
    },
    "RUMOR_SHARED": {
        "class": "outcome-dependent",
        "reason": "undistorted confidence propagation is routine; actual distortion is narrative",
    },
}
_UNEVENTFUL_ENCOUNTERS = {"mutual_withdrawal", "parley_ended_fight"}


def classify_event(event_type: str, data: Mapping[str, Any],
                   requested_class: str) -> tuple[str, str | None]:
    """Apply consequence-aware overrides and return class plus policy rationale key."""
    if event_type == "ENCOUNTER_RESOLVED":
        no_effect = (data.get("outcome") in _UNEVENTFUL_ENCOUNTERS
                     and not data.get("damage_event_ids")
                     and not data.get("outcome_event_ids")
                     and not any(data.get("gunnery_damage", ())))
        return ("routine", "uneventful_encounter") if no_effect else ("narrative", None)
    if event_type == "FEUD_RAID_ATTEMPTED":
        has_consequence = (data.get("material_transfer") is True
                           or any(data.get(key) for key in (
                               "damage_event_ids", "injury_event_ids", "consequence_event_ids")))
        return (("narrative", None) if has_consequence
                else ("routine", "no_impact_feud_attempt"))
    if event_type == "RUMOR_SHARED":
        actual_distortion = (data.get("distorted") is True
                            and data.get("value") != data.get("original_value"))
        return (("narrative", None) if actual_distortion
                else ("routine", "undistorted_rumor"))
    if event_type == "RAIDER_PURSUIT_RESOLVED":
        return "routine", "pursuit_terminal_status"
    if event_type == "INJURY_RECOVERY":
        return ("narrative", None) if data.get("recovered") else ("routine", "injury_healing_step")
    if event_type == "PRISONER_ESCAPE_ATTEMPT":
        return ("narrative", None) if data.get("escaped") else ("routine", "escape_attempt_failed")
    policy = EVENT_CLASS_POLICY.get(event_type)
    if policy is not None and policy["class"] in _EVENT_CLASSES:
        return policy["class"], event_type
    return requested_class, None


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, MappingProxyType):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _compress_json_field(record: dict[str, Any], field: str) -> None:
    value = record.get(field)
    if value is None:
        return
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(raw) < 256:
        return
    encoded = base64.b64encode(zlib.compress(raw, level=3)).decode("ascii")
    if len(encoded) < len(raw):
        del record[field]
        record[f"{field}_zlib_b64"] = encoded


def _compact_story_event(event: Event) -> dict[str, Any]:
    record = event.to_record()
    raw = json.dumps(record, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    encoded = base64.b64encode(zlib.compress(raw, level=3)).decode("ascii")
    wrapper = {
        "record_kind": "event_payload_zlib", "id": event.id, "tick": event.tick,
        "type": event.type, "class": event.event_class, "payload_zlib_b64": encoded,
    }
    wrapper_bytes = json.dumps(wrapper, sort_keys=True, ensure_ascii=False,
                               separators=(",", ":"), allow_nan=False).encode("utf-8")
    return wrapper if len(wrapper_bytes) < len(raw) else record


def decode_compacted_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Expand losslessly compressed fields in the compact event-log export."""
    decoded = dict(record)
    if decoded.get("record_kind") == "event_payload_zlib":
        encoded = decoded.get("payload_zlib_b64")
        try:
            raw = zlib.decompress(base64.b64decode(encoded, validate=True))
            event = json.loads(raw.decode("utf-8"))
        except (TypeError, ValueError, UnicodeError, zlib.error, json.JSONDecodeError) as exc:
            raise ValueError("Invalid compact event payload") from exc
        if (not isinstance(event, dict)
                or any(event.get(key) != decoded.get(key) for key in ("id", "tick", "type", "class"))):
            raise ValueError("Compact event header does not match its payload")
        return event
    for field in ("data", "days"):
        encoded = decoded.pop(f"{field}_zlib_b64", None)
        if encoded is not None:
            try:
                raw = zlib.decompress(base64.b64decode(encoded, validate=True))
                decoded[field] = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeError, zlib.error, json.JSONDecodeError) as exc:
                raise ValueError(f"Invalid compact event {field} payload") from exc
    return decoded


@dataclass(frozen=True)
class Event:
    id: str
    tick: int
    type: str
    location: str | None
    actors: tuple[Mapping[str, str], ...]
    targets: tuple[Mapping[str, str], ...]
    data: Mapping[str, Any]
    caused_by: tuple[str, ...]
    causes_tags: tuple[str, ...]
    magnitude: float
    event_class: str

    def to_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tick": self.tick,
            "type": self.type,
            "location": self.location,
            "actors": [_thaw(actor) for actor in self.actors],
            "targets": [_thaw(target) for target in self.targets],
            "data": _thaw(self.data),
            "caused_by": list(self.caused_by),
            "causes_tags": list(self.causes_tags),
            "magnitude": self.magnitude,
            "class": self.event_class,
        }


class EventLog:
    """Append-only event store; IDs and all indexes derive from append order."""

    def __init__(self, root_event_types: set[str] | tuple[str, ...] | list[str],
                 event_classes: Mapping[str, str] | None = None,
                 ticks_per_day: int = 24) -> None:
        roots = tuple(sorted(set(root_event_types)))
        if not roots or any(not isinstance(kind, str) or not _EVENT_TYPE.fullmatch(kind)
                            for kind in roots):
            raise ValueError("At least one valid root event type is required")
        self.root_event_types = frozenset(roots)
        self.event_classes = dict(event_classes or {})
        if type(ticks_per_day) is not int or ticks_per_day < 1:
            raise ValueError("ticks_per_day must be a positive integer")
        self.ticks_per_day = ticks_per_day
        if (any(not isinstance(kind, str) or not _EVENT_TYPE.fullmatch(kind)
                or not isinstance(event_class, str) or event_class not in _EVENT_CLASSES
                for kind, event_class in self.event_classes.items())):
            raise ValueError("Invalid event class catalog")
        self._events: list[Event] = []
        self._by_id: dict[str, Event] = {}
        self._by_entity: dict[str, list[str]] = defaultdict(list)
        self._by_entity_type: dict[tuple[str, str], list[str]] = defaultdict(list)
        self._by_location: dict[str, list[str]] = defaultdict(list)
        self._by_type: dict[str, list[str]] = defaultdict(list)
        self._by_type_positions: dict[str, list[int]] = defaultdict(list)
        self._by_tick: dict[int, list[str]] = defaultdict(list)
        self._caused_by: dict[str, list[str]] = defaultdict(list)

    def append(self, *, tick: int, event_type: str, location: str | None = None,
               actors: list[dict[str, str]] | tuple[dict[str, str], ...] = (),
               targets: list[dict[str, str]] | tuple[dict[str, str], ...] = (),
               data: dict[str, Any] | None = None, caused_by: list[str] | tuple[str, ...] = (),
               causes_tags: list[str] | tuple[str, ...] = (), magnitude: int | float = 0.0,
               event_class: str | None = None) -> Event:
        if isinstance(actors, (list, tuple)):
            actors = tuple(_thaw(actor) for actor in actors)
        if isinstance(targets, (list, tuple)):
            targets = tuple(_thaw(target) for target in targets)
        data = _thaw(data) if data is not None else {}
        resolved_class = (event_class if event_class is not None
                          else self.event_classes.get(event_type, "narrative"))
        if event_type == "NATURAL_EVENT" and data.get("kind") == "WEATHER_CHANGED":
            resolved_class = "routine"
        resolved_class, _ = classify_event(event_type, data, resolved_class)
        self._validate_event(tick, event_type, location, actors, targets, data,
                             caused_by, causes_tags, magnitude, resolved_class)
        event_id = f"event_{len(self._events) + 1:06d}"
        parents = tuple(caused_by)
        event = Event(
            id=event_id,
            tick=tick,
            type=event_type,
            location=location,
            actors=tuple(_freeze(dict(actor)) for actor in actors),
            targets=tuple(_freeze(dict(target)) for target in targets),
            data=_freeze(dict(data or {})),
            caused_by=parents,
            causes_tags=tuple(causes_tags),
            magnitude=float(magnitude),
            event_class=resolved_class,
        )
        self._events.append(event)
        self._by_id[event.id] = event
        for entity_id in sorted({item["entity_id"] for item in (*event.actors, *event.targets)}):
            self._by_entity[entity_id].append(event.id)
            self._by_entity_type[(entity_id, event_type)].append(event.id)
        if location is not None:
            self._by_location[location].append(event.id)
        self._by_type[event_type].append(event.id)
        self._by_type_positions[event_type].append(len(self._events) - 1)
        self._by_tick[tick].append(event.id)
        for parent_id in parents:
            self._caused_by[parent_id].append(event.id)
        return event

    def _validate_event(self, tick: int, event_type: str, location: str | None,
                         actors: Any, targets: Any, data: Any, caused_by: Any,
                         causes_tags: Any, magnitude: Any, event_class: Any) -> None:
        if type(tick) is not int or tick < 0:
            raise ValueError("Event tick must be a nonnegative integer")
        if not isinstance(event_type, str) or not _EVENT_TYPE.fullmatch(event_type):
            raise ValueError("Event type must be an uppercase enum-like name")
        if location is not None and (not isinstance(location, str) or not location):
            raise ValueError("Event location must be null or a nonempty ID")
        for label, participants in (("actors", actors), ("targets", targets)):
            if not isinstance(participants, (list, tuple)):
                raise ValueError(f"Event {label} must be a list")
            for participant in participants:
                if (not isinstance(participant, dict)
                        or set(participant) != {"role", "entity_id"}
                        or not all(isinstance(value, str) and value for value in participant.values())):
                    raise ValueError(f"Event {label} entries require role and entity_id")
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValueError("Event data must be a mapping")
        try:
            json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("Event data must contain JSON-compatible values") from exc
        if (not isinstance(caused_by, (list, tuple))
                or any(not isinstance(parent_id, str) or not parent_id for parent_id in caused_by)
                or len(set(caused_by)) != len(caused_by)):
            raise ValueError("Event caused_by must be a list of unique event IDs")
        for parent_id in caused_by:
            parent = self._by_id.get(parent_id)
            if parent is None:
                raise ValueError(f"Unknown cause event: {parent_id}")
            if parent.tick > tick:
                raise ValueError("An event cannot be caused by an event from the future")
        if not isinstance(causes_tags, (list, tuple)) or any(
                not isinstance(tag, str) or not tag for tag in causes_tags):
            raise ValueError("Event causes_tags must contain nonempty strings")
        if caused_by:
            if event_type in self.root_event_types:
                raise ValueError("Root event types cannot have caused_by parents")
        elif event_type not in self.root_event_types:
            raise ValueError(f"Non-root event type {event_type} requires caused_by")
        elif not causes_tags:
            raise ValueError("Root events require at least one explanatory causes_tag")
        if (type(magnitude) not in (int, float) or not math.isfinite(magnitude)
                or not 0.0 <= magnitude <= 1.0):
            raise ValueError("Event magnitude must be finite and in [0, 1]")
        if not isinstance(event_class, str) or event_class not in _EVENT_CLASSES:
            raise ValueError("Event class must be routine, narrative, or structural")

    def get(self, event_id: str) -> Event:
        return self._by_id[event_id]

    def by_entity(self, entity_id: str) -> tuple[Event, ...]:
        return tuple(self._by_id[event_id] for event_id in self._by_entity.get(entity_id, ()))

    def by_entity_type(self, entity_id: str, event_type: str) -> tuple[Event, ...]:
        """Return only indexed events involving an entity with the requested type."""
        return tuple(self._by_id[event_id]
                     for event_id in self._by_entity_type.get((entity_id, event_type), ()))

    def by_location(self, location: str) -> tuple[Event, ...]:
        return tuple(self._by_id[event_id] for event_id in self._by_location.get(location, ()))

    def by_type(self, event_type: str) -> tuple[Event, ...]:
        return tuple(self._by_id[event_id] for event_id in self._by_type.get(event_type, ()))

    def by_tick(self, tick: int) -> tuple[Event, ...]:
        return tuple(self._by_id[event_id] for event_id in self._by_tick.get(tick, ()))

    def in_tick_range(self, start: int, end: int) -> tuple[Event, ...]:
        if type(start) is not int or type(end) is not int or start < 0 or end < start:
            raise ValueError("Invalid event tick range")
        return tuple(event for event in self._events if start <= event.tick <= end)

    def causes(self, event_id: str) -> tuple[Event, ...]:
        event = self.get(event_id)
        return tuple(self._by_id[parent_id] for parent_id in event.caused_by)

    def consequences(self, event_id: str) -> tuple[Event, ...]:
        self.get(event_id)
        return tuple(self._by_id[child_id] for child_id in self._caused_by.get(event_id, ()))

    def validate_causality(self) -> None:
        """Audit all references and temporal ordering, including restored logs."""
        expected_causes: dict[str, list[str]] = defaultdict(list)
        event_positions = {event.id: index for index, event in enumerate(self._events)}
        for index, event in enumerate(self._events, start=1):
            if event.id != f"event_{index:06d}" or self._by_id.get(event.id) is not event:
                raise ValueError(f"Event identity index is inconsistent at position {index}")
            if event.caused_by:
                if event.type in self.root_event_types:
                    raise ValueError(f"Root event {event.id} has parents")
            elif event.type not in self.root_event_types or not event.causes_tags:
                raise ValueError(f"Event {event.id} has no valid root cause")
            for parent_id in event.caused_by:
                parent = self._by_id.get(parent_id)
                if (parent is None or parent.tick > event.tick
                        or event_positions.get(parent_id, index) >= index - 1):
                    raise ValueError(f"Invalid cause link in {event.id}")
                expected_causes[parent_id].append(event.id)
        expected_entity: dict[str, list[str]] = defaultdict(list)
        expected_location: dict[str, list[str]] = defaultdict(list)
        expected_type: dict[str, list[str]] = defaultdict(list)
        expected_type_positions: dict[str, list[int]] = defaultdict(list)
        expected_tick: dict[int, list[str]] = defaultdict(list)
        for position, event in enumerate(self._events):
            for entity_id in sorted({item["entity_id"] for item in (*event.actors, *event.targets)}):
                expected_entity[entity_id].append(event.id)
            if event.location is not None:
                expected_location[event.location].append(event.id)
            expected_type[event.type].append(event.id)
            expected_type_positions[event.type].append(position)
            expected_tick[event.tick].append(event.id)
        actual_indexes = (
            (self._by_entity, expected_entity), (self._by_location, expected_location),
            (self._by_type, expected_type), (self._by_tick, expected_tick),
            (self._by_type_positions, expected_type_positions),
            (self._caused_by, expected_causes),
        )
        if any(dict(actual) != dict(expected) for actual, expected in actual_indexes):
            raise ValueError("Event log indexes are inconsistent with append-only records")

    def to_records(self) -> list[dict[str, Any]]:
        return [event.to_record() for event in self._events]

    def since(self, cursor: int) -> tuple[Event, ...]:
        """Return an append-ordered suffix for persisted incremental consumers."""
        if type(cursor) is not int or not 0 <= cursor <= len(self._events):
            raise ValueError("Event cursor must be within the current log")
        return tuple(self._events[cursor:])

    def since_types(self, cursor: int, event_types: Any) -> tuple[Event, ...]:
        """Return only matching event types since a cursor, in append order."""
        if type(cursor) is not int or not 0 <= cursor <= len(self._events):
            raise ValueError("Event cursor must be within the current log")
        if not isinstance(event_types, (list, tuple, set, frozenset)):
            raise ValueError("Event types must be a collection")
        if any(not isinstance(event_type, str) for event_type in event_types):
            raise ValueError("Event types must be strings")
        positions = []
        for event_type in sorted(set(event_types)):
            rows = self._by_type_positions.get(event_type, ())
            start = bisect_left(rows, cursor)
            if start < len(rows):
                positions.append(islice(rows, start, None))
        return tuple(self._events[index] for index in merge(*positions))

    @classmethod
    def from_records(cls, root_event_types: set[str] | tuple[str, ...] | list[str],
                     records: list[dict[str, Any]],
                     event_classes: Mapping[str, str] | None = None,
                     ticks_per_day: int = 24) -> "EventLog":
        if not isinstance(records, list):
            raise ValueError("Event records must be a list")
        # Accept pre-class records for utility callers; world snapshots remain versioned.
        log = cls(root_event_types, event_classes, ticks_per_day)
        for record in records:
            old_keys = {
                    "id", "tick", "type", "location", "actors", "targets", "data",
                    "caused_by", "causes_tags", "magnitude"}
            if not isinstance(record, dict) or set(record) not in (old_keys, old_keys | {"class"}):
                raise ValueError("Invalid event record schema")
            expected_id = f"event_{len(log) + 1:06d}"
            if record["id"] != expected_id:
                raise ValueError("Event IDs must be contiguous and append-ordered")
            log.append(
                tick=record["tick"], event_type=record["type"], location=record["location"],
                actors=record["actors"], targets=record["targets"], data=record["data"],
                caused_by=record["caused_by"], causes_tags=record["causes_tags"],
                magnitude=record["magnitude"],
                event_class=record.get("class"),
            )
        log.validate_causality()
        return log

    def to_jsonl(self) -> str:
        return "".join(
            json.dumps(event.to_record(), sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), allow_nan=False) + "\n"
            for event in self._events
        )

    def to_compacted_records(self) -> list[dict[str, Any]]:
        """Return full story events and aggregate all routine rows into counters.

        Routine IDs directly referenced by narrative/structural events are
        retained as aliases; deeper routine-only chains collapse into counters.
        This keeps every story event and its direct causal references while the
        versioned world snapshot retains every raw event and payload.
        """
        required_ids = {
            parent for event in self._events
            if event.event_class in {"narrative", "structural"}
            for parent in event.caused_by
            if self._by_id[parent].event_class == "routine"
        }

        aggregates: dict[tuple, dict[str, Any]] = {}
        output = []
        for event in self._events:
            if event.event_class != "routine":
                output.append((event.tick, event.id, _compact_story_event(event)))
                continue
            day = event.tick // self.ticks_per_day
            if any(key in event.data for key in ("ship_id", "voyage_id")):
                scope_kind = "ship_voyage" if event.data.get("voyage_id") else "ship_day"
                scope_id = event.data.get("voyage_id") or event.data.get("ship_id")
            elif event.location is not None:
                scope_kind, scope_id = "island_day", event.location
            else:
                scope_kind, scope_id = "world_day", "world"
            key = (event.type, scope_kind, str(scope_id))
            row = aggregates.get(key)
            if row is None:
                row = {
                    "record_kind": "routine_aggregate", "class": "routine",
                    "event_type": event.type,
                    "scope": {"kind": scope_kind, "id": str(scope_id)},
                    "count": 0, "days": {},
                }
                aggregates[key] = row
            day_row = row["days"].setdefault(day, {"count": 0})
            row["count"] += 1
            day_row["count"] += 1
            if event.id in required_ids:
                day_row.setdefault("event_ids", []).append(event.id)

        for index, row in enumerate(aggregates.values()):
            row["days"] = [
                {"day": day, **day_row}
                for day, day_row in sorted(row["days"].items())
            ]
            first_tick = row["days"][0]["day"] * self.ticks_per_day
            _compress_json_field(row, "days")
            output.append((first_tick, f"aggregate:{index:012d}", row))
        output.sort(key=lambda item: (item[0], item[1]))
        return [row for _, _, row in output]

    def to_compacted_jsonl(self) -> str:
        return "".join(
            json.dumps(row, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), allow_nan=False) + "\n"
            for row in self.to_compacted_records()
        )

    def compaction_stats(self) -> dict[str, int | float]:
        """Measure the default compact stream without losing a narrative record."""
        raw_bytes = sum(len(json.dumps(
            event.to_record(), sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False).encode("utf-8")) + 1
            for event in self._events)
        compact_records = self.to_compacted_records()
        compact_bytes = 0
        raw_rows = len(self._events)
        compact_rows = len(compact_records)
        story_bytes_by_type: dict[str, int] = defaultdict(int)
        aggregate_bytes = 0
        for row in compact_records:
            encoded_size = len(json.dumps(
                row, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                allow_nan=False).encode("utf-8")) + 1
            compact_bytes += encoded_size
            if row.get("record_kind") == "routine_aggregate":
                aggregate_bytes += encoded_size
            else:
                story_bytes_by_type[row["type"]] += encoded_size
        return {
            "raw_rows": raw_rows,
            "compact_rows": compact_rows,
            "raw_bytes": raw_bytes,
            "compact_bytes": compact_bytes,
            "rows_reduced_percent": (100.0 * (raw_rows - compact_rows) / raw_rows)
            if raw_rows else 0.0,
            "bytes_reduced_percent": (100.0 * (raw_bytes - compact_bytes) / raw_bytes)
            if raw_bytes else 0.0,
            "narrative_structural_rows": sum(
                event.event_class in {"narrative", "structural"} for event in self._events),
            "routine_aggregate_bytes": aggregate_bytes,
            "largest_story_event_types_by_bytes": dict(sorted(
                story_bytes_by_type.items(), key=lambda item: (-item[1], item[0]))[:10]),
        }

    def __iter__(self):
        return iter(self._events)

    def __len__(self) -> int:
        return len(self._events)
