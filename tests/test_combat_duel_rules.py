from dataclasses import asdict
import json

import pytest

from pirates.combat_duel import (
    ActionKind,
    DuelAction,
    DuelDecisionState,
    Duelant,
    DuelStats,
    DuelWeapon,
    IncompleteOracleSearch,
    OraclePolicy,
    OraclePolicyConfig,
    SearchResult,
    TerminalResult,
    action_duration,
    advance_to_decision,
    apply_action,
    attack_force,
    can_reach,
    commit_action,
    counter_is_eligible,
    effective_swing_speed,
    fatigue_factor,
    resolve_contact,
    search_best_action,
    legal_actions,
    state_cycle_key,
    stamina_factor,
    terminal_result,
    tile_distance,
    duel_state_from_data,
)
from tools.combat_duel_headless import run_matrix


def test_contact_range_counts_diagonal_and_reach_advantage():
    short = DuelWeapon(reach_tiles=1)
    long = DuelWeapon(reach_tiles=2)
    assert tile_distance((2, 2), (3, 3)) == 1
    assert can_reach((2, 2), (3, 3), short)
    assert not can_reach((2, 2), (4, 2), short)
    assert can_reach((2, 2), (4, 2), long)
    assert not can_reach((2, 2), (5, 2), long)


def test_guided_duel_state_json_roundtrip_continues_identically_and_rejects_bad_data():
    weapon = DuelWeapon(reach_tiles=2, handling=92, mass=3)
    left = Duelant(DuelStats(stamina=74, fatigue=8), weapon, 0, 0)
    right = Duelant(DuelStats(speed=90), DuelWeapon(), 2, 0, facing=(-1, 0))
    state = commit_action(DuelDecisionState(0, left, right),
                          DuelAction(ActionKind.LIGHT))
    encoded = json.loads(json.dumps(asdict(state)))
    restored = duel_state_from_data(encoded)
    assert restored == state
    action = DuelAction(ActionKind.PARRY)
    assert apply_action(state, action) == apply_action(restored, action)

    malformed = json.loads(json.dumps(encoded))
    malformed["left"]["stats"]["stamina"] = True
    with pytest.raises(ValueError, match="integer"):
        duel_state_from_data(malformed)
    malformed = json.loads(json.dumps(encoded))
    malformed["extra"] = 1
    with pytest.raises(ValueError, match="fields"):
        duel_state_from_data(malformed)


def test_long_weapon_commits_first_contact_before_short_weapon_can_reach():
    spear = DuelWeapon(reach_tiles=2, handling=100, mass=2)
    sword = DuelWeapon(reach_tiles=1, handling=100, mass=1)
    spearman = Duelant(DuelStats(), spear, 0, 0, facing=(1, 0))
    swordsman = Duelant(DuelStats(), sword, 2, 0, facing=(-1, 0))
    state = DuelDecisionState(tick=0, left=spearman, right=swordsman)
    assert DuelAction(ActionKind.LIGHT) in legal_actions(state)
    right_turn = commit_action(state, DuelAction(ActionKind.LIGHT))
    assert DuelAction(ActionKind.LIGHT) not in legal_actions(right_turn)
    right_turn = commit_action(right_turn, DuelAction(ActionKind.DASH, 0, 1, 1))
    resolved = advance_to_decision(right_turn)
    thrust = next(event for event in resolved.events if event.kind == "light")
    assert thrust.outcome == "hit"
    assert resolved.state.right.vitality < 100


def test_speed_formula_has_exact_neutral_fatigue_and_empty_stamina_bounds():
    weapon = DuelWeapon()
    neutral = DuelStats()
    assert fatigue_factor(neutral) == 100
    assert stamina_factor(neutral) == 100
    assert effective_swing_speed(neutral, weapon) == 100
    assert action_duration(30, neutral, weapon) == 30

    fatigued = DuelStats(fatigue=100)
    exhausted = DuelStats(stamina=0)
    both = DuelStats(fatigue=100, stamina=0)
    assert effective_swing_speed(fatigued, weapon) == 50
    assert effective_swing_speed(exhausted, weapon) == 75
    assert effective_swing_speed(both, weapon) == 37
    assert action_duration(30, fatigued, weapon) == 60


def test_low_stamina_boundary_is_discrete_and_deterministic():
    weapon = DuelWeapon()
    at_threshold = DuelStats(stamina=30)
    below_threshold = DuelStats(stamina=29)
    assert stamina_factor(at_threshold) == 100
    assert stamina_factor(below_threshold) == 99
    assert effective_swing_speed(at_threshold, weapon) == 100
    assert effective_swing_speed(below_threshold, weapon) == 99


def test_aggressive_opening_can_be_parried_and_quick_counter_is_stat_gated():
    weapon = DuelWeapon()
    attacker = DuelStats()
    defender = DuelStats()
    result = resolve_contact(
        attacker, weapon, defender, weapon, base_force=8,
        parry_active=True, parry_facing=True,
    )
    assert result.outcome == "parried"
    assert counter_is_eligible(
        ticks_since_parry=0,
        defender_speed=effective_swing_speed(defender, weapon),
        opponent_speed=effective_swing_speed(attacker, weapon),
        in_reach=True, in_lane=True,
    )

    slower = DuelStats(swing_skill=80)
    assert not counter_is_eligible(
        ticks_since_parry=0,
        defender_speed=effective_swing_speed(slower, weapon),
        opponent_speed=effective_swing_speed(attacker, weapon),
        in_reach=True, in_lane=True,
    )
    assert not counter_is_eligible(
        ticks_since_parry=30, defender_speed=100, opponent_speed=100,
        in_reach=True, in_lane=True,
    )


def test_overwhelming_force_defeats_a_weak_low_structure_parry():
    attacker_weapon = DuelWeapon(mass=8)
    defender_weapon = DuelWeapon(handling=50)
    attacker = DuelStats(strength=8)
    defender = DuelStats(
        balance=0, torso_structure=0, left_arm_structure=0,
        right_arm_structure=0,
    )
    result = resolve_contact(
        attacker, attacker_weapon, defender, defender_weapon,
        base_force=14, parry_active=True, parry_facing=True,
    )
    assert result.force == attack_force(14, attacker, attacker_weapon)
    assert result.force > result.parry_capacity
    assert result.outcome == "overpowered"


def test_overpowered_parry_event_reports_force_and_capacity_cause():
    attacker_weapon = DuelWeapon(mass=8)
    defender_weapon = DuelWeapon(handling=50)
    attacker = Duelant(DuelStats(strength=8), attacker_weapon, 0, 0)
    defender = Duelant(DuelStats(balance=0, torso_structure=0,
                                 left_arm_structure=0, right_arm_structure=0),
                       defender_weapon, 1, 0)
    state = DuelDecisionState(0, attacker, defender)
    state = commit_action(state, DuelAction(ActionKind.LIGHT))
    state = commit_action(state, DuelAction(ActionKind.PARRY))
    event = next(event for event in advance_to_decision(state).events
                 if event.kind == "light")
    assert event.outcome == "overpowered"
    assert event.force > event.parry_capacity
    assert "exceeded parry capacity" in event.reason


def test_dash_escape_avoids_frozen_lane_but_dash_strike_has_exposure():
    weapon = DuelWeapon()
    attacker = DuelStats()
    defender = DuelStats()
    escaped = resolve_contact(
        attacker, weapon, defender, weapon, base_force=8, lane_contact=False,
    )
    assert escaped.outcome == "committed-lane-miss"
    assert attack_force(8, attacker, weapon, completed_dash_approach=True) == 19


def test_contact_result_does_not_give_actor_order_priority_on_equal_contacts():
    weapon = DuelWeapon()
    a = DuelStats()
    b = DuelStats()
    ab = resolve_contact(a, weapon, b, weapon, base_force=8)
    ba = resolve_contact(b, weapon, a, weapon, base_force=8)
    assert ab == ba
    assert ab.outcome == "hit"


def test_decision_state_generates_finite_legal_actions_by_range_and_resources():
    weapon = DuelWeapon(reach_tiles=1)
    left = Duelant(DuelStats(), weapon, 0, 0, facing=(1, 0))
    right = Duelant(DuelStats(), weapon, 1, 0, facing=(-1, 0))
    state = DuelDecisionState(tick=0, left=left, right=right)
    actions = legal_actions(state)
    assert DuelAction(ActionKind.LIGHT) in actions
    assert DuelAction(ActionKind.HEAVY) in actions
    assert DuelAction(ActionKind.PARRY) in actions
    assert DuelAction(ActionKind.WAIT, steps=1) in actions
    assert len(actions) == len(set(actions))
    assert all(action.kind in ActionKind for action in actions)

    far_right = Duelant(DuelStats(), weapon, 3, 0, facing=(-1, 0))
    far = DuelDecisionState(tick=0, left=left, right=far_right)
    far_actions = legal_actions(far)
    assert DuelAction(ActionKind.LIGHT) not in far_actions
    assert any(action.kind == ActionKind.DASH_STRIKE for action in far_actions)

    exhausted = Duelant(DuelStats(stamina=10), weapon, 0, 0, facing=(1, 0))
    poor = DuelDecisionState(tick=0, left=exhausted, right=right)
    poor_actions = legal_actions(poor)
    assert len(poor_actions) == 100
    assert all(action.kind == ActionKind.WAIT for action in poor_actions)


def test_decision_state_has_no_actions_before_ready_or_after_terminal():
    weapon = DuelWeapon()
    left = Duelant(DuelStats(), weapon, 0, 0, ready_at=5)
    right = Duelant(DuelStats(), weapon, 1, 0)
    assert legal_actions(DuelDecisionState(tick=0, left=left, right=right)) == ()
    dead = Duelant(DuelStats(), weapon, 0, 0, vitality=0)
    assert legal_actions(DuelDecisionState(tick=0, left=dead, right=right)) == ()


def test_search_state_has_explicit_win_mutual_ko_cycle_and_horizon_results():
    weapon = DuelWeapon()
    left = Duelant(DuelStats(), weapon, 0, 0)
    right = Duelant(DuelStats(), weapon, 1, 0)
    state = DuelDecisionState(tick=0, left=left, right=right)
    assert terminal_result(state) is None
    assert terminal_result(DuelDecisionState(
        tick=0, left=left, right=Duelant(DuelStats(), weapon, 1, 0, vitality=0),
    )) == TerminalResult.LEFT_WIN
    assert terminal_result(DuelDecisionState(
        tick=0, left=Duelant(DuelStats(), weapon, 0, 0, vitality=0),
        right=Duelant(DuelStats(), weapon, 1, 0, vitality=0),
    )) == TerminalResult.DRAW_MUTUAL_KO
    assert terminal_result(state, (state,)) == TerminalResult.DRAW_CYCLE
    shifted = DuelDecisionState(
        tick=10,
        left=Duelant(DuelStats(), weapon, 0, 0, ready_at=10, resource_tick=10),
        right=Duelant(DuelStats(), weapon, 1, 0, ready_at=10, resource_tick=10),
        plies=3,
    )
    assert state_cycle_key(state) == state_cycle_key(shifted)
    assert terminal_result(shifted, (state_cycle_key(state),)) == TerminalResult.DRAW_CYCLE
    horizon = DuelDecisionState(tick=0, left=left, right=right, plies=4, max_plies=4)
    assert terminal_result(horizon) == TerminalResult.DRAW_HORIZON


def test_timed_aggressive_opener_parry_and_fast_counter_sequence():
    weapon = DuelWeapon()
    left = Duelant(DuelStats(), weapon, 0, 0, facing=(1, 0))
    right = Duelant(DuelStats(), weapon, 1, 0, facing=(-1, 0))
    state = DuelDecisionState(tick=0, left=left, right=right)
    state = commit_action(state, DuelAction(ActionKind.LIGHT))
    state = commit_action(state, DuelAction(ActionKind.PARRY))
    transition = advance_to_decision(state)

    assert [event.outcome for event in transition.events if event.kind == "light"] == ["parried"]
    assert transition.state.tick == 31
    assert transition.state.right.counter_until == 61
    assert transition.state.right.vitality == 100

    counter = commit_action(transition.state, DuelAction(ActionKind.LIGHT))
    intent = counter.right.intent
    assert intent is not None and intent.counter
    assert intent.impact_tick - intent.accepted_tick == action_duration(
        15, counter.right.stats, weapon,
    )
    assert counter.right.counter_until == -1


def test_lower_speed_still_parries_but_does_not_get_quick_counter_commitment():
    weapon = DuelWeapon()
    left = Duelant(DuelStats(), weapon, 0, 0)
    right = Duelant(DuelStats(swing_skill=80), weapon, 1, 0)
    state = DuelDecisionState(tick=0, left=left, right=right)
    state = commit_action(state, DuelAction(ActionKind.LIGHT))
    state = commit_action(state, DuelAction(ActionKind.PARRY))
    state = advance_to_decision(state).state
    assert state.right.counter_until == 61
    slower_counter = commit_action(state, DuelAction(ActionKind.LIGHT))
    assert not slower_counter.right.intent.counter


def test_same_tick_attacks_resolve_from_one_snapshot_without_actor_priority():
    weapon = DuelWeapon()
    left = Duelant(DuelStats(), weapon, 0, 0)
    right = Duelant(DuelStats(), weapon, 1, 0)
    state = DuelDecisionState(tick=0, left=left, right=right)
    state = commit_action(state, DuelAction(ActionKind.LIGHT))
    state = commit_action(state, DuelAction(ActionKind.LIGHT))
    result = advance_to_decision(state)
    hits = [event for event in result.events if event.kind == "light"]
    assert len(hits) == 2
    assert [event.damage for event in hits] == [9, 9]
    assert all("simultaneous contact" in event.reason for event in hits)
    assert result.state.left.vitality == result.state.right.vitality == 91


def test_dash_can_evade_a_frozen_heavy_lane_without_invulnerability():
    weapon = DuelWeapon()
    left = Duelant(DuelStats(), weapon, 0, 0)
    right = Duelant(DuelStats(), weapon, 1, 0)
    state = DuelDecisionState(tick=0, left=left, right=right)
    state = commit_action(state, DuelAction(ActionKind.HEAVY))
    state = commit_action(state, DuelAction(ActionKind.DASH, 0, 1, 1))
    result = advance_to_decision(state)
    strike = next(event for event in result.events if event.kind == "heavy")
    assert strike.outcome == "committed-lane-miss"
    assert result.state.right.pos == (1, 1)
    assert result.state.right.vitality == 100


def test_dash_has_no_invulnerability_before_its_first_scheduled_tile():
    weapon = DuelWeapon()
    left = Duelant(DuelStats(), weapon, 0, 0)
    right = Duelant(DuelStats(), weapon, 1, 0)
    state = DuelDecisionState(tick=0, left=left, right=right)
    state = commit_action(state, DuelAction(ActionKind.LIGHT))
    state = commit_action(state, DuelAction(ActionKind.DASH, 0, 1, 1))
    result = advance_to_decision(state)
    light = next(event for event in result.events if event.kind == "light")
    assert light.outcome == "hit"
    assert result.state.right.vitality < 100


def test_wait_recovery_and_dash_strike_commitment_are_deterministic():
    weapon = DuelWeapon()
    left = Duelant(DuelStats(), weapon, 0, 0)
    right = Duelant(DuelStats(), weapon, 3, 0, facing=(-1, 0))
    state = DuelDecisionState(tick=0, left=left, right=right)
    committed = commit_action(state, DuelAction(ActionKind.DASH_STRIKE, 1, 0, 2))
    strike = committed.left.intent
    assert strike.path == ((1, 0), (2, 0))
    assert strike.target_pos == right.pos
    assert strike.impact_tick > strike.path_ticks[-1]
    assert committed.left.stats.stamina == 40

    waiting = Duelant(DuelStats(stamina=20, fatigue=12), weapon, 0, 0)
    idle = Duelant(DuelStats(), weapon, 4, 0)
    wait_state = DuelDecisionState(tick=0, left=waiting, right=idle)
    wait_state = commit_action(wait_state, DuelAction(ActionKind.WAIT, steps=100))
    wait_state = commit_action(wait_state, DuelAction(ActionKind.WAIT, steps=100))
    recovered = advance_to_decision(wait_state).state
    assert recovered.tick == 100
    assert recovered.left.stats.stamina == 42
    assert recovered.left.stats.fatigue == 8


def test_delayed_parry_catches_a_dash_strike_after_its_approach():
    weapon = DuelWeapon()
    left = Duelant(DuelStats(), weapon, 0, 0)
    right = Duelant(DuelStats(), weapon, 3, 0, facing=(-1, 0))
    state = DuelDecisionState(tick=0, left=left, right=right)
    state = commit_action(state, DuelAction(ActionKind.DASH_STRIKE, 1, 0, 2))
    assert DuelAction(ActionKind.WAIT, steps=45) in legal_actions(state)
    state = commit_action(state, DuelAction(ActionKind.WAIT, steps=45))
    state = advance_to_decision(state).state
    assert state.tick == 45 and state.to_act == 1
    state = commit_action(state, DuelAction(ActionKind.PARRY))
    result = advance_to_decision(state)
    strike = next(event for event in result.events
                  if event.kind == "dash_strike" and event.outcome == "parried")
    assert strike.outcome == "parried"
    assert result.state.right.vitality == 100


def test_oracle_finds_forced_kill_while_opponent_is_still_recovering():
    weapon = DuelWeapon()
    state = DuelDecisionState(
        tick=0,
        left=Duelant(DuelStats(), weapon, 0, 0),
        right=Duelant(DuelStats(stamina=0), weapon, 1, 0, vitality=9, ready_at=100),
    )
    first = search_best_action(state, depth=2, node_budget=20_000)
    second = search_best_action(state, depth=2, node_budget=20_000)
    assert isinstance(first, SearchResult)
    assert first == second
    assert first.action == DuelAction(ActionKind.LIGHT)
    assert first.score > 1_000_000
    assert first.complete
    assert first.nodes < 20_000
    assert first.principal_variation[0] == first.action


def test_oracle_search_preserves_seat_swap_score_symmetry():
    weapon = DuelWeapon()
    left = Duelant(DuelStats(), weapon, 0, 0)
    right = Duelant(DuelStats(), weapon, 1, 0)
    state0 = DuelDecisionState(tick=0, left=left, right=right, to_act=0)
    state1 = DuelDecisionState(tick=0, left=right, right=left, to_act=1)
    first = search_best_action(state0, depth=2, node_budget=20_000)
    second = search_best_action(state1, depth=2, node_budget=20_000)
    assert first.complete and second.complete
    assert first.score == -second.score
    assert first.action == second.action


def test_node_budget_exhaustion_is_reported_as_incomplete_not_a_proof():
    weapon = DuelWeapon()
    state = DuelDecisionState(
        tick=0,
        left=Duelant(DuelStats(), weapon, 0, 0),
        right=Duelant(DuelStats(), weapon, 1, 0),
    )
    result = search_best_action(state, depth=6, node_budget=1)
    assert not result.complete
    assert result.action in legal_actions(state)


def test_oracle_policy_has_explicit_profile_and_rejects_incomplete_or_wrong_inputs():
    weapon = DuelWeapon()
    state = DuelDecisionState(
        tick=0,
        left=Duelant(DuelStats(), weapon, 0, 0),
        right=Duelant(DuelStats(), weapon, 1, 0),
    )
    policy = OraclePolicy(OraclePolicyConfig(depth=2, node_budget=100_000))
    assert policy.profile_id == "duel_oracle"
    assert policy.choose_action(state) in legal_actions(state)
    try:
        policy.analyze(object())
    except TypeError as error:
        assert "DuelDecisionState" in str(error)
    else:
        raise AssertionError("oracle accepted a non-search/live object")
    incomplete = OraclePolicy(OraclePolicyConfig(depth=6, node_budget=1))
    try:
        incomplete.choose_action(state)
    except IncompleteOracleSearch:
        pass
    else:
        raise AssertionError("oracle silently accepted an incomplete search")


def test_repeatable_wait_loop_is_detected_as_a_draw_cycle():
    weapon = DuelWeapon()
    left = Duelant(DuelStats(), weapon, 0, 0)
    right = Duelant(DuelStats(), weapon, 1, 0)
    start = DuelDecisionState(tick=0, left=left, right=right)
    state = commit_action(start, DuelAction(ActionKind.WAIT, steps=1))
    state = commit_action(state, DuelAction(ActionKind.WAIT, steps=1))
    repeated = advance_to_decision(state).state
    assert state_cycle_key(repeated) == state_cycle_key(start)
    assert terminal_result(repeated, (state_cycle_key(start),)) == TerminalResult.DRAW_CYCLE


def test_headless_equal_stat_matrix_is_bounded_and_seat_symmetric():
    report = run_matrix(depth=2, node_budget=100_000)
    assert report["all_searches_complete"]
    assert report["all_seat_symmetry_checks_pass"]
    assert report["unbounded_nonloss_proven"] is False
    search_rows = [row for row in report["results"] if "complete" in row]
    assert len(search_rows) == 10
    assert all(row["score_left_perspective"] == 0 for row in search_rows)


def test_equal_stat_nonloss_sample_is_complete_only_to_three_plies():
    weapon = DuelWeapon()
    state = DuelDecisionState(
        tick=0,
        left=Duelant(DuelStats(), weapon, 0, 0),
        right=Duelant(DuelStats(), weapon, 1, 0),
    )
    result = search_best_action(state, depth=3, node_budget=200_000)
    assert result.complete
    assert result.score == 0
    assert result.action == DuelAction(ActionKind.WAIT, steps=1)
