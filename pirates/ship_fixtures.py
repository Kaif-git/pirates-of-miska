"""Persisted functional equipment identities, separate from structural floors.

Starter constants define migration templates only. Live station/bunk positions
are read from this registry. Installation/relocation is handled by physical jobs.
"""
from dataclasses import dataclass, replace

from omnisim.world import tile as T
from pirates.ship_map import ORIGIN, WIDTH, HEIGHT


@dataclass(frozen=True)
class Fixture:
    id: str
    kind: str
    x: int
    y: int
    z: int
    tile: int
    mass_kg: int
    carrier: str | None = None


class FixtureLayout:
    def __init__(self, fixtures, reference_points):
        self._fixtures = {f.id: f for f in fixtures}
        self.reference_points = dict(reference_points)

    @classmethod
    def starter(cls, tiles, stations, bunks):
        fixtures = []
        points = {k: tuple(stations[k]) for k in ("repair", "bilge")}
        for key, (x, y) in stations.items():
            if key in points:
                continue
            mass = 700 if "cannon" in key else 150 if key == "rigging" else 60
            fixtures.append(Fixture(key, key, x - ORIGIN[0], y - ORIGIN[1], 0,
                                    int(tiles[y, x]), mass))
        for index, (x, y) in enumerate(bunks):
            fixtures.append(Fixture(f"bunk:{index}", "bunk", x - ORIGIN[0], y - ORIGIN[1],
                                    0, T.BED, 30))
        return cls(fixtures, points)

    @property
    def fixtures(self):
        return tuple(self._fixtures[k] for k in sorted(self._fixtures))

    def get(self, fid):
        return self._fixtures.get(fid)

    @staticmethod
    def world_position(fixture):
        return fixture.x + ORIGIN[0], fixture.y + ORIGIN[1]

    @property
    def stations(self):
        return self.reference_points | {f.id: self.world_position(f)
                                        for f in self.fixtures if f.kind != "bunk"}

    @property
    def bunks(self):
        return [self.world_position(self._fixtures[f"bunk:{i}"])
                for i in range(sum(f.kind == "bunk" for f in self.fixtures))]

    @property
    def overlay(self):
        # Include floor-glyph stations as well, so they cannot be walled over.
        return {(f.x, f.y): f.tile for f in self.fixtures if f.carrier is None}

    def available(self, fid):
        fixture = self.get(fid)
        return fixture is None or fixture.carrier is None

    def carried(self, fid, sailor_id):
        fixture = self.get(fid)
        if fixture is None or fixture.carrier is not None:
            raise ValueError("Equipment is already being transported")
        return FixtureLayout([replace(f, carrier=sailor_id) if f.id == fid else f
                              for f in self.fixtures], self.reference_points)

    def relocated(self, fid, wx, wy):
        fixture = self.get(fid)
        if fixture is None or type(wx) is not int or type(wy) is not int:
            raise ValueError("Invalid equipment identity or destination")
        x, y = wx - ORIGIN[0], wy - ORIGIN[1]
        if not (0 <= x < WIDTH and 0 <= y < HEIGHT):
            raise ValueError("Equipment destination is outside the ship")
        if any(f.id != fid and (f.x, f.y) == (x, y) for f in self.fixtures):
            raise ValueError("Equipment destination is occupied")
        return FixtureLayout([replace(f, x=x, y=y, carrier=None) if f.id == fid else f
                              for f in self.fixtures], self.reference_points)

    def snapshot(self):
        return [{"id": f.id, "x": f.x, "y": f.y, "z": f.z, "carrier": f.carrier}
                for f in self.fixtures]

    @classmethod
    def restore(cls, rows, canonical):
        if not isinstance(rows, list) or len(rows) != len(canonical.fixtures):
            raise ValueError("Invalid equipment registry")
        fixtures, ids, occupied = [], set(), set()
        for row in rows:
            if (not isinstance(row, dict) or set(row) not in ({"id", "x", "y", "z"}, {"id", "x", "y", "z", "carrier"})
                    or not isinstance(row["id"], str)
                    or (row.get("carrier") is not None and not isinstance(row["carrier"], str))
                    or any(type(row[k]) is not int for k in ("x", "y", "z"))):
                raise ValueError("Invalid equipment record")
            original = canonical.get(row["id"])
            at = row["x"], row["y"], row["z"]
            if (original is None or row["id"] in ids or at in occupied
                    or not (0 <= row["x"] < WIDTH and 0 <= row["y"] < HEIGHT)
                    or row["z"] != 0):
                raise ValueError("Invalid equipment identity or overlapping position")
            ids.add(row["id"])
            occupied.add(at)
            fixtures.append(replace(original, x=row["x"], y=row["y"], z=row["z"], carrier=row.get("carrier")))
        return cls(fixtures, canonical.reference_points)
