"""Save-free native walkthrough of a crew-haul between real stockpile locations."""
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import play
from PIL import Image
from pirates.ship_storage import queue_transfer
from pirates.management import ship_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    world = play.World()
    world.ship.hull = world.ship.rigging = 100
    world.ship.bilge = 0
    world.ship.meals = 10
    world.ship.ammo = {key: 1 for key in world.ship.ammo}
    world.ship.dirt = {key: 0 for key in world.ship.dirt}
    world.ship.jobs = []
    pile = world.ship.storage.designate(world.ship_model, world.ship_fixtures, 78, 27, 500)
    job = queue_transfer(world, "hold", pile.id, {"planks": 1})
    job.priority = 1
    world.ship.assign(job.id, "mara")
    renderer = play.PiratesRenderer(settings=play.RenderSettings(
        world_font_size=20, world_cell_width=20, world_cell_height=24))
    captured = False
    try:
        renderer.init_for(100, 64)
        renderer.zoom(-1, 100, 64)

        def capture(name, modal=False):
            renderer.render(world)
            if modal:
                renderer.render_list(world, "Spatial stockpiles — actual counts and capacities",
                                     ship_rows(world), selectable=False)
            renderer._present_layers()
            Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(args.output / f"{name}.png")

        capture("reserved")
        for _ in range(900):
            world.advance(100)
            if world.ship.crew[0].cargo and not captured:
                assert pile.contents == {}
                assert world.ship.check_orders() == []
                capture("carried")
                captured = True
            if job.state == "done":
                break
        assert captured and job.state == "done", job.reason
        assert pile.contents == {"planks": 1}
        assert world.ship.stores["planks"] == 16
        assert world.ship.check_orders() == []
        capture("delivered", modal=True)
        print(f"Native stockpile transfer verified in {world.ship.seconds}s; screenshots: {args.output}")
    finally:
        renderer.shutdown()


if __name__ == "__main__":
    main()
