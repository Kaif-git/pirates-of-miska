"""Persistent named characters, deterministic generation, and lifecycle."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sim.core.names import living_name_in_use


CHARACTERS_PATH = Path(__file__).resolve().parents[1] / "data" / "characters.json"
_STAT_NAMES = {
    "combat", "seamanship", "navigation", "leadership", "cunning",
    "willpower", "gunnery", "medicine", "trade", "strength", "speed",
    "endurance", "toughness",
}
_LEGACY_CHARACTER_DATA_DIGESTS = {
    "ce9c335acff824d65a60e67d51e5a442dc6897ba1e6a443fb355fd137959e198",
    "652d29bb45fd1fe4f5c1d4c346857304dfcf3a0274e48a671d3f741de32b424f",
}
_TRAIT_NAMES = {
    "ambition", "honor", "greed", "loyalty", "cruelty", "curiosity",
    "pride", "paranoia", "boldness", "sociability",
}
_VALUES = {"freedom", "order", "wealth", "glory", "kin", "knowledge", "vengeance"}


def load_character_data(path: str | Path = CHARACTERS_PATH) -> tuple[dict[str, Any], str]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load character data {path}: {exc}") from exc
    validate_character_data(data)
    digest = hashlib.sha256(json.dumps(
        data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    return data, digest


def validate_character_data(data: Any) -> None:
    required = {
        "schema_version", "name_pools", "stat_names", "trait_names", "default_traits",
        "rules", "role_profiles", "archetypes", "dream_templates", "want_kinds",
        "initial_major_roles", "initial_minor_roles", "rookie_roles", "rookie_age_years",
        "injuries", "combat_equipment",
    }
    if (not isinstance(data, dict) or set(data) != required
            or type(data["schema_version"]) is not int or data["schema_version"] != 1):
        raise ValueError("Invalid character catalog schema")
    names = data["name_pools"]
    if (not isinstance(names, dict) or set(names) != {"given", "family"}
            or any(not isinstance(names[key], list) or len(names[key]) < 10
                   or any(not isinstance(name, str) or not name.strip()
                          or any(character.isdigit() for character in name)
                          for name in names[key])
                   or len(set(names[key])) != len(names[key]) for key in names)):
        raise ValueError("Character name pools must contain unique nonempty names")
    if (set(data["stat_names"]) != _STAT_NAMES
            or set(data["trait_names"]) != _TRAIT_NAMES
            or set(data["default_traits"]) != _TRAIT_NAMES
            or any(type(value) is not int or not 0 <= value <= 100
                   for value in data["default_traits"].values())):
        raise ValueError("Character stat/trait catalog does not match the schema")
    rules = data["rules"]
    required_rules = {
        "initial_major_count", "initial_minor_count", "initial_major_age_min",
        "initial_major_age_max", "initial_minor_age_min", "initial_minor_age_max",
        "rookie_replacement_floor", "rookie_target_major_count", "major_population_cap",
        "age_stat_decay_start", "age_stat_decay_per_year", "death_start_age",
        "death_base_basis_points", "death_per_year_basis_points", "death_max_basis_points",
        "generation_stat_spread", "generation_trait_spread", "age_decay_stats",
    }
    if not isinstance(rules, dict) or set(rules) != required_rules:
        raise ValueError("Character lifecycle rules have invalid fields")
    if any(type(value) is not int or value < 0 for key, value in rules.items()
           if key != "age_decay_stats"):
        raise ValueError("Character lifecycle numeric rules must be nonnegative integers")
    if not (8 <= rules["initial_major_count"] <= 15
            and 100 <= rules["initial_minor_count"] <= 300
            and rules["initial_major_age_min"] <= rules["initial_major_age_max"]
            and rules["initial_minor_age_min"] <= rules["initial_minor_age_max"]
            and 1 <= rules["rookie_replacement_floor"]
            < rules["rookie_target_major_count"] <= rules["major_population_cap"]
            and rules["death_max_basis_points"] <= 10000
            and rules["death_base_basis_points"] <= rules["death_max_basis_points"]
            and isinstance(rules["age_decay_stats"], list)
            and set(rules["age_decay_stats"]) <= _STAT_NAMES):
        raise ValueError("Character lifecycle tuning is outside supported bounds")
    roles = data["role_profiles"]
    if not isinstance(roles, dict) or not roles:
        raise ValueError("Character role profiles must be a nonempty object")
    dream_templates = data["dream_templates"]
    if not isinstance(dream_templates, dict) or not dream_templates:
        raise ValueError("Character dream templates must be a nonempty object")
    for role, profile in roles.items():
        if (not isinstance(role, str) or not role or not isinstance(profile, dict)
                or set(profile) != {"offsets", "values", "dream_kinds"}
                or not isinstance(profile["offsets"], dict)
                or set(profile["offsets"]) - _STAT_NAMES
                or any(type(value) is not int or not -100 <= value <= 100
                       for value in profile["offsets"].values())
                or not isinstance(profile["values"], list) or not profile["values"]
                or not set(profile["values"]) <= _VALUES
                or not isinstance(profile["dream_kinds"], list) or not profile["dream_kinds"]
                or not set(profile["dream_kinds"]) <= set(dream_templates)):
            raise ValueError(f"Invalid character role profile: {role}")
    archetypes = data["archetypes"]
    if (not isinstance(archetypes, list) or len(archetypes) < 10
            or any(not isinstance(row, dict) or set(row) != {"id", "trait_modifiers"}
                   or not isinstance(row["id"], str) or not row["id"]
                   or not isinstance(row["trait_modifiers"], dict)
                   or set(row["trait_modifiers"]) - _TRAIT_NAMES
                   or any(type(value) is not int or not -100 <= value <= 100
                          for value in row["trait_modifiers"].values())
                   for row in archetypes)
             or len({row["id"] for row in archetypes}) != len(archetypes)):
        raise ValueError("Character archetypes must be unique and use valid trait modifiers")
    if (not isinstance(data["want_kinds"], list) or not data["want_kinds"]
            or any(not isinstance(kind, str) or not kind for kind in data["want_kinds"])):
        raise ValueError("Character wants must be a nonempty list of string kinds")
    for key in ("initial_major_roles", "initial_minor_roles", "rookie_roles"):
        if (not isinstance(data[key], list) or not data[key]
                or any(role not in roles for role in data[key])):
            raise ValueError(f"Invalid character role assignment list: {key}")
    if (type(data["rookie_age_years"]) is not int or data["rookie_age_years"] < 0
            or not isinstance(data["injuries"], dict) or not data["injuries"]):
        raise ValueError("Invalid character rookie or injury data")
    equipment = data["combat_equipment"]
    if (not isinstance(equipment, dict)
            or set(equipment) != {"weapons", "armor", "role_loadouts", "rules"}
            or not isinstance(equipment["weapons"], dict) or not equipment["weapons"]
            or not isinstance(equipment["armor"], dict) or not equipment["armor"]
            or not isinstance(equipment["role_loadouts"], dict)
            or set(equipment["role_loadouts"]) != set(roles)):
        raise ValueError("Invalid character combat-equipment catalog")
    for weapon_id, weapon in equipment["weapons"].items():
        if (not isinstance(weapon_id, str) or not weapon_id or not isinstance(weapon, dict)
                or set(weapon) != {"force", "accuracy", "reach", "wear_permille"}
                or type(weapon["force"]) is not int or not 0 <= weapon["force"] <= 30
                or type(weapon["accuracy"]) is not int or not -20 <= weapon["accuracy"] <= 20
                or type(weapon["reach"]) is not int or not 1 <= weapon["reach"] <= 3
                or type(weapon["wear_permille"]) is not int
                or not 0 <= weapon["wear_permille"] <= 1000):
            raise ValueError(f"Invalid personal weapon definition: {weapon_id}")
    for armor_id, armor in equipment["armor"].items():
        if (not isinstance(armor_id, str) or not armor_id or not isinstance(armor, dict)
                or set(armor) != {"protection", "handling", "wear_permille"}
                or type(armor["protection"]) is not int or not 0 <= armor["protection"] <= 30
                or type(armor["handling"]) is not int or not -20 <= armor["handling"] <= 20
                or type(armor["wear_permille"]) is not int
                or not 0 <= armor["wear_permille"] <= 1000):
            raise ValueError(f"Invalid personal armor definition: {armor_id}")
    for role, loadout in equipment["role_loadouts"].items():
        if (not isinstance(loadout, dict) or set(loadout) != {"weapon_id", "armor_id"}
                or loadout["weapon_id"] not in equipment["weapons"]
                or loadout["armor_id"] not in equipment["armor"]):
            raise ValueError(f"Invalid personal role loadout: {role}")
    rules = equipment["rules"]
    required_combat_rules = {
        "max_exchanges", "hit_roll_min", "hit_roll_max", "attack_skill_weight",
        "attack_speed_weight", "defense_skill_weight", "defense_speed_weight",
        "defense_willpower_weight", "morale_composure_per_10", "guard_bonus", "strength_force_weight",
        "toughness_protection_per_10", "armor_protection_weight",
        "attack_stamina_cost", "guard_stamina_cost", "evade_stamina_cost",
        "endurance_cost_reduction_per_10", "exhaustion_penalty",
        "injury_impact_threshold", "injury_basis_points_per_impact",
        "max_injury_basis_points", "boarding_morale_shift",
    }
    if (not isinstance(rules, dict) or set(rules) != required_combat_rules
            or any(type(value) is not int for value in rules.values())
            or not 1 <= rules["max_exchanges"] <= 8
            or rules["hit_roll_min"] > rules["hit_roll_max"]
            or max(abs(rules["hit_roll_min"]), abs(rules["hit_roll_max"])) > 100
            or any(not 0 <= rules[key] <= 100 for key in required_combat_rules - {
                "max_exchanges", "hit_roll_min", "hit_roll_max", "attack_stamina_cost",
                "guard_stamina_cost", "evade_stamina_cost", "max_injury_basis_points"})
            or any(not 1 <= rules[key] <= 100 for key in (
                "attack_stamina_cost", "guard_stamina_cost", "evade_stamina_cost"))
            or rules["injury_basis_points_per_impact"] > 10_000
            or not 0 <= rules["max_injury_basis_points"] <= 10_000
            or rules["boarding_morale_shift"] > 20):
        raise ValueError("Invalid personal-combat rules")
    for injury_id, injury in data["injuries"].items():
        if (not isinstance(injury_id, str) or not injury_id or not isinstance(injury, dict)
                or set(injury) != {"severity", "hp_loss", "recovery_days", "scar_on_recovery",
                                   "fatal_basis_points",
                                   "fatal_care_reduction_basis_points_per_point", "stat_effects"}
                or type(injury["severity"]) is not int or not 0 <= injury["severity"] <= 100
                or type(injury["hp_loss"]) is not int or not 0 <= injury["hp_loss"] < 100
                or type(injury["recovery_days"]) is not int or injury["recovery_days"] < 0
                or type(injury["scar_on_recovery"]) is not bool
                or type(injury["fatal_basis_points"]) is not int
                or not 0 <= injury["fatal_basis_points"] <= 10_000
                or type(injury["fatal_care_reduction_basis_points_per_point"]) is not int
                or injury["fatal_care_reduction_basis_points_per_point"] < 0
                or not isinstance(injury["stat_effects"], dict)
                or set(injury["stat_effects"]) - _STAT_NAMES
                or any(type(value) is not int or not -100 <= value <= 100
                       for value in injury["stat_effects"].values())):
            raise ValueError(f"Invalid character injury profile: {injury_id}")
    try:
        json.dumps(data, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Character data must be JSON-compatible") from exc


class CharacterSystem:
    """Owns catalog identity and daily lifecycle for character entity records."""

    def __init__(self, *, digest: str, last_updated_day: int = 0) -> None:
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("Invalid character data digest")
        if type(last_updated_day) is not int or last_updated_day < 0:
            raise ValueError("Invalid character lifecycle day")
        self.data, actual_digest = load_character_data()
        if digest != actual_digest:
            raise ValueError("Character data changed since snapshot creation")
        self.digest = digest
        self.last_updated_day = last_updated_day
        self._island_ids: set[str] = set()
        self._route_ids: set[str] = set()

    @classmethod
    def create(cls) -> "CharacterSystem":
        _, digest = load_character_data()
        return cls(digest=digest)

    @classmethod
    def restore(cls, snapshot: dict[str, Any], *, allow_legacy_digest: bool = False) -> "CharacterSystem":
        if (not isinstance(snapshot, dict) or set(snapshot) != {"digest", "last_updated_day"}
                or type(snapshot.get("last_updated_day")) is not int):
            raise ValueError("Invalid character system snapshot")
        _, current_digest = load_character_data()
        digest = snapshot["digest"]
        if allow_legacy_digest and digest in _LEGACY_CHARACTER_DATA_DIGESTS:
            digest = current_digest
        return cls(digest=digest, last_updated_day=snapshot["last_updated_day"])

    @staticmethod
    def migrate_legacy_physical_stats(seed: int | str, entity_id: str,
                                      stats: dict[str, Any]) -> dict[str, int]:
        """Deterministically map pre-P1 naval skills to bounded physical attributes."""
        bases = {
            "strength": (2 * stats["combat"] + stats["gunnery"] + stats["willpower"]) // 4,
            "speed": (stats["seamanship"] + stats["navigation"] + stats["cunning"]) // 3,
            "endurance": (stats["seamanship"] + stats["willpower"] + stats["combat"]) // 3,
            "toughness": (2 * stats["willpower"] + stats["combat"] + stats["leadership"]) // 4,
        }
        mapped = {}
        for attribute, base in bases.items():
            key = f"{seed}\0{entity_id}\0{attribute}".encode("utf-8")
            jitter = int(hashlib.sha256(key).hexdigest()[:8], 16) % 11 - 5
            mapped[attribute] = max(0, min(100, base + jitter))
        return mapped

    @staticmethod
    def default_combat_equipment(role: str) -> dict[str, Any]:
        """Return deterministic catalog loadout and pristine per-character condition."""
        data, _digest = load_character_data()
        loadout = data["combat_equipment"]["role_loadouts"].get(role)
        if loadout is None:
            raise ValueError(f"No combat-equipment loadout exists for role: {role}")
        return {"weapon_id": loadout["weapon_id"], "armor_id": loadout["armor_id"],
                "weapon_condition_permille": 1000, "armor_condition_permille": 1000}

    def seed_world(self, world) -> None:
        if self.last_updated_day != 0 or any(
                entity.get("kind") == "character" for entity in world.entities.values()):
            raise ValueError("Character population can only be seeded into an empty new world")
        cause = world.event_log.by_type("WORLD_GENERATED")[0].id
        rules = self.data["rules"]
        for index in range(rules["initial_major_count"]):
            self.create_character(
                world, tier="MAJOR", cause_event_id=cause,
                role=self.data["initial_major_roles"][index % len(self.data["initial_major_roles"])],
            )
        for index in range(rules["initial_minor_count"]):
            self.create_character(
                world, tier="MINOR", cause_event_id=cause,
                role=self.data["initial_minor_roles"][index % len(self.data["initial_minor_roles"])],
            )

    def create_character(self, world, *, tier: str, cause_event_id: str, role: str | None = None,
                         name: str | None = None, age_years: int | None = None,
                         island_id: str | None = None,
                         heritage: dict[str, Any] | None = None) -> str:
        if tier not in {"LEGEND", "MAJOR", "MINOR", "BACKGROUND"}:
            raise ValueError("Unknown character tier")
        if role is None:
            role = sorted(self.data["role_profiles"])[0]
        if role not in self.data["role_profiles"]:
            raise ValueError(f"Unknown character role: {role}")
        self._validate_cause(world, cause_event_id)
        character_id = world.ids.next("character")
        rng = world.rng.stream(f"character:{character_id}")
        island_ids = sorted(world.geography.islands)
        if island_id is None:
            island_id = rng.choice(island_ids)
        if island_id not in world.geography.islands:
            raise ValueError("Character birthplace must be a known island")
        if name is None:
            name = self._next_name(world, rng)
        elif not isinstance(name, str) or not name.strip() or self._name_in_use(world, name):
            raise ValueError("Character name must be nonempty and unique among living named actors")
        rules = self.data["rules"]
        if age_years is None:
            minimum, maximum = ((rules["initial_major_age_min"], rules["initial_major_age_max"])
                                if tier in {"LEGEND", "MAJOR"}
                                else (rules["initial_minor_age_min"], rules["initial_minor_age_max"]))
            age_years = rng.randint(minimum, maximum)
        if type(age_years) is not int or age_years < 0:
            raise ValueError("Character age must be a nonnegative integer")
        heritage_record = self._heritage_record(world, heritage)
        archetype = rng.choice(self.data["archetypes"])
        profile = self.data["role_profiles"][role]
        equipment = self.data["combat_equipment"]["role_loadouts"][role]
        spread = rules["generation_stat_spread"]
        stats = {stat: max(0, min(100, 40 + profile["offsets"].get(stat, 0)
                                  + rng.randint(-spread, spread)))
                 for stat in sorted(_STAT_NAMES)}
        parents = [world.entities.get(parent) or world.archive.get(parent)
                   for parent in heritage_record["parents"] if parent is not None]
        if parents:
            for stat in stats:
                inherited = sum(parent["stats"][stat] - 50 for parent in parents)
                stats[stat] = max(0, min(100, stats[stat] + inherited // (4 * len(parents))))
        traits = {}
        trait_spread = rules["generation_trait_spread"]
        modifiers = archetype["trait_modifiers"]
        for trait in sorted(_TRAIT_NAMES):
            baseline = self.data["default_traits"][trait] + modifiers.get(trait, 0)
            traits[trait] = max(0, min(100, baseline + rng.randint(-trait_spread, trait_spread)))
        if parents:
            for trait in traits:
                inherited = sum(parent["traits"][trait] - 50 for parent in parents)
                traits[trait] = max(0, min(100, traits[trait] + inherited // (4 * len(parents))))
        # Small correlated priors keep personalities coherent without adding RNG draws.
        traits["cruelty"] = max(0, min(100, traits["cruelty"] - (traits["honor"] - 50) // 5))
        dream_kind = rng.choice(profile["dream_kinds"])
        dream = {"kind": dream_kind, "target_ref": None, "progress": 0, "stated_tick": world.clock.tick}
        character = {
            "id": character_id,
            "kind": "character",
            "display_name": name,
            "epithet": None,
            "gender": None,
            "birth_tick": world.clock.tick - age_years * self._ticks_per_year(world),
            "birthplace": island_id,
            "death_tick": None,
            "cause_of_death_event": None,
            "heritage": heritage_record,
            "home_faction": None,
            "location": {"kind": "island", "id": island_id},
            "stats": stats,
            "combat_equipment": {
                "weapon_id": equipment["weapon_id"], "armor_id": equipment["armor_id"],
                "weapon_condition_permille": 1000, "armor_condition_permille": 1000,
            },
            "traits": traits,
            "values": list(profile["values"][:3]),
            "dream": dream,
            "wants": [{"kind": rng.choice(self.data["want_kinds"]), "target_ref": None,
                        "urgency_permille": rng.randint(100, 700), "created_tick": world.clock.tick,
                        "source_event": cause_event_id}],
            "health": {"hp": 100, "max_hp": 100, "max_hp_from_age": 100,
                       "injuries": [], "chronic": []},
            "bounty": {"amount": 0, "issuer_faction": None, "tick_set": None},
            "reputation": {},
            "wealth": {"silver": 0, "assets": [], "debts": []},
            "memory": [],
            "opinions": {},
            "role": role,
            "crew_id": None,
            "ship_id": None,
            "tier": tier,
            "flags": [],
            "age_years": age_years,
            "archetype_id": archetype["id"],
            "last_event_id": cause_event_id,
        }
        event = world.event_log.append(
            tick=world.clock.tick,
            event_type="CHARACTER_GENERATED",
            location=island_id,
            actors=[{"role": "character", "entity_id": character_id}],
            data={"display_name": name, "role": role, "tier": tier,
                  "age_years": age_years, "archetype_id": archetype["id"],
                  "physical_stats": {stat: stats[stat] for stat in
                                     ("strength", "speed", "endurance", "toughness")},
                  "combat_equipment": dict(character["combat_equipment"])},
            caused_by=[cause_event_id],
            causes_tags=["character_generation", f"tier:{tier.lower()}"],
            magnitude=0.15 if tier in {"LEGEND", "MAJOR"} else 0.05,
        )
        character["last_event_id"] = event.id
        world.entities[character_id] = character
        return character_id

    def effective_stat(self, character: dict[str, Any], stat: str) -> int:
        if stat not in _STAT_NAMES:
            raise ValueError(f"Unknown character stat: {stat}")
        base = character["stats"][stat]
        injury_delta = sum(
            injury.get("stat_effects", {}).get(stat, 0)
            for injury in character["health"]["injuries"]
        )
        return max(0, min(100, base + injury_delta))

    def personal_combat_profile(self, character: dict[str, Any]) -> dict[str, Any]:
        """Return an explainable view of combat-related stats without persisting a rating."""
        names = ("strength", "speed", "endurance", "toughness", "combat", "willpower")
        profile = {}
        for stat in names:
            base = character["stats"][stat]
            injury_delta = sum(
                injury.get("stat_effects", {}).get(stat, 0)
                for injury in character["health"]["injuries"]
            )
            profile[stat] = {
                "base": base,
                "injury_delta": injury_delta,
                "effective": max(0, min(100, base + injury_delta)),
            }
        gear = character["combat_equipment"]
        catalog = self.data["combat_equipment"]
        weapon = catalog["weapons"][gear["weapon_id"]]
        armor = catalog["armor"][gear["armor_id"]]
        return {
            "age_years": character["age_years"], "stats": profile,
            "equipment": {
                "weapon_id": gear["weapon_id"], "weapon": dict(weapon),
                "weapon_condition_permille": gear["weapon_condition_permille"],
                "armor_id": gear["armor_id"], "armor": dict(armor),
                "armor_condition_permille": gear["armor_condition_permille"],
            },
        }

    def improve_skill(self, world, character_id: str, skill: str, amount: int, *,
                      caused_by: str) -> str:
        character = self._character(world, character_id)
        if skill not in _STAT_NAMES or type(amount) is not int or amount <= 0:
            raise ValueError("Skill improvement requires a known skill and positive integer gain")
        self._validate_cause(world, caused_by)
        before = character["stats"][skill]
        after = min(100, before + amount)
        if after == before:
            raise ValueError("Character skill is already at its cap")
        event = world.event_log.append(
            tick=world.clock.tick, event_type="SKILL_IMPROVED",
            location=character["location"]["id"],
            actors=[{"role": "learner", "entity_id": character_id}],
            data={"skill": skill, "before": before, "after": after, "gain": after - before},
            caused_by=[caused_by], causes_tags=["skill_use", f"skill:{skill}"], magnitude=0.1,
        )
        character["stats"][skill] = after
        character["last_event_id"] = event.id
        return event.id

    def inflict_injury(self, world, character_id: str, injury_id: str, *,
                       caused_by: str) -> str:
        character = self._character(world, character_id)
        self._validate_cause(world, caused_by)
        injury = self.data.get("injuries", {}).get(injury_id)
        if injury is None:
            raise ValueError(f"Unknown injury: {injury_id}")
        care = self._medical_care_for(world, character)
        fatality_chance = max(
            0, injury["fatal_basis_points"]
            - care * injury["fatal_care_reduction_basis_points_per_point"])
        record = {"injury_id": injury_id, "severity": injury["severity"],
                  "initial_severity": injury["severity"],
                  "recovery_days_total": injury["recovery_days"],
                  "recovery_days_elapsed": 0,
                  "tick": world.clock.tick, "stat_effects": dict(injury["stat_effects"]),
                  "base_stat_effects": dict(injury["stat_effects"]),
                  "source_event": caused_by, "last_recovery_event": None}
        event = world.event_log.append(
            tick=world.clock.tick, event_type="INJURY",
            location=character["location"]["id"],
            targets=[{"role": "injured", "entity_id": character_id}],
            data={"injury_id": injury_id, "severity": injury["severity"],
                  "hp_loss": injury["hp_loss"],
                  "stat_effects": dict(injury["stat_effects"]),
                  "fatality_basis_points": fatality_chance,
                  "medical_care": care},
            caused_by=[caused_by], causes_tags=["injury", f"injury:{injury_id}"], magnitude=0.35,
        )
        character["health"]["injuries"].append(record)
        record["event_id"] = event.id
        character["health"]["hp"] = max(1, character["health"]["hp"] - injury["hp_loss"])
        character["last_event_id"] = event.id
        fatality_roll = world.rng.stream(f"injury-fatality:{event.id}").randbelow(10_000)
        if fatality_roll < fatality_chance:
            self.kill_character(world, character_id, cause="injury_complication",
                                caused_by=event.id,
                                tags=["injury_complication", "injury_fatality"])
        return event.id

    def update_day(self, day: int, world) -> None:
        if type(day) is not int or day != self.last_updated_day + 1:
            raise ValueError("Character lifecycle days must be consecutive")
        days_per_year = world.config["time"]["days_per_year"]
        for character_id in sorted(tuple(world.entities)):
            character = world.entities.get(character_id)
            if character and character.get("kind") == "character":
                self._recover_injuries(world, character_id, day)
        if day % days_per_year == 0:
            deaths = []
            for character_id in sorted(tuple(world.entities)):
                character = world.entities.get(character_id)
                if not character or character.get("kind") != "character":
                    continue
                if self._age_character(world, character, day):
                    death = self._maybe_old_age_death(world, character, day)
                    if death:
                        deaths.append(death)
            self._replace_major_rookies(world, deaths)
        self.last_updated_day = day

    def _recover_injuries(self, world, character_id: str, day: int) -> None:
        character = world.entities[character_id]
        for injury in character["health"]["injuries"]:
            total = injury.get("recovery_days_total", 0)
            elapsed = injury.get("recovery_days_elapsed", 0)
            if type(total) is not int or total <= 0 or elapsed >= total:
                continue
            before = injury["severity"]
            initial = injury.get("initial_severity", before)
            elapsed += 1
            after = max(0, (initial * (total - elapsed) + total - 1) // total)
            base_effects = injury.get("base_stat_effects", injury["stat_effects"])
            effects = {stat: (value * after // initial if initial else 0)
                       for stat, value in base_effects.items()}
            hp_before = character["health"]["hp"]
            hp_after = min(character["health"]["max_hp"], hp_before + 1)
            completed = elapsed == total
            event = world.event_log.append(
                tick=day * self._ticks_per_day(world), event_type="INJURY_RECOVERY",
                location=character["location"]["id"],
                actors=[{"role": "patient", "entity_id": character_id}],
                targets=[{"role": "injury", "entity_id": character_id}],
                data={"injury_id": injury["injury_id"],
                      "source_injury_event_id": injury["event_id"],
                      "severity_before": before, "severity_after": after,
                      "hp_before": hp_before, "hp_after": hp_after,
                      "recovered": completed, "care": "natural_recovery"},
                caused_by=[injury["event_id"]],
                causes_tags=["injury_follow_up", "injury_recovery",
                             *( ["injury_recovered"] if completed else [])],
                magnitude=0.25 if completed else 0.05)
            injury.update({"severity": after, "stat_effects": effects,
                           "recovery_days_elapsed": elapsed,
                           "last_recovery_event": event.id})
            character["health"]["hp"] = hp_after
            character["last_event_id"] = event.id
            if completed:
                injury_data = self.data["injuries"][injury["injury_id"]]
                if injury_data["scar_on_recovery"] and not injury.get("scar_event_id"):
                    scar_data = self.data["injuries"]["scar"]
                    scar_event = world.event_log.append(
                        tick=day * self._ticks_per_day(world), event_type="INJURY",
                        location=character["location"]["id"],
                        targets=[{"role": "injured", "entity_id": character_id}],
                        data={"injury_id": "scar", "severity": scar_data["severity"],
                              "hp_loss": 0, "scar_from_injury_event_id": injury["event_id"]},
                        caused_by=[event.id, injury["event_id"]],
                        causes_tags=["injury", "injury:scar", "healed_injury_scar"],
                        magnitude=0.1)
                    character["health"]["injuries"].append({
                        "injury_id": "scar", "severity": scar_data["severity"],
                        "initial_severity": scar_data["severity"],
                        "recovery_days_total": 0, "recovery_days_elapsed": 0,
                        "tick": scar_event.tick, "stat_effects": dict(scar_data["stat_effects"]),
                        "base_stat_effects": dict(scar_data["stat_effects"]),
                        "source_event": event.id, "event_id": scar_event.id,
                        "last_recovery_event": None,
                    })
                    injury["scar_event_id"] = scar_event.id
                    character["last_event_id"] = scar_event.id

    @staticmethod
    def _medical_care_for(world, character: dict[str, Any]) -> int:
        crew = world.crew_ships.crews.get(character.get("crew_id"))
        if not crew:
            return 0
        surgeon_id = crew.get("roles", {}).get("surgeon")
        surgeon = world.entities.get(surgeon_id)
        if not surgeon:
            return 0
        return world.characters.effective_stat(surgeon, "medicine")

    @staticmethod
    def _ticks_per_day(world) -> int:
        return world.config["time"]["hours_per_day"] // world.config["time"]["hours_per_tick"]

    def _age_character(self, world, character: dict[str, Any], day: int) -> bool:
        year_ticks = self._ticks_per_year(world)
        age = max(0, (world.clock.tick - character["birth_tick"]) // year_ticks)
        old_age = character["age_years"]
        if age <= old_age:
            return False
        changes = {}
        age_health_cap = max(40, 100 - max(0, age - 50) // 2)
        if age_health_cap != character["health"]["max_hp_from_age"]:
            character["health"]["max_hp_from_age"] = age_health_cap
            character["health"]["max_hp"] = min(character["health"]["max_hp"], age_health_cap)
            character["health"]["hp"] = min(character["health"]["hp"], age_health_cap)
        if age > self.data["rules"]["age_stat_decay_start"]:
            decay = min(100, (age - max(old_age, self.data["rules"]["age_stat_decay_start"]))
                        * self.data["rules"]["age_stat_decay_per_year"])
            for stat in self.data["rules"]["age_decay_stats"]:
                before = character["stats"][stat]
                after = max(0, before - decay)
                if after != before:
                    changes[stat] = {"before": before, "after": after}
                    character["stats"][stat] = after
        event = world.event_log.append(
            tick=world.clock.tick, event_type="CHARACTER_AGED",
            location=character["location"]["id"],
            actors=[{"role": "character", "entity_id": character["id"]}],
            data={"age_before": old_age, "age_after": age, "stat_changes": changes},
            caused_by=[character["last_event_id"]],
            causes_tags=["birthday", "aging"], magnitude=0.08,
        )
        character["age_years"] = age
        character["last_event_id"] = event.id
        return True

    def _maybe_old_age_death(self, world, character: dict[str, Any], day: int) -> str | None:
        rules = self.data["rules"]
        age = character["age_years"]
        if age < rules["death_start_age"]:
            return None
        risk = min(rules["death_max_basis_points"],
                   rules["death_base_basis_points"]
                   + (age - rules["death_start_age"]) * rules["death_per_year_basis_points"])
        if world.rng.stream(f"character:{character['id']}").randbelow(10000) >= risk:
            return None
        return self.kill_character(world, character["id"], cause="old_age",
                                   caused_by=character["last_event_id"],
                                   tags=["natural_death", "old_age"],
                                   replace_majors=False)

    def kill_character(self, world, character_id: str, *, cause: str,
                       caused_by: str, tags: list[str] | None = None,
                       replace_majors: bool = True) -> str:
        character = self._character(world, character_id)
        self._validate_cause(world, caused_by)
        event = world.event_log.append(
            tick=world.clock.tick, event_type="CHARACTER_DIED",
            location=world.location_of(character_id)["id"],
            targets=[{"role": "deceased", "entity_id": character_id}],
            data={"cause": cause, "age_years": character["age_years"],
                  "cause_event_id": caused_by},
            caused_by=[caused_by], causes_tags=tags or ["violent_death", cause], magnitude=0.8,
        )
        character["death_tick"] = world.clock.tick
        character["cause_of_death_event"] = caused_by
        character["last_event_id"] = event.id
        world.archive[character_id] = character
        del world.entities[character_id]
        decisions = getattr(world, "decisions", None)
        if decisions is not None:
            decisions.on_agent_terminal(world, character_id)
        if hasattr(world, "crew_ships"):
            world.crew_ships.on_character_death(world, character_id, event.id)
        prisoner = getattr(getattr(world, "encounters", None), "prisoners", {}).get(character_id)
        if prisoner is not None and prisoner["status"] != "executed":
            prisoner["status"] = "deceased"
            prisoner["fate_event_id"] = event.id
        if hasattr(world, "memories"):
            world.memories.on_character_death(world, character_id, event.id)
        if replace_majors:
            self._replace_major_rookies(world, [event.id])
        return event.id

    def depart_character(self, world, character_id: str, *, destination: str,
                         caused_by: str, reason: str) -> str:
        """Record a living, unaffiliated island resident's explicit world departure."""
        character = self._character(world, character_id)
        self._validate_cause(world, caused_by)
        if (not isinstance(destination, str) or not destination.strip()
                or not isinstance(reason, str) or not reason.strip()):
            raise ValueError("Departure requires a destination and causal reason")
        if (character.get("death_tick") is not None
                or character.get("departure_tick") is not None
                or character.get("missing_at_sea") is not None
                or character.get("lod_resident_origin") is True
                or character.get("tier") == "BACKGROUND"):
            raise ValueError("Only an active named resident can depart the world")
        if character.get("crew_id") is not None or character.get("ship_id") is not None:
            raise ValueError("A crew member must leave their crew before departing the world")
        if world.location_of(character_id).get("kind") != "island":
            raise ValueError("World departures require a valid island departure location")
        if character_id in world.encounters.prisoners and world.encounters.prisoners[
                character_id].get("status") == "held":
            raise ValueError("A held prisoner cannot depart the world")
        if any(character_id in crew["roster"] for crew in world.crew_ships.crews.values()):
            raise ValueError("A rostered character cannot depart the world")

        location = world.location_of(character_id)
        event = world.event_log.append(
            tick=world.clock.tick, event_type="CHARACTER_DEPARTED", location=location["id"],
            actors=[{"role": "emigrant", "entity_id": character_id}],
            targets=[{"role": "departed", "entity_id": character_id}],
            data={"character_id": character_id, "location": location,
                  "destination": destination.strip(), "reason": reason.strip()},
            caused_by=[caused_by], causes_tags=["population_departure", "emigration", reason],
            magnitude=0.35)
        character["departure_tick"] = world.clock.tick
        character["departure_event_id"] = event.id
        character["last_event_id"] = event.id
        world.archive[character_id] = character
        del world.entities[character_id]
        decisions = getattr(world, "decisions", None)
        if decisions is not None:
            decisions.on_agent_terminal(world, character_id)
        return event.id

    def _replace_major_rookies(self, world, death_events: list[str]) -> None:
        rules = self.data["rules"]
        major_count = sum(
            1 for entity in world.entities.values()
            if entity.get("kind") == "character" and entity.get("tier") in {"LEGEND", "MAJOR"}
        )
        if major_count >= rules["rookie_replacement_floor"]:
            return
        cause = death_events[-1] if death_events else world.event_log.by_type("WORLD_GENERATED")[0].id
        while major_count < rules["rookie_target_major_count"] and major_count < rules["major_population_cap"]:
            character_id = self.create_character(
                world, tier="MAJOR", cause_event_id=cause,
                role=self.data["rookie_roles"][major_count % len(self.data["rookie_roles"])],
                age_years=self.data["rookie_age_years"],
            )
            generated = world.entities[character_id]["last_event_id"]
            event = world.event_log.append(
                tick=world.clock.tick, event_type="ROOKIE_SPAWNED",
                location=world.entities[character_id]["location"]["id"],
                actors=[{"role": "rookie", "entity_id": character_id}],
                data={"tier": "MAJOR", "age_years": world.entities[character_id]["age_years"]},
                caused_by=[generated], causes_tags=["population_replacement"], magnitude=0.3,
            )
            world.entities[character_id]["last_event_id"] = event.id
            cause = event.id
            major_count += 1

    def _next_name(self, world, rng) -> str:
        given = self.data["name_pools"]["given"]
        family = self.data["name_pools"]["family"]
        start_given = rng.randbelow(len(given))
        start_family = rng.randbelow(len(family))
        for offset in range(len(given) * len(family)):
            name = (given[(start_given + offset // len(family)) % len(given)] + " "
                    + family[(start_family + offset) % len(family)])
            if not self._name_in_use(world, name):
                return name
        raise ValueError("Character name pool exhausted")

    def _heritage_record(self, world, heritage: dict[str, Any] | None) -> dict[str, Any]:
        if heritage is None:
            heritage = {}
        allowed = {"parents", "mentor", "house", "inherited_grudges", "inherited_debts", "titles"}
        if not isinstance(heritage, dict) or set(heritage) - allowed:
            raise ValueError("Invalid character heritage fields")
        parents = heritage.get("parents", [None, None])
        if not isinstance(parents, list) or len(parents) != 2:
            raise ValueError("Character heritage requires two parent slots")
        mentor = heritage.get("mentor")
        refs = [*parents, mentor]
        for ref in refs:
            if ref is None:
                continue
            if not isinstance(ref, str):
                raise ValueError(f"Invalid character heritage reference: {ref}")
            entity = world.entities.get(ref) or world.archive.get(ref)
            if not entity or entity.get("kind") != "character":
                raise ValueError(f"Unknown character heritage reference: {ref}")
        house = heritage.get("house")
        if house is not None and (not isinstance(house, str) or not house.strip()):
            raise ValueError("Character house reference must be a nonempty string or null")
        grudges = heritage.get("inherited_grudges", [])
        debts = heritage.get("inherited_debts", [])
        titles = heritage.get("titles", [])
        if (not isinstance(grudges, list) or not isinstance(debts, list) or not isinstance(titles, list)
                or any(not isinstance(row, dict)
                       or set(row) not in ({"target", "intensity"},
                                           {"target", "intensity", "source_event_id"})
                       or not isinstance(row["target"], str) or not row["target"]
                       or type(row["intensity"]) is not int or not 0 <= row["intensity"] <= 100
                       or "source_event_id" in row
                       and not isinstance(row["source_event_id"], str)
                       for row in grudges)
                or any(not isinstance(row, dict) for row in debts)
                or any(not isinstance(title, str) or not title for title in titles)):
            raise ValueError("Invalid inherited character history")
        if world is not None:
            for row in grudges:
                if "source_event_id" in row:
                    world.event_log.get(row["source_event_id"])
        return {
            "parents": list(parents), "mentor": mentor, "house": house,
            "inherited_grudges": [dict(row) for row in grudges],
            "inherited_debts": [dict(row) for row in debts], "titles": list(titles),
        }

    @staticmethod
    def _name_in_use(world, name: str) -> bool:
        normalized = name.strip().casefold()
        archived_collision = any(
            character.get("kind") == "character"
            and character.get("display_name", "").strip().casefold() == normalized
            for character in world.archive.values()
        )
        lod_collision = any(
            record.get("display_name", "").strip().casefold() == normalized
            for record in getattr(getattr(world, "lod", None), "named_records", {}).values()
        )
        return (living_name_in_use(world, name) or archived_collision or lod_collision
                or any(merchant.get("name", "").casefold() == name.strip().casefold()
                       for merchant in world.economy.merchants.values()))

    @staticmethod
    def _ticks_per_year(world) -> int:
        time = world.config["time"]
        return time["days_per_year"] * time["hours_per_day"] // time["hours_per_tick"]

    @staticmethod
    def _validate_cause(world, event_id: str) -> None:
        try:
            world.event_log.get(event_id)
        except KeyError as exc:
            raise ValueError(f"Unknown character cause event: {event_id}") from exc

    @staticmethod
    def _character(world, character_id: str) -> dict[str, Any]:
        character = world.entities.get(character_id)
        if not character or character.get("kind") != "character":
            raise ValueError(f"Unknown living character: {character_id}")
        return character

    def validate_world(self, world) -> None:
        names = set()
        for entity_id, character in sorted(world.entities.items()):
            if character.get("kind") != "character":
                continue
            self._validate_character_record(world, entity_id, character, living=True)
            name = character["display_name"]
            if name in names:
                raise ValueError("Living character display names must be unique")
            names.add(name)
            try:
                world.event_log.get(character["last_event_id"])
            except KeyError as exc:
                raise ValueError(f"Character has unknown last event: {entity_id}") from exc
        for entity_id, character in sorted(world.archive.items()):
            if character.get("kind") != "character":
                continue
            self._validate_character_record(world, entity_id, character, living=False)
            if character["display_name"] in names:
                raise ValueError("Living and archived character identities collide")
            terminal_events = [character["last_event_id"]]
            if character.get("death_tick") is not None:
                terminal_events.append(character["cause_of_death_event"])
            else:
                terminal_events.append(character["departure_event_id"])
            for event_id in terminal_events:
                try:
                    world.event_log.get(event_id)
                except KeyError as exc:
                    raise ValueError(f"Archived character has unknown event: {entity_id}") from exc
            if character.get("death_tick") is None:
                departure_event = world.event_log.get(character["departure_event_id"])
                if (departure_event.type != "CHARACTER_DEPARTED"
                        or departure_event.tick != character["departure_tick"]
                        or not any(part.get("entity_id") == entity_id
                                   and part.get("role") == "departed"
                                   for part in departure_event.targets)):
                    raise ValueError(f"Archived character has invalid departure event: {entity_id}")
        for merchant in world.economy.merchants.values():
            if merchant["name"] in names:
                raise ValueError("Living merchant and character names must be unique")

    def _validate_character_record(self, world, entity_id: str, character: dict[str, Any], *,
                                   living: bool) -> None:
        if (character.get("id") != entity_id or character.get("kind") != "character"
                or not isinstance(character.get("display_name"), str)
                or not character["display_name"].strip()
                or character.get("epithet") is not None and not isinstance(character.get("epithet"), str)
                or character.get("gender") is not None and not isinstance(character.get("gender"), str)
                or character.get("role") not in self.data["role_profiles"]
                or character.get("tier") not in {"LEGEND", "MAJOR", "MINOR", "BACKGROUND"}
                or character.get("archetype_id") not in {row["id"] for row in self.data["archetypes"]}
                or type(character.get("birth_tick")) is not int
                or type(character.get("age_years")) is not int or character["age_years"] < 0
                or set(character.get("stats", {})) != _STAT_NAMES
                or not self._valid_combat_equipment(character.get("combat_equipment"))
                or set(character.get("traits", {})) != _TRAIT_NAMES
                or any(type(value) is not int or not 0 <= value <= 100
                       for value in (*character["stats"].values(), *character["traits"].values()))
                or not self._valid_character_location(character.get("location"))):
            raise ValueError(f"Invalid character record: {entity_id}")
        reputation = character.get("reputation")
        if not isinstance(reputation, dict):
            raise ValueError(f"Invalid character reputation: {entity_id}")
        if reputation:
            if (set(reputation) != {"public_renown", "deed_event_ids", "paths"}
                    or type(reputation["public_renown"]) is not int
                    or not 0 < reputation["public_renown"] <= 1000
                    or not isinstance(reputation["deed_event_ids"], list)
                    or len(reputation["deed_event_ids"]) > 64
                    or len(set(reputation["deed_event_ids"])) != len(reputation["deed_event_ids"])
                    or not isinstance(reputation["paths"], list)
                    or not set(reputation["paths"]) <= {"profitable_trade", "combat_victory"}):
                raise ValueError(f"Invalid public-renown record: {entity_id}")
            recognized = [event for event in world.event_log.by_type("PUBLIC_RENOWN_EARNED")
                          if any(actor.get("entity_id") == entity_id for actor in event.actors)]
            recognized.sort(key=lambda event: (event.tick, event.id))
            if not set(reputation["deed_event_ids"]) <= {
                    event.data.get("source_event_id") for event in recognized}:
                raise ValueError(f"Public renown cites an unrecognized deed: {entity_id}")
            if not set(reputation["paths"]) <= {
                    event.data.get("deed_kind") for event in recognized}:
                raise ValueError(f"Public-renown paths lack recognition events: {entity_id}")
            expected_renown = 0
            for event in recognized:
                increment = {"profitable_trade": 20, "combat_victory": 50}.get(
                    event.data.get("deed_kind"))
                expected_after = min(1000, expected_renown + increment) if increment else -1
                if (increment is None or event.data.get("renown_before") != expected_renown
                        or event.data.get("delta") != expected_after - expected_renown
                        or event.data.get("renown_after") != expected_after
                        or event.caused_by != (event.data.get("source_event_id"),)):
                    raise ValueError(f"Public-renown history is not a bounded deed chain: {entity_id}")
                expected_renown = expected_after
            if expected_renown != reputation["public_renown"]:
                raise ValueError(f"Public-renown balance disagrees with its event history: {entity_id}")
        health = character.get("health")
        if (not isinstance(health, dict)
                or set(health) != {"hp", "max_hp", "max_hp_from_age", "injuries", "chronic"}
                or type(health["hp"]) is not int or type(health["max_hp"]) is not int
                or type(health["max_hp_from_age"]) is not int
                or not 1 <= health["max_hp_from_age"] <= 100
                or not 1 <= health["hp"] <= health["max_hp"]
                or not isinstance(health["injuries"], list) or not isinstance(health["chronic"], list)):
            raise ValueError(f"Invalid character health: {entity_id}")
        for injury in health["injuries"]:
            if (not isinstance(injury, dict)
                    or not isinstance(injury.get("injury_id"), str)
                    or type(injury.get("severity")) is not int
                    or not isinstance(injury.get("stat_effects"), dict)):
                raise ValueError(f"Invalid character injury record: {entity_id}")
        if living:
            if (character.get("death_tick") is not None
                    or character.get("cause_of_death_event") is not None
                    or character.get("departure_tick") is not None
                    or character.get("departure_event_id") is not None):
                raise ValueError(f"Living character has terminal state: {entity_id}")
        else:
            deceased = (type(character.get("death_tick")) is int
                        and isinstance(character.get("cause_of_death_event"), str)
                        and character.get("departure_tick") is None
                        and character.get("departure_event_id") is None)
            departed = (character.get("death_tick") is None
                        and character.get("cause_of_death_event") is None
                        and type(character.get("departure_tick")) is int
                        and isinstance(character.get("departure_event_id"), str))
            if not (deceased ^ departed):
                raise ValueError(f"Archived character lacks one valid terminal state: {entity_id}")

    def _valid_combat_equipment(self, equipment: Any) -> bool:
        if (not isinstance(equipment, dict)
                or set(equipment) != {"weapon_id", "armor_id", "weapon_condition_permille",
                                      "armor_condition_permille"}):
            return False
        catalog = self.data["combat_equipment"]
        return (isinstance(equipment["weapon_id"], str)
                and equipment["weapon_id"] in catalog["weapons"]
                and isinstance(equipment["armor_id"], str)
                and equipment["armor_id"] in catalog["armor"]
                and type(equipment["weapon_condition_permille"]) is int
                and 0 <= equipment["weapon_condition_permille"] <= 1000
                and type(equipment["armor_condition_permille"]) is int
                and 0 <= equipment["armor_condition_permille"] <= 1000)

    @property
    def data_island_ids(self) -> set[str]:
        # Geography is validated by WorldState; this property is replaced at
        # record validation time from the loaded authored island ID list.
        return self._island_ids

    def bind_islands(self, island_ids: set[str], route_ids: set[str] | None = None) -> None:
        self._island_ids = set(island_ids)
        self._route_ids = set(route_ids or ())

    def _valid_character_location(self, location: Any) -> bool:
        if not isinstance(location, dict):
            return False
        if location.get("kind") == "island":
            return location.get("id") in self.data_island_ids
        if location.get("kind") == "route":
            return (location.get("id") in self._route_ids
                    and location.get("from") in self.data_island_ids
                    and location.get("to") in self.data_island_ids
                    and type(location.get("progress_permille")) is int
                    and 0 <= location["progress_permille"] < 1000)
        return False

    def snapshot(self) -> dict[str, Any]:
        return {"digest": self.digest, "last_updated_day": self.last_updated_day}
