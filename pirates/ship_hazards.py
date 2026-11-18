"""Persisted breaches and deliberately coarse, mass-conserving flooding."""

from collections import deque
import math

from pirates.ship_map import ORIGIN, WIDTH, HEIGHT, project


class ShipHazards:
    VERSION = 2
    TILE_CAPACITY_KG = 1_000_000_000.0
    MAX_FLOW_KG_PER_SECOND = 0.5
    LEGACY_BILGE_KG_PER_POINT = 10.0

    def __init__(self):
        self.breaches = {}  # "x,y,z" -> kg/s ingress rate
        self.water = {}     # "x,y,z" -> kilograms of water on that floor
        self.fires = {}     # "x,y,z" -> heat intensity, sustained by real wooden components
        self.smoke = {}     # "x,y,z" -> coarse local smoke concentration

    @staticmethod
    def key(x, y, z):
        return f"{x},{y},{z}"

    @staticmethod
    def cell(key):
        return tuple(map(int, key.split(",")))

    def seed_legacy_bilge(self, world):
        """Migrate the old ship-wide bilge meter into its physical bilge tile."""
        if self.water or world.ship.bilge <= 0:
            return
        station = world.ship.stations.get("bilge")
        at = ((station[0] - ORIGIN[0], station[1] - ORIGIN[1], station[2] if len(station) == 3 else 0)
              if station else None)
        floors = {(c.x, c.y, c.z) for c in world.ship_model.components if c.kind == "floor"}
        if not at or at not in floors:
            at = next(((c.x, c.y, c.z) for c in world.ship_model.components
                       if c.kind == "floor" and c.z == 0), None)
        if at:
            self.water[self.key(*at)] = world.ship.bilge * self.LEGACY_BILGE_KG_PER_POINT

    def snapshot(self):
        return {"version": self.VERSION,
                "breaches": [[*self.cell(key), rate] for key, rate in sorted(self.breaches.items())],
                "water": [[*self.cell(key), amount]
                          for key, amount in sorted(self.water.items()) if amount > 0],
                "fires": [[*self.cell(key), amount]
                          for key, amount in sorted(self.fires.items()) if amount > 0],
                "smoke": [[*self.cell(key), amount]
                          for key, amount in sorted(self.smoke.items()) if amount > 0]}

    @classmethod
    def restore(cls, data, model):
        old_shape = {"version", "breaches", "water"}
        new_shape = old_shape | {"fires", "smoke"}
        if (not isinstance(data, dict) or set(data) not in (old_shape, new_shape)
                or type(data["version"]) is not int or data["version"] not in (1, cls.VERSION)
                or not isinstance(data["breaches"], list) or not isinstance(data["water"], list)):
            raise ValueError("Invalid ship hazard state")
        result = cls()
        floors = {(c.x, c.y, c.z) for c in model.components if c.kind == "floor"}
        for rows, target, upper in ((data["breaches"], result.breaches, 100.0),
                                    (data["water"], result.water, cls.TILE_CAPACITY_KG),
                                    (data.get("fires", []), result.fires, 10.0),
                                    (data.get("smoke", []), result.smoke, 100.0)):
            for row in rows:
                if (not isinstance(row, list) or len(row) != 4
                        or any(type(n) is not int for n in row[:3])
                        or tuple(row[:3]) not in floors
                        or type(row[3]) not in (int, float) or not math.isfinite(row[3])
                        or not 0 < row[3] <= upper):
                    raise ValueError("Invalid local breach/flood record")
                key = cls.key(*row[:3])
                if key in target:
                    raise ValueError("Duplicate local breach/flood record")
                target[key] = float(row[3])
        for key in result.breaches:
            x, y, z = result.cell(key)
            if any(c.kind == "wall" for c in model.at(x, y, z)):
                raise ValueError("A sealed hull wall cannot retain an active breach")
        for key in result.fires:
            x, y, z = result.cell(key)
            if not any(c.kind in ("floor", "wall", "door", "beam", "pillar", "stair")
                       and c.material != "iron" for c in model.at(x, y, z)):
                raise ValueError("A fire must remain on a combustible component")
        return result

    def ignite(self, world, x, y, z=0, intensity=1.0):
        """Start a coarse fire on an actual combustible structural component."""
        if (any(type(n) is not int for n in (x, y, z)) or not 0 <= x < WIDTH
                or not 0 <= y < HEIGHT or not 0 <= z <= 8
                or type(intensity) not in (int, float) or not math.isfinite(intensity)
                or not 0 < intensity <= 10):
            raise ValueError("Invalid fire location or intensity")
        key = self.key(x, y, z)
        fuel = self._fuel_component(world, x, y, z)
        if fuel is None:
            raise ValueError("Fire needs a real combustible ship component")
        self.fires[key] = max(self.fires.get(key, 0.0), float(intensity))
        self.smoke[key] = min(100.0, self.smoke.get(key, 0.0) + intensity * 4)
        world.message(f"FIRE on {fuel.material} {fuel.kind} at deck {z}, tile ({x},{y}).")
        return key

    def breach(self, world, x, y, z=0, rate_kg_s=0.75):
        """Damage an actual exterior hull wall and open that same traversable tile."""
        if (any(type(n) is not int for n in (x, y, z)) or not 0 <= x < WIDTH
                or not 0 <= y < HEIGHT or not 0 <= z <= 8
                or type(rate_kg_s) not in (int, float) or not math.isfinite(rate_kg_s)
                or not 0 < rate_kg_s <= 100):
            raise ValueError("Invalid breach location or severity")
        key = self.key(x, y, z)
        if key in self.breaches:
            raise ValueError("That hull tile is already breached")
        parts = world.ship_model.at(x, y, z)
        floor = any(c.kind == "floor" for c in parts)
        wall = next((c for c in parts if c.kind == "wall"), None)
        if not floor or wall is None or not self._outer_wall(world, x, y, z):
            raise ValueError("Breach damage must strike an exterior hull wall")
        world.ship_model.remove(wall.id)
        self.breaches[key] = float(rate_kg_s) * max(0.15, 1.0 - z * 0.3)
        self._project(world, z)
        world.message(f"HULL BREACH at deck {z}, tile ({x},{y}); water is entering.")
        return key

    def close_breach(self, x, y, z=0):
        return self.breaches.pop(self.key(x, y, z), None) is not None

    def extinguish(self, x, y, z=0, water_units=1):
        """Use delivered water to cool a real fire; smoke dissipates separately."""
        if type(water_units) is not int or water_units < 1:
            raise ValueError("Fire suppression requires a positive whole water-unit count")
        key = self.key(x, y, z)
        intensity = self.fires.get(key)
        if intensity is None:
            return False
        remaining = intensity - 2.5 * water_units
        if remaining <= 0.05:
            self.fires.pop(key, None)
            return True
        self.fires[key] = remaining
        self.smoke[key] = max(0.0, self.smoke.get(key, 0.0) - 12.0 * water_units)
        return False

    def total_water_kg(self):
        return math.fsum(self.water[key] for key in sorted(self.water))

    def pump(self, kilograms, world=None):
        """Discharge water through the existing staffed bilge-pump work loop."""
        remaining = max(0.0, float(kilograms))
        discharged = 0.0
        candidates = list(self.water)
        if world is not None:
            station = world.ship.stations.get("bilge")
            if station is None:
                return 0.0
            from pirates.ship_navigation import route
            start = (station[0] - ORIGIN[0], station[1] - ORIGIN[1],
                     station[2] if len(station) == 3 else 0)
            candidates = [key for key in candidates
                          if route(world.ship_model, start, self.cell(key)) is not None]
        for key in sorted(candidates, key=lambda k: (self.cell(k)[2], self.cell(k)[1], self.cell(k)[0])):
            take = min(self.water[key], remaining)
            self.water[key] -= take
            remaining -= take
            discharged += take
            if remaining <= 0:
                break
        self.water = {key: value for key, value in self.water.items() if value > 0.001}
        return discharged

    def advance(self, world):
        # Keep the pre-existing global leak meter compatible while migrating
        # its increments into the local water ledger at the bilge station.
        difference_kg = (world.ship.bilge * self.LEGACY_BILGE_KG_PER_POINT
                         - self.total_water_kg())
        legacy_kg = difference_kg if difference_kg > 0.000001 else 0.0
        if legacy_kg > 0:
            station = world.ship.stations.get("bilge")
            at = ((station[0] - ORIGIN[0], station[1] - ORIGIN[1],
                   station[2] if len(station) == 3 else 0) if station else
                  next(((c.x, c.y, c.z) for c in world.ship_model.components if c.kind == "floor"), None))
            if at:
                key = self.key(*at)
                self.water[key] = self.water.get(key, 0.0) + legacy_kg

        for key, rate in tuple(self.breaches.items()):
            self.water[key] = self.water.get(key, 0.0) + rate
        self._spread(world)
        self._advance_fire(world)
        # Canonical meter precision prevents harmless JSON/dict summation
        # roundoff from forking deterministic save/resume simulation.
        world.ship.bilge = min(100.0, round(
            self.total_water_kg() / self.LEGACY_BILGE_KG_PER_POINT, 9))

    def report(self):
        return {"breaches": tuple((self.cell(key), rate) for key, rate in sorted(self.breaches.items())),
                "water_kg": round(self.total_water_kg(), 1),
                "wet_tiles": tuple((self.cell(key), round(amount, 1))
                                    for key, amount in sorted(self.water.items()) if amount > 0.1),
                "fires": tuple((self.cell(key), round(amount, 1))
                               for key, amount in sorted(self.fires.items())),
                "smoke_tiles": tuple((self.cell(key), round(amount, 1))
                                     for key, amount in sorted(self.smoke.items()) if amount > 1)}

    @staticmethod
    def _fuel_component(world, x, y, z):
        return next((c for c in world.ship_model.at(x, y, z)
                     if c.kind in ("floor", "wall", "door", "beam", "pillar", "stair")
                     and c.material != "iron" and c.condition > 0), None)

    def _advance_fire(self, world):
        # Smoke is a bounded connected-space approximation, diffusing through open
        # passages and stairs; closed doors contain it and concentration slowly vents.
        spread_smoke = dict(self.smoke)
        for key, level in tuple(self.smoke.items()):
            x, y, z = self.cell(key)
            spread_smoke[key] = max(0.0, level * 0.985 - 0.08)
            for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)):
                at = (x + dx, y + dy, z)
                if not any(c.kind == "floor" for c in world.ship_model.at(*at)):
                    continue
                if self._passage_closed(world, (x, y, z), at):
                    continue
                target = self.key(*at)
                spread_smoke[target] = min(100.0, spread_smoke.get(target, 0.0) + level * 0.025)
            for connector in world.ship_model.at(x, y, z):
                if connector.kind != "stair":
                    continue
                upper = (x, y, z + 1)
                target = self.key(*upper)
                spread_smoke[target] = min(100.0, spread_smoke.get(target, 0.0) + level * 0.04)
        self.smoke = {k: v for k, v in spread_smoke.items() if v > 0.1}

        next_fires = {}
        for key, intensity in sorted(self.fires.items()):
            x, y, z = self.cell(key)
            fuel = self._fuel_component(world, x, y, z)
            if fuel is None:
                continue
            world.ship_model.set_condition(fuel.id, max(0, fuel.condition - max(1, int(intensity))))
            self.smoke[key] = min(100.0, self.smoke.get(key, 0.0) + intensity * 0.8)
            next_fires[key] = max(0.2, intensity * 0.995)
            for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)):
                nx, ny = x + dx, y + dy
                neighbor = self._fuel_component(world, nx, ny, z)
                if neighbor and (world.ship.seconds + nx * 7 + ny * 13) % 20 == 0:
                    target = self.key(nx, ny, z)
                    next_fires[target] = max(next_fires.get(target, 0.0), intensity * 0.45)
            for sailor in world.ship.crew:
                if sailor.z == z and abs(sailor.x - (x + ORIGIN[0])) + abs(sailor.y - (y + ORIGIN[1])) <= 1:
                    sailor.energy = max(0, sailor.energy - intensity * 0.15)
                    sailor.stress = min(100, sailor.stress + intensity * 0.25)
                    cause = f"fire at deck {z}, tile ({x},{y})"
                    severity = 1 if intensity < 3 else 2 if intensity < 6 else 3
                    if sailor.suffer_injury("burn", severity, cause):
                        world.message(f"{sailor.name} suffered a severity-{severity} burn near ({x},{y}).")
        self.fires = next_fires

    @staticmethod
    def _passage_closed(world, a, b):
        barriers = {(c.x, c.y, c.z): c.kind for c in world.ship_model.components
                    if c.kind in ("wall", "door")}
        for x, y, z in (a, b):
            if barriers.get((x, y, z)) == "wall":
                return True
            if barriers.get((x, y, z)) == "door":
                wx, wy = x + ORIGIN[0], y + ORIGIN[1]
                if not world.door_state(z)[wy, wx]:
                    return True
        return False

    def _spread(self, world):
        floors = {(c.x, c.y, c.z) for c in world.ship_model.components if c.kind == "floor"}
        barriers = {(c.x, c.y, c.z): c.kind for c in world.ship_model.components
                    if c.kind in ("wall", "door")}
        for key in sorted(self.water):
            x, y, z = self.cell(key)
            for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)):
                at = (x + dx, y + dy, z)
                neighbor_key = self.key(*at)
                if at not in floors or barriers.get(at) == "wall":
                    continue
                if barriers.get((x, y, z)) == "door":
                    wx, wy = x + ORIGIN[0], y + ORIGIN[1]
                    if not world.door_state(z)[wy, wx]:
                        continue
                if barriers.get(at) == "door":
                    wx, wy = x + dx + ORIGIN[0], y + dy + ORIGIN[1]
                    if not world.door_state(z)[wy, wx]:
                        continue
                source = self.water.get(key, 0.0)
                target = self.water.get(neighbor_key, 0.0)
                diff = source - target
                if diff <= 1.0:
                    continue
                transfer = min(self.MAX_FLOW_KG_PER_SECOND, diff * 0.02,
                               source / 4, self.TILE_CAPACITY_KG - target)
                if transfer > 0:
                    self.water[key] -= transfer
                    self.water[neighbor_key] = target + transfer
        self.water = {key: value for key, value in self.water.items() if value > 0.001}

    @staticmethod
    def _outer_wall(world, x, y, z):
        floors = {(c.x, c.y) for c in world.ship_model.components if c.kind == "floor" and c.z == z}
        if (x, y) not in floors:
            return False
        xmin, ymin = -1, -1
        xmax, ymax = WIDTH, HEIGHT
        empty = {(cx, cy) for cy in range(ymin, ymax + 1)
                 for cx in range(xmin, xmax + 1) if (cx, cy) not in floors}
        exterior, todo = set(), deque([(-1, -1)])
        empty.discard((-1, -1))
        while todo:
            cx, cy = todo.popleft()
            exterior.add((cx, cy))
            for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)):
                at = cx + dx, cy + dy
                if at in empty:
                    empty.remove(at)
                    todo.append(at)
        return any((x + dx, y + dy) in exterior
                   for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)))

    @staticmethod
    def _project(world, z):
        if z == world.active_deck:
            project(world.ship_model, world.fixtures.overlay_for(z), world.game_map, z)
            world.ship_fixtures = world.fixtures.overlay_for(z)
            world.refresh()
