"""Render the compact supply-readiness summary from a saved at-sea state."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

import play
from PIL import Image
from pirates.provisioning import readiness_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save", type=Path, required=True,
                        help="mid-voyage save produced by tools/preview_supply_voyage.py")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--title", default="At sea — supply readiness")
    args = parser.parse_args()

    world = play.World.load(args.save)
    voyage = world.voyages.voyage
    if not world.at_sea or not voyage or voyage["status"] != "underway":
        raise SystemExit("Save must be an underway at-sea state")

    remaining_hours = max(0.0, (voyage["duration_seconds"] - voyage["elapsed_seconds"]) / 3600)
    target_days = remaining_hours / 24
    rows = [row for row in readiness_rows(world, target_days=target_days)
            if not row.startswith("Next: ")][:7]

    args.output.mkdir(parents=True, exist_ok=True)
    renderer = play.PiratesRenderer(settings=play.RenderSettings(
        world_font_size=20, world_cell_width=20, world_cell_height=24))
    try:
        renderer.init_for(100, 64)
        renderer.render(world)
        renderer.render_list(world, f"{args.title} ({remaining_hours:.1f} h remaining)",
                             rows, "Save-free at-sea readiness capture", selectable=False)
        renderer._present_layers()
        image = args.output / "at-sea-supply-readiness.png"
        Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(image)
    finally:
        renderer.shutdown()

    report = {
        "source_save": str(args.save),
        "voyage_status": voyage["status"],
        "voyage_elapsed_seconds": voyage["elapsed_seconds"],
        "voyage_duration_seconds": voyage["duration_seconds"],
        "remaining_hours": round(remaining_hours, 2),
        "port": world.economy.port,
        "ready_food_kg": round(world.ship.ready_food_g() / 1000, 2),
        "potable_water_l": round(world.ship.clean_water_total_ml() / 1000, 2),
        "crew": len(world.ship.crew),
        "readiness_rows": rows,
    }
    (args.output / "at-sea-verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"At-sea supply readiness captured in {args.output}")


if __name__ == "__main__":
    main()
