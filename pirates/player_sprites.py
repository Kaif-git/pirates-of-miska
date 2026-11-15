"""Character-only Kenney sprites layered onto the Urizen world art."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

ASSET = (Path(__file__).resolve().parents[1] / "assets" / "sprites" / "characters"
         / "kenney" / "captain_wide_brim_hat.png")
CAPTAIN_CODEPOINT = 0xE900


def install_player_sprite(tileset, tile_width: int, tile_height: int) -> dict[str, int]:
    """Add the single CC0 captain sprite to the atlas without mixing world tiles."""
    with Image.open(ASSET) as source:
        image = source.convert("RGBA")
    scale = min(tile_width / image.width, tile_height / image.height)
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    image = image.resize(size, Image.Resampling.NEAREST)
    frame = Image.new("RGBA", (tile_width, tile_height), (0, 0, 0, 0))
    frame.alpha_composite(image, ((tile_width - size[0]) // 2,
                                  (tile_height - size[1]) // 2))
    tileset[CAPTAIN_CODEPOINT] = np.asarray(frame, dtype=np.uint8)
    return {"captain": CAPTAIN_CODEPOINT}


def sprite_for_entity(entity) -> str | None:
    return "captain" if entity.get("role") == "player" else None
