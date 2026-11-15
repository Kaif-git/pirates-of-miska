"""Field of view + explored memory (fog of war).

Sight is computed with tcod shadowcasting; walls/trees/closed doors block it.
Tiles stay in `explored` memory once seen — the map fills in as you wander.
Lighting (brightness) lives in systems/lighting.py; this file only decides
what is *visible*, never how bright it is.
"""
from __future__ import annotations
import numpy as np
import tcod.map

from omnisim.world import tile as T

SIGHT_RADIUS = 12

_transparency_lut = np.array([t.transparent for t in T.TILE_DEFS], dtype=bool)


def compute_visible(game_map, x: int, y: int, radius: int = SIGHT_RADIUS) -> np.ndarray:
    """Bool (h, w) array of tiles visible from world (x, y).

    NOTE: tcod.map.compute_fov takes pov in (row, col) order for C-order
    (h, w) arrays, so we pass (y, x). Verified empirically — passing (x, y)
    centers sight on the transposed cell.
    """
    transparency = _transparency_lut[game_map.tiles].copy()
    # open doors see through; closed doors seal rooms
    door_cells = game_map.tiles == T.DOOR
    transparency |= door_cells & game_map.doors
    transparency &= ~(door_cells & ~game_map.doors)
    return tcod.map.compute_fov(
        transparency, (y, x), radius=radius, light_walls=True,
    )


def refresh_visibility(village, radius: int = SIGHT_RADIUS) -> None:
    """Recompute what the player sees; fold it into explored memory."""
    gm = village.game_map
    gm.visible = compute_visible(gm, village.player.x, village.player.y, radius)
    gm.explored |= gm.visible


