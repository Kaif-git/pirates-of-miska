"""Validated island/route graph and deterministic shortest-path queries."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import heapq
import json
from pathlib import Path
from typing import Any, Callable


DEFAULT_MAP_PATH = Path(__file__).resolve().parents[1] / "data" / "islands.json"


@dataclass(frozen=True)
class Region:
    id: str
    name: str
    climate: str
    season: str
    initial_weather: str
    danger_level: float
    dangerous_route_only: bool


@dataclass(frozen=True)
class Island:
    id: str
    name: str
    coords: tuple[int, int]
    region_id: str
    climate: str
    size: str


@dataclass(frozen=True)
class Route:
    id: str
    origin: str
    destination: str
    distance_days: int
    hazards: tuple[str, ...]
    patrol_density: float
    season_modifiers: tuple[tuple[str, int], ...]
    monster_risk: float
    chokepoint: bool


class WorldMap:
    def __init__(self, *, regions: dict[str, Region], islands: dict[str, Island],
                 routes: dict[str, Route], hazards: dict[str, dict[str, Any]],
                 source_data: dict[str, Any]) -> None:
        self.regions = regions
        self.islands = islands
        self.routes = routes
        self.hazards = hazards
        self.source_data = source_data
        self._path_cache: dict[tuple[str, str, frozenset[str]], tuple[Route, ...]] = {}
        adjacency: dict[str, list[Route]] = {island_id: [] for island_id in islands}
        for route in routes.values():
            adjacency[route.origin].append(route)
            adjacency[route.destination].append(route)
        self.adjacency = {
            island_id: tuple(sorted(edges, key=lambda route: route.id))
            for island_id, edges in adjacency.items()
        }
        self.digest = hashlib.sha256(
            json.dumps(source_data, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest()

    @classmethod
    def load(cls, path: str | Path = DEFAULT_MAP_PATH) -> "WorldMap":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot load world map {path}: {exc}") from exc
        return cls.from_data(data)

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> "WorldMap":
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("Unsupported world map schema")
        regions: dict[str, Region] = {}
        for row in data.get("regions", []):
            required = {"id", "name", "climate", "season", "initial_weather", "danger_level"}
            if not isinstance(row, dict) or not required.issubset(row):
                raise ValueError("Invalid region data")
            region_id = row["id"]
            if not isinstance(region_id, str) or not region_id or region_id in regions:
                raise ValueError("Region IDs must be unique nonempty strings")
            danger_level = row["danger_level"]
            if (type(danger_level) not in (int, float)
                    or not 0 <= danger_level <= 1):
                raise ValueError(f"Invalid danger level for region {region_id}")
            regions[region_id] = Region(
                id=region_id, name=row["name"], climate=row["climate"],
                season=row["season"], initial_weather=row["initial_weather"],
                danger_level=float(danger_level),
                dangerous_route_only=row.get("dangerous_route_only", False),
            )
        islands: dict[str, Island] = {}
        for row in data.get("islands", []):
            if not isinstance(row, dict):
                raise ValueError("Invalid island data")
            island_id, region_id = row.get("id"), row.get("region_id")
            coords = row.get("coords")
            if (not isinstance(island_id, str) or not island_id or island_id in islands
                    or region_id not in regions or not isinstance(coords, list) or len(coords) != 2
                    or any(type(value) is not int for value in coords)):
                raise ValueError("Invalid island ID, region, or coordinates")
            islands[island_id] = Island(
                id=island_id, name=row["name"], coords=(coords[0], coords[1]),
                region_id=region_id, climate=row["climate"], size=row["size"],
            )
        hazard_rows = data.get("hazards", [])
        hazards = {}
        for row in hazard_rows:
            if not isinstance(row, dict):
                raise ValueError("Invalid hazard data")
            hazard_id = row.get("id")
            try:
                probability = Decimal(str(row.get("trigger_probability")))
            except InvalidOperation as exc:
                raise ValueError("Invalid hazard trigger probability") from exc
            avoidance, effect = row.get("avoidance"), row.get("effect")
            if (not isinstance(hazard_id, str) or not hazard_id or hazard_id in hazards
                    or not probability.is_finite() or not 0 <= probability <= 1
                    or not isinstance(avoidance, dict)
                    or not isinstance(avoidance.get("skill"), str)
                    or type(avoidance.get("difficulty")) is not int
                    or not 0 <= avoidance["difficulty"] <= 100
                    or not isinstance(effect, dict)):
                raise ValueError("Invalid hazard definition")
            for field, value in effect.items():
                if field.endswith("_hours") or field.endswith("_points"):
                    if type(value) is not int or value < 0:
                        raise ValueError(f"Invalid hazard effect: {hazard_id}/{field}")
                elif field.endswith("_chance") or field.endswith("_risk"):
                    try:
                        chance = Decimal(str(value))
                    except InvalidOperation as exc:
                        raise ValueError(f"Invalid hazard effect: {hazard_id}/{field}") from exc
                    if not chance.is_finite() or not 0 <= chance <= 1:
                        raise ValueError(f"Invalid hazard effect: {hazard_id}/{field}")
            hazards[hazard_id] = row
        routes: dict[str, Route] = {}
        for row in data.get("routes", []):
            if not isinstance(row, dict):
                raise ValueError("Invalid route data")
            route_id = row.get("id")
            origin, destination = row.get("from"), row.get("to")
            hazard_ids = row.get("hazards")
            distance = row.get("distance_days_at_base_speed")
            patrol, monster = row.get("patrol_density"), row.get("monster_risk")
            if (not isinstance(route_id, str) or not route_id or route_id in routes
                    or origin not in islands or destination not in islands or origin == destination
                    or type(distance) is not int or distance <= 0
                    or not isinstance(hazard_ids, list)
                    or any(hazard_id not in hazards for hazard_id in hazard_ids)
                    or type(patrol) not in (int, float) or not 0 <= patrol <= 1
                    or type(monster) not in (int, float) or not 0 <= monster <= 1
                    or not isinstance(row.get("season_modifiers"), dict)):
                raise ValueError(f"Invalid route: {route_id}")
            modifiers = row["season_modifiers"]
            permille_modifiers = []
            try:
                for key, value in modifiers.items():
                    modifier = Decimal(str(value))
                    if not isinstance(key, str) or not modifier.is_finite() or modifier <= 0:
                        raise ValueError
                    scaled = modifier * 1000
                    if scaled != scaled.to_integral_value():
                        raise ValueError
                    permille_modifiers.append((key, int(scaled)))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"Invalid seasonal route modifier: {route_id}") from exc
            routes[route_id] = Route(
                id=route_id, origin=origin, destination=destination,
                distance_days=distance, hazards=tuple(hazard_ids),
                patrol_density=float(patrol),
                season_modifiers=tuple(sorted(permille_modifiers)),
                monster_risk=float(monster),
                chokepoint=row.get("chokepoint_flag") is True,
            )
        if not regions or not islands or not routes:
            raise ValueError("World map requires regions, islands, and routes")
        return cls(regions=regions, islands=islands, routes=routes,
                   hazards=hazards, source_data=data)

    def routes_between(self, island_id: str) -> tuple[Route, ...]:
        try:
            return self.adjacency[island_id]
        except KeyError as exc:
            raise ValueError(f"Unknown island: {island_id}") from exc

    def other_end(self, route_id: str, island_id: str) -> str:
        route = self.routes[route_id]
        if route.origin == island_id:
            return route.destination
        if route.destination == island_id:
            return route.origin
        raise ValueError(f"Island {island_id} is not on route {route_id}")

    def shortest_path(self, origin: str, destination: str,
                      edge_cost: Callable[[Route], int] | None = None,
                      blocked_routes: set[str] | frozenset[str] = frozenset()) -> tuple[Route, ...]:
        if origin not in self.islands or destination not in self.islands:
            raise ValueError("Unknown island in path endpoint")
        if origin == destination:
            return ()
        blocked_key = frozenset(blocked_routes)
        cache_key = (origin, destination, blocked_key)
        if edge_cost is None and cache_key in self._path_cache:
            return self._path_cache[cache_key]
        cost_fn = edge_cost or (lambda route: route.distance_days)
        # The route-ID tuple makes equal-cost results deterministic and stable.
        queue: list[tuple[int, tuple[str, ...], str, tuple[str, ...]]] = [(0, (), origin, ())]
        best: dict[str, tuple[int, tuple[str, ...]]] = {origin: (0, ())}
        while queue:
            total, route_key, island_id, path_ids = heapq.heappop(queue)
            if best.get(island_id) != (total, route_key):
                continue
            if island_id == destination:
                result = tuple(self.routes[route_id] for route_id in path_ids)
                if edge_cost is None:
                    self._path_cache[cache_key] = result
                return result
            for route in self.routes_between(island_id):
                if route.id in blocked_routes:
                    continue
                step_cost = cost_fn(route)
                if type(step_cost) is not int or step_cost <= 0:
                    raise ValueError("Path edge costs must be positive integers")
                next_id = self.other_end(route.id, island_id)
                candidate = (total + step_cost, route_key + (route.id,))
                if next_id not in best or candidate < best[next_id]:
                    best[next_id] = candidate
                    heapq.heappush(queue, (*candidate, next_id, path_ids + (route.id,)))
        raise ValueError(f"No route from {origin} to {destination}")
