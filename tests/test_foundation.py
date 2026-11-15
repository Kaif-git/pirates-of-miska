import json
import sys
from collections import deque
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "engine")]

from pirates.game import World
from omnisim.ui.ascii_renderer import MovementRepeater, world_action_for_key, compute_camera
from tcod.event import KeySym


def harbor_world():
    world = World()
    world.player.x, world.player.y = 30, 23
    world.refresh()
    return world


def test_movement_collision_and_time():
    world = harbor_world()
    before = world.clock.total_ticks
    assert world.move(1, 0)
    assert world.clock.total_ticks == before + 100
    world.player.x, world.player.y = 44, 30
    before = world.clock.total_ticks
    assert not world.move(1, 0)
    assert world.clock.total_ticks == before
    assert not world.move(-1000, 0)


def test_ship_is_connected_to_port():
    world = harbor_world()
    seen = {(world.player.x, world.player.y)}
    todo = deque(seen)
    while todo:
        x, y = todo.popleft()
        for dx, dy in ((0, 1), (1, 0), (0, -1), (-1, 0)):
            at = x + dx, y + dy
            if at not in seen and world.game_map.is_walkable(*at):
                seen.add(at)
                todo.append(at)
    assert (75, 25) in seen
    assert (45, 30) not in seen  # sea


def test_door_and_fog():
    world = harbor_world()
    world.player.x, world.player.y = 25, 20
    world.refresh()
    assert not world.game_map.is_walkable(25, 19)
    world.toggle_door()
    assert world.game_map.is_walkable(25, 19)
    assert world.move(0, -1)
    world.toggle_door()
    assert world.game_map.is_walkable(25, 19)  # cannot close on yourself
    assert world.game_map.visible[19, 25]
    assert not world.game_map.explored[0, 99]


def test_pickup_drop_and_load_cost():
    world = harbor_world()
    world.move(1, 0)
    world.pickup()
    assert len(world.inventory) == 2
    assert (31, 23) not in world.ground_items
    before = world.clock.total_ticks
    world.move(1, 0)
    assert world.clock.total_ticks > before + 100
    world.drop(0)
    assert len(world.inventory) == 1
    assert world.ground_items[(32, 23)][0]["id"] == "rope"


def test_full_pack_never_deletes_ground_items():
    world = harbor_world()
    world.player.x = 31
    world.inventory.pockets["satchel"]["max_weight_g"] = 0
    world.pickup()
    assert len(world.ground_items[(31, 23)]) == 2
    assert not world.inventory


def test_save_roundtrip_and_atomic_validation(tmp_path):
    world = harbor_world()
    world.move(1, 0)
    world.pickup()
    path = tmp_path / "captain.json"
    world.save(path)
    restored = World.load(path)
    assert restored.player.x == 31
    assert restored.inventory.items == world.inventory.items
    assert restored.ground_items == world.ground_items
    assert restored.clock.total_ticks == world.clock.total_ticks
    assert (restored.game_map.explored == world.game_map.explored).all()
    data = json.loads(path.read_text())
    data["player"] = [99, 0]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        World.load(path)
    assert world.player.x == 31


@pytest.mark.parametrize("field,value", [("ticks", -1), ("version", 999), ("doors", [])])
def test_bad_save_rejected(tmp_path, field, value):
    path = tmp_path / "captain.json"
    World().save(path)
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        World.load(path)


def test_copied_controls_and_repeater():
    assert world_action_for_key(KeySym.UP) == ("move", 0, -1)
    assert world_action_for_key(KeySym.H) == ("move", -1, 0)
    assert world_action_for_key(KeySym.MINUS) == ("zoom", -1)
    repeater = MovementRepeater()
    action = ("move", 1, 0)
    assert repeater.press(KeySym.RIGHT, action, 0) == action
    assert repeater.poll(0.1) is None
    assert repeater.poll(0.2) == action
    repeater.release(KeySym.RIGHT)
    assert repeater.poll(1) is None
    assert compute_camera(100, 64, 40, 20, 99, 63)[:2] == (60, 44)


def test_copied_source_matches_manifest():
    import hashlib
    manifest = json.loads((ROOT / "engine-origin.json").read_text(encoding="utf-8-sig"))
    for entry in manifest["files"]:
        assert hashlib.sha256((ROOT / entry["path"]).read_bytes()).hexdigest().upper() == entry["sha256"]
