"""Save-free native preview of port prospects and a new crew profile."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import play
from PIL import Image
from pirates.management import profile_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    world = play.World()
    world.player.x, world.player.y, world.player.z = 32, 22, 0
    renderer = play.PiratesRenderer(settings=play.RenderSettings(
        world_font_size=20, world_cell_width=20, world_cell_height=24))
    candidates = world.economy.recruitment.at_port(world.economy.port)
    try:
        renderer.init_for(100, 64)

        def capture(name, title, rows):
            renderer.render(world)
            renderer.render_list(world, title, rows, "Save-free recruitment preview", selectable=False)
            renderer._present_layers()
            Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(args.output / f"{name}.png")

        prospect_rows = [
            f"{row['name']} | {row['background']}, {row['age']} | signing advance {row['asking_price']} Marks"
            for row in candidates
        ]
        capture("01-port-prospects", "Crew prospects at Verdant Cay", prospect_rows)
        world.economy.recruit_candidate(world, "pippin_ash")
        recruit = world.ship.sailor("hire:pippin_ash")
        capture("02-new-crew-profile", recruit.name, profile_rows(recruit, world))
        assert len(world.ship.crew) == 7
        assert world.fixtures.get("bunk:6") is not None
        assert world.economy.money == 565
        assert world.npcs.market["enslaved_people"] == 5
        result = {"passed": True, "prospects": [row["id"] for row in candidates],
                  "recruited": recruit.id, "crew_count": len(world.ship.crew),
                  "bunk_count": len(world.fixtures.bunks), "money": world.economy.money,
                  "captive_headcount_unchanged": world.npcs.market["enslaved_people"] == 5}
        (args.output / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    finally:
        renderer.shutdown()


if __name__ == "__main__":
    main()
