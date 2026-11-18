"""Measure normal shipboard food/water use against the read-only forecast."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

from pirates.game import World
from pirates.provisioning import (CLEAN_WATER_L_PER_CREW_DAY,
                                  FOOD_KG_PER_CREW_DAY, forecast_provisions)


def crew_state(world):
    return [
        {
            "id": sailor.id,
            "hunger": round(sailor.hunger, 2),
            "thirst": round(sailor.thirst, 2),
            "health": round(sailor.health, 2),
            "food_consumed_today_g": sailor.food_consumed_today_g,
            "water_consumed_today_ml": sailor.water_consumed_today_ml,
            "job_id": sailor.job_id,
            "intent": sailor.intent,
            "activity": sailor.activity,
        }
        for sailor in world.ship.crew
    ]


def sample(world):
    forecast = forecast_provisions(world)
    return {
        "day": world.clock.day,
        "time": world.clock.hour_minute,
        "consumption_day": world.ship.consumption_day,
        "ship_seconds": world.ship.seconds,
        "ready_food_g": world.ship.ready_food_g(),
        "edible_food_g": world.ship.edible_food_g(),
        "clean_water_ml": world.ship.clean_water_total_ml(),
        "food_consumed_today_g": world.ship.food_consumed_today_g,
        "water_consumed_today_ml": world.ship.water_consumed_today_ml,
        "food_waste_g": world.ship.stores.get("food_waste_g", 0),
        "forecast": forecast,
        "crew": crew_state(world),
        "crew_history": list(world.ship.crew_history),
        "crew_events": list(world.ship.events),
        "open_orders": world.ship.check_orders(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=24, help="normal simulation hours (1-72)")
    parser.add_argument("--sample-hours", type=int, default=1, help="sampling interval")
    parser.add_argument("--output", type=Path, required=True, help="JSON report path")
    args = parser.parse_args()
    if not 1 <= args.hours <= 72 or not 1 <= args.sample_hours <= args.hours:
        parser.error("hours must be 1-72 and sample-hours must be between 1 and hours")

    world = World()
    before = sample(world)
    samples = [before]
    remaining_seconds = args.hours * 3600
    while remaining_seconds:
        day_ticks = 24 * 3600 * 100
        until_midnight = (day_ticks - world.clock.total_ticks % day_ticks) // 100
        interval = min(args.sample_hours * 3600, remaining_seconds, until_midnight)
        start_seconds = world.ship.seconds
        world.advance(interval * 100)
        if world.ship.seconds == start_seconds:
            raise RuntimeError("Ship simulation stopped before the requested sample interval")
        samples.append(sample(world))
        remaining_seconds -= interval

    after = samples[-1]
    actual_food_g = actual_water_ml = 0
    for previous, current in zip(samples, samples[1:]):
        if current["consumption_day"] == previous["consumption_day"]:
            actual_food_g += max(0, current["food_consumed_today_g"]
                                 - previous["food_consumed_today_g"])
            actual_water_ml += max(0, current["water_consumed_today_ml"]
                                   - previous["water_consumed_today_ml"])
        else:
            # Counter has rolled into a new consumption day; exact-midnight
            # sampling above ensures no prior-day consumption was skipped.
            actual_food_g += current["food_consumed_today_g"]
            actual_water_ml += current["water_consumed_today_ml"]
    elapsed_seconds = after["ship_seconds"] - before["ship_seconds"]
    forecast_crew = before["forecast"]["crew_count"]
    expected_food_g = round(forecast_crew * FOOD_KG_PER_CREW_DAY * 1000
                            * elapsed_seconds / 86400)
    expected_water_ml = round(forecast_crew * CLEAN_WATER_L_PER_CREW_DAY * 1000
                              * elapsed_seconds / 86400)
    result = {
        "scenario": "fresh-world normal shipboard simulation; no voyage, forced stock changes, or needs reset",
        "requested_hours": args.hours,
        "actual_elapsed_seconds": elapsed_seconds,
        "samples": samples,
        "summary": {
            "food_stock_delta_g": after["edible_food_g"] - before["edible_food_g"],
            "clean_water_stock_delta_ml": after["clean_water_ml"] - before["clean_water_ml"],
            "food_consumed_g": actual_food_g,
            "water_consumed_ml": actual_water_ml,
            "forecast_food_for_elapsed_horizon_g": expected_food_g,
            "forecast_water_for_elapsed_horizon_ml": expected_water_ml,
            "food_actual_to_forecast_ratio": (actual_food_g / expected_food_g if expected_food_g else None),
            "water_actual_to_forecast_ratio": (actual_water_ml / expected_water_ml if expected_water_ml else None),
            "ready_food_coverage_start_days": before["forecast"]["ready_food_days"],
            "water_coverage_start_days": before["forecast"]["water_days"],
            "orders_at_end": after["open_orders"],
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], indent=2))
    print(f"Wrote {args.output} ({result['actual_elapsed_seconds']} simulated seconds).")


if __name__ == "__main__":
    main()
