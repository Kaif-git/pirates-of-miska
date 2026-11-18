"""Headless simulation CLI: `python -m sim.tools.run_sim`."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sim.core.world import WorldState


def _seed(value: str) -> int | str:
    try:
        return int(value, 10)
    except ValueError:
        return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a deterministic headless world simulation")
    parser.add_argument("--seed", type=_seed, default=1, help="integer or string master seed")
    parser.add_argument("--years", type=int, required=True, help="number of simulation years to advance")
    parser.add_argument("--output", type=Path, required=True, help="versioned world snapshot output path")
    parser.add_argument("--resume", type=Path, help="resume from a prior world snapshot")
    parser.add_argument("--director", choices=("on", "off"), default=None,
                        help="override the configured story director state")
    parser.add_argument("--keep-routine", action="store_true",
                        help="write every raw routine event instead of aggregated routine counters")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.years < 0:
        parser.error("--years must be nonnegative")
    world = WorldState.load(args.resume) if args.resume else WorldState.create(
        args.seed, director_enabled=None if args.director is None else args.director == "on")
    if args.resume and args.director is not None:
        enabled = args.director == "on"
        settings = world.config.setdefault("director", {
            "enabled": False, "intensity_permille": 0, "check_interval_days": 30,
            "conflict_drought_days": 180, "injection_cooldown_days": 180,
            "max_injections_per_year": 2, "minimum_major_characters": 4,
        })
        settings["enabled"] = enabled
        world.director.settings = dict(settings)
        roots = world.config["event_rules"]["root_event_types"]
        if enabled and "DIRECTOR_INJECTION" not in roots:
            roots.append("DIRECTOR_INJECTION")
        world.event_log.root_event_types = frozenset(roots)
    hours = world.config["time"]["hours_per_day"] * world.config["time"]["days_per_year"]
    hours_per_tick = world.config["time"]["hours_per_tick"]
    elapsed_ticks = args.years * hours // hours_per_tick
    world.run_until(world.clock.tick + elapsed_ticks)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    world.save(args.output)
    event_path = args.output.with_suffix(args.output.suffix + ".events.jsonl")
    event_jsonl = (world.event_log.to_jsonl() if args.keep_routine
                   else world.event_log.to_compacted_jsonl())
    event_path.write_text(event_jsonl, encoding="utf-8", newline="\n")
    print(json.dumps({"snapshot": str(args.output), "events": str(event_path),
                      "tick": world.clock.tick, "event_count": len(world.event_log),
                      "raw_routine_events_kept": args.keep_routine,
                      "event_log_rows": event_jsonl.count("\n")},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
