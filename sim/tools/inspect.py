"""Read-only world snapshot inspection and event-cause tracing."""

from __future__ import annotations

import argparse
import json

from sim.core.world import SNAPSHOT_SCHEMA_VERSION, WorldState
from sim.systems.geography import WorldMap


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect a saved simulation snapshot")
    parser.add_argument("target", nargs="?", help="snapshot path, or 'map' for the default chart")
    parser.add_argument("--map", action="store_true", help="show the map in a saved world snapshot")
    parser.add_argument("--entity", help="show an active or archived entity by stable ID")
    parser.add_argument("--event", help="show a structured event by ID")
    parser.add_argument("--decisions", action="store_true",
                        help="show persisted agent decision state and private score traces")
    parser.add_argument("--decision-agent", help="filter decision diagnostics by character ID")
    parser.add_argument("--trace-causes", action="store_true", help="recursively show event parents")
    parser.add_argument("--validate-locations", action="store_true",
                         help="validate entity locations against the map graph")
    return parser


def _map_report(geography, weather=None, vessels=None) -> dict:
    return {
        "map_digest": geography.digest,
        "regions": [
            {"id": region.id, "name": region.name, "season": region.season,
             "danger_level": region.danger_level,
             "dangerous_route_only": region.dangerous_route_only,
             "weather": weather.state(region.id) if weather else region.initial_weather}
            for region in sorted(geography.regions.values(), key=lambda row: row.id)
        ],
        "islands": [
            {"id": island.id, "name": island.name, "coords": list(island.coords),
             "region_id": island.region_id}
            for island in sorted(geography.islands.values(), key=lambda row: row.id)
        ],
        "routes": [
            {"id": route.id, "from": route.origin, "to": route.destination,
             "distance_days_at_base_speed": route.distance_days,
             "hazards": list(route.hazards), "chokepoint": route.chokepoint}
            for route in sorted(geography.routes.values(), key=lambda row: row.id)
        ],
        "vessels": [
            {"id": ship_id, "name": vessel.get("name"),
             "location": vessels.location_of(ship_id)}
            for ship_id, vessel in sorted((vessels.entities if vessels else {}).items())
            if vessel.get("kind") == "ship"
        ],
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.target == "map" or (args.target is None and args.map):
        if (args.entity or args.event or args.decisions or args.decision_agent
                or args.trace_causes or args.validate_locations):
            raise SystemExit("Entity, event, and location checks require a snapshot path")
        geography = WorldMap.load()
        print(json.dumps(_map_report(geography), sort_keys=True, ensure_ascii=False, indent=2))
        return 0
    if args.target is None:
        raise SystemExit("Provide a snapshot path or use 'map'")
    world = WorldState.load(args.target)
    if args.decision_agent and not args.decisions:
        raise SystemExit("--decision-agent requires --decisions")
    if args.decisions:
        if args.entity or args.event or args.map:
            raise SystemExit("Decision diagnostics cannot be combined with entity, event, or map output")
        if args.decision_agent and args.decision_agent not in world.entities \
                and args.decision_agent not in world.archive:
            raise SystemExit(f"No character found: {args.decision_agent}")
        decision_state = world.decisions.snapshot()
        agent_ids = ([args.decision_agent] if args.decision_agent
                     else sorted(decision_state["agents"]))
        print(json.dumps({
            "tick": world.clock.tick,
            "event_cursor": decision_state["event_cursor"],
            "pending_wakes": [row for row in decision_state["pending_wakes"]
                              if args.decision_agent is None
                              or row["agent_id"] == args.decision_agent],
            "agents": {agent_id: decision_state["agents"][agent_id]
                       for agent_id in agent_ids if agent_id in decision_state["agents"]},
            "debug_traces": [row for row in decision_state["debug_history"]
                             if args.decision_agent is None
                             or row["agent_id"] == args.decision_agent],
        }, sort_keys=True, ensure_ascii=False, indent=2))
        return 0
    if args.validate_locations:
        world.validate_locations()
        if not args.entity and not args.event and not args.map:
            print(json.dumps({"locations_valid": True, "entity_count": len(world.entities)},
                             sort_keys=True, ensure_ascii=False))
            return 0
    if args.map:
        print(json.dumps(_map_report(world.geography, world.weather, world),
                         sort_keys=True, ensure_ascii=False, indent=2))
        return 0
    if args.entity:
        if args.entity in world.entities:
            table = "entities"
            entity = dict(world.entities[args.entity])
            entity["location"] = world.location_of(args.entity)
        elif args.entity in world.archive:
            table = "archive"
            entity = dict(world.archive[args.entity])
        elif args.entity in world.lod.named_records:
            table = "lod.named_records"
            entity = dict(world.lod.named_records[args.entity])
        else:
            raise SystemExit(f"No entity found: {args.entity}")
        print(json.dumps({"table": table, "id": args.entity,
                          "entity": entity},
                         sort_keys=True, ensure_ascii=False, indent=2))
        return 0
    if args.event:
        event = world.event_log.get(args.event)
        if not args.trace_causes:
            print(json.dumps(event.to_record(), sort_keys=True, ensure_ascii=False, indent=2))
            return 0
        pending = [event]
        visited = set()
        traced = []
        while pending:
            current = pending.pop()
            if current.id in visited:
                continue
            visited.add(current.id)
            traced.append(current.to_record())
            pending.extend(reversed(world.event_log.causes(current.id)))
        traced.sort(key=lambda record: (record["tick"], record["id"]))
        print(json.dumps(traced, sort_keys=True, ensure_ascii=False, indent=2))
        return 0
    print(json.dumps({
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "seed": world.seed,
        "tick": world.clock.tick,
        "entities": len(world.entities),
        "archived_entities": len(world.archive),
        "events": len(world.event_log),
        "regions": len(world.geography.regions),
        "islands": len(world.geography.islands),
        "named_characters": (
            sum(entity.get("kind") == "character" for entity in world.entities.values())
            + sum(entity.get("kind") == "character" for entity in world.archive.values())
            + len(world.lod.named_records)),
        "named_background": len(world.lod.named_records),
        "regional_coarse_summaries": sum(
            len(rows) for rows in world.lod.regional_history.values()),
        "routes": len(world.geography.routes),
        "transit_vessels": len(world.travel.transits),
        "next_event": (world.scheduler.peek().event_type if world.scheduler.peek() else None),
    }, sort_keys=True, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
