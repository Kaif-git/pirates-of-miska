"""Native SDL preview of a localized ship fire and its smoke overlay."""
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import play
from PIL import Image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path("C:/Users/Warp/AppData/Local/Temp/opencode/pirates-fire.png"))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    world = play.World()
    floor = next(c for c in world.ship_model.components if c.kind == "floor" and c.material != "iron"
                 and any(any(p.kind == "floor" for p in world.ship_model.at(c.x + dx, c.y + dy, c.z))
                         and not world.hazards._passage_closed(
                             world, (c.x, c.y, c.z), (c.x + dx, c.y + dy, c.z))
                         for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1))))
    world.hazards.ignite(world, floor.x, floor.y, floor.z, 2.0)
    for _ in range(20):
        world.hazards.advance(world)
    world.player.x, world.player.y = 69 + floor.x + 2, 12 + floor.y
    world.refresh()
    renderer = play.PiratesRenderer(settings=play.RenderSettings(
        world_font_size=20, world_cell_width=20, world_cell_height=24))
    try:
        renderer.init_for(100, 64)
        renderer.zoom(-1, 100, 64)
        renderer.render(world)
        renderer._present_layers()
        Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(args.output)
        assert world.hazards.fires and len(world.hazards.smoke) > 1
        print(f"Fire/smoke projection captured: {args.output}; {world.hazards.report()}")
    finally:
        renderer.shutdown()


if __name__ == "__main__":
    main()
