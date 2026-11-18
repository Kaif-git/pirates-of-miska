"""Coarse boarding-route resolution against real locked ship doors."""

from pirates.ship_navigation import route
from pirates.ship_map import ORIGIN, project


def route_boarding_party(world, start, goal, seconds_per_door=30):
    """Route a boarding party through the built deck, forcing locks only if needed.

    Positions are ship-local ``(x, y, z)``. An intact locked door blocks the
    preferred route. If no alternate route exists, boarders spend time forcing
    the locked doors on the shortest fallback route, then remove those actual
    door components; locks delay entry, they are not force-fields.
    """
    if type(seconds_per_door) is not int or seconds_per_door < 0:
        raise ValueError("Breach delay must be a nonnegative integer")
    locked = set(world.locked_doors)
    path = route(world.ship_model, start, goal, locked)
    if path is not None:
        return {"path": tuple(path), "breached": (), "delay_seconds": 0}
    path = route(world.ship_model, start, goal)
    if path is None:
        return {"path": None, "breached": (), "delay_seconds": None}
    doors = {(c.x, c.y, c.z): c for c in world.ship_model.components if c.kind == "door"}
    breached = tuple(at for at in path if at in locked and at in doors)
    if breached and seconds_per_door:
        world.advance(seconds_per_door * len(breached) * 100)
    for at in breached:
        component = doors[at]
        world.ship_model.remove(component.id)
        world.locked_doors.discard(at)
        wx, wy = at[0] + ORIGIN[0], at[1] + ORIGIN[1]
        world.door_state(at[2])[wy, wx] = False
        if at[2] == world.active_deck:
            project(world.ship_model, world.fixtures.overlay_for(at[2]), world.game_map, at[2])
            world.ship_fixtures = world.fixtures.overlay_for(at[2])
    if breached:
        world.refresh()
        world.message(f"Boarders forced {len(breached)} locked door(s); the barriers are destroyed.")
    return {"path": tuple(path), "breached": breached,
            "delay_seconds": seconds_per_door * len(breached)}


def breach_hull_for_boarders(world, seconds_per_door=30):
    """Choose a reachable outer hull tile and resolve the raiders' entry route."""
    from pirates.ship_hazards import ShipHazards

    goal = (world.player.x - ORIGIN[0], world.player.y - ORIGIN[1], world.player.z)
    floors = {(c.x, c.y, c.z) for c in world.ship_model.components if c.kind == "floor"}
    if goal not in floors:
        candidates = sorted(floors)
        if not candidates:
            return None
        goal = min(candidates, key=lambda at: (abs(at[0] + ORIGIN[0] - world.player.x)
                                                + abs(at[1] + ORIGIN[1] - world.player.y), at))
    walls = [c for c in world.ship_model.components if c.kind == "wall"
             and ShipHazards._outer_wall(world, c.x, c.y, c.z)]
    candidates = [(len(path), component) for component in walls
                  if (path := route(world.ship_model, (component.x, component.y, component.z), goal))]
    if not candidates:
        return None
    _, wall = min(candidates, key=lambda row: (row[0], row[1].z, row[1].y, row[1].x))
    world.hazards.breach(world, wall.x, wall.y, wall.z, rate_kg_s=0.45)
    entry = (wall.x, wall.y, wall.z)
    route_result = route_boarding_party(world, entry, goal, seconds_per_door)
    return {"entry": entry, **route_result}
