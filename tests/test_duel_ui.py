"""M9 real keyboard/controller paths: no action occurs before confirmation."""
from tcod.event import KeySym
import pytest

from pirates.duel_system.engine import Choice
from pirates.duel_system.model import ActionKind, Status
from pirates.duel_system.session import DuelSession
from pirates.duel_ui import DuelUI


def test_sidebar_explains_state_stats_and_bleeding_without_ascii_map_or_predictions():
    session = DuelSession.new(guide_mode="off")
    ui = DuelUI(session)
    before = session.state.to_data()
    text = "\n".join(ui.sidebar_rows(ui.model()))
    for label in ("YOUR WOUNDS / BLEEDING", "Blood:", "Stamina:", "Strength:", "Speed:", "Accuracy:", "No timed action underway."):
        assert label in text
    assert "YOU   " not in text and "#  #  #" not in text
    assert session.state.to_data() == before


def test_preview_sidebar_uses_exact_selected_action_timing_not_generic_estimate():
    ui = DuelUI(DuelSession.new(guide_mode="off"))
    ui.activate("Wait")
    text = "\n".join(ui.sidebar_rows(ui.model()))
    assert "Ready again in" in text
    assert "Movement estimate" not in text
    assert "Time has not advanced" in text


def test_mouse_distance_controls_change_only_pending_movement_distance():
    ui = DuelUI(DuelSession.new(guide_mode="off"))
    ui.activate("Dash")
    before = ui.session.state.to_data()
    ui.activate("2 tiles")
    assert ui.tiles == 2 and ui.panel == "direction"
    assert ui.session.state.to_data() == before


def test_attack_key_opens_zone_menu_then_zone_preview_then_enter_commits():
    session = DuelSession.new(guide_mode="off")
    ui = DuelUI(session)
    ui.handle_key(KeySym.SPACE)
    assert ui.panel == "zones"
    assert session.state.tick == 0 and not session.player.action
    ui.handle_key(KeySym.T)
    assert ui.panel == "preview"
    assert session.selected is not None and not session.player.action
    ui.handle_key(KeySym.RETURN)
    assert session.player.action is not None


def test_arrows_preview_ordinary_walk_and_escape_cancels_without_leaving():
    session = DuelSession.new(guide_mode="off")
    ui = DuelUI(session)
    ui.handle_key(KeySym.DOWN)
    assert session.selected is not None
    before = session.state.to_data()
    ui.handle_key(KeySym.ESCAPE)
    assert session.selected is None and ui.panel == "main"
    assert session.state.to_data() == before
    assert not ui.closed


def test_repeated_same_movement_selection_confirms_double_tap():
    session = DuelSession.new(guide_mode="off")
    ui = DuelUI(session)
    ui.handle_key(KeySym.DOWN)
    ui.handle_key(KeySym.DOWN)
    assert session.player.action is not None


def test_notebook_log_and_help_are_read_only_and_escape_returns():
    session = DuelSession.new(guide_mode="off")
    ui = DuelUI(session)
    before = session.state.to_data()
    for key, panel in ((KeySym.K, "notebook"), (KeySym.L, "log"), (KeySym.H, "help")):
        ui.handle_key(key)
        assert ui.panel == panel
        ui.handle_key(KeySym.ESCAPE)
        assert ui.panel == "main"
    assert session.state.to_data() == before


def test_dash_light_direction_and_zone_are_both_explicit():
    session = DuelSession.new(guide_mode="off")
    ui = DuelUI(session)
    ui.handle_key(KeySym.J)
    assert ui.panel == "direction"
    ui.handle_key(KeySym.RIGHT)
    assert ui.panel == "zones"
    ui.handle_key(KeySym.X)
    assert ui.panel == "preview" and session.selected is not None
    assert session.state.tick == 0


def test_repeated_dash_direction_confirms_dash_not_walk():
    session = DuelSession.new(guide_mode="off")
    ui = DuelUI(session)
    ui.handle_key(KeySym.D)
    ui.handle_key(KeySym.DOWN)
    # Three tiles would reach the arena wall, so explicitly choose one tile.
    ui.handle_key(KeySym.ESCAPE)
    ui.handle_key(KeySym.D)
    ui.handle_key(KeySym.N1)
    ui.handle_key(KeySym.DOWN)
    ui.handle_key(KeySym.DOWN)
    assert session.player.action is not None
    assert session.player.action.kind.value == "DASH"


@pytest.mark.parametrize(("kind", "label"), [("DASH", "Dash"), ("RUN", "Run")])
def test_blocked_default_dash_or_run_explains_enemy_and_shorter_route_moves(kind, label):
    session = DuelSession.new(guide_mode="off")
    session.enemy.pos = (session.player.pos[0]+2, session.player.pos[1])
    session._npc_choice = lambda: Choice(ActionKind.WAIT)
    ui = DuelUI(session, rounds=True)
    start = session.player.pos
    ui.activate(label)
    assert ui.tiles == 3
    ui.activate("E")
    assert session.player.pos == start and not session.inputs
    assert "Ronin occupies the path" in ui.message
    assert ui.message in ui.sidebar_rows(ui.model())
    ui.activate("1 tile")
    ui.activate("E")
    assert session.player.pos == (start[0]+1, start[1])
    assert len(session.inputs) == 1
    assert session.inputs[-1]["choice"]["kind"].value == kind


@pytest.mark.parametrize(("direction", "delta"), [
    ("NW", (-1, -1)), ("N", (0, -1)), ("NE", (1, -1)), ("W", (-1, 0)),
    ("E", (1, 0)), ("SW", (-1, 1)), ("S", (0, 1)), ("SE", (1, 1)),
])
def test_walk_direction_buttons_move_to_the_selected_neighbor(direction, delta):
    session = DuelSession.new(guide_mode="off")
    session._npc_choice = lambda: Choice(ActionKind.WAIT)
    ui = DuelUI(session, rounds=True)
    start = session.player.pos
    ui.activate("Move")
    ui.activate(direction)
    assert session.player.pos == (start[0]+delta[0], start[1]+delta[1])
    assert len([event for event in session.state.event_log if event.type.value == "MOVE"]) == 1


@pytest.mark.parametrize("tiles", [1, 2, 3])
def test_run_distance_controls_move_exactly_the_selected_number_of_tiles(tiles):
    session = DuelSession.new(guide_mode="off")
    session._npc_choice = lambda: Choice(ActionKind.WAIT)
    ui = DuelUI(session, rounds=True)
    ui.activate("Run")
    ui.activate(f"{tiles} tile" if tiles == 1 else f"{tiles} tiles")
    ui.activate("E")
    assert session.player.pos == (4+tiles, 3)
    assert len([event for event in session.state.event_log if event.type.value == "MOVE"]) == tiles


def test_arena_edge_and_leg_injury_rejections_are_visible_and_free():
    session = DuelSession.new(guide_mode="off")
    session.player.pos = (1, 3)
    ui = DuelUI(session, rounds=True)
    before = session.state.to_data()
    ui.activate("Run")
    ui.activate("W")
    assert "outside the arena floor" in ui.message
    assert ui.message in ui.sidebar_rows(ui.model())
    assert not session.inputs and session.state.to_data() == before

    session = DuelSession.new(guide_mode="off")
    session.player.statuses.append(Status("crippled", no_dash=True))
    ui = DuelUI(session, rounds=True)
    ui.activate("Dash")
    ui.activate("E")
    assert ui.message == "Leg injury prevents dashing"
    assert ui.message in ui.sidebar_rows(ui.model()) and not session.inputs


def test_exhausted_dash_rejection_explains_why_no_movement_was_committed():
    session = DuelSession.new(guide_mode="off")
    session.player.exhausted = True
    ui = DuelUI(session, rounds=True)
    ui.activate("Dash")
    ui.activate("S")
    assert "Exhausted" in ui.message
    assert ui.message in ui.sidebar_rows(ui.model()) and not session.inputs


def test_direction_panel_explains_speed_scaled_movement_ticks_and_shared_tick_order():
    session = DuelSession.new(guide_mode="off")
    ui = DuelUI(session, rounds=True)
    ui.activate("Dash")
    rows = ui.sidebar_rows(ui.model())
    assert any("Travel: 2 ticks/tile (6 total)" in row for row in rows)
    assert any("strikes resolve before same-tick movement" in row for row in rows)
    ui.activate("Back")
    ui.activate("More")
    ui.activate("Run")
    rows = ui.sidebar_rows(ui.model())
    assert any("Travel: 3 ticks/tile (9 total)" in row for row in rows)


def test_input_while_busy_cannot_open_invalid_preview_and_freeze_auto_time():
    session = DuelSession.new(guide_mode="off")
    ui = DuelUI(session)
    ui.handle_key(KeySym.DOWN)
    ui.handle_key(KeySym.RETURN)
    before = session.state.tick
    ui.handle_key(KeySym.RIGHT)
    assert ui.panel == "main" and ui.pending is None
    ui.tick()
    assert session.state.tick > before
    ui.handle_key(KeySym.SPACE)
    assert ui.panel == "main"
