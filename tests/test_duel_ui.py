"""M9 real keyboard/controller paths: no action occurs before confirmation."""
from tcod.event import KeySym
import pytest

from pirates.duel_system.engine import Choice
from pirates.duel_system.model import ActionKind, Status
from pirates.duel_system.session import DuelSession
from pirates.duel_ui import DuelUI, _choose_duel_archetype, _new_sword_duel_session


def test_sidebar_explains_state_stats_and_bleeding_without_ascii_map_or_predictions():
    session = DuelSession.new(guide_mode="off")
    ui = DuelUI(session)
    before = session.state.to_data()
    text = "\n".join(ui.sidebar_rows(ui.model()))
    for label in ("YOUR WOUNDS / BLEEDING", "Blood:", "Stamina:", "Strength:", "Speed:", "Accuracy:", "No timed action underway."):
        assert label in text
    assert "YOU   " not in text and "#  #  #" not in text
    assert session.state.to_data() == before


def test_fresh_duel_opponent_picker_is_clear_safe_and_maps_to_authored_profiles():
    class Renderer:
        def __init__(self, selected):
            self.selected = selected
            self.rows = None
            self.footer = None

        def render_list(self, world, title, rows, footer):
            self.title, self.rows, self.footer = title, rows, footer

        def choose(self, count):
            assert count == len(self.rows)
            return self.selected

    renderer = Renderer(3)
    assert _choose_duel_archetype(renderer, object()) == "counterfighter"
    assert renderer.title == "Choose your sword-duel opponent"
    assert any("Veteran" in row for row in renderer.rows)
    assert any("Counterfighter" in row for row in renderer.rows)
    assert "Esc cancels without campaign changes" in renderer.footer
    assert _choose_duel_archetype(Renderer(None), object()) is None


@pytest.mark.parametrize("selection,profile", enumerate(
    ("brute", "aggressor", "veteran", "counterfighter", "recruit", "bully")))
def test_fresh_duel_setup_starts_selected_profile_deterministically_without_world_mutation(selection, profile):
    class Renderer:
        def render_list(self, *_args):
            pass

        def choose(self, _count):
            return selection

    class Clock:
        total_ticks = 1234

    class World:
        clock = Clock()
        duel_journal = None

    world = World()
    session = _new_sword_duel_session(Renderer(), world)
    replay = _new_sword_duel_session(Renderer(), world)

    assert session.enemy.personality.archetype == profile
    assert session.state.rng_seed == world.clock.total_ticks
    assert session.state.to_data() == replay.state.to_data()
    assert world.duel_journal is None


def test_cancelled_fresh_duel_setup_does_not_create_session_or_journal():
    class Renderer:
        def render_list(self, *_args):
            pass

        def choose(self, _count):
            return None

    class World:
        class Clock:
            total_ticks = 42
        clock = Clock()
        duel_journal = None

    world = World()
    assert _new_sword_duel_session(Renderer(), world) is None
    assert world.duel_journal is None


def test_disengage_opens_direction_picker_and_explains_clear_space_is_not_enough():
    session = DuelSession.new(seed=14, guide_mode="off")
    player, enemy = session.state.fighters
    player.pos, enemy.pos = (4, 3), (5, 3)
    ui = DuelUI(session, rounds=True)

    ui.activate("Disengage")
    assert ui.panel == "direction"
    assert "strictly increases distance" in ui.message
    ui.choose_direction((0, -1))
    assert "must increase distance" in ui.message
    assert ui.panel == "direction"

    ui.choose_direction((-1, 0))
    starts = [event for event in session.state.event_log
              if event.type.value == "ACTION_START" and event.actor == player.id]
    assert starts[-1].deltas["kind"] == "DISENGAGE"


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


def test_dash_light_single_target_automatically_approaches_and_only_asks_for_hit_zone():
    session = DuelSession.new(guide_mode="off")
    ui = DuelUI(session, rounds=True)
    before = session.player.pos

    ui.handle_key(KeySym.J)

    assert ui.panel == "zones"
    assert ui.target_id == session.enemy.id
    choice = ui._dash_light_choices[session.enemy.id]
    assert choice.direction == (1, 0) and choice.tiles == 3
    assert session.player.pos == before and not session.inputs
    zone_text = " ".join(ui.sidebar_rows(ui.model())).lower()
    assert "approach path is automatic" in zone_text
    assert "choose hit zone" in zone_text
    assert "choose direction" not in zone_text and "1/2/3 changes" not in zone_text
    ui.handle_key(KeySym.X)
    assert len(session.inputs) == 1
    assert session.inputs[0]["choice"]["kind"].value == "DASH_LIGHT"
    assert session.inputs[0]["choice"]["direction"] == (1, 0)
    assert session.inputs[0]["choice"]["tiles"] == 3


def test_dash_light_multiple_targets_lists_only_reachable_hostiles():
    from test_duel_system_foundation import bare_fighter

    player = bare_fighter("player", pos=(2, 3), team_id="players")
    near_a = bare_fighter("near-a", pos=(6, 2), team_id="raiders")
    near_b = bare_fighter("near-b", pos=(6, 4), team_id="raiders")
    far = bare_fighter("far", pos=(9, 3), team_id="raiders")
    session = DuelSession.new_group([player, near_a, near_b, far], guide_mode="off", seed=13)
    ui = DuelUI(session, rounds=True)

    ui.activate("Dash+Light")

    assert ui.panel == "targets"
    assert set(ui._dash_light_choices) == {"near-a", "near-b"}
    rows = "\n".join(ui.sidebar_rows(ui.model())).lower()
    assert "near-a" in rows and "near-b" in rows and "far" not in rows
    assert "direction" in rows.lower()  # explicitly explained as automatic
    ui.activate("Target 2")
    assert ui.panel == "zones" and ui.target_id == "near-b"
    assert ui._dash_light_choices["near-b"].tiles <= 3


def test_dash_light_out_of_range_returns_to_actions_without_direction_or_distance_prompt():
    from test_duel_system_foundation import bare_fighter

    player = bare_fighter("player", pos=(1, 3), team_id="players")
    hostile = bare_fighter("hostile", pos=(8, 3), team_id="raiders")
    session = DuelSession.new_group([player, hostile], guide_mode="off", seed=14)
    ui = DuelUI(session, rounds=False)
    before = session.state.to_data()

    ui.activate("Dash+Light")

    assert ui.panel == "main" and not ui._dash_light_choices
    assert "Out of range" in ui.message or "clear Dash+Light approach" in ui.message
    assert "direction" not in ui.message.lower() and "tile" not in ui.message.lower()
    assert session.state.to_data() == before and not session.inputs


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
