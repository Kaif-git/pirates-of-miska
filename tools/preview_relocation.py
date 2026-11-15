"""Save-free native verification of physical fixture pickup and installation."""
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import play
from PIL import Image
from pirates.ship_relocation import queue
from pirates.ship_spaces import report_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    world = play.World()
    job = queue(world, "galley", (78, 27))
    job.priority = 1
    world.ship.assign(job.id, "mara")
    renderer = play.PiratesRenderer(settings=play.RenderSettings(
        world_font_size=20, world_cell_width=20, world_cell_height=24))
    captured = set()
    try:
        renderer.init_for(100, 64)
        renderer.zoom(-1, 100, 64)

        def capture(name, report=False):
            renderer.render(world)
            if report:
                renderer.render_list(world, "Relocated galley — actual equipment report",
                                     report_rows(world, 76, 25), selectable=False)
            renderer._present_layers()
            Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(args.output / f"{name}.png")

        capture("planned")
        for _ in range(900):
            world.advance(100)
            if job.relocation["stage"] == "haul" and "pickup" not in captured:
                capture("pickup")
                captured.add("pickup")
                assert world.fixtures.get("galley").carrier == "mara"
                assert not world.ship.check_orders()
            if job.state == "done":
                break
        assert job.state == "done", job.reason
        assert "pickup" in captured
        assert world.ship.stations["galley"] == (78, 27)
        assert not world.ship.check_orders()
        capture("installed")
        world.spaces.designate(world, 76, 25, "galley")
        capture("equipment-report", report=True)
        print(f"Native pickup/hauling/install verified at {world.ship.seconds}s; screenshots: {args.output}")
    finally:
        renderer.shutdown()


if __name__ == "__main__":
    main()
