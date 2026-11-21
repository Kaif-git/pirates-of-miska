from copy import deepcopy
import math
import pytest

from pirates.game import World
from pirates import sea_encounter
from pirates.sea_traffic import (LOOKOUT_RADIUS, NPC_LANE_HOP_TICKS,
                                 interpolate_chart_edge, relative_observation)
from pirates.voyage import LANES, NpcFleetSystem, SEA_NODES


def quiet_world():
    world = World()
    world.npcs.sighted.update(ship["id"] for ship in world.npcs.ships)
    return world


def test_chart_edge_interpolation_is_linear_and_clamped():
    assert interpolate_chart_edge((0, 1), (4, 5), 0.5) == (2, 3)
    assert interpolate_chart_edge((0, 1), (4, 5), -2) == (0, 1)
    assert interpolate_chart_edge((0, 1), (4, 5), 2) == (4, 5)


def test_relative_motion_uses_observed_samples_for_crossing_overtaking_and_escape():
    def classify(player_before, vessel_before, player_now, vessel_now):
        return relative_observation(
            player_now, vessel_now, player_before=player_before,
            vessel_before=vessel_before, elapsed_seconds=1)["relative_motion"]

    assert relative_observation((0, 0), (1, 0))["relative_motion"] == "unknown"
    assert classify((0, 0), (1, 0), (0, 0), (0.5, 0)) == "closing"  # head-on
    assert classify((0, 0), (0.5, 0), (0, 0), (1, 0)) == "opening"  # escape
    assert classify((0, 0), (1, 0), (0, 0), (math.cos(0.01), math.sin(0.01))) == "crossing"
    assert classify((0, 0), (2, 0), (0.5, 0), (2.1, 0)) == "overtaking"
    assert classify((0, 0), (2, 0), (0.5, 0), (2.5, 0)) == "following"
    rows = sea_encounter.contact_rows(quiet_world(), {
        "id": "traffic", "contact_instance_id": "traffic:1",
        "relative_observation": {"range_units": 1.0, "bearing_deg": 0.0,
                                 "relative_motion": "overtaking"},
        "contact": sea_encounter.new_contact(),
    })
    assert any("same-course traffic is overtaking" in row for row in rows)


def test_npc_lane_motion_interpolates_mid_hour_and_arrives_on_next_hour():
    world = quiet_world()
    ship = next(row for row in world.npcs.ships if row["id"] == "syndicate_1")
    motion = deepcopy(ship["motion"])
    start_tick = world.clock.total_ticks
    assert motion is not None
    assert {motion["from"], motion["to"]} in [set(lane) for lane in LANES]
    start = SEA_NODES[motion["from"]]["xy"]
    end = SEA_NODES[motion["to"]]["xy"]

    world.clock.total_ticks = start_tick + NPC_LANE_HOP_TICKS // 2
    world.npcs.advance_second(world, (-10, -10))
    halfway = world.npcs._chart_position(ship, world.clock.total_ticks)
    assert halfway == ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
    assert ship["node"] == motion["from"]  # node is the last reached waypoint

    world.clock.total_ticks = start_tick + NPC_LANE_HOP_TICKS
    world.npcs.advance_second(world, (-10, -10))
    assert ship["node"] == motion["to"]
    assert ship["motion"] is not None
    assert ship["motion"]["from"] == motion["to"]


def test_reserved_encounter_pauses_and_resumes_motion_without_teleporting():
    world = quiet_world()
    ship = next(row for row in world.npcs.ships if row["id"] == "syndicate_1")
    ship_id = ship["id"]
    arrived_at = ship["motion"]["to"]
    start_tick = world.clock.total_ticks
    world.clock.total_ticks = start_tick + NPC_LANE_HOP_TICKS // 2
    world.npcs.advance_second(world, (-10, -10), reserved_ship_ids={ship_id})
    held_position = world.npcs._chart_position(ship, world.clock.total_ticks)
    restored = NpcFleetSystem.restore(
        world.npcs.snapshot(), current_tick=world.clock.total_ticks)
    restored_ship = next(row for row in restored.ships if row["id"] == ship_id)
    assert restored._chart_position(restored_ship, world.clock.total_ticks) == held_position

    world.clock.total_ticks = start_tick + 2 * NPC_LANE_HOP_TICKS
    world.npcs.advance_second(world, (-10, -10), reserved_ship_ids={ship_id})
    assert world.npcs._chart_position(ship, world.clock.total_ticks) == held_position
    assert ship["node"] == "verdant"

    world.npcs.advance_second(world, (-10, -10))
    resumed_position = world.npcs._chart_position(ship, world.clock.total_ticks)
    assert resumed_position == held_position
    world.clock.total_ticks = start_tick + 2 * NPC_LANE_HOP_TICKS + NPC_LANE_HOP_TICKS // 2
    world.npcs.advance_second(world, (-10, -10))
    assert ship["node"] == arrived_at and ship["motion"] is None


def test_live_encounter_reservation_then_voyage_resume_recovers_both_routes():
    world = quiet_world()
    fleet = world.npcs
    fleet.sighted.clear()
    fleet.sighting_reports.clear()
    fleet.contact_tracks.clear()
    for ship in fleet.ships:
        ship["node"] = ship["destination"] = "deepwater"
        ship["motion"] = None
    target = next(row for row in fleet.ships if row["id"] == "government_1")
    start_tick = world.clock.total_ticks
    target["node"], target["destination"] = "verdant", "shoals"
    target["motion"] = {
        "from": "verdant", "to": "shoals", "start_tick": start_tick,
        "paused_at_tick": None,
    }
    world.voyages.confirm(world, "cinder")
    world.enter_sea()
    world.voyages.voyage["elapsed_seconds"] = 1800
    world.advance(100)
    assert world.voyages.voyage["status"] == "encounter"
    encounter = world.voyages.voyage["encounter"]
    assert encounter["contact_instance_id"] == "government_1:1"

    world.advance(120 * 100)
    held_position = fleet._chart_position(target, world.clock.total_ticks)
    assert target["motion"]["paused_at_tick"] is not None
    paused_at_tick = target["motion"]["paused_at_tick"]
    original_motion_start = start_tick
    world.advance(120 * 100)
    assert fleet._chart_position(target, world.clock.total_ticks) == held_position

    old_leg, old_progress = (world.voyages.voyage["leg"],
                             world.voyages.voyage["leg_progress"])
    world.voyages.continue_after_sighting()
    release_tick = world.clock.total_ticks
    world.advance(100)
    assert world.voyages.voyage["status"] == "underway"
    assert (world.voyages.voyage["leg"], world.voyages.voyage["leg_progress"]) > (
        old_leg, old_progress)
    assert target["motion"]["paused_at_tick"] is None
    resumed_position = fleet._chart_position(target, world.clock.total_ticks)
    assert resumed_position == held_position
    world.advance(100)
    resumed_position = fleet._chart_position(target, world.clock.total_ticks)
    assert resumed_position != held_position
    assert target["motion"]["start_tick"] == (
        release_tick + 100 - (paused_at_tick - original_motion_start))


def test_lookout_sighting_uses_chart_position_without_private_vessel_state():
    world = quiet_world()
    fleet = world.npcs
    fleet.sighted.clear()
    for ship in fleet.ships:
        ship["node"] = ship["destination"] = "deepwater"
        ship["motion"] = None
    target = next(row for row in fleet.ships if row["id"] == "government_1")
    target["node"] = target["destination"] = "shoals"
    sighting = fleet.advance_second(world, (3.0, 2.6), sighting_enabled=True)
    assert sighting["id"] == target["id"]
    assert sighting["chart_position"] == [3.0, 2.0]
    assert sighting["relative_observation"]["range_units"] == 0.6
    assert sighting["relative_observation"]["relative_motion"] == "unknown"
    assert not {"cargo", "silver", "mission", "destination", "motion"} & set(sighting)


def test_live_lookout_labels_relative_motion_only_after_two_nearby_samples():
    world = quiet_world()
    fleet = world.npcs
    fleet.sighted.clear()
    for ship in fleet.ships:
        ship["node"] = ship["destination"] = "deepwater"
        ship["motion"] = None
    target = next(row for row in fleet.ships if row["id"] == "government_1")
    target["node"] = target["destination"] = "shoals"
    fleet.advance_second(world, (3.0, 2.8), sighting_enabled=False)
    world.clock.total_ticks += 100
    sighting = fleet.advance_second(world, (3.0, 2.7995), sighting_enabled=True)
    assert sighting["id"] == target["id"]
    assert sighting["relative_observation"]["relative_motion"] == "closing"
    rows = sea_encounter.contact_rows(world, {**sighting, "contact": sea_encounter.new_contact()})
    assert any("Observed motion: closing" in row for row in rows)
    assert all("destination" not in row and "cargo" not in row for row in rows)


def test_minimum_voyage_age_gates_sighting_without_exhausting_early_contact():
    world = quiet_world()
    fleet = world.npcs
    fleet.sighted.clear()
    for ship in fleet.ships:
        ship["node"] = ship["destination"] = "deepwater"
        ship["motion"] = None
    target = fleet.ships[0]
    target["node"] = target["destination"] = "verdant"
    world.voyages.confirm(world, "cinder")
    world.enter_sea()
    world.voyages.voyage["elapsed_seconds"] = 1799

    world.advance(100)
    assert world.voyages.voyage["status"] == "underway"
    assert target["id"] not in fleet.sighted
    world.advance(100)
    assert world.voyages.voyage["status"] == "encounter"
    assert target["id"] in fleet.sighted


def test_repeat_meeting_gets_fresh_instance_after_hysteretic_contact_loss():
    # Exercise the authored lawful-trader roster as well as the shared fleet
    # lifecycle; prior sighting history must not exhaust its vessel identities.
    world = World.new_authored_mvp()
    fleet = world.npcs
    fleet.sighted.clear()
    fleet.sighting_reports.clear()
    fleet.contact_tracks.clear()
    for ship in fleet.ships:
        ship["node"] = ship["destination"] = "deepwater"
        ship["motion"] = None
    target = next(row for row in fleet.ships if row["id"] == "government_1")
    target["node"] = target["destination"] = "shoals"
    first = fleet.advance_second(world, (3.0, 2.0), sighting_enabled=True)
    assert first["contact_instance_id"] == "government_1:1"
    assert fleet.advance_second(world, (3.0, 2.0), sighting_enabled=True) is None
    first_contact = sea_encounter.contact_of(first)
    first_contact["parley"] = {
        "topics": ["transfer_permission"], "stand_down": False,
        "transfer_permission": True, "warned_off": False,
    }

    # A brief excursion inside the loss hysteresis does not end this meeting.
    for _ in range(90):
        world.clock.total_ticks += 100
        assert fleet.advance_second(world, (4.4, 2.0), sighting_enabled=True) is None
    assert fleet.contact_tracks[target["id"]]["active"] is True

    # More than 1.5 chart units for one minute ends the instance; reunion is new.
    for _ in range(61):
        world.clock.total_ticks += 100
        fleet.advance_second(world, (4.6, 2.0), sighting_enabled=True)
    assert fleet.contact_tracks[target["id"]]["active"] is False
    world.clock.total_ticks += 100
    second = fleet.advance_second(world, (3.0, 2.0), sighting_enabled=True)
    assert second["contact_instance_id"] == "government_1:2"
    second_contact = sea_encounter.contact_of(second)
    assert second_contact["parley"] is None
    assert second_contact["signals"] == [] and second_contact["crossing"] is None

    restored = NpcFleetSystem.restore(
        fleet.snapshot(), scenario_id=world.scenario_id,
        current_tick=world.clock.total_ticks)
    assert restored.contact_tracks[target["id"]]["instance_id"] == "government_1:2"
    assert restored.contact_tracks[target["id"]]["active"] is True


def test_reunion_allows_fresh_signal_without_reusing_old_consent_or_hail(tmp_path):
    world = quiet_world()
    fleet = world.npcs
    fleet.sighted.clear()
    fleet.sighting_reports.clear()
    fleet.contact_tracks.clear()
    fleet.player_contacts.clear()
    for ship in fleet.ships:
        ship["node"] = ship["destination"] = "deepwater"
        ship["motion"] = None
    target = next(row for row in fleet.ships if row["id"] == "government_1")
    target["node"] = target["destination"] = "verdant"
    world.voyages.confirm(world, "cinder")
    world.enter_sea()
    world.voyages.voyage["elapsed_seconds"] = 1800

    world.advance(100)
    first = world.voyages.voyage["encounter"]
    assert first["contact_instance_id"] == "government_1:1"
    sea_encounter.contact_of(first)["parley"] = {
        "topics": ["transfer_permission"], "stand_down": False,
        "transfer_permission": True, "warned_off": False,
    }
    fleet.hail_ship(world, target["id"])
    assert fleet.player_contacts[target["id"]]["contact_instance_id"] == "government_1:1"
    ticks_after_first_hail = world.clock.total_ticks
    with pytest.raises(ValueError, match="already recorded"):
        fleet.hail_ship(world, target["id"])
    assert world.clock.total_ticks == ticks_after_first_hail

    world.voyages.continue_after_sighting()
    target["node"] = target["destination"] = "deepwater"
    target["motion"] = None
    world.advance(61 * 100)
    assert fleet.contact_tracks[target["id"]]["active"] is False

    target["node"] = target["destination"] = "verdant"
    world.advance(100)
    second = world.voyages.voyage["encounter"]
    assert second["contact_instance_id"] == "government_1:2"
    assert sea_encounter.contact_of(second)["parley"] is None
    assert fleet.contact_record_for_encounter(target["id"], second) is None
    fleet.hail_ship(world, target["id"])
    assert fleet.player_contacts[target["id"]]["contact_instance_id"] == "government_1:2"

    save_path = tmp_path / "m4-repeat-contact.json"
    world.save(save_path)
    resumed = World.load(save_path)
    resumed_contact = resumed.voyages.voyage["encounter"]
    assert resumed_contact["contact_instance_id"] == "government_1:2"
    assert resumed.npcs.contact_tracks[target["id"]]["active"] is True
    assert resumed.npcs.contact_record_for_encounter(target["id"], resumed_contact)[
        "contact_instance_id"] == "government_1:2"


def test_npc_motion_save_resume_and_hourly_chunking_are_equivalent():
    fine, coarse = quiet_world(), quiet_world()
    fine_ship = next(row for row in fine.npcs.ships if row["id"] == "syndicate_1")
    start_tick = fine.clock.total_ticks
    coarse_start = coarse.clock.total_ticks
    fine.clock.total_ticks = start_tick + NPC_LANE_HOP_TICKS // 3
    fine.npcs.advance_second(fine, (-10, -10))
    restored = NpcFleetSystem.restore(
        fine.npcs.snapshot(), current_tick=fine.clock.total_ticks)
    restored_ship = next(row for row in restored.ships if row["id"] == "syndicate_1")
    assert restored._chart_position(restored_ship, fine.clock.total_ticks) == fine.npcs._chart_position(
        fine_ship, fine.clock.total_ticks)
    fine.npcs = restored

    def assert_same_chart_positions(tick):
        fine_by_id = {row["id"]: row for row in fine.npcs.ships}
        coarse_by_id = {row["id"]: row for row in coarse.npcs.ships}
        assert route_state(fine.npcs) == route_state(coarse.npcs)
        for ship_id in fine_by_id:
            assert fine.npcs._chart_position(fine_by_id[ship_id], tick) == \
                coarse.npcs._chart_position(coarse_by_id[ship_id], tick)

    checkpoints = [coarse_start + (i * NPC_LANE_HOP_TICKS) // 3 for i in range(1, 10)]
    coarse.clock.total_ticks = checkpoints[0]
    coarse.npcs.advance_second(coarse, (-10, -10))

    def route_state(system):
        return [(row["id"], row["node"], row["destination"], deepcopy(row["motion"]))
                for row in system.ships]

    assert_same_chart_positions(checkpoints[0])
    previous = checkpoints[0]
    for tick in checkpoints[1:]:
        for fine_tick in range(previous + 100, tick + 1, 100):
            fine.clock.total_ticks = fine_tick
            fine.npcs.advance_second(fine, (-10, -10))
        coarse.clock.total_ticks = tick
        coarse.npcs.advance_second(coarse, (-10, -10))
        assert_same_chart_positions(tick)
        previous = tick
    assert LOOKOUT_RADIUS == 1.25


def test_save_validation_rejects_invalid_lane_motion_and_lookout_geometry():
    world = quiet_world()
    snapshot = world.npcs.snapshot()
    snapshot["ships"][0]["motion"]["to"] = "freeport"
    with pytest.raises(ValueError, match="lane motion"):
        NpcFleetSystem.restore(snapshot, current_tick=world.clock.total_ticks)

    encounter = {
        "id": "traffic_probe", "name": "Lookout contact", "faction": "unmarked",
        "threat": "merchant", "crew": 3, "condition": 80,
        "chart_position": [3.0, 2.0],
        "relative_observation": {"range_units": 1.0, "bearing_deg": 360.0,
                                 "relative_motion": "closing"},
    }
    with pytest.raises(ValueError, match="relative lookout observation"):
        sea_encounter.validate_sighting(encounter)


def test_legacy_fleet_save_migrates_without_inventing_contact_history():
    world = quiet_world()
    ship = world.npcs.ships[0]
    world.npcs.sighted.add(ship["id"])
    world.npcs.player_contacts[ship["id"]] = {
        "first_hour": world.npcs.hour, "last_hour": world.npcs.hour,
        "hailed": True, "aid_count": 0,
    }
    legacy = world.npcs.snapshot()
    legacy.pop("contact_tracks")
    for row in legacy["ships"]:
        row.pop("motion")

    restored = NpcFleetSystem.restore(
        legacy, current_tick=world.clock.total_ticks)
    assert restored.contact_tracks == {}
    assert restored.player_contacts[ship["id"]] == world.npcs.player_contacts[ship["id"]]
    assert all(row["motion"] is None for row in restored.ships)
