"""M9 controller gates: preview/confirm, pacing, learning, saves and state truth."""
import json
from types import SimpleNamespace

from pirates.duel_system.engine import Choice, check_end_conditions, legality
from pirates.duel_system.model import ActionKind, EventType, Zone
from pirates.duel_system.session import DuelSession


def test_select_is_free_confirm_commits_and_npc_uses_pre_choice_snapshot():
    session = DuelSession.new(guide_mode="off")
    player, enemy = session.state.fighters
    player.pos, enemy.pos = (4, 3), (5, 3)
    enemy.personality.stress_composure = 0
    before = session.state.to_data()
    preview = session.select(Choice(ActionKind.LIGHT, Zone.TORSO))
    assert preview.valid
    assert session.state.to_data() == before
    assert session.confirm()
    decisions = [e for e in session.state.event_log if e.type == EventType.DECISION]
    assert decisions[0].deltas["category"] == "LIGHT"  # not mind-reading GUARD
    assert player.action is not None
    session.run_to_player()
    assert session.ready or session.state.end_reason


def test_walk_remains_a_real_action_after_a_committed_attack():
    session = DuelSession.new(guide_mode="off")
    session.select(Choice(ActionKind.LIGHT, Zone.TORSO))  # out-of-range whiff is still a committed action
    session.confirm()
    session.run_to_player()
    assert session.select(Choice(ActionKind.WALK, direction=(0, 1))).valid
    before = session.state.fighters[0].pos
    session.confirm()
    session.run_to_player()
    assert session.state.fighters[0].pos != before


def test_guard_hold_is_explicit_decision_and_release_resolves_without_freeze():
    session = DuelSession.new(guide_mode="off")
    session.select(Choice(ActionKind.GUARD))
    session.confirm()
    session.run_to_player()
    assert session.holding_guard or session.state.end_reason
    if not session.state.end_reason:
        tick = session.state.tick
        session.hold_guard()
        assert session.state.tick > tick
        if session.holding_guard:
            session.release_guard()
            session.run_to_player()
            assert session.ready or session.state.end_reason


def test_npc_guard_release_does_not_peek_at_unseen_player_heavy():
    from pirates.duel_system.engine import commit_action
    session = DuelSession.new(guide_mode="off")
    session.state.config["ai"]["reaction_delay_default"] = 20
    commit_action(session.state, session.enemy.id, Choice(ActionKind.GUARD))
    commit_action(session.state, session.player.id, Choice(ActionKind.HEAVY, Zone.TORSO))
    session.state.tick = session.enemy.action.guard_ready_tick + session.enemy.action.parry_window
    session._release_npc_guard()
    assert session.enemy.action.released


def test_previewing_and_reading_model_do_not_record_extra_npc_decisions():
    session = DuelSession.new(guide_mode="off")
    before = session.notebook.to_data()
    session.render_model()
    session.select(Choice(ActionKind.WAIT))
    session.render_model()
    assert session.notebook.to_data() == before
    session.confirm()
    assert session.notebook.to_data() != before
    recorded = session.notebook.to_data()
    session.render_model()
    session.render_model()
    assert session.notebook.to_data() == recorded


def test_session_roundtrip_preserves_active_replay_notebook_and_pending_preview():
    session = DuelSession.new(guide_mode="off")
    session.select(Choice(ActionKind.WALK, direction=(0, 1)))
    data = json.loads(json.dumps(session.to_data()))
    restored = DuelSession.from_data(data)
    assert restored.to_data() == data
    assert restored.confirm()
    restored.run_to_player()
    assert restored.notebook.to_data() != session.notebook.to_data()


def test_schema26_session_without_mode_latches_refreshes_on_first_decision():
    session = DuelSession.new(guide_mode="off", seed=27)
    session.enemy.fear_meter = 60
    data = json.loads(json.dumps(session.to_data()))
    data["version"] = 26
    data["notebook"]["version"] = 26
    data["state"]["schema_version"] = 26
    data["state"]["config"]["schema_version"] = 26
    data["state"]["config"].pop("behavior_thresholds")
    for fighter in data["state"]["fighters"]:
        fighter.pop("behavior_modes", None)
    restored = DuelSession.from_data(data)
    assert restored.state.config["schema_version"] == 30
    assert restored.enemy.behavior_modes["fear"] == "NONE"
    assert restored._start_exchange()
    assert restored.enemy.behavior_modes["fear"] == "CAUTIOUS"
    transition = next(event for event in restored.state.event_log
                      if event.type == EventType.BEHAVIOR_MODE)
    assert transition.deltas["cause"] == "DECISION_BOUNDARY"


def test_finished_model_distinguishes_death_unconscious_and_victory():
    for outcome in ("dead", "unconscious", "victory"):
        session = DuelSession.new(guide_mode="off")
        if outcome == "dead":
            session.state.fighters[0].blood = 0
        elif outcome == "unconscious":
            session.state.fighters[0].unconscious = True
        else:
            session.state.fighters[1].yielded = True
        check_end_conditions(session.state)
        model = session.render_model()
        assert outcome.upper() in model.player_state
        assert "retry" in model.instructions.lower()
        assert not any(action.enabled for action in model.actions)


def test_exit_does_not_mutate_ship_world_and_preserves_learning(tmp_path):
    from pirates.game import World
    world = World()
    before = (world.game_map, world.player.x, world.player.y, world.clock.total_ticks)
    session = DuelSession.new(guide_mode="off")
    session.select(Choice(ActionKind.WAIT))
    session.confirm()
    session.run_to_player()
    session.leave()
    world.duel_journal = session.to_data()
    path = tmp_path / "duel-learning.json"
    world.save(path)
    loaded = World.load(path)
    assert loaded.duel_journal == world.duel_journal
    assert (world.game_map, world.player.x, world.player.y, world.clock.total_ticks) == before


def test_next_duel_carries_injuries_resources_and_expiring_status_deadlines():
    from pirates.duel_system.model import Severity, Status, Wound
    session = DuelSession.new(guide_mode="off")
    session.state.tick = 30
    session.player.wounds = [Wound(Zone.TORSO, Severity.CUT, 3)]
    session.player.blood, session.player.fatigue, session.player.stamina = 75, 40, 55
    session.player.bandages = 0
    session.player.statuses = [Status("weak_grip", until_tick=50, strength_mult=.8)]
    session.enemy.yielded = True
    check_end_conditions(session.state)
    following = session.restart(carry=True)
    assert (following.player.blood, following.player.fatigue, following.player.stamina) == (75, 40, 55)
    assert following.player.wounds[0].bleed_per_turn == 3
    assert following.player.bandages == 0
    assert following.player.statuses[0].until_tick == 20
    assert following.state.tick == 0
    assert following.enemy.personality.archetype == "aggressor"


def test_critical_fear_low_morale_without_viable_exit_yields_before_fight_end():
    session = DuelSession.new(guide_mode="off")
    session.state.exits.clear()
    session.enemy.morale, session.enemy.fear_meter = 25, 100
    session.select(Choice(ActionKind.WAIT))
    assert session.confirm()
    assert session.state.end_reason == "yield"
    decision_index = next(index for index, event in enumerate(session.state.event_log)
                          if event.type == EventType.DECISION and event.deltas["category"] == "YIELD")
    end_index = next(index for index, event in enumerate(session.state.event_log) if event.type == EventType.FIGHT_END)
    assert decision_index < end_index
    assert any("YIELD" in entry.counts
               for entry in session.notebook.named[session.enemy.id].values())


def test_simultaneous_critical_pain_fear_and_hot_anger_yield_once_at_low_morale():
    session = DuelSession.new(guide_mode="off")
    session.state.exits.clear()
    session.enemy.personality.pain_tolerance = 0
    session.enemy.pain = session.enemy.fear_meter = session.enemy.anger = 100
    session.enemy.morale = 25
    session.select(Choice(ActionKind.WAIT))
    assert session.confirm()
    assert session.state.end_reason == "yield"
    assert session.enemy.yielded
    assert session.enemy.behavior_modes == {"pain": "BREAK", "fear": "BREAK", "anger": "HOT"}
    starts = [event for event in session.state.event_log if event.type == EventType.ACTION_START]
    assert {fighter.id: sum(event.actor == fighter.id for event in starts)
            for fighter in session.state.fighters} == {"player": 1, "ronin": 1}


def test_low_morale_alone_does_not_yield_and_subcritical_fear_has_no_legacy_counter(monkeypatch):
    session = DuelSession.new(guide_mode="off", seed=804)
    session.state.exits.clear()
    session.enemy.morale, session.enemy.fear_meter = 0, 99.99
    monkeypatch.setattr("pirates.duel_system.session.choose_action",
                        lambda state, fighter_id: SimpleNamespace(
                            category="WAIT", choice=Choice(ActionKind.WAIT)))

    assert session.resolve_round(Choice(ActionKind.WAIT))
    assert session.enemy.fear_decisions == 0 and not session.state.end_reason
    assert session.resolve_round(Choice(ActionKind.WAIT))
    assert session.enemy.fear_decisions == 0 and not session.state.end_reason
    decisions = [event for event in session.state.event_log
                 if event.type == EventType.DECISION and event.actor == session.enemy.id]
    assert [event.deltas["category"] for event in decisions] == ["WAIT", "WAIT"]


def test_critical_fear_with_viable_exit_routes_instead_of_yielding():
    session = DuelSession.new(guide_mode="off")
    session.enemy.pos = (9, 3)
    session.enemy.morale = 0
    session.enemy.fear_meter = 100
    session.enemy.behavior_modes["fear"] = "BREAK"
    observation, category, choice = session._npc_plan(session.state.clone(), session.enemy.id)
    assert observation is not None
    assert category in {"DASH_OUT", "WALK_OUT", "RUN_OUT"}
    from pirates.duel_system.engine import movement_path
    destination = movement_path(session.state, session.enemy, choice)[-1]
    assert min(max(abs(destination[0]-x), abs(destination[1]-y))
               for x, y in session.state.exits) < min(
                   max(abs(session.enemy.pos[0]-x), abs(session.enemy.pos[1]-y))
                   for x, y in session.state.exits)


def test_critical_fear_without_exit_and_above_yield_morale_falls_back_to_legal_action():
    session = DuelSession.new(guide_mode="off")
    session.state.exits.clear()
    session.enemy.morale = 26
    session.enemy.behavior_modes["fear"] = "BREAK"
    observation, category, choice = session._npc_plan(session.state.clone(), session.enemy.id)
    assert observation is not None
    assert category != "YIELD"
    assert choice is not None and not legality(session.state, session.enemy.id, choice)
