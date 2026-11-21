"""Offline coherence audit for durable simulation event JSONL logs."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from sim.core.events import EVENT_CLASS_POLICY


def read_event_records(path: str | Path) -> list[dict[str, Any]]:
    """Read plain or gzip JSONL as emitted by Event.to_record()."""
    source = Path(path)
    opener = gzip.open if source.suffix.lower() == ".gz" else open
    records = []
    with opener(source, "rt", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid event JSON at {source}:{line_number}: {exc}") from exc
            if not isinstance(record, dict) or not isinstance(record.get("id"), str):
                raise ValueError(f"Invalid event record at {source}:{line_number}")
            records.append(record)
    return records


def audit_faction_membership_transitions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Replay coalition membership intervals independently from crew IDs."""
    memberships: dict[str, set[str]] = defaultdict(set)
    intervals: dict[str, list[dict[str, Any]]] = defaultdict(list)
    errors: list[dict[str, Any]] = []
    transitions = 0

    def fail(row: dict[str, Any], message: str, faction_id: Any,
             crew_id: Any = None) -> None:
        errors.append({"event_id": row.get("id"), "event_type": row.get("type"),
                       "faction_id": faction_id, "crew_id": crew_id,
                       "error": message})

    for row in rows:
        data = row.get("data", {})
        event_type = row.get("type")
        faction_id = data.get("faction_id")
        if event_type == "FACTION_FOUNDED":
            member_ids = data.get("member_crew_ids")
            if member_ids is None:  # pre-coalition historical event
                continue
            if data.get("kind") == "marine" and member_ids == []:
                continue  # institutional government faction, not a crew coalition
            if (not isinstance(member_ids, list) or len(member_ids) < 2
                    or any(not isinstance(crew_id, str) or not crew_id for crew_id in member_ids)
                    or len(member_ids) != len(set(member_ids))):
                fail(row, "coalition founding has invalid member crew IDs", faction_id)
                continue
            if memberships[faction_id]:
                fail(row, "faction founded while already active", faction_id)
                continue
            memberships[faction_id] = set(member_ids)
            for crew_id in member_ids:
                intervals[crew_id].append({"faction_id": faction_id,
                                           "joined_event_id": row["id"],
                                           "joined_tick": row.get("tick"),
                                           "left_event_id": None, "left_tick": None})
            transitions += len(member_ids)
        elif event_type == "FACTION_MEMBER_JOINED":
            crew_id = data.get("crew_id")
            if not isinstance(faction_id, str) or not isinstance(crew_id, str):
                fail(row, "membership join lacks faction or crew identity", faction_id, crew_id)
                continue
            if crew_id in memberships[faction_id]:
                fail(row, "crew joined a faction it already belongs to", faction_id, crew_id)
                continue
            memberships[faction_id].add(crew_id)
            intervals[crew_id].append({"faction_id": faction_id,
                                       "joined_event_id": row["id"],
                                       "joined_tick": row.get("tick"),
                                       "left_event_id": None, "left_tick": None})
            transitions += 1
        elif event_type == "FACTION_MEMBER_DEPARTED":
            crew_id = data.get("crew_id")
            if not isinstance(faction_id, str) or not isinstance(crew_id, str):
                fail(row, "membership departure lacks faction or crew identity",
                     faction_id, crew_id)
                continue
            if crew_id not in memberships[faction_id]:
                fail(row, "crew departed a faction it did not belong to", faction_id, crew_id)
                continue
            memberships[faction_id].remove(crew_id)
            active_interval = next((interval for interval in reversed(intervals[crew_id])
                                    if interval["faction_id"] == faction_id
                                    and interval["left_event_id"] is None), None)
            if active_interval is None:
                fail(row, "departure has no open membership interval", faction_id, crew_id)
            else:
                active_interval["left_event_id"] = row["id"]
                active_interval["left_tick"] = row.get("tick")
            transitions += 1
        elif event_type == "FACTION_DISSOLVED":
            declared = set(data.get("member_crew_ids", ()))
            active = memberships.get(faction_id, set())
            if declared and declared != active:
                fail(row, "dissolution member ledger disagrees with active coalition",
                     faction_id)
            for crew_id in sorted(active):
                active_interval = next((interval for interval in reversed(intervals[crew_id])
                                        if interval["faction_id"] == faction_id
                                        and interval["left_event_id"] is None), None)
                if active_interval:
                    active_interval["left_event_id"] = row["id"]
                    active_interval["left_tick"] = row.get("tick")
            memberships[faction_id] = set()
            transitions += len(active)
    return {"transitions": transitions,
            "active_member_crew_ids_by_faction": {
                faction_id: sorted(member_ids) for faction_id, member_ids in sorted(memberships.items())},
            "membership_intervals_by_crew": dict(sorted(intervals.items())),
            "errors": errors}


def audit_faction_authority_transitions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Verify coalition founders/captains held the recorded crew authority at founding."""
    captains: dict[str, str] = {}
    errors: list[dict[str, Any]] = []
    checked = 0

    def fail(row: dict[str, Any], message: str, crew_id: Any = None) -> None:
        errors.append({"event_id": row.get("id"), "event_type": row.get("type"),
                       "crew_id": crew_id, "error": message})

    for row in rows:
        event_type = row.get("type")
        data = row.get("data", {})
        if event_type == "CREW_FOUNDED":
            crew_id = data.get("crew_id")
            roles = data.get("roles", {})
            captain_id = roles.get("captain") if isinstance(roles, dict) else None
            if isinstance(crew_id, str) and isinstance(captain_id, str):
                captains[crew_id] = captain_id
        elif event_type == "CAPTAIN_CHANGED":
            crew_id = data.get("crew_id")
            former, successor = data.get("former_captain_id"), data.get("new_captain_id")
            if captains.get(crew_id) != former:
                fail(row, "captain change does not follow the recorded authority", crew_id)
            elif isinstance(successor, str) and successor:
                captains[crew_id] = successor
        elif event_type == "PRIZE_CREW_INSTALLED":
            crew_id = data.get("crew_id")
            former, successor = data.get("former_captain_id"), data.get("prize_captain_id")
            if captains.get(crew_id) != former:
                fail(row, "prize captain transfer does not follow the recorded authority", crew_id)
            elif isinstance(successor, str) and successor:
                captains[crew_id] = successor
        elif event_type == "FACTION_FOUNDED" and data.get("member_crew_ids") is not None:
            if data.get("kind") == "marine":
                continue
            member_ids = data.get("member_crew_ids", ())
            declared = data.get("member_captain_ids", ())
            if not isinstance(member_ids, (list, tuple)) or not isinstance(declared, (list, tuple)):
                fail(row, "coalition founding lacks historical captain identities")
                continue
            expected = sorted(captains.get(crew_id, "") for crew_id in member_ids)
            founder_id = data.get("founder_id")
            if (not all(expected) or expected != sorted(declared)
                    or founder_id not in expected):
                fail(row, "coalition founding captain authority disagrees with prior crew history")
                continue
            founder_actors = [actor.get("entity_id") for actor in row.get("actors", ())
                              if actor.get("role") == "faction_founder"]
            if founder_actors != [founder_id]:
                fail(row, "founding event does not identify exactly its historical captain-founder")
                continue
            checked += 1

    return {"coalition_foundings_checked": checked, "captain_ids_by_crew": dict(sorted(captains.items())),
            "errors": errors}


def audit_crew_membership_transitions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Replay event-backed roster changes and verify each transition's preconditions."""
    memberships: dict[str, str] = {}
    errors: list[dict[str, Any]] = []
    transitions = 0
    membership_flow: list[dict[str, Any]] = []
    before_by_person: dict[str, str | None] = {}

    def fail(row: dict[str, Any], message: str, person_id: str | None = None) -> None:
        errors.append({"event_id": row["id"], "event_type": row.get("type"),
                       "character_id": person_id, "error": message})

    def add(row: dict[str, Any], person_id: Any, crew_id: Any) -> None:
        nonlocal transitions
        if not isinstance(person_id, str) or not person_id or not isinstance(crew_id, str) or not crew_id:
            fail(row, "membership join lacks character or crew identity",
                 person_id if isinstance(person_id, str) else None)
            return
        current = memberships.get(person_id)
        if current is not None:
            fail(row, f"character already belongs to {current} before join", person_id)
            return
        before_by_person.setdefault(person_id, None)
        memberships[person_id] = crew_id
        transitions += 1

    def remove(row: dict[str, Any], person_id: Any, crew_id: Any) -> bool:
        nonlocal transitions
        if not isinstance(person_id, str) or not person_id:
            fail(row, "membership departure lacks character identity")
            return False
        current = memberships.get(person_id)
        if current is None:
            fail(row, "character has no recorded crew membership to leave", person_id)
            return False
        if isinstance(crew_id, str) and crew_id and current != crew_id:
            fail(row, f"membership departure names {crew_id}, current crew is {current}", person_id)
            return False
        before_by_person.setdefault(person_id, current)
        del memberships[person_id]
        transitions += 1
        return True

    for row in rows:
        before_by_person.clear()
        active_before = len(memberships)
        event_type = row.get("type")
        data = row.get("data", {})
        if event_type == "CREW_FOUNDED":
            crew_id = data.get("crew_id")
            roster = data.get("roster", ())
            if not isinstance(roster, list):
                fail(row, "founding roster is not a list")
                continue
            if any(not isinstance(person_id, str) or not person_id for person_id in roster):
                fail(row, "founding roster contains invalid identity")
                continue
            if len(roster) != len(set(roster)):
                fail(row, "founding roster contains duplicate identities")
            for person_id in roster:
                add(row, person_id, crew_id)
        elif event_type == "RECRUITED":
            add(row, data.get("character_id"), data.get("crew_id"))
        elif event_type == "CHARACTER_ARRIVED" and data.get("crew_id"):
            person_id = next((part.get("entity_id") for part in row.get("actors", ())
                             if part.get("role") == "new_crew_member"), None)
            add(row, person_id, data.get("crew_id"))
        elif event_type in {"CREW_MEMBER_TRANSFERRED", "CREW_MEMBER_DEFECTED",
                            "PRISONER_CAPTURED"}:
            person_id = data.get("character_id")
            if person_id is None and event_type == "CREW_MEMBER_DEFECTED":
                person_id = next((part.get("entity_id") for part in row.get("actors", ())
                                  if part.get("role") == "defector"), None)
            source_crew = data.get("from_crew_id") or data.get("crew_id")
            target_crew = data.get("to_crew_id")
            if remove(row, person_id, source_crew):
                if target_crew is not None:
                    add(row, person_id, target_crew)
                elif event_type != "PRISONER_CAPTURED":
                    fail(row, "membership transfer lacks destination crew", person_id)
        elif event_type == "DESERTION":
            person_id = next((part.get("entity_id") for part in row.get("actors", ())
                             if part.get("role") == "deserter"), None)
            remove(row, person_id, data.get("crew_id"))
        elif event_type == "PRIZE_CREW_INSTALLED":
            former_captain = data.get("former_captain_id")
            crew_id = data.get("crew_id")
            if former_captain in memberships:
                remove(row, former_captain, crew_id)
        elif event_type == "CHARACTER_DIED":
            person_id = next((part.get("entity_id") for part in row.get("targets", ())
                              if part.get("role") == "deceased"), None)
            if person_id in memberships:
                remove(row, person_id, memberships[person_id])
        elif event_type == "CHARACTER_DEPARTED":
            person_id = next((part.get("entity_id") for part in row.get("targets", ())
                              if part.get("role") in {"departed", "emigrant"}), None)
            if person_id in memberships:
                fail(row, "world departure retains active crew membership", person_id)
        elif event_type == "SHIP_LOSS_DISPOSITION":
            crew_id = data.get("crew_id")
            expected = sorted(person_id for person_id, member_crew in memberships.items()
                              if member_crew == crew_id)
            recorded = data.get("crew_roster_before")
            if not isinstance(recorded, list) or sorted(recorded) != expected:
                fail(row, "pre-loss roster does not match replayed membership", crew_id)

        if before_by_person or event_type == "SHIP_LOSS_DISPOSITION":
            entered = sum(before is None and person_id in memberships
                          for person_id, before in before_by_person.items())
            left = sum(before is not None and person_id not in memberships
                       for person_id, before in before_by_person.items())
            transferred = sum(before is not None and memberships.get(person_id) is not None
                              and memberships[person_id] != before
                              for person_id, before in before_by_person.items())
            membership_flow.append({
                "event_id": row["id"], "event_type": event_type,
                "active_memberships_before": active_before,
                "memberships_entered": entered,
                "memberships_left": left,
                "memberships_transferred": transferred,
                "active_membership_delta": len(memberships) - active_before,
                "active_memberships_after": len(memberships),
                "affected_people": sorted(before_by_person),
            })

    return {
        "transition_count": transitions,
        "membership_flow_transition_count": len(membership_flow),
        "membership_flow": membership_flow,
        "active_membership_count": len(memberships),
        "active_memberships": dict(sorted(memberships.items())),
        "errors": errors,
    }


def audit_character_identity_transitions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Ensure LOD representation changes preserve one identity and death is terminal."""
    status_by_person: dict[str, str] = {}
    errors: list[dict[str, Any]] = []
    transition_count = 0

    def person_id_for(row: dict[str, Any], role: str, collection: str) -> Any:
        return next((part.get("entity_id") for part in row.get(collection, ())
                     if part.get("role") == role), None)

    for row in rows:
        event_type = row.get("type")
        if event_type == "CHARACTER_GENERATED":
            person_id = person_id_for(row, "character", "actors")
            if not isinstance(person_id, str) or not person_id:
                errors.append({"event_id": row["id"], "error": "generated identity is missing"})
            elif person_id in status_by_person:
                errors.append({"event_id": row["id"], "character_id": person_id,
                               "error": "identity generated more than once"})
            else:
                status_by_person[person_id] = "active"
                transition_count += 1
            continue

        if event_type == "LOD_BACKGROUND_RECORDED":
            person_id = person_id_for(row, "named_resident", "actors")
            expected, next_status = "active", "background"
        elif event_type == "LOD_PROMOTED":
            person_id = person_id_for(row, "promoted_character", "actors")
            expected, next_status = "background", "active"
        elif event_type == "LOD_DEMOTED":
            person_id = person_id_for(row, "demoted_character", "actors")
            expected, next_status = "active", "background"
        elif event_type == "CHARACTER_DIED":
            person_id = person_id_for(row, "deceased", "targets")
            expected, next_status = None, "deceased"
        elif event_type == "CHARACTER_DEPARTED":
            person_id = person_id_for(row, "departed", "targets")
            if person_id is None:
                person_id = person_id_for(row, "emigrant", "targets")
            expected, next_status = "active", "departed"
        elif event_type == "CHARACTER_MISSING":
            person_id = person_id_for(row, "missing_person", "actors")
            expected, next_status = "active", "missing"
        elif event_type == "CHARACTER_RESCUED":
            person_id = person_id_for(row, "rescued_person", "targets")
            expected, next_status = "missing", "active"
        elif event_type == "CHARACTER_ARRIVED":
            person_id = person_id_for(row, "new_crew_member", "actors")
            expected, next_status = None, None
        else:
            continue

        previous = status_by_person.get(person_id) if isinstance(person_id, str) else None
        error = None
        if not isinstance(person_id, str) or not person_id:
            error = "identity transition lacks character ID"
        elif previous is None:
            error = "identity transition references a character without generation history"
        elif previous in {"deceased", "departed"}:
            error = f"identity transition occurs after terminal state {previous}"
        elif expected is not None and previous != expected:
            error = f"expected {expected} representation, found {previous}"
        if error:
            errors.append({"event_id": row["id"], "character_id": person_id,
                           "error": error})
            continue
        if next_status is not None:
            status_by_person[person_id] = next_status
            transition_count += 1

    return {
        "transition_count": transition_count,
        "identity_count": len(status_by_person),
        "identities_by_status": dict(sorted(Counter(status_by_person.values()).items())),
        "errors": errors,
    }


def audit_prisoner_custody_transitions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Replay custody, captor transfers, release and death from causal events."""
    prisoners: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []

    def fail(row: dict[str, Any], person_id: str | None, message: str) -> None:
        errors.append({"event_id": row["id"], "event_type": row.get("type"),
                       "character_id": person_id, "error": message})

    def target_id(row: dict[str, Any], role: str) -> str | None:
        return next((part.get("entity_id") for part in row.get("targets", ())
                     if part.get("role") == role), None)

    for row in rows:
        event_type = row.get("type")
        data = row.get("data", {})
        affected: list[tuple[str, str, dict[str, Any]]] = []
        if event_type == "PRISONER_CAPTURED":
            person_id = data.get("character_id") or target_id(row, "prisoner")
            captor_ship_id = data.get("captor_ship_id")
            if not isinstance(person_id, str) or not person_id:
                fail(row, None, "capture lacks prisoner identity")
                continue
            if person_id in prisoners and prisoners[person_id]["status"] in {"held", "fate_pending"}:
                fail(row, person_id, "prisoner captured again before custody ended")
                continue
            if not isinstance(captor_ship_id, str) or not captor_ship_id:
                fail(row, person_id, "capture lacks captor vessel")
                continue
            before = prisoners.get(person_id)
            prisoners[person_id] = {"status": "held", "captor_ship_id": captor_ship_id,
                                    "former_crew_id": data.get("from_crew_id"),
                                    "capture_event_id": row["id"]}
            affected.append((person_id, before["status"] if before else "free", prisoners[person_id]))
        elif event_type == "PRIZE_CREW_INSTALLED":
            # Prize disposition holds the former captain aboard the captor
            # vessel even though it predates the dedicated custody event type.
            person_id = data.get("former_captain_id")
            captor_ship_id = data.get("captor_ship_id")
            if not isinstance(person_id, str) or not person_id:
                fail(row, None, "prize installation lacks former captain identity")
                continue
            if not isinstance(captor_ship_id, str) or not captor_ship_id:
                fail(row, person_id, "prize installation lacks captor vessel")
                continue
            before = prisoners.get(person_id)
            if before is not None and before["status"] in {"held", "fate_pending"}:
                fail(row, person_id, "prize installation recaptures a person already held")
                continue
            state = {"status": "held", "captor_ship_id": captor_ship_id,
                     "former_crew_id": data.get("crew_id"),
                     "capture_event_id": row["id"], "capture_kind": "prize_crew_installation"}
            prisoners[person_id] = state
            affected.append((person_id, before["status"] if before else "free", state))
        elif event_type == "PRISONER_CUSTODY_TRANSFERRED":
            from_ship_id, to_ship_id = data.get("from_ship_id"), data.get("to_ship_id")
            ids = data.get("prisoner_ids")
            if not isinstance(ids, list):
                fail(row, None, "custody transfer lacks prisoner ID list")
                continue
            for person_id in ids:
                state = prisoners.get(person_id) if isinstance(person_id, str) else None
                if state is None or state["status"] != "held":
                    fail(row, person_id if isinstance(person_id, str) else None,
                         "custody transfer references a person not held")
                    continue
                if state["captor_ship_id"] != from_ship_id:
                    fail(row, person_id, "custody transfer source vessel disagrees with current captor")
                    continue
                before = dict(state)
                state["captor_ship_id"] = to_ship_id
                affected.append((person_id, before["status"], state))
        elif event_type == "PRISONER_FATE":
            person_id = target_id(row, "prisoner")
            state = prisoners.get(person_id) if person_id else None
            fate = data.get("fate")
            if state is None or state["status"] != "held":
                fail(row, person_id, "fate references a person not held")
                continue
            if fate not in {"ransom", "execute", "release"}:
                fail(row, person_id, "unknown prisoner fate")
                continue
            before = dict(state)
            state.update({"status": "fate_pending", "pending_fate": fate,
                          "fate_event_id": row["id"]})
            affected.append((person_id, before["status"], state))
        elif event_type == "PRISONER_RELEASED":
            person_id = target_id(row, "released_prisoner") or data.get("character_id")
            state = prisoners.get(person_id) if isinstance(person_id, str) else None
            if state is None or state["status"] not in {"held", "fate_pending"}:
                fail(row, person_id if isinstance(person_id, str) else None,
                     "release references a person not in custody")
                continue
            fate = data.get("fate")
            if (state["status"] == "fate_pending"
                    and fate is not None and fate != state.get("pending_fate")):
                fail(row, person_id, "release fate disagrees with pending fate")
                continue
            before = dict(state)
            state.update({"status": "released", "release_event_id": row["id"]})
            affected.append((person_id, before["status"], state))
        elif event_type == "CHARACTER_DIED":
            person_id = target_id(row, "deceased")
            state = prisoners.get(person_id) if isinstance(person_id, str) else None
            if state is None or state["status"] not in {"held", "fate_pending"}:
                continue
            before = dict(state)
            if state.get("pending_fate") not in {None, "execute"}:
                fail(row, person_id, "prisoner died after a non-execution fate")
                continue
            state.update({"status": "deceased", "death_event_id": row["id"]})
            affected.append((person_id, before["status"], state))

        for person_id, before_status, after in affected:
            transitions.append({"event_id": row["id"], "event_type": event_type,
                                "character_id": person_id, "status_before": before_status,
                                "status_after": after["status"],
                                "captor_ship_id": after.get("captor_ship_id")})

    return {"transition_count": len(transitions), "prisoner_count": len(prisoners),
            "statuses": dict(sorted(Counter(row["status"] for row in prisoners.values()).items())),
            "prisoners": dict(sorted(prisoners.items())), "transitions": transitions,
            "errors": errors}


def _ancestors(record: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    pending = list(record.get("caused_by", ()))
    seen: set[str] = set()
    result = []
    while pending:
        event_id = pending.pop()
        if event_id in seen:
            continue
        seen.add(event_id)
        parent = by_id.get(event_id)
        if parent is None:
            continue
        result.append(parent)
        pending.extend(parent.get("caused_by", ()))
    return result


def audit_event_records(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(records)
    by_id = {row["id"]: row for row in rows}
    counts_by_class_type: Counter[tuple[str, str]] = Counter()
    bytes_by_class_type: Counter[tuple[str, str]] = Counter()
    origin_counts: Counter[str] = Counter()
    origin_bytes: Counter[str] = Counter()
    type_counts_by_period: dict[int, Counter[str]] = defaultdict(Counter)
    digest = hashlib.sha256()
    for row in rows:
        event_type = str(row.get("type", "UNKNOWN"))
        event_class = str(row.get("class", "unclassified"))
        key = (event_class, event_type)
        counts_by_class_type[key] += 1
        encoded = json.dumps(row, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
        bytes_by_class_type[key] += len(encoded) + 1
        origin = "initialization_tick_0" if int(row.get("tick", 0)) == 0 else "simulation_tick_gt_0"
        origin_counts[origin] += 1
        origin_bytes[origin] += len(encoded) + 1
        digest.update(encoded)
        digest.update(b"\n")
        day = max(0, int(row.get("tick", 0)) // 24)
        type_counts_by_period[0 if day == 0 else (day - 1) // 30 + 1][event_type] += 1

    crews: dict[str, dict[str, Any]] = {}
    merchant_ships: set[str] = set()
    for row in rows:
        data = row.get("data", {})
        if row.get("type") == "CREW_FOUNDED":
            crew_id = data.get("crew_id")
            if crew_id:
                crews[crew_id] = {"owner_type": data.get("owner_type"),
                                  "name": data.get("name"), "faction_id": None}
        elif row.get("type") == "FACTION_FOUNDED":
            faction_id = data.get("faction_id")
            member_crew_ids = data.get("member_crew_ids")
            if member_crew_ids is None and faction_id in crews:
                member_crew_ids = [faction_id]  # legacy singleton snapshot/history
            for crew_id in member_crew_ids or ():
                if crew_id in crews:
                    crews[crew_id]["faction_id"] = faction_id
        elif row.get("type") == "FACTION_MEMBER_DEPARTED":
            faction_id = data.get("faction_id")
            crew_id = data.get("crew_id")
            if crew_id in crews and crews[crew_id].get("faction_id") == faction_id:
                crews[crew_id]["faction_id"] = None
        elif row.get("type") == "FACTION_DISSOLVED":
            faction_id = data.get("faction_id")
            for crew_id in data.get("member_crew_ids", ()):
                if crew_id in crews and crews[crew_id].get("faction_id") == faction_id:
                    crews[crew_id]["faction_id"] = None
        elif row.get("type") == "MERCHANT_CREATED" and data.get("ship_id"):
            merchant_ships.add(data["ship_id"])
        elif row.get("type") == "MERCHANT_VESSEL_REPLACED" and data.get("old_ship_id"):
            merchant_ships.add(data["old_ship_id"])

    ship_to_crew: dict[str, str] = {}
    capture_history: dict[str, list[dict[str, Any]]] = defaultdict(list)
    capture_chain_mismatches = []
    encounter_owner_types: dict[str, dict[str, str | None]] = {}
    ship_control_history: dict[str, list[dict[str, Any]]] = defaultdict(list)
    membership_history: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        data = row.get("data", {})
        event_type = row.get("type")
        if event_type == "SHIP_CREATED":
            ship_id = next((part.get("entity_id") for part in row.get("targets", ())
                            if part.get("role") == "created_vessel"), None)
            if ship_id:
                ship_control_history[ship_id].append({
                    "event_id": row["id"], "tick": row.get("tick"), "event_type": event_type,
                    "asserted_controller_crew_id": None, "status": "created; controller not yet recorded",
                })
        elif event_type == "CREW_FOUNDED":
            ship_id, crew_id = data.get("ship_id"), data.get("crew_id")
            if ship_id and crew_id:
                ship_to_crew[ship_id] = crew_id
                ship_control_history[ship_id].append({
                    "event_id": row["id"], "tick": row.get("tick"), "event_type": event_type,
                    "asserted_controller_crew_id": crew_id, "status": "crew membership founded",
                })
            for person_id in data.get("roster", ()):
                membership_history[person_id].append({
                    "event_id": row["id"], "tick": row.get("tick"), "event_type": event_type,
                    "crew_id": crew_id, "change": "joined_or_initially_recorded",
                })
        elif event_type == "ENCOUNTER_RESOLVED":
            encounter_owner_types[row["id"]] = {
                ship_id: crews.get(ship_to_crew.get(ship_id), {}).get("owner_type")
                or ("merchant" if ship_id in merchant_ships else None)
                for ship_id in data.get("ships", ())
            }
        elif event_type == "SHIP_CAPTURED":
            ship_id = next((part.get("entity_id") for part in row.get("targets", ())
                            if part.get("role") == "captured_ship"), None)
            if not ship_id:
                continue
            history = capture_history[ship_id]
            reported_previous = (data.get("previous_controller_crew_id")
                                 or data.get("previous_crew_id"))
            expected_previous = ship_to_crew.get(ship_id)
            if expected_previous is not None and expected_previous != reported_previous:
                capture_chain_mismatches.append({
                    "ship_id": ship_id, "event_id": row["id"],
                    "tick": row.get("tick"), "expected_previous_crew_id": expected_previous,
                    "reported_previous_crew_id": reported_previous,
                    "prior_capture_event_id": history[-1]["event_id"] if history else None,
                })
            history.append({"event_id": row["id"], "tick": row.get("tick"),
                            "previous_crew_id": reported_previous,
                            "new_crew_id": data.get("new_crew_id")})
            current_controller = (data.get("new_controller_crew_id")
                                  or data.get("onboard_crew_id")
                                  or data.get("new_crew_id"))
            if current_controller:
                ship_to_crew[ship_id] = current_controller
            ship_control_history[ship_id].append({
                "event_id": row["id"], "tick": row.get("tick"), "event_type": event_type,
                "asserted_controller_crew_id": current_controller,
                "reported_previous_controller_crew_id": reported_previous,
                "status": "title transferred; controller and onboard crew recorded independently",
            })
        elif event_type == "SHIP_ACQUIRED":
            ship_id = next((part.get("entity_id") for part in row.get("targets", ())
                            if part.get("role") == "acquired_ship"), None)
            crew_id = (data.get("controller_crew_id") or data.get("new_crew_id")
                       or data.get("crew_id"))
            if ship_id and crew_id:
                ship_to_crew[ship_id] = crew_id
                ship_control_history[ship_id].append({
                    "event_id": row["id"], "tick": row.get("tick"), "event_type": event_type,
                    "asserted_controller_crew_id": crew_id,
                    "reported_previous_controller_crew_id": data.get("previous_crew_id"),
                    "method": data.get("method"), "status": "acquisition recorded",
                })
        elif event_type == "SHIP_SUNK":
            ship_id = next((part.get("entity_id") for part in row.get("targets", ())
                            if part.get("role") == "sunken_ship"), None)
            if ship_id:
                ship_control_history[ship_id].append({
                    "event_id": row["id"], "tick": row.get("tick"), "event_type": event_type,
                    "asserted_controller_crew_id": ship_to_crew.get(ship_id),
                    "status": "ship terminally lost",
                })
        elif event_type in {"DESERTION", "CREW_MEMBER_TRANSFERRED"}:
            person_id = next((part.get("entity_id") for part in row.get("targets", ())
                              if part.get("role") in {"deserter", "transferred_person", "defector"}), None)
            if person_id:
                membership_history[person_id].append({
                    "event_id": row["id"], "tick": row.get("tick"), "event_type": event_type,
                    "crew_id": data.get("crew_id") or data.get("from_crew_id"),
                    "to_crew_id": data.get("to_crew_id"), "change": "membership_transition",
                })

    dissolved_at: dict[str, dict[str, Any]] = {}
    post_dissolution_actor_actions = []
    org_command_roles = {
        "faction", "organization", "faction_actor", "faction_command",
        "organizational_controller", "dissolved_faction", "government",
        "attacking_faction", "trading_faction", "proposer", "requester",
        "obligated_faction", "obligated_ally",
    }
    indirect_command_types = {
        "FACTION_POWER_VACUUM", "TERRITORY_CLAIMED", "ISLAND_FORTIFIED",
        "TREATY_BREACHED", "DIPLOMATIC_EXCHANGE", "FACTION_TRADE_RECORDED",
        "TREATY_TRANSITION", "TREATY_AID_REQUESTED", "TREATY_OBLIGATION_RESOLVED",
        "WAR_DECLARED",
    }
    post_dissolution_org_commands = []
    for position, row in enumerate(rows):
        for actor in row.get("actors", ()):
            actor_id = actor.get("entity_id")
            terminal = dissolved_at.get(actor_id)
            if terminal and position > terminal["position"]:
                post_dissolution_actor_actions.append({
                    "organization_id": actor_id, "dissolution_event_id": terminal["event_id"],
                    "actor_role": actor.get("role"), "event_id": row["id"],
                    "event_type": row.get("type"),
                    "tick": row.get("tick"),
                })
                if actor.get("role") in org_command_roles:
                    post_dissolution_org_commands.append({
                        "organization_id": actor_id,
                        "dissolution_event_id": terminal["event_id"],
                        "actor_role": actor.get("role"), "event_id": row["id"],
                        "event_type": row.get("type"), "tick": row.get("tick"),
                        "attribution": "explicit actor role",
                    })
        if row.get("type") in indirect_command_types:
            actors = row.get("actors", ())
            direct_ids = {actor.get("entity_id") for actor in actors
                          if actor.get("role") in org_command_roles}
            data = row.get("data", {})
            referenced_ids = set()
            if isinstance(data.get("faction_id"), str):
                referenced_ids.add(data["faction_id"])
            if isinstance(data.get("factions"), (list, tuple)):
                referenced_ids.update(item for item in data["factions"]
                                      if isinstance(item, str))
            for organization_id in referenced_ids - direct_ids:
                terminal = dissolved_at.get(organization_id)
                if terminal and position > terminal["position"]:
                    post_dissolution_org_commands.append({
                        "organization_id": organization_id,
                        "dissolution_event_id": terminal["event_id"],
                        "actor_role": "indirect_command", "event_id": row["id"],
                        "event_type": row.get("type"), "tick": row.get("tick"),
                        "attribution": "command event faction reference",
                    })
        if row.get("type") == "FACTION_DISSOLVED":
            faction_id = row.get("data", {}).get("faction_id")
            if faction_id:
                dissolved_at[faction_id] = {"event_id": row["id"], "tick": row.get("tick"),
                                            "position": position}

    encounters = [row for row in rows if row.get("type") == "ENCOUNTER_RESOLVED"]
    encounter_outcomes = Counter(row.get("data", {}).get("outcome", "unknown")
                                 for row in encounters)
    pair_events: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in encounters:
        ships = sorted(set(row.get("data", {}).get("ships", ())))
        if len(ships) == 2:
            pair_events[(ships[0], ships[1])].append(row)
    cadence_pairs = []
    for (left, right), pair_rows in pair_events.items():
        pair_rows.sort(key=lambda row: (row.get("tick", 0), row["id"]))
        intervals = [(b.get("tick", 0) - a.get("tick", 0)) // 24
                     for a, b in zip(pair_rows, pair_rows[1:])]
        if len(pair_rows) >= 3:
            interval_counts = Counter(intervals)
            locations = [row.get("location") for row in pair_rows]
            outcome_sequence = [row.get("data", {}).get("outcome", "unknown")
                                for row in pair_rows]
            stance_signatures = [tuple(sorted((row.get("data", {}).get("stances") or {}).items()))
                                 for row in pair_rows]
            dominant_interval, dominant_count = interval_counts.most_common(1)[0]
            regularity_permille = dominant_count * 1000 // max(1, len(intervals))
            same_location = len(set(map(repr, locations))) == 1
            if all(isinstance(location, str) and location.startswith("isle_")
                   for location in locations) and same_location:
                cadence_context = "repeated_island_co_location_observed"
            elif all(isinstance(location, str) and location.startswith("route_")
                     for location in locations) and same_location:
                cadence_context = "repeated_route_encounter_location_observed"
            elif all(location is None for location in locations):
                cadence_context = "spatial_context_missing"
            else:
                cadence_context = "spatial_context_varies"
            unexplained_periodicity = (
                regularity_permille >= 800 and cadence_context == "spatial_context_missing"
                and len(set(outcome_sequence)) == 1 and len(set(stance_signatures)) == 1)
            cadence_assessment = (
                "unexplained_periodicity_candidate" if unexplained_periodicity else
                "contextual_repeat_not_flagged")
            cadence_pairs.append({
                "ships": [left, right], "encounters": len(pair_rows),
                "days": [row.get("tick", 0) // 24 for row in pair_rows],
                "interval_days": intervals,
                "most_common_interval_days": dominant_interval,
                "most_common_interval_count": dominant_count,
                "regularity_permille": regularity_permille,
                "locations": locations,
                "cadence_context": cadence_context,
                "outcome_sequence": outcome_sequence,
                "repeated_outcome_share_permille": max(Counter(outcome_sequence).values())
                * 1000 // len(outcome_sequence),
                "stance_sequence": [dict(signature) for signature in stance_signatures],
                "cadence_assessment": cadence_assessment,
                "outcomes": dict(Counter(outcome_sequence)),
            })
    cadence_pairs.sort(key=lambda row: (-row["encounters"], row["ships"]))
    cadence_flags = [row for row in cadence_pairs
                     if row["cadence_assessment"] == "unexplained_periodicity_candidate"]

    target_events = [row for row in rows if row.get("type") == "RAIDER_TARGET_SELECTED"]
    target_groups: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    target_ship_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in target_events:
        target_ship = next((part.get("entity_id") for part in row.get("targets", ())
                            if part.get("role") == "merchant_vessel"), None)
        if not target_ship:
            continue
        target_groups[(int(row.get("tick", 0)), target_ship)].append(row)
        target_ship_events[target_ship].append(row)
    target_same_tick = [
        {"tick": tick, "day": tick // 24, "target_ship_id": ship_id,
         "selection_count": len(group),
         "captains": [next((actor.get("entity_id") for actor in row.get("actors", ())
                            if actor.get("role") == "raider_captain"), None) for row in group],
         "distinct_estimates": len({(row.get("data", {}).get("estimated_cargo_value_milli"),
                                      row.get("data", {}).get("estimated_patrol_density_milli"))
                                     for row in group})}
        for (tick, ship_id), group in sorted(target_groups.items()) if len(group) > 1
    ]
    target_dispositions = []
    encounter_ticks_by_ship: dict[str, list[int]] = defaultdict(list)
    for row in encounters:
        for ship_id in row.get("data", {}).get("ships", ()):
            encounter_ticks_by_ship[ship_id].append(int(row.get("tick", 0)))
    for ship_id, target_rows in sorted(target_ship_events.items()):
        first_target_tick = min(int(row.get("tick", 0)) for row in target_rows)
        future_encounters = [tick for tick in encounter_ticks_by_ship.get(ship_id, ())
                             if tick >= first_target_tick]
        target_dispositions.append({
            "target_ship_id": ship_id, "selections": len(target_rows),
            "selection_days": sorted({int(row.get("tick", 0)) // 24 for row in target_rows}),
            "first_selection_event_id": min(target_rows, key=lambda row: row.get("tick", 0))["id"],
            "any_later_encounter_involving_target": bool(future_encounters),
            "later_encounter_count_proxy": len(future_encounters),
            "limitation": "Proxy only: event log does not link a selected raider to a specific later encounter.",
        })

    pursuit_commits = [row for row in rows if row.get("type") == "RAIDER_PURSUIT_COMMITTED"]
    pursuit_resolutions = [row for row in rows if row.get("type") == "RAIDER_PURSUIT_RESOLVED"]
    resolutions_by_selection: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for resolution in pursuit_resolutions:
        selected_id = resolution.get("data", {}).get("selected_event_id")
        if selected_id:
            resolutions_by_selection[selected_id].append(resolution)
    pursuit_rows = []
    unresolved_selections = []
    uncommitted_selections = []
    invalid_pursuit_links = []
    resolved_counts = Counter()
    for selection in target_events:
        selection_id = selection["id"]
        target_ship_id = next((part.get("entity_id") for part in selection.get("targets", ())
                               if part.get("role") == "merchant_vessel"), None)
        commits = [row for row in pursuit_commits
                   if selection_id in row.get("caused_by", ())
                   and any(part.get("entity_id") == target_ship_id
                           for part in row.get("targets", ())) ]
        results = resolutions_by_selection.get(selection_id, [])
        if not commits:
            uncommitted_selections.append(selection_id)
        if not results:
            unresolved_selections.append(selection_id)
        for resolution in results:
            commit_id = resolution.get("data", {}).get("commitment_event_id")
            matching_commit = next((row for row in commits if row["id"] == commit_id), None)
            valid = (matching_commit is not None
                     and selection_id in resolution.get("caused_by", ())
                     and commit_id in resolution.get("caused_by", ()))
            if resolution.get("data", {}).get("reason") == "causally_linked_encounter":
                encounter_parent = next((by_id.get(parent_id)
                                         for parent_id in resolution.get("caused_by", ())
                                         if by_id.get(parent_id, {}).get("type") == "ENCOUNTER_RESOLVED"),
                                        None)
                valid = valid and encounter_parent is not None
            if not valid:
                invalid_pursuit_links.append({
                    "selection_event_id": selection_id,
                    "resolution_event_id": resolution["id"],
                    "commitment_event_id": commit_id,
                })
            outcome = resolution.get("data", {}).get("outcome", "unknown")
            resolved_counts[outcome] += 1
            pursuit_rows.append({
                "selection_event_id": selection_id,
                "commitment_event_id": commit_id,
                "resolution_event_id": resolution["id"],
                "target_ship_id": target_ship_id,
                "outcome": outcome,
                "causal_links_valid": bool(valid),
            })
    orphan_pursuit_resolutions = [row["id"] for row in pursuit_resolutions
                                  if row.get("data", {}).get("selected_event_id")
                                  not in {event["id"] for event in target_events}]
    target_selection_count = len(target_events)
    target_counts = Counter(
        next((part.get("entity_id") for part in row.get("targets", ())
              if part.get("role") == "merchant_vessel"), None)
        for row in target_events)
    target_counts.pop(None, None)
    raider_pursuit = {
        "selections": target_selection_count,
        "commitments": len(pursuit_commits),
        "resolutions": len(pursuit_resolutions),
        "resolved_outcomes": dict(sorted(resolved_counts.items())),
        "successful_interceptions": resolved_counts.get("intercepted", 0),
        "interception_permille_of_selections": (
            resolved_counts.get("intercepted", 0) * 1000 // target_selection_count
            if target_selection_count else None),
        "uncommitted_selection_event_ids": uncommitted_selections,
        "unresolved_selection_event_ids": unresolved_selections,
        "orphan_resolution_event_ids": orphan_pursuit_resolutions,
        "invalid_causal_links": invalid_pursuit_links,
        "dispositions": pursuit_rows,
        "target_selections": dict(sorted(target_counts.items())),
        "distinct_targets": len(target_counts),
        "maximum_target_share_permille": (
            max(target_counts.values()) * 1000 // target_selection_count
            if target_selection_count else None),
    }

    merchant_loss_events = []
    loss_types = {"SHIP_SUNK", "SHIP_CAPTURED", "COMBAT_LOOT_TRANSFERRED"}
    for row in rows:
        if row.get("type") not in loss_types:
            continue
        data = row.get("data", {})
        if row["type"] == "SHIP_SUNK":
            ship_id = next((part.get("entity_id") for part in row.get("targets", ())
                            if part.get("role") == "sunken_ship"), None)
        elif row["type"] == "SHIP_CAPTURED":
            ship_id = next((part.get("entity_id") for part in row.get("targets", ())
                            if part.get("role") == "captured_ship"), None)
        else:
            ship_id = data.get("loser_ship_id")
            if not any(data.get("goods_milli", {}).values()):
                continue
        if ship_id not in merchant_ships:
            continue
        ancestry = _ancestors(row, by_id)
        encounter = next((parent for parent in ancestry
                          if parent.get("type") == "ENCOUNTER_RESOLVED"), None)
        owner_types = encounter_owner_types.get(encounter["id"], {}) if encounter else {}
        if encounter:
            for involved_ship in encounter.get("data", {}).get("ships", ()):
                owner_types.setdefault(
                    involved_ship,
                    "merchant" if involved_ship in merchant_ships else None)
        involved_owner_types = list(owner_types.values())
        merchant_loss_events.append({
            "event_id": row["id"], "tick": row.get("tick"), "event_type": row["type"],
            "ship_id": ship_id,
            "causal_encounter_id": encounter.get("id") if encounter else None,
            "causal_outcome": encounter.get("data", {}).get("outcome") if encounter else None,
            "participant_owner_types": owner_types,
            "cause_resolved": encounter is not None,
            "pirate_involved_by_event_time_owner_history": "pirate" in involved_owner_types,
        })

    loss_incidents: dict[tuple[str, str], dict[str, Any]] = {}
    for loss in merchant_loss_events:
        incident_key = (loss["causal_encounter_id"] or loss["event_id"], loss["ship_id"])
        previous = loss_incidents.get(incident_key)
        if previous is None:
            loss_incidents[incident_key] = dict(loss)
        else:
            previous["pirate_involved_by_event_time_owner_history"] |= loss[
                "pirate_involved_by_event_time_owner_history"]
            previous["event_ids"] = sorted(set(previous.get("event_ids", [previous["event_id"]])
                                                + [loss["event_id"]]))

    death_causes = Counter(row.get("data", {}).get("cause", "unknown") for row in rows
                           if row.get("type") == "CHARACTER_DIED")
    injury_count = sum(row.get("type") == "INJURY" for row in rows)
    non_consequential_encounters = {
        "mutual_withdrawal": encounter_outcomes["mutual_withdrawal"],
        "parley": sum(count for name, count in encounter_outcomes.items()
                       if "parley" in str(name)),
    }
    repeated_grudge_pairs: Counter[tuple[str, str]] = Counter()
    for row in rows:
        if row.get("type") == "FEUD_RAID_ATTEMPTED":
            actor = next((part.get("entity_id") for part in row.get("actors", ())), "unknown")
            target = next((part.get("entity_id") for part in row.get("targets", ())), "unknown")
            repeated_grudge_pairs[(actor, target)] += 1

    population_disposition_errors = []
    ship_loss_dispositions = []
    for row in rows:
        if row.get("type") != "SHIP_LOSS_DISPOSITION":
            continue
        data = row.get("data", {})
        roster_before = data.get("crew_roster_before")
        partitions = {name: data.get(name) for name in ("survivors", "missing", "deceased")}
        invalid = []
        if (not isinstance(roster_before, list)
                or any(not isinstance(person_id, str) or not person_id
                       for person_id in roster_before)
                or len(roster_before) != len(set(roster_before))):
            invalid.append("invalid crew_roster_before")
        normalized = {}
        for name, values in partitions.items():
            if (not isinstance(values, list)
                    or any(not isinstance(person_id, str) or not person_id for person_id in values)
                    or len(values) != len(set(values))):
                invalid.append(f"invalid {name} partition")
            else:
                normalized[name] = set(values)
        if len(normalized) == 3 and isinstance(roster_before, list):
            union = set().union(*normalized.values())
            if sum(map(len, normalized.values())) != len(union):
                invalid.append("overlapping survivor/missing/deceased partitions")
            if union != set(roster_before):
                invalid.append("crew roster is not exhaustively dispositioned")
        released = data.get("prisoners_released", [])
        if (not isinstance(released, list)
                or any(not isinstance(person_id, str) or not person_id for person_id in released)
                or len(released) != len(set(released))):
            invalid.append("invalid prisoners_released partition")
        ship_loss_dispositions.append({
            "event_id": row["id"],
            "crew_roster_count": len(roster_before) if isinstance(roster_before, list) else None,
            "survivors": len(partitions["survivors"])
            if isinstance(partitions["survivors"], list) else None,
            "missing": len(partitions["missing"])
            if isinstance(partitions["missing"], list) else None,
            "deceased": len(partitions["deceased"])
            if isinstance(partitions["deceased"], list) else None,
            "prisoners_released": len(released) if isinstance(released, list) else None,
            "status": "invalid" if invalid else "balanced",
            "errors": invalid,
        })
        if invalid:
            population_disposition_errors.append({"event_id": row["id"], "errors": invalid})
    membership_transition_ledger = audit_crew_membership_transitions(rows)
    faction_membership_ledger = audit_faction_membership_transitions(rows)
    faction_authority_ledger = audit_faction_authority_transitions(rows)
    character_identity_ledger = audit_character_identity_transitions(rows)
    prisoner_custody_ledger = audit_prisoner_custody_transitions(rows)

    return {
        "event_count": len(rows),
        "event_log_sha256": digest.hexdigest(),
        "exported_event_rows_and_bytes": {
            "rows": len(rows),
            "canonical_jsonl_bytes": sum(bytes_by_class_type.values()),
            "by_tick_origin": {
                origin: {"rows": origin_counts[origin], "canonical_jsonl_bytes": origin_bytes[origin]}
                for origin in sorted(origin_counts)
            },
        },
        "events_by_class_and_type": {
            event_class: {
                event_type: {"count": counts_by_class_type[(event_class, event_type)],
                             "canonical_jsonl_bytes": bytes_by_class_type[(event_class, event_type)]}
                for klass, event_type in sorted(counts_by_class_type)
                if klass == event_class
            }
            for event_class in sorted({key[0] for key in counts_by_class_type})
        },
        "event_classification_policy": {
            event_type: dict(policy)
            for event_type, policy in sorted(EVENT_CLASS_POLICY.items())
        },
        "events_by_30_day_period": {
            str(period): dict(sorted(counts.items()))
            for period, counts in sorted(type_counts_by_period.items())
        },
        "capture_histories": dict(sorted(capture_history.items())),
        "capture_owner_chain_mismatches": capture_chain_mismatches,
        "recorded_ship_control_history": dict(sorted(ship_control_history.items())),
        "recorded_person_membership_history": dict(sorted(membership_history.items())),
        "dissolved_organizations": dissolved_at,
        "post_dissolution_actor_actions": post_dissolution_actor_actions,
        "post_dissolution_org_commands": post_dissolution_org_commands,
        "encounter_outcomes": dict(sorted(encounter_outcomes.items())),
        "repeated_encounter_pairs": cadence_pairs,
        "cadence_audit_policy": {
            "minimum_encounters": 3,
            "periodic_interval_share_permille": 800,
            "flag_requires": ["missing encounter location", "same outcome sequence",
                              "same stance sequence"],
            "same_island_or_route_location": "reported as observed spatial opportunity, not flagged solely by frequency",
        },
        "unexplained_cadence_candidates": cadence_flags,
        "target_same_tick_concentration": target_same_tick,
        "target_dispositions_proxy": target_dispositions,
        "raider_pursuit": raider_pursuit,
        "merchant_loss_events": merchant_loss_events,
        "merchant_loss_incidents": [loss_incidents[key]
                                     for key in sorted(loss_incidents)],
        "character_deaths_by_cause": dict(sorted(death_causes.items())),
        "ship_loss_dispositions": ship_loss_dispositions,
        "population_disposition_errors": population_disposition_errors,
        "membership_transition_ledger": membership_transition_ledger,
        "faction_membership_ledger": faction_membership_ledger,
        "faction_authority_ledger": faction_authority_ledger,
        "character_identity_ledger": character_identity_ledger,
        "prisoner_custody_ledger": prisoner_custody_ledger,
        "injury_event_count": injury_count,
        "withdrawals_and_parleys": non_consequential_encounters,
        "feud_attempts": sum(repeated_grudge_pairs.values()),
        "repeated_feud_pairs": [
            {"actor_id": actor, "target_id": target, "attempts": count}
            for (actor, target), count in repeated_grudge_pairs.most_common(20)
        ],
        "limitations": [
            "An event-only log cannot prove current ownership when no transfer event is emitted; capture-chain mismatches detect repeated stale prior-owner claims.",
            "Legacy target-selection-only logs retain target-to-encounter proxy matching; explicit raider pursuit records are audited separately.",
            "Headcount conservation requires population/squad census snapshots; event counts alone cannot establish it.",
            "Historical owner-type attribution is partial where the event log omits owner type or crew-to-ship membership changes.",
        ],
    }


def write_audit(records: list[dict[str, Any]], output: str | Path) -> tuple[Path, Path]:
    outdir = Path(output)
    outdir.mkdir(parents=True, exist_ok=True)
    report = audit_event_records(records)
    json_path = outdir / "coherence-audit.json"
    md_path = outdir / "coherence-audit.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8", newline="\n")
    lines = [
        "# Simulation coherence audit", "",
        f"- Events: {report['event_count']:,}",
        f"- Canonical JSONL SHA-256: `{report['event_log_sha256']}`", "",
        "## Event histogram by class and type", "",
        "| Class | Event type | Count | Canonical JSONL bytes |",
        "|---|---|---:|---:|",
    ]
    for event_class, types in report["events_by_class_and_type"].items():
        for event_type, values in types.items():
            lines.append(f"| {event_class} | `{event_type}` | {values['count']:,} | {values['canonical_jsonl_bytes']:,} |")
    lines.extend(["", "## Confirmed event-trace flags", "",
                  f"- Capture owner-chain mismatches: {len(report['capture_owner_chain_mismatches'])}",
                  f"- Actor events referencing dissolved faction IDs: {len(report['post_dissolution_actor_actions'])} "
                  f"(role-qualified references required to distinguish crew/faction ID collisions)",
                  f"- Attributed faction/organization commands after dissolution: "
                  f"{len(report['post_dissolution_org_commands'])}",
                  f"- Encounter outcomes: `{json.dumps(report['encounter_outcomes'], sort_keys=True)}`",
                   f"- Repeated encounter pairs (3+ meets): {len(report['repeated_encounter_pairs'])}",
                   f"- Cadence review candidates: {len(report['unexplained_cadence_candidates'])} "
                   "(periodic gaps + missing location + unchanged outcome/stances)",
                   f"- Same-tick duplicate target groups: {len(report['target_same_tick_concentration'])}",
                   f"- Raider pursuits: {report['raider_pursuit']['selections']} selected, "
                   f"{report['raider_pursuit']['commitments']} committed, "
                   f"{report['raider_pursuit']['resolutions']} resolved; outcomes "
                   f"`{json.dumps(report['raider_pursuit']['resolved_outcomes'], sort_keys=True)}`; "
                   f"causal-link errors: {len(report['raider_pursuit']['invalid_causal_links'])}",
                   f"- Raider target distribution: {report['raider_pursuit']['distinct_targets']} distinct, "
                   f"max share {report['raider_pursuit']['maximum_target_share_permille']}‰; "
                   f"uncommitted {len(report['raider_pursuit']['uncommitted_selection_event_ids'])}, "
                   f"unresolved {len(report['raider_pursuit']['unresolved_selection_event_ids'])}",
                   f"- Merchant loss incidents with a linked encounter ancestor: "
                  f"{sum(row['cause_resolved'] for row in report['merchant_loss_incidents'])}/"
                  f"{len(report['merchant_loss_incidents'])}",
                  f"- Character deaths by cause: `{json.dumps(report['character_deaths_by_cause'], sort_keys=True)}`",
                  f"- Ship-loss population dispositions: {len(report['ship_loss_dispositions'])}; "
                  f"partition errors: {len(report['population_disposition_errors'])}",
                  f"- Replayed crew membership transitions: "
                  f"{report['membership_transition_ledger']['transition_count']}; "
                  f"per-event flow rows: "
                  f"{report['membership_transition_ledger']['membership_flow_transition_count']}; "
                  f"closing active members: "
                  f"{report['membership_transition_ledger']['active_membership_count']}; "
                  f"transition errors: {len(report['membership_transition_ledger']['errors'])}",
                  f"- Replayed character identity/LOD transitions: "
                  f"{report['character_identity_ledger']['transition_count']}; "
                  f"transition errors: {len(report['character_identity_ledger']['errors'])}",
                  f"- Replayed prisoner custody transitions: "
                  f"{report['prisoner_custody_ledger']['transition_count']}; "
                  f"prisoners: {report['prisoner_custody_ledger']['prisoner_count']}; "
                  f"transition errors: {len(report['prisoner_custody_ledger']['errors'])}",
                  f"- Injury events: {report['injury_event_count']}",
                  f"- Feud attempts: {report['feud_attempts']:,}", "",
                  "## Capture owner-chain mismatches", ""])
    for row in report["capture_owner_chain_mismatches"]:
        lines.append(f"- `{row['ship_id']}` `{row['event_id']}` tick {row['tick']}: "
                     f"reported previous {row['reported_previous_crew_id']}, expected "
                     f"{row['expected_previous_crew_id']} after `{row['prior_capture_event_id']}`.")
    lines.extend(["", "## Dissolved-organization actor actions", ""])
    for row in report["post_dissolution_actor_actions"]:
        lines.append(f"- `{row['organization_id']}` acted in `{row['event_id']}` "
                     f"({row['event_type']}, tick {row['tick']}) after "
                     f"`{row['dissolution_event_id']}`.")
    lines.extend(["", "## Attributed faction/organization commands after dissolution", ""])
    for row in report["post_dissolution_org_commands"]:
        lines.append(f"- `{row['organization_id']}` as `{row['actor_role']}` in `{row['event_id']}` "
                     f"({row['event_type']}, tick {row['tick']}) after "
                     f"`{row['dissolution_event_id']}`; {row.get('attribution', 'role')}.")
    lines.extend(["", "## Ship-loss population disposition partitions", ""])
    for row in report["ship_loss_dispositions"]:
        lines.append(f"- `{row['event_id']}`: {row['status']}; roster={row['crew_roster_count']}, "
                     f"survivors={row['survivors']}, missing={row['missing']}, "
                     f"deceased={row['deceased']}, released prisoners={row['prisoners_released']}. "
                     + (f"Errors: {', '.join(row['errors'])}." if row["errors"] else ""))
    lines.extend(["", "## Repeated encounter pairs", "",
                  "| Ships | Encounters | Most common interval (days) | Repetitions at interval |",
                  "|---|---:|---:|---:|"])
    for row in report["repeated_encounter_pairs"][:30]:
        lines.append(f"| `{row['ships'][0]}` / `{row['ships'][1]}` | {row['encounters']} | "
                     f"{row['most_common_interval_days']} | {row['most_common_interval_count']} |")
    lines.extend(["", "## Known limits", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit a durable simulation event JSONL log")
    parser.add_argument("events", type=Path, help="plain or gzip-compressed Event.to_record JSONL")
    parser.add_argument("--output", type=Path, default=Path("reports/coherence"))
    args = parser.parse_args(argv)
    records = read_event_records(args.events)
    json_path, md_path = write_audit(records, args.output)
    print(json.dumps({"event_count": len(records), "json": str(json_path), "markdown": str(md_path)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
