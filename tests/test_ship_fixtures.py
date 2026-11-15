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


def test_dynamic_jobs_and_room_equipment_use_registry():
    world = World()
    bind_for_test(world, world.fixtures.relocated("galley", 78, 27))
    world.ship.jobs = []
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
        rows[0]["z"] = 1
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
