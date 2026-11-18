import pytest

from pirates.duel_layout import context_labels, dashboard_geometry
from pirates.duel_system.session import DuelSession
from pirates.duel_ui import DuelUI


@pytest.mark.parametrize("size", [(640, 600), (800, 600), (960, 700), (1280, 800), (1600, 900)])
@pytest.mark.parametrize("count", [1, 2, 4, 5, 6, 9, 10, 11, 12])
def test_dashboard_regions_fit_and_do_not_overlap(size, count):
    width, height = size
    layout = dashboard_geometry(width, height, count)
    for key in ("arena", "info", "controls"):
        x, y, w, h = layout[key]
        assert x >= 0 and y >= 0
        assert x+w <= width and y+h <= height
    ax, ay, aw, ah = layout["arena"]
    ix, iy, iw, ih = layout["info"]
    cx, cy, cw, ch = layout["controls"]
    assert ay+ah <= iy
    assert iy+ih <= cy
    if not layout["wide"]:
        assert 76+layout["card_height"] <= ay


@pytest.mark.parametrize("size", [(640, 600), (960, 700), (1280, 800)])
def test_decision_playback_results_and_panels_keep_identical_anchors(size):
    baseline = dashboard_geometry(*size, 9)
    for count in (1, 2, 6, 9, 11, 12):
        for detailed in (False, True):
            current = dashboard_geometry(*size, count, detailed=detailed)
            for key in ("arena", "info", "controls", "card_height", "card_width"):
                assert current[key] == baseline[key]
    assert dashboard_geometry(*size, 10)["columns"] == baseline["columns"]


def test_context_controls_track_real_round_controller():
    ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
    def labels():
        return context_labels(ui.sidebar_rows(ui.model()))
    assert labels()[0] == "Light"
    ui.activate("Light")
    assert labels() == ["Eye", "Head", "Torso", "Arm", "Hand", "Leg", "Back"]
    ui.activate("Back")
    ui.activate("Dash")
    assert labels()[:4] == ["1 tile", "2 tiles", "3 tiles", "Back"]
    assert "NW" in labels() and "SE" in labels()
    ui.activate("Back")
    ui.activate("More")
    assert "Bandage" in labels() and "Guide" in labels()
    ui.activate("Help")
    assert labels() == ["Back"]
    assert "tick" not in " ".join(ui.rows(ui.model())).lower()


@pytest.mark.parametrize("size", [(640, 600), (960, 700), (1280, 800)])
def test_tactical_menu_adds_a_third_control_row_without_overlapping_panels(size):
    width, height = size
    layout = dashboard_geometry(width, height, 13)
    ax, ay, aw, ah = layout["arena"]
    ix, iy, iw, ih = layout["info"]
    cx, cy, cw, ch = layout["controls"]
    assert ay+ah <= iy and iy+ih <= cy and cy+ch <= height


def test_retry_clears_previous_exchange_feedback():
    ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
    ui.activate("Wait")
    assert ui.round_result
    ui.activate("Retry")
    assert not ui.round_result
    assert ui.session.state.tick == 0


def test_direction_buttons_commit_only_after_distance_selection():
    ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
    before = ui.session.player.pos
    ui.activate("Move")
    ui.activate("1 tile")
    assert ui.session.state.tick == 0
    ui.activate("S")
    assert ui.panel == "main" and len(ui.session.inputs) == 1
    assert ui.session.player.pos == (before[0], before[1]+1)


def test_extreme_speed_still_produces_only_one_action_per_fighter():
    ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
    ui.session.player.speed = 200
    ui.session.enemy.speed = 10
    ui.activate("Light")
    ui.activate("Torso")
    starts = [e for e in ui.session.state.event_log if e.type.value == "ACTION_START"]
    assert len(starts) == 2
    assert len({e.actor for e in starts}) == 2
