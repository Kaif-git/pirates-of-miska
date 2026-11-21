"""Script the authored free-trade path and capture its native SDL screens."""
from __future__ import annotations

import argparse
from collections import deque
import json
from pathlib import Path
import re
import sys

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import play
import omnisim.ui.ascii_renderer as ascii_renderer
from omnisim.ui.render_settings import RenderSettings
from pirates.economy import MERCHANTS, build_port
from pirates.game import World
from pirates.management import sea_map_rows, voyage_menu
from pirates.sea_encounter import request_pending
from pirates.trading_ui import (cargo_menu, market_observation_rows, market_rows,
                                merchant_menu, port_guide_rows, port_menu)

SIZES = ((1280, 800), (960, 700), (640, 600))


def make_renderer(size):
    ascii_renderer.WIN_PX = size
    renderer = play.PiratesRenderer(settings=RenderSettings(
        world_tileset="ascii", world_font_size=20,
        world_cell_width=20, world_cell_height=24))
    renderer.init_for(100, 64)
    renderer.context.sdl_window.size = size
    renderer._sync_console_size()
    if renderer.context.sdl_renderer.output_size != size:
        raise RuntimeError(f"SDL output size mismatch for {size}")
    return renderer


def walk_to(world, merchant_id):
    mx, my = MERCHANTS[merchant_id]["at"]
    moves = 0
    while abs(world.player.x - mx) + abs(world.player.y - my) > 2:
        moves += 1
        if moves > 200:
            raise RuntimeError(f"Walking to {merchant_id} exceeded 200 actual moves; last position "
                               f"was {(world.player.x, world.player.y)}")
        start = (world.player.x, world.player.y)
        blocked = {(e.x, e.y) for e in world.all_entities()
                   if e is not world.player and getattr(e, "z", 0) == world.player.z}
        queue = deque([start])
        parent = {start: None}
        goal = None
        while queue:
            pos = queue.popleft()
            if abs(pos[0] - mx) + abs(pos[1] - my) <= 2:
                goal = pos
                break
            x, y = pos
            for nxt in ((x, y - 1), (x + 1, y), (x, y + 1), (x - 1, y)):
                if nxt in parent or nxt in blocked or not world.game_map.is_walkable(*nxt):
                    continue
                parent[nxt] = pos
                queue.append(nxt)
        if goal is None:
            raise RuntimeError(f"No normal walking path to {merchant_id}")
        path = []
        pos = goal
        while parent[pos] is not None:
            path.append(pos)
            pos = parent[pos]
        path.reverse()
        if path:
            nxt = path[0]
            if not world.move(nxt[0] - start[0], nxt[1] - start[1]):
                raise RuntimeError(f"Walking to {merchant_id} was blocked at {start} → {nxt}; "
                                   f"tile={world.game_map.tile_id(*nxt)}, "
                                   f"walkable={world.game_map.is_walkable(*nxt)}")
    if not world.game_map.visible[my, mx]:
        raise RuntimeError(f"Merchant {merchant_id} is not locally visible")


def capture_native(world, title, rows, footer, *, selectable=True):
    capture_native.sequence += 1
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48] or "screen"
    for size, renderer in capture_native.renderers.items():
        renderer.render_list(world, title, list(rows), footer, selectable=selectable)
        renderer._present_layers()
        path = capture_native.output / f"{capture_native.sequence:03d}-{slug}-{size[0]}x{size[1]}.png"
        Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(path)
    capture_native.index.append({"sequence": capture_native.sequence, "title": title,
                                 "rows": list(rows), "footer": footer,
                                 "selectable": selectable})


capture_native.sequence = 0
capture_native.renderers = {}
capture_native.output = Path()
capture_native.index = []
capture_native.answer = None
capture_native.choice_count = 0
capture_native.choice_limit = 80


def scripted_choose(_renderer, world, title, rows, footer="Enter selects; Esc returns", *_args, **_kwargs):
    capture_native.choice_count += 1
    if capture_native.choice_count > capture_native.choice_limit:
        raise RuntimeError(f"Walkthrough exceeded {capture_native.choice_limit} scripted menu choices; "
                           f"last menu was {title!r}")
    capture_native(world, title, rows, footer, selectable=True)
    if capture_native.answer is None:
        raise AssertionError(f"No scripted choice handler for {title!r}")
    return capture_native.answer(title, list(rows))


def scripted_panel(_renderer, world, title, rows, *_args, **_kwargs):
    capture_native(world, title, rows, "Esc returns", selectable=False)


def queued_answers(values):
    pending = iter(values)
    return lambda _title, _rows: next(pending)


def sail_to_arrival(world):
    voyage = world.voyages.voyage
    storm = voyage.get("storm_event")
    if storm:
        # A calm-weather presentation control; route and all canonical time remain active.
        storm.update({"triggered": False, "trigger_roll": storm["chance_percent"]})
    steps = 0
    while voyage["status"] not in ("arrived", "aborted"):
        world.advance(6000)
        steps += 1
        if voyage["status"] == "encounter":
            if request_pending(voyage):
                raise RuntimeError("Walkthrough unexpectedly needs an unplanned sighting decision")
            world.voyages.continue_after_sighting()
        elif voyage["status"] == "storm":
            world.voyages.resolve_storm(world, "press_on")
        if steps > 3000:
            raise RuntimeError("Authored UI walkthrough did not arrive")


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    capture_native.output = output
    capture_native.sequence = 0
    capture_native.index = []
    capture_native.choice_count = 0
    capture_native.renderers = {size: make_renderer(size) for size in SIZES}

    import pirates.management as management
    import pirates.trading_ui as trading_ui

    old_choose_m, old_panel_m = management.choose, management.panel
    old_choose_t, old_panel_t = trading_ui.choose, trading_ui.panel
    management.choose = trading_ui.choose = scripted_choose
    management.panel = trading_ui.panel = scripted_panel
    world = World.new_authored_mvp()
    try:
        walk_to(world, "ada")
        print("Stage: local guide, unknown notes and chart", flush=True)
        capture_native(world, "Island guide — public locations", port_guide_rows(world),
                       "Public facts only; inspect a merchant for current local quotes", selectable=False)
        capture_native(world, "Unknown remote market notes", market_observation_rows(world, "nella"),
                       "Remote quotes remain unknown until local inspection", selectable=False)
        capture_native(world, "Sea chart — commercial notes", sea_map_rows(world),
                       "Chart review never refreshes remote market data", selectable=False)

        capture_native.answer = queued_answers((3, 4))
        print("Stage: inspect Ada's local quotes", flush=True)
        merchant_menu(capture_native.renderers[SIZES[0]], world, "ada")
        capture_native.answer = queued_answers((1, 0, 1, 2, 4))
        print("Stage: preview and freely cancel a buy", flush=True)
        cash_before_cancel = world.economy.money
        ticks_before_cancel = world.clock.total_ticks
        merchant_menu(capture_native.renderers[SIZES[0]], world, "ada")
        if world.economy.money != cash_before_cancel or world.clock.total_ticks != ticks_before_cancel:
            raise AssertionError("Cancelled purchase changed cash or time")

        capture_native.answer = queued_answers((1, 0, 1, 0, 4))
        print("Stage: buy finite cargo through merchant UI", flush=True)
        merchant_menu(capture_native.renderers[SIZES[0]], world, "ada")
        if world.economy.cargo.get("grain") != 5:
            raise AssertionError("Normal merchant UI purchase did not deliver the chosen cargo")

        # Demonstrate a legitimate bad choice at the same merchant without softlocking trade.
        loss_start = world.economy.money
        capture_native.answer = queued_answers((1, 0, 0, 0, 4))
        merchant_menu(capture_native.renderers[SIZES[0]], world, "ada")
        capture_native.answer = queued_answers((2, 0, 0, 0, 4))
        merchant_menu(capture_native.renderers[SIZES[0]], world, "ada")
        if world.economy.money >= loss_start or world.economy.cargo.get("grain") != 5:
            raise AssertionError("Same-market buy/sell did not show a real loss while retaining route cargo")

        capture_native.answer = lambda title, rows: (
            next(capture_native.voyage_commands) if title.startswith("Voyage command") else
            rows.index(next(capture_native.destinations)) if title.startswith("Choose destination") else
            0 if title.startswith("Choose a chart route") or title.startswith("Commit this voyage order") else
            None)
        capture_native.voyage_commands = iter((4, None))
        capture_native.destinations = iter(("Freeport Exchange", "Cinder Quay"))
        world.npcs.ships = []  # Controlled presentation run; no rival contact/campaign combat.
        print("Stage: order and begin charted passage", flush=True)
        voyage_menu(capture_native.renderers[SIZES[0]], world)
        if world.voyages.voyage["status"] != "underway" or not world.at_sea:
            raise AssertionError("Voyage UI did not commit free chart passage")
        capture_native(world, "Underway — Freeport crossing", sea_map_rows(world),
                       "Canonical sailing; this controlled walkthrough has no NPC contacts", selectable=False)
        # Keep canonical sailing/upkeep/arrival active, but accelerate this presentation-only walkthrough.
        world.voyages.voyage["duration_seconds"] = 1
        print("Stage: advance the canonical passage to arrival", flush=True)
        sail_to_arrival(world)
        if world.economy.port != "freeport":
            raise AssertionError("Voyage did not arrive at Freeport Exchange")

        walk_to(world, "nella")
        print("Stage: sell at Freeport before opening the report", flush=True)
        capture_native.answer = queued_answers((2, 0, 1, 0, 4))
        merchant_menu(capture_native.renderers[SIZES[0]], world, "nella")
        if world.economy.cargo or world.economy.markets["nella"]["stock"]["grain"] != 5:
            raise AssertionError("Normal merchant UI sale did not clear the selected cargo")

        capture_native.answer = queued_answers((8, None))
        cargo_menu(capture_native.renderers[SIZES[0]], world)
        capture_native.voyage_commands = iter((6, 4, None))
        capture_native.answer = lambda title, rows: (
            next(capture_native.voyage_commands) if title.startswith("Voyage command") else
            rows.index(next(capture_native.destinations)) if title.startswith("Choose destination") else
            0 if title.startswith("Choose a chart route") or title.startswith("Commit this voyage order") else
            None)
        print("Stage: read optional arrival report, then order another voyage", flush=True)
        voyage_menu(capture_native.renderers[SIZES[0]], world)
        if world.voyages.voyage["status"] != "underway" or world.economy.money == 0:
            raise AssertionError("Captain could not order another free chart voyage after trading")

        capture_native(world, "Remembered Ada quote after remote market change",
                       market_observation_rows(world, "ada"), "Historical local observation", selectable=False)
        remembered = market_observation_rows(world, "ada")
        if (not any("not a live or executable offer" in row for row in remembered)
                or not any("30 in stock then" in row for row in remembered)
                or not any("purse then: 1500 Marks" in row for row in remembered)):
            raise AssertionError("Remembered market screen lacks a non-live label")

        # Separate synthetic failure fixture: an exhausted local buyer purse must not transfer cargo or time.
        failed = World.new_authored_mvp()
        failed.economy.port = "freeport"
        build_port(failed)
        failed.player.x, failed.player.y = MERCHANTS["nella"]["at"][0] + 1, MERCHANTS["nella"]["at"][1]
        failed.refresh()
        failed.economy.cargo = {"grain": 1}
        failed.economy.markets["nella"]["money"] = 0
        failed_cash, failed_ticks = failed.economy.money, failed.clock.total_ticks
        capture_native.answer = queued_answers((2, 0, 0, 0, 4))
        merchant_menu(capture_native.renderers[SIZES[0]], failed, "nella")
        if (failed.economy.money != failed_cash or failed.clock.total_ticks != failed_ticks
                or failed.economy.cargo != {"grain": 1}):
            raise AssertionError("Rejected sale changed state or time")
    finally:
        management.choose, management.panel = old_choose_m, old_panel_m
        trading_ui.choose, trading_ui.panel = old_choose_t, old_panel_t
        for renderer in capture_native.renderers.values():
            renderer.context.close()
    (output / "screens.json").write_text(json.dumps(capture_native.index, indent=2), encoding="utf-8")
    print(f"Captured {capture_native.sequence} authored UI states × {len(SIZES)} native sizes in {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path.home() / "AppData/Local/Temp/opencode/free-trade-m3-ui-verified")
    args = parser.parse_args()
    run(args.output)


if __name__ == "__main__":
    main()
