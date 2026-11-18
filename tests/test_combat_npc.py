from dataclasses import replace

import pytest

from pirates.combat_beliefs import CombatBelief, PublicObservation
from pirates.combat_duel import (
    ActionKind,
    DuelAction,
    DuelDecisionState,
    DuelStats,
    DuelWeapon,
    Duelant,
    action_duration,
    advance_to_decision,
    apply_action,
    commit_action,
    legal_actions,
)
from pirates.combat_npc import (
    BehaviorProfile,
    IntelligenceTier,
    NPCObservation,
    ObservationLimitedPolicy,
    VisibleCommitment,
)


def observation(*, own_stats=None, own_pos=(0, 0), opponent_pos=(1, 0),
                commitment=None, opponent_weapon=None, options=None,
                counter_window=False):
    own_stats = own_stats or DuelStats()
    weapon = DuelWeapon()
    if options is None:
        target_weapon = opponent_weapon or DuelWeapon()
        state = DuelDecisionState(
            tick=0,
            left=Duelant(own_stats, weapon, *own_pos),
            right=Duelant(DuelStats(), target_weapon, *opponent_pos),
        )
        options = legal_actions(state)
    return NPCObservation(
        tick=0, own_pos=own_pos, own_facing=(1, 0),
        own_stats=own_stats, own_weapon=weapon,
        opponent_pos=opponent_pos, opponent_facing=(-1, 0),
        opponent_weapon=opponent_weapon,
        visible_commitment=commitment, belief=CombatBelief(),
        legal_actions=tuple(options), counter_window=counter_window,
    )


def test_tiers_are_repeatable_and_low_reaction_quality_makes_exploitable_error():
    seen_attack = VisibleCommitment(
        action=ActionKind.LIGHT, target_pos=(0, 0), impact_tick=20,
        force_upper_bound=20,
    )
    view = observation(commitment=seen_attack, opponent_weapon=DuelWeapon())
    limited = ObservationLimitedPolicy(IntelligenceTier.LIMITED)
    disciplined = ObservationLimitedPolicy(IntelligenceTier.DISCIPLINED)

    assert limited.choose_action(view) == limited.choose_action(view)
    assert limited.choose_action(view).kind == ActionKind.LIGHT
    assert disciplined.choose_action(view).kind == ActionKind.PARRY


def test_inference_uses_belief_upper_bound_and_visible_weapon_without_hidden_stats():
    seen_attack = VisibleCommitment(
        action=ActionKind.HEAVY, target_pos=(0, 0), impact_tick=100,
        force_upper_bound=None,
    )
    view = observation(commitment=seen_attack,
                       opponent_weapon=DuelWeapon(mass=1))
    result = ObservationLimitedPolicy(IntelligenceTier.INFORMED).choose_action(view)
    assert result.kind == ActionKind.PARRY


def test_no_parry_is_selected_when_public_force_bound_exceeds_exact_capacity():
    seen_attack = VisibleCommitment(
        action=ActionKind.HEAVY, target_pos=(0, 0), impact_tick=100,
        force_upper_bound=100,
    )
    weak_guard = replace(
        DuelStats(), balance=0, torso_structure=0,
        left_arm_structure=0, right_arm_structure=0,
    )
    view = observation(own_stats=weak_guard, commitment=seen_attack,
                       opponent_weapon=DuelWeapon())
    result = ObservationLimitedPolicy(IntelligenceTier.DISCIPLINED).choose_action(view)
    assert result.kind == ActionKind.DASH


def test_no_visible_opponent_uses_shortest_deterministic_wait():
    view = observation(options=(DuelAction(ActionKind.WAIT, steps=12),
                               DuelAction(ActionKind.WAIT, steps=3)))
    view = NPCObservation(
        tick=view.tick, own_pos=view.own_pos, own_facing=view.own_facing,
        own_stats=view.own_stats, own_weapon=view.own_weapon,
        opponent_pos=(3, 0), opponent_facing=view.opponent_facing,
        opponent_weapon=view.opponent_weapon, visible_commitment=None,
        belief=view.belief, legal_actions=view.legal_actions,
    )
    action = ObservationLimitedPolicy(IntelligenceTier.LIMITED).choose_action(view)
    assert action == DuelAction(ActionKind.WAIT, steps=3)


def test_planning_limits_change_contact_vs_contact_plus_recovery_choice():
    options = (
        DuelAction(ActionKind.DASH_STRIKE, 1, 0, 1),
        DuelAction(ActionKind.HEAVY),
    )
    view = observation(options=options)
    informed = ObservationLimitedPolicy(IntelligenceTier.INFORMED)
    disciplined = ObservationLimitedPolicy(IntelligenceTier.DISCIPLINED)
    assert informed.config.planning_depth == 1
    assert disciplined.config.planning_depth == 2
    # Informed optimizes the first known contact; disciplined includes action
    # recovery in its two-stage deterministic comparison.
    assert informed.choose_action(view).kind == ActionKind.DASH_STRIKE
    assert disciplined.choose_action(view).kind == ActionKind.HEAVY


def test_observation_limited_policy_rejects_full_duel_state_and_actor_inputs():
    weapon = DuelWeapon()
    state = DuelDecisionState(
        0, Duelant(DuelStats(), weapon, 0, 0),
        Duelant(DuelStats(), weapon, 1, 0),
    )
    policy = ObservationLimitedPolicy(IntelligenceTier.DISCIPLINED)
    with pytest.raises(TypeError, match="NPCObservation"):
        policy.choose_action(state)
    with pytest.raises(TypeError, match="NPCObservation"):
        policy.choose_action(state.left)


def test_first_mover_tell_matches_repeatable_no_parry_behavior():
    seen_attack = VisibleCommitment(
        action=ActionKind.LIGHT, target_pos=(0, 0), impact_tick=40,
        force_upper_bound=20,
    )
    view = observation(commitment=seen_attack)
    policy = ObservationLimitedPolicy(
        IntelligenceTier.DISCIPLINED, BehaviorProfile.FIRST_MOVER,
    )
    assert policy.tell == "Rushes to strike first; does not parry."
    action = policy.choose_action(view)
    assert action.kind == ActionKind.HEAVY
    assert action.kind != ActionKind.PARRY


def test_dash_and_slash_specialist_prefers_contextual_committed_strike():
    view = observation(options=(
        DuelAction(ActionKind.LIGHT),
        DuelAction(ActionKind.DASH_STRIKE, 1, 0, 2),
        DuelAction(ActionKind.HEAVY),
    ))
    policy = ObservationLimitedPolicy(
        IntelligenceTier.INFORMED, BehaviorProfile.DASH_AND_SLASH,
    )
    assert "dash-strike" in policy.tell
    assert policy.choose_action(view) == DuelAction(
        ActionKind.DASH_STRIKE, 1, 0, 2,
    )


def test_patient_counter_profile_waits_for_evidence_then_parries_or_counters():
    patient = ObservationLimitedPolicy(
        IntelligenceTier.DISCIPLINED, BehaviorProfile.PATIENT_COUNTER,
    )
    waiting = observation()
    assert patient.choose_action(waiting) == DuelAction(ActionKind.WAIT, steps=10)

    seen_attack = VisibleCommitment(
        action=ActionKind.LIGHT, target_pos=(0, 0), impact_tick=100,
        force_upper_bound=20,
    )
    threatened = observation(commitment=seen_attack)
    assert patient.choose_action(threatened).kind == ActionKind.PARRY

    counter_ready = observation(counter_window=True)
    assert patient.choose_action(counter_ready).kind == ActionKind.LIGHT


def test_lower_strength_player_exploits_first_mover_heavy_with_timed_parry_counter():
    weapon = DuelWeapon()
    player = Duelant(DuelStats(strength=0), weapon, 0, 0, facing=(1, 0))
    rusher_stats = replace(DuelStats(), strength=7)
    rusher = Duelant(rusher_stats, weapon, 1, 0, facing=(-1, 0))
    npc_turn = DuelDecisionState(0, player, rusher, to_act=1)
    rusher_view = NPCObservation(
        tick=0, own_pos=rusher.pos, own_facing=rusher.facing,
        own_stats=rusher.stats, own_weapon=weapon,
        opponent_pos=player.pos, opponent_facing=player.facing,
        opponent_weapon=weapon, visible_commitment=None,
        belief=CombatBelief(), legal_actions=legal_actions(npc_turn),
    )
    first_mover = ObservationLimitedPolicy(
        IntelligenceTier.DISCIPLINED, BehaviorProfile.FIRST_MOVER,
    )
    heavy = first_mover.choose_action(rusher_view)
    assert heavy.kind == ActionKind.HEAVY
    state = commit_action(npc_turn, heavy)

    threat = state.right.intent
    assert threat is not None
    parry_stats = replace(player.stats, stamina=player.stats.stamina - 20,
                          fatigue=player.stats.fatigue + 2)
    startup = action_duration(10, parry_stats, weapon)
    window = action_duration(35, parry_stats, weapon)
    delay = threat.impact_tick - state.tick - startup - window + 1
    wait = DuelAction(ActionKind.WAIT, steps=delay)
    state = apply_action(state, wait).state
    state = commit_action(state, DuelAction(ActionKind.PARRY))
    result = advance_to_decision(state)
    assert any(event.outcome == "parried" for event in result.events)
    assert result.state.left.vitality == 100

    counter = commit_action(result.state, DuelAction(ActionKind.LIGHT))
    assert counter.left.intent.counter
    resolved = advance_to_decision(counter)
    assert any(event.kind == "light" and event.actor == 0 and event.damage > 0
               for event in resolved.events)
    assert resolved.state.right.vitality < 100


def test_new_public_pattern_evidence_changes_future_response_from_dash_to_parry():
    weak_guard = replace(
        DuelStats(), balance=0, torso_structure=0,
        left_arm_structure=0, right_arm_structure=0,
    )
    commitment = VisibleCommitment(
        action=ActionKind.HEAVY, target_pos=(0, 0), impact_tick=100,
        force_upper_bound=None,
    )
    weapon = DuelWeapon(mass=1)
    initial = observation(own_stats=weak_guard, commitment=commitment,
                          opponent_weapon=weapon)
    policy = ObservationLimitedPolicy(IntelligenceTier.INFORMED)
    before_evidence = policy.choose_action(initial)
    assert before_evidence.kind == ActionKind.DASH

    learned = initial.belief.observe(PublicObservation(
        event_id="visible-light-force", kind="force_seen", tick=20,
        action_kind="light", observed_force=9,
    ))
    informed = replace(initial, belief=learned)
    after_evidence = policy.choose_action(informed)
    assert learned.strength.low == learned.strength.high == 0
    assert after_evidence.kind == ActionKind.PARRY
    assert policy.choose_action(informed) == after_evidence
