"""M9 immutable RenderModel: projections, exact threats, timeline and 40 columns."""
from dataclasses import FrozenInstanceError

import pytest

from pirates.duel_system.engine import Choice, commit_action
from pirates.duel_system.model import ActionKind, Severity, Wound, Zone
from pirates.duel_system.render_model import format_ascii
from pirates.duel_system.session import DuelSession


def test_read_only_model_and_formatter_are_40_columns_with_persistent_state():
    session = DuelSession.new(guide_mode="off")
    before = session.state.to_data()
    model = session.render_model()
    rows = format_ascii(model)
    assert rows and all(len(row) <= 40 for row in rows)
    assert "ALIVE" in "\n".join(rows)
    assert "Blood" in "\n".join(rows)
    assert {name for name, _ in model.player_sheet} >= {"Strength", "Speed", "Accuracy"}
    light = next(action for action in model.actions if action.label == "Light")
    assert "Hits in" in light.details and "stamina" in light.details
    assert "LV 1 XP 0/100" in "\n".join(rows)
    assert "[Light]" in "\n".join(rows)
    assert session.state.to_data() == before
    with pytest.raises(FrozenInstanceError):
        model.player_state = "anything"


def test_grounded_readout_restricts_controls_and_explains_stability_and_rise():
    from pirates.duel_layout import context_labels

    session = DuelSession.new(guide_mode="off")
    player = session.player
    player.stability, player.grounded, player.grounded_since = 0, True, session.state.tick
    before = session.state.to_data()
    model = session.render_model()
    rows = format_ascii(model)
    assert model.player_stability == 0 and model.player_grounded
    assert "GROUNDED" in model.player_state
    assert set(option.label for option in model.actions) == {"Rise", "Crawl", "Cover"}
    assert all(option.enabled for option in model.actions)
    assert "[Rise][Crawl][Cover]" in "\n".join(rows)
    assert "Balance" in dict(model.player_sheet)
    rise = next(option for option in model.actions if option.label == "Rise")
    assert "Exposed" in rise.details and "interrupt" in rise.details
    assert context_labels(rows, model) == ["Rise", "Crawl", "Cover", "Sheet", "Help"]
    assert session.state.to_data() == before


def test_stability_meter_help_uses_stable_stat_key_and_tactical_actions_are_reachable():
    from pirates.duel_layout import context_labels
    from pirates.duel_stat_help import STATS, stat_rows
    from pirates.duel_ui import DuelUI

    session = DuelSession.new(guide_mode="off")
    ui = DuelUI(session, rounds=True)
    model = ui.model()
    assert "Stability" in STATS
    help_rows = stat_rows(model, "Stability (bal 44)")
    assert "Stability:" in help_rows[0]
    assert "current balance reserve" in help_rows[1].lower()
    assert f"{model.player_stability:.1f} / 100" in help_rows[0]

    # The contextual menu intentionally nests the six tactics under More > Tactics.
    ui.activate("More")
    assert "Tactics" in context_labels(ui.sidebar_rows(ui.model()))
    ui.activate("Tactics")
    assert context_labels(ui.sidebar_rows(ui.model())) == [
        "Feint", "Shove", "Disengage", "Thrust", "Disarm", "Adrenaline", "Back"]
    options = {option.label for option in ui.model().actions}
    assert {"Feint", "Shove", "Disengage", "Thrust", "Disarm", "Adrenaline"} <= options


def test_fall_cause_and_fallen_state_are_visible_without_mutating_during_render():
    from pirates.duel_system.stability import apply_stability_loss

    session = DuelSession.new(guide_mode="off")
    player = session.player
    player.stability = 1
    apply_stability_loss(session.state, player, "OVEREXTENDED_HEAVY_WHIFF", 100)
    before = session.state.to_data()
    model = session.render_model()
    rows = "\n".join(format_ascii(model))
    assert model.player_grounded
    assert any("fell from overextended heavy whiff" in row for row in model.full_log)
    assert "heavy overextends" in " ".join(model.why)
    assert "FALLEN!" in {popup.text for popup in model.popups}
    assert "Stability lost" in {popup.text for popup in model.popups}
    assert "@v" in rows
    assert session.state.to_data() == before


def test_danger_and_glyph_come_from_committed_attack_not_hypothetical_ai():
    session = DuelSession.new(guide_mode="off")
    model = session.render_model()
    assert not model.danger
    enemy = session.state.fighters[1]
    commit_action(session.state, enemy.id, Choice(ActionKind.HEAVY, Zone.TORSO))
    model = session.render_model()
    assert model.enemy_glyph == "R^"
    assert model.danger
    assert (enemy.pos[0]-1, enemy.pos[1]) in model.danger
    assert "!" in "\n".join(format_ascii(model))


def test_attack_preview_shows_zone_exposure_cost_fatigue_and_timelines():
    session = DuelSession.new(guide_mode="off")
    session.select(Choice(ActionKind.DASH_LIGHT, Zone.HAND, (1, 0), 3))
    model = session.render_model()
    assert model.preview is not None and model.preview.stamina > 0 and model.preview.fatigue > 0
    assert model.preview.strike_tick is not None
    assert model.player_timeline
    assert any(option.zone == Zone.HAND and not option.exposed for option in model.zones)
    assert "confirm" in "\n".join(format_ascii(model)).lower()


def test_enemy_sheet_redacts_exact_stats_until_three_observed_exchanges():
    session = DuelSession.new(guide_mode="off")
    assert "?" in dict(session.render_model().enemy_sheet)["Strength"]
    for _ in range(3):
        session.notebook.record_exchange("ronin")
    assert "?" not in dict(session.render_model().enemy_sheet)["Strength"]


def test_preview_warns_when_current_target_is_out_of_reach():
    session = DuelSession.new(guide_mode="off")
    preview = session.select(Choice(ActionKind.LIGHT, Zone.TORSO))
    assert "out of reach" in preview.risk


def test_progression_level_and_xp_are_read_from_state_each_render():
    session = DuelSession.new(guide_mode="off")
    session.player.level, session.player.xp = 3, 40
    model = session.render_model()
    assert (model.player_level, model.player_xp, model.player_xp_next) == (3, 40, 156)
    assert "LV 3 XP 40/156" in "\n".join(format_ascii(model))


def test_learned_heavy_has_ghost_glyph_and_scheduled_prediction_not_false_commitment():
    from pirates.duel_system.ai import FullKey
    from pirates.duel_system.guide import make_guide_view
    session = DuelSession.new(guide_mode="off")
    view = make_guide_view(session.state, "player", session.notebook.public_view())
    for index in range(5):
        session.notebook.record("brute", "ronin", view.public_key, "HEAVY", tick=index,
                                full_key=FullKey(view.public_key, "OK", "BOLD", "LOW"))
    model = session.render_model()
    assert model.enemy_glyph == "R^~"
    assert model.enemy_timeline.startswith("~") and "HABIT" in model.enemy_timeline
    assert "h" in model.enemy_timeline and not model.danger


def test_emotion_readout_is_exact_for_player_and_enemy_tells_are_public_and_uncertain():
    session = DuelSession.new(guide_mode="off")
    session.player.anger, session.player.pain = 33, 22
    session.enemy.anger, session.enemy.pain, session.enemy.morale = 70, 99, 1
    session.enemy.wounds = [Wound(Zone.TORSO, Severity.CUT, 3)]
    model = session.render_model()
    assert "Fear" in model.player_emotion_rows[0] and "anger 33" in model.player_emotion_rows[0]
    assert any("uncertain" in line or "unknown" in line for line in model.enemy_tell)
    assert any("Visible wounds" in line for line in model.enemy_tell)
    assert not {"Pain", "Morale", "Anger"}.intersection(name for name, _ in model.enemy_sheet)
    assert "99" not in " ".join(model.enemy_tell)


def test_hidden_archetype_notebook_history_does_not_create_public_ghost_or_tell():
    from pirates.duel_system.ai import FullKey
    from pirates.duel_system.guide import make_guide_view
    session = DuelSession.new(guide_mode="off")
    view = make_guide_view(session.state, "player", session.notebook.public_view())
    for index in range(6):
        session.notebook.record("brute", "ronin", view.public_key, "HEAVY", tick=index,
                                full_key=FullKey(view.public_key, "OK", "BOLD", "LOW"))
    session.notebook.named.clear()
    model = session.render_model()
    assert not model.enemy_glyph.endswith("~")
    assert model.enemy_tell == ("No reliable read yet; next choice unknown.",)


def test_render_projection_never_reads_hidden_enemy_profile_or_emotional_values():
    session = DuelSession.new(guide_mode="off")
    session.state.end_reason = "yield"
    before = session.render_model()
    session.enemy.personality = type("NoSecrets", (), {
        "__getattr__": lambda self, name: (_ for _ in ()).throw(AssertionError(name)),
    })()
    session.enemy.pain, session.enemy.morale = 99, 0
    after = session.render_model()
    assert after.enemy_tell == before.enemy_tell
    assert after.enemy_sheet == before.enemy_sheet
    assert after.enemy_mood == before.enemy_mood
