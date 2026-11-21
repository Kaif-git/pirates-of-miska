"""Exposure, deterministic zone drift, wound classification and strike resolution."""
from dataclasses import dataclass

from .model import ActionKind, EventType, Severity, Status, Wound, Zone, wound_structure
from .geometry import attack_contains, attack_reach
from .stats import clamp, effective_stats, weapon_edge


@dataclass(frozen=True)
class DefenseSnapshot:
    defense: str
    phase: str
    hand_exposed: bool
    guard_ready_tick: int | None = None
    parry_window: int = 0


def defense_snapshot(state, fighter):
    action = fighter.action
    phase = action.phase_at(state.tick) if action else "IDLE"
    if state.tick < fighter.stagger_until:
        return DefenseSnapshot("OPEN", "STAGGER", True)
    if action and phase in {"GUARD_RAISE", "GUARD_HOLD"}:
        return DefenseSnapshot("GUARD_HOLD", phase, True,
                               action.guard_ready_tick, action.parry_window)
    moving = phase in {"MOVE", "DASH"}
    return DefenseSnapshot("DEFENSELESS" if moving else "OPEN", phase,
                           phase in {"WINDUP", "ACTIVE", "RECOVERY"})


def resolve_zone(state, attacker, defender, aimed, snap, action=None, *, trace=None):
    action = action or attacker.action
    cfg = state.config
    aimed_zone = Zone(aimed)
    accuracy = effective_stats(attacker, cfg, state.tick).accuracy
    rule = cfg["accuracy"]["aim_dispersion"]
    absent_site = ((aimed_zone == Zone.EYE and defender.permanent_injuries.eyes_damaged >= 2)
                   or (aimed_zone == Zone.HAND and not _has_eligible_hand(defender)))
    if absent_site:
        fallback = Zone(cfg["zones"]["fallback"][aimed_zone.value])
        if trace is not None:
            trace.update(accuracy=accuracy, aimed_zone=aimed_zone.value,
                         clean_probability=0, dispersion_roll=None, drift_roll=None,
                         resolved_zone=fallback.value, hand_exposed=snap.hand_exposed,
                         drifted=True, absent_site=True)
        return fallback, True
    difficulty = cfg["zones"]["difficulty"][aimed_zone.value]
    torso_difficulty = cfg["zones"]["difficulty"][Zone.TORSO.value]
    clean_probability = clamp(
        rule["clean_base"] + accuracy * rule["per_accuracy"]
        - max(0, difficulty-torso_difficulty) * rule["difficulty_penalty"],
        rule["clean_min"], rule["clean_max"])
    if aimed_zone == Zone.HAND and not snap.hand_exposed:
        # A covered hand is not a legal precise contact; preserve the longstanding
        # adjacent-arm fallback instead of turning the whole action into a miss.
        resolved_zone, drifted = Zone.ARM, True
        if trace is not None:
            trace.update(accuracy=accuracy, aimed_zone=aimed_zone.value,
                         clean_probability=0, dispersion_roll=None, drift_roll=None,
                         resolved_zone=resolved_zone.value, hand_exposed=False,
                         drifted=True)
        return resolved_zone, drifted
    dispersion_roll = state.uniform(0, 1)
    if dispersion_roll < clean_probability:
        resolved_zone, drifted = aimed_zone, False
        drift_roll = None
    else:
        drift_roll = state.uniform(0, 1)
        candidates = []
        for value in rule["nearby_zones"][aimed_zone.value]:
            candidate = Zone(value)
            if candidate == Zone.HAND and (not snap.hand_exposed or not _has_eligible_hand(defender)):
                continue
            if candidate == Zone.EYE and defender.permanent_injuries.eyes_damaged >= 2:
                continue
            candidates.append(candidate)
        if candidates and drift_roll < rule["drift_share"]:
            index = min(len(candidates)-1, int(state.uniform(0, len(candidates))))
            resolved_zone, drifted = candidates[index], True
        else:
            resolved_zone, drifted = None, True
    if trace is not None:
        trace.update(accuracy=accuracy, aimed_zone=aimed_zone.value,
                     clean_probability=clean_probability, dispersion_roll=dispersion_roll,
                     drift_roll=drift_roll,
                     resolved_zone=resolved_zone.value if resolved_zone else None,
                     hand_exposed=snap.hand_exposed, drifted=drifted)
    return resolved_zone, drifted


def _has_eligible_hand(fighter):
    severed = set(fighter.permanent_injuries.severed_hands)
    if fighter.hand_severed and not severed:
        severed.add(fighter.weapon_hand)
    return len(severed) < 2


def classify_wound(config, zone, net, kind, drifted, damage_type="CUTTING", *, site=None):
    thresholds = config["wound_net"]
    if net <= 0:
        return Severity.BRUISE
    if (zone == Zone.HAND and net >= thresholds["sever"] and kind == ActionKind.HEAVY
            and not drifted and str(damage_type).upper() == "CUTTING"
            and (site is None or site == "WRIST")):
        return Severity.SEVERED
    if (zone == Zone.HEAD and net >= thresholds["sever"] and kind == ActionKind.HEAVY
            and not drifted and str(damage_type).upper() == "CUTTING" and site == "EAR"):
        return Severity.SEVERED
    if net >= thresholds["deep"]:
        return Severity.DEEP
    if net >= thresholds["cut"][0]:
        return Severity.CUT
    return Severity.GRAZE


def injury_number_context(config, zone, kind, damage_type, net, *, drifted, site, severity,
                          side=None, eye_trauma=0):
    """Store the real classification thresholds relevant to this resolved contact."""
    zone, kind = Zone(zone), ActionKind(kind)
    rules = config["wound_net"]
    bands = {
        "graze_min": rules["graze"][0],
        "cut_min": rules["cut"][0],
        "deep_min": rules["deep"],
    }
    special = []
    damage_type = str(damage_type).upper()
    if (zone == Zone.HAND and kind == ActionKind.HEAVY and not drifted
            and damage_type == "CUTTING" and site == "WRIST"):
        special.append({"label": "wrist severance", "net_threshold": rules["sever"]})
    if (zone == Zone.EYE and not drifted and damage_type in config["weapon_integrity"]["eye_damage_contact_types"]
            and Severity(severity) in {Severity.CUT, Severity.DEEP, Severity.SEVERED}):
        special.append({"label": "lasting eye injury", "net_threshold": rules["eye_injury"],
                        "trauma_current": eye_trauma})
    if zone == Zone.HEAD:
        special.append({"label": "unconsciousness", "net_threshold": rules["unconscious_head"]})
        if kind == ActionKind.HEAVY and not drifted and damage_type == "CUTTING" and site == "NECK":
            special.append({"label": "decapitation", "net_threshold": rules["decapitation"]})
        if kind == ActionKind.HEAVY and not drifted and damage_type == "CUTTING" and site == "EAR":
            special.append({"label": "permanent ear loss", "net_threshold": rules["sever"]})
    if zone == Zone.LEG and kind == ActionKind.HEAVY:
        special.append({"label": "crippled leg", "net_threshold": rules["crippled_leg"]})
    vital = config["injury"]["vital_sites"]
    is_lung = site == "LUNG" and side in {"LEFT", "RIGHT"}
    if (zone == Zone.TORSO and (site in {"HEART", "LIVER"} or is_lung)
            and damage_type in vital["qualifying_damage_types"]):
        special.append({"label": f"{(side.lower() + ' ' if is_lung else '')}{site.lower()} penetration",
                        "net_threshold": vital["penetration_net"]})
        if site == "HEART":
            special.append({"label": "fatal heart injury", "net_threshold": vital["heart_fatal_net"]})
    standard = [("cut", rules["cut"][0]), ("deep", rules["deep"])]
    next_consequence = [(label, threshold) for label, threshold in standard if threshold > net]
    next_consequence.extend((row["label"], row["net_threshold"])
                            for row in special if row["net_threshold"] > net)
    label, threshold = min(next_consequence, key=lambda row: row[1]) if next_consequence else (None, None)
    return {
        "bands": bands,
        "special": special,
        "next_label": label,
        "next_net_threshold": threshold,
        "net_to_next": max(0, threshold - net) if threshold is not None else None,
    }


def injury_profile(config, zone, severity, kind, damage_type, net, *, site=None, side=None,
                   weapon_hand=None):
    """Return deterministic coarse anatomy and the current function consequence."""
    zone, severity, kind = Zone(zone), Severity(severity), ActionKind(kind)
    damage_type = str(damage_type).upper()
    depth = {
        Severity.BRUISE: "CONTUSION", Severity.GRAZE: "SUPERFICIAL",
        Severity.CUT: "MUSCLE", Severity.DEEP: "DEEP",
        Severity.SEVERED: "STRUCTURAL",
    }[severity]
    structure = wound_structure(zone, severity, damage_type)
    vital = config["injury"]["vital_sites"]
    is_lung = site == "LUNG" and side in {"LEFT", "RIGHT"}
    vital_qualified = (zone == Zone.TORSO
                       and (site in {"HEART", "LIVER"} or is_lung)
                       and severity in {Severity.DEEP, Severity.SEVERED}
                       and damage_type in vital["qualifying_damage_types"]
                       and net >= vital["penetration_net"])
    vital_fatal = vital_qualified and site == "HEART" and net >= vital["heart_fatal_net"]
    vital_outcome = None
    bleed_per_turn = None
    if vital_qualified:
        structure = "HEART" if site == "HEART" else "LUNG" if is_lung else "LIVER"
        bleed_per_turn = 0 if vital_fatal else vital["organ_bleed_per_turn"]
    if zone == Zone.HEAD and severity in {Severity.DEEP, Severity.SEVERED}:
        if site == "NECK":
            structure = "NECK"
        elif site == "SKULL":
            structure = "SKULL"
        elif site == "EAR":
            structure = "EAR"
        elif site in {"JAW", "NOSE"}:
            structure = "FACIAL_BONE"
    if (zone == Zone.LEG and net >= config["wound_net"]["crippled_leg"]
            and kind == ActionKind.HEAVY and severity == Severity.DEEP):
        structure, depth = "BONE", "STRUCTURAL"
    elif zone == Zone.LEG and site in {"FOOT", "ANKLE"}:
        if severity == Severity.DEEP:
            structure = f"{site}_TENDON"
        elif severity == Severity.CUT:
            structure = f"{site}_SOFT_TISSUE"
    effect = config["wounds"][zone.value].get(severity.value, {})
    function = effect.get("status")
    if effect.get("disarm"):
        function = "disarmed"
    if zone == Zone.HAND and severity == Severity.SEVERED:
        function = ("weapon hand severed; cannot hold a weapon"
                    if side is None or weapon_hand is None or side == weapon_hand
                    else "supporting hand severed; one-handed weapon remains usable")
    if zone == Zone.HEAD and net >= config["wound_net"]["unconscious_head"]:
        function = "unconscious"
    if zone == Zone.HEAD and site == "EAR" and severity == Severity.SEVERED:
        function = "permanent ear loss; side-specific hearing not modeled"
        if net >= config["wound_net"]["unconscious_head"]:
            function += "; unconscious from head impact"
    elif zone == Zone.LEG and net >= config["wound_net"]["crippled_leg"] and kind == ActionKind.HEAVY:
        function = "crippled"
    elif zone == Zone.LEG and site in {"FOOT", "ANKLE"} and severity == Severity.DEEP:
        function = "impaired footing; reduced speed and no dash"
    elif zone == Zone.LEG and site in {"FOOT", "ANKLE"} and severity == Severity.CUT:
        function = "painful footing; reduced speed"
    if vital_qualified:
        if vital_fatal:
            function, vital_outcome = "fatal qualified heart injury", "HEART_FATAL"
        elif site == "HEART":
            function, vital_outcome = "major heart bleeding", "HEART_BLEED"
        elif is_lung:
            function, vital_outcome = "major lung bleeding; winded", "LUNG_BLEED"
        else:
            function, vital_outcome = "major liver bleeding", "LIVER_BLEED"
    return {"site": site or zone.value, "side": side,
            "depth": depth, "structure": structure,
            "function": function or "bleeding and pain only", "damage_type": damage_type,
            "vital_qualified": vital_qualified, "vital_outcome": vital_outcome,
            "vital_fatal": vital_fatal, "vital_bleed_per_turn": bleed_per_turn}


def choose_anatomical_site(state, zone, *, severity=None, defender=None):
    """Choose a seeded, physically eligible subsite within the aimed broad zone."""
    options = state.config["anatomy_sites"][Zone(zone).value]
    if Zone(zone) == Zone.HAND and severity == Severity.SEVERED:
        options = [item for item in options if item.endswith("_WRIST")]
    if defender is not None and Zone(zone) == Zone.HAND:
        severed = set(defender.permanent_injuries.severed_hands)
        if defender.hand_severed and not severed:
            severed.add(defender.weapon_hand)
        options = [item for item in options
                   if not any(item.startswith(side + "_") for side in severed)]
    if defender is not None and Zone(zone) == Zone.HEAD:
        severed_ears = set(defender.permanent_injuries.severed_ears)
        options = [item for item in options
                   if not any(item == f"{side}_EAR" for side in severed_ears)]
    if defender is not None and Zone(zone) == Zone.EYE and defender.permanent_injuries.eyes_damaged >= 2:
        options = []
    elif defender is not None and Zone(zone) == Zone.EYE and defender.permanent_injuries.eyes_damaged:
        damaged_sites = {f"{wound.side}_EYE" for wound in defender.wounds
                         if wound.zone == Zone.EYE and wound.side in {"LEFT", "RIGHT"}}
        options = [item for item in options if item not in damaged_sites]
    if not options:
        raise ValueError("No anatomical site is eligible for this injury severity")
    index = min(len(options) - 1, int(state.uniform(0, len(options))))
    label = options[index]
    side = None
    for candidate in ("LEFT", "RIGHT"):
        prefix = candidate + "_"
        if label.startswith(prefix):
            side = candidate
            label = label[len(prefix):]
            break
    return label, side


def impact_stability_request(config, zone, severity, damage_type, net, defense):
    """Bound post-armor physical impact, independent of composure and reflex shock."""
    rule = config["injury"]
    net_force = max(0.0, net - rule["impact_net_threshold"])
    request = (net_force * rule["impact_per_net"]
               * rule["impact_severity_mult"][Severity(severity).value]
               * rule["impact_zone_mult"][Zone(zone).value]
               * rule["impact_contact_mult"][str(damage_type).upper()]
               * rule["impact_defense_mult"][defense])
    return min(rule["impact_max_stability_loss"], request)


def injury_shock(state, defender, zone, severity, damage_type):
    """Derive a bounded reflex response; it never changes structural wound effects."""
    from .ai import decision_control
    rule = state.config["injury"]
    control = decision_control(state, defender.id)
    load = (rule["shock_by_severity"][Severity(severity).value]
            * rule["shock_zone_mult"][Zone(zone).value]
            * rule["damage_types"][str(damage_type).upper()]["shock_mult"]
            * (1 + rule["shock_susceptibility"] * (1 - control)))
    response = ("HOLD" if load < rule["flinch_threshold"] else
                "FLINCH" if load < rule["recoil_threshold"] else "RECOIL")
    return load, control, response


def apply_wound(state, attacker, defender, zone, severity, power, net, damage_type="CUTTING", *,
                allow_disarm=True, drifted=False, site=None, side=None):
    from .mental import change_anger, change_fear
    from .stats import pain_burden
    cfg = state.config
    cap = cfg["defaults"]["pool_max"]
    data = cfg["wounds"][zone.value].get(severity.value, {})
    contact = cfg["injury"]["damage_types"][str(damage_type).upper()]
    if site is None:
        site, side = choose_anatomical_site(state, zone, severity=severity, defender=defender)
    profile = injury_profile(cfg, zone, severity,
                             attacker.action.kind if attacker.action else ActionKind.LIGHT,
                             damage_type, net, site=site, side=side,
                             weapon_hand=defender.weapon_hand)
    graze_loss = (cfg["blood"]["graze_initial_loss"] * contact["bleed_mult"]
                  if severity == Severity.GRAZE else 0)
    initial_loss = min(defender.blood, graze_loss)
    bleed = 0 if severity == Severity.GRAZE else data.get("bleed", 0) * contact["bleed_mult"]
    if profile["vital_qualified"]:
        bleed = profile["vital_bleed_per_turn"]
    wound = Wound(zone, severity, bleed, initial_blood_loss=initial_loss,
                  depth=profile["depth"],
                  structure=profile["structure"], damage_type=profile["damage_type"],
                  site=site, side=side, vital_outcome=profile["vital_outcome"])
    if initial_loss > 0:
        before = defender.blood
        defender.blood = max(0, defender.blood - initial_loss)
        from .mental import blood_morale
        blood_morale(state, defender)
        state.emit(EventType.POOL_CHANGE, defender.id,
                   deltas={"blood": defender.blood - before,
                           "initial_blood_loss": before - defender.blood,
                           "source": "GRAZE", "site": site, "side": side},
                   cause_tags=("GRAZE_BLOOD_LOSS",))
    if profile["vital_fatal"]:
        before = defender.blood
        defender.blood = 0
        from .mental import blood_morale
        blood_morale(state, defender)
        state.emit(EventType.POOL_CHANGE, defender.id,
                   deltas={"blood": -before, "source": "HEART_FATAL", "site": site,
                           "net_after_armor": net},
                   cause_tags=("QUALIFIED_VITAL_INJURY", "HEART_FATAL"))
    if severity != Severity.BRUISE:
        defender.wounds.append(wound)
        state.emit(EventType.WOUND, attacker.id, defender.id, zone, severity,
                    deltas={"bleed_per_turn": bleed,
                            "initial_blood_loss": initial_loss, **profile})
    status_name = data.get("status")
    if status_name:
        until = state.tick + data["duration"] if "duration" in data else None
        defender.statuses.append(Status(status_name, until, data.get("speed_mult", 1),
                                        data.get("strength_mult", 1), data.get("accuracy_mult", 1),
                                        data.get("parry_penalty", 0), data.get("no_dash", False)))
        state.emit(EventType.STATUS, defender.id, deltas={"status": status_name, "until_tick": until})
    from .engine import drop_weapon
    weapon_hand_lost = False
    if data.get("disarm") and allow_disarm and not (
            zone == Zone.HAND and severity == Severity.SEVERED
            and side in {"LEFT", "RIGHT"} and side != defender.weapon_hand):
        drop_weapon(state, defender, "HAND_WOUND")
    if zone == Zone.HAND and severity == Severity.SEVERED:
        if side in {"LEFT", "RIGHT"} and side not in defender.permanent_injuries.severed_hands:
            defender.permanent_injuries.severed_hands.append(side)
        weapon_hand_lost = side not in {"LEFT", "RIGHT"} or side == defender.weapon_hand
        if weapon_hand_lost:
            defender.hand_severed = True
            drop_weapon(state, defender, "HAND_SEVERED")
        state.emit(EventType.SEVER, attacker.id, defender.id, zone, severity,
                   deltas={"site": site, "side": side,
                           "weapon_hand_lost": weapon_hand_lost,
                           "weapon_hand": defender.weapon_hand})
    if zone == Zone.HEAD and site == "EAR" and severity == Severity.SEVERED:
        if side in {"LEFT", "RIGHT"} and side not in defender.permanent_injuries.severed_ears:
            defender.permanent_injuries.severed_ears.append(side)
        state.emit(EventType.SEVER, attacker.id, defender.id, zone, severity,
                   deltas={"site": "EAR", "side": side, "ear_lost": True},
                   cause_tags=("PERMANENT_EAR_LOSS",))
    if (zone == Zone.EYE and not drifted
            and severity in {Severity.CUT, Severity.DEEP, Severity.SEVERED}
            and str(damage_type).upper() in cfg["weapon_integrity"]["eye_damage_contact_types"]):
        threshold = cfg["wound_net"]["eye_injury"]
        trauma_before = defender.permanent_injuries.eye_trauma
        trauma_total = trauma_before + net
        injury_qualifies = net >= threshold or trauma_total >= threshold
        before = defender.permanent_injuries.eyes_damaged
        if injury_qualifies and before < 2:
            defender.permanent_injuries.eyes_damaged = before + 1
            defender.permanent_injuries.eye_trauma = max(0, trauma_total - threshold)
            state.emit(EventType.EYE_INJURY, attacker.id, defender.id, zone, severity,
                       deltas={"eyes_damaged_before": before,
                                "eyes_damaged_after": before + 1, "net_after_armor": net,
                                "eye_trauma_before": trauma_before,
                                "eye_trauma_after": defender.permanent_injuries.eye_trauma,
                                "eye_trauma_threshold": threshold,
                                "cumulative_contact": net < threshold,
                                "damage_type": str(damage_type).upper()},
                       cause_tags=("CLEAN_EYE_CONTACT", "CUMULATIVE_EYE_TRAUMA",
                                   "PERMANENT_INJURY"))
        elif before < 2:
            defender.permanent_injuries.eye_trauma = trauma_total
    action = attacker.action
    if (zone == Zone.HEAD and not drifted
            and action and action.kind == ActionKind.HEAVY
            and site == "NECK"
            and str(damage_type).upper() == "CUTTING"
            and net >= cfg["wound_net"]["decapitation"]):
        defender.decapitated = True
        defender.blood = 0
        state.emit(EventType.DECAPITATION, attacker.id, defender.id, zone, severity,
                   deltas={"net_after_armor": net,
                           "required_net": cfg["wound_net"]["decapitation"],
                           "damage_type": str(damage_type).upper()},
                   cause_tags=("CLEAN_HEAVY_CUTTING_HEAD_HIT", "DEATH"))
    defender.stamina = max(0, defender.stamina - data.get("stamina_loss", 0))
    if "stagger" in data:
        repeated = state.tick < defender.stagger_until
        repeat_mult = cfg["injury"]["stagger_repeat_duration_mult"] if repeated else 1
        defender.stagger_until = max(defender.stagger_until,
                                     state.tick + data["stagger"] * repeat_mult)
        state.pending_interrupts.append(defender.id)
        state.emit(EventType.STAGGER, defender.id,
                   deltas={"until_tick": defender.stagger_until, "repeated": repeated,
                           "repeat_duration_mult": repeat_mult})
    if zone == Zone.HEAD and net >= cfg["wound_net"]["unconscious_head"] and not defender.decapitated:
        defender.unconscious = True
        state.emit(EventType.INCAPACITATED, attacker.id, defender.id, zone, severity,
                   deltas={"reason": "UNCONSCIOUS", "cause": "HEAD_IMPACT"},
                   cause_tags=("HEAD_IMPACT", "UNCONSCIOUS"))
    if (zone == Zone.LEG and net >= cfg["wound_net"]["crippled_leg"]
            and attacker.action and attacker.action.kind == ActionKind.HEAVY):
        defender.unconscious = True
        defender.statuses.append(Status("crippled", no_dash=True))
        state.emit(EventType.INCAPACITATED, attacker.id, defender.id, zone, severity,
                   deltas={"reason": "UNCONSCIOUS", "cause": "CRIPPLED_LEG"},
                   cause_tags=("CRIPPLED_LEG", "UNCONSCIOUS"))
    pain = min(cfg["pain"]["bruise_cap"], cfg["pain"]["bruise_power"] * power) if severity == Severity.BRUISE else cfg["pain"][severity.value]
    pain *= cfg["pain"]["zone_mult"].get(zone.value, 1)
    defender.pain = clamp(defender.pain + pain, 0, cap)
    end_eff = effective_stats(defender, cfg, state.tick).endurance
    defender.fatigue = clamp(defender.fatigue + pain * cfg["fatigue"]["pain_spike"]
                             * max(0, cfg["fatigue"]["gain_base"] - end_eff / cfg["fatigue"]["endurance_divisor"]), 0, cap)
    pain_pressure = pain_burden(defender, cfg)
    anger_gain = cfg["anger"]["hit_gain"] + cfg["anger"]["pain_gain"] * pain_pressure
    if severity != Severity.BRUISE:
        anger_gain += cfg["anger"]["injury_gain"]
    change_anger(state, defender, anger_gain, "HIT_INJURY_PAIN")
    change_fear(state, defender,
                cfg["fear"]["hit_gain"] + cfg["fear"]["pain_gain"] * pain_pressure
                + bleed * cfg["fear"]["bleed_gain_per_turn"], "HIT_PAIN_BLEED")
    morale_key = {Severity.CUT: "cut", Severity.DEEP: "deep", Severity.SEVERED: "sever"}.get(severity)
    if morale_key:
        defender.morale = clamp(defender.morale + cfg["morale"][morale_key], 0, cap)
    return pain


def resolve_pending_injury_responses(state):
    """Apply at most one collision-safe reflex displacement per fighter per tick."""
    pending, state.pending_injury_responses = state.pending_injury_responses, []
    strike_positions = {fighter.id: fighter.pos for fighter in state.fighters}
    by_fighter = {}
    for reaction in pending:
        if reaction["response"] == "HOLD":
            continue
        current = by_fighter.get(reaction["fighter_id"])
        if current is None or reaction["shock_load"] > current["shock_load"]:
            by_fighter[reaction["fighter_id"]] = reaction
    for fighter_id, reaction in sorted(by_fighter.items()):
        fighter = next(item for item in state.fighters if item.id == fighter_id)
        attacker = next((item for item in state.fighters if item.id == reaction["attacker_id"]), None)
        response = reaction["response"]
        deltas = {"response": response, "shock_load": reaction["shock_load"],
                  "decision_control": reaction["decision_control"],
                  "depth": reaction["depth"], "structure": reaction["structure"]}
        tags = ["INJURY_SHOCK", "SHOCK_" + response]
        if response in {"FLINCH", "RECOIL"}:
            rule = state.config["injury"]
            if fighter.last_recoil_tick is not None and state.tick-fighter.last_recoil_tick < rule["recoil_cooldown_ticks"]:
                deltas.update(response="REFRACTORY", next_recoil_tick=fighter.last_recoil_tick + rule["recoil_cooldown_ticks"])
                tags = ["INJURY_SHOCK", "RECOIL_REFRACTORY"]
            elif fighter.grounded or fighter.unconscious or fighter.decapitated or not state.combat_active(fighter):
                deltas["response"] = "NO_REFLEX"
                tags = ["INJURY_SHOCK", "REFLEX_UNAVAILABLE"]
            elif attacker is None:
                deltas["response"] = "BRACED"
                deltas["blocker"] = "UNKNOWN_ATTACKER"
                tags = ["INJURY_SHOCK", "RECOIL_BLOCKED", "INJURY_REFLEX_BLOCKED"]
            else:
                origin, attacker_origin = strike_positions[fighter.id], strike_positions[attacker.id]
                dx = (origin[0] > attacker_origin[0]) - (origin[0] < attacker_origin[0])
                dy = (origin[1] > attacker_origin[1]) - (origin[1] < attacker_origin[1])
                destination = (origin[0] + dx * rule["recoil_distance"],
                               origin[1] + dy * rule["recoil_distance"])
                if (dx, dy) == (0, 0):
                    deltas.update(response="BRACED", blocker="NO_DIRECTION")
                    tags = ["INJURY_SHOCK", "RECOIL_BLOCKED", "INJURY_REFLEX_BLOCKED"]
                else:
                    from .forced_movement import resolve_forced_movement
                    request = {"actor": attacker, "target": fighter, "direction": (dx, dy),
                               "steps": rule["recoil_distance"], "cause_tag": "INJURY_REFLEX"}
                    resolve_forced_movement(state, [request])
                    if not request["actual"]:
                        deltas.update(response="BRACED", blocker=request.get("blocker", "BLOCKED"),
                                      position=destination)
                        tags = ["INJURY_SHOCK", "RECOIL_BLOCKED", "INJURY_REFLEX_BLOCKED"]
                    else:
                        before = origin
                        destination = fighter.pos
                        fighter.last_recoil_tick = state.tick
                        deltas.update(from_pos=before, to_pos=destination)
                        tags.extend(("RECOIL_MOVED", "INJURY_REFLEX_MOVED"))
                        from .stability import interrupt_unresolved_action
                        interrupt_unresolved_action(state, fighter)
        state.emit(EventType.INJURY_RESPONSE, fighter.id, reaction["attacker_id"],
                   reaction["zone"], reaction["severity"], deltas=deltas, cause_tags=tuple(tags))


def resolve_strike(state, attacker, defender, snap):
    from .mental import change_anger
    action, cfg = attacker.action, state.config
    weapon = attacker.equipment.weapon
    reach = attack_reach(action.kind)
    distance = max(abs(attacker.pos[0] - defender.pos[0]), abs(attacker.pos[1] - defender.pos[1]))
    if distance > reach:
        attacker.last_strike_whiffed = True
        change_anger(state, attacker, cfg["anger"]["whiff"], "OWN_WHIFF")
        state.emit(EventType.WHIFF, attacker.id, defender.id,
                   deltas={"distance": max(abs(attacker.pos[0]-defender.pos[0]), abs(attacker.pos[1]-defender.pos[1])),
                             "reach": reach, "kind": action.kind.value}, cause_tags=("OUT_OF_REACH",))
        if action.kind == ActionKind.HEAVY:
            from .stability import apply_stability_loss
            apply_stability_loss(state, attacker, "OVEREXTENDED_HEAVY_WHIFF",
                                 cfg["stability"]["overextension_heavy_whiff"],
                                 details={"distance": max(abs(attacker.pos[0]-defender.pos[0]),
                                                          abs(attacker.pos[1]-defender.pos[1])),
                                            "reach": reach})
        if action.kind == ActionKind.DISARM:
            state.emit(EventType.DISARM, attacker.id, defender.id, action.aim,
                       deltas={"outcome": "OUT_OF_REACH", "drifted": False},
                       cause_tags=("DISARM_RESISTED",))
        return
    pile_contact = (distance == 0 and (attacker.grounded or defender.grounded))
    if not pile_contact and not attack_contains(
            attacker.pos, defender.pos, action.attack_bearing, action.kind):
        attacker.last_strike_whiffed = True
        change_anger(state, attacker, cfg["anger"]["whiff"], "OWN_WHIFF")
        state.emit(EventType.WHIFF, attacker.id, defender.id,
                   deltas={"distance": distance, "reach": reach,
                           "kind": action.kind.value,
                           "bearing": action.attack_bearing,
                           "position": defender.pos},
                   cause_tags=("OUTSIDE_LOCKED_ATTACK_GEOMETRY",))
        if action.kind == ActionKind.HEAVY:
            from .stability import apply_stability_loss
            apply_stability_loss(state, attacker, "OVEREXTENDED_HEAVY_WHIFF",
                                 cfg["stability"]["overextension_heavy_whiff"],
                                 details={"distance": distance, "reach": reach,
                                          "bearing": action.attack_bearing})
        if action.kind == ActionKind.DISARM:
            state.emit(EventType.DISARM, attacker.id, defender.id, action.aim,
                       deltas={"outcome": "OUTSIDE_LOCKED_ATTACK_GEOMETRY", "drifted": False},
                       cause_tags=("DISARM_RESISTED", "OUTSIDE_LOCKED_ATTACK_GEOMETRY"))
        return
    obstruction = state.line_blocker(attacker, defender)
    if obstruction:
        blocker_id, position = obstruction
        blocker = next((fighter for fighter in state.fighters if fighter.id == blocker_id), None)
        tag = ("ARENA_BLOCKED_LINE" if blocker_id == "ARENA_BLOCKED" else
               "ALLY_BLOCKED_LINE" if blocker and blocker.team_id == attacker.team_id else
               "BODY_BLOCKED_LINE")
        attacker.last_strike_whiffed = True
        change_anger(state, attacker, cfg["anger"]["whiff"], "OWN_WHIFF")
        state.emit(EventType.WHIFF, attacker.id, defender.id,
                   deltas={"distance": max(abs(attacker.pos[0]-defender.pos[0]),
                                             abs(attacker.pos[1]-defender.pos[1])),
                            "reach": reach, "blocker": blocker_id, "position": position,
                            "kind": action.kind.value},
                   cause_tags=(tag,))
        if action.kind == ActionKind.DISARM:
            state.emit(EventType.DISARM, attacker.id, defender.id, action.aim,
                       deltas={"outcome": "BLOCKED_LINE", "drifted": False,
                               "blocker": blocker_id}, cause_tags=("DISARM_RESISTED", tag))
        return
    trace = {}
    zone, drifted = resolve_zone(state, attacker, defender, action.aim, snap, action, trace=trace)
    if zone is None:
        attacker.last_strike_whiffed = True
        change_anger(state, attacker, cfg["anger"]["whiff"], "OWN_WHIFF")
        state.emit(EventType.WHIFF, attacker.id, defender.id,
                   deltas={"distance": distance, "reach": reach,
                           "kind": action.kind.value, **trace},
                   cause_tags=("AIM_DISPERSION_MISS",))
        if action.kind == ActionKind.DISARM:
            state.emit(EventType.DISARM, attacker.id, defender.id, action.aim,
                       deltas={"outcome": "AIM_DISPERSION_MISS", "drifted": True,
                               "clean_probability": trace.get("clean_probability")},
                    cause_tags=("DISARM_RESISTED", "AIM_DISPERSION_MISS"))
        return
    if zone == Zone.EYE and defender.permanent_injuries.eyes_damaged >= 2:
        # Defend against stale/legacy aim results even if resolve_zone's
        # eligibility filter was bypassed by a saved or externally built state.
        fallback = Zone(cfg["zones"]["fallback"][zone.value])
        trace.update(resolved_zone=fallback.value, absent_site=True)
        zone, drifted = fallback, True
    from .guard import try_guard
    if try_guard(state, attacker, defender, zone, snap):
        if action.kind == ActionKind.DISARM:
            state.emit(EventType.DISARM, attacker.id, defender.id, zone,
                       deltas={"outcome": "PARRIED_OR_BLOCKED", "drifted": drifted},
                       cause_tags=("DISARM_RESISTED",))
        return
    live = effective_stats(attacker, cfg, state.tick)
    power = live.strength * (cfg["gear"]["unarmed_power"] if attacker.disarmed else action.power_mult)
    power += weapon_edge(attacker, cfg)
    if snap.phase == "STAGGER":
        power *= 1 + cfg["guard"]["clean_power_bonus"]
    armor = defender.equipment.armor.get(zone)
    armor_value = armor.value if armor else 0
    net = power - armor_value
    eye_trauma_before_hit = defender.permanent_injuries.eye_trauma
    eyes_damaged_before_hit = defender.permanent_injuries.eyes_damaged
    damage_type = cfg["actions"][action.kind.value].get(
        "damage_type", weapon.damage_type if weapon is not None else "BLUNT")
    try:
        site, side = choose_anatomical_site(state, zone, defender=defender)
    except ValueError as error:
        if str(error) not in {
                "No anatomical site is eligible for this injury",
                "No anatomical site is eligible for this injury severity"}:
            raise
        attacker.last_strike_whiffed = True
        change_anger(state, attacker, cfg["anger"]["whiff"], "OWN_WHIFF")
        state.emit(EventType.WHIFF, attacker.id, defender.id,
                   deltas={"distance": distance, "reach": reach,
                           "kind": action.kind.value, "zone": zone.value,
                           "reason": "NO_ELIGIBLE_ANATOMICAL_SITE"},
                   cause_tags=("NO_ELIGIBLE_ANATOMICAL_SITE",))
        return
    severity = classify_wound(cfg, zone, net, action.kind, drifted, damage_type, site=site)
    profile = injury_profile(cfg, zone, severity, action.kind, damage_type, net,
                             site=site, side=side, weapon_hand=defender.weapon_hand)
    disarm_result = None
    if action.kind == ActionKind.DISARM:
        attacker_strength = effective_stats(attacker, cfg, state.tick).strength
        defender_strength = effective_stats(defender, cfg, state.tick).strength
        won = zone == Zone.HAND and not drifted and attacker_strength > (
            defender_strength * cfg["tactics"]["disarm_strength_ratio"])
        profile["function"] = "disarmed" if won else "grip held; no disarm"
        disarm_result = {"outcome": "DISARMED" if won else "GRIP_HELD",
                         "attacker_strength": attacker_strength,
                         "defender_strength": defender_strength,
                         "required_ratio": cfg["tactics"]["disarm_strength_ratio"],
                         "drifted": drifted}
    pain = apply_wound(state, attacker, defender, zone, severity, power, net, damage_type,
                       allow_disarm=action.kind != ActionKind.DISARM, drifted=drifted,
                       site=site, side=side)
    if disarm_result and disarm_result["outcome"] == "DISARMED":
        from .engine import drop_weapon
        drop_weapon(state, defender, "DISARM")
    shock_load, control, shock_response = injury_shock(state, defender, zone, severity, damage_type)
    if shock_response != "HOLD":
        state.pending_injury_responses.append({
            "fighter_id": defender.id, "attacker_id": attacker.id,
            "zone": zone.value, "severity": severity.value,
            "depth": profile["depth"], "structure": profile["structure"],
            "shock_load": shock_load, "decision_control": control,
            "response": shock_response,
        })
    tags = []
    if snap.defense == "GUARD_HOLD":
        tags.append("GUARD_BYPASSED")
    if drifted:
        tags.append("ZONE_DRIFT")
    if snap.defense == "DEFENSELESS" and snap.phase == "MOVE":
        tags.append("WALK_DEFENSELESS")
    if (snap.phase == "WINDUP" and defender.action and defender.action.kind == ActionKind.HEAVY
            and action.kind in {ActionKind.LIGHT, ActionKind.DASH_LIGHT, ActionKind.DASH_SLASH}):
        tags.append("LIGHT_INTERRUPTS_HEAVY")
    if snap.phase == "RECOVERY":
        tags.append("LIGHT_CATCHES_RECOVERY")
    if shock_response != "HOLD":
        tags.append("SHOCK_" + shock_response)
    impact_requested = impact_stability_request(cfg, zone, severity, damage_type, net, snap.defense)
    impact_applied = 0
    if impact_requested > 0:
        from .stability import apply_stability_loss
        impact_applied = apply_stability_loss(
            state, defender, "HIT_IMPACT", impact_requested,
            details={"attacker": attacker.id, "zone": zone.value,
                     "severity": severity.value, "damage_type": damage_type,
                     "net": net, "defense": snap.defense,
                     "requested_loss": impact_requested})
        if impact_applied > 0:
            tags.append("HIT_IMPACT")
    if defender.action and snap.phase != "RECOVERY" and (snap.phase in {"WINDUP", "DASH", "RISE"} or defender.action.kind in {
             ActionKind.BANDAGE, ActionKind.EAT, ActionKind.DRINK, ActionKind.PICKUP,
              ActionKind.DASH, ActionKind.DASH_LIGHT, ActionKind.DASH_SLASH, ActionKind.SHOVE,
             ActionKind.CRAWL, ActionKind.RISE, ActionKind.ADRENALINE}):
        if severity != Severity.BRUISE:
            state.pending_interrupts.append(defender.id)
    attacker.morale = min(cfg["defaults"]["pool_max"], attacker.morale + cfg["morale"]["land_hit"])
    attacker.last_strike_whiffed = False
    attacker.hits_without_return = 0
    defender.hits_without_return += 1
    if defender.hits_without_return % cfg["anger"]["no_return_count"] == 0:
        change_anger(state, defender, cfg["anger"]["no_return_hits"], "UNANSWERED_HITS")
    attacker.last_hit_tick = state.tick
    attacker.first_exchange_tick = attacker.first_exchange_tick if attacker.first_exchange_tick is not None else state.tick
    defender.first_exchange_tick = defender.first_exchange_tick if defender.first_exchange_tick is not None else state.tick
    wound_event = next((event for event in reversed(state.event_log)
                        if event.type == EventType.WOUND and event.actor == attacker.id
                        and event.target == defender.id and event.tick == state.tick
                        and event.zone == zone and event.severity == severity), None)
    wound_effect = cfg["wounds"][zone.value].get(severity.value, {})
    numeric_effects = {
        key: wound_effect[key] for key in ("strength_mult", "speed_mult", "accuracy_mult", "parry_penalty")
        if key in wound_effect and wound_effect[key] != (0 if key == "parry_penalty" else 1)
    }
    if wound_effect.get("duration"):
        numeric_effects["duration_ticks"] = wound_effect["duration"]
    numbers = injury_number_context(
        cfg, zone, action.kind, damage_type, net, drifted=drifted, site=site,
        side=side, severity=severity,
        eye_trauma=eye_trauma_before_hit)
    state.emit(EventType.HIT, attacker.id, defender.id, zone, severity,
                deltas={"power": power, "armor_applied": armor_value,
                        "armor_present": armor is not None, "armor_zone": zone.value,
                        "wound_force_unit": "points",
                        "net": net, "pain": pain, "drifted": drifted,
                        "damage_type": damage_type, "depth": profile["depth"],
                         "structure": profile["structure"], "function": profile["function"],
                          "site": profile["site"], "side": profile["side"],
                         "vital_outcome": profile["vital_outcome"],
                        "injury_numbers": numbers,
                        "numeric_effects": numeric_effects,
                        "bleed_per_turn": wound_event.deltas.get("bleed_per_turn", 0) if wound_event else 0,
                        "initial_blood_loss": wound_event.deltas.get("initial_blood_loss", 0) if wound_event else 0,
                         "eye_trauma_after": (defender.permanent_injuries.eye_trauma
                                              if zone == Zone.EYE else None),
                         "eye_trauma_before": (eye_trauma_before_hit
                                              if zone == Zone.EYE else None),
                          "eyes_damaged_before": (eyes_damaged_before_hit
                                                  if zone == Zone.EYE else None),
                        "eyes_damaged_after": (defender.permanent_injuries.eyes_damaged
                                               if zone == Zone.EYE else None),
                        "shock_load": shock_load, "decision_control": control,
                        "shock_response": shock_response,
                        "impact_requested": impact_requested,
                        "impact_applied": impact_applied,
                       "strength": live.strength, "armor": armor.value if armor else 0,
                       "power_mult": cfg["gear"]["unarmed_power"] if attacker.disarmed else action.power_mult,
                         "weapon_edge": weapon_edge(attacker, cfg),
                        "defender_phase": snap.phase, **trace}, cause_tags=tags)
    if disarm_result is not None:
        won = disarm_result["outcome"] == "DISARMED"
        state.emit(EventType.DISARM, attacker.id, defender.id, zone,
                   deltas=disarm_result,
                   cause_tags=("RIPOSTE_DISARM" if won else "DISARM_RESISTED",))
