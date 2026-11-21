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


def test_action_options_label_conditional_and_unavailable_states_instead_of_all_green():
    session = DuelSession.new(guide_mode="off")
    model = session.render_model()
    options = {option.label: option for option in model.actions}

    assert options["Light"].enabled
    assert options["Light"].assessment == "conditional"
    assert "CONDITIONAL" in options["Light"].details
    assert not options["Shove"].enabled
    assert options["Shove"].assessment == "unavailable"
    assert "close contact" in options["Shove"].details
    assert not options["Adrenaline"].enabled
    assert options["Adrenaline"].assessment == "unavailable"
    assert len({option.assessment for option in options.values()}) > 1


def test_action_assessment_uses_observed_control_window_and_incoming_threat_timing():
    session = DuelSession.new(guide_mode="off")
    player, enemy = session.player, session.enemy
    player.pos, enemy.pos = (4, 3), (5, 3)
    enemy.stagger_until = session.state.tick + 6
    options = {option.label: option for option in session.render_model().actions}
    assert options["Light"].assessment == "favorable"
    assert "fits before recovery" in options["Light"].details
    assert options["Heavy"].assessment == "conditional"
    assert "opening may expire first" in options["Heavy"].details

    enemy.stagger_until = session.state.tick
    commit_action(session.state, enemy.id, Choice(ActionKind.HEAVY, Zone.TORSO,
                                                   target_id=player.id))
    options = {option.label: option for option in session.render_model().actions}
    assert options["Wait"].assessment == "exposed"
    assert "Visible attack can reach you" in options["Wait"].details


def test_action_buttons_pair_cost_with_effect_time_and_enemy_response_deadline():
    session = DuelSession.new(guide_mode="off")
    session.enemy.pos = (session.player.pos[0] + 1, session.player.pos[1])
    before = session.state.to_data()
    model = session.render_model()
    options = {option.label: option for option in model.actions}

    assert options["Light"].stamina > 0
    assert options["Light"].time_ticks == 3
    assert options["Move"].stamina > 0
    assert options["Move"].time_ticks > 0
    assert options["Defend"].stamina == pytest.approx(
        session.state.config["guard"]["defend_setup_cost"]
        * (1 + session.state.config["gear"]["stamina_per_weight"]
           * session.player.equipment.total_weight))
    assert "Can choose now" in model.enemy_opportunity
    assert "step 3t (dash)" in model.enemy_opportunity
    assert "Light strike 4t" in model.enemy_opportunity
    assert "Opponent timing:" in options["Light"].details
    assert session.state.to_data() == before


def test_defend_button_reports_the_selected_response_cost_and_deadline():
    session = DuelSession.new(guide_mode="off")
    session.player.pos, session.enemy.pos = (4, 3), (5, 3)
    commit_action(session.state, session.enemy.id,
                  Choice(ActionKind.HEAVY, Zone.TORSO,
                         target_id=session.player.id))
    model = session.render_model()
    defend = next(option for option in model.actions if option.label == "Defend")

    assert "Defend → walk " in defend.details or "Defend → run " in defend.details \
        or "Defend → dash " in defend.details
    movement_kind = defend.details.split("Defend → ", 1)[1].split(" ", 1)[0].upper()
    expected_cost = (session.state.config["actions"][movement_kind]["stamina"]
                     * (1 + session.state.config["gear"]["stamina_per_weight"]
                        * session.player.equipment.total_weight))
    assert defend.stamina == pytest.approx(expected_cost)
    assert defend.time_ticks == int(
        defend.details.split("Leaves every visible attack line at t", 1)[1].split(",", 1)[0])
    assert defend.assessment == "favorable"
    assert "least stamina cost" in defend.details


def test_every_selectable_combat_and_recovery_action_has_cost_and_time_preview():
    session = DuelSession.new(guide_mode="off")
    options = {option.label: option for option in session.render_model().actions}
    actions = {"Light", "Heavy", "Thrust", "Defend", "Dash", "Move", "Run",
               "Dash+Light", "Feint", "Shove", "Disarm", "Disengage", "Wait",
               "Bandage", "Eat", "Drink", "Pickup"}

    assert actions <= set(options)
    for label in actions:
        option = options[label]
        if option.enabled:
            assert option.stamina is not None, label
            assert option.time_ticks is not None, label
            assert "Opponent timing:" in option.details, label
        else:
            assert option.assessment == "unavailable", label
            assert option.details, label


def test_opponent_earliest_response_slows_with_current_effective_condition():
    from pirates.duel_system.render_model import _opponent_read

    session = DuelSession.new(guide_mode="off")
    enemy = session.enemy
    _, fresh, _ = _opponent_read(session.state, enemy, visible=True, group_combat=False)
    enemy.fatigue = 95
    enemy.pain = 75
    enemy.blood = 45
    _, impaired, _ = _opponent_read(session.state, enemy, visible=True, group_combat=False)

    def step_ticks(text):
        return int(text.split("step ", 1)[1].split("t", 1)[0])

    assert step_ticks(impaired) > step_ticks(fresh)
    assert "condition-adjusted effective Speed" in impaired


def test_known_stagger_response_countdown_is_not_described_as_ready_now():
    session = DuelSession.new(guide_mode="off")
    session.enemy.stagger_until = session.state.tick + 5
    model = session.render_model()
    assert "Cannot voluntarily act for 5 more ticks" in model.enemy_opportunity
    assert "then step " in model.enemy_opportunity
    assert "next action unknown" not in model.enemy_opportunity.lower()


def test_held_guard_response_deadline_never_points_into_the_past():
    from pirates.duel_system.render_model import _opponent_read

    session = DuelSession.new(guide_mode="off")
    enemy = session.enemy
    guard = commit_action(session.state, enemy.id, Choice(ActionKind.GUARD))
    session.state.tick = guard.guard_ready_tick + guard.parry_window + 2
    _, opportunity, _ = _opponent_read(
        session.state, enemy, visible=True, group_combat=False)

    assert "earliest next choice no sooner than t" in opportunity
    deadline = int(opportunity.split("no sooner than t", 1)[1].split(",", 1)[0])
    assert deadline >= session.state.tick


def test_held_guard_deadline_includes_witnessed_heavy_window_extension():
    from pirates.duel_system.render_model import _opponent_read, _opponent_response_summary

    session = DuelSession.new(guide_mode="off")
    session.player.pos, session.enemy.pos = (4, 3), (5, 3)
    commit_action(session.state, session.enemy.id, Choice(ActionKind.GUARD))
    commit_action(session.state, session.player.id,
                  Choice(ActionKind.HEAVY, Zone.TORSO, target_id=session.enemy.id))
    _, opportunity, _ = _opponent_read(
        session.state, session.enemy, visible=True, group_combat=False)
    summary = _opponent_response_summary(
        session.state, session.enemy, visible=True, group_combat=False)

    detailed_tick = int(opportunity.split("no sooner than t", 1)[1].split(",", 1)[0])
    relative_tick = int(summary.split("+", 1)[1].split("t", 1)[0])
    assert detailed_tick == session.state.tick + relative_tick


def test_parry_riposte_is_actionable_and_consumed_by_followup_copy():
    session = DuelSession.new(guide_mode="off")
    session.player.pos, session.enemy.pos = (4, 3), (5, 3)
    session.player.riposte = True
    session.enemy.stagger_until = session.state.tick + 5
    options = {option.label: option for option in session.render_model().actions}

    assert "Parry riposte: this attack's windup is shortened" in options["Light"].details
    assert "commits the riposte" in options["Disarm"].details
    assert "Observed parry opening until t5" in options["Disarm"].details


def test_unsteady_opponent_condition_and_shove_advantage_are_explained():
    session = DuelSession.new(guide_mode="off")
    session.player.pos, session.enemy.pos = (4, 3), (5, 3)
    session.enemy.stability = 60
    model = session.render_model()
    shove = next(option for option in model.actions if option.label == "Shove")

    assert "unsteady stance" in model.enemy_condition
    assert shove.enabled and shove.assessment == "conditional"
    assert "contact costs stability" in shove.details.lower()
    assert "off-balance interrupts a winding heavy" in shove.details.lower()
    assert "observed unsteady stance favors your contest" in shove.details.lower()


def test_player_bleed_readout_reports_capped_ten_tick_rate_and_treatment_by_wound():
    session = DuelSession.new(guide_mode="off")
    session.player.wounds = [Wound(Zone.TORSO, Severity.DEEP, 19),
                             Wound(Zone.ARM, Severity.CUT, 4, bandaged=True)]
    model = session.render_model()
    assert any("Bleeding 20 blood per turn (10 ticks)" in alert for alert in model.functional_alerts)
    assert any("torso" in line.lower() and "continuing bleed 19/turn (untreated)" in line
               for line in model.player_injury_rows)
    assert any("arm" in line.lower() and "continuing bleed 1.6/turn (partially bandaged)" in line
               for line in model.player_injury_rows)


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


def test_attack_action_explains_committed_aim_lane_and_clash_window():
    model = DuelSession.new(guide_mode="off").render_model()
    light = next(option for option in model.actions if option.label == "Light")
    assert "aim commits to the mid blade line" in light.details.lower()
    assert "intersecting weapon path" in light.details.lower()
    assert "within 1 tick" in light.details.lower()


def test_zone_forecast_is_conditional_numeric_and_render_pure():
    session = DuelSession.new(guide_mode="off")
    session.enemy.pos = (session.player.pos[0]+1, session.player.pos[1])
    before = session.state.to_data()

    model = session.render_model(zone_kind=ActionKind.LIGHT)

    forecast_options = [option for option in model.zones if option.forecast]
    forecasts = [option.forecast for option in forecast_options]
    assert len(forecasts) >= 5
    assert all("If line/reach holds and contact is clean" in forecast for forecast in forecasts)
    assert all("wound force" in forecast and "zone-protection points" in forecast
               for forecast in forecasts)
    assert all("clean " in forecast and "Defense/next choice unknown" in forecast
               for forecast in forecasts)
    assert any("Strength ×" in forecast or "Speed ×" in forecast or "bleed" in forecast
               for forecast in forecasts)
    eye = next(option.forecast for option in forecast_options if option.zone == Zone.EYE)
    assert "Eye-trauma track" in eye and "/24 points" in eye
    assert "permanently impair" in eye
    assert session.state.to_data() == before


def test_observed_guard_forecast_shows_a_conditional_zero_body_injury_branch():
    session = DuelSession.new(guide_mode="off")
    session.enemy.pos = (session.player.pos[0]+1, session.player.pos[1])
    commit_action(session.state, session.enemy.id, Choice(ActionKind.GUARD))

    model = session.render_model(zone_kind=ActionKind.LIGHT)

    assert all("Observed Guard succeeds: 0 impact, no wound" in option.forecast
               for option in model.zones if option.forecast)


def test_stability_meter_help_uses_stable_stat_key_and_tactical_actions_are_reachable():
    from pirates.duel_layout import context_labels
    from pirates.duel_stat_help import STATS, stat_rows
    from pirates.duel_ui import DuelUI

    session = DuelSession.new(guide_mode="off")
    ui = DuelUI(session, rounds=True)
    model = ui.model()
    labels = context_labels(ui.sidebar_rows(model), model)
    assert {"Defend", "Thrust", "Dash+Light", "Feint", "Shove", "Disarm",
            "Disengage", "Move", "Run"} <= set(labels)
    defend = next(option for option in model.actions if option.label == "Defend")
    assert "No committed threat is visible" in defend.details
    assert "bounded" in defend.details.lower()
    assert {"Defend", "Feint", "Shove", "Disarm", "Disengage", "Thrust"} <= set(
        context_labels(format_ascii(model), model))
    assert "Stability" in STATS
    help_rows = stat_rows(model, "Stability (bal 44)")
    assert "Stability:" in help_rows[0]
    assert "current balance reserve" in help_rows[1].lower()
    assert f"{model.player_stability:.1f} / 100" in help_rows[0]

    # Keep Tactics discoverable on the main row and retain the More-menu alias.
    ui.activate("More")
    assert "Tactics" in context_labels(ui.sidebar_rows(ui.model()))
    ui.activate("Tactics")
    assert context_labels(ui.sidebar_rows(ui.model())) == [
        "Feint", "Shove", "Disengage", "Thrust", "Disarm", "Adrenaline", "Back"]
    options = {option.label for option in ui.model().actions}
    assert {"Feint", "Shove", "Disengage", "Thrust", "Disarm", "Adrenaline"} <= options


def test_accuracy_help_explains_fear_and_anger_debuffs_without_claiming_raw_skill_changes():
    from pirates.duel_stat_help import stat_rows
    from pirates.duel_system.model import Status

    session = DuelSession.new(guide_mode="off")
    player = session.player
    authored = player.accuracy
    player.level = 8
    player.fatigue, player.pain, player.blood = 90, 90, 20
    player.anger, player.morale, player.nervousness = 90, 0, 1
    player.permanent_injuries.eyes_damaged = 2
    player.statuses.append(Status("concussed", until_tick=20, accuracy_mult=0.1))
    model = session.render_model()
    assert dict(model.player_sheet)["Accuracy"] == f"{authored * (1 - session.state.config['anger']['acc_penalty'] * .9):.1f} (base {authored:g})"
    text = " ".join(stat_rows(model, "Accuracy")).lower()
    assert "authored skill stays fixed" in text
    assert "fear and anger" in text
    assert "authored accuracy is permanent" in text
    assert "fear lowers speed/accuracy" in " ".join(model.player_emotion_rows).lower()


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


def test_opponent_read_separates_observed_commitment_opportunity_and_condition():
    session = DuelSession.new(guide_mode="off")
    model = session.render_model()
    assert model.enemy_commitment == "Ready; no committed action visible."
    assert "next choice unknown" in model.enemy_opportunity.lower()
    assert "No obvious visible" in model.enemy_condition
    assert "COMMITMENT:" in "\n".join(format_ascii(model))

    enemy = session.enemy
    commit_action(session.state, enemy.id, Choice(ActionKind.HEAVY, Zone.TORSO,
                                                  target_id=session.player.id))
    model = session.render_model()
    assert "Committed Heavy" in model.enemy_commitment
    assert "tracking west, locks t" in model.enemy_commitment
    assert "contact t" in model.enemy_commitment
    light = next(option for option in model.actions if option.label == "Light")
    assert "Intent: quick cut" in light.details
    assert "Current commitments overlap" in light.details or "before your contact" in light.details


def test_opponent_read_hides_unseen_solo_enemy_state_and_actions():
    session = DuelSession.new(guide_mode="off")
    commit_action(session.state, session.enemy.id,
                  Choice(ActionKind.HEAVY, Zone.TORSO, target_id=session.player.id))
    session.enemy.pos = (12, 6)
    model = session.render_model()
    assert not model.enemy_visible
    assert model.enemy_action == "UNKNOWN"
    assert "No visible commitment" in model.enemy_commitment
    assert "unknown" in model.enemy_opportunity.lower()
    assert "hidden" not in model.enemy_condition.lower()  # no claim about an unseen state


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
