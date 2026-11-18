"""M9 controller gates: preview/confirm, pacing, learning, saves and state truth."""
import json
from types import SimpleNamespace

from pirates.duel_system.engine import Choice, check_end_conditions
from pirates.duel_system.model import ActionKind, EventType, Zone
from pirates.duel_system.session import DuelSession


def test_select_is_free_confirm_commits_and_npc_uses_pre_choice_snapshot():
    session = DuelSession.new(guide_mode="off")
    player, enemy = session.state.fighters
    player.pos, enemy.pos = (4, 3), (5, 3)
    enemy.personality.weights["reading"] = 3
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
    session.enemy.personality.reaction_delay = 20
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
    session.enemy.ai_experience = [{"category": "LIGHT", "outcome": "COUNTERED"}]
    session.enemy.ai_stagnant_decisions = 2
    session.enemy.ai_current_category = "LIGHT"
    session.select(Choice(ActionKind.WALK, direction=(0, 1)))
    data = json.loads(json.dumps(session.to_data()))
    restored = DuelSession.from_data(data)
    assert restored.to_data() == data
    assert restored.enemy.ai_experience == session.enemy.ai_experience
    assert restored.enemy.ai_stagnant_decisions == 2
    assert restored.confirm()
    restored.run_to_player()
    assert restored.notebook.to_data() != session.notebook.to_data()


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


def test_fear_break_is_recorded_as_observed_yield_before_fight_end():
    session = DuelSession.new(guide_mode="off")
    session.enemy.morale, session.enemy.anger, session.enemy.fear_decisions = 0, 0, 2
    session.select(Choice(ActionKind.WAIT))
    assert not session.confirm()
    decision_index = next(index for index, event in enumerate(session.state.event_log)
                          if event.type == EventType.DECISION and event.deltas["category"] == "YIELD")
    end_index = next(index for index, event in enumerate(session.state.event_log) if event.type == EventType.FIGHT_END)
    assert decision_index < end_index
    assert any("YIELD" in entry.counts for entry in session.notebook.archetypes["brute"].values())


def test_session_advances_fear_break_counter_on_each_npc_decision(monkeypatch):
    session = DuelSession.new(guide_mode="off", seed=804)
    session.enemy.morale, session.enemy.anger = 0, 0
    monkeypatch.setattr("pirates.duel_system.session.choose_action",
                        lambda state, fighter_id: SimpleNamespace(
                            category="WAIT", choice=Choice(ActionKind.WAIT)))

    assert session.resolve_round(Choice(ActionKind.WAIT))
    assert session.enemy.fear_decisions == 1 and not session.state.end_reason
    assert session.resolve_round(Choice(ActionKind.WAIT))
    assert session.enemy.fear_decisions == 2 and not session.state.end_reason
    assert not session.resolve_round(Choice(ActionKind.WAIT))
    assert session.state.end_reason == "yield" and session.enemy.fear_decisions == 3
    decisions = [event for event in session.state.event_log
                 if event.type == EventType.DECISION and event.actor == session.enemy.id]
    assert [event.deltas["category"] for event in decisions] == ["WAIT", "WAIT", "YIELD"]
