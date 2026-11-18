"""Exposure, deterministic zone drift, wound classification and strike resolution."""
from dataclasses import dataclass

from .model import ActionKind, EventType, Severity, Status, Wound, Zone, wound_structure
from .stats import clamp, effective_stats, weapon_edge, weapon_reach


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
    zone, drifted = Zone(aimed), False
    accuracy = effective_stats(attacker, cfg, state.tick).accuracy
    effective_accuracy = accuracy
    jitter = cfg["accuracy"]["jitter"]
    if jitter:
        accuracy += state.uniform(-jitter, jitter)
    while True:
        extra = cfg["zones"]["guard_hand_extra"] if zone == Zone.HAND and snap.phase == "GUARD_HOLD" else 0
        difficulty = cfg["zones"]["difficulty"][zone.value] + (action.acc_mod if action else 0) + extra
        if trace is not None and not trace:
            trace.update(accuracy=effective_accuracy, rolled_accuracy=accuracy,
                         required_accuracy=difficulty, aimed_zone=Zone(aimed).value,
                         hand_exposed=snap.hand_exposed)
        if zone == Zone.TORSO:
            return zone, drifted
        if (zone != Zone.HAND or snap.hand_exposed) and accuracy >= difficulty:
            return zone, drifted
        zone = Zone(cfg["zones"]["fallback"][zone.value])
        drifted = True


def classify_wound(config, zone, net, kind, drifted, damage_type="CUTTING"):
    thresholds = config["wound_net"]
    if net <= 0:
        return Severity.BRUISE
    if (zone == Zone.HAND and net >= thresholds["sever"] and kind == ActionKind.HEAVY
            and not drifted and str(damage_type).upper() == "CUTTING"):
        return Severity.SEVERED
    if net >= thresholds["deep"]:
        return Severity.DEEP
    if net >= thresholds["cut"][0]:
        return Severity.CUT
    return Severity.GRAZE


def injury_profile(config, zone, severity, kind, damage_type, net):
    """Return deterministic coarse anatomy and the current function consequence."""
    zone, severity, kind = Zone(zone), Severity(severity), ActionKind(kind)
    damage_type = str(damage_type).upper()
    depth = {
        Severity.BRUISE: "CONTUSION", Severity.GRAZE: "SUPERFICIAL",
        Severity.CUT: "MUSCLE", Severity.DEEP: "DEEP",
        Severity.SEVERED: "STRUCTURAL",
    }[severity]
    structure = wound_structure(zone, severity, damage_type)
    if (zone == Zone.LEG and net >= config["wound_net"]["crippled_leg"]
            and kind == ActionKind.HEAVY and severity == Severity.DEEP):
        structure, depth = "BONE", "STRUCTURAL"
    effect = config["wounds"][zone.value].get(severity.value, {})
    function = effect.get("status")
    if effect.get("disarm"):
        function = "disarmed"
    if zone == Zone.HEAD and net >= config["wound_net"]["unconscious_head"]:
        function = "unconscious"
    elif zone == Zone.LEG and net >= config["wound_net"]["crippled_leg"] and kind == ActionKind.HEAVY:
        function = "crippled"
    return {"depth": depth, "structure": structure,
            "function": function or "bleeding and pain only", "damage_type": damage_type}


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
                allow_disarm=True, drifted=False):
    from .mental import change_anger
    cfg = state.config
    cap = cfg["defaults"]["pool_max"]
    data = cfg["wounds"][zone.value].get(severity.value, {})
    contact = cfg["injury"]["damage_types"][str(damage_type).upper()]
    profile = injury_profile(cfg, zone, severity,
                             attacker.action.kind if attacker.action else ActionKind.LIGHT,
                             damage_type, net)
    bleed = data.get("bleed", 0) * contact["bleed_mult"]
    wound = Wound(zone, severity, bleed, depth=profile["depth"],
                  structure=profile["structure"], damage_type=profile["damage_type"])
    if severity != Severity.BRUISE:
        defender.wounds.append(wound)
        state.emit(EventType.WOUND, attacker.id, defender.id, zone, severity,
                   deltas={"bleed_per_turn": bleed, **profile})
    status_name = data.get("status")
    if status_name:
        until = state.tick + data["duration"] if "duration" in data else None
        defender.statuses.append(Status(status_name, until, data.get("speed_mult", 1),
                                        data.get("strength_mult", 1), data.get("accuracy_mult", 1),
                                        data.get("parry_penalty", 0), data.get("no_dash", False)))
        state.emit(EventType.STATUS, defender.id, deltas={"status": status_name, "until_tick": until})
    from .engine import drop_weapon
    if data.get("disarm") and allow_disarm:
        drop_weapon(state, defender, "HAND_WOUND")
    if zone == Zone.HAND and severity == Severity.SEVERED:
        defender.hand_severed = True
        drop_weapon(state, defender, "HAND_SEVERED")
        state.emit(EventType.SEVER, attacker.id, defender.id, zone, severity)
    if (zone == Zone.EYE and not drifted
            and net >= cfg["wound_net"]["eye_injury"]
            and str(damage_type).upper() in cfg["weapon_integrity"]["eye_damage_contact_types"]):
        before = defender.permanent_injuries.eyes_damaged
        after = min(2, before + 1)
        defender.permanent_injuries.eyes_damaged = after
        penalty = min(cfg["weapon_integrity"]["eye_accuracy_penalty_max"],
                      after * cfg["weapon_integrity"]["eye_accuracy_penalty_per_eye"])
        if after > before:
            state.emit(EventType.EYE_INJURY, attacker.id, defender.id, zone, severity,
                       deltas={"eyes_damaged_before": before,
                               "eyes_damaged_after": after,
                               "accuracy_penalty": penalty, "net_after_armor": net,
                               "damage_type": str(damage_type).upper()},
                       cause_tags=("CLEAN_EYE_CONTACT", "PERMANENT_INJURY"))
    action = attacker.action
    if (zone == Zone.HEAD and not drifted
            and action and action.kind == ActionKind.HEAVY
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
        defender.stagger_until = max(defender.stagger_until, state.tick + data["stagger"])
        state.pending_interrupts.append(defender.id)
        state.emit(EventType.STAGGER, defender.id, deltas={"until_tick": defender.stagger_until})
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
    change_anger(state, defender, cfg["anger"]["pain_gain"] * pain, "HIT_PAIN")
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
        if response == "RECOIL":
            rule = state.config["injury"]
            if fighter.last_recoil_tick is not None and state.tick-fighter.last_recoil_tick < rule["recoil_cooldown_ticks"]:
                deltas.update(response="REFRACTORY", next_recoil_tick=fighter.last_recoil_tick + rule["recoil_cooldown_ticks"])
                tags = ["INJURY_SHOCK", "RECOIL_REFRACTORY"]
            elif not fighter.can_act(state.tick):
                deltas["response"] = "NO_REFLEX"
                tags = ["INJURY_SHOCK", "REFLEX_UNAVAILABLE"]
            elif attacker is None:
                deltas["response"] = "BRACED"
                deltas["blocker"] = "UNKNOWN_ATTACKER"
                tags = ["INJURY_SHOCK", "RECOIL_BLOCKED"]
            else:
                origin, attacker_origin = strike_positions[fighter.id], strike_positions[attacker.id]
                dx = (origin[0] > attacker_origin[0]) - (origin[0] < attacker_origin[0])
                dy = (origin[1] > attacker_origin[1]) - (origin[1] < attacker_origin[1])
                destination = (origin[0] + dx * rule["recoil_distance"],
                               origin[1] + dy * rule["recoil_distance"])
                blocker = next((other for other in state.fighters if other.id != fighter.id
                                and other.pos == destination and other.blood > 0 and not other.fled), None)
                if (dx, dy) == (0, 0):
                    deltas.update(response="BRACED", blocker="NO_DIRECTION")
                elif state.walkable is not None and destination not in state.walkable:
                    deltas.update(response="BRACED", blocker="ARENA_EDGE", position=destination)
                elif blocker:
                    deltas.update(response="BRACED", blocker=blocker.id, position=destination)
                else:
                    before = origin
                    fighter.pos = destination
                    fighter.last_recoil_tick = state.tick
                    deltas.update(response="RECOIL", from_pos=before, to_pos=destination)
                    tags.append("RECOIL_MOVED")
                    action = fighter.action
                    unresolved_strike = action is None or action.strike_tick is None or state.tick < action.strike_tick
                    if action and unresolved_strike and action.phase_at(state.tick) != "RECOVERY":
                        state.pending_interrupts.append(fighter.id)
                    state.emit(EventType.MOVE, fighter.id, attacker.id,
                               deltas={"from": before, "to": destination, "forced": True},
                               cause_tags=("INJURY_RECOIL",))
                if deltas.get("response") == "BRACED":
                    tags = ["INJURY_SHOCK", "RECOIL_BLOCKED"]
        state.emit(EventType.INJURY_RESPONSE, fighter.id, reaction["attacker_id"],
                   reaction["zone"], reaction["severity"], deltas=deltas, cause_tags=tuple(tags))


def resolve_strike(state, attacker, defender, snap):
    from .mental import change_anger
    action, cfg = attacker.action, state.config
    weapon = attacker.equipment.weapon
    reach = weapon_reach(attacker, cfg)
    if max(abs(attacker.pos[0] - defender.pos[0]), abs(attacker.pos[1] - defender.pos[1])) > reach:
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
    net = power - (armor.value if armor else 0)
    damage_type = weapon.damage_type if weapon is not None else "BLUNT"
    severity = classify_wound(cfg, zone, net, action.kind, drifted, damage_type)
    profile = injury_profile(cfg, zone, severity, action.kind, damage_type, net)
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
                       allow_disarm=action.kind != ActionKind.DISARM, drifted=drifted)
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
            and action.kind in {ActionKind.LIGHT, ActionKind.DASH_LIGHT}):
        tags.append("LIGHT_INTERRUPTS_HEAVY")
    if snap.phase == "RECOVERY":
        tags.append("LIGHT_CATCHES_RECOVERY")
    if shock_response != "HOLD":
        tags.append("SHOCK_" + shock_response)
    if defender.action and snap.phase != "RECOVERY" and (snap.phase in {"WINDUP", "DASH", "RISE"} or defender.action.kind in {
            ActionKind.BANDAGE, ActionKind.EAT, ActionKind.DRINK, ActionKind.PICKUP,
            ActionKind.DASH, ActionKind.DASH_LIGHT,
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
    state.emit(EventType.HIT, attacker.id, defender.id, zone, severity,
               deltas={"power": power, "net": net, "pain": pain, "drifted": drifted,
                       "damage_type": damage_type, "depth": profile["depth"],
                       "structure": profile["structure"], "function": profile["function"],
                       "shock_load": shock_load, "decision_control": control,
                       "shock_response": shock_response,
                       "strength": live.strength, "armor": armor.value if armor else 0,
                       "power_mult": cfg["gear"]["unarmed_power"] if attacker.disarmed else action.power_mult,
                         "weapon_edge": weapon_edge(attacker, cfg),
                        "defender_phase": snap.phase, **trace}, cause_tags=tags)
    if disarm_result is not None:
        won = disarm_result["outcome"] == "DISARMED"
        state.emit(EventType.DISARM, attacker.id, defender.id, zone,
                   deltas=disarm_result,
                   cause_tags=("RIPOSTE_DISARM" if won else "DISARM_RESISTED",))
