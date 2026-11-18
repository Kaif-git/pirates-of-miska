"""Mental event consequences, threshold memory, fight start/end carry (M5)."""
from copy import deepcopy

from .config import load_config
from .model import CombatState, EventType, PreFightState
from .stats import clamp, fear


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
    fighter.fear_decisions = fighter.fear_decisions + 1 if fear(fighter, state.config) >= rule["break_threshold"] else 0
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
    """Own the inputs and apply the specified level/courage starting morale."""
    cfg = deepcopy(config) if config is not None else load_config()
    state = CombatState(deepcopy(fighters), rng_seed=rng_seed, config=cfg,
                        walkable=deepcopy(walkable), exits=set(exits), leaders=deepcopy(leaders or {}))
    for fighter in state.fighters:
        visible_hostiles = state.visible_hostiles_for(fighter.id)
        other = visible_hostiles[0] if visible_hostiles else (
            state.other(fighter) if len(state.fighters) <= 2 else None)
        courage = fighter.personality.courage if fighter.personality else 0
        level_penalty = (cfg["morale"]["level_gap_penalty"] * max(0, other.level - fighter.level)
                         if other else 0)
        fighter.morale = clamp(cfg["morale"]["start"] + courage - level_penalty,
                                0, cfg["defaults"]["pool_max"])
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
                         nervousness=fighter.nervousness,
                         permanent_injuries=deepcopy(fighter.permanent_injuries))
