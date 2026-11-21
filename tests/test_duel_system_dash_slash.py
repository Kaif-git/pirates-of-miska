"""Dash+Slash stays separate from the forward Dash+Light action."""
import pytest

from pirates.duel_system.engine import Choice, advance_one_tick, commit_action, legality
from pirates.duel_system.model import ActionKind, EventType, Zone
from pirates.duel_system.session import DuelSession
from pirates.duel_ui import DuelUI
from pirates.duel_system.wounds import defense_snapshot
from test_duel_system_scheduler import arena


@pytest.mark.parametrize("direction", [
    (0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1),
])
def test_dash_slash_accepts_each_legal_retreat_or_lateral_direction(direction):
    state = arena()
    player, enemy = state.fighters
    player.pos, enemy.pos = (4, 3), (5, 3)
    choice = Choice(ActionKind.DASH_SLASH, Zone.TORSO, direction, 1, target_id=enemy.id)

    error = legality(state, player.id, choice)
    if direction == (1, 0):  # The enemy occupies this destination.
        assert "blocked" in error.lower() and "occupies the path" in error.lower()
    else:
        assert error == ""


def test_dash_slash_requires_current_reach_and_never_closes_distance():
    state = arena()
    player, enemy = state.fighters
    player.pos, enemy.pos = (2, 2), (4, 2)
    out_of_reach = Choice(ActionKind.DASH_SLASH, Zone.TORSO, (0, -1), 1,
                          target_id=enemy.id)
    assert "current weapon reach" in legality(state, player.id, out_of_reach)

    player.pos, enemy.pos = (4, 3), (5, 3)
    closing = Choice(ActionKind.DASH_SLASH, Zone.TORSO, (1, 0), 1,
                     target_id=enemy.id)
    assert "blocked" in legality(state, player.id, closing).lower()
    toward_from_diagonal = Choice(ActionKind.DASH_SLASH, Zone.TORSO, (1, 1), 1,
                                  target_id=enemy.id)
    # A diagonal step that does not reduce Chebyshev distance is a legal lateral
    # retreat; the action never gains ground on the target.
    assert legality(state, player.id, toward_from_diagonal) == ""


def test_dash_slash_strikes_from_current_tile_before_one_tile_retreat_and_keeps_action_distinct():
    state = arena()
    player, enemy = state.fighters
    player.pos, enemy.pos = (4, 3), (5, 3)
    start = player.pos
    choice = Choice(ActionKind.DASH_SLASH, Zone.TORSO, (0, -1), 1,
                    target_id=enemy.id)
    action = commit_action(state, player.id, choice)
    assert action.kind == ActionKind.DASH_SLASH
    assert action.strike_tick == action.path[0][0]
    assert action.strike_tick > state.tick

    while state.tick < action.strike_tick:
        advance_one_tick(state)

    hit = next(event for event in state.event_log
               if event.type == EventType.HIT and event.actor == player.id)
    movement = next(event for event in state.event_log
                    if event.type == EventType.MOVE and event.actor == player.id)
    starts = [event for event in state.event_log
              if event.type == EventType.ACTION_START and event.actor == player.id]
    assert hit.zone == Zone.TORSO
    assert hit.tick == movement.tick == action.strike_tick
    assert movement.deltas["from"] == start
    assert movement.deltas["to"] == (4, 2)
    assert len(starts) == 1 and starts[0].deltas["kind"] == "DASH_SLASH"
    event_order = [event.type for event in state.event_log
                   if event.tick == action.strike_tick and event.actor in {player.id, enemy.id}]
    assert event_order.index(EventType.HIT) < event_order.index(EventType.MOVE)

    resumed = DuelSession.from_data(DuelSession.from_state(state).to_data())
    assert resumed.player.action is not None
    assert resumed.player.action.kind == ActionKind.DASH_SLASH


def test_dash_light_remains_a_distinct_forward_approach_action():
    state = arena()
    player, enemy = state.fighters
    player.pos, enemy.pos = (2, 2), (5, 2)
    choice = Choice(ActionKind.DASH_LIGHT, Zone.TORSO, (1, 0), 2,
                    target_id=enemy.id)
    assert legality(state, player.id, choice) == ""
    action = commit_action(state, player.id, choice)
    assert action.kind == ActionKind.DASH_LIGHT
    assert action.path[-1][1] == (4, 2)
    assert action.strike_tick > action.path[-1][0]


def test_dashboard_exposes_dash_slash_only_in_range_and_commits_separate_action():
    session = DuelSession.new(seed=11, guide_mode="off")
    player, enemy = session.state.fighters
    player.pos, enemy.pos = (4, 3), (5, 3)
    ui = DuelUI(session, rounds=True)

    ui.activate("Dash+Slash")
    assert ui.panel == "direction"
    ui.choose_direction((0, -1))
    assert ui.panel == "zones"
    assert "then the one-tile retreat/lateral step" in ui.message
    ui.activate("Torso")

    player_starts = [event for event in session.state.event_log
                     if event.type == EventType.ACTION_START and event.actor == player.id]
    assert len(player_starts) == 1
    assert player_starts[0].deltas["kind"] == "DASH_SLASH"

    far = DuelSession.new(seed=12, guide_mode="off")
    far_ui = DuelUI(far, rounds=True)
    far_ui.activate("Dash+Slash")
    assert far_ui.panel == "main"
    assert "current weapon reach" in far_ui.message
