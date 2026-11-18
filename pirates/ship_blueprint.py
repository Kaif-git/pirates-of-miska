"""Persistent unbuilt structural edits with bounded undo and selection transforms."""
from copy import deepcopy

from pirates.ship_model import ShipModel
from pirates.ship_map import WIDTH, HEIGHT

KINDS = {"floor", "wall", "door", "stair", "beam", "pillar"}
ACTIONS = {"build", "dismantle"}


class ShipBlueprint:
    def __init__(self):
        self.changes = []
        self.history = []

    @staticmethod
    def key(change):
        return change["x"], change["y"], change["z"], change["kind"]

    def set(self, x, y, z, action, kind):
        self._commit([{"x": x, "y": y, "z": z, "action": action, "kind": kind}])

    def _commit(self, rows, model=None):
        """Apply one undoable drawing gesture, rolling back invalid proposals."""
        old = deepcopy(self.changes)
        updated = {self.key(row): row for row in old}
        for row in rows:
            self._validate_change(row)
            updated[self.key(row)] = deepcopy(row)
        candidate = sorted(updated.values(), key=lambda r: (r["z"], r["y"], r["x"], r["kind"]))
        self.changes = candidate
        try:
            if model is not None:
                self.apply(model)
        except Exception:
            self.changes = old
            raise
        if candidate != old:
            self.history.append(old)
            self.history = self.history[-100:]
        return len(candidate) - len(old)

    @staticmethod
    def _line_points(x1, y1, x2, y2):
        """Integer Bresenham line including both cursor endpoints."""
        dx, sx = abs(x2 - x1), 1 if x1 < x2 else -1
        dy, sy = -abs(y2 - y1), 1 if y1 < y2 else -1
        error = dx + dy
        points = []
        while True:
            points.append((x1, y1))
            if (x1, y1) == (x2, y2):
                return points
            twice = 2 * error
            if twice >= dy:
                error += dy
                x1 += sx
            if twice <= dx:
                error += dx
                y1 += sy

    def draw_line(self, model, start, end, action, kind, *, symmetry=False, z=0):
        points = self._line_points(*start, *end)
        return self._draw_points(model, points, action, kind, symmetry, z)

    def draw_rectangle(self, model, start, end, action, kind, *, symmetry=False, z=0):
        x1, y1 = start
        x2, y2 = end
        points = set()
        for x in range(min(x1, x2), max(x1, x2) + 1):
            points.update(((x, y1), (x, y2)))
        for y in range(min(y1, y2), max(y1, y2) + 1):
            points.update(((x1, y), (x2, y)))
        return self._draw_points(model, sorted(points), action, kind, symmetry, z)

    def _draw_points(self, model, points, action, kind, symmetry, z=0):
        expanded = set(points)
        if symmetry:
            expanded.update((WIDTH - 1 - x, y) for x, y in points)
        return self._commit([{"x": x, "y": y, "z": z, "action": action, "kind": kind}
                             for x, y in sorted(expanded)], model)

    def flood_fill(self, model, start, action, kind, z=0):
        """Fill a same-kind occupancy region within the current deck bounds.

        The deck bounding box is an intentional safety boundary: an empty-cell
        fill cannot accidentally spread over the entire ship grid into the sea.
        """
        final = self.apply(model)
        floors = [(c.x, c.y) for c in final.components if c.z == z and c.kind == "floor"]
        if not floors:
            raise ValueError("Flood fill needs an existing deck footprint")
        xmin, xmax = min(x for x, _ in floors), max(x for x, _ in floors)
        ymin, ymax = min(y for _, y in floors), max(y for _, y in floors)
        sx, sy = start
        if not (xmin <= sx <= xmax and ymin <= sy <= ymax):
            raise ValueError("Flood fill cursor is outside the deck bounds")
        def occupied(x, y):
            return any(c.kind == kind for c in final.at(x, y, z))
        target = occupied(sx, sy)
        todo, region, seen = [(sx, sy)], set(), {(sx, sy)}
        while todo:
            x, y = todo.pop()
            if occupied(x, y) != target:
                continue
            region.add((x, y))
            for at in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if xmin <= at[0] <= xmax and ymin <= at[1] <= ymax and at not in seen:
                    seen.add(at)
                    todo.append(at)
        return self._draw_points(model, sorted(region), action, kind, False, z)

    def undo(self):
        if not self.history:
            return False
        self.changes = self.history.pop()
        return True

    def clear(self):
        if self.changes:
            self.history.append(deepcopy(self.changes))
            self.history = self.history[-100:]
            self.changes = []

    def discard(self, change):
        self.history.append(deepcopy(self.changes))
        self.history = self.history[-100:]
        key = self.key(change)
        self.changes = [row for row in self.changes if self.key(row) != key]

    @classmethod
    def _validate_change(cls, row):
        if (not isinstance(row, dict) or set(row) != {"x", "y", "z", "action", "kind"}
                or any(type(row[k]) is not int for k in ("x", "y", "z"))
                or not 0 <= row["x"] < WIDTH or not 0 <= row["y"] < HEIGHT or not 0 <= row["z"] <= 8
                or row["action"] not in ACTIONS or row["kind"] not in KINDS):
            raise ValueError("Invalid unbuilt blueprint change")

    def apply(self, model):
        result = ShipModel.restore(model.snapshot())
        # Establish new floor before placing its enclosing wall/door.
        ordered = sorted(self.changes, key=lambda r: (
            0 if r["action"] == "dismantle" else 1 if r["kind"] == "floor" else 2,
            r["y"], r["x"]))
        for row in ordered:
            self._validate_change(row)
            parts = result.at(row["x"], row["y"], row["z"])
            existing = next((c for c in parts if c.kind == row["kind"]), None)
            if row["action"] == "dismantle":
                if existing is None:
                    raise ValueError(f"Blueprint target changed: no {row['kind']} to dismantle")
                result.remove(existing.id)
            elif existing is None:
                result.place(row["x"], row["y"], row["z"], row["kind"])
        return result

    def transform_selection(self, model, x1, y1, x2, y2, destination, *, mirror_x=False, rotate=0, z=0):
        """Copy actual-plus-planned structural components into a translated selection."""
        if any(type(v) is not int for v in (x1, y1, x2, y2, *destination)):
            raise ValueError("Selection coordinates must be integers")
        lo_x, hi_x = sorted((x1, x2))
        lo_y, hi_y = sorted((y1, y2))
        if not (0 <= lo_x <= hi_x < WIDTH and 0 <= lo_y <= hi_y < HEIGHT):
            raise ValueError("Selection outside ship grid")
        if rotate not in (0, 90, 180, 270):
            raise ValueError("Rotation must be a quarter turn")
        final = self.apply(model)
        source = [c for c in final.components if lo_x <= c.x <= hi_x and lo_y <= c.y <= hi_y and c.z == z]
        if not source:
            raise ValueError("Selection contains no built or planned structure")
        width, height = hi_x - lo_x + 1, hi_y - lo_y + 1
        copied = []
        for component in source:
            dx, dy = component.x - lo_x, component.y - lo_y
            if mirror_x:
                dx = width - 1 - dx
            if rotate == 90:
                dx, dy = height - 1 - dy, dx
            elif rotate == 180:
                dx, dy = width - 1 - dx, height - 1 - dy
            elif rotate == 270:
                dx, dy = dy, width - 1 - dx
            x, y = destination[0] + dx, destination[1] + dy
            if not (0 <= x < WIDTH and 0 <= y < HEIGHT):
                raise ValueError("Transformed selection extends beyond the ship grid")
        for component in source:
            dx, dy = component.x - lo_x, component.y - lo_y
            if mirror_x:
                dx = width - 1 - dx
            if rotate == 90:
                dx, dy = height - 1 - dy, dx
            elif rotate == 180:
                dx, dy = width - 1 - dx, height - 1 - dy
            elif rotate == 270:
                dx, dy = dy, width - 1 - dx
            copied.append({"x": destination[0] + dx, "y": destination[1] + dy,
                           "z": z, "action": "build", "kind": component.kind})
        old_changes = deepcopy(self.changes)
        self.history.append(old_changes)
        self.history = self.history[-100:]
        for row in copied:
            self._validate_change(row)
            key = self.key(row)
            self.changes = [current for current in self.changes if self.key(current) != key]
            self.changes.append(row)
        self.changes.sort(key=lambda r: (r["z"], r["y"], r["x"], r["kind"]))
        try:
            self.apply(model)
        except Exception:
            self.changes = old_changes
            self.history.pop()
            raise
        return len(source)

    def snapshot(self):
        return {"version": 1, "changes": deepcopy(self.changes), "history": deepcopy(self.history)}

    @classmethod
    def restore(cls, data):
        if (not isinstance(data, dict) or set(data) != {"version", "changes", "history"}
                or type(data["version"]) is not int or data["version"] != 1
                or not isinstance(data["changes"], list) or len(data["changes"]) > WIDTH * HEIGHT * 3
                or not isinstance(data["history"], list) or len(data["history"]) > 100):
            raise ValueError("Invalid ship blueprint")
        result = cls()
        for row in data["changes"]:
            cls._validate_change(row)
        if len({cls.key(r) for r in data["changes"]}) != len(data["changes"]):
            raise ValueError("Duplicate blueprint target")
        for snapshot in data["history"]:
            if not isinstance(snapshot, list) or len(snapshot) > WIDTH * HEIGHT * 3:
                raise ValueError("Invalid blueprint undo history")
            for row in snapshot:
                cls._validate_change(row)
        result.changes = deepcopy(data["changes"])
        result.history = deepcopy(data["history"])
        return result
