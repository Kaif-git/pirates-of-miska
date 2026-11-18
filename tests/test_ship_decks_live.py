import pytest

from pirates.game import World
from pirates.ship_map import ORIGIN


def add_upper_run(world):
    # Three unobstructed cells on the starter deck, chosen away from fixtures.
    start = (3, 2)
    for x in range(start[0], start[0] + 3):
        world.ship_model.place(x, start[1], 1, "floor")
    stair = world.ship_model.place(start[0], start[1], 0, "stair")
    door = world.ship_model.place(start[0] + 1, start[1], 1, "door")
    return start, stair, door


def test_captain_changes_deck_only_at_built_stair_and_renders_that_deck():
    world = World()
    start, _, _ = add_upper_run(world)
    world.player.x, world.player.y = start[0] + ORIGIN[0], start[1] + ORIGIN[1]
    world.player.z = 0
    world.set_active_deck(1)
    assert world.game_map.is_walkable(world.player.x, world.player.y)
    assert world.move_vertical(1)
    assert world.player.z == world.active_deck == 1
    assert world.game_map.is_walkable(world.player.x, world.player.y)
    assert world.move_vertical(-1)
    assert world.player.z == world.active_deck == 0
    world.player.x += 1  # no stair connector at this adjacent cell
    assert not world.move_vertical(1)
    assert world.player.z == 0


def test_captain_cannot_enter_an_occupied_stair_landing():
    world = World()
    start, _, _ = add_upper_run(world)
    wx, wy = start[0] + ORIGIN[0], start[1] + ORIGIN[1]
    world.player.x, world.player.y = wx, wy
    sailor = world.ship.crew[0]
    sailor.x, sailor.y, sailor.z = wx, wy, 1
    assert not world.move_vertical(1)
    assert world.player.z == 0


def test_deck_door_states_and_player_elevation_survive_save_load(tmp_path):
    world = World()
    start, _, upper_door = add_upper_run(world)
    sx, sy = start[0] + ORIGIN[0], start[1] + ORIGIN[1]
    world.player.x, world.player.y = sx, sy
    world.set_active_deck(1)
    world.game_map.doors[sy, sx + 1] = True
    world.player.z = 1
    path = tmp_path / "upper-deck.json"
    world.save(path)
    restored = World.load(path)
    assert restored.player.z == restored.active_deck == 1
    assert restored.game_map.tiles[sy, sx + 1] != 0
    assert restored.game_map.doors[sy, sx + 1]
    assert restored.move_vertical(-1)
    assert restored.active_deck == 0
    assert not restored.game_map.doors[sy, sx + 1]
    assert restored.move_vertical(1)
    assert restored.game_map.doors[sy, sx + 1]


def test_lockdown_closes_and_persists_locks_on_all_decks(tmp_path):
    world = World()
    start, _, upper_door = add_upper_run(world)
    wx, wy = start[0] + ORIGIN[0], start[1] + ORIGIN[1]
    world.player.x, world.player.y, world.player.z = wx, wy, 1
    world.set_active_deck(1)
    world.set_lockdown(True)
    assert world.lockdown
    assert (upper_door.x, upper_door.y, upper_door.z) in world.locked_doors
    assert not world.game_map.doors[wy, wx + 1]
    world.toggle_door()
    assert not world.game_map.doors[wy, wx + 1]
    world.toggle_adjacent_door_lock()  # Individual override is legal during a global lockdown.
    assert (upper_door.x, upper_door.y, upper_door.z) not in world.locked_doors
    path = tmp_path / "ship-lockdown.json"
    world.save(path)
    restored = World.load(path)
    assert restored.lockdown
    assert restored.locked_doors == world.locked_doors
    assert (upper_door.x, upper_door.y, upper_door.z) not in restored.locked_doors
    restored.set_lockdown(False)
    assert not restored.locked_doors


def test_boarding_route_forces_locked_doors_only_when_no_unlocked_route_exists():
    from pirates.ship_boarding import route_boarding_party

    world = World()
    start, _, door = add_upper_run(world)
    world.player.x, world.player.y, world.player.z = start[0] + ORIGIN[0], start[1] + ORIGIN[1], 1
    world.set_active_deck(1)
    world.set_lockdown(True)
    initial_ticks = world.clock.total_ticks
    result = route_boarding_party(world, (3, 2, 1), (5, 2, 1), seconds_per_door=2)
    assert result["path"] == ((3, 2, 1), (4, 2, 1), (5, 2, 1))
    assert result["breached"] == ((door.x, door.y, door.z),)
    assert result["delay_seconds"] == 2
    assert world.clock.total_ticks == initial_ticks + 200
    assert not any(c.id == door.id for c in world.ship_model.components)
    assert (door.x, door.y, door.z) not in world.locked_doors


def test_boarders_take_unlocked_alternate_route_without_damaging_locks():
    from pirates.ship_boarding import route_boarding_party

    world = World()
    start, _, door = add_upper_run(world)
    # Add a three-tile bypass around the locked center door.
    for at in ((3, 1, 1), (4, 1, 1), (5, 1, 1)):
        world.ship_model.place(*at, "floor")
    world.player.x, world.player.y, world.player.z = start[0] + ORIGIN[0], start[1] + ORIGIN[1], 1
    world.set_active_deck(1)
    world.set_lockdown(True)
    result = route_boarding_party(world, (3, 2, 1), (5, 2, 1))
    assert result["path"] is not None
    assert not result["breached"]
    assert any(c.id == door.id for c in world.ship_model.components)


def test_crew_routes_respect_locked_barriers():
    world = World()
    start, _, door = add_upper_run(world)
    sailor = world.ship.crew[0]
    sailor.x, sailor.y, sailor.z = start[0] + ORIGIN[0], start[1] + ORIGIN[1], 1
    world.locked_doors.add((door.x, door.y, door.z))
    destination = (start[0] + ORIGIN[0] + 2, start[1] + ORIGIN[1], 1)
    assert world.ship.path(world, sailor, destination) is None


def test_crew_physically_routes_from_upper_deck_to_lower_worksite():
    world = World()
    start, _, _ = add_upper_run(world)
    sailor = world.ship.crew[0]
    sailor.x, sailor.y, sailor.z = start[0] + ORIGIN[0] + 2, start[1] + ORIGIN[1], 1
    target = (start[0] + ORIGIN[0] + 3, start[1] + ORIGIN[1])
    path = world.ship.path(world, sailor, target)
    assert path is not None and any(at[2] == 0 for at in path)
    assert any(at[2] == 1 for at in path)
    for _ in range(20):
        if sailor.z == 0:
            break
        world.ship.walk(world, sailor, target)
    assert sailor.z == 0
    assert (sailor.x, sailor.y) != (target[0], target[1])


def test_crew_elevation_roundtrips_and_inactive_deck_crew_are_hidden(tmp_path):
    world = World()
    start, _, _ = add_upper_run(world)
    sailor = world.ship.crew[0]
    sailor.x, sailor.y, sailor.z = start[0] + ORIGIN[0] + 2, start[1] + ORIGIN[1], 1
    assert sailor not in world.entities()
    path = tmp_path / "crew-above.json"
    world.save(path)
    restored = World.load(path)
    saved_sailor = restored.ship.sailor(sailor.id)
    assert (saved_sailor.x, saved_sailor.y, saved_sailor.z) == (sailor.x, sailor.y, 1)
    assert saved_sailor not in restored.entities()


def test_ground_items_drop_pickup_render_key_and_save_on_upper_deck(tmp_path):
    world = World()
    start, _, _ = add_upper_run(world)
    local_x, local_y = start[0] + 2, start[1]
    wx, wy = local_x + ORIGIN[0], local_y + ORIGIN[1]
    world.player.x, world.player.y, world.player.z = wx, wy, 1
    world.set_active_deck(1)
    item = {"id": "upper-rope", "name": "Upper-deck rope", "quantity": 1,
            "weight_g": 1500, "bulk_ml": 2500}
    assert world.inventory.add(item)
    world.drop(0)
    assert world.ground_items[(wx, wy, 1)][0]["id"] == "upper-rope"
    assert (wx, wy) in world.ground
    path = tmp_path / "upper-ground.json"
    world.save(path)
    restored = World.load(path)
    assert restored.ground_items[(wx, wy, 1)][0]["id"] == "upper-rope"
    assert (wx, wy) in restored.ground
    restored.pickup()
    assert (wx, wy, 1) not in restored.ground_items
    assert any(row["id"] == "upper-rope" for row in restored.inventory.items)


def test_save_rejects_actor_elevation_without_deck_floor(tmp_path):
    world = World()
    sailor = world.ship.crew[0]
    sailor.z = 1
    path = tmp_path / "bad-elevation.json"
    world.save(path)
    with pytest.raises(ValueError, match="crew position"):
        World.load(path)
