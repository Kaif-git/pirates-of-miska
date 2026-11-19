from pirates.duel_system.engine import Choice, commit_action
from pirates.duel_system.exchange_log import format_exchange, summarize_exchange
from pirates.duel_system.model import ActionKind, Event, EventType, Zone
from pirates.duel_system.session import DuelSession
from pirates.duel_ui import DuelUI
from pirates.duel_stat_help import stat_rows
from test_duel_system_scheduler import arena, advance_to
from tcod.event import KeySym


NAMES = {"player": "You", "ronin": "Ronin"}


def test_compact_recap_separates_bleed_control_injury_and_private_uncertainty():
    events = [
        Event(1, EventType.ACTION_START, actor="player", deltas={"kind": "SHOVE"}),
        Event(1, EventType.STABILITY, actor="ronin", deltas={"source": "HIT_IMPACT", "loss": 4.5}),
        Event(1, EventType.MOVE, actor="ronin", deltas={"forced": True, "from": (1, 0), "to": (2, 0)}),
        Event(2, EventType.POOL_CHANGE, actor="ronin", deltas={"bleed_loss": .42}),
        Event(2, EventType.WOUND, actor="player", target="ronin", zone=Zone.TORSO),
    ]
    normal = summarize_exchange(events, NAMES)
    assert len(normal) == 4
    assert "−4.5 stability" in normal[2]
    assert "pushed 1 tile" in normal[2]
    assert "−0.4 blood" in normal[2]
    private = summarize_exchange(events, NAMES, private_actor_ids={"ronin"})
    assert "off-balance" in private[2]
    assert "bleeding; amount unknown" in private[2]
    assert "4.5 stability" not in private[2] and "0.42" not in private[2]


def test_default_recap_is_compact_and_separate_from_the_calculation_ledger():
    ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
    ui.activate("Light")
    ui.activate("Torso")
    choices, outcome, consequences, reason = ui.round_summary
    assert "Light → Torso" in choices and "Ronin:" in choices
    assert "missed" in outcome and "hit" in outcome
    assert reason == "Reach: Distance 3 > reach 1."
    assert len(ui.round_summary) == 4
    assert "Effects:" in consequences
    assert sum(map(len, ui.round_summary)) < 360
    assert "Endurance" not in " ".join(ui.round_summary)
    assert "Endurance" in " ".join(ui.round_result)
    assert not any(token in " ".join(ui.round_summary).lower()
                   for token in ("net damage", "damage trade", "power", "armor"))
    assert "injury" in consequences and "pain +" in consequences


def test_recap_uses_accuracy_threshold_for_drift_not_power():
    state = arena()
    state.fighters[0].accuracy = 1
    commit_action(state, "player", Choice(ActionKind.LIGHT, Zone.HEAD))
    advance_to(state, state.fighters[0].action.strike_tick)
    recap = summarize_exchange(state.event_log, NAMES)
    assert recap[3].startswith("Aim: Accuracy") and "needed → torso" in recap[3]
    assert "Impact:" not in recap[3]


def test_tactical_exchange_recap_explains_feint_shove_and_setup_gated_disarm():
    state = arena()
    player, enemy = state.fighters
    player.strength, enemy.strength = 40, 1
    commit_action(state, "player", Choice(ActionKind.SHOVE))
    advance_to(state, player.action.end_tick)
    short = summarize_exchange(state.event_log, NAMES)
    details = " ".join(format_exchange(state.event_log, NAMES))
    assert "shove success" in short[1].lower() and "strength/balance" in short[3].lower()
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
    assert not any(token in " ".join(ui.round_summary).lower()
                   for token in ("net damage", "damage trade"))


def test_round_recap_keeps_an_already_active_visible_opponent_commitment():
    session = DuelSession.new(guide_mode="off")
    enemy = session.enemy
    commit_action(session.state, enemy.id,
                  Choice(ActionKind.HEAVY, Zone.TORSO, target_id=session.player.id))
    ui = DuelUI(session, rounds=True)
    current_events = [Event(session.state.tick, EventType.ACTION_START, actor=session.player.id,
                            deltas={"kind": "WAIT"})]
    events = ui._include_boundary_commitments(current_events, [(enemy, enemy.action)])
    summary = summarize_exchange(events, {"player": "You", "ronin": "Ronin"})
    choices = summary[0]
    assert "You: Wait" in choices
    assert "Ronin: Heavy" in choices
    assert "Ronin" in " ".join(summary[1:3])


def test_detailed_hit_recap_reports_wound_and_function_without_power_arithmetic():
    state = arena()
    player, enemy = state.fighters
    player.accuracy = 100
    commit_action(state, player.id, Choice(ActionKind.LIGHT, Zone.TORSO, target_id=enemy.id))
    advance_to(state, player.action.strike_tick)
    details = " ".join(format_exchange(state.event_log, NAMES))
    assert "contact reached" in details
    assert "function:" in details
    assert "pain increased" in details
    assert "power" not in details.lower()
    assert "net" not in details.lower()
    assert "armor" not in details.lower()
