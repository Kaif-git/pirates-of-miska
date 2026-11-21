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
from pirates.ship_map import ORIGIN, WIDTH, HEIGHT, capture_starter, project
from pirates.ship_model import ShipModel
from pirates.ship_spaces import ShipSpaces
from pirates.ship_fixtures import FixtureLayout
from pirates.ship_hazards import ShipHazards
from pirates.ship_blueprint import ShipBlueprint
from pirates.economy import Economy, PORTS, MERCHANTS, build_port
from pirates.voyage import VoyageSystem, NpcFleetSystem
from pirates.society import NpcSocietySystem
from pirates.scenario import load_scenario
from pirates.combat import CombatArena

ROOT = Path(__file__).resolve().parents[1]
SAVE_PATH = ROOT / "saves" / "captain.json"
MVP_SAVE_PATH = ROOT / "saves" / "lawful-trader-mvp.json"
MVP_SCENARIO_ID = "lawful-trader-mvp"
POCKETS = [{"id": "satchel", "name": "Captain's satchel",
            "max_weight_g": 12000, "max_bulk_ml": 18000}]


@dataclass
class Captain:
    x: int = 76
    y: int = 25
    z: int = 0
    id: str = "captain"
    name: str = "Captain"
    glyph: str = "@"
    color: tuple = (245, 225, 165)

    def get(self, key, default=None):
        return "player" if key == "role" else getattr(self, key, default)


class World:
    def __init__(self):
        self.scenario_id = None
        self.scenario_version = None
        self.save_path = SAVE_PATH
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
        self.active_deck = 0
        self.deck_door_states = {0: self.game_map.doors.copy()}
        self.locked_doors = set()  # ship-local (x,y,z), independent of open/closed state
        self.lockdown = False
        self.traffic_overlay = {}  # transient (ship-local x,y,z) -> shared route count
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
        self.hazards = ShipHazards()
        self.hazards.seed_legacy_bilge(self)
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
        self.generation_manifest = None
        self.voyages = VoyageSystem()
        self.npcs = NpcFleetSystem()
        initial_npc_hour = self.clock.total_ticks // 360000
        self.npcs.hour = initial_npc_hour
        self.npcs._seed_motion(self.clock.total_ticks)
        self.society = NpcSocietySystem(self.npcs, initial_npc_hour)
        self.combat = CombatArena()
        self.duel_journal = None  # New sword-duel session, learning and between-fight carry.
        self.at_sea = False
        self.refresh()

    @classmethod
    def new_generated(cls, seed=None, settings=None):
        """Create a new seeded NPC society without changing the authored test world."""
        from pirates.npc_generation import generate_society, make_manifest, run_prehistory

        manifest = make_manifest(seed, settings)
        generated = generate_society(manifest)
        world = cls()
        world.generation_manifest = manifest
        world.npcs = NpcFleetSystem.from_generated(generated)
        world.npcs.hour = world.clock.total_ticks // 360000
        world.npcs._seed_motion(world.clock.total_ticks)
        world.society = NpcSocietySystem.from_generated(
            generated, world.npcs, world.clock.total_ticks // 360000)
        run_prehistory(world)
        world.refresh()
        return world

    @classmethod
    def new_authored_mvp(cls):
        """Create the fixed lawful-trader sample without generated history."""
        from pirates.voyage import SEA_NODES
        scenario = load_scenario(MVP_SCENARIO_ID, ports=PORTS, sea_nodes=SEA_NODES)
        if scenario["entry_port"] not in PORTS:
            raise ValueError("Authored scenario entry port is unavailable")
        world = cls()
        world.scenario_id = scenario["id"]
        world.scenario_version = scenario["version"]
        world.save_path = MVP_SAVE_PATH
        world.economy.port = scenario["entry_port"]
        build_port(world)

        # The legacy authored roster contains a captive market. The lawful MVP
        # keeps the named crew/vessels but starts without that feature or ledger.
        world.npcs.market["enslaved_people"] = 0
        for ship in world.npcs.ships:
            ship["captives"] = 0
        world.npcs.population_total = sum(ship["crew"] for ship in world.npcs.ships)
        world.refresh()
        return world

    @property
    def ground(self):
        return [(at[0], at[1]) for at in self.ground_items
                if (self.active_deck == 0 and len(at) == 2)
                or (len(at) == 3 and at[2] == self.active_deck)]

    def ground_at(self, x, y, z=None):
        z = self.active_deck if z is None else z
        return self.ground_items.get((x, y) if z == 0 else (x, y, z), [])

    def entities(self):
        if self.combat.active:
            return [self.player, *self.combat.visible_entities()]
        else:
            crew = [c for c in self.ship.crew if c.z == self.player.z]
        merchants = [] if self.at_sea or self.player.z != 0 else self.economy.merchants()
        return [self.player, *crew, *merchants, *self.combat.visible_entities()]

    def all_entities(self):
        if self.combat.active:
            return self.entities()
        return [self.player, *self.ship.crew, *([] if self.at_sea else self.economy.merchants()),
                *self.combat.visible_entities()]

    def door_state(self, z):
        if z == self.active_deck:
            return self.game_map.doors
        return self.deck_door_states.setdefault(z, np.zeros(self.game_map.tiles.shape, dtype=np.bool_))

    def set_active_deck(self, z):
        """Render a built deck while retaining independent door-open state per deck."""
        if type(z) is not int or z < 0:
            raise ValueError("Invalid deck elevation")
        if not any(c.kind == "floor" and c.z == z for c in self.ship_model.components):
            raise ValueError("That deck has no built floor")
        if z == self.active_deck:
            return
        self.deck_door_states[self.active_deck] = self.game_map.doors.copy()
        project(self.ship_model, self.fixtures.overlay_for(z), self.game_map, z)
        self.ship_fixtures = self.fixtures.overlay_for(z)
        state = self.deck_door_states.get(z)
        if state is None:
            state = np.zeros(self.game_map.tiles.shape, dtype=np.bool_)
        self.game_map.doors = state.copy()
        self.game_map.doors[self.game_map.tiles != T.DOOR] = False
        self.active_deck = z
        self.refresh_fixture_objects()
        self.refresh()

    def move_vertical(self, delta):
        if type(delta) is not int or delta not in (-1, 1):
            return False
        x, y = self.player.x - ORIGIN[0], self.player.y - ORIGIN[1]
        stair_z = self.player.z if delta > 0 else self.player.z - 1
        stair = any(c.kind == "stair" and (c.x, c.y, c.z) == (x, y, stair_z)
                    for c in self.ship_model.components)
        if not stair:
            self.message("No stair connects this position to that deck.")
            return False
        destination = self.player.z + delta
        if not any(c.kind == "floor" and (c.x, c.y, c.z) == (x, y, destination)
                   for c in self.ship_model.components):
            self.message("The stair has no built floor at its other end.")
            return False
        if any((e.x, e.y, getattr(e, "z", 0)) ==
               (self.player.x, self.player.y, destination)
               for e in self.all_entities() if e is not self.player):
            self.message("Someone is occupying the stair landing.")
            return False
        self.player.z = destination
        self.set_active_deck(destination)
        self.advance(TICKS_PER_SECOND)
        self.message(f"Moved to deck {destination}.")
        return True

    def enter_sea(self, preserve_harbor=False):
        if not self.at_sea and not preserve_harbor:
            self.economy.harbors[self.economy.port] = self.economy.remember_harbor(self)
        self.at_sea = True
        active_transfer = bool(
            getattr(self, "voyages", None)
            and self.voyages.voyage
            and self.voyages.voyage.get("deck_visit") is not None)
        if self.player.x < 69 and not active_transfer:
            self.player.x, self.player.y = 76, 25
        self.game_map.tiles[:, :69] = T.WATER
        self.game_map.doors[:, :69] = False
        for at in list(self.objects_at):
            self.objects_at[at] = [o for o in self.objects_at[at] if not o.get("port_object")]
            if not self.objects_at[at]:
                del self.objects_at[at]
        if hasattr(self, "voyages"):
            self.voyages.rebuild_transfer_deck(self)
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
        self.voyages.refresh_transfer_state(self)
        voyage_status = self.voyages.voyage.get("status") if self.voyages.voyage else None
        underway = voyage_status == "underway"
        encounter = self.voyages.voyage.get("encounter") if self.voyages.voyage else None
        naval_combat = bool(encounter and encounter.get("combat", {}).get("status") == "active")
        if naval_combat:
            # Keep the sighted vessel in range for a turn-based artillery exchange.
            sighting = None
        else:
            previous_npc_events = list(self.npcs.events[-80:])
            reserved = ({encounter["id"]} if encounter and isinstance(encounter.get("id"), str)
                        and self.voyages.voyage.get("status") == "encounter" else set())
            eligible_for_lookout = bool(
                underway and self.voyages.voyage.get("elapsed_seconds", 0) >= 1800)
            sighting = self.npcs.advance_second(
                self, self.voyages.map_coordinate(), sighting_enabled=eligible_for_lookout,
                reserved_ship_ids=reserved)
            if self.scenario_id == "lawful-trader-mvp":
                # Keep the shared persisted clock aligned without advancing the
                # strategic society state in this authored sample.
                self.society.hour = self.npcs.hour
            else:
                self.society.advance(self, previous_npc_events)
        if voyage_status not in ("underway", "storm", "encounter"):
            self.npcs.clear_active_contacts(self.clock.total_ticks)
        if underway:
            self.voyages.step_second(self, sighting)

    def advance(self, ticks):
        if type(ticks) is not int or ticks < 0:
            raise ValueError("Time must advance by a non-negative integer tick count")
        if self.combat.active:
            # Combat drills are discrete turns; needs, jobs, voyages and markets
            # resume only after the encounter is explicitly closed.
            return
        self.voyages.arrival_interrupt = False
        self.ship.advance(self, ticks)
        self.economy.update_day(self.clock.day)
        # Defensive idempotent pass: the per-second ship hook owns normal
        # boundaries, but a loaded or externally advanced clock must not skip pay.
        self.economy.employment.settle_day(self, self.clock.day)
        # Arrival and walking ashore also flow through here, so a delivered
        # undertaking closes as soon as the captain is ashore at the destination.
        self.economy.employment.settle_undertakings(self)
        voyage = self.voyages.voyage
        if (voyage and voyage.get("status") == "arrived"
                and voyage.get("arrival_snapshot", {}).get("ticks") == self.clock.total_ticks):
            # The arrival checkpoint is taken in the per-second voyage hook;
            # finalize it after calendar-boundary contract expiry/refunds run.
            voyage["arrival_snapshot"] = self.voyages._ledger_checkpoint(self)
        self.refresh()

    def refresh(self):
        aboard = 69 <= self.player.x < 92 and 12 <= self.player.y < 44
        refresh_visibility(self, radius=24 if self.ship.light_on and aboard else 18)

    def room_name(self, x, y, z=0):
        if self.at_sea and x < 69:
            visit = (self.voyages.voyage.get("deck_visit")
                     if self.voyages.voyage else None)
            if visit and visit.get("target_id"):
                from pirates.sea_transfer import GANGPLANK, in_deck
                if (x, y) == GANGPLANK:
                    return "Gangplank"
                if in_deck(x, y):
                    encounter = self.voyages.voyage.get("encounter")
                    return f"{encounter.get('name', 'Other vessel')} deck" if encounter else "Other vessel deck"
            return "Open sea"
        if x < 45:
            return PORTS[self.economy.port]["name"]
        if z != 0:
            return f"Deck {z}"
        return self.spaces.name(self, x, y)

    def refresh_fixture_objects(self):
        # Only replace our own equipment inspection records, preserving other objects.
        for at in list(self.objects_at):
            self.objects_at[at] = [o for o in self.objects_at[at] if "fixture_id" not in o]
            if not self.objects_at[at]:
                del self.objects_at[at]
        for fixture in self.fixtures.fixtures:
            if fixture.carrier is not None or fixture.z != self.active_deck:
                continue
            at = self.fixtures.world_position(fixture)
            name = (f"Crew bunk {int(fixture.id.split(':')[1]) + 1}" if fixture.kind == "bunk"
                    else fixture.kind.replace("_", " ").title())
            self.objects_at.setdefault(at[:2], []).append({"name": name, "fixture_id": fixture.id,
                                                      "station": fixture.kind})

    def bind_fixtures(self, layout):
        """Commit a prevalidated equipment transition and update all live adapters."""
        project(self.ship_model, layout.overlay_for(self.active_deck), self.game_map,
                self.active_deck)
        self.fixtures = layout
        self.ship_fixtures = layout.overlay_for(self.active_deck)
        self.ship.layout = layout
        for pile in self.ship.storage.piles.values():
            if pile.fixture_id:
                fixture = layout.get(pile.fixture_id)
                pile.x, pile.y, pile.z = fixture.x, fixture.y, fixture.z
        self.refresh_fixture_objects()
        self.refresh()

    def message(self, text):
        self.log.append(text)
        self.log = self.log[-60:]

    def move(self, dx, dy):
        if (dx, dy) not in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            return False
        x, y = self.player.x + dx, self.player.y + dy
        if ((x - ORIGIN[0], y - ORIGIN[1], self.player.z) in self.locked_doors
                and self.game_map.tile_id(x, y) == T.DOOR):
            self.message("The locked door blocks the way.")
            return False
        if not self.game_map.is_walkable(x, y):
            self.message("The way is blocked. Use Enter near a door to open it.")
            return False
        if any((c.x, c.y, getattr(c, "z", 0)) == (x, y, self.player.z)
               for c in self.all_entities() if c is not self.player):
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
                local = (nx - ORIGIN[0], ny - ORIGIN[1], self.active_deck)
                if local in self.locked_doors:
                    self.message("That door is locked. Release the lock before operating it.")
                    return
                self.game_map.doors[ny, nx] ^= True
                self.advance(TICKS_PER_SECOND)
                self.refresh()
                self.message("Door opened." if self.game_map.doors[ny, nx] else "Door closed.")
                return
        self.message("No door within reach.")

    def set_lockdown(self, enabled):
        """Lock every built ship door and close any clear opening; doors are still breachable."""
        if type(enabled) is not bool:
            raise ValueError("Lockdown state must be boolean")
        doors = {(c.x, c.y, c.z) for c in self.ship_model.components if c.kind == "door"}
        occupied = {(e.x - ORIGIN[0], e.y - ORIGIN[1], getattr(e, "z", 0))
                    for e in self.all_entities()}
        if enabled:
            self.lockdown = True
            self.locked_doors = doors
            for x, y, z in doors - occupied:
                wx, wy = x + ORIGIN[0], y + ORIGIN[1]
                self.door_state(z)[wy, wx] = False
        else:
            self.lockdown = False
            self.locked_doors.clear()
        if self.active_deck == 0:
            self.game_map.doors[self.game_map.tiles != T.DOOR] = False
        self.refresh()
        self.message("Ship lockdown engaged: doors are locked; enemy breaching remains possible."
                      if enabled else "Ship lockdown released; door locks cleared.")

    def toggle_adjacent_door_lock(self):
        x, y = self.player.x, self.player.y
        for dx, dy in ((0, 0), (0, -1), (1, 0), (0, 1), (-1, 0)):
            wx, wy = x + dx, y + dy
            if (self.game_map.in_bounds(wx, wy) and self.game_map.tile_id(wx, wy) == T.DOOR):
                local = (wx - ORIGIN[0], wy - ORIGIN[1], self.active_deck)
                if (wx, wy) == (x, y) or any((e.x, e.y, getattr(e, "z", 0)) ==
                                               (wx, wy, self.active_deck)
                                               for e in self.all_entities() if e is not self.player):
                    self.message("Clear the doorway before changing its lock.")
                    return
                if local in self.locked_doors:
                    self.locked_doors.remove(local)
                    self.message("Door unlocked.")
                else:
                    self.game_map.doors[wy, wx] = False
                    self.locked_doors.add(local)
                    self.message("Door closed and locked.")
                self.advance(TICKS_PER_SECOND)
                self.refresh()
                return
        self.message("No door within reach.")

    def pickup(self):
        at = ((self.player.x, self.player.y) if self.player.z == 0 else
              (self.player.x, self.player.y, self.player.z))
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
        at = ((self.player.x, self.player.y) if self.player.z == 0 else
              (self.player.x, self.player.y, self.player.z))
        self.ground_items.setdefault(at, []).append(item)
        self.advance(TICKS_PER_SECOND)
        self.message(f"Dropped {item['name']}.")

    def save(self, path=None):
        if self.combat.active:
            raise ValueError("Leave the combat arena before saving the ship.")
        data = {"version": 4, "player": [self.player.x, self.player.y], "player_z": self.player.z,
                "ticks": self.clock.total_ticks, "inventory": self.inventory.items,
                "ground": {",".join(map(str, at)): items for at, items in self.ground_items.items()},
                "doors": self.game_map.doors.tolist(),
                "deck_doors": {str(z): state[ORIGIN[1]:ORIGIN[1] + HEIGHT,
                                             ORIGIN[0]:ORIGIN[0] + WIDTH].tolist()
                               for z, state in self.deck_door_states.items() if z != self.active_deck},
                "explored": self.game_map.explored.tolist(), "ship": self.ship.snapshot(),
                "ship_model": self.ship_model.snapshot(), "room_zones": self.spaces.snapshot(),
                "fixtures": self.fixtures.snapshot(), "economy": self.economy.snapshot(self),
                 "blueprint": self.blueprint.snapshot(), "voyage": self.voyages.snapshot(),
                 "npcs": self.npcs.snapshot(), "society": self.society.snapshot(),
                  "generation_manifest": deepcopy(self.generation_manifest),
                    "combat_progression": self.combat.progression_snapshot(),
                    "duel_journal": deepcopy(self.duel_journal),
                    "ship_hazards": self.hazards.snapshot(),
                    "locked_doors": [list(at) for at in sorted(self.locked_doors)],
                    "lockdown": self.lockdown}
        if self.scenario_id is not None:
            data["scenario"] = {"id": self.scenario_id, "version": self.scenario_version}
        path = Path(path or self.save_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary.replace(path)
        self.message(f"Saved {path.name}.")

    @classmethod
    def load(cls, path=SAVE_PATH, *, expected_scenario_id=...):
        # Construct/validate a replacement world; never partially mutate live state.
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or type(data.get("version")) is not int
                or data.get("version") not in (1, 2, 3, 4)):
            raise ValueError("Unsupported save version")
        scenario_data = data.get("scenario")
        scenario_id = None
        scenario_version = None
        if scenario_data is not None:
            if (data["version"] < 4 or not isinstance(scenario_data, dict)
                    or set(scenario_data) != {"id", "version"}
                    or not isinstance(scenario_data.get("id"), str)
                    or type(scenario_data.get("version")) is not int):
                raise ValueError("Invalid scenario save metadata")
            scenario = load_scenario(scenario_data["id"])
            if scenario_data["version"] != scenario["version"]:
                raise ValueError("Unsupported authored scenario version")
            scenario_id, scenario_version = scenario["id"], scenario["version"]
        if expected_scenario_id is not ... and scenario_id != expected_scenario_id:
            raise ValueError("Save belongs to a different scenario or legacy mode")
        if scenario_id == MVP_SCENARIO_ID:
            world = cls.new_authored_mvp()
            world.scenario_version = scenario_version
        else:
            world = cls()
        world.save_path = path
        if "combat_progression" in data:
            world.combat.restore_progression(data["combat_progression"])
        if data.get("duel_journal") is not None:
            from pirates.duel_system.session import DuelSession
            journal = DuelSession.from_data(data["duel_journal"])
            world.duel_journal = journal.to_data()
            if 'coach_enabled' in data['duel_journal']:
                enabled = data['duel_journal']['coach_enabled']
                if type(enabled) is not bool:
                    raise ValueError('Invalid duel coach preference')
                world.duel_journal['coach_enabled'] = enabled
        gm = world.game_map
        if "economy" in data:
            if (not isinstance(data["economy"], dict) or not isinstance(data["economy"].get("port"), str)
                    or data["economy"]["port"] not in PORTS):
                raise ValueError("Invalid economy / island")
            world.economy.port = data["economy"]["port"]
            build_port(world)
        if data["version"] >= 3:
            world.ship_model = ShipModel.restore(data["ship_model"])
            active_deck = data.get("player_z", 0)
            if (type(active_deck) is not int or not 0 <= active_deck <= 8
                    or not any(c.kind == "floor" and c.z == active_deck
                               for c in world.ship_model.components)):
                raise ValueError("Invalid active deck")
            world.active_deck = active_deck
            world.player.z = active_deck
            if "fixtures" in data:
                world.fixtures = FixtureLayout.restore(data["fixtures"], world.fixtures)
                world.ship_fixtures = world.fixtures.overlay_for(active_deck)
                world.ship.layout = world.fixtures
                for fixture in world.fixtures.fixtures:
                    if fixture.carrier is not None:
                        continue
                    parts = world.ship_model.at(fixture.x, fixture.y, fixture.z)
                    if (not any(c.kind == "floor" for c in parts)
                            or any(c.kind in ("wall", "door", "stair") for c in parts)):
                        raise ValueError("Invalid equipment deck geometry")
                world.refresh_fixture_objects()
            project(world.ship_model, world.ship_fixtures, gm, active_deck)
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
        world.deck_door_states = {world.active_deck: gm.doors.copy()}
        saved_deck_doors = data.get("deck_doors", {})
        if not isinstance(saved_deck_doors, dict) or len(saved_deck_doors) > 8:
            raise ValueError("Invalid inactive deck door states")
        for key, rows in saved_deck_doors.items():
            try:
                z = int(key)
            except (TypeError, ValueError) as error:
                raise ValueError("Invalid deck door-state key") from error
            state = np.asarray(rows)
            if (str(z) != key or not 0 <= z <= 8 or z == world.active_deck
                    or state.shape != (HEIGHT, WIDTH) or state.dtype != np.bool_
                    or not any(c.kind == "floor" and c.z == z for c in world.ship_model.components)):
                raise ValueError("Invalid inactive deck door-state grid")
            full = np.zeros(gm.tiles.shape, dtype=np.bool_)
            full[ORIGIN[1]:ORIGIN[1] + HEIGHT, ORIGIN[0]:ORIGIN[0] + WIDTH] = state
            door_cells = {(c.x + ORIGIN[0], c.y + ORIGIN[1]) for c in world.ship_model.components
                          if c.z == z and c.kind == "door"}
            for cy, cx in zip(*np.nonzero(full)):
                if (cx, cy) not in door_cells:
                    full[cy, cx] = False
            world.deck_door_states[z] = full
        locked_rows = data.get("locked_doors", [])
        if not isinstance(locked_rows, list) or len(locked_rows) > 1000:
            raise ValueError("Invalid locked-door registry")
        door_cells = {(c.x, c.y, c.z) for c in world.ship_model.components if c.kind == "door"}
        for row in locked_rows:
            if (not isinstance(row, list) or len(row) != 3
                    or any(type(v) is not int for v in row) or tuple(row) not in door_cells
                    or tuple(row) in world.locked_doors):
                raise ValueError("Invalid locked-door location")
            world.locked_doors.add(tuple(row))
        lockdown = data.get("lockdown", False)
        if type(lockdown) is not bool:
            raise ValueError("Invalid ship lockdown flag")
        world.lockdown = lockdown
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
        saved_transfer = None
        voyage_snapshot = data.get("voyage")
        if isinstance(voyage_snapshot, dict):
            saved_voyage = voyage_snapshot.get("voyage")
            if isinstance(saved_voyage, dict):
                saved_transfer = saved_voyage.get("deck_visit")
        from pirates.sea_transfer import in_deck
        visiting_captain = (
            isinstance(saved_transfer, dict)
            and isinstance(saved_transfer.get("participants"), list)
            and world.player.id in saved_transfer["participants"]
            and in_deck(x, y))
        if not gm.is_walkable(x, y) and not visiting_captain:
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
            try:
                coords = tuple(map(int, key.split(",")))
            except (AttributeError, TypeError, ValueError) as error:
                raise ValueError("Invalid ground coordinate") from error
            if len(coords) not in (2, 3):
                raise ValueError("Invalid ground coordinate")
            gx, gy = coords[:2]
            gz = coords[2] if len(coords) == 3 else 0
            if not 0 <= gz <= 8:
                raise ValueError("Invalid ground deck")
            if gz == 0:
                gx, gy = migrate_old_deck_position(gx, gy)
            if ORIGIN[0] <= gx < ORIGIN[0] + WIDTH and ORIGIN[1] <= gy < ORIGIN[1] + HEIGHT:
                lx, ly = gx - ORIGIN[0], gy - ORIGIN[1]
                parts = world.ship_model.at(lx, ly, gz)
                walkable = (any(c.kind == "floor" for c in parts)
                            and not any(c.kind in ("wall", "door") for c in parts)
                            and not any(c.kind == "stair" and c.z in (gz, gz - 1) and c.z >= 0
                                        for c in world.ship_model.components
                                        if (c.x, c.y) == (lx, ly))
                            and (lx, ly) not in world.fixtures.overlay_for(gz))
            else:
                walkable = gz == 0 and gm.is_walkable(gx, gy)
            if not walkable:
                raise ValueError("Invalid ground location")
            validate_items(items)
            at = (gx, gy) if gz == 0 else (gx, gy, gz)
            ground.setdefault(at, []).extend(items)
        world.ground_items = ground
        world.player.x, world.player.y = x, y
        world.clock = GameClock(ticks)
        if data["version"] >= 2:
            world._restore_deck_visit = saved_transfer
            try:
                world.ship = ShipLife.restore(world, data["ship"])
            except (KeyError, TypeError, AttributeError, OverflowError) as error:
                raise ValueError(f"Invalid ship save: {error}") from error
            finally:
                del world._restore_deck_visit
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
            if "ship_hazards" in data:
                world.hazards = ShipHazards.restore(data["ship_hazards"], world.ship_model)
            else:
                world.hazards = ShipHazards()
                world.hazards.seed_legacy_bilge(world)
        except (KeyError, TypeError, AttributeError, OverflowError) as error:
            raise ValueError(f"Invalid ship hazard save: {error}") from error
        try:
            world.economy = Economy.restore(data.get("economy"), world)
        except (KeyError, TypeError, AttributeError, OverflowError) as error:
            raise ValueError(f"Invalid economy save: {error}") from error
        world.voyages = VoyageSystem.restore(data.get("voyage"), world)
        generation_manifest = data.get("generation_manifest")
        if generation_manifest is not None:
            from pirates.npc_generation import validate_manifest
            try:
                generation_manifest = validate_manifest(generation_manifest)
            except (KeyError, TypeError, AttributeError, OverflowError) as error:
                raise ValueError(f"Invalid NPC generation manifest: {error}") from error
        world.generation_manifest = deepcopy(generation_manifest)
        try:
            world.npcs = NpcFleetSystem.restore(
                data.get("npcs"), generation_manifest, scenario_id=scenario_id,
                current_tick=world.clock.total_ticks)
        except (KeyError, TypeError, AttributeError, OverflowError) as error:
            raise ValueError(f"Invalid NPC fleet save: {error}") from error
        sighting = (world.voyages.voyage or {}).get("encounter")
        if sighting and "combat" in sighting and not any(
                ship["id"] == sighting["id"] for ship in world.npcs.ships):
            raise ValueError("Saved naval combat target is not in the NPC fleet")
        if data.get("society") is None:
            # Pre-society v3 saves have no shared strategic clock. Seed it from
            # the loaded calendar so migration does not replay an arbitrary gap.
            world.npcs.hour = world.clock.total_ticks // 360000
        try:
            world.society = NpcSocietySystem.restore(data.get("society"), world.npcs,
                                                      world.clock.total_ticks // 360000,
                                                      generation_manifest)
        except (KeyError, TypeError, AttributeError, OverflowError) as error:
            raise ValueError(f"Invalid NPC society save: {error}") from error
        if world.society.hour != world.npcs.hour:
            raise ValueError("NPC fleet and society clocks do not match")
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
        if world.voyages.voyage and world.voyages.voyage["status"] in ("underway", "encounter", "storm"):
            world.enter_sea(preserve_harbor=True)
        world.refresh()
        world.message("Captain's save restored.")
        return world
