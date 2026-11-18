import json

import numpy as np
import pytest

from omnisim.world import tile as T
from pirates.game import World
from pirates.ship_fixtures import FixtureLayout
from pirates.ship_map import project
from pirates.ship_spaces import report_rows


def bind_for_test(world, layout):
    # Fixture jobs will own gameplay transitions; this helper assembles test worlds.
    project(world.ship_model, layout.overlay, world.game_map)
    world.fixtures = layout
    world.ship_fixtures = layout.overlay
    world.ship.layout = layout
    world.refresh_fixture_objects()


def test_starter_registry_preserves_authored_tiles():
    world = World()
    before = world.game_map.tiles.copy()
    project(world.ship_model, world.fixtures.overlay, world.game_map)
    assert np.array_equal(before, world.game_map.tiles)
    assert world.ship.stations["hold"] == (88, 40)
    assert world.ship.bunks[0] == (72, 39)
    assert len({f.id for f in world.fixtures.fixtures}) == len(world.fixtures.fixtures)
    assert world.fixtures.get("medicine_chest") is not None


def test_dynamic_jobs_and_room_equipment_use_registry():
    world = World()
    bind_for_test(world, world.fixtures.relocated("galley", 78, 27))
    world.ship.jobs = []
    for key in ("fruit_g", "dried_fish_g", "biscuit_g"):
        world.ship.stores[key] = 0
    world.ship.generate_jobs()
    job = next(j for j in world.ship.jobs if j.kind == "cook")
    assert job.target == (78, 27)
    assert world.game_map.tile_id(78, 27) == T.HEARTH
    assert world.game_map.tile_id(72, 36) == T.FLOOR
    world.spaces.designate(world, 76, 25, "galley")
    assert any("Galley: installed station present" == row for row in report_rows(world, 76, 25))
    assert any(o.get("fixture_id") == "galley" for o in world.objects_at[78, 27])
    assert not any(o.get("fixture_id") == "galley" for o in world.objects_at.get((72, 36), []))


def test_relocated_bunk_retains_assigned_identity_and_save(tmp_path):
    world = World()
    bind_for_test(world, world.fixtures.relocated("bunk:0", 78, 38))
    assert world.ship.bunks[world.ship.crew[0].bunk] == (78, 38)
    assert world.game_map.tile_id(72, 39) == T.FLOOR
    assert world.game_map.tile_id(78, 38) == T.BED
    path = tmp_path / "captain.json"
    world.save(path)
    restored = World.load(path)
    assert restored.fixtures.snapshot() == world.fixtures.snapshot()
    assert restored.ship.bunks[0] == (78, 38)
    assert restored.ship.layout is restored.fixtures
    assert restored.game_map.tile_id(78, 38) == T.BED


def test_older_v3_without_registry_migrates(tmp_path):
    world = World()
    path = tmp_path / "captain.json"
    world.save(path)
    data = json.loads(path.read_text())
    del data["fixtures"]
    path.write_text(json.dumps(data))
    assert World.load(path).fixtures.snapshot() == world.fixtures.snapshot()


def test_infirmary_designation_and_installed_medical_chest_provide_healing():
    world = World()
    chest = world.fixtures.get("medicine_chest")
    wx, wy = chest.x + 69, chest.y + 12
    assert world.spaces.find(world.ship_model, wx, wy) is not None
    world.spaces.designate(world, wx, wy, "infirmary")
    rows = report_rows(world, wx, wy)
    assert any("medicine chest installed" in row for row in rows)
    sailor = world.ship.crew[0]
    sailor.health = 60
    assert world.ship.choose_need(sailor, 12, world) == "infirmary"
    sailor.x, sailor.y, sailor.z = 77, 25, 0
    for _ in range(20):
        world.ship.satisfy(world, sailor, "infirmary")
        if sailor.health > 60:
            break
    assert sailor.health > 60
    assert sailor.activity == "Receiving infirmary care"


def test_infirmary_recovery_advances_a_persistent_injury_to_resolved():
    world = World()
    chest = world.fixtures.get("medicine_chest")
    wx, wy = chest.x + 69, chest.y + 12
    world.spaces.designate(world, wx, wy, "infirmary")
    sailor = world.ship.crew[0]
    sailor.x, sailor.y, sailor.z = 77, 25, 0
    injury = sailor.suffer_injury("burn", 1, "test fire")
    assert injury is not None
    assert world.ship.choose_need(sailor, 12, world) == "infirmary"
    for _ in range(450):
        world.ship.satisfy(world, sailor, "infirmary")
    assert sailor.injuries[0]["recovery"] == 100
    assert any("Recovered from burn" in thought for thought in sailor.thoughts)


def test_older_fixture_snapshot_migrates_missing_medicine_chest():
    world = World()
    rows = world.fixtures.snapshot()
    rows = [row for row in rows if row["id"] not in ("medicine_chest", "brig_cage")]
    restored = FixtureLayout.restore(rows, world.fixtures)
    assert restored.get("medicine_chest") == world.fixtures.get("medicine_chest")
    assert restored.get("brig_cage") == world.fixtures.get("brig_cage")


def test_pre_medicine_chest_save_load_adds_default_equipment(tmp_path):
    world = World()
    path = tmp_path / "pre-medicine-chest.json"
    world.save(path)
    data = json.loads(path.read_text())
    data["fixtures"] = [row for row in data["fixtures"]
                        if row["id"] not in ("medicine_chest", "brig_cage")]
    path.write_text(json.dumps(data))
    restored = World.load(path)
    assert restored.fixtures.get("medicine_chest") is not None
    assert restored.fixtures.get("brig_cage") is not None
    assert restored.ship.layout is restored.fixtures


def test_magazine_and_brig_reports_use_live_ship_state_not_room_bonuses():
    world = World()
    cannon = world.fixtures.get("port_cannon")
    cannon_xy = (cannon.x + 69, cannon.y + 12)
    world.spaces.designate(world, *cannon_xy, "magazine")
    rows = report_rows(world, *cannon_xy)
    assert any("cannon station(s)" in row and "loaded charge(s)" in row for row in rows)
    brig_cell = next((c.x + 69, c.y + 12) for c in world.ship_model.components
                     if c.kind == "floor" and world.spaces.find(world.ship_model, c.x + 69, c.y + 12))
    world.spaces.designate(world, *brig_cell, "brig")
    assert any("MISSING restraint cage" in row for row in report_rows(world, *brig_cell))


def test_brig_requires_its_designation_equipment_and_locked_boundaries(tmp_path):
    from types import SimpleNamespace

    world = World()
    cage = world.fixtures.get("brig_cage")
    at = (cage.x + 69, cage.y + 12)
    world.spaces.designate(world, *at, "brig")
    room = world.spaces.find(world.ship_model, *at)
    assert room.doors
    enemy = SimpleNamespace(id="raider_1", name="Rogue boarder", faction="pirates")
    assert world.ship.capture_boarders([enemy], world) == ()
    world.set_lockdown(True)
    assert world.ship.brig_ready(world)
    captured = world.ship.capture_boarders([enemy], world)
    assert captured == ("captured:raider_1",)
    assert "1/3 captive berths" in " ".join(report_rows(world, *at))
    path = tmp_path / "brig-save.json"
    world.save(path)
    restored = World.load(path)
    assert restored.ship.captives == world.ship.captives
    assert restored.ship.release_captive(captured[0])["name"] == "Rogue boarder"
    assert restored.ship.captives == []


@pytest.mark.parametrize("change", ["duplicate", "identity", "offdeck", "bool", "deck", "missing"])
def test_reject_bad_registry(change):
    world = World()
    rows = world.fixtures.snapshot()
    if change == "duplicate":
        rows[1] = dict(rows[0])
    elif change == "identity":
        rows[0]["id"] = "unknown"
    elif change == "offdeck":
        rows[0]["x"] = 999
    elif change == "bool":
        rows[0]["x"] = True
    elif change == "deck":
        rows[0]["z"] = 9
    else:
        rows.pop()
    with pytest.raises(ValueError):
        FixtureLayout.restore(rows, world.fixtures)


def test_no_overlap_or_wall_placement():
    world = World()
    with pytest.raises(ValueError):
        world.fixtures.relocated("galley", *world.ship.stations["hold"])
    layout = world.fixtures.relocated("galley", 78, 20)
    before = world.game_map.tiles.copy()
    with pytest.raises(ValueError):
        project(world.ship_model, layout.overlay, world.game_map)
    assert np.array_equal(before, world.game_map.tiles)
