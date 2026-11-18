"""Native SDL preview of ship lockdown and physical door breach counterplay."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import play
from PIL import Image
from pirates.ship_boarding import route_boarding_party
from pirates.ship_map import ORIGIN


def capture(renderer, world, path):
    world.refresh()
    renderer.render(world)
    renderer._present_layers()
    Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path("C:/Users/Warp/AppData/Local/Temp/opencode/pirates-boarding"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    world = play.World()
    for x in (3, 4, 5):
        world.ship_model.place(x, 2, 1, "floor")
    door = world.ship_model.place(4, 2, 1, "door")
    world.ship_model.place(3, 2, 0, "stair")
    world.player.x, world.player.y, world.player.z = 69 + 3, 12 + 2, 1
    world.set_active_deck(1)
    world.set_lockdown(True)
    renderer = play.PiratesRenderer(settings=play.RenderSettings(
        world_font_size=20, world_cell_width=20, world_cell_height=24))
    try:
        renderer.init_for(100, 64)
        renderer.zoom(-1, 100, 64)
        before = args.output / "lockdown.png"
        capture(renderer, world, before)
        result = route_boarding_party(world, (3, 2, 1), (5, 2, 1), seconds_per_door=2)
        after = args.output / "breached.png"
        capture(renderer, world, after)
        assert result["breached"] == ((door.x, door.y, door.z),)
        world.set_active_deck(0)
        world.player.z = 0
        from pirates.ship_hazards import ShipHazards
        hull = next(c for c in world.ship_model.components if c.kind == "wall"
                    and ShipHazards._outer_wall(world, c.x, c.y, c.z))
        world.hazards.breach(world, hull.x, hull.y, hull.z)
        entry = (hull.x + 69, hull.y + 12)
        assert world.combat.start_boarding(world, entry, count=2, seed=7)
        boarding = args.output / "boarding.png"
        capture(renderer, world, boarding)
        (args.output / "verification.json").write_text(json.dumps({
            "lockdown": world.lockdown,
            "breached": [list(at) for at in result["breached"]],
            "delay_seconds": result["delay_seconds"],
            "door_destroyed": not any(c.id == door.id for c in world.ship_model.components),
            "boarding_combatants": len(world.combat.fighters),
            "screenshots": [str(before), str(after), str(boarding)],
        }, indent=2), encoding="utf-8")
        print(f"Lockdown and forced-door previews captured under {args.output}")
    finally:
        renderer.shutdown()


if __name__ == "__main__":
    main()
