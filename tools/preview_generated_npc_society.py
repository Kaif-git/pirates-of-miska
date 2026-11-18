"""Capture a save-free native walkthrough of seeded generated NPC society."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

import play
from PIL import Image
from pirates.management import generated_person_profile_rows, sea_map_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", default="native-generated-preview")
    parser.add_argument("--history-days", type=int, default=12)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    world = play.World.new_generated(args.seed, {"history_days": args.history_days})
    renderer = play.PiratesRenderer(settings=play.RenderSettings(
        world_font_size=20, world_cell_width=20, world_cell_height=24))
    report = {"seed": args.seed, "history_days": args.history_days, "frames": []}
    try:
        renderer.init_for(100, 64)

        def capture(name, title, rows):
            renderer.render(world)
            renderer.render_list(world, title, rows,
                                 "Save-free generated society walkthrough", selectable=False)
            renderer._present_layers()
            Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(args.output / f"{name}.png")
            report["frames"].append(name)

        capture("01-generated-chart", "Known contacts on the sea chart", sea_map_rows(world))
        person = next(iter(sorted(world.society.people.values(), key=lambda row: row["name"])))
        capture("02-public-profile", f"Public profile — {person['name']}",
                generated_person_profile_rows(world, person))
        news = list(reversed(world.society.chronicle_rows(8, public_only=True)))
        news_rows = []
        for item in news:
            news_rows += [item["headline"], item["body"],
                          f"Cause: {item['cause']} | Outcome: {item['outcome']}", ""]
        capture("03-generated-history", "Recent reports from the sea lanes",
                news_rows or ["No public reports yet."])

        headcount = (world.npcs.market["enslaved_people"]
                     + sum(ship["crew"] + ship["captives"] for ship in world.npcs.ships))
        assert headcount == world.npcs.population_total
        assert world.society.hour == world.npcs.hour == world.clock.total_ticks // 360000
        assert world.generation_manifest["seed"] == f"s:{args.seed}"
        assert person["name"] in "\n".join(generated_person_profile_rows(world, person))
        report.update({"passed": True, "world_hour": world.clock.total_ticks // 360000,
                       "headcount": headcount, "people": len(world.society.people),
                       "organizations": len(world.society.organizations),
                       "vessels": len(world.npcs.ships), "news_count": len(world.society.news)})
        (args.output / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Generated NPC walkthrough passed: {len(report['frames'])} screenshots; no save touched.")
    finally:
        renderer.shutdown()


if __name__ == "__main__":
    main()
