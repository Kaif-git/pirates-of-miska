"""Geometry-derived deck spaces and persistent, non-magical use designations.

Doors separate room identities even while open; aperture state is reported
separately. These are deck spaces, NOT sealed flood/air compartments yet.
"""
from collections import deque
from dataclasses import dataclass

from omnisim.world import tile as T
from pirates.ship_map import ORIGIN, WIDTH, HEIGHT

PURPOSES = ("working deck", "berths", "mess", "galley", "hold", "workshop",
            "infirmary", "magazine", "brig")
NEIGHBORS = ((0, -1), (1, 0), (0, 1), (-1, 0))


@dataclass(frozen=True)
class Space:
    cells: frozenset
    doors: frozenset
    exposed: bool


class ShipSpaces:
    def __init__(self):
        # Per-tile zones survive room splits, merges and temporary wall placement.
        self.zones = {}

    def geometry(self, model):
        floors, walls, doors = set(), set(), set()
        for c in model.components:
            if c.z != 0:
                continue
            at = (c.x, c.y)
            if c.kind == "floor":
                floors.add(at)
            elif c.kind == "wall":
                walls.add(at)
            elif c.kind == "door":
                doors.add(at)
        remaining = floors - walls - doors
        spaces = []
        while remaining:
            start = min(remaining)
            remaining.remove(start)
            cells, adjacent_doors, todo = {start}, set(), deque([start])
            exposed = False
            while todo:
                x, y = todo.popleft()
                for dx, dy in NEIGHBORS:
                    at = x + dx, y + dy
                    if at in remaining:
                        remaining.remove(at)
                        cells.add(at)
                        todo.append(at)
                    if at in doors:
                        adjacent_doors.add(at)
                    if at not in floors:
                        exposed = True
            spaces.append(Space(frozenset(cells), frozenset(adjacent_doors), exposed))
        return tuple(spaces)

    def find(self, model, wx, wy):
        local = wx - ORIGIN[0], wy - ORIGIN[1]
        return next((s for s in self.geometry(model) if local in s.cells), None)

    def purposes(self, space):
        return tuple(sorted({self.zones[at] for at in space.cells if at in self.zones}))

    def name(self, world, wx, wy):
        space = self.find(world.ship_model, wx, wy)
        if space:
            labels = self.purposes(space)
            return " / ".join(p.title() for p in labels) if labels else "Undesignated ship space"
        x, y = wx - ORIGIN[0], wy - ORIGIN[1]
        if world.ship_model.at(x, y, 0):
            return "Ship doorway / structure"
        return "Harbor / sea"

    def designate(self, world, wx, wy, purpose):
        if purpose is not None and purpose not in PURPOSES:
            raise ValueError("Unknown room purpose")
        space = self.find(world.ship_model, wx, wy)
        if space is None:
            raise ValueError("Stand inside a ship space, not a wall or doorway")
        for at in space.cells:
            if purpose is None:
                self.zones.pop(at, None)
            else:
                self.zones[at] = purpose
        return space

    def snapshot(self):
        return [{"x": x, "y": y, "purpose": p} for (x, y), p in sorted(self.zones.items())]

    @classmethod
    def restore(cls, rows, model):
        if not isinstance(rows, list) or len(rows) > WIDTH * HEIGHT:
            raise ValueError("Invalid room designations")
        floors = {(c.x, c.y) for c in model.components if c.z == 0 and c.kind == "floor"}
        result = cls()
        for row in rows:
            if (not isinstance(row, dict) or set(row) != {"x", "y", "purpose"}
                    or type(row["x"]) is not int or type(row["y"]) is not int
                    or row["purpose"] not in PURPOSES):
                raise ValueError("Invalid room designation")
            at = row["x"], row["y"]
            if at not in floors or at in result.zones:
                raise ValueError("Duplicate or off-deck room designation")
            result.zones[at] = row["purpose"]
        return result


def hold_routes(world):
    """Static shortest walking distances, excluding bodies, allowing door operation.

    These are tile steps, NOT timed or congestion-aware performance estimates.
    """
    gm = world.game_map
    x, y = world.ship.stations["hold"]
    starts = {(x + dx, y + dy) for dx, dy in NEIGHBORS
              if gm.in_bounds(x + dx, y + dy)
              and (gm.is_walkable(x + dx, y + dy) or gm.tile_id(x + dx, y + dy) == T.DOOR)}
    distance = {at: 0 for at in starts}
    todo = deque(sorted(starts))
    while todo:
        x, y = todo.popleft()
        for dx, dy in NEIGHBORS:
            at = x + dx, y + dy
            if (at not in distance and gm.in_bounds(*at)
                    and (gm.is_walkable(*at) or gm.tile_id(*at) == T.DOOR)):
                distance[at] = distance[x, y] + 1
                todo.append(at)
    return distance


def report_rows(world, wx, wy):
    spaces = world.spaces
    room = spaces.find(world.ship_model, wx, wy)
    if room is None:
        return ["Stand inside a ship space to inspect or designate it."]
    ox, oy = ORIGIN
    cells = {(x + ox, y + oy) for x, y in room.cells}
    stations = sorted(k for k, at in world.ship.stations.items()
                      if at in cells and world.fixtures.available(k))
    bunks = sum(at in cells and world.fixtures.available(f"bunk:{index}")
                for index, at in enumerate(world.ship.bunks))
    open_doors = sum(bool(world.game_map.doors[y + oy, x + ox]) for x, y in room.doors)
    free = sum(world.game_map.is_walkable(x, y) for x, y in cells)
    labels = spaces.purposes(room)
    rows = [spaces.name(world, wx, wy), f"Deck area: {len(cells)} tiles | walkable tiles: {free}",
            f"Boundary doors: {len(room.doors)} | currently open: {open_doors}",
            "Open to ship edge: " + ("yes" if room.exposed else "no"),
            "These are deck boundaries, not certified watertight or roofed rooms.",
            f"Installed bunks: {bunks}", "Stations: " + (", ".join(stations) or "none"),
            "", "DESIGNATION CHECKS (no passive bonuses)"]
    required = {"galley": "galley", "mess": "mess", "hold": "hold", "workshop": "workshop"}
    if not labels:
        rows.append("No purpose assigned. Existing stations still function.")
    for purpose in labels:
        if purpose == "berths":
            rows.append(f"Berths: {bunks} actual assigned bunk(s); a label creates no beds.")
        elif purpose in required:
            key = required[purpose]
            rows.append(f"{purpose.title()}: " + ("installed station present" if key in stations else "MISSING installed station"))
        elif purpose in ("infirmary", "magazine", "brig"):
            rows.append(f"{purpose.title()}: designation only; dedicated equipment/workflows not implemented.")
        else:
            rows.append("Working deck: usable space follows actual obstacles and access.")
    if len(labels) > 1:
        rows.append("Merged space has multiple purposes; original tile designations are retained.")
    rows += ["", "SUPPLY ACCESS (shortest tile steps; doors passable, people ignored)"]
    distances = hold_routes(world)
    for key in stations:
        x, y = world.ship.stations[key]
        values = [distances[at] for dx, dy in NEIGHBORS
                  if (at := (x + dx, y + dy)) in distances]
        rows.append(f"Hold -> {key}: " + (f"{min(values)} steps" if values else "UNREACHABLE"))
    return rows
