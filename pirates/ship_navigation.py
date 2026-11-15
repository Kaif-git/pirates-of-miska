"""Structural deck-to-deck route graph; routes exist only across built stairs."""
from collections import deque


def route(model, start, goal):
    """Find a shortest walking route through built floors and stair connectors.

    Positions are ship-local ``(x, y, z)`` coordinates. Ordinary doors are
    routeable apertures (callers must open them at traversal); solid walls block
    horizontal travel. There is deliberately no implicit vertical teleport.
    """
    if (not isinstance(start, (tuple, list)) or not isinstance(goal, (tuple, list))
            or len(start) != 3 or len(goal) != 3
            or any(type(v) is not int for v in (*start, *goal))):
        raise ValueError("Navigation positions must be integer (x, y, deck) triples")
    start, goal = tuple(start), tuple(goal)
    floors = {(c.x, c.y, c.z) for c in model.components if c.kind == "floor"}
    if start not in floors or goal not in floors:
        return None
    walls = {(c.x, c.y, c.z) for c in model.components if c.kind == "wall"}
    doors = {(c.x, c.y, c.z) for c in model.components if c.kind == "door"}
    stairs = {(c.x, c.y, c.z) for c in model.components if c.kind == "stair"}
    parent = {start: None}
    todo = deque([start])
    while todo:
        x, y, z = todo.popleft()
        if (x, y, z) == goal:
            path = []
            current = goal
            while current is not None:
                path.append(current)
                current = parent[current]
            return list(reversed(path))
        neighbors = [(x + dx, y + dy, z) for dx, dy in
                     ((0, -1), (1, 0), (0, 1), (-1, 0))]
        if (x, y, z) in stairs:
            neighbors.append((x, y, z + 1))
        if (x, y, z - 1) in stairs:
            neighbors.append((x, y, z - 1))
        for at in neighbors:
            if at not in parent and at in floors and at not in walls:
                parent[at] = (x, y, z)
                todo.append(at)
    return None


def traversed_doors(model, path):
    """Return door coordinates on a route, preserving route order."""
    doors = {(c.x, c.y, c.z) for c in model.components if c.kind == "door"}
    return tuple(at for at in path or () if at in doors)


def route_to_worksite(model, start, target):
    """Route to a reachable cardinal work position beside a 3D target tile."""
    if (not isinstance(target, (tuple, list)) or len(target) != 3
            or any(type(v) is not int for v in target)):
        raise ValueError("Work targets must be integer (x, y, deck) triples")
    x, y, z = target
    candidates = [(x + dx, y + dy, z) for dx, dy in
                  ((0, -1), (1, 0), (0, 1), (-1, 0))]
    paths = [path for at in candidates if (path := route(model, start, at)) is not None]
    return min(paths, key=lambda path: (len(path), path[-1])) if paths else None
