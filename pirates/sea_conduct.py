"""Small, local, versioned evidence ledger for sea-encounter lawfulness.

This records evidence supplied by trusted encounter/combat/warrant systems;
it does not infer an offence from colours, threat estimates, refusal, or range.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import blake2b
import json


VERSION = 1
MAX_EVENTS = 100
MAX_ID_LENGTH = 128
MAX_WITNESSES = 32
HOSTILE_CAUSES = {"witnessed_attack", "committed_boarding", "committed_fire"}


def _event_id(row):
    body = json.dumps(
        [row["tick"], row["cause"], row["aggressor_id"], row["target_id"],
         row["encounter_id"], row["witnesses"], row["warrant_id"]],
        ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return blake2b(body.encode("utf-8"), digest_size=12).hexdigest()


def _validate_event(row, *, current_tick):
    fields = {"event_id", "tick", "cause", "aggressor_id", "target_id",
              "encounter_id", "witnesses", "warrant_id"}
    if (not isinstance(row, dict) or set(row) != fields
            or not isinstance(row["event_id"], str) or len(row["event_id"]) != 24
            or any(char not in "0123456789abcdef" for char in row["event_id"])
            or type(row["tick"]) is not int or not 0 <= row["tick"] <= current_tick
            or not isinstance(row["cause"], str)
            or row["cause"] not in HOSTILE_CAUSES | {"verified_warrant"}
            or any(not isinstance(row[key], str) or not row[key]
                   or len(row[key]) > MAX_ID_LENGTH
                   for key in ("aggressor_id", "target_id", "encounter_id"))
            or not isinstance(row["witnesses"], list)
            or len(row["witnesses"]) > MAX_WITNESSES
            or any(not isinstance(value, str) or not value for value in row["witnesses"])
            or any(len(value) > MAX_ID_LENGTH for value in row["witnesses"])
            or len(set(row["witnesses"])) != len(row["witnesses"])):
        raise ValueError("Invalid saved sea conduct evidence")
    if row["cause"] == "verified_warrant":
        if (not isinstance(row["warrant_id"], str) or not row["warrant_id"]
                or len(row["warrant_id"]) > MAX_ID_LENGTH):
            raise ValueError("A verified warrant requires its stable warrant identity")
    elif row["warrant_id"] is not None or not row["witnesses"]:
        raise ValueError("A witnessed hostile act requires witnesses and no warrant identity")
    if row["event_id"] != _event_id(row):
        raise ValueError("Sea conduct evidence identity does not match its contents")
    return row


class SeaConductLedger:
    """Bounded evidence; hostile permission is scoped to target/contact/witness."""

    def __init__(self, events=()):
        self.events = deepcopy(list(events))

    def record(self, *, tick, cause, aggressor_id, target_id, encounter_id,
               witnesses=(), warrant_id=None):
        row = {
            "event_id": "pending", "tick": tick, "cause": cause,
            "aggressor_id": aggressor_id, "target_id": target_id,
            "encounter_id": encounter_id, "witnesses": list(witnesses),
            "warrant_id": warrant_id,
        }
        row["event_id"] = _event_id(row)
        _validate_event(row, current_tick=tick)
        existing = next((event for event in self.events
                         if event["event_id"] == row["event_id"]), None)
        if existing is not None:
            return deepcopy(existing)
        self.events = sorted(self.events + [row], key=lambda event: event["tick"])[-MAX_EVENTS:]
        return deepcopy(row)

    def authorization(self, *, target_id, actor_id, encounter_id, current_tick):
        """Return the evidence permitting this encounter's hostile commitment."""
        for row in reversed(self.events):
            if row["tick"] > current_tick:
                continue
            if (row["cause"] == "verified_warrant"
                    and row["target_id"] == target_id):
                return deepcopy(row)
            if (row["cause"] in HOSTILE_CAUSES
                    and row["aggressor_id"] == target_id
                    and row["target_id"] == actor_id
                    and row["encounter_id"] == encounter_id
                    and actor_id in row["witnesses"]):
                return deepcopy(row)
        return None

    def has_event(self, event_id):
        return any(row["event_id"] == event_id for row in self.events)

    def snapshot(self):
        return {"version": VERSION, "events": deepcopy(self.events)}

    @classmethod
    def restore(cls, data, *, current_tick):
        if data is None:
            return cls()
        if (not isinstance(data, dict) or set(data) != {"version", "events"}
                or type(data["version"]) is not int or data["version"] != VERSION
                or not isinstance(data["events"], list)
                or len(data["events"]) > MAX_EVENTS):
            raise ValueError("Invalid saved sea conduct ledger")
        events = []
        seen = set()
        for row in data["events"]:
            _validate_event(row, current_tick=current_tick)
            if row["event_id"] in seen:
                raise ValueError("Duplicate saved sea conduct evidence")
            seen.add(row["event_id"])
            events.append(deepcopy(row))
        if [row["tick"] for row in events] != sorted(row["tick"] for row in events):
            raise ValueError("Saved sea conduct evidence is out of order")
        return cls(events)
