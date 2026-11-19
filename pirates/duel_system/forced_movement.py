"""Shared deterministic collision-safe resolver for involuntary movement."""
from .model import EventType


def resolve_forced_movement(state, requests):
    """Resolve forced routes against one occupancy snapshot, then apply legal steps.

    Each request contains actor/target fighter objects, a unit direction, a bounded
    step count, and a cause tag. Occupied cells are never chain-pushed through.
    Conflicting routes stop at their first shared cell for every contender.
    """
    route_owners = {}
    for request in requests:
        target = request["target"]
        dx, dy = request["direction"]
        origin = target.pos
        route = [(origin[0] + dx * step, origin[1] + dy * step)
                 for step in range(1, request["steps"] + 1)]
        request["route"] = route
        request["actual"] = []
        for cell in route:
            route_owners.setdefault(cell, []).append(request)
    conflicts = {cell for cell, owners in route_owners.items() if len(owners) > 1}
    occupancy = {fighter.pos: fighter for fighter in state.fighters
                 if state.occupies_tile(fighter)}
    for request in requests:
        target = request["target"]
        for destination in request["route"]:
            if destination in conflicts:
                request["blocker"] = "DESTINATION_CONFLICT"
                break
            if state.walkable is not None and destination not in state.walkable:
                request["blocker"] = "ARENA_EDGE"
                break
            blocker = occupancy.get(destination)
            if blocker is not None and blocker.id != target.id:
                request["blocker"] = blocker.id
                break
            request["actual"].append(destination)
    for request in requests:
        target, actor = request["target"], request["actor"]
        for destination in request["actual"]:
            origin = target.pos
            target.pos = destination
            state.emit(EventType.MOVE, target.id, actor.id,
                       deltas={"from": origin, "to": destination, "forced": True},
                       cause_tags=(request["cause_tag"],))
    return requests
