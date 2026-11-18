import pytest

from pirates.combat_beliefs import CombatBelief, IntInterval, PublicObservation
from pirates.combat_duel import DuelWeapon


def test_beliefs_start_wide_and_visible_weapon_identity_narrows_only_weapon_facts():
    belief = CombatBelief()
    assert belief.weapon_reach == IntInterval(1, 4)
    assert belief.strength == IntInterval(0, 8)
    assert belief.body_stability == IntInterval(0, 100)
    known = belief.observe(PublicObservation(
        event_id="e1", kind="weapon_seen", tick=0,
        weapon=DuelWeapon(reach_tiles=2, handling=125, mass=3),
    ))
    assert known.weapon_reach == IntInterval(2, 2)
    assert known.weapon_handling == IntInterval(125, 125)
    assert known.weapon_mass == IntInterval(3, 3)
    assert known.strength == belief.strength
    assert known.body_stability == belief.body_stability
    assert known.evidence[-1].event_id == "e1"
    assert known.evidence[-1].intervals == (
        ("weapon_handling", IntInterval(125, 125)),
        ("weapon_mass", IntInterval(3, 3)),
        ("weapon_reach", IntInterval(2, 2)),
    )


def test_public_timing_observation_narrows_speed_to_exact_ceil_interval():
    belief = CombatBelief().observe(PublicObservation(
        event_id="timed-light-1", kind="timed_action", tick=31,
        base_ticks=30, elapsed_ticks=31,
    ))
    assert belief.effective_swing_speed == IntInterval(97, 99)
    assert belief.observed_event_ids == ("timed-light-1",)


def test_action_acceptance_and_exhaustion_only_bound_stamina_not_exact_values():
    accepted = CombatBelief().observe(PublicObservation(
        event_id="light-start", kind="action_started", tick=0,
        stamina_cost=40, fatigue_cost=4, available=True,
    ))
    assert accepted.stamina == IntInterval(0, 60)
    assert accepted.fatigue == IntInterval(4, 100)
    rejected = CombatBelief().observe(PublicObservation(
        event_id="heavy-rejected", kind="action_started", tick=1,
        stamina_cost=70, fatigue_cost=8, available=False,
    ))
    assert rejected.stamina == IntInterval(0, 69)
    assert rejected.fatigue == IntInterval(0, 100)


def test_parry_comparison_infers_capacity_inequality_not_hidden_strength():
    belief = CombatBelief().observe(PublicObservation(
        event_id="force-known-publicly", kind="parry_comparison", tick=10,
        parry_force=30, parry_outcome="parried",
    ))
    assert belief.parry_capacity == IntInterval(30, 76)
    assert belief.strength == IntInterval(0, 8)
    assert belief.body_stability == IntInterval(0, 100)


def test_joint_public_force_and_parry_evidence_can_bound_strength_and_structure():
    belief = CombatBelief()
    belief = belief.observe(PublicObservation(
        event_id="weapon", kind="weapon_seen", tick=0,
        weapon=DuelWeapon(reach_tiles=1, handling=100, mass=1),
    ))
    belief = belief.observe(PublicObservation(
        event_id="force", kind="force_seen", tick=1,
        action_kind="light", observed_force=17,
    ))
    assert belief.strength == IntInterval(4, 4)
    belief = belief.observe(PublicObservation(
        event_id="balance", kind="balance_seen", tick=2, balance_value=100,
    ))
    belief = belief.observe(PublicObservation(
        event_id="parry-lower", kind="parry_comparison", tick=3,
        parry_force=55, parry_outcome="parried",
    ))
    belief = belief.observe(PublicObservation(
        event_id="parry-upper", kind="parry_comparison", tick=4,
        parry_force=56, parry_outcome="overpowered",
    ))
    assert belief.parry_capacity == IntInterval(55, 55)
    assert belief.body_stability == IntInterval(70, 79)


def test_visible_precision_result_narrows_accuracy_without_probability():
    belief = CombatBelief().observe(PublicObservation(
        event_id="placement-hit", kind="precision_check", tick=10,
        precision_evasion=80, precision_modifier=15, precision_hit=True,
    ))
    assert belief.accuracy == IntInterval(65, 100)
    missed = CombatBelief().observe(PublicObservation(
        event_id="placement-miss", kind="precision_check", tick=10,
        precision_evasion=80, precision_modifier=15, precision_hit=False,
    ))
    assert missed.accuracy == IntInterval(0, 64)


def test_duplicate_or_contradictory_evidence_is_deterministic_and_inspectable():
    start = CombatBelief()
    observation = PublicObservation(
        event_id="parry", kind="parry_comparison", tick=2,
        parry_force=30, parry_outcome="parried",
    )
    once = start.observe(observation)
    assert once.observe(observation) == once
    conflict = once.observe(PublicObservation(
        event_id="impossible-followup", kind="parry_comparison", tick=3,
        parry_force=20, parry_outcome="overpowered",
    ))
    assert conflict.parry_capacity == once.parry_capacity
    assert conflict.inconsistencies == (
        "impossible-followup:parry_capacity:10..19",
    )
    assert conflict.evidence[-1].inconsistency == conflict.inconsistencies[0]


def test_belief_observation_is_immutable_monotonic_and_rejects_hidden_actor_input():
    original = CombatBelief()
    updated = original.observe(PublicObservation(
        event_id="known-reach", kind="weapon_seen", tick=1,
        weapon=DuelWeapon(reach_tiles=3),
    ))
    assert original.weapon_reach == IntInterval(1, 4)
    assert updated.weapon_reach == IntInterval(3, 3)
    assert updated.accuracy.low >= original.accuracy.low
    assert updated.accuracy.high <= original.accuracy.high

    from pirates.combat_duel import DuelStats, Duelant

    hidden_actor = Duelant(DuelStats(), DuelWeapon(), 1, 1)
    with pytest.raises(TypeError, match="PublicObservation"):
        original.observe(hidden_actor)
