"""Tiles: single source of truth for ASCII char now, tilesheet sprite later.

Tile ids are ints stored in GameMap.tiles. To add a tilesheet later, add a
`sprite` name per id here — sim code never changes.
"""
from __future__ import annotations
from dataclasses import dataclass

GRASS = 0
ROAD = 1
FLOOR = 2
WALL_WOOD = 3
WALL_STONE = 4
WALL_PALISADE = 5
DOOR = 6
TREE = 7
WATER = 8
STALL = 9
WELL = 10
FARM = 11
TORCH = 12
BED = 13
TABLE = 14
CHAIR = 15
CHEST = 16
HEARTH = 17
SHELF = 18
ANVIL = 19
WORKBENCH = 20


@dataclass(frozen=True)
class TileDef:
    id: int
    glyph: str
    name: str
    walkable: bool
    transparent: bool
    fg: tuple[int, int, int] = (255, 255, 255)


TILE_DEFS: list[TileDef] = [
    TileDef(GRASS, ".", "grass", True, True, (80, 140, 70)),
    TileDef(ROAD, "=", "road", True, True, (150, 120, 80)),
    TileDef(FLOOR, ",", "floor", True, True, (170, 170, 170)),
    TileDef(WALL_WOOD, "#", "wooden wall", False, False, (160, 110, 60)),
    TileDef(WALL_STONE, "#", "stone wall", False, False, (140, 140, 150)),
    TileDef(WALL_PALISADE, "#", "palisade", False, False, (120, 85, 45)),
    TileDef(DOOR, "+", "door", True, False, (200, 160, 90)),
    TileDef(TREE, "T", "tree", False, False, (40, 180, 60)),
    TileDef(WATER, "~", "water", False, True, (60, 120, 230)),
    TileDef(STALL, "=", "market stall", False, True, (230, 200, 80)),
    TileDef(WELL, "O", "well", False, True, (120, 220, 230)),
    TileDef(FARM, ",", "tilled soil", True, True, (170, 160, 60)),
    TileDef(TORCH, "*", "lantern post", True, True, (255, 150, 50)),
    TileDef(BED, "b", "bed", True, True, (150, 130, 220)),
    TileDef(TABLE, "t", "table", False, True, (170, 120, 70)),
    TileDef(CHAIR, "c", "chair", True, True, (150, 130, 90)),
    TileDef(CHEST, "C", "chest", False, True, (200, 160, 80)),
    TileDef(HEARTH, "^", "hearth", False, True, (255, 120, 40)),
    TileDef(SHELF, "s", "shelf", False, True, (160, 140, 100)),
    TileDef(ANVIL, "A", "anvil", False, True, (150, 150, 165)),
    TileDef(WORKBENCH, "w", "workbench", False, True, (170, 120, 70)),
]

BY_ID: dict[int, TileDef] = {t.id: t for t in TILE_DEFS}


def tile_name(tile_id: int) -> str:
    return BY_ID[int(tile_id)].name


OPEN_DOOR_FG = (150, 120, 80)


def glyph_at(game_map, x: int, y: int) -> tuple[str, tuple[int, int, int]]:
    """Screen glyph for a cell: open doors hang wide open, else the tile def."""
    tid = int(game_map.tiles[y, x])
    if tid == DOOR and game_map.doors[y, x]:
        return "'", OPEN_DOOR_FG
    t = BY_ID[tid]
    return t.glyph, t.fg


