"""Save-free, real SDL round-mode duel UX audit at desktop and compact sizes."""
from pathlib import Path
import argparse
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "engine")]

from PIL import Image
from tcod.event import KeySym
import play
from pirates.duel_system.engine import check_end_conditions
from pirates.duel_system.session import DuelSession
from pirates.duel_ui import DuelUI, GameDuelDisplay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    world = play.World()
    renderer = play.PiratesRenderer(settings=play.RenderSettings(
        world_font_size=20, world_cell_width=20, world_cell_height=24))
    display = GameDuelDisplay()
    ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
    frames = []
    try:
        renderer.init_for(world.game_map.width, world.game_map.height)
        window = renderer.context.sdl_window
        window.min_size = (640, 600)

        def capture(name):
            model = ui.model()
            before = ui.session.state.to_data()
            display.rounds, display.message, display.round_result = ui.rounds, ui.message, ui.round_result
            display.round_summary, display.round_assessment, display.stat_enemy = (
                ui.round_summary, ui.round_assessment, ui.stat_enemy)
            display.coach_enabled = ui.coach_enabled
            display.selected_distance = f"{ui.tiles} tile" + ("s" if ui.tiles != 1 else "") if ui.panel == "direction" else ""
            display.draw(renderer, model, ui.sidebar_rows(model), present=False)
            assert before == ui.session.state.to_data(), "Drawing mutated combat"
            width, height = renderer.context.sdl_renderer.output_size
            for (x, y, w, h), label in display.buttons:
                assert 0 <= x < x+w <= width and 0 <= y < y+h <= height, label
                assert display.button_at((x+w/2, y+h/2)) == label
            mx, my, tw, th = display.map_rect
            if not display.reading_modal:
                for pos in (model.player_pos, model.enemy_pos):
                    assert display.tile_at((mx+(pos[0]+.5)*tw, my+(pos[1]+.5)*th)) == pos
            Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(args.output / f"{name}.png")
            renderer.context.sdl_renderer.present()
            frames.append(dict(name=name, size=[width, height], tick=model.tick,
                               panel=ui.panel, scroll=display.scroll, max_scroll=display.max_scroll,
                               controls=[label for _, label in display.buttons]))

        def click(label):
            rect = next(rect for rect, candidate in display.buttons if candidate == label)
            x, y, w, h = rect
            ui.activate(display.button_at((x+w/2, y+h/2)))
            display.hover_action = None
            display.scroll = 0

        for size in ((1280, 800), (960, 700), (640, 600)):
            window.size = size
            prefix = str(size[0])
            ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
            capture(prefix+"-01-ready")
            anchors = {key: display.layout[key] for key in ("arena", "info", "controls")}
            builds = (display.terrain_builds, display.dashboard_builds)
            capture(prefix+"-01-cached")
            assert builds == (display.terrain_builds, display.dashboard_builds), "Idle frame rebuilt terrain/dashboard"
            click('Coach')
            capture(prefix+'-01-coach-on')
            assert ui.coach_enabled
            click('Strategy')
            capture(prefix+'-01-strategy')
            click('Back')
            capture(prefix+'-01-strategy-closed')
            assert anchors == {key: display.layout[key] for key in anchors}
            click("Light")
            capture(prefix+"-02-target")
            click("Torso")
            assert len(ui.session.inputs) == 1
            assert len([e for e in ui.session.state.event_log if e.type.value == "ACTION_START"]) == 2
            capture(prefix+"-03-outcome")
            assert anchors == {key: display.layout[key] for key in anchors}, "Result moved the interface"
            assert ui.round_result[0].startswith("You chose:")
            assert ui.round_result[1].startswith("Ronin chose:")
            assert "Why:" in " ".join(ui.round_result)
            display.scroll = 5
            capture(prefix+"-03-explained-scroll")
            display.scroll = 0
            click("Details")
            capture(prefix+"-03-details")
            assert display.reading_modal
            assert anchors == {key: display.layout[key] for key in anchors}
            click("Back")
            capture(prefix+"-03-details-closed")
            click("Stat:Stamina")
            capture(prefix+"-03-stamina-help")
            click("Stat:Speed")
            capture(prefix+"-03-speed-help")
            click("Back")
            capture(prefix+"-03-help-closed")
            from pirates.duel_scene import ExchangePlayback
            playback = ExchangePlayback(ui.playback)
            for stage, elapsed in (("tell", .1), ("motion", playback.tell+playback.step*1.5),
                                   ("impact", playback.duration-.12)):
                playback.started = time.monotonic()-elapsed
                display.playback = playback
                capture(prefix+"-03-"+stage)
                assert display.animating
                assert anchors == {key: display.layout[key] for key in anchors}, "Playback moved the interface"
                assert [label for _, label in display.buttons] == ["Skip"]
            display.playback = None
            ui.playback = ()
            capture(prefix+"-03-finished")
            click("Move")
            capture(prefix+"-04-direction")
            click("1 tile")
            click("S")
            capture(prefix+"-05-moved")
            movement = ExchangePlayback(ui.playback)
            moving_index = next(i for i in range(len(ui.playback)-1)
                                if ui.playback[i].player_pos != ui.playback[i+1].player_pos)
            movement.started = time.monotonic()-movement.offset(moving_index, .5)
            display.playback = movement
            capture(prefix+"-05-camera-tween")
            if size == (640, 600):
                # At compact size the map can clamp both endpoints to the same
                # integral camera origin; movement playback still renders and is
                # checked above without requiring a camera pan that cannot occur.
                assert display.playback is movement and display.animating
            display.playback = None
            ui.playback = ()
            capture(prefix+"-05-ready-again")
            from pirates.duel_system.engine import Choice
            from pirates.duel_system.model import ActionKind
            ui.session.enemy.pos = (ui.session.player.pos[0]+2, ui.session.player.pos[1])
            ui.session._npc_choice = lambda: Choice(ActionKind.WAIT)
            # Isolate this P0 path regression from earlier P3 shock reactions.
            # The movement assertions start from the documented open test lane.
            state = ui.session.state
            state.event_log.clear()
            state.pending_interrupts.clear()
            state.pending_injury_responses.clear()
            state.end_reason = state.winner = None
            for fighter in state.fighters:
                fighter.action = None
                fighter.pos = (4, 4) if fighter.id == ui.session.player.id else (6, 4)
                fighter.blood = state.config["blood"]["start"]
                fighter.stamina = fighter.stamina_stat * state.config["stamina"]["pool_per_stat"]
                fighter.fatigue = fighter.pain = fighter.anger = fighter.nervousness = 0
                fighter.morale = state.config["morale"]["start"]
                fighter.wounds.clear()
                fighter.statuses.clear()
                fighter.unconscious = fighter.yielded = fighter.fled = False
                fighter.disarmed = fighter.hand_severed = False
                fighter.stagger_until = fighter.idle_since = state.tick
                fighter.last_recoil_tick = None
                fighter.first_exchange_tick = fighter.last_hit_tick = None
                fighter.hits_without_return = fighter.fear_decisions = 0
            ui.session.selected = None
            ui.session._exchange_cursor = ui.session._ai_outcome_cursor = 0
            ui.session._guide_cache = None
            click("Dash")
            capture(prefix+"-05-dash-direction")
            click("E")
            assert ui.session.player.pos == (4, 4) and len(ui.session.inputs) == 2
            assert "Ronin occupies the path" in ui.message
            capture(prefix+"-05-dash-blocked-explained")
            click("1 tile")
            click("E")
            assert ui.session.player.pos == (5, 4) and len(ui.session.inputs) == 3
            assert "moved 1 tile" in " ".join(ui.round_summary)
            capture(prefix+"-05-dash-short-route")
            click("More")
            capture(prefix+"-05-more-run")
            click("Run")
            capture(prefix+"-05-run-direction")
            click("E")
            assert ui.session.player.pos == (5, 4) and len(ui.session.inputs) == 3
            assert "Ronin occupies the path" in ui.message
            capture(prefix+"-05-run-blocked-explained")
            click("1 tile")
            click("S")
            assert ui.session.player.pos == (5, 5) and len(ui.session.inputs) == 4
            assert "moved 1 tile" in " ".join(ui.round_summary)
            capture(prefix+"-05-run-short-route")
            click("More")
            capture(prefix+"-06-more")
            click("Log")
            capture(prefix+"-06-detailed-log")
            display.scroll = 4
            capture(prefix+"-06-detailed-log-scrolled")
            display.scroll = 0
            click("Back")
            ui.activate("Help")
            capture(prefix+"-07-help")
            ui.activate("Back")
            ui.activate("Sheet")
            capture(prefix+"-08-sheet")
            ui.activate("Back")
            display.hover_action = "Heavy"
            capture(prefix+"-09-hover")
            display.hover_action = None
            ui.session.player.unconscious = True
            check_end_conditions(ui.session.state)
            capture(prefix+"-10-defeat")
            assert not {"Light", "Heavy", "Guard"} & {label for _, label in display.buttons}
            from pirates.duel_system.model import EventType
            ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True, coach_enabled=True)
            ui.session.player.stability = 1
            ui.session._npc_choice = lambda: Choice(ActionKind.WAIT)
            capture(prefix+"-p4-ready")
            click("Heavy")
            capture(prefix+"-p4-heavy-target")
            click("Torso")
            assert ui.session.player.grounded
            assert any(event.type == EventType.FALL for event in ui.session.state.event_log)
            capture(prefix+"-p4-grounded-recovery")
            assert {"Rise", "Crawl", "Cover"} <= {label for _, label in display.buttons}
            assert "fell from" in " ".join(ui.model().full_log)
            click("Rise")
            assert not ui.session.player.grounded
            assert any(event.type == EventType.RISE for event in ui.session.state.event_log)
            capture(prefix+"-p4-rise-completed")

            # P5: native tactical menu, threshold disclosure, preview and the
            # complete adrenaline activate→expire lifecycle.
            ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=False)
            ui.session.player.pain = ui.session.state.config["adrenaline"]["trigger_pain_min"]
            capture(prefix+"-p5-ready")
            click("More")
            capture(prefix+"-p5-more")
            click("Tactics")
            capture(prefix+"-p5-tactics")
            assert "READY" in ui.model().adrenaline_status
            assert "Adrenaline" in [label for _, label in display.buttons]
            if display.max_scroll:
                display.scroll = 1
                capture(prefix+"-p5-tactics-status")
                display.scroll = 0
            click("Adrenaline")
            capture(prefix+"-p5-adrenaline-preview")
            assert ui.panel == "preview" and ui.model().preview.valid
            assert "Once per duel" in " ".join(ui.sidebar_rows(ui.model()))
            assert display.max_scroll > 0
            display.scroll = min(4, display.max_scroll)
            capture(prefix+"-p5-adrenaline-preview-details")
            display.scroll = 0
            ui.session._npc_choice = lambda: Choice(ActionKind.WAIT)
            click("Confirm")
            from pirates.duel_system.engine import advance_one_tick
            assert ui.session.player.action is not None, (ui.panel, ui.message, ui.session.selected)
            activation_tick = ui.session.player.action.end_tick
            while ui.session.state.tick < activation_tick:
                advance_one_tick(ui.session.state)
            assert ui.session.player.adrenaline_until_tick is not None
            capture(prefix+"-p5-adrenaline-active")
            assert "ACTIVE until" in ui.model().adrenaline_status
            click("More")
            capture(prefix+"-p5-active-more")
            click("Tactics")
            assert "ACTIVE until" in ui.model().adrenaline_status
            capture(prefix+"-p5-adrenaline-active-status")
            assert display.max_scroll > 0
            display.scroll = display.max_scroll
            capture(prefix+"-p5-adrenaline-active-status-details")
            display.scroll = 0
            while ui.session.state.tick < ui.session.player.adrenaline_until_tick:
                advance_one_tick(ui.session.state)
            assert not ui.session.player.adrenaline_debt_pending
            assert any(event.type == EventType.ADRENALINE
                       and event.deltas.get("outcome") == "EXPIRED"
                       for event in ui.session.state.event_log)
            capture(prefix+"-p5-adrenaline-expired")
            assert "USED" in ui.model().adrenaline_status
            capture(prefix+"-p5-adrenaline-expired-status")
            if display.max_scroll:
                display.scroll = display.max_scroll
                capture(prefix+"-p5-adrenaline-expired-status-details")
                display.scroll = 0

            # P6: explicit ground-item selection/replacement, persistent eye
            # injury feedback, and a thresholded terminal head outcome.
            from pirates.duel_system.engine import Choice
            from pirates.duel_system.model import EventType, GroundWeapon, Weapon, Zone
            ui = DuelUI(DuelSession.new(seed=600 + size[0], guide_mode="off"), rounds=True)
            ui.session._npc_choice = lambda: Choice(ActionKind.WAIT)
            held = ui.session.player.equipment.weapon
            replacement = Weapon(item_id=f"p6-replacement-{size[0]}", name="Worn iron cutlass",
                                material="IRON", condition=43)
            broken = Weapon(item_id=f"p6-broken-{size[0]}", name="Broken blade",
                            material="WOOD", condition=0)
            ui.session.state.ground_weapons.extend((GroundWeapon(replacement, ui.session.player.pos),
                                                    GroundWeapon(broken, ui.session.player.pos)))
            capture(prefix+"-p6-ground-items")
            click("More")
            capture(prefix+"-p6-more")
            click("Pickup")
            capture(prefix+"-p6-pickup-list")
            assert [label for _, label in display.buttons].count("Pick 1") == 1
            click("Pick 1")
            assert ui.session.player.equipment.weapon.item_id == replacement.item_id
            assert any(row.weapon.item_id == held.item_id for row in ui.session.state.ground_weapons)
            capture(prefix+"-p6-pickup-replacement")

            ui = DuelUI(DuelSession.new(seed=700 + size[0], guide_mode="off"), rounds=True)
            ui.session._npc_choice = lambda: Choice(ActionKind.WAIT)
            ui.session.player.strength, ui.session.player.accuracy = 50, 100
            ui.session.player.equipment.weapon.damage_type = "PIERCING"
            ui.session.state.config["accuracy"]["jitter"] = 0
            ui.session.enemy.pos = (ui.session.player.pos[0]+1, ui.session.player.pos[1])
            ui.activate("Heavy")
            capture(prefix+"-p6-eye-target")
            assert "Eye" in [label for _, label in display.buttons]
            click("Eye")
            assert ui.session.enemy.permanent_injuries.eyes_damaged == 1
            assert any(event.type == EventType.EYE_INJURY for event in ui.session.state.event_log)
            capture(prefix+"-p6-eye-injury")
            click("More")
            capture(prefix+"-p6-eye-more")
            click("Log")
            capture(prefix+"-p6-eye-recap")

            ui = DuelUI(DuelSession.new(seed=800 + size[0], guide_mode="off"), rounds=True)
            ui.session._npc_choice = lambda: Choice(ActionKind.WAIT)
            ui.session.player.strength, ui.session.player.accuracy = 100, 100
            ui.session.player.equipment.weapon.damage_type = "CUTTING"
            ui.session.state.config["accuracy"]["jitter"] = 0
            ui.session.enemy.pos = (ui.session.player.pos[0]+1, ui.session.player.pos[1])
            ui.activate("Heavy")
            capture(prefix+"-p6-death-target")
            click("Head")
            assert ui.session.state.end_reason == "death"
            assert ui.session.enemy.decapitated
            labels = [popup.text for popup in ui.model().popups]
            assert labels.count("DEATH — decapitation") == 1 and len(labels) == 1, labels
            capture(prefix+"-p6-death")
        # Controlled light/light trade: verify both impact numbers and effects.
        from pirates.duel_system.model import Zone
        window.size = (1280, 800)
        ui = DuelUI(DuelSession.new(guide_mode='off'), rounds=True, coach_enabled=True)
        ui.session.enemy.pos = (ui.session.player.pos[0]+1, ui.session.player.pos[1])
        ui.session._npc_choice = lambda: Choice(ActionKind.LIGHT, Zone.TORSO)
        ui.activate('Light')
        ui.activate('Torso')
        display.playback = None
        capture('trade-light-light-recap')
        assert 'You 14.0 cut' in ui.round_summary[3] and 'deep ≥ 16' in ui.round_summary[3]
        ui.session.player.fatigue = 75
        capture('trade-fatigue-consequences')
        ui.activate('Strategy')
        capture('trade-strategy-condition')
        (args.output / "verification.json").write_text(json.dumps(frames, indent=2), encoding="utf-8")
        # Exercise the actual modal dispatch: premature combat input is ignored
        # during playback, then the next normal movement action animates.
        import tcod.event
        from pirates.duel_ui import run_sword_duel
        window.size = (640, 600)
        original_world = (world.game_map, world.player.x, world.player.y, world.clock.total_ticks)
        world.duel_journal = DuelSession.new(guide_mode="off").to_data()
        world.duel_journal['coach_enabled'] = True
        native_wait, native_draw = tcod.event.wait, GameDuelDisplay.draw
        modal = {"phase": 0, "animating": False, "frames": 0, "movement_frames": 0}
        def track_draw(instance, *a, **kw):
            native_draw(instance, *a, **kw)
            modal["animating"] = instance.animating
            modal["frames"] += 1
            playback = getattr(instance, "playback", None)
            if instance.animating and playback and any(
                    first.player_pos != second.player_pos or first.enemy_pos != second.enemy_pos
                    for first, second in zip(playback.frames, playback.frames[1:])):
                modal["movement_frames"] += 1
        def scripted_events(timeout=None):
            phase = modal["phase"]
            if phase in (3, 6) and modal["animating"]:
                list(native_wait(timeout))
                return []
            sym = {0: KeySym.SPACE, 1: KeySym.T, 2: KeySym.G,
                   3: KeySym.M, 4: KeySym.S, 5: KeySym.DOWN, 6: KeySym.ESCAPE}.get(phase, KeySym.ESCAPE)
            # S is intentionally not a direction key: the real arrow commits.
            modal["phase"] += 1
            return [tcod.event.KeyDown(scancode=tcod.event.Scancode.UNKNOWN, sym=sym,
                                       mod=tcod.event.Modifier.NONE, repeat=False)]
        try:
            tcod.event.wait, GameDuelDisplay.draw = scripted_events, track_draw
            assert run_sword_duel(renderer, world) == "paused"
        finally:
            tcod.event.wait, GameDuelDisplay.draw = native_wait, native_draw
        restored = DuelSession.from_data(world.duel_journal)
        assert len(restored.inputs) == 2, "Animation accepted an extra combat action"
        assert world.duel_journal['coach_enabled'], 'Coach preference did not survive pause'
        assert modal["movement_frames"] > 0, modal
        assert original_world == (world.game_map, world.player.x, world.player.y, world.clock.total_ticks)
        (args.output / "modal-verification.json").write_text(json.dumps(modal, indent=2), encoding="utf-8")
        print(f"Native dashboard passed: {len(frames)} captures; real target/direction clicks, one commitment each, pure rendering, all hitboxes on screen.")
    finally:
        renderer.shutdown()


if __name__ == "__main__":
    main()
