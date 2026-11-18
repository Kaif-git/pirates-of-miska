"""Save-free native SDL validation of the rebuilt intent/tick combat arena."""
from pathlib import Path
from dataclasses import asdict, replace
import argparse
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "engine")]
import play
from PIL import Image
from pirates.combat_duel import ActionKind, DuelAction, commit_action


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    world = play.World()
    original_map = world.game_map
    original_pos = world.player.x, world.player.y, world.player.z
    starting_ticks = world.clock.total_ticks
    renderer = play.PiratesRenderer(settings=play.RenderSettings(
        world_font_size=20, world_cell_width=20, world_cell_height=24))
    frames = []
    try:
        renderer.init_for(world.game_map.width, world.game_map.height)
        world.combat.start(world, seed=41)
        play.COMBAT_INPUT_MODE = True
        arena = world.combat

        def capture(name):
            before_render = arena.engine.snapshot()
            renderer.render(world)
            assert arena.engine.snapshot() == before_render, "Rendering mutated combat simulation state"
            renderer._present_layers()
            Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(args.output / f"{name}.png")
            frames.append({"name": name, "tick": arena.engine.tick, "state": arena.engine.snapshot()})

        capture("01-ready")
        mode_label, expected_buttons = play.combat_action_bar(arena)
        assert mode_label.startswith("GROUP / MELEE")
        assert len(renderer._combat_button_rects) == len(expected_buttons)
        for rect, expected in zip(renderer._combat_button_rects,
                                  expected_buttons):
            (left, top, width, height), action = rect
            assert action == expected[2]
            assert renderer._combat_action_at((left + width // 2,
                                               top + height // 2)) == action
        player, enemy = arena.fighters["captain"], arena.target()
        # Show actionable distance and a real spear commitment at its two-tile
        # reach before returning to the adjacent cutlass example.
        enemy.weapon = "spear"
        enemy.x, enemy.y = player.x + 3, player.y
        enemy.facing = (-1, 0)
        enemy.next_ready_tick = arena.engine.tick
        arena.selected_target = enemy.id
        assert arena.guided_duel_available
        assert play.combat_action_bar(arena)[0].startswith("GUIDED DUEL")
        capture("01b-range-three-tiles")
        enemy.x, enemy.y = player.x + 2, player.y
        state = arena._guided_duel_state(enemy)
        assert state is not None and state.right.weapon.reach_tiles == 2
        state = commit_action(replace(state, to_act=1), DuelAction(ActionKind.LIGHT))
        arena._duel_state = state
        arena.engine.guided_duel_state = asdict(state)
        assert any(label.startswith("Preparing Light ·")
                   for aid, _, label, _ in arena.floating_labels() if aid == enemy.id)
        capture("01c-long-reach-intent")
        arena._duel_state = None
        arena.engine.guided_duel_state = None
        arena._duel_actor_ids = ()
        enemy.weapon = "sword"
        adjacent = next((pos for pos in ((player.x + 1, player.y),
                                          (player.x - 1, player.y),
                                          (player.x, player.y + 1),
                                          (player.x, player.y - 1))
                         if pos in arena.engine.walkable and not arena.engine.at(pos)), None)
        assert adjacent, "arena needs one free adjacent contact tile"
        enemy.x, enemy.y = adjacent
        enemy.facing = (-1 if adjacent[0] > player.x else 1, 0) if adjacent[1] == player.y else (0, -1 if adjacent[1] > player.y else 1)
        enemy.next_ready_tick = arena.engine.tick
        arena.selected_target = enemy.id
        assert arena.guided_duel_available
        assert play.combat_action_bar(arena)[0].startswith("GUIDED DUEL")
        assert set(action for _, action in renderer._combat_button_rects) == {
            "combat_duel_light", "combat_duel_heavy", "combat_duel_parry",
            "combat_dash_arm", "combat_duel_dash_strike",
        }
        capture("02-guided-contact")
        state = arena._guided_duel_state(enemy)
        state = commit_action(replace(state, to_act=1), DuelAction(ActionKind.LIGHT))
        arena._duel_state = state
        arena.engine.guided_duel_state = asdict(state)
        labels = arena.floating_labels()
        assert any(label.startswith("Preparing Light ·")
                   for aid, _, label, _ in labels if aid == enemy.id)
        assert any("Parry [G] fits window" in label and "force uncertain" in label
                   for aid, _, label, _ in labels if aid == player.id)
        capture("03-committed-threat-guide")
        assert not hasattr(arena.engine, "rng")
        tick_before = arena.engine.tick
        assert arena.act(world, "duel_parry")
        assert not hasattr(arena.engine, "rng")
        assert arena.engine.tick > tick_before
        assert arena.engine.replay[-1]["ruleset"] == "guided-duel-v1"
        assert any(event["outcome"] == "parried" for event in arena.engine.feedback)
        capture("04-parry-resolution")
        # Ask the actual observation-limited profile to choose its authored
        # opener, then render that frozen intent through the native label path.
        arena.reset(world)
        player, enemy = arena.fighters["captain"], arena.target()
        adjacent = next((pos for pos in ((player.x + 1, player.y),
                                          (player.x - 1, player.y),
                                          (player.x, player.y + 1),
                                          (player.x, player.y - 1))
                         if pos in arena.engine.walkable and not arena.engine.at(pos)), None)
        assert adjacent
        enemy.x, enemy.y = adjacent
        enemy.facing = (-1, 0) if adjacent[0] > player.x else (1, 0) if adjacent[0] < player.x else (0, -1) if adjacent[1] > player.y else (0, 1)
        enemy.tactics = "aggressive"
        enemy.next_ready_tick = arena.engine.tick
        arena.selected_target = enemy.id
        state = arena._guided_duel_state(enemy)
        npc_action = arena._guided_npc_action(replace(state, to_act=1))
        assert npc_action.kind == ActionKind.HEAVY
        state = commit_action(replace(state, to_act=1), npc_action)
        arena._duel_state = state
        arena.engine.guided_duel_state = asdict(state)
        capture("04b-authored-first-mover")
        arena.reset(world)
        player, enemy = arena.fighters["captain"], arena.target()
        approach = next(((dx, dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                         if all((player.x + dx * step, player.y + dy * step)
                                in arena.engine.walkable
                                for step in (1, 2, 3))), None)
        assert approach
        dx, dy = approach
        enemy.x, enemy.y = player.x + dx * 3, player.y + dy * 3
        enemy.balance = 0
        enemy.body_parts["left_arm"]["structure"] = 0
        enemy.body_parts["right_arm"]["structure"] = 0
        enemy.next_ready_tick = arena.engine.tick
        arena.selected_target = enemy.id
        assert arena.act(world, "duel_dash_strike")
        miss = next(item for item in arena.engine.feedback
                    if item["outcome"] == "committed-lane-miss")
        assert "exposed recovery" in miss["text"]
        capture("04c-dash-strike-miss-risk")
        player = arena.fighters["captain"]
        player.unconscious = True
        capture("04c-unconscious-result")
        assert "UNCONSCIOUS" in arena.player_status()[0]
        player.hp = 0
        capture("04c-dead-result")
        assert "DEAD" in arena.player_status()[0]
        # Non-duel encounters get a distinct, working legacy action strip rather
        # than five disabled duel commands. Ranged controls explain aim/fire/reload.
        arena.reset(world)
        player, enemy = arena.fighters["captain"], arena.target()
        spare_tile = next(pos for pos in sorted(arena.engine.walkable)
                          if pos not in {actor.pos for actor in arena.engine.actors.values()}
                          and max(abs(pos[0] - player.x), abs(pos[1] - player.y)) >= 5)
        assert arena.spawn("duelist", pos=spare_tile)
        group_title, group_buttons = play.combat_action_bar(arena)
        assert group_title.startswith("GROUP / MELEE")
        assert tuple(item[0] for item in group_buttons) == (
            "Attack", "Heavy", "Guard", "Dash", "More")
        capture("04d-group-legacy-actions")
        player.weapon = "gun"
        player.ammo = 1
        player.spare_shots = 3
        ranged_title, ranged_buttons = play.combat_action_bar(arena)
        assert ranged_title.startswith("RANGED")
        assert tuple(item[0] for item in ranged_buttons) == (
            "Aim", "Fire", "Reload", "Guard", "More")
        capture("04e-ranged-aim-fire-reload")
        player.weapon = "sword"
        arena.monochrome = True
        capture("05-monochrome-duel")
        arena.monochrome = False
        capture("06-duel-feedback")
        # Exercise simultaneous outcomes at one position and prove their idle
        # presentation animates using wall-clock time without advancing combat.
        arena.engine.show_feedback(enemy, "BLOCK", "block")
        arena.engine.show_feedback(enemy, "HIT · TORSO", "wound")
        arena.engine.show_feedback(arena.fighters["captain"], "PARRY", "parry")
        before_feedback = arena.engine.snapshot()
        capture("07-feedback-stack")
        # Drive the renderer's real idle-input loop: one quiet wait forces the
        # active animation redraw, then a queued Quit safely ends the preview.
        import tcod.event
        original_event_wait = tcod.event.wait
        waited = 0

        def idle_wait(timeout):
            nonlocal waited
            waited += 1
            if waited == 1:
                time.sleep(0.45)
                return []
            return [tcod.event.Quit(timestamp_ns=0)]

        tcod.event.wait = idle_wait
        try:
            assert renderer.wait_action() == ("quit",)
        finally:
            tcod.event.wait = original_event_wait
        assert waited >= 2
        Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(
            args.output / "08-feedback-mid-animation.png")
        frames.append({"name": "08-feedback-mid-animation", "tick": arena.engine.tick,
                       "state": arena.engine.snapshot()})
        assert arena.engine.snapshot() == before_feedback

        class ScriptedSetupRenderer:
            def __init__(self):
                self.choices = iter([10, 1, 0, 9, 8, 6, 1, 7, None])
                self.captured = set()
                self.real_renderer = renderer

            def __getattr__(self, name):
                return getattr(self.real_renderer, name)

            def render_list(self, *args, **kwargs):
                renderer.render_list(*args, **kwargs)
                title = args[1] if len(args) > 1 else ""
                capture_name = {
                    "Movement & fitness": "08-profile-category",
                }.get(title)
                if title.startswith("Speed · resolved"):
                    capture_name = "09-profile-field"
                elif title.startswith("Set exact Speed"):
                    capture_name = "10-profile-numeric-entry"
                elif title.startswith("Review enemy profile"):
                    capture_name = "11-profile-review"
                if capture_name and capture_name not in self.captured:
                    renderer._present_layers()
                    Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(
                        self.output / f"{capture_name}.png")
                    frames.append({"name": capture_name, "tick": arena.engine.tick,
                                   "state": arena.engine.snapshot()})
                    self.captured.add(capture_name)

            def choose(self, _count):
                return next(self.choices)

            def wait_any(self):
                pass

        before_setup = arena.engine.snapshot()
        before_profile = dict(arena.npc_overrides)
        setup_renderer = ScriptedSetupRenderer()
        # `args` is local to main, so expose the destination for the wrapper.
        setup_renderer.output = args.output
        tcod.event.wait = lambda _timeout=None: [
            tcod.event.TextInput(timestamp_ns=0, text="137"),
            tcod.event.KeyDown(timestamp_ns=0, scancode=tcod.event.Scancode.RETURN,
                                sym=tcod.event.KeySym.RETURN, mod=tcod.event.Modifier.NONE),
        ]
        try:
            play.combat_setup(setup_renderer, world)
        finally:
            tcod.event.wait = original_event_wait
        assert arena.engine.snapshot() == before_setup
        assert arena.npc_overrides == before_profile
        assert setup_renderer.captured == {
            "08-profile-category", "09-profile-field", "10-profile-numeric-entry",
            "11-profile-review"}

        # Exercise the supported display modes at native SDL sizes, not only
        # by changing a projection helper in tests.
        window = renderer.context.sdl_window
        original_size = window.size
        window.size = (640, 400)
        capture("12-small-window")
        window.size = original_size
        capture("13-restored-window")
        original_settings = renderer.settings
        renderer.settings = play.RenderSettings(world_font_size=24, world_cell_width=24,
                                                 world_cell_height=28, ui_font_size=18)
        renderer.zoom(1, world.game_map.width, world.game_map.height)
        capture("14-enlarged-font-zoom-in")
        renderer.zoom(-1, world.game_map.width, world.game_map.height)
        renderer.settings = original_settings
        renderer.zoom(1, world.game_map.width, world.game_map.height)
        renderer.zoom(-1, world.game_map.width, world.game_map.height)
        capture("15-default-font-zoom-restored")
        play.COMBAT_UI_PREFERENCES["combat_reduced_motion"] = True
        arena.engine.show_feedback(enemy, "PARRY", "parry")
        assert play.combat_feedback_offset(0.9, reduced_motion=True) == 0
        capture("16-reduced-motion")
        play.COMBAT_UI_PREFERENCES["combat_reduced_motion"] = False

        class HistoryPreviewRenderer:
            def __init__(self):
                self.real = renderer
                self.captured = set()

            def __getattr__(self, name):
                return getattr(self.real, name)

            def render_list(self, *args, **kwargs):
                self.real.render_list(*args, **kwargs)
                title = args[1] if len(args) > 1 else ""
                if title.startswith("Combat history"):
                    self.real._present_layers()
                    Image.fromarray(self.real.context.sdl_renderer.read_pixels()).save(
                        self.output / "17-combat-history.png")
                    self.captured.add("history")

            def choose(self, _count):
                return None

        history_renderer = HistoryPreviewRenderer()
        history_renderer.output = args.output
        play.combat_history_menu(history_renderer, world)
        assert "history" in history_renderer.captured
        arena.dump(args.output / "replay.json")
        assert world.clock.total_ticks == starting_ticks
        arena.exit(world)
        assert world.game_map is original_map
        assert (world.player.x, world.player.y, world.player.z) == original_pos
        (args.output / "verification.json").write_text(json.dumps({
            "passed": True, "frames": frames, "clock_frozen": True,
            "original_map_restored": True, "save_slot_touched": False}, indent=2), encoding="utf-8")
        print(f"Native tick/intent arena passed: {len(frames)} screenshots; clean map restore; no save touched.")
    finally:
        play.COMBAT_INPUT_MODE = False
        play.COMBAT_DASH_ARMED = False
        renderer.shutdown()


if __name__ == "__main__":
    main()
