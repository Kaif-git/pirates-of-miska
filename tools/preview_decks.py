"""Native SDL proof of deck projection and stair-only captain elevation changes."""
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import play
from PIL import Image
from pirates.ship_map import ORIGIN


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path("C:/Users/Warp/AppData/Local/Temp/opencode/pirates-upper-deck.png"))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    world = play.World()
    local = (3, 2)
    for x in range(local[0], local[0] + 4):
        world.ship_model.place(x, local[1], 1, "floor")
    world.ship_model.place(local[0], local[1], 0, "stair")
    world.ship_model.place(local[0] + 1, local[1], 1, "door")
    upper_pile = world.ship.storage.designate(
        world.ship_model, world.ship_fixtures,
        local[0] + 2 + ORIGIN[0], local[1] + ORIGIN[1], z=1)
    upper_pile.contents["planks"] = 1
    world.ground_items[(local[0] + 2 + ORIGIN[0], local[1] + ORIGIN[1], 1)] = [{
        "id": "preview-rope", "name": "Preview rope", "quantity": 1,
        "weight_g": 1500, "bulk_ml": 2500}]
    world.player.x, world.player.y = local[0] + ORIGIN[0], local[1] + ORIGIN[1]
    assert world.move_vertical(1)
    assert world.active_deck == world.player.z == 1
    assert (local[0] + 2 + ORIGIN[0], local[1] + ORIGIN[1]) in world.ground
    # Rendering proof only: the physical cross-deck install is covered by tests.
    world.bind_fixtures(world.fixtures.relocated(
        "galley", local[0] + 3 + ORIGIN[0], local[1] + ORIGIN[1], z=1))
    renderer = play.PiratesRenderer(settings=play.RenderSettings(
        world_font_size=20, world_cell_width=20, world_cell_height=24))
    try:
        renderer.init_for(100, 64)
        renderer.zoom(-1, 100, 64)
        renderer.render(world)
        renderer._present_layers()
        Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(args.output)
        print(f"Live upper-deck projection and stair transition captured: {args.output}")
    finally:
        renderer.shutdown()


if __name__ == "__main__":
    main()
