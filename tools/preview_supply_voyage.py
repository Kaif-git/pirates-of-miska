"""Native paid provisioning, voyage, save/resume, restock and return scenario."""
from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

import play
from PIL import Image
from pirates.economy import MERCHANTS
from pirates.provisioning import forecast_provisions
from omnisim.world import tile as T


class ConsumptionRecorder:
    """Accumulate daily counters across date rollovers without changing game state."""

    def __init__(self):
        self.food_g = 0
        self.drinking_water_ml = 0

    def attach(self, world):
        original_advance = world.advance

        def advance(ticks):
            remaining = ticks
            while remaining > 0:
                # Split at exact midnight so a daily counter reset cannot hide
                # consumption from the previous day in a long advance call.
                day_ticks = 24 * 3600 * 100
                until_midnight = day_ticks - world.clock.total_ticks % day_ticks
                chunk = min(remaining, until_midnight)
                before_ticks = world.clock.total_ticks
                before_day = world.clock.day
                before_consumption_day = world.ship.consumption_day
                before_food = world.ship.food_consumed_today_g
                before_water = world.ship.water_consumed_today_ml
                original_advance(chunk)
                after_food = world.ship.food_consumed_today_g
                after_water = world.ship.water_consumed_today_ml
                if world.ship.consumption_day == before_consumption_day:
                    self.food_g += max(0, after_food - before_food)
                    self.drinking_water_ml += max(0, after_water - before_water)
                else:
                    # A new day's record began inside this chunk; midnight was
                    # a boundary, so its counters contain only new-day use.
                    self.food_g += after_food
                    self.drinking_water_ml += after_water
                elapsed = world.clock.total_ticks - before_ticks
                remaining -= elapsed
                if elapsed <= 0 or world.clock.day == before_day and elapsed < chunk:
                    break  # an arrival/encounter interrupted this advance
            return None

        world.advance = advance


def walk_to(world, target):
    for _ in range(1200):
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
        if not world.move(x - world.player.x, y - world.player.y):
            raise AssertionError(f"Normal movement rejected route step {(x, y)} toward {target}")
    raise AssertionError(f"Could not reach interaction range of {target}")


def state(world):
    forecast = forecast_provisions(world)
    from pirates.ship_physics import calculate
    mass = calculate(world)
    return {
        "port": world.economy.port,
        "voyage_status": world.voyages.voyage.get("status") if world.voyages.voyage else "moored",
        "voyage_elapsed_seconds": world.voyages.voyage.get("elapsed_seconds", 0) if world.voyages.voyage else 0,
        "ship_seconds": world.ship.seconds,
        "money": world.economy.money,
        "ready_food_g": world.ship.ready_food_g(),
        "edible_food_g": world.ship.edible_food_g(),
        "clean_water_ml": world.ship.clean_water_total_ml(),
        "food_consumed_today_g": world.ship.food_consumed_today_g,
        "water_consumed_today_ml": world.ship.water_consumed_today_ml,
        "food_waste_g": world.ship.stores.get("food_waste_g", 0),
        "stores": dict(world.ship.stores),
        "containers": deepcopy(world.ship.containers),
        "fresh_food_lots": list(world.ship.fresh_food_lots),
        "cooked_food_batches": list(world.ship.cooked_food_batches),
        "rain_collected_today_ml": world.ship.rain_collected_today_ml,
        "light": {"on": world.ship.light_on, "type": world.ship.light_type,
                  "fuel_ml": world.ship.light_fuel_ml,
                  "candle_seconds": world.ship.candle_fuel_seconds},
        "garment_condition": world.economy.gear_condition,
        "jobs": [{"id": job.id, "kind": job.kind, "state": job.state,
                  "worker": job.worker, "reason": job.reason}
                 for job in world.ship.jobs],
        "completed_jobs": [{"id": job.id, "kind": job.kind, "worker": job.worker}
                            for job in world.ship.jobs if job.state == "done"],
        "recent_work_audit": list(world.ship.audit[-30:]),
        "mass_kg": mass["mass_kg"],
        "total_mass_kg": mass["total_mass_kg"],
        "crew": [{"id": s.id, "hunger": round(s.hunger, 1), "thirst": round(s.thirst, 1),
                  "health": round(s.health, 1), "job": s.job_id, "intent": s.intent}
                 for s in world.ship.crew],
        "open_orders": world.ship.check_orders(),
        "forecast": forecast,
    }


def voyage(world, destination, save_path, recorder, on_progress=None):
    plan = world.voyages.confirm(world, destination)
    world.enter_sea()
    # Save at a real mid-route state; load it and continue without editing time,
    # quantities, crew state, or route state.
    world.advance(900 * 100)
    world.save(save_path)
    world = play.World.load(save_path)
    recorder.attach(world)
    while world.voyages.voyage["status"] == "underway":
        world.advance(1800 * 100)
        if on_progress:
            on_progress(world)
    if world.voyages.voyage["status"] != "arrived":
        raise AssertionError(f"Baseline voyage interrupted: {world.voyages.voyage['status']}")
    return world, plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--round-trips", type=int, default=5,
                        help="paid Verdant–Cinder–Verdant trips (default 5, about two game days)")
    parser.add_argument("--verdant-water-kegs", type=int, default=15)
    parser.add_argument("--verdant-biscuits", type=int, default=30)
    parser.add_argument("--cinder-water-kegs", type=int, default=3)
    parser.add_argument("--cinder-biscuits", type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.round_trips <= 8:
        parser.error("round-trips must be between 1 and 8")
    if any(value < 0 for value in (args.verdant_water_kegs, args.verdant_biscuits,
                                    args.cinder_water_kegs, args.cinder_biscuits)):
        parser.error("provision quantities must be non-negative")
    args.output.mkdir(parents=True, exist_ok=True)
    save_path = args.output / "mid-voyage-save.json"
    world = play.World()
    recorder = ConsumptionRecorder()
    recorder.attach(world)
    geometry = world.ship_model.snapshot()
    # Keep the baseline focused on supply and ship work rather than forcing an
    # unrelated tactical encounter; this is recorded as scenario setup below.
    world.npcs.sighted.update(ship["id"] for ship in world.npcs.ships)
    baseline = state(world)
    renderer = play.PiratesRenderer(settings=play.RenderSettings(
        world_font_size=20, world_cell_width=20, world_cell_height=24))
    report = {"setup": "fresh world; no needs/labor/stock/time reset; NPC vessels marked sighted to isolate the no-combat supply baseline",
              "provisioning_plan": {
                  "verdant_water_kegs": args.verdant_water_kegs,
                  "verdant_biscuits": args.verdant_biscuits,
                  "cinder_water_kegs": args.cinder_water_kegs,
                  "cinder_biscuits": args.cinder_biscuits,
              },
              "frames": [], "route_plans": [], "stages": []}

    def persist_report(*, error=None, current_world=None):
        """Keep completed milestones usable if a long native run is interrupted."""
        if error is not None:
            report["error"] = f"{type(error).__name__}: {error}"
            report["last_state"] = state(current_world or world)
        temporary = args.output / "verification.json.tmp"
        temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
        temporary.replace(args.output / "verification.json")

    try:
        renderer.init_for(100, 64)

        def capture(name, title):
            forecast = forecast_provisions(world)
            rows = [
                f"Ready food: {world.ship.ready_food_g() / 1000:.2f} kg | {forecast['ready_food_days']:.2f} forecast days",
                f"Potable water: {world.ship.clean_water_total_ml() / 1000:.1f} L | {forecast['water_days']:.2f} forecast days",
                f"Crew aboard: {len(world.ship.crew)} | Marks: {world.economy.money}",
                *forecast["blockers"],
                forecast["assumptions"],
            ]
            renderer.render(world)
            renderer.render_list(world, title, rows, "Paid supply/voyage walkthrough", selectable=False)
            renderer._present_layers()
            Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(args.output / f"{name}.png")
            report["frames"].append({"name": name, **state(world)})

        # Buy finite, ordinary provisions from Bo using the market transaction,
        # then carry them to the hold and pay the physical allocation cost/time.
        walk_to(world, MERCHANTS["bo"]["at"])
        # Keep enough water-pile headroom for the explicitly planned Cinder
        # restock after the first leg; the hold capacity is finite and real.
        if args.verdant_water_kegs:
            world.economy.trade(world, "bo", "water_cask", args.verdant_water_kegs, True)
        if args.verdant_biscuits:
            world.economy.trade(world, "bo", "biscuit", args.verdant_biscuits, True)
        walk_to(world, world.ship.stations["hold"])
        if args.verdant_water_kegs:
            world.economy.allocate(world, "water_cask", args.verdant_water_kegs)
        if args.verdant_biscuits:
            world.economy.allocate(world, "biscuit", args.verdant_biscuits)
        report["stages"].append({"stage": "paid_departure_provisioning", **state(world)})
        capture("01-departure-readiness", "Paid departure provisions — Verdant Cay")
        persist_report()

        for trip in range(1, args.round_trips + 1):
            def report_progress(current_world):
                voyage_state = current_world.voyages.voyage
                report["progress"] = {
                    "trip": trip,
                    "leg": "to_cinder",
                    "voyage_elapsed_seconds": voyage_state["elapsed_seconds"],
                    "voyage_duration_seconds": voyage_state["duration_seconds"],
                    "route_leg_index": voyage_state["leg"],
                    "route_leg_progress": voyage_state["leg_progress"],
                    "wind": voyage_state["wind"],
                    "sailing": dict(voyage_state.get("sailing", {})),
                    "state": state(current_world),
                }
                persist_report(current_world=current_world)

            world, outbound = voyage(world, "cinder", save_path, recorder, report_progress)
            report["route_plans"].append(outbound["route"])
            report["stages"].append({"stage": f"trip_{trip}_arrival_cinder", **state(world)})
            capture(f"trip-{trip:02d}-cinder-arrival", f"Trip {trip}: arrival at Cinder Quay")
            persist_report()

            # Restock once from the destination's finite market stock and real
            # Marks; all later route legs draw down the documented supplies.
            if trip == 1 and (args.cinder_water_kegs or args.cinder_biscuits):
                walk_to(world, MERCHANTS["dara"]["at"])
                if args.cinder_water_kegs:
                    world.economy.trade(world, "dara", "water_cask", args.cinder_water_kegs, True)
                if args.cinder_biscuits:
                    world.economy.trade(world, "dara", "biscuit", args.cinder_biscuits, True)
                walk_to(world, world.ship.stations["hold"])
                if args.cinder_water_kegs:
                    world.economy.allocate(world, "water_cask", args.cinder_water_kegs)
                if args.cinder_biscuits:
                    world.economy.allocate(world, "biscuit", args.cinder_biscuits)
                report["stages"].append({"stage": "paid_cinder_restock", **state(world)})
                capture("03-cinder-restock", "Purchased and allocated Cinder restock")
                persist_report()

            def report_progress(current_world):
                voyage_state = current_world.voyages.voyage
                report["progress"] = {
                    "trip": trip,
                    "leg": "to_verdant",
                    "voyage_elapsed_seconds": voyage_state["elapsed_seconds"],
                    "voyage_duration_seconds": voyage_state["duration_seconds"],
                    "route_leg_index": voyage_state["leg"],
                    "route_leg_progress": voyage_state["leg_progress"],
                    "wind": voyage_state["wind"],
                    "sailing": dict(voyage_state.get("sailing", {})),
                    "state": state(current_world),
                }
                persist_report(current_world=current_world)

            world, inbound = voyage(world, "verdant", save_path, recorder, report_progress)
            report["route_plans"].append(inbound["route"])
            report["stages"].append({"stage": f"trip_{trip}_return_arrival", **state(world)})
            capture(f"trip-{trip:02d}-verdant-return", f"Trip {trip}: return arrival — Verdant Cay")
            persist_report()
        checks = {
            "returned_to_starting_port": world.economy.port == "verdant" and not world.at_sea,
            "all_crew_survived": len(world.ship.crew) == 6,
            "ship_geometry_unchanged": world.ship_model.snapshot() == geometry,
            "job_invariants_clear": not world.ship.check_orders(),
        }
        report.update({"passed": all(checks.values()), "checks": checks,
                       "round_trips": args.round_trips,
                       "total_elapsed_ship_seconds": world.ship.seconds,
                       "consumption_since_fresh_world": {
                           "food_g": recorder.food_g,
                           "drinking_water_ml": recorder.drinking_water_ml,
                           "time_hours": (world.ship.seconds - baseline["ship_seconds"]) / 3600,
                       },
                        "fresh_world_forecast": baseline["forecast"],
                        "fresh_world_crew": baseline["crew"],
                        "fresh_world_start": baseline,
                       "final_money": world.economy.money,
                       "geometry_unchanged": world.ship_model.snapshot() == geometry,
                        "crew_departures": world.ship.crew_history,
                        "trade_history": world.economy.history})
        persist_report()
        result = "passed" if report["passed"] else "completed with failed acceptance checks"
        print(f"Paid provisioning and {args.round_trips} round trip(s) {result}; {len(report['frames'])} captures.")
    except Exception as error:
        persist_report(error=error)
        raise
    finally:
        renderer.shutdown()


if __name__ == "__main__":
    main()
