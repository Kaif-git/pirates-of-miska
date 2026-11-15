"""Lightweight body and item temperature simulation inspired by CDDA's model.

The simulation uses per-region body temperatures, clothing warmth/coverage and
exposure, then relaxes temperatures toward local conditions. Item cooling uses
Newton-style exponential cooling with mass and storage insulation.
"""
from __future__ import annotations

import math

from tales_sim.clock import TICKS_PER_DAY, TICKS_PER_HOUR, TICKS_PER_SECOND


BODY_PARTS = ("head", "torso", "arms", "hands", "legs", "feet")
BODY_TEMP_NORMAL_C = 37.0
BODY_TEMP_COLD_C = 34.0
BODY_TEMP_VERY_COLD_C = 31.0
BODY_TEMP_FREEZING_C = 28.0
BODY_TEMP_HOT_C = 40.0
_BODY_TAU_SECONDS = 3000.0  # CDDA-like gradual convergence; roughly 35 min half-life.


def default_body_temperatures() -> dict[str, float]:
    return {part: BODY_TEMP_NORMAL_C for part in BODY_PARTS}


def is_indoor(view, at: tuple[int, int]) -> bool:
    origin_x, origin_y = view.island["house_origin"]
    local_x, local_y = at[0] - origin_x, at[1] - origin_y
    size = view.house["tile_size"]
    if 0 <= local_x < size["width"] and 0 <= local_y < size["height"]:
        return True
    return any(
        room["bounds"][0] <= at[0] <= room["bounds"][2]
        and room["bounds"][1] <= at[1] <= room["bounds"][3]
        for room in view.island.get("interior_rooms", [])
    )


def ambient_temperature_c(view, at: tuple[int, int], tick: float | None = None) -> float:
    """Simple coastal day cycle with indoor buffering and nearby active-fire warmth."""
    tick = float(view.clock.total_ticks if tick is None else tick)
    hour = (tick % TICKS_PER_DAY) / TICKS_PER_HOUR
    # Mild island weather: coolest before dawn, warmest in mid-afternoon.
    outdoor = 14.0 + 5.0 * math.cos((hour - 14.0) * math.tau / 24.0)
    temperature = 18.0 + (outdoor - 14.0) * 0.16 if is_indoor(view, at) else outdoor

    elapsed = max(0.0, tick - float(view.clock.total_ticks))
    for objects in view.objects_at.values():
        for obj in objects:
            furniture = view.furniture_defs.get(obj.get("furniture_id", obj.get("kind")), {})
            heat_source = furniture.get("heat_source")
            if not heat_source:
                continue
            timer_name = {"fireplace": "hearth_lit_ticks", "oven": "oven_lit_ticks",
                          "smithy": "smithy_lit_ticks"}.get(heat_source)
            if timer_name is None:
                continue
            remaining = max(0.0, float(getattr(view, timer_name, 0)) - elapsed)
            if remaining <= 0:
                continue
            distance = max(abs(at[0] - obj["at"][0]), abs(at[1] - obj["at"][1]))
            if distance <= 3:
                temperature += max(0.0, 5.0 - 1.5 * distance)
    return temperature


def clothing_profile(view, item_catalog: dict[str, dict] | None = None) -> dict[str, dict[str, float]]:
    """Return effective warmth and wind protection for each body region."""
    item_catalog = item_catalog or {}
    profile = {part: {"warmth": 0.0, "wind_protection": 0.0} for part in BODY_PARTS}
    exposed = {part: 1.0 for part in BODY_PARTS}
    for gear in view.inventory.equipment:
        spec = item_catalog.get(gear.get("id"), gear)
        warmth = max(0.0, float(spec.get("warmth", 0)))
        wind_resistance = min(100.0, max(0.0, float(spec.get("wind_resistance", 0)))) / 100.0
        coverage = spec.get("coverage", {})
        for part in BODY_PARTS:
            covered = min(100.0, max(0.0, float(coverage.get(part, 0)))) / 100.0
            profile[part]["warmth"] += warmth * covered
            exposed[part] *= 1.0 - covered * wind_resistance
    for part in BODY_PARTS:
        profile[part]["wind_protection"] = 1.0 - exposed[part]
    return profile


def target_body_temperature_c(view, part: str, tick: float | None = None,
                              item_catalog: dict[str, dict] | None = None) -> float:
    """Estimate a region's steady temperature from local climate and worn gear."""
    profile = clothing_profile(view, item_catalog)[part]
    at = (view.player.x, view.player.y)
    ambient = ambient_temperature_c(view, at, tick)
    return _body_target_from_profile(view, part, ambient, profile, at)


def _body_target_from_profile(view, part: str, ambient: float,
                              profile: dict[str, float], at: tuple[int, int]) -> float:
    wind_chill = 2.0 * (1.0 - profile["wind_protection"]) if not is_indoor(view, at) else 0.0
    nutrition = float(view.needs.get("nutrition", 50))
    metabolic_adjustment = max(-1.0, min(0.0, (nutrition - 50.0) * 0.02))
    # CDDA's convergent temperature uses ambient deviation, clothing warmth and
    # wind exposure; this keeps the same factors on a compact Celsius scale.
    target = (BODY_TEMP_NORMAL_C + (ambient - 19.0 - wind_chill) * 0.20
              + profile["warmth"] * 0.06 + metabolic_adjustment)
    return max(20.0, min(43.0, target))


def thermal_condition(view) -> str:
    torso = float(view.body_temperature_current_c.get("torso", BODY_TEMP_NORMAL_C))
    if torso <= BODY_TEMP_FREEZING_C:
        return "freezing"
    if torso <= BODY_TEMP_VERY_COLD_C:
        return "very cold"
    if torso <= BODY_TEMP_COLD_C:
        return "cold"
    if torso >= 43.0:
        return "very hot"
    if torso >= BODY_TEMP_HOT_C:
        return "hot"
    return "comfortable"


def thermal_work_penalty_percent(view) -> int:
    torso = float(view.body_temperature_current_c.get("torso", BODY_TEMP_NORMAL_C))
    if torso <= BODY_TEMP_VERY_COLD_C or torso >= 43.0:
        return 25
    if torso <= BODY_TEMP_COLD_C or torso >= BODY_TEMP_HOT_C:
        return 10
    return 0


def item_temperature_label(item: dict) -> str | None:
    temperature = item.get("temperature_c")
    if temperature is None:
        return None
    temperature = float(temperature)
    if temperature >= 75.0:
        state = "piping hot"
    elif temperature >= 60.0:
        state = "hot"
    elif temperature >= 38.0:
        state = "warm"
    elif temperature <= 10.0:
        state = "cold"
    else:
        state = "cooled"
    return f"{state} · {temperature:.0f}°C"


def _thermal_item_locations(view):
    player_at = (view.player.x, view.player.y)
    for item in view.inventory:
        if "temperature_c" in item:
            yield item, player_at, 2.0
    for at, items in view.ground_items.items():
        for item in items:
            if "temperature_c" in item:
                yield item, at, 1.0
    for objects in view.objects_at.values():
        for obj in objects:
            for item in view.surface_items.get(obj["id"], []):
                if "temperature_c" in item:
                    yield item, tuple(obj["at"]), 1.2
    origin_x, origin_y = view.island["house_origin"]
    container_positions = {row["id"]: (origin_x + row["at"][0], origin_y + row["at"][1])
                           for row in view.house.get("containers", [])}
    for objects in view.objects_at.values():
        for obj in objects:
            if obj.get("container_id"):
                container_positions[obj["container_id"]] = tuple(obj["at"])
    for container_id, items in view.containers.items():
        at = container_positions.get(container_id, player_at)
        for item in items:
            if "temperature_c" in item:
                yield item, at, 3.0


def _cool_item(item: dict, ambient: float, seconds: float, insulation: float) -> None:
    if "temperature_c" not in item:
        return
    old = float(item["temperature_c"])
    if abs(old - ambient) < 0.05:
        item["temperature_c"] = round(ambient, 2)
        return
    mass_g = max(50.0, float(item.get("weight_g", 200)) * max(1, int(item.get("quantity", 1))))
    # Conductivity-to-heat-capacity ratio scales approximately with mass^-1/3.
    time_constant = 900.0 * (mass_g / 200.0) ** (1.0 / 3.0) * insulation
    item["temperature_c"] = round(ambient + (old - ambient) * math.exp(-seconds / time_constant), 2)


def advance_temperature(view, start_tick: int, ticks: int, item_catalog: dict[str, dict]) -> None:
    """Advance region body temperatures and exposed/carry-stored item temperatures."""
    remaining = max(0, int(ticks))
    elapsed = 0
    thermal_items = list(_thermal_item_locations(view)) if remaining else []
    player_at = (view.player.x, view.player.y)
    while remaining:
        step = min(60000, remaining)
        midpoint = start_tick + elapsed + step / 2.0
        profile = clothing_profile(view, item_catalog)
        player_ambient = ambient_temperature_c(view, player_at, midpoint)
        for part in BODY_PARTS:
            target = _body_target_from_profile(
                view, part, player_ambient, profile[part], player_at)
            current = float(view.body_temperature_current_c.get(part, BODY_TEMP_NORMAL_C))
            alpha = 1.0 - math.exp(-(step / TICKS_PER_SECOND) / _BODY_TAU_SECONDS)
            view.body_temperature_target_c[part] = round(target, 2)
            view.body_temperature_current_c[part] = round(current + (target - current) * alpha, 2)
        ambient_cache = {player_at: player_ambient}
        for item, at, insulation in thermal_items:
            if at not in ambient_cache:
                ambient_cache[at] = ambient_temperature_c(view, at, midpoint)
            _cool_item(item, ambient_cache[at], step / TICKS_PER_SECOND, insulation)
        elapsed += step
        remaining -= step


def thermal_stacks_compatible(existing: dict, incoming: dict) -> bool:
    left, right = existing.get("temperature_c"), incoming.get("temperature_c")
    if left is None and right is None:
        return True
    return left is not None and right is not None and abs(float(left) - float(right)) <= 2.0


def merge_thermal_stack(existing: dict, incoming: dict) -> bool:
    """Merge thermal state by quantity; return false when temperatures differ too much."""
    if not thermal_stacks_compatible(existing, incoming):
        return False
    left, right = existing.get("temperature_c"), incoming.get("temperature_c")
    if left is None:
        return True
    left_quantity = max(1, int(existing.get("quantity", 1)))
    right_quantity = max(1, int(incoming.get("quantity", 1)))
    existing["temperature_c"] = round(
        (float(left) * left_quantity + float(right) * right_quantity)
        / (left_quantity + right_quantity), 2)
    return True
