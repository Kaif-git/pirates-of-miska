"""Loader for the headless simulation's data-driven base configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "data" / "config.json"


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load and minimally validate a simulation JSON configuration.

    Keep content and tuning in data. This initial loader validates only the
    clock fields needed to establish the M0 package contract; later milestones
    may extend the schema without coupling it to the game runtime.
    """
    source = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load simulation config {source}: {exc}") from exc

    return validate_config(data)


def validate_config(data: Any) -> dict[str, Any]:
    """Validate and detach a decoded config object."""
    if (not isinstance(data, dict) or type(data.get("schema_version")) is not int
            or data["schema_version"] < 1):
        raise ValueError("Simulation config must be an object with an integer schema_version")
    time = data.get("time")
    required_time_keys = {"hours_per_day", "days_per_year", "hours_per_tick"}
    if not isinstance(time, dict) or set(time) != required_time_keys:
        raise ValueError("Simulation config time must define hours_per_day, days_per_year, and hours_per_tick")
    if any(type(value) is not int or value <= 0 for value in time.values()):
        raise ValueError("Simulation config time values must be positive integers")
    if time["hours_per_day"] % time["hours_per_tick"]:
        raise ValueError("hours_per_tick must divide evenly into hours_per_day")
    rules = data.get("event_rules")
    roots = rules.get("root_event_types") if isinstance(rules, dict) else None
    if (not isinstance(roots, list) or not roots
            or any(not isinstance(name, str) or not name for name in roots)
            or len(set(roots)) != len(roots) or "WORLD_GENERATED" not in roots):
        raise ValueError("Simulation config event_rules must define unique root_event_types")
    classes = rules.get("class_by_type", {})
    if (not isinstance(classes, dict)
            or any(not isinstance(name, str) or not name
                   or not isinstance(event_class, str)
                   or event_class not in {"routine", "narrative", "structural"}
                   for name, event_class in classes.items())):
        raise ValueError("Simulation config event_rules.class_by_type is invalid")
    director = data.get("director")
    if director is not None:
        director_keys = {"enabled", "intensity_permille", "check_interval_days",
                         "conflict_drought_days", "injection_cooldown_days",
                         "max_injections_per_year", "minimum_major_characters"}
        if (not isinstance(director, dict) or set(director) != director_keys
                or type(director["enabled"]) is not bool
                or any(type(director[key]) is not int for key in director_keys - {"enabled"})
                or not 0 <= director["intensity_permille"] <= 1000
                or any(director[key] <= 0 for key in director_keys -
                       {"enabled", "intensity_permille"})):
            raise ValueError("Simulation config director settings are invalid")
        if director["enabled"] and "DIRECTOR_INJECTION" not in roots:
            raise ValueError("Enabled director requires DIRECTOR_INJECTION as a root event type")
    world = data.get("world")
    if world is not None:
        if not isinstance(world, dict) or set(world) != {"actors"}:
            raise ValueError("Simulation config world must define actors")
        actors = world["actors"]
        if (not isinstance(actors, dict)
                or set(actors) != {"ship_bands", "captain_bands"}):
            raise ValueError("Simulation config world.actors has invalid fields")
        ship_bands = actors["ship_bands"]
        required_ship_bands = {"merchant", "pirate", "marine", "privateer"}
        if not isinstance(ship_bands, dict) or set(ship_bands) != required_ship_bands:
            raise ValueError("Simulation config world.actors.ship_bands is invalid")
        per_island_keys = {"minimum_per_island_milli", "target_per_island_milli",
                           "maximum_per_island_milli"}
        for band, values in ship_bands.items():
            if (not isinstance(values, dict) or set(values) != per_island_keys
                    or any(type(value) is not int or value < 0 for value in values.values())
                    or not values["minimum_per_island_milli"]
                    <= values["target_per_island_milli"]
                    <= values["maximum_per_island_milli"]):
                raise ValueError(f"Invalid actor ship budget: {band}")
        captain_bands = actors["captain_bands"]
        if (not isinstance(captain_bands, dict) or set(captain_bands) != {"major", "legend"}):
            raise ValueError("Simulation config world.actors.captain_bands is invalid")
        for band, values in captain_bands.items():
            if (not isinstance(values, dict)
                    or set(values) != {"minimum", "target", "maximum"}
                    or any(type(value) is not int or value < 0 for value in values.values())
                    or not values["minimum"] <= values["target"] <= values["maximum"]):
                raise ValueError(f"Invalid actor captain budget: {band}")
    try:
        return json.loads(json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("Simulation config must contain JSON-compatible values") from exc
