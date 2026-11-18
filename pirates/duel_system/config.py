"""Single numeric source of truth for the specification combat engine."""
from copy import deepcopy
import json
import math
from pathlib import Path


CONFIG_PATH = Path(__file__).resolve().parents[2] / "content" / "duel_system.json"


def _validate_shape(value, reference, location="config"):
    if isinstance(reference, dict):
        if not isinstance(value, dict) or set(value) != set(reference):
            raise ValueError(f"{location}: missing or unknown configuration keys")
        for key in reference:
            _validate_shape(value[key], reference[key], f"{location}.{key}")
    elif isinstance(reference, bool):
        if not isinstance(value, bool):
            raise ValueError(f"{location}: expected boolean")
    elif isinstance(reference, (int, float)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{location}: expected finite number")
    elif isinstance(reference, list):
        if not isinstance(value, list) or len(value) != len(reference):
            raise ValueError(f"{location}: unexpected list shape")
        for index, (item, template) in enumerate(zip(value, reference)):
            _validate_shape(item, template, f"{location}[{index}]")
    elif reference is None:
        if value is not None:
            raise ValueError(f"{location}: expected null")
    elif not isinstance(value, str):
        raise ValueError(f"{location}: expected text")


def validate_config(config, reference=None):
    if reference is None:
        reference = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if config.get("schema_version") in {1, 2, 3, 4, 5, 6}:
        return upgrade_config(config)
    _validate_shape(config, reference)
    if config["schema_version"] != reference["schema_version"]:
        raise ValueError("Unsupported combat configuration version")
    for group, key in (("time", "ticks_per_turn"), ("time", "speed_reference"),
                       ("time", "min_phase_ticks"), ("fatigue", "range"),
                       ("fatigue", "endurance_divisor"), ("stamina", "pool_per_stat"),
                       ("defaults", "pool_max")):
        if config[group][key] <= 0:
            raise ValueError(f"{group}.{key} must be positive")
    if config["accuracy"]["jitter"] < 0:
        raise ValueError("accuracy.jitter cannot be negative")
    for kind, action in config["actions"].items():
        if any(value < 0 for value in action.values()):
            raise ValueError(f"Negative action value in {kind}")
    _validate_profiles(config)
    _validate_injury(config)
    _validate_stability(config)
    _validate_tactics(config)
    _validate_adrenaline(config)
    _validate_weapon_integrity(config)
    group = config["group"]
    if (isinstance(group["guard_exchange_ticks"], bool)
            or not isinstance(group["guard_exchange_ticks"], int)
            or group["guard_exchange_ticks"] <= 0
            or isinstance(group["vision_range"], bool) or not isinstance(group["vision_range"], int)
            or group["vision_range"] <= 0
            or isinstance(group["hearing_range"], bool) or not isinstance(group["hearing_range"], int)
            or group["hearing_range"] < 0
            or isinstance(group["contact_memory_exchanges"], bool)
            or not isinstance(group["contact_memory_exchanges"], int)
            or group["contact_memory_exchanges"] < 0
            or isinstance(group["threat_summary_count"], bool)
            or not isinstance(group["threat_summary_count"], int)
            or group["threat_summary_count"] <= 0
            or not 0 <= group["coordination_discipline_threshold"] <= 1
            or set(group["witnessed_morale"]) != {
                "ally_wounded", "ally_incapacitated", "ally_withdrew", "leader_lost", "failed_rush"}
            or any(value > 0 for value in group["witnessed_morale"].values())):
        raise ValueError("Invalid group visibility, exchange, memory or UI tuning")
    return config


def _validate_profiles(config):
    roster = config["personality"]["roster"]
    profiles = config["ai_profiles"]
    if not roster or len(set(roster)) != len(roster) or any(name not in profiles for name in roster):
        raise ValueError("personality.roster must contain unique configured profiles")
    trait_names = {"aggression", "discipline", "confidence", "patience", "adaptability"}
    categories = set(config["ai"]["tie_order"])
    for name, profile in profiles.items():
        if set(profile["traits"]) != trait_names:
            raise ValueError(f"ai_profiles.{name}.traits has invalid dimensions")
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) or not 0 <= value <= 1
               for value in profile["traits"].values()):
            raise ValueError(f"ai_profiles.{name}.trait values must be in [0, 1]")
        if set(profile["score_bias"]) != categories:
            raise ValueError(f"ai_profiles.{name}.score_bias must cover legal categories")
        if not 0 <= profile["composure"] <= 1 or profile["reaction_delay"] < 0:
            raise ValueError(f"ai_profiles.{name} has invalid composure/reaction delay")
    personality = config["personality"]
    if abs(sum(personality["pressure_weights"].values()) - 1) > 1e-9:
        raise ValueError("personality.pressure_weights must sum to 1")
    if personality["variation"]["margin"] < 0 or personality["variation"]["amplitude"] < 0:
        raise ValueError("personality variation must be nonnegative")
    adaptation = personality["adaptation"]
    if adaptation["history"] <= 0 or adaptation["minimum_counters"] < 1:
        raise ValueError("personality adaptation history/threshold must be positive")
    if min(adaptation["counter_penalty"], adaptation["success_decay"], adaptation["max_penalty"]) < 0:
        raise ValueError("personality adaptation scores must be nonnegative")


def _validate_injury(config):
    injury = config["injury"]
    damage_types = injury["damage_types"]
    if set(damage_types) != {"CUTTING", "PIERCING", "BLUNT"}:
        raise ValueError("injury.damage_types must define cutting, piercing and blunt contact")
    for name, values in damage_types.items():
        if values["bleed_mult"] < 0 or values["shock_mult"] < 0:
            raise ValueError(f"injury.damage_types.{name} multipliers must be nonnegative")
    if set(injury["shock_by_severity"]) != {"BRUISE", "GRAZE", "CUT", "DEEP", "SEVERED"}:
        raise ValueError("injury.shock_by_severity must define all wound severities")
    if set(injury["shock_zone_mult"]) != {"HEAD", "EYE", "TORSO", "ARM", "HAND", "LEG"}:
        raise ValueError("injury.shock_zone_mult must define all target zones")
    if (injury["flinch_threshold"] < 0 or injury["recoil_threshold"] <= injury["flinch_threshold"]
            or injury["recoil_distance"] != 1 or injury["recoil_cooldown_ticks"] <= 0
            or injury["shock_susceptibility"] < 0):
        raise ValueError("Invalid bounded injury-response thresholds")


def _validate_weapon_integrity(config):
    rule = config["weapon_integrity"]
    if (set(rule["hard_contact_wear"]) != {"PARRY", "HEAVY_BLOCK"}
            or any(value <= 0 for value in rule["hard_contact_wear"].values())):
        raise ValueError("weapon_integrity must define positive hard-contact wear")
    if (set(rule["materials"]) != {"WOOD", "IRON", "STEEL"}
            or any(values["wear_mult"] <= 0 for values in rule["materials"].values())):
        raise ValueError("weapon_integrity material resistance is invalid")
    if (not 0 <= rule["edge_loss_at_zero"] <= 1
            or not 0 <= rule["eye_accuracy_penalty_per_eye"] <= rule["eye_accuracy_penalty_max"] <= 1
            or not rule["eye_damage_contact_types"]
            or set(rule["eye_damage_contact_types"]) - {"CUTTING", "PIERCING"}
            or isinstance(rule["pickup_range"], bool) or not isinstance(rule["pickup_range"], int)
            or rule["pickup_range"] < 0):
        raise ValueError("weapon_integrity eye/edge values are invalid")
    weapon = config["gear"]["weapon"]
    if (not weapon["name"] or weapon["material"] not in rule["materials"]
            or not 0 <= weapon["condition"] <= 100):
        raise ValueError("Invalid default weapon identity or condition")
    if (config["wound_net"]["eye_injury"] <= 0
            or config["wound_net"]["decapitation"] <= config["wound_net"]["unconscious_head"]):
        raise ValueError("Severe eye/decapitation thresholds must be positive and ordered")


def _validate_stability(config):
    rule = config["stability"]
    if (rule["start"] != 100 or rule["reference_balance"] <= 0
            or rule["loss_min_mult"] <= 0 or rule["loss_max_mult"] < rule["loss_min_mult"]
            or rule["overextension_heavy_whiff"] <= 0 or rule["sharp_turn_loss"] <= 0
            or not 0 < rule["sharp_turn_min_degrees"] <= 180
            or rule["fast_turn_window_ticks"] <= 0
            or not 0 < rule["off_balance_threshold"] < rule["stable_threshold"] < rule["start"]
            or not 0 < rule["rise_stability"] < rule["off_balance_threshold"]
            or rule["wait_recovery_per_tick"] <= 0):
        raise ValueError("Invalid stability thresholds, loss limits or recovery values")
    if set(rule["leg_wound_penalty"]) != {"BRUISE", "GRAZE", "CUT", "DEEP", "SEVERED"}:
        raise ValueError("stability.leg_wound_penalty must define all severities")
    if set(rule["grounded_ai_scores"]) != {"RISE", "GUARD", "CRAWL"}:
        raise ValueError("stability.grounded_ai_scores must define Rise, cover and crawl")
    if any(value < 0 for value in (*rule["leg_wound_penalty"].values(),
                                   *rule["grounded_ai_scores"].values(),
                                   rule["grounded_cover_bonus"],
                                   rule["grounded_crawl_bonus"])):
        raise ValueError("Stability penalties and grounded AI scores must be nonnegative")
    if rule["grounded_crawl_gap"] < 0:
        raise ValueError("Grounded crawl gap must be nonnegative")


def _validate_tactics(config):
    rule = config["tactics"]
    if (rule["shove_balance_fraction"] < 0 or rule["shove_guard_brace"] < 0
            or rule["shove_stability_loss"] <= 0 or rule["feint_guard_drain"] < 0
            or rule["disarm_strength_ratio"] <= 0):
        raise ValueError("Invalid tactical-action contest or effect tuning")
    expected = {"DISENGAGE", "FEINT", "SHOVE", "THRUST", "DISARM", "ADRENALINE"}
    if not expected <= set(config["actions"]):
        raise ValueError("Missing P5 tactical actions")
    for action in expected:
        if config["actions"][action]["stamina"] < 0:
            raise ValueError(f"Negative tactical-action stamina cost in {action}")
    if set(rule["ai_scores"]) != expected:
        raise ValueError("tactics.ai_scores must define every P5 action")


def _validate_adrenaline(config):
    rule = config["adrenaline"]
    pool = config["defaults"]["pool_max"]
    for key in ("trigger_blood_max", "trigger_pain_min", "trigger_fatigue_min"):
        if not 0 <= rule[key] <= pool:
            raise ValueError(f"adrenaline.{key} must be within the combat pool")
    if not 0 <= rule["trigger_stability_max"] <= 100:
        raise ValueError("adrenaline.trigger_stability_max must be in [0, 100]")
    if (isinstance(rule["duration_ticks"], bool) or not isinstance(rule["duration_ticks"], int)
            or rule["duration_ticks"] <= 0 or not 0 <= rule["pain_suppression"] <= 0.75
            or rule["fatigue_debt"] <= 0 or "ADRENALINE" not in config["actions"]):
        raise ValueError("Invalid adrenaline duration, suppression, debt or action tuning")


def _fill_missing(target, defaults):
    for key, value in defaults.items():
        if key not in target:
            target[key] = deepcopy(value)
        elif isinstance(value, dict) and isinstance(target[key], dict):
            _fill_missing(target[key], value)


def upgrade_config(config):
    """Migrate persisted v1-v6 combat tuning while preserving authored values."""
    migrated = deepcopy(config)
    version = migrated.get("schema_version")
    if version not in {1, 2, 3, 4, 5, 6}:
        raise ValueError("Unsupported combat configuration version")
    current = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    migrated["schema_version"] = current["schema_version"]
    if version == 1:
        for key in ("ai_profiles", "personality"):
            migrated[key] = deepcopy(current[key])
        legacy_brute = current["ai_profiles"]["brute"]
        migrated["ronin"].setdefault("traits", deepcopy(legacy_brute["traits"]))
        migrated["ronin"].setdefault("score_bias", deepcopy(legacy_brute["score_bias"]))
    if version < 3:
        migrated["injury"] = deepcopy(current["injury"])
    for action, values in current["actions"].items():
        migrated["actions"].setdefault(action, deepcopy(values))
    migrated.setdefault("stability", deepcopy(current["stability"]))
    migrated["tactics"] = deepcopy(current["tactics"])
    migrated["adrenaline"] = deepcopy(current["adrenaline"])
    _fill_missing(migrated, current)
    return validate_config(migrated)


def load_config(path=None):
    reference = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    config = json.loads(Path(path).read_text(encoding="utf-8")) if path else deepcopy(reference)
    if config.get("schema_version") in {1, 2, 3, 4, 5, 6}:
        return upgrade_config(config)
    return validate_config(config, reference)
