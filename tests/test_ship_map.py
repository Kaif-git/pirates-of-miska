import json

import numpy as np
import pytest

from omnisim.world import tile as T
from pirates.game import World
from pirates.ship_map import ORIGIN, WIDTH, HEIGHT, project


def test_starter_projection_preserves_map():
    world = World()
    before = world.game_map.tiles.copy()
    project(world.ship_model, world.ship_fixtures, world.game_map)
    assert np.array_equal(before, world.game_map.tiles)
    assert world.ship_model.mass_summary()["structural_mass_kg"] > 0


def test_wall_dismantle_rebuild_and_save(tmp_path):
    world = World()
    wall = next(c for c in world.ship_model.at(1, 8, 0) if c.kind == "wall")
    world.ship_model.remove(wall.id)
    project(world.ship_model, world.ship_fixtures, world.game_map)
    x, y = ORIGIN[0] + 1, ORIGIN[1] + 8
    assert world.game_map.is_walkable(x, y)
    path = tmp_path / "captain.json"
    world.save(path)
    restored = World.load(path)
    assert restored.game_map.is_walkable(x, y)
    assert restored.ship_model.snapshot() == world.ship_model.snapshot()
    restored.ship_model.place(1, 8, 0, "wall")
    project(restored.ship_model, restored.ship_fixtures, restored.game_map)
    assert not restored.game_map.is_walkable(x, y)


def test_legacy_save_migrates(tmp_path):
    world = World()
    path = tmp_path / "captain.json"
    world.save(path)
    data = json.loads(path.read_text())
    data["version"] = 2
    del data["ship_model"]
    path.write_text(json.dumps(data))
    restored = World.load(path)
    assert restored.ship_model.snapshot() == world.ship_model.snapshot()


def test_bad_projection_is_atomic():
    world = World()
    before = world.game_map.tiles.copy()
    world.ship_model.place(WIDTH, HEIGHT, 0, "floor")
    with pytest.raises(ValueError):
        project(world.ship_model, world.ship_fixtures, world.game_map)
    assert np.array_equal(before, world.game_map.tiles)


def test_invalid_saved_geometry_rejected(tmp_path):
    world = World()
    path = tmp_path / "captain.json"
    world.save(path)
    data = json.loads(path.read_text())
    data["ship_model"]["components"][0]["z"] = 99
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        World.load(path)


def test_removed_door_loses_open_state():
    world = World()
    door = next(c for c in world.ship_model.components if c.kind == "door")
    x, y = ORIGIN[0] + door.x, ORIGIN[1] + door.y
    world.game_map.doors[y, x] = True
    world.ship_model.remove(door.id)
    project(world.ship_model, world.ship_fixtures, world.game_map)
    assert world.game_map.tiles[y, x] == T.FLOOR
    assert not world.game_map.doors[y, x]
