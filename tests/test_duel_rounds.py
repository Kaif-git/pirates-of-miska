from pirates.duel_system.session import DuelSession
from pirates.duel_system.engine import Choice
from pirates.duel_system.model import ActionKind, Zone
from pirates.duel_ui import DuelUI


def test_round_has_only_one_commitment_per_fighter_and_finishes_both():
    session = DuelSession.new(guide_mode="off")
    assert session.resolve_round(Choice(ActionKind.LIGHT, Zone.TORSO))
    starts = [e for e in session.state.event_log if e.type.value == "ACTION_START"]
    assert len(starts) == 2
    assert len({e.actor for e in starts}) == 2
    assert session.ready or session.state.end_reason
    assert all(f.action is None for f in session.state.fighters) or session.state.end_reason


def test_round_guard_ends_without_waiting_for_manual_release():
    session = DuelSession.new(guide_mode="off")
    assert session.resolve_round(Choice(ActionKind.GUARD))
    assert session.ready or session.state.end_reason
    assert not session.holding_guard


def test_round_ui_executes_without_confirm_and_idle_frames_do_not_advance_time():
    ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
    ui.activate("Light")
    assert ui.panel == "zones" and ui.session.state.tick == 0
    ui.activate("Torso")
    assert ui.panel == "main" and len(ui.session.inputs) == 1
    assert ui.round_result
    before = ui.session.state.to_data()
    ui.tick()
    assert ui.session.state.to_data() == before
