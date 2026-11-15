"""Ship-local construction data, independent of renderer tile identifiers.

This foundation deliberately does not claim to calculate seaworthiness yet.
"""
from dataclasses import dataclass
from collections import deque


@dataclass(frozen=True)
class Material:
    mass_kg: int
    strength: int


MATERIALS = {
    "pine": Material(40, 60),
    "oak": Material(60, 100),
    "iron": Material(180, 220),
}
# Mass per standardized component, intentionally balance data rather than density.
KINDS = {"floor", "wall", "door", "beam", "pillar", "stair"}
SLOTS = {"floor": "floor", "wall": "barrier", "door": "barrier",
         "beam": "support", "pillar": "support", "stair": "connector"}


@dataclass(frozen=True)
class Component:
    id: int
    x: int
    y: int
    z: int
    kind: str
    material: str
    condition: int = 100

    def __post_init__(self):
        if any(type(value) is not int for value in
               (self.id, self.x, self.y, self.z, self.condition)):
            raise ValueError("Component coordinates and values must be integers")
        if self.id < 1 or not 0 <= self.condition <= 100:
            raise ValueError("Invalid component identity or condition")
        if self.kind not in KINDS or self.material not in MATERIALS:
            raise ValueError("Unknown component kind or material")


class ShipModel:
    """Sparse arbitrary-shaped decks with independent component slots."""

    def __init__(self):
        self._components = {}
        self._slots = {}
        self.next_id = 1

    @property
    def components(self):
        return tuple(self._components[key] for key in sorted(self._components))

    def place(self, x, y, z, kind, material="oak"):
        component = Component(self.next_id, x, y, z, kind, material)
        if kind == "stair":
            if not any(c.kind == "floor" and (c.x, c.y, c.z) == (x, y, z)
                       for c in self.components):
                raise ValueError("A stair needs a lower-deck floor")
            if not any(c.kind == "floor" and (c.x, c.y, c.z) == (x, y, z + 1)
                       for c in self.components):
                raise ValueError("A stair needs an upper-deck floor directly above")
        self._insert(component)
        self.next_id += 1
        return component

    def _insert(self, component):
        key = (component.x, component.y, component.z, SLOTS[component.kind])
        if component.id in self._components or key in self._slots:
            raise ValueError("Overlapping component or duplicate identity")
        self._components[component.id] = component
        self._slots[key] = component.id

    def remove(self, component_id):
        component = self._components.pop(component_id)
        del self._slots[(component.x, component.y, component.z, SLOTS[component.kind])]
        return component

    def at(self, x, y, z):
        return tuple(c for c in self.components if (c.x, c.y, c.z) == (x, y, z))

    def mass_summary(self):
        """Structure only; damage does not silently remove component mass."""
        total = sum(MATERIALS[c.material].mass_kg for c in self.components)
        center = None if not total else tuple(
            sum(getattr(c, axis) * MATERIALS[c.material].mass_kg
                for c in self.components) / total for axis in ("x", "y", "z"))
        return {"structural_mass_kg": total, "center_of_mass": center}

    def deck_geometry(self, z=0):
        """Return measurable deck footprint/contour data from built floor tiles.

        A contour edge is an exposed cardinal side of an actual floor component;
        it is not guessed from the starter rectangle or renderer tiles.
        """
        if type(z) is not int:
            raise ValueError("Deck elevation must be an integer")
        floors = {(c.x, c.y) for c in self.components if c.z == z and c.kind == "floor"}
        if not floors:
            return {"deck": z, "tiles": 0, "bounds": None, "perimeter_edges": (),
                    "perimeter": 0, "components": 0, "holes": 0}
        xmin, xmax = min(x for x, _ in floors), max(x for x, _ in floors)
        ymin, ymax = min(y for _, y in floors), max(y for _, y in floors)
        directions = ((0, -1), (1, 0), (0, 1), (-1, 0))
        edges = tuple(sorted((x, y, dx, dy) for x, y in floors for dx, dy in directions
                             if (x + dx, y + dy) not in floors))
        unseen = set(floors)
        component_count = 0
        while unseen:
            component_count += 1
            todo = [unseen.pop()]
            while todo:
                x, y = todo.pop()
                for dx, dy in directions:
                    at = x + dx, y + dy
                    if at in unseen:
                        unseen.remove(at)
                        todo.append(at)
        # Empty regions entirely enclosed by deck tiles are structural cutouts;
        # water outside the footprint is not counted as an interior hole.
        empty = {(x, y) for x in range(xmin, xmax + 1) for y in range(ymin, ymax + 1)
                 if (x, y) not in floors}
        holes = 0
        while empty:
            seed = empty.pop()
            todo = [seed]
            touches_bounds = False
            while todo:
                x, y = todo.pop()
                touches_bounds |= x in (xmin, xmax) or y in (ymin, ymax)
                for dx, dy in directions:
                    at = x + dx, y + dy
                    if at in empty:
                        empty.remove(at)
                        todo.append(at)
            if not touches_bounds:
                holes += 1
        return {"deck": z, "tiles": len(floors), "bounds": (xmin, ymin, xmax, ymax),
                "perimeter_edges": edges, "perimeter": len(edges),
                "components": component_count, "holes": holes}

    def snapshot(self):
        from dataclasses import asdict
        return {"version": 1, "next_id": self.next_id,
                "components": [asdict(c) for c in self.components]}

    @classmethod
    def restore(cls, data):
        if not isinstance(data, dict) or set(data) != {"version", "next_id", "components"}:
            raise ValueError("Invalid ship model")
        if type(data["version"]) is not int or data["version"] != 1:
            raise ValueError("Unsupported ship model version")
        if type(data["next_id"]) is not int or data["next_id"] < 1:
            raise ValueError("Invalid next component identity")
        if not isinstance(data["components"], list):
            raise ValueError("Invalid component list")
        model = cls()
        try:
            for row in data["components"]:
                model._insert(Component(**row))
        except (TypeError, KeyError) as error:
            raise ValueError("Invalid component record") from error
        if data["next_id"] <= max(model._components, default=0):
            raise ValueError("Component identity would be reused")
        for c in model.components:
            if c.kind == "stair" and not all(
                    any(f.kind == "floor" and (f.x, f.y, f.z) == (c.x, c.y, c.z + dz)
                        for f in model.components) for dz in (0, 1)):
                raise ValueError("Stair connector has no floor at both deck ends")
        model.next_id = data["next_id"]
        return model
