"""Save-free native SDL walkthrough of COMBAT SPEC's 40-column duel UI."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "engine")]

from PIL import Image
from tcod.event import KeySym
import play
from pirates.duel_system.engine import Choice, check_end_conditions, commit_action
from pirates.duel_system.model import ActionKind, Zone
from pirates.duel_system.session import DuelSession
from pirates.duel_ui import DuelDisplay, DuelUI, GameDuelDisplay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--game-display", action="store_true", help="Verify the live WorldRenderer/sidebar presentation")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    world = play.World()
    original = (world.game_map, world.player.x, world.player.y, world.clock.total_ticks)
    renderer = play.PiratesRenderer(settings=play.RenderSettings(
        world_font_size=20, world_cell_width=20, world_cell_height=24))
    frames = []
    display = GameDuelDisplay() if args.game_display else DuelDisplay()
    ui = DuelUI(DuelSession.new(guide_mode="off"))
    try:
        renderer.init_for(world.game_map.width, world.game_map.height)

        def capture(name):
            before = ui.session.state.to_data()
            model = ui.model()
            rows = ui.sidebar_rows(model) if args.game_display else ui.rows(model)
            if not args.game_display:
                assert all(len(row) <= 40 for row in rows)
            display.draw(renderer, model, rows, present=False)
            assert ui.session.state.to_data() == before, "Rendering mutated combat"
            for (left, top, width, height), label in display.buttons:
                assert display.button_at((left+width/2, top+height/2)) == label
            Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(args.output / f"{name}.png")
            renderer.context.sdl_renderer.present()
            frames.append({"name": name, "tick": model.tick, "state": model.player_state,
                           "columns": max(map(len, rows)), "rows": len(rows)})

        capture("01-start")
        if args.game_display:
            labels = {label for _, label in display.buttons}
            assert {"Defend", "Thrust", "Feint", "Shove", "Disarm", "Disengage"} <= labels
        ui.handle_key(KeySym.SPACE)
        capture("02-zone-menu")
        ui.handle_key(KeySym.T)
        capture("03-preview-confirm")
        assert ui.session.state.tick == 0
        ui.handle_key(KeySym.RETURN)
        capture("04-committed")
        ui.session.run_to_player()
        capture("05-next-decision")
        ui.handle_key(KeySym.DOWN)
        capture("06-walk-preview")
        ui.handle_key(KeySym.ESCAPE)
        ui.handle_key(KeySym.K)
        capture("07-notebook")
        ui.handle_key(KeySym.ESCAPE)
        ui.handle_key(KeySym.L)
        capture("08-full-log")
        ui.handle_key(KeySym.ESCAPE)
        ui.handle_key(KeySym.H)
        capture("09-help")

        ui = DuelUI(DuelSession.new(guide_mode="off"))
        commit_action(ui.session.state, "ronin", Choice(ActionKind.HEAVY, Zone.TORSO))
        capture("10-heavy-tell-danger")
        ui.handle_key(KeySym.G)
        capture("11-defend-preview")
        assert ui.session.selected is not None
        assert ui.message.startswith("Defend")
        ui.handle_key(KeySym.RETURN)
        ui.session.run_to_player()
        capture("12-defend-response")
        assert ui.session.inputs and ui.session.inputs[-1]["choice"]["kind"] in {
            "WALK", "RUN", "DASH", "GUARD"}

        for condition in ("unconscious", "dead", "victory"):
            ui = DuelUI(DuelSession.new(guide_mode="off"))
            if condition == "unconscious":
                ui.session.player.unconscious = True
            elif condition == "dead":
                ui.session.player.blood = 0
            else:
                ui.session.enemy.yielded = True
            check_end_conditions(ui.session.state)
            capture(f"13-{condition}")

        ui = DuelUI(DuelSession.new(guide_mode="off"))
        window = renderer.context.sdl_window
        original_size = window.size
        original_min = window.min_size
        window.min_size = (320, 400)
        window.size = (960, 700) if args.game_display else (360, 800)
        assert renderer.context.sdl_renderer.output_size[0] == (960 if args.game_display else 360)
        capture("14-portrait-40-columns")
        ui.handle_key(KeySym.SPACE)
        capture("15-portrait-zones")
        window.size = original_size
        window.min_size = original_min

        ui = DuelUI(DuelSession.new(guide_mode="hints"))
        capture("16-guide-unknown-hints")
        ui.session.guide_mode = "full"
        capture("17-guide-full")

        ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
        ui.activate("Light")
        ui.activate("Torso")
        display.round_summary = ui.round_summary
        recap_text = " ".join(ui.round_summary).lower()
        assert "you:" in recap_text and "ronin:" in recap_text
        assert "effects:" in recap_text
        assert not any(token in recap_text for token in ("net damage", "damage trade", "power", "armor"))
        capture("18-consequence-recap")
        display.round_summary = ()
        # Exercise the actual modal loop and tcod KeyDown dispatch, not just helpers.
        import tcod.event
        from pirates.duel_ui import run_sword_duel
        from collections import deque
        world.duel_journal = DuelSession.new(guide_mode="off").to_data()
        keys = deque((KeySym.SPACE, KeySym.T, KeySym.ESCAPE))
        native_wait = tcod.event.wait
        def scripted_events(_timeout=None):
            sym = keys.popleft() if keys else KeySym.ESCAPE
            return [tcod.event.KeyDown(scancode=tcod.event.Scancode.UNKNOWN, sym=sym,
                                       mod=tcod.event.Modifier.NONE, repeat=False)]
        try:
            tcod.event.wait = scripted_events
            assert run_sword_duel(renderer, world) == "paused"
        finally:
            tcod.event.wait = native_wait
        resumed = DuelSession.from_data(world.duel_journal)
        assert resumed.ready and resumed.state.tick > 0
        assert len(resumed.inputs) == 1
        assert (world.game_map, world.player.x, world.player.y, world.clock.total_ticks) == original
        (args.output / "verification.json").write_text(json.dumps(frames, indent=2), encoding="utf-8")
        print(f"Native UI passed: {len(frames)} captures; {'live WorldRenderer/sidebar' if args.game_display else '40-column legacy presentation'}; free previews, clean ship/time; no save touched.")
    finally:
        renderer.shutdown()


if __name__ == "__main__":
    main()
