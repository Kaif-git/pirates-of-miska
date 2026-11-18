import pytest

from pirates.duel_system.engine import Choice, commit_action
from pirates.duel_system.exchange_log import describe_choice, format_exchange
from pirates.duel_system.model import ActionKind, EventType, Zone
from pirates.duel_system.session import DuelSession
from pirates.duel_ui import DuelUI
from test_duel_system_guard import guarded
from test_duel_system_scheduler import arena, advance_to


NAMES = {"player": "You", "ronin": "Ronin"}


def test_latest_exchange_names_both_exact_choices_and_observed_causes():
    ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
    ui.activate("Light")
    ui.activate("Torso")
    text = "\n".join(ui.round_result)
    assert "You chose: Light at torso." in text
    assert "Ronin chose: Dash + Light at torso, 3 tiles west." in text
    assert "Why:" in text and "weapon reach" in text
    assert "Strength" in text and "armor" in text and "Speed" in text
    assert "You resources:" in text


def test_whiff_reason_is_actual_range_not_an_invented_accuracy_failure():
    state = arena()
    state.fighters[1].pos = (3, 0)
    commit_action(state, "player", Choice(ActionKind.LIGHT, Zone.HEAD))
    advance_to(state, state.fighters[0].action.strike_tick)
    text = "\n".join(format_exchange(state.event_log, NAMES))
    assert "distance 3 > weapon reach 1" in text
    assert "not an accuracy roll" in text


def test_zone_drift_reports_strike_time_accuracy_and_actual_fallback():
    state = arena()
    state.fighters[0].accuracy = 1
    commit_action(state, "player", Choice(ActionKind.LIGHT, Zone.HEAD))
    advance_to(state, state.fighters[0].action.strike_tick)
    hit = next(event for event in state.event_log if event.type == EventType.HIT)
    text = "\n".join(format_exchange(state.event_log, NAMES))
    assert hit.deltas["rolled_accuracy"] < hit.deltas["required_accuracy"]
    assert "aim drifted from head to torso" in text
    assert f"Accuracy {hit.deltas['accuracy']:.1f}" in text
    assert f"net {hit.deltas['net']:.1f}" in text
    assert "You chose: Light at head." in text


@pytest.mark.parametrize("speed,kind,expected", [(10, ActionKind.LIGHT, "parried"),
                                                  (6, ActionKind.LIGHT, "too early/late")])
def test_guard_explains_strength_and_actual_parry_window(speed, kind, expected):
    state = guarded(attacker_speed=speed, kind=kind)
    advance_to(state, state.fighters[0].action.strike_tick)
    text = "\n".join(format_exchange(state.event_log, NAMES))
    assert expected in text
    assert "Strength" in text and "parry window" in text


def test_guard_break_reports_the_real_failed_strength_threshold():
    state = guarded(kind=ActionKind.HEAVY)
    state.fighters[0].strength, state.fighters[1].strength = 20, 5
    advance_to(state, state.fighters[0].action.strike_tick)
    text = "\n".join(format_exchange(state.event_log, NAMES))
    assert "broke Ronin guard" in text
    assert "defender Strength <" in text


def test_movement_and_non_attack_outcomes_are_not_silent():
    state = arena()
    state.fighters[1].pos = (5, 0)
    commit_action(state, "player", Choice(ActionKind.WALK, direction=(0, 1)))
    commit_action(state, "ronin", Choice(ActionKind.WAIT))
    advance_to(state, 20)
    text = "\n".join(format_exchange(state.event_log, NAMES))
    assert "Walk, 1 tile south" in text
    assert "You movement: 1 tile" in text
    assert "Ronin: wait completed. No strike was attempted." in text


def test_direction_and_distance_are_preserved_in_choice_label():
    assert describe_choice(dict(kind="DASH_LIGHT", aim="HAND", direction=(-1, 1), tiles=2)) == "Dash + Light at hand, 2 tiles southwest"


def test_explaining_an_exchange_does_not_mutate_events_or_rng():
    ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
    ui.activate("Wait")
    before = ui.session.to_data()
    assert format_exchange(ui.session.state.event_log, NAMES)
    assert before == ui.session.to_data()


def test_latest_choice_and_reason_survive_pause_and_resume():
    ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
    ui.activate("Light")
    ui.activate("Torso")
    restored = DuelUI(DuelSession.from_data(ui.session.to_data()), rounds=True)
    assert restored.round_result[:2] == ui.round_result[:2]
    assert "Why:" in "\n".join(restored.round_result)


def test_log_panel_includes_detailed_latest_exchange_not_just_terse_history():
    ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
    ui.activate("Light")
    ui.activate("Torso")
    ui.activate("Log")
    text = " ".join(ui.rows(ui.model()))
    assert "LATEST EXCHANGE" in text and "You chose:" in text and "Ronin chose:" in text
    assert "Why:" in text
