"""Static tcod/SDL renderer fixture; it creates no simulation or Sim tick."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from omnisim.entities.entity import Entity
from omnisim.ui.ascii_renderer import AsciiRenderer
from omnisim.ui.render_settings import RenderSettings
from omnisim.world import tile as T
from omnisim.world.map import GameMap


def make_diagnostic_map() -> tuple[GameMap, list[Entity]]:
    """Create a dense static grid with terrain, a house, loot, and residents."""
    width, height = 84, 42
    tiles = np.full((height, width), T.GRASS, dtype=np.int16)
    tiles[[0, -1], :] = T.WALL_STONE
    tiles[:, [0, -1]] = T.WALL_STONE
    tiles[20, 1:-1] = T.ROAD
    tiles[1:-1, 42] = T.ROAD
    tiles[4:17, 5:18] = T.TREE
    tiles[24:39, 65:79] = T.TREE
    tiles[28:35, 5:23] = T.WATER
    tiles[30, 11:16] = T.ROAD
    tiles[8:14, 25:35] = T.FLOOR
    tiles[8, 25:35] = T.WALL_WOOD
    tiles[13, 25:35] = T.WALL_WOOD
    tiles[8:14, 25] = T.WALL_WOOD
    tiles[8:14, 34] = T.WALL_WOOD
    tiles[10, 25] = T.DOOR
    tiles[10, 34] = T.DOOR
    tiles[10, 30] = T.HEARTH
    tiles[11, 29] = T.TABLE
    tiles[11, 31] = T.CHAIR
    tiles[16:19, 24:36] = T.FARM
    tiles[17, 29] = T.WELL
    game_map = GameMap(width, height, tiles)
    game_map.explored[:, :] = True
    game_map.visible[:, :] = True
    game_map.visible[5:16, 47:61] = False
    entities = [
        Entity(0, "Player", 30, 18, "@", (250, 250, 250),
               comps={"role": "player", "activity": "idle"}),
        Entity(1, "Mira", 28, 18, "M", (190, 210, 255),
               comps={"role": "resident", "activity": "working"}),
        Entity(2, "Oren", 34, 18, "O", (240, 190, 150),
               comps={"role": "resident", "activity": "sleeping"}),
    ]
    return game_map, entities


class DiagnosticRenderer(AsciiRenderer):
    """Use the production compositor with a deliberately dense sidebar sample."""

    def _sidebar_lines(self, village, width: int, height: int):
        lines = [
            [("RENDERER DIAGNOSTIC", (230, 200, 120), (30, 30, 62))],
            [("Population 124 · grain 42 · water 18", (120, 220, 120), (14, 14, 24))],
            [("Day 3 · 14:25 · 18:00 sunset", (220, 220, 220), (14, 14, 24))],
            [("Selected: Mira the farmer", (230, 200, 120), (14, 14, 24))],
            [("Activity: tending the north field", (220, 220, 220), (14, 14, 24))],
            [("Ordinary UI text uses proportional font advances and wraps inside "
              "this pixel-width sidebar. The map zoom does not change sidebar "
              "typography. Resize the window to check that the map remains in "
              "fixed cells while the text panel keeps its natural line spacing.",
              (220, 220, 220), (14, 14, 24))],
            [("Unicode: ┌──┬──┐ · ★ · × · →", (220, 220, 220), (14, 14, 24))],
            [("World: Consolas · UI: Segoe UI", (140, 140, 140), (14, 14, 24))],
        ]
        return lines

    def _status_lines(self, village) -> list[str]:
        return ["Dense map: walls, terrain, house, fields, actors, and loot.",
                "+/- zoom world only · I inventory popup · resize this window · Q quit"]


def make_diagnostic_view():
    game_map, entities = make_diagnostic_map()
    player = entities[0]
    view = SimpleNamespace(
        name="Renderer Diagnostic", game_map=game_map, player=player,
        npcs=entities[1:], watch_id=entities[1].id, light=np.ones(
            (game_map.height, game_map.width), dtype=np.float32),
        ground=[(32, 16), (33, 16)], clock_pts=0, log=["Static renderer fixture"],
        field=[{"x": 26, "y": 17, "crop": None}],
    )
    view.entities = lambda: entities
    return view


def run_diagnostic(world_font="Consolas", ui_font="Segoe UI",
                   world_font_size=16, ui_font_size=15,
                   world_cell_width=None, world_cell_height=None,
                   ui_scale=1.0) -> None:
    """Open the presentation diagnostic without constructing or advancing Game."""
    settings = RenderSettings(
        world_font_family=world_font, world_font_size=world_font_size,
        world_cell_width=world_cell_width,
        world_cell_height=world_cell_height,
        ui_font_family=ui_font, ui_font_size=ui_font_size,
        ui_scale=ui_scale)
    renderer = DiagnosticRenderer(settings=settings)
    view = make_diagnostic_view()
    renderer.init_for(view.game_map.width, view.game_map.height)
    try:
        while True:
            renderer.render(view)
            command = renderer.wait_action()
            if command[0] == "quit":
                return
            if command[0] == "zoom":
                renderer.zoom(command[1], view.game_map.width, view.game_map.height)
            elif command[0] == "inspect":
                renderer.render_list(
                    view, "Inventory popup",
                    ["Roots × 4 — fresh", "Water × 2", "Unicode sample: ┌─┬─┐ │ ★ │ └─┴─┘",
                     "This overlay uses the UI font, wraps naturally, and scrolls "
                     "independently of the ASCII world."],
                    footer="Any key closes · arrows/wheel scroll")
                renderer.wait_any()
    finally:
        renderer.shutdown()
