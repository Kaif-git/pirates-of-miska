"""Coarse regional accounting and coherent named-background character LOD."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


LOD_PATH = Path(__file__).resolve().parents[1] / "data" / "lod.json"
_INCIDENT_TYPES = {
    "ENCOUNTER_RESOLVED", "BATTLE", "STORM", "HURRICANE", "PLAGUE",
    "MONSTER_SIGHTING", "SHIP_SUNK", "CHARACTER_DIED", "FACTION_DISSOLVED",
}


def load_lod_data(path: str | Path = LOD_PATH) -> tuple[dict[str, Any], str]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load LOD data {path}: {exc}") from exc
    validate_lod_data(data)
    digest = hashlib.sha256(json.dumps(
        data, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    return data, digest


def validate_lod_data(data: dict[str, Any]) -> None:
    if not isinstance(data, dict) or set(data) != {"schema_version", "rules"} \
            or data["schema_version"] != 1:
        raise ValueError("Invalid LOD catalog schema")
    rules = data["rules"]
    required = {"coarse_step_days", "regional_history_weeks", "named_background_per_year",
                "background_role_cycle", "named_history_reference_limit",
                "promotion_magnitude_permille",
                "max_promotions_per_day", "demotion_after_days"}
    if not isinstance(rules, dict) or set(rules) != required:
        raise ValueError("Invalid LOD rules")
    integer_rules = required - {"background_role_cycle"}
    if any(type(rules[key]) is not int or rules[key] < 0 for key in integer_rules):
        raise ValueError("LOD rules must be nonnegative integers")
    if (rules["coarse_step_days"] < 1 or rules["regional_history_weeks"] < 1
            or rules["named_background_per_year"] < 0
            or rules["named_history_reference_limit"] < 1
            or rules["promotion_magnitude_permille"] > 1000
            or rules["max_promotions_per_day"] < 1 or rules["demotion_after_days"] < 1
            or not isinstance(rules["background_role_cycle"], list)
            or not rules["background_role_cycle"]
            or any(not isinstance(role, str) or not role for role in rules["background_role_cycle"])):
        raise ValueError("LOD rule ranges are invalid")


class LODSystem:
    """Tracks aggregate distant-region outcomes and compact named residents.

    Island economy populations remain the census authority. Naming a resident
    moves one person from its island's aggregate count into this registry;
    promotion/demotion only changes representation, never the person.
    """

    def __init__(self, *, digest: str, last_updated_day: int = 0,
                 event_cursor: int = 0, cohorts: dict[str, dict[str, Any]] | None = None,
                 named_records: dict[str, dict[str, Any]] | None = None,
                 regional_pending: dict[str, dict[str, Any]] | None = None,
                 regional_history: dict[str, list[dict[str, Any]]] | None = None,
                 last_named_year: int = 0,
                 census_population_initial: int | None = None) -> None:
        self.data, actual_digest = load_lod_data()
        if digest != actual_digest:
            raise ValueError("LOD catalog changed since snapshot creation")
        if (type(last_updated_day) is not int or last_updated_day < 0
                or type(event_cursor) is not int or event_cursor < 0
                or type(last_named_year) is not int or last_named_year < 0
                or census_population_initial is not None
                   and (type(census_population_initial) is not int
                        or census_population_initial <= 0)):
            raise ValueError("Invalid LOD clock or cursor")
        self.digest = digest
        self.last_updated_day = last_updated_day
        self.event_cursor = event_cursor
        self.cohorts = _copy(cohorts or {})
        self.named_records = _copy(named_records or {})
        self.regional_pending = _copy(regional_pending or {})
        self.regional_history = _copy(regional_history or {})
        self.last_named_year = last_named_year
        self.census_population_initial = census_population_initial

    @classmethod
    def create(cls) -> "LODSystem":
        _, digest = load_lod_data()
        return cls(digest=digest)

    @classmethod
    def restore(cls, snapshot: dict[str, Any]) -> "LODSystem":
        required = {"digest", "last_updated_day", "event_cursor", "cohorts",
                    "named_records", "regional_pending", "regional_history", "last_named_year",
                    "census_population_initial"}
        if not isinstance(snapshot, dict) or set(snapshot) != required:
            raise ValueError("Invalid LOD snapshot fields")
        if any(not isinstance(snapshot[key], dict) for key in
               ("cohorts", "named_records", "regional_pending", "regional_history")):
            raise ValueError("Invalid LOD snapshot tables")
        return cls(**snapshot)

    def seed_world(self, world) -> None:
        if self.cohorts or self.regional_history or self.last_updated_day:
            raise ValueError("LOD state can only be seeded once")
        for island_id, state in sorted(world.economy.islands.items()):
            self.cohorts[island_id] = {"population": state["population"],
                                       "population_classes": dict(state["population_classes"])}
        self.census_population_initial = sum(
            state["population"] for state in world.economy.islands.values())
        for region_id in sorted(world.geography.regions):
            self.regional_pending[region_id] = _empty_aggregate()
            self.regional_history[region_id] = []
        self.event_cursor = len(world.event_log)

    def update_day(self, day: int, world) -> None:
        if type(day) is not int or day != self.last_updated_day + 1:
            raise ValueError("LOD days must be updated consecutively")
        rules = self.data["rules"]
        events = world.event_log.since(self.event_cursor)
        daily = {region_id: _empty_aggregate() for region_id in sorted(world.geography.regions)}
        causes: dict[str, list[str]] = {region_id: [] for region_id in daily}
        for event in events:
            region_id = self._event_region(world, event)
            if region_id is None:
                continue
            row = daily[region_id]
            if event.type == "MARKET_TRADE":
                row["trade_quantity_milli"] += event.data["quantity_milli"]
            if event.type in _INCIDENT_TYPES:
                row["incident_count"] += 1
            if event.type not in {"LOD_REGION_AGGREGATED"} and event.id not in causes[region_id]:
                causes[region_id].append(event.id)
        for region_id in sorted(daily):
            coverage = 0
            for assignment in world.politics.response_assignments.values():
                island_id = assignment["island_id"]
                if world.geography.islands[island_id].region_id == region_id:
                    coverage += {"patrol": 250, "hunter": 600, "fleet": 1000}[assignment["level"]]
            daily[region_id]["patrol_coverage_permille_days"] = min(1000, coverage)
            pending = self.regional_pending[region_id]
            _merge_aggregate(pending, daily[region_id])
            pending["days"] += 1
            pending["cause_event_ids"] = list(dict.fromkeys(
                (*pending["cause_event_ids"], *causes[region_id])))[-16:]
        if day % rules["coarse_step_days"] == 0:
            self._close_coarse_step(day, world)
        self._promote_from_events(events, world)
        if day % world.config["time"]["days_per_year"] == 0:
            self._age_background_records(world)
            self._name_background_residents(day, world)
        self._demote_inactive(day, world)
        self._sync_cohorts(world)
        self.event_cursor = len(world.event_log)
        self.last_updated_day = day

    def _close_coarse_step(self, day: int, world) -> None:
        keep = self.data["rules"]["regional_history_weeks"]
        for region_id in sorted(self.regional_pending):
            pending = self.regional_pending[region_id]
            if not pending["days"]:
                continue
            summary = {
                "start_day": day - pending["days"] + 1,
                "end_day": day,
                "days": pending["days"],
                "trade_quantity_milli": pending["trade_quantity_milli"],
                "patrol_coverage_permille_days": pending["patrol_coverage_permille_days"],
                "patrol_coverage_permille": (
                    pending["patrol_coverage_permille_days"] // pending["days"]),
                "incident_count": pending["incident_count"],
            }
            history = self.regional_history[region_id]
            history.append(summary)
            del history[:-keep]
            parents = list(pending["cause_event_ids"])
            if not parents:
                island_id = next(island_id for island_id in sorted(world.geography.islands)
                                 if world.geography.islands[island_id].region_id == region_id)
                parents = [world.economy.islands[island_id]["last_economy_event_id"]]
            world.event_log.append(
                tick=world.clock.tick, event_type="LOD_REGION_AGGREGATED", location=region_id,
                data=summary, caused_by=parents,
                causes_tags=["lod_coarse_step", f"region:{region_id}"], magnitude=0.05,
            )
            self.regional_pending[region_id] = _empty_aggregate()

    def _name_background_residents(self, day: int, world) -> None:
        year = day // world.config["time"]["days_per_year"]
        if year <= self.last_named_year:
            return
        count = self.data["rules"]["named_background_per_year"]
        islands = [island_id for island_id, state in sorted(world.economy.islands.items())
                   if state["population"] > 1]
        if not islands and count:
            raise ValueError("No island population available for named-background sampling")
        for index in range(count):
            island_id = islands[((year - 1) * count + index) % len(islands)]
            role_cycle = self.data["rules"]["background_role_cycle"]
            role = role_cycle[((year - 1) * count + index) % len(role_cycle)]
            cause = world.event_log.get(world.economy.islands[island_id]["last_economy_event_id"])
            character_id = world.characters.create_character(
                world, tier="BACKGROUND", cause_event_id=cause.id,
                role=role, island_id=island_id,
            )
            character = world.entities.pop(character_id)
            generated_event_id = character["last_event_id"]
            state = world.economy.islands[island_id]
            population_class = self._take_resident(state)
            transfer = world.event_log.append(
                tick=world.clock.tick, event_type="LOD_BACKGROUND_RECORDED", location=island_id,
                actors=[{"role": "named_resident", "entity_id": character_id}],
                data={"population_before": state["population"] + 1,
                      "population_after": state["population"],
                      "population_class": population_class,
                      "recorded_tier": "BACKGROUND"},
                caused_by=[generated_event_id],
                causes_tags=["lod_background_sample", "resident_to_named"], magnitude=0.1,
            )
            character["last_event_id"] = transfer.id
            self.named_records[character_id] = self._compact_character(
                character, status="background", history=[generated_event_id, transfer.id],
                history_limit=self.data["rules"]["named_history_reference_limit"])
        self.last_named_year = year

    def _age_background_records(self, world) -> None:
        year_ticks = world.characters._ticks_per_year(world)
        rules = world.characters.data["rules"]
        for character_id, record in sorted(tuple(self.named_records.items())):
            age = max(0, (world.clock.tick - record["birth_tick"]) // year_ticks)
            old_age = record["age_years"]
            if age <= old_age:
                continue
            if age > rules["age_stat_decay_start"]:
                decay = min(100, (age - max(old_age, rules["age_stat_decay_start"]))
                            * rules["age_stat_decay_per_year"])
                for stat in rules["age_decay_stats"]:
                    record["stats"][stat] = max(0, record["stats"][stat] - decay)
            event = world.event_log.append(
                tick=world.clock.tick, event_type="CHARACTER_AGED",
                location=record["location"]["id"],
                actors=[{"role": "character", "entity_id": character_id}],
                data={"age_before": old_age, "age_after": age, "stat_changes": {},
                      "lod_coarse": True},
                caused_by=[record["last_event_id"]],
                causes_tags=["birthday", "aging", "lod_background"], magnitude=0.08,
            )
            record["age_years"] = age
            record["last_event_id"] = event.id
            record["history_event_ids"].append(event.id)
            record["history_event_ids"] = record["history_event_ids"][-
                self.data["rules"]["named_history_reference_limit"]:]
            if age < rules["death_start_age"]:
                continue
            risk = min(rules["death_max_basis_points"],
                       rules["death_base_basis_points"]
                       + (age - rules["death_start_age"]) * rules["death_per_year_basis_points"])
            if world.rng.stream(f"character:{character_id}").randbelow(10000) >= risk:
                continue
            deceased = self._materialize(record)
            deceased["age_years"] = age
            deceased["last_event_id"] = event.id
            world.entities[character_id] = deceased
            del self.named_records[character_id]
            death_id = world.characters.kill_character(
                world, character_id, cause="old_age", caused_by=event.id,
                tags=["natural_death", "old_age", "lod_background"], replace_majors=False,
            )
            world.archive[character_id]["lod_history_event_ids"] = [
                *record["history_event_ids"], death_id
            ][-self.data["rules"]["named_history_reference_limit"]:]

    @staticmethod
    def _take_resident(state: dict[str, Any]) -> str:
        classes = state["population_classes"]
        if state["population"] <= 1:
            raise ValueError("Cannot sample the final resident from an island")
        population_class = "laborers" if classes.get("laborers", 0) else min(
            (name for name, count in classes.items() if count),
            key=lambda name: (-classes[name], name),
        )
        classes[population_class] -= 1
        state["population"] -= 1
        return population_class

    def _promote_from_events(self, events, world) -> None:
        promoted = 0
        threshold = self.data["rules"]["promotion_magnitude_permille"]
        for event in events:
            if promoted >= self.data["rules"]["max_promotions_per_day"]:
                return
            if (event.magnitude * 1000 < threshold or event.location not in world.geography.islands):
                continue
            candidates = [record for record in self.named_records.values()
                          if record["status"] == "background"
                          and record["location"]["id"] == event.location]
            if not candidates:
                continue
            record = min(candidates, key=lambda item: item["id"])
            self._promote(record["id"], event.id, world)
            promoted += 1

    def _promote(self, character_id: str, cause_event_id: str, world) -> str:
        record = self.named_records.pop(character_id)
        event = world.event_log.append(
            tick=world.clock.tick, event_type="LOD_PROMOTED", location=record["location"]["id"],
            actors=[{"role": "promoted_character", "entity_id": character_id}],
            data={"name": record["display_name"], "from_tier": "BACKGROUND",
                  "to_tier": "MINOR", "retained_history": len(record["history_event_ids"])},
            caused_by=[cause_event_id], causes_tags=["lod_promotion", "notable_event"], magnitude=0.4,
        )
        character = self._materialize(record)
        character["tier"] = "MINOR"
        character["last_event_id"] = event.id
        character["lod_history_event_ids"] = [
            *record["history_event_ids"], event.id
        ][-self.data["rules"]["named_history_reference_limit"]:]
        character["lod_promoted_day"] = self.last_updated_day + 1
        character["lod_promotion_event_id"] = event.id
        world.entities[character_id] = character
        if record.get("decision_state") is not None:
            world.decisions.agents[character_id] = record["decision_state"]
        return event.id

    def demote_character(self, world, character_id: str, *, cause_event_id: str) -> str:
        character = world.entities.get(character_id)
        if (not character or character.get("kind") != "character"
                or character.get("tier") != "MINOR" or character.get("crew_id") is not None
                or character.get("ship_id") is not None):
            raise ValueError("Only an uncrewed promoted MINOR can be demoted")
        if character.get("memory") or character.get("opinions"):
            raise ValueError("Cannot demote a character with active memory state")
        if any(character_id == faction.get("leader_id")
               or character_id in faction.get("council", [])
               for faction in world.politics.factions.values()):
            raise ValueError("Cannot demote a political faction member")
        event = world.event_log.append(
            tick=world.clock.tick, event_type="LOD_DEMOTED", location=world.location_of(character_id)["id"],
            actors=[{"role": "demoted_character", "entity_id": character_id}],
            data={"name": character["display_name"], "from_tier": "MINOR",
                  "to_tier": "BACKGROUND"},
            caused_by=[cause_event_id], causes_tags=["lod_demotion", "retained_identity"], magnitude=0.2,
        )
        history = [*character.get("lod_history_event_ids", []), event.id]
        record = self._compact_character(
            character, status="background", history=history,
            history_limit=self.data["rules"]["named_history_reference_limit"])
        record["last_event_id"] = event.id
        record["decision_state"] = world.decisions.agents.pop(character_id, None)
        self.named_records[character_id] = record
        del world.entities[character_id]
        return event.id

    def _demote_inactive(self, day: int, world) -> None:
        age_days = self.data["rules"]["demotion_after_days"]
        for character_id, character in sorted(tuple(world.entities.items())):
            if (character.get("kind") != "character" or character.get("tier") != "MINOR"
                    or character.get("lod_promotion_event_id") is None
                    or day - character.get("lod_promoted_day", day) < age_days
                    or character.get("last_event_id") != character["lod_promotion_event_id"]):
                continue
            if (character.get("crew_id") is not None or character.get("ship_id") is not None
                    or character.get("memory") or character.get("opinions")
                    or any(character_id == faction.get("leader_id")
                           or character_id in faction.get("council", [])
                           for faction in world.politics.factions.values())):
                continue
            self.demote_character(
                world, character_id, cause_event_id=character["lod_promotion_event_id"])

    def _sync_cohorts(self, world) -> None:
        for island_id, state in world.economy.islands.items():
            self.cohorts[island_id] = {"population": state["population"],
                                       "population_classes": dict(state["population_classes"])}

    @staticmethod
    def _compact_character(character: dict[str, Any], *, status: str,
                           history: list[str], history_limit: int = 128) -> dict[str, Any]:
        return {
            "id": character["id"], "kind": "character",
            "display_name": character["display_name"],
            "epithet": character.get("epithet"), "birth_tick": character["birth_tick"],
            "birthplace": character["birthplace"], "location": dict(character["location"]),
            "lod_resident_origin": True,
            "stats": dict(character["stats"]), "traits": dict(character["traits"]),
            "values": list(character["values"]), "heritage": _copy(character["heritage"]),
            "role": character["role"], "age_years": character["age_years"],
            "archetype_id": character["archetype_id"], "tier": "BACKGROUND",
            "last_event_id": character["last_event_id"],
            "history_event_ids": list(dict.fromkeys(history))[-history_limit:], "status": status,
            "decision_state": None,
        }

    @staticmethod
    def _materialize(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": record["id"], "kind": "character",
            "display_name": record["display_name"], "epithet": record["epithet"],
            "gender": None, "birth_tick": record["birth_tick"],
            "birthplace": record["birthplace"], "death_tick": None,
            "lod_resident_origin": True,
            "cause_of_death_event": None, "heritage": _copy(record["heritage"]),
            "home_faction": None, "location": dict(record["location"]),
            "stats": dict(record["stats"]), "traits": dict(record["traits"]),
            "values": list(record["values"]),
            "dream": {"kind": "protect_entity", "target_ref": None,
                      "progress": 0, "stated_tick": max(0, record["birth_tick"])},
            "wants": [], "health": {"hp": 100, "max_hp": 100,
                                     "max_hp_from_age": 100, "injuries": [], "chronic": []},
            "bounty": {"amount": 0, "issuer_faction": None, "tick_set": None},
            "reputation": {}, "wealth": {"silver": 0, "assets": [], "debts": []},
            "memory": [], "opinions": {}, "role": record["role"],
            "crew_id": None, "ship_id": None, "tier": "MINOR", "flags": [],
            "age_years": record["age_years"], "archetype_id": record["archetype_id"],
            "last_event_id": record["last_event_id"],
        }

    def _event_region(self, world, event) -> str | None:
        location = event.location
        if location in world.geography.islands:
            return world.geography.islands[location].region_id
        route = world.geography.routes.get(location)
        if route:
            return world.geography.islands[route.origin].region_id
        return None

    @staticmethod
    def aggregate_daily(samples: list[dict[str, int]]) -> dict[str, int]:
        result = _empty_aggregate()
        for sample in samples:
            _merge_aggregate(result, sample)
            result["days"] += 1
        return result

    @staticmethod
    def merge_aggregates(aggregates: list[dict[str, Any]]) -> dict[str, Any]:
        result = _empty_aggregate()
        for aggregate in aggregates:
            _merge_aggregate(result, aggregate)
            result["days"] += aggregate["days"]
        return result

    def snapshot(self) -> dict[str, Any]:
        return {"digest": self.digest, "last_updated_day": self.last_updated_day,
                "event_cursor": self.event_cursor, "cohorts": self.cohorts,
                "named_records": self.named_records, "regional_pending": self.regional_pending,
                "regional_history": self.regional_history,
                "last_named_year": self.last_named_year,
                "census_population_initial": self.census_population_initial}

    def validate_world(self, world) -> None:
        validate_lod_data(self.data)
        if (self.last_updated_day != world.clock.tick //
                (world.config["time"]["hours_per_day"] // world.config["time"]["hours_per_tick"])):
            raise ValueError("LOD update day does not match world clock")
        if self.event_cursor > len(world.event_log):
            raise ValueError("LOD event cursor exceeds the event log")
        if (set(self.cohorts) != set(world.geography.islands)
                or set(self.regional_pending) != set(world.geography.regions)
                or set(self.regional_history) != set(world.geography.regions)):
            raise ValueError("LOD geography tables do not cover the world")
        sampled_ids = set(self.named_records)
        sampled_ids.update(entity_id for entity_id, entity in world.entities.items()
                           if entity.get("lod_resident_origin") is True)
        sampled_ids.update(entity_id for entity_id, entity in world.archive.items()
                           if entity.get("lod_resident_origin") is True)
        if (self.census_population_initial is None
                or sum(row["population"] for row in world.economy.islands.values())
                   + len(sampled_ids) != self.census_population_initial):
            raise ValueError("LOD census residents do not reconcile with named resident records")
        for island_id, cohort in self.cohorts.items():
            state = world.economy.islands[island_id]
            if (not isinstance(cohort, dict) or set(cohort) != {"population", "population_classes"}
                    or cohort["population"] != state["population"]
                    or cohort["population_classes"] != state["population_classes"]):
                raise ValueError(f"LOD cohort disagrees with island census: {island_id}")
        for region_id, pending in self.regional_pending.items():
            if not _valid_aggregate(pending, pending=True):
                raise ValueError(f"Invalid pending coarse aggregate: {region_id}")
        for region_id, rows in self.regional_history.items():
            if (not isinstance(rows, list)
                    or len(rows) > self.data["rules"]["regional_history_weeks"]
                    or any(not _valid_summary(row) for row in rows)):
                raise ValueError(f"Invalid regional coarse history: {region_id}")
        for character_id, record in self.named_records.items():
            if (not isinstance(record, dict) or record.get("id") != character_id
                    or record.get("status") != "background"
                    or record.get("tier") != "BACKGROUND"
                    or character_id in world.entities or character_id in world.archive
                    or record.get("location", {}).get("kind") != "island"
                    or record["location"].get("id") not in world.geography.islands
                    or not record.get("display_name")
                    or not isinstance(record.get("history_event_ids"), list)
                    or not record["history_event_ids"]):
                raise ValueError(f"Invalid named-background record: {character_id}")
            world.characters._validate_character_record(
                character_id, self._materialize(record), living=True)
            for event_id in (*record["history_event_ids"], record["last_event_id"]):
                world.event_log.get(event_id)
        names = [record["display_name"].strip().casefold()
                 for record in self.named_records.values()]
        names.extend(character["display_name"].strip().casefold()
                     for character in world.entities.values()
                     if character.get("kind") == "character")
        names.extend(character["display_name"].strip().casefold()
                     for character in world.archive.values()
                     if character.get("kind") == "character")
        names.extend(merchant["name"].strip().casefold()
                     for merchant in world.economy.merchants.values())
        if len(names) != len(set(names)):
            raise ValueError("Named-background display names must be unique")


def _copy(value):
    return json.loads(json.dumps(value, sort_keys=True))


def _empty_aggregate() -> dict[str, Any]:
    return {"days": 0, "trade_quantity_milli": 0,
            "patrol_coverage_permille_days": 0, "incident_count": 0,
            "cause_event_ids": []}


def _merge_aggregate(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key in ("trade_quantity_milli", "patrol_coverage_permille_days", "incident_count"):
        target[key] += source[key]


def _valid_aggregate(row: Any, *, pending: bool) -> bool:
    expected = {"days", "trade_quantity_milli", "patrol_coverage_permille_days",
                "incident_count", "cause_event_ids"}
    return (isinstance(row, dict) and set(row) == expected
            and all(type(row[key]) is int and row[key] >= 0 for key in expected - {"cause_event_ids"})
            and isinstance(row["cause_event_ids"], list)
            and len(row["cause_event_ids"]) <= 16
            and all(isinstance(event_id, str) and event_id for event_id in row["cause_event_ids"]))


def _valid_summary(row: Any) -> bool:
    required = {"start_day", "end_day", "days", "trade_quantity_milli",
                "patrol_coverage_permille", "incident_count"}
    exact = required | {"patrol_coverage_permille_days"}
    return (isinstance(row, dict) and set(row) in (required, exact)
            and all(type(row[key]) is int and row[key] >= 0 for key in row)
            and row["days"] == row["end_day"] - row["start_day"] + 1
            and row["patrol_coverage_permille"] <= 1000
            and ("patrol_coverage_permille_days" not in row
                 or row["patrol_coverage_permille_days"] <= 1000 * row["days"])
            and ("patrol_coverage_permille_days" not in row
                 or row["patrol_coverage_permille"] ==
                 row["patrol_coverage_permille_days"] // row["days"]))
