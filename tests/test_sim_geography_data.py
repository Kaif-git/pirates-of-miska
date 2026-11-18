import json
from collections import defaultdict
from pathlib import Path

from sim.core.rng import RandomStreams
from sim.systems.geography import WorldMap
from sim.systems.weather import WeatherSystem


ROOT = Path(__file__).resolve().parents[1]
MAP_PATH = ROOT / "sim" / "data" / "islands.json"
WORLD_DATA = json.loads(MAP_PATH.read_text(encoding="utf-8"))


def test_default_world_has_forty_islands_in_five_regions():
    regions = {region["id"] for region in WORLD_DATA["regions"]}
    islands = WORLD_DATA["islands"]

    assert len(islands) >= 40
    assert len(regions) >= 5
    assert {island["region_id"] for island in islands} == regions
    assert len({island["id"] for island in islands}) == len(islands)
    assert len({island["name"] for island in islands}) == len(islands)
    assert all(len(island["coords"]) == 2 for island in islands)


def test_route_graph_is_connected_and_uses_valid_deterministic_weights():
    island_ids = {island["id"] for island in WORLD_DATA["islands"]}
    route_ids = set()
    adjacency = defaultdict(set)
    for route in WORLD_DATA["routes"]:
        assert route["id"] not in route_ids
        route_ids.add(route["id"])
        assert route["from"] in island_ids and route["to"] in island_ids
        assert route["from"] != route["to"]
        assert type(route["distance_days_at_base_speed"]) is int
        assert route["distance_days_at_base_speed"] > 0
        assert 0 <= route["patrol_density"] <= 1
        assert 0 <= route["monster_risk"] <= 1
        assert all(value > 0 for value in route["season_modifiers"].values())
        adjacency[route["from"]].add(route["to"])
        adjacency[route["to"]].add(route["from"])

    start = min(island_ids)
    reached = {start}
    frontier = [start]
    while frontier:
        for neighbor in sorted(adjacency[frontier.pop()]):
            if neighbor not in reached:
                reached.add(neighbor)
                frontier.append(neighbor)
    assert reached == island_ids
    assert len([route for route in WORLD_DATA["routes"] if route["chokepoint_flag"]]) >= 2


def test_dangerous_only_region_has_only_hazardous_external_access():
    regions = {region["id"]: region for region in WORLD_DATA["regions"]}
    island_regions = {island["id"]: island["region_id"] for island in WORLD_DATA["islands"]}
    dangerous = {region_id for region_id, region in regions.items()
                 if region.get("dangerous_route_only") is True}
    assert dangerous == {"tempest_belt"}
    ingress = [route for route in WORLD_DATA["routes"]
               if ((island_regions[route["from"]] in dangerous)
                   != (island_regions[route["to"]] in dangerous))]
    assert ingress
    assert all(route["hazards"] for route in ingress)
    assert all(route["chokepoint_flag"] for route in ingress)


def test_route_hazard_references_resolve_to_valid_tunable_hazard_records():
    hazard_rows = WORLD_DATA["hazards"]
    hazards = {hazard["id"]: hazard for hazard in hazard_rows}
    assert len(hazards) == len(hazard_rows)
    for hazard in hazards.values():
        assert 0 <= hazard["trigger_probability"] <= 1
        assert hazard["effect"]
        assert hazard["avoidance"]["skill"] == "navigation"
        assert 0 <= hazard["avoidance"]["difficulty"] <= 100
    assert all(hazard_id in hazards for route in WORLD_DATA["routes"]
               for hazard_id in route["hazards"])
    tempest_islands = {island["id"] for island in WORLD_DATA["islands"]
                       if island["region_id"] == "tempest_belt"}
    assert len(tempest_islands) >= 16
    assert all(route["hazards"] for route in WORLD_DATA["routes"]
               if route["from"] in tempest_islands and route["to"] in tempest_islands)


def test_world_map_loader_and_shortest_path_are_stable_and_weighted():
    world_map = WorldMap.load(MAP_PATH)
    same_map = WorldMap.load(MAP_PATH)

    assert world_map.digest == same_map.digest
    path = world_map.shortest_path("isle_001", "isle_006")
    assert tuple(route.id for route in path) == ("route_003", "route_004", "route_005")
    assert sum(route.distance_days for route in path) == 5
    assert world_map.shortest_path("isle_001", "isle_001") == ()
    detour = world_map.shortest_path(
        "isle_001", "isle_006",
        edge_cost=lambda route: route.distance_days + (100 if route.id == "route_003" else 0),
    )
    assert tuple(route.id for route in detour) == ("route_001", "route_030", "route_005")


def test_regional_weather_is_seeded_independent_and_snapshotable():
    world_map = WorldMap.load(MAP_PATH)
    rng_a = RandomStreams(481516)
    weather_a = WeatherSystem(world_map, rng_a)
    daily_a = [weather_a.update_day(day) for day in range(1, 61)]

    rng_b = RandomStreams(481516)
    weather_b = WeatherSystem(world_map, rng_b)
    # Interleaving an unrelated named stream must not perturb regional weather.
    rng_b.stream("encounters").randint(0, 100000)
    daily_b = [weather_b.update_day(day) for day in range(1, 61)]
    assert daily_a == daily_b
    assert weather_a.snapshot() == weather_b.snapshot()

    checkpoint_rng = RandomStreams(90210)
    checkpoint_weather = WeatherSystem(world_map, checkpoint_rng)
    for day in range(1, 12):
        checkpoint_weather.update_day(day)
    restored = WeatherSystem(world_map, RandomStreams.restore(90210, checkpoint_rng.snapshot()),
                             **checkpoint_weather.snapshot())
    expected = [checkpoint_weather.update_day(day) for day in range(12, 31)]
    actual = [restored.update_day(day) for day in range(12, 31)]
    assert actual == expected


def test_weather_effect_values_change_travel_speed_and_stay_in_data():
    world_map = WorldMap.load(MAP_PATH)
    weather = WeatherSystem(world_map, RandomStreams(1))
    assert weather.speed_permille("tempest_belt") < weather.speed_permille("glass_shoals")
    assert weather.effects("blue_coast")["visibility_permille"] == 1000
    assert [weather.calendar_season(day) for day in (1, 92, 183, 274)] == [
        "spring", "summer", "autumn", "winter",
    ]
