"""Best-effort ship traffic, work-route and emergency-response inspection."""
from collections import Counter
import math

from pirates.ship_map import ORIGIN
from pirates.ship_navigation import route_to_worksite, traversed_doors
from pirates.ship_storage import bundle_mass


def inspect(world):
    """Return projected crew routes, congestion, recent throughput and hazard ETAs.

    Travel estimates use the live structural route graph, closed-door opening
    delay and current carried load. They ignore future needs, door contention,
    route replanning and work duration; the ledger labels them as estimates.
    """
    model, ship = world.ship_model, world.ship
    blocked = {(f.x, f.y, f.z) for f in world.fixtures.fixtures if f.carrier is None}
    blocked.update(world.locked_doors)
    route_rows, tile_users, route_by_crew = [], {}, {}
    bodies = {(c.x - ORIGIN[0], c.y - ORIGIN[1], getattr(c, "z", 0)): c.id
              for c in world.all_entities()}
    for sailor in ship.crew:
        job = ship.job(sailor.job_id)
        if not job or job.state in ("done", "cancelled") or sailor.intent:
            continue
        target = job.target
        z = (job.construction.get("z", 0) if job.construction else
             target[2] if len(target) > 2 else 0)
        start = (sailor.x - ORIGIN[0], sailor.y - ORIGIN[1], sailor.z)
        goal = (target[0] - ORIGIN[0], target[1] - ORIGIN[1], z)
        path = route_to_worksite(model, start, goal, blocked)
        if path is None:
            route_rows.append(f"{sailor.name} -> {ship.job_label(job)}: NO STRUCTURAL ROUTE")
            route_by_crew[sailor.id] = None
            continue
        doors = traversed_doors(model, path)
        closed = sum(not world.door_state(dz)[dy + ORIGIN[1], dx + ORIGIN[0]]
                     for dx, dy, dz in doors)
        mass = bundle_mass(sailor.cargo)
        per_tile = max(1, math.ceil(mass / 20)) if mass else 1
        steps = max(0, len(path) - 1)
        seconds = steps * per_tile + closed
        occupied = sum(at in bodies and bodies[at] != sailor.id for at in path[1:])
        route_by_crew[sailor.id] = path
        for at in path[1:]:
            tile_users.setdefault(at, set()).add(sailor.id)
        route_rows.append(f"{sailor.name} -> {ship.job_label(job)}: {steps} tiles, "
                          f"~{seconds}s travel | {mass} kg carried | "
                          f"{closed} closed doors | {occupied} occupied route tiles")

    hot = {at: names for at, names in tile_users.items() if len(names) > 1}
    body_conflicts = sum(1 for sailor in ship.crew if route_by_crew.get(sailor.id)
                        and any(at in bodies and bodies[at] != sailor.id
                                for at in route_by_crew[sailor.id][1:]))
    recent_cutoff = max(0, ship.seconds - 600)
    completed = sum(row["event"] == "completed" and row["second"] >= recent_cutoff
                    for row in ship.audit)
    rows = ["LOGISTICS / TRAFFIC — estimates from current routes and recorded work",
            f"Claimed crew routes: {len(route_rows)} | shared route tiles: {len(hot)} | "
            f"routes currently crossing another body: {body_conflicts}",
            f"Completed jobs in last 10 min: {completed} ({completed * 6:.1f}/hour equivalent)",
            "Travel estimate excludes work time, future needs and changing congestion.",
            "Route intensity overlay: planned paths; brighter `;` cells are shared routes.", "", "CREW WORK ROUTES"]
    rows.extend(route_rows or ["No crew currently has an active route to a job."])
    rows += ["", "EMERGENCY RESPONSE — nearest repair labor to active incidents"]
    incidents = [("FIRE", world.hazards.cell(key)) for key in sorted(world.hazards.fires)]
    incidents += [("BREACH", world.hazards.cell(key)) for key in sorted(world.hazards.breaches)]
    if not incidents:
        rows.append("No active fire or hull breach.")
    for label, at in incidents:
        candidates = []
        for sailor in ship.crew:
            if sailor.labors.get("repair", 0) <= 0:
                continue
            start = (sailor.x - ORIGIN[0], sailor.y - ORIGIN[1], sailor.z)
            path = route_to_worksite(model, start, at, blocked)
            if path is None:
                continue
            doors = traversed_doors(model, path)
            closed = sum(not world.door_state(dz)[dy + ORIGIN[1], dx + ORIGIN[0]]
                         for dx, dy, dz in doors)
            candidates.append((len(path) - 1 + closed, sailor, len(path) - 1))
        if candidates:
            seconds, sailor, steps = min(candidates, key=lambda row: (row[0], row[1].id))
            job = ship.job(sailor.job_id)
            commitment = f"; busy on {ship.job_label(job)}" if job else ""
            rows.append(f"{label} at deck {at[2]} tile ({at[0]},{at[1]}): "
                        f"{sailor.name}, {steps} tiles / ~{seconds}s travel{commitment}")
        else:
            rows.append(f"{label} at deck {at[2]} tile ({at[0]},{at[1]}): NO REPAIR-LABOR ROUTE")
    return {"rows": rows, "traffic": {at: len(users) for at, users in hot.items()},
            "planned_routes": {sid: tuple(path) if path else None
                               for sid, path in route_by_crew.items()},
            "body_conflicts": body_conflicts, "completed_last_10m": completed}
