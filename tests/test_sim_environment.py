import copy

import pytest

from sim.core.world import WorldState
from sim.systems.environment import validate_environment_data


def _set_weather_to_hurricanes(world):
    world.weather.weather_data["persistence_bonus_weight"] = 0
    for season in world.weather.weather_data["calendar_weight_multipliers"]:
        world.weather.weather_data["calendar_weight_multipliers"][season] = {
            state: 100 for state in world.weather.weather_data["states"]
        }
    for season in world.weather.weather_data["seasonal_weights"]:
        world.weather.weather_data["seasonal_weights"][season] = {
            state: int(state == "hurricane") for state in world.weather.weather_data["states"]
        }


def test_environment_catalog_rejects_invalid_rules():
    world = WorldState.create(901)
    bad = copy.deepcopy(world.environment.data)
    bad["rules"]["disease_spread_basis_points_per_day"] = 10001
    with pytest.raises(ValueError, match="bounds"):
        validate_environment_data(bad)


def test_annual_harvest_failure_reduces_finite_grain_production():
    world = WorldState.create("harvest-failure")
    world.environment.rules["harvest_min_yield_permille"] = 500
    world.environment.rules["harvest_max_yield_permille"] = 500
    world.environment.rules["harvest_failure_threshold_permille"] = 700
    world.run_until(24)

    failures = world.event_log.by_type("HARVEST_FAILURE")
    assert failures
    island_id = failures[0].location
    assert world.environment.harvests[island_id]["yield_permille"] == 500
    assert world.economy.islands[island_id]["harvest_yield_permille"] == 500
    daily = next(event for event in world.event_log.by_type("ISLAND_ECONOMY_UPDATED")
                 if event.data["day"] == 1 and event.location == island_id)
    assert daily.data["production_milli"]["grain"] < \
        world.economy.islands[island_id]["produces_per_day"].get("grain", 0) * 1000
    world.event_log.validate_causality()


def test_disease_spreads_causally_and_reduces_productivity():
    infected = WorldState.create("disease-spread")
    control = WorldState.create("disease-spread")
    infected.environment.rules["disease_outbreak_basis_points_per_island_day"] = 10000
    infected.environment.rules["disease_spread_basis_points_per_day"] = 10000
    infected.environment.rules["disease_productivity_loss_permille"] = 500
    infected.run_until(24)
    source_island = "isle_001"
    source = infected.environment.outbreaks[source_island]
    infected.environment.outbreaks = {source_island: source}
    infected.environment.rules["disease_outbreak_basis_points_per_island_day"] = 0
    infected.run_until(48)
    control.run_until(48)

    spreads = infected.event_log.by_type("DISEASE_SPREAD")
    assert spreads
    assert all(event.caused_by for event in spreads)
    island_id = source_island
    sick_day = next(event for event in infected.event_log.by_type("ISLAND_ECONOMY_UPDATED")
                    if event.data["day"] == 2 and event.location == island_id)
    healthy_day = next(event for event in control.event_log.by_type("ISLAND_ECONOMY_UPDATED")
                       if event.data["day"] == 2 and event.location == island_id)
    assert sick_day.data["production_milli"]["fish"] < healthy_day.data["production_milli"]["fish"]
    infected.event_log.validate_causality()


def test_storm_transition_damages_a_ship_and_is_logged():
    world = WorldState.create("storm-path")
    _set_weather_to_hurricanes(world)
    ship_id = next(ship_id for ship_id, ship in world.entities.items()
                   if ship.get("kind") == "ship" and "hull" in ship)
    location = world.location_of(ship_id)
    island_id = (location["id"] if location["kind"] == "island" else
                 world.geography.routes[location["id"]].origin)
    region_id = world.geography.islands[island_id].region_id
    world.weather.states[region_id] = "calm"
    before = world.entities[ship_id]["hull"]["current"]
    world.run_until(4 * 24)

    storms = world.event_log.by_type("HURRICANE")
    damage = [event for event in world.event_log.by_type("SHIP_DAMAGED")
              if event.targets[0]["entity_id"] == ship_id]
    assert storms
    assert world.event_log.by_type("STORM_PATH_UPDATED")
    assert damage
    assert world.entities[ship_id]["hull"]["current"] < before
    world.to_snapshot()


def test_monsters_move_and_treasure_is_finite_and_snapshot_safe():
    world = WorldState.create("finite-environment")
    world.environment.rules["monster_spawn_basis_points_per_day"] = 10000
    world.environment.rules["monster_max_active"] = 1
    world.run_until(8 * 24)
    assert world.environment.monsters
    assert world.event_log.by_type("MONSTER_SIGHTING")
    assert world.event_log.by_type("MONSTER_MOVED")
    clue_spreads = world.event_log.by_type("TREASURE_CLUE_SPREAD")
    assert clue_spreads
    assert all(event.caused_by for event in clue_spreads)
    treasure_id, treasure = next(iter(world.environment.treasures.items()))
    candidates = [entity_id for entity_id, character in world.entities.items()
                  if character.get("kind") == "character" and character.get("death_tick") is None
                  and character.get("ship_id") is None
                  and world.location_of(entity_id).get("kind") == "island"]
    assert candidates
    character_id = candidates[0]
    world.entities[character_id]["location"] = {"kind": "island", "id": treasure["island_id"]}
    event_id = world.environment.discover_treasure(
        world, treasure_id, character_id, caused_by=treasure["last_rumor_event_id"])
    with pytest.raises(ValueError, match="already found"):
        world.environment.discover_treasure(
            world, treasure_id, character_id, caused_by=event_id)
    resumed = WorldState.from_snapshot(world.to_snapshot())
    assert resumed.environment.snapshot() == world.environment.snapshot()
    assert resumed.event_log.to_jsonl() == world.event_log.to_jsonl()


def test_environment_snapshot_resume_matches_uninterrupted_random_events():
    uninterrupted = WorldState.create("environment-resume")
    uninterrupted.run_until(40 * 24)
    resumed = WorldState.from_snapshot(uninterrupted.to_snapshot())

    uninterrupted.run_until(100 * 24)
    resumed.run_until(100 * 24)

    assert resumed.environment.snapshot() == uninterrupted.environment.snapshot()
    assert resumed.event_log.to_jsonl() == uninterrupted.event_log.to_jsonl()
    assert resumed.to_snapshot() == uninterrupted.to_snapshot()
