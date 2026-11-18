from pirates.duel_system.engine import Choice, commit_action
from pirates.duel_system.exchange_log import format_exchange, summarize_exchange
from pirates.duel_system.model import ActionKind, EventType, Zone
from pirates.duel_system.session import DuelSession
from pirates.duel_ui import DuelUI
from pirates.duel_stat_help import stat_rows
from test_duel_system_scheduler import arena, advance_to
from tcod.event import KeySym


NAMES = {"player": "You", "ronin": "Ronin"}


def test_default_recap_is_three_short_lines_not_the_calculation_ledger():
    ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
    ui.activate("Light")
    ui.activate("Torso")
    choices, outcome, reason = ui.round_summary
    assert "Light → Torso" in choices and "Ronin:" in choices
    assert "missed" in outcome and "hit" in outcome
    assert reason == "Reach: Distance 4 > reach 1."
    assert len(ui.round_summary) == 3
    assert sum(map(len, ui.round_summary)) < 250
    assert "Endurance" not in " ".join(ui.round_summary)
    assert "Endurance" in " ".join(ui.round_result)


def test_recap_uses_accuracy_threshold_for_drift_not_power():
    state = arena()
    state.fighters[0].accuracy = 1
    commit_action(state, "player", Choice(ActionKind.LIGHT, Zone.HEAD))
    advance_to(state, state.fighters[0].action.strike_tick)
    recap = summarize_exchange(state.event_log, NAMES)
    assert recap[2].startswith("Aim: Accuracy") and "needed → torso" in recap[2]
    assert "Impact:" not in recap[2]


def test_tactical_exchange_recap_explains_feint_shove_and_setup_gated_disarm():
    state = arena()
    player, enemy = state.fighters
    player.strength, enemy.strength = 40, 1
    commit_action(state, "player", Choice(ActionKind.SHOVE))
    advance_to(state, player.action.end_tick)
    short = summarize_exchange(state.event_log, NAMES)
    details = " ".join(format_exchange(state.event_log, NAMES))
    assert "shove success" in short[1].lower() and "strength/balance" in short[2].lower()
    assert "destination" in details and "stability" in details

    state = arena()
    player, enemy = state.fighters
    commit_action(state, "ronin", Choice(ActionKind.GUARD))
    commit_action(state, "player", Choice(ActionKind.FEINT))
    advance_to(state, player.action.end_tick)
    details = " ".join(format_exchange(state.event_log, NAMES))
    assert "feint disrupted" in details and "guard stamina" in details

    state = arena()
    player, enemy = state.fighters
    player.riposte, player.accuracy, player.strength = True, 100, 40
    player.equipment.weapon.damage_type = "PIERCING"
    commit_action(state, "ronin", Choice(ActionKind.HEAVY, Zone.TORSO))
    commit_action(state, "player", Choice(ActionKind.DISARM, Zone.HAND))
    advance_to(state, player.action.strike_tick)
    details = " ".join(format_exchange(state.event_log, NAMES))
    assert "disarm attempt" in details and "grip" in details.lower()


def test_full_details_and_stat_inspection_are_read_only_and_back_is_free():
    ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
    ui.activate("Wait")
    before = ui.session.to_data()
    for label, title in (("Details", "EXCHANGE DETAILS"), ("Stat:Speed", "STAT: Speed"),
                         ("Enemy stat:Stamina", "STAT: Stamina")):
        ui.activate(label)
        assert ui.sidebar_rows(ui.model())[0] == title
        ui.handle_key(KeySym.DOWN)
        ui.handle_key(KeySym.RETURN)
        ui.handle_key(KeySym.BACKSPACE)
        ui.activate("Back")
        assert ui.panel == "main"
    assert ui.session.to_data() == before


def test_stat_help_explains_purpose_current_values_and_real_pressure_factors():
    ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
    ui.session.player.fatigue = 75
    model = ui.model()
    text = " ".join(stat_rows(model, "Speed"))
    assert "never how many actions" in text
    assert "Fatigue pressure:" in text and "base" in text
    assert "unlearned" in " ".join(stat_rows(model, "Strength", enemy=True))


def test_recap_survives_pause_and_reset_clears_it():
    ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
    ui.activate("Light")
    ui.activate("Torso")
    restored = DuelUI(DuelSession.from_data(ui.session.to_data()), rounds=True)
    assert restored.round_summary == ui.round_summary
    ui.activate("Retry")
    assert not ui.round_summary
