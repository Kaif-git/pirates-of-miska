"""P5 tactical actions: commitments, costs, counterplay and public outcomes."""
from copy import deepcopy

import pytest

from pirates.duel_system.ai import candidates, choose_action, observe, score_actions
from pirates.duel_system.config import load_config
from pirates.duel_system.engine import (Choice, advance_one_tick, commit_action,
                                        legality, resolve_adrenaline_expiry)
from pirates.duel_system.exchange_log import format_exchange
from pirates.duel_system.model import ActionKind, CombatState, EventType, Zone
from pirates.duel_system.notebook import Notebook
from pirates.duel_system.session import DuelSession
from pirates.duel_system.guide import legal_choices
from pirates.duel_system.stats import effective_stats
from pirates.duel_system.wounds import defense_snapshot, resolve_strike
from test_duel_system_foundation import bare_fighter
from test_duel_system_scheduler import advance_to, arena


def tactic_state(*, walkable=None):
    cfg = load_config()
    cfg["accuracy"]["jitter"] = 0
    return CombatState([bare_fighter(pos=(0, 0)), bare_fighter("ronin", pos=(1, 0))],
                       config=cfg, walkable=walkable)


def events(state, kind, actor=None):
    return [event for event in state.event_log
            if event.type == kind and (actor is None or event.actor == actor)]


def test_disengage_is_one_tile_away_movement_and_has_no_dodge_immunity():
    state = tactic_state()
    player, enemy = state.fighters
    assert legality(state, player.id, Choice(ActionKind.DISENGAGE, direction=(1, 0)))
    choice = Choice(ActionKind.DISENGAGE, direction=(-1, 0))
    assert not legality(state, player.id, choice)
    commit_action(state, player.id, choice)
    assert player.action.path == [(7, (-1, 0))]
    commit_action(state, enemy.id, Choice(ActionKind.LIGHT, Zone.TORSO))
    advance_to(state, enemy.action.strike_tick)
    assert events(state, EventType.HIT, enemy.id)
    advance_to(state, player.action.end_tick)
    assert player.pos == (-1, 0)


def test_feint_only_disrupts_an_in_reach_committed_guard_and_consumes_riposte():
    state = tactic_state()
    player, enemy = state.fighters
    commit_action(state, enemy.id, Choice(ActionKind.GUARD))
    commit_action(state, player.id, Choice(ActionKind.FEINT))
    enemy.riposte = True
    advance_to(state, player.action.end_tick)
    event = events(state, EventType.FEINT, player.id)[0]
    assert event.deltas["outcome"] == "GUARD_DISRUPTED"
    assert event.deltas["riposte_consumed"]
    assert not enemy.riposte
    assert enemy.stamina < 100
    assert enemy.action and enemy.action.kind == ActionKind.GUARD
    assert sum(event.type == EventType.ACTION_START and event.actor == player.id
               for event in state.event_log) == 1


def test_feint_at_range_costs_action_but_does_not_change_guard_or_riposte():
    state = tactic_state()
    player, enemy = state.fighters
    enemy.pos = (4, 0)
    commit_action(state, enemy.id, Choice(ActionKind.GUARD))
    commit_action(state, player.id, Choice(ActionKind.FEINT))
    enemy.riposte = True
    advance_to(state, player.action.end_tick)
    event = events(state, EventType.FEINT, player.id)[0]
    assert event.deltas["outcome"] == "NO_EFFECT"
    assert enemy.riposte
    assert event.deltas["guard_stamina_drained"] == 0


def test_shove_wins_strength_balance_contest_pushes_one_tile_and_costs_stability():
    state = tactic_state()
    player, enemy = state.fighters
    player.strength = 40
    enemy.strength = 1
    commit_action(state, player.id, Choice(ActionKind.SHOVE))
    advance_to(state, player.action.end_tick)
    event = events(state, EventType.SHOVE, player.id)[0]
    assert event.deltas["outcome"] == "SUCCESS"
    assert enemy.pos == (2, 0)
    assert enemy.stability < 100
    assert events(state, EventType.STABILITY, enemy.id)
    assert events(state, EventType.MOVE, enemy.id)[0].deltas["forced"]


def test_shove_blocked_by_edge_or_guard_has_no_push_or_stability_loss():
    state = tactic_state(walkable={(0, 0), (1, 0)})
    player, enemy = state.fighters
    player.strength = 40
    enemy.strength = 1
    commit_action(state, player.id, Choice(ActionKind.SHOVE))
    advance_to(state, player.action.end_tick)
    assert events(state, EventType.SHOVE, player.id)[0].deltas["outcome"] == "BLOCKED"
    assert enemy.pos == (1, 0) and enemy.stability == 100


def test_committed_shove_misses_if_target_disengages_before_resolution():
    state = tactic_state()
    player, enemy = state.fighters
    player.strength = 40
    commit_action(state, enemy.id, Choice(ActionKind.DISENGAGE, direction=(1, 0)))
    commit_action(state, player.id, Choice(ActionKind.SHOVE))
    advance_to(state, player.action.end_tick)
    assert enemy.pos == (2, 0)
    assert events(state, EventType.SHOVE, player.id)[0].deltas["outcome"] == "OUT_OF_RANGE"
    assert enemy.stability == 100

    state = tactic_state()
    player, enemy = state.fighters
    player.strength = enemy.strength = 10
    commit_action(state, enemy.id, Choice(ActionKind.GUARD))
    commit_action(state, player.id, Choice(ActionKind.SHOVE))
    advance_to(state, player.action.end_tick)
    event = events(state, EventType.SHOVE, player.id)[0]
    assert event.deltas["outcome"] == "RESISTED" and event.deltas["braced"]
    assert enemy.pos == (1, 0) and enemy.stability == 100


def test_same_tick_strike_resolves_before_forced_shove_movement():
    state = tactic_state()
    player, enemy = state.fighters
    player.strength = 40
    enemy.strength = 1
    commit_action(state, enemy.id, Choice(ActionKind.LIGHT, Zone.TORSO))
    commit_action(state, player.id, Choice(ActionKind.SHOVE))
    advance_to(state, player.action.end_tick)
    assert events(state, EventType.HIT, enemy.id)
    assert events(state, EventType.SHOVE, player.id)[0].deltas["outcome"] == "SUCCESS"
    assert enemy.pos == (2, 0)


def test_thrust_uses_standard_strike_pipeline_and_rejects_blunt_weapons():
    state = tactic_state()
    player, _ = state.fighters
    player.equipment.weapon.damage_type = "PIERCING"
    commit_action(state, player.id, Choice(ActionKind.THRUST, Zone.TORSO))
    assert player.action.strike_tick == 7
    advance_to(state, player.action.strike_tick)
    hit = events(state, EventType.HIT, player.id)[0]
    assert hit.deltas["damage_type"] == "PIERCING"
    assert hit.deltas["power_mult"] == state.config["actions"]["THRUST"]["power"]

    state = tactic_state()
    state.fighters[0].equipment.weapon.damage_type = "BLUNT"
    assert "cannot thrust" in legality(state, "player", Choice(ActionKind.THRUST, Zone.TORSO))


def test_disarm_requires_parry_setup_clean_hand_and_grip_win():
    state = tactic_state()
    player, enemy = state.fighters
    player.strength, player.accuracy, player.riposte = 40, 100, True
    player.equipment.weapon.damage_type = "PIERCING"
    commit_action(state, enemy.id, Choice(ActionKind.HEAVY, Zone.TORSO))
    commit_action(state, player.id, Choice(ActionKind.DISARM, Zone.HAND))
    assert not player.riposte
    advance_to(state, player.action.strike_tick)
    outcome = events(state, EventType.DISARM, player.id)[0]
    assert outcome.deltas["outcome"] == "DISARMED"
    assert enemy.disarmed

    state = tactic_state()
    player, enemy = state.fighters
    player.riposte = True
    player.riposte = False
    assert "requires a riposte" in legality(state, player.id, Choice(ActionKind.DISARM, Zone.HAND))
    player.riposte = True
    assert "must target the hand" in legality(state, player.id, Choice(ActionKind.DISARM, Zone.TORSO))
    commit_action(state, player.id, Choice(ActionKind.DISARM, Zone.HAND))
    advance_to(state, player.action.strike_tick)
    outcome = events(state, EventType.DISARM, player.id)[0]
    assert outcome.deltas["drifted"] and outcome.deltas["outcome"] == "GRIP_HELD"
    assert not enemy.disarmed

    state = tactic_state()
    player, enemy = state.fighters
    player.strength, player.accuracy, player.riposte = 10, 100, True
    enemy.strength = 40
    commit_action(state, enemy.id, Choice(ActionKind.HEAVY, Zone.TORSO))
    commit_action(state, player.id, Choice(ActionKind.DISARM, Zone.HAND))
    advance_to(state, player.action.strike_tick)
    outcome = events(state, EventType.DISARM, player.id)[0]
    assert not outcome.deltas["drifted"] and outcome.deltas["outcome"] == "GRIP_HELD"
    assert not enemy.disarmed


def test_guard_can_parry_a_disarm_attempt_and_keep_its_counterplay():
    state = tactic_state()
    attacker, defender = state.fighters
    attacker.riposte, attacker.accuracy, attacker.strength = True, 100, 20
    defender.strength = 100
    commit_action(state, defender.id, Choice(ActionKind.GUARD))
    action = commit_action(state, attacker.id, Choice(ActionKind.DISARM, Zone.HAND))
    state.tick = 5
    action.strike_tick = state.tick
    snapshot = defense_snapshot(state, defender)
    resolve_strike(state, attacker, defender, snapshot)
    assert events(state, EventType.PARRY, defender.id)
    assert defender.riposte and not defender.disarmed


def test_tactical_choices_are_legal_ai_and_guide_choices_with_public_setup_gates():
    state = tactic_state()
    player, enemy = state.fighters
    commit_action(state, enemy.id, Choice(ActionKind.GUARD))
    enemy.riposte = True
    advance_to(state, 4)
    view = observe(state, player.id)
    options = dict((category, choice) for category, choice in candidates(state, player, view))
    assert options["FEINT"].kind == ActionKind.FEINT
    assert options["SHOVE"].kind == ActionKind.SHOVE
    assert options["DISENGAGE"].kind == ActionKind.DISENGAGE
    assert options["THRUST"].kind == ActionKind.THRUST
    assert all(not legality(state, player.id, choice) for choice in options.values())
    assert "DISARM" not in options
    player.riposte = True
    options = dict((category, choice) for category, choice in candidates(state, player, observe(state, player.id)))
    assert options["DISARM"] == Choice(ActionKind.DISARM, Zone.HAND)

    ai_state = tactic_state()
    player, ronin = ai_state.fighters
    commit_action(ai_state, player.id, Choice(ActionKind.GUARD))
    advance_to(ai_state, 4)
    assert choose_action(ai_state, ronin.id).category == "FEINT"

    ai_state = tactic_state()
    ronin = ai_state.fighters[1]
    ronin.pain = 70
    assert choose_action(ai_state, ronin.id).category == "ADRENALINE"

    ai_state = tactic_state()
    player, ronin = ai_state.fighters
    player.pos, ronin.pos = (6, 3), (7, 3)
    action = commit_action(ai_state, player.id, Choice(ActionKind.LIGHT, Zone.TORSO))
    advance_to(ai_state, action.strike_tick+1)
    scores = {row.category: dict(row.reasons) for row in score_actions(ai_state, ronin.id)}
    assert "P5_SHOVE_OPENING" in scores["SHOVE"]
    assert "P5_THRUST_OPENING" in scores["THRUST"]


def test_v4_config_and_session_migrate_to_v7_without_changing_active_deadlines():
    session = DuelSession.new(seed=29, guide_mode="off")
    session.select(Choice(ActionKind.RUN, direction=(0, 1), tiles=2))
    assert session.confirm()
    data = session.to_data()
    old_path = deepcopy(data["state"]["fighters"][0]["action"]["path"])
    old_end = data["state"]["fighters"][0]["action"]["end_tick"]
    old_config = deepcopy(data["state"]["config"])
    old_config["schema_version"] = 4
    for key in ("FEINT", "SHOVE", "THRUST", "DISARM", "DISENGAGE"):
        old_config["actions"].pop(key)
    old_config.pop("tactics")
    old_config.pop("adrenaline", None)
    data["version"] = data["state"]["schema_version"] = data["state"]["config"]["schema_version"] = 4
    data["state"]["config"] = old_config
    for fighter_data in data["state"]["fighters"]:
        for key in ("adrenaline_used", "adrenaline_until_tick",
                    "adrenaline_debt_pending", "adrenaline_debt_due"):
            fighter_data.pop(key, None)
    data["notebook"]["version"] = 4
    restored = DuelSession.from_data(data)
    assert restored.state.config["schema_version"] == 7
    assert restored.to_data()["version"] == 7
    assert restored.player.action.path == [(tick, tuple(pos)) for tick, pos in old_path]
    assert restored.player.action.end_tick == old_end


def test_tactics_panel_exposes_legal_preview_and_thrust_target_controls():
    from pirates.duel_layout import context_labels
    from pirates.duel_ui import DuelUI

    ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=False)
    ui.activate("More")
    assert "Tactics" in context_labels(ui.sidebar_rows(ui.model()))
    ui.activate("Tactics")
    assert context_labels(ui.sidebar_rows(ui.model())) == ["Feint", "Shove", "Disengage", "Thrust", "Disarm", "Adrenaline", "Back"]
    assert "LOCKED" in ui.model().adrenaline_status
    ui.activate("Thrust")
    assert ui.panel == "zones" and "Choose target:" in " ".join(ui.sidebar_rows(ui.model()))
    ui.activate("Torso")
    assert ui.panel == "preview" and ui.session.selected.kind == ActionKind.THRUST
    model = ui.model()
    assert model.preview.valid
    assert "slower than Light" in next(option.details for option in model.actions if option.label == "Thrust")


def test_adrenaline_is_threshold_gated_once_only_and_suppresses_only_pain_stat_penalty():
    state = tactic_state()
    player = state.fighters[0]
    choice = Choice(ActionKind.ADRENALINE)
    assert "meaningful danger" in legality(state, player.id, choice)
    assert choice not in legal_choices(state, player.id)

    player.pain = state.config["adrenaline"]["trigger_pain_min"]
    assert not legality(state, player.id, choice)
    assert choice in legal_choices(state, player.id)
    candidate_map = dict(candidates(state, state.fighters[1], observe(state, "ronin")))
    # The AI's own threshold is independently checked; a healthy Ronin has no surge candidate.
    assert "ADRENALINE" not in candidate_map
    scores = {row.category: row for row in score_actions(state, player.id)}
    assert "P5_ADRENALINE_TRIGGER" in dict(scores["ADRENALINE"].reasons)

    initial_pain = player.pain
    commit_action(state, player.id, choice)
    assert player.adrenaline_used and player.adrenaline_until_tick is None
    assert not player.adrenaline_debt_pending
    assert player.action.windup_len == 5 and player.action.end_tick == 8
    advance_to(state, player.action.end_tick)
    rule = state.config["adrenaline"]
    assert player.adrenaline_until_tick == state.tick + rule["duration_ticks"]
    assert player.adrenaline_debt_pending and player.adrenaline_debt_due == rule["fatigue_debt"]
    assert player.pain < initial_pain  # ordinary passive pain decay, not an adrenaline heal
    active = effective_stats(player, state.config, state.tick)
    ordinary = deepcopy(player)
    ordinary.adrenaline_until_tick = None
    baseline = effective_stats(ordinary, state.config, state.tick)
    assert active.pain_penalty == pytest.approx(baseline.pain_penalty * (1-rule["pain_suppression"]))
    assert active.speed > baseline.speed and active.strength > baseline.strength
    assert "already used" in legality(state, player.id, choice)
    activated_details = " ".join(format_exchange(state.event_log, {"player": "You", "ronin": "Ronin"}))
    assert "adrenaline active through" in activated_details.lower()
    assert "fatigue debt" in activated_details.lower()

    until = player.adrenaline_until_tick
    resumed = CombatState.from_data(state.to_data())
    advance_to(state, until)
    advance_to(resumed, until)
    assert state.to_data() == resumed.to_data()
    expired = events(state, EventType.ADRENALINE, player.id)
    expiry = [event for event in expired if event.deltas.get("outcome") == "EXPIRED"]
    assert len(expiry) == 1 and expiry[0].deltas["debt_applied"] == rule["fatigue_debt"]
    assert not player.adrenaline_debt_pending and player.adrenaline_debt_due == 0
    after_debt = player.fatigue
    resolve_adrenaline_expiry(state)
    assert player.fatigue == after_debt
    assert len([event for event in events(state, EventType.ADRENALINE, player.id)
                if event.deltas.get("outcome") == "EXPIRED"]) == 1
    expired_details = " ".join(format_exchange(state.event_log, {"player": "You", "ronin": "Ronin"}))
    assert "fatigue debt" in expired_details.lower() and "expired" in expired_details.lower()


def test_interrupted_adrenaline_spends_use_without_activating_or_creating_debt():
    state = tactic_state()
    player, enemy = state.fighters
    player.pain = 40
    enemy.strength, enemy.accuracy = 50, 100
    commit_action(state, player.id, Choice(ActionKind.ADRENALINE))
    commit_action(state, enemy.id, Choice(ActionKind.LIGHT, Zone.TORSO))
    advance_to(state, 4)
    outcomes = [event.deltas["outcome"] for event in events(state, EventType.ADRENALINE, player.id)]
    assert outcomes == ["COMMITTED", "INTERRUPTED"]
    assert player.action is None and player.adrenaline_used
    assert player.adrenaline_until_tick is None
    assert not player.adrenaline_debt_pending and player.adrenaline_debt_due == 0
    assert "already used" in legality(state, player.id, Choice(ActionKind.ADRENALINE))


def test_adrenaline_active_timer_and_unpaid_debt_carry_but_fresh_restart_resets_gate():
    session = DuelSession.new(seed=47, guide_mode="off")
    player = session.player
    player.pain = 45
    player.adrenaline_used = True
    player.adrenaline_until_tick = 24
    player.adrenaline_debt_pending = True
    player.adrenaline_debt_due = 18
    session.state.tick = 9
    session.state.end_reason, session.state.winner = "yield", session.enemy.id

    carried = session.restart(carry=True)
    next_player = carried.player
    assert next_player.adrenaline_used
    assert next_player.adrenaline_until_tick == 15
    assert next_player.adrenaline_debt_pending and next_player.adrenaline_debt_due == 18
    next_player.pain = 45
    active = effective_stats(next_player, carried.state.config, 0)
    ordinary = deepcopy(next_player)
    ordinary.adrenaline_until_tick = None
    assert active.pain_penalty < effective_stats(ordinary, carried.state.config, 0).pain_penalty
    advance_to(carried.state, 15)
    assert not next_player.adrenaline_debt_pending
    expiry = [event for event in events(carried.state, EventType.ADRENALINE, next_player.id)
              if event.deltas.get("outcome") == "EXPIRED"]
    assert len(expiry) == 1 and expiry[0].deltas["debt_applied"] == 18

    carried.state.end_reason, carried.state.winner = "yield", carried.enemy.id
    carried_again = carried.restart(carry=True)
    assert carried_again.player.adrenaline_used
    assert not carried_again.player.adrenaline_debt_pending
    fresh = session.restart(carry=False)
    assert not fresh.player.adrenaline_used
    assert fresh.player.adrenaline_until_tick is None
