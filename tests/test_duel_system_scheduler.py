"""M2 timing, movement, commitment and simultaneous-decision gates."""
from copy import deepcopy

import pytest

from pirates.duel_system.config import load_config
from pirates.duel_system.model import ActionKind, CombatState, EventType, Zone
from pirates.duel_system.engine import (
    Choice, advance_one_tick, commit_action, decide_batch, legality,
    release_guard, run_until_decision,
)
from test_duel_system_foundation import bare_fighter


def arena(**kwargs):
    cfg = load_config()
    cfg["accuracy"]["jitter"] = 0
    return CombatState([bare_fighter(pos=(0, 0)), bare_fighter("ronin", pos=(1, 0))],
                       config=cfg, **kwargs)


def advance_to(state, tick):
    while state.tick < tick and not state.end_reason:
        advance_one_tick(state)


def test_actions_lock_stats_timing_and_pay_cost_at_commit_not_completion():
    state = arena()
    fighter = state.fighters[0]
    fighter.speed = 20
    commit_action(state, fighter.id, Choice(ActionKind.LIGHT, Zone.TORSO))
    action = fighter.action
    assert action.strike_tick == 2
    assert action.end_tick == 4  # impact t2 + one contact tick + one recovery tick
    assert action.cost_paid == pytest.approx(5.66)
    assert fighter.stamina == pytest.approx(94.34)
    fighter.speed = 1
    assert action.strike_tick == 2 and action.locked_stats.speed == 20
    before = state.to_data()
    with pytest.raises(ValueError, match="committed"):
        commit_action(state, fighter.id, Choice(ActionKind.WAIT))
    assert state.to_data() == before


def test_matched_cutlass_novice_uses_approved_impact_ready_and_escape_targets():
    from pirates.duel_system.session import DuelSession
    session = DuelSession.new(guide_mode="off")
    fighter, enemy = session.player, session.enemy
    fighter.pos, enemy.pos = (4, 3), (5, 3)
    expected_attacks = {
        ActionKind.LIGHT: (3, 6, 6),
        ActionKind.HEAVY: (7, 14, 14),
        ActionKind.THRUST: (4, 9, 8),
    }
    for kind, (impact, ready, cost) in expected_attacks.items():
        fighter.action = None
        fighter.stamina, fighter.fatigue = 100, 0
        action = commit_action(session.state, fighter.id,
                               Choice(kind, Zone.TORSO, target_id=enemy.id))
        assert (action.strike_tick, action.end_tick, action.cost_paid) == pytest.approx(
            (impact, ready, cost), abs=0.01)

    expected_movement = {
        ActionKind.WALK: (4, 6, 3),
        ActionKind.RUN: (3, 6, 5),
        ActionKind.DASH: (2, 7, 9),
    }
    for kind, (step, ready, cost) in expected_movement.items():
        fighter.action = None
        fighter.stamina, fighter.fatigue = 100, 0
        action = commit_action(session.state, fighter.id,
                               Choice(kind, direction=(0, 1), tiles=1))
        assert action.path[0][0] == step
        assert (action.end_tick, action.cost_paid) == pytest.approx((ready, cost), abs=0.01)


def test_walk_commits_until_end_and_run_updates_each_tile():
    state = arena()
    state.fighters[1].pos = (8, 0)
    commit_action(state, "player", Choice(ActionKind.WALK, direction=(0, 1)))
    advance_to(state, 3)
    assert state.fighters[0].pos == (0, 0)
    assert state.fighters[0].action.phase_at(state.tick) == "MOVE"
    advance_to(state, 4)
    assert state.fighters[0].pos == (0, 1)
    assert state.fighters[0].action is not None  # movement landed; readiness is t6
    advance_to(state, 6)
    assert state.fighters[0].action is None
    commit_action(state, "player", Choice(ActionKind.RUN, direction=(1, 0), tiles=2))
    scheduled = deepcopy(state.fighters[0].action.path)
    assert scheduled[1][0] > scheduled[0][0]
    advance_to(state, scheduled[0][0])
    assert state.fighters[0].pos == (1, 1)
    advance_to(state, scheduled[1][0])
    assert state.fighters[0].pos == (2, 1)


def test_dash_light_skips_dash_recovery_and_scales_each_tile():
    state = arena()
    fighter = state.fighters[0]
    fighter.speed = 20
    state.fighters[1].pos = (4, 0)
    commit_action(state, "player", Choice(ActionKind.DASH_LIGHT, Zone.HAND, (1, 0), 3))
    assert fighter.action.path == [(1, (1, 0)), (2, (2, 0)), (3, (3, 0))]
    assert fighter.action.strike_tick == 5
    assert fighter.action.end_tick == 8
    assert fighter.action.cost_paid == pytest.approx(12.26)


def test_reference_fighter_speed_gap_breaks_movement_ticks_not_action_count():
    from pirates.duel_system.session import DuelSession
    expected = {"DASH": (1, 2), "RUN": (2, 3), "DASH_LIGHT": (1, 2)}
    for action_name, (player_step, ronin_step) in expected.items():
        session = DuelSession.new(guide_mode="off")
        session.player.speed, session.enemy.speed = 20, 14
        kind = ActionKind(action_name)
        if kind == ActionKind.DASH_LIGHT:
            player_choice = Choice(kind, Zone.TORSO, (1, 0), 3)
            ronin_choice = Choice(kind, Zone.TORSO, (-1, 0), 3)
        else:
            player_choice = Choice(kind, direction=(1, 0), tiles=1)
            ronin_choice = Choice(kind, direction=(-1, 0), tiles=1)
        player = commit_action(session.state, session.player.id, player_choice)
        ronin = commit_action(session.state, session.enemy.id, ronin_choice)
        assert player.path[0][0]-player.start_tick == player_step
        assert ronin.path[0][0]-ronin.start_tick == ronin_step
        if kind == ActionKind.DASH_LIGHT:
            assert player.strike_tick == 5 and ronin.strike_tick == 9
        assert len([event for event in session.state.event_log if event.type == EventType.ACTION_START]) == 2


def test_faster_reference_dash_gains_position_before_ronin_moves():
    from pirates.duel_system.session import DuelSession
    session = DuelSession.new(guide_mode="off")
    session.player.pos, session.enemy.pos = (4, 3), (8, 3)
    session.player.speed, session.enemy.speed = 20, 14
    commit_action(session.state, session.player.id, Choice(ActionKind.DASH, direction=(1, 0), tiles=1))
    commit_action(session.state, session.enemy.id, Choice(ActionKind.DASH, direction=(-1, 0), tiles=1))
    advance_to(session.state, 1)
    assert session.player.pos == (5, 3)
    assert session.enemy.pos == (8, 3)
    advance_to(session.state, 2)
    assert session.enemy.pos == (7, 3)


def test_fatigue_degrades_dash_timing_under_the_candidate_action_table():
    from pirates.duel_system.session import DuelSession
    session = DuelSession.new(guide_mode="off")
    expected_deadlines = {0: 2, 35: 3, 60: 4, 85: 5}
    for fatigue, deadline in expected_deadlines.items():
        fighter = session.player
        fighter.action = None
        fighter.fatigue = fatigue
        fighter.stamina = 100
        action = commit_action(session.state, fighter.id,
                               Choice(ActionKind.DASH, direction=(0, 1), tiles=1))
        assert action.path[0][0] == session.state.tick + deadline
        fighter.action = None


def test_strike_on_dash_step_tick_resolves_before_movement_and_does_not_evade():
    state = arena()
    player, enemy = state.fighters
    player.pos, enemy.pos = (0, 0), (1, 0)
    player.speed, enemy.speed = 20, 30
    enemy.accuracy = 100
    state.config["accuracy"]["jitter"] = 0
    dash = commit_action(state, player.id, Choice(ActionKind.DASH, direction=(-1, 0), tiles=1))
    attack = commit_action(state, enemy.id, Choice(ActionKind.LIGHT, Zone.TORSO))
    assert dash.path[0][0] == attack.strike_tick == 1
    advance_to(state, 1)
    tick_events = [event.type for event in state.event_log if event.tick == 1]
    assert tick_events.index(EventType.HIT) < tick_events.index(EventType.MOVE)
    assert player.wounds and player.pos == (-1, 0)


def test_both_decide_from_same_snapshot_and_invalid_batch_is_atomic():
    state = arena()
    seen = []
    def choose(snapshot, fighter_id):
        seen.append((fighter_id, snapshot.to_data()))
        # Deliberately attempt to tamper with an observation; never touches live state
        # or the other chooser's independent snapshot.
        snapshot.other(next(f for f in snapshot.fighters if f.id == fighter_id)).anger = 99
        return Choice(ActionKind.LIGHT if fighter_id == "player" else ActionKind.WAIT,
                      Zone.TORSO if fighter_id == "player" else None)
    decide_batch(state, choose)
    assert len(seen) == 2
    assert all(f["action"] is None for _, data in seen for f in data["fighters"])
    assert state.fighters[1].anger == 0
    assert state.fighters[1].action.kind == ActionKind.WAIT
    state = arena()
    before = state.to_data()
    with pytest.raises(ValueError):
        decide_batch(state, lambda snapshot, fid: Choice(ActionKind.WAIT) if fid == "player"
                     else Choice(ActionKind.DASH, direction=(0, 0)))
    assert state.to_data() == before


def test_busy_scheduler_advances_to_first_free_and_never_waits_for_wall_time():
    state = arena()
    decide_batch(state, lambda snapshot, fid: Choice(ActionKind.WAIT))
    assert {fighter.id for fighter in run_until_decision(state)} == {"player", "ronin"}
    assert state.tick == 5
    before = state.to_data()
    assert len(run_until_decision(state)) == 2
    assert state.to_data() == before


@pytest.mark.parametrize("kind", [ActionKind.HEAVY, ActionKind.DASH, ActionKind.DASH_LIGHT])
def test_exhaustion_disallows_heavy_and_dash(kind):
    state = arena()
    fighter = state.fighters[0]
    fighter.stamina = 0
    fighter.exhausted = True
    choice = Choice(kind, Zone.TORSO, (0, 1))
    assert "exhaust" in legality(state, fighter.id, choice).lower()
    before = state.to_data()
    with pytest.raises(ValueError):
        commit_action(state, fighter.id, choice)
    assert state.to_data() == before


def test_invalid_directions_and_blocked_paths_are_free_rejections():
    state = arena(walkable={(0, 0), (1, 0), (0, 1)})
    for choice in [Choice(ActionKind.WALK, direction=(2, 0)),
                   Choice(ActionKind.WALK, direction=(1, 0)),
                   Choice(ActionKind.DASH, direction=(0, 1), tiles=3)]:
        before = state.to_data()
        assert legality(state, "player", choice)
        with pytest.raises(ValueError):
            commit_action(state, "player", choice)
        assert state.to_data() == before


def test_runtime_occupied_step_names_blocker_and_cancels_remaining_route():
    from pirates.duel_system.render_model import event_line
    state = arena()
    state.fighters[0].pos, state.fighters[1].pos = (0, 0), (4, 0)
    commit_action(state, "player", Choice(ActionKind.DASH, direction=(1, 0), tiles=2))
    # The destination becomes occupied after commitment, as with a simultaneous
    # approach. Move the blocker away after the collision to ensure no path skip.
    state.fighters[1].pos = (1, 0)
    advance_to(state, state.fighters[0].action.path[0][0])
    assert state.fighters[0].pos == (0, 0)
    blocked = next(event for event in state.event_log if "MOVEMENT_BLOCKED" in event.cause_tags)
    assert blocked.actor == "player" and blocked.target == "ronin"
    assert blocked.deltas == {"status": "movement_blocked", "position": (1, 0), "kind": "DASH"}
    assert event_line(blocked, {"player": "You", "ronin": "Ronin"}) == "You: movement blocked at (1, 0) by Ronin"
    state.fighters[1].pos = (4, 0)
    advance_to(state, 8)
    assert state.fighters[0].pos == (0, 0), "A later path deadline must not skip the blocked tile"


def test_committed_dash_interrupted_by_landed_cut_before_first_step_is_logged():
    state = arena()
    state.fighters[0].pos, state.fighters[1].pos = (0, 0), (1, 0)
    state.fighters[1].speed = 40  # land a cut before the first dash step
    state.fighters[1].strength = 20
    commit_action(state, "player", Choice(ActionKind.DASH, direction=(-1, 0), tiles=1))
    commit_action(state, "ronin", Choice(ActionKind.LIGHT, Zone.TORSO))
    advance_to(state, 1)
    assert state.fighters[0].pos == (0, 0)
    interrupted = next(event for event in state.event_log if event.type == EventType.INTERRUPT)
    from pirates.duel_system.render_model import event_line
    assert event_line(interrupted, {"player": "You", "ronin": "Ronin"}) == "You: dash interrupted"


def test_guard_release_does_not_teleport_to_ready():
    state = arena()
    commit_action(state, "player", Choice(ActionKind.GUARD))
    advance_to(state, 3)
    assert state.fighters[0].action.phase_at(3) == "GUARD_HOLD"
    release_guard(state, "player")
    assert state.fighters[0].action.phase_at(3) == "GUARD_LOWER"
    assert not state.fighters[0].can_act(3) or state.fighters[0].action is not None
    advance_to(state, 6)
    assert state.fighters[0].action is None


def test_light_interrupts_heavy_and_both_same_tick_strikes_trade():
    state = arena()
    decide_batch(state, lambda snapshot, fid: Choice(
        ActionKind.LIGHT if fid == "player" else ActionKind.HEAVY, Zone.TORSO))
    advance_to(state, 4)
    assert state.fighters[1].action is None
    assert any(e.type == EventType.INTERRUPT and e.actor == "ronin" for e in state.event_log)
    state = arena()
    decide_batch(state, lambda snapshot, fid: Choice(ActionKind.LIGHT, Zone.TORSO))
    advance_to(state, 4)
    hits = [e for e in state.event_log if e.type == EventType.HIT]
    assert {e.actor for e in hits} == {"player", "ronin"}
    assert all(fighter.wounds for fighter in state.fighters)


def test_active_action_snapshot_preserves_timing_paths_and_replay_100_runs():
    state = arena()
    state.fighters[1].pos = (4, 0)
    state.fighters[0].speed = 20
    commit_action(state, "player", Choice(ActionKind.DASH_LIGHT, Zone.HAND, (1, 0), 3))
    advance_to(state, 2)
    snapshot = state.to_data()
    expected = None
    for _ in range(100):
        restored = CombatState.from_data(snapshot)
        advance_to(restored, 6)
        if expected is None:
            expected = restored.to_data()
        assert restored.to_data() == expected
    assert state.to_data() == snapshot


def test_npc_free_before_player_is_a_decision_not_silent_time_skip():
    state = arena()
    decide_batch(state, lambda snapshot, fid: Choice(ActionKind.LIGHT, Zone.TORSO)
                 if fid == "player" else Choice(ActionKind.WAIT))
    free = run_until_decision(state)
    assert state.tick == 5
    assert [fighter.id for fighter in free] == ["ronin"]
    assert state.fighters[0].action is not None
