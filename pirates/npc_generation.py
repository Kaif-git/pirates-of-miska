"""Seeded, creation-time NPC generation primitives.

This module is deliberately separate from the live society and tactical combat
systems. Generation uses stable, field-scoped random streams so adding a field
does not reroll unrelated character details.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import blake2b
import json
import math
import random
import secrets

GENERATOR_VERSION = 1
SCHEMA_VERSION = 1
CONTENT_VERSION = "npc-core-1"

DEFAULT_SETTINGS = {
    "notable_people": 24,
    "organization_count": 4,
    "vessel_count": 8,
    "history_days": 45,
}

ATTRIBUTES = ("strength", "endurance", "perception", "intellect", "presence")
PERSONALITY = ("boldness", "greed", "empathy", "pride", "patience", "sociability", "discipline")
VALUES = ("loyalty", "freedom", "wealth", "order", "fame", "family")
SKILLS = ("leadership", "navigation", "trade", "tactics", "diplomacy", "seamanship", "medicine")
TRAITS = {"cautious", "bold", "greedy", "merciful", "vindictive", "disciplined", "charismatic", "steadfast"}
RELATION_DIMS = ("trust", "affection", "respect", "fear", "grievance", "obligation")
TRAIT_EFFECTS = {
    "cautious": {"risk_tolerance": -15, "retreat_threshold": 10},
    "bold": {"risk_tolerance": 12, "visible_success_weight": 4},
    "greedy": {"wealth_goal_weight": 14, "resource_cost_weight": 5},
    "merciful": {"harm_aversion": 14, "diplomacy_weight": 4},
    "vindictive": {"grievance_weight": 16, "forgiveness_rate": -8},
    "disciplined": {"commitment_weight": 12, "goal_switch_penalty": 5},
    "charismatic": {"persuasion_weight": 10, "trust_gain_weight": 3},
    "steadfast": {"commitment_weight": 8, "goal_switch_penalty": 8},
}

ORIGINS = ("Verdant Cay", "Cinder Quay", "Freeport", "The Glass Shoals", "The Blue Reach", "Government Patrol Lane")
FIRST_NAMES = ("Mara", "Tamsin", "Oren", "Sella", "Ilyan", "Nera", "Corin", "Veya", "Dain", "Lio",
               "Rhea", "Jorin", "Kessa", "Tarin", "Mira", "Bram", "Anwen", "Rusk", "Edda", "Pavel")
LAST_NAMES = ("Vey", "Rill", "Pike", "Quill", "Rusk", "Vale", "Morrow", "Kest", "Darrow", "Sorn",
              "Ash", "Fen", "Hale", "Wren", "Calder", "Thorne", "Voss", "Reef", "Drake", "Mast")

# Each background provides biases, not guarantees. Skill biases are XP units.
BACKGROUNDS = {
    "deckhand": {"occupation": "deckhand", "attributes": {"endurance": 8, "strength": 5},
                 "personality": {"discipline": 5, "sociability": 3},
                 "skills": {"seamanship": 1200, "navigation": 450}},
    "merchant": {"occupation": "merchant", "attributes": {"intellect": 6, "presence": 3},
                 "personality": {"greed": 7, "patience": 5},
                 "skills": {"trade": 1500, "diplomacy": 500}},
    "naval_cadet": {"occupation": "naval cadet", "attributes": {"perception": 5, "intellect": 3},
                    "personality": {"discipline": 12, "order": 8},
                    "skills": {"tactics": 1100, "leadership": 500, "navigation": 500}},
    "fisher": {"occupation": "fisher", "attributes": {"perception": 7, "endurance": 4},
               "personality": {"patience": 8, "sociability": -2},
               "skills": {"seamanship": 1000, "navigation": 700}},
    "dockworker": {"occupation": "dockworker", "attributes": {"strength": 10, "endurance": 5},
                   "personality": {"boldness": 2, "discipline": 3},
                   "skills": {"seamanship": 350, "leadership": 250}},
    "scholar": {"occupation": "scholar", "attributes": {"intellect": 12, "perception": 4},
                "personality": {"patience": 7, "sociability": -3},
                "skills": {"medicine": 800, "diplomacy": 450, "navigation": 250}},
    "shore_guard": {"occupation": "shore guard", "attributes": {"perception": 5, "strength": 4},
                    "personality": {"discipline": 8, "boldness": 3},
                    "skills": {"tactics": 750, "leadership": 350}},
}

ROLE_BIASES = {
    "captain": {"leadership": 1100, "navigation": 250},
    "officer": {"leadership": 450, "diplomacy": 200},
    "crew": {},
    "commander": {"leadership": 1400, "tactics": 700, "diplomacy": 300},
}


def _canonical_seed(seed):
    if seed is None:
        return f"u:{secrets.randbits(128):032x}"
    if type(seed) is int:
        if not 0 <= seed < 2**128:
            raise ValueError("Integer seed must fit an unsigned 128-bit value")
        return f"i:{seed:032x}"
    if isinstance(seed, str) and seed and len(seed) <= 128 and seed == seed.strip():
        return f"s:{seed}"
    raise ValueError("Seed must be an unsigned 128-bit integer or a non-empty string")


def _validate_settings(settings):
    if settings is None:
        settings = {}
    if not isinstance(settings, dict) or set(settings) - set(DEFAULT_SETTINGS):
        raise ValueError("Unknown NPC generation setting")
    result = dict(DEFAULT_SETTINGS) | settings
    ranges = {
        "notable_people": (4, 200),
        "organization_count": (2, 20),
        "vessel_count": (2, 100),
        "history_days": (0, 365),
    }
    for name, (low, high) in ranges.items():
        value = result[name]
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"Invalid NPC generation setting: {name}")
    if result["organization_count"] > result["notable_people"]:
        raise ValueError("Each organization requires at least one notable person")
    if result["vessel_count"] < result["organization_count"]:
        raise ValueError("Each organization requires at least one vessel")
    return result


def make_manifest(seed=None, settings=None):
    """Create a JSON-safe immutable-by-convention world-generation manifest."""
    return {
        "seed": _canonical_seed(seed),
        "generator_version": GENERATOR_VERSION,
        "schema_version": SCHEMA_VERSION,
        "content_version": CONTENT_VERSION,
        "settings": _validate_settings(settings),
    }


def validate_manifest(manifest):
    if not isinstance(manifest, dict) or set(manifest) != {
            "seed", "generator_version", "schema_version", "content_version", "settings"}:
        raise ValueError("Invalid NPC generation manifest")
    seed = manifest["seed"]
    if not isinstance(seed, str) or ":" not in seed:
        raise ValueError("Invalid canonical generation seed")
    seed_kind, seed_value = seed.split(":", 1)
    if seed_kind in ("i", "u"):
        try:
            valid_seed = len(seed_value) == 32 and seed_value == seed_value.lower() and int(seed_value, 16) < 2**128
        except ValueError:
            valid_seed = False
        if not valid_seed:
            raise ValueError("Invalid canonical generation seed")
    elif seed_kind != "s" or not seed_value or len(seed_value) > 128 or seed_value != seed_value.strip():
        raise ValueError("Invalid canonical generation seed")
    if (type(manifest["generator_version"]) is not int or manifest["generator_version"] != GENERATOR_VERSION
            or type(manifest["schema_version"]) is not int or manifest["schema_version"] != SCHEMA_VERSION):
        raise ValueError("Unsupported NPC generator/schema version")
    if manifest["content_version"] != CONTENT_VERSION:
        raise ValueError("Unsupported NPC generation content version")
    settings = _validate_settings(manifest["settings"])
    if settings != manifest["settings"]:
        raise ValueError("NPC generation settings are not canonical")
    return deepcopy(manifest)


def random_stream(manifest, stream, key=""):
    """Return an independent deterministic RNG for a named stream/entity key."""
    manifest = validate_manifest(manifest)
    if not isinstance(stream, str) or not stream or not isinstance(key, str):
        raise ValueError("Random stream and key must be strings")
    payload = json.dumps([manifest["seed"], GENERATOR_VERSION, stream, key],
                         ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    seed = int.from_bytes(blake2b(payload, digest_size=16, person=b"MiskaNPCStream").digest(), "big")
    return random.Random(seed)


def stable_id(manifest, entity_type, ordinal):
    if not isinstance(entity_type, str) or not entity_type or type(ordinal) is not int or ordinal < 0:
        raise ValueError("Invalid generated entity identity")
    digest = blake2b(digest_size=10, person=b"MiskaNPCId")
    digest.update(validate_manifest(manifest)["seed"].encode("utf-8"))
    digest.update(f"\0{entity_type}\0{ordinal}".encode("utf-8"))
    return f"{entity_type}_{digest.hexdigest()}"


def _clamp(value, low=0, high=100):
    return max(low, min(high, int(value)))


def _biased_value(rng, bias=0, spread=22):
    return _clamp(50 + bias + rng.randint(-spread, spread))


def _weighted_choice(rng, weights):
    total = sum(weights.values())
    roll = rng.randrange(total)
    for value, weight in weights.items():
        roll -= weight
        if roll < 0:
            return value
    raise AssertionError("weights must be positive")


def _background_weights(organization_kind):
    weights = {key: 10 for key in BACKGROUNDS}
    if organization_kind in ("pirate_fleet", "independent_crew", "pirate"):
        weights.update(deckhand=24, fisher=18, dockworker=16, merchant=9, naval_cadet=5)
    elif organization_kind in ("naval_command", "marine"):
        weights.update(naval_cadet=28, shore_guard=20, deckhand=12, scholar=9)
    elif organization_kind in ("trading_company", "merchant"):
        weights.update(merchant=30, deckhand=12, dockworker=14, scholar=8)
    return weights


def _traits_for(personality, attributes, rng):
    candidates = []
    if personality["boldness"] <= 38 or personality["patience"] >= 78:
        candidates.append("cautious")
    if personality["boldness"] >= 76 and personality["patience"] <= 55:
        candidates.append("bold")
    if personality["greed"] >= 76:
        candidates.append("greedy")
    if personality["empathy"] >= 76:
        candidates.append("merciful")
    if personality["pride"] >= 76 and personality["empathy"] <= 42:
        candidates.append("vindictive")
    if personality["discipline"] >= 76:
        candidates.append("disciplined")
    if attributes["presence"] >= 70 and personality["sociability"] >= 68:
        candidates.append("charismatic")
    if personality["discipline"] >= 62 and personality["patience"] >= 62:
        candidates.append("steadfast")
    # Traits derive primarily from personality; a small keyed draw adds rarity
    # without making them decorative or allowing unbounded trait stacking.
    selected = [trait for trait in candidates if rng.random() < 0.72]
    if not selected and candidates:
        selected = [candidates[rng.randrange(len(candidates))]]
    return sorted(selected[:3])


def generate_person(manifest, person_id, role="crew", organization_kind="independent_crew", ordinal=0):
    """Generate one JSON-safe person record with correlated background and skills."""
    manifest = validate_manifest(manifest)
    if not isinstance(person_id, str) or not person_id or len(person_id) > 96:
        raise ValueError("Invalid generated person ID")
    if role not in ROLE_BIASES:
        raise ValueError("Unknown generated NPC role")
    if organization_kind not in ("pirate_fleet", "independent_crew", "naval_command", "trading_company"):
        raise ValueError("Unknown organization kind")
    if type(ordinal) is not int or ordinal < 0:
        raise ValueError("Invalid person ordinal")

    identity_rng = random_stream(manifest, "identity", person_id)
    name = f"{identity_rng.choice(FIRST_NAMES)} {identity_rng.choice(LAST_NAMES)}"
    background_rng = random_stream(manifest, "background", person_id)
    background_id = _weighted_choice(background_rng, _background_weights(organization_kind))
    background = BACKGROUNDS[background_id]

    age_rng = random_stream(manifest, "age", person_id)
    age = age_rng.randint(20, 68)
    attrs_rng = random_stream(manifest, "attributes", person_id)
    attributes = {key: _biased_value(attrs_rng, background["attributes"].get(key, 0)) for key in ATTRIBUTES}
    personality_rng = random_stream(manifest, "personality", person_id)
    personality = {key: _biased_value(personality_rng, background["personality"].get(key, 0))
                   for key in PERSONALITY}
    values_rng = random_stream(manifest, "values", person_id)
    values = {key: _biased_value(values_rng) for key in VALUES}
    # Values are related to motives but remain distinct from personality.
    values["wealth"] = _biased_value(values_rng, (personality["greed"] - 50) // 4)
    values["fame"] = _biased_value(values_rng, (personality["pride"] + personality["sociability"] - 100) // 6)
    values["loyalty"] = _biased_value(values_rng, (personality["empathy"] + personality["discipline"] - 100) // 7)

    xp_rng = random_stream(manifest, "skill_xp", person_id)
    skill_xp = {}
    for skill in SKILLS:
        background_xp = background["skills"].get(skill, 0)
        role_xp = ROLE_BIASES[role].get(skill, 0)
        age_factor = max(0, age - 18) / 50
        skill_xp[skill] = max(0, min(10000, int((background_xp + role_xp) * age_factor)
                                  + xp_rng.randint(0, 650)))
    skills = {skill: min(100, int(math.sqrt(xp))) for skill, xp in skill_xp.items()}
    traits = _traits_for(personality, attributes, random_stream(manifest, "traits", person_id))
    skill_total = sum(skills.values())
    career_level = max(1, min(20, 1 + skill_total // 35))
    personal_capability = int(round(
        attributes["strength"] * 0.12 + attributes["endurance"] * 0.12
        + attributes["perception"] * 0.14 + attributes["intellect"] * 0.16
        + attributes["presence"] * 0.10 + skills["leadership"] * 0.12
        + skills["tactics"] * 0.12 + skills["navigation"] * 0.12))
    reputation = random_stream(manifest, "reputation", person_id).randint(5, 70)
    rank_multiplier = {"crew": 0.45, "officer": 0.70, "captain": 0.88, "commander": 1.0}[role]
    command_power = _clamp(round((skills["leadership"] * 0.42 + attributes["presence"] * 0.23
                                  + career_level * 2 + reputation * 0.13) * rank_multiplier))
    result = {
        "person_id": person_id,
        "name": name,
        "age": age,
        "origin": random_stream(manifest, "origin", person_id).choice(ORIGINS),
        "background": background_id,
        "occupation": background["occupation"],
        "role": role,
        "organization_kind": organization_kind,
        "attributes": attributes,
        "personality": personality,
        "values": values,
        "traits": traits,
        "skills": skills,
        "skill_xp": skill_xp,
        "career_level": career_level,
        "personal_capability": personal_capability,
        "command_power": command_power,
        "reputation": reputation,
    }
    validate_person(result)
    return result


def derive_command_power(person):
    """Backfill the strategic role score without regenerating an old identity."""
    role = person.get("role")
    rank_multiplier = {"crew": 0.45, "officer": 0.70, "captain": 0.88, "commander": 1.0}.get(role)
    if rank_multiplier is None:
        raise ValueError("Cannot migrate unknown generated NPC role")
    skills, attributes = person.get("skills", {}), person.get("attributes", {})
    reputation = person.get("reputation")
    if (type(skills.get("leadership")) is not int or type(attributes.get("presence")) is not int
            or type(person.get("career_level")) is not int or type(reputation) is not int):
        raise ValueError("Cannot migrate incomplete generated NPC command score")
    return _clamp(round((skills["leadership"] * 0.42 + attributes["presence"] * 0.23
                         + person["career_level"] * 2 + reputation * 0.13) * rank_multiplier))


def generate_people(manifest, count=None, organization_kind="independent_crew", role="crew"):
    """Generate stable-ID people; collision suffixes are deterministic."""
    manifest = validate_manifest(manifest)
    if count is None:
        count = manifest["settings"]["notable_people"]
    if type(count) is not int or not 1 <= count <= 200:
        raise ValueError("Generated person count must be between 1 and 200")
    people = {}
    used_names = set()
    for ordinal in range(count):
        person_id = stable_id(manifest, "person", ordinal)
        person = generate_person(manifest, person_id, role, organization_kind, ordinal)
        if person["name"] in used_names:
            person["name"] = f"{person['name']} {ordinal + 1}"
        used_names.add(person["name"])
        people[person_id] = person
    return people


def _organization_kinds(count):
    if count < 2:
        raise ValueError("A generated society requires at least two organizations")
    kinds = ["pirate_fleet"]
    while len(kinds) < count - 1:
        kinds.append("independent_crew")
    kinds.append("naval_command")
    return kinds


def _even_counts(total, buckets):
    counts = [total // buckets] * buckets
    for index in range(total % buckets):
        counts[index] += 1
    return counts


def _ambition_for(manifest, person, organization_kind, entity_id):
    rng = random_stream(manifest, "ambition", entity_id)
    if organization_kind == "naval_command":
        options = ["contain a notorious pirate fleet", "protect a shipping lane", "earn a command promotion"]
        weights = [5 + person["values"]["order"] // 10,
                   4 + person["values"]["family"] // 15,
                   3 + person["values"]["fame"] // 12]
    elif organization_kind == "pirate_fleet":
        options = ["expand fleet influence", "become feared across the lanes", "secure a dependable ally"]
        weights = [4 + person["values"]["freedom"] // 12,
                   3 + person["values"]["fame"] // 10,
                   2 + person["values"]["loyalty"] // 15]
    else:
        options = ["grow wealthy without taking sides", "protect crew independence", "gain a respected name"]
        weights = [3 + person["values"]["wealth"] // 10,
                   3 + person["values"]["freedom"] // 10,
                   2 + person["values"]["fame"] // 12]
    return _weighted_choice(rng, dict(zip(options, weights)))


def _active_goal(manifest, entity_id, ambition, targets, actor_kind):
    rng = random_stream(manifest, "active_goal", entity_id)
    goal_type = _goal_type_for(ambition)
    target_id = targets[rng.randrange(len(targets))] if targets else None
    goal_digest = blake2b(f"{manifest['seed']}\0{actor_kind}\0{entity_id}".encode("utf-8"),
                          digest_size=10, person=b"MiskaNPCGoal").hexdigest()
    return {
        "goal_id": f"goal_{goal_digest}",
        "type": goal_type,
        "target_id": target_id,
        "reason": ambition,
        "priority": rng.randint(45, 90),
        "progress": 0,
        "commitment": rng.randint(45, 90),
        "created_hour": 0,
        "review_hour": 24,
        "status": "active",
        "completed_hour": None,
    }


def _goal_type_for(ambition):
    lowered = ambition.lower()
    if "trade" in lowered or "wealth" in lowered:
        return "trade"
    elif "ally" in lowered or "dependable" in lowered:
        return "negotiate"
    elif "protect" in lowered or "contain" in lowered or "promotion" in lowered:
        return "patrol"
    return "hunt"


def _person_relationship(manifest, source, target):
    rng = random_stream(manifest, "person_relationship", f"{source['person_id']}->{target['person_id']}")
    same_org = source["organization_id"] == target["organization_id"]
    affinity = 12 if same_org else 0
    empathy = (source["personality"]["empathy"] + target["personality"]["empathy"] - 100) // 8
    respect = target["skills"]["leadership"] // 8 + target["skills"]["seamanship"] // 12
    basis = ["shared service"] if same_org else ["different affiliations"]
    if source["background"] == target["background"]:
        basis.append("shared background")
    return {
        "source_id": source["person_id"],
        "target_id": target["person_id"],
        "entity_type": "person",
        "dimensions": {
            "trust": _clamp(rng.randint(18, 68) + affinity + empathy),
            "affection": _clamp(rng.randint(10, 65) + affinity + source["personality"]["sociability"] // 8),
            "respect": _clamp(rng.randint(12, 62) + respect + (10 if same_org else 0)),
            "fear": _clamp(rng.randint(0, 34) + target["reputation"] // 5),
            "grievance": _clamp(rng.randint(0, 24) + (0 if same_org else 5)),
            "obligation": _clamp(rng.randint(0, 16) + (8 if same_org else 0)),
        },
        "basis": basis,
    }


def _organization_relationship(manifest, source, target):
    rng = random_stream(manifest, "organization_relationship", f"{source['organization_id']}->{target['organization_id']}")
    source_leader = source["_leader"]
    target_leader = target["_leader"]
    source_kind, target_kind = source["kind"], target["kind"]
    shared_interests = sum(abs(source["values"][key] - target["values"][key]) <= 20
                           for key in VALUES)
    antagonistic = {source_kind, target_kind} == {"pirate_fleet", "naval_command"}
    basis = ["compatible organizational interests" if shared_interests >= 3 else "different organizational priorities"]
    if antagonistic:
        basis.append("opposed strategic mandates")
    return {
        "source_id": source["organization_id"],
        "target_id": target["organization_id"],
        "entity_type": "organization",
        "dimensions": {
            "trust": _clamp(25 + shared_interests * 7 + rng.randint(-15, 15) - (30 if antagonistic else 0)),
            "affection": _clamp(25 + shared_interests * 5 + rng.randint(-15, 15)),
            "respect": _clamp(30 + target_leader["skills"]["leadership"] // 3 + rng.randint(-12, 12)),
            "fear": _clamp(10 + target["reputation"] // 2 + rng.randint(-10, 10)),
            "grievance": _clamp(rng.randint(10, 30) + (35 if antagonistic else 0)),
            "obligation": _clamp(rng.randint(0, 12)),
        },
        "basis": basis,
    }


def _validate_relationship(row, known_ids):
    if (not isinstance(row, dict) or not isinstance(row.get("source_id"), str)
            or not isinstance(row.get("target_id"), str)
            or row.get("source_id") not in known_ids
            or row.get("target_id") not in known_ids or row["source_id"] == row["target_id"]
            or row.get("entity_type") not in ("person", "organization")):
        raise ValueError("Invalid generated relationship endpoints")
    dimensions = row.get("dimensions")
    if not isinstance(dimensions, dict) or set(dimensions) != set(RELATION_DIMS) or any(
            type(value) is not int or not 0 <= value <= 100 for value in dimensions.values()):
        raise ValueError("Invalid generated relationship dimensions")
    if not isinstance(row.get("basis"), list) or not row["basis"] or any(
            not isinstance(value, str) or not value for value in row["basis"]):
        raise ValueError("Invalid generated relationship basis")
    return True


def generate_society(manifest):
    """Generate a coherent opening cast, organization hierarchy and fleet roster.

    Named people are identities within vessel crew headcounts, not additional
    people. World.new_generated and the generated save/restore paths consume this
    hierarchy and ledger directly.
    """
    from pirates.voyage import SEA_NODES

    manifest = validate_manifest(manifest)
    settings = manifest["settings"]
    org_count = settings["organization_count"]
    vessel_count = settings["vessel_count"]
    people_count = settings["notable_people"]
    kinds = _organization_kinds(org_count)
    people_per_org = _even_counts(people_count, org_count)
    vessels_per_org = _even_counts(vessel_count, org_count)

    org_ids = [stable_id(manifest, "organization", index) for index in range(org_count)]
    vessel_ids = [stable_id(manifest, "vessel", index) for index in range(vessel_count)]
    people = {}
    organizations = {}
    vessels = {}
    used_person_names = set()
    used_organization_names = set()
    used_vessel_names = set()
    person_ordinal = 0
    vessel_ordinal = 0
    node_names = sorted(SEA_NODES)

    for org_index, (org_id, kind) in enumerate(zip(org_ids, kinds)):
        member_ids = []
        for member_index in range(people_per_org[org_index]):
            person_id = stable_id(manifest, "person", person_ordinal)
            person_ordinal += 1
            role = "commander" if kind == "naval_command" and member_index == 0 else (
                "captain" if member_index < vessels_per_org[org_index] else "officer")
            person = generate_person(manifest, person_id, role, kind, person_ordinal - 1)
            if person["name"] in used_person_names:
                person["name"] = f"{person['name']} {person_ordinal}"
            used_person_names.add(person["name"])
            person["organization_id"] = org_id
            person["superior_id"] = None
            person["rank"] = "commander" if kind == "naval_command" and member_index == 0 else (
                "captain" if member_index < vessels_per_org[org_index] else "officer")
            person["ambition"] = _ambition_for(manifest, person, kind, person_id)
            person["aspiration"] = person["ambition"]
            member_ids.append(person_id)
            people[person_id] = person

        leader_id = member_ids[0]
        org_stream = random_stream(manifest, "organization", org_id)
        org_name = f"{org_stream.choice(('Blackwake', 'Cinder', 'Salt', 'Ashen', 'Verdant', 'Iron'))} " \
                   f"{org_stream.choice(('Fleet', 'Company', 'Jackals', 'Corsairs', 'Watch', 'Guard'))}"
        if kind == "naval_command":
            org_name = f"{org_stream.choice(('Royal', 'Coastal', 'Eastern', 'Harbor'))} Marine Command"
        if org_name in used_organization_names:
            org_name = f"{org_name} {org_index + 1}"
        used_organization_names.add(org_name)
        org_vessel_ids = vessel_ids[vessel_ordinal:vessel_ordinal + vessels_per_org[org_index]]
        organizations[org_id] = {
            "organization_id": org_id,
            "name": org_name,
            "kind": kind,
            "leader_id": leader_id,
            "member_ids": list(member_ids),
            "vessel_ids": list(org_vessel_ids),
            "ambition": _ambition_for(manifest, people[leader_id], kind, org_id),
            "values": {key: sum(people[member_id]["values"][key] for member_id in member_ids) // len(member_ids)
                       for key in VALUES},
            "reputation": random_stream(manifest, "org_reputation", org_id).randint(20, 80),
            "cohesion": random_stream(manifest, "org_cohesion", org_id).randint(35, 90),
            "_leader": people[leader_id],
        }
        for member_id in member_ids[1:]:
            people[member_id]["superior_id"] = leader_id

        # Crew members are distributed over the organization's vessels. Named
        # members are included inside crew counts; this layer adds no people.
        vessel_captains = member_ids[:len(org_vessel_ids)]
        for local_index, (vessel_id, captain_id) in enumerate(zip(org_vessel_ids, vessel_captains)):
            vessel_rng = random_stream(manifest, "vessel", vessel_id)
            faction_name = org_name
            if kind == "naval_command":
                mission, threat = "patrol", "marines"
                base_name = vessel_rng.choice(("Sentinel", "Resolve", "Meridian", "Vigilant"))
            elif kind == "pirate_fleet":
                mission, threat = "hunt", "pirates"
                base_name = vessel_rng.choice(("Black Gull", "Ashen Crown", "Widow's Wake", "Reef Wolf"))
            else:
                mission = "trade" if vessel_rng.random() < 0.65 else "hunt"
                threat = "merchant" if mission == "trade" else "pirates"
                base_name = vessel_rng.choice(("Far Lantern", "Swift Current", "Cinder Kite", "Salt Finch"))
            if base_name in used_vessel_names:
                base_name = f"{base_name} {vessel_ordinal + 1}"
            used_vessel_names.add(base_name)
            node = node_names[vessel_rng.randrange(len(node_names))]
            destinations = [other for other in node_names if other != node]
            destination = destinations[vessel_rng.randrange(len(destinations))]
            named_aboard = sum(1 for member_id in member_ids if member_id == captain_id or
                               people[member_id]["rank"] != "captain" and
                               member_ids.index(member_id) % len(org_vessel_ids) == local_index)
            crew = max(named_aboard, 4 + vessel_rng.randint(0, 6))
            vessels[vessel_id] = {
                "id": vessel_id,
                "name": base_name,
                "faction": faction_name,
                "organization_id": org_id,
                "node": node,
                "destination": destination,
                "mission": mission,
                "cargo": {},
                "silver": vessel_rng.randint(60, 400),
                "crew": crew,
                "captives": 0,
                "threat": threat,
                "condition": vessel_rng.randint(70, 100),
                "captain_id": captain_id,
                "member_ids": [member_id for member_id in member_ids
                               if member_id == captain_id or
                               people[member_id]["rank"] != "captain" and
                               member_ids.index(member_id) % len(org_vessel_ids) == local_index],
            }
            for member_id in vessels[vessel_id]["member_ids"]:
                people[member_id]["vessel_id"] = vessel_id
            vessel_ordinal += 1

    for person in people.values():
        person.setdefault("vessel_id", None)

    # Starting ambitions have a concrete organization target and a review
    # horizon. Later phases will make them actionable and advance their state.
    for org_id, organization in organizations.items():
        targets = (node_names if _goal_type_for(organization["ambition"]) == "trade" else
                   [candidate for candidate in org_ids if candidate != org_id])
        organization["active_goal"] = _active_goal(
            manifest, org_id, organization["ambition"], targets, "organization")
    for person_id, person in people.items():
        targets = (node_names if _goal_type_for(person["aspiration"]) == "trade" else
                   [candidate for candidate in org_ids if candidate != person["organization_id"]])
        person["active_goal"] = _active_goal(
            manifest, person_id, person["aspiration"], targets or [person["organization_id"]], "person")

    # Generate a sparse directed web: sociability affects its size, shared
    # service/background affects candidate weights, not guaranteed friendship.
    relationships = []
    for person_id, source in people.items():
        candidates = [target for target_id, target in people.items() if target_id != person_id]
        edge_rng = random_stream(manifest, "social_graph", person_id)
        max_edges = min(len(candidates), 1 + source["personality"]["sociability"] // 20)
        for _ in range(max_edges):
            weights = []
            for target in candidates:
                weight = 2
                if target["organization_id"] == source["organization_id"]:
                    weight += 8
                if target["background"] == source["background"]:
                    weight += 2
                weight += sum(abs(target["values"][key] - source["values"][key]) <= 18 for key in VALUES)
                weights.append(weight)
            pick = edge_rng.randrange(sum(weights))
            for index, weight in enumerate(weights):
                pick -= weight
                if pick < 0:
                    target = candidates.pop(index)
                    break
            relationships.append(_person_relationship(manifest, source, target))

    organization_relationships = []
    relationship_lookup = {}
    for source_id, source in organizations.items():
        for target_id, target in organizations.items():
            if source_id == target_id:
                continue
            row = _organization_relationship(manifest, source, target)
            organization_relationships.append(row)
            relationship_lookup[(source_id, target_id)] = row
    relationships.extend(organization_relationships)

    # Founding treaties are explicit, signed records. Compatibility and mutual
    # trust can create one; a raw score alone never means an alliance.
    agreements = []
    for first_index, first_id in enumerate(org_ids):
        for second_id in org_ids[first_index + 1:]:
            first, second = organizations[first_id], organizations[second_id]
            if "naval_command" in (first["kind"], second["kind"]):
                continue
            forward = relationship_lookup[(first_id, second_id)]["dimensions"]
            reverse = relationship_lookup[(second_id, first_id)]["dimensions"]
            if min(forward["trust"], reverse["trust"]) < 58 or max(
                    forward["grievance"], reverse["grievance"]) > 45:
                continue
            treaty_rng = random_stream(manifest, "founding_agreement", f"{first_id}|{second_id}")
            if treaty_rng.random() >= 0.35:
                continue
            agreements.append({
                "agreement_id": stable_id(manifest, "agreement", len(agreements)),
                "kind": "mutual_aid",
                "participants": [first_id, second_id],
                "signatories": [first["leader_id"], second["leader_id"]],
                "purpose": "mutual protection and shared route information",
                "terms": ["provide aid when able", "share reports of serious threats"],
                "start_hour": 0,
                "expires_hour": 24 * 90,
                "status": "active",
                "basis": "mutual trust and compatible strategic interests at founding",
                "fulfillments": [],
                "shared_reports": [],
            })

    for organization in organizations.values():
        organization.pop("_leader")

    person_ids, organization_id_set = set(people), set(organizations)
    for person in people.values():
        validate_person(person)
        if (person["organization_id"] not in organization_id_set
                or person["vessel_id"] not in vessels
                or person["superior_id"] is not None and person["superior_id"] not in person_ids):
            raise ValueError("Generated person has a dangling hierarchy reference")
    for organization_id, organization in organizations.items():
        if (organization["leader_id"] not in organization["member_ids"]
                or any(member_id not in people or people[member_id]["organization_id"] != organization_id
                       for member_id in organization["member_ids"])
                or any(vessel_id not in vessels or vessels[vessel_id]["organization_id"] != organization_id
                       for vessel_id in organization["vessel_ids"])):
            raise ValueError("Generated organization hierarchy is inconsistent")
    for vessel in vessels.values():
        if (vessel["captain_id"] not in vessel["member_ids"]
                or any(member_id not in people or people[member_id]["vessel_id"] != vessel["id"]
                       for member_id in vessel["member_ids"])
                or vessel["crew"] < len(vessel["member_ids"])):
            raise ValueError("Generated vessel crew assignment is inconsistent")
    for relationship in relationships:
        valid_ids = person_ids if relationship["entity_type"] == "person" else organization_id_set
        _validate_relationship(relationship, valid_ids)
    agreement_ids = set()
    for agreement in agreements:
        participants = agreement["participants"]
        if (agreement["agreement_id"] in agreement_ids or len(participants) != 2
                or participants[0] == participants[1]
                or any(participant not in organization_id_set for participant in participants)
                or len(agreement["signatories"]) != 2
                or any(signatory != organizations[participant]["leader_id"]
                       for participant, signatory in zip(participants, agreement["signatories"]))):
            raise ValueError("Generated founding agreement has invalid participants")
        agreement_ids.add(agreement["agreement_id"])

    # Include an aggregate free population pool so later recruitment can be an
    # explicit transfer. The vessel crews themselves remain the conserved ledger.
    total_population = sum(vessel["crew"] + vessel["captives"] for vessel in vessels.values())
    return {
        "manifest": deepcopy(manifest),
        "people": people,
        "organizations": organizations,
        "vessels": vessels,
        "relationships": relationships,
        "agreements": agreements,
        "population_ledger": {
            "total": total_population,
            "aboard": total_population,
            "unassigned": 0,
        },
    }


def run_prehistory(world):
    """Simulate the generated society's bounded headless opening history.

    The player starts after the configured span, so NPC/news/fleet clocks agree
    with the visible world clock. This uses the same hourly fleet and society
    hooks as live play, but never advances player/crew simulation.
    """
    manifest = validate_manifest(world.generation_manifest)
    days = manifest["settings"]["history_days"]
    if world.npcs.roster_mode != "generated":
        raise ValueError("Prehistory requires a generated NPC roster")
    if not 0 <= days <= 365:
        raise ValueError("Prehistory exceeds the bounded history window")
    start_hour = world.clock.total_ticks // 360000
    end_hour = start_hour + days * 24
    for hour in range(start_hour + 1, end_hour + 1):
        world.clock.total_ticks = hour * 360000
        world.economy.update_day(world.clock.day)
        previous_events = list(world.npcs.events[-80:])
        world.npcs.advance_second(world, world.voyages.position, sighting_enabled=False)
        world.society.advance(world, previous_events)
    world.refresh()
    return end_hour


def validate_person(person):
    """Validate generated character shape and derived values before persistence."""
    if not isinstance(person, dict):
        raise ValueError("Invalid generated person")
    if not isinstance(person.get("person_id"), str) or not person["person_id"]:
        raise ValueError("Invalid generated person identity")
    for key in ("name", "origin", "background", "occupation", "role", "organization_kind"):
        if not isinstance(person.get(key), str) or not person[key]:
            raise ValueError(f"Invalid generated person {key}")
    if type(person.get("age")) is not int or not 18 <= person["age"] <= 80:
        raise ValueError("Invalid generated person age")
    for field, expected in (("attributes", ATTRIBUTES), ("personality", PERSONALITY), ("values", VALUES)):
        values = person.get(field)
        if not isinstance(values, dict) or set(values) != set(expected) or any(
                type(value) is not int or not 0 <= value <= 100 for value in values.values()):
            raise ValueError(f"Invalid generated person {field}")
    xp = person.get("skill_xp")
    skills = person.get("skills")
    if not isinstance(xp, dict) or set(xp) != set(SKILLS) or any(type(value) is not int or not 0 <= value <= 10000 for value in xp.values()):
        raise ValueError("Invalid generated person skill experience")
    if not isinstance(skills, dict) or set(skills) != set(SKILLS) or any(
            type(value) is not int or value != min(100, int(math.sqrt(xp[skill]))) for skill, value in skills.items()):
        raise ValueError("Generated skill levels disagree with experience")
    traits = person.get("traits")
    if not isinstance(traits, list) or len(traits) > 3 or len(set(traits)) != len(traits) or any(
            trait not in TRAITS for trait in traits):
        raise ValueError("Invalid generated person traits")
    if any(trait not in TRAIT_EFFECTS for trait in traits):
        raise ValueError("Generated trait has no strategic effect contract")
    if type(person.get("career_level")) is not int or not 1 <= person["career_level"] <= 20:
        raise ValueError("Invalid generated career level")
    if type(person.get("personal_capability")) is not int or not 0 <= person["personal_capability"] <= 100:
        raise ValueError("Invalid personal capability")
    if type(person.get("command_power")) is not int or not 0 <= person["command_power"] <= 100:
        raise ValueError("Invalid generated command power")
    if type(person.get("reputation")) is not int or not 0 <= person["reputation"] <= 100:
        raise ValueError("Invalid generated reputation")
    return True
