"""Render chronicler artifacts from a saved headless simulation snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sim.chronicler import Chronicler
from sim.core.world import WorldState


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate prose artifacts from a sim snapshot")
    parser.add_argument("snapshot", type=Path, help="input versioned world snapshot")
    parser.add_argument("--output", type=Path, required=True, help="JSON artifact bundle path")
    parser.add_argument("--tone", choices=("plain", "tavern"), default="plain")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    world = WorldState.load(args.snapshot)
    artifacts = Chronicler().generate_all(world, tone=args.tone)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".tmp")
    temporary.write_text(json.dumps(artifacts, sort_keys=True, ensure_ascii=False,
                                   indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({"artifacts": str(args.output),
                      "chronicle_entries": len(artifacts["chronicle"]),
                      "story_threads": len(artifacts["story_threads"]),
                      "events": artifacts["simulation_report"]["event_counts"]["total"],
                      "rumors": len(artifacts["rumors"]),
                      "epithet_awards": len(artifacts["epithet_awards"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
