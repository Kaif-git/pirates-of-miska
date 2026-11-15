"""Native SDL capture of a nonphysical blueprint overlay and queued construction order."""
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import play
from PIL import Image
from pirates.ship_building import queue_blueprint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    world = play.World()
    world.blueprint.set(9, 15, 0, "build", "wall")
    renderer = play.PiratesRenderer(settings=play.RenderSettings(
        world_font_size=20, world_cell_width=20, world_cell_height=24))
    try:
        renderer.init_for(100, 64)
        renderer.zoom(-1, 100, 64)
        renderer.render(world)
        renderer._present_layers()
        Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(args.output / "planned-map.png")
        renderer.render_list(world, "Ship blueprint — plans are not physical",
                             ["Cursor: 9,15 deck 0 | Mode: build wall",
                              "Unbuilt changes: 1 | Copy: mirror X, rotate 90° | symmetry: off",
                              "Arrows move | B mode | Space point | T anchor | L line | G rectangle | F fill",
                              "Y symmetry | [ start | ] end | D destination | C copy | Z undo | Enter queue"],
                             "F4 advances crew work; Esc exits", selectable=False)
        renderer._present_layers()
        Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(args.output / "planned.png")
        jobs = queue_blueprint(world)
        assert len(jobs) == 1 and jobs[0].construction["kind"] == "wall"
        renderer.render(world)
        renderer._present_layers()
        Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(args.output / "queued-map.png")
        renderer.render_list(world, "Physical construction order queued",
                             [f"Job #{jobs[0].id}: build wall at {tuple(jobs[0].target)}",
                              "Crew must carry a plank bundle and complete carpentry work."],
                             "A manages/cancels jobs; F4 advances time", selectable=False)
        renderer._present_layers()
        Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(args.output / "queued.png")
        print(f"Native blueprint overlay and physical order verified: {args.output}")
    finally:
        renderer.shutdown()


if __name__ == "__main__":
    main()
