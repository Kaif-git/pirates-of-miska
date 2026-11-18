from pirates.economy import MERCHANTS, PORTS
from pirates.game import World
from omnisim.world import tile as T


def walk_to(world, target, max_steps=1200):
    for _ in range(max_steps):
        route = world.ship.path(world, world.player, target)
        if route == []:
            return
        assert route is not None, f"No ordinary route from {(world.player.x, world.player.y)} to {target}"
        x, y = route[0][:2]
        if world.game_map.tile_id(x, y) == T.DOOR and not world.game_map.doors[y, x]:
            world.toggle_door()
            continue
        assert world.move(x - world.player.x, y - world.player.y)
    raise AssertionError(f"Movement did not reach interaction range of {target}")


def test_every_merchant_is_reachable_through_normal_movement_in_both_ports():
    world = World()
    for port in PORTS:
        assert world.economy.port == port
        for merchant_id, merchant in MERCHANTS.items():
            if merchant["port"] != port:
                continue
            walk_to(world, merchant["at"])
            assert abs(world.player.x - merchant["at"][0]) + abs(world.player.y - merchant["at"][1]) == 1
        walk_to(world, (42, 23))
        if port == "verdant":
            world.economy.travel(world, "cinder")
        else:
            world.economy.travel(world, "verdant")


def test_harbor_route_never_crosses_water_or_occupied_tiles():
    world = World()
    route = world.ship.path(world, world.player, MERCHANTS["ada"]["at"])
    assert route
    occupied = {(entity.x, entity.y) for entity in world.all_entities() if entity is not world.player}
    for x, y, *_ in route:
        assert world.game_map.tile_id(x, y) != T.WATER
        assert (x, y) not in occupied
    assert all(abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1
               for a, b in zip([(world.player.x, world.player.y), *route], route))


def test_closed_harbor_door_is_routeable_but_locked_ship_door_is_not():
    world = World()
    harbor_door = next((x, y) for y in range(45) for x in range(45)
                       if world.game_map.tile_id(x, y) == T.DOOR)
    interior = (25, 15)
    route = world.ship._world_path(world, world.player, interior, exact=True)
    assert route is not None
    local = (harbor_door[0] - 69, harbor_door[1] - 12, 0)
    world.locked_doors.add(local)
    assert world.ship._world_path(world, world.player, interior, exact=True) is None
