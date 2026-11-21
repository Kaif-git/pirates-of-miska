"""Small walkable transfer deck for one consented sea encounter at a time."""
from __future__ import annotations

from collections import deque

from omnisim.world import tile as T


DECK_BOUNDS = (58, 19, 68, 29)  # left, top, right-exclusive, bottom-exclusive
DECK_INTERIOR = (59, 20, 67, 28)
DECK_CAPACITY = 6
LANDING_CELLS = ((66, 23), (65, 23), (66, 22), (66, 24),
                 (65, 22), (65, 24))
RETURN_CELLS = ((69, 23), (69, 24), (70, 23), (70, 24),
                (71, 23), (71, 24), (70, 22), (70, 25))
GANGPLANK = (68, 23)


def in_deck(x, y):
    left, top, right, bottom = DECK_INTERIOR
    return (left <= x < right and top <= y < bottom) or (x, y) in ((67, 23), GANGPLANK)


def in_home_ship(x, y, z=0):
    return z == 0 and 69 <= x < 92 and 12 <= y < 44


def apply_deck(world):
    """Lay a bounded exterior NPC deck and connected gangplank over open sea."""
    left, top, right, bottom = DECK_BOUNDS
    tiles = world.game_map.tiles
    tiles[top:bottom, left:right] = T.WATER
    tiles[top, left:right] = T.WALL_WOOD
    tiles[bottom - 1, left:right] = T.WALL_WOOD
    tiles[top:bottom, left] = T.WALL_WOOD
    tiles[top:bottom, right - 1] = T.WALL_WOOD
    interior_left, interior_top, interior_right, interior_bottom = DECK_INTERIOR
    tiles[interior_top:interior_bottom, interior_left:interior_right] = T.FLOOR
    # The right-hand hull opening is connected to the Wayward's built port
    # gangway. The plank and doorway are ordinary walkable map cells.
    tiles[23, right - 1] = T.FLOOR
    tiles[GANGPLANK[1], GANGPLANK[0]] = T.FLOOR
    world.game_map.doors[top:bottom, left:right] = False
    world.game_map.doors[GANGPLANK[1], GANGPLANK[0]] = False


def clear_deck(world):
    """Remove only the encounter deck overlay; at-sea terrain is open water."""
    left, top, right, bottom = DECK_BOUNDS
    world.game_map.tiles[top:bottom, left:right] = T.WATER
    world.game_map.doors[top:bottom, left:right] = False
    world.refresh()


def actor_map(world):
    return {actor.id: actor for actor in [world.player, *world.ship.crew]}


def reachable(world, actor, target, moving_ids):
    """Check a four-way, same-deck path while ignoring the moving party."""
    if getattr(actor, "z", 0) != 0:
        return False
    start = (actor.x, actor.y)
    target = tuple(target)
    if start == target:
        return True
    moving_ids = set(moving_ids)
    occupied = {
        (other.x, other.y)
        for other in world.all_entities()
        if other is not actor and getattr(other, "id", None) not in moving_ids
        and getattr(other, "z", 0) == 0
    }

    def passable(at):
        x, y = at
        if (at in occupied or not world.game_map.in_bounds(x, y)
                or world.game_map.tile_id(x, y) == T.WATER):
            return False
        if world.game_map.tile_id(x, y) == T.DOOR:
            return (x - 69, y - 12, 0) not in world.locked_doors
        return world.game_map.is_walkable(x, y)

    if not passable(target):
        return False
    todo = deque([start])
    seen = {start}
    while todo:
        x, y = todo.popleft()
        for nxt in ((x, y - 1), (x + 1, y), (x, y + 1), (x - 1, y)):
            if nxt == target:
                return True
            if nxt not in seen and passable(nxt):
                seen.add(nxt)
                todo.append(nxt)
    return False
