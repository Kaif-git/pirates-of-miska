"""Run the ASCII-first Pirates of Miska prototype with the copied SDL engine."""
from pathlib import Path
import argparse
import json
import os
import re
import sys
from PIL import Image
from tcod.event import KeySym
import tcod.event
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "engine"))

import omnisim.ui.ascii_renderer as ascii_renderer
from omnisim.ui.ascii_renderer import AsciiRenderer
from omnisim.ui.render_settings import RenderSettings
from omnisim.world import tile as T
from tales_sim.clock import TICKS_PER_SECOND
from pirates.game import World, SAVE_PATH, MVP_SAVE_PATH
from pirates.combat import (NPC_PROFILE_INTEGER_FIELDS, NPC_PROFILE_RANGES,
                             speed_band, technique_summary, validate_npc_profile)
from pirates.combat_core import WEAPONS, duel_weapon_for_actor
from pirates.management import (crew_menu, chores_menu, ship_rows, time_menu,
                                context_menu, profile_rows, events_menu)
from pirates.economy import PORTS, MERCHANTS

# Bind the game's extra command at the adapter boundary; keep the vendored
# renderer snapshot byte-for-byte intact for provenance verification.
_engine_world_action_for_key = ascii_renderer.world_action_for_key
COMBAT_INPUT_MODE = False
COMBAT_DASH_ARMED = False
UI_PREFERENCES_PATH = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) \
    / "Pirates of Miska" / "ui-preferences.json"


def load_ui_preferences(path=None):
    path = Path(path or UI_PREFERENCES_PATH)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"combat_reduced_motion": False, "combat_tips_dismissed": False}
    if not isinstance(data, dict):
        data = {}
    return {"combat_reduced_motion": bool(data.get("combat_reduced_motion", False)),
            "combat_tips_dismissed": bool(data.get("combat_tips_dismissed", False))}


def save_ui_preferences(preferences, path=None):
    path = Path(path or UI_PREFERENCES_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps({
        "combat_reduced_motion": bool(preferences.get("combat_reduced_motion", False)),
        "combat_tips_dismissed": bool(preferences.get("combat_tips_dismissed", False)),
    }, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


COMBAT_UI_PREFERENCES = load_ui_preferences()
GUIDED_COMBAT_ACTION_BUTTONS = (
    ("Light", "Space", "combat_duel_light"), ("Heavy", "F", "combat_duel_heavy"),
    ("Parry", "G", "combat_duel_parry"), ("Dash", "D+dir", "combat_dash_arm"),
    ("Dash-strike", "J", "combat_duel_dash_strike"),
)
LEGACY_MELEE_ACTION_BUTTONS = (
    ("Attack", "Space", "combat_attack"), ("Heavy", "F", "combat_heavy"),
    ("Guard", "G", "combat_guard"), ("Dash", "D+dir", "combat_dash_arm"),
    ("More", "C", "combat_context"),
)
LEGACY_RANGED_ACTION_BUTTONS = (
    ("Aim", "R", "combat_aim"), ("Fire", "Space/F", "combat_attack"),
    ("Reload", "T", "combat_reload"), ("Guard", "G", "combat_guard"),
    ("More", "C", "combat_context"),
)
# Retain the historical name for callers that explicitly need the duel controls.
COMBAT_ACTION_BUTTONS = GUIDED_COMBAT_ACTION_BUTTONS


def combat_action_bar(arena):
    """Return a truthful mode label and quick actions for the current encounter."""
    if arena.guided_duel_engaged or arena.guided_duel_available:
        return "GUIDED DUEL · deterministic actions", GUIDED_COMBAT_ACTION_BUTTONS
    player = arena.fighters.get("captain")
    if player and WEAPONS[player.weapon]["reload"]:
        return "RANGED · aim, fire, reload", LEGACY_RANGED_ACTION_BUTTONS
    return "GROUP / MELEE · legacy actions", LEGACY_MELEE_ACTION_BUTTONS


def legacy_action_for_duel_key(action_name):
    """Map shared Space/F/G keys to truthful legacy actions outside guided mode."""
    return {"combat_duel_light": "attack", "combat_duel_heavy": "heavy",
            "combat_duel_parry": "guard"}.get(action_name)


def combat_feedback_rise(age_seconds, lifetime=1.8, distance_px=28):
    """Pure UI animation curve; deliberately independent of simulation ticks."""
    if age_seconds < 0 or age_seconds >= lifetime:
        return None
    return distance_px * age_seconds / lifetime


def combat_feedback_offset(age_seconds, reduced_motion=False):
    return 0 if reduced_motion else combat_feedback_rise(age_seconds)


def combat_control_hint(dash_armed=False, arena=None):
    if arena and arena.finished:
        return arena.player_status()[1]
    if dash_armed:
        return "DASH ARMED · choose an eight-way landing with arrows/click · Esc cancels"
    motion = "ON" if COMBAT_UI_PREFERENCES["combat_reduced_motion"] else "OFF"
    common = f"Arrows move · Shift sprint · H history · M reduced motion {motion} · I help · Esc exit"
    if arena and (arena.guided_duel_engaged or arena.guided_duel_available):
        return "Space Light · F Heavy · G Parry · D+dir Dash · J Dash-strike · I help · Esc exit"
    if arena and arena.fighters.get("captain") \
            and WEAPONS[arena.fighters["captain"].weapon]["reload"]:
        return f"R Aim · Space/F Fire · T Reload · G Guard · C More · {common}"
    if arena:
        return f"Space Attack · F Heavy · G Guard · D+dir Dash · C More · {common}"
    return common


def execute_combat_movement(renderer, world, action, direction, *, sprint_burst=True):
    """Execute movement deterministically, then present only its actual path."""
    arena = world.combat
    player = arena.fighters["captain"]
    if sprint_burst and action == "move" and player.move_mode == "run":
        action = "run"
    start = player.pos
    preview = arena.preview(action, direction)
    result = arena.request(world, action, direction)
    end = player.pos
    path = tuple(preview.get("path", ())) if preview.get("valid") else ()
    if result is not False and end in path:
        traversed = (start, *path[:path.index(end) + 1])
        renderer.animate_combat_path(world, traversed, player.movement_mode)
    return result


def _pirates_world_action_for_key(sym, shift=False, control=False):
    global COMBAT_DASH_ARMED
    if sym == tcod.event.KeySym.C and not COMBAT_INPUT_MODE:
        return ("combat_arena",)
    if COMBAT_INPUT_MODE:
        if sym == tcod.event.KeySym.H:
            return ("combat_history",)
        if sym == tcod.event.KeySym.M:
            return ("combat_motion_toggle",)
        if shift and sym == tcod.event.KeySym.N:
            return ("combat_fast_step",)
        directions = {tcod.event.KeySym.UP: (0, -1), tcod.event.KeySym.RIGHT: (1, 0),
                      tcod.event.KeySym.DOWN: (0, 1), tcod.event.KeySym.LEFT: (-1, 0)}
        if sym in directions and control:
            return ("combat_dash", *directions[sym])
        if sym in directions and COMBAT_DASH_ARMED:
            COMBAT_DASH_ARMED = False
            return ("combat_dash", *directions[sym])
        if sym in (tcod.event.KeySym.LSHIFT, tcod.event.KeySym.RSHIFT):
            return ("combat_run_toggle",)
        diagonals = {
            tcod.event.KeySym.KP_7: (-1, -1), tcod.event.KeySym.KP_9: (1, -1),
            tcod.event.KeySym.KP_1: (-1, 1), tcod.event.KeySym.KP_3: (1, 1),
        }
        if sym in diagonals:
            direction = diagonals[sym]
            armed = COMBAT_DASH_ARMED
            if armed:
                COMBAT_DASH_ARMED = False
            return ("combat_dash", *direction) if control or armed else ("move", *direction)
        if sym == tcod.event.KeySym.ESCAPE and COMBAT_DASH_ARMED:
            COMBAT_DASH_ARMED = False
            return ("combat_dash_cancel",)
        if sym in (tcod.event.KeySym.F11, tcod.event.KeySym.ESCAPE):
            return ("combat_arena",)
        if sym in (tcod.event.KeySym.GRAVE, tcod.event.KeySym.CAPSLOCK):
            return ("combat_run_toggle",)
        if sym == tcod.event.KeySym.PERIOD:
            return ("combat_wait",)
        if sym == tcod.event.KeySym.I:
            return ("combat_help",)
        if sym == tcod.event.KeySym.R:
            return ("combat_aim",)
        if sym == tcod.event.KeySym.T:
            return ("combat_reload",)
        if sym == tcod.event.KeySym.RETURN:
            return ("combat_wait",)
        if sym == tcod.event.KeySym.C:
            return ("combat_context",)
        if sym == tcod.event.KeySym.D:
            COMBAT_DASH_ARMED = True
            return ("combat_dash_arm",)
        combat_keys = {
            tcod.event.KeySym.SPACE: ("combat_duel_light",),
            tcod.event.KeySym.G: ("combat_duel_parry",),
            tcod.event.KeySym.F: ("combat_duel_heavy",),
            tcod.event.KeySym.J: ("combat_duel_dash_strike",),
            tcod.event.KeySym.E: ("combat_bandage",),
            tcod.event.KeySym.X: ("combat_look",),
            tcod.event.KeySym.TAB: ("combat_target",),
            tcod.event.KeySym.BACKSPACE: ("combat_reset",),
            tcod.event.KeySym.F10: ("combat_debug",),
            tcod.event.KeySym.N: ("combat_tick_step",),
            tcod.event.KeySym.Z: ("combat_body",),
            tcod.event.KeySym.F12: ("combat_setup",),
            tcod.event.KeySym.N1: ("combat_part", "head"),
            tcod.event.KeySym.N2: ("combat_part", "torso"),
            tcod.event.KeySym.N3: ("combat_part", "arm"),
            tcod.event.KeySym.N4: ("combat_part", "leg"),
            tcod.event.KeySym.N5: ("combat_body",),
            tcod.event.KeySym.N6: ("combat_part", "hand"),
        }
        if sym in combat_keys:
            return combat_keys[sym]
    if sym == tcod.event.KeySym.F11:
        return ("combat_arena",)
    if sym == tcod.event.KeySym.F12:
        return ("combat_setup",)
    if sym == tcod.event.KeySym.F8:
        return ("blueprint",)
    if sym == tcod.event.KeySym.V:
        return ("voyage",)
    if sym == tcod.event.KeySym.PAGEUP:
        return ("deck", 1)
    if sym == tcod.event.KeySym.PAGEDOWN:
        return ("deck", -1)
    return _engine_world_action_for_key(sym, shift)


ascii_renderer.world_action_for_key = _pirates_world_action_for_key


class PiratesRenderer(AsciiRenderer):
    def wait_action(self):
        """Keep the engine input loop while exposing combat-only mouse/modifier events."""
        while True:
            now = time.perf_counter()
            timeout = self._move_repeater.timeout(now)
            if self.floats or self._activity_dirty or self._combat_feedback_active(now):
                timeout = ascii_renderer.FLOAT_TICK_S if timeout is None else min(
                    timeout, ascii_renderer.FLOAT_TICK_S)
            events = ([self._pending_events.popleft()]
                      if self._pending_events else tcod.event.wait(timeout))
            for event in events:
                if isinstance(event, tuple):
                    if event and event[0] == "inspect_at":
                        return event
                    continue
                if isinstance(event, tcod.event.Quit):
                    return ("quit",)
                if isinstance(event, tcod.event.WindowResized):
                    if self._last_village is not None:
                        self.render(self._last_village)
                    continue
                if isinstance(event, tcod.event.MouseMotion):
                    if self._set_hover_from_position(event.position) and self._last_village is not None:
                        self.render(self._last_village)
                        self._present_layers()
                    continue
                if isinstance(event, tcod.event.MouseButtonDown):
                    if self._last_village is None:
                        continue
                    if self._last_village.combat.active \
                            and event.button == tcod.event.MouseButton.LEFT:
                        button_action = self._combat_action_at(event.position)
                        if button_action:
                            return (button_action,)
                    at = self._mouse_world_tile(event.position, self._last_village)
                    if at is None:
                        continue
                    self._hover_world_tile = at
                    if event.button == tcod.event.MouseButton.RIGHT:
                        if self._last_village.combat.active:
                            target = self._last_village.combat.engine.at(at)
                            if target and target.id != "captain":
                                if (self._last_village.combat.guided_duel_available
                                        or self._last_village.combat.guided_duel_engaged):
                                    return ("combat_duel_light",)
                                return ("combat_target_attack", target.id)
                            if COMBAT_DASH_ARMED:
                                return ("combat_dash_cancel",)
                        return ("combat_context", at[0], at[1])
                    if event.button == tcod.event.MouseButton.LEFT:
                        mods = tcod.event.get_modifier_state()
                        return ("inspect_at", at[0], at[1],
                                bool(mods & tcod.event.Modifier.SHIFT))
                    continue
                if isinstance(event, tcod.event.KeyUp):
                    was_moving = self._move_repeater.action is not None
                    self._move_repeater.release(event.sym)
                    if was_moving and self._move_repeater.action is None:
                        self._finish_motion_cache()
                    continue
                if not isinstance(event, tcod.event.KeyDown):
                    continue
                shift = bool(event.mod & tcod.event.Modifier.SHIFT)
                control = bool(event.mod & (tcod.event.Modifier.LCTRL | tcod.event.Modifier.RCTRL))
                action = _pirates_world_action_for_key(event.sym, shift, control)
                if action is None or (event.repeat and action[0] in {"move", "combat_run_toggle"}):
                    continue
                if action[0] == "move":
                    immediate = self._move_repeater.press(
                        event.sym, action, time.perf_counter(), repeat=event.repeat)
                    if immediate is not None:
                        self._finish_motion_cache()
                        return immediate
                    continue
                self._move_repeater.cancel()
                self._finish_motion_cache()
                return action
            repeated = self._move_repeater.poll(time.perf_counter())
            if repeated is not None:
                return repeated
            if self.floats or self._activity_dirty or self._combat_feedback_active(time.perf_counter()):
                self._purge_expired_floats(time.perf_counter())
                if self._combat_feedback_active(time.perf_counter()) and self._last_village is not None:
                    self.render(self._last_village)
                    self._present_layers()
                else:
                    self._present_layers()
                self._activity_dirty = False

    def _combat_feedback_active(self, now):
        feedback_active = (not COMBAT_UI_PREFERENCES["combat_reduced_motion"] and any(
            now - started < 1.8
            for started in getattr(self, "_combat_feedback_started", {}).values()))
        travel = getattr(self, "_combat_travel_path", None)
        return feedback_active or bool(travel and now < travel[2])

    def _combat_text_texture(self, key, image):
        cache = getattr(self, "_combat_textures", None)
        if cache is None:
            cache = self._combat_textures = {}
        cached = cache.get(key)
        if cached is not None:
            return cached
        texture = self.context.sdl_renderer.upload_texture(
            ascii_renderer.np.asarray(image, dtype=ascii_renderer.np.uint8))
        texture.blend_mode = ascii_renderer.BlendMode.BLEND
        if len(cache) >= 128:
            cache.clear()
        cache[key] = (texture, image.width, image.height)
        return cache[key]

    def _combat_action_at(self, position):
        x, y = position
        for rect, action in getattr(self, "_combat_button_rects", ()):
            left, top, width, height = rect
            if left <= x < left + width and top <= y < top + height:
                return action
        return None

    def _draw_combat_action_bar(self, renderer):
        world = self._last_village
        if not world or not world.combat.active:
            self._combat_button_rects = ()
            return
        arena = world.combat
        output_w, output_h = renderer.output_size
        view_w, view_h = self.viewport
        map_w = max(1, min(output_w, view_w * self.tile_width))
        bar_h = max(1, min(100, output_h))
        top = max(0, min(output_h - bar_h, view_h * self.tile_height - bar_h))
        gap, inset = 4, 5
        mode_label, buttons = combat_action_bar(arena)
        button_w = max(1, (map_w - inset * 2 - gap * (len(buttons) - 1))
                       // len(buttons))
        image = Image.new("RGBA", (map_w, bar_h), (8, 13, 18, 238))
        draw = ascii_renderer.ImageDraw.Draw(image)
        font = self.ui.font_for_family(self.settings.ui_font_family,
                                       size=max(11, min(16, round(bar_h * 0.29))))
        actor = arena.fighters.get("captain")
        target = arena.target()
        ready = bool(actor and actor.active and not arena.finished
                     and actor.next_ready_tick <= arena.engine.tick)
        guided = arena.guided_duel_engaged or arena.guided_duel_available
        duel_ready = bool(ready and arena.guided_duel_available)
        target_gap, reach = 99, 0
        if actor and target:
            target_gap = max(abs(actor.x - target.x), abs(actor.y - target.y))
            try:
                reach = duel_weapon_for_actor(actor).reach_tiles
            except ValueError:
                pass
        if guided:
            enabled = {"combat_duel_light": duel_ready and target_gap <= reach,
                       "combat_duel_heavy": duel_ready and target_gap <= reach + 1,
                       "combat_duel_parry": duel_ready,
                       "combat_dash_arm": duel_ready and bool(actor and actor.stamina >= 35),
                       "combat_duel_dash_strike": duel_ready and bool(actor and actor.stamina >= 60
                                                                          and target and target_gap >= 2)}
        elif actor and WEAPONS[actor.weapon]["reload"]:
            enabled = {
                "combat_aim": ready and arena.preview("aim").get("valid", False),
                "combat_attack": ready and arena.preview("attack").get("valid", False),
                "combat_reload": ready and arena.preview("reload").get("valid", False),
                "combat_guard": ready and arena.preview("guard").get("valid", False),
                "combat_context": True,
            }
        else:
            enabled = {
                "combat_attack": ready and arena.preview("attack").get("valid", False),
                "combat_heavy": ready and arena.preview("heavy").get("valid", False),
                "combat_guard": ready and arena.preview("guard").get("valid", False),
                "combat_dash_arm": ready and bool(actor and actor.stamina >= 35),
                "combat_context": True,
            }
        title_font = self.ui.font_for_family(self.settings.ui_font_family, size=max(9, font.size - 2))
        state, guidance = arena.player_status()
        state_color = ((255, 110, 100, 255) if not actor.active else
                       (130, 245, 170, 255) if arena.finished else (255, 230, 150, 255))
        draw.text((inset, 0), state, font=font, fill=state_color)
        draw.text((inset, 22), guidance, font=title_font, fill=(245, 245, 235, 255))
        draw.text((inset, 42), mode_label, font=title_font, fill=(180, 220, 220, 255))
        if arena.finished:
            enabled = dict.fromkeys(enabled, False)
        rects = []
        for index, (label, key, action) in enumerate(buttons):
            left = inset + index * (button_w + gap)
            width = button_w if index < len(buttons) - 1 else map_w - inset - left
            rect = (left, 62, width, max(1, bar_h - 64))
            active = enabled[action]
            fill = (34, 74, 78, 255) if active else (35, 39, 42, 250)
            outline = (150, 220, 195, 255) if active else (100, 110, 112, 255)
            draw.rounded_rectangle((rect[0], rect[1], rect[0] + width - 1,
                                    rect[1] + rect[3] - 1), radius=5,
                                   fill=fill, outline=outline, width=1)
            text = f"{label} [{key}]"
            bounds = draw.textbbox((0, 0), text, font=font)
            text_w = bounds[2] - bounds[0]
            if text_w > width - 8:
                text = label
                bounds = draw.textbbox((0, 0), text, font=font)
                text_w = bounds[2] - bounds[0]
            x = left + max(4, (width - text_w) // 2)
            y = rect[1] + max(0, (rect[3] - (bounds[3] - bounds[1])) // 2) - bounds[1]
            draw.text((x, y), text, font=font,
                      fill=(255, 255, 245, 255) if active else (175, 183, 185, 255))
            rects.append(((left, top + rect[1], width, rect[3]), action))
        key = ("combat-actionbar", output_w, output_h, map_w, bar_h,
               self.settings.ui_font_size, mode_label, state, guidance, tuple(buttons),
               tuple(enabled.items()))
        texture, _, _ = self._combat_text_texture(key, image)
        renderer.copy(texture, dest=(0, top, map_w, bar_h))
        self._combat_button_rects = tuple(rects)

    def animate_combat_path(self, world, path, mode):
        """Play the already-resolved path visually; never advances combat ticks."""
        if not world.combat.active or len(path) < 2:
            return
        path = tuple(tuple(cell) for cell in path)
        if COMBAT_UI_PREFERENCES["combat_reduced_motion"]:
            self._combat_travel_path = (path, mode, time.perf_counter() + 0.5)
            self.render(world)
            self._present_layers()
            return
        per_tile = {"walk": 0.10, "run": 0.075, "dash": 0.045}.get(mode, 0.08)
        original = (world.player.x, world.player.y)
        self._combat_travel_path = (path, mode, time.perf_counter() + 0.8)
        try:
            for x, y in path[1:]:
                world.player.x, world.player.y = x, y
                self.render(world)
                self._present_layers()
                time.sleep(per_tile)
        finally:
            world.player.x, world.player.y = original
        self._combat_travel_path = (path, mode, time.perf_counter() + 0.45)
        self.render(world)
        self._present_layers()

    def _draw_combat_text_overlays(self, renderer):
        overlay = getattr(self, "_combat_text_overlay", None)
        if not overlay:
            return
        world = self._last_village
        arena = world.combat
        vw, vh = self.viewport
        tw, th = self.tile_width, self.tile_height
        rows, labels = overlay
        engine = arena.engine
        if getattr(self, "_combat_feedback_engine", None) is not engine:
            self._combat_feedback_engine = engine
            self._combat_feedback_started = {}
            self._combat_feedback_last_id = 0
        now = time.perf_counter()
        output_w, output_h = renderer.output_size
        font = self.ui.font_for_family(self.settings.ui_font_family,
                                       size=max(12, round(th * 0.68)))
        draw = ascii_renderer.ImageDraw.Draw(ascii_renderer.Image.new("RGBA", (1, 1)))
        line_h = sum(font.getmetrics()) + 3
        pad = 9
        widths = [draw.textbbox((0, 0), row, font=font)[2] for row in rows]
        panel_w = min(vw * tw, max(widths, default=0) + pad * 2)
        panel_h = min(vh * th, len(rows) * line_h + pad * 2)
        if rows and panel_w > 0:
            key = ("hud", tuple(rows), arena.monochrome, font.size, panel_w, panel_h)
            cached = getattr(self, "_combat_hud_image_cache", {}).get(key)
            if cached is None:
                image = ascii_renderer.Image.new("RGBA", (panel_w, panel_h))
                image_draw = ascii_renderer.ImageDraw.Draw(image)
                border = (210, 210, 210, 235) if arena.monochrome else (220, 190, 110, 235)
                image_draw.rounded_rectangle((0, 0, panel_w - 1, panel_h - 1), radius=6,
                                             fill=(10, 16, 20, 222), outline=border, width=2)
                for index, row in enumerate(rows):
                    image_draw.text((pad, pad + index * line_h), row, font=font,
                                    fill=(235, 238, 228, 255), stroke_width=1,
                                    stroke_fill=(0, 0, 0, 180))
                image_cache = getattr(self, "_combat_hud_image_cache", None)
                if image_cache is None:
                    image_cache = self._combat_hud_image_cache = {}
                image_cache[key] = image
                cached = image
            texture, _, _ = self._combat_text_texture(key, cached)
            renderer.copy(texture, dest=(6, max(6, vh * th - panel_h - 8), panel_w, panel_h))

        cx, cy, ox, oy = self._active_camera
        popup_stacks = {}
        placed_labels = []
        active_feedback_keys = set()
        for label in labels:
            aid, (x, y), text, color = label[:4]
            rise = 0
            if len(label) > 4:
                event_key = (id(engine), label[4])
                active_feedback_keys.add(event_key)
                started = self._combat_feedback_started.get(event_key)
                if started is None:
                    if label[4] <= self._combat_feedback_last_id:
                        continue
                    started = self._combat_feedback_started[event_key] = now
                    self._combat_feedback_last_id = max(self._combat_feedback_last_id, label[4])
                age = now - started
                rise = combat_feedback_offset(age, COMBAT_UI_PREFERENCES["combat_reduced_motion"])
                if rise is None:
                    continue
            if not world.game_map.visible[y, x] or not text:
                continue
            stack_key = (aid, x, y)
            stack = popup_stacks.get(stack_key, 0)
            popup_stacks[stack_key] = stack + 1
            px = (x - cx + ox + 0.5) * tw
            py = (y - cy + oy) * th
            label_font = self.ui.font_for_family(self.settings.world_font_family,
                                                 size=max(13, round(th * 0.72)))
            lp, top_pad = 7, 4
            max_text_w = max(120, min(output_w, vw * tw) - 32 - lp * 2)
            wrapped, line = [], ""
            for word in text.split():
                candidate = f"{line} {word}".strip()
                candidate_w = draw.textbbox((0, 0), candidate, font=label_font,
                                            stroke_width=1)[2]
                if line and candidate_w > max_text_w:
                    wrapped.append(line)
                    line = word
                else:
                    line = candidate
            if line:
                wrapped.append(line)
            display_text = "\n".join(wrapped)
            bbox = draw.multiline_textbbox((0, 0), display_text, font=label_font,
                                           spacing=2, stroke_width=1)
            text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            w, h = text_w + lp * 2, text_h + top_pad * 2
            label_key = ("intent", display_text, tuple(color), label_font.size, w, h)
            image = getattr(self, "_combat_label_images", {}).get(label_key)
            if image is None:
                image = ascii_renderer.Image.new("RGBA", (w, h))
                label_draw = ascii_renderer.ImageDraw.Draw(image)
                bg = (48, 20, 16, 220) if color == (255, 165, 125) else (48, 38, 12, 220)
                label_draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=5, fill=bg)
                label_draw.multiline_text((lp - bbox[0], top_pad - bbox[1]), display_text,
                                          font=label_font, fill=(*color, 255), spacing=2,
                                          stroke_width=1, stroke_fill=(0, 0, 0, 160))
                image_cache = getattr(self, "_combat_label_images", None)
                if image_cache is None:
                    image_cache = self._combat_label_images = {}
                if len(image_cache) >= 96:
                    image_cache.clear()
                image_cache[label_key] = image
            texture, _, _ = self._combat_text_texture(label_key, image)
            left = px - w / 2
            left = max(0, min(output_w - w, left))
            top = py - h - max(2, th * 0.12) - rise - stack * (h + 3)
            # Adjacent actors and simultaneous feedback can have nearly
            # identical anchors. Lift colliding labels while space remains,
            # keeping intent, player guidance, and outcome text legible.
            while (top > 0 and any(left < old_right and left + w > old_left
                                   and top < old_bottom and top + h > old_top
                                   for old_left, old_top, old_right, old_bottom in placed_labels)):
                top -= h + 3
            top = max(0, top)
            if left < output_w and left + w > 0 and top < output_h and top + h > 0:
                source_x, source_y = max(0, -left), max(0, -top)
                right, bottom = min(output_w, left + w), min(output_h, top + h)
                renderer.copy(texture, source=(source_x, source_y, right - max(0, left),
                                                bottom - max(0, top)),
                              dest=(max(0, left), max(0, top), right - max(0, left),
                                    bottom - max(0, top)))
                placed_labels.append((left, top, left + w, top + h))
        self._combat_feedback_started = {
            key: started for key, started in self._combat_feedback_started.items()
            if key in active_feedback_keys and now - started < 1.8
        }

    def _draw_world_floats(self, renderer):
        super()._draw_world_floats(renderer)
        self._draw_combat_text_overlays(renderer)

    def _present_layers(self):
        super()._present_layers()
        if self.context is not None:
            # Draw contextual controls last so they remain visible/clickable.
            self._draw_combat_action_bar(self.context.sdl_renderer)

    def _build(self):
        # Install before tcod.context.new uploads the atlas to SDL.
        loader = ascii_renderer.tcod.tileset.load_truetype_font
        sprite_codes = {}

        def load_font_with_urizen(*args, **kwargs):
            tileset = loader(*args, **kwargs)
            from pirates.urizen_tileset import install_sprite_atlas
            sprite_codes.update(install_sprite_atlas(
                tileset, self.tile_width, self.tile_height))
            from pirates.player_sprites import install_player_sprite
            sprite_codes.update(install_player_sprite(
                tileset, self.tile_width, self.tile_height))
            return tileset

        ascii_renderer.tcod.tileset.load_truetype_font = load_font_with_urizen
        try:
            super()._build()
        finally:
            ascii_renderer.tcod.tileset.load_truetype_font = loader
        # Zoom/font rebuilds replace the SDL renderer. Textures uploaded to the
        # previous renderer are invalid even though our image-key caches survive.
        self._combat_textures = {}
        self._sprite_codepoints.update(sprite_codes)
        if self.world:
            # AsciiRenderer replaces its dictionary after atlas loading; reconnect
            # the WorldRenderer to the newly installed private-use codepoints.
            self.world.sprite_codepoints = self._sprite_codepoints
        if self.context.sdl_window:
            self.context.sdl_window.title = f"Pirates of Miska — Urizen [{self.zoom_name}]"

    def sprite_for_tile(self, game_map, x, y):
        if COMBAT_INPUT_MODE:
            return None
        from pirates.urizen_tileset import sprite_for_tile
        tile_id = int(game_map.tiles[y, x])
        return sprite_for_tile(tile_id, door_open=(tile_id == T.DOOR and bool(game_map.doors[y, x])))

    def sprite_for_entity(self, entity):
        if COMBAT_INPUT_MODE:
            return None
        from pirates.player_sprites import sprite_for_entity
        return sprite_for_entity(entity)

    def sprite_for_ground(self, world, wx, wy):
        from pirates.urizen_tileset import sprite_for_ground
        return sprite_for_ground(world.ground_at(wx, wy))

    def _draw_lit_cell(self, sx, sy, glyph, *, fg, bg, sprite, brightness):
        if sprite and sprite in self._sprite_codepoints:
            # Urizen artwork is already colored; avoid tcod tinting its palette.
            self.world.draw_cell(sx, sy, glyph, fg=(255, 255, 255), bg=bg, sprite=sprite)
        else:
            super()._draw_lit_cell(sx, sy, glyph, fg=fg, bg=bg,
                                   sprite=sprite, brightness=brightness)

    def _sidebar_lines(self, world, width, height):
        ship = world.ship
        status = world.voyages.voyage["status"] if world.voyages.voyage else "moored"
        place = "OPEN SEA" if world.at_sea else PORTS[world.economy.port]['name']
        food_line = (f"Food {ship.food_stock_g() / 1000:.0f}kg | Water {ship.stores['clean_water_ml'] / 1000:.0f}L | "
                     f"Planks {ship.stores['planks']}"
                     if world.scenario_id == "lawful-trader-mvp" else
                     f"Food {ship.food_stock_g() / 1000:.1f} kg | Clean water {ship.stores['clean_water_ml'] / 1000:.1f} L | "
                     f"Planks {ship.stores['planks']}")
        lines = [f"THE WAYWARD | {place} | {status}", f"{world.economy.money} Marks", world.clock.format(),
                 world.room_name(world.player.x, world.player.y, world.player.z),
                  f"Hull {ship.hull:.0f}% | Bilge {ship.bilge:.0f} | Rig {ship.rigging:.0f}%",
                    food_line, "", "CREW"]
        if world.scenario_id == "lawful-trader-mvp":
            lines.insert(1, "AUTHORED MVP | SIM OFF")
        if world.lockdown:
            lines.insert(5, "LOCKDOWN | ship doors barred")
        if world.hazards.fires or world.hazards.smoke:
            lines.insert(5, f"HAZARD: {len(world.hazards.fires)} fires | {len(world.hazards.smoke)} smoky tiles")
        if world.combat.active:
            arena = world.combat
            enemy = arena.target()
            sprint = "SPRINT ON · 2-tile burst" if arena.fighters["captain"].move_mode == "run" else "Walk"
            if arena.guided_duel_engaged or arena.guided_duel_available:
                mode = "GUIDED DUEL"
            elif WEAPONS[arena.fighters["captain"].weapon]["reload"]:
                mode = "RANGED · legacy rules"
            else:
                mode = "GROUP / MELEE · legacy rules"
            lines = [f"COMBAT · {mode}",
                     *arena.player_status(),
                     f"Target: {enemy.name}" if enemy else "Target: none · Tab cycles",
                     ("Fight ended · Backspace retry · Esc leave" if arena.finished else
                      "D + direction to reposition" if arena.guided_duel_engaged else
                      f"{sprint} · click buttons below · Esc leave")]
            return [[(line, (220, 220, 220), (16, 16, 16))] for line in lines]
        for sailor in ship.crew:
            if sailor.z != world.player.z:
                continue
            lines.extend([f"{sailor.glyph} {sailor.name} | {sailor.mood_label} {sailor.morale:.0f}", f"  {sailor.activity}"])
        lines += ["", "F2 Crew / labors | A Chores", "F3 Ship | F4 Pass time | F6 Events",
                  f"C Arena: {'closed duel' if world.combat.scenario == 'skirmish' else world.combat.scenario} | F12 setup",
                  "V Voyage / sea chart | Enter: interact"]
        return [[(line, (210, 210, 210), (16, 16, 16))] for line in lines]

    def _status_lines(self, world):
        if world.combat.active:
            if world.combat.finished:
                return list(world.combat.player_status())
            latest = world.combat.engine.log[-1].split("] ", 1)[-1]
            latest = re.sub(r"\s+at tick \d+", "", latest)
            return [latest, combat_control_hint(COMBAT_DASH_ARMED, world.combat)]
        status_width = self.viewport[0] * self.tile_width
        if status_width <= 400:
            max_chars = max(20, status_width // 7 - 2)
            latest = world.log[-1]
            if len(latest) > max_chars:
                latest = latest[:max_chars - 3].rstrip() + "..."
            return [latest, "Arrows move | Enter | F2 crew | V"]
        return [world.log[-1], "Move: arrows/HJKL | PgUp/PgDn stairs | Enter interact | F2 crew | F8 blueprint | +/- zoom"]

    def _ship_mark(self, world, sx, sy, wx, wy, skip_actors=False):
        if world.combat.active:
            return  # Arena has only its fighters/props, never ship dirt/job overlays.
        if not world.game_map.visible[wy, wx]:
            return
        if skip_actors and any((e.x, e.y) == (wx, wy) for e in world.entities()):
            return
        if world.active_deck != 0:
            lx, ly = wx - 69, wy - 12
            if (lx, ly, world.active_deck) in world.locked_doors:
                self._draw_lit_cell(sx, sy, "L", fg=(245, 185, 85), bg=(0, 0, 0),
                                    sprite=None, brightness=1)
                return
            hazard_key = world.hazards.key(lx, ly, world.active_deck)
            if hazard_key in world.hazards.breaches:
                self._draw_lit_cell(sx, sy, "!", fg=(245, 100, 80), bg=(0, 0, 0),
                                    sprite=None, brightness=1)
                return
            if hazard_key in world.hazards.fires:
                self._draw_lit_cell(sx, sy, "*", fg=(255, 95, 35), bg=(45, 12, 0),
                                    sprite=None, brightness=1)
                return
            if world.hazards.water.get(hazard_key, 0) >= 25:
                self._draw_lit_cell(sx, sy, "~", fg=(100, 180, 245), bg=(0, 0, 0),
                                    sprite=None, brightness=1)
                return
            support = next((c for c in world.ship_model.at(lx, ly, world.active_deck)
                            if c.kind in ("beam", "pillar")), None)
            if support:
                self._draw_lit_cell(sx, sy, "=" if support.kind == "beam" else "P",
                                    fg=(230, 190, 120), bg=(0, 0, 0),
                                    sprite=None, brightness=1)
            fixture = next((f for f in world.fixtures.fixtures
                            if f.carrier is None and (f.x, f.y, f.z) ==
                            (lx, ly, world.active_deck)), None)
            if fixture:
                glyph = {"galley": "&", "water": "W", "hold": "H",
                         "bunk": "B", "port_cannon": "C", "starboard_cannon": "C"}.get(
                             fixture.kind, "E")
                self._draw_lit_cell(sx, sy, glyph, fg=(220, 190, 120), bg=(0, 0, 0),
                                    sprite=None, brightness=1)
            change = next((row for row in world.blueprint.changes
                           if (row["x"], row["y"], row["z"]) == (lx, ly, world.active_deck)), None)
            if change:
                glyph = ("x" if change["action"] == "dismantle" else
                         "=" if change["kind"] == "beam" else "P" if change["kind"] == "pillar" else
                         "+" if change["kind"] == "door" else "#" if change["kind"] == "wall" else ".")
                self._draw_lit_cell(sx, sy, glyph, fg=(225, 150, 245), bg=(0, 0, 0),
                                    sprite=None, brightness=1)
                return
            if any(c.kind == "stair" and (c.x, c.y) == (lx, ly)
                   and c.z in (world.active_deck, world.active_deck - 1)
                   for c in world.ship_model.components):
                self._draw_lit_cell(sx, sy, "S", fg=(245, 210, 110), bg=(0, 0, 0),
                                    sprite=None, brightness=1)
            if any(p.z == world.active_deck and p.fixture_id is None
                   and (p.x + 69, p.y + 12) == (wx, wy)
                   for p in world.ship.storage.piles.values()):
                self._draw_lit_cell(sx, sy, "*", fg=(220, 190, 100), bg=(0, 0, 0),
                                    sprite=None, brightness=1)
            for job in world.ship.jobs:
                if (job.construction and job.state not in ("done", "cancelled")
                        and job.construction.get("z", 0) == world.active_deck
                        and tuple(job.target) == (wx, wy)):
                    glyph = "x" if job.construction["action"] == "dismantle" else "+"
                    self._draw_lit_cell(sx, sy, glyph, fg=(100, 220, 245), bg=(0, 0, 0),
                                        sprite=None, brightness=1)
            return
        glyph = None
        color = (185, 170, 125)
        station_glyphs = {"helm": "O", "rigging": "|", "port_cannon": "C", "starboard_cannon": "C",
                          "galley": "&", "water": "W", "hold": "H", "lookout": "^"}
        for name, at in world.ship.stations.items():
            if at == (wx, wy) and name in station_glyphs and world.fixtures.available(name):
                glyph = station_glyphs[name]
        if world.ship.dirt.get(f"{wx},{wy}", 0) >= 20:
            glyph, color = "%", (175, 145, 75)
        if (wx, wy) == world.ship.stations["repair"] and world.ship.hull < 100:
            glyph, color = "!", (240, 140, 80)
        if (wx, wy) == world.ship.stations["bilge"] and world.ship.bilge >= 3:
            glyph, color = "~", (100, 175, 225)
        for job in world.ship.jobs:
            if (job.relocation and job.state not in ("done", "cancelled")
                    and tuple(job.target) == (wx, wy)):
                glyph, color = "F", (100, 220, 245)
            if (job.construction and job.state not in ("done", "cancelled")
                    and tuple(job.target) == (wx, wy)
                    and job.construction.get("z", 0) == world.active_deck):
                glyph = "x" if job.construction["action"] == "dismantle" else "+" if job.construction["kind"] == "door" else "#"
                color = (100, 220, 245)
        lx, ly = wx - 69, wy - 12
        support = next((c for c in world.ship_model.at(lx, ly, world.active_deck)
                        if c.kind in ("beam", "pillar")), None)
        if support:
            glyph, color = ("=" if support.kind == "beam" else "P"), (230, 190, 120)
        if any(c.kind == "stair" and (c.x, c.y) == (lx, ly)
               and c.z in (world.active_deck, world.active_deck - 1)
               for c in world.ship_model.components):
            glyph, color = "S", (245, 210, 110)
        change = next((row for row in world.blueprint.changes
                       if (row["x"] + 69, row["y"] + 12) == (wx, wy)
                       and row["z"] == world.active_deck), None)
        if change:
            glyph = ("x" if change["action"] == "dismantle" else
                     "=" if change["kind"] == "beam" else "P" if change["kind"] == "pillar" else
                     "+" if change["kind"] == "door" else "#" if change["kind"] == "wall" else ".")
            color = (225, 150, 245)
        hazard_key = world.hazards.key(wx - 69, wy - 12, world.active_deck)
        if hazard_key in world.hazards.breaches:
            glyph, color = "!", (245, 100, 80)
        elif hazard_key in world.hazards.fires:
            glyph, color = "*", (255, 95, 35)
        elif world.hazards.water.get(hazard_key, 0) >= 25:
            glyph, color = "~", (100, 180, 245)
        if (lx, ly, world.active_deck) in world.locked_doors:
            glyph, color = "L", (245, 185, 85)
        traffic = world.traffic_overlay.get((lx, ly, world.active_deck), 0)
        if (traffic and world.game_map.is_walkable(wx, wy)
                and hazard_key not in world.hazards.breaches
                and hazard_key not in world.hazards.fires
                and world.hazards.water.get(hazard_key, 0) < 25
                and (lx, ly, world.active_deck) not in world.locked_doors):
            glyph, color = ";", (245, min(240, 165 + traffic * 18), 85)
        if glyph:
            self._draw_lit_cell(sx, sy, glyph, fg=color, bg=(0, 0, 0), sprite=None, brightness=1)
        for pile in world.ship.storage.piles.values():
            at = world.ship.storage.position(pile.id, world.fixtures)
            if at[:2] == (wx, wy) and (at[2] if len(at) == 3 else 0) == world.active_deck:
                self._draw_lit_cell(sx, sy, "*", fg=(220, 190, 100), bg=(0, 0, 0), sprite=None, brightness=1)

    def _frame_cell_extras(self, world):
        cx, cy, ox, oy = self._active_camera
        if world.combat.active:
            arena = world.combat
            threatened = {p for a in arena.fighters.values() if a.intent for p in a.intent.target_tiles}
            aimed = {p for a in arena.fighters.values() for p in a.aim_tiles}
            threatened.update(aimed)
            rows = []  # Keep technical detail out of the default view.
            labels = arena.floating_labels() + arena.feedback_labels()
            preview_summary = None
            environment = {**{p: ("/" if opened else "+", (225, 185, 90))
                              for p, opened in arena.engine.doors.items()},
                           **{p: ("^" if kind == "spikes" else "*", (250, 95, 55))
                              for p, kind in arena.engine.hazards.items()},
                           **{p: ("%", (155, 155, 165)) for p, until in arena.engine.smoke.items()
                              if until > arena.engine.tick}}
            for (x, y), (glyph, color) in environment.items():
                if world.game_map.visible[y, x]:
                    sx, sy = x - cx + ox, y - cy + oy
                    if 0 <= sx < self.console.width and 0 <= sy < self.console.height:
                        self._draw_lit_cell(sx, sy, glyph, fg=color, bg=(0, 0, 0), sprite=None, brightness=1)
            for x, y in sorted(threatened):
                overlay = arena.overlay((x, y))
                if not overlay or not world.game_map.visible[y, x]:
                    continue
                sx, sy = x - cx + ox, y - cy + oy
                if not (0 <= sx < self.console.width and 0 <= sy < self.console.height):
                    continue
                glyph, color = overlay
                actor = arena.engine.at((x, y))
                if actor:
                    glyph = actor.glyph.upper()
                self._draw_lit_cell(sx, sy, glyph, fg=(255, 255, 255),
                                    bg=(55, 55, 55) if arena.monochrome else tuple(c // 3 for c in color),
                                    sprite=None, brightness=1)
            selected = arena.target()
            if selected and world.game_map.visible[selected.y, selected.x]:
                for dx, marker in ((-1, "["), (1, "]")):
                    pos = selected.x + dx, selected.y
                    if not world.game_map.in_bounds(*pos) or not world.game_map.visible[pos[1], pos[0]]:
                        continue
                    if arena.engine.at(pos):
                        continue
                    sx, sy = pos[0] - cx + ox, pos[1] - cy + oy
                    if 0 <= sx < self.console.width and 0 <= sy < self.console.height:
                        self._draw_lit_cell(sx, sy, marker, fg=(255, 220, 105),
                                            bg=(48, 37, 12), sprite=None, brightness=1)
            travel = getattr(self, "_combat_travel_path", None)
            if travel and time.perf_counter() < travel[2]:
                route, mode, _expires = travel
                marker = {"dash": "*", "run": ">", "walk": "."}.get(mode, ".")
                for x, y in route[1:-1]:
                    if not world.game_map.visible[y, x] or arena.engine.at((x, y)):
                        continue
                    sx, sy = x - cx + ox, y - cy + oy
                    if 0 <= sx < self.console.width and 0 <= sy < self.console.height:
                        self._draw_lit_cell(sx, sy, marker,
                                            fg=(255, 225, 130) if mode == "dash" else (140, 230, 175),
                                            bg=(45, 40, 18), sprite=None, brightness=1)
            if COMBAT_DASH_ARMED:
                for direction in ((0, -1), (1, 0), (0, 1), (-1, 0),
                                  (-1, -1), (1, -1), (-1, 1), (1, 1)):
                    preview = arena.preview("dash", direction)
                    if not preview.get("valid"):
                        continue
                    path = preview.get("path", ())
                    for index, (x, y) in enumerate(path):
                        if not world.game_map.visible[y, x]:
                            continue
                        sx, sy = x - cx + ox, y - cy + oy
                        if 0 <= sx < self.console.width and 0 <= sy < self.console.height:
                            marker = "D" if index == len(path) - 1 else ":"
                            color = (240, 225, 120) if not arena.monochrome else (255, 255, 255)
                            self._draw_lit_cell(sx, sy, marker, fg=color,
                                                bg=(45, 45, 45), sprite=None, brightness=1)
            hovered = self._hover_world_tile
            if hovered:
                hover_actor = arena.engine.at(hovered)
                if hover_actor and hover_actor.id != "captain":
                    labels.append((hover_actor.id, hover_actor.pos,
                                   f"CLICK TO TARGET · {hover_actor.name}", (245, 235, 180)))
                player = arena.fighters["captain"]
                dx, dy = hovered[0] - player.x, hovered[1] - player.y
                cardinal = (dx == 0) != (dy == 0)
                distance = max(abs(dx), abs(dy))
                if (cardinal or COMBAT_DASH_ARMED) and distance:
                    direction = (0 if dx == 0 else (1 if dx > 0 else -1),
                                 0 if dy == 0 else (1 if dy > 0 else -1))
                    action = ("dash" if COMBAT_DASH_ARMED else
                              "run" if player.move_mode == "run" else "move")
                    if action == "dash" and distance > 3:
                        action = "move"
                    if action != "dash" and distance > (2 if action == "run" else 1):
                        action = "move"
                    preview_target = (*direction, distance) if action == "dash" else direction
                    preview = arena.preview(action, preview_target)
                    committed = next((foe for foe in arena.enemies
                                      if foe.intent and player.pos in foe.intent.target_tiles), None)
                    if not preview.get("valid"):
                        preview_summary = f"BLOCKED · {preview.get('error') or 'cannot move there'}"
                    elif committed:
                        intent_label = arena._apparent_action(committed)
                        safe = preview.get("safe_disengage")
                        preview_summary = (f"SAFE · {action.upper()} OUT" if safe else
                                           f"TOO LATE · {intent_label} HITS FIRST")
                        if safe and preview.get("stamina_cost", 0) > 0:
                            preview_summary += f" · {preview['stamina_cost']} STA"
                    else:
                        steps = len(preview.get("path", ()))
                        preview_summary = f"{action.upper()} HERE · {steps} TILE{'S' if steps != 1 else ''}"
                    path = preview.get("path", ()) if preview.get("valid") else ()
                    for index, (x, y) in enumerate(path):
                        if not world.game_map.visible[y, x]:
                            continue
                        sx, sy = x - cx + ox, y - cy + oy
                        if not (0 <= sx < self.console.width and 0 <= sy < self.console.height):
                            continue
                        last = index == len(path) - 1
                        danger = last and preview.get("enemy_acts_first")
                        glyph = "!" if danger else "X" if last else "."
                        color = (255, 255, 255) if arena.monochrome else (
                            (245, 65, 65) if danger else (105, 235, 150))
                        self._draw_lit_cell(sx, sy, glyph, fg=color,
                                            bg=(50, 50, 50) if arena.monochrome else (15, 30, 18),
                                            sprite=None, brightness=1)
            if preview_summary:
                labels.append(("preview", hovered, preview_summary,
                               (145, 235, 175) if preview_summary.startswith("SAFE") else
                               (255, 155, 130) if preview_summary.startswith(("TOO LATE", "BLOCKED")) else
                               (225, 235, 220)))
            self._combat_text_overlay = (rows, labels)
            return
        self._combat_text_overlay = None
        deck = world.active_deck
        breach_xy = {tuple(world.hazards.cell(key)[:2]) for key in world.hazards.breaches
                     if world.hazards.cell(key)[2] == deck}
        wet_xy = {tuple(world.hazards.cell(key)[:2]) for key, amount in world.hazards.water.items()
                  if world.hazards.cell(key)[2] == deck and amount >= 25}
        fire_xy = {tuple(world.hazards.cell(key)[:2]) for key in world.hazards.fires
                   if world.hazards.cell(key)[2] == deck}
        smoke_xy = {tuple(world.hazards.cell(key)[:2]) for key, amount in world.hazards.smoke.items()
                    if world.hazards.cell(key)[2] == deck and amount >= 5}
        station_xy = {at[:2] for at in world.ship.stations.values()
                      if (at[2] if len(at) == 3 else 0) == deck}
        job_xy = {tuple(j.target[:2]) for j in world.ship.jobs if (j.construction or j.relocation)
                  and (j.construction.get("z", 0) if j.construction else
                       (j.target[2] if len(j.target) == 3 else 0)) == deck}
        relocation_xy = {tuple(j.relocation["source"][:2]) for j in world.ship.jobs if j.relocation
                         and (j.relocation["source"][2] if len(j.relocation["source"]) == 3 else 0) == deck}
        dirt_xy = {tuple(map(int, k.split(','))) for k in world.ship.dirt if deck == 0}
        for at in (station_xy | dirt_xy
                   | job_xy
                   | relocation_xy
                   | {world.ship.storage.position(p.id, world.fixtures)[:2]
                      for p in world.ship.storage.piles.values()
                      if p.z == world.active_deck}
                     | {(r["x"] + 69, r["y"] + 12) for r in world.blueprint.changes
                        if r["z"] == world.active_deck}
                   | {(c.x + 69, c.y + 12) for c in world.ship_model.components
                      if (c.kind == "stair" and c.z in (world.active_deck, world.active_deck - 1))
                       or (c.kind in ("beam", "pillar") and c.z == world.active_deck)}
                      | {(x + 69, y + 12) for x, y in breach_xy | wet_xy | fire_xy | smoke_xy}):
            x, y = at
            sx, sy = x - cx + ox, y - cy + oy
            if 0 <= sx < self.console.width and 0 <= sy < self.console.height:
                lx, ly = x - 69, y - 12
                hazard_key = world.hazards.key(lx, ly, world.active_deck)
                if hazard_key in world.hazards.breaches:
                    self._draw_lit_cell(sx, sy, "!", fg=(245, 100, 80), bg=(0, 0, 0),
                                        sprite=None, brightness=1)
                    continue
                if hazard_key in world.hazards.fires:
                    self._draw_lit_cell(sx, sy, "*", fg=(255, 95, 35), bg=(45, 12, 0),
                                        sprite=None, brightness=1)
                    continue
                if hazard_key in world.hazards.water and world.hazards.water[hazard_key] >= 25:
                    self._draw_lit_cell(sx, sy, "~", fg=(100, 180, 245), bg=(0, 0, 0),
                                        sprite=None, brightness=1)
                    continue
                if world.hazards.smoke.get(hazard_key, 0) >= 5:
                    self._draw_lit_cell(sx, sy, ":", fg=(150, 150, 150), bg=(20, 20, 20),
                                        sprite=None, brightness=1)
                    continue
                self._ship_mark(world, sx, sy, x, y, skip_actors=True)

    def _draw_world_cell(self, world, sx, sy, wx, wy):
        super()._draw_world_cell(world, sx, sy, wx, wy)
        if world.game_map.in_bounds(wx, wy):
            self._ship_mark(world, sx, sy, wx, wy)

    def _refresh_motion_cache(self, world, cache, camera_start, camera_end, offset):
        super()._refresh_motion_cache(world, cache, camera_start, camera_end, offset)
        # Job completion can remove a dirt/leak marker during held movement.
        ax, ay = cache['anchor']
        ox, oy = cache['offset']
        bw, bh = cache['buffer_size']
        deck = world.active_deck
        positions = [at[:2] for at in world.ship.stations.values()
                     if (at[2] if len(at) == 3 else 0) == deck]
        positions += [tuple(map(int, k.split(','))) for k in world.ship.dirt if deck == 0]
        positions += [tuple(world.hazards.cell(key)[:2]) for key in world.hazards.breaches
                      if world.hazards.cell(key)[2] == deck]
        positions += [tuple(world.hazards.cell(key)[:2]) for key, amount in world.hazards.water.items()
                      if world.hazards.cell(key)[2] == deck and amount >= 25]
        positions += [tuple(j.target[:2]) for j in world.ship.jobs if (j.construction or j.relocation)
                      and (j.construction.get("z", 0) if j.construction else
                           (j.target[2] if len(j.target) == 3 else 0)) == deck]
        positions += [tuple(j.relocation["source"][:2]) for j in world.ship.jobs if j.relocation
                      and (j.relocation["source"][2] if len(j.relocation["source"]) == 3 else 0) == deck]
        for x, y in [*positions,
                      *(world.ship.storage.position(p.id, world.fixtures)[:2]
                        for p in world.ship.storage.piles.values() if p.z == world.active_deck),
                       *((r["x"] + 69, r["y"] + 12) for r in world.blueprint.changes
                         if r["z"] == world.active_deck),
                       *((c.x + 69, c.y + 12) for c in world.ship_model.components
                         if (c.kind == "stair" and c.z in (world.active_deck, world.active_deck - 1))
                         or (c.kind in ("beam", "pillar") and c.z == world.active_deck))]:
            sx, sy = x - ax + ox, y - ay + oy
            if 0 <= sx < bw and 0 <= sy < bh:
                self._draw_world_cell(world, sx, sy, x, y)
                self._draw_world_overlays_at(world, sx, sy, x, y)


def panel(renderer, world, title, rows):
    renderer.render_list(world, title, rows, "Any key to return", selectable=False)
    renderer.wait_any()


def combat_history_menu(renderer, world):
    """Browse retained combat outcomes; selecting a row opens its full detail."""
    arena = world.combat
    while arena.active and arena.engine:
        events = list(arena.engine.feedback)
        rows = [f"#{item['event_id']} · t{item['tick']} · {item['text']}"
                for item in reversed(events)]
        if not rows:
            rows = ["No combat outcomes recorded yet."]
            selectable_count = 0
        else:
            selectable_count = len(rows)
        if events and events[0]["event_id"] > 1:
            rows.append("Older outcomes have expired from the bounded history.")
        rows.append("Close history")
        renderer.render_list(world, "Combat history · newest first", rows,
                             "Select an outcome for details · Esc closes")
        selected = renderer.choose(len(rows))
        if selected is None or selected >= selectable_count:
            return
        item = list(reversed(events))[selected]
        source = arena.fighters.get(item.get("source_id"))
        recipient = arena.fighters.get(item.get("recipient_id"))
        consequences = item.get("consequences", {})
        detail = [f"Tick {item['tick']} · event #{item['event_id']}",
                  f"Outcome: {item.get('outcome', item['type']).replace('_', ' ')}",
                  f"Source: {source.name if source else item.get('source_id') or 'unknown'}",
                  f"Recipient: {recipient.name if recipient else item.get('recipient_id') or 'none'}",
                  f"Body region: {item.get('body_part') or 'not applicable'}"]
        detail.extend(f"{key.replace('_', ' ')}: {value}"
                      for key, value in consequences.items())
        panel(renderer, world, item["text"], detail)


def combat_onboarding(renderer, world):
    """Offer a one-time, free first-arena controls primer."""
    if COMBAT_UI_PREFERENCES["combat_tips_dismissed"]:
        return
    options = ["Show the quick tips", "Skip this time", "Don't show again"]
    renderer.render_list(world, "First arena · optional quick tips", options,
                         "This screen is free and does not advance combat")
    choice = renderer.choose(len(options))
    if choice == 0:
        panel(renderer, world, "Combat quick tips", [
            "Threat tiles show where a committed attack will land; it will not track you.",
            "Arrows move; Shift toggles sprint. D arms a three-tile eight-way dash.",
            "Space/F attack immediately. Enter or . waits and advances combat time.",
            "G braces toward the selected foe; Q shoves. I opens full controls.",
            "H opens recent outcome history; M toggles reduced-motion feedback.",
            "The arena is temporary: Esc exits and does not save combat injuries.",
        ])
    elif choice == 2:
        COMBAT_UI_PREFERENCES["combat_tips_dismissed"] = True
        save_ui_preferences(COMBAT_UI_PREFERENCES)


def parse_profile_number(key, text):
    """Parse exact bounded profile input; return a typed number or raise ValueError."""
    if key not in NPC_PROFILE_RANGES:
        raise ValueError("This profile field is not numeric")
    low, high = NPC_PROFILE_RANGES[key]
    try:
        value = int(text.strip()) if key in NPC_PROFILE_INTEGER_FIELDS else float(text.strip())
    except (TypeError, ValueError):
        raise ValueError("Enter a valid number") from None
    if not low <= value <= high:
        raise ValueError(f"Value must be between {low} and {high}")
    return value


def edit_profile_number(renderer, world, label, key, current):
    """Collect bounded exact numeric input through SDL text events."""
    low, high = NPC_PROFILE_RANGES[key]
    buffer = ""
    error = None
    window = renderer.context.sdl_window
    window.start_text_input()
    try:
        while True:
            rows = [f"Current: {current}", f"New value: {buffer or '_'}",
                    f"Allowed: {low}–{high}", "Type a value; Enter accepts; Esc cancels."]
            if error:
                rows.append(error)
            renderer.render_list(world, f"Set exact {label}", rows,
                                 "Numeric entry does not advance combat time", selectable=False)
            renderer._present_layers()
            for event in tcod.event.wait():
                if isinstance(event, tcod.event.Quit):
                    return None
                if isinstance(event, tcod.event.TextInput):
                    allowed = "0123456789" + ("" if key in NPC_PROFILE_INTEGER_FIELDS else ".-")
                    buffer += "".join(character for character in event.text if character in allowed)
                    error = None
                elif isinstance(event, tcod.event.KeyDown):
                    if event.sym == KeySym.ESCAPE:
                        return None
                    if event.sym == KeySym.BACKSPACE:
                        buffer = buffer[:-1]
                        error = None
                    elif event.sym == KeySym.RETURN:
                        try:
                            value = parse_profile_number(key, buffer)
                            validate_npc_profile({key: value})
                            return value
                        except ValueError as validation_error:
                            error = str(validation_error)
    finally:
        window.stop_text_input()


def combat_setup(renderer, world):
    from pirates.combat_core import ARCHETYPES, PERSONALITIES, WEAPONS
    arena = world.combat
    def choose(title, rows):
        renderer.render_list(world, title, rows, "Enter selects; Esc cancels")
        return renderer.choose(len(rows))

    profile_categories = [
        ("Weapons & tactics", "Combat style inherited from the selected archetype unless customized.", [
            ("Weapon", "weapon", [None, *WEAPONS]),
            ("Off-hand weapon", "secondary_weapon", [None, *WEAPONS]),
            ("Personality", "personality", [None, *PERSONALITIES]),
            ("Tactics", "tactics", [None, "standard", "flanker"]),
            ("Starting aim", "target_part", [None, "head", "torso", "hand", "leg"]),
        ]),
        ("Movement & fitness", "Load may exceed capacity intentionally to test encumbrance.", [
            ("Speed", "speed", [None, 25, 50, 75, 100, 125, 150, 175, 200]),
            ("Movement skill", "movement_skill", [None, 0, 25, 50, 75, 100]),
            ("Stamina", "stamina", [None, 25, 50, 75, 100, 125, 150]),
            ("Maximum stamina", "max_stamina", [None, 50, 75, 100, 125, 150]),
            ("Balance", "balance", [None, 0, 25, 50, 75, 100]),
            ("Fatigue", "fatigue", [None, 0, 25, 50, 75, 100]),
            ("Carried load (kg)", "carried_weight_kg", [None, 0, 5, 10, 15, 20, 24]),
            ("Carry capacity (kg)", "carry_capacity_kg", [None, 10, 15, 24, 30, 40]),
        ]),
        ("Protection & condition", "Starting damage uses the arena's coarse body-part model.", [
            ("Strength", "strength", [None, 0, 2, 4, 6, 8]),
            ("Armor", "armor", [None, 0, 1, 2]),
            ("Shield", "shield", [None, True, False]),
            ("Starting head condition", "condition_head", [None, "healthy", "wounded", "maimed", "critical"]),
            ("Starting torso condition", "condition_torso", [None, "healthy", "wounded", "maimed", "critical"]),
            ("Starting left arm condition", "condition_left_arm", [None, "healthy", "wounded", "maimed", "critical", "severed"]),
            ("Starting right arm condition", "condition_right_arm", [None, "healthy", "wounded", "maimed", "critical", "severed"]),
            ("Starting left leg condition", "condition_left_leg", [None, "healthy", "wounded", "maimed", "critical", "severed"]),
            ("Starting right leg condition", "condition_right_leg", [None, "healthy", "wounded", "maimed", "critical", "severed"]),
        ]),
        ("Supplies & display", "Supply overrides affect spawned opponents, not the campaign inventory.", [
            ("Morale", "morale", [None, 0, 25, 50, 75, 100]),
            ("Perception", "perception", [None, 4, 6, 8, 10, 12, 15]),
            ("Loaded ammo", "ammo", [None, 0, 1, 2]),
            ("Spare shots", "spare_shots", [None, 0, 2, 4, 6, 10]),
            ("Smoke charges", "smoke_charges", [None, 0, 1, 2, 3]),
            ("Glyph", "glyph", [None, "d", "b", "s", "g", "h", "p", "q", "m"]),
        ]),
    ]
    profile_presets = {
        "Agile duelist": {"weapon": "sword", "tactics": "flanker", "speed": 125,
                          "movement_skill": 50, "shield": False},
        "Armored guard": {"weapon": "spear", "speed": 75, "strength": 4,
                          "armor": 2, "shield": True, "stamina": 100,
                          "max_stamina": 100},
        "Pistol marksman": {"weapon": "gun", "personality": "sniper",
                            "target_part": "head", "ammo": 1, "spare_shots": 6},
    }

    def customize_profile():
        draft = dict(arena.npc_overrides)
        archetype_data = ARCHETYPES[arena.archetype]

        def shown(value):
            if value is None:
                return "Use inherited value"
            if isinstance(value, bool):
                return "On" if value else "Off"
            return str(value)

        def resolved(key, value):
            if value is not None:
                return value, "explicit profile"
            defaults = {
                "weapon": arena.weapon_override or archetype_data["weapon"],
                "secondary_weapon": archetype_data.get("secondary_weapon"),
                "personality": arena.personality or archetype_data["personality"],
                "speed": arena.speed or archetype_data["speed"],
                "strength": arena.strength,
                "armor": archetype_data["armor"],
                "shield": archetype_data.get("shield", False),
                "tactics": archetype_data.get("tactics", "standard"),
            }
            return defaults.get(key, {"stamina": 100, "max_stamina": 100,
                                      "balance": 100, "fatigue": 0,
                                      "morale": 100, "perception": 10,
                                      "carried_weight_kg": 0, "carry_capacity_kg": 24,
                                      "target_part": "torso", "ammo": 1,
                                      "spare_shots": 6, "smoke_charges": 0,
                                      "glyph": archetype_data["glyph"]}.get(
                                          key, "healthy" if key.startswith("condition_") else None)), \
                "archetype / arena default"

        while True:
            rows = [f"{name} · {description}"
                    for name, description, _ in profile_categories]
            rows.extend(["Load preset into draft…",
                         "Reset all custom values to inherited defaults",
                         "Review draft and apply…", "Discard draft and return"])
            choice = choose("Enemy profile draft · shared by every spawned opponent", rows)
            if choice is None or choice == len(rows) - 1:
                return
            if choice == len(profile_categories):
                presets = [*profile_presets, "Back"]
                preset_choice = choose("Preset · replaces current draft values", presets)
                if preset_choice is not None and preset_choice < len(profile_presets):
                    draft = dict(profile_presets[presets[preset_choice]])
                continue
            if choice == len(profile_categories) + 1:
                draft.clear()
                continue
            if choice == len(profile_categories) + 2:
                try:
                    validate_npc_profile(draft)
                except ValueError as error:
                    panel(renderer, world, "Profile needs correction", [str(error)])
                    continue
                review = []
                for _, _, fields in profile_categories:
                    for label, key, _ in fields:
                        value, source = resolved(key, draft.get(key))
                        review.append(f"{label}: {shown(value)} · {source}")
                effective = {key: resolved(key, draft.get(key))[0]
                             for _, _, fields in profile_categories
                             for _, key, _ in fields}
                warnings = []
                weapon = effective.get("weapon")
                two_handed = bool(weapon in WEAPONS and WEAPONS[weapon].get("two_handed"))
                severe_arm = any(effective.get(f"condition_{side}_arm") in {"critical", "severed"}
                                 for side in ("left", "right"))
                if two_handed and severe_arm:
                    warnings.append("Warning: starting arm condition restricts the two-handed weapon.")
                if weapon in {"gun", "rifle", "shotgun"} and not effective.get("ammo") \
                        and not effective.get("spare_shots"):
                    warnings.append("Warning: firearm starts empty with no spare shots.")
                severe_leg = any(effective.get(f"condition_{side}_leg") in {"critical", "severed"}
                                 for side in ("left", "right"))
                if severe_leg and effective.get("carried_weight_kg", 0) > effective.get("carry_capacity_kg", 24):
                    warnings.append("Warning: severe leg condition and over-capacity load compound movement penalties.")
                review.extend(["", "Changes apply to every newly spawned opponent.",
                               "Body/hand condition remains a coarse limb model."])
                if warnings:
                    review.extend(["", *warnings])
                panel(renderer, world, "Review enemy profile · no changes applied yet", review)
                actions = ["Apply and restart current arena" if arena.active else "Apply for next arena",
                           "Continue editing draft", "Discard draft"]
                selected = choose("Apply this shared profile?", actions)
                if selected == 0:
                    old_profile = arena.npc_overrides
                    arena.npc_overrides = dict(draft)
                    if arena.active and not arena.reset(world):
                        arena.npc_overrides = old_profile
                        panel(renderer, world, "Profile not applied", ["Arena could not be restarted."])
                    return
                if selected == 2:
                    return
                continue
            fields = profile_categories[choice][2]
            while True:
                rows = []
                for label, key, _ in fields:
                    value, source = resolved(key, draft.get(key))
                    rows.append(f"{label}: {shown(value)} · {source}")
                rows.append("Back to profile categories")
                selected = choose(profile_categories[choice][0], rows)
                if selected is None or selected == len(fields):
                    break
                label, key, values = fields[selected]
                value, source = resolved(key, draft.get(key))
                options = [shown(item) + (" · inherited" if item is None else "")
                           for item in values]
                if key in NPC_PROFILE_RANGES:
                    options.append("Set exact numeric value…")
                picked = choose(f"{label} · resolved {shown(value)} ({source})", options)
                if picked is not None:
                    if key in NPC_PROFILE_RANGES and picked == len(values):
                        exact_value = edit_profile_number(renderer, world, label, key, value)
                        if exact_value is not None:
                            draft[key] = exact_value
                    else:
                        draft[key] = values[picked]

    while True:
        row = choose("Arena setup (changes reset the fight)", [
            f"Enemy: {arena.archetype}", f"Count: {arena.count}",
            f"Speed: {arena.speed or 'archetype default'}", f"Strength bonus: {arena.strength}",
            f"Personality: {arena.personality or 'archetype default'}",
            f"Pillar cover: {arena.cover}", f"Weapon: {arena.weapon_override or 'archetype default'}",
            f"Tactical doors: {arena.tactical_doors}", f"Floor hazards: {arena.tactical_hazards}",
            f"Squad composition: {arena.squad_mode}",
            "Customize enemy stats / equipment / condition…",
            "Apply / start arena"])
        if row is None:
            return
        if row == 11:
            if arena.active:
                arena.reset(world)
            else:
                arena.start(world)
            return
        if row == 10:
            customize_profile()
            continue
        choices = {0: list(ARCHETYPES), 1: [1, 2, 3, 5], 2: [None, 75, 100, 125, 150],
                   3: [0, 2, 4], 4: [None, *PERSONALITIES], 6: [None, *WEAPONS]}
        if row == 5:
            arena.cover = not arena.cover
            continue
        if row == 7:
            arena.tactical_doors = not arena.tactical_doors
            continue
        if row == 8:
            arena.tactical_hazards = not arena.tactical_hazards
            continue
        if row == 9:
            arena.squad_mode = "mixed" if arena.squad_mode == "uniform" else "uniform"
            continue
        values = choices[row]
        selected = choose("Choose arena parameter", [str(v) if v is not None else "Archetype default" for v in values])
        if selected is not None:
            setattr(arena, {0: "archetype", 1: "count", 2: "speed", 3: "strength", 4: "personality", 6: "weapon_override"}[row], values[selected])


def combat_debug(renderer, world):
    arena = world.combat
    rows = ["Inspect actors / utility scores", "Toggle threat overlay (debug only)",
            "Toggle monochrome", "Toggle single-event pause/step mode",
            "Heal captain", "Kill selected enemy", "Spawn configured archetype",
            "Give one throwable bottle", "Dump replay/state to Temp/opencode", "Reset same seed"]
    renderer.render_list(world, "Arena debug (cheats; not normal combat)", rows, "Enter selects; Esc cancels")
    selected = renderer.choose(len(rows))
    if selected == 0:
        panel(renderer, world, "Actor state and utility scores", arena.debug_rows())
    elif selected == 1:
        arena.show_intents = not arena.show_intents
    elif selected == 2:
        arena.monochrome = not arena.monochrome
    elif selected == 3:
        arena.debug_step = not arena.debug_step
    elif selected == 4:
        a = arena.fighters["captain"]
        a.hp = a.max_hp
        a.statuses.clear()
    elif selected == 5 and arena.target():
        a = arena.target()
        a.hp, a.intent, a.state = 0, None, "dead"
        a.token += 1
    elif selected == 6:
        arena.spawn(arena.archetype)
    elif selected == 7:
        a = arena.fighters["captain"]
        a.statuses["bottles"] = a.statuses.get("bottles", 0) + 1
    elif selected == 8:
        output = Path("C:/Users/Warp/AppData/Local/Temp/opencode/pirates-combat-replay.json")
        arena.dump(output)
        arena.engine.say(f"Saved replay/state: {output}")
    elif selected == 9:
        arena.reset(world)


def inspect(renderer, world, x, y):
    gm = world.game_map
    if not gm.in_bounds(x, y) or not gm.visible[y, x]:
        world.message("That tile is out of sight.")
        return
    rows = [T.tile_name(gm.tile_id(x, y))]
    rows += [o["name"] for o in world.objects_at.get((x, y), [])]
    rows += [f"{i['name']} x{i.get('quantity', 1)}" for i in world.ground_at(x, y)]
    rows += ["", f"Area: {world.room_name(x, y, world.active_deck)}"]
    pile = next((p for p in world.ship.storage.piles.values()
                 if world.ship.storage.position(p.id, world.fixtures)[:2] == (x, y)
                 and p.z == world.active_deck), None)
    if pile:
        from pirates.ship_storage import bundle_mass
        rows += [f"Stockpile {pile.id}: {pile.contents or 'empty'}",
                 f"Mass/capacity: {bundle_mass(pile.contents)}/{pile.capacity_kg} kg"]
    for sailor in world.ship.crew:
        if (sailor.x, sailor.y, sailor.z) == (x, y, world.active_deck):
            rows += ["", *profile_rows(sailor, world)]
    for merchant in world.economy.merchants():
        if (merchant.x, merchant.y) == (x, y):
            record = MERCHANTS[merchant.id]
            rows += ["", record["title"], *record["dialogue"], "Stand nearby and press Enter to talk or trade."]
    panel(renderer, world, f"Inspect ({x}, {y})", rows)


def _inspect_action(renderer, world, action):
    if action[0] == "inspect_at":
        x, y = action[1], action[2]
    else:
        x, y = world.player.x, world.player.y
    inspect(renderer, world, x, y)


def resolve_launch_world(args, parser):
    generated_options = (args.seed is not None or args.npc_people is not None
                         or args.npc_organizations is not None or args.npc_vessels is not None
                         or args.history_days is not None)
    if generated_options and not args.new:
        parser.error("--seed and NPC settings require --new")
    if args.replace_slot and (args.scenario is None or not args.new):
        parser.error("--replace-slot requires --scenario lawful-trader-mvp and --new")
    if args.scenario:
        if generated_options:
            parser.error("Generated NPC settings cannot be used with an authored scenario")
        if args.smoke_render:
            return World.new_authored_mvp()
        if args.new:
            if MVP_SAVE_PATH.exists() and not args.replace_slot:
                parser.error(f"{MVP_SAVE_PATH} already exists; resume it or pass --replace-slot to replace it")
            world = World.new_authored_mvp()
            world.save_path = MVP_SAVE_PATH
            return world
        if MVP_SAVE_PATH.exists():
            try:
                return World.load(MVP_SAVE_PATH, expected_scenario_id=args.scenario)
            except (ValueError, KeyError, TypeError, OSError) as error:
                parser.exit(1, f"Cannot load {MVP_SAVE_PATH}: {error}\nUse --new --replace-slot to replace this slot.\n")
        return World.new_authored_mvp()
    if args.smoke_render:
        return World()
    if not args.new and SAVE_PATH.exists():
        try:
            return World.load(SAVE_PATH, expected_scenario_id=None)
        except (ValueError, KeyError, TypeError, OSError) as error:
            parser.exit(1, f"Cannot load {SAVE_PATH}: {error}\nUse --new for a fresh game.\n")
    settings = {key: value for key, value in {
        "notable_people": args.npc_people,
        "organization_count": args.npc_organizations,
        "vessel_count": args.npc_vessels,
        "history_days": args.history_days,
    }.items() if value is not None}
    try:
        return World.new_generated(args.seed, settings)
    except ValueError as error:
        parser.error(str(error))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--new", action="store_true",
                        help="Start a fresh game (generated unless --scenario is selected)")
    parser.add_argument("--scenario", choices=("lawful-trader-mvp",),
                        help="Resume or start an authored scenario in its own save slot")
    parser.add_argument("--replace-slot", action="store_true",
                        help="Allow --new --scenario to replace that scenario's save file")
    parser.add_argument("--seed", help="Reproducible seed for a generated --new world")
    parser.add_argument("--npc-people", type=int, help="Number of notable generated NPCs (default: 24)")
    parser.add_argument("--npc-organizations", type=int, help="Number of generated NPC organizations (default: 4)")
    parser.add_argument("--npc-vessels", type=int, help="Number of generated NPC vessels (default: 8)")
    parser.add_argument("--history-days", type=int, help="Headless NPC prehistory days, 0-365 (default: 45)")
    parser.add_argument("--smoke-render", action="store_true", help="Render, exercise movement and exit without saving")
    parser.add_argument("--screenshot", type=Path, help="Save a native SDL screenshot")
    args = parser.parse_args()
    world = resolve_launch_world(args, parser)
    renderer = PiratesRenderer(settings=RenderSettings(
        world_tileset="ascii", world_font_size=20,
        world_cell_width=20, world_cell_height=24))
    try:
        renderer.init_for(world.game_map.width, world.game_map.height)
        if args.smoke_render:
            from pirates.ship_building import queue
            queue(world, (78, 27), "build", "door")
            world.spaces.designate(world, world.player.x, world.player.y, "working deck")
        renderer.render(world)
        if args.smoke_render:
            def smoke_step():
                for dx, dy in ((-1, 0), (0, 1), (0, -1), (1, 0)):
                    x, y = world.player.x + dx, world.player.y + dy
                    if world.game_map.is_walkable(x, y) and not any((c.x, c.y) == (x, y) for c in world.ship.crew):
                        assert world.move(dx, dy)
                        return
                raise AssertionError("Smoke movement is blocked in every direction")
            start = world.player.x, world.player.y
            smoke_step()
            renderer.animate_move(world, start, (world.player.x, world.player.y))
            renderer.render(world)
            from pirates.ship_spaces import report_rows
            renderer.render_list(world, "Room report", report_rows(world, world.player.x, world.player.y), selectable=False)
            renderer.render(world)
            renderer._present_layers()
            # Check both zoom rebuilds, then held-motion cache reuse on the pier.
            for direction in (1, -1, -1, 1):
                renderer.zoom(direction, world.game_map.width, world.game_map.height)
                renderer.render(world)
            renderer._move_repeater.press(KeySym.RIGHT, ("move", 1, 0), time.perf_counter())
            for _ in range(3):
                start = world.player.x, world.player.y
                smoke_step()
                renderer.animate_move(world, start, (world.player.x, world.player.y))
            renderer._move_repeater.cancel()
            renderer._finish_motion_cache()
            renderer.render(world)
            renderer._present_layers()
            if args.screenshot:
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(args.screenshot)
            print("Urizen SDL render, movement tween and modal smoke passed.")
            return
        while True:
            global COMBAT_INPUT_MODE, COMBAT_DASH_ARMED
            COMBAT_INPUT_MODE = world.combat.active
            renderer.render(world)
            action = renderer.wait_action()
            name = action[0]
            if world.combat.active:
                if name in ("combat_arena", "quit"):
                    COMBAT_DASH_ARMED = False
                    world.combat.exit(world)
                elif name == "move":
                    direction = tuple(action[1:])
                    if COMBAT_DASH_ARMED and (world.combat.guided_duel_available
                                              or world.combat.guided_duel_engaged):
                        COMBAT_DASH_ARMED = False
                        world.combat.act(world, "duel_dash", (*direction, 3))
                    else:
                        execute_combat_movement(renderer, world, "move", direction)
                elif name == "combat_run":
                    execute_combat_movement(renderer, world, "run", tuple(action[1:]))
                elif name == "combat_dash":
                    direction = tuple(action[1:]) if len(action) > 1 else None
                    COMBAT_DASH_ARMED = False
                    if (world.combat.guided_duel_available
                            or world.combat.guided_duel_engaged) and direction:
                        world.combat.act(world, "duel_dash", (*direction, 3))
                    else:
                        execute_combat_movement(renderer, world, "dash", direction)
                elif name == "combat_dash_arm":
                    COMBAT_DASH_ARMED = True
                    world.message("DASH ARMED: press an arrow key to commit, Esc to cancel.")
                elif name == "combat_dash_cancel":
                    world.message("Dash cancelled.")
                elif name == "combat_target_attack":
                    world.combat.selected_target = action[1]
                    world.combat.request(world, "attack")
                elif name == "combat_run_toggle":
                    world.combat.act(world, "toggle_run")
                elif name == "combat_wait":
                    world.combat.act(world, "wait")
                elif name == "combat_help":
                    actor = world.combat.fighters["captain"]
                    if (world.combat.guided_duel_engaged
                            or world.combat.guided_duel_available):
                        action_help = [
                            "GUIDED DUEL: Space Light · F Heavy · G Parry · D+direction Dash · J Dash-strike",
                            "These five actions use the deterministic close-duel rules; mouse bar matches.",
                        ]
                    elif WEAPONS[actor.weapon]["reload"]:
                        action_help = [
                            "RANGED: R Aim · Space/F Fire · T Reload · G Guard · C More actions",
                            "Aim locks a visible line; Fire requires aim and loaded ammunition.",
                        ]
                    else:
                        action_help = [
                            "GROUP / MELEE: Space Attack · F Heavy · G Guard · D+direction Dash · C More",
                            "These encounters use the legacy group-combat rules, not the guided duel kernel.",
                        ]
                    panel(renderer, world, "Combat controls", [
                        "Arrows move | tap Shift to toggle sprint; HUD shows ON/OFF",
                        "` / Caps also toggle sprint | Enter / . wait (advances simulated time)",
                        *action_help,
                        "D then arrow dash | Ctrl+arrow dash | numpad 1/3/7/9 diagonal dash",
                        "1 head · 2 torso · 3 arms · 4 legs · 5 cycle · 6 hand target (left-arm region)",
                        "Tab target | . wait | C/right-click more actions | Esc leaves",
                        "Actions happen immediately. Hover previews path/time/stamina/risk.",
                        "Left-click selects only · right-click attacks the selected target.",
                        "Click a tile to move; Shift-click or arm D to dash, including diagonally.",
                        "H browses retained outcomes; M toggles reduced-motion feedback (saved).",
                        "Monochrome: ! threat, x committed hit, ? aiming, o recovery.",
                    ])
                elif name == "combat_history":
                    combat_history_menu(renderer, world)
                elif name == "combat_motion_toggle":
                    COMBAT_UI_PREFERENCES["combat_reduced_motion"] = not COMBAT_UI_PREFERENCES[
                        "combat_reduced_motion"]
                    save_ui_preferences(COMBAT_UI_PREFERENCES)
                    world.message("Reduced-motion feedback " + (
                        "ON: outcome labels stay still." if COMBAT_UI_PREFERENCES[
                            "combat_reduced_motion"] else "OFF: outcome labels rise."))
                elif name == "combat_context":
                    if len(action) >= 3:
                        clicked = world.combat.engine.at((action[1], action[2]))
                        if clicked and clicked.id != "captain":
                            world.combat.selected_target = clicked.id
                    options = ["Inspect target", "Open/close door", "Deploy smoke", "Throw bottle",
                               "Aim / fire", "Reload", "Switch: cutlass", "Switch: fists",
                               "Switch: pistol", "Target: head", "Target: torso", "Target: arms",
                               "Target: legs", "Bandage", "Safe sidestep"]
                    renderer.render_list(world, "Combat context", options,
                                         "Choose an action · Esc cancels", selectable=True)
                    choice = renderer.choose(len(options))
                    if choice == 0:
                        target = world.combat.target()
                        if target:
                            panel(renderer, world, "Enemy inspection", [world.combat.look(target.id)])
                    elif choice == 1:
                        world.combat.request(world, "door")
                    elif choice == 2:
                        world.combat.request(world, "smoke")
                    elif choice == 3:
                        world.combat.request(world, "throw")
                    elif choice == 4:
                        world.combat.request(world, "aim")
                    elif choice == 5:
                        world.combat.request(world, "reload")
                    elif choice in (6, 7, 8):
                        weapon = ("sword", "brawl", "gun")[choice - 6]
                        world.combat.request(world, "style", weapon)
                    elif choice in (9, 10, 11, 12):
                        part = ("head", "torso", "arm", "leg")[choice - 9]
                        world.combat.act(world, "part", part)
                    elif choice == 13:
                        world.combat.request(world, "bandage")
                    elif choice == 14:
                        world.combat.request(world, "dodge")
                elif name == "inspect_at":
                    x, y = action[1], action[2]
                    target = world.combat.engine.at((x, y))
                    if target and target.id != "captain":
                        world.combat.select_at(world, x, y)
                    else:
                        player = world.combat.fighters["captain"]
                        dx, dy = x - player.x, y - player.y
                        steps = max(abs(dx), abs(dy))
                        if dx == 0 or dy == 0 or COMBAT_DASH_ARMED or (len(action) > 3 and action[3]):
                            direction = (0 if dx == 0 else (1 if dx > 0 else -1),
                                         0 if dy == 0 else (1 if dy > 0 else -1))
                            if COMBAT_DASH_ARMED or (len(action) > 3 and action[3]):
                                if steps <= 3:
                                    COMBAT_DASH_ARMED = False
                                    if (world.combat.guided_duel_available
                                            or world.combat.guided_duel_engaged):
                                        world.combat.act(world, "duel_dash", (*direction, steps))
                                    else:
                                        execute_combat_movement(renderer, world, "dash",
                                                                (*direction, steps), sprint_burst=False)
                                else:
                                    world.message("Dash landing must be within three tiles.")
                            elif player.move_mode == "run" and steps == 2:
                                execute_combat_movement(renderer, world, "run", direction,
                                                        sprint_burst=False)
                            elif steps == 1 and (dx == 0 or dy == 0):
                                execute_combat_movement(renderer, world, "move", direction,
                                                        sprint_burst=False)
                            else:
                                world.message("Click one tile, or arm dash with D for up to three.")
                        else:
                            world.message("Click adjacent tiles to move; dash accepts diagonal paths.")
                elif name == "combat_style":
                    world.combat.act(world, "style", action[1])
                elif name == "combat_part":
                    world.combat.act(world, "part", action[1])
                elif name in ("combat_attack", "combat_aim", "combat_reload", "combat_dodge",
                              "combat_guard", "combat_throw", "combat_hide", "combat_heavy",
                              "combat_shove", "combat_bandage"):
                    operation = {"combat_attack": "attack", "combat_aim": "aim",
                                 "combat_reload": "reload", "combat_dodge": "dodge",
                                 "combat_guard": "guard", "combat_throw": "throw",
                                 "combat_hide": "hide", "combat_heavy": "heavy",
                                 "combat_shove": "shove", "combat_bandage": "bandage"}[name]
                    world.combat.request(world, operation)
                elif name in ("combat_duel_light", "combat_duel_heavy",
                              "combat_duel_parry", "combat_duel_dash_strike"):
                    if (world.combat.guided_duel_available
                            or world.combat.guided_duel_engaged):
                        operation = {"combat_duel_light": "duel_light",
                                     "combat_duel_heavy": "duel_heavy",
                                     "combat_duel_parry": "duel_parry",
                                     "combat_duel_dash_strike": "duel_dash_strike"}[name]
                        world.combat.act(world, operation)
                    else:
                        # The physical keys Space/F/G are shared by the legacy
                        # quick bar; while no guided exchange is actionable,
                        # keep them useful instead of sending them to a gated
                        # duel route that can only return a blocker message.
                        operation = legacy_action_for_duel_key(name)
                        if operation:
                            world.combat.request(world, operation)
                elif name == "combat_target":
                    targets = [e for e in world.combat.enemies if e.hp > 0 and not e.fled]
                    if targets:
                        ids = [e.id for e in targets]
                        current = ids.index(world.combat.selected_target) if world.combat.selected_target in ids else -1
                        world.combat.selected_target = ids[(current + 1) % len(ids)]
                elif name == "combat_body":
                    world.combat.act(world, "body")
                elif name == "combat_door":
                    world.combat.act(world, "door")
                elif name == "combat_smoke":
                    world.combat.act(world, "smoke")
                elif name == "combat_look":
                    target = world.combat.target()
                    if target:
                        panel(renderer, world, "Enemy / exact intent", [world.combat.look(target.id)])
                elif name == "combat_reset":
                    world.combat.reset(world)
                elif name == "combat_setup":
                    combat_setup(renderer, world)
                elif name == "combat_debug":
                    combat_debug(renderer, world)
                elif name == "combat_tick_step":
                    world.combat.engine.advance_debug_ticks(1)
                    p = world.combat.fighters["captain"]
                    world.player.x, world.player.y = p.pos
                    world.refresh()
                elif name == "combat_fast_step":
                    world.combat.engine.advance_debug_ticks(100)
                    p = world.combat.fighters["captain"]
                    world.player.x, world.player.y = p.pos
                    world.refresh()
                elif name == "wait":
                    world.combat.act(world, "wait")
                elif name in ("inventory", "help", "inspect"):
                    panel(renderer, world, "Arena kit and combat guide", [
                    *world.combat.hud_lines(world), "", "HOW TURNS WORK",
                        "Looking, clicking targets and this guide are free.",
                        "Actions execute immediately and spend energy/ticks. Fast enemies act sooner.",
                        "An unavailable action never spends time. Windup locks you in until recovery ends.",
                        "Space light / F heavy: preview a melee pattern. Committed tiles do not track movement.",
                        "C/right-click opens context for aim, reload, doors, smoke, bottle, weapon and target part.",
                        "G braces/parries toward selected foe. Ranged shots cannot be parried.",
                        "Q shoves: interrupts windup, pushes and breaks shield stance.",
                        ". or Enter waits. Context includes bandage/dodge; Escape leaves combat.",
                        "Weapons are loaned. F12 configures archetype, count, profile, cover, doors, hazards, squads.",
                        "F10 debug: pause mode, overlays, heal/kill/spawn, dump and monochrome.",
                        "N advances one tick; Shift+N advances up to 100 ticks, stopping at your next decision.",
                        "Backspace resets same configuration/seed. Timeline lists upcoming events and threats.",
                        "P throws a debug-supplied bottle. No random hit rolls or skill-gated damage.",
                        "Arena gear is separate from your ship inventory. No permanent injuries."])
                else:
                    world.message("Combat drill: use the combat controls shown in the sidebar.")
                continue
            if name == "combat_arena":
                from pirates.duel_ui import run_sword_duel
                if run_sword_duel(renderer, world) == "quit":
                    world.save()
                    break
                continue
            if name == "combat_setup":
                from pirates.duel_ui import run_sword_duel
                if run_sword_duel(renderer, world) == "quit":
                    world.save()
                    break
                continue
            if name == "quit":
                try:
                    world.save()
                except OSError as error:
                    world.message(f"Cannot quit safely: save failed ({error}). Try F5 again.")
                    continue
                break
            if name == "move":
                start = world.player.x, world.player.y
                if world.move(*action[1:]):
                    renderer.animate_move(world, start, (world.player.x, world.player.y))
            elif name == "zoom":
                renderer.zoom(action[1], world.game_map.width, world.game_map.height)
            elif name == "deck":
                world.move_vertical(action[1])
            elif name == "wait":
                world.advance(TICKS_PER_SECOND)
                world.message("You wait a moment.")
            elif name == "context_menu":
                context_menu(renderer, world)
            elif name == "open_container":
                world.toggle_door()
            elif name in ("tutorial_journal", "progression"):
                crew_menu(renderer, world)
            elif name == "tutorial_guidance":
                panel(renderer, world, "Ship ledger", ship_rows(world))
            elif name == "tutorial_track":
                time_menu(renderer, world)
            elif name == "activities":
                chores_menu(renderer, world)
            elif name == "activity_history":
                events_menu(renderer, world)
            elif name == "blueprint":
                from pirates.management import blueprint_menu
                blueprint_menu(renderer, world)
            elif name == "voyage":
                from pirates.management import voyage_menu
                voyage_menu(renderer, world)
            elif name == "get":
                world.pickup()
            elif name in ("pack", "drop"):
                rows = [f"{i['name']} x{i.get('quantity', 1)}" for i in world.inventory]
                if name == "pack":
                    panel(renderer, world, "Captain's satchel", rows or ["Empty. G picks up supplies; D drops them."])
                elif rows:
                    renderer.render_list(world, "Drop which item?", rows, "Enter drops; Esc cancels")
                    index = renderer.choose(len(rows))
                    if index is not None:
                        world.drop(index)
                else:
                    world.message("Your satchel is empty.")
            elif name in ("inspect", "inspect_at"):
                _inspect_action(renderer, world, action)
            elif name == "save_game":
                try:
                    world.save()
                except OSError as error:
                    world.message(f"Save failed: {error}")
            elif name == "load_game":
                try:
                    world = World.load(world.save_path, expected_scenario_id=world.scenario_id)
                except (ValueError, KeyError, TypeError, OSError) as error:
                    world.message(f"Load failed: {error}")
            elif name == "help":
                panel(renderer, world, "Controls", [
                    "Arrows or HJKL: move (hold to repeat)", ". / keypad 5: wait",
                    "Enter: nearby crew / door interactions   O: toggle door", "G: pick up supplies at your feet",
                    "I: inventory   D: drop an item", "E: inspect your tile; click: inspect visible tile",
                    "+ / -: zoom   F5: save   F9: load", "Esc / Q: save and quit",
                    "F2: crew profiles and labor priorities (0 off, 1 high, 5 low)",
                    "A: chore board, priorities, cancellations and blocked reasons",
                    "F3: ship ledger   F4: pass 10s / 1m / 10m   F6: crew events",
                    "V: consult officers, order a voyage, set free-time and watch sea chart.",
                    "Menus freeze time; movement / waiting advances the whole crew.",
                    "Needs preempt work; carried supplies and progress are retained.",
                    "$: merchant. Stand within two tiles, then Enter to talk / buy / sell.",
                    "Enter > Trade cargo / crew equipment: allocate supplies at the hold.",
                    ("Authored MVP: fixed sample NPCs; strategic world simulation is disconnected."
                     if world.scenario_id == "lawful-trader-mvp" else
                     "Enter > Island guide / coastal passage: arrange travel at helm / harbor office."),
                    "F8: ship blueprint editor — B cycle, Space mark, C copy, M mirror, R rotate, Z undo, Enter queue.",
                    "C: shipboard combat arena. Combat: click target, Space attack, 1-3 styles, R aim, T reload, B dodge, G guard, P throw, Y hide; F11 exit."])
            else:
                world.message("That system is not part of the foundation yet. F1 lists available controls.")
    finally:
        renderer.shutdown()


if __name__ == "__main__":
    main()
