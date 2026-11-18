"""Save-free native two-port trade, provisioning and equipment walkthrough."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import play
from PIL import Image
from pirates.economy import MERCHANTS, PORTS
from pirates.trading_ui import market_rows, cargo_rows
from pirates.management import profile_rows
from omnisim.world import tile as T


def walk_to(world, target):
    for _ in range(600):
        route = world.ship.path(world, world.player, target)
        if route == []:
            return
        if route is None:
            world.advance(100)
            continue
        x, y = route[0][:2]
        if world.game_map.tile_id(x, y) == T.DOOR and not world.game_map.doors[y, x]:
            world.toggle_door()
            continue
        assert world.move(x - world.player.x, y - world.player.y)
    raise AssertionError(f"Could not reach {target}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    world = play.World()
    e = world.economy
    geometry = world.ship_model.snapshot()
    renderer = play.PiratesRenderer(settings=play.RenderSettings(world_font_size=20, world_cell_width=20, world_cell_height=24))
    manifest = {"frames": [], "starting_money": e.money}
    try:
        renderer.init_for(100, 64)
        def capture(name, title=None, rows=None):
            renderer.render(world)
            if title:
                renderer.render_list(world, title, rows, "Save-free trading walkthrough", selectable=False)
            renderer._present_layers()
            Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(args.output / f"{name}.png")
            manifest["frames"].append({"name": name, "island": e.port, "money": e.money, "cargo": dict(e.cargo)})
        walk_to(world, MERCHANTS["ada"]["at"])
        capture("01-verdant-market")
        capture("02-ada-conversation", "Ada Reed: grower", MERCHANTS["ada"]["dialogue"])
        capture("03-verdant-prices", "Verdant exports", market_rows(world, "ada"))
        e.trade(world, "ada", "grain", 10, True)
        walk_to(world, MERCHANTS["bo"]["at"])
        e.trade(world, "bo", "water_cask", 1, True)
        capture("04-bo-conversation", "Bo: water and fish", MERCHANTS["bo"]["dialogue"])
        walk_to(world, MERCHANTS["lina"]["at"])
        e.trade(world, "lina", "timber", 1, True)
        capture("05-lina-conversation", "Lina: forest and flax", MERCHANTS["lina"]["dialogue"])
        walk_to(world, (42, 23))
        e.travel(world, "cinder")
        walk_to(world, MERCHANTS["dara"]["at"])
        capture("06-cinder-market")
        capture("07-cinder-demand", "Dara: an island that imports food", market_rows(world, "dara"))
        e.trade(world, "dara", "grain", 10, False)
        walk_to(world, MERCHANTS["brann"]["at"])
        capture("08-brann-conversation", "Brann: tools and steel", MERCHANTS["brann"]["dialogue"])
        e.trade(world, "brann", "carpenter_tools", 2, True)
        e.trade(world, "brann", "cutlass", 1, True)
        walk_to(world, MERCHANTS["petra"]["at"])
        e.trade(world, "petra", "oilskin", 1, True)
        e.trade(world, "petra", "boots", 1, True)
        capture("09-petra-conversation", "Petra: practical kit", MERCHANTS["petra"]["dialogue"])
        walk_to(world, (26, 33))
        capture("10-industrial-shore")
        walk_to(world, (42, 23))
        e.travel(world, "verdant")
        walk_to(world, MERCHANTS["lina"]["at"])
        e.trade(world, "lina", "carpenter_tools", 1, False)
        walk_to(world, world.ship.stations["hold"])
        before_water = world.ship.stores["clean_water_ml"]
        before_planks = world.ship.stores["planks"]
        e.allocate(world, "water_cask", 1)
        e.allocate(world, "timber", 1)
        assert world.ship.stores["clean_water_ml"] >= before_water + 9000
        assert world.ship.stores["planks"] >= before_planks + 1
        for key in ("carpenter_tools", "cutlass", "oilskin", "boots"):
            e.equip(world, "mara", key)
        capture("11-cargo-equipment", "Purchased kit and cargo", cargo_rows(world))
        capture("12-equipped-carpenter", "Mara's useful equipment", profile_rows(world.ship.crew[0], world))
        capture("13-trade-history", "The two-island trading run", e.history)
        assert world.ship_model.snapshot() == geometry
        assert not world.ship.check_orders()
        assert set(e.gear["mara"]) == {"tool", "coat", "weapon", "feet"}
        manifest.update({"passed": True, "final_money": e.money, "islands": list(PORTS),
                         "gear": e.gear, "history": e.history, "geometry_unchanged": True})
        (args.output / "verification.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"Native two-island trading verified: {len(manifest['frames'])} screenshots; no save touched.")
    finally:
        renderer.shutdown()


if __name__ == "__main__":
    main()
