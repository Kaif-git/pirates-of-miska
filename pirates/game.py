"""Independent world adapter for the copied Miska rendering engine."""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from omnisim.world import tile as T
from omnisim.world.map import GameMap
from omnisim.world.fov import refresh_visibility
from tales_sim.clock import GameClock, TICKS_PER_SECOND
from tales_sim.inventory import Inventory, movement_cost_ticks
from pirates.ship import ShipLife, STATIONS, BUNKS, build_ship
from pirates.ship_map import capture_starter, project
from pirates.ship_model import ShipModel
from pirates.ship_spaces import ShipSpaces
from pirates.ship_fixtures import FixtureLayout
from pirates.ship_blueprint import ShipBlueprint
from pirates.economy import Economy, PORTS, MERCHANTS, build_port
from pirates.voyage import VoyageSystem, NpcFleetSystem

ROOT = Path(__file__).resolve().parents[1]
SAVE_PATH = ROOT / "saves" / "captain.json"
POCKETS = [{"id": "satchel", "name": "Captain's satchel",
            "max_weight_g": 12000, "max_bulk_ml": 18000}]


@dataclass
class Captain:
    x: int = 76
    y: int = 25
    id: str = "captain"
    name: str = "Captain"
    glyph: str = "@"
    color: tuple = (245, 225, 165)

    def get(self, key, default=None):
        return "player" if key == "role" else getattr(self, key, default)


class World:
    def __init__(self):
        # Walkable port, a connected pier, and a moored starter vessel.
        tiles = np.full((64, 100), T.WATER, dtype=np.int8)
        tiles[8:56, 8:45] = T.GRASS
        tiles[10:54, 40:45] = T.ROAD
        tiles[22:26, 12:70] = T.ROAD
        tiles[16:34, 69:81] = T.FLOOR
        tiles[16, 69:81] = tiles[33, 69:81] = T.WALL_WOOD
        tiles[16:34, 69] = tiles[16:34, 80] = T.WALL_WOOD
        tiles[23:25, 69] = T.FLOOR  # gangplank
        tiles[11:20, 19:32] = T.FLOOR
        tiles[11, 19:32] = tiles[19, 19:32] = T.WALL_STONE
        tiles[11:20, 19] = tiles[11:20, 31] = T.WALL_STONE
        tiles[19, 25] = T.DOOR
        tiles[13, 21] = T.CHEST
        tiles[17, 73] = T.TABLE  # helm placeholder
        tiles[21, 79] = tiles[28, 79] = T.ANVIL  # cannon placeholders
        for x, y in [(12, 12), (15, 30), (21, 40), (36, 15), (35, 47)]:
            tiles[y, x] = T.TREE
        build_ship(tiles)
        self.game_map = GameMap(100, 64, tiles)
        self.ship_model, self.ship_fixtures = capture_starter(tiles)
        self.fixtures = FixtureLayout.starter(tiles, STATIONS, BUNKS)
        self.ship_fixtures = self.fixtures.overlay
        self.spaces = ShipSpaces()
        self.blueprint = ShipBlueprint()
        project(self.ship_model, self.ship_fixtures, self.game_map)
        self.player = Captain()
        self.clock = GameClock()
        self.inventory = Inventory(deepcopy(POCKETS))
        self.light = np.ones(tiles.shape, dtype=np.float32)
        self.watch_id = None
        self.objects_at = {
            (21, 13): [{"name": "Harbor supply chest (scenery)"}],
        }
        self.refresh_fixture_objects()
        self.ship = ShipLife()
        self.ship.layout = self.fixtures
        self.ship.generate_jobs()
        self.ground_items = {(31, 23): [
            {"id": "rope", "name": "Coiled rope", "quantity": 1,
             "weight_g": 1500, "bulk_ml": 2500},
            {"id": "biscuit", "name": "Ship's biscuit", "quantity": 3,
             "weight_g": 100, "bulk_ml": 150},
        ]}
        self.log = ["Welcome aboard the Wayward. F2: crew and labors. A: chores. F4: let time pass."]
        self.economy = Economy(self.clock.day)
        build_port(self)
        self.voyages = VoyageSystem()
        self.npcs = NpcFleetSystem()
        self.at_sea = False
        self.refresh()

    @property
    def ground(self):
        return list(self.ground_items)

    def entities(self):
        return [self.player, *self.ship.crew, *([] if self.at_sea else self.economy.merchants())]

    def enter_sea(self, preserve_harbor=False):
        if not self.at_sea and not preserve_harbor:
            self.economy.harbors[self.economy.port] = self.economy.remember_harbor(self)
        self.at_sea = True
        if self.player.x < 69:
            self.player.x, self.player.y = 76, 25
        self.game_map.tiles[:, :69] = T.WATER
        self.game_map.doors[:, :69] = False
        for at in list(self.objects_at):
            self.objects_at[at] = [o for o in self.objects_at[at] if not o.get("port_object")]
            if not self.objects_at[at]:
                del self.objects_at[at]
        self.refresh()

    def restore_harbor(self, port):
        memory = self.economy.harbors[port]
        self.game_map.explored[:, :45] = memory["explored"]
        self.game_map.doors[:, :45] = memory["doors"]
        self.game_map.visible[:, :45] = False
        self.ground_items = {at: items for at, items in self.ground_items.items() if at[0] >= 45}
        self.ground_items.update({tuple(map(int, key.split(','))): deepcopy(items)
                                  for key, items in memory["ground"].items()})
        self.refresh()

    def on_ship_second(self):
        underway = bool(self.voyages.voyage and self.voyages.voyage["status"] == "underway")
        sighting = self.npcs.advance_second(self, self.voyages.map_coordinate(), sighting_enabled=underway)
        if underway:
            self.voyages.step_second(self, sighting)

    def advance(self, ticks):
        if type(ticks) is not int or ticks < 0:
            raise ValueError("Time must advance by a non-negative integer tick count")
        self.voyages.arrival_interrupt = False
        self.ship.advance(self, ticks)
        self.economy.update_day(self.clock.day)
        self.refresh()

    def refresh(self):
        refresh_visibility(self, radius=18)

    def room_name(self, x, y):
        if self.at_sea and x < 69:
            return "Open sea"
        if x < 45:
            return PORTS[self.economy.port]["name"]
        return self.spaces.name(self, x, y)

    def refresh_fixture_objects(self):
        # Only replace our own equipment inspection records, preserving other objects.
        for at in list(self.objects_at):
            self.objects_at[at] = [o for o in self.objects_at[at] if "fixture_id" not in o]
            if not self.objects_at[at]:
                del self.objects_at[at]
        for fixture in self.fixtures.fixtures:
            if fixture.carrier is not None:
                continue
            at = self.fixtures.world_position(fixture)
            name = (f"Crew bunk {int(fixture.id.split(':')[1]) + 1}" if fixture.kind == "bunk"
                    else fixture.kind.replace("_", " ").title())
            self.objects_at.setdefault(at, []).append({"name": name, "fixture_id": fixture.id,
                                                      "station": fixture.kind})

    def bind_fixtures(self, layout):
        """Commit a prevalidated equipment transition and update all live adapters."""
        project(self.ship_model, layout.overlay, self.game_map)
        self.fixtures = layout
        self.ship_fixtures = layout.overlay
        self.ship.layout = layout
        self.refresh_fixture_objects()
        self.refresh()

    def message(self, text):
        self.log.append(text)
        self.log = self.log[-60:]

    def move(self, dx, dy):
        if (dx, dy) not in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            return False
        x, y = self.player.x + dx, self.player.y + dy
        if not self.game_map.is_walkable(x, y):
            self.message("The way is blocked. Use Enter near a door to open it.")
            return False
        if any((c.x, c.y) == (x, y) for c in self.entities() if c is not self.player):
            self.message("A crewmate is in the way. Wait a moment or go around.")
            return False
        self.player.x, self.player.y = x, y
        self.advance(movement_cost_ticks(
            TICKS_PER_SECOND, self.inventory.carried_weight_g(), 5000, 10, 100))
        self.refresh()
        return True

    def toggle_door(self):
        x, y = self.player.x, self.player.y
        for dx, dy in ((0, 0), (0, -1), (1, 0), (0, 1), (-1, 0)):
            nx, ny = x + dx, y + dy
            if (self.game_map.in_bounds(nx, ny)
                    and self.game_map.tile_id(nx, ny) == T.DOOR):
                if (nx, ny) == (x, y) or any((c.x, c.y) == (nx, ny) for c in self.ship.crew):
                    self.message("Step out of the doorway before closing it.")
                    return
                self.game_map.doors[ny, nx] ^= True
                self.advance(TICKS_PER_SECOND)
                self.refresh()
                self.message("Door opened." if self.game_map.doors[ny, nx] else "Door closed.")
                return
        self.message("No door within reach.")

    def pickup(self):
        at = self.player.x, self.player.y
        items = self.ground_items.get(at, [])
        if not items:
            self.message("Nothing to pick up here. Stand on a * supply pile.")
            return
        taken = 0
        for item in list(items):
            if self.inventory.add(deepcopy(item)):
                items.remove(item)
                taken += 1
        if not items:
            self.ground_items.pop(at, None)
        if taken:
            self.advance(TICKS_PER_SECOND)
        self.message(f"Picked up {taken} stack(s)." + (" Satchel full." if items else ""))

    def drop(self, index):
        item = self.inventory.pop(index)
        item.pop("pocket", None)
        self.ground_items.setdefault((self.player.x, self.player.y), []).append(item)
        self.advance(TICKS_PER_SECOND)
        self.message(f"Dropped {item['name']}.")

    def save(self, path=SAVE_PATH):
        data = {"version": 3, "player": [self.player.x, self.player.y],
                "ticks": self.clock.total_ticks, "inventory": self.inventory.items,
                "ground": {f"{x},{y}": items for (x, y), items in self.ground_items.items()},
                "doors": self.game_map.doors.tolist(),
                "explored": self.game_map.explored.tolist(), "ship": self.ship.snapshot(),
                "ship_model": self.ship_model.snapshot(), "room_zones": self.spaces.snapshot(),
                "fixtures": self.fixtures.snapshot(), "economy": self.economy.snapshot(self),
                "blueprint": self.blueprint.snapshot(), "voyage": self.voyages.snapshot(),
                "npcs": self.npcs.snapshot()}
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary.replace(path)
        self.message(f"Saved {path.name}.")

    @classmethod
    def load(cls, path=SAVE_PATH):
        # Construct/validate a replacement world; never partially mutate live state.
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or type(data.get("version")) is not int
                or data.get("version") not in (1, 2, 3)):
            raise ValueError("Unsupported save version")
        world = cls()
        gm = world.game_map
        if "economy" in data:
            if (not isinstance(data["economy"], dict) or not isinstance(data["economy"].get("port"), str)
                    or data["economy"]["port"] not in PORTS):
                raise ValueError("Invalid economy / island")
            world.economy.port = data["economy"]["port"]
            build_port(world)
        if data["version"] == 3:
            world.ship_model = ShipModel.restore(data["ship_model"])
            if "fixtures" in data:
                world.fixtures = FixtureLayout.restore(data["fixtures"], world.fixtures)
                world.ship_fixtures = world.fixtures.overlay
                world.ship.layout = world.fixtures
                world.refresh_fixture_objects()
            project(world.ship_model, world.ship_fixtures, gm)
        world.spaces = ShipSpaces.restore(data.get("room_zones", []), world.ship_model)
        world.blueprint = ShipBlueprint.restore(data["blueprint"]) if "blueprint" in data else ShipBlueprint()
        world.blueprint.apply(world.ship_model)
        coordinates = data["player"]
        if not isinstance(coordinates, list) or len(coordinates) != 2:
            raise ValueError("Invalid captain coordinates")
        x, y = coordinates
        if type(x) is not int or type(y) is not int:
            raise ValueError("Invalid captain coordinates")
        for key in ("doors", "explored"):
            array = np.asarray(data[key])
            if array.shape != gm.tiles.shape or array.dtype != np.bool_:
                raise ValueError(f"Invalid {key} grid")
            setattr(gm, key, array.copy())
        # A saved door-state bit cannot turn a removed wall/floor into a door.
        gm.doors[gm.tiles != T.DOOR] = False
        def migrate_old_deck_position(px, py, occupied=()):
            # Only former walkable deck cells may be displaced by new fixtures.
            if (data["version"] == 1 and 70 <= px <= 79 and 17 <= py <= 32
                    and (px, py) not in ((73, 17), (79, 21), (79, 28))
                    and not gm.is_walkable(px, py)):
                choices = [(nx, ny) for ny in range(17, 33) for nx in range(70, 80)
                           if gm.is_walkable(nx, ny) and (nx, ny) not in occupied]
                if choices:
                    return min(choices, key=lambda at: (abs(at[0] - px) + abs(at[1] - py), at))
            new_stalls = {(m["at"][0], m["at"][1] - 1) for m in MERCHANTS.values() if m["port"] == "verdant"}
            merchants = {m["at"] for m in MERCHANTS.values() if m["port"] == "verdant"}
            if "economy" not in data and (px, py) in new_stalls | merchants:
                for dx, dy in ((0, 1), (1, 0), (0, -1), (-1, 0)):
                    at = px + dx, py + dy
                    if gm.is_walkable(*at) and at not in occupied and at not in merchants:
                        return at
            return px, py
        x, y = migrate_old_deck_position(x, y, {(c.x, c.y) for c in world.ship.crew})
        if not gm.is_walkable(x, y):
            raise ValueError("Captain is not on a walkable tile")
        ticks = data["ticks"]
        if type(ticks) is not int or ticks < 0:
            raise ValueError("Invalid clock")
        def validate_items(items):
            if not isinstance(items, list):
                raise ValueError("Invalid item list")
            for item in items:
                if (not isinstance(item, dict)
                        or not all(isinstance(item.get(k), str) for k in ("id", "name"))
                        or any(type(item.get(k)) is not int or item[k] < minimum
                               for k, minimum in (("quantity", 1), ("weight_g", 0), ("bulk_ml", 0)))):
                    raise ValueError("Invalid item stack")
        validate_items(data["inventory"])
        world.inventory = Inventory(deepcopy(POCKETS), data["inventory"])
        ground = {}
        if not isinstance(data["ground"], dict):
            raise ValueError("Invalid ground items")
        for key, items in data["ground"].items():
            gx, gy = map(int, key.split(","))
            gx, gy = migrate_old_deck_position(gx, gy)
            if not gm.is_walkable(gx, gy):
                raise ValueError("Invalid ground location")
            validate_items(items)
            ground.setdefault((gx, gy), []).extend(items)
        world.ground_items = ground
        world.player.x, world.player.y = x, y
        world.clock = GameClock(ticks)
        if data["version"] in (2, 3):
            try:
                world.ship = ShipLife.restore(world, data["ship"])
            except (KeyError, TypeError, AttributeError, OverflowError) as error:
                raise ValueError(f"Invalid ship save: {error}") from error
        else:
            # Old saves have no crew state. Retain captain/cargo/time, add new crew.
            for sailor in world.ship.crew:
                if (sailor.x, sailor.y) == (x, y):
                    occupied = {(c.x, c.y) for c in world.ship.crew} | {(x, y)}
                    for nx, ny in sorted(world.ship.goals(world, (x, y))):
                        if gm.is_walkable(nx, ny) and (nx, ny) not in occupied:
                            sailor.x, sailor.y = nx, ny
                            break
                    else:
                        raise ValueError("Cannot safely place legacy crew")
        try:
            world.economy = Economy.restore(data.get("economy"), world)
        except (KeyError, TypeError, AttributeError, OverflowError) as error:
            raise ValueError(f"Invalid economy save: {error}") from error
        world.voyages = VoyageSystem.restore(data.get("voyage"), world)
        world.npcs = NpcFleetSystem.restore(data.get("npcs"))
        if any((world.player.x, world.player.y) == merchant["at"] for merchant in MERCHANTS.values()
               if merchant["port"] == world.economy.port):
            if "economy" in data:
                raise ValueError("Captain overlaps a merchant")
            # Legacy captains may have occupied a newly authored merchant tile.
            occupied = {(c.x, c.y) for c in world.entities() if c is not world.player}
            candidates = [(world.player.x + dx, world.player.y + dy) for dx, dy in ((0, 1), (1, 0), (0, -1), (-1, 0))]
            safe = next((at for at in candidates if gm.is_walkable(*at) and at not in occupied), None)
            if safe is None:
                raise ValueError("Cannot safely migrate captain past new merchants")
            world.player.x, world.player.y = safe
        if world.voyages.voyage and world.voyages.voyage["status"] in ("underway", "encounter"):
            world.enter_sea(preserve_harbor=True)
        world.refresh()
        world.message("Captain's save restored.")
        return world
