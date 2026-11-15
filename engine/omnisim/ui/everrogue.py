"""EverRogue 16px atlas, visually audited coordinates, and Miska supplements.

The FULL sheet is 16 columns by 15 rows, NOT the 8-column 2.0 sheet.
Coordinates below are zero-based (column, row); no glyph-based guessing.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from omnisim.world import tile as T

ASSET_ROOT = Path(__file__).resolve().parents[3] / "assets/tilesets/everrogue"
SHEET = "EverRogueTileset FULL Packed Alpha.png"
BACKGROUND = (15, 59, 58)  # #0F3B3A: floor / outside canvas.
SOURCE_BACKGROUND = (17, 57, 52)
SHADOW_COLOR = (21, 83, 82)  # #155352: unlit object ink.
INK = (178, 200, 192, 255)
LIGHT = (255, 255, 255, 255)
GOLD = (201, 174, 71, 255)
WOOD = (149, 129, 98, 255)
DARK = (*BACKGROUND, 255)
MEMORY_EXPOSURE = 0.0
FADE_STEPS = 32
LUMA_WEIGHTS = (.2126, .7152, .0722)

# Reviewed against a numbered, nearest-neighbor contact sheet. For example,
# 208-211 are books, 212-215 chests, 216-219 ovens, not beds or floor tiles.
SOURCE_CELLS = {
    "grass": (10, 3), "grass_tuft": (13, 3), "reeds": (12, 0),
    "sand": (3, 4), "dry_grass": (6, 8), "soil": (7, 8),
    "stone_blocks": (8, 0), "brick": (5, 4), "bare_ground": (0, 0),
    "wood_wall": (6, 4),  # Warm solid masonry, not the source fence/rails.
    "tree": (3, 8), "tree_gold": (4, 8), "tree_red": (5, 8),
    "tree_canopy": (6, 0), "water": (7, 7),
    "door_closed": (12, 8), "door_open": (0, 12),
    "stairs": (15, 8), "window": (1, 0), "sign": (6, 3),
    "book": (9, 12), "book_mint": (11, 12),
    "chest": (5, 13), "oven": (8, 13),
    "bowl": (7, 1), "basin": (6, 1), "pot": (11, 1),
    "lamp": (5, 11), "pillar": (9, 11), "rug": (1, 13),
    "potion": (12, 10), "player": (0, 14),
    "npc": (1, 14), "guard": (2, 14), "scholar": (3, 14),
}
SUPPLEMENTS = (
    "wood_floor", "boardwalk", "dirt", "fence", "bars", "bed",
    "table", "chair", "shelf", "wardrobe", "folded_clothes", "barrel",
    "anvil", "forge", "counter", "well", "stump", "driftwood", "roof",
    "seedling", "crop", "harvest", "torch", "torch_bracket", "peg",
    "basket", "bellows", "flue", "net", "plate", "pitcher", "mound",
    "parcel", "fish", "tiger", "calendar", "notice", "rock", "sand_alt", "paving",
)
WALL_NAMES = {"wooden wall": "wood_wall", "stone wall": "brick",
              "palisade": "fence"}
WALL_KEYS = tuple(f"{family}:{mask}" for family in WALL_NAMES.values()
                  for mask in range(16))
FURNITURE_FAMILIES = {"bed", "table", "counter", "shelf", "oven", "forge"}
FLOOR_SPRITES = frozenset({
    "bare_ground", "grass", "grass_tuft", "sand", "sand_alt", "soil",
    "dirt", "wood_floor", "paving", "boardwalk", "rug",
})
FURNITURE_KEYS = tuple(
    f"{family}@{w}x{h}:{x},{y}"
    for family in sorted(FURNITURE_FAMILIES)
    for w in range(1, 5) for h in range(1, 5) if w * h > 1
    for y in range(h) for x in range(w))
SPRITE_CODEPOINTS = {name: 0xE000 + i for i, name in enumerate(
    (*SOURCE_CELLS, *SUPPLEMENTS, *WALL_KEYS, *FURNITURE_KEYS))}

TILE_SPRITES_BY_NAME = {
    "grass": "grass", "road": "dirt", "floor": "paving",
    "tree": "tree", "water": "water", "market stall": "table",
    "well": "well", "tilled soil": "soil", "lantern post": "lamp",
    "bed": "bed", "table": "table", "chair": "chair", "chest": "chest",
    "hearth": "forge", "shelf": "shelf", "anvil": "anvil",
    "workbench": "table", "rug": "rug", "iron stove": "oven",
    "writing desk": "table", "washbasin": "basin",
    "interior floor": "wood_floor", "shore sand": "sand",
    "weathered signpost": "sign", "driftwood": "driftwood",
    "shop sign": "sign", "cage bars": "bars", "growing crop": "seedling",
    "mature crop": "crop", "established crop": "harvest",
    "oil lantern": "lamp", "wall calendar": "calendar",
    "shaded tutorial path": "dirt", "earth-covered charcoal mound": "mound",
    "masonry oven": "oven", "water pitcher": "pitcher",
    "charcoal mound site": "soil", "forge": "forge",
    "palm-thatch roof": "roof", "timber roof post": "pillar",
    "smithy flue": "flue", "smithing tool rack": "shelf",
    "quench-water barrel": "barrel", "smithy stock chest": "chest",
    "hearth bellows": "bellows", "shallow tide pool": "water",
    "shoreline jetty": "boardwalk", "shore bait patch": "soil",
    "house window": "window", "wall torch bracket": "torch_bracket",
    "oak barrel": "barrel", "coat peg": "peg", "repair bench": "table",
    "kitchen counter": "counter", "stone hearth": "forge",
    "firewood basket": "basket",
}
OBJECT_SPRITES = {
    "waseem_wardrobe": "wardrobe", "wash_line": "folded_clothes",
    "book_shelf": "shelf", "living_books": "shelf", "bedside_shelf": "shelf",
    "kitchen_stove": "oven", "kitchen_oven": "oven",
    "kitchen_counter": "counter", "repair_bench": "table",
    "living_table": "table", "kitchen_table": "table", "jes_table": "table",
    "writing_desk": "table", "waseem_bedside_table": "table",
    "jes_bed": "bed", "waseem_bed": "bed", "pantry": "wardrobe",
    "pantry_crock": "pot", "pantry_crate": "chest", "pantry_barrel": "barrel",
    "firewood_basket": "basket", "keepsake_chest": "chest",
    "living_storage_cabinet": "wardrobe", "living_lantern": "lamp",
    "wall_calendar": "calendar", "jes_firewood_note": "notice",
    "washbasin": "basin", "kitchen_rack": "shelf",
    "breakfast_plate": "plate", "cocoa_pot": "pot",
    "smithy_forge_hearth": "forge", "home_forge": "anvil",
    "smithy_tool_rack": "shelf", "smithy_stock_chest": "chest",
    "smithy_quench_barrel": "barrel", "charcoal_mound_site": "soil",
    "square_notice_board": "notice", "town_signpost": "sign",
    "square_well": "well", "dock_net_rack": "net",
    "dock_boat_nameplate": "notice", "dock_mooring_posts": "pillar",
    "shore_driftwood": "driftwood", "working_repair_shed": "table",
    "working_garden": "crop", "marine_public_notice": "notice",
    "mayor_civic_crest": "notice", "smithy_flue": "flue",
    "smithy_bellows": "bellows", "clean_water_pitcher": "pitcher",
    "dirty_water_pitcher": "pitcher", "was_face_drawer": "chest",
    "entry_peg": "peg", "jes_peg": "peg",
}


def _supplement(name: str) -> Image.Image:
    """Original Miska-only props; deliberately not labeled as source sprites."""
    im = Image.new("RGBA", (16, 16))
    d = ImageDraw.Draw(im)
    if name == "dirt":
        # Quiet packed earth. The source's upright tan strokes are dry grass,
        # not a road; never tile those across paths or open fields.
        d.rectangle((0, 0, 15, 15), fill=DARK)
        for x, y in ((3, 5), (11, 12), (13, 2)):
            d.point((x, y), fill=(25, 74, 69, 255))
    elif name in {"wood_floor", "boardwalk", "roof"}:
        grain = (32, 72, 60, 255) if name == "wood_floor" else WOOD
        for y in range(1, 16, 4):
            d.line((0, y, 15, y), fill=grain if name != "roof" else GOLD)
            d.point(((y * 3) % 16, y + 1), fill=grain)
    elif name == "paving":
        # A floor is not the source's chunky stone wall. Recessed joints keep
        # walkable stone distinct from solid masonry without drowning props.
        c = (39, 79, 66, 255)
        d.line((0, 0, 15, 0), fill=c)
        d.line((0, 0, 0, 7), fill=c)
        d.line((0, 8, 15, 8), fill=c)
        d.line((8, 8, 8, 15), fill=c)
    elif name in {"fence", "bars"}:
        for x in (2, 7, 12):
            d.rectangle((x, 1, x + 1, 14), fill=INK if name == "bars" else WOOD)
        d.line((0, 5, 15, 5), fill=INK)
        d.line((0, 11, 15, 11), fill=INK)
    elif name == "bed":
        d.rectangle((3, 1, 12, 14), fill=WOOD)
        d.rectangle((4, 2, 11, 5), fill=LIGHT)
        d.rectangle((4, 6, 11, 12), fill=(69, 171, 180, 255))
        d.line((4, 7, 11, 7), fill=INK)
    elif name in {"table", "counter"}:
        d.rectangle((2, 4, 13, 10), fill=WOOD, outline=GOLD)
        d.line((3, 6, 12, 6), fill=INK)
        for x in (3, 11):
            d.rectangle((x, 11, x + 1, 14), fill=WOOD)
        if name == "counter":
            d.rectangle((2, 3, 13, 5), fill=INK)
    elif name == "chair":
        d.rectangle((4, 2, 11, 8), outline=WOOD, width=2)
        d.rectangle((3, 9, 12, 11), fill=GOLD)
        d.line((4, 12, 4, 14), fill=WOOD)
        d.line((11, 12, 11, 14), fill=WOOD)
    elif name in {"shelf", "wardrobe"}:
        d.rectangle((2, 1, 13, 14), outline=WOOD)
        if name == "shelf":
            for y in (6, 12):
                d.line((3, y, 12, y), fill=GOLD)
                for x, c in ((4, INK), (7, GOLD), (10, WOOD)):
                    d.rectangle((x, y - 4, x + 1, y - 1), fill=c)
        else:
            d.line((7, 2, 7, 13), fill=WOOD)
            d.point((6, 8), fill=LIGHT)
            d.point((9, 8), fill=LIGHT)
    elif name == "folded_clothes":
        for x, y, c in ((3, 10, WOOD), (2, 7, INK), (5, 4, GOLD)):
            d.rectangle((x, y, x + 8, y + 2), fill=c)
            d.point((x + 1, y), fill=LIGHT)
    elif name in {"barrel", "well", "basket"}:
        d.rectangle((3, 4, 12, 13), fill=WOOD, outline=GOLD)
        d.ellipse((2, 2, 13, 6), fill=DARK, outline=INK)
        d.line((3, 8, 12, 8), fill=INK)
        if name == "well":
            d.line((2, 1, 2, 12), fill=WOOD)
            d.line((13, 1, 13, 12), fill=WOOD)
            d.line((2, 1, 13, 1), fill=GOLD)
            d.line((7, 1, 7, 5), fill=INK)
        if name == "basket":
            d.line((5, 2, 7, 7), fill=WOOD, width=2)
            d.line((10, 1, 8, 6), fill=GOLD, width=2)
    elif name == "anvil":
        d.polygon(((1, 4), (14, 4), (12, 7), (9, 8), (9, 11),
                   (12, 13), (3, 13), (6, 10), (6, 7), (3, 7)), fill=INK)
        d.line((1, 4, 14, 4), fill=LIGHT)
    elif name in {"forge", "mound", "rock", "stump"}:
        c = WOOD if name == "stump" else INK
        d.polygon(((2, 13), (2, 8), (5, 4), (10, 4), (13, 8), (13, 13)),
                  fill=c)
        d.line((3, 13, 12, 13), fill=GOLD)
        if name == "forge":
            d.rectangle((4, 8, 11, 11), fill=DARK)
            d.polygon(((5, 11), (6, 7), (8, 9), (9, 6), (11, 11)),
                      fill=(232, 80, 43, 255))
            d.line((6, 11, 9, 11), fill=GOLD)
        elif name == "stump":
            d.ellipse((3, 3, 12, 7), fill=GOLD, outline=WOOD)
    elif name == "driftwood":
        d.line((2, 12, 13, 5), fill=WOOD, width=3)
        d.line((7, 8, 5, 3), fill=GOLD, width=2)
        d.line((9, 7, 13, 9), fill=GOLD)
    elif name in {"seedling", "crop", "harvest"}:
        for x in ((7,) if name == "seedling" else (3, 8, 12)):
            d.line((x, 6, x, 13), fill=GOLD)
            d.line((x, 9, x - 2, 7), fill=(17, 141, 7, 255), width=2)
            d.line((x, 8, x + 2, 5), fill=INK if name == "harvest" else GOLD)
    elif name in {"torch", "torch_bracket", "peg"}:
        d.line((6, 7, 6, 13), fill=WOOD, width=2)
        d.line((6, 12, 11, 12), fill=INK, width=2)
        if name == "torch":
            d.polygon(((5, 7), (4, 4), (7, 1), (9, 5), (8, 7)), fill=GOLD)
            d.point((7, 4), fill=LIGHT)
    elif name == "bellows":
        d.polygon(((2, 8), (8, 3), (12, 6), (8, 12)), fill=WOOD, outline=GOLD)
        d.line((11, 6, 15, 5), fill=INK, width=2)
    elif name == "flue":
        d.rectangle((5, 1, 10, 14), fill=INK)
        for y in (4, 8, 12):
            d.line((5, y, 10, y), fill=DARK)
    elif name == "net":
        for x in range(2, 15, 3):
            d.line((x, 3, x, 12), fill=INK)
        for y in (3, 6, 9, 12):
            d.line((2, y, 14, y), fill=WOOD)
    elif name in {"plate", "pitcher"}:
        d.ellipse((3, 6, 12, 11), outline=INK)
        if name == "pitcher":
            d.rectangle((5, 3, 10, 10), fill=INK)
            d.rectangle((10, 4, 13, 7), outline=GOLD)
            d.line((5, 2, 10, 2), fill=LIGHT)
    elif name == "fish":
        d.ellipse((2, 6, 11, 10), fill=INK)
        d.polygon(((10, 8), (14, 5), (14, 11)), fill=GOLD)
        d.point((4, 7), fill=DARK)
    elif name == "tiger":
        d.rectangle((3, 6, 12, 10), fill=GOLD)
        d.rectangle((1, 4, 5, 8), fill=GOLD)
        d.point((1, 3), fill=GOLD)
        d.point((4, 3), fill=GOLD)
        for x in (4, 7, 10):
            d.line((x, 6, x + 1, 8), fill=DARK)
            d.line((x, 10, x, 13), fill=GOLD)
        d.line((12, 6, 14, 3), fill=GOLD)
        d.point((2, 5), fill=LIGHT)
    elif name in {"calendar", "notice"}:
        d.rectangle((3, 2, 12, 13), fill=INK)
        for y in (5, 8, 11):
            d.line((5, y, 10, y), fill=DARK)
        d.line((3, 2, 12, 2), fill=GOLD)
    elif name == "sand_alt":
        for x, y in ((2, 4), (8, 7), (13, 12)):
            d.point((x, y), fill=WOOD)
    elif name == "parcel":
        d.rectangle((4, 5, 11, 12), fill=WOOD, outline=GOLD)
        d.line((7, 5, 7, 12), fill=INK)
        d.line((4, 8, 11, 8), fill=INK)
    else:
        raise KeyError(name)
    return im


@lru_cache(maxsize=1)
def _sheet() -> Image.Image:
    with Image.open(ASSET_ROOT / SHEET) as source:
        if source.size != (256, 240):
            raise ValueError("EverRogue FULL sheet must be 256x240 (16px cells)")
        return source.convert("RGBA")


@lru_cache(maxsize=1024)
def source_sprite(name: str) -> Image.Image:
    if name in SOURCE_CELLS:
        x, y = SOURCE_CELLS[name]
        return _sheet().crop((x * 16, y * 16, x * 16 + 16, y * 16 + 16))
    if "@" in name:
        family, footprint = name.split("@")
        dimensions, position = footprint.split(":")
        w, h = map(int, dimensions.split("x"))
        x, y = map(int, position.split(","))
        furniture = source_sprite(family).resize((w * 16, h * 16), Image.Resampling.NEAREST)
        return furniture.crop((x * 16, y * 16, (x + 1) * 16, (y + 1) * 16))
    if ":" in name:
        family, value = name.split(":")
        mask = int(value)
        texture = source_sprite(family)
        if family == "fence":
            return texture.copy()
        # Real full-width masonry. Thin clipped strips made the cottage read
        # as fencing; topology now controls only the exposed face shading.
        out = Image.new("RGBA", (16, 16), DARK)
        out.alpha_composite(texture)
        d = ImageDraw.Draw(out)
        for bit, edge in ((1, (0, 0, 15, 0)), (2, (15, 0, 15, 15)),
                          (4, (0, 15, 15, 15)), (8, (0, 0, 0, 15))):
            if not mask & bit:
                d.line(edge, fill=(27, 49, 40, 255))
        return out
    return _supplement(name)


def fitted_sprite(name: str, width: int, height: int) -> Image.Image:
    if "@" in name:
        # Resize the whole prop once at its final footprint resolution, then
        # slice it. Resizing pre-sliced 16px pieces independently introduces
        # inconsistent nearest-neighbor sampling at fractional zoom factors.
        family, footprint = name.split("@")
        dimensions, position = footprint.split(":")
        w, h = map(int, dimensions.split("x"))
        x, y = map(int, position.split(","))
        size = min(width, height)
        furniture = fitted_sprite(family, 16, 16).resize(
            (w * size, h * size), Image.Resampling.NEAREST)
        piece = furniture.crop((x * size, y * size, (x + 1) * size, (y + 1) * size))
        frame = Image.new("RGBA", (width, height))
        frame.paste(piece, ((width - size) // 2, (height - size) // 2))
        return frame
    sprite = source_sprite(name)
    # Keep the audited source untouched, but replace its old matte in-game.
    pixels = np.asarray(sprite).copy()
    pixels[np.all(pixels[..., :3] == SOURCE_BACKGROUND, axis=-1), :3] = BACKGROUND
    sprite = Image.fromarray(pixels)
    if name in {"tree", "tree_gold", "tree_red", "tree_canopy"}:
        # Preserve the source silhouette, but give its flat-color canopy a
        # light-facing rim and cooler underside instead of one solid green.
        pixels = np.asarray(sprite).copy()
        yy, xx = np.mgrid[0:16, 0:16]
        solid = pixels[..., 3] > 0
        north = np.pad(solid, ((1, 0), (0, 0)))[:-1]
        south = np.pad(solid, ((0, 1), (0, 0)))[1:]
        tone = 1.16 - xx * .012 - yy * .014
        tone[solid & ~north] = 1.12
        tone[solid & ~south & (yy < 11)] = .73
        pixels[..., :3] = np.clip(pixels[..., :3] * tone[..., None], 0, 255).astype(np.uint8)
        sprite = Image.fromarray(pixels)
        # Soft contact shade follows the tree's feet, not its rectangular cell.
        mask = Image.new("L", (16, 16))
        ImageDraw.Draw(mask).ellipse((3, 11, 13, 15), fill=75)
        mask = mask.filter(ImageFilter.GaussianBlur(.8))
        shadow = Image.new("RGBA", (16, 16), (3, 22, 20, 0))
        shadow.putalpha(mask)
        shadow.alpha_composite(sprite)
        sprite = shadow
    # Fill the cell at every zoom. Rounding to whole 16px multiples shrank
    # 24px cells to 16px artwork and opened gaps in walls/furniture. Nearest
    # neighbor preserves hard pixel edges even at non-integral zoom factors.
    size = min(width, height)
    sprite = sprite.resize((size, size), Image.Resampling.NEAREST)
    frame = Image.new("RGBA", (width, height))
    frame.alpha_composite(sprite, ((width - size) // 2, (height - size) // 2))
    return frame


def install_sprite_atlas(tileset, tile_width: int, tile_height: int) -> dict[str, int]:
    for name, codepoint in SPRITE_CODEPOINTS.items():
        tileset[codepoint] = np.asarray(fitted_sprite(name, tile_width, tile_height))
    return dict(SPRITE_CODEPOINTS)


def sprite_for_tile(tile_id: int, door_open: bool = False, game_map=None,
                    x: int | None = None, y: int | None = None) -> str | None:
    tile = T.BY_ID.get(int(tile_id))
    if tile is None:
        return None
    if tile.name == "door":
        return "door_open" if door_open else "door_closed"
    if tile.name in WALL_NAMES:
        family = WALL_NAMES[tile.name]
        if game_map is None or x is None or y is None:
            return family
        mask = 0
        h, w = game_map.tiles.shape
        for bit, dx, dy in ((1, 0, -1), (2, 1, 0), (4, 0, 1), (8, -1, 0)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h:
                neighbor = T.BY_ID.get(int(game_map.tiles[ny, nx]))
                if neighbor and (neighbor.name == tile.name or neighbor.name == "door"):
                    mask |= bit
        return f"{family}:{mask}"
    key = TILE_SPRITES_BY_NAME.get(tile.name)
    if key in FURNITURE_FAMILIES and game_map is not None and x is not None and y is not None:
        key = _furniture_piece(key, tile_id, game_map.tiles, x, y)
    if x is not None and y is not None:
        variant = (x * 37 + y * 17 + x * y * 13) % 61
        if key == "grass":
            key = "grass_tuft" if variant == 0 else "grass" if variant == 23 else "bare_ground"
        elif key == "sand" and variant % 2:
            key = "sand_alt"
    return key


def shade_color(rgb, brightness):
    """Swap glyph ink toward the authored shadow color, without changing alpha."""
    if tuple(rgb) == BACKGROUND:
        return BACKGROUND
    b = max(0.0, min(1.0, float(brightness)))
    return tuple(round(s + (c - s) * b) for s, c in zip(SHADOW_COLOR, rgb))


def fade_level(brightness):
    return round(max(0.0, min(1.0, float(brightness))) * FADE_STEPS)


def fade_sprite(pixels, brightness):
    """Recolor opaque ink to #155352, retaining the original texture's tones.

    The dominant ink becomes exactly the shadow color. Other source colors
    retain ordered light/dark differences as a restrained quarter-strength
    tonal ramp, not a flat overlay. Alpha is never reduced. Canvas pixels stay
    #0F3B3A; full light restores the original in-game sprite exactly.
    """
    out = np.array(pixels, dtype=np.uint8, copy=True)
    b = fade_level(brightness) / FADE_STEPS
    if b == 1.0:
        return out
    rgb = out[..., :3].astype(np.float32)
    luma = np.sum(rgb * LUMA_WEIGHTS, axis=-1, keepdims=True)
    canvas = np.all(rgb == BACKGROUND, axis=-1) | np.all(rgb == SOURCE_BACKGROUND, axis=-1)
    ink = (out[..., 3] > 0) & ~canvas
    anchor = 0.0
    if np.any(ink):
        colors, counts = np.unique(out[..., :3][ink], axis=0, return_counts=True)
        anchor = float(np.sum(colors[np.argmax(counts)] * LUMA_WEIGHTS))
    shadow = np.asarray(SHADOW_COLOR) + (luma - anchor) * .25
    recolored = np.clip(np.rint(shadow + (rgb - shadow) * b), 0, 255).astype(np.uint8)
    recolored[canvas] = BACKGROUND
    out[..., :3] = recolored
    return out


def visual_brightness(game_map, light, x, y, brightness):
    """Palette strength with a softened sight edge; never changes gameplay light.

    Only explored neighbors contribute, so shading cannot disclose hidden
    rooms. A tree shades nearby ground, rather than painting a dark cell.
    """
    if not game_map.visible[y, x]:
        return MEMORY_EXPOSURE if game_map.explored[y, x] else 0.0
    h, w = game_map.tiles.shape
    occlusion = 0.0
    hidden = 0
    neighbors = 0
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            nx, ny = x + dx, y + dy
            if (dx or dy) and 0 <= nx < w and 0 <= ny < h:
                neighbors += 1
                hidden += not game_map.visible[ny, nx]
    for dx, dy in ((0, -1), (-1, 0), (1, 0), (0, 1), (-1, -1)):
        nx, ny = x + dx, y + dy
        if not (0 <= nx < w and 0 <= ny < h and game_map.explored[ny, nx]):
            continue
        neighbor = T.BY_ID.get(int(game_map.tiles[ny, nx]))
        if neighbor and (neighbor.name in WALL_NAMES or neighbor.name == "tree"):
            occlusion += .075 if dx <= 0 and dy <= 0 else .035
    edge = 1.0 - .42 * hidden / max(1, neighbors)
    return max(0.0, float(brightness) * edge * (1.0 - min(.24, occlusion)))


def _furniture_piece(key, tile_id, tiles, x, y):
    """One 3x2 bed / 4x3 table, not six beds / twelve tiny tables.

    Only bounded, rectangular, same-ID footprints qualify. No geometry or
    collision is changed, and nonrectangular structures retain ordinary art.
    """
    height, width = tiles.shape
    pending, visited = [(x, y)], set()
    while pending:
        at = pending.pop()
        if at in visited:
            continue
        visited.add(at)
        if len(visited) > 16:
            return key
        for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
            nx, ny = at[0] + dx, at[1] + dy
            if 0 <= nx < width and 0 <= ny < height and int(tiles[ny, nx]) == tile_id:
                if (nx, ny) not in visited:
                    pending.append((nx, ny))
    x0, y0 = min(at[0] for at in visited), min(at[1] for at in visited)
    w = max(at[0] for at in visited) - x0 + 1
    h = max(at[1] for at in visited) - y0 + 1
    if w > 4 or h > 4 or w * h != len(visited) or w * h == 1:
        return key
    return f"{key}@{w}x{h}:{x - x0},{y - y0}"


def sprite_for_entity(entity) -> str | None:
    if entity.get("role") == "player":
        return "player"
    # Creature appearance is identity-based: the frog/rat in the source sheet
    # must never be assigned to Miska's tiger just because it is a resident.
    identity = str(getattr(entity, "name", entity.get("name", "")))
    identity += " " + str(entity.get("character_id", ""))
    if "tiger" in identity.lower() or getattr(entity, "glyph", "") == "T":
        return "tiger"
    if entity.get("role") == "resident":
        if "jes" in identity.lower():
            return "scholar"
        return "npc"
    return None


def sprite_for_object(obj: dict, terrain_sprite: str | None = None) -> str | None:
    kind, identity = obj.get("kind"), str(obj.get("id", ""))
    if kind in {"door", "tree", "tutorial_marker", "shop", "enclosure"}:
        return None
    if kind in {"npc", "creature"}:
        if identity == "tiger":
            return "tiger"
        if "marine" in identity:
            return "guard"
        return "scholar" if identity in {"jes", "bookseller"} else "npc"
    if identity in OBJECT_SPRITES:
        return OBJECT_SPRITES[identity]
    if "chair" in identity or "stool" in identity:
        return "chair"
    if "window" in identity:
        return "window"
    if "wall_torch" in identity:
        return "torch_bracket"
    if "post_" in identity:
        return "pillar"
    # Keep the actual registered terrain for other authored fixtures. This
    # avoids turning coastline, roofs and facade entrances into generic loot.
    return {
        "container": "chest", "large_store": "chest", "shelf": "shelf",
        "bed": "bed", "charcoal_mound": "mound", "charcoal_site": "soil",
    }.get(obj.get("furniture_id", kind), terrain_sprite)


def sprite_for_item(item: dict) -> str:
    identity = str(item.get("id", ""))
    tags = set(item.get("tags", ()))
    if "book" in tags or "book" in identity or "notes" in identity:
        return "book"
    if "bucket" in identity:
        return "barrel"
    if "fish" in identity:
        return "fish"
    if any(word in identity for word in ("branch", "log", "firewood", "plank")):
        return "driftwood"
    if "water" in identity or "pitcher" in identity:
        return "pitcher"
    if "clothes" in identity or tags & {"clothing", "wearable"}:
        return "folded_clothes"
    return "parcel"
