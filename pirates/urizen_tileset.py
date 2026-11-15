"""Urizen pixel-art atlas and Pirates-of-Miska semantic mapping.

The upstream pack has no supplied license notice; see assets/tilesets/urizen/.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from omnisim.world import tile as T

ASSET_ROOT = Path(__file__).resolve().parents[1] / "assets" / "tilesets" / "urizen"
MANIFEST = ASSET_ROOT / "manifest.json"
FIRST_CODEPOINT = 0xE000

TILE_SPRITES_BY_ID = {
    T.GRASS: "floor_grass_0",
    T.ROAD: "floor_dirt",
    T.FLOOR: "floor_plank",
    T.WALL_WOOD: "wall_plank",
    T.WALL_STONE: "wall_stone",
    T.WALL_PALISADE: "wall_fence",
    T.TREE: "flora_tree_palm_0",
    T.WATER: "water",
    T.STALL: "furniture_table",
    T.WELL: "well",
    T.FARM: "farm_vegetables",
    T.TORCH: "torch_stand",
    T.BED: "furniture_bed_single",
    T.TABLE: "furniture_table",
    T.CHAIR: "furniture_chair_0",
    T.CHEST: "furniture_chest",
    T.HEARTH: "furniture_hearth",
    T.SHELF: "furniture_bookcase",
    T.ANVIL: "anvil",
    T.WORKBENCH: "furniture_table",
}
DOOR_SPRITES = {False: "door_closed_wooden", True: "door_open"}
GROUND_SPRITES = {
    "rope": "rope_h",
    "biscuit": "bread_loaf",
}


def _records() -> list[dict]:
    with MANIFEST.open(encoding="utf-8") as stream:
        data = json.load(stream)
    return [row for row in data["sprites"]
            if row.get("paths", {}).get("transparent", "").startswith("tiles/transparent/")]


def install_sprite_atlas(tileset, tile_width: int, tile_height: int) -> dict[str, int]:
    """Install every extracted Urizen tile in the font-backed tcod atlas."""
    required = set(TILE_SPRITES_BY_ID.values()) | set(DOOR_SPRITES.values()) | set(GROUND_SPRITES.values())
    records = [row for row in _records() if row["label"] in required]
    labels = {row["label"] for row in records}
    missing = required - labels
    if missing:
        raise ValueError(f"Urizen manifest is missing mapped sprites: {sorted(missing)}")

    codepoints: dict[str, int] = {}
    for row in records:
        name = row["label"]
        if name in codepoints:
            continue
        codepoint = FIRST_CODEPOINT + len(codepoints)
        image_path = ASSET_ROOT / row["paths"]["transparent"]
        with Image.open(image_path) as source:
            image = source.convert("RGBA")
        scale = min(tile_width / image.width, tile_height / image.height)
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        image = image.resize(size, Image.Resampling.NEAREST)
        frame = Image.new("RGBA", (tile_width, tile_height), (0, 0, 0, 0))
        frame.alpha_composite(image, ((tile_width - size[0]) // 2,
                                      (tile_height - size[1]) // 2))
        tileset[codepoint] = np.asarray(frame, dtype=np.uint8)
        codepoints[name] = codepoint
    return codepoints


def sprite_for_tile(tile_id: int, door_open: bool = False) -> str | None:
    if int(tile_id) == T.DOOR:
        return DOOR_SPRITES[bool(door_open)]
    return TILE_SPRITES_BY_ID.get(int(tile_id))


def sprite_for_entity(_entity) -> None:
    # This Urizen extraction has no people sprites; retain Pirates' distinct crew glyphs.
    return None


def sprite_for_ground(items) -> str | None:
    for item in items:
        sprite = GROUND_SPRITES.get(str(item.get("id", "")))
        if sprite:
            return sprite
    return None
