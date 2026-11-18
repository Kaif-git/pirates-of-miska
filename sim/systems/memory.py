"""Trait-shaped memories and deterministically derived interpersonal opinions."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any


MEMORY_RULES_PATH = Path(__file__).resolve().parents[1] / "data" / "memory_rules.json"
_DIMENSIONS = {"trust", "fear", "respect", "grievance", "affection", "debt"}


def load_memory_rules(path: str | Path = MEMORY_RULES_PATH) -> tuple[dict[str, Any], str]:
    try:
        rules = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load memory rules {path}: {exc}") from exc
    validate_memory_rules(rules)
    digest = hashlib.sha256(json.dumps(
        rules, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    return rules, digest


def validate_memory_rules(data: Any) -> None:
    if (not isinstance(data, dict) or set(data) != {"schema_version", "rules", "memory_kinds"}
            or type(data["schema_version"]) is not int or data["schema_version"] != 1):
        raise ValueError("Invalid memory rules catalog schema")
    rules = data["rules"]
    rule_keys = {
        "max_memories_per_character", "repeat_multiplier_permille",
        "max_repeat_multiplier_permille", "core_intensity_permille",
        "grievance_pride_decay_modifier_permille",
        "grievance_paranoia_decay_modifier_permille", "sociability_decay_modifier_permille",
        "max_trait_decay_modifier_permille", "inherited_grudge_threshold",
        "inheritance_intensity_permille", "revenge_want_threshold",
        "vengeance_dream_threshold",
    }
    if (not isinstance(rules, dict) or set(rules) != rule_keys
            or any(type(value) is not int or value < 0 for value in rules.values())
            or rules["max_memories_per_character"] < 1
            or rules["repeat_multiplier_permille"] > rules["max_repeat_multiplier_permille"]
            or rules["max_repeat_multiplier_permille"] > 10000
            or rules["core_intensity_permille"] > 1000
            or rules["inherited_grudge_threshold"] > 100
            or rules["inheritance_intensity_permille"] > 1000
            or rules["revenge_want_threshold"] > 100
            or rules["vengeance_dream_threshold"] > 100
            or rules["max_trait_decay_modifier_permille"] > 1000):
        raise ValueError("Invalid memory rules")
    kinds = data["memory_kinds"]
    if not isinstance(kinds, dict) or not kinds:
        raise ValueError("Memory rules must define kinds")
    for kind, profile in kinds.items():
        if (not isinstance(kind, str) or not kind
                or not isinstance(profile, dict)
                or set(profile) != {"decay_permille_per_day", "protected", "effects"}
                or type(profile["decay_permille_per_day"]) is not int
                or not 0 <= profile["decay_permille_per_day"] <= 1000
                or type(profile["protected"]) is not bool
                or not isinstance(profile["effects"], dict)
                or set(profile["effects"]) - _DIMENSIONS
                or any(type(value) is not int or not -1000 <= value <= 1000
                       for value in profile["effects"].values())):
            raise ValueError(f"Invalid memory kind profile: {kind}")


class MemorySystem:
    """Stores event-attributed memories and recomputes bounded opinion records."""

    def __init__(self, *, digest: str, last_updated_day: int = 0) -> None:
        if type(last_updated_day) is not int or last_updated_day < 0:
            raise ValueError("Invalid memory update day")
        self.rules_data, actual_digest = load_memory_rules()
        if digest != actual_digest:
            raise ValueError("Memory rules changed since snapshot creation")
        self.digest = digest
        self.last_updated_day = last_updated_day
        self._intensity_cache_day: int | None = None
        self._intensity_cache: dict[str, int] = {}

    @classmethod
    def create(cls) -> "MemorySystem":
        _, digest = load_memory_rules()
        return cls(digest=digest)

    @classmethod
    def restore(cls, snapshot: dict[str, Any]) -> "MemorySystem":
        if (not isinstance(snapshot, dict) or set(snapshot) != {"digest", "last_updated_day"}
                or type(snapshot.get("last_updated_day")) is not int):
            raise ValueError("Invalid memory system snapshot")
        return cls(digest=snapshot["digest"], last_updated_day=snapshot["last_updated_day"])

    def add_memory(self, world, holder_id: str, target_id: str, kind: str, *,
                   event_id: str, intensity_permille: int = 600,
                   valence_permille: int = -500) -> str:
        holder = self._character(world, holder_id)
        self._character(world, target_id)
        try:
            source = world.event_log.get(event_id)
        except KeyError as exc:
            raise ValueError(f"Memory references unknown event: {event_id}") from exc
        profile = self.rules_data["memory_kinds"].get(kind)
        if profile is None:
            raise ValueError(f"Unknown memory kind: {kind}")
        if (source.tick > world.clock.tick or type(intensity_permille) is not int
                or not 0 <= intensity_permille <= 1000
                or type(valence_permille) is not int or not -1000 <= valence_permille <= 1000):
            raise ValueError("Invalid memory intensity, valence, or source event time")

        today = self._day_at(world, world.clock.tick)
        repeat_count = sum(
            1 for memory in holder["memory"]
            if memory["kind"] == kind and target_id in memory["subject_ids"]
            and self.current_intensity(memory, today) > 0
        )
        multiplier = min(
            self.rules_data["rules"]["max_repeat_multiplier_permille"],
            1000 + repeat_count * self.rules_data["rules"]["repeat_multiplier_permille"],
        )
        intensity = min(1000, intensity_permille * multiplier // 1000)
        rate = self._trait_adjusted_decay(holder, kind, profile["decay_permille_per_day"])
        protected = profile["protected"] or intensity >= self.rules_data["rules"][
            "core_intensity_permille"]
        event = world.event_log.append(
            tick=world.clock.tick, event_type="MEMORY_CREATED",
            location=holder.get("location", {}).get("id"),
            actors=[{"role": "memory_holder", "entity_id": holder_id}],
            targets=[{"role": "remembered_subject", "entity_id": target_id}],
            data={"kind": kind, "source_event_id": event_id,
                  "intensity_permille": intensity, "valence_permille": valence_permille,
                  "repeat_count": repeat_count, "decay_permille_per_day": rate,
                  "protected": protected},
            caused_by=[event_id], causes_tags=["memory", f"memory:{kind}"],
            magnitude=intensity / 1000,
        )
        holder["memory"].append({
            "event_id": event.id, "source_event_id": event_id,
            "subject_ids": [target_id], "kind": kind,
            "valence_permille": valence_permille,
            "base_intensity_permille": intensity,
            "decay_permille_per_day": rate, "tick": world.clock.tick,
            "day": today, "protected": protected,
        })
        pruned_targets = self._prune(holder, today)
        holder["last_event_id"] = event.id
        affected_targets = {target_id, *pruned_targets}
        if today == self.last_updated_day:
            opinions = holder["opinions"]
            for affected_target in affected_targets:
                opinion = self._recompute_target(holder, affected_target, today)
                if opinion is None:
                    opinions.pop(affected_target, None)
                else:
                    opinions[affected_target] = opinion
        else:
            opinions = self.recompute(world, holder_id)
        grievance = opinions.get(target_id, {}).get("grievance", 0)
        self._register_revenge(world, holder_id, target_id, grievance, event.id)
        return event.id

    def reconcile(self, world, holder_id: str, target_id: str, *, manner: str,
                  caused_by: str) -> str:
        if manner not in {"apology_gift", "shared_victory", "mediation"}:
            raise ValueError("Unknown reconciliation manner")
        holder = self._character(world, holder_id)
        target = self._character(world, target_id)
        if holder.get("death_tick") is not None or target.get("death_tick") is not None:
            raise ValueError("Reconciliation requires living characters")
        opinion = holder.get("opinions", {}).get(target_id, {})
        grievance = max(0, opinion.get("grievance", 0))
        world.event_log.get(caused_by)
        event = world.event_log.append(
            tick=world.clock.tick, event_type="RECONCILIATION",
            location=world.location_of(holder_id)["id"],
            actors=[{"role": "reconciler", "entity_id": holder_id}],
            targets=[{"role": "forgiven", "entity_id": target_id}],
            data={"manner": manner, "grievance_before": grievance},
            caused_by=[caused_by], causes_tags=["reconciliation", manner], magnitude=0.3,
        )
        intensity = min(1000, max(350, grievance * 10))
        memory_id = self.add_memory(
            world, holder_id, target_id, "reconciled", event_id=event.id,
            intensity_permille=intensity, valence_permille=1000,
        )
        if world.event_log.get(memory_id).caused_by != (event.id,):
            raise ValueError("Reconciliation memory is not causally linked")
        return event.id

    def insult(self, world, instigator_id: str, target_id: str, *, audience_size: int,
               caused_by: str) -> str:
        instigator = self._character(world, instigator_id)
        target = self._character(world, target_id)
        if (instigator_id == target_id or instigator.get("death_tick") is not None
                or target.get("death_tick") is not None
                or type(audience_size) is not int or not 0 <= audience_size <= 1000):
            raise ValueError("Insults require two living characters and a bounded audience")
        world.event_log.get(caused_by)
        intensity = min(1000, 250 + target["traits"]["pride"] * 4
                        + min(audience_size, 25) * 12)
        event = world.event_log.append(
            tick=world.clock.tick, event_type="INSULT",
            location=world.location_of(target_id)["id"],
            actors=[{"role": "insulter", "entity_id": instigator_id}],
            targets=[{"role": "insulted", "entity_id": target_id}],
            data={"audience_size": audience_size, "target_pride": target["traits"]["pride"],
                  "intensity_permille": intensity},
            caused_by=[caused_by], causes_tags=["social_offense", "public_insult"],
            magnitude=intensity / 1000,
        )
        self.add_memory(
            world, target_id, instigator_id, "humiliated", event_id=event.id,
            intensity_permille=intensity, valence_permille=-1000,
        )
        return event.id

    def assign_mentor(self, world, mentor_id: str, student_id: str, *,
                      caused_by: str) -> str:
        if mentor_id == student_id:
            raise ValueError("A character cannot mentor themself")
        mentor = self._character(world, mentor_id)
        student = self._character(world, student_id)
        if mentor.get("death_tick") is not None or student.get("death_tick") is not None:
            raise ValueError("Mentorship requires living characters")
        world.event_log.get(caused_by)
        event = world.event_log.append(
            tick=world.clock.tick, event_type="MENTORSHIP_ESTABLISHED",
            location=world.location_of(student_id)["id"],
            actors=[{"role": "mentor", "entity_id": mentor_id}],
            targets=[{"role": "student", "entity_id": student_id}],
            data={"mentor_id": mentor_id, "student_id": student_id},
            caused_by=[caused_by], causes_tags=["mentorship", "heritage_link"], magnitude=0.2,
        )
        student["heritage"]["mentor"] = mentor_id
        student["last_event_id"] = event.id
        return event.id

    def on_character_death(self, world, deceased_id: str, death_event_id: str) -> list[str]:
        deceased = self._character(world, deceased_id)
        threshold = self.rules_data["rules"]["inherited_grudge_threshold"]
        inherit_scale = self.rules_data["rules"]["inheritance_intensity_permille"]
        dependents = [
            (entity_id, character) for entity_id, character in sorted(world.entities.items())
            if character.get("kind") == "character"
            and (deceased_id in character["heritage"].get("parents", [])
                 or character["heritage"].get("mentor") == deceased_id)
        ]
        inherited_events = []
        for student_id, student in dependents:
            for target_id, opinion in sorted(deceased.get("opinions", {}).items()):
                grievance = opinion.get("grievance", 0)
                if grievance < threshold:
                    continue
                intensity = min(100, grievance * inherit_scale // 1000)
                if intensity <= 0:
                    continue
                evidence = self.grudge_evidence(world, deceased_id, target_id)
                parents = list(dict.fromkeys([death_event_id, *evidence]))
                event = world.event_log.append(
                    tick=world.clock.tick, event_type="GRUDGE_INHERITED",
                    location=student.get("location", {}).get("id"),
                    actors=[{"role": "heir", "entity_id": student_id}],
                    targets=[{"role": "deceased", "entity_id": deceased_id},
                             {"role": "inherited_target", "entity_id": target_id}],
                    data={"source_character_id": deceased_id, "target_id": target_id,
                          "grievance": grievance, "intensity": intensity},
                    caused_by=parents, causes_tags=["heritage", "inherited_grudge"], magnitude=0.5,
                )
                student["heritage"].setdefault("inherited_grudges", []).append({
                    "target": target_id, "intensity": intensity,
                    "source_event_id": event.id,
                })
                student["heritage"]["inherited_grudges"] = sorted(
                    student["heritage"]["inherited_grudges"],
                    key=lambda row: (row["target"], row.get("source_event_id", "")),
                )
                student["last_event_id"] = event.id
                inherited_events.append(event.id)
                opinions = self.recompute(world, student_id)
                self._register_revenge(
                    world, student_id, target_id,
                    opinions.get(target_id, {}).get("grievance", 0), event.id,
                )
        return inherited_events

    def grudge_evidence(self, world, holder_id: str, target_id: str) -> list[str]:
        holder = self._character(world, holder_id)
        evidence = [memory["event_id"] for memory in holder["memory"]
                    if target_id in memory["subject_ids"]
                    and self.current_intensity(memory, self._day_at(world, world.clock.tick)) > 0
                    and self.rules_data["memory_kinds"][memory["kind"]]["effects"].get(
                        "grievance", 0) > 0]
        evidence.extend(
            row["source_event_id"] for row in holder["heritage"].get("inherited_grudges", [])
            if row["target"] == target_id and row.get("source_event_id")
        )
        return list(dict.fromkeys(evidence))

    def _register_revenge(self, world, holder_id: str, target_id: str,
                          grievance: int, source_event_id: str) -> None:
        holder = self._character(world, holder_id)
        rules = self.rules_data["rules"]
        if grievance < rules["revenge_want_threshold"] or holder.get("death_tick") is not None:
            return
        urgency = min(1000, grievance * 10)
        want = next((row for row in holder["wants"]
                     if row.get("kind") == "revenge" and row.get("target_ref") == target_id), None)
        if want:
            want["urgency_permille"] = max(want["urgency_permille"], urgency)
            want["source_event"] = source_event_id
        else:
            holder["wants"].append({"kind": "revenge", "target_ref": target_id,
                                    "urgency_permille": urgency,
                                    "created_tick": world.clock.tick,
                                    "source_event": source_event_id})
            holder["wants"] = holder["wants"][-8:]
        dream = holder.get("dream", {})
        if (grievance >= rules["vengeance_dream_threshold"]
                and dream.get("kind") not in {"defeat_character", "destroy_entity"}):
            event = world.event_log.append(
                tick=world.clock.tick, event_type="VENGEANCE_DREAM",
                location=holder.get("location", {}).get("id"),
                actors=[{"role": "dreamer", "entity_id": holder_id}],
                targets=[{"role": "revenge_target", "entity_id": target_id}],
                data={"grievance": grievance, "dream_kind": "defeat_character"},
                caused_by=[source_event_id], causes_tags=["grudge", "vengeance_dream"], magnitude=0.4,
            )
            holder["dream"] = {"kind": "defeat_character", "target_ref": target_id,
                               "progress": 0, "stated_tick": world.clock.tick,
                               "source_event_id": event.id}
            holder["last_event_id"] = event.id

    def current_intensity(self, memory: dict[str, Any], day: int) -> int:
        if day != self._intensity_cache_day:
            self._intensity_cache_day = day
            self._intensity_cache.clear()
        event_id = memory["event_id"]
        cached = self._intensity_cache.get(event_id)
        if cached is not None:
            return cached
        age = max(0, day - memory["day"])
        factor = self._permille_power(1000 - memory["decay_permille_per_day"], age)
        intensity = memory["base_intensity_permille"] * factor // 1000
        self._intensity_cache[event_id] = intensity
        return intensity

    def recompute(self, world, holder_id: str) -> dict[str, dict[str, int]]:
        holder = self._character(world, holder_id)
        today = self._day_at(world, world.clock.tick)
        totals: dict[str, dict[str, int]] = {}
        latest_interaction: dict[str, int] = {}
        for memory in holder["memory"]:
            target_id = memory["subject_ids"][0]
            latest_interaction[target_id] = max(
                latest_interaction.get(target_id, 0), memory["tick"])
            strength = (self.current_intensity(memory, today)
                        * abs(memory["valence_permille"]) // 1000)
            if not strength:
                continue
            row = totals.setdefault(target_id, {dimension: 0 for dimension in _DIMENSIONS})
            effects = self.rules_data["memory_kinds"][memory["kind"]]["effects"]
            for dimension, effect in effects.items():
                row[dimension] += effect * strength // 1000
        for inherited in holder["heritage"].get("inherited_grudges", []):
            row = totals.setdefault(inherited["target"], {dimension: 0 for dimension in _DIMENSIONS})
            row["grievance"] += inherited["intensity"]
        opinions = {}
        for target_id, raw in sorted(totals.items()):
            opinions[target_id] = {
                **{dimension: max(-100, min(100, value)) for dimension, value in raw.items()},
                "last_interaction_tick": latest_interaction.get(target_id, 0),
            }
        holder["opinions"] = opinions
        return opinions

    def _recompute_target(self, holder: dict[str, Any], target_id: str,
                          today: int) -> dict[str, int] | None:
        """Rebuild one target's opinion after a same-day memory insertion/prune."""
        raw = None
        latest_interaction = 0
        for memory in holder["memory"]:
            if target_id not in memory["subject_ids"]:
                continue
            latest_interaction = max(latest_interaction, memory["tick"])
            strength = (self.current_intensity(memory, today)
                        * abs(memory["valence_permille"]) // 1000)
            if not strength:
                continue
            if raw is None:
                raw = {dimension: 0 for dimension in _DIMENSIONS}
            effects = self.rules_data["memory_kinds"][memory["kind"]]["effects"]
            for dimension, effect in effects.items():
                raw[dimension] += effect * strength // 1000
        for inherited in holder["heritage"].get("inherited_grudges", []):
            if inherited["target"] != target_id:
                continue
            if raw is None:
                raw = {dimension: 0 for dimension in _DIMENSIONS}
            raw["grievance"] += inherited["intensity"]
        if raw is None:
            return None
        return {
            **{dimension: max(-100, min(100, value)) for dimension, value in raw.items()},
            "last_interaction_tick": latest_interaction,
        }

    def update_day(self, day: int, world) -> None:
        if type(day) is not int or day != self.last_updated_day + 1:
            raise ValueError("Memory days must be consecutive")
        for entity_id, character in sorted({**world.archive, **world.entities}.items()):
            if character.get("kind") == "character":
                self.recompute(world, entity_id)
        self.last_updated_day = day

    def validate_world(self, world) -> None:
        ticks_per_day = self._ticks_per_day_for(world)
        if self.last_updated_day != world.clock.tick // ticks_per_day:
            raise ValueError("Memory system day does not match world clock")
        for entity_id, character in {**world.archive, **world.entities}.items():
            if character.get("kind") != "character":
                continue
            if not isinstance(character.get("memory"), list) or not isinstance(
                    character.get("opinions"), dict):
                raise ValueError(f"Invalid memory/opinion store: {entity_id}")
            cap = self.rules_data["rules"]["max_memories_per_character"]
            if (len(character["memory"]) > cap
                    and any(not isinstance(memory, dict)
                            or not memory.get("protected", False)
                            for memory in character["memory"])):
                raise ValueError(f"Unprotected memories exceed the configured cap: {entity_id}")
            for memory in character["memory"]:
                expected = {"event_id", "source_event_id", "subject_ids", "kind",
                            "valence_permille", "base_intensity_permille",
                            "decay_permille_per_day", "tick", "day", "protected"}
                if (not isinstance(memory, dict) or set(memory) != expected
                        or not isinstance(memory["subject_ids"], list)
                        or len(memory["subject_ids"]) != 1
                        or not isinstance(memory["subject_ids"][0], str)
                        or not isinstance(memory["event_id"], str)
                        or not isinstance(memory["source_event_id"], str)
                        or memory["kind"] not in self.rules_data["memory_kinds"]
                        or type(memory["valence_permille"]) is not int
                        or not -1000 <= memory["valence_permille"] <= 1000
                        or type(memory["base_intensity_permille"]) is not int
                        or not 0 <= memory["base_intensity_permille"] <= 1000
                        or type(memory["decay_permille_per_day"]) is not int
                        or not 0 <= memory["decay_permille_per_day"] <= 1000
                        or type(memory["tick"]) is not int or memory["tick"] > world.clock.tick
                        or type(memory["day"]) is not int
                        or memory["day"] < 0
                        or memory["day"] != memory["tick"] // ticks_per_day
                        or memory["day"] > self.last_updated_day
                        or memory["protected"] != (
                            self.rules_data["memory_kinds"][memory["kind"]]["protected"]
                            or memory["base_intensity_permille"] >= self.rules_data[
                                "rules"]["core_intensity_permille"])
                        or type(memory["protected"]) is not bool):
                    raise ValueError(f"Invalid memory record: {entity_id}")
                for field in ("event_id", "source_event_id"):
                    world.event_log.get(memory[field])
                created = world.event_log.get(memory["event_id"])
                if (created.type != "MEMORY_CREATED"
                        or memory["source_event_id"] not in created.caused_by
                        or created.data.get("kind") != memory["kind"]
                        or created.data.get("intensity_permille")
                        != memory["base_intensity_permille"]):
                    raise ValueError(f"Memory disagrees with its causal event: {entity_id}")
                if memory["subject_ids"][0] not in world.entities \
                        and memory["subject_ids"][0] not in world.archive:
                    raise ValueError("Memory references unknown subject")
            if self.recompute(world, entity_id) != character["opinions"]:
                raise ValueError(f"Stored opinions disagree with memory derivation: {entity_id}")

    def snapshot(self) -> dict[str, Any]:
        return {"digest": self.digest, "last_updated_day": self.last_updated_day}

    def _prune(self, holder: dict[str, Any], day: int) -> set[str]:
        cap = self.rules_data["rules"]["max_memories_per_character"]
        removed_targets = set()
        while len(holder["memory"]) > cap:
            candidates = [(self.current_intensity(memory, day), index)
                          for index, memory in enumerate(holder["memory"])
                          if not memory["protected"]]
            if not candidates:
                break
            _, index = min(candidates)
            removed_targets.add(holder["memory"][index]["subject_ids"][0])
            del holder["memory"][index]
        return removed_targets

    def _trait_adjusted_decay(self, holder: dict[str, Any], kind: str, rate: int) -> int:
        rules = self.rules_data["rules"]
        traits = holder["traits"]
        modifier = (traits["sociability"] - 50) * rules["sociability_decay_modifier_permille"]
        if kind in {"betrayed", "robbed", "cheated", "insulted", "humiliated",
                    "witnessed_death_of", "defeated_by"}:
            modifier -= max(0, traits["pride"] - 50) * rules[
                "grievance_pride_decay_modifier_permille"]
            modifier -= max(0, traits["paranoia"] - 50) * rules[
                "grievance_paranoia_decay_modifier_permille"]
        modifier = max(-rules["max_trait_decay_modifier_permille"],
                       min(rules["max_trait_decay_modifier_permille"], modifier))
        return max(0, min(1000, rate * (1000 + modifier) // 1000))

    @staticmethod
    @lru_cache(maxsize=65536)
    def _permille_power(base: int, exponent: int) -> int:
        result = 1000
        while exponent:
            if exponent & 1:
                result = result * base // 1000
            base = base * base // 1000
            exponent >>= 1
        return result

    @staticmethod
    def _character(world, character_id: str) -> dict[str, Any]:
        person = world.entities.get(character_id) or world.archive.get(character_id)
        if not person or person.get("kind") != "character":
            raise ValueError(f"Unknown character: {character_id}")
        return person

    @staticmethod
    def _ticks_per_day_for(world) -> int:
        return world.config["time"]["hours_per_day"] // world.config["time"]["hours_per_tick"]

    def _day_at(self, world, tick: int) -> int:
        return tick // self._ticks_per_day_for(world)
