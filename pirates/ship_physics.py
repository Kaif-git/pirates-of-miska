"""Explainable, deliberately approximate ship loading and hydrostatics."""

from math import atan, atan2, cos, degrees, hypot, radians, sin, sqrt

from pirates.ship_map import ORIGIN, WIDTH, HEIGHT
from pirates.ship_model import MATERIALS
from pirates.ship_storage import UNIT_MASS, bundle_mass

CELL_SIZE_M = 0.4
DECK_HEIGHT_M = 2.0
HULL_DRAFT_M = 2.0
SEAWATER_KG_M3 = 1025
ACTOR_MASS_KG = 80
BILGE_KG_PER_POINT = 10


def load_by_position(world):
    """Return non-structural mass categories and masses at ship-local floor cells."""
    categories = {key: 0.0 for key in (
        "fixtures", "supplies", "containers", "trade_cargo", "crew_kit", "personal_inventory",
        "ground_items", "actors", "prepared_food", "bilge", "floodwater", "loaded_ammunition",
    )}
    positions = {}
    floors = {(c.x, c.y, c.z) for c in world.ship_model.components if c.kind == "floor"}
    hold = world.ship.stations.get("hold")
    hold_at = ((hold[0] - ORIGIN[0], hold[1] - ORIGIN[1], hold[2] if len(hold) == 3 else 0)
               if hold is not None else None)

    def add(category, at, mass):
        if at is None or mass <= 0:
            return
        at = tuple(at)
        if len(at) == 2:
            at = (*at, 0)
        if at not in floors:
            return
        mass = float(mass)
        categories[category] += mass
        positions[at] = positions.get(at, 0.0) + mass

    for fixture in world.fixtures.fixtures:
        if fixture.carrier is None:
            add("fixtures", (fixture.x, fixture.y, fixture.z), fixture.mass_kg)

    for pile in world.ship.storage.piles.values():
        fixture = world.fixtures.get(pile.fixture_id) if pile.fixture_id else None
        at = ((fixture.x, fixture.y, fixture.z) if fixture else (pile.x, pile.y, pile.z))
        add("supplies", at, bundle_mass(pile.contents))
    if world.ship.light_fuel_ml:
        add("supplies", hold_at, world.ship.light_fuel_ml / 1000)
    if world.ship.candle_fuel_seconds:
        add("supplies", hold_at, 0.1 * world.ship.candle_fuel_seconds / 3600)

    water_station = world.ship.stations.get("water")
    for vessel in world.ship.containers:
        if vessel["owner"]:
            owner = world.ship.sailor(vessel["owner"])
            at = ((owner.x - ORIGIN[0], owner.y - ORIGIN[1], owner.z) if owner else None)
        elif water_station is not None:
            at = (water_station[0] - ORIGIN[0], water_station[1] - ORIGIN[1],
                  water_station[2] if len(water_station) == 3 else 0)
        else:
            at = None
        add("containers", at, (vessel["tare_g"] + vessel["contents_ml"]) / 1000)

    if hold_at is not None:
        from pirates.economy import CATALOG
        for item_id, quantity in world.economy.cargo.items():
            item = CATALOG.get(item_id)
            if item:
                add("trade_cargo", hold_at, item["weight_g"] * quantity / 1000)

    from pirates.economy import CATALOG
    for sailor in world.ship.crew:
        crew_at = (sailor.x - ORIGIN[0], sailor.y - ORIGIN[1], sailor.z)
        add("actors", crew_at, ACTOR_MASS_KG)
        add("supplies", crew_at, bundle_mass(sailor.cargo))
        for item_id in world.economy.gear.get(sailor.id, {}).values():
            item = CATALOG.get(item_id)
            if item:
                add("crew_kit", crew_at, item["weight_g"] / 1000)

    player_at = _aboard_position(world, world.player.x, world.player.y, world.player.z)
    if player_at is not None:
        add("actors", player_at, ACTOR_MASS_KG)
        for item in world.inventory.items:
            add("personal_inventory", player_at,
                item.get("weight_g", 0) * item.get("quantity", 1) / 1000)

    for key, items in world.ground_items.items():
        if len(key) not in (2, 3):
            continue
        wx, wy = key[:2]
        z = key[2] if len(key) == 3 else 0
        at = _aboard_position(world, wx, wy, z)
        for item in items:
            add("ground_items", at,
                item.get("weight_g", 0) * item.get("quantity", 1) / 1000)

    meal_at = world.ship.stations.get("galley")
    if meal_at is not None:
        at = (meal_at[0] - ORIGIN[0], meal_at[1] - ORIGIN[1],
              meal_at[2] if len(meal_at) == 3 else 0)
        add("prepared_food", at, world.ship.prepared_food_g / 1000)

    bilge_at = world.ship.stations.get("bilge")
    flooded_kg = 0.0
    hazards = getattr(world, "hazards", None)
    if hazards is not None:
        for key in sorted(hazards.water):
            kilograms = hazards.water[key]
            add("floodwater", hazards.cell(key), kilograms)
            flooded_kg += kilograms
        categories["floodwater"] = hazards.total_water_kg()
    if bilge_at is not None:
        at = (bilge_at[0] - ORIGIN[0], bilge_at[1] - ORIGIN[1],
              bilge_at[2] if len(bilge_at) == 3 else 0)
        add("bilge", at, max(0.0, world.ship.bilge * BILGE_KG_PER_POINT - flooded_kg))

    for cannon, loaded in world.ship.ammo.items():
        if not loaded:
            continue
        fixture = world.fixtures.get(cannon)
        if fixture:
            add("loaded_ammunition", (fixture.x, fixture.y, fixture.z), UNIT_MASS["shot"] * loaded)

    return categories, positions


def calculate(world):
    """Estimate displacement, draft, freeboard, trim/list and stability margin.

    One map tile is provisionally 0.4 m. Deck-zero floor geometry is the
    waterplane footprint; component/material masses and spatial loads are real
    inputs, but the resulting naval model is intentionally low-order.
    """
    model = world.ship_model
    geometry = model.deck_geometry(0)
    categories, positions = load_by_position(world)
    categories["structure"] = sum(MATERIALS[c.material].mass_kg for c in model.components)
    total = sum(categories.values())
    if total <= 0:
        total = 0.0

    weighted_x = weighted_y = weighted_z = 0.0
    for component in model.components:
        mass = MATERIALS[component.material].mass_kg
        weighted_x += (component.x + 0.5) * mass
        weighted_y += (component.y + 0.5) * mass
        weighted_z += (component.z * DECK_HEIGHT_M + 0.5) * mass
    for (x, y, z), mass in positions.items():
        weighted_x += (x + 0.5) * mass
        weighted_y += (y + 0.5) * mass
        weighted_z += (z * DECK_HEIGHT_M + 0.5) * mass
    com = (None if total == 0 else {
        "x_tiles": weighted_x / total,
        "y_tiles": weighted_y / total,
        "height_m": weighted_z / total,
    })

    tiles = geometry["tiles"]
    area_m2 = tiles * CELL_SIZE_M ** 2
    max_displacement = area_m2 * HULL_DRAFT_M * SEAWATER_KG_M3
    displaced_volume = total / SEAWATER_KG_M3
    draft = displaced_volume / area_m2 if area_m2 else 0.0
    freeboard = max(0.0, HULL_DRAFT_M - draft)
    reserve = max(0.0, max_displacement - total)
    bounds = geometry["bounds"]
    if bounds and com:
        xmin, ymin, xmax, ymax = bounds
        beam_tiles = xmax - xmin + 1
        length_tiles = ymax - ymin + 1
        beam_m = beam_tiles * CELL_SIZE_M
        length_m = length_tiles * CELL_SIZE_M
        center_x, center_y = (xmin + xmax + 1) / 2, (ymin + ymax + 1) / 2
        list_deg = degrees(atan(2 * (com["x_tiles"] - center_x) / max(beam_tiles, 1)))
        trim_m = (com["y_tiles"] - center_y) / max(length_tiles, 1) * draft * 2
        # A deliberately capped beam-based estimate: upper-deck mass raises KG.
        gm_m = max(-5.0, min(5.0, beam_m * 0.22 + 0.35 - com["height_m"]))
    else:
        beam_m = length_m = list_deg = trim_m = gm_m = 0.0
    warnings = []
    if tiles == 0:
        warnings.append("No lower-deck hull footprint; displacement estimate unavailable")
    elif total > max_displacement:
        warnings.append("Estimated loading exceeds the hull's provisional displacement limit")
    if freeboard < 0.25:
        warnings.append("Very low estimated freeboard")
    if gm_m <= 0:
        warnings.append("Estimated vertical stability is neutral or negative")
    return {
        "mass_kg": {key: round(value, 1) for key, value in categories.items()},
        "total_mass_kg": round(total, 1),
        "center_of_mass": ({key: round(value, 3) for key, value in com.items()} if com else None),
        "waterplane_area_m2": round(area_m2, 2),
        "hull_beam_m": round(beam_m, 2),
        "hull_length_m": round(length_m, 2),
        "displaced_volume_m3": round(displaced_volume, 2),
        "draft_m": round(draft, 3),
        "freeboard_m": round(freeboard, 3),
        "displacement_limit_kg": round(max_displacement, 1),
        "loading_margin_kg": round(reserve, 1),
        "trim_m": round(trim_m, 3),
        "list_deg": round(list_deg, 2),
        "estimated_gm_m": round(gm_m, 3),
        "warnings": tuple(warnings),
    }


def sailing_forces(world, wind, course_deg, speed_mps=0.0):
    """Return a legible provisional sail-drive/resistance estimate in newtons.

    Wind vectors are the chart's downwind direction. Effective sail area is a
    tunable rigging proxy (not measured sail geometry); the force equation is
    intentionally low-order and its constants are exposed in this result.
    """
    report = calculate(world)
    wind_speed = (5.5 if wind["dx"] or wind["dy"] else 1.2)
    wind_heading = degrees(atan2(wind["dy"], wind["dx"])) if wind["dx"] or wind["dy"] else course_deg
    relative = radians((wind_heading - course_deg + 180) % 360 - 180)
    point_of_sail = max(0.12, 0.32 + 0.58 * abs(sin(relative)) + 0.10 * max(0.0, cos(relative)))
    rig = next((f for f in world.fixtures.fixtures if f.kind == "rigging" and f.carrier is None), None)
    sail_area_m2 = 36.0 if rig else 0.0
    helm = next((f for f in world.fixtures.fixtures if f.kind == "helm" and f.carrier is None), None)
    if helm:
        max_y = max((c.y for c in world.ship_model.components if c.kind == "floor" and c.z == 0), default=helm.y)
        rudder_authority = max(0.5, min(1.25, 0.75 + 0.5 * helm.y / max(1, max_y)))
    else:
        rudder_authority = 0.0
    condition = max(0.0, min(1.0, world.ship.rigging / 100))
    nav = world.ship.sailor(world.voyages.roles["navigator"])
    crew_factor = 0.8 + min(0.3, nav.skills["rig"] * 0.025) if nav else 0.8
    sail_force_n = sail_area_m2 * wind_speed ** 2 * 0.78 * point_of_sail * condition * crew_factor
    # Hull size and immersion raise the effective drag area; calm-water trim
    # is not a wave-resistance or seakeeping solution.
    drag_coefficient = max(550.0, 550.0 + report["hull_length_m"] * 42
                           + report["hull_beam_m"] * 30 + report["draft_m"] * 250)
    resistance_n = drag_coefficient * max(0.0, speed_mps) ** 2
    mass_kg = max(1000.0, report["total_mass_kg"])
    acceleration_mps2 = (sail_force_n - resistance_n) / mass_kg
    target_speed_mps = sqrt(max(0.0, sail_force_n / drag_coefficient))
    return {
        "wind_speed_mps": wind_speed, "wind_heading_deg": round(wind_heading, 1),
        "course_heading_deg": round(course_deg % 360, 1),
        "relative_wind_deg": round(degrees(relative), 1),
        "point_of_sail": round(point_of_sail, 3), "sail_area_m2": sail_area_m2,
        "sail_condition": round(condition, 3), "sail_force_n": round(sail_force_n, 1),
        "resistance_n": round(resistance_n, 1),
        "acceleration_mps2": round(acceleration_mps2, 5),
        "target_speed_mps": round(target_speed_mps, 3),
        "target_speed_knots": round(target_speed_mps * 1.94384, 2),
        "mass_kg": round(mass_kg, 1), "drag_coefficient": round(drag_coefficient, 1),
        "rudder_authority": round(rudder_authority, 3),
    }


def _aboard_position(world, wx, wy, z):
    if not (ORIGIN[0] <= wx < ORIGIN[0] + WIDTH and ORIGIN[1] <= wy < ORIGIN[1] + HEIGHT):
        return None
    at = (wx - ORIGIN[0], wy - ORIGIN[1], z)
    if not any(c.kind == "floor" and (c.x, c.y, c.z) == at for c in world.ship_model.components):
        return None
    return at
