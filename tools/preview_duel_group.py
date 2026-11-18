"""Native SDL readability/input audit for 2/5/10/20-actor duel scenarios."""
from pathlib import Path
import argparse
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "engine")]

from PIL import Image
import play
from duel_group_fixture import SUPPORTED_ACTOR_COUNTS, make_group_session
from pirates.duel_system.model import ActionKind, EventType
from pirates.duel_ui import DuelUI, GameDuelDisplay


SIZES = ((1280, 800), (960, 700), (640, 600))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    world = play.World()
    renderer = play.PiratesRenderer(settings=play.RenderSettings(
        world_font_size=20, world_cell_width=20, world_cell_height=24))
    measurements = []
    try:
        renderer.init_for(world.game_map.width, world.game_map.height)
        window = renderer.context.sdl_window
        window.min_size = (640, 600)
        for actors in SUPPORTED_ACTOR_COUNTS:
            for width, height in SIZES:
                window.size = (width, height)
                session = make_group_session(actors, seed=4300+actors, guide_mode="off")
                ui = DuelUI(session, rounds=True)
                display = GameDuelDisplay()
                display.rounds, display.message = ui.rounds, ui.message
                display.exchange_number = len(session.inputs)+1
                model = ui.model()
                before = session.state.to_data()
                started = time.perf_counter()
                display.draw(renderer, model, ui.sidebar_rows(model), present=False)
                first_render_ms = (time.perf_counter()-started)*1000
                steady_samples = []
                for _ in range(4):
                    warm_model = session.render_model(timeline=True, include_advice=False)
                    started = time.perf_counter()
                    display.draw(renderer, warm_model, ui.sidebar_rows(warm_model), present=False)
                    steady_samples.append((time.perf_counter()-started)*1000)
                render_ms = sorted(steady_samples)[len(steady_samples)//2]
                assert before == session.state.to_data(), "Native group rendering mutated combat"
                assert len(model.threats) <= 3
                assert model.threat_overflow == max(0, len(model.target_options)-len(model.threats))
                assert len(model.actors) <= actors
                assert all(actor.actor_id in {fighter.id for fighter in session.state.fighters}
                           for actor in model.actors)
                pixels = renderer.context.sdl_renderer.output_size
                for (x, y, w, h), label in display.buttons:
                    assert 0 <= x < x+w <= pixels[0] and 0 <= y < y+h <= pixels[1], (actors, width, label)
                    assert display.button_at((x+w/2, y+h/2)) == label
                image = Image.fromarray(renderer.context.sdl_renderer.read_pixels())
                image.save(args.output / f"group-{actors:02d}-{width}-ready.png")
                if actors == 2 and (width, height) == (1280, 800):
                    stability_button = next(label for _, label in display.buttons
                                            if label.startswith("Stat:Stability"))
                    assert stability_button == "Stat:Stability"
                    display.hover_action = stability_button
                    display.draw(renderer, model, ui.sidebar_rows(model), present=False)
                    Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(
                        args.output / "group-stability-help.png")
                    display.hover_action = None
                    display.draw(renderer, model, ui.sidebar_rows(model), present=False)
                measurements.append({"actors": actors, "size": [width, height],
                                     "visible_actors": len(model.actors),
                                     "visible_targets": len(model.target_options),
                                     "summarized_threats": len(model.threats),
                                     "overflow": model.threat_overflow,
                                     "first_render_ms": round(first_render_ms, 3),
                                     "render_ms": round(render_ms, 3),
                                     "controls": [label for _, label in display.buttons]})

        # Exercise the real multi-target -> explicit target -> body-zone ->
        # simultaneous exchange flow, then capture the resolved recap.
        window.size = (960, 700)
        session = make_group_session(5, seed=4310, guide_mode="off")
        ui = DuelUI(session, rounds=True)
        display = GameDuelDisplay()
        display.rounds, display.message = ui.rounds, ui.message

        def capture(name):
            display.exchange_number = len(session.inputs)+1
            model = ui.model()
            display.message = ui.message
            display.round_result, display.round_summary = ui.round_result, ui.round_summary
            display.draw(renderer, model, ui.sidebar_rows(model), present=False)
            Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(args.output / f"walkthrough-{name}.png")
            return model

        ui.activate("Light")
        assert ui.panel == "targets" and len(ui.model().target_options) > 1
        target_panel = capture("target-list")
        assert "Choose a visible hostile:" in "\n".join(ui.rows(target_panel))
        ui.activate("Target 1")
        assert ui.panel == "zones" and ui.target_id == target_panel.target_options[0].actor_id
        capture("locked-target-zones")
        cursor = len(session.state.event_log)
        ui.activate("Torso")
        assert len(session.inputs) == 1
        exchange_events = session.state.event_log[cursor:]
        starts = [event for event in exchange_events if event.type == EventType.ACTION_START]
        counts = {fighter.id: sum(event.actor == fighter.id for event in starts)
                  for fighter in session.state.fighters}
        assert counts[session.player.id] == 1 and all(count <= 1 for count in counts.values()), counts
        capture("resolved-exchange")

        # Make P5 tactics and unavailable-action explanations visible in the audit.
        from pirates.duel_layout import context_labels
        tactical_session = make_group_session(5, seed=4311, guide_mode="off")
        tactical_ui = DuelUI(tactical_session, rounds=True)
        tactical_display = GameDuelDisplay()
        tactical_display.rounds = True
        home_labels = context_labels(tactical_ui.sidebar_rows(tactical_ui.model()), tactical_ui.model())
        assert "Tactics" in home_labels, home_labels
        tactical_ui.activate("Tactics")
        tactical_model = tactical_ui.model()
        tactical_labels = context_labels(tactical_ui.sidebar_rows(tactical_model))
        expected_tactics = ["Feint", "Shove", "Disengage", "Thrust", "Disarm", "Adrenaline", "Back"]
        assert tactical_labels == expected_tactics, tactical_labels
        tactical_display.draw(renderer, tactical_model, tactical_ui.sidebar_rows(tactical_model), present=False)
        Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(
            args.output / "group-tactical-actions.png")
        locked_option = next(option for option in tactical_model.actions if not option.enabled)
        assert any(label == locked_option.label for _, label in tactical_display.buttons)
        tactical_display.hover_action = locked_option.label
        tactical_display.draw(renderer, tactical_model, tactical_ui.sidebar_rows(tactical_model), present=False)
        Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(
            args.output / "group-tactical-locked-reason.png")
        before_locked_click = tactical_session.state.to_data()
        tactical_ui.activate(locked_option.label)
        assert tactical_ui.message == locked_option.details
        assert tactical_session.state.to_data() == before_locked_click

        # Enter the same group journal through run_sword_duel's real SDL event
        # loop, then pause/resume-save it without advancing the ship world.
        import tcod.event
        from tcod.event import KeySym
        from pirates.duel_system.session import DuelSession
        from pirates.duel_ui import run_sword_duel
        window.size = (960, 700)
        world.duel_journal = make_group_session(5, seed=4320, guide_mode="off").to_data()
        world_before = (world.game_map, world.player.x, world.player.y, world.clock.total_ticks)
        original_wait, original_draw = tcod.event.wait, GameDuelDisplay.draw
        live = {"phase": 0, "display": None, "frames": 0}

        def tracked_draw(instance, *draw_args, **draw_kwargs):
            original_draw(instance, *draw_args, **draw_kwargs)
            live["display"] = instance
            live["frames"] += 1

        def scripted_wait(timeout=None):
            phase = live["phase"]
            display_now = live["display"]
            if phase == 0:
                event = tcod.event.KeyDown(scancode=tcod.event.Scancode.UNKNOWN,
                                           sym=KeySym.SPACE, mod=tcod.event.Modifier.NONE)
            elif phase in {1, 2}:
                label = "Target 1" if phase == 1 else "Torso"
                rect = next(rect for rect, candidate in display_now.buttons if candidate == label)
                x, y, width, height = rect
                event = tcod.event.MouseButtonDown(position=(x+width/2, y+height/2),
                                                   button=tcod.event.MouseButton.LEFT)
            elif phase == 3:
                if not display_now.animating:
                    return []
                event = tcod.event.KeyDown(scancode=tcod.event.Scancode.UNKNOWN,
                                           sym=KeySym.RETURN, mod=tcod.event.Modifier.NONE)
            else:
                event = tcod.event.KeyDown(scancode=tcod.event.Scancode.UNKNOWN,
                                           sym=KeySym.ESCAPE, mod=tcod.event.Modifier.NONE)
            live["phase"] += 1
            return [event]

        try:
            tcod.event.wait, GameDuelDisplay.draw = scripted_wait, tracked_draw
            assert run_sword_duel(renderer, world) == "paused"
        finally:
            tcod.event.wait, GameDuelDisplay.draw = original_wait, original_draw
        restored = DuelSession.from_data(world.duel_journal)
        assert len(restored.state.fighters) == 5 and len(restored.inputs) == 1
        native_starts = [event for event in restored.state.event_log if event.type == EventType.ACTION_START]
        assert sum(event.actor == restored.player.id for event in native_starts) == 1
        assert world_before == (world.game_map, world.player.x, world.player.y, world.clock.total_ticks)

        (args.output / "verification.json").write_text(
            json.dumps({"sizes": measurements,
                         "walkthrough": {"player_commits": len(session.inputs),
                                         "action_start_counts": counts,
                                         "target_id": ui.target_id,
                                         "events": len(exchange_events)},
                        "tactical_actions": {"navigation": "Primary Tactics button (also listed under More)",
                                              "controls": tactical_labels,
                                              "locked_action": locked_option.label,
                                              "locked_reason_visible": bool(locked_option.details)},
                        "live_native_pause_resume": {"actors": len(restored.state.fighters),
                                                     "player_commits": len(restored.inputs),
                                                     "native_frames": live["frames"],
                                                     "ship_world_unchanged": True}}, indent=2),
            encoding="utf-8")
        print(f"Group native audit passed: {len(measurements)} size/count captures plus target-lock walkthrough.")
    finally:
        renderer.shutdown()


if __name__ == "__main__":
    main()
