"""Save-free native frame/commit profiler for the live duel dashboard."""
from pathlib import Path
import argparse
import cProfile
import json
import statistics
import sys
import time
import gc

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "engine")]

import play
from pirates.duel_system.session import DuelSession
from pirates.duel_ui import DuelUI, GameDuelDisplay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=40)
    parser.add_argument("--no-profile", action="store_true", help="Measure native frame work without cProfile overhead")
    parser.add_argument('--coach', action='store_true', help='Include the observation-only strategy coach')
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    renderer = play.PiratesRenderer(settings=play.RenderSettings(
        world_font_size=20, world_cell_width=20, world_cell_height=24))
    profiler = cProfile.Profile() if not args.no_profile else None
    result = {'coach_enabled': args.coach}
    gc_pauses, gc_started = [], {}
    phase = 'startup'
    def record_gc(event, info):
        generation = info['generation']
        if event == 'start':
            gc_started[generation] = time.perf_counter()
        elif generation in gc_started:
            elapsed = (time.perf_counter()-gc_started.pop(generation))*1000
            if elapsed > 5:
                gc_pauses.append(dict(phase=phase, generation=generation, ms=elapsed))
    gc.callbacks.append(record_gc)
    try:
        world = play.World()
        renderer.init_for(world.game_map.width, world.game_map.height)
        renderer.context.sdl_window.size = (1280, 800)
        for mode in ("off", "full"):
            phase = mode+'_setup'
            ui = DuelUI(DuelSession.new(guide_mode=mode), rounds=True, coach_enabled=args.coach)
            display = GameDuelDisplay()
            display.rounds, display.message, display.round_result = True, ui.message, ()
            display.coach_enabled = ui.coach_enabled
            display.draw(renderer, ui.model(), ui.sidebar_rows(ui.model()))
            samples = {"model_ms": [], "rows_ms": [], "draw_ms": [], "frame_ms": []}
            if profiler:
                profiler.enable()
            for _ in range(args.frames):
                phase = mode+'_idle'
                start = time.perf_counter()
                model = ui.model()
                at = time.perf_counter()
                rows = ui.sidebar_rows(model)
                then = time.perf_counter()
                display.draw(renderer, model, rows, present=False)
                end = time.perf_counter()
                renderer.context.sdl_renderer.present()
                presented = time.perf_counter()
                for name, value in (("model_ms", at-start), ("rows_ms", then-at),
                                    ("draw_ms", end-then), ("frame_ms", presented-start)):
                    samples[name].append(value*1000)
            start = time.perf_counter()
            phase = mode+'_commit'
            ui.activate("Light")
            ui.activate("Torso")
            result[f"{mode}_commit_ms"] = (time.perf_counter()-start)*1000
            result[f"{mode}_playback_frames"] = len(ui.playback)
            from pirates.duel_scene import ExchangePlayback
            playback = ExchangePlayback(ui.playback)
            busy_samples = []
            model = ui.model()
            rows = ui.sidebar_rows(model)
            display.round_result = ui.round_result
            display.round_summary = ui.round_summary
            for index in range(args.frames):
                phase = mode+'_playback'
                playback.started = time.monotonic()-playback.duration*index/args.frames
                display.playback = playback
                start = time.perf_counter()
                display.draw(renderer, model, rows, present=False)
                busy_samples.append((time.perf_counter()-start)*1000)
                renderer.context.sdl_renderer.present()
            result[f"{mode}_playback_draw_ms"] = {"median": statistics.median(busy_samples),
                                                   "p95": sorted(busy_samples)[min(len(busy_samples)-1, int(len(busy_samples)*.95))]}
            result[f"{mode}_terrain_builds"] = display.terrain_builds
            result[f"{mode}_dashboard_builds"] = display.dashboard_builds
            if profiler:
                profiler.disable()
            result[mode] = {key: {"median": statistics.median(values),
                                 "p95": sorted(values)[min(len(values)-1, int(len(values)*.95))]}
                            for key, values in samples.items()}
        from pirates.duel_system.model import EventType
        ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
        for _ in range(2000):
            ui.session.state.emit(EventType.POOL_CHANGE, ui.session.player.id, deltas={"stamina": 0})
        start = time.perf_counter()
        ui.model()
        result["history_2000_first_model_ms"] = (time.perf_counter()-start)*1000
        start = time.perf_counter()
        ui.activate("Light")
        ui.activate("Torso")
        result["history_2000_commit_ms"] = (time.perf_counter()-start)*1000
        if profiler:
            profiler.dump_stats(str(args.output.with_suffix(".prof")))
        result['gc_pauses'] = gc_pauses
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
    finally:
        gc.callbacks.remove(record_gc)
        renderer.shutdown()


if __name__ == "__main__":
    main()
