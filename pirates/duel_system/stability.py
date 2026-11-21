"""Deterministic balance rating, stability losses and grounded transitions."""
import math

from .model import ActionKind, EventType
from .stats import clamp, pain_burden


def balance_rating(fighter, config, tick):
    """Derive physical balance once from raw condition, load and persistent leg wounds."""
    rule = config["stability"]
    reference = rule["balance_stat_reference"]
    leg_wounds = sum(rule["leg_wound_penalty"][wound.severity.value]
                     for wound in fighter.wounds if wound.zone.value == "LEG")
    rating = (rule["balance_base"]
              + rule["balance_strength_coef"] * (fighter.strength - reference)
              + rule["balance_speed_coef"] * (fighter.speed - reference)
              + rule["balance_endurance_coef"] * (fighter.endurance - reference)
              - rule["balance_weight_penalty"] * fighter.equipment.total_weight
              - rule["balance_fatigue_penalty"] * fighter.fatigue
              - rule["balance_pain_penalty"] * pain_burden(fighter, config)
              - config["anger"]["balance_penalty"] * fighter.anger / config["defaults"]["pool_max"]
              - leg_wounds)
    return clamp(rating, 0, 100)


def stability_band(fighter, config):
    if fighter.grounded:
        return "GROUNDED"
    rule = config["stability"]
    if fighter.stability >= rule["stable_threshold"]:
        return "STABLE"
    if fighter.stability >= rule["off_balance_threshold"]:
        return "OFF_BALANCE"
    return "STUMBLING"


def interrupt_unresolved_action(state, fighter):
    action = fighter.action
    if not action:
        return
    unresolved = (state.tick < action.strike_tick if action.strike_tick is not None
                  else action.end_tick is None or state.tick < action.end_tick)
    if unresolved and action.phase_at(state.tick) != "RECOVERY":
        state.pending_interrupts.append(fighter.id)


def apply_stability_loss(state, fighter, source, base_loss, *, details=None):
    """Apply one causal loss, latch a fall at zero, and never undo a landed strike."""
    if fighter.grounded or base_loss <= 0:
        return 0
    rule = state.config["stability"]
    balance = balance_rating(fighter, state.config, state.tick)
    multiplier = clamp(1 + (rule["reference_balance"] - balance) / 100,
                       rule["loss_min_mult"], rule["loss_max_mult"])
    requested = base_loss * multiplier
    before = fighter.stability
    prior_band = stability_band(fighter, state.config)
    fighter.stability = max(0, before - requested)
    loss = before - fighter.stability
    if loss <= 0:
        return 0
    state.emit(EventType.STABILITY, fighter.id,
               deltas={"source": source, "before": before, "after": fighter.stability,
                       "loss": loss, "balance_rating": balance, "multiplier": multiplier,
                       **(details or {})}, cause_tags=(source,))
    if fighter.stability == 0 and not fighter.grounded:
        fighter.grounded = True
        fighter.grounded_since = state.tick
        state.emit(EventType.FALL, fighter.id,
                   deltas={"source": source, "from_band": prior_band,
                           "position": fighter.pos}, cause_tags=("STABILITY_ZERO", source))
        interrupt_unresolved_action(state, fighter)
    return loss


def recover_stability(state, fighter):
    """Standing recovery is explicit: only ticks of a committed WAIT restore control."""
    if fighter.grounded or not fighter.action or fighter.action.kind != ActionKind.WAIT:
        return
    before = fighter.stability
    fighter.stability = min(state.config["stability"]["start"],
                            before + state.config["stability"]["wait_recovery_per_tick"])
    if fighter.stability != before:
        state.emit(EventType.STABILITY, fighter.id,
                   deltas={"source": "WAIT_RECOVERY", "before": before,
                           "after": fighter.stability, "gain": fighter.stability-before,
                           "band": stability_band(fighter, state.config)},
                   cause_tags=("WAIT_RECOVERY",))


def _turn_degrees(previous, current):
    dot = previous[0] * current[0] + previous[1] * current[1]
    cross = previous[0] * current[1] - previous[1] * current[0]
    return abs(math.degrees(math.atan2(cross, dot)))


def record_successful_movement(state, fighter, action):
    """Apply a turn cost only after a fast tile actually moved successfully."""
    if action.kind not in {ActionKind.DASH, ActionKind.DASH_LIGHT, ActionKind.DASH_SLASH,
                           ActionKind.RUN}:
        fighter.last_fast_direction = None
        fighter.last_fast_move_tick = None
        return
    direction = tuple(action.direction)
    rule = state.config["stability"]
    if (fighter.last_fast_direction is not None and fighter.last_fast_move_tick is not None
            and state.tick - fighter.last_fast_move_tick <= rule["fast_turn_window_ticks"]):
        degrees = _turn_degrees(fighter.last_fast_direction, direction)
        if degrees >= rule["sharp_turn_min_degrees"]:
            apply_stability_loss(state, fighter, "SHARP_FAST_TURN", rule["sharp_turn_loss"],
                                 details={"turn_degrees": round(degrees, 1),
                                          "from_direction": fighter.last_fast_direction,
                                          "to_direction": direction})
    fighter.last_fast_direction = direction
    fighter.last_fast_move_tick = state.tick
