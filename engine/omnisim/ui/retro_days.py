"""Curated RetroDays pixel art used by the Miska MVP renderer."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from omnisim.world import tile as T


ASSET_ROOT = Path(__file__).resolve().parents[3] / "assets" / "tilesets" / "retrodays"
SPRITE_FILES: dict[str, str] = {
    "grass": "terrain/67_t_grass_0.png",
    "sand": "terrain/38_t_sand_0.png",
    "deep_water": "terrain/29_t_water_moving_dp_0.png",
    "shore_water": "terrain/30_t_water_moving_sh_0.png",
    "dirt": "terrain/35_t_dirtfloor_0.png",
    "concrete": "terrain/48_t_concrete_floor_0.png",
    "wood_floor": "terrain/57_t_floor_primitive_0.png",
    "tree": "terrain/113_t_tree_season_spring_0.png",
    "log_wall": "terrain/343_t_wall_log_0.png",
    "log_wall_center": "terrain/344_t_wall_log_1.png",
    "log_wall_corner": "terrain/345_t_wall_log_2.png",
    "log_wall_edge": "terrain/346_t_wall_log_3.png",
    "log_wall_end": "terrain/347_t_wall_log_4.png",
    "log_wall_t": "terrain/348_t_wall_log_5.png",
    "stone_wall": "terrain/422_t_rock_wall_0.png",
    "stone_wall_center": "terrain/423_t_rock_wall_1.png",
    "stone_wall_corner": "terrain/424_t_rock_wall_2.png",
    "stone_wall_edge": "terrain/425_t_rock_wall_3.png",
    "stone_wall_end": "terrain/426_t_rock_wall_4.png",
    "stone_wall_t": "terrain/427_t_rock_wall_5.png",
    "palisade_wall": "terrain/390_t_wall_wattle_0.png",
    "palisade_wall_center": "terrain/391_t_wall_wattle_1.png",
    "palisade_wall_corner": "terrain/392_t_wall_wattle_2.png",
    "palisade_wall_edge": "terrain/393_t_wall_wattle_3.png",
    "palisade_wall_end": "terrain/394_t_wall_wattle_4.png",
    "palisade_wall_t": "terrain/395_t_wall_wattle_5.png",
    "door_open": "terrain/501_t_door_o_0.png",
    "door_closed": "terrain/502_t_door_c_0.png",
    "water_pump": "terrain/609_t_water_pump_0.png",
    "bookcase": "furniture/597_f_bookcase_0.png",
    "bed": "furniture/665_f_bed_1.png",
    "counter": "furniture/683_f_counter_0.png",
    "table": "furniture/696_f_table_0.png",
    "stove": "furniture/771_f_woodstove_0.png",
    "forge": "furniture/773_f_forge_0.png",
    "fireplace": "furniture/778_f_fireplace_0.png",
    "shelf": "furniture/784_f_utility_shelf_0.png",
    "sign": "furniture/791_f_sign_0.png",
    "crate": "furniture/794_f_crate_c_0.png",
    "stool": "furniture/798_f_stool_0.png",
    "plant_seed": "furniture/762_f_plant_seed_0.png",
    "plant_seedling": "furniture/763_f_plant_seedling_0.png",
    "plant_mature": "furniture/764_f_plant_mature_0.png",
    "plant_harvest": "furniture/765_f_plant_harvest_0.png",
    "counter_gate": "furniture/3062_f_counter_gate_c_0.png",
    "lamp": "furniture/f_floor_lamp.png",
    "player": "character/player_male.png",
    "npc": "character/npc_male.png",
}
ROTATABLE_WALL_SPRITES = {
    f"{family}_{shape}"
    for family in ("log_wall", "stone_wall", "palisade_wall")
    for shape in ("corner", "edge", "end", "t")
}
SPRITE_CODEPOINTS: dict[str, int] = {}
for _sprite_name in SPRITE_FILES:
    SPRITE_CODEPOINTS[_sprite_name] = 0xE000 + len(SPRITE_CODEPOINTS)
    if _sprite_name in ROTATABLE_WALL_SPRITES:
        for _angle in (90, 180, 270):
            SPRITE_CODEPOINTS[f"{_sprite_name}@{_angle}"] = (
                0xE000 + len(SPRITE_CODEPOINTS))
for _sprite_name in ("wardrobe", "folded_clothes"):
    SPRITE_CODEPOINTS[_sprite_name] = 0xE000 + len(SPRITE_CODEPOINTS)


def _procedural_sprite(name: str) -> Image.Image:
    """Tiny Miska-specific props absent from the curated RetroDays tiles."""
    from PIL import ImageDraw

    image = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if name == "wardrobe":
        dark, wood, panel, highlight = (
            (62, 39, 25, 255), (133, 83, 48, 255),
            (160, 108, 65, 255), (198, 150, 91, 255))
        draw.rectangle((3, 2, 16, 18), fill=dark)
        draw.rectangle((4, 3, 15, 16), fill=wood)
        draw.rectangle((5, 4, 9, 15), fill=panel)
        draw.rectangle((11, 4, 14, 15), fill=panel)
        draw.line((10, 4, 10, 15), fill=dark)
        draw.rectangle((8, 9, 8, 10), fill=highlight)
        draw.rectangle((11, 9, 11, 10), fill=highlight)
        draw.rectangle((5, 17, 7, 18), fill=dark)
        draw.rectangle((13, 17, 15, 18), fill=dark)
        draw.line((4, 3, 15, 3), fill=highlight)
    elif name == "folded_clothes":
        dark = (46, 38, 35, 255)
        layers = [
            ((5, 12, 15, 17), (171, 93, 64, 255), (215, 139, 99, 255)),
            ((3, 8, 13, 13), (81, 119, 142, 255), (123, 164, 181, 255)),
            ((6, 4, 16, 9), (205, 183, 140, 255), (235, 219, 180, 255)),
        ]
        for (x1, y1, x2, y2), color, highlight in layers:
            draw.rectangle((x1 - 1, y1, x2 + 1, y2), fill=dark)
            draw.rectangle((x1, y1, x2, y2 - 1), fill=color)
            draw.line((x1 + 1, y1 + 1, x2 - 1, y1 + 1), fill=highlight)
            draw.line((x1 + 2, y1 + 2, x1 + 2, y2 - 2), fill=highlight)
    else:
        raise KeyError(name)
    return image

TILE_SPRITES_BY_NAME: dict[str, str] = {
    "grass": "grass",
    "road": "dirt",
    "floor": "concrete",
    "tree": "tree",
    "water": "deep_water",
    "market stall": "counter",
    "well": "water_pump",
    "tilled soil": "dirt",
    "lantern post": "lamp",
    "bed": "bed",
    "table": "table",
    "chair": "stool",
    "chest": "crate",
    "hearth": "fireplace",
    "shelf": "shelf",
    "anvil": "forge",
    "workbench": "table",
    "rug": "wood_floor",
    "iron stove": "stove",
    "writing desk": "table",
    "interior floor": "wood_floor",
    "shore sand": "sand",
    "weathered signpost": "sign",
    "driftwood": "log_wall",
    "shop sign": "sign",
    "cage bars": "counter_gate",
    "growing crop": "plant_seedling",
    "mature crop": "plant_mature",
    "established crop": "plant_harvest",
    "oil lantern": "lamp",
}
WALL_FAMILY_BY_NAME = {
    "wooden wall": "log_wall",
    "stone wall": "stone_wall",
    "palisade": "palisade_wall",
}
WALL_CONNECTIONS_BY_SHAPE = {
    "corner": frozenset(("east", "south")),
    "edge": frozenset(("north", "south")),
    "end": frozenset(("south",)),
    "t": frozenset(("west", "east", "south")),
}
WALL_NEIGHBOR_OFFSETS = {
    "north": (0, -1), "east": (1, 0),
    "south": (0, 1), "west": (-1, 0),
}


def install_sprite_atlas(tileset, tile_width: int, tile_height: int) -> dict[str, int]:
    """Add transparent, nearest-neighbor sprites to a font-backed tcod atlas."""
    for name, relative_path in SPRITE_FILES.items():
        path = ASSET_ROOT / relative_path
        if not path.is_file():
            raise FileNotFoundError(f"RetroDays sprite is missing: {path}")
        with Image.open(path) as source:
            image = source.convert("RGBA")

        pixels = np.asarray(image).copy()
        black = np.all(pixels[..., :3] == 0, axis=-1)
        pixels[black, 3] = 0  # Upstream uses black as the tileset transparency color.
        image = Image.fromarray(pixels, mode="RGBA")

        variants = [(name, 0)]
        if name in ROTATABLE_WALL_SPRITES:
            variants.extend((f"{name}@{angle}", angle) for angle in (90, 180, 270))
        for sprite_key, angle in variants:
            variant = (image.rotate(angle, resample=Image.Resampling.NEAREST)
                       if angle else image)
            scale = min(tile_width / variant.width, tile_height / variant.height)
            scaled_size = (max(1, round(variant.width * scale)),
                           max(1, round(variant.height * scale)))
            variant = variant.resize(scaled_size, Image.Resampling.NEAREST)
            frame = Image.new("RGBA", (tile_width, tile_height), (0, 0, 0, 0))
            frame.alpha_composite(variant, ((tile_width - scaled_size[0]) // 2,
                                            (tile_height - scaled_size[1]) // 2))
            tileset[SPRITE_CODEPOINTS[sprite_key]] = np.asarray(frame, dtype=np.uint8)
    for name in ("wardrobe", "folded_clothes"):
        image = _procedural_sprite(name)
        scale = min(tile_width / image.width, tile_height / image.height)
        scaled_size = (max(1, round(image.width * scale)),
                       max(1, round(image.height * scale)))
        image = image.resize(scaled_size, Image.Resampling.NEAREST)
        frame = Image.new("RGBA", (tile_width, tile_height), (0, 0, 0, 0))
        frame.alpha_composite(image, ((tile_width - scaled_size[0]) // 2,
                                      (tile_height - scaled_size[1]) // 2))
        tileset[SPRITE_CODEPOINTS[name]] = np.asarray(frame, dtype=np.uint8)
    return dict(SPRITE_CODEPOINTS)


def _rotate_directions(directions: frozenset[str], angle: int) -> frozenset[str]:
    rotate_ccw = {"north": "west", "west": "south",
                  "south": "east", "east": "north"}
    result = set(directions)
    for _ in range(angle // 90):
        result = {rotate_ccw[direction] for direction in result}
    return frozenset(result)


def _orientation_for(shape: str, connections: frozenset[str]) -> int:
    canonical = WALL_CONNECTIONS_BY_SHAPE[shape]
    for angle in (0, 90, 180, 270):
        if _rotate_directions(canonical, angle) == connections:
            return angle
    raise ValueError(f"No {shape} wall orientation for {sorted(connections)}")


def _wall_sprite(tile_id: int, family: str, game_map, x: int, y: int) -> str:  # type: ignore[no-untyped-def]
    tiles = game_map.tiles
    height, width = tiles.shape
    connections = set()
    for direction, (dx, dy) in WALL_NEIGHBOR_OFFSETS.items():
        nx, ny = x + dx, y + dy
        if not (0 <= nx < width and 0 <= ny < height):
            continue
        neighbor = T.BY_ID.get(int(tiles[ny, nx]))
        if neighbor and WALL_FAMILY_BY_NAME.get(neighbor.name) == family:
            connections.add(direction)

    if len(connections) == 4:
        return f"{family}_center"
    if len(connections) == 0:
        return family
    if len(connections) == 1:
        shape = "end"
    elif len(connections) == 2:
        is_straight = frozenset(connections) in (
            frozenset(("north", "south")), frozenset(("east", "west")))
        shape = "edge" if is_straight else "corner"
    else:
        shape = "t"

    angle = _orientation_for(shape, frozenset(connections))
    sprite = f"{family}_{shape}"
    return f"{sprite}@{angle}" if angle else sprite


def sprite_for_tile(tile_id: int, door_open: bool = False,
                    game_map=None, x: int | None = None,
                    y: int | None = None) -> str | None:  # type: ignore[no-untyped-def]
    """Return the MVP sprite key for a game tile, or None for ASCII fallback."""
    tile = T.BY_ID.get(int(tile_id))
    if tile is None:
        return None
    if tile.name == "door":
        return "door_open" if door_open else "door_closed"
    family = WALL_FAMILY_BY_NAME.get(tile.name)
    if family and game_map is not None and x is not None and y is not None:
        return _wall_sprite(int(tile_id), family, game_map, x, y)
    if family:
        return family
    return TILE_SPRITES_BY_NAME.get(tile.name)


def sprite_for_entity(entity) -> str | None:  # type: ignore[no-untyped-def]
    """Use the matching tiny player/resident sprites; keep special glyph states."""
    role = entity.get("role")
    activity = entity.get("activity", "idle")
    if role == "player":
        return "player"
    if role == "resident" and activity not in ("sleeping", "hungry"):
        return "npc"
    return None
