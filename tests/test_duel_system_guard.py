"""M4 guard-line, timing, strength, stagger and riposte acceptance."""
import pytest

from pirates.duel_system.model import ActionKind, EventType, Zone
from pirates.duel_system.engine import Choice, commit_action, release_guard
from test_duel_system_scheduler import arena, advance_to


def guarded(attacker_speed=10, zone=Zone.TORSO, kind=ActionKind.LIGHT):
    state = arena()
    state.fighters[0].speed = attacker_speed
    commit_action(state, "player", Choice(kind, zone))
    commit_action(state, "ronin", Choice(ActionKind.GUARD))
    return state


def test_inside_parry_window_costs_no_hit_stamina_staggers_and_grants_riposte():
    state = guarded()
    attacker, defender = state.fighters
    advance_to(state, 4)
    event = next(e for e in state.event_log if e.type == EventType.PARRY)
    assert event.deltas["stamina_cost"] == 0
    assert not defender.wounds
    assert attacker.stagger_until == 10
    assert attacker.action is None
    assert defender.riposte
    release_guard(state, "ronin")
    advance_to(state, defender.action.end_tick)
    commit_action(state, "ronin", Choice(ActionKind.LIGHT, Zone.TORSO))
    assert defender.action.windup_len == 2
    assert not defender.riposte


def test_one_tick_outside_window_is_block_not_parry():
    state = guarded(attacker_speed=6)
    defender = state.fighters[1]
    assert defender.action.guard_ready_tick == 3
    assert defender.action.parry_window == 4
    advance_to(state, 7)
    assert not any(e.type == EventType.PARRY for e in state.event_log)
    block = next(e for e in state.event_log if e.type == EventType.BLOCK)
    assert block.deltas["stamina_cost"] >= 2
    assert not defender.wounds


@pytest.mark.parametrize("zone,bypasses", [(Zone.LEG, True), (Zone.HAND, True), (Zone.ARM, False)])
def test_guard_line_bypass(zone, bypasses):
    state = guarded(zone=zone)
    state.fighters[0].accuracy = 40
    advance_to(state, 4)
    assert bool(state.fighters[1].wounds) == bypasses
    if bypasses:
        hit = next(e for e in state.event_log if e.type == EventType.HIT)
        assert "GUARD_BYPASSED" in hit.cause_tags
    else:
        assert any(e.type == EventType.PARRY for e in state.event_log)


def test_weak_defender_cannot_parry_heavy_and_heavy_breaks_guard():
    state = guarded(kind=ActionKind.HEAVY)
    state.fighters[0].strength = 20
    state.fighters[1].strength = 5
    advance_to(state, state.fighters[0].action.strike_tick)
    assert not any(e.type == EventType.PARRY for e in state.event_log)
    assert any(e.type == EventType.GUARD_BREAK for e in state.event_log)
    assert state.fighters[1].stagger_until == state.tick + 8
    assert state.fighters[1].action is None


def test_block_cost_shortfall_breaks_guard_but_does_not_cancel_attack_recovery():
    state = guarded(attacker_speed=5)
    state.fighters[1].stamina = 1
    advance_to(state, 8)
    assert any(e.type == EventType.GUARD_BREAK for e in state.event_log)
    assert state.fighters[0].action is not None
    assert state.fighters[0].action.phase_at(state.tick) == "ACTIVE"


def test_heavy_gets_extra_parry_window_but_still_needs_strength():
    state = guarded(attacker_speed=15, kind=ActionKind.HEAVY)
    # Heavy strikes t8, outside base [3,7) but inside extended [3,9).
    advance_to(state, 8)
    assert any(e.type == EventType.PARRY for e in state.event_log)


def test_guard_raise_blocks_but_cannot_parry_before_ready():
    state = guarded(attacker_speed=20)
    advance_to(state, 2)
    assert any(e.type == EventType.BLOCK for e in state.event_log)
    assert not any(e.type == EventType.PARRY for e in state.event_log)
