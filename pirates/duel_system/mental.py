"""Mental event consequences, threshold memory, fight start/end carry (M5)."""
from copy import deepcopy

from .config import load_config
from .model import CombatState, EventType, PreFightState
from .stats import clamp, legacy_fear_pressure, pain_burden


def anger_crash(state, fighter, cause):
    cfg = state.config
    fatigue_before, stamina_before = fighter.fatigue, fighter.stamina
    fighter.fatigue = min(cfg["defaults"]["pool_max"], fighter.fatigue
                          + cfg["anger"]["crash_pain_fatigue"] * fighter.pain)
    fighter.stamina = max(0, fighter.stamina - cfg["anger"]["crash_stamina"])
    fighter.anger_history = [(state.tick, fighter.anger)]
    state.emit(EventType.ANGER_CRASH, fighter.id,
               deltas={"fatigue": fighter.fatigue - fatigue_before,
                       "stamina": fighter.stamina - stamina_before}, cause_tags=(cause,))


def change_anger(state, fighter, delta, cause):
    rule = state.config["anger"]
    if delta > 0:
        tolerance = (fighter.personality.anger_tolerance if fighter.personality
                     else state.config["defaults"]["anger_tolerance"])
        delta *= 1 - tolerance
    before = fighter.anger
    fighter.anger_history = [(tick, anger) for tick, anger in fighter.anger_history
                             if state.tick - tick < rule["crash_window"]]
    fighter.anger_history.append((state.tick, before))
    fighter.anger = clamp(before + delta, 0, state.config["defaults"]["pool_max"])
    if fighter.anger != before:
        state.emit(EventType.POOL_CHANGE, fighter.id,
                   deltas={"anger": fighter.anger - before}, cause_tags=(cause,))
    if (fighter.anger < rule["crash_low"] and before >= rule["crash_low"]
            and any(value > rule["crash_high"] for _, value in fighter.anger_history)):
                anger_crash(state, fighter, "ANGER_DROP")
    refresh_behavior_modes(state, fighter, cause, before_values={"anger": before})


def change_fear(state, fighter, delta, cause):
    before = fighter.fear_meter
    if delta > 0 and fighter.personality is not None:
        delta *= fighter.personality.fear_gain_susceptibility
    fighter.fear_meter = clamp(before + delta, 0, state.config["defaults"]["pool_max"])
    if fighter.fear_meter != before:
        state.emit(EventType.POOL_CHANGE, fighter.id,
                   deltas={"fear": fighter.fear_meter,
                           "fear_delta": fighter.fear_meter - before}, cause_tags=(cause,))
    refresh_behavior_modes(state, fighter, cause, before_values={"fear": before})


def _next_behavior_mode(meter, current, medium, critical, thresholds):
    if meter >= thresholds["critical_enter"]:
        return critical
    if current == critical and meter >= thresholds["critical_exit"]:
        return critical
    if meter >= thresholds["mode_enter"]:
        return medium
    if current == medium and meter >= thresholds["mode_exit"]:
        return medium
    return "NONE"


def behavior_values(fighter, config):
    from .stats import pain_burden
    return {"pain": pain_burden(fighter, config),
            "fear": fighter.fear_meter,
            "anger": fighter.anger}


def refresh_behavior_modes(state, fighter, cause="STATE_SYNC", *, before_values=None, emit=True):
    """Latch shared threshold modes; called only from simulation state changes/decisions."""
    thresholds = state.config["behavior_thresholds"]
    values = behavior_values(fighter, state.config)
    modes = {"pain": ("PROTECTIVE", "BREAK"),
             "fear": ("CAUTIOUS", "BREAK"),
             "anger": ("PRESS", "HOT")}
    before_values = before_values or {}
    changed = []
    for meter, (medium, critical) in modes.items():
        old_mode = fighter.behavior_modes[meter]
        new_mode = _next_behavior_mode(values[meter], old_mode, medium, critical, thresholds)
        if new_mode == old_mode:
            continue
        fighter.behavior_modes[meter] = new_mode
        if emit:
            if new_mode == critical:
                threshold = thresholds["critical_enter"]
                transition = "ENTER_CRITICAL"
            elif new_mode == medium:
                threshold = (thresholds["critical_exit"] if old_mode == critical
                             else thresholds["mode_enter"])
                transition = "ENTER_MODE" if old_mode == "NONE" else "DOWNGRADE"
            elif old_mode == critical:
                threshold, transition = thresholds["critical_exit"], "EXIT_CRITICAL"
            else:
                threshold, transition = thresholds["mode_exit"], "EXIT_MODE"
            state.emit(EventType.BEHAVIOR_MODE, fighter.id,
                       deltas={"meter": meter,
                               "value_before": before_values.get(meter, values[meter]),
                               "value_after": values[meter], "mode_before": old_mode,
                               "mode_after": new_mode, "transition": transition,
                               "threshold": threshold, "cause": cause},
                       cause_tags=("BEHAVIOR_THRESHOLD", transition, str(cause)))
        changed.append((meter, old_mode, new_mode))
    return tuple(changed)


def initialize_behavior_modes(state, fighter):
    """Set latches for new/migrated state without generating synthetic events."""
    return refresh_behavior_modes(state, fighter, emit=False)


def blood_morale(state, fighter):
    """Each low-blood morale loss occurs once, even after healing and rebleeding."""
    cfg = state.config
    for threshold, key in ((cfg["blood"]["penalty_start"], "blood_low"),
                           (cfg["blood"]["penalty_mid"], "blood_critical")):
        if fighter.blood < threshold and threshold not in fighter.blood_thresholds:
            before = fighter.morale
            fighter.morale = max(0, fighter.morale + cfg["morale"][key])
            fighter.blood_thresholds.append(threshold)
            state.emit(EventType.POOL_CHANGE, fighter.id,
                       deltas={"morale": fighter.morale - before}, cause_tags=(key.upper(),))


def fear_decision(state, fighter, *, on_break=None):
    """Called exactly once per NPC decision, not per scored candidate or UI read."""
    if state.end_reason:
        return fighter.yielded or fighter.fled
    rule = state.config["fear"]
    fighter.fear_decisions = (fighter.fear_decisions + 1
                              if legacy_fear_pressure(fighter, state.config) >= rule["break_threshold"] else 0)
    if fighter.fear_decisions < rule["break_decisions"]:
        return False
    if on_break is not None:
        on_break()
    # Yield is the v1 morale-break outcome. Flee remains an ordinary exit-reaching action.
    fighter.yielded = True
    fighter.action = None
    state.emit(EventType.FEAR_BREAK, fighter.id, deltas={"outcome": "yield"})
    from .engine import check_end_conditions
    check_end_conditions(state)
    return True


def initialize_fight(fighters, *, config=None, rng_seed=0, walkable=None, exits=(), leaders=None):
    """Own inputs; seed current fear from confidence and preserve legacy morale metadata."""
    cfg = deepcopy(config) if config is not None else load_config()
    state = CombatState(deepcopy(fighters), rng_seed=rng_seed, config=cfg,
                        walkable=deepcopy(walkable), exits=set(exits), leaders=deepcopy(leaders or {}))
    for fighter in state.fighters:
        visible_hostiles = state.visible_hostiles_for(fighter.id)
        other = visible_hostiles[0] if visible_hostiles else (
            state.other(fighter) if len(state.fighters) <= 2 else None)
        starting_fear = (fighter.personality.starting_fear if fighter.personality
                         else cfg["defaults"]["starting_fear"])
        legacy_prefight_nerves = fighter.prefight.nervousness if fighter.prefight else 0
        fighter.fear_meter = clamp(starting_fear
                                    + legacy_prefight_nerves * cfg["defaults"]["pool_max"],
                                    0, cfg["defaults"]["pool_max"])
        level_penalty = (cfg["morale"]["level_gap_penalty"] * max(0, other.level - fighter.level)
                         if other else 0)
        morale_bias = fighter.personality.morale_bias if fighter.personality else 0
        fighter.morale = clamp(cfg["morale"]["start"] + morale_bias - level_penalty,
                                0, cfg["defaults"]["pool_max"])
        fighter.nervousness = 0
        fighter.anger_history = [(state.tick, fighter.anger)]
        blood_morale(state, fighter)
    from .perception import refresh_contacts
    refresh_contacts(state)
    from .engine import check_end_conditions
    check_end_conditions(state)
    return state


def postfight_state(state, fighter_id):
    fighter = next(item for item in state.fighters if item.id == fighter_id)
    return PreFightState(fatigue=fighter.fatigue, stamina=fighter.stamina,
                         blood=fighter.blood, injuries=deepcopy(fighter.wounds),
                          nervousness=0,
                         permanent_injuries=deepcopy(fighter.permanent_injuries))
