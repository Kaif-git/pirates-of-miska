"""Guard-line defense and deferred stagger: same-tick strikes still trade."""
from .model import ActionKind, EventType, Phase
from .stats import clamp, effective_stats, pay_stamina, stamina_cost


def stagger(state, fighter, ticks, cause):
    fighter.stagger_until = max(fighter.stagger_until, state.tick + ticks)
    state.pending_interrupts.append(fighter.id)
    state.emit(EventType.STAGGER, fighter.id, deltas={"until_tick": fighter.stagger_until},
               cause_tags=(cause,))


def try_guard(state, attacker, defender, zone, snap):
    """Return true when guard/parry absorbed the strike; bypasses return false."""
    cfg, action = state.config, attacker.action
    disarm_hand = action.kind == ActionKind.DISARM and zone.value == "HAND"
    if snap.defense != "GUARD_HOLD" or (zone.value not in cfg["zones"]["guard_line"] and not disarm_hand):
        return False
    rule = cfg["guard"]
    heavy = action.kind == ActionKind.HEAVY
    att_str = effective_stats(attacker, cfg, state.tick).strength
    def_str = effective_stats(defender, cfg, state.tick).strength
    window = snap.parry_window + (rule["heavy_window_bonus"] if heavy else 0)
    in_window = snap.guard_ready_tick is not None and snap.guard_ready_tick <= state.tick < snap.guard_ready_tick + window
    ratio = rule["parry_str_ratio"]["heavy" if heavy else "light"]
    cap = cfg["defaults"]["pool_max"]
    trace = {"attacker_strength": att_str, "defender_strength": def_str, "parry_ratio": ratio,
             "in_parry_window": in_window, "guard_ready_tick": snap.guard_ready_tick,
             "parry_window": window, "break_ratio": rule["break_str_ratio"]}
    if in_window and def_str >= ratio * att_str:
        parry_cost = 0.0
        if defender.action and defender.action.contextual_defense:
            before = defender.stamina
            pay_stamina(state, defender, stamina_cost(
                defender, rule["defend_parry_cost"], cfg))
            parry_cost = before - defender.stamina
        stagger(state, attacker, rule["parry_stagger"], "PARRY_WINDOW")
        defender.riposte = True
        from .mental import change_anger
        change_anger(state, attacker, cfg["anger"]["parried"], "PARRIED")
        attacker.morale = clamp(attacker.morale + cfg["morale"]["parried"], 0, cap)
        defender.morale = clamp(defender.morale + cfg["morale"]["parry_success"], 0, cap)
        state.emit(EventType.PARRY, defender.id, attacker.id, zone,
                   deltas={"stamina_cost": parry_cost, **trace}, cause_tags=("PARRY_WINDOW",))
        if defender.action and defender.action.contextual_defense:
            defender.action.end_tick = state.tick + 1
            defender.action.phases = [phase for phase in defender.action.phases
                                      if phase.name not in {"GUARD_HOLD", "RECOVERY"}]
            defender.action.phases.extend((
                Phase("GUARD_HOLD", defender.action.guard_ready_tick, state.tick),
                Phase("RECOVERY", state.tick, state.tick + 1)))
        from .engine import apply_weapon_stress
        apply_weapon_stress(state, defender, "PARRY", opponent=attacker)
        apply_weapon_stress(state, attacker, "PARRY", opponent=defender)
        return True
    cost = max(rule["block_min"], rule["block_base"] + (att_str - def_str) * rule["block_strength_coef"])
    cost *= rule["heavy_block_mult"] if heavy else 1
    stamina_before = defender.stamina
    breaks = stamina_before - cost < 0 or (heavy and def_str < rule["break_str_ratio"] * att_str)
    pay_stamina(state, defender, cost)
    if heavy:
        pain_before = defender.pain
        defender.pain = min(cap, defender.pain + rule["heavy_pain"])
        from .mental import refresh_behavior_modes
        from .stats import pain_burden
        refresh_behavior_modes(state, defender, "HEAVY_GUARD_PAIN",
                               before_values={"pain": pain_burden(defender, state.config)
                                              - max(0, defender.pain-pain_before)
                                              * (1 - (defender.personality.pain_tolerance
                                                      if defender.personality else
                                                      state.config["defaults"]["pain_tolerance"])
                                                 * state.config["stats"]["pain_tolerance_suppression"])})
    tags = ("HEAVY_BEATS_GUARD",) if heavy else ("GUARD_BLOCK",)
    state.emit(EventType.BLOCK, defender.id, attacker.id, zone,
               deltas={"stamina_cost": cost, "stamina_before": stamina_before, **trace}, cause_tags=tags)
    if heavy:
        from .engine import apply_weapon_stress
        apply_weapon_stress(state, defender, "HEAVY_BLOCK", opponent=attacker)
        apply_weapon_stress(state, attacker, "HEAVY_BLOCK", opponent=defender)
    if breaks:
        stagger(state, defender, rule["break_stagger"], "GUARD_BREAK")
        state.emit(EventType.GUARD_BREAK, attacker.id, defender.id, zone,
                   deltas={"stamina_cost": cost, "stamina_before": stamina_before, **trace}, cause_tags=tags)
    return True
