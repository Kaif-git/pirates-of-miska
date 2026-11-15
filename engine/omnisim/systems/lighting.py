"""Lantern + torch lighting. Pure numpy, no tcod — headless-testable.

Brightness per tile (0..1) = ambient dusk + player lantern falloff + torch
glow with a slow sin flicker (torches breathe as turns pass). Only *visible*
tiles get full light; remembered (explored, not visible) tiles render flat dim
— handled by the renderer, not here.
"""
from __future__ import annotations
import math
import numpy as np

AMBIENT = 0.22
LANTERN_RADIUS = 10
TORCH_RADIUS = 7


def _falloff(dist2: np.ndarray, radius: int) -> np.ndarray:
    d = np.sqrt(dist2) / radius
    return np.clip(1.0 - d * d, 0.0, 1.0)


def compute_light(village, ambient: float = AMBIENT) -> np.ndarray:
    gm = village.game_map
    ys, xs = np.mgrid[0:gm.height, 0:gm.width]
    light = np.full((gm.height, gm.width), ambient, dtype=float)

    px, py = village.player.x, village.player.y
    light += _falloff((xs - px) ** 2 + (ys - py) ** 2, LANTERN_RADIUS)

    for i, (tx, ty) in enumerate(village.torch_xy):
        flicker = 0.90 + 0.10 * math.sin(village.turn * 0.9 + i * 2.1)
        light += flicker * _falloff((xs - tx) ** 2 + (ys - ty) ** 2, TORCH_RADIUS)

    return np.clip(light, 0.0, 1.0)
