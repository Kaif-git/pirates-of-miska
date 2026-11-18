"""Section13 curve and guided rewards. No resource refill on level-up."""
import math

from .model import EventType
from .stats import clamp


def xp_to_next(level, config):
    return round(config["level"]["xp_base"] * config["level"]["xp_growth"] ** (level - 1))


def kill_xp(player_level, enemy_level, config, mode="off"):
    rule = config["level"]
    if mode not in rule["guided_xp"] or player_level <= 0 or enemy_level <= 0:
        raise ValueError("Invalid XP levels or guide mode")
    ratio = clamp((enemy_level / player_level) ** rule["kill_ratio_power"], *rule["kill_ratio_clamp"])
    return rule["kill_base_xp"] * ratio * rule["guided_xp"][mode]


def award_xp(state, fighter, amount, *, guided="off"):
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount < 0 or guided not in state.config["level"]["guided_xp"]:
        raise ValueError("Invalid XP award")
    cap = state.config["defaults"]["level_max"]
    if fighter.level >= cap:
        return
    fighter.xp += amount
    state.emit(EventType.XP_GAIN, fighter.id,
               deltas={"xp": amount, "guided": guided != "off", "guide_mode": guided})
    while fighter.level < cap and fighter.xp >= xp_to_next(fighter.level, state.config):
        fighter.xp -= xp_to_next(fighter.level, state.config)
        fighter.level += 1
        state.emit(EventType.LEVEL_UP, fighter.id, deltas={"level": fighter.level})
    if fighter.level == cap:
        fighter.xp = 0
