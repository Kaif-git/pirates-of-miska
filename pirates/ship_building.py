"""Single-deck physical construction for connected floor and interior barriers.

Orders use carpentry labor and the existing exclusive hauling/job lifecycle.
Exterior-hull openings and moving installed stations are deliberately gated.
"""
from collections import deque

from omnisim.world import tile as T
from pirates.ship_map import ORIGIN, WIDTH, HEIGHT, project
from pirates.ship_model import ShipModel


def candidate(world, target, plan):
    if not isinstance(plan, dict) or set(plan) not in (
            {"action", "kind", "component_id"}, {"action", "kind", "component_id", "z"}):
        raise ValueError("Invalid construction order")
    if plan["action"] not in ("build", "dismantle") or plan["kind"] not in ("floor", "wall", "door", "stair", "beam", "pillar"):
        raise ValueError("Unsupported structural component")
    if (not isinstance(target, (tuple, list)) or len(target) not in (2, 3)
            or any(type(v) is not int for v in target)):
        raise ValueError("Invalid construction position")
    wx, wy = target[:2]
    z = target[2] if len(target) == 3 else plan.get("z", 0)
    if not 0 <= z <= 8:
        raise ValueError("Invalid construction deck")
    if any(j.relocation and j.state not in ("done", "cancelled")
           and target[:2] == tuple(j.target) for j in world.ship.jobs):
        raise ValueError("An equipment relocation reserves this tile")
    x, y = wx - ORIGIN[0], wy - ORIGIN[1]
    if not (0 <= x < WIDTH and 0 <= y < HEIGHT):
        raise ValueError("Select a tile inside the ship")
    parts = world.ship_model.at(x, y, z)
    existing_floor = next((c for c in parts if c.kind == "floor"), None)
    if plan["kind"] not in ("floor", "beam") and existing_floor is None:
        raise ValueError("Construction requires an existing deck floor")
    fixture_overlay = (world.fixtures.overlay_for(z) if hasattr(world.fixtures, "overlay_for")
                       else world.ship_fixtures if z == 0 else {})
    fixture_targets = {*world.ship.stations.values(), *world.ship.bunks}
    if (z == 0 and (wx, wy) in fixture_targets) or (x, y) in fixture_overlay:
        raise ValueError("An installed station or bunk needs this tile")
    if any(p.fixture_id is None and (p.x, p.y, p.z) == (x, y, z)
           for p in world.ship.storage.piles.values()):
        raise ValueError("A stockpile designation reserves this tile")
    if plan["kind"] == "stair" and any(
            p.fixture_id is None and (p.x, p.y) == (x, y) and p.z in (z, z + 1)
            for p in world.ship.storage.piles.values()):
        raise ValueError("A stockpile designation reserves this stair landing")
    if any((at[:2] == (wx, wy) and (at[2] if len(at) == 3 else 0) == z and items)
           for at, items in world.ground_items.items()):
        raise ValueError("Ground items must be moved before altering this tile")
    barrier = next((c for c in parts if c.kind in ("wall", "door")), None)
    existing_component = next((c for c in parts if c.kind == plan["kind"]), None)
    model = ShipModel.restore(world.ship_model.snapshot())
    if plan["action"] == "build":
        if plan["component_id"] is not None:
            raise ValueError("Invalid construction identity")
        if plan["kind"] == "floor":
            if existing_floor or barrier or (x, y) in fixture_overlay:
                raise ValueError("New deck floor needs an empty non-fixture tile")
            adjacent = any(any(c.kind == "floor" for c in world.ship_model.at(x + dx, y + dy, z))
                           for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)))
            first_upper_landing = (z > 0 and any(c.kind == "floor" for c in
                                                world.ship_model.at(x, y, z - 1)))
            if not adjacent and not first_upper_landing:
                raise ValueError("New floor must connect to existing deck for crew access")
            model.place(x, y, z, "floor")
        else:
            if plan["kind"] == "beam":
                if barrier:
                    raise ValueError("A beam cannot overlap a wall or door")
                adjacent_floor_or_beam = any(
                    any(c.kind in ("floor", "beam") for c in world.ship_model.at(x + dx, y + dy, z))
                    for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)))
                if not adjacent_floor_or_beam:
                    raise ValueError("A beam must connect to an existing deck bearing")
                model.place(x, y, z, "beam")
                return model
            if plan["kind"] == "pillar" and (not existing_floor or barrier):
                raise ValueError("A pillar needs an unobstructed deck floor")
            if plan["kind"] == "stair":
                if barrier:
                    raise ValueError("A stair cannot overlap a wall or door")
                model.place(x, y, z, "stair")
                return model
            if barrier:
                raise ValueError("Dismantle the existing barrier first")
            model.place(x, y, z, plan["kind"])
    else:
        component = existing_floor if plan["kind"] == "floor" else existing_component
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
                      if c.z == z and c.kind == "floor"}
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


def validate_access(world, model, layout=None, z=0):
    """Try projection on a private map and preserve crew/essential-station access."""
    from omnisim.world.map import GameMap
    gm = GameMap(world.game_map.width, world.game_map.height,
                 world.game_map.tiles.copy(), doors=world.game_map.doors.copy())
    if z == 0:
        project(model, layout.overlay_for(z) if layout is not None else world.ship_fixtures, gm, z)
    else:
        from pirates.ship_navigation import route, route_to_worksite
        starts = [(c.x - ORIGIN[0], c.y - ORIGIN[1], c.z)
                  for c in world.ship.crew]
        if not any(route(model, start, start) is not None for start in starts):
            raise ValueError("Upper deck has no reachable crew access")
        for fixture in (layout.fixtures if layout is not None else world.fixtures.fixtures):
            if fixture.carrier is not None or fixture.z != z:
                continue
            parts = model.at(fixture.x, fixture.y, fixture.z)
            if (not any(c.kind == "floor" for c in parts)
                    or any(c.kind in ("wall", "door", "stair") for c in parts)):
                raise ValueError("Equipment needs a clear upper-deck floor tile")
            if not any(route_to_worksite(model, start, (fixture.x, fixture.y, z))
                       for start in starts):
                raise ValueError("This order would isolate upper-deck equipment")
        for pile in world.ship.storage.piles.values():
            if pile.fixture_id is not None or pile.z == 0:
                continue
            target = pile.x, pile.y, pile.z
            if not any(route_to_worksite(model, start, target) is not None for start in starts):
                raise ValueError("This order would block or isolate an upper-deck stockpile")
        return set()
    occupied = {(e.x, e.y) for e in world.all_entities()
                if getattr(e, "z", 0) == 0}
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
    bunks = [at[:2] for at in bunks if len(at) == 2 or at[2] == 0]
    if not occupied <= seen or not set(bunks) <= seen:
        raise ValueError("This order would strand an actor or isolate a bunk")
    for pile in world.ship.storage.piles.values():
        if pile.fixture_id is None:
            if pile.z != 0:
                from pirates.ship_navigation import route_to_worksite
                if not route_to_worksite(model, (7, 13, 0), (pile.x, pile.y, pile.z)):
                    raise ValueError("This order would block or isolate an upper-deck stockpile")
                continue
            wx, wy = pile.x + 69, pile.y + 12
            if (wx, wy) not in seen:
                raise ValueError("This order would block or isolate a floor stockpile")
    for position in stations.values():
        if len(position) == 3 and position[2] != 0:
            continue
        x, y = position[:2]
        if not any((x + dx, y + dy) in seen
                   for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0))):
            raise ValueError("This order would isolate a working station")
    from pirates.ship_navigation import route
    lower_landing = (7, 13, 0)
    for sailor in world.ship.crew:
        if sailor.z > 0 and not route(model, lower_landing,
                                      (sailor.x - ORIGIN[0], sailor.y - ORIGIN[1], sailor.z)):
            raise ValueError("This order would strand crew on an upper deck")
    from pirates.ship_navigation import route_to_worksite
    for fixture in (layout.fixtures if layout is not None else world.fixtures.fixtures):
        if fixture.carrier is not None or fixture.z == 0:
            continue
        if not route_to_worksite(model, lower_landing,
                                 (fixture.x, fixture.y, fixture.z)):
            raise ValueError("This order would isolate upper-deck equipment")
    return seen


def validate_work_access(world, target, reachable):
    wx, wy = target
    if not any((wx + dx, wy + dy) in reachable
               for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0))):
        raise ValueError("No reachable adjacent work site for this construction order")


def queue(world, target, action, kind="wall", z=None):
    target = tuple(target)
    deck = target[2] if len(target) == 3 else (world.active_deck if z is None else z)
    x, y = target[0] - ORIGIN[0], target[1] - ORIGIN[1]
    parts = world.ship_model.at(x, y, deck)
    component = next((c for c in parts if c.kind == kind), None)
    actual_kind = component.kind if action == "dismantle" and component else kind
    plan = {"action": action, "kind": actual_kind,
            "component_id": component.id if action == "dismantle" and component else None}
    target3 = (target[0], target[1], deck)
    model = candidate(world, target3, plan)
    reachable = validate_access(world, model, z=deck)
    if deck == 0:
        validate_work_access(world, target, reachable)
    else:
        from pirates.ship_navigation import route_to_worksite
        starts = [(c.x - ORIGIN[0], c.y - ORIGIN[1], c.z)
                  for c in world.ship.crew]
        goals = [(x + dx, y + dy, deck) for dx, dy in ((0,-1),(1,0),(0,1),(-1,0))]
        reachable = any(route_to_worksite(world.ship_model, start, (x,y,deck)) is not None
                        for start in starts)
        if not reachable and actual_kind == "floor" and any(
                c.kind == "floor" for c in world.ship_model.at(x, y, deck - 1)):
            reachable = any(route_to_worksite(world.ship_model, start, (x,y,deck - 1)) is not None
                            for start in starts)
        if not reachable:
            raise ValueError("No stair-connected work route to this deck")
    key = f"construction:{target[0]},{target[1]}:{deck}"
    if any(j.key == key and j.state not in ("done", "cancelled") for j in world.ship.jobs):
        raise ValueError("A construction order already exists here")
    job = world.ship.add_job(key, "repair", target, priority=2, work=25 if actual_kind == "floor" else 30)
    if job is None:
        raise ValueError("This site is on cancellation cooldown")
    job.construction = plan
    job.construction["z"] = deck
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
    deck_levels = {c.z for c in proposed.components if c.kind == "floor"}
    if 0 not in deck_levels:
        raise ValueError("A ship must retain at least one deck floor")
    for level in deck_levels:
        floors = {(c.x, c.y) for c in proposed.components if c.z == level and c.kind == "floor"}
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
            raise ValueError(f"Blueprint contains disconnected floor regions on deck {level}")
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
            candidate(world, (row["x"] + ORIGIN[0], row["y"] + ORIGIN[1], row["z"]),
                      {"action": "dismantle", "kind": row["kind"],
                       "component_id": component.id})
        if row["z"] == 0:
            validate_work_access(world, (row["x"] + ORIGIN[0], row["y"] + ORIGIN[1]), reachable)
        else:
            from pirates.ship_navigation import route_to_worksite
            starts = [(c.x - ORIGIN[0], c.y - ORIGIN[1], c.z) for c in world.ship.crew]
            reachable = any(route_to_worksite(proposed, start,
                                              (row["x"], row["y"], row["z"]))
                            for start in starts)
            if (not reachable and row["kind"] == "floor" and row["z"] > 0
                    and any(c.kind == "floor" and (c.x, c.y, c.z) ==
                            (row["x"], row["y"], row["z"] - 1)
                            for c in proposed.components)):
                reachable = any(route_to_worksite(proposed, start,
                                                   (row["x"], row["y"], row["z"] - 1))
                                for start in starts)
            if not reachable:
                raise ValueError("No stair-connected work route to this blueprint deck")
    build_cost = sum(1 for row in effective if row["action"] == "build")
    if world.ship.stores["planks"] < build_cost:
        raise ValueError(f"Blueprint needs {build_cost} plank bundles; only {world.ship.stores['planks']} available")
    for row in effective:
        if row["action"] == "build" and row["kind"] == "floor":
            existing = world.ship_model.at(row["x"], row["y"], row["z"])
            if (any(c.kind in ("wall", "door") for c in existing)
                    or (row["x"], row["y"]) in world.fixtures.overlay_for(row["z"])):
                raise ValueError("New deck floor needs a clear non-fixture tile")
    # Put floor dependencies in a physically buildable order: first the cells
    # touching the current deck, then each successive connected frontier.
    floor_rows = [row for row in effective if row["action"] == "build" and row["kind"] == "floor"]
    ordered_floors = []
    for level in sorted({r["z"] for r in floor_rows}):
        existing_floors = {(c.x, c.y) for c in world.ship_model.components
                           if c.z == level and c.kind == "floor"}
        pending = [r for r in floor_rows if r["z"] == level]
        while pending:
            frontier = [row for row in pending if any(
                (row["x"] + dx, row["y"] + dy) in existing_floors
                for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)))]
            if not frontier:
                if level > 0 and any(c.kind == "stair" and c.z == level - 1
                                     and (c.x, c.y) in existing_floors for c in world.ship_model.components):
                    frontier = pending[:]
                elif level > 0:
                    frontier = [row for row in pending if any(
                        c.kind == "floor" and (c.x, c.y, c.z) ==
                        (row["x"], row["y"], level - 1) for c in world.ship_model.components)]
                else:
                    raise ValueError("New floors have no physical construction path from an existing floor")
                if not frontier:
                    raise ValueError("New floors have no physical construction path from an existing floor")
            frontier.sort(key=lambda row: (row["y"], row["x"]))
            ordered_floors.extend(frontier)
            for row in frontier:
                existing_floors.add((row["x"], row["y"]))
                pending.remove(row)
    floor_ids = {id(row) for row in ordered_floors}
    beam_rows = [row for row in effective if row["action"] == "build" and row["kind"] == "beam"]
    ordered_beams = []
    for level in sorted({r["z"] for r in beam_rows}):
        anchored = {(c.x, c.y) for c in world.ship_model.components
                    if c.z == level and c.kind in ("floor", "beam")}
        pending = [r for r in beam_rows if r["z"] == level]
        while pending:
            frontier = [row for row in pending if any(
                (row["x"] + dx, row["y"] + dy) in anchored
                for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)))]
            if not frontier:
                raise ValueError("Beam blueprint has no sequential construction path from a deck bearing")
            frontier.sort(key=lambda row: (row["y"], row["x"]))
            ordered_beams.extend(frontier)
            for row in frontier:
                anchored.add((row["x"], row["y"]))
                pending.remove(row)
    beam_ids = {id(row) for row in ordered_beams}
    effective = ([row for row in effective if id(row) in floor_ids]
                 + [row for row in effective if id(row) in beam_ids]
                 + [row for row in effective if id(row) not in floor_ids | beam_ids])
    jobs = []
    keys = [f"construction:{row['x'] + ORIGIN[0]},{row['y'] + ORIGIN[1]}:{row['kind']}:{row['z']}"
            for row in effective]
    for key in keys:
        if world.ship.cooldowns.get(key, 0) > world.ship.seconds:
            raise ValueError("A blueprint site is on cancellation cooldown")
        if any(j.key == key and j.state not in ("done", "cancelled") for j in world.ship.jobs):
            raise ValueError("A blueprint site already has an active order")
    for row in effective:
        target = row["x"] + ORIGIN[0], row["y"] + ORIGIN[1]
        plan = {"action": row["action"], "kind": row["kind"], "component_id": None,
                "z": row["z"]}
        if row["action"] == "dismantle":
            component = next((c for c in world.ship_model.at(row["x"], row["y"], row["z"])
                              if c.kind == row["kind"]), None)
            plan["component_id"] = component.id
        job = world.ship.add_job(f"construction:{target[0]},{target[1]}:{row['kind']}:{row['z']}", "repair", target,
                                 priority=2, work=25 if row["kind"] == "floor" else 30)
        if job is None:
            raise ValueError("A blueprint site is on cancellation cooldown")
        job.construction = plan
        jobs.append(job)
    blueprint.changes = []
    blueprint.history = []
    return jobs


def complete(world, job):
    z = job.construction.get("z", 0)
    local = (job.target[0] - ORIGIN[0], job.target[1] - ORIGIN[1], z)
    if (job.construction.get("action") == "dismantle" and local in world.locked_doors):
        raise ValueError("A locked barrier cannot be dismantled during lockdown")
    model = candidate(world, (*job.target, z), job.construction)
    validate_access(world, model, z=z)
    project(model, world.ship_fixtures, world.game_map, world.active_deck)
    world.ship_model = model
    if job.construction.get("action") == "build" and job.construction.get("kind") == "door" and world.lockdown:
        world.locked_doors.add(local)
        wx, wy = job.target
        world.door_state(z)[wy, wx] = False
    if (job.construction.get("action") == "build" and job.construction.get("kind") == "wall"
            and hasattr(world, "hazards")):
        world.hazards.close_breach(job.target[0] - ORIGIN[0],
                                   job.target[1] - ORIGIN[1], z)
    world.refresh()
