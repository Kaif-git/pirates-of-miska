from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from sim.core.world import WorldState
from sim.tools.coherence_run import _census


ROOT = Path(__file__).resolve().parents[1]


def make_voyage(seed="travel-seed", weather="fair"):
    world = WorldState.create(seed)
    world.weather.states["blue_coast"] = weather
    ship_id, created_event_id = world.create_vessel(
        "North Star", "isle_001", navigation=100, condition=100,
    )
    crew_id = world.crew_ships.provision_recruited_vessel(
        world, ship_id, "privateer", caused_by=created_event_id)
    voyage_event_id = world.start_voyage(
        ship_id, "isle_006", caused_by=world.crew_ships.crews[crew_id]["last_event_id"],
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


def test_commanded_ship_arrival_creates_causal_stranded_employment_opportunity():
    world, rescuer_ship_id, voyage_event_id = make_voyage("arrival-stranded-employment")
    stranded_ship_id, stranded_created_id = world.create_vessel(
        "Wrecked Hope", "isle_006", navigation=45)
    stranded_crew_id = world.crew_ships.provision_recruited_vessel(
        world, stranded_ship_id, "privateer", caused_by=stranded_created_id)
    stranded_cause = world.crew_ships.crews[stranded_crew_id]["last_event_id"]
    world.encounters._sink_ship(world, stranded_ship_id, stranded_cause)

    arrival_tick = world.travel.transits[rescuer_ship_id]["arrival_tick"]
    world.run_until(arrival_tick)

    arrival = world.event_log.by_type("VOYAGE_ARRIVED")[-1]
    search = next(event for event in world.event_log.by_type("CREW_EMPLOYMENT_SOUGHT")
                  if event.data["crew_id"] == stranded_crew_id)
    accepted = next(event for event in world.event_log.by_type("CREW_EMPLOYMENT_ACCEPTED")
                    if event.data["from_crew_id"] == stranded_crew_id)

    assert arrival.id in search.caused_by
    assert search.id in accepted.caused_by
    assert accepted.data["to_crew_id"] == world.entities[rescuer_ship_id]["controller_crew_id"]
    assert stranded_crew_id not in world.crew_ships.crews
    transferred_members = [event.targets[0]["entity_id"]
                           for event in world.event_log.by_type("CREW_MEMBER_TRANSFERRED")
                           if accepted.id in event.caused_by]
    assert transferred_members
    assert all(world.entities[person_id]["crew_id"] == accepted.data["to_crew_id"]
               for person_id in transferred_members)
    world.event_log.validate_causality()


def test_arriving_commanded_ship_rescues_only_matching_missing_crew_members():
    world, rescuer_ship_id, _voyage_event_id = make_voyage(
        "arrival-stranded-rescue")
    wreck_id, wreck_created_id = world.create_vessel("Lost Hope", "isle_006")
    stranded_crew_id = world.crew_ships.provision_recruited_vessel(
        world, wreck_id, "privateer", caused_by=wreck_created_id)
    stranded = world.crew_ships.crews[stranded_crew_id]
    extra_id = world.characters.create_character(
        world, tier="MINOR", cause_event_id=wreck_created_id,
        island_id="isle_006")
    world.crew_ships.recruit(
        world, stranded_crew_id, extra_id, caused_by=stranded["last_event_id"])
    sink_event_id = world.encounters._sink_ship(
        world, wreck_id, stranded["last_event_id"])
    disposition = world.event_log.by_type("SHIP_LOSS_DISPOSITION")[-1]
    person_id = world.crew_ships.crews[stranded_crew_id]["roster"][0]
    route_id, route = next(
        (route_id, route) for route_id, route in sorted(world.geography.routes.items())
        if "isle_006" in {route.origin, route.destination})
    last_known = {"kind": "route", "id": route_id,
                  "from": route.origin, "to": route.destination,
                  "progress_permille": 500}
    person = world.entities[person_id]
    person.update({"ship_id": None, "location": last_known,
                   "missing_at_sea": {
                       "since_tick": world.clock.tick,
                       "last_known_location": last_known,
                       "search_location": {"kind": "island", "id": "isle_006"},
                       "ship_id": wreck_id,
                       "cause_event_id": disposition.id,
                   }})
    missing_event = world.event_log.append(
        tick=world.clock.tick, event_type="CHARACTER_MISSING", location=route_id,
        actors=[{"role": "missing_person", "entity_id": person_id}],
        data={"character_id": person_id, "ship_id": wreck_id,
              "last_known_location": last_known,
              "search_location": {"kind": "island", "id": "isle_006"}},
        caused_by=[sink_event_id, disposition.id],
        causes_tags=["shipwreck", "missing_person"])
    person["last_event_id"] = missing_event.id
    other_person_id = world.crew_ships.crews[stranded_crew_id]["roster"][1]
    other_search_island = "isle_005"
    other_route_id, other_route = next(
        (candidate_id, candidate) for candidate_id, candidate
        in sorted(world.geography.routes.items())
        if other_search_island in {candidate.origin, candidate.destination})
    other_last_known = {"kind": "route", "id": other_route_id,
                        "from": other_route.origin, "to": other_route.destination,
                        "progress_permille": 500}
    other_person = world.entities[other_person_id]
    other_person.update({"ship_id": None, "location": other_last_known,
                         "missing_at_sea": {
                             "since_tick": world.clock.tick,
                             "last_known_location": other_last_known,
                             "search_location": {"kind": "island",
                                                 "id": other_search_island},
                             "ship_id": wreck_id,
                             "cause_event_id": disposition.id,
                         }})
    other_missing_event = world.event_log.append(
        tick=world.clock.tick, event_type="CHARACTER_MISSING", location=other_route_id,
        actors=[{"role": "missing_person", "entity_id": other_person_id}],
        data={"character_id": other_person_id, "ship_id": wreck_id,
              "last_known_location": other_last_known,
              "search_location": {"kind": "island", "id": other_search_island}},
        caused_by=[sink_event_id, disposition.id],
        causes_tags=["shipwreck", "missing_person"])
    other_person["last_event_id"] = other_missing_event.id
    population_before = _census(world, 0)["people"][
        "world_population_conservation"]["observed_living_population"]

    arrival_tick = world.travel.transits[rescuer_ship_id]["arrival_tick"]
    world.run_until(arrival_tick)

    arrival = next(event for event in reversed(world.event_log.by_type("VOYAGE_ARRIVED"))
                   if event.actors[0]["entity_id"] == rescuer_ship_id)
    rescued = next(event for event in world.event_log.by_type("CHARACTER_RESCUED")
                   if event.targets[0]["entity_id"] == person_id)
    assert arrival.id in rescued.caused_by
    assert person_id in world.entities and person_id not in world.archive
    assert "missing_at_sea" not in world.entities[person_id]
    assert world.entities[person_id]["location"] == {
        "kind": "island", "id": "isle_006"}
    assert "missing_at_sea" in world.entities[other_person_id]
    assert not any(event.targets and event.targets[0].get("entity_id") == other_person_id
                   for event in world.event_log.by_type("CHARACTER_RESCUED"))
    assert _census(world, 0)["people"]["world_population_conservation"][
        "observed_living_population"] == population_before
    world.crew_ships.validate_world(world)
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
    crew_id = world.crew_ships.provision_recruited_vessel(
        world, ship_id, "privateer", caused_by=created_event_id)
    world.start_voyage(ship_id, "isle_006",
                       caused_by=world.crew_ships.crews[crew_id]["last_event_id"])

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
    crew_id = world.crew_ships.provision_recruited_vessel(
        world, ship_id, "privateer", caused_by=created_event_id)
    world.start_voyage(ship_id, "isle_006",
                       caused_by=world.crew_ships.crews[crew_id]["last_event_id"])

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
    crew_id = world.crew_ships.provision_recruited_vessel(
        world, ship_id, "privateer", caused_by=created_event_id)
    cause = world.crew_ships.crews[crew_id]["last_event_id"]
    before = deepcopy(world.to_snapshot())

    with pytest.raises(ValueError, match="differ"):
        world.start_voyage(ship_id, "isle_001", caused_by=cause)
    with pytest.raises(ValueError, match="Unknown island"):
        world.start_voyage(ship_id, "isle_999", caused_by=cause)
    with pytest.raises(ValueError, match="Unknown cause"):
        world.start_voyage(ship_id, "isle_002", caused_by="event_999999")

    assert world.to_snapshot() == before


def test_uncrewed_new_hull_cannot_depart_without_a_controller():
    world = WorldState.create("uncrewed-vessel")
    ship_id, created_event_id = world.create_vessel("Unclaimed Hull", "isle_001")

    with pytest.raises(ValueError, match="valid onboard controller"):
        world.start_voyage(ship_id, "isle_002", caused_by=created_event_id)

    assert ship_id not in world.travel.transits
    assert world.entities[ship_id]["legal_owner"] is None
    assert world.entities[ship_id]["ownership_event_id"] == created_event_id
    world.to_snapshot()


def test_archived_faction_does_not_terminal_its_distinct_crew():
    world = WorldState.create("terminal-faction-command")
    ship_id = sorted(ship_id for ship_id, row in world.entities.items()
                     if row.get("kind") == "ship")[0]
    crew_id = world.entities[ship_id]["crew_id"]
    faction_id = world.crew_ships.crews[crew_id]["faction_id"]
    faction = world.politics.factions[faction_id]
    world.politics.dissolve_faction(
        world, faction_id, caused_by=faction["last_event_id"],
        reason="terminal_guard_test")

    assert faction_id in world.politics.archived_factions
    assert crew_id in world.crew_ships.crews
    assert world.crew_ships.crews[crew_id]["faction_id"] is None
    active_faction = next(candidate for candidate in world.politics.factions
                          if candidate != faction_id)
    event_count = len(world.event_log)
    with pytest.raises(ValueError, match="Unknown faction"):
        world.politics.propose_treaty(
            world, faction_id, active_faction, "alliance",
            caused_by=world.event_log.by_type("WORLD_GENERATED")[0].id)
    assert len(world.event_log) == event_count
    assert world.ship_is_commandable(ship_id)
    with pytest.raises(ValueError, match="Unknown faction"):
        world.politics._faction(world, faction_id)
    world.start_voyage(
        ship_id, "isle_002", caused_by=world.crew_ships.crews[crew_id]["last_event_id"])
    restored = WorldState.from_snapshot(world.to_snapshot())
    assert restored.ship_is_commandable(ship_id)


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
