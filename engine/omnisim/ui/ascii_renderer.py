"""World renderer on tcod with ASCII fallback and optional pixel tilesets.

Later: TilesheetRenderer implements the same methods; zero sim changes.

Camera: viewport follows the player, clamped to the map (centered when the
viewport is bigger than the map). Zoom reloads the truetype font at a new
pixel size so the window stays fixed while cells grow/shrink. RetroDays sprites
share the font atlas; the pixel-positioned UI remains independent.
Light: legacy themes multiply artwork by the lantern/torch brightness field.
EverRogue swaps chroma toward a green-gray palette on a constant green canvas;
texture luminance and opacity stay intact. Unexplored geometry is never drawn.
"""
from __future__ import annotations
import math
import time
from collections import deque
from dataclasses import dataclass
import numpy as np
import tcod
import tcod.event
import tcod.sdl.video
from PIL import Image, ImageDraw, ImageFont
from tcod.sdl.render import BlendMode

from omnisim.ui.overlay_renderer import OverlayRenderer
from omnisim.ui.render_settings import RenderSettings
from omnisim.ui.sidebar import build_sidebar
from omnisim.ui.ui_renderer import UIRenderer
from omnisim.ui.world_renderer import WorldRenderer
from omnisim.world import tile as T

WIN_PX = (1280, 800)
UI_ROWS = 2
SIDEBAR_PX = 360  # sidebar target width in pixels; tile count varies by zoom
SIDEBAR_MIN = 13
MEMORY_DIM = 0.30

# (name, tile_px_w, tile_px_h) — window stays 1280x800, grids become:
# Far 106x36, Mid 80x28, Near 53x20 (minus UI_ROWS of viewport each).
# Landscape: the map viewport is always wider than tall. Order matters:
# +1 direction = zoom IN toward Near.
ZOOM_LEVELS: list[tuple[str, int, int]] = [
    ("Far", 12, 22),
    ("Mid", 16, 28),
    ("Near", 24, 40),
]

# --- On-map feedback: floating attempt messages and the stamina bar --------
#
# While Waseem works a tree, the water, or a station, no interface appears.
# Instead the feedback lives over the world itself, above the thing being
# worked: each attempt's message, its energy cost, and its XP gain slide up
# from the tile and fade away, while a small constant bar shows the stamina
# he has left. Pure presentation: the renderer only draws what the sim spawns.

FLOAT_RISE_TILES = 1.6       # how far a float climbs across its life
FLOAT_FADE_FROM = 0.55       # start fading after this share of its life
FLOAT_LANE_GAP = 0.60        # vertical spacing between parallel float lanes
FLOAT_STAGGER_S = 0.14       # delay between floats spawned into the same lane
FLOAT_TICK_S = 1 / 30        # idle frame budget while floats are alive
ACTIVITY_BAR_TILES = 3.0     # stamina bar width, in world tiles
CAPTION_WIDTH_TILES = 11    # odd width centers ink below the speaker's tile

# --- Cinematic dialogue: zoom + speech bubbles -----------------------------
#
# A story dialogue leans the camera in toward the speakers and floats a
# bubble over whoever is talking. The zoom does NOT rebuild the console
# (that would snap fonts); instead the already-baked world texture is
# cropped around the speakers and scaled up over the tween, the same
# trick the move animation uses for its source rect. Bubbles and the
# narration caption are drawn through the same crop transform, so they
# ride the zoom instead of fighting it.

DIALOGUE_ZOOM_SCALE = 2.0    # how close the camera leans during a dialogue
CINE_EASE = 5.0              # per-second approach rate of the zoom tween
CINE_SNAP = 0.01             # close enough to 1.0 to call the zoom-out done
BUBBLE_GAP_TILES = 0.35      # gap between a bubble and its speaker, in tiles
BUBBLE_MAX_PX = 520          # bubble text wrap width cap, in window pixels


@dataclass
class WorldFloat:
    """Work feedback rises; grounded dialogue stays on its assigned map rows."""

    text: str
    color: tuple[int, int, int]
    world_xy: tuple[int, int]
    born: float
    ttl: float
    lane: int = 0
    grounded: bool = False
    tile_row: int = 1
    tile_rows: int = 1


MOVE_KEYS: dict[tcod.event.KeySym, tuple[int, int]] = {
    tcod.event.KeySym.UP: (0, -1),
    tcod.event.KeySym.DOWN: (0, 1),
    tcod.event.KeySym.LEFT: (-1, 0),
    tcod.event.KeySym.RIGHT: (1, 0),
    tcod.event.KeySym.K: (0, -1),
    tcod.event.KeySym.J: (0, 1),
    tcod.event.KeySym.H: (-1, 0),
    tcod.event.KeySym.L: (1, 0),
}


def inventory_action_for_key(sym):
    """Map inventory-selector keys to actions; world controls remain separate."""
    actions = {
        tcod.event.KeySym.RETURN: "menu",
        tcod.event.KeySym.KP_ENTER: "menu",
        tcod.event.KeySym.V: "view",
        tcod.event.KeySym.I: "menu",
        tcod.event.KeySym.E: "examine",
        tcod.event.KeySym.O: "examine_contents",
        tcod.event.KeySym.T: "toggle_navigation",
        tcod.event.KeySym.U: "use",
        tcod.event.KeySym.W: "wear",
        tcod.event.KeySym.D: "drop",
        tcod.event.KeySym.R: "reset_filter",
        tcod.event.KeySym.P: "repack",
        tcod.event.KeySym.F: "filter",
        tcod.event.KeySym.SLASH: "filter",
    }
    semicolon = getattr(tcod.event.KeySym, "SEMICOLON", None)
    if semicolon is not None:
        actions[semicolon] = "toggle_view"
    return actions.get(sym)

ZOOM_IN_KEYS = frozenset({
    tcod.event.KeySym.PLUS, tcod.event.KeySym.EQUALS,
    tcod.event.KeySym.KP_PLUS, tcod.event.KeySym.Z,
})
ZOOM_OUT_KEYS = frozenset({
    tcod.event.KeySym.MINUS, tcod.event.KeySym.KP_MINUS,
})


def world_action_for_key(sym, shift: bool = False):
    """Pure Miska world-key map; menus have their own navigation bindings."""
    key_actions = {
        tcod.event.KeySym.RETURN: ("context_menu",),
        tcod.event.KeySym.F1: ("help",),
        tcod.event.KeySym.F2: ("tutorial_journal",),
        tcod.event.KeySym.F3: ("tutorial_guidance",),
        tcod.event.KeySym.F4: ("tutorial_track",),
        tcod.event.KeySym.F5: ("save_game",),
        tcod.event.KeySym.F6: ("activity_history",),
        tcod.event.KeySym.F9: ("load_game",),
        tcod.event.KeySym.F10: ("dev_route",),
        tcod.event.KeySym.Y: ("water_crop",),
        tcod.event.KeySym.PERIOD: ("wait",),
        tcod.event.KeySym.KP_5: ("wait",),
        tcod.event.KeySym.E: ("inspect",),
        tcod.event.KeySym.F7: ("progression",),
        tcod.event.KeySym.U: ("use_item",),
        tcod.event.KeySym.G: ("get",),
        tcod.event.KeySym.BACKSLASH: ("haul",),
        tcod.event.KeySym.I: ("pack",),
        tcod.event.KeySym.O: ("open_container",),
        tcod.event.KeySym.F: ("place",),
        tcod.event.KeySym.R: ("repack",),
        tcod.event.KeySym.A: ("activities",),
        tcod.event.KeySym.X: ("cancel_activity",),
        tcod.event.KeySym.SLASH: ("inventory_manager",),
    }
    if sym in (tcod.event.KeySym.ESCAPE, tcod.event.KeySym.Q):
        return ("quit",)
    if sym == tcod.event.KeySym.W:
        return ("wear",) if not shift else None
    if sym == tcod.event.KeySym.D:
        return ("drop_adj",) if shift else ("drop",)
    if sym == tcod.event.KeySym.F and shift:
        return ("drop",)
    if sym in ZOOM_IN_KEYS:
        return ("zoom", 1)
    if sym in ZOOM_OUT_KEYS:
        return ("zoom", -1)
    if sym in MOVE_KEYS:
        dx, dy = MOVE_KEYS[sym]
        return ("move", dx, dy)
    return key_actions.get(sym)


def compute_camera(map_w: int, map_h: int, view_w: int, view_h: int,
                   px: int, py: int) -> tuple[int, int, int, int]:
    """Return (cam_x, cam_y, off_x, off_y): top-left world cell of the viewport
    plus screen offset used to center maps smaller than the viewport."""
    if view_w >= map_w:
        cam_x, off_x = 0, (view_w - map_w) // 2
    else:
        cam_x = min(max(px - view_w // 2, 0), map_w - view_w)
        off_x = 0
    if view_h >= map_h:
        cam_y, off_y = 0, (view_h - map_h) // 2
    else:
        cam_y = min(max(py - view_h // 2, 0), map_h - view_h)
        off_y = 0
    return cam_x, cam_y, off_x, off_y


def cine_crop(focus_px: tuple[float, float], texture_px: tuple[float, float],
              window_px: tuple[int, int], scale: float) -> tuple[float, float, float, float]:
    """The world-texture crop (sx, sy, sw, sh) that, scaled by `scale`,
    fills the window. Centered on the focus point, clamped inside the
    texture so a speaker near an edge never exposes empty black."""
    scale = max(1.0, float(scale))
    sw = max(1.0, window_px[0] / scale)
    sh = max(1.0, window_px[1] / scale)
    sx = min(max(0.0, focus_px[0] - sw / 2), max(0.0, texture_px[0] - sw))
    sy = min(max(0.0, focus_px[1] - sh / 2), max(0.0, texture_px[1] - sh))
    return sx, sy, sw, sh


MOVE_REPEAT_DELAY = 0.18
MOVE_REPEAT_INTERVAL = 0.075
MOTION_BUFFER_EXTRA_Y = 12
MOTION_CAMERA_LAG = 0.38


def movement_frame(progress: float, start: tuple[int, int], end: tuple[int, int],
                   camera_start: tuple[int, int], camera_end: tuple[int, int],
                   linear: bool = False,
                   ) -> tuple[tuple[float, float], tuple[float, float]]:
    """Return eased player/camera coordinates; player leads the camera slightly."""
    progress = max(0.0, min(1.0, float(progress)))

    def smoothstep(value: float) -> float:
        value = max(0.0, min(1.0, value))
        return value * value * (3.0 - 2.0 * value)

    player_progress = progress if linear else smoothstep(progress / 0.7)
    camera_progress = progress if linear else smoothstep(progress)
    player = tuple(a + (b - a) * player_progress for a, b in zip(start, end))
    camera = tuple(a + (b - a) * camera_progress
                   for a, b in zip(camera_start, camera_end))
    return player, camera


def screen_to_world_tile(position: tuple[float, float], camera: tuple[float, float],
                        offset: tuple[int, int], tile_size: tuple[int, int],
                        viewport: tuple[int, int]) -> tuple[int, int] | None:
    """Map a pixel position in the world viewport to its current world cell."""
    pixel_x, pixel_y = position
    tile_width, tile_height = tile_size
    view_width, view_height = viewport
    cell_x, cell_y = pixel_x / tile_width, pixel_y / tile_height
    if not (0 <= cell_x < view_width and 0 <= cell_y < view_height):
        return None
    return (math.floor(camera[0] + cell_x - offset[0]),
            math.floor(camera[1] + cell_y - offset[1]))


class MovementRepeater:
    """Repeat a held grid direction independently of OS key-repeat settings."""

    def __init__(self, delay: float = MOVE_REPEAT_DELAY,
                 interval: float = MOVE_REPEAT_INTERVAL) -> None:
        self.delay = delay
        self.interval = interval
        self.sym = None
        self.action = None
        self.next_at = 0.0
        self.last_was_repeat = False

    def press(self, sym, action, now: float, repeat: bool = False):  # type: ignore[no-untyped-def]
        if action is None:
            self.cancel()
            return None
        if repeat:
            return None  # use our own clock instead of queued OS repeats
        self.sym = sym
        self.action = action
        self.next_at = now + self.delay
        self.last_was_repeat = False
        return action

    def release(self, sym) -> None:  # type: ignore[no-untyped-def]
        if sym == self.sym:
            self.cancel()

    def cancel(self) -> None:
        self.sym = None
        self.action = None
        self.next_at = 0.0
        self.last_was_repeat = False

    def timeout(self, now: float) -> float | None:
        if self.action is None:
            return None
        return max(0.0, self.next_at - now)

    def poll(self, now: float):  # type: ignore[no-untyped-def]
        if self.action is None or now < self.next_at:
            return None
        self.next_at = now + self.interval
        self.last_was_repeat = True
        return self.action


def scale_color(rgb: tuple[int, int, int], brightness: float) -> tuple[int, int, int]:
    b = max(0.0, min(1.0, brightness))
    return (int(rgb[0] * b), int(rgb[1] * b), int(rgb[2] * b))


# Interactable highlight: background tints for actionable tiles near the
# player (chebyshev <= HILITE_RADIUS), so "where do I what" reads at a glance.
HILITE_RADIUS = 8
INTERACT_BG: dict[int, tuple[int, int, int]] = {
    T.FARM: (50, 40, 10),
    T.STALL: (55, 45, 10),
    T.HEARTH: (60, 25, 5),
    T.BED: (25, 25, 60),
    T.TREE: (10, 40, 10),
    T.WATER: (10, 20, 50),
}
DOOR_BG = (60, 40, 10)
LOOT_GLYPH, LOOT_FG, LOOT_BG = "*", (240, 210, 120), (60, 50, 10)
WATCH_BG = (70, 60, 20)

# Activity tints added to the NPC's own color: work glows warm, trade glitters,
# meals content. Identity (glyph) never changes — except sleep and starvation.
ACTIVITY_TINT: dict[str, tuple[int, int, int]] = {
    "working": (80, 50, 0),
    "buying": (70, 60, 0),
    "eating": (0, 60, 0),
}
def entity_style(e, watched_id=None):  # type: ignore[no-untyped-def]
    """Pure (glyph, fg, bg): what an entity looks like this turn."""
    if e.get("role") != "player":
        act = e.get("activity", "idle")
        if act == "sleeping":
            return ("z", (120, 120, 160), None)
        if act == "hungry":
            return ("?", e.color, None)
    fg = e.color
    tint = ACTIVITY_TINT.get(e.get("activity", "idle"), (0, 0, 0))
    if tint != (0, 0, 0):
        fg = tuple(min(255, c + t) for c, t in zip(e.color, tint))
    bg = WATCH_BG if (e.id == watched_id and e.get("role") != "player") else None
    return (e.glyph, fg, bg)


class AsciiRenderer:
    def __init__(self, zoom_idx: int = 1,
                 settings: RenderSettings | None = None) -> None:
        self.zoom_idx = zoom_idx  # default Mid: follow-cam with room to see
        self.settings = settings or RenderSettings()
        self.width = 80
        self.height = 50
        self.map_w = 80
        self.map_h = 45
        self.console: tcod.console.Console | None = None
        self.context: tcod.context.Context | None = None
        self.world: WorldRenderer | None = None
        self.ui = UIRenderer(self.settings)
        self.overlays = OverlayRenderer(self.ui)
        self.tile_width, self.tile_height = ZOOM_LEVELS[zoom_idx][1:]
        self._ui_texture = None
        self._base_ui_image = None
        self._sprite_codepoints: dict[str, int] = {}
        self._motion: dict | None = None
        self._motion_cache: dict | None = None
        self._motion_texture = None
        self._motion_texture_key = None
        self._world_texture = None
        self._base_ui_texture = None
        self._active_camera = (0, 0, 0, 0)
        self._move_repeater = MovementRepeater()
        self._pending_events = deque()
        self._skip_next_animation = False
        self._hover_world_tile: tuple[int, int] | None = None
        self._last_mouse_click: tuple[str, tuple[int, int], int] | None = None
        self._last_village = None
        self._frame_size = (0, 0)
        self._modal_lines = []
        self._modal_footer = ""
        self._modal_choice_start = None
        self._modal_choice_count = 0
        self._modal_selected_index = 0
        self.floats: list[WorldFloat] = []
        self.activity: dict | None = None
        self._activity_dirty = False
        self._float_textures: dict = {}
        # Cinematic dialogue state: the zoom tween, plus the one persistent
        # speech bubble / caption the runner refreshes every frame.
        self.cine: dict | None = None
        self._cine_applied: tuple[float, float, float] | None = None
        self._cine_prev = 0.0
        self.dialogue_bubble: dict | None = None
        self.dialogue_caption: str | None = None
        self.dialogue_choices: dict | None = None
        self._bubble_cache: dict = {}

    # -- setup / zoom --
    @property
    def zoom_name(self) -> str:
        return ZOOM_LEVELS[self.zoom_idx][0]

    @property
    def sidebar_tiles(self) -> int:
        return max(SIDEBAR_MIN, math.ceil(SIDEBAR_PX / self.tile_width))

    @property
    def viewport(self) -> tuple[int, int]:
        status_rows = math.ceil(
            (self.ui.line_height(12) * 2 + 12) / self.tile_height)
        return (self.width - self.sidebar_tiles,
                self.height - max(UI_ROWS, status_rows))

    def _find_font(self) -> str:
        from omnisim.ui.ui_renderer import resolve_font_path
        try:
            return resolve_font_path(self.settings.world_font_family)
        except FileNotFoundError:
            return resolve_font_path("DejaVu Sans Mono")

    def _world_cell_size(self, measured_width: int, measured_height: int) -> tuple[int, int]:
        _, zoom_w, zoom_h = ZOOM_LEVELS[self.zoom_idx]
        mid_w, mid_h = ZOOM_LEVELS[1][1:]
        scale_x, scale_y = zoom_w / mid_w, zoom_h / mid_h
        if self.settings.world_tileset == "everrogue":
            # Sprite cells use uniform zoom, not the legacy text aspect ratio.
            scale_y = scale_x
        tw = round((self.settings.world_cell_width or measured_width)
                   * scale_x * self.settings.world_zoom)
        th = round((self.settings.world_cell_height or measured_height)
                   * scale_y * self.settings.world_zoom)
        return max(4, tw), max(6, th)

    def _build(self) -> None:
        world_font_path = self._find_font()
        from PIL import ImageFont
        measured_font = ImageFont.truetype(
            world_font_path, self.settings.world_font_size)
        measured_width = max(1, round(measured_font.getlength("M")))
        measured_height = max(1, sum(measured_font.getmetrics()))
        self.tile_width, self.tile_height = self._world_cell_size(measured_width, measured_height)
        self.width = WIN_PX[0] // self.tile_width
        self.height = WIN_PX[1] // self.tile_height
        self._ui_texture = None
        self._motion_texture = None
        self._motion_texture_key = None
        self._world_texture = None
        self._base_ui_texture = None
        self._float_textures = {}
        self._minimap_texture = None
        self._minimap_key = None
        self.world = None
        self.console = None
        if self.context is not None:
            self.context.close()
            self.context = None
        tileset = tcod.tileset.load_truetype_font(
            world_font_path, self.tile_width, self.tile_height)
        if self.settings.world_tileset in ("retrodays", "kenney", "everrogue"):
            if self.settings.world_tileset == "everrogue":
                from omnisim.ui.everrogue import install_sprite_atlas
            elif self.settings.world_tileset == "kenney":
                from omnisim.ui.kenney_1bit import install_sprite_atlas
            else:
                from omnisim.ui.retro_days import install_sprite_atlas
            self._sprite_codepoints = install_sprite_atlas(
                tileset, self.tile_width, self.tile_height)
        else:
            self._sprite_codepoints = {}
        try:
            self.context = tcod.context.new(
                columns=self.width, rows=self.height,
                tileset=tileset,
                title=f"Omni Sim — Thornhollow [{self.zoom_name}]",
                sdl_window_flags=int(tcod.sdl.video.WindowFlags.RESIZABLE),
            )
            window = self.context.sdl_window
            if window is not None:
                window.min_size = (640, 400)
        except RuntimeError as e:
            raise SystemExit(
                f"Could not open a window ({e}). "
                "On headless machines use: python -m omnisim --smoke"
            ) from e
        self._sync_console_size()

    def _sync_console_size(self) -> None:
        """Match the world grid to the native-size resizable SDL window."""
        if self.context is None:
            return
        renderer = self.context.sdl_renderer
        if renderer is None:
            return
        pixel_width, pixel_height = renderer.output_size
        self._frame_size = (pixel_width, pixel_height)
        width = max(1, pixel_width // self.tile_width)
        height = max(1, pixel_height // self.tile_height)
        if self.console is None or (width, height) != (self.width, self.height):
            self.width, self.height = width, height
            self.console = tcod.console.Console(
                width, height + MOTION_BUFFER_EXTRA_Y, order="F")
            self.world = WorldRenderer(
                self.context, self.console, self._sprite_codepoints,
                background=self.ui.PANEL)
            self._world_texture = None
            self._base_ui_texture = None

    def sprite_for_tile(self, game_map, x: int, y: int) -> str | None:  # type: ignore[no-untyped-def]
        if not self._sprite_codepoints:
            return None
        tile_id = int(game_map.tiles[y, x])
        door_open = tile_id == T.DOOR and bool(game_map.doors[y, x])
        if self.settings.world_tileset == "kenney":
            from omnisim.ui.kenney_1bit import sprite_for_tile
            return sprite_for_tile(tile_id, door_open=door_open)
        if self.settings.world_tileset == "everrogue":
            from omnisim.ui.everrogue import sprite_for_tile
            return sprite_for_tile(
                tile_id, door_open=door_open, game_map=game_map, x=x, y=y)
        from omnisim.ui.retro_days import sprite_for_tile
        return sprite_for_tile(
            tile_id, door_open=door_open, game_map=game_map, x=x, y=y)

    def sprite_for_entity(self, entity) -> str | None:  # type: ignore[no-untyped-def]
        if not self._sprite_codepoints:
            return None
        if self.settings.world_tileset == "everrogue":
            from omnisim.ui.everrogue import sprite_for_entity
            return sprite_for_entity(entity)
        from omnisim.ui.retro_days import sprite_for_entity
        return sprite_for_entity(entity)

    def sprite_for_ground(self, village, wx: int, wy: int) -> str | None:
        if self.settings.world_tileset == "everrogue":
            from omnisim.ui.everrogue import sprite_for_item
            items = village.ground_items.get((wx, wy), ())
            if items:
                return sprite_for_item(items[0])
        return None

    def _ground_is_known_at(self, village, wx, wy):
        gm = village.game_map
        known = gm.explored if self.settings.world_tileset == "everrogue" else gm.visible
        return gm.in_bounds(wx, wy) and bool(known[wy, wx])

    def _world_background(self, brightness: float = 1.0):
        if self.settings.world_tileset == "everrogue":
            return self.ui.PANEL  # Recolor artwork, never the surrounding canvas.
        return self._shade_color(self.ui.PANEL, brightness)

    def _draw_lit_cell(self, sx, sy, glyph, *, fg, bg, sprite, brightness):
        if sprite and self.settings.world_tileset == "everrogue":
            from omnisim.ui.everrogue import FLOOR_SPRITES
            if sprite in FLOOR_SPRITES:
                brightness = 1.0  # Flooring is always shown in its original palette.
            self.world.draw_cell(sx, sy, glyph, fg=(255, 255, 255), bg=bg,
                                 sprite=sprite, sprite_fade=brightness)
        else:
            self.world.draw_cell(sx, sy, glyph, fg=fg, bg=bg, sprite=sprite)

    def _shade_color(self, rgb, brightness):
        if self.settings.world_tileset == "everrogue":
            from omnisim.ui.everrogue import shade_color
            return shade_color(rgb, brightness)
        return scale_color(rgb, brightness)

    def _visual_brightness(self, village, wx, wy, brightness):
        if self.settings.world_tileset == "everrogue":
            from omnisim.ui.everrogue import visual_brightness
            return visual_brightness(village.game_map, village.light, wx, wy, brightness)
        return brightness

    def _sprite_colors_at(self, village, wx, wy):
        if self.settings.world_tileset != "everrogue":
            return (255, 255, 255), (0, 0, 0)
        light = village.light
        b = float(light[wy, wx]) if light is not None else 1.0
        b = self._visual_brightness(village, wx, wy, b)
        return self._shade_color((255, 255, 255), b), self._world_background(b)

    def init_for(self, map_w: int, map_h: int) -> None:
        self.map_w, self.map_h = map_w, map_h
        self._build()

    def init(self, width: int | None = None, height: int | None = None) -> None:
        self._build()

    def zoom(self, direction: int, map_w: int, map_h: int) -> None:
        new = min(max(self.zoom_idx + direction, 0), len(ZOOM_LEVELS) - 1)
        if new != self.zoom_idx:
            self.zoom_idx = new
            self.map_w, self.map_h = map_w, map_h
            self._build()

    def _sidebar_lines(self, village, width: int, height: int):
        return build_sidebar(village, width, height)

    def _status_lines(self, village) -> list[str]:
        latest = village.log[-1] if village.log else ""
        controls = ("WASD move · arrows move · . wait · e eat · g get · W work · "
                    "T trade · Z sleep · Tab watch · i inspect · M board · p pack · q quit")
        return [latest, controls]

    def _present_layers(self) -> None:
        if self.context is None or self.world is None or self.console is None:
            return
        renderer = self.context.sdl_renderer
        if renderer is None:
            return
        width, height = renderer.output_size
        self._cine_tick()
        if self._world_texture is None:
            self._world_texture = self.world.render_texture()
        world_texture = self._world_texture
        renderer.draw_color = (*self.ui.PANEL, 255)
        renderer.clear()
        if self._motion is None:
            if self.cine is not None:
                # Dialogue zoom: the baked world texture is cropped around
                # the speakers and stretched over the window; the cue and
                # float layers map through the same crop.
                self._cine_apply(renderer)
            elif self._motion_cache is not None:
                self._cine_applied = None
                # Idle feedback frames must use the same overscan crop as
                # the last tween, not display the buffer from its origin.
                cache = self._motion_cache
                cx, cy = cache["camera"]
                ax, ay = cache["anchor"]
                vw, vh = self.viewport
                renderer.copy(world_texture,
                              source=((cx - ax) * self.tile_width,
                                      (cy - ay) * self.tile_height,
                                      vw * self.tile_width, vh * self.tile_height),
                              dest=(0, 0, vw * self.tile_width, vh * self.tile_height))
                player_texture = self._get_motion_texture(self._last_village, renderer)
                ox, oy = cache["offset"]
                player = self._last_village.player
                renderer.copy(player_texture, dest=(
                    (player.x - cx + ox) * self.tile_width,
                    (player.y - cy + oy) * self.tile_height,
                    self.tile_width, self.tile_height))
            else:
                self._cine_applied = None
                renderer.copy(world_texture, dest=(
                    0, 0, self.width * self.tile_width,
                    self.height * self.tile_height), source=(
                    0, 0, self.width * self.tile_width,
                    self.height * self.tile_height))
        else:
            vw, vh = self.viewport
            source_x, source_y = self._motion["source_offset"]
            renderer.copy(
                world_texture,
                source=(source_x, source_y,
                        vw * self.tile_width, vh * self.tile_height),
                dest=(0, 0, vw * self.tile_width, vh * self.tile_height))
            player_x, player_y = self._motion["player"]
            camera_x, camera_y = self._motion["camera"]
            _, _, off_x, off_y = self._active_camera
            player_dest = (
                (player_x - camera_x + off_x) * self.tile_width,
                (player_y - camera_y + off_y) * self.tile_height,
                self.tile_width, self.tile_height,
            )
            player_texture = self._get_motion_texture(
                self._motion["view"], renderer)
            if self.settings.world_tileset == "everrogue":
                player_texture.color_mod = (255, 255, 255)
            renderer.copy(player_texture, dest=player_dest)
        self._draw_hover_outline(renderer)
        self._draw_world_floats(renderer)
        self._draw_dialogue_cues(renderer, width, height)
        if self.overlays.active:
            image = self._base_ui_image
            if image is None or image.size != (width, height):
                image = self.ui.new_overlay(width, height)
            image = self.overlays.draw(image)
            texture = renderer.upload_texture(np.asarray(image, dtype=np.uint8))
            texture.blend_mode = BlendMode.BLEND
        else:
            if (self._base_ui_texture is None or self._base_ui_image is None
                    or self._base_ui_image.size != (width, height)):
                image = self._base_ui_image
                if image is None or image.size != (width, height):
                    image = self.ui.new_overlay(width, height)
                self._base_ui_texture = renderer.upload_texture(
                    np.asarray(image, dtype=np.uint8))
                self._base_ui_texture.blend_mode = BlendMode.BLEND
            texture = self._base_ui_texture
        renderer.copy(texture)
        if self._last_village is not None and not self.overlays.active:
            self._draw_minimap(renderer, self._last_village)
        renderer.present()
        # Keep the uploaded texture alive until SDL has consumed its draw call.
        self._ui_texture = texture

    def _display_camera(self, village):  # type: ignore[no-untyped-def]
        if self._motion is not None:
            return self._motion["camera"], self._motion["offset"]
        if self._motion_cache is not None:
            return self._motion_cache["camera"], self._motion_cache["offset"]
        vw, vh = self.viewport
        cam_x, cam_y, off_x, off_y = compute_camera(
            village.game_map.width, village.game_map.height,
            vw, vh, village.player.x, village.player.y)
        return (float(cam_x), float(cam_y)), (off_x, off_y)

    def _mouse_world_tile(self, position, village):  # type: ignore[no-untyped-def]
        camera, offset = self._display_camera(village)
        pixel = (float(position.x), float(position.y))
        cell = screen_to_world_tile(
            pixel, camera, offset, (self.tile_width, self.tile_height),
            self.viewport)
        if cell is None:
            return None
        x, y = cell
        gm = village.game_map
        if not gm.in_bounds(x, y) or not gm.visible[y, x]:
            return None
        return cell

    def _set_hover_from_position(self, position) -> bool:  # type: ignore[no-untyped-def]
        village = self._last_village
        if village is None:
            return False
        at = self._mouse_world_tile(position, village)
        if at is not None:
            has_object = bool(village.objects_at.get(at) or at in village.ground)
            if not has_object:
                has_object = any(
                    (entity.x, entity.y) == at for entity in village.entities())
            if not has_object:
                at = None
        if at == self._hover_world_tile:
            return False
        self._hover_world_tile = at
        return True

    def _draw_hover_outline(self, renderer) -> None:  # type: ignore[no-untyped-def]
        at = self._hover_world_tile
        village = self._last_village
        if at is None or village is None or self.overlays.active:
            return
        x, y = at
        if not village.game_map.visible[y, x]:
            return
        camera, offset = self._display_camera(village)
        sx = (x - camera[0] + offset[0]) * self.tile_width
        sy = (y - camera[1] + offset[1]) * self.tile_height
        vw, vh = self.viewport
        if (sx + self.tile_width <= 0 or sy + self.tile_height <= 0
                or sx >= vw * self.tile_width or sy >= vh * self.tile_height):
            return
        renderer.draw_color = (255, 255, 255, 255)
        renderer.draw_rect((sx + 1, sy + 1,
                            max(1, self.tile_width - 2),
                            max(1, self.tile_height - 2)))

    # -- on-map feedback: floats and the activity stamina bar --

    def spawn_float(self, text: str, world_xy: tuple[int, int],
                    color: tuple[int, int, int] = (232, 232, 222),
                    ttl: float = 2.4, lane: int = 0) -> None:
        """Queue one floating message above a world tile.

        Floats sharing a lane stagger themselves so a message, an energy
        cost, and an XP gain read as a small cascade instead of one blob.
        """
        if not text:
            return
        now = time.perf_counter()
        delay = FLOAT_STAGGER_S * sum(
            1 for row in self.floats if not row.grounded and row.lane == lane)
        self.floats.append(WorldFloat(
            text=text, color=color, world_xy=tuple(world_xy),
            born=now + delay, ttl=max(0.4, ttl), lane=max(0, lane)))

    def spawn_caption(self, text: str, world_xy: tuple[int, int],
                      color: tuple[int, int, int] = (250, 224, 150),
                      ttl: float = 5.0) -> None:
        """Set dialogue on fixed tile rows BELOW the speaker's original tile.

        No rise, stagger, fade, edge-clamping or following a walking actor.
        Parallel lines reserve their own rows; expiry never reflows survivors.
        """
        if not text:
            return
        now = time.perf_counter()
        self._purge_expired_floats(now)
        at = tuple(world_xy)
        existing = [row for row in self.floats if row.grounded and row.world_xy == at]
        image = self._caption_image(text, color)
        self.floats.append(WorldFloat(
            text=text, color=color, world_xy=at, born=now,
            ttl=max(ttl, 2.0 + len(text) / 22), grounded=True,
            tile_row=max((row.tile_row + row.tile_rows for row in existing), default=1),
            tile_rows=max(1, math.ceil(image.height / self.tile_height))))

    def _caption_image(self, text, color):
        """Wrapped, tile-sized dialogue art; transparent apart from the ink."""
        font_px = max(18, round(20 * self.settings.ui_scale))
        font = self.ui.font_for_family(self.settings.world_font_family, size=font_px)
        width = CAPTION_WIDTH_TILES * self.tile_width
        pad = 4
        lines = self.ui.wrap_text(text, font, max(1, width - pad * 2))
        line_h = sum(font.getmetrics()) + 2
        height = math.ceil((line_h * len(lines) + pad * 2) / self.tile_height) * self.tile_height
        image = Image.new("RGBA", (width, height))
        draw = ImageDraw.Draw(image)
        for index, line in enumerate(lines):
            draw.text((width / 2, pad + index * line_h), line, font=font,
                      anchor="mt", fill=(*color, 255), stroke_width=1,
                      stroke_fill=(0, 0, 0, 190))
        return image

    def _caption_texture(self, text, color):
        key = ("caption", text, color, self.tile_width, self.tile_height, self.settings.ui_scale)
        cached = self._float_textures.get(key)
        if cached is not None:
            return cached
        image = self._caption_image(text, color)
        texture = self.context.sdl_renderer.upload_texture(np.asarray(image, dtype=np.uint8))
        texture.blend_mode = BlendMode.BLEND
        if len(self._float_textures) > 96:
            self._float_textures.clear()
        self._float_textures[key] = (texture, image.width, image.height)
        return self._float_textures[key]

    def _minimap_image(self, village):
        """Only explored terrain: never include hidden residents or objects."""
        gm = village.game_map
        pixels = np.zeros((gm.height, gm.width, 4), dtype=np.uint8)
        pixels[:] = (8, 24, 25, 255)
        for y, x in np.argwhere(gm.explored):
            _, color = T.glyph_at(gm, int(x), int(y))
            pixels[y, x] = (*color, 255)
        return Image.fromarray(pixels)

    def _draw_minimap(self, renderer, village):
        gm = village.game_map
        left = self.viewport[0] * self.tile_width + 12
        available = renderer.output_size[0] - left - 12
        if available < 32:
            return
        width = min(available, 240)
        height = max(1, round(width * gm.height / gm.width))
        key = (id(gm), gm.explored.tobytes(), gm.tiles.tobytes(), width, height)
        if getattr(self, "_minimap_key", None) != key:
            image = self._minimap_image(village).resize(
                (width, height), Image.Resampling.NEAREST)
            self._minimap_texture = renderer.upload_texture(np.asarray(image))
            self._minimap_key = key
        renderer.copy(self._minimap_texture, dest=(left, 12, width, height))
        renderer.draw_color = (235, 209, 112, 255)
        # tcod's SDL3 draw_rect currently fills the rectangle despite its
        # outline docstring; paint four thin edges explicitly.
        for edge in ((left - 1, 11, width + 2, 1),
                     (left - 1, height + 12, width + 2, 1),
                     (left - 1, 12, 1, height),
                     (left + width, 12, 1, height)):
            renderer.fill_rect(edge)
        renderer.fill_rect((left + village.player.x * width / gm.width - 2,
                            12 + village.player.y * height / gm.height - 2, 5, 5))

    def set_activity_bar(self, world_xy: tuple[int, int], stamina: float,
                         label: str = "") -> None:
        """Pin the constant stamina bar above the thing being worked."""
        self.activity = {"xy": (int(world_xy[0]), int(world_xy[1])),
                         "stamina": max(0.0, min(1.0, float(stamina))),
                         "label": label}
        self._activity_dirty = True

    def update_activity_bar(self, stamina: float) -> None:
        if self.activity is not None:
            self.activity["stamina"] = max(0.0, min(1.0, float(stamina)))
            self._activity_dirty = True

    def clear_activity_bar(self) -> None:
        if self.activity is not None:
            self._activity_dirty = True
        self.activity = None

    def _purge_expired_floats(self, now: float) -> bool:
        alive = [row for row in self.floats if now - row.born < row.ttl]
        changed = len(alive) != len(self.floats)
        self.floats = alive
        return changed

    def _float_texture(self, text: str, color: tuple[int, int, int]):  # type: ignore[no-untyped-def]
        """A cached RGBA sprite of one message at world scale, with a soft
        dark shadow so it stays readable over any terrain."""
        font_px = max(11, round(self.tile_height * 0.62))
        key = (text, color, font_px)
        cached = self._float_textures.get(key)
        if cached is not None:
            return cached
        font = ImageFont.truetype(self._find_font(), font_px)
        measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        left, top, right, bottom = measure.textbbox((0, 0), text, font=font)
        pad = 2
        image = Image.new(
            "RGBA", (right - left + pad * 2, bottom - top + pad * 2), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.text((pad - left + 1, pad - top + 1), text, font=font,
                  fill=(0, 0, 0, 170))
        draw.text((pad - left, pad - top), text, font=font, fill=(*color, 255))
        assert self.context is not None
        texture = self.context.sdl_renderer.upload_texture(
            np.asarray(image, dtype=np.uint8))
        texture.blend_mode = BlendMode.BLEND
        if len(self._float_textures) > 96:
            self._float_textures.clear()
        self._float_textures[key] = (texture, image.width, image.height)
        return self._float_textures[key]

    def _draw_world_floats(self, renderer) -> None:  # type: ignore[no-untyped-def]
        """Grounded NPC captions remain visible through camera/movement tweens.

        Work floats and stamina bars retain their existing idle-only behavior.
        """
        if self._last_village is None:
            return
        if not self.floats and self.activity is None:
            return
        camera, offset = self._display_camera(self._last_village)
        tw, th = self.tile_width, self.tile_height
        visible = self._last_village.game_map.visible
        # While the dialogue zoom holds, every feedback layer maps through
        # the same crop the world copy used, so bubbles ride the lean.
        cine_scale, cine_x, cine_y = self._cine_applied or (1.0, 0.0, 0.0)
        view_px_w = self.viewport[0] * tw
        view_px_h = self.viewport[1] * th

        def screen_pos(wx: float, wy: float) -> tuple[float, float]:
            px = (wx - camera[0] + offset[0]) * tw
            py = (wy - camera[1] + offset[1]) * th
            return ((px - cine_x) * cine_scale, (py - cine_y) * cine_scale)

        if self.activity is not None and self._motion is None and self.cine is None:
            wx, wy = self.activity["xy"]
            if (0 <= wy < visible.shape[0] and 0 <= wx < visible.shape[1]
                    and visible[wy, wx]):
                ax, ay = screen_pos(wx, wy)
                bar_w = tw * ACTIVITY_BAR_TILES
                bar_h = max(4, th // 5)
                bx = min(max(0.0, ax + (tw - bar_w) / 2),
                         max(0.0, view_px_w - bar_w))
                by = max(0.0, ay - bar_h - math.ceil(th * 0.30))
                stamina = self.activity["stamina"]
                fill = int(round(bar_w * stamina))
                color = ((106, 190, 92) if stamina > 0.5
                         else (222, 170, 70) if stamina > 0.25
                         else (196, 84, 72))
                renderer.draw_color = (12, 12, 12, 205)
                renderer.fill_rect((bx - 1, by - 1, bar_w + 2, bar_h + 2))
                if fill > 0:
                    renderer.draw_color = (*color, 235)
                    renderer.fill_rect((bx, by, fill, bar_h))

        now = time.perf_counter()
        self._purge_expired_floats(now)
        for row in self.floats:
            age = now - row.born
            if age < 0:
                continue
            wx, wy = row.world_xy
            if not (0 <= wy < visible.shape[0] and 0 <= wx < visible.shape[1]
                    and visible[wy, wx]):
                continue
            if row.grounded:
                texture, tex_w, tex_h = self._caption_texture(row.text, row.color)
                fx, fy = screen_pos(wx - CAPTION_WIDTH_TILES // 2, wy + row.tile_row)
                # Clip the texture, never move it toward a screen edge. This
                # keeps its map anchor stable while the camera passes it.
                left, top = max(0.0, fx), max(0.0, fy)
                right, bottom = min(view_px_w, fx + tex_w), min(view_px_h, fy + tex_h)
                if right > left and bottom > top:
                    texture.alpha_mod = 255
                    renderer.copy(texture,
                                  source=(left - fx, top - fy, right - left, bottom - top),
                                  dest=(left, top, right - left, bottom - top))
                continue
            if self._motion is not None:
                continue
            t = min(1.0, age / row.ttl)
            ax, ay = screen_pos(wx, wy)
            texture, tex_w, tex_h = self._float_texture(row.text, row.color)
            tex_w, tex_h = tex_w * cine_scale, tex_h * cine_scale
            alpha = (255 if t < FLOAT_FADE_FROM else int(round(
                255 * max(0.0, 1.0 - (t - FLOAT_FADE_FROM)
                         / (1.0 - FLOAT_FADE_FROM)))))
            climb = FLOAT_RISE_TILES * th * cine_scale * min(1.0, t * 1.9)
            fx = min(max(0.0, ax + (tw * cine_scale - tex_w) / 2),
                     max(0.0, view_px_w - tex_w))
            fy = max(1.0, ay - climb - row.lane * FLOAT_LANE_GAP * th * cine_scale
                     - tex_h)
            texture.alpha_mod = alpha
            renderer.draw_color = (255, 255, 255, 255)
            renderer.copy(texture, dest=(fx, fy, tex_w, tex_h))

    def _get_motion_texture(self, village, renderer):  # type: ignore[no-untyped-def]
        player = village.player
        key = (self.tile_width, self.tile_height, self.settings.world_tileset,
               player.glyph, tuple(player.color))
        brightness = 1.0
        if self.settings.world_tileset == "everrogue":
            from omnisim.ui.everrogue import fade_level, FADE_STEPS
            light = village.light
            brightness = float(light[player.y, player.x]) if light is not None else 1.0
            brightness = self._visual_brightness(village, player.x, player.y, brightness)
            brightness = fade_level(brightness) / FADE_STEPS
            key += (brightness,)
        if self._motion_texture is not None and self._motion_texture_key == key:
            return self._motion_texture

        frame = Image.new("RGBA", (self.tile_width, self.tile_height),
                          (0, 0, 0, 0))
        if self.settings.world_tileset == "everrogue":
            from omnisim.ui.everrogue import fitted_sprite, fade_sprite
            frame = Image.fromarray(fade_sprite(
                fitted_sprite("player", self.tile_width, self.tile_height), brightness))
        elif self.settings.world_tileset == "retrodays":
            from omnisim.ui.retro_days import ASSET_ROOT, SPRITE_FILES
            with Image.open(ASSET_ROOT / SPRITE_FILES["player"]) as source:
                sprite = source.convert("RGBA")
            pixels = np.asarray(sprite).copy()
            pixels[np.all(pixels[..., :3] == 0, axis=-1), 3] = 0
            sprite = Image.fromarray(pixels, mode="RGBA")
            scale = min(self.tile_width / sprite.width,
                        self.tile_height / sprite.height)
            size = (max(1, round(sprite.width * scale)),
                    max(1, round(sprite.height * scale)))
            sprite = sprite.resize(size, Image.Resampling.NEAREST)
            frame.alpha_composite(sprite, ((self.tile_width - size[0]) // 2,
                                           (self.tile_height - size[1]) // 2))
        else:
            draw = ImageDraw.Draw(frame)
            font = ImageFont.truetype(
                self._find_font(), max(6, self.tile_height - 2))
            draw.text((self.tile_width / 2, self.tile_height / 2), player.glyph,
                      font=font, fill=(*player.color, 255), anchor="mm")

        self._motion_texture = renderer.upload_texture(
            np.asarray(frame, dtype=np.uint8))
        self._motion_texture.blend_mode = BlendMode.BLEND
        self._motion_texture_key = key
        return self._motion_texture

    def animate_move(self, village, start: tuple[int, int],
                     end: tuple[int, int]) -> bool:  # type: ignore[no-untyped-def]
        """Animate a grid step, reusing a buffered scene while a key is held."""
        if self._skip_next_animation:
            self._skip_next_animation = False
            self._motion_cache = None
            return False
        duration = self.settings.movement_animation_ms / 1000.0
        if duration <= 0 or self.context is None or start == end:
            return False

        vw, vh = self.viewport
        normal_start = compute_camera(
            village.game_map.width, village.game_map.height,
            vw, vh, start[0], start[1])
        normal_end = compute_camera(
            village.game_map.width, village.game_map.height,
            vw, vh, end[0], end[1])
        direction = (int(math.copysign(1, end[0] - start[0])) if end[0] != start[0] else 0,
                     int(math.copysign(1, end[1] - start[1])) if end[1] != start[1] else 0)
        continuing = self._move_repeater.last_was_repeat
        cache = self._motion_cache
        camera_delta = (normal_end[0] - normal_start[0],
                        normal_end[1] - normal_start[1])
        cache_camera_end = None
        if cache is not None:
            camera_max = (max(0, village.game_map.width - vw),
                          max(0, village.game_map.height - vh))
            cache_camera_end = tuple(
                max(0.0, min(float(camera_max[axis]),
                             cache["camera"][axis] + camera_delta[axis]))
                for axis in range(2))
        reuse_cache = bool(
            continuing and cache is not None
            and cache["direction"] == direction
            and self._camera_fits_cache(
                cache_camera_end, end, cache, vw, vh, normal_start[2:]))

        if reuse_cache:
            anchor_x, anchor_y = cache["anchor"]
            offset = cache["offset"]
            buffer_width, buffer_height = cache["buffer_size"]
            camera_start = cache["camera"]
            camera_end = cache_camera_end
        elif continuing:
            assert self.console is not None
            buffer_width = min(self.console.width,
                               vw + (self.console.width - vw if direction[0] else 0))
            buffer_height = min(self.console.height,
                                vh + (self.console.height - vh if direction[1] else 0))
            spare_x = max(0, buffer_width - vw)
            spare_y = max(0, buffer_height - vh)
            anchor_x = normal_start[0] - (spare_x if direction[0] < 0 else 0)
            anchor_y = normal_start[1] - (spare_y if direction[1] < 0 else 0)
            anchor_x = max(0, anchor_x)
            anchor_y = max(0, anchor_y)
            offset = (normal_start[2], normal_start[3])
            camera_start = (float(normal_start[0]), float(normal_start[1]))
            camera_delta = (normal_end[0] - normal_start[0],
                            normal_end[1] - normal_start[1])
            camera_end_list = [camera_start[0], camera_start[1]]
            camera_max = (max(0, village.game_map.width - vw),
                          max(0, village.game_map.height - vh))
            for axis in range(2):
                delta = camera_delta[axis]
                if delta:
                    lag = direction[axis] * MOTION_CAMERA_LAG
                    camera_end_list[axis] = max(
                        0.0, min(float(camera_max[axis]),
                                  camera_start[axis] + delta - lag))
            camera_end = tuple(camera_end_list)
        else:
            buffer_width = min(vw + 1, self.width)
            buffer_height = min(vh + 1, self.height)
            anchor_x = min(normal_start[0], normal_end[0])
            anchor_y = min(normal_start[1], normal_end[1])
            offset = (normal_start[2], normal_start[3])
            camera_start = (float(normal_start[0]), float(normal_start[1]))
            camera_end = (float(normal_end[0]), float(normal_end[1]))

        motion_cache = cache if reuse_cache else ({
            "anchor": (anchor_x, anchor_y),
            "offset": offset,
            "buffer_size": (buffer_width, buffer_height),
            "direction": direction,
            "camera": camera_end,
        } if continuing else None)
        interrupted = False

        def update_frame(progress: float) -> None:
            player, camera = movement_frame(
                progress, start, end, camera_start, camera_end,
                linear=continuing)
            self._motion = {
                "view": village,
                "player": player,
                "camera": camera,
                "source_offset": (
                    (camera[0] - anchor_x) * self.tile_width,
                    (camera[1] - anchor_y) * self.tile_height,
                ),
                "anchor": (anchor_x, anchor_y),
                "offset": offset,
                "buffer_width": buffer_width,
                "buffer_height": buffer_height,
            }

        try:
            update_frame(0.0)
            if not reuse_cache:
                # Expand the scene buffer once when a held direction is
                # confirmed; repeated tile steps then only present cached art.
                self.render(village)
                if motion_cache is not None:
                    self._capture_motion_state(village, motion_cache)
            else:
                self._refresh_motion_cache(
                    village, cache, camera_start, camera_end, offset)
                self._present_layers()
            started = time.perf_counter()
            while True:
                time.sleep(1 / 60)
                progress = min(1.0, (time.perf_counter() - started) / duration)
                update_frame(progress)
                self._present_layers()
                if self._poll_animation_input():
                    interrupted = True
                    break
                if progress >= 1.0:
                    break
        finally:
            self._motion = None
        self._motion_cache = None if interrupted else motion_cache
        if interrupted:
            return True
        # The endpoint frame is already on screen. The game loop can reuse it
        # rather than immediately rebuilding the same world a second time.
        return True

    def _poll_animation_input(self) -> bool:
        """Queue actionable events and cut a tween short when the player acts."""
        interrupted = False
        for event in tcod.event.get():
            if isinstance(event, tcod.event.MouseMotion):
                self._set_hover_from_position(event.position)
                continue
            if isinstance(event, tcod.event.MouseButtonDown):
                if (event.button == tcod.event.MouseButton.LEFT
                        and self._last_village is not None):
                    at = self._mouse_world_tile(event.position, self._last_village)
                    if at is not None:
                        self._hover_world_tile = at
                        self._pending_events.append(("inspect_at", at[0], at[1]))
                        interrupted = True
                continue
            if isinstance(event, tcod.event.KeyUp):
                self._pending_events.append(event)
                continue
            if isinstance(event, tcod.event.KeyDown):
                action = world_action_for_key(
                    event.sym, bool(event.mod & tcod.event.Modifier.SHIFT))
                if action is None or (action[0] == "move" and event.repeat):
                    continue
                self._pending_events.append(event)
                interrupted = True
                if action[0] == "move":
                    self._skip_next_animation = True
                continue
            if isinstance(event, (tcod.event.Quit, tcod.event.WindowResized)):
                self._pending_events.append(event)
                interrupted = True
        return interrupted

    def _camera_fits_cache(self, camera, player_end, cache: dict,
                           view_width: int, view_height: int,
                           offset) -> bool:  # type: ignore[no-untyped-def]
        """Check that a cached scene can show the next viewport/player position."""
        anchor_x, anchor_y = cache["anchor"]
        buffer_width, buffer_height = cache["buffer_size"]
        if camera is None:
            return False
        source_x = camera[0] - anchor_x
        source_y = camera[1] - anchor_y
        if (source_x < 0 or source_y < 0
                or source_x + view_width > buffer_width
                or source_y + view_height > buffer_height):
            return False
        screen_x = player_end[0] - camera[0] + offset[0]
        screen_y = player_end[1] - camera[1] + offset[1]
        return 0 <= screen_x < view_width and 0 <= screen_y < view_height

    def _finish_motion_cache(self) -> None:
        """Refresh the world/UI after a held movement sequence ends."""
        if self._motion_cache is None:
            return
        self._motion_cache = None
        if self.context is not None and self._last_village is not None:
            self.render(self._last_village)

    def _draw_world_cell(self, village, sx: int, sy: int,
                         wx: int, wy: int) -> None:  # type: ignore[no-untyped-def]
        gm = village.game_map
        if not gm.in_bounds(wx, wy) or not gm.explored[wy, wx]:
            return
        glyph, fg = T.glyph_at(gm, wx, wy)
        sprite = self.sprite_for_tile(gm, wx, wy)
        bg = None
        tutorial_hearth_lit = (
            getattr(village, "tutorial_mode", False)
            and getattr(village, "hearth_lit_ticks", 0) > 0
            and any(obj.get("id") == "kitchen_stove"
                    for obj in village.objects_at.get((wx, wy), ())))
        if tutorial_hearth_lit:
            fg, bg = (255, 178, 88), (88, 38, 12)
        if gm.visible[wy, wx]:
            light = village.light
            brightness = float(light[wy, wx]) if light is not None else 1.0
            near = max(abs(wx - village.player.x), abs(wy - village.player.y))
            tile_id = int(gm.tiles[wy, wx])
            if tile_id == T.FARM and hasattr(village, "clock_pts"):
                from omnisim.core.clock import day_of as _day
                from omnisim.systems.crops import ensure_plots as _fp, ripe as _ripe
                for plot in _fp(village):
                    if plot["x"] == wx and plot["y"] == wy:
                        if _ripe(plot, _day(village.clock_pts)):
                            fg = (255, 215, 100)
                        elif plot["crop"] is not None:
                            fg = (120, 200, 120)
                        break
            if (self.settings.world_tileset != "everrogue"
                    and near <= HILITE_RADIUS and tile_id in INTERACT_BG):
                bg = INTERACT_BG[tile_id]
            elif (self.settings.world_tileset != "everrogue"
                  and tile_id == T.DOOR and not gm.doors[wy, wx] and near <= 2):
                bg = DOOR_BG
            if getattr(village, "tutorial_mode", False):
                if tutorial_hearth_lit:
                    bg = (88, 38, 12)
                elif (wx, wy) == getattr(village, "guidance_target", None):
                    bg = (105, 78, 25)
                elif (wx, wy) in getattr(village, "guidance_route", ()):
                    bg = (18, 58, 70)
        else:
            brightness = MEMORY_DIM
        brightness = self._visual_brightness(village, wx, wy, brightness)
        self._draw_lit_cell(
            sx, sy, glyph,
            fg=self._shade_color((255, 255, 255) if sprite else fg, brightness),
            bg=self._shade_color(bg, brightness) if bg else self._world_background(brightness),
            sprite=sprite, brightness=brightness)

    def _draw_world_overlays_at(self, village, sx: int, sy: int,
                                wx: int, wy: int) -> None:  # type: ignore[no-untyped-def]
        gm = village.game_map
        if not self._ground_is_known_at(village, wx, wy):
            return
        if (wx, wy) in village.ground:
            sprite = self.sprite_for_ground(village, wx, wy)
            sprite_fg, sprite_bg = self._sprite_colors_at(village, wx, wy)
            brightness = self._visual_brightness(
                village, wx, wy, float(village.light[wy, wx]) if village.light is not None else 1.0)
            self._draw_lit_cell(sx, sy, LOOT_GLYPH,
                                 fg=sprite_fg if sprite else LOOT_FG,
                                 bg=sprite_bg if sprite else LOOT_BG,
                                 sprite=sprite, brightness=brightness)
        if not gm.visible[wy, wx]:
            return  # Remember static loot, never disclose hidden moving actors.
        for entity in village.entities():
            if (entity is village.player or (entity.x, entity.y) != (wx, wy)):
                continue
            glyph, fg, bg = entity_style(entity, village.watch_id)
            sprite = self.sprite_for_entity(entity)
            sprite_fg, sprite_bg = self._sprite_colors_at(village, wx, wy)
            brightness = self._visual_brightness(
                village, wx, wy, float(village.light[wy, wx]) if village.light is not None else 1.0)
            self._draw_lit_cell(
                sx, sy, glyph,
                fg=sprite_fg if sprite else fg,
                bg=bg or sprite_bg, sprite=sprite, brightness=brightness)

    @staticmethod
    def _motion_entity_state(village) -> dict:  # type: ignore[no-untyped-def]
        return {
            entity.id: (entity.x, entity.y, entity.glyph, tuple(entity.color),
                        entity.get("role"), entity.get("activity", "idle"))
            for entity in village.entities() if entity is not village.player
        }

    @staticmethod
    def _motion_ground_state(village) -> dict:  # type: ignore[no-untyped-def]
        return {
            at: tuple((item.get("id"), int(item.get("quantity", 1)))
                      for item in items)
            for at, items in village.ground_items.items()
        }

    def _capture_motion_state(self, village, cache: dict) -> None:  # type: ignore[no-untyped-def]
        gm = village.game_map
        cache["visible"] = gm.visible.copy()
        cache["explored"] = gm.explored.copy()
        cache["tiles"] = gm.tiles.copy()
        cache["doors"] = gm.doors.copy()
        cache["light"] = None if village.light is None else village.light.copy()
        cache["player"] = (village.player.x, village.player.y)
        cache["entities"] = self._motion_entity_state(village)
        cache["ground"] = self._motion_ground_state(village)

    def _refresh_motion_cache(self, village, cache: dict,
                              camera_start, camera_end,
                              offset) -> None:  # type: ignore[no-untyped-def]
        """Redraw only changed visible cells in the cached movement viewport."""
        gm = village.game_map
        view_width, view_height = self.viewport
        anchor_x, anchor_y = cache["anchor"]
        buffer_width, buffer_height = cache["buffer_size"]
        x0 = max(0, math.floor(min(camera_start[0], camera_end[0])) - offset[0])
        y0 = max(0, math.floor(min(camera_start[1], camera_end[1])) - offset[1])
        x1 = min(gm.width,
                 math.ceil(max(camera_start[0], camera_end[0]) + view_width) - offset[0])
        y1 = min(gm.height,
                 math.ceil(max(camera_start[1], camera_end[1]) + view_height) - offset[1])
        x0, y0 = max(0, x0), max(0, y0)
        if x1 <= x0 or y1 <= y0:
            self._capture_motion_state(village, cache)
            cache["camera"] = tuple(camera_end)
            return

        region = np.s_[y0:y1, x0:x1]
        changed_mask = (
            (gm.visible[region] != cache["visible"][region])
            | (gm.explored[region] != cache["explored"][region])
            | (gm.tiles[region] != cache["tiles"][region])
            | (gm.doors[region] != cache["doors"][region])
        )
        if (village.light is None) != (cache["light"] is None):
            changed_mask[:] = True
        elif village.light is not None and cache["light"] is not None:
            changed_mask |= np.abs(
                village.light[region] - cache["light"][region]) > 1e-4
        changed = {
            (x0 + int(dx), y0 + int(dy))
            for dy, dx in np.argwhere(changed_mask)
        }

        # Interaction highlights are centered on the moving player even when
        # visibility/brightness happens to be unchanged.
        for px, py in (cache["player"], (village.player.x, village.player.y)):
            for wy in range(max(y0, py - HILITE_RADIUS),
                            min(y1, py + HILITE_RADIUS + 1)):
                for wx in range(max(x0, px - HILITE_RADIUS),
                                min(x1, px + HILITE_RADIUS + 1)):
                    tile_id = int(gm.tiles[wy, wx])
                    if tile_id in INTERACT_BG or (
                            tile_id == T.DOOR and not gm.doors[wy, wx]
                            and max(abs(wx - px), abs(wy - py)) <= 2):
                        changed.add((wx, wy))

        # Crop readiness and mobile/ground overlays can change without a tile
        # or FOV delta, so refresh their cells while they are in this view.
        for at in getattr(village, "crops", {}):
            if x0 <= at[0] < x1 and y0 <= at[1] < y1:
                changed.add(at)
        entities = self._motion_entity_state(village)
        for entity_state in (cache["entities"], entities):
            for ex, ey, *_ in entity_state.values():
                if x0 <= ex < x1 and y0 <= ey < y1:
                    changed.add((ex, ey))
        ground = self._motion_ground_state(village)
        for pile_state in (cache["ground"], ground):
            for gx, gy in pile_state:
                if x0 <= gx < x1 and y0 <= gy < y1:
                    changed.add((gx, gy))

        # If a connected wall changes, adjacent topology sprites may rotate.
        wall_tiles = (T.WALL_WOOD, T.WALL_STONE, T.WALL_PALISADE)
        for wx, wy in tuple(changed):
            if not gm.in_bounds(wx, wy):
                continue
            old_tile = int(cache["tiles"][wy, wx])
            new_tile = int(gm.tiles[wy, wx])
            if old_tile in wall_tiles or new_tile in wall_tiles:
                changed.update(
                    (wx + dx, wy + dy)
                    for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0))
                    if gm.in_bounds(wx + dx, wy + dy))

        if self.settings.world_tileset == "everrogue":
            # Local shade depends on neighboring visibility and solid tiles.
            # Refresh that fringe too, so held motion/felled trees leave no
            # stale squares around the old shadow boundary.
            changed.update(
                (wx + dx, wy + dy) for wx, wy in tuple(changed)
                for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                if gm.in_bounds(wx + dx, wy + dy))

        for wx, wy in changed:
            if not (x0 <= wx < x1 and y0 <= wy < y1):
                continue
            sx, sy = wx - anchor_x + offset[0], wy - anchor_y + offset[1]
            if not (0 <= sx < buffer_width and 0 <= sy < buffer_height):
                continue
            self._draw_world_cell(village, sx, sy, wx, wy)
            self._draw_world_overlays_at(village, sx, sy, wx, wy)

        self._capture_motion_state(village, cache)
        cache["camera"] = tuple(camera_end)
        if changed:
            self._world_texture = None

    # -- frame --
    def _frame_cell_extras(self, village) -> None:  # type: ignore[no-untyped-def]
        """Hook for subclasses: cell overlays drawn after tiles and entities
        but before the world texture is baked for this frame."""

    def render(self, village) -> None:  # type: ignore[no-untyped-def]
        assert self.console is not None and self.context is not None
        self._last_village = village
        self.overlays.close()
        self._sync_console_size()
        assert self.world is not None
        con = self.console
        self.world.clear()
        self._world_texture = None
        gm = village.game_map
        vw, vh = self.viewport
        if self._motion is None:
            self._motion_cache = None
            cam_x, cam_y, off_x, off_y = compute_camera(
                gm.width, gm.height, vw, vh,
                village.player.x, village.player.y)
            render_vw, render_vh = vw, vh
        else:
            cam_x, cam_y = self._motion["anchor"]
            off_x, off_y = self._motion["offset"]
            render_vw = min(self._motion["buffer_width"], self.console.width)
            render_vh = min(self._motion["buffer_height"], self.console.height)
        self._active_camera = (cam_x, cam_y, off_x, off_y)
        light = village.light

        for sy in range(render_vh):
            for sx in range(render_vw):
                wx = sx - off_x + cam_x
                wy = sy - off_y + cam_y
                if not gm.in_bounds(wx, wy):
                    continue
                if not gm.explored[wy, wx]:
                    continue  # Blank canvas: no hidden geometry is disclosed.
                glyph, fg = T.glyph_at(gm, wx, wy)
                sprite = self.sprite_for_tile(gm, wx, wy)
                bg = None
                if gm.visible[wy, wx]:
                    b = float(light[wy, wx]) if light is not None else 1.0
                    near = max(abs(wx - village.player.x), abs(wy - village.player.y))
                    tid = int(gm.tiles[wy, wx])
                    if tid == T.FARM and hasattr(village, "clock_pts"):
                        from omnisim.core.clock import day_of as _day
                        from omnisim.systems.crops import ensure_plots as _fp, ripe as _ripe
                        for _p in _fp(village):
                            if _p["x"] == wx and _p["y"] == wy:
                                if _ripe(_p, _day(village.clock_pts)):
                                    fg = (255, 215, 100)  # ripe rows glow gold
                                elif _p["crop"] is not None:
                                    fg = (120, 200, 120)  # growing green
                                break
                    if (self.settings.world_tileset != "everrogue"
                            and near <= HILITE_RADIUS and tid in INTERACT_BG):
                        bg = INTERACT_BG[tid]
                    elif (self.settings.world_tileset != "everrogue"
                          and tid == T.DOOR and not gm.doors[wy, wx] and near <= 2):
                        bg = DOOR_BG
                else:
                    b = MEMORY_DIM
                b = self._visual_brightness(village, wx, wy, b)
                self._draw_lit_cell(
                    sx, sy, glyph,
                    fg=self._shade_color((255, 255, 255) if sprite else fg, b),
                    bg=self._shade_color(bg, b) if bg else self._world_background(b),
                    sprite=sprite, brightness=b)

        for wx, wy in village.ground:
            if self._ground_is_known_at(village, wx, wy):
                sx, sy = wx - cam_x + off_x, wy - cam_y + off_y
                if 0 <= sx < render_vw and 0 <= sy < render_vh:
                    sprite = self.sprite_for_ground(village, wx, wy)
                    sprite_fg, sprite_bg = self._sprite_colors_at(village, wx, wy)
                    brightness = self._visual_brightness(
                        village, wx, wy, float(light[wy, wx]) if light is not None else 1.0)
                    self._draw_lit_cell(
                        sx, sy, LOOT_GLYPH,
                        fg=sprite_fg if sprite else LOOT_FG,
                        bg=sprite_bg if sprite else LOOT_BG, sprite=sprite, brightness=brightness)

        for e in village.entities():
            if self._motion is not None and e is village.player:
                continue  # the player is drawn at a pixel-interpolated position
            if e is not village.player and not gm.visible[e.y, e.x]:
                continue  # can't see them — no free wallhacks
            sx = e.x - cam_x + off_x
            sy = e.y - cam_y + off_y
            if 0 <= sx < render_vw and 0 <= sy < render_vh:
                glyph, fg, bg = entity_style(e, village.watch_id)
                sprite = self.sprite_for_entity(e)
                sprite_fg, sprite_bg = self._sprite_colors_at(village, e.x, e.y)
                brightness = self._visual_brightness(
                    village, e.x, e.y, float(light[e.y, e.x]) if light is not None else 1.0)
                self._draw_lit_cell(
                    sx, sy, glyph,
                    fg=sprite_fg if sprite else fg,
                    bg=bg or sprite_bg, sprite=sprite, brightness=brightness)

        # Game-specific cell overlays (object visuals, guidance marks) land
        # here, BEFORE the world texture is baked — one texture build and one
        # present cover the whole frame instead of two of each.
        self._frame_cell_extras(village)

        pixel_width, pixel_height = self.context.sdl_renderer.output_size
        sidebar_x = vw * self.tile_width
        sidebar_width = max(1, pixel_width - sidebar_x)
        sidebar_lines = self._sidebar_lines(
            village,
            max(12, int((sidebar_width - 24) / max(1, self.ui.measure("M")))),
            max(1, pixel_height // self.ui.line_height()))
        image = self.ui.new_overlay(pixel_width, pixel_height)
        minimap_height = (round(min(max(1, sidebar_width - 24), 240)
                                * gm.height / gm.width) + 28)
        self.ui.draw_sidebar(
            image, x=sidebar_x, y=minimap_height, width=sidebar_width,
            height=max(1, pixel_height - minimap_height), lines=sidebar_lines)
        status_y = vh * self.tile_height
        self.ui.draw_status(
            image, width=sidebar_x, y=status_y,
            height=max(1, pixel_height - status_y),
            lines=self._status_lines(village))
        self._base_ui_image = image
        self._base_ui_texture = None
        self._present_layers()

    # -- modal panels (CDDA-style: boxed list over the live frame) --
    def _boxed(self, colored_lines, footer="") -> None:  # type: ignore[no-untyped-def]
        """Draw a pixel-space modal over the current world/UI frame."""
        self._open_overlay(colored_lines, footer=footer)
        self._present_layers()

    def _open_overlay(self, lines, *, footer="", choice_start=None,
                      choice_count=0, selected_index=0) -> None:
        self._modal_lines = list(lines)
        self._modal_footer = footer
        self._modal_choice_start = choice_start
        self._modal_choice_count = choice_count
        self._modal_selected_index = selected_index
        self.overlays.open(
            self._modal_lines, footer=footer, choice_start=choice_start,
            choice_count=choice_count, selected_index=selected_index)

    def render_profile(self, village, npc) -> None:  # type: ignore[no-untyped-def]
        """Centered profile card over the live frame. Any key closes it."""
        from omnisim.ui.sidebar import profile_lines
        self.render(village)  # fresh frame underneath
        self._boxed(profile_lines(village, npc), footer="i:close")

    def render_inspect(self, village, npc):  # type: ignore[no-untyped-def]
        """Profile card plus lettered pocket rows. Returns [(item, qty)] for choose()."""
        from omnisim.entities.items import item_name
        from omnisim.ui.sidebar import inspectable_pocket, profile_lines
        rows = inspectable_pocket(npc) if npc.get("role") != "player" else []
        self.render(village)
        lines = profile_lines(village, npc)
        if rows:
            lines.append([("Lift (Sneaking applies):", (230, 200, 120), (12, 12, 22))])
            for i, (item_id, qty) in enumerate(rows):
                lines.append([(f"  {chr(97 + i)}) {item_name(item_id)} ×{qty}",
                               (220, 220, 220), (12, 12, 22))])
        self._open_overlay(
            lines, footer="letter: lift · Esc closes",
            choice_start=len(profile_lines(village, npc)) + 1,
            choice_count=len(rows))
        self._present_layers()
        return rows

    def render_inspect_tile(self, village, title: str, tile_rows: list[str],
                            item_rows: list[str], details: str = "",
                            footer: str = "", selected_index: int = 0) -> None:
        """Show tile facts beside its ground, surface, and container contents."""
        if not self._modal_underlay_is_current(village, "inspect"):
            self.render(village)
        self.overlays.open_columns(
            title, "TILE / OBJECTS", "ITEMS & CONTENTS",
            tile_rows, item_rows, 1 if item_rows else 0, selected_index, details, footer,
            screen_kind="inspect")
        self._present_layers()

    def _modal_underlay_is_current(self, village, screen_kind: str) -> bool:  # type: ignore[no-untyped-def]
        if (not self.overlays.active or self.overlays.screen_kind != screen_kind
                or self._last_village is not village):
            return False
        if self.context is None:
            return True
        renderer = self.context.sdl_renderer
        return renderer is None or tuple(renderer.output_size) == self._frame_size

    def _is_double_click(self, scope: str, target: tuple[int, int], event) -> bool:  # type: ignore[no-untyped-def]
        timestamp = int(getattr(event, "timestamp_ns", 0)) or time.perf_counter_ns()
        previous = self._last_mouse_click
        is_double = bool(
            previous is not None and previous[0] == scope
            and previous[1] == target
            and 0 <= timestamp - previous[2] <= 450_000_000)
        self._last_mouse_click = None if is_double else (scope, target, timestamp)
        return is_double

    def render_list(self, village, title: str, rows, footer: str = "",
                    selected_index: int = 0, *, selectable: bool = True) -> None:
        """Centered plain-text panel (work menu, village board, pack).

        A row may also arrive as pre-colored (text, fg, bg) segments — the
        tree portraits — which pass through without letter markers and draw
        mono cell-aligned."""
        self.render(village)
        lines = [[(title, (240, 240, 240), (8, 8, 8))], [("", (0, 0, 0), (8, 8, 8))]]
        plain_index = 0
        for r in rows:
            if isinstance(r, list):
                lines.append(list(r))
                continue
            marker = f"{chr(97 + plain_index)}) " if plain_index < 26 \
                else f"{plain_index + 1:>2}. "
            if not selectable:
                marker = ""
            plain_index += 1
            lines.append([(marker + r, (195, 195, 195), (8, 8, 8))])
        self._open_overlay(
            lines, footer=footer or "↑/↓ select · Enter choose · a-z quick select · Esc back · PgUp/PgDn",
            choice_start=2 if selectable else None,
            choice_count=max(0, plain_index) if selectable else 0,
            selected_index=selected_index)
        self._present_layers()

    def render_transfer_list(self, village, title: str, left_rows: list[str],
                             right_rows: list[str], pane: int, left_index: int,
                             right_index: int, details: str = "",
                             left_title: str = "PACK", right_title: str = "STORAGE",
                             footer: str = "↑/↓ select · ←/→ switch pane · Enter move one · m amount · M stack · , all · e examine · Esc close",
                             *, left_selectable=None,
                             right_selectable=None) -> None:
        """Render a paired pack/container browser with a persistent selection.

        The optional selectable masks limit which rows of each pane respond to
        hover and keyboard selection; masked-out rows are read-only info.
        """
        if not self._modal_underlay_is_current(village, "transfer"):
            self.render(village)
        self.overlays.open_columns(
            title, left_title, right_title, left_rows, right_rows, pane,
            left_index if pane == 0 else right_index, details, footer,
            screen_kind="transfer",
            left_selectable=left_selectable, right_selectable=right_selectable)
        self._present_layers()

    def render_fishing_scene(self, village, title: str, rows, status: str,
                             footer: str, popup: str = "", popup_id=None) -> None:
        """Render the interactive, colored fishing map without side panes."""
        if not self._modal_underlay_is_current(village, "fishing"):
            self.render(village)
        self.overlays.open_fishing_scene(title, rows, status, footer, popup,
                                         popup_id)
        self._present_layers()

    def render_anvil_scene(self, village, title: str, rows, status: str,
                           footer: str, hud: dict) -> None:
        """Render the forge canvas, meters, test slider, and clickable controls."""
        if not self._modal_underlay_is_current(village, "anvil"):
            self.render(village)
        self.overlays.open_anvil_scene(title, rows, status, footer, hud)
        self._present_layers()

    def wait_fishing_action(self, cursor: tuple[int, int], *,
                            tick_s: float = 1 / 30):
        """Return map aiming, tool selection, use, close, or an animation tick."""
        while True:
            events = tcod.event.wait(tick_s)
            if not events:
                return ("tick", cursor)
            for event in events:
                if isinstance(event, tcod.event.Quit):
                    self.overlays.close()
                    self._present_layers()
                    return ("close", cursor)
                if isinstance(event, tcod.event.WindowResized):
                    return ("redraw", cursor)
                if isinstance(event, (tcod.event.MouseMotion,
                                      tcod.event.MouseButtonDown)):
                    position = getattr(event, "position", None)
                    rect = self.overlays.fishing_map_rect
                    if position is None or rect is None:
                        continue
                    x0, y0, char_w, line_h, rows = rect
                    x = int((position.x - x0) // char_w)
                    y = int((position.y - y0) // line_h)
                    if 0 <= x < 78 and 0 <= y < rows:
                        aimed = (x, y)
                        if isinstance(event, tcod.event.MouseButtonDown):
                            if event.button == tcod.event.MouseButton.LEFT:
                                return ("use", aimed)
                        elif aimed != cursor:
                            return ("aim", aimed)
                    continue
                if not isinstance(event, tcod.event.KeyDown):
                    continue
                if event.sym in (tcod.event.KeySym.ESCAPE, tcod.event.KeySym.Q):
                    self.overlays.close()
                    self._present_layers()
                    return ("close", cursor)
                if event.sym in (tcod.event.KeySym.RETURN, tcod.event.KeySym.KP_ENTER,
                                 tcod.event.KeySym.SPACE):
                    return ("use", cursor)
                digit_keys = (tcod.event.KeySym.N1, tcod.event.KeySym.N2,
                              tcod.event.KeySym.N3,
                              tcod.event.KeySym.KP_1, tcod.event.KeySym.KP_2,
                              tcod.event.KeySym.KP_3)
                if event.sym in digit_keys:
                    name = getattr(event.sym, "name", "N1")
                    digit = int(name[-1])
                    return ("tool", digit)
                if event.sym in MOVE_KEYS:
                    dx, dy = MOVE_KEYS[event.sym]
                    return ("move", (max(0, min(77, cursor[0] + dx)),
                                     max(0, min(25, cursor[1] + dy))))
            return ("tick", cursor)

    def wait_anvil_action(self, cursor: tuple[int, int], *,
                          tick_s: float = 1 / 30):
        """Wait for forge map actions, drawn buttons, and Fusion test-slider input."""
        while True:
            events = tcod.event.wait(tick_s)
            if not events:
                return ("tick", cursor)
            for event in events:
                if isinstance(event, tcod.event.Quit):
                    self.overlays.close()
                    self._present_layers()
                    return ("close", cursor)
                if isinstance(event, tcod.event.WindowResized):
                    return ("redraw", cursor)
                if isinstance(event, tcod.event.MouseButtonUp):
                    self._anvil_slider_drag = False
                    continue
                if isinstance(event, (tcod.event.MouseMotion,
                                      tcod.event.MouseButtonDown)):
                    position = getattr(event, "position", None)
                    if position is None:
                        continue
                    down = isinstance(event, tcod.event.MouseButtonDown)
                    slider = self.overlays.anvil_slider_rect
                    if slider and (down or getattr(self, "_anvil_slider_drag", False)):
                        x1, y1, x2, y2 = slider
                        if down and x1 <= position.x <= x2 and y1 <= position.y <= y2:
                            self._anvil_slider_drag = True
                        if getattr(self, "_anvil_slider_drag", False):
                            power = round(max(0, min(100,
                                (position.x - x1) / max(1, x2 - x1) * 100)))
                            return ("fusion", power)
                    if down:
                        for name, (x1, y1, x2, y2) in self.overlays.anvil_control_rects.items():
                            if x1 <= position.x <= x2 and y1 <= position.y <= y2:
                                if name == "stop":
                                    self.overlays.close()
                                    self._present_layers()
                                    return ("close", cursor)
                                return ({"heat": "toggle_heat", "strike": "strike",
                                         "quench": "quench"}[name], cursor)
                    rect = self.overlays.fishing_map_rect
                    if rect is None:
                        continue
                    x0, y0, char_w, line_h, rows = rect
                    x = int((position.x - x0) // char_w)
                    y = int((position.y - y0) // line_h)
                    if 0 <= x < 78 and 0 <= y < rows:
                        aimed = (x, y)
                        if down:
                            if event.button == tcod.event.MouseButton.LEFT:
                                return ("strike", aimed)
                        elif aimed != cursor:
                            return ("aim", aimed)
                    continue
                if isinstance(event, tcod.event.KeyUp):
                    if event.sym == tcod.event.KeySym.H:
                        return ("cool", cursor)
                    continue
                if not isinstance(event, tcod.event.KeyDown):
                    continue
                if event.sym in (tcod.event.KeySym.ESCAPE, tcod.event.KeySym.Q):
                    if event.sym == tcod.event.KeySym.Q:
                        return ("quench", cursor)
                    self.overlays.close()
                    self._present_layers()
                    return ("close", cursor)
                if event.sym == tcod.event.KeySym.H:
                    return ("heat", cursor)
                if event.sym in (tcod.event.KeySym.RETURN, tcod.event.KeySym.KP_ENTER,
                                 tcod.event.KeySym.SPACE):
                    return ("strike", cursor)
                if event.sym in MOVE_KEYS:
                    dx, dy = MOVE_KEYS[event.sym]
                    return ("move", (max(0, min(77, cursor[0] + dx)),
                                      max(0, min(25, cursor[1] + dy))))
            return ("tick", cursor)

    def render_crafting_menu(self, village, title: str, category_rows: list[str],
                             recipe_rows: list[str], pane: int, category_index: int,
                             recipe_index: int, details: str = "") -> None:
        """Render CDDA-inspired recipe categories, list, and selected details."""
        if not self._modal_underlay_is_current(village, "crafting"):
            self.render(village)
        self.overlays.open_columns(
            title, "CATEGORIES", "CRAFTS & TASKS", category_rows, recipe_rows,
            pane, category_index if pane == 0 else recipe_index, details,
            "↑/↓ select · ←/→ or Tab switch · Enter start · Esc back",
            screen_kind="crafting")
        self._present_layers()

    def wait_crafting_action(self, category_count: int, recipe_count: int, pane: int,
                             category_index: int, recipe_index: int) -> tuple[str, int, int]:
        """Wait for category/recipe navigation or recipe confirmation."""
        while True:
            for event in tcod.event.wait():
                if isinstance(event, tcod.event.Quit):
                    return ("close", pane, category_index if pane == 0 else recipe_index)
                if isinstance(event, tcod.event.WindowResized):
                    return ("redraw", pane, category_index if pane == 0 else recipe_index)
                selected = category_index if pane == 0 else recipe_index
                count = category_count if pane == 0 else recipe_count
                if isinstance(event, tcod.event.MouseWheel):
                    if count:
                        selected = max(0, min(count - 1, selected - event.y))
                        return ("select", pane, selected)
                    continue
                if isinstance(event, (tcod.event.MouseMotion,
                                      tcod.event.MouseButtonDown)):
                    position = getattr(event, "position", None)
                    if position is not None:
                        picked = self.overlays.choice_at(int(position.x), int(position.y))
                        if picked is not None:
                            if picked < category_count:
                                target = (0, picked)
                                if isinstance(event, tcod.event.MouseButtonDown):
                                    self._is_double_click("crafting", target, event)
                                if target != (pane, selected):
                                    return ("select", *target)
                                continue
                            if picked < category_count + recipe_count:
                                target = (1, picked - category_count)
                                if isinstance(event, tcod.event.MouseButtonDown):
                                    if self._is_double_click("crafting", target, event):
                                        return ("choose", *target)
                                if target != (pane, selected):
                                    return ("select", *target)
                if not isinstance(event, tcod.event.KeyDown):
                    continue
                if event.sym in (tcod.event.KeySym.ESCAPE, tcod.event.KeySym.Q):
                    self.overlays.close()
                    self._present_layers()
                    return ("close", pane, selected)
                if event.sym in (tcod.event.KeySym.LEFT, tcod.event.KeySym.H,
                                 tcod.event.KeySym.N4, tcod.event.KeySym.KP_4):
                    return ("select", 0, category_index)
                if event.sym in (tcod.event.KeySym.RIGHT, tcod.event.KeySym.L,
                                 tcod.event.KeySym.N6, tcod.event.KeySym.KP_6,
                                 tcod.event.KeySym.TAB):
                    return ("select", 1, recipe_index)
                if event.sym in (tcod.event.KeySym.UP, tcod.event.KeySym.DOWN,
                                 tcod.event.KeySym.K, tcod.event.KeySym.J,
                                 tcod.event.KeySym.N8, tcod.event.KeySym.KP_8,
                                 tcod.event.KeySym.N2, tcod.event.KeySym.KP_2,
                                 tcod.event.KeySym.PAGEUP, tcod.event.KeySym.PAGEDOWN,
                                 tcod.event.KeySym.HOME, tcod.event.KeySym.END):
                    if not count:
                        continue
                    if event.sym == tcod.event.KeySym.HOME:
                        selected = 0
                    elif event.sym == tcod.event.KeySym.END:
                        selected = count - 1
                    else:
                        delta = 10 if event.sym in (tcod.event.KeySym.PAGEUP,
                                                    tcod.event.KeySym.PAGEDOWN) else 1
                        selected += -delta if event.sym in (tcod.event.KeySym.UP,
                                                              tcod.event.KeySym.K,
                                                              tcod.event.KeySym.N8,
                                                              tcod.event.KeySym.KP_8,
                                                              tcod.event.KeySym.PAGEUP) else delta
                        selected = max(0, min(count - 1, selected))
                    return ("select", pane, selected)
                if event.sym in (tcod.event.KeySym.RETURN, tcod.event.KeySym.KP_ENTER):
                    return ("choose", pane, selected)

    def render_inventory(self, village, title: str, hint: str,
                         left_title: str, right_title: str,
                         left_rows: list[dict], right_rows: list[dict],
                         pane: int, left_index: int, right_index: int,
                         details: str, stats: str, filter_text: str = "",
                         view_mode: str = "categories", navigation_mode: str = "item",
                         footer: str = "") -> None:
        """Render the character inventory selector with terminal-style hierarchy."""
        if not self._modal_underlay_is_current(village, "inventory"):
            self.render(village)
        self.overlays.open_inventory(
            title, hint, left_title, right_title, left_rows, right_rows, pane,
            left_index, right_index, details, stats, filter_text,
            view_mode, navigation_mode, footer)
        self._present_layers()

    def wait_inventory_filter(self, initial: str = "") -> str | None:
        """Edit the selector filter inline; Escape restores the prior filter."""
        text = initial
        self.overlays.update_inventory_filter(text)
        self._present_layers()
        while True:
            for event in tcod.event.wait():
                if isinstance(event, tcod.event.Quit):
                    self.overlays.inventory_filter_edit = False
                    return None
                if isinstance(event, tcod.event.WindowResized):
                    self._present_layers()
                    continue
                if not isinstance(event, tcod.event.KeyDown):
                    continue
                if event.sym == tcod.event.KeySym.ESCAPE:
                    self.overlays.inventory_filter_edit = False
                    self._present_layers()
                    return None
                if event.sym in (tcod.event.KeySym.RETURN, tcod.event.KeySym.KP_ENTER):
                    self.overlays.inventory_filter_edit = False
                    self._present_layers()
                    return text.strip()
                if event.sym == tcod.event.KeySym.BACKSPACE:
                    text = text[:-1]
                else:
                    name = getattr(event.sym, "name", "")
                    if name == "SPACE":
                        text += " "
                    elif len(name) == 1 and name.isalnum():
                        text += name.upper() if event.mod & tcod.event.Modifier.SHIFT else name.lower()
                    else:
                        continue
                self.overlays.update_inventory_filter(text)
                self._present_layers()

    def wait_transfer_action(self, left_count: int, right_count: int, pane: int,
                             left_index: int, right_index: int,
                             category_mode: bool = False,
                             left_groups: list[str] | None = None,
                             right_groups: list[str] | None = None, *,
                             left_selectable=None,
                             right_selectable=None,
                             tree_keys: bool = False,
                             stage_keys: bool = False,
                             hearth_keys: bool = False,
                             tick_s: float | None = None) -> tuple[str, int, int]:
        """CDDA advanced-inventory controls, adapted to Miska's two storage areas.

        The optional selectable masks list which row indices of each pane take
        part in selection; rows outside the mask are plain information, so the
        arrows, wheel, and letters skip them and an empty pane cannot be
        switched into at all.

        ``tree_keys`` arms the tree target screen's prototype bindings: W/S
        pick a target like the arrows, D asks for the fruit under the branch,
        A steps back to the branch itself, and Space acts like Enter.

        ``stage_keys`` arms the tree stage's aim mode instead: the arrows and
        W/S pick target rows or walk the tool list in aim mode, Left/Right
        walk along the aimed part, Tab toggles segment/fruit, and 1-4 pick a
        tool. ``tick_s`` turns the wait into an animation frame:
        when no event arrives within the slice it returns ``("tick", …)`` so
        the caller can advance sparks, debris and shake and redraw.

        ``hearth_keys`` arms the hearth screen's shortcuts: E stir, T turn,
        B beans, F fish, U utensil, I inspect, X finish, G gauges — each
        returned as ``("hearth_key", pane, letter)``. Digits 1-9 come
        through as ``("tool", pane, digit)`` either way.
        """
        total = left_count + right_count
        counts = (left_count, right_count)
        masks = (None if left_selectable is None else frozenset(left_selectable),
                 None if right_selectable is None else frozenset(right_selectable))

        def move(side: int, index: int, delta: int = 0, *, home: bool = False,
                 end: bool = False) -> int | None:
            count = counts[side]
            if masks[side] is None:
                if not count:
                    return None
                if home:
                    return 0
                if end:
                    return count - 1
                return max(0, min(count - 1, index + delta))
            valid = sorted(row for row in masks[side] if 0 <= row < count)
            if not valid:
                return None
            if home:
                return valid[0]
            if end:
                return valid[-1]
            later = [row for row in valid if row > index]
            earlier = [row for row in valid if row < index]
            if delta > 0:
                return later[0] if later else (index if index in valid else valid[-1])
            return earlier[-1] if earlier else (index if index in valid else valid[0])

        def snap(side: int, index: int) -> int | None:
            """Nearest selectable row when landing on a pane."""
            count = counts[side]
            if masks[side] is None:
                return max(0, min(count - 1, index)) if count else 0
            valid = sorted(row for row in masks[side] if 0 <= row < count)
            if not valid:
                return None
            if index in valid:
                return index
            return min(valid, key=lambda row: abs(row - index))

        while True:
            consumed = False
            if tick_s is not None:
                events = tcod.event.wait(tick_s)
            else:
                events = tcod.event.wait()
            for event in events:
                consumed = True
                if isinstance(event, tcod.event.Quit):
                    return ("close", pane, left_index if pane == 0 else right_index)
                if isinstance(event, tcod.event.WindowResized):
                    return ("redraw", pane, left_index if pane == 0 else right_index)
                selected = left_index if pane == 0 else right_index
                count = left_count if pane == 0 else right_count
                if isinstance(event, tcod.event.MouseWheel):
                    target = move(pane, selected, -event.y)
                    if target is None:
                        continue
                    return ("select", pane, target)
                if isinstance(event, (tcod.event.MouseMotion,
                                      tcod.event.MouseButtonDown)):
                    position = getattr(event, "position", None)
                    if position is not None:
                        if (stage_keys and isinstance(event, tcod.event.MouseButtonDown)
                                and event.button == tcod.event.MouseButton.LEFT):
                            art = self.overlays.art_cell_at(int(position.x),
                                                            int(position.y))
                            if art is not None:
                                # The tree itself is clickable: a press on the
                                # drawing aims whatever part it landed on.
                                return ("stage_art", art[0], art[1])
                        picked = self.overlays.choice_at(int(position.x), int(position.y))
                        if picked is not None and picked < total:
                            if picked < left_count:
                                target = (0, picked)
                            else:
                                target = (1, picked - left_count)
                            if isinstance(event, tcod.event.MouseButtonDown):
                                if event.button == tcod.event.MouseButton.RIGHT:
                                    return ("view", target[0], target[1])
                                if stage_keys:
                                    # The stage answers a single press: a
                                    # clicked target row aims it, a clicked
                                    # tool row swings — no double-click
                                    # ritual in front of the tree.
                                    return ("transfer_one", target[0], target[1])
                                if self._is_double_click("transfer", target, event):
                                    self.overlays.close()
                                    self._present_layers()
                                    return ("transfer_one", target[0], target[1])
                            if target != (pane, selected):
                                return ("select", target[0], target[1])
                if not isinstance(event, tcod.event.KeyDown):
                    continue
                if event.sym in (tcod.event.KeySym.ESCAPE, tcod.event.KeySym.Q):
                    self.overlays.close()
                    self._present_layers()
                    return ("close", pane, selected)
                if stage_keys and event.sym in (tcod.event.KeySym.W,
                                               tcod.event.KeySym.S,
                                               tcod.event.KeySym.UP,
                                               tcod.event.KeySym.DOWN,
                                               tcod.event.KeySym.K,
                                               tcod.event.KeySym.J,
                                               tcod.event.KeySym.N8,
                                               tcod.event.KeySym.KP_8,
                                               tcod.event.KeySym.N2,
                                               tcod.event.KeySym.KP_2):
                    step = -1 if event.sym in (tcod.event.KeySym.W,
                                               tcod.event.KeySym.UP,
                                               tcod.event.KeySym.K,
                                               tcod.event.KeySym.N8,
                                               tcod.event.KeySym.KP_8) else 1
                    return ("tree_vert", pane, step)
                if stage_keys and event.sym in (tcod.event.KeySym.LEFT,
                                               tcod.event.KeySym.H,
                                               tcod.event.KeySym.N4,
                                               tcod.event.KeySym.KP_4,
                                               tcod.event.KeySym.RIGHT,
                                               tcod.event.KeySym.L,
                                               tcod.event.KeySym.N6,
                                               tcod.event.KeySym.KP_6):
                    step = -1 if event.sym in (tcod.event.KeySym.LEFT,
                                               tcod.event.KeySym.H,
                                               tcod.event.KeySym.N4,
                                               tcod.event.KeySym.KP_4) else 1
                    return ("tree_move", pane, step)
                if stage_keys and event.sym == tcod.event.KeySym.TAB:
                    return ("tree_sub", pane, selected)
                if stage_keys and event.sym in (tcod.event.KeySym.N1,
                                               tcod.event.KeySym.N2,
                                               tcod.event.KeySym.N3,
                                               tcod.event.KeySym.N4,
                                               tcod.event.KeySym.N5,
                                               tcod.event.KeySym.N6,
                                               tcod.event.KeySym.N7,
                                               tcod.event.KeySym.N8,
                                               tcod.event.KeySym.N9,
                                               tcod.event.KeySym.KP_1,
                                               tcod.event.KeySym.KP_2,
                                               tcod.event.KeySym.KP_3,
                                               tcod.event.KeySym.KP_4,
                                               tcod.event.KeySym.KP_5,
                                               tcod.event.KeySym.KP_6,
                                               tcod.event.KeySym.KP_7,
                                               tcod.event.KeySym.KP_8,
                                               tcod.event.KeySym.KP_9):
                    digit = int(getattr(event.sym, "name", "N1")[1])
                    return ("tool", pane, digit)
                if hearth_keys and event.sym in (
                        tcod.event.KeySym.E, tcod.event.KeySym.T,
                        tcod.event.KeySym.B, tcod.event.KeySym.F,
                        tcod.event.KeySym.U, tcod.event.KeySym.I,
                        tcod.event.KeySym.X, tcod.event.KeySym.G):
                    letter = getattr(event.sym, "name", "E")[0].lower()
                    return ("hearth_key", pane, letter)
                if tree_keys and event.sym in (tcod.event.KeySym.W,
                                               tcod.event.KeySym.S):
                    target = move(pane, selected,
                                  -1 if event.sym == tcod.event.KeySym.W else 1)
                    if target is None:
                        return ("select", pane, selected)
                    return ("select", pane, target)
                if tree_keys and event.sym == tcod.event.KeySym.SPACE:
                    if stage_keys:
                        # The tree stage re-renders itself in place on every
                        # command — closing the overlay here would flash the
                        # bare world frame between two views of the same
                        # screen (the "closes and reopens" jolt).
                        return ("transfer_one", pane, selected)
                    self.overlays.close()
                    self._present_layers()
                    return ("transfer_one", pane, selected)
                if tree_keys and event.sym == tcod.event.KeySym.D:
                    return ("tree_fruit", pane, selected)
                if tree_keys and event.sym == tcod.event.KeySym.A:
                    return ("tree_back", pane, selected)
                if event.sym in (tcod.event.KeySym.LEFT, tcod.event.KeySym.H,
                                 tcod.event.KeySym.N4, tcod.event.KeySym.KP_4):
                    if masks[0] is not None:
                        target = snap(0, left_index)
                        if target is None:
                            return ("select", pane, selected)
                        return ("select", 0, target)
                    next_pane = 0
                    next_count = left_count
                    next_index = left_index
                    return ("select", next_pane, min(next_index, max(0, next_count - 1)))
                if event.sym in (tcod.event.KeySym.RIGHT, tcod.event.KeySym.L,
                                 tcod.event.KeySym.N6, tcod.event.KeySym.KP_6):
                    if masks[1] is not None:
                        target = snap(1, right_index)
                        if target is None:
                            return ("select", pane, selected)
                        return ("select", 1, target)
                    next_pane = 1
                    next_count = right_count
                    next_index = right_index
                    return ("select", next_pane, min(next_index, max(0, next_count - 1)))
                if event.sym == tcod.event.KeySym.TAB:
                    next_pane = 1 - pane
                    if masks[next_pane] is not None:
                        target = snap(next_pane,
                                      right_index if next_pane else left_index)
                        if target is None:
                            return ("select", pane, selected)
                        return ("select", next_pane, target)
                    next_count = right_count if next_pane else left_count
                    next_index = right_index if next_pane else left_index
                    return ("select", next_pane, min(next_index, max(0, next_count - 1)))
                if event.sym in (tcod.event.KeySym.UP, tcod.event.KeySym.DOWN,
                                 tcod.event.KeySym.K, tcod.event.KeySym.J,
                                 tcod.event.KeySym.N8, tcod.event.KeySym.KP_8,
                                 tcod.event.KeySym.N2, tcod.event.KeySym.KP_2,
                                 tcod.event.KeySym.PAGEUP, tcod.event.KeySym.PAGEDOWN,
                                 tcod.event.KeySym.HOME, tcod.event.KeySym.END):
                    if masks[pane] is not None:
                        if event.sym == tcod.event.KeySym.HOME:
                            target = move(pane, selected, home=True)
                        elif event.sym == tcod.event.KeySym.END:
                            target = move(pane, selected, end=True)
                        else:
                            step = 10 if event.sym in (tcod.event.KeySym.PAGEUP,
                                                       tcod.event.KeySym.PAGEDOWN) else 1
                            target = move(pane, selected, -step if event.sym in (
                                tcod.event.KeySym.UP, tcod.event.KeySym.K,
                                tcod.event.KeySym.N8, tcod.event.KeySym.KP_8,
                                tcod.event.KeySym.PAGEUP) else step)
                        if target is None:
                            return ("select", pane, selected)
                        return ("select", pane, target)
                    if not count:
                        continue
                    if event.sym == tcod.event.KeySym.HOME:
                        selected = 0
                    elif event.sym == tcod.event.KeySym.END:
                        selected = count - 1
                    else:
                        groups = left_groups if pane == 0 else right_groups
                        if category_mode and groups and event.sym in (
                                tcod.event.KeySym.UP, tcod.event.KeySym.DOWN,
                                tcod.event.KeySym.K, tcod.event.KeySym.J,
                                tcod.event.KeySym.N8, tcod.event.KeySym.KP_8,
                                tcod.event.KeySym.N2, tcod.event.KeySym.KP_2):
                            direction = -1 if event.sym in (
                                tcod.event.KeySym.UP, tcod.event.KeySym.K,
                                tcod.event.KeySym.N8, tcod.event.KeySym.KP_8) else 1
                            categories = []
                            for index, group in enumerate(groups):
                                if not categories or categories[-1][0] != group:
                                    categories.append((group, index))
                            current_group = next((i for i, (group, _first) in enumerate(categories)
                                                  if group == groups[selected]), 0)
                            next_group = max(0, min(len(categories) - 1,
                                                    current_group + direction))
                            return ("select", pane, categories[next_group][1])
                        delta = 10 if event.sym in (tcod.event.KeySym.PAGEUP,
                                                    tcod.event.KeySym.PAGEDOWN) else 1
                        selected += -delta if event.sym in (tcod.event.KeySym.UP,
                                                              tcod.event.KeySym.K,
                                                              tcod.event.KeySym.N8,
                                                              tcod.event.KeySym.KP_8,
                                                              tcod.event.KeySym.PAGEUP) else delta
                        selected = max(0, min(count - 1, selected))
                    return ("select", pane, selected)
                if event.sym in (tcod.event.KeySym.RETURN, tcod.event.KeySym.KP_ENTER):
                    if stage_keys:
                        # The tree stage keeps its own screen up: Enter aims
                        # and swings in place, so no close-and-reopen flash.
                        return ("transfer_one", pane, selected)
                    self.overlays.close()
                    self._present_layers()
                    return ("transfer_one", pane, selected)
                if event.sym == tcod.event.KeySym.M:
                    self.overlays.close()
                    self._present_layers()
                    if event.mod & tcod.event.Modifier.SHIFT:
                        return ("transfer_stack", pane, selected)
                    return ("transfer_amount", pane, selected)
                if event.sym == tcod.event.KeySym.COMMA:
                    self.overlays.close()
                    self._present_layers()
                    return ("transfer_all", pane, selected)
                if event.sym in (tcod.event.KeySym.F, tcod.event.KeySym.SLASH):
                    self.overlays.close()
                    self._present_layers()
                    return ("filter", pane, selected)
                if event.sym == tcod.event.KeySym.R:
                    return ("reset_filter", pane, selected)
                if event.sym == tcod.event.KeySym.S:
                    self.overlays.close()
                    self._present_layers()
                    return ("sort", pane, selected)
                if event.sym == tcod.event.KeySym.T:
                    return ("toggle_category", pane, selected)
                if event.sym == tcod.event.KeySym.C:
                    self.overlays.close()
                    self._present_layers()
                    return ("retarget", pane, selected)
                if event.sym in (tcod.event.KeySym.V, tcod.event.KeySym.I,
                                  tcod.event.KeySym.E):
                    self.overlays.close()
                    self._present_layers()
                    return ("view", pane, selected)
                key_name = getattr(event.sym, "name", "")
                if len(key_name) == 1 and key_name in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" and key_name not in "TV":
                    picked = ord(key_name) - ord("A")
                    if picked < left_count:
                        side, raw = 0, picked
                    elif picked < total:
                        side, raw = 1, picked - left_count
                    else:
                        continue
                    if masks[side] is not None:
                        valid = sorted(row for row in masks[side]
                                       if 0 <= row < counts[side])
                        landed = next((row for row in valid if row >= raw), None)
                        if landed is None:
                            continue
                        return ("select", side, landed)
                    return ("select", side, raw)
            if not consumed:
                # The wait timed out with nothing on the queue: an animation
                # frame for whoever drives one (the tree stage).
                return ("tick", pane, left_index if pane == 0 else right_index)

    def wait_transfer_filter(self, village, initial: str = "") -> str | None:
        """Edit the active advanced-inventory pane filter."""
        text = initial

        def redraw() -> None:
            self.render(village)
            line = [(f"Filter: {text}_", (220, 220, 220), (8, 8, 8))]
            self._open_overlay([line], footer="Enter applies · Esc cancels")
            self._present_layers()

        redraw()
        while True:
            for event in tcod.event.wait():
                if isinstance(event, tcod.event.Quit):
                    return None
                if isinstance(event, tcod.event.WindowResized):
                    redraw()
                    continue
                if not isinstance(event, tcod.event.KeyDown):
                    continue
                if event.sym == tcod.event.KeySym.ESCAPE:
                    self.overlays.close()
                    self._present_layers()
                    return None
                if event.sym in (tcod.event.KeySym.RETURN, tcod.event.KeySym.KP_ENTER):
                    self.overlays.close()
                    self._present_layers()
                    return text.strip()
                if event.sym == tcod.event.KeySym.BACKSPACE:
                    text = text[:-1]
                else:
                    name = getattr(event.sym, "name", "")
                    if name == "SPACE":
                        text += " "
                    elif len(name) == 1 and name.isalnum():
                        text += name.upper() if event.mod & tcod.event.Modifier.SHIFT else name.lower()
                    else:
                        continue
                redraw()

    def wait_transfer_quantity(self, village, maximum: int) -> int | None:
        """Prompt for a positive amount when moving part of an item stack."""
        if maximum <= 0:
            return None
        value = ""

        def redraw() -> None:
            self.render(village)
            line = [(f"Move how many? 1-{maximum}: {value or '_'}", (220, 220, 220), (8, 8, 8))]
            self._open_overlay([line], footer="Enter moves this amount · Esc cancels")
            self._present_layers()

        redraw()
        while True:
            for event in tcod.event.wait():
                if isinstance(event, tcod.event.Quit):
                    return None
                if isinstance(event, tcod.event.WindowResized):
                    redraw()
                    continue
                if not isinstance(event, tcod.event.KeyDown):
                    continue
                if event.sym == tcod.event.KeySym.ESCAPE:
                    self.overlays.close()
                    self._present_layers()
                    return None
                if event.sym in (tcod.event.KeySym.RETURN, tcod.event.KeySym.KP_ENTER):
                    self.overlays.close()
                    self._present_layers()
                    try:
                        return max(1, min(maximum, int(value or "1")))
                    except ValueError:
                        return None
                if event.sym == tcod.event.KeySym.BACKSPACE:
                    value = value[:-1]
                else:
                    name = getattr(event.sym, "name", "")
                    digit = name[1:] if name.startswith("N") else name
                    if len(digit) != 1 or not digit.isdigit():
                        continue
                    if not value or value == "0":
                        value = digit
                    else:
                        value += digit
                redraw()

    def wait_inventory_action(self, left_rows: list[dict], right_rows: list[dict], pane: int,
                              left_index: int, right_index: int,
                              navigation_mode: str = "item", filter_text: str = "") -> tuple[str, int, int]:
        """Navigate inventory/gear columns with CDDA-style item and category controls."""
        rows_by_pane = (left_rows, right_rows)
        item_count = lambda rows: sum(row.get("kind") == "item" for row in rows)
        counts = (item_count(left_rows), item_count(right_rows))

        def category_target(rows: list[dict], selected: int, direction: int) -> int:
            groups: list[tuple[str, int]] = []
            for row in rows:
                if row.get("kind") != "item":
                    continue
                group = str(row.get("group", ""))
                if not groups or groups[-1][0] != group:
                    groups.append((group, int(row.get("index", 0))))
            if not groups:
                return 0
            current_group = next((i for i, (group, _first) in enumerate(groups)
                                  if group == next((row.get("group") for row in rows
                                                    if row.get("kind") == "item" and
                                                    row.get("index") == selected), None)), 0)
            next_group = max(0, min(len(groups) - 1, current_group + direction))
            return groups[next_group][1]

        while True:
            for event in tcod.event.wait():
                if isinstance(event, tcod.event.Quit) or (
                        isinstance(event, tcod.event.KeyDown) and event.sym in
                        (tcod.event.KeySym.ESCAPE, tcod.event.KeySym.Q)):
                    self.overlays.close()
                    self._present_layers()
                    return ("close", pane, left_index if pane == 0 else right_index)
                if isinstance(event, tcod.event.WindowResized):
                    return ("redraw", pane, left_index if pane == 0 else right_index)
                selected = left_index if pane == 0 else right_index
                count = counts[pane]
                rows = rows_by_pane[pane]
                if isinstance(event, tcod.event.MouseWheel):
                    if count:
                        target = (category_target(rows, selected, -1 if event.y > 0 else 1)
                                  if navigation_mode == "category" and event.y
                                  else max(0, min(count - 1, selected - event.y)))
                        return ("select", pane, target)
                    continue
                if isinstance(event, (tcod.event.MouseMotion,
                                      tcod.event.MouseButtonDown)):
                    position = getattr(event, "position", None)
                    if position is not None:
                        picked = self.overlays.choice_at(int(position.x), int(position.y))
                        if picked is not None:
                            picked_pane, picked_index = divmod(picked, 100000)
                            if picked_pane in (0, 1) and picked_index < counts[picked_pane]:
                                target = (picked_pane, picked_index)
                                if isinstance(event, tcod.event.MouseButtonDown):
                                    if event.button == tcod.event.MouseButton.RIGHT:
                                        return ("menu", picked_pane, picked_index)
                                    if self._is_double_click("inventory", target, event):
                                        return ("activate", picked_pane, picked_index)
                                if target != (pane, selected):
                                    return ("select", picked_pane, picked_index)
                if not isinstance(event, tcod.event.KeyDown):
                    continue
                if event.sym in (tcod.event.KeySym.UP, tcod.event.KeySym.DOWN,
                                 tcod.event.KeySym.K, tcod.event.KeySym.J,
                                 tcod.event.KeySym.PAGEUP, tcod.event.KeySym.PAGEDOWN,
                                 tcod.event.KeySym.HOME, tcod.event.KeySym.END):
                    if not count:
                        continue
                    if event.sym in (tcod.event.KeySym.HOME,):
                        return ("select", pane, 0)
                    if event.sym in (tcod.event.KeySym.END,):
                        return ("select", pane, count - 1)
                    direction = -1 if event.sym in (tcod.event.KeySym.UP, tcod.event.KeySym.K,
                                                      tcod.event.KeySym.N8, tcod.event.KeySym.KP_8,
                                                      tcod.event.KeySym.PAGEUP) else 1
                    if navigation_mode == "category" and event.sym in (
                            tcod.event.KeySym.UP, tcod.event.KeySym.DOWN,
                            tcod.event.KeySym.K, tcod.event.KeySym.J,
                            tcod.event.KeySym.N8, tcod.event.KeySym.KP_8,
                            tcod.event.KeySym.N2, tcod.event.KeySym.KP_2):
                        return ("select", pane, category_target(rows, selected, direction))
                    amount = 10 if event.sym in (tcod.event.KeySym.PAGEUP,
                                                  tcod.event.KeySym.PAGEDOWN) else 1
                    delta = direction * amount
                    return ("select", pane, max(0, min(count - 1, selected + delta)))
                shifted = bool(event.mod & tcod.event.Modifier.SHIFT)
                if event.sym in (tcod.event.KeySym.LEFT, tcod.event.KeySym.H,
                                 tcod.event.KeySym.N4, tcod.event.KeySym.KP_4,
                                 tcod.event.KeySym.RIGHT, tcod.event.KeySym.L,
                                 tcod.event.KeySym.TAB):
                    next_pane = 1 - pane
                    if event.sym == tcod.event.KeySym.TAB and shifted:
                        next_pane = 1 - pane
                    next_count = counts[next_pane]
                    next_index = right_index if next_pane else left_index
                    return ("select", next_pane, min(next_index, max(0, next_count - 1)))
                action = inventory_action_for_key(event.sym)
                if action in ("toggle_navigation", "toggle_view", "filter", "reset_filter"):
                    return (action, pane, selected)
                if action and count:
                    if pane == 1 and action in ("wear", "drop", "use", "repack"):
                        if action == "wear":
                            action = "remove"
                        else:
                            continue
                    self.overlays.close()
                    self._present_layers()
                    return (action, pane, selected)
                key_name = getattr(event.sym, "name", "")
                if len(key_name) == 1 and key_name.isalpha():
                    key = key_name.lower()
                    for item_row in rows:
                        if item_row.get("kind") == "item" and item_row.get("hotkey") == key:
                            return ("activate", pane, int(item_row["index"]))

    def choose(self, n: int) -> int | None:
        """Navigate a modal list; arrows + Enter, letter quick-pick, Escape backs out."""
        import tcod.event
        letters = {}
        for i, name in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
            letters[getattr(tcod.event.KeySym, name)] = i
        selected = max(0, min(max(0, n - 1), self._modal_selected_index))
        while True:
            for event in tcod.event.wait():
                if isinstance(event, tcod.event.Quit):
                    self.overlays.close()
                    return None
                if isinstance(event, tcod.event.WindowResized):
                    if self._last_village is not None:
                        self.render(self._last_village)
                        self.overlays.open(
                            self._modal_lines, footer=self._modal_footer,
                            choice_start=self._modal_choice_start,
                            choice_count=self._modal_choice_count,
                            selected_index=selected)
                        self._present_layers()
                    continue
                if isinstance(event, tcod.event.MouseWheel):
                    if n:
                        selected = min(max(0, selected - event.y), n - 1)
                        self._modal_selected_index = selected
                        self.overlays.select(selected)
                        self._present_layers()
                    continue
                if isinstance(event, (tcod.event.MouseMotion,
                                      tcod.event.MouseButtonDown)):
                    position = getattr(event, "position", None)
                    if position is not None:
                        picked = self.overlays.choice_at(
                            int(position.x), int(position.y))
                        if picked is not None and picked < n:
                            if isinstance(event, tcod.event.MouseButtonDown):
                                self.overlays.close()
                                self._present_layers()
                                return picked
                            if picked != selected:
                                selected = picked
                                self._modal_selected_index = selected
                                self.overlays.select(selected)
                                self._present_layers()
                    continue
                if not isinstance(event, tcod.event.KeyDown):
                    continue
                if event.sym in (tcod.event.KeySym.ESCAPE, tcod.event.KeySym.Q):
                    self.overlays.close()
                    self._present_layers()
                    return None
                if n == 0 and event.sym in (tcod.event.KeySym.RETURN, tcod.event.KeySym.KP_ENTER):
                    self.overlays.close()
                    self._present_layers()
                    return None
                up_keys = (tcod.event.KeySym.UP, tcod.event.KeySym.K,
                           tcod.event.KeySym.N8, tcod.event.KeySym.KP_8)
                down_keys = (tcod.event.KeySym.DOWN, tcod.event.KeySym.J,
                             tcod.event.KeySym.N2, tcod.event.KeySym.KP_2)
                if event.sym in up_keys + down_keys + (
                        tcod.event.KeySym.PAGEUP, tcod.event.KeySym.PAGEDOWN,
                        tcod.event.KeySym.HOME, tcod.event.KeySym.END):
                    if not n:
                        continue
                    if event.sym == tcod.event.KeySym.HOME:
                        selected = 0
                    elif event.sym == tcod.event.KeySym.END:
                        selected = n - 1
                    elif event.sym in (tcod.event.KeySym.PAGEUP,
                                       tcod.event.KeySym.PAGEDOWN):
                        delta = -10 if event.sym == tcod.event.KeySym.PAGEUP else 10
                        selected = min(n - 1, max(0, selected + delta))
                    else:
                        delta = -1 if event.sym in up_keys else 1
                        selected = (selected + delta) % n
                    self._modal_selected_index = selected
                    self.overlays.select(selected)
                    self._present_layers()
                    continue
                if event.sym in (tcod.event.KeySym.RETURN, tcod.event.KeySym.KP_ENTER):
                    self.overlays.close()
                    self._present_layers()
                    return selected if n else None
                if event.sym in letters and letters[event.sym] < n:
                    self.overlays.close()
                    self._present_layers()
                    return letters[event.sym]

    def wait_any(self) -> None:
        """Block until any keypress (closes overlays)."""
        while True:
            for event in tcod.event.wait():
                if isinstance(event, tcod.event.Quit):
                    self.overlays.close()
                    return
                if isinstance(event, tcod.event.WindowResized):
                    lines, footer = self._modal_lines, self._modal_footer
                    if self._last_village is not None:
                        self.render(self._last_village)
                    self.overlays.open(lines, footer=footer)
                    self._present_layers()
                    continue
                if isinstance(event, tcod.event.MouseWheel):
                    self.overlays.scroll(-event.y * 3)
                    self._present_layers()
                    continue
                if isinstance(event, tcod.event.MouseButtonDown):
                    self.overlays.close()
                    self._present_layers()
                    return
                if isinstance(event, tcod.event.KeyDown) and event.sym in (
                        tcod.event.KeySym.UP, tcod.event.KeySym.PAGEUP,
                        tcod.event.KeySym.DOWN, tcod.event.KeySym.PAGEDOWN):
                    amount = -5 if event.sym == tcod.event.KeySym.PAGEUP else (
                        -1 if event.sym == tcod.event.KeySym.UP else (
                            5 if event.sym == tcod.event.KeySym.PAGEDOWN else 1))
                    self.overlays.scroll(amount)
                    self._present_layers()
                    continue
                if isinstance(event, tcod.event.KeyDown):
                    self.overlays.close()
                    self._present_layers()
                    return

    def render_summary_panel(self, village, title: str, lines: list, footer: str) -> None:
        """Colored completion/summary card; any key closes."""
        self.render(village)
        rows = [[(title, (240, 226, 170), (8, 8, 8))], [("", (0, 0, 0), (8, 8, 8))]]
        rows.extend([[*line] if isinstance(line, list) else [(line, (220, 220, 220), (8, 8, 8))]
                     for line in lines])
        self._open_overlay(rows, footer=footer)
        self._present_layers()
        self.wait_any()

    # -- cinematic dialogue: camera lean + speech bubbles + captions --

    def begin_cine(self, focus_world_xy: tuple[float, float],
                   target_scale: float = DIALOGUE_ZOOM_SCALE) -> None:
        """Lean the camera in toward a world point (the speakers' midpoint).
        The zoom eases in over ~half a second; the scene runs while it holds."""
        self.cine = {"focus": (float(focus_world_xy[0]), float(focus_world_xy[1])),
                     "scale": 1.0, "target": max(1.0, float(target_scale)),
                     "ending": False}
        self._cine_prev = time.perf_counter()

    def end_cine(self) -> None:
        """Ease back out; the scene's outro frames run until cine clears."""
        if self.cine is not None:
            self.cine["target"] = 1.0
            self.cine["ending"] = True
            self._cine_prev = time.perf_counter()

    @property
    def cine_active(self) -> bool:
        return self.cine is not None

    def _cine_tick(self) -> None:
        """Tween the zoom toward its target; clear it once the lean-out lands."""
        if self.cine is None:
            self._cine_applied = None
            return
        now = time.perf_counter()
        dt = min(0.1, max(0.0, now - (self._cine_prev or now)))
        self._cine_prev = now
        scale = self.cine["scale"] + (self.cine["target"] - self.cine["scale"]) \
            * min(1.0, dt * CINE_EASE)
        if self.cine.get("ending") and abs(scale - 1.0) <= CINE_SNAP:
            self.cine = None
            self._cine_applied = None
            return
        self.cine["scale"] = scale

    def _cine_apply(self, renderer) -> None:  # type: ignore[no-untyped-def]
        """Copy the world texture through the zoom crop and remember the
        transform the bubble/caption/float layers must share."""
        world_texture = self._world_texture
        assert world_texture is not None and self._last_village is not None
        camera, offset = self._display_camera(self._last_village)
        tw, th = self.tile_width, self.tile_height
        fx, fy = self.cine["focus"]
        focus_px = ((fx - camera[0] + offset[0]) * tw,
                    (fy - camera[1] + offset[1]) * th)
        width, height = renderer.output_size
        sx, sy, sw, sh = cine_crop(
            focus_px, (self.width * tw, self.height * th), (width, height),
            self.cine["scale"])
        renderer.copy(world_texture, source=(sx, sy, sw, sh),
                      dest=(0, 0, width, height))
        self._cine_applied = (self.cine["scale"], sx, sy)

    def set_dialogue_bubble(self, text: str, world_xy: tuple[int, int],
                            color: tuple[int, int, int] = (230, 226, 214),
                            label: str = "") -> None:
        """Pin one speech bubble above a tile for this frame; the runner
        refreshes it every frame, so it never needs its own fade."""
        if not text:
            return
        self.dialogue_bubble = {
            "text": str(text), "xy": (int(world_xy[0]), int(world_xy[1])),
            "color": tuple(color)[:3], "label": str(label)}

    def set_dialogue_caption(self, text: str) -> None:
        self.dialogue_caption = str(text) if text else None

    def set_dialogue_choices(self, options, selected: int = 0,
                             hint: str = "\u2191/\u2193 choose \u00b7 Enter confirm \u00b7 Esc leave") -> None:
        """A conversation choice list: a compact panel over the world, the
        highlighted row marked, Up/Down + Enter handled by the runner.
        An empty options list clears the panel."""
        rows = [str(option) for option in (options or [])]
        if not rows:
            self.dialogue_choices = None
            return
        self.dialogue_choices = {
            "options": rows,
            "selected": max(0, min(int(selected), len(rows) - 1)),
            "hint": str(hint)}

    def clear_dialogue_bubble(self) -> None:
        self.dialogue_bubble = None

    def clear_dialogue_cues(self) -> None:
        self.dialogue_bubble = None
        self.dialogue_caption = None
        self.dialogue_choices = None

    def _upload_rgba(self, image):  # type: ignore[no-untyped-def]
        assert self.context is not None
        texture = self.context.sdl_renderer.upload_texture(
            np.asarray(image, dtype=np.uint8))
        texture.blend_mode = BlendMode.BLEND
        return texture, image.width, image.height

    def _wrap_by_width(self, text: str, font, max_w: float) -> list[str]:  # type: ignore[no-untyped-def]
        lines: list[str] = []
        current = ""
        for word in text.split():
            candidate = f"{current} {word}".strip()
            if font.getlength(candidate) <= max_w or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

    def _bubble_image(self, text: str, label: str,
                      color: tuple[int, int, int], font_px: int):  # type: ignore[no-untyped-def]
        """A rounded speech bubble with the speaker's name in their color;
        built at the zoom-scaled font so it stays crisp mid-lean."""
        key = (text, label, color, font_px)
        cached = self._bubble_cache.get(key)
        if cached is not None:
            return cached[1]
        font = ImageFont.truetype(self._find_font(), font_px)
        max_w = min(BUBBLE_MAX_PX, max(120, font_px * 24))
        rows = self._wrap_by_width(text, font, max_w)
        line_h = int(font_px * 1.34)
        text_w = max(int(font.getlength(row)) for row in rows)
        label_w = int(font.getlength(label)) if label else 0
        pad = max(10, font_px * 2 // 3)
        label_h = (line_h + 2) if label else 0
        width = max(text_w, label_w) + pad * 2 + 4
        height = pad + label_h + len(rows) * line_h + pad - (2 if label else 6)
        image = Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=10,
                               fill=(17, 15, 23, 214), outline=(*color, 235),
                               width=2)
        y = pad
        if label:
            draw.text((pad, y), label, font=font, fill=(*color, 255))
            y += label_h
        for row in rows:
            draw.text((pad, y), row, font=font, fill=(236, 233, 226, 255))
            y += line_h
        if len(self._bubble_cache) > 64:
            self._bubble_cache.clear()
        self._bubble_cache[key] = (True, image)
        return image

    def _tail_image(self, size: int, up: bool):  # type: ignore[no-untyped-def]
        """The little triangle joining a bubble to its speaker."""
        key = ("tail", size, up)
        cached = self._bubble_cache.get(key)
        if cached is not None:
            return cached[1]
        width, height = size * 2 + 2, size + 2
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        if up:
            points = [(1, height - 1), (width - 2, height - 1), (width // 2, 0)]
        else:
            points = [(1, 1), (width - 2, 1), (width // 2, height - 1)]
        draw.polygon(points, fill=(17, 15, 23, 214))
        if len(self._bubble_cache) > 64:
            self._bubble_cache.clear()
        self._bubble_cache[key] = (True, image)
        return image

    def _dialogue_caption_image(self, text: str, font_px: int):  # type: ignore[no-untyped-def]
        """The quiet narration strip: dark bar, ink text, no speaker."""
        key = ("caption", text, font_px)
        cached = self._bubble_cache.get(key)
        if cached is not None:
            return cached[1]
        font = ImageFont.truetype(self._find_font(), font_px)
        rows = self._wrap_by_width(text, font, min(BUBBLE_MAX_PX + 120, font_px * 30))
        line_h = int(font_px * 1.3)
        text_w = max(int(font.getlength(row)) for row in rows)
        pad_x, pad_y = font_px, font_px // 2
        width = text_w + pad_x * 2
        height = len(rows) * line_h + pad_y * 2
        image = Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=8,
                               fill=(13, 11, 18, 205), outline=(72, 64, 88, 220),
                               width=1)
        y = pad_y
        for row in rows:
            draw.text((pad_x, y), row, font=font, fill=(216, 211, 199, 255))
            y += line_h
        if len(self._bubble_cache) > 64:
            self._bubble_cache.clear()
        self._bubble_cache[key] = (True, image)
        return image

    def _choices_image(self, options: list, selected: int, hint: str,
                       font_px: int):  # type: ignore[no-untyped-def]
        """A conversation's choice list: dark panel, one row per option,
        the highlighted one marked and bright, the control hint dim."""
        key = ("choices", tuple(options), int(selected), hint, font_px)
        cached = self._bubble_cache.get(key)
        if cached is not None:
            return cached[1]
        font = ImageFont.truetype(self._find_font(), font_px)
        line_h = int(font_px * 1.3)
        pad_x, pad_y = font_px, font_px // 2
        rows = [(("» ", (240, 234, 222)) if index == selected
                 else ("   ", (150, 145, 136)))
                for index in range(len(options))]
        text_w = max(
            (int(font.getlength(prefix + option)) for (prefix, _color), option
             in zip(rows, options)), default=0)
        hint_w = int(font.getlength(hint)) if hint else 0
        width = max(text_w, hint_w) + pad_x * 2
        height = len(options) * line_h + (line_h if hint else 0) + pad_y * 2
        image = Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=8,
                               fill=(13, 11, 18, 205), outline=(72, 64, 88, 220),
                               width=1)
        y = pad_y
        for (prefix, color), option in zip(rows, options):
            draw.text((pad_x, y), prefix + option, font=font,
                      fill=(*color, 255))
            y += line_h
        if hint:
            draw.text((pad_x, y), hint, font=font, fill=(128, 124, 116, 255))
        if len(self._bubble_cache) > 64:
            self._bubble_cache.clear()
        self._bubble_cache[key] = (True, image)
        return image

    def _draw_dialogue_cues(self, renderer, width: int, height: int) -> None:  # type: ignore[no-untyped-def]
        """The dialogue layer between the world and the sidebar: a speech
        bubble over whoever is talking and the narration caption strip.
        Both map through the zoom crop, so they ride the camera lean."""
        if self._motion is not None or self._last_village is None:
            return
        village = self._last_village
        gm = village.game_map
        camera, offset = self._display_camera(village)
        tw, th = self.tile_width, self.tile_height
        scale, crop_x, crop_y = self._cine_applied or (1.0, 0.0, 0.0)
        # The sidebar stays unscaled UI on the right; cues keep clear of it.
        world_right = max(0, self.viewport[0] * tw)
        world_bottom = max(0, self.viewport[1] * th)

        def to_window(wx: float, wy: float) -> tuple[float, float]:
            px = (wx - camera[0] + offset[0]) * tw
            py = (wy - camera[1] + offset[1]) * th
            return ((px - crop_x) * scale, (py - crop_y) * scale)

        bubble = self.dialogue_bubble
        if bubble:
            wx, wy = bubble["xy"]
            if (0 <= wy < gm.visible.shape[0] and 0 <= wx < gm.visible.shape[1]
                    and gm.visible[wy, wx]):
                ax, ay = to_window(wx, wy)
                font_px = max(13, round(th * 0.62 * scale))
                image = self._bubble_image(
                    bubble["text"], bubble["label"], bubble["color"], font_px)
                texture, tex_w, tex_h = self._upload_rgba(image)
                bx = min(max(8.0, ax + tw * scale / 2 - tex_w / 2),
                         max(8.0, world_right - tex_w - 8))
                gap = max(6.0, BUBBLE_GAP_TILES * th * scale)
                by = ay - tex_h - gap
                below = by < 6
                if below:  # no headroom: hang the bubble under the speaker
                    by = ay + th * scale + gap
                tail_size = max(8, font_px // 3)
                tail = self._tail_image(tail_size, up=below)
                t_texture, t_w, t_h = self._upload_rgba(tail)
                speaker_cx = ax + tw * scale / 2
                tx = min(max(bx + 8, speaker_cx - t_w / 2),
                         max(bx + 8, bx + tex_w - t_w - 8))
                ty = by + tex_h if not below else by - t_h
                renderer.draw_color = (255, 255, 255, 255)
                renderer.copy(texture, dest=(bx, by, tex_w, tex_h))
                renderer.copy(t_texture, dest=(tx, ty, t_w, t_h))

        caption = self.dialogue_caption
        if caption:
            font_px = max(13, round(th * 0.62 * scale))
            image = self._dialogue_caption_image(caption, font_px)
            texture, tex_w, tex_h = self._upload_rgba(image)
            cx = min(max(8.0, (world_right - tex_w) / 2),
                     max(8.0, world_right - tex_w - 8))
            cy = max(6.0, world_bottom - tex_h - 8)
            renderer.draw_color = (255, 255, 255, 255)
            renderer.copy(texture, dest=(cx, cy, tex_w, tex_h))

        choices = self.dialogue_choices
        if choices:
            font_px = max(13, round(th * 0.62 * scale))
            image = self._choices_image(
                choices["options"], choices["selected"], choices["hint"],
                font_px)
            texture, tex_w, tex_h = self._upload_rgba(image)
            cx = min(max(8.0, (world_right - tex_w) / 2),
                     max(8.0, world_right - tex_w - 8))
            cy = max(6.0, world_bottom - tex_h - 8)
            renderer.draw_color = (255, 255, 255, 255)
            renderer.copy(texture, dest=(cx, cy, tex_w, tex_h))

    # -- input --
    def wait_action(self) -> tuple:  # type: ignore[no-untyped-def]
        """Block for one keypress. Returns ('move',dx,dy) | ('wait',) |
        ('zoom',dir) | ('quit',).

        NOTE: tcod.event.wait() returns after ONE batch (startup posts a burst
        of window events), so loop it until a real command arrives.
        """
        while True:
            now = time.perf_counter()
            timeout = self._move_repeater.timeout(now)
            if self.floats or self._activity_dirty:
                # On-map feedback animates while the world waits for input.
                timeout = FLOAT_TICK_S if timeout is None else min(
                    timeout, FLOAT_TICK_S)
            events = ([self._pending_events.popleft()]
                      if self._pending_events
                      else tcod.event.wait(timeout))
            for event in events:
                if isinstance(event, tuple) and event[0] == "inspect_at":
                    return event
                if isinstance(event, tcod.event.Quit):
                    return ("quit",)
                if isinstance(event, tcod.event.WindowResized):
                    if self._last_village is not None:
                        self.render(self._last_village)
                    continue
                if isinstance(event, tcod.event.MouseMotion):
                    if self._set_hover_from_position(event.position):
                        self._present_layers()
                    continue
                if isinstance(event, tcod.event.MouseButtonDown):
                    if (event.button == tcod.event.MouseButton.LEFT
                            and self._last_village is not None):
                        at = self._mouse_world_tile(
                            event.position, self._last_village)
                        if at is not None:
                            self._hover_world_tile = at
                            self._present_layers()
                            return ("inspect_at", at[0], at[1])
                    continue
                if isinstance(event, tcod.event.KeyUp):
                    was_moving = self._move_repeater.action is not None
                    self._move_repeater.release(event.sym)
                    if was_moving and self._move_repeater.action is None:
                        self._finish_motion_cache()
                    continue
                if not isinstance(event, tcod.event.KeyDown):
                    continue
                action = world_action_for_key(
                    event.sym, bool(event.mod & tcod.event.Modifier.SHIFT))
                if action is not None:
                    if action[0] == "move":
                        immediate = self._move_repeater.press(
                            event.sym, action, time.perf_counter(),
                            repeat=event.repeat)
                        if immediate is not None:
                            if self._motion_cache is not None:
                                self._finish_motion_cache()
                            return immediate
                        continue
                    self._move_repeater.cancel()
                    self._finish_motion_cache()
                    return action
                self._move_repeater.cancel()
                self._finish_motion_cache()

            repeated = self._move_repeater.poll(time.perf_counter())
            if repeated is not None:
                return repeated
            if self.floats or self._activity_dirty:
                self._purge_expired_floats(time.perf_counter())
                self._present_layers()
                self._activity_dirty = False

    # Backwards-compat shims for the old scaffold interface
    def render_debug(self, text: str) -> None:
        print(text)

    def read_input(self) -> str:
        return "quit"

    def shutdown(self) -> None:
        self._minimap_texture = None
        self._minimap_key = None
        self._ui_texture = None
        self.world = None
        self.console = None
        self.floats = []
        self.activity = None
        self._activity_dirty = False
        self._float_textures = {}
        if self.context is not None:
            self.context.close()
            self.context = None
