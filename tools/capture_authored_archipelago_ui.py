"""Capture save-free authored archipelago UI evidence through native SDL."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import play
import omnisim.ui.ascii_renderer as ascii_renderer
from omnisim.ui.render_settings import RenderSettings
from pirates.economy import MERCHANTS, build_port
from pirates.game import World
from pirates.management import sea_map_rows
from pirates.trading_ui import market_rows, port_guide_rows
from pirates.voyage import SEA_NODES


SIZES = ((1280, 800), (960, 700), (640, 600))


def new_renderer(size):
    ascii_renderer.WIN_PX = size
    renderer = play.PiratesRenderer(settings=RenderSettings(
        world_tileset="ascii", world_font_size=20,
        world_cell_width=20, world_cell_height=24))
    renderer.init_for(100, 64)
    renderer.context.sdl_window.size = size
    renderer._sync_console_size()
    actual = renderer.context.sdl_renderer.output_size
    if actual != size:
        raise RuntimeError(f"SDL output size {actual} did not match requested {size}")
    return renderer


def save_frame(renderer, output, size, name, world, title=None, rows=None,
               footer="Esc returns"):
    renderer.render(world)
    if title is not None:
        renderer.render_list(world, title, rows or [], footer, selectable=False)
    renderer._present_layers()
    path = output / f"{size[0]}x{size[1]}-{name}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(path)


def prepare_at_port(world, port):
    world.economy.port = port
    build_port(world)
    world.at_sea = False
    world.refresh()


def capture(output: Path):
    for size in SIZES:
        renderer = new_renderer(size)
        world = World.new_authored_mvp()
        save_frame(renderer, output, size, "scenario-status", world)
        save_frame(renderer, output, size, "public-port-guide", world,
                   "Island guide — public locations", port_guide_rows(world),
                   "Local quotes only; V opens the sea chart")

        for port, merchant_id in (("verdant", "ada"), ("cinder", "dara"), ("freeport", "nella")):
            prepare_at_port(world, port)
            save_frame(renderer, output, size, f"{port}-harbor", world)
            x, y = MERCHANTS[merchant_id]["at"]
            world.player.x, world.player.y = x + 1, y
            world.refresh()
            save_frame(renderer, output, size, f"{port}-local-market", world,
                       f"{MERCHANTS[merchant_id]['name']} — local prices",
                       market_rows(world, merchant_id),
                       "Ask buys; bid sells; stock and purses are finite")

        route_world = World.new_authored_mvp()
        options = route_world.voyages.route_options("verdant", "cinder")
        route_rows = [" → ".join(SEA_NODES[node]["name"] for node in option["route"])
                      + f" | {option['distance']:.2f} chart units; no prior lookout report"
                      for option in options]
        save_frame(renderer, output, size, "route-choice", route_world,
                   "Choose a chart route — Verdant Cay to Cinder Quay", route_rows,
                   "Equal-distance chart routes; no fare is charged")

        departure = World.new_authored_mvp()
        departure.voyages.confirm(departure, "freeport")
        departure.enter_sea()
        save_frame(renderer, output, size, "freeport-departure", departure,
                   "Underway — Freeport Exchange", sea_map_rows(departure),
                   "Canonical sailing; ship time continues")

        arrival = World.new_authored_mvp()
        prepare_at_port(arrival, "freeport")
        arrival.voyages.position = "freeport"
        arrival.voyages.confirm(arrival, "cinder")
        arrival.enter_sea()
        voyage = arrival.voyages.voyage
        voyage["duration_seconds"] = 1
        if voyage["storm_event"]:
            voyage["storm_event"].update({"triggered": False,
                                          "trigger_roll": voyage["storm_event"]["chance_percent"]})
        for _ in range(10):
            if voyage["status"] != "underway":
                break
            arrival.advance(100)
        if voyage["status"] != "arrived" or arrival.economy.port != "cinder":
            raise RuntimeError("Authored arrival did not return to Cinder Quay")
        save_frame(renderer, output, size, "arrival-free-control", arrival)

        with tempfile.TemporaryDirectory(prefix="authored-ui-") as folder:
            path = Path(folder) / "lawful-trader-mvp.json"
            arrival.save(path)
            resumed = World.load(path, expected_scenario_id="lawful-trader-mvp")
            save_frame(renderer, output, size, "resumed-at-cinder", resumed)

        renderer.context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "reports" / "authored-archipelago" / "ui",
                        help="Directory for native PNG captures")
    args = parser.parse_args()
    capture(args.output)
    print(f"Captured {len(SIZES) * 12} native SDL frames in {args.output}")


if __name__ == "__main__":
    main()
