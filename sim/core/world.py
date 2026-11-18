"""Versioned, JSON-serializable root state for headless simulation runs."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any

from sim.config import load_config, validate_config
from sim.core.clock import EventScheduler, SimClock
from sim.core.events import EventLog
from sim.core.ids import IdAllocator
from sim.core.rng import RandomStreams
from sim.systems.geography import WorldMap
from sim.systems.economy import EconomySystem
from sim.systems.characters import CharacterSystem
from sim.systems.crews_ships import CrewShipSystem
from sim.systems.decisions import DecisionSystem
from sim.systems.encounters import EncounterSystem
from sim.systems.memory import MemorySystem
from sim.systems.politics import PoliticsSystem
from sim.systems.travel import TravelSystem
from sim.systems.weather import WeatherSystem
from sim.systems.environment import EnvironmentSystem
from sim.systems.director import DirectorSystem
from sim.systems.battles import BattleSystem
from sim.systems.lod import LODSystem


SNAPSHOT_SCHEMA_VERSION = 17


class WorldState:
    """All deterministic M0 state, with entity tables keyed by stable IDs."""

    def __init__(self, *, seed: int | str, config: dict[str, Any], clock: SimClock,
                 scheduler: EventScheduler, rng: RandomStreams, ids: IdAllocator,
                 event_log: EventLog, entities: dict[str, dict[str, Any]] | None = None,
                 archive: dict[str, dict[str, Any]] | None = None,
                 geography: WorldMap | None = None,
                  weather: WeatherSystem | None = None,
                  travel: TravelSystem | None = None,
                  economy: EconomySystem | None = None,
                  characters: CharacterSystem | None = None,
                  crew_ships: CrewShipSystem | None = None,
                   decisions: DecisionSystem | None = None,
                   encounters: EncounterSystem | None = None,
                     memories: MemorySystem | None = None,
                       politics: PoliticsSystem | None = None,
                       environment: EnvironmentSystem | None = None,
                       director: DirectorSystem | None = None,
                        battles: BattleSystem | None = None,
                         lod: LODSystem | None = None) -> None:
        self.seed = seed
        self.config = config
        self.clock = clock
        self.scheduler = scheduler
        self.rng = rng
        self.ids = ids
        self.event_log = event_log
        self.entities = entities or {}
        self.archive = archive or {}
        self.geography = geography or WorldMap.load()
        self.weather = weather or WeatherSystem(
            self.geography, rng, days_per_year=config["time"]["days_per_year"])
        self.travel = travel or TravelSystem(self.geography, self.weather)
        self.economy = economy or EconomySystem.create(self.geography)
        self.characters = characters or CharacterSystem.create()
        self.characters.bind_islands(set(self.geography.islands), set(self.geography.routes))
        self.crew_ships = crew_ships or CrewShipSystem.create()
        self.decisions = decisions or DecisionSystem.create()
        self.encounters = encounters or EncounterSystem.create()
        self.memories = memories or MemorySystem.create()
        self.politics = politics or PoliticsSystem.create()
        self.environment = environment or EnvironmentSystem.create(self.geography, self.rng)
        self.director = director or DirectorSystem.create(config)
        self.battles = battles or BattleSystem.create()
        self.lod = lod or LODSystem.create()
        # Transient observability counters; intentionally excluded from snapshots.
        self.system_diagnostics: dict[str, dict[str, Any]] = {}
        self.lifecycle_diagnostics: list[dict[str, Any]] = []
        self._lifecycle_diagnostic_keys: set[tuple[str, str, str]] = set()

    def _record_system_call(self, name: str, tick: int) -> None:
        stats = self.system_diagnostics.setdefault(
            name, {"call_count": 0, "last_fired_tick": None, "call_ticks": []})
        stats["call_count"] = int(stats["call_count"] or 0) + 1
        stats["last_fired_tick"] = tick
        stats["call_ticks"].append(tick)

    def _record_lifecycle_diagnostic(self, code: str, entity_id: str,
                                     event_id: str, reason: str) -> None:
        key = (code, entity_id, event_id)
        if key in self._lifecycle_diagnostic_keys:
            return
        self._lifecycle_diagnostic_keys.add(key)
        self.lifecycle_diagnostics.append({
            "code": code, "tick": self.clock.tick, "entity_id": entity_id,
            "event_id": event_id, "reason": reason,
        })

    @classmethod
    def create(cls, seed: int | str, config: dict[str, Any] | None = None, *,
               director_enabled: bool | None = None) -> "WorldState":
        if type(seed) not in (int, str):
            raise ValueError("World seed must be an integer or string")
        config = load_config() if config is None else validate_config(config)
        if director_enabled is not None:
            if type(director_enabled) is not bool:
                raise ValueError("director_enabled must be boolean")
            config.setdefault("director", {
                "enabled": False, "intensity_permille": 0, "check_interval_days": 30,
                "conflict_drought_days": 180, "injection_cooldown_days": 180,
                "max_injections_per_year": 2, "minimum_major_characters": 4,
            })["enabled"] = director_enabled
            roots = config["event_rules"]["root_event_types"]
            if director_enabled and "DIRECTOR_INJECTION" not in roots:
                roots.append("DIRECTOR_INJECTION")
        root_types = config.get("event_rules", {}).get("root_event_types", [])
        rng = RandomStreams(seed)
        geography = WorldMap.load()
        world = cls(seed=seed, config=config, clock=SimClock(),
                    scheduler=EventScheduler(), rng=rng, ids=IdAllocator(),
                    event_log=EventLog(
                        root_types, config["event_rules"].get("class_by_type"),
                        config["time"]["hours_per_day"] // config["time"]["hours_per_tick"]),
                    geography=geography,
                    weather=WeatherSystem(
                        geography, rng, days_per_year=config["time"]["days_per_year"]))
        world.event_log.append(tick=0, event_type="WORLD_GENERATED",
                               data={"seed": seed, "map_digest": geography.digest},
                               causes_tags=["world_generation"])
        world.economy = EconomySystem.create(geography)
        world.economy.emit_initial_events(world)
        for merchant in world.economy.economy_data.get("initial_merchants", []):
            world.create_merchant(
                merchant["name"], merchant["island_id"],
                silver=merchant["silver"],
                capacity_milli=merchant["capacity_milli"],
                travel_cost_silver_per_day=merchant["travel_cost_silver_per_day"],
                risk_cost_basis_points=merchant["risk_cost_basis_points"],
                risk_beliefs_ppm={route_id: merchant["risk_belief_ppm"]
                                  for route_id in geography.routes},
            )
        world.characters.seed_world(world)
        world.crew_ships.seed_world(world)
        world.politics.seed_world(world)
        world.battles.seed_world(world)
        world.environment.seed_world(world)
        world.decisions.set_event_cursor(len(world.event_log))
        world.lod.seed_world(world)
        return world

    def schedule_event(self, tick: int, event_type: str, *, priority: int = 0,
                       location: str | None = None,
                       actors: list[dict[str, str]] | None = None,
                       targets: list[dict[str, str]] | None = None,
                       data: dict[str, Any] | None = None,
                       caused_by: list[str] | None = None,
                       causes_tags: list[str] | None = None,
                       magnitude: int | float = 0.0) -> None:
        if type(tick) is not int or tick < self.clock.tick:
            raise ValueError("Cannot schedule an event before the current clock tick")
        payload = {
            "location": location,
            "actors": actors or [],
            "targets": targets or [],
            "data": data or {},
            "caused_by": caused_by or [],
            "causes_tags": causes_tags or [],
            "magnitude": magnitude,
        }
        self.scheduler.schedule(tick, event_type, payload, priority)

    def create_vessel(self, name: str, island_id: str, **attributes: Any) -> tuple[str, str]:
        return self.travel.create_vessel(self, name, island_id, **attributes)

    def start_voyage(self, ship_id: str, destination: str, *,
                     caused_by: str | list[str] | tuple[str, ...]) -> str:
        return self.travel.start_voyage(self, ship_id, destination, caused_by=caused_by)

    def create_merchant(self, name: str, island_id: str, **attributes: Any) -> tuple[str, str]:
        return self.economy.create_merchant(self, name, island_id, **attributes)

    def trade(self, merchant_id: str, island_id: str, goods_id: str, side: str,
              quantity_milli: int, *, caused_by: str) -> dict[str, Any]:
        return self.economy.trade(self, merchant_id, island_id, goods_id, side,
                                  quantity_milli, caused_by=caused_by)

    def location_of(self, entity_id: str, tick: int | None = None) -> dict[str, Any]:
        if entity_id in self.travel.transits or (
                entity_id in self.entities and self.entities[entity_id].get("kind") == "ship"):
            return self.travel.location_at(self, entity_id, tick)
        entity = self.entities.get(entity_id) or self.archive.get(entity_id)
        if entity is None:
            raise ValueError(f"Unknown entity: {entity_id}")
        location = entity.get("location")
        if not isinstance(location, dict):
            raise ValueError(f"Entity has no stored location: {entity_id}")
        if (entity.get("kind") == "character" and entity.get("ship_id") is not None
                and entity["ship_id"] in self.entities):
            return self.location_of(entity["ship_id"], tick)
        return dict(location)

    def validate_locations(self) -> None:
        self.travel.validate_state(self)
        for entity_id, entity in sorted(self.entities.items()):
            if not isinstance(entity, dict) or entity.get("id") != entity_id:
                raise ValueError(f"Entity identity mismatch: {entity_id}")
            if entity_id in self.travel.transits or (
                    entity.get("kind") == "character" and entity.get("ship_id") is not None):
                location = self.location_of(entity_id)
            else:
                location = entity.get("location")
            if not isinstance(location, dict):
                raise ValueError(f"Entity location is missing: {entity_id}")
            kind = location.get("kind")
            if kind == "island":
                if location.get("id") not in self.geography.islands:
                    raise ValueError(f"Unknown island location for {entity_id}")
            elif kind == "route":
                if (location.get("id") not in self.geography.routes
                        or location.get("from") not in self.geography.islands
                        or location.get("to") not in self.geography.islands
                        or type(location.get("progress_permille")) is not int
                        or not 0 <= location["progress_permille"] < 1000
                        or self.geography.other_end(location["id"], location["from"])
                        != location["to"]):
                    raise ValueError(f"Invalid route location for {entity_id}")
            else:
                raise ValueError(f"Unknown location kind for {entity_id}: {kind}")
        for entity_id, entity in sorted(self.archive.items()):
            if not isinstance(entity, dict) or entity.get("id") != entity_id:
                raise ValueError(f"Archived entity identity mismatch: {entity_id}")
            location = entity.get("location")
            if location is not None:
                if (not isinstance(location, dict)
                        or location.get("kind") not in ("island", "route")):
                    raise ValueError(f"Archived entity has invalid location: {entity_id}")
                if location["kind"] == "island" and location.get("id") not in self.geography.islands:
                    raise ValueError(f"Archived entity has invalid island: {entity_id}")
                if location["kind"] == "route":
                    if (location.get("id") not in self.geography.routes
                            or location.get("from") not in self.geography.islands
                            or location.get("to") not in self.geography.islands
                            or type(location.get("progress_permille")) is not int
                            or not 0 <= location["progress_permille"] < 1000
                            or self.geography.other_end(location["id"], location["from"])
                            != location["to"]):
                        raise ValueError(f"Archived entity has invalid route: {entity_id}")

    def run_until(self, tick: int) -> None:
        if type(tick) is not int or tick < self.clock.tick:
            raise ValueError("World cannot run backward")
        ticks_per_day = (
            self.config["time"]["hours_per_day"]
            // self.config["time"]["hours_per_tick"]
        )
        while True:
            next_event = self.scheduler.peek()
            next_weather_tick = (self.weather.last_updated_day + 1) * ticks_per_day
            candidates = [candidate for candidate in
                          (next_weather_tick, next_event.tick if next_event else None)
                          if candidate is not None and candidate <= tick]
            if not candidates:
                break
            due_tick = min(candidates)
            self.clock.advance_to(due_tick)
            if due_tick == next_weather_tick:
                day = self.weather.last_updated_day + 1
                weather_transitions = self.weather.update_day(day)
                for transition in weather_transitions:
                    self.event_log.append(
                        tick=due_tick,
                        event_type="NATURAL_EVENT",
                        location=None,
                        data={"kind": "WEATHER_CHANGED", "day": day,
                              "calendar_season": self.weather.calendar_season(day), **transition},
                        causes_tags=["seasonal_weather", f"region:{transition['region_id']}"],
                        magnitude=0.2,
                    )
                self.environment.update_day(day, self, weather_transitions)
                self._record_system_call("weather_environment", due_tick)
                self.crew_ships.update_day(day, self)
                self._record_system_call("crew_ships", due_tick)
                self.economy.update_day(day, self)
                self._record_system_call("economy", due_tick)
                self.characters.update_day(day, self)
                self._record_system_call("characters", due_tick)
                self.decisions.update_day(day, self)
                self._record_system_call("ai", due_tick)
            for scheduled in self.scheduler.pop_due(due_tick):
                payload = scheduled.payload
                event = self.event_log.append(
                    tick=scheduled.tick,
                    event_type=scheduled.event_type,
                    location=payload["location"],
                    actors=payload["actors"],
                    targets=payload["targets"],
                    data=payload["data"],
                    caused_by=payload["caused_by"],
                    causes_tags=payload["causes_tags"],
                    magnitude=payload["magnitude"],
                )
                self.travel.handle_scheduled_event(self, scheduled, event)
                self.economy.handle_scheduled_event(self, scheduled, event)
            if due_tick == next_weather_tick:
                self.encounters.update_day(day, self)
                self._record_system_call("encounters", due_tick)
                self.memories.update_day(day, self)
                self._record_system_call("memory", due_tick)
                self.politics.update_day(day, self)
                self._record_system_call("factions", due_tick)
                self._record_system_call("heat", due_tick)
                self.battles.update_day(day, self)
                self._record_system_call("battles", due_tick)
                self.director.update_day(day, self)
                self._record_system_call("director", due_tick)
                self.crew_ships.update_actor_budgets(day, self)
                self.lod.update_day(day, self)
                self._record_system_call("lod", due_tick)
            self.decisions.process_event_wakes(self)
            if due_tick == next_weather_tick:
                self.decisions.process_scheduled_agents(self)
        self.clock.advance_to(tick)

    def to_snapshot(self) -> dict[str, Any]:
        self.event_log.validate_causality()
        self.validate_locations()
        self.economy.validate_world(self)
        self.characters.validate_world(self)
        self.crew_ships.validate_world(self)
        self.decisions.validate_world(self)
        self.encounters.validate_world(self)
        self.memories.validate_world(self)
        self.politics.validate_world(self)
        self.environment.validate_world(self)
        self.director.validate_world(self)
        self.battles.validate_world(self)
        self.lod.validate_world(self)
        return {
            "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
            "seed": self.seed,
            "config": self.config,
            "clock": self.clock.snapshot(),
            "scheduler": self.scheduler.snapshot(),
            "rng_counters": self.rng.snapshot(),
            "id_counters": self.ids.snapshot(),
            "entities": self.entities,
            "archive": self.archive,
            "map_digest": self.geography.digest,
            "weather_config_digest": self.weather.digest,
            "weather": self.weather.snapshot(),
            "travel": self.travel.snapshot(),
            "economy": self.economy.snapshot(),
            "characters": self.characters.snapshot(),
            "crew_ships": self.crew_ships.snapshot(),
            "decisions": self.decisions.snapshot(),
            "encounters": self.encounters.snapshot(),
            "memories": self.memories.snapshot(),
            "politics": self.politics.snapshot(),
            "environment": self.environment.snapshot(),
            "director": self.director.snapshot(),
            "battles": self.battles.snapshot(),
            "lod": self.lod.snapshot(),
            "root_event_types": sorted(self.event_log.root_event_types),
            "events": self.event_log.to_records(),
        }

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any]) -> "WorldState":
        required = {
            "snapshot_schema_version", "seed", "config", "clock", "scheduler",
            "rng_counters", "id_counters", "entities", "archive",
            "map_digest", "weather_config_digest", "weather", "travel",
            "economy", "characters", "crew_ships", "decisions", "encounters", "memories",
            "politics",
            "environment",
            "director",
            "battles",
            "lod",
            "root_event_types", "events",
        }
        if not isinstance(snapshot, dict) or set(snapshot) != required:
            raise ValueError("Invalid world snapshot fields")
        schema_version = snapshot["snapshot_schema_version"]
        if schema_version not in {SNAPSHOT_SCHEMA_VERSION - 1, SNAPSHOT_SCHEMA_VERSION}:
            raise ValueError("Unsupported world snapshot schema version")
        if schema_version == SNAPSHOT_SCHEMA_VERSION - 1:
            # Schema 17 adds event.class without changing simulation state.
            # Upgrade schema-16 event rows in memory so existing saves resume.
            snapshot = dict(snapshot)
            snapshot["snapshot_schema_version"] = SNAPSHOT_SCHEMA_VERSION
            snapshot["config"] = json.loads(json.dumps(snapshot["config"]))
            defaults = load_config()["event_rules"].get("class_by_type", {})
            rules = snapshot["config"].setdefault("event_rules", {})
            classes = rules.setdefault("class_by_type", defaults)

            def migrate_event_record(record):
                if not isinstance(record, dict) or "class" in record:
                    return record
                data = record.get("data")
                data = data if isinstance(data, dict) else {}
                event_class = classes.get(record.get("type"), "narrative")
                if (record.get("type") == "NATURAL_EVENT"
                        and data.get("kind") == "WEATHER_CHANGED"):
                    event_class = "routine"
                elif (record.get("type") == "AGENT_ACTION"
                      and data.get("action_id") == "hunt_rival"):
                    event_class = "narrative"
                return {**record, "class": event_class}

            snapshot["events"] = [migrate_event_record(record)
                                  for record in snapshot["events"]]
        seed = snapshot["seed"]
        if type(seed) not in (int, str):
            raise ValueError("Invalid world snapshot seed")
        config = validate_config(snapshot["config"])
        event_rules = config.get("event_rules")
        roots = event_rules.get("root_event_types") if isinstance(event_rules, dict) else None
        root_types = snapshot["root_event_types"]
        if (not isinstance(roots, list) or not isinstance(root_types, list)
                or sorted(set(roots)) != root_types):
            raise ValueError("Invalid world snapshot root event types")
        entities, archive = snapshot["entities"], snapshot["archive"]
        if not isinstance(entities, dict) or not isinstance(archive, dict):
            raise ValueError("Invalid world entity tables")
        try:
            detached_entities = json.loads(json.dumps(
                {"entities": entities, "archive": archive}, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise ValueError("World entity tables must be JSON-compatible") from exc
        entities = detached_entities["entities"]
        archive = detached_entities["archive"]
        rng = RandomStreams.restore(seed, snapshot["rng_counters"])
        geography = WorldMap.load()
        if snapshot["map_digest"] != geography.digest:
            raise ValueError("World map content changed since snapshot creation")
        weather = WeatherSystem(
            geography, rng, days_per_year=config["time"]["days_per_year"],
            **snapshot["weather"])
        if snapshot["weather_config_digest"] != weather.digest:
            raise ValueError("Weather config changed since snapshot creation")
        if (not isinstance(snapshot["travel"], dict)
                or set(snapshot["travel"]) != {"transits", "closed_routes"}):
            raise ValueError("Invalid transit snapshot")
        travel = TravelSystem(geography, weather, snapshot["travel"]["transits"],
                              snapshot["travel"]["closed_routes"])
        economy = EconomySystem.restore(geography, snapshot["economy"])
        characters = CharacterSystem.restore(snapshot["characters"])
        crew_ships = CrewShipSystem.restore(snapshot["crew_ships"])
        decisions = DecisionSystem.restore(snapshot["decisions"])
        encounters = EncounterSystem.restore(snapshot["encounters"])
        memories = MemorySystem.restore(snapshot["memories"])
        politics = PoliticsSystem.restore(snapshot["politics"])
        environment = EnvironmentSystem.restore(geography, rng, snapshot["environment"])
        director = DirectorSystem.restore(config, snapshot["director"])
        battles = BattleSystem.restore(snapshot["battles"])
        lod = LODSystem.restore(snapshot["lod"])
        world = cls(
            seed=seed,
            config=config,
            clock=SimClock.restore(snapshot["clock"]),
            scheduler=EventScheduler.restore(snapshot["scheduler"]),
            rng=rng,
            ids=IdAllocator.restore(snapshot["id_counters"]),
            event_log=EventLog.from_records(
                root_types, snapshot["events"], event_rules.get("class_by_type"),
                config["time"]["hours_per_day"] // config["time"]["hours_per_tick"]),
            entities=entities,
            archive=archive,
            geography=geography,
            weather=weather,
            travel=travel,
            economy=economy,
            characters=characters,
            crew_ships=crew_ships,
            decisions=decisions,
            encounters=encounters,
            memories=memories,
            politics=politics,
            environment=environment,
            director=director,
            battles=battles,
            lod=lod,
        )
        ticks_per_day = config["time"]["hours_per_day"] // config["time"]["hours_per_tick"]
        if world.weather.last_updated_day != world.clock.tick // ticks_per_day:
            raise ValueError("Weather snapshot day does not match world clock")
        if world.economy.last_updated_day != world.clock.tick // ticks_per_day:
            raise ValueError("Economy snapshot day does not match world clock")
        if world.characters.last_updated_day != world.clock.tick // ticks_per_day:
            raise ValueError("Character lifecycle snapshot day does not match world clock")
        if world.memories.last_updated_day != world.clock.tick // ticks_per_day:
            raise ValueError("Memory snapshot day does not match world clock")
        if world.politics.last_updated_day != world.clock.tick // ticks_per_day:
            raise ValueError("Politics snapshot day does not match world clock")
        if world.environment.last_updated_day != world.clock.tick // ticks_per_day:
            raise ValueError("Environment snapshot day does not match world clock")
        if world.director.last_checked_day != world.clock.tick // ticks_per_day:
            raise ValueError("Director snapshot day does not match world clock")
        if world.lod.last_updated_day != world.clock.tick // ticks_per_day:
            raise ValueError("LOD snapshot day does not match world clock")
        if world.battles.last_updated_day != world.clock.tick // ticks_per_day:
            raise ValueError("Battle snapshot day does not match world clock")
        if world.crew_ships.last_updated_day != world.clock.tick // ticks_per_day:
            raise ValueError("Crew/ship lifecycle snapshot day does not match world clock")
        if world.decisions.last_updated_day != world.clock.tick // ticks_per_day:
            raise ValueError("Decision/belief snapshot day does not match world clock")
        world.economy.validate_state()
        world.economy.validate_world(world)
        world.characters.validate_world(world)
        world.crew_ships.validate_world(world)
        world.decisions.validate_world(world)
        world.encounters.validate_world(world)
        world.memories.validate_world(world)
        world.politics.validate_world(world)
        world.environment.validate_world(world)
        world.director.validate_world(world)
        world.battles.validate_world(world)
        for island_id, island_state in world.economy.islands.items():
            try:
                event = world.event_log.get(island_state["last_economy_event_id"])
            except KeyError as exc:
                raise ValueError(f"Island {island_id} references an unknown economy event") from exc
            if event.type not in {
                    "WORLD_GENERATED", "ISLAND_ECONOMY_INITIALIZED",
                    "ISLAND_ECONOMY_UPDATED", "MARKET_TRADE", "SHIP_SUPPLIED"}:
                raise ValueError(f"Island {island_id} references a non-economy event")
        world.validate_locations()
        if any(event.tick > world.clock.tick for event in world.event_log):
            raise ValueError("World snapshot contains an event after the current clock")
        if any(event.tick < world.clock.tick for event in world.scheduler.events()):
            raise ValueError("World snapshot contains a scheduled event before the current clock")
        return world

    def save(self, path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(self.to_snapshot(), sort_keys=True, ensure_ascii=False,
                                separators=(",", ":"), allow_nan=False) + "\n"
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent,
            text=True,
        )
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, destination)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
        return destination

    @classmethod
    def load(cls, path: str | Path) -> "WorldState":
        try:
            snapshot = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot load world snapshot {path}: {exc}") from exc
        return cls.from_snapshot(snapshot)
