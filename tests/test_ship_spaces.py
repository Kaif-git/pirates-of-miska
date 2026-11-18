import json

import pytest

from pirates.game import World
from pirates.ship_map import ORIGIN, project
from pirates.ship_model import ShipModel
from pirates.ship_spaces import ShipSpaces, report_rows


def divided_model():
    model = ShipModel()
    for y in range(3):
        for x in range(5):
            model.place(x, y, 0, "floor")
    for y in range(3):
        model.place(2, y, 0, "door" if y == 1 else "wall")
    return model


def test_geometry_splits_at_walls_and_doors():
    spaces = ShipSpaces().geometry(divided_model())
    assert len(spaces) == 2
    assert {len(s.cells) for s in spaces} == {6}
    assert all(s.doors == {(2, 1)} for s in spaces)
    assert all(s.exposed for s in spaces)


def test_zones_survive_merge_split_and_temporary_wall():
    world = World()
    world.ship_model = divided_model()
    ox, oy = ORIGIN
    world.spaces.designate(world, ox, oy, "berths")
    world.spaces.designate(world, ox + 4, oy, "galley")
    door = next(c for c in world.ship_model.components if c.kind == "door")
    world.ship_model.remove(door.id)
    merged = world.spaces.find(world.ship_model, ox, oy)
    assert world.spaces.purposes(merged) == ("berths", "galley")
    # Designating a boundary does not silently label the former doorway.
    assert (2, 1) not in world.spaces.zones
    world.ship_model.place(2, 1, 0, "door")
    assert world.spaces.purposes(world.spaces.find(world.ship_model, ox, oy)) == ("berths",)
    assert world.spaces.purposes(world.spaces.find(world.ship_model, ox + 4, oy)) == ("galley",)
    temporary = world.ship_model.place(0, 0, 0, "wall")
    saved = world.spaces.snapshot()
    world.spaces = ShipSpaces.restore(saved, world.ship_model)
    world.ship_model.remove(temporary.id)
    assert world.spaces.zones[0, 0] == "berths"


def test_door_open_state_does_not_merge_spaces():
    world = World()
    before = world.spaces.geometry(world.ship_model)
    door = next(c for c in world.ship_model.components if c.kind == "door")
    world.game_map.doors[door.y + ORIGIN[1], door.x + ORIGIN[0]] = True
    assert world.spaces.geometry(world.ship_model) == before


def test_report_checks_actual_equipment_not_label():
    world = World()
    world.spaces.designate(world, 76, 25, "galley")
    rows = report_rows(world, 76, 25)
    assert any("MISSING installed station" in row for row in rows)
    assert any("Hold ->" in row for row in rows)
    world.spaces.designate(world, 76, 25, "infirmary")
    assert any("MISSING medicine chest" in row for row in report_rows(world, 76, 25))


def test_actual_room_changes_after_wall_removed():
    world = World()
    before = world.spaces.find(world.ship_model, 76, 25)
    component = next(c for c in world.ship_model.at(1, 8, 0) if c.kind == "wall")
    world.ship_model.remove(component.id)
    project(world.ship_model, world.ship_fixtures, world.game_map)
    after = world.spaces.find(world.ship_model, 76, 25)
    assert len(after.cells) > len(before.cells)


def test_save_reload_and_old_save_without_zones(tmp_path):
    world = World()
    ticks = world.clock.total_ticks
    world.spaces.designate(world, 76, 25, "working deck")
    assert world.clock.total_ticks == ticks
    path = tmp_path / "captain.json"
    world.save(path)
    restored = World.load(path)
    assert restored.spaces.snapshot() == world.spaces.snapshot()
    assert restored.room_name(76, 25) == "Working Deck"
    data = json.loads(path.read_text())
    del data["room_zones"]
    path.write_text(json.dumps(data))
    assert World.load(path).spaces.snapshot() == []


@pytest.mark.parametrize("rows", [
    {}, [{"x": True, "y": 1, "purpose": "brig"}],
    [{"x": 999, "y": 1, "purpose": "brig"}],
    [{"x": 1, "y": 1, "purpose": "unknown"}],
    [{"x": 1, "y": 1, "purpose": "brig"}] * 2,
])
def test_invalid_zone_saves(rows):
    with pytest.raises(ValueError):
        ShipSpaces.restore(rows, divided_model())


def test_clear_and_doorway_rejected():
    world = World()
    world.spaces.designate(world, 76, 25, "hold")
    world.spaces.designate(world, 76, 25, None)
    assert world.spaces.snapshot() == []
    with pytest.raises(ValueError):
        world.spaces.designate(world, 76, 20, "brig")
    with pytest.raises(ValueError):
        world.spaces.designate(world, 30, 23, "brig")


def test_room_menu_designates_without_time():
    from pirates.management import room_menu

    class Renderer:
        choices = iter([1, 1, None])  # designate -> berths -> exit

        def render_list(self, *args, **kwargs):
            pass

        def choose(self, count):
            return next(self.choices)

    world = World()
    ticks = world.clock.total_ticks
    room_menu(Renderer(), world)
    assert world.clock.total_ticks == ticks
    assert world.room_name(world.player.x, world.player.y) == "Berths"
