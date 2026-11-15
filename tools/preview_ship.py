"""Save-free native SDL walkthrough with optional developer visibility reveal."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import play
from PIL import Image
from pirates.management import profile_rows, ship_rows, work_rows, observe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reveal", action="store_true", help="Reveal geometry for inspection, not normal play")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    world = play.World()
    world.ship.assign(next(j.id for j in world.ship.jobs if j.kind == "repair"), "mara")
    renderer = play.PiratesRenderer(settings=play.RenderSettings(
        world_font_size=20, world_cell_width=20, world_cell_height=24))
    verification = {"developer_reveal": args.reveal, "frames": []}
    try:
        renderer.init_for(100, 64)
        renderer.zoom(-1, 100, 64)
        def capture(name, title=None, rows=None):
            if args.reveal:
                world.game_map.visible[:] = True
                world.game_map.explored[:] = True
            renderer.render(world)
            if title:
                renderer.render_list(world, title, rows, "Preview; no save file touched", selectable=False)
            renderer._present_layers()
            Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(args.output / f"{name}.png")
            verification["frames"].append({"name": name, "second": world.ship.seconds,
                "crew": [{"name": c.name, "x": c.x, "y": c.y, "activity": c.activity,
                          "completed": c.completed} for c in world.ship.crew],
                "hull": world.ship.hull, "bilge": world.ship.bilge, "meals": world.ship.meals})
        capture("00-ship")
        for second in (10, 30, 60, 180, 600):
            world.advance((second - world.ship.seconds) * 100)
            capture(f"{second:03d}-crew")
            if second == 10:
                capture("010-orders", "Live assignments / need pauses", work_rows(world))
        capture("ship-ledger", "Wayward ledger", ship_rows(world))
        capture("crew-thoughts", "Mara: needs, wants and memories", profile_rows(world.ship.crew[0]))
        capture("crew-events", "Completed work / crew events", world.ship.events)
        capture("work-compliance", "Who is following orders?", work_rows(world))
        capture("work-audit", "Recorded work transitions", [
            f"{r['second']}s {r['crew'] or '-'} #{r['job'] or '-'} {r['event']}: {r['reason']}"
            for r in world.ship.audit[-22:]])
        observe(renderer, world, seconds=3, frame_delay=0)
        assert world.ship.hull == 100 and world.ship.bilge < 3
        assert all(world.ship.ammo.values())
        assert sum(c.completed for c in world.ship.crew) >= 9
        assert not world.ship.check_orders()
        verification["passed"] = True
        (args.output / "verification.json").write_text(json.dumps(verification, indent=2), encoding="utf-8")
        print(f"Native ship walkthrough verified; {len(verification['frames'])} screenshots in {args.output}")
    finally:
        renderer.shutdown()


if __name__ == "__main__":
    main()
