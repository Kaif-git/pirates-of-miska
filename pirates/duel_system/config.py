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
    if config.get("schema_version") in set(range(1, 30)):
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
    if any(isinstance(config["defaults"][key], bool)
           or not isinstance(config["defaults"][key], (int, float))
           or not 0 <= config["defaults"][key] <= 1
           for key in ("anger_tolerance", "pain_tolerance")):
        raise ValueError("Default mental tolerances must be in [0, 1]")
    starting_fear = config["defaults"]["starting_fear"]
    if (isinstance(starting_fear, bool) or not isinstance(starting_fear, (int, float))
            or not math.isfinite(starting_fear)
            or not 0 <= starting_fear <= config["defaults"]["pool_max"]):
        raise ValueError("Default starting fear is outside the fear pool")
    thresholds = config["behavior_thresholds"]
    expected_thresholds = {"mode_enter": 50, "mode_exit": 40,
                           "critical_enter": 100, "critical_exit": 90,
                           "yield_morale_max": 25}
    if (thresholds != expected_thresholds
            or any(type(value) is not int for value in thresholds.values())):
        raise ValueError("Behavior thresholds must match the approved shared levels")
    dispersion = config["accuracy"]["aim_dispersion"]
    if (config["accuracy"]["jitter"] != 0
            or not 0 <= dispersion["clean_min"] <= dispersion["clean_base"] <= dispersion["clean_max"] <= 1
            or dispersion["per_accuracy"] < 0 or dispersion["difficulty_penalty"] < 0
            or not 0 <= dispersion["drift_share"] <= 1
            or set(dispersion["nearby_zones"]) != set(config["zones"]["difficulty"])
            or any(not choices or len(choices) != len(set(choices))
                   or any(zone not in config["zones"]["difficulty"] or zone == aimed
                          for zone in choices)
                   for aimed, choices in dispersion["nearby_zones"].items())):
        raise ValueError("Invalid seeded aim-dispersion tuning")
    if (config["blood"]["graze_initial_loss"] < 0
            or config["blood"]["total_bleed_cap_per_turn"] <= 0
            or not 0 <= config["blood"]["bandage_reduction"] <= 1):
        raise ValueError("Invalid bleeding or one-off graze-loss tuning")
    if any(zone.get("GRAZE", {}).get("bleed", 0) != 0 for zone in config["wounds"].values()):
        raise ValueError("Grazes cannot have ongoing bleed")
    for kind, action in config["actions"].items():
        if (any(value < 0 for key, value in action.items() if key != "damage_type")
                or action.get("damage_type", "CUTTING") not in {"CUTTING", "PIERCING", "BLUNT"}):
            raise ValueError(f"Invalid action tuning in {kind}")
    if not 0 < config["guard"]["riposte_disarm_windup_mult"] <= 1:
        raise ValueError("guard.riposte_disarm_windup_mult must be in (0, 1]")
    if (config["stats"]["fatigue_accuracy"] != 0
            or config["stats"]["pain_accuracy"] != 0
            or config["zones"]["heavy_acc_mod"] != 0
            or config["weapon_integrity"]["eye_accuracy_penalty_per_eye"] != 0
            or config["weapon_integrity"]["eye_accuracy_penalty_max"] != 0
            or any(action.get("acc_mod", 0) != 0 for action in config["actions"].values())
            or any(status.get("accuracy_mult", 1) != 1
                   for zone in config["wounds"].values() for status in zone.values())):
        raise ValueError("Only authored fear/anger may modify effective Accuracy")
    if (not 0 <= config["stats"]["fear_accuracy"] <= 0.75
            or not 0 <= config["stats"]["fear_speed"] <= 0.75
            or not 0 <= config["stats"]["pain_tolerance_suppression"] <= 0.9
            or not 0 <= config["anger"]["acc_penalty"] <= 0.75
            or not 0 <= config["anger"]["balance_penalty"] <= 100
            or any(not 0 <= config["anger"][key] <= 100
                   for key in ("hit_gain", "injury_gain", "bleed_gain"))
            or not 0 <= config["fear"]["hit_gain"] <= 100
            or not 0 <= config["fear"]["pain_gain"] <= 10
            or not 0 <= config["fear"]["bleed_gain_per_turn"] <= 10):
        raise ValueError("Invalid fear, anger or pain-tolerance effects")
    sites = config["anatomy_sites"]
    if (set(sites) != set(config["zones"]["difficulty"])
            or any(not values or len(values) != len(set(values))
                   or any(not isinstance(site, str) or not site for site in values)
                   for values in sites.values())):
        raise ValueError("Anatomical site catalogue must cover each zone with unique labels")
    _validate_profiles(config)
    universal = config["universal_ai"]
    expected_categories = (set(config["ai"]["tie_order"]) - {"YIELD"}
                           | {"FEINT", "SHOVE", "DISENGAGE", "THRUST", "DISARM",
                              "ADRENALINE", "DASH_SLASH", "RISE", "CRAWL"})
    if set(universal["base"]) != expected_categories:
        raise ValueError("universal_ai.base must score every supported NPC candidate category")
    if set(universal["mode_bonus"]) != {
            "protective_guard", "protective_exit", "cautious_guard", "cautious_exit",
            "press_attack", "hot_attack", "break_guard", "break_exit",
            "break_unsafe_trade"}:
        raise ValueError("universal_ai.mode_bonus has invalid dimensions")
    if set(universal["weights"]) != {
            "opening", "counter", "unsafe_slow_attack", "position_per_gap", "stamina_cost",
            "action_duration", "imminent_guard", "unsafe_bandage", "safe_bandage",
            "wait_low_vigor", "shove_unsteady", "shove_open_space", "bleed_pressure",
            "injury_risk", "adrenaline_trigger"}:
        raise ValueError("universal_ai.weights has invalid dimensions")
    if set(universal["tie_break"]) != {"margin", "amplitude"}:
        raise ValueError("universal_ai.tie_break has invalid dimensions")
    for section in ("base", "mode_bonus", "weights", "tie_break"):
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) or value < 0
               for value in universal[section].values()):
            raise ValueError(f"universal_ai.{section} values must be finite and nonnegative")
    susceptibility = config["ronin"]["fear_gain_susceptibility"]
    if (isinstance(susceptibility, bool) or not isinstance(susceptibility, (int, float))
            or not math.isfinite(susceptibility) or not 0.5 <= susceptibility <= 1.5):
        raise ValueError("ronin.fear_gain_susceptibility must be in [0.5, 1.5]")
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
    for name, profile in {"ronin": config["ronin"], **profiles}.items():
        numeric = ("starting_fear", "morale_bias", "composure", "stress_composure",
                   "anger_tolerance", "pain_tolerance")
        if any(isinstance(profile[key], bool) or not isinstance(profile[key], (int, float))
               or not math.isfinite(profile[key]) for key in numeric):
            raise ValueError(f"ai_profiles.{name} numeric response fields must be finite")
        if not 0 <= profile["starting_fear"] <= config["defaults"]["pool_max"]:
            raise ValueError(f"ai_profiles.{name}.starting_fear is outside the fear pool")
        if any(not 0 <= profile[key] <= 1
               for key in ("composure", "stress_composure", "anger_tolerance", "pain_tolerance")):
            raise ValueError(f"ai_profiles.{name} bounded response fields must be in [0, 1]")
        susceptibility = profile["fear_gain_susceptibility"]
        if (isinstance(susceptibility, bool) or not isinstance(susceptibility, (int, float))
                or not math.isfinite(susceptibility) or not 0.5 <= susceptibility <= 1.5):
            raise ValueError(f"ai_profiles.{name}.fear_gain_susceptibility must be in [0.5, 1.5]")
    pressure_weights = config["personality"]["pressure_weights"]
    if (set(pressure_weights) != {"fear", "pain", "nervousness"}
            or abs(sum(pressure_weights.values()) - 1) > 1e-9
            or any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(value) or value < 0 for value in pressure_weights.values())):
        raise ValueError("Shared injury-response pressure weights must be nonnegative and sum to 1")


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
            or injury["shock_susceptibility"] < 0
            or not 0 <= injury["stagger_repeat_duration_mult"] <= 1):
        raise ValueError("Invalid bounded injury-response thresholds")
    if (injury["impact_net_threshold"] < 0 or injury["impact_per_net"] < 0
            or injury["impact_max_stability_loss"] <= 0
            or set(injury["impact_severity_mult"]) != set(injury["shock_by_severity"])
            or set(injury["impact_zone_mult"]) != set(injury["shock_zone_mult"])
            or set(injury["impact_contact_mult"]) != set(damage_types)
            or set(injury["impact_defense_mult"]) != {"OPEN", "DEFENSELESS", "GUARD_HOLD"}
            or injury["impact_severity_mult"]["BRUISE"] != 0
            or any(value < 0 for key in ("impact_severity_mult", "impact_zone_mult",
                                         "impact_contact_mult", "impact_defense_mult")
                   for value in injury[key].values())):
        raise ValueError("Invalid anatomical impact tuning")
    vital = injury["vital_sites"]
    if (vital["penetration_net"] < config["wound_net"]["deep"]
            or vital["heart_fatal_net"] < vital["penetration_net"]
            or vital["organ_bleed_per_turn"] <= 0
            or not vital["qualifying_damage_types"]
            or set(vital["qualifying_damage_types"]) - set(damage_types)
            or injury["bilateral_vision_range"] < 0
            or isinstance(injury["unilateral_vision_range"], bool)
            or not isinstance(injury["unilateral_vision_range"], int)
            or injury["unilateral_vision_range"] <= injury["bilateral_vision_range"]):
        raise ValueError("Invalid vital-site force/contact or bilateral-vision tuning")


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
    if (rule["shove_balance_fraction"] < 0 or rule["shove_unsteady_bonus"] < 0
            or rule["shove_resist_strength_gap"] <= 0
            or rule["shove_guard_brace"] < 0
            or rule["shove_committed_heavy_bonus"] < 0
            or rule["shove_move_margin"] <= 0
            or rule["shove_two_step_margin"] <= rule["shove_move_margin"]
            or rule["shove_min_stability_loss"] <= 0
            or rule["shove_max_stability_loss"] < rule["shove_min_stability_loss"]
            or rule["shove_margin_for_max_loss"] <= 0
            or not 0 <= rule["shove_braced_impact_mult"] <= 1
            or not 0 <= rule["shove_blocked_impact_mult"] <= 1
            or rule["feint_guard_drain"] < 0
            or isinstance(rule["feint_disrupt_ticks"], bool)
            or not isinstance(rule["feint_disrupt_ticks"], int)
            or rule["feint_disrupt_ticks"] <= 0
            or rule["disarm_strength_ratio"] <= 0):
        raise ValueError("Invalid tactical-action contest or effect tuning")
    action_categories = {"DISENGAGE", "FEINT", "SHOVE", "THRUST", "DISARM", "ADRENALINE"}
    score_keys = action_categories | {"SHOVE_UNSTEADY", "SHOVE_OPEN_SPACE", "BANDAGE_BLEEDING",
                                     "BANDAGE_UNSAFE_PENALTY", "WAIT_STABILITY", "BLEED_PATIENCE"}
    if not action_categories <= set(config["actions"]):
        raise ValueError("Missing P5 tactical actions")
    for action in action_categories:
        if config["actions"][action]["stamina"] < 0:
            raise ValueError(f"Negative tactical-action stamina cost in {action}")
    if set(rule["ai_scores"]) != score_keys:
        raise ValueError("tactics.ai_scores must define every P5 action and consequence response")


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


def _legacy_fear_susceptibility(profile):
    """Map old confidence to a bounded, neutral-centered fear-gain multiplier."""
    traits = profile.get("traits", {}) if isinstance(profile, dict) else {}
    confidence = traits.get("confidence", 0.5) if isinstance(traits, dict) else 0.5
    if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        confidence = 0.5
    return round(0.75 + (1 - confidence) * 0.5, 3)


def upgrade_config(config):
    """Migrate persisted combat tuning while preserving compatible authored values."""
    migrated = deepcopy(config)
    version = migrated.get("schema_version")
    if version not in set(range(1, 30)):
        raise ValueError("Unsupported combat configuration version")
    current = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    migrated["schema_version"] = current["schema_version"]
    if version < 30:
        migrated.setdefault("injury", {})["unilateral_vision_range"] = current["injury"]["unilateral_vision_range"]
        migrated.setdefault("wound_net", {})["eye_injury"] = current["wound_net"]["eye_injury"]
    if version <= 16:
        # Schema 17 adds the wrist as an anatomically eligible severe hand site.
        migrated["anatomy_sites"] = deepcopy(current["anatomy_sites"])
    else:
        torso_sites = migrated.setdefault("anatomy_sites", {}).setdefault("TORSO", [])
        for site in ("HEART", "LEFT_LUNG", "RIGHT_LUNG", "LIVER"):
            if site not in torso_sites:
                torso_sites.append(site)
    if version < 18:
        migrated["blood"]["graze_initial_loss"] = current["blood"]["graze_initial_loss"]
        for zone in migrated["wounds"].values():
            if "GRAZE" in zone:
                zone["GRAZE"]["bleed"] = 0
    # Schema 19 replaces the Accuracy threshold/jitter roll with seeded bounded
    # aimed-zone dispersion; legacy jitter no longer changes hit eligibility.
    migrated["accuracy"]["jitter"] = 0
    if version == 1:
        for key in ("ai_profiles", "personality"):
            migrated[key] = deepcopy(current[key])
    if version < 3:
        migrated["injury"] = deepcopy(current["injury"])
    for action, values in current["actions"].items():
        migrated["actions"].setdefault(action, deepcopy(values))
    migrated.setdefault("stability", deepcopy(current["stability"]))
    old_tactics = migrated.get("tactics", {})
    migrated["tactics"] = deepcopy(current["tactics"])
    for key, value in old_tactics.items():
        if key == "shove_stability_loss":
            migrated["tactics"]["shove_max_stability_loss"] = deepcopy(value)
        elif key in migrated["tactics"]:
            migrated["tactics"][key] = deepcopy(value)
    if version < 22:
        # Before schema 22 fear/anger Accuracy effects were prohibited and
        # shipped neutral. Adopt the newly approved authored tuning defaults.
        migrated["stats"]["fear_accuracy"] = current["stats"]["fear_accuracy"]
        migrated["stats"]["fear_speed"] = current["stats"]["fear_speed"]
        migrated["anger"]["acc_penalty"] = current["anger"]["acc_penalty"]
        migrated["anger"]["balance_penalty"] = current["anger"]["balance_penalty"]
    if version < 23:
        # Adopt the reviewed injury/bleed starter rates when the persisted
        # value is the prior shipped default; preserve deliberate custom rates.
        previous = {
            "TORSO": {"CUT": 3, "DEEP": 6},
            "HEAD": {"CUT": 3, "DEEP": 6},
            "EYE": {"CUT": 3, "DEEP": 6},
            "LEG": {"CUT": 2, "DEEP": 4},
            "ARM": {"CUT": 2, "DEEP": 4},
            "HAND": {"CUT": 2, "DEEP": 4, "SEVERED": 8},
        }
        for zone, severities in previous.items():
            for severity, old_rate in severities.items():
                wound = migrated.get("wounds", {}).get(zone, {}).get(severity)
                if isinstance(wound, dict) and wound.get("bleed") == old_rate:
                    wound["bleed"] = current["wounds"][zone][severity]["bleed"]
    # Schema 26 moves fear-response variation into one explicit numeric gain
    # susceptibility. Preserve existing confidence as deterministic migration input.
    for profile in migrated.get("ai_profiles", {}).values():
        if isinstance(profile, dict) and "fear_gain_susceptibility" not in profile:
            profile["fear_gain_susceptibility"] = _legacy_fear_susceptibility(profile)
    legacy_default_profile = migrated.get("ronin")
    if isinstance(legacy_default_profile, dict) and "fear_gain_susceptibility" not in legacy_default_profile:
        legacy_default_profile["fear_gain_susceptibility"] = _legacy_fear_susceptibility(
            legacy_default_profile)
    def clean_profile(profile, fallback):
        traits = profile.get("traits", {}) if isinstance(profile, dict) else {}
        confidence = traits.get("confidence", 0.5) if isinstance(traits, dict) else 0.5
        if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                or not math.isfinite(confidence)):
            confidence = 0.5
        confidence = min(1, max(0, confidence))
        return {
            "fear_gain_susceptibility": profile.get("fear_gain_susceptibility",
                                                      _legacy_fear_susceptibility(profile)),
            "starting_fear": profile.get("starting_fear", 100 * (1 - confidence)),
            "morale_bias": profile.get("morale_bias", profile.get(
                "courage", fallback.get("morale_bias", 0))),
            "composure": profile.get("composure", fallback.get("composure", 1)),
            "stress_composure": profile.get("stress_composure", traits.get(
                "discipline", fallback.get("stress_composure", 0.5))),
            "anger_tolerance": profile.get("anger_tolerance", fallback.get("anger_tolerance", 0.5)),
            "pain_tolerance": profile.get("pain_tolerance", fallback.get("pain_tolerance", 0.5)),
        }
    old_ronin = migrated.get("ronin", {})
    new_ronin = clean_profile(old_ronin, current["ronin"])
    migrated["ronin"] = {"archetype": old_ronin.get("archetype", "ronin"), **new_ronin}
    migrated["ai_profiles"] = {
        name: clean_profile(profile, current["ai_profiles"].get(name, new_ronin))
        for name, profile in migrated.get("ai_profiles", {}).items()
    }
    legacy_default_confidence = migrated["defaults"].get("confidence", 1)
    if (isinstance(legacy_default_confidence, bool)
            or not isinstance(legacy_default_confidence, (int, float))
            or not math.isfinite(legacy_default_confidence)):
        legacy_default_confidence = 0.5
    migrated["defaults"].setdefault(
        "starting_fear", 100 * (1 - min(1, max(0, legacy_default_confidence))))
    migrated["defaults"].pop("confidence", None)
    migrated["ai"] = deepcopy(current["ai"])
    migrated["personality"] = deepcopy(current["personality"])
    migrated["adrenaline"] = deepcopy(current["adrenaline"])
    _fill_missing(migrated, current)
    # Keep authored Accuracy permanent while only the approved fear/anger
    # modifiers affect effective Accuracy. Neutralize unrelated legacy knobs.
    for key in ("fatigue_accuracy", "pain_accuracy"):
        migrated["stats"][key] = 0
    migrated["zones"]["heavy_acc_mod"] = 0
    migrated["weapon_integrity"]["eye_accuracy_penalty_per_eye"] = 0
    migrated["weapon_integrity"]["eye_accuracy_penalty_max"] = 0
    for action in migrated["actions"].values():
        if "acc_mod" in action:
            action["acc_mod"] = 0
    for zone in migrated["wounds"].values():
        for wound in zone.values():
            if isinstance(wound, dict) and "accuracy_mult" in wound:
                wound.pop("accuracy_mult")
    return validate_config(migrated)


def load_config(path=None):
    reference = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    config = json.loads(Path(path).read_text(encoding="utf-8")) if path else deepcopy(reference)
    if config.get("schema_version") in set(range(1, 30)):
        return upgrade_config(config)
    return validate_config(config, reference)
