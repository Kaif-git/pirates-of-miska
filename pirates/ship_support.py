"""Conservative load-path checks for sparse multi-deck ship structure.

This is a game-scale rule set, not a strength-of-materials solver. Deck-zero
floors represent the supported hull; upper floors need a bearing below, a
pillar, or a bounded beam run to a supported bearing.
"""

from pirates.ship_model import MATERIALS
from pirates.ship_map import ORIGIN, WIDTH, HEIGHT


def assess(model, loads_kg=None):
    """Return supported/unsupported deck floors and beam span/load warnings.

    ``loads_kg`` maps ship-local ``(x, y, z)`` floor coordinates to extra
    carried load. Each floor contributes its own component mass.
    """
    loads_kg = loads_kg or {}
    floors = {(c.x, c.y, c.z): c for c in model.components if c.kind == "floor"}
    pillars = {(c.x, c.y, c.z): c for c in model.components if c.kind == "pillar"}
    beams = {(c.x, c.y, c.z): c for c in model.components if c.kind == "beam"}
    structure = {}
    for component in model.components:
        if component.kind == "floor":
            continue
        at = (component.x, component.y, component.z)
        structure[at] = structure.get(at, 0) + MATERIALS[component.material].mass_kg
    supported = {at for at in floors if at[2] == 0}
    unsupported = set()
    spans = []
    overloaded = []
    beam_loads = {}
    floor_load = {
        at: (MATERIALS[floor.material].mass_kg + structure.get(at, 0)
             + max(0, int(loads_kg.get(at, 0))))
        for at, floor in floors.items()
    }
    bearing_loads = {}

    max_deck = max((z for _, _, z in floors), default=0)
    for z in range(1, max_deck + 1):
        for at, floor in sorted(floors.items()):
            x, y, level = at
            if level != z:
                continue
            below = (x, y, z - 1)
            if below in supported:
                supported.add(at)
                bearing_loads[below] = bearing_loads.get(below, 0) + floor_load[at]
                bearing = pillars.get(below, floors[below])
                multiplier = 20 if below in pillars else 8
                capacity = MATERIALS[bearing.material].strength * multiplier * bearing.condition // 100
                if bearing_loads[below] > capacity:
                    overloaded.append({"support": below, "kind": bearing.kind,
                                       "load_kg": bearing_loads[below], "capacity_kg": capacity})
                floor_load[below] += floor_load[at]
                continue
            pillar = pillars.get(below)
            if pillar and below in supported:
                supported.add(at)
                floor_load[below] += floor_load[at]
                continue
            bearing = _beam_bearing((x, y, z - 1), z - 1, beams, supported)
            if bearing is None:
                unsupported.add(at)
                continue
            anchor, run, capacity = bearing
            supported.add(at)
            load = floor_load[at]
            for beam_at in run:
                beam_loads[beam_at] = beam_loads.get(beam_at, 0) + load
            spans.append({"floor": at, "anchor": anchor, "beam_run": tuple(run),
                          "span_tiles": len(run), "capacity_kg": capacity,
                          "load_kg": load})
            floor_load[anchor] += load

    # A bridge beam carries the sum of every upper floor whose route uses it,
    # not just the heaviest single floor's load.
    for at, load in sorted(beam_loads.items()):
        beam = beams[at]
        capacity = MATERIALS[beam.material].strength * 10 * beam.condition // 100
        if load > capacity:
            overloaded.append({"beam": at, "load_kg": load, "capacity_kg": capacity})

    return {"supported": tuple(sorted(supported)),
            "unsupported": tuple(sorted(unsupported)),
            "beam_spans": tuple(spans), "overloaded": tuple(overloaded)}


def world_loads(world):
    """Estimate stationary loads carried by built floor cells in a live world."""
    from pirates.ship_physics import load_by_position
    return load_by_position(world)[1]


def advance(world):
    """Apply one second of structural fatigue and detach failed components.

    This deliberate game-scale fatigue step is persisted through each
    component's existing condition field. Unsupported floors fail before
    occupied or fixture-bearing cells are removed; they remain critically
    damaged and can fail once the tile is clear.
    """
    model = world.ship_model
    report = assess(model, world_loads(world))
    damage = set(report["unsupported"])
    damage_ids = set()
    for row in report["overloaded"]:
        at = row.get("beam", row.get("support"))
        kind = "beam" if "beam" in row else row.get("kind")
        component = next((c for c in model.at(*at) if c.kind == kind), None)
        if component:
            damage_ids.add(component.id)
    failed = set()
    for component in model.components:
        stressed = ((component.x, component.y, component.z) in damage
                    if component.kind == "floor" else component.id in damage_ids)
        if not stressed:
            continue
        condition = max(0, component.condition - 1)
        model.set_condition(component.id, condition)
        if condition == 0:
            failed.add(component.id)

    for component_id in sorted(failed):
        component = next((c for c in model.components if c.id == component_id), None)
        if component is None:
            continue
        at = (component.x, component.y, component.z)
        if component.kind == "floor":
            section = _unsupported_section(at, set(report["unsupported"]))
            if any(_occupied(world, cell) or _fixture_at(world, cell) or _stockpile_at(world, cell)
                   or _ground_item_at(world, cell) for cell in section):
                continue
            _detach_section(world, section)
            world.message(f"Structural failure: unsupported {len(section)}-tile deck section detached at {at}.")
        else:
            model.remove(component.id)
            world.message(f"Structural failure: overloaded {component.kind} failed at {at}.")
    if failed:
        from pirates.ship_map import project
        project(world.ship_model, world.fixtures.overlay_for(world.active_deck),
                world.game_map, world.active_deck)
        world.ship_fixtures = world.fixtures.overlay_for(world.active_deck)
        world.refresh()


def _occupied(world, at):
    x, y, z = at
    return any((actor.x - ORIGIN[0], actor.y - ORIGIN[1], getattr(actor, "z", 0)) == at
               for actor in [world.player, *world.ship.crew])


def _fixture_at(world, at):
    x, y, z = at
    return any(f.carrier is None and (f.x, f.y, f.z) == at for f in world.fixtures.fixtures)


def _stockpile_at(world, at):
    x, y, z = at
    return any(p.fixture_id is None and (p.x, p.y, p.z) == at
               for p in world.ship.storage.piles.values())


def _ground_item_at(world, at):
    x, y, z = at
    world_at = (x + ORIGIN[0], y + ORIGIN[1])
    key = world_at if z == 0 else (*world_at, z)
    return bool(world.ground_items.get(key))


def _unsupported_section(start, unsupported):
    section, todo = {start}, [start]
    while todo:
        x, y, z = todo.pop()
        for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
            at = x + dx, y + dy, z
            if at in unsupported and at not in section:
                section.add(at)
                todo.append(at)
    return section


def _detach_section(world, section):
    model = world.ship_model
    for x, y, z in section:
        for component in tuple(model.at(x, y, z)):
            model.remove(component.id)
        # A stair is one connector; losing either endpoint removes it.
        for component in tuple(model.components):
            if component.kind == "stair" and (component.x, component.y) == (x, y) \
                    and component.z in (z - 1, z):
                model.remove(component.id)


def _beam_bearing(start, z, beams, supported):
    """Find a collinear beam path to an already-supported lower floor."""
    directions = ((1, 0), (-1, 0), (0, 1), (0, -1))
    for dx, dy in directions:
        run = []
        x, y = start[:2]
        first_beam = beams.get(start)
        if first_beam is None:
            continue
        run.append(start)
        for distance in range(1, 9):
            x, y = x + dx, y + dy
            at = (x, y, z)
            if at in supported:
                if not run:
                    break
                limit = min(MATERIALS[beams[b].material].strength // 25 for b in run)
                if len(run) <= limit:
                    capacity = min(MATERIALS[beams[b].material].strength * 10
                                   * beams[b].condition // 100 for b in run)
                    return at, run, capacity
                break
            beam = beams.get(at)
            if beam is None:
                break
            run.append(at)
    return None
