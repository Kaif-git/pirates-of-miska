"""Pure, bounded personal-combat exchange arithmetic for world simulation."""

from __future__ import annotations

from typing import Any


def resolve_exchange(attacker: dict[str, int], defender: dict[str, int], *,
                    weapon: dict[str, int], armor: dict[str, int],
                    weapon_condition_permille: int, armor_condition_permille: int,
                    attacker_stamina: int, defender_stamina: int,
                    defender_action: str, attacker_roll: int, defender_roll: int,
                    rules: dict[str, int], attacker_morale: int = 50,
                    defender_morale: int = 50, distance: int = 1) -> dict[str, Any]:
    """Resolve one legal melee attack against one committed defensive action.

    Strength only contributes to force after the hit contest. Range is checked
    before this function by the caller; attributes cannot grant reach.
    """
    names = {"combat", "speed", "strength", "endurance", "toughness", "willpower"}
    if (set(attacker) != names or set(defender) != names
            or any(type(value) is not int or not 0 <= value <= 100
                   for value in (*attacker.values(), *defender.values()))
            or defender_action not in {"guard", "evade"}
            or type(weapon_condition_permille) is not int
            or not 0 <= weapon_condition_permille <= 1000
            or type(armor_condition_permille) is not int
            or not 0 <= armor_condition_permille <= 1000
            or type(attacker_stamina) is not int or not 0 <= attacker_stamina <= 100
            or type(defender_stamina) is not int or not 0 <= defender_stamina <= 100
            or type(attacker_morale) is not int or not 0 <= attacker_morale <= 100
            or type(defender_morale) is not int or not 0 <= defender_morale <= 100
            or type(distance) is not int or distance < 1
            or not rules["hit_roll_min"] <= attacker_roll <= rules["hit_roll_max"]
            or not rules["hit_roll_min"] <= defender_roll <= rules["hit_roll_max"]):
        raise ValueError("Invalid personal-combat exchange inputs")
    if distance > weapon["reach"]:
        raise ValueError("Personal attack is outside weapon reach")

    weapon_quality = weapon_condition_permille
    armor_quality = armor_condition_permille
    attack_cost = max(
        1, rules["attack_stamina_cost"]
        - attacker["endurance"] // 10 * rules["endurance_cost_reduction_per_10"])
    defense_cost_base = rules[
        "evade_stamina_cost" if defender_action == "evade" else "guard_stamina_cost"]
    defense_cost = max(
        1, defense_cost_base
        - defender["endurance"] // 10 * rules["endurance_cost_reduction_per_10"])
    attacker_exhausted = attacker_stamina < attack_cost
    defender_exhausted = defender_stamina < defense_cost
    exhaustion = rules["exhaustion_penalty"]

    weapon_accuracy = weapon["accuracy"] * weapon_quality // 1000
    armor_handling = armor["handling"] * armor_quality // 1000
    attacker_morale_modifier = ((attacker_morale - 50) // 10
                                * rules["morale_composure_per_10"])
    defender_morale_modifier = ((defender_morale - 50) // 10
                                * rules["morale_composure_per_10"])
    attack_score = (
        attacker["combat"] * rules["attack_skill_weight"]
        + attacker["speed"] * rules["attack_speed_weight"]
        + weapon_accuracy + attacker_roll
        + attacker_morale_modifier - (exhaustion if attacker_exhausted else 0))
    defense_score = (
        defender["combat"] * rules["defense_skill_weight"]
        + defender["speed"] * rules["defense_speed_weight"]
        + defender["willpower"] * rules["defense_willpower_weight"] // 2
        + armor_handling + defender_roll
        + defender_morale_modifier - (exhaustion if defender_exhausted else 0))
    if defender_action == "guard":
        defense_score += rules["guard_bonus"]
    else:
        defense_score += defender["speed"] // 2
    hit = attack_score > defense_score

    weapon_force = weapon["force"] * weapon_quality // 1000
    armor_protection = (armor["protection"] * armor_quality // 1000
                        * rules["armor_protection_weight"])
    force = (attacker["strength"] * rules["strength_force_weight"] + weapon_force
             if hit else 0)
    protection = (defender["toughness"] // 10
                  * rules["toughness_protection_per_10"] + armor_protection)
    impact = max(0, force - protection) if hit else 0
    return {
        "attacker_action": "attack", "defender_action": defender_action,
        "distance": distance, "weapon_reach": weapon["reach"],
        "attack_score": attack_score, "defense_score": defense_score,
        "hit": hit, "force": force, "protection": protection, "impact": impact,
        "attacker_stamina_before": attacker_stamina,
        "attacker_stamina_after": max(0, attacker_stamina - attack_cost),
        "attacker_stamina_cost": attack_cost,
        "defender_stamina_before": defender_stamina,
        "defender_stamina_after": max(0, defender_stamina - defense_cost),
        "defender_stamina_cost": defense_cost,
        "attacker_exhausted": attacker_exhausted,
        "defender_exhausted": defender_exhausted,
        "weapon_condition_before_permille": weapon_quality,
        "weapon_condition_after_permille": max(0, weapon_quality - weapon["wear_permille"]),
        "armor_condition_before_permille": armor_quality,
        "armor_condition_after_permille": max(
            0, armor_quality - (armor["wear_permille"] if hit else 0)),
    }
