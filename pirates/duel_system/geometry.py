"""Shared deterministic geometry for locked sword attacks and their telegraphs."""
from __future__ import annotations

from .model import ActionKind


_OCTANTS = ((0, -1), (1, -1), (1, 0), (1, 1),
            (0, 1), (-1, 1), (-1, 0), (-1, -1))


def bearing_from(origin, target):
    """Return the nearest 8-way bearing from origin to target, or None if coincident."""
    dx = target[0] - origin[0]
    dy = target[1] - origin[1]
    if dx == 0 and dy == 0:
        return None
    direction = ((dx > 0) - (dx < 0), (dy > 0) - (dy < 0))
    return direction


def _octant(direction):
    try:
        return _OCTANTS.index(tuple(direction))
    except ValueError:
        return None


def attack_reach(kind):
    # §31 novice sword contract: cuts stay one tile even if a different weapon
    # record carries a larger nominal reach; only Thrust gets the two-tile ray.
    return 2 if ActionKind(kind) == ActionKind.THRUST else 1


def attack_cells(origin, bearing, kind):
    """Cells threatened by one attack; caller filters blockers/walkable terrain."""
    kind = ActionKind(kind)
    center = _octant(bearing) if bearing is not None else None
    if center is None:
        return frozenset()
    reach = attack_reach(kind)
    if kind == ActionKind.THRUST:
        dx, dy = _OCTANTS[center]
        return frozenset((origin[0] + dx * step, origin[1] + dy * step)
                         for step in range(1, reach + 1))
    if kind in {ActionKind.LIGHT, ActionKind.HEAVY, ActionKind.DASH_LIGHT,
                ActionKind.DISARM}:
        if reach != 1:
            return frozenset()
        return frozenset((origin[0] + _OCTANTS[(center + offset) % 8][0],
                          origin[1] + _OCTANTS[(center + offset) % 8][1])
                         for offset in (-1, 0, 1))
    return frozenset()


def attack_contains(origin, target, bearing, kind):
    return target in attack_cells(origin, bearing, kind)


def lock_delay(kind):
    """Base preparation ticks before target bearing locks; scaled by scheduler."""
    return {ActionKind.LIGHT: 1, ActionKind.HEAVY: 2,
            ActionKind.THRUST: 1, ActionKind.DASH_LIGHT: 3,
            ActionKind.DISARM: 1}.get(ActionKind(kind), 0)
