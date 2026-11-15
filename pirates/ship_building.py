"""Single-deck physical construction for connected floor and interior barriers.

Orders use carpentry labor and the existing exclusive hauling/job lifecycle.
Exterior-hull openings and moving installed stations are deliberately gated.
"""
from collections import deque

from omnisim.world import tile as T
from pirates.ship_map import ORIGIN, WIDTH, HEIGHT, project
from pirates.ship_model import ShipModel


def candidate(world, target, plan):
    if not isinstance(plan, dict) or set(plan) != {"action", "kind", "component_id"}:
        raise ValueError("Invalid construction order")
    if plan["action"] not in ("build", "dismantle") or plan["kind"] not in ("floor", "wall", "door"):
        raise ValueError("Unsupported structural component")
    if (not isinstance(target, (tuple, list)) or len(target) != 2
            or any(type(v) is not int for v in target)):
        raise ValueError("Invalid construction position")
    wx, wy = target
    if any(j.relocation and j.state not in ("done", "cancelled")
           and target == tuple(j.target) for j in world.ship.jobs):
        raise ValueError("An equipment relocation reserves this tile")
    x, y = wx - ORIGIN[0], wy - ORIGIN[1]
    if not (0 <= x < WIDTH and 0 <= y < HEIGHT):
        raise ValueError("Select a tile inside the ship")
    parts = world.ship_model.at(x, y, 0)
    existing_floor = next((c for c in parts if c.kind == "floor"), None)
    if plan["kind"] != "floor" and existing_floor is None:
        raise ValueError("Construction requires an existing deck floor")
    if (x, y) in world.ship_fixtures or (wx, wy) in set(world.ship.stations.values()) | set(world.ship.bunks):
        raise ValueError("An installed station or bunk needs this tile")
    if any(p.fixture_id is None and (p.x, p.y) == (x, y)
           for p in world.ship.storage.piles.values()):
        raise ValueError("A stockpile designation reserves this tile")
    barrier = next((c for c in parts if c.kind in ("wall", "door")), None)
    model = ShipModel.restore(world.ship_model.snapshot())
    if plan["action"] == "build":
        if plan["component_id"] is not None:
            raise ValueError("Invalid construction identity")
        if plan["kind"] == "floor":
            if existing_floor or barrier or (x, y) in world.ship_fixtures:
                raise ValueError("New deck floor needs an empty non-fixture tile")
            if not any(any(c.kind == "floor" for c in world.ship_model.at(x + dx, y + dy, 0))
                       for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0))):
                raise ValueError("New floor must connect to existing deck for crew access")
            model.place(x, y, 0, "floor")
        else:
            if barrier:
                raise ValueError("Dismantle the existing barrier first")
            model.place(x, y, 0, plan["kind"])
    else:
        component = existing_floor if plan["kind"] == "floor" else barrier
        if (type(plan["component_id"]) is not int or not component
                or component.id != plan["component_id"] or component.kind != plan["kind"]):
            raise ValueError("The designated component has changed")
        if plan["kind"] == "floor" and (barrier or x in (0, WIDTH - 1) or y in (0, HEIGHT - 1)):
            raise ValueError("Floor removal under walls or at the outer hull is not supported")
        if x in (0, WIDTH - 1) or y in (0, HEIGHT - 1):
            raise ValueError("Opening the outer hull requires the later flooding system")
        for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
            nx, ny = wx + dx, wy + dy
            if (not world.game_map.in_bounds(nx, ny)
                    or world.game_map.tile_id(nx, ny) == T.WATER):
                raise ValueError("Opening the outer hull requires the later flooding system")
        model.remove(component.id)
        if plan["kind"] == "floor":
            floors = {(c.x, c.y) for c in model.components
                      if c.z == 0 and c.kind == "floor"}
            if not floors:
                raise ValueError("A ship must retain at least one deck floor")
            seen = {next(iter(floors))}
            todo = deque(seen)
            while todo:
                x, y = todo.popleft()
                for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
                    at = x + dx, y + dy
                    if at in floors and at not in seen:
                        seen.add(at)
                        todo.append(at)
            if seen != floors:
                raise ValueError("Floor removal would split the deck into disconnected regions")
    return model


def validate_access(world, model, layout=None):
    """Try projection on a private map and preserve crew/essential-station access."""
    from omnisim.world.map import GameMap
    gm = GameMap(world.game_map.width, world.game_map.height,
                 world.game_map.tiles.copy(), doors=world.game_map.doors.copy())
    project(model, layout.overlay if layout is not None else world.ship_fixtures, gm)
    occupied = {(e.x, e.y) for e in world.entities()}
    if any(not gm.is_walkable(x, y) for x, y in occupied):
        raise ValueError("An actor occupies the work tile; waiting for it to clear")
    start = (69, 23)
    seen, todo = {start}, deque([start])
    while todo:
        x, y = todo.popleft()
        for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
            at = x + dx, y + dy
            if (at not in seen and gm.in_bounds(*at)
                    and (gm.is_walkable(*at) or gm.tile_id(*at) == T.DOOR)):
                seen.add(at)
                todo.append(at)
    bunks = layout.bunks if layout is not None else world.ship.bunks
    stations = layout.stations if layout is not None else world.ship.stations
    if not occupied <= seen or not set(bunks) <= seen:
        raise ValueError("This order would strand an actor or isolate a bunk")
    for pile in world.ship.storage.piles.values():
        if pile.fixture_id is None:
            wx, wy = pile.x + 69, pile.y + 12
            if (wx, wy) not in seen:
                raise ValueError("This order would block or isolate a floor stockpile")
    for x, y in stations.values():
        if not any((x + dx, y + dy) in seen
                   for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0))):
            raise ValueError("This order would isolate a working station")
    return seen


def validate_work_access(world, target, reachable):
    wx, wy = target
    if not any((wx + dx, wy + dy) in reachable
               for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0))):
        raise ValueError("No reachable adjacent work site for this construction order")


def queue(world, target, action, kind="wall"):
    target = tuple(target)
    x, y = target[0] - ORIGIN[0], target[1] - ORIGIN[1]
    parts = world.ship_model.at(x, y, 0)
    component = (next((c for c in parts if c.kind == "floor"), None) if kind == "floor" else
                 next((c for c in parts if c.kind in ("wall", "door")), None))
    actual_kind = component.kind if action == "dismantle" and component else kind
    plan = {"action": action, "kind": actual_kind,
            "component_id": component.id if action == "dismantle" and component else None}
    model = candidate(world, target, plan)
    reachable = validate_access(world, model)
    validate_work_access(world, target, reachable)
    key = f"construction:{target[0]},{target[1]}"
    if any(j.key == key and j.state not in ("done", "cancelled") for j in world.ship.jobs):
        raise ValueError("A construction order already exists here")
    job = world.ship.add_job(key, "repair", target, priority=2, work=25 if actual_kind == "floor" else 30)
    if job is None:
        raise ValueError("This site is on cancellation cooldown")
    job.construction = plan
    world.message(f"Queued {action} {plan['kind']} at {target}; carpentry labor required.")
    return job


def queue_blueprint(world):
    """Validate the complete proposal first, then materialize its edits as real jobs."""
    blueprint = world.blueprint
    if not blueprint.changes:
        raise ValueError("There are no unbuilt changes to queue")
    proposed = blueprint.apply(world.ship_model)
    reachable = validate_access(world, proposed)
    from collections import deque
    floors = {(c.x, c.y) for c in proposed.components if c.z == 0 and c.kind == "floor"}
    if not floors:
        raise ValueError("A ship must retain at least one deck floor")
    seen = {next(iter(floors))}
    todo = deque(seen)
    while todo:
        x, y = todo.popleft()
        for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
            at = x + dx, y + dy
            if at in floors and at not in seen:
                seen.add(at)
                todo.append(at)
    if seen != floors:
        raise ValueError("Blueprint contains disconnected deck-floor regions")
    # Skip copied components already present in the built structure (especially
    # deck floors, which form the background of most source selections).
    effective = [row for row in blueprint.changes
                 if not (row["action"] == "build" and any(
                      c.kind == row["kind"] for c in world.ship_model.at(row["x"], row["y"], row["z"]))) ]
    if not effective:
        raise ValueError("Every proposed component already exists")
    for row in effective:
        if row["action"] == "dismantle":
            component = next((c for c in world.ship_model.at(row["x"], row["y"], row["z"])
                              if c.kind == row["kind"]), None)
            if component is None:
                raise ValueError("A planned dismantling target changed")
            # The physical-order shortcut must enforce the same hull and deck
            # invariants as queue(); otherwise a draft could bypass the safety gate.
            candidate(world, (row["x"] + ORIGIN[0], row["y"] + ORIGIN[1]),
                      {"action": "dismantle", "kind": row["kind"],
                       "component_id": component.id})
        validate_work_access(world, (row["x"] + ORIGIN[0], row["y"] + ORIGIN[1]), reachable)
    build_cost = sum(1 for row in effective if row["action"] == "build")
    if world.ship.stores["planks"] < build_cost:
        raise ValueError(f"Blueprint needs {build_cost} plank bundles; only {world.ship.stores['planks']} available")
    for row in effective:
        if row["action"] == "build" and row["kind"] == "floor":
            existing = world.ship_model.at(row["x"], row["y"], row["z"])
            if (any(c.kind in ("wall", "door") for c in existing)
                    or (row["x"], row["y"]) in world.ship_fixtures):
                raise ValueError("New deck floor needs a clear non-fixture tile")
    # Put floor dependencies in a physically buildable order: first the cells
    # touching the current deck, then each successive connected frontier.
    floor_rows = [row for row in effective if row["action"] == "build" and row["kind"] == "floor"]
    existing_floors = {(c.x, c.y) for c in world.ship_model.components
                       if c.z == 0 and c.kind == "floor"}
    ordered_floors, pending = [], list(floor_rows)
    while pending:
        frontier = [row for row in pending if any(
            (row["x"] + dx, row["y"] + dy) in existing_floors
            for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)))]
        if not frontier:
            raise ValueError("New floors have no physical construction path from the existing deck")
        frontier.sort(key=lambda row: (row["y"], row["x"]))
        ordered_floors.extend(frontier)
        for row in frontier:
            existing_floors.add((row["x"], row["y"]))
            pending.remove(row)
    floor_ids = {id(row) for row in ordered_floors}
    effective = ([row for row in effective if id(row) in floor_ids]
                 + [row for row in effective if id(row) not in floor_ids])
    jobs = []
    keys = [f"construction:{row['x'] + ORIGIN[0]},{row['y'] + ORIGIN[1]}:{row['kind']}"
            for row in effective]
    for key in keys:
        if world.ship.cooldowns.get(key, 0) > world.ship.seconds:
            raise ValueError("A blueprint site is on cancellation cooldown")
        if any(j.key == key and j.state not in ("done", "cancelled") for j in world.ship.jobs):
            raise ValueError("A blueprint site already has an active order")
    for row in effective:
        target = row["x"] + ORIGIN[0], row["y"] + ORIGIN[1]
        plan = {"action": row["action"], "kind": row["kind"], "component_id": None}
        if row["action"] == "dismantle":
            component = next((c for c in world.ship_model.at(row["x"], row["y"], row["z"])
                              if c.kind == row["kind"]), None)
            plan["component_id"] = component.id
        job = world.ship.add_job(f"construction:{target[0]},{target[1]}:{row['kind']}", "repair", target,
                                 priority=2, work=25 if row["kind"] == "floor" else 30)
        if job is None:
            raise ValueError("A blueprint site is on cancellation cooldown")
        job.construction = plan
        jobs.append(job)
    blueprint.changes = []
    blueprint.history = []
    return jobs


def complete(world, job):
    model = candidate(world, job.target, job.construction)
    validate_access(world, model)
    project(model, world.ship_fixtures, world.game_map)
    world.ship_model = model
    world.refresh()
