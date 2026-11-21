"""M5 mental-resource, end-state and worked-example formula gates."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from pirates.duel_system.config import load_config
from pirates.duel_system.engine import Choice, advance_one_tick, check_end_conditions, commit_action
from pirates.duel_system.mental import (
    change_anger, change_fear, fear_decision, initialize_fight, postfight_state,
    refresh_behavior_modes,
)
from pirates.duel_system.model import (
    ActionKind, CombatState, Equipment, EventType, Personality, Severity, Weapon, Wound, Zone,
)
from pirates.duel_system.stats import effective_stats, fear, pain_burden, pay_stamina
from test_duel_system_foundation import bare_fighter
from duel_test_helpers import force_clean_aims
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


def test_shared_behavior_mode_edges_hysteresis_and_private_transition_events():
    state = arena()
    fighter = state.fighters[0]
    fighter.personality = Personality(**deepcopy(state.config["ronin"]))
    fighter.personality.pain_tolerance = 0

    fighter.pain = 49.99
    assert refresh_behavior_modes(state, fighter) == ()
    fighter.pain = 50
    assert refresh_behavior_modes(state, fighter, "TEST") == (("pain", "NONE", "PROTECTIVE"),)
    fighter.pain = 40
    assert refresh_behavior_modes(state, fighter, "TEST") == ()
    fighter.pain = 39.99
    assert refresh_behavior_modes(state, fighter, "TEST") == (("pain", "PROTECTIVE", "NONE"),)
    fighter.fear_meter = 100
    assert refresh_behavior_modes(state, fighter, "TEST") == (("fear", "NONE", "BREAK"),)
    fighter.fear_meter = 90
    assert refresh_behavior_modes(state, fighter, "TEST") == ()
    fighter.fear_meter = 89.99
    assert refresh_behavior_modes(state, fighter, "TEST") == (("fear", "BREAK", "CAUTIOUS"),)
    rows = [event for event in state.event_log if event.type == EventType.BEHAVIOR_MODE]
    assert [row.deltas["threshold"] for row in rows] == [50, 40, 100, 90]
    assert [row.deltas["cause"] for row in rows] == ["TEST"] * 4
    assert [row.deltas["mode_after"] for row in rows] == [
        "PROTECTIVE", "NONE", "BREAK", "CAUTIOUS"]
    assert all(row.witnesses == () for row in rows)


def test_anger_mode_edges_use_shared_thresholds_and_hysteresis():
    state = arena()
    fighter = state.fighters[0]
    fighter.anger = 49.99
    assert refresh_behavior_modes(state, fighter) == ()
    fighter.anger = 50
    assert refresh_behavior_modes(state, fighter, "ANGER_TEST") == (("anger", "NONE", "PRESS"),)
    fighter.anger = 40
    assert refresh_behavior_modes(state, fighter, "ANGER_TEST") == ()
    fighter.anger = 39.99
    assert refresh_behavior_modes(state, fighter, "ANGER_TEST") == (("anger", "PRESS", "NONE"),)
    fighter.anger = 100
    assert refresh_behavior_modes(state, fighter, "ANGER_TEST") == (("anger", "NONE", "HOT"),)
    fighter.anger = 90
    assert refresh_behavior_modes(state, fighter, "ANGER_TEST") == ()
    fighter.anger = 89.99
    assert refresh_behavior_modes(state, fighter, "ANGER_TEST") == (("anger", "HOT", "PRESS"),)
    assert [event.deltas["threshold"] for event in state.event_log
            if event.type == EventType.BEHAVIOR_MODE] == [50, 40, 100, 90]


def test_fear_gain_susceptibility_scales_positive_gain_only_and_latches_once():
    state = arena()
    fighter = state.fighters[1]
    profile = deepcopy(state.config["ronin"])
    profile["fear_gain_susceptibility"] = state.config["ai_profiles"]["recruit"][
        "fear_gain_susceptibility"]
    profile["archetype"] = "recruit"
    fighter.personality = Personality(**profile)
    fighter.fear_meter = 49
    change_fear(state, fighter, 2, "TEST_GAIN")
    assert fighter.fear_meter == pytest.approx(51.3)
    assert fighter.behavior_modes["fear"] == "CAUTIOUS"
    assert sum(event.type == EventType.BEHAVIOR_MODE for event in state.event_log) == 1
    change_fear(state, fighter, -1, "TEST_DECAY")
    assert fighter.fear_meter == pytest.approx(50.3)
    assert sum(event.type == EventType.BEHAVIOR_MODE for event in state.event_log) == 1


def test_group_behavior_transition_is_actor_private():
    from pirates.duel_system.mental import refresh_behavior_modes

    actors = [bare_fighter("player", pos=(1, 1), team_id="player"),
              bare_fighter("ally", pos=(3, 1), team_id="allies"),
              bare_fighter("opponent", pos=(4, 1), team_id="opponents")]
    state = CombatState(actors)
    opponent = state.fighter("opponent")
    opponent.fear_meter = 50
    refresh_behavior_modes(state, opponent, "TEST")
    event = next(event for event in state.event_log if event.type == EventType.BEHAVIOR_MODE)
    assert event.witnesses == ("opponent",)
    assert not event.hearers


def test_threshold_event_readout_names_meter_value_threshold_and_cause():
    from pirates.duel_system.render_model import event_line

    state = arena()
    fighter = state.fighters[0]
    fighter.fear_meter = 50
    refresh_behavior_modes(state, fighter, "VISIBLE_TEST_CAUSE")
    event = next(event for event in state.event_log if event.type == EventType.BEHAVIOR_MODE)
    assert event_line(event, {fighter.id: "You"}) == (
        "You: fear cautious at 50/100 (threshold 50; visible test cause)")


def test_threshold_crossing_preserves_committed_action_and_mode_roundtrips():
    state = arena(rng_seed=992)
    fighter = state.fighters[1]
    action = commit_action(state, fighter.id, Choice(ActionKind.WAIT))
    change_fear(state, fighter, 100, "TEST_CRITICAL")
    assert fighter.behavior_modes["fear"] == "BREAK"
    assert fighter.action is action
    assert not fighter.yielded
    restored = CombatState.from_data(state.to_data())
    assert restored.fighter(fighter.id).behavior_modes == fighter.behavior_modes
    assert restored.fighter(fighter.id).action == action


def test_two_hits_without_return_increase_anger_and_own_hit_resets_counter():
    from pirates.duel_system.wounds import resolve_strike, defense_snapshot
    state = arena()
    attacker, defender = state.fighters
    commit_action(state, attacker.id, Choice(ActionKind.LIGHT, Zone.TORSO))
    resolve_strike(state, attacker, defender, defense_snapshot(state, defender))
    first = defender.anger
    resolve_strike(state, attacker, defender, defense_snapshot(state, defender))
    assert defender.anger - first == pytest.approx(9.375)
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


def test_fight_uses_numeric_starting_fear_and_separate_morale_bias():
    cfg = load_config()
    recruit_personality = Personality(**deepcopy(cfg["ronin"]))
    veteran_personality = Personality(**deepcopy(cfg["ronin"]))
    recruit_personality.starting_fear = cfg["ai_profiles"]["recruit"]["starting_fear"]
    veteran_personality.starting_fear = cfg["ai_profiles"]["veteran"]["starting_fear"]
    recruit = bare_fighter("recruit", personality=recruit_personality)
    veteran = bare_fighter("veteran", personality=veteran_personality)
    recruit_personality.morale_bias, veteran_personality.morale_bias = 25, -20
    recruit.morale, veteran.morale = 100, 0
    state = initialize_fight([recruit, veteran], config=cfg, rng_seed=7)
    assert fear(state.fighters[0], cfg) == pytest.approx(80)
    assert fear(state.fighters[1], cfg) == pytest.approx(30)
    assert state.fighters[0].morale != state.fighters[1].morale


def test_hit_pain_and_continuing_bleed_raise_fear_once_per_resolved_cause():
    from pirates.duel_system.wounds import apply_wound
    state = arena()
    attacker, defender = state.fighters
    before = fear(defender, state.config)
    apply_wound(state, attacker, defender, Zone.TORSO, Severity.CUT,
                power=10, net=12, site="CHEST")
    after_hit = fear(defender, state.config)
    assert after_hit > before
    assert any(event.type == EventType.POOL_CHANGE and "HIT_PAIN_BLEED" in event.cause_tags
               for event in state.event_log)
    state.fighters[1].action = None
    advance_one_tick(state)
    assert fear(defender, state.config) > after_hit
    assert any(event.type == EventType.POOL_CHANGE and "CONTINUING_BLEED" in event.cause_tags
               for event in state.event_log)


def test_anger_tolerance_moderates_positive_gain_but_not_decay():
    state = arena()
    fighter = state.fighters[0]
    fighter.personality = Personality(**deepcopy(state.config["ronin"]))
    fighter.personality.anger_tolerance = 0.5
    change_anger(state, fighter, 20, "TEST_HIT")
    assert fighter.anger == 10
    change_anger(state, fighter, -4, "TEST_CALM")
    assert fighter.anger == 6
    fighter.personality.anger_tolerance = 1
    change_anger(state, fighter, 20, "STOIC")
    assert fighter.anger == 6


def test_pain_tolerance_reduces_effective_pain_without_erasing_raw_injury_burden():
    state = arena()
    fighter = state.fighters[0]
    fighter.pain = 80
    fighter.personality = Personality(**deepcopy(state.config["ronin"]))
    fighter.personality.pain_tolerance = 0
    untrained_burden = pain_burden(fighter, state.config)
    untrained_speed = effective_stats(fighter, state.config).speed
    fighter.personality.pain_tolerance = 1
    trained_burden = pain_burden(fighter, state.config)
    trained_speed = effective_stats(fighter, state.config).speed
    assert fighter.pain == 80
    assert trained_burden < untrained_burden
    assert trained_speed > untrained_speed


def test_fear_and_anger_effective_stats_and_balance_are_monotonic():
    from pirates.duel_system.stability import balance_rating
    state = arena()
    fighter = state.fighters[0]
    fighter.fear_meter = 0
    fighter.anger = 0
    calm = effective_stats(fighter, state.config)
    calm_balance = balance_rating(fighter, state.config, state.tick)
    fighter.fear_meter = 100
    fearful = effective_stats(fighter, state.config)
    assert fearful.speed < calm.speed
    assert fearful.accuracy < calm.accuracy
    fighter.fear_meter = 0
    fighter.anger = 100
    angry = effective_stats(fighter, state.config)
    assert angry.speed > calm.speed
    assert angry.strength > calm.strength
    assert angry.accuracy < calm.accuracy
    assert balance_rating(fighter, state.config, state.tick) < calm_balance


def test_legacy_save_folds_morale_fear_and_nervousness_into_fear_pool():
    state = arena()
    data = state.to_data()
    row = data["fighters"][0]
    row.pop("fear_meter")
    row["morale"], row["anger"], row["nervousness"] = 40, 20, 0.3
    restored = CombatState.from_data(data)
    fighter = restored.fighters[0]
    assert fear(fighter, restored.config) == pytest.approx(40)
    assert fighter.nervousness == 0
    assert CombatState.from_data(restored.to_data()).fighters[0].fear_meter == pytest.approx(40)


def test_v21_session_load_migrates_fear_and_drops_live_nervousness():
    from pirates.duel_system.session import DuelSession
    session = DuelSession.new(guide_mode="off")
    data = deepcopy(session.to_data())
    data["version"] = data["state"]["schema_version"] = data["state"]["config"]["schema_version"] = 21
    row = next(item for item in data["state"]["fighters"] if item["id"] == session.player.id)
    row.pop("fear_meter")
    row["morale"], row["anger"], row["nervousness"] = 40, 20, 0.3
    restored = DuelSession.from_data(data)
    assert fear(restored.player, restored.state.config) == pytest.approx(40)
    assert restored.player.nervousness == 0
    assert restored.to_data()["version"] == 30


def worked(kind, weight=0, *, tiles=3, enemy_pos=(4, 0), aim=Zone.HAND):
    cfg = load_config()
    force_clean_aims(cfg)
    fighters = [bare_fighter("player", pos=(0, 0), equipment=Equipment(Weapon(weight=weight)), **cfg["fixtures"]["dorime"]),
                bare_fighter("ronin", pos=enemy_pos, equipment=Equipment(Weapon(weight=weight)), **cfg["fixtures"]["ronin"])]
    fighters[0].accuracy = 20  # Explicit called-hand fixture; the matched novice baseline remains Accuracy 10.
    state = CombatState(fighters, config=cfg)
    commit_action(state, "player", Choice(ActionKind.DASH_LIGHT, aim, (1, 0), tiles))
    commit_action(state, "ronin", Choice(kind, Zone.TORSO if kind in {ActionKind.LIGHT, ActionKind.HEAVY} else None))
    return state


def test_worked_unweighted_dash_light_interrupts_heavy_and_guard_parries_covered_arm():
    state = worked(ActionKind.HEAVY, tiles=1, enemy_pos=(2, 0))
    assert state.fighters[0].action.strike_tick == 5
    assert state.fighters[1].action.strike_tick == 7
    advance_to(state, 5)
    hit = next(e for e in state.event_log if e.type == EventType.HIT)
    assert (hit.zone, hit.severity) == (Zone.HAND, Severity.CUT)
    assert hit.deltas["power"] == 14
    assert state.fighters[1].action is None
    state = worked(ActionKind.GUARD, tiles=1, enemy_pos=(2, 0), aim=Zone.ARM)
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
