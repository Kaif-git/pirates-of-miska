"""Duel view of the normal PiratesRenderer, plus read-only exchange playback.

The renderer shares the live SDL context/atlas. The ship world is never rebound
or advanced. Terrain, light, camera and sprite textures use the game engine.
"""
import time
import bisect
import math
from types import SimpleNamespace

import numpy as np
from PIL import Image


class ExchangePlayback:
    def __init__(self, frames, *, started=None, movement_seconds=.095, reduced_motion=False):
        self.frames = tuple(frames)
        self.started = time.monotonic() if started is None else started
        self.reduced_motion = reduced_motion
        self.tell = .22
        self.step = min(.045, 1.2/max(1, len(frames)-1))
        self.boundaries = [0.0]
        for first, second in zip(self.frames, self.frames[1:]):
            finish = {actor.actor_id: actor.pos for actor in second.actors}
            moving = any(actor.pos != finish.get(actor.actor_id, actor.pos) for actor in first.actors)
            impact_pause = (.08 if not reduced_motion and any(
                popup.tick == second.tick and "· impact" in popup.text
                for popup in second.popups) else 0)
            self.boundaries.append(self.boundaries[-1]
                                   +(max(self.step, movement_seconds) if moving else self.step)
                                   +impact_pause)
        self.duration = self.tell + self.boundaries[-1] + .25

    def offset(self, index, progress=0):
        end = min(index+1, len(self.boundaries)-1)
        return self.tell+self.boundaries[index]+progress*(self.boundaries[end]-self.boundaries[index])

    def sample(self, now=None):
        from omnisim.ui.ascii_renderer import movement_frame
        elapsed = max(0, (time.monotonic() if now is None else now)-self.started)
        cursor = max(0, elapsed-self.tell)
        index = min(len(self.frames)-1, max(0, bisect.bisect_right(self.boundaries, cursor)-1))
        following = min(len(self.frames)-1, index+1)
        model, end = self.frames[index], self.frames[following]
        segment = self.boundaries[following]-self.boundaries[index]
        progress = min(1, (cursor-self.boundaries[index])/segment) if segment else 1
        finish = {actor.actor_id: actor.pos for actor in end.actors}
        positions = tuple(movement_frame(progress, actor.pos,
                                         finish.get(actor.actor_id, actor.pos), (0, 0), (0, 0))[0]
                          for actor in model.actors)
        if not self.reduced_motion and segment > 0:
            # Flinch is a short sprite offset inside the occupied tile, never a
            # simulated move. Key it to the actual witnessed reaction event.
            flinch_pops = {popup.pos for popup in end.popups
                           if popup.text == "Flinched" and popup.tick == end.tick}
            impacts = {popup.pos: popup.direction for popup in end.popups
                       if "· impact" in popup.text and popup.tick == end.tick}
            if flinch_pops or impacts:
                pulse = math.sin(progress * math.pi) * .16
                positions = tuple((pos[0] + (impacts[actor.pos][0]*pulse if actor.pos in impacts else
                                              pulse if actor.pos in flinch_pops else 0),
                                   pos[1] + (impacts[actor.pos][1]*pulse if actor.pos in impacts else 0))
                                  for actor, pos in zip(model.actors, positions))
        return model, positions, elapsed >= self.duration, (model, end, progress)


def create_scene_renderer(host):
    """Reuse the complete game render path, with isolated duel-only view hooks."""
    from play import PiratesRenderer

    class TerrainOnlyUI:
        """Inherited render() must not paint the unused ship sidebar/status."""
        def __getattr__(self, name):
            return getattr(host.ui, name)

        def new_overlay(self, width, height):
            return Image.new("RGBA", (1, 1))

        def draw_sidebar(self, *args, **kwargs):
            pass

        def draw_status(self, *args, **kwargs):
            pass

    class DuelSceneRenderer(PiratesRenderer):
        @property
        def viewport(self):
            return self.duel_viewport

        def _sidebar_lines(self, world, width, height):
            return []

        def _status_lines(self, world):
            return []

        def _frame_cell_extras(self, world):
            cx, cy, ox, oy = self._active_camera
            occupied = {actor.pos for actor in world.model.actors}
            for _, _, (x, y), condition, _ in world.model.ground_weapons:
                if (x, y) not in occupied:
                    self._draw_lit_cell(x-cx+ox, y-cy+oy, "w",
                                        fg=(220, 190, 110) if condition > 0 else (120, 110, 100),
                                        bg=(15, 40, 39), sprite=None, brightness=1)
            for x, y in world.model.exits:
                self._draw_lit_cell(x-cx+ox, y-cy+oy, ">", fg=(122, 176, 216),
                                    bg=(15, 40, 39), sprite=None, brightness=1)
            for x, y in world.model.danger:
                if (x, y) in {actor.pos for actor in world.model.actors}:
                    continue
                self._draw_lit_cell(x-cx+ox, y-cy+oy, "!", fg=(245, 180, 70),
                                    bg=(65, 35, 20), sprite=None, brightness=1)

        def _present_layers(self):
            # Engine render() owns baking. The dashboard composes this exact
            # world texture into its viewport instead of the ship's sidebar.
            self._world_texture = self.world.render_texture()

    scene = DuelSceneRenderer.__new__(DuelSceneRenderer)
    scene.__dict__ = host.__dict__.copy()
    scene.ui = TerrainOnlyUI()
    scene.duel_viewport = host.viewport
    scene._motion = scene._motion_cache = None
    scene.cine = None
    scene._combat_text_overlay = None
    scene._world_texture = None
    return scene


def render_scene(display, host, model, arena, positions, transition=None):
    """Run normal engine render(), then project its camera at native pixel size."""
    from omnisim.world.map import GameMap
    from omnisim.world import tile as T
    from omnisim.ui.ascii_renderer import compute_camera, movement_frame
    from tcod.sdl.render import BlendMode

    if getattr(display, "scene_renderer", None) is None or display.scene_renderer.context is not host.context:
        display.scene_renderer = create_scene_renderer(host)
        display.actor_texture = None
    scene = display.scene_renderer
    # Window resizing can replace the host console/world layer.
    scene.console, scene.world = host.console, host.world
    scene.width, scene.height = host.width, host.height
    ax, ay, aw, ah = arena
    tw, th = host.tile_width, host.tile_height
    scene.duel_viewport = (max(1, (aw-16)//tw), max(1, (ah-28)//th))
    gm = getattr(display, "scene_map", None)
    if gm is None or (gm.width, gm.height) != (model.width, model.height):
        gm = GameMap(model.width, model.height, np.full((model.height, model.width), T.WALL_WOOD, dtype=np.int8))
        for x, y in model.floor:
            gm.tiles[y, x] = T.FLOOR
        gm.explored[:] = gm.visible[:] = True
        display.scene_map = gm
    view = SimpleNamespace(game_map=gm, player=SimpleNamespace(x=model.player_pos[0], y=model.player_pos[1]),
                           ground=(), entities=lambda: (), light=None, model=model)
    # This is the same render() used aboard: tile mapping, lighting, atlas,
    # compute_camera, overscan and cell extras, not a second hand-painted map.
    vw, vh = scene.viewport
    current_camera = compute_camera(model.width, model.height, vw, vh, *model.player_pos)
    camera = tuple(float(v) for v in current_camera[:2])
    if transition:
        start, end, progress = transition
        cs = compute_camera(model.width, model.height, vw, vh, *start.player_pos)
        ce = compute_camera(model.width, model.height, vw, vh, *end.player_pos)
        _, camera = movement_frame(progress, start.player_pos, end.player_pos, cs[:2], ce[:2])
        scene._motion = {"anchor": (min(cs[0], ce[0]), min(cs[1], ce[1])), "offset": cs[2:],
                         "buffer_width": vw+1, "buffer_height": vh+1}
    else:
        scene._motion = None
    terrain_key = (id(scene.world), scene.viewport, model.floor, model.exits, model.danger,
                   model.ground_weapons,
                   tuple((actor.actor_id, actor.pos) for actor in model.actors),
                   tuple(scene._motion["anchor"]) if scene._motion else current_camera[:2],
                   bool(scene._motion))
    if getattr(display, "terrain_key", None) != terrain_key:
        scene.render(view)
        display.terrain_key = terrain_key
        display.terrain_builds = getattr(display, "terrain_builds", 0)+1
    # Terrain uses one extra row/column for fractional camera panning.
    cx, cy, ox, oy = scene._active_camera
    dx, dy = (camera[0]-cx)*tw, (camera[1]-cy)*th
    pxw, pxh = vw*tw, vh*th
    left, top = ax+(aw-pxw)//2, ay+22+(ah-22-pxh)//2
    display.map_rect = (left+(ox-camera[0])*tw, top+(oy-camera[1])*th, tw, th)
    display.arena_rect = (left, top, pxw, pxh)
    display.camera = camera
    sdl = host.context.sdl_renderer
    # Fractional camera shifts track the same eased movement_frame as walking.
    sdl.copy(scene._world_texture, source=(max(0, dx), max(0, dy), pxw, pxh), dest=(left, top, pxw, pxh))
    if display.actor_texture is None:
        codepoint = scene._sprite_codepoints["captain"]
        display.actor_texture = sdl.upload_texture(np.asarray(scene.world._tileset[codepoint], dtype=np.uint8))
        display.actor_texture.blend_mode = BlendMode.BLEND
    screens = []
    for index, (actor, pos) in enumerate(zip(model.actors, positions)):
        x, y = left+(pos[0]-camera[0]+ox)*tw, top+(pos[1]-camera[1]+oy)*th
        screens.append((x, y))
        grounded = actor.grounded
        # Strong identity rings survive the shared captain sprite.
        sdl.draw_color = ((232, 184, 80, 255) if grounded else
                          (70, 175, 110, 255) if actor.is_player else
                          (205, 75, 60, 255) if actor.hostile else (86, 132, 206, 255))
        sdl.fill_rect((x, y, tw, th))
        if grounded:
            sdl.copy(display.actor_texture, dest=(x, y+th//2, tw, max(1, th//2)))
            sdl.draw_color = (255, 210, 100, 255)
            sdl.fill_rect((x, y+th-3, tw, 3))
        else:
            sdl.copy(display.actor_texture, dest=(x, y, tw, th))
    for _, _, (gx, gy), _, _ in model.ground_weapons:
        if (gx, gy) in {actor.pos for actor in model.actors}:
            x, y = left+(gx-camera[0]+ox)*tw, top+(gy-camera[1]+oy)*th
            marker = max(5, tw//4)
            sdl.draw_color = (12, 12, 16, 255)
            sdl.fill_rect((x+tw-marker-1, y+1, marker+2, marker+2))
            sdl.draw_color = (255, 207, 92, 255)
            sdl.fill_rect((x+tw-marker, y+2, marker, marker))
    return screens
