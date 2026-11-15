"""Selected sprites from Kenney's CC0 1-Bit Pack colored sheet."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from omnisim.world import tile as T

ASSET_ROOT = Path(__file__).resolve().parents[3] / "assets" / "tilesets" / "kenney-1bit"
SHEET = "colored_transparent_packed.png"
SHEET_COLUMNS = 48
SOURCE_TILE_SIZE = 16

# Source indices are row-major in the 48-column packed sheet. The cells below
# are taken from the user's labeled atlas review; keep unclassified tile kinds
# on their normal glyph fallback rather than assigning a visually unrelated icon.
SPRITE_TILES = {
    "soil": 752,
    "grass": 96,
    "tree": 48,
    "bush": 50,
    "water": 248,
    "water_edge": 249,
    "fence": 149,
    "wood_wall": 104,
    "stone_wall": 240,
    "door_closed": 348,
    "door_open": 349,
    "bed": 345,
    "table": 350,
    "chair": 385,
    "chest": 536,
    "hearth": 529,
    "shelf": 396,
}

TILE_SPRITES_BY_ID = {
    T.GRASS: "grass", T.ROAD: "soil", T.FLOOR: "soil",
    T.GRASS: "grass", T.ROAD: "soil", T.FLOOR: "soil",
    T.WALL_WOOD: "wood_wall", T.WALL_STONE: "stone_wall",
    T.WALL_PALISADE: "fence", T.TREE: "tree", T.WATER: "water",
    T.FARM: "soil", T.BED: "bed", T.TABLE: "table", T.CHAIR: "chair",
    T.CHEST: "chest", T.HEARTH: "hearth", T.SHELF: "shelf",
    T.WORKBENCH: "table",
}

SPRITE_CODEPOINTS = {name: 0xE000 + i for i, name in enumerate(SPRITE_TILES)}


def install_sprite_atlas(tileset, tile_width: int, tile_height: int) -> dict[str, int]:
    """Crop selected 16x16 transparent sprites and fit them into the atlas."""
    path = ASSET_ROOT / SHEET
    with Image.open(path) as source:
        sheet = source.convert("RGBA")
    for name, index in SPRITE_TILES.items():
        left = (index % SHEET_COLUMNS) * SOURCE_TILE_SIZE
        top = (index // SHEET_COLUMNS) * SOURCE_TILE_SIZE
        sprite = sheet.crop((left, top, left + SOURCE_TILE_SIZE,
                             top + SOURCE_TILE_SIZE))
        scale = min(tile_width / SOURCE_TILE_SIZE, tile_height / SOURCE_TILE_SIZE)
        size = (max(1, round(SOURCE_TILE_SIZE * scale)),
                max(1, round(SOURCE_TILE_SIZE * scale)))
        sprite = sprite.resize(size, Image.Resampling.NEAREST)
        frame = Image.new("RGBA", (tile_width, tile_height), (0, 0, 0, 0))
        frame.alpha_composite(sprite, ((tile_width - size[0]) // 2,
                                       (tile_height - size[1]) // 2))
        tileset[SPRITE_CODEPOINTS[name]] = np.asarray(frame, dtype=np.uint8)
    return dict(SPRITE_CODEPOINTS)


def sprite_for_tile(tile_id: int, door_open: bool = False) -> str | None:
    """Return the first-pass Kenney sprite key; unknown IDs use glyphs."""
    if tile_id == T.DOOR and door_open:
        return "door_open"
    if tile_id == T.DOOR:
        return "door_closed"
    return TILE_SPRITES_BY_ID.get(int(tile_id))
