"""Named places: answers "where am I?" and "what's that over there?".

Built once at worldgen from the scripted spec; pure data lookup afterwards.
Priority: building interior > special zone (market/fields/pond/gate) >
village streets (standing on road) > inside walls > the wilds.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Place:
    name: str
    x1: int
    y1: int
    x2: int
    y2: int
    indoor: bool = False

    def contains(self, x: int, y: int) -> bool:
        return self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2

    def center(self) -> tuple[int, int]:
        return ((self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2)


def build_places(spec: dict) -> list[Place]:
    places: list[Place] = []
    for b in spec.get("buildings", []):
        places.append(Place(b["name"], b["x"], b["y"],
                            b["x"] + b["w"] - 1, b["y"] + b["h"] - 1, indoor=True))
    m = spec.get("market")
    if m:
        places.append(Place("Market Square", m["x1"], m["y1"], m["x2"], m["y2"]))
    f = spec.get("farm")
    if f:
        places.append(Place("Tilled Fields", f["x1"], f["y1"], f["x2"], f["y2"]))
    p = spec.get("pond")
    if p:
        places.append(Place("Stillpond", p["cx"] - p["rx"], p["cy"] - p["ry"],
                             p["cx"] + p["rx"], p["cy"] + p["ry"]))
    wall = spec.get("wall", {})
    for g in spec.get("gates", []):
        gate_name = "North Gate" if g["y"] == wall.get("y1") else "South Gate"
        places.append(Place(gate_name, g["x"] - 2, g["y"] - 2, g["x"] + 2, g["y"] + 2))
    return places


def describe_location(village, x: int, y: int) -> str:
    """Human name for the tile (x, y): innards first, then zones, then town/wild."""
    from omnisim.world import tile as T
    for p in village.places:
        if p.indoor and p.contains(x, y):
            return p.name
    for p in village.places:
        if not p.indoor and p.contains(x, y):
            return p.name
    if village.game_map.in_bounds(x, y):
        if village.game_map.tile_id(x, y) == T.ROAD:
            return "Village Streets"
        wall = village.wall_rect
        if wall and wall[0] <= x <= wall[2] and wall[1] <= y <= wall[3]:
            return village.name
    return "The Wilds"


def compass(dx: int, dy: int) -> str:
    """Compass direction of (dx, dy). Straight-ish wins over diagonal."""
    if dx == 0 and dy == 0:
        return "here"
    horiz = "E" if dx > 0 else "W" if dx < 0 else ""
    vert = "S" if dy > 0 else "N" if dy < 0 else ""
    if horiz and vert and abs(dx) != abs(dy):
        return vert if abs(dy) > abs(dx) * 2 else horiz if abs(dx) > abs(dy) * 2 else vert + horiz
    return (vert + horiz) or "here"
