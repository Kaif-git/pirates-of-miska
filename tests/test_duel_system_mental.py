"""M5 mental-resource, end-state and worked-example formula gates."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from pirates.duel_system.config import load_config
from pirates.duel_system.engine import Choice, advance_one_tick, check_end_conditions, commit_action
from pirates.duel_system.mental import (
    change_anger, fear_decision, initialize_fight, postfight_state,
)
from pirates.duel_system.model import (
    ActionKind, CombatState, Equipment, EventType, Personality, Severity, Weapon, Wound, Zone,
)
from pirates.duel_system.stats import effective_stats, pay_stamina
from test_duel_system_foundation import bare_fighter
from test_duel_system_scheduler import arena, advance_to


def test_anger_drop_crash_has_strict_thresholds_window_and_is_not_repeated():
    state = arena()
    fighter = state.fighters[0]
    fighter.anger, fighter.pain, fighter.stamina = 70, 40, 80
    change_anger(state, fighter, -20, "TEST")
    state.tick = 19
    change_anger(state, fighter, -21, "TEST")
    assert fighter.anger == 29
    assert fighter.fatigue == 10
    assert fighter.stamina == 70
    assert sum(e.type == EventType.ANGER_CRASH for e in state.event_log) == 1
    change_anger(state, fighter, -1, "TEST")
    assert fighter.fatigue == 10
    state = arena()
    fighter = state.fighters[0]
    fighter.anger = 70
    change_anger(state, fighter, -20, "TEST")
    state.tick = 20
    change_anger(state, fighter, -21, "TEST")
    assert not any(e.type == EventType.ANGER_CRASH for e in state.event_log)


def test_exhaustion_while_angry_causes_one_crash_in_addition_to_shortfall():
    state = arena()
    fighter = state.fighters[0]
    fighter.stamina, fighter.pain, fighter.anger = 1, 40, 60
    pay_stamina(state, fighter, 6)
    assert fighter.exhausted
    assert fighter.fatigue == pytest.approx(.95 + 5 + 10)
    assert sum(e.type == EventType.ANGER_CRASH for e in state.event_log) == 1
    pay_stamina(state, fighter, 1)
    assert sum(e.type == EventType.ANGER_CRASH for e in state.event_log) == 1


def test_two_hits_without_return_increase_anger_and_own_hit_resets_counter():
    from pirates.duel_system.wounds import resolve_strike, defense_snapshot
    state = arena()
    attacker, defender = state.fighters
    commit_action(state, attacker.id, Choice(ActionKind.LIGHT, Zone.TORSO))
    resolve_strike(state, attacker, defender, defense_snapshot(state, defender))
    first = defender.anger
    resolve_strike(state, attacker, defender, defense_snapshot(state, defender))
    assert defender.anger - first == pytest.approx(7 + 6)
    assert defender.hits_without_return == 2
    commit_action(state, defender.id, Choice(ActionKind.LIGHT, Zone.TORSO))
    resolve_strike(state, defender, attacker, defense_snapshot(state, attacker))
    assert defender.hits_without_return == 0


def test_circling_angers_opponent_only_if_near_and_no_recent_hit():
    state = arena()
    player, enemy = state.fighters
    commit_action(state, player.id, Choice(ActionKind.WALK, direction=(0, 1)))
    advance_to(state, 10)
    assert enemy.anger > 0
    state = arena()
    player, enemy = state.fighters
    enemy.last_hit_tick = 0
    commit_action(state, player.id, Choice(ActionKind.WALK, direction=(0, 1)))
    advance_to(state, 10)
    assert enemy.anger == 0


def test_blood_threshold_morale_penalties_are_once_each_and_persist_in_snapshot():
    state = arena()
    fighter = state.fighters[0]
    fighter.blood = 49
    advance_one_tick(state)
    assert fighter.morale == 60
    advance_one_tick(state)
    assert fighter.morale == 60
    fighter.blood = 24
    advance_one_tick(state)
    assert fighter.morale == 45
    restored = CombatState.from_data(state.to_data())
    advance_one_tick(restored)
    assert restored.fighters[0].morale == 45


def test_fear_break_requires_three_consecutive_npc_decisions_and_anger_offsets_it():
    state = arena()
    fighter = state.fighters[1]
    fighter.personality = Personality(**deepcopy(state.config["ronin"]))
    fighter.morale = 0
    assert not fear_decision(state, fighter)
    assert not fear_decision(state, fighter)
    assert fear_decision(state, fighter)
    assert fighter.yielded
    assert state.end_reason == "yield"
    assert any(e.type == EventType.FEAR_BREAK for e in state.event_log)
    state = arena()
    fighter = state.fighters[1]
    fighter.morale = 0
    fighter.anger = 100
    assert not fear_decision(state, fighter)
    assert fighter.fear_decisions == 0


@pytest.mark.parametrize("field,value,reason", [("blood", 0, "death"), ("blood", 15, "unconscious"),
                                               ("yielded", True, "yield"), ("fled", True, "flee")])
def test_end_reasons_are_explicit_idempotent_and_frozen(field, value, reason):
    state = arena()
    setattr(state.fighters[0], field, value)
    check_end_conditions(state)
    assert state.end_reason == reason
    assert state.winner == "ronin"
    before = state.to_data()
    check_end_conditions(state)
    advance_one_tick(state)
    assert state.to_data() == before
    assert sum(e.type == EventType.FIGHT_END for e in state.event_log) == 1


def test_critical_blood_with_fatigue_collapses_and_exits_emit_flee():
    state = arena()
    state.fighters[0].blood, state.fighters[0].fatigue = 24, 71
    check_end_conditions(state)
    assert state.end_reason == "unconscious"
    state = arena(exits={(0, 1)})
    commit_action(state, "player", Choice(ActionKind.WALK, direction=(0, 1)))
    advance_to(state, 10)
    assert state.end_reason == "flee"


def test_prefight_morale_level_fear_and_postfight_carry_are_owned():
    player = bare_fighter(level=1)
    enemy = bare_fighter("ronin", level=5)
    enemy.personality = Personality(**deepcopy(load_config()["ronin"]))
    state = initialize_fight([player, enemy], rng_seed=17)
    assert state.fighters[0].morale == 50
    assert state.fighters[1].morale == 80
    assert player.morale == 70  # no mutation of caller's fighters
    fighter = state.fighters[0]
    fighter.blood, fighter.fatigue, fighter.stamina = 55, 35, 42
    fighter.wounds = [Wound(Zone.TORSO, Severity.CUT, 3)]
    carry = postfight_state(state, fighter.id)
    assert (carry.blood, carry.fatigue, carry.stamina) == (55, 35, 42)
    next_fighter = bare_fighter(prefight=carry)
    assert next_fighter.blood == 55 and next_fighter.wounds[0].bleed_per_turn == 3
    carry.injuries[0].bandaged = True
    assert not fighter.wounds[0].bandaged


def worked(kind, weight=0, *, tiles=3, enemy_pos=(4, 0)):
    cfg = load_config()
    cfg["accuracy"]["jitter"] = 0
    fighters = [bare_fighter("player", pos=(0, 0), equipment=Equipment(Weapon(weight=weight)), **cfg["fixtures"]["dorime"]),
                bare_fighter("ronin", pos=enemy_pos, equipment=Equipment(Weapon(weight=weight)), **cfg["fixtures"]["ronin"])]
    fighters[0].accuracy = 20  # Explicit called-hand fixture; the matched novice baseline remains Accuracy 10.
    state = CombatState(fighters, config=cfg)
    commit_action(state, "player", Choice(ActionKind.DASH_LIGHT, Zone.HAND, (1, 0), tiles))
    commit_action(state, "ronin", Choice(kind, Zone.TORSO if kind in {ActionKind.LIGHT, ActionKind.HEAVY} else None))
    return state


def test_worked_unweighted_dash_light_interrupts_heavy_at_t5_and_guard_parries_drifted_arm():
    state = worked(ActionKind.HEAVY, tiles=1, enemy_pos=(2, 0))
    assert state.fighters[0].action.strike_tick == 5
    assert state.fighters[1].action.strike_tick == 7
    advance_to(state, 5)
    hit = next(e for e in state.event_log if e.type == EventType.HIT)
    assert (hit.zone, hit.severity) == (Zone.HAND, Severity.CUT)
    assert hit.deltas["power"] == 14
    assert state.fighters[1].action is None
    state = worked(ActionKind.GUARD, tiles=1, enemy_pos=(2, 0))
    state.fighters[1].action.parry_window = 4  # isolate a supported parry-window contact
    assert state.fighters[0].action.strike_tick == 5
    advance_to(state, 5)
    event = next(e for e in state.event_log if e.type == EventType.PARRY)
    assert event.zone == Zone.ARM
    assert state.fighters[1].riposte


def test_worked_light_case_follows_range_and_strike_before_movement_not_impossible_example():
    state = worked(ActionKind.LIGHT)
    advance_to(state, 3)
    # The t3 strike resolves before the player's next movement step at t4.
    assert any(e.type == EventType.WHIFF and e.actor == "ronin" for e in state.event_log)
    assert not state.fighters[0].wounds
    assert state.fighters[0].pos == (1, 0)


def test_weighted_production_example_uses_formulas_not_unweighted_timing_claims():
    state = worked(ActionKind.HEAVY, weight=3)
    player, enemy = state.fighters
    assert player.action.strike_tick == 9  # weighted three-tile approach, then scaled Light windup
    assert enemy.action.strike_tick == 7  # matched novice Heavy windup under Cutlass weight
    assert player.action.cost_paid == pytest.approx(state.config["actions"]["DASH_LIGHT"]["stamina"] * 1.06)
    assert player.fatigue > 0 and enemy.fatigue > 0


def test_passive_anger_delta_is_logged_once_not_twice():
    state = arena()
    fighter = state.fighters[0]
    fighter.anger = 30
    before = fighter.anger
    advance_one_tick(state)
    logged = sum(e.deltas.get("anger", 0) for e in state.event_log
                 if e.type == EventType.POOL_CHANGE and e.actor == fighter.id)
    assert logged == pytest.approx(fighter.anger - before)
