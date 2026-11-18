from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from sim.core.world import WorldState


ROOT = Path(__file__).resolve().parents[1]


def make_voyage(seed="travel-seed", weather="fair"):
    world = WorldState.create(seed)
    world.weather.states["blue_coast"] = weather
    ship_id, created_event_id = world.create_vessel(
        "North Star", "isle_001", navigation=100, condition=100,
    )
    voyage_event_id = world.start_voyage(
        ship_id, "isle_006", caused_by=created_event_id,
    )
    return world, ship_id, voyage_event_id


def test_voyage_uses_route_time_and_transit_progress_without_teleporting():
    world, ship_id, voyage_event_id = make_voyage()
    transit = world.travel.transits[ship_id]
    assert transit["route_ids"] == ["route_003", "route_004", "route_005"]
    assert transit["arrival_tick"] - transit["start_tick"] == (
        sum(transit["route_durations_hours"]) * world.config["time"]["hours_per_tick"]
    )
    assert sum(transit["route_durations_hours"]) == 123
    assert world.location_of(ship_id) == {
        "kind": "route", "id": "route_003", "from": "isle_001",
        "to": "isle_004", "progress_permille": 0,
    }

    first_leg_ticks = transit["route_durations_hours"][0] * world.config["time"]["hours_per_tick"]
    world.run_until(transit["start_tick"] + max(1, first_leg_ticks // 2))
    location = world.location_of(ship_id)
    assert location["kind"] == "route"
    assert location["id"] == "route_003"
    assert 0 < location["progress_permille"] < 1000
    assert "location" not in world.entities[ship_id]
    world.validate_locations()
    assert not world.event_log.by_type("VOYAGE_ARRIVED")

    world.run_until(transit["arrival_tick"])
    arrival = world.event_log.by_type("VOYAGE_ARRIVED")[-1]
    assert arrival.tick == transit["arrival_tick"]
    assert voyage_event_id in arrival.caused_by
    assert world.location_of(ship_id) == {"kind": "island", "id": "isle_006"}
    assert ship_id not in world.travel.transits
    world.event_log.validate_causality()


def test_regional_weather_changes_actual_voyage_duration():
    fair, fair_ship, _ = make_voyage("weather-travel", "fair")
    storm, storm_ship, _ = make_voyage("weather-travel", "storm")

    fair_hours = sum(fair.travel.transits[fair_ship]["route_durations_hours"])
    storm_hours = sum(storm.travel.transits[storm_ship]["route_durations_hours"])
    assert storm_hours > fair_hours


def test_route_hazards_are_seeded_avoided_or_have_logged_persistent_effects():
    world = WorldState.create("forced-route-hazard")
    reef = world.geography.hazards["reef_chain"]
    reef["trigger_probability"] = 1
    reef["avoidance"]["difficulty"] = 100
    reef["effect"].update({
        "travel_delay_hours": 9,
        "hull_damage_chance": 1,
        "hull_damage_points": 7,
    })
    ship_id, created_event_id = world.create_vessel(
        "Low-Tide", "isle_001", navigation=0, condition=100,
    )
    world.start_voyage(ship_id, "isle_006", caused_by=created_event_id)

    hazard_events = world.event_log.by_type("ROUTE_HAZARD")
    assert hazard_events
    assert all(event.data["hazard_id"] == "reef_chain" for event in hazard_events)
    assert all(event.data["avoided"] is False for event in hazard_events)
    assert all(event.data["delay_hours"] == 9 for event in hazard_events)
    assert world.entities[ship_id]["condition"] < 100
    assert sum(world.travel.transits[ship_id]["route_durations_hours"]) >= 9


def test_navigation_skill_can_avoid_a_triggered_route_hazard():
    world = WorldState.create("avoid-route-hazard")
    reef = world.geography.hazards["reef_chain"]
    reef["trigger_probability"] = 1
    reef["avoidance"]["difficulty"] = 100
    reef["effect"].update({
        "travel_delay_hours": 9,
        "hull_damage_chance": 1,
        "hull_damage_points": 7,
    })
    ship_id, created_event_id = world.create_vessel(
        "Reef Reader", "isle_001", navigation=100, condition=100,
    )
    world.start_voyage(ship_id, "isle_006", caused_by=created_event_id)

    hazards = world.event_log.by_type("ROUTE_HAZARD")
    assert hazards
    assert all(event.data["avoided"] is True for event in hazards)
    assert all(event.data["delay_hours"] == 0 for event in hazards)
    assert all(event.data["condition_damage"] == 0 for event in hazards)
    assert world.entities[ship_id]["condition"] == 100


def test_mid_voyage_snapshot_restores_position_weather_and_arrival_exactly(tmp_path):
    uninterrupted, ship_a, created_a = make_voyage("voyage-resume")
    resumed_source, ship_b, created_b = make_voyage("voyage-resume")
    assert (ship_a, created_a) == (ship_b, created_b)
    arrival_tick = uninterrupted.travel.transits[ship_a]["arrival_tick"]
    halfway = max(1, arrival_tick // 2)
    uninterrupted.run_until(halfway)
    resumed_source.run_until(halfway)
    snapshot = resumed_source.save(tmp_path / "at-sea.json")
    resumed = WorldState.load(snapshot)

    assert resumed.location_of(ship_b) == uninterrupted.location_of(ship_a)
    uninterrupted.run_until(arrival_tick)
    resumed.run_until(arrival_tick)

    assert resumed.to_snapshot() == uninterrupted.to_snapshot()
    assert resumed.event_log.to_jsonl() == uninterrupted.event_log.to_jsonl()
    assert resumed.location_of(ship_b) == {"kind": "island", "id": "isle_006"}


def test_invalid_voyage_requests_do_not_mutate_world_state():
    world = WorldState.create("invalid-voyage")
    ship_id, created_event_id = world.create_vessel("Harbor Light", "isle_001")
    before = deepcopy(world.to_snapshot())

    with pytest.raises(ValueError, match="differ"):
        world.start_voyage(ship_id, "isle_001", caused_by=created_event_id)
    with pytest.raises(ValueError, match="Unknown island"):
        world.start_voyage(ship_id, "isle_999", caused_by=created_event_id)
    with pytest.raises(ValueError, match="Unknown cause"):
        world.start_voyage(ship_id, "isle_002", caused_by="event_999999")

    assert world.to_snapshot() == before


def test_location_validator_rejects_teleport_and_mismatched_route_coordinates():
    world, ship_id, _ = make_voyage("location-validator")
    world.run_until(1)
    world.validate_locations()
    location = world.location_of(ship_id)
    world.entities[ship_id]["location"] = {"kind": "island", "id": "isle_030"}
    with pytest.raises(ValueError, match="transit state"):
        world.validate_locations()

    world.entities[ship_id].pop("location")
    # An incorrect segment endpoint must not pass the single-source location audit.
    world.travel.transits[ship_id]["origin"] = "isle_030"
    with pytest.raises(ValueError):
        world.validate_locations()


def test_inspect_cli_renders_default_and_live_maps_and_checks_locations(tmp_path):
    static = subprocess.run(
        [sys.executable, "-m", "sim.tools.inspect", "map"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert static.returncode == 0, static.stderr
    static_map = json.loads(static.stdout)
    assert len(static_map["regions"]) == 5
    assert len(static_map["islands"]) == 40
    assert len(static_map["routes"]) >= 2

    world, ship_id, created_event_id = make_voyage("inspect-live-map")
    world.run_until(1)
    snapshot = world.save(tmp_path / "inspect-world.json")
    live = subprocess.run(
        [sys.executable, "-m", "sim.tools.inspect", str(snapshot), "--map"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert live.returncode == 0, live.stderr
    report = json.loads(live.stdout)
    vessel = next(row for row in report["vessels"] if row["id"] == ship_id)
    assert vessel["location"]["kind"] == "route"

    validation = subprocess.run(
        [sys.executable, "-m", "sim.tools.inspect", str(snapshot), "--validate-locations"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert validation.returncode == 0, validation.stderr
    assert json.loads(validation.stdout)["locations_valid"] is True
