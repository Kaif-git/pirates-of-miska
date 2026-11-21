"""Native authored-scenario ship-preparation walkthrough and UI captures."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

import play
import omnisim.ui.ascii_renderer as ascii_renderer
from omnisim.ui.render_settings import RenderSettings
from pirates.economy import MERCHANTS
from pirates.game import World
from pirates.management import (CONSTRUCTION_HELP, CONSTRUCTION_OPTIONS, STORAGE_HELP,
                                STORAGE_OPTIONS, transfer_review_rows, work_rows)
from pirates.provisioning import labor_coverage_rows, readiness_rows
from pirates.ship import LABORS
from pirates.ship_building import queue
from pirates.ship_storage import queue_transfer

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
        renderer.shutdown()
        raise RuntimeError(f"SDL output size {actual} did not match requested {size}")
    return renderer


def capture(renderer, output, size, name, world, title, rows, footer):
    renderer.render(world)
    renderer.render_list(world, title, rows, footer, selectable=False)
    renderer._present_layers()
    path = output / f"{size[0]}x{size[1]}-{name}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(path)
    return {"name": name, "size": size, "path": str(path), "title": title,
            "row_count": len(rows)}


def advance_until(world, predicate, limit_seconds=1800):
    for _ in range(limit_seconds):
        if predicate():
            return
        world.advance(100)
    raise AssertionError("Preparation walkthrough timed out: " + repr([
        (job.id, job.state, job.reason, job.phase) for job in world.ship.jobs
        if job.state not in ("done", "cancelled")]))


def at_merchant(world, merchant_id):
    x, y = MERCHANTS[merchant_id]["at"]
    world.player.x, world.player.y = x + 1, y
    world.refresh()


def at_hold(world):
    x, y = world.ship.stations["hold"]
    world.player.x, world.player.y = x - 1, y
    world.refresh()


def prepared_walkthrough(renderer, output, size):
    world = World.new_authored_mvp()
    if world.economy.port != MERCHANTS["lina"]["port"]:
        raise AssertionError("Authored fresh-start port changed; update the verified merchant route")
    for job in list(world.ship.jobs):
        world.ship.cancel(job.id)
    for sailor in world.ship.crew:
        sailor.labors = {kind: 0 for kind in LABORS}
        sailor.intent = ""
        sailor.hunger = sailor.thirst = sailor.fatigue = sailor.loneliness = sailor.boredom = 0
        sailor.sleep_debt = 20

    frames = []
    frames.append(capture(renderer, output, size, "construction-menu", world,
                          "Construction at connected deck site", list(CONSTRUCTION_OPTIONS),
                          CONSTRUCTION_HELP))
    frames.append(capture(renderer, output, size, "storage-menu", world,
                          "Spatial storage — location and contents matter", list(STORAGE_OPTIONS),
                          STORAGE_HELP))

    # Buy a real two-charge plank bundle at Verdant, then allocate it to the hold.
    at_merchant(world, "lina")
    money_before = world.economy.money
    market_before = world.economy.markets["lina"]["stock"]["planks"]
    world.economy.trade(world, "lina", "planks", 1, True)
    if world.economy.money >= money_before or world.economy.markets["lina"]["stock"]["planks"] != market_before - 1:
        raise AssertionError("Finite plank purchase did not debit money and market stock")
    at_hold(world)
    planks_before_allocation = world.ship.stores["planks"]
    world.economy.allocate(world, "planks", 1)
    if world.ship.stores["planks"] != planks_before_allocation + 2:
        raise AssertionError("Catalog bundle yield was not allocated exactly once")

    # A real tool purchase and assignment: rope improves rig work, not construction.
    at_merchant(world, "lina")
    world.economy.trade(world, "lina", "rope", 1, True)
    at_hold(world)
    worker = world.ship.crew[0]
    world.economy.equip(world, worker.id, "rope")
    world.ship.set_labor(worker.id, "rig", 2)

    # Fix a real potable-water shortage through paid cargo and finite allocation.
    world.ship.stores["clean_water_ml"] = 0
    at_merchant(world, "bo")
    water_stock_before = world.economy.markets["bo"]["stock"]["spring_water_refill"]
    water_money_before = world.economy.money
    world.economy.trade(world, "bo", "spring_water_refill", 1, True)
    if (world.economy.markets["bo"]["stock"]["spring_water_refill"] != water_stock_before - 1
            or world.economy.money >= water_money_before):
        raise AssertionError("Finite water purchase did not debit money and market stock")
    at_hold(world)
    water_before = world.ship.clean_water_total_ml()
    world.economy.allocate(world, "spring_water_refill", 1)
    if world.ship.clean_water_total_ml() <= water_before:
        raise AssertionError("Allocated water did not improve the readiness forecast")

    # A real blocked floor-extension order is repaired via the existing repair labor.
    deck_target = (72, 13)
    world.player.x, world.player.y = deck_target[0], deck_target[1] + 1
    world.refresh()
    build_job = queue(world, deck_target, "build", "floor")
    # The captain's current tile is the only reachable approach to this edge
    # extension. Step clear through the ordinary movement path before workers
    # haul; leaving the captain there correctly blocks construction.
    if not world.move(0, 1):
        raise AssertionError("Captain could not clear the construction approach tile")
    world.advance(100)
    if build_job.state != "blocked":
        raise AssertionError(f"Missing repair labor did not block the order: {build_job.state}")
    frames.append(capture(renderer, output, size, "blocked-work", world,
                          "Work compliance — blocked construction", work_rows(world),
                          "Enable repair labor, assign a worker, and inspect the same order"))
    world.ship.set_labor(worker.id, "repair", 1)
    world.ship.assign(build_job.id, worker.id)
    advance_until(world, lambda: bool(world.ship.sailor(worker.id).cargo))
    if world.ship.sailor(worker.id).cargo != {"planks": 1}:
        raise AssertionError("The construction worker did not physically carry its reserved plank")

    # Save and reload in transit, then finish the exact same construction job.
    with tempfile.TemporaryDirectory(prefix="ship-preparation-build-") as folder:
        save_path = Path(folder) / "lawful-trader-mvp.json"
        world.save(save_path)
        world = World.load(save_path, expected_scenario_id="lawful-trader-mvp")
    build_job = world.ship.job(build_job.id)
    worker = world.ship.sailor(worker.id)
    if worker.cargo != {"planks": 1} or build_job.state not in ("claimed", "returning"):
        raise AssertionError(f"Construction save/reload changed transit state: cargo={worker.cargo}, "
                             f"job={build_job.state}/{build_job.phase}/{build_job.reason}")
    frames.append(capture(renderer, output, size, "resumed-construction", world,
                          "Resumed construction — material still in transit", work_rows(world),
                          "The saved job and carried bundle resume under canonical time"))
    advance_until(world, lambda: build_job.state == "done")
    if world.ship_model.deck_geometry(0)["tiles"] < 1:
        raise AssertionError("Constructed deck did not project into ship geometry")
    if not any(component.kind == "floor" and (component.x, component.y, component.z) == (3, 1, 0)
               for component in world.ship_model.components):
        raise AssertionError("The designated connected floor was not physically constructed")

    # The added floor supplies a valid physical stock location; hauling changes piles, not totals.
    pile = world.ship.storage.designate(world.ship_model, world.ship_fixtures,
                                        *deck_target, capacity_kg=750)
    source = world.ship.storage.piles["hold"]
    total_planks_before_haul = world.ship.stores["planks"]
    transfer_bundle = {"planks": 1}
    frames.append(capture(renderer, output, size, "transfer-review", world,
                          "Review physical transfer",
                          transfer_review_rows(world, source, pile, transfer_bundle),
                          "Confirm queues the physical haul; cancel spends no time"))
    # Transfer uses the existing repair labor. Turn off ordinary repair
    # preferences and directly assign this order so automatic hull maintenance
    # cannot consume planks during the conservation check.
    world.ship.set_labor(worker.id, "repair", 0)
    transfer = queue_transfer(world, source.id, pile.id, transfer_bundle)
    world.ship.set_labor(worker.id, "repair", 1)
    world.ship.assign(transfer.id, worker.id)
    advance_until(world, lambda: bool(world.ship.sailor(worker.id).cargo))
    if world.ship.sailor(worker.id).cargo != transfer_bundle:
        raise AssertionError("Transfer worker did not carry the reviewed bundle")

    # A second saved transit proves source identity and quantity survive an in-flight stock move.
    with tempfile.TemporaryDirectory(prefix="ship-preparation-haul-") as folder:
        save_path = Path(folder) / "lawful-trader-mvp.json"
        world.save(save_path)
        world = World.load(save_path, expected_scenario_id="lawful-trader-mvp")
    transfer = world.ship.job(transfer.id)
    worker = world.ship.sailor(worker.id)
    if worker.cargo != transfer_bundle:
        raise AssertionError("Reloaded transfer lost its carried stock")
    frames.append(capture(renderer, output, size, "resumed-hauling", world,
                          "Resumed physical haul — cargo retained", work_rows(world),
                          "The worker returns to the original source/destination plan"))
    advance_until(world, lambda: transfer.state == "done")
    world.ship.set_labor(worker.id, "repair", 0)
    if (world.ship.storage.piles[pile.id].contents != transfer_bundle
            or world.ship.stores["planks"] != total_planks_before_haul):
        raise AssertionError("Physical stock transfer duplicated or lost planks: "
                             f"pile={world.ship.storage.piles[pile.id].contents}, "
                             f"total={world.ship.stores['planks']} before={total_planks_before_haul}, "
                             f"job={transfer.state}/{transfer.reason}, "
                             f"cargo={world.ship.sailor(worker.id).cargo}, "
                             f"piles={[(pid, row.contents.get('planks', 0)) for pid, row in world.ship.storage.piles.items()]}, "
                             f"audit={world.ship.audit[-15:]}")

    # Inspect issued tool, work coverage and warning-only departure readiness.
    worker = world.ship.sailor(worker.id)
    if world.economy.gear.get(worker.id, {}).get("tool") != "rope":
        raise AssertionError("Issued rope tool did not persist")
    world.voyages.assign_role(world, "purser", None)
    world.voyages.assign_role(world, "captain", worker.id)
    coverage = labor_coverage_rows(world)
    crew_coverage = [coverage[0], *(row for row in coverage if row.startswith("Tend rigging:")), coverage[-1]]
    frames.append(capture(renderer, output, size, "crew-equipment", world,
                          "Crew coverage — labor, equipment and officer posts",
                          crew_coverage,
                          "Enabled labor, matching issued tools and officer appointments; needs still apply"))

    world.ship.hull = 40
    review = world.voyages.role_report(world)
    readiness = readiness_rows(world)
    frames.append(capture(renderer, output, size, "readiness", world,
                          "Ship readiness — inspect before departure", readiness,
                          "Read-only; warnings are distinct from existing voyage rules"))
    readiness_systems = [row for row in readiness if row.startswith((
        "SHIP AND CREW PREPARATION", "Labor coverage", "Tend rigging:",
        "Unfinished work:", "Medical stores:", "Ammunition:",
        "Departure gate policy"))]
    first_blocked = next((row for row in readiness if row.startswith("Warning: order #")), None)
    if first_blocked:
        readiness_systems.insert(readiness_systems.index(next(
            row for row in readiness_systems if row.startswith("Unfinished work:"))) + 1, first_blocked)
    frames.append(capture(renderer, output, size, "readiness-systems", world,
                          "Readiness details — access, work and optional stores", readiness_systems,
                          "Canonical readiness rows; informational findings do not gate departure"))
    if not any(row.startswith("Warning: hull is below") for row in review):
        raise AssertionError("Departure review omitted the supported hull caution")
    frames.append(capture(renderer, output, size, "departure-warning", world,
                          "Review voyage order — warning, not a gate", review,
                          "Confirm to sail; unfinished work and risk remain real"))

    world.voyages.confirm(world, "cinder")
    world.enter_sea()
    with tempfile.TemporaryDirectory(prefix="ship-preparation-voyage-") as folder:
        save_path = Path(folder) / "lawful-trader-mvp.json"
        world.save(save_path)
        world = World.load(save_path, expected_scenario_id="lawful-trader-mvp")
    if world.voyages.voyage["status"] != "underway" or world.scenario_id != "lawful-trader-mvp":
        raise AssertionError("Authored voyage failed to persist underway")
    frames.append(capture(renderer, output, size, "resumed-voyage", world,
                          "Underway — authored ship preparation persisted",
                          world.voyages.role_report(world),
                          "Scenario identity and voyage state survive reload"))

    if world.ship.check_orders():
        raise AssertionError("Authored walkthrough ended with work-order invariant failures")
    return {"size": size, "scenario_id": world.scenario_id, "port": world.economy.port,
            "voyage_status": world.voyages.voyage["status"], "frames": frames,
            "planks_in_new_pile": world.ship.storage.piles[pile.id].contents.get("planks", 0),
            "stockpile_invariants": world.ship.check_orders(),
            "authored_simulation_mode": world.society.mode}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path.home() / "AppData" / "Local" / "Temp" / "opencode"
                        / "ship-preparation-m2",
                        help="Directory for native UI PNG captures and verification.json")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for size in SIZES:
        renderer = new_renderer(size)
        try:
            results.append(prepared_walkthrough(renderer, args.output, size))
        finally:
            renderer.shutdown()
    report = {"passed": True, "scripted_native_ui_not_human_playtest": True, "sizes": results}
    (args.output / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Authored ship-preparation walkthrough passed: {sum(len(row['frames']) for row in results)} native captures in {args.output}")


if __name__ == "__main__":
    main()
