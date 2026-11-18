"""Native SDL proof of a damaged outer hull, contained ingress, and water marker."""
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import play
from PIL import Image
from pirates.ship_hazards import ShipHazards
from pirates.ship_map import ORIGIN


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path("C:/Users/Warp/AppData/Local/Temp/opencode/pirates-flooding.png"))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    world = play.World()
    world.ship.hull = 100
    wall = next(c for c in world.ship_model.components
                if c.kind == "wall" and ShipHazards._outer_wall(world, c.x, c.y, c.z))
    world.hazards.breach(world, wall.x, wall.y, wall.z)
    for _ in range(180):
        world.hazards.advance(world)
    assert world.hazards.breaches
    assert world.hazards.total_water_kg() > 180
    world.player.x, world.player.y = ORIGIN[0] + wall.x + 1, ORIGIN[1] + wall.y
    world.refresh()
    renderer = play.PiratesRenderer(settings=play.RenderSettings(
        world_font_size=20, world_cell_width=20, world_cell_height=24))
    try:
        renderer.init_for(100, 64)
        renderer.zoom(-1, 100, 64)
        renderer.render(world)
        renderer._present_layers()
        Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(args.output)
        print(f"Breach/flooding projection captured: {args.output}; {world.hazards.report()}")
    finally:
        renderer.shutdown()


if __name__ == "__main__":
    main()
