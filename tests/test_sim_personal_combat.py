import copy

from sim.systems.characters import load_character_data
from sim.systems.personal_combat import resolve_exchange


def _fixture():
    catalog, _digest = load_character_data()
    rules = catalog["combat_equipment"]["rules"]
    attacker = {key: 50 for key in (
        "combat", "speed", "strength", "endurance", "toughness", "willpower")}
    defender = dict(attacker)
    weapon = catalog["combat_equipment"]["weapons"]["cutlass"]
    armor = catalog["combat_equipment"]["armor"]["leather_jack"]
    return attacker, defender, weapon, armor, rules


def _resolve(attacker, defender, weapon, armor, rules, **overrides):
    args = {
        "weapon_condition_permille": 1000,
        "armor_condition_permille": 1000,
        "attacker_stamina": 100,
        "defender_stamina": 100,
        "defender_action": "guard",
        "attacker_roll": 10,
        "defender_roll": -10,
    }
    args.update(overrides)
    return resolve_exchange(attacker, defender, weapon=weapon, armor=armor,
                            rules=rules, **args)


def test_strength_changes_force_not_hit_legality_or_accuracy():
    attacker, defender, weapon, armor, rules = _fixture()
    stronger = dict(attacker, strength=90)

    ordinary_result = _resolve(attacker, defender, weapon, armor, rules)
    strong_result = _resolve(stronger, defender, weapon, armor, rules)

    assert ordinary_result["attack_score"] == strong_result["attack_score"]
    assert ordinary_result["defense_score"] == strong_result["defense_score"]
    assert ordinary_result["hit"] == strong_result["hit"]
    assert strong_result["force"] > ordinary_result["force"]
    assert strong_result["impact"] > ordinary_result["impact"]


def test_combat_and_speed_change_the_hit_contest_but_not_raw_force():
    attacker, defender, weapon, armor, rules = _fixture()
    test_defender = dict(defender, combat=10, speed=20, willpower=10)
    weak_technique = _resolve(dict(attacker, combat=20), test_defender, weapon, armor, rules)
    trained_technique = _resolve(dict(attacker, combat=80), test_defender, weapon, armor, rules)
    slow = _resolve(dict(attacker, speed=20), test_defender, weapon, armor, rules)
    fast = _resolve(dict(attacker, speed=80), test_defender, weapon, armor, rules)

    assert trained_technique["attack_score"] > weak_technique["attack_score"]
    assert fast["attack_score"] > slow["attack_score"]
    assert trained_technique["force"] == weak_technique["force"]
    assert fast["force"] == slow["force"]


def test_equal_stats_have_seeded_bounded_variance_and_a_disadvantaged_fighter_can_land():
    attacker, defender, weapon, armor, rules = _fixture()
    favorable = _resolve(attacker, defender, weapon, armor, rules,
                         attacker_roll=10, defender_roll=-10)
    unfavorable = _resolve(attacker, defender, weapon, armor, rules,
                           attacker_roll=-10, defender_roll=10)
    underdog = _resolve(dict(attacker, combat=46), dict(defender, combat=50),
                        weapon, armor, rules, attacker_roll=10, defender_roll=-10)

    assert favorable["hit"] and not unfavorable["hit"]
    assert not unfavorable["force"] and not unfavorable["impact"]
    assert abs(favorable["attack_score"] - unfavorable["attack_score"]) <= 20
    assert underdog["hit"]


def test_toughness_armor_endurance_and_condition_have_distinct_effects():
    attacker, defender, weapon, armor, rules = _fixture()
    base = _resolve(attacker, defender, weapon, armor, rules)
    tough = _resolve(attacker, dict(defender, toughness=90), weapon, armor, rules)
    unarmored = _resolve(attacker, defender, weapon,
                         {"protection": 0, "handling": 0, "wear_permille": 0}, rules)
    fit = _resolve(dict(attacker, endurance=90), defender, weapon, armor, rules,
                   attacker_stamina=10)
    tired = _resolve(dict(attacker, endurance=10), defender, weapon, armor, rules,
                     attacker_stamina=10)
    worn = _resolve(attacker, defender, weapon, armor, rules,
                    weapon_condition_permille=250, armor_condition_permille=250)

    assert tough["impact"] < base["impact"]
    assert tough["impact"] > 0
    assert unarmored["impact"] > base["impact"]
    assert fit["attacker_stamina_cost"] < tired["attacker_stamina_cost"]
    assert worn["weapon_condition_after_permille"] < 250
    assert worn["armor_condition_after_permille"] < 250
    assert worn["force"] <= base["force"]


def test_exchange_is_pure_deterministic_and_rejects_unknown_defense_action():
    attacker, defender, weapon, armor, rules = _fixture()
    before = copy.deepcopy((attacker, defender, weapon, armor))
    first = _resolve(attacker, defender, weapon, armor, rules)
    second = _resolve(attacker, defender, weapon, armor, rules)

    assert first == second
    assert (attacker, defender, weapon, armor) == before
    try:
        _resolve(attacker, defender, weapon, armor, rules, defender_action="counterspell")
    except ValueError as error:
        assert "Invalid personal-combat" in str(error)
    else:
        raise AssertionError("Unknown defense action was accepted")
