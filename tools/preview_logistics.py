"""Capture projected crew-route congestion and emergency-response diagnostics."""
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import play
from PIL import Image
from pirates.ship_logistics import inspect


def capture(renderer, world, path):
    renderer.render(world)
    renderer._present_layers()
    Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path("C:/Users/Warp/AppData/Local/Temp/opencode/pirates-logistics"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    world = play.World()
    for index, (sailor, at) in enumerate(zip(world.ship.crew[:2], ((77, 25), (77, 27)))):
        sailor.x, sailor.y, sailor.z = *at, 0
        sailor.labors["repair"] = 1
        job = world.ship.add_job(f"preview:{index}", "repair", world.ship.stations["repair"], work=20)
        job.state, job.worker, sailor.job_id = "claimed", sailor.id, job.id
    floor = next(c for c in world.ship_model.components if c.kind == "floor" and c.material != "iron")
    world.hazards.ignite(world, floor.x, floor.y, floor.z)
    report = inspect(world)
    world.traffic_overlay = report["traffic"]
    world.player.x, world.player.y = 77, 25
    world.refresh()
    renderer = play.PiratesRenderer(settings=play.RenderSettings(
        world_font_size=20, world_cell_width=20, world_cell_height=24))
    try:
        renderer.init_for(100, 64)
        renderer.zoom(-1, 100, 64)
        map_path = args.output / "traffic-overlay.png"
        capture(renderer, world, map_path)
        renderer.render_list(world, "Logistics / emergency response", report["rows"], selectable=False)
        renderer._present_layers()
        panel_path = args.output / "logistics-report.png"
        Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(panel_path)
        assert report["traffic"] and report["body_conflicts"] >= 0
        assert any(row.startswith("FIRE at") for row in report["rows"])
        print(f"Traffic map and logistics report captured under {args.output}")
    finally:
        renderer.shutdown()


if __name__ == "__main__":
    main()
