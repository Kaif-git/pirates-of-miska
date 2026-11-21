"""COMBAT SPEC sections 4/5/9. Display rounding never enters these formulas."""
import math

from .model import EffStats, EventType


def clamp(value, lower, upper):
    return max(lower, min(upper, value))


def level_multiplier(fighter, config):
    return 1 + config["level"]["stat_per_level"] * (fighter.level - 1)


def max_stamina(fighter, config):
    return fighter.stamina_stat * level_multiplier(fighter, config) * config["stamina"]["pool_per_stat"]


def weapon_reach(fighter, config):
    weapon = fighter.equipment.weapon
    return weapon.reach if weapon is not None else config["gear"]["unarmed_reach"]


def weapon_edge(fighter, config):
    weapon = fighter.equipment.weapon
    if weapon is None or fighter.disarmed:
        return 0
    integrity = config["weapon_integrity"]
    condition_loss = integrity["edge_loss_at_zero"] * (1 - weapon.condition / 100)
    return weapon.edge * (1 - condition_loss)


def fear(fighter, config):
    """Current fight fear pool, seeded from confidence and changed by lived events."""
    return clamp(fighter.fear_meter, 0, config["defaults"]["pool_max"])


def legacy_fear_pressure(fighter, config):
    """Old morale-derived fear retained only for unchanged NPC choice thresholds."""
    rule = config["fear"]
    return clamp((rule["morale_center"] - fighter.morale) * rule["morale_scale"]
                 - rule["anger_offset"] * fighter.anger, 0, config["defaults"]["pool_max"])


def pain_burden(fighter, config, *, tolerance=None):
    """Pain after authored tolerance; raw pain remains intact for wounds/UI."""
    if tolerance is None:
        tolerance = (fighter.personality.pain_tolerance if fighter.personality
                     else config["defaults"]["pain_tolerance"])
    suppression = config["stats"]["pain_tolerance_suppression"]
    return fighter.pain * (1 - tolerance * suppression)


def fatigue_penalty(fighter, config):
    rule = config["fatigue"]
    end_eff = fighter.endurance * level_multiplier(fighter, config)
    x = max(0, fighter.fatigue - rule["deadzone"]) / rule["range"]
    k = rule["k_base"] - end_eff / rule["endurance_divisor"]
    # At high levels endurance can exceed k_base * divisor: never turn debt into a buff.
    return clamp(x * k * rule["pen_scale"], 0, rule["pen_cap"])


def effective_stats(fighter, config, tick=0, *, pain_tolerance=None):
    pool = config["defaults"]["pool_max"]
    rule, blood_rule, gear = config["stats"], config["blood"], config["gear"]
    lvl = level_multiplier(fighter, config)
    endurance = fighter.endurance * lvl
    fatigue = fatigue_penalty(fighter, config)
    pain_eff = pain_burden(fighter, config, tolerance=pain_tolerance)
    pain = clamp(pain_eff / pool, 0, 1) * max(rule["pain_floor"], rule["pain_base"]
                                              - endurance / config["fatigue"]["endurance_divisor"])
    if fighter.adrenaline_until_tick is not None and tick < fighter.adrenaline_until_tick:
        pain *= 1 - config["adrenaline"]["pain_suppression"]
    if fighter.blood >= blood_rule["penalty_start"]:
        blood = 0
    elif fighter.blood >= blood_rule["penalty_mid"]:
        blood = ((blood_rule["penalty_start"] - fighter.blood)
                 / (blood_rule["penalty_start"] - blood_rule["penalty_mid"]) * blood_rule["penalty_first"])
    else:
        blood = (blood_rule["penalty_first"] + (blood_rule["penalty_mid"] - fighter.blood)
                 / blood_rule["penalty_mid"] * blood_rule["penalty_last"])
    anger = fighter.anger / pool
    fear_ratio = fear(fighter, config) / pool
    exhaustion = config["stamina"]["exhaust_mult"] if fighter.exhausted else 1
    speed_status = strength_status = 1
    for status in fighter.statuses:
        if status.active(tick):
            speed_status *= status.speed_mult
            strength_status *= status.strength_mult
    gear_speed = max(gear["speed_floor"], 1 - gear["speed_per_weight"] * fighter.equipment.total_weight)
    weapon_speed = fighter.equipment.weapon.speed_mod if fighter.equipment.weapon is not None else 1
    speed = (fighter.speed * lvl * (1-rule["fatigue_speed"]*fatigue)
              * (1-rule["pain_speed"]*pain) * (1-blood)
              * (1-rule["fear_speed"]*fear_ratio)
              * (1+config["anger"]["spd_bonus"]*anger) * speed_status * exhaustion
              * gear_speed * weapon_speed)
    strength = (fighter.strength * lvl * (1-rule["fatigue_strength"]*fatigue)
                 * (1-rule["pain_strength"]*pain) * (1-blood)
                 * (1+config["anger"]["str_bonus"]*anger) * strength_status * exhaustion)
    accuracy = (fighter.accuracy
                * (1 - rule["fear_accuracy"] * fear_ratio)
                * (1 - config["anger"]["acc_penalty"] * anger))
    return EffStats(max(fighter.speed * rule["floor_pct"], speed),
                    max(fighter.strength * rule["floor_pct"], strength),
                    max(fighter.accuracy * rule["floor_pct"], accuracy), endurance, fatigue, pain, blood)


def phase_ticks(base, speed, config, *, recovery_weight=0):
    if base == 0:
        return 0
    if speed <= 0:
        raise ValueError("Phase speed must be positive")
    # Weight multiplies the scaled duration; round once at the end, never for display.
    return max(config["time"]["min_phase_ticks"], math.ceil(
        base * config["time"]["speed_reference"] / speed
        * (1 + config["gear"]["recovery_per_weight"] * recovery_weight)))


def stamina_cost(fighter, base_cost, config):
    return base_cost * (1 + config["gear"]["stamina_per_weight"] * fighter.equipment.total_weight)


def fatigue_gain(fighter, paid_cost, config):
    return (paid_cost * max(0, config["fatigue"]["gain_base"]
                           - fighter.endurance * level_multiplier(fighter, config)
                           / config["fatigue"]["endurance_divisor"])
            * (1 + config["gear"]["fatigue_per_weight"] * fighter.equipment.total_weight))


def refresh_exhaustion(fighter, config):
    if fighter.stamina >= max_stamina(fighter, config) * config["stamina"]["exhaust_recover_pct"]:
        fighter.exhausted = False


def pay_stamina(state, fighter, cost):
    """Cost already includes weight; unpaid shortfall becomes undiscounted debt."""
    paid = min(fighter.stamina, cost)
    fighter.stamina -= paid
    shortfall = cost - paid
    fighter.fatigue = clamp(fighter.fatigue + fatigue_gain(fighter, paid, state.config)
                            + shortfall * state.config["stamina"]["shortfall_fatigue"],
                            0, state.config["defaults"]["pool_max"])
    if shortfall > 0:
        newly_exhausted = not fighter.exhausted
        fighter.exhausted = True
        if newly_exhausted:
            state.emit(EventType.EXHAUST, fighter.id, deltas={"shortfall": shortfall})
            if fighter.anger > state.config["anger"]["crash_exhaust_anger"]:
                from .mental import anger_crash
                anger_crash(state, fighter, "ANGRY_EXHAUSTION")
    return paid
