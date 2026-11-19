from pirates.duel_scene import ExchangePlayback
from pirates.duel_system.session import DuelSession
from pirates.duel_ui import DuelUI
from dataclasses import replace
from pirates.duel_system.render_model import Popup


def test_playback_records_real_independent_commitments_and_intermediate_moves():
    ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
    ui.activate("Move")
    ui.activate("S")
    assert ui.playback
    first, last = ui.playback[0], ui.playback[-1]
    assert first.player_action == "WALK"
    assert first.enemy_action != "IDLE"
    assert last.player_pos != first.player_pos
    assert last.tick == ui.session.state.tick
    starts = [e for e in ui.session.state.event_log if e.type.value == "ACTION_START"]
    assert len(starts) == 2
    before = ui.session.state.to_data()
    playback = ExchangePlayback(ui.playback, started=0)
    for now in (0, .2, .7, 1.1, 3, 5):
        model, positions, done, transition = playback.sample(now)
        assert model in ui.playback
    assert ui.session.state.to_data() == before
    assert playback.sample(playback.duration+.1)[2]


def test_playback_uses_engine_easing_not_teleportation():
    ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
    ui.activate("Move")
    ui.activate("S")
    playback = ExchangePlayback(ui.playback, started=0)
    index = next(i for i in range(len(ui.playback)-1)
                 if ui.playback[i].player_pos != ui.playback[i+1].player_pos)
    model, positions, done, _ = playback.sample(playback.offset(index, .5))
    start, end = ui.playback[index].player_pos, ui.playback[index+1].player_pos
    assert start[1] < positions[0][1] < end[1]
    assert not done


def test_flinch_playback_is_bounded_within_tile_and_disabled_by_reduced_motion():
    ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
    model = ui.model()
    enemy = next(actor for actor in model.actors if not actor.is_player)
    end = replace(model, tick=model.tick+1,
                  popups=(Popup(enemy.pos, "Flinched", True, model.tick+1),))
    normal = ExchangePlayback((model, end), started=0)
    sample_time = normal.tell + normal.step/2
    _, positions, _, _ = normal.sample(sample_time)
    by_id = {actor.actor_id: position for actor, position in zip(model.actors, positions)}
    assert 0 < by_id[enemy.actor_id][0]-enemy.pos[0] <= .161
    assert by_id[enemy.actor_id][1] == enemy.pos[1]
    reduced = ExchangePlayback((model, end), started=0, reduced_motion=True)
    _, reduced_positions, _, _ = reduced.sample(sample_time)
    assert reduced_positions == tuple(actor.pos for actor in model.actors)


def test_commitment_is_visible_before_first_movement():
    ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
    ui.activate("Light")
    ui.activate("Torso")
    playback = ExchangePlayback(ui.playback, started=0)
    model, positions, done, _ = playback.sample(.2)
    assert model.player_action == "LIGHT" and model.enemy_action != "IDLE"
    assert positions == (model.player_pos, model.enemy_pos)
    assert not done


def test_recording_frames_does_not_change_resolution_or_rng():
    from pirates.duel_system.engine import Choice
    from pirates.duel_system.model import ActionKind
    from pirates.duel_system.render_model import build_render_model
    plain, recorded = DuelSession.new(guide_mode="off"), DuelSession.new(guide_mode="off")
    choice = Choice(ActionKind.WAIT)
    plain.resolve_round(choice)
    frames = []
    recorded.resolve_round(choice, observe=lambda: frames.append(
        build_render_model(recorded.state, recorded.notebook.public_view(), timeline=False)))
    assert frames
    assert recorded.to_data() == plain.to_data()


def test_playback_does_not_compute_action_previews_or_clone_for_each_tick(monkeypatch):
    from pirates.duel_system.model import CombatState
    ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
    original = CombatState.clone
    calls = []
    def counted(state):
        calls.append(state.tick)
        return original(state)
    monkeypatch.setattr(CombatState, "clone", counted)
    ui.activate("Light")
    ui.activate("Torso")
    assert len(calls) < len(ui.playback)
    assert all(not frame.actions for frame in ui.playback)


def test_unchanged_paused_model_is_reused_but_stat_edits_invalidate_it():
    ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
    model = ui.model()
    assert ui.model() is model
    ui.session.player.blood -= 1
    assert ui.model() is not model


def test_live_round_dashboard_never_launches_the_unused_tactical_solver(monkeypatch):
    import pirates.duel_system.session as session_module
    def forbidden(*args, **kwargs):
        raise AssertionError("Round dashboard launched a blocking tactical rollout")
    monkeypatch.setattr(session_module, "guide", forbidden)
    ui = DuelUI(DuelSession.new(guide_mode="full"), rounds=True)
    ui.model()
    ui.activate("Light")
    ui.activate("Torso")
    assert ui.model().advice is None


def test_action_preview_never_copies_the_growing_combat_history(monkeypatch):
    from pirates.duel_system.engine import Choice
    from pirates.duel_system.model import ActionKind, CombatState, EventType, Zone
    from pirates.duel_system.render_model import preview_action
    session = DuelSession.new(guide_mode="off")
    for _ in range(2000):
        session.state.emit(EventType.POOL_CHANGE, session.player.id, deltas={"stamina": 0})
    before = session.to_data()
    def forbidden(*args, **kwargs):
        raise AssertionError("Preview deep-copied the complete combat history")
    monkeypatch.setattr(CombatState, "clone", forbidden)
    assert preview_action(session.state, Choice(ActionKind.LIGHT, Zone.TORSO)).valid
    assert before == session.to_data()
