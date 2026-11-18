"""Structural deck-to-deck route graph; routes exist only across built stairs."""
from collections import deque


def route(model, start, goal, blocked=()):
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
    geometry = model.navigation_geometry()
    floors = geometry["floor"]
    if start not in floors or goal not in floors:
        return None
    walls = geometry["wall"]
    stairs = geometry["stair"]
    blocked = set(blocked) - {start}
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
            if at not in parent and at not in blocked and at in floors and at not in walls:
                parent[at] = (x, y, z)
                todo.append(at)
    return None


def traversed_doors(model, path):
    """Return door coordinates on a route, preserving route order."""
    doors = model.navigation_geometry()["door"]
    return tuple(at for at in path or () if at in doors)


def route_to_worksite(model, start, target, blocked=()):
    """Route to a reachable cardinal work position beside a 3D target tile."""
    if (not isinstance(target, (tuple, list)) or len(target) != 3
            or any(type(v) is not int for v in target)):
        raise ValueError("Work targets must be integer (x, y, deck) triples")
    x, y, z = target
    candidates = [(x + dx, y + dy, z) for dx, dy in
                  ((0, -1), (1, 0), (0, 1), (-1, 0))]
    geometry = model.navigation_geometry()
    floors, walls, stairs = geometry["floor"], geometry["wall"], geometry["stair"]
    start = tuple(start)
    if start not in floors:
        return None
    blocked = set(blocked) - {start}
    goals = {at for at in candidates if at in floors and at not in walls and at not in blocked}
    if not goals:
        return None

    parent = {start: None}
    distances = {start: 0}
    todo = deque([start])
    found = []
    found_distance = None
    while todo:
        current = todo.popleft()
        distance = distances[current]
        if found_distance is not None and distance > found_distance:
            break
        if current in goals:
            found_distance = distance
            found.append(current)
            continue
        x, y, z = current
        neighbors = [(x + dx, y + dy, z) for dx, dy in
                     ((0, -1), (1, 0), (0, 1), (-1, 0))]
        if current in stairs:
            neighbors.append((x, y, z + 1))
        if (x, y, z - 1) in stairs:
            neighbors.append((x, y, z - 1))
        for at in neighbors:
            if at not in parent and at not in blocked and at in floors and at not in walls:
                parent[at] = current
                distances[at] = distance + 1
                todo.append(at)

    if not found:
        return None
    endpoint = min(found)
    path = []
    current = endpoint
    while current is not None:
        path.append(current)
        current = parent[current]
    return list(reversed(path))
