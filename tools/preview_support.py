"""Capture a live support-beam bridge over an irregular multi-deck opening."""
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import play
from PIL import Image
from pirates.ship_map import ORIGIN
from pirates.ship_support import assess, world_loads


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path("C:/Users/Warp/AppData/Local/Temp/opencode/pirates-support.png"))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    world = play.World()
    # Existing lower bearing at (2,2); two oak beams bridge to an upper floor.
    world.ship_model.place(0, 2, 1, "floor")
    world.ship_model.place(0, 2, 0, "beam", "oak")
    world.ship_model.place(1, 2, 0, "beam", "oak")
    report = assess(world.ship_model, world_loads(world))
    assert (0, 2, 1) in report["supported"]
    assert report["beam_spans"]
    world.player.x, world.player.y = ORIGIN[0] + 2, ORIGIN[1] + 2
    world.refresh()
    renderer = play.PiratesRenderer(settings=play.RenderSettings(
        world_font_size=20, world_cell_width=20, world_cell_height=24))
    try:
        renderer.init_for(100, 64)
        renderer.zoom(-1, 100, 64)
        renderer.render(world)
        renderer._present_layers()
        Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(args.output)
        print(f"Structural support map captured: {args.output}; {report['beam_spans']}")
    finally:
        renderer.shutdown()


if __name__ == "__main__":
    main()
