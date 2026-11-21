"""Save-free, real SDL round-mode duel UX audit at desktop and compact sizes."""
from pathlib import Path
import argparse
import json
import sys
import time
import tcod.event

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "engine")]

from PIL import Image
from tcod.event import KeySym
import play
from pirates.duel_system.engine import check_end_conditions
from pirates.duel_system.session import DuelSession
from pirates.duel_ui import DuelUI, GameDuelDisplay, run_sword_duel


def force_clean_aims(config):
    """Keep mechanics-coverage captures independent of production aim rolls."""
    config["accuracy"]["aim_dispersion"].update(
        clean_base=1, per_accuracy=0, difficulty_penalty=0,
        clean_min=1, clean_max=1, drift_share=0)


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
            display.round_summary, display.stat_enemy = ui.round_summary, ui.stat_enemy
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
                    observed = display.tile_at((mx+(pos[0]+.5)*tw, my+(pos[1]+.5)*th))
                    assert observed == pos, (name, pos, observed, model.player_pos,
                                             model.enemy_pos, display.map_rect)
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

        from pirates.duel_system.model import Severity, Wound, Zone
        entry_npc_actions = []
        for size in ((1280, 800), (960, 700), (640, 600)):
            window.size = size
            prefix = str(size[0])
            # Exercise the real fresh-duel launch loop with native modal input:
            # select a distinct profile, enter the dashboard, then pause. This
            # checks the live launch/session/journal boundary, not just the
            # picker helper or a manually constructed DuelUI.
            profile_index = {1280: 2, 960: 3, 640: 1}[size[0]]
            launch_world = play.World()
            campaign_tick = launch_world.clock.total_ticks
            launch_events = [
                [tcod.event.KeyDown(timestamp_ns=0, scancode=tcod.event.Scancode.DOWN,
                                    sym=KeySym.DOWN, mod=tcod.event.Modifier.NONE)
                 for _ in range(profile_index)]
                + [tcod.event.KeyDown(timestamp_ns=0, scancode=tcod.event.Scancode.RETURN,
                                      sym=KeySym.RETURN, mod=tcod.event.Modifier.NONE)],
                [tcod.event.KeyDown(timestamp_ns=0, scancode=tcod.event.Scancode.J,
                                    sym=KeySym.J, mod=tcod.event.Modifier.NONE)],
                [tcod.event.KeyDown(timestamp_ns=0, scancode=tcod.event.Scancode.T,
                                    sym=KeySym.T, mod=tcod.event.Modifier.NONE)],
                [tcod.event.KeyDown(timestamp_ns=0, scancode=tcod.event.Scancode.RETURN,
                                    sym=KeySym.RETURN, mod=tcod.event.Modifier.NONE)],
                [tcod.event.KeyDown(timestamp_ns=0, scancode=tcod.event.Scancode.ESCAPE,
                                    sym=KeySym.ESCAPE, mod=tcod.event.Modifier.NONE)],
            ]
            original_wait = tcod.event.wait
            tcod.event.wait = lambda *_args, **_kwargs: launch_events.pop(0)
            try:
                assert run_sword_duel(renderer, launch_world) == "paused"
            finally:
                tcod.event.wait = original_wait
            resumed = DuelSession.from_data(launch_world.duel_journal)
            expected_profile = ("veteran", "counterfighter", "aggressor")[
                {1280: 0, 960: 1, 640: 2}[size[0]]]
            assert resumed.enemy.personality.archetype == expected_profile
            assert launch_world.clock.total_ticks == campaign_tick
            assert not launch_events
            npc_commits = [event for event in resumed.state.event_log
                           if event.type.value == "ACTION_START" and event.actor == resumed.enemy.id]
            player_commits = [event for event in resumed.state.event_log
                              if event.type.value == "ACTION_START" and event.actor == resumed.player.id]
            assert len(player_commits) == len(npc_commits) == 1
            assert player_commits[0].deltas["kind"] == "DASH_LIGHT"
            entry_npc_actions.append(npc_commits[0].deltas["kind"])
            Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(
                args.output / f"{prefix}-00-played-{expected_profile}-{npc_commits[0].deltas['kind'].lower()}.png")

            renderer.render_list(
                world, "Choose your sword-duel opponent",
                ["Brute — presses forward and attacks directly",
                 "Aggressor — fast, assertive pressure",
                 "Veteran — patient, disciplined choices",
                 "Counterfighter — guards and punishes openings",
                 "Recruit — cautious and easier to unsettle",
                 "Bully — confident, forceful attacks"],
                "Standalone practice duel; campaign clock pauses. Esc cancels without campaign changes.")
            renderer._present_layers()
            Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(
                args.output / f"{prefix}-00-opponent-picker.png")
            ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
            cut_rate = ui.session.state.config["wounds"]["TORSO"]["CUT"]["bleed"]
            ui.session.player.wounds = [Wound(Zone.TORSO, Severity.CUT, cut_rate)]
            ui.session.enemy.wounds = [Wound(Zone.EYE, Severity.GRAZE, 0)]
            injury_model = ui.model()
            assert f"continuing bleed {cut_rate:g}/turn" in " ".join(injury_model.player_injury_rows)
            assert "eye" in " ".join(injury_model.enemy_injury_rows).lower()
            capture(prefix+"-01-injury-ledger")
            ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
            capture(prefix+"-01-ready")
            click("Dash+Light")
            assert ui.panel == "zones" and "approach path is automatic" in ui.message.lower()
            capture(prefix+"-01-dash-light-target-zone")
            ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
            ui.session.player.pos, ui.session.enemy.pos = (4, 3), (5, 3)
            ui._model_cache = None
            slash_option = next(option for option in ui.model().actions
                                if option.label == "Dash+Slash")
            assert slash_option.enabled
            capture(prefix+"-01-dash-slash-ready")
            click("Dash+Slash")
            assert ui.panel == "direction"
            capture(prefix+"-01-dash-slash-direction")
            click("N")
            assert ui.panel == "zones" and "retreat/lateral step" in ui.message
            capture(prefix+"-01-dash-slash-zone")
            ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
            ui.session.player.pos, ui.session.enemy.pos = (4, 3), (5, 3)
            ui._model_cache = None
            display.hover_action = "Feint"
            capture(prefix+"-01-feint-range-explanation")
            display.hover_action = None
            click("Disengage")
            assert ui.panel == "direction" and "strictly increases distance" in ui.message
            capture(prefix+"-01-disengage-direction")
            ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
            from pirates.duel_system.engine import Choice, advance_one_tick, commit_action
            from pirates.duel_system.exchange_log import format_exchange, summarize_exchange
            from pirates.duel_system.model import ActionKind, EventType
            clash_session = DuelSession.new(guide_mode="off")
            clash_session.player.pos, clash_session.enemy.pos = (4, 3), (5, 3)
            p_action = commit_action(
                clash_session.state, clash_session.player.id,
                Choice(ActionKind.LIGHT, Zone.TORSO, target_id=clash_session.enemy.id))
            commit_action(
                clash_session.state, clash_session.enemy.id,
                Choice(ActionKind.LIGHT, Zone.TORSO, target_id=clash_session.player.id))
            while (clash_session.state.tick < p_action.strike_tick
                   and not clash_session.state.end_reason):
                advance_one_tick(clash_session.state)
            assert any(event.type == EventType.CLASH for event in clash_session.state.event_log)
            ui = DuelUI(clash_session, rounds=True)
            names = {clash_session.player.id: "You", clash_session.enemy.id: clash_session.enemy.name}
            ui.round_result = format_exchange(clash_session.state.event_log, names)
            ui.round_summary = summarize_exchange(clash_session.state.event_log, names)
            assert any("weapon paths intersected" in line for line in ui.round_result)
            capture(prefix+"-01-blade-clash")
            ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
            ui.session.player.pos = (4, 3)
            ui.session.enemy.pos = (5, 3)
            ui._model_cache = None
            shove_option = next(option for option in ui.model().actions if option.label == "Shove")
            assert "shove contest t" in shove_option.details.lower()
            assert "same-tick strikes land first" in shove_option.details.lower()
            display.hover_action = "Shove"
            capture(prefix+"-01-shove-windup-preview")
            display.hover_action = None
            capture(prefix+"-01-shove-preview-closed")
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
            zone_forecasts = [option.forecast for option in ui.model().zones if option.forecast]
            assert len(zone_forecasts) >= 5 and all("wound force" in forecast and "zone-protection points" in forecast
                                          for forecast in zone_forecasts)
            capture(prefix+"-02-target")
            display.scroll = display.max_scroll
            capture(prefix+"-02-target-forecast-scroll")
            display.scroll = 0
            click("Torso")
            assert len(ui.session.inputs) == 1
            assert len([e for e in ui.session.state.event_log if e.type.value == "ACTION_START"]) == 2
            capture(prefix+"-03-outcome")
            assert anchors == {key: display.layout[key] for key in anchors}, "Result moved the interface"
            assert ui.round_result[0].startswith("You chose:")
            assert ui.round_result[1].startswith("Ronin chose:")
            assert ("Why:" in " ".join(ui.round_result)
                    or "both body strikes were stopped" in " ".join(ui.round_result)), ui.round_result
            display.scroll = 5
            capture(prefix+"-03-explained-scroll")
            display.scroll = 0
            click("Details")
            capture(prefix+"-03-details")
            assert display.reading_modal
            mx, my, mw, mh = display.modal_rect
            assert mw > 0 and mh > 0 and 0 <= mx < mx+mw <= size[0]
            assert 0 <= my < my+mh <= size[1]
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
            from pirates.duel_system.engine import Choice, commit_action
            from pirates.duel_system.model import ActionKind, Zone
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
            ui.activate("Stat:Fear")
            assert "Fear reduces effective Speed and Accuracy" in " ".join(ui.sidebar_rows(ui.model()))
            capture(prefix+"-08-fear-stat")
            ui.activate("Back")
            ui.activate("Stat:Anger")
            assert "Anger" in " ".join(ui.sidebar_rows(ui.model()))
            capture(prefix+"-08-anger-stat")
            ui.activate("Back")
            ui.activate("Stat:Pain")
            assert "Pain tolerance" in " ".join(ui.sidebar_rows(ui.model()))
            capture(prefix+"-08-pain-stat")
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
            ui = DuelUI(DuelSession.new(seed=180 + size[0], guide_mode="off"), rounds=True)
            ui.session.player.pos, ui.session.enemy.pos = (4, 3), (5, 3)
            ui.session._npc_choice = lambda: Choice(ActionKind.WAIT)
            commit_action(ui.session.state, ui.session.enemy.id,
                          Choice(ActionKind.HEAVY, Zone.TORSO,
                                 target_id=ui.session.player.id))
            display.hover_action = "Defend"
            capture(prefix+"-p4-defend-threat")
            defend_details = next(option.details for option in ui.model().actions
                                  if option.label == "Defend")
            assert "Defend →" in defend_details and "Costs" in defend_details
            display.hover_action = None
            ui = DuelUI(DuelSession.new(seed=280 + size[0], guide_mode="off"),
                        rounds=True)
            ui.session.player.pos, ui.session.enemy.pos = (4, 3), (5, 3)
            ui.session.player.speed = 40
            commit_action(ui.session.state, ui.session.enemy.id,
                          Choice(ActionKind.SHOVE, target_id=ui.session.player.id))
            display.hover_action = "Defend"
            capture(prefix+"-p4-defend-shove-dash-slash")
            shove_defend_details = next(option.details for option in ui.model().actions
                                        if option.label == "Defend")
            assert "Dash+Light" in shove_defend_details and "before its t" in shove_defend_details
            display.hover_action = None
            ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True, coach_enabled=True)
            ui.session.player.stability = 1
            ui.session._npc_choice = lambda: Choice(ActionKind.WAIT)
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

            ui = DuelUI(DuelSession.new(guide_mode="off"), rounds=True)
            ui.session.enemy.pos = ui.session.player.pos
            ui.session.enemy.stability = 0
            ui.session.enemy.grounded = True
            ui.session.enemy.grounded_since = ui.session.state.tick
            from pirates.duel_scene import pile_visible_actor_indices
            display.pile_blink_now = 0.1
            assert pile_visible_actor_indices(ui.model().actors,
                                              [actor.pos for actor in ui.model().actors],
                                              display.pile_blink_now) == frozenset({0})
            capture(prefix+"-p4-ground-pile-player")
            display.pile_blink_now = 0.4
            assert pile_visible_actor_indices(ui.model().actors,
                                              [actor.pos for actor in ui.model().actors],
                                              display.pile_blink_now) == frozenset({1})
            capture(prefix+"-p4-ground-pile-opponent")
            display.pile_blink_now = None

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
            force_clean_aims(ui.session.state.config)
            ui.session.enemy.pos = (ui.session.player.pos[0]+1, ui.session.player.pos[1])
            ui.activate("Heavy")
            capture(prefix+"-p6-eye-target")
            assert "Eye" in [label for _, label in display.buttons]
            click("Eye")
            assert ui.session.enemy.permanent_injuries.eyes_damaged == 1
            assert any(event.type == EventType.EYE_INJURY for event in ui.session.state.event_log)
            reflex = next(event for event in ui.session.state.event_log
                          if event.type == EventType.INJURY_RESPONSE)
            assert reflex.deltas["response"] in {"FLINCH", "RECOIL"}
            assert reflex.deltas["to_pos"] != reflex.deltas["from_pos"]
            assert "forced back 1 tile (injury reflex)" in " ".join(ui.round_summary)
            assert any(popup.text == "Back 1 tile" and popup.big for popup in ui.model().popups)
            capture(prefix+"-p6-eye-injury")
            if display.max_scroll:
                display.scroll = display.max_scroll
                capture(prefix+"-p6-eye-injury-scrolled")
                display.scroll = 0
            click("Details")
            assert ui.panel == "details"
            capture(prefix+"-p6-eye-injury-details")
            click("Back")
            capture(prefix+"-p6-eye-injury-details-closed")
            click("More")
            capture(prefix+"-p6-eye-more")
            click("Log")
            capture(prefix+"-p6-eye-recap")

            # Repeat the user's low-damage eye-contact loop through the real
            # dashboard controls. Bound the arena so recoil braces at the edge
            # and the fighters remain in range for the second chosen strike.
            ui = DuelUI(DuelSession.new(seed=900 + size[0], guide_mode="off"), rounds=True)
            ui.session._npc_choice = lambda: Choice(ActionKind.WAIT)
            player, enemy = ui.session.player, ui.session.enemy
            enemy.pos = (player.pos[0]+1, player.pos[1])
            ui.session.state.walkable = {player.pos, enemy.pos}
            player.accuracy = 100
            force_clean_aims(ui.session.state.config)
            capture(prefix+"-p6-repeat-eye-ready")
            for attempt, expected_meter in ((1, 14), (2, 4)):
                click("Light")
                assert ui.panel == "zones"
                capture(prefix+f"-p6-repeat-eye-{attempt}-target")
                click("Eye")
                if attempt == 1:
                    assert enemy.permanent_injuries.eye_trauma == expected_meter
                    assert enemy.permanent_injuries.eyes_damaged == 0
                    assert "14/24 points" in " ".join(ui.round_summary)
                    assert "bleeding 0.5 blood/turn" in " ".join(ui.round_summary)
                else:
                    assert enemy.permanent_injuries.eyes_damaged == 1
                    assert enemy.permanent_injuries.eye_trauma == expected_meter
                    assert any(event.type == EventType.EYE_INJURY
                               for event in ui.session.state.event_log)
                    assert "Eye trauma reached 24" in " ".join(ui.round_summary)
                    assert "4 points carry toward the next eye" in " ".join(ui.round_summary)
                capture(prefix+f"-p6-repeat-eye-{attempt}")

            ui = DuelUI(DuelSession.new(seed=7, guide_mode="off"), rounds=True)
            ui.session._npc_choice = lambda: Choice(ActionKind.WAIT)
            ui.session.player.strength, ui.session.player.accuracy = 100, 100
            ui.session.player.equipment.weapon.damage_type = "CUTTING"
            force_clean_aims(ui.session.state.config)
            ui.session.state.config["anatomy_sites"]["HEAD"] = ["NECK"]
            ui.session.enemy.pos = (ui.session.player.pos[0]+1, ui.session.player.pos[1])
            ui.activate("Heavy")
            capture(prefix+"-p6-death-target")
            click("Head")
            assert ui.session.state.end_reason == "death"
            assert ui.session.enemy.decapitated
            labels = [popup.text for popup in ui.model().popups]
            assert labels.count("DEATH — decapitation") == 1 and len(labels) == 1, labels
            capture(prefix+"-p6-death")
        assert len(set(entry_npc_actions)) >= 2, entry_npc_actions
        # Controlled Light/Light exchange now exercises the same-line clash
        # rule and its public recap, without damage-trade arithmetic.
        from pirates.duel_system.model import Zone
        window.size = (1280, 800)
        ui = DuelUI(DuelSession.new(guide_mode='off'), rounds=True, coach_enabled=True)
        ui.session.enemy.pos = (ui.session.player.pos[0]+1, ui.session.player.pos[1])
        ui.session._npc_choice = lambda: Choice(ActionKind.LIGHT, Zone.TORSO)
        ui.activate('Light')
        ui.activate('Torso')
        display.playback = None
        capture('consequence-light-light-recap')
        recap = " ".join(ui.round_summary).lower()
        assert "you: light" in recap and "ronin: light" in recap
        assert "weapon paths clashed" in recap and "effects:" in recap
        assert not any(token in recap for token in ("net damage", "damage trade", "power"))
        if "wound force" in recap:
            assert "zone protection" in recap
        ui.session.player.fatigue = 75
        capture('consequence-fatigue')
        ui.activate('Strategy')
        capture('consequence-strategy-condition')
        (args.output / "verification.json").write_text(json.dumps(frames, indent=2), encoding="utf-8")
        # Exercise the actual modal dispatch: premature combat input is ignored
        # during playback, then the next normal movement action animates.
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
