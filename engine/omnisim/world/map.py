"""Tile grid backed by numpy for fast FOV/pathfinding with 100+ combatants."""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np

from omnisim.world import tile as T


@dataclass
class GameMap:
    width: int
    height: int
    tiles: np.ndarray  # shape (height, width), dtype int8 of tile ids
    explored: np.ndarray = field(init=False)
    visible: np.ndarray = field(init=False)

    doors: np.ndarray = field(default=None)  # open-door state, shape (h, w)

    def __post_init__(self) -> None:
        self.explored = np.zeros((self.height, self.width), dtype=bool)
        self.visible = np.zeros((self.height, self.width), dtype=bool)
        if self.doors is None:
            self.doors = np.zeros((self.height, self.width), dtype=bool)

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def tile_id(self, x: int, y: int) -> int:
        return int(self.tiles[y, x])

    def is_open_door(self, x: int, y: int) -> bool:
        return bool(self.doors[y, x]) if self.in_bounds(x, y) else False

    def is_walkable(self, x: int, y: int) -> bool:
        if not self.in_bounds(x, y):
            return False
        if int(self.tiles[y, x]) == T.DOOR:
            return bool(self.doors[y, x])
        return T.BY_ID[int(self.tiles[y, x])].walkable

