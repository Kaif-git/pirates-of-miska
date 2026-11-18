"""Read-only, deliberately explicit shipboard provision forecasts."""

from copy import deepcopy

from pirates.ship import FOOD_RECIPES, FOOD_RESOURCE_KEYS, READY_FOODS

FOOD_KG_PER_CREW_DAY = 0.85
CLEAN_WATER_L_PER_CREW_DAY = 2.2
WATER_SAFETY_DAYS = 1.0


def forecast_provisions(world, *, target_days=None):
    """Estimate ready and cookable food without reserving or mutating stock.

    Cookable projections use available supplies and clean water left after a
    one-day crew drinking reserve. They do not claim that a recipe will finish
    before a voyage unless route duration and labor are included by the caller.
    """
    ship = world.ship
    crew_count = max(1, len(ship.crew))
    food_per_day_g = crew_count * FOOD_KG_PER_CREW_DAY * 1000
    water_per_day_ml = crew_count * CLEAN_WATER_L_PER_CREW_DAY * 1000
    ready_food_g = ship.edible_food_g()
    ready_days = ready_food_g / food_per_day_g
    water_ml = ship.clean_water_total_ml()
    water_days = water_ml / water_per_day_ml
    drink_reserve_ml = round(water_per_day_ml * WATER_SAFETY_DAYS)
    water_stored_in_vessels = ship.container_water_ml()
    cooking_water_ml = max(0, ship.stores["clean_water_ml"]
                            - max(0, drink_reserve_ml - water_stored_in_vessels))

    cookers = [sailor for sailor in ship.crew if sailor.labors.get("cook", 0) > 0]
    galley_available = world.fixtures.available("galley")
    galley_access = (bool(cookers) and galley_available
                     and any(ship.path(world, sailor, ship.stations["galley"]) is not None
                             for sailor in cookers))
    can_cook = (ship.galley_policy["mode"] != "paused"
                and galley_available and bool(cookers) and galley_access)
    galley_status = {"mode": ship.galley_policy["mode"], "fixture_available": galley_available,
                     "cook_laborers": len(cookers), "reachable": galley_access,
                     "can_cook": can_cook, "cause": None, "remedy": None}
    if not can_cook:
        if ship.galley_policy["mode"] == "paused":
            galley_status["cause"] = "galley automation is paused"
            galley_status["remedy"] = "unpause the galley in Galley orders and reserves"
        elif not galley_available:
            galley_status["cause"] = "the galley is unavailable or being relocated"
            galley_status["remedy"] = "finish relocating the galley, or refit it before departure"
        elif not cookers:
            galley_status["cause"] = "no crew member is assigned cook labor"
            galley_status["remedy"] = "assign cook labor to a sailor"
        else:
            galley_status["cause"] = "cook crew cannot reach the galley through the current ship layout"
            galley_status["remedy"] = "open a route to the galley or unlock its doors"
    ingredients = deepcopy(ship.stores)
    ready_remaining_g = (ship.prepared_food_g
                         + sum(ingredients.get(key, 0) for key in READY_FOODS if key != "biscuit_g")
                         + max(0, ingredients.get("biscuit_g", 0)
                               - ship.galley_policy["emergency_biscuit_reserve_g"]))
    if can_cook:
        ingredients["clean_water_ml"] = cooking_water_ml
        recipe_ids = ([ship.galley_policy["preferred_recipe"]]
                      if ship.galley_policy["mode"] == "preferred"
                      and ship.galley_policy["preferred_recipe"] else sorted(FOOD_RECIPES))
        for recipe_id in recipe_ids:
            recipe = FOOD_RECIPES[recipe_id]
            required = recipe["ingredients"]
            batches = min((ingredients.get(key, 0) // amount for key, amount in required.items()),
                          default=0)
            if batches <= 0:
                continue
            batches = min(batches, *(cooking_water_ml // amount
                                     for key, amount in required.items()
                                     if key == "clean_water_ml"))
            if not batches:
                continue
            for key, amount in required.items():
                ingredients[key] -= amount * batches
                if key in READY_FOODS:
                    ready_remaining_g -= amount * batches
            cooking_water_ml -= required.get("clean_water_ml", 0) * batches
            ingredients["clean_water_ml"] = cooking_water_ml
            ready_remaining_g += recipe["output_g"] * batches

    projected_food_g = max(0, ready_remaining_g)
    projected_days = projected_food_g / food_per_day_g
    cook_only_g = sum(ship.stores.get(key, 0) for key in FOOD_RESOURCE_KEYS
                      if key not in READY_FOODS)
    blockers = []
    if not can_cook and cook_only_g:
        blockers.append("Cook-only ingredients are aboard, but " + galley_status["cause"] + ".")
    if can_cook and projected_food_g == ready_food_g and any(
            all(ship.stores.get(key, 0) >= amount for key, amount in recipe["ingredients"].items()
                if key != "clean_water_ml")
            and ship.stores["clean_water_ml"] >= recipe["ingredients"].get("clean_water_ml", 0)
            and ship.stores["clean_water_ml"] - recipe["ingredients"].get("clean_water_ml", 0)
                < ship.galley_water_reserve_ml()
            for recipe in FOOD_RECIPES.values()):
        blockers.append("Cooking water is available only by breaching the protected drinking reserve.")
    if ready_days < 1 and projected_days > ready_days:
        blockers.append("Additional food coverage depends on cooking and is not immediately ready to eat.")
    if water_days < 1:
        blockers.append("Clean water is below the one-day crew drinking reserve.")
    if target_days is not None and water_days < target_days:
        blockers.append(f"Water is short by about {max(0, target_days - water_days):.1f} crew-days.")

    return {
        "crew_count": len(ship.crew),
        "ready_food_g": ready_food_g,
        "total_ready_food_g": ship.ready_food_g(),
        "ready_food_days": ready_days,
        "projected_food_g": projected_food_g,
        "projected_food_days": projected_days,
        "water_ml": water_ml,
        "water_liters": water_ml / 1000,
        "water_days": water_days,
        "water_safety_reserve_ml": min(water_ml, drink_reserve_ml),
        "emergency_biscuit_reserve_g": min(ship.stores["biscuit_g"],
                                            ship.galley_policy["emergency_biscuit_reserve_g"]),
        "water_available_for_cooking_ml": cooking_water_ml,
        "can_cook": can_cook,
        "cook_only_g": cook_only_g,
        "galley_status": galley_status,
        "target_days": target_days,
        "blockers": blockers,
        "assumptions": (f"{CLEAN_WATER_L_PER_CREW_DAY:.1f} L clean water and "
                        f"{FOOD_KG_PER_CREW_DAY:.2f} kg food per crew member/day; "
                        "one-day drinking reserve protected before projected cooking."),
    }


def readiness_rows(world, *, target_days=None, forecast=None):
    """Return compact, read-only departure readiness rows with remedies.

    Every value comes from authoritative state; calling this never advances
    time, changes policy, reserves stock or moves cargo. Trade cargo, carried
    quantities and questionable water are reported separately so they cannot
    be mistaken for ready provisions.
    """
    forecast = forecast or forecast_provisions(world, target_days=target_days)
    ship = world.ship
    rows = [
        f"Food: {forecast['ready_food_g'] / 1000:.2f} kg ready "
        f"({forecast['ready_food_days']:.1f} crew-days); "
        f"{forecast['projected_food_g'] / 1000:.2f} kg with cooking "
        f"({forecast['projected_food_days']:.1f} crew-days); "
        f"{forecast['cook_only_g'] / 1000:.2f} kg needs cooking.",
        f"Water: {forecast['water_liters']:.1f} L potable "
        f"({forecast['water_days']:.1f} crew-days, {ship.container_water_ml() / 1000:.1f} L in vessels); "
        f"{forecast['water_safety_reserve_ml'] / 1000:.1f} L protected drinking reserve.",
    ]
    if ship.stores["questionable_water_ml"]:
        rows.append(f"Questionable water: {ship.stores['questionable_water_ml'] / 1000:.2f} L unchecked and "
                    "excluded from potable coverage.")
        rows.append("Next: treat questionable water before relying on it for drinking.")
    if forecast["emergency_biscuit_reserve_g"]:
        rows.append(f"Protected reserve: {forecast['emergency_biscuit_reserve_g'] / 1000:.2f} kg biscuit "
                    "is held out of routine ready food.")

    if target_days is not None:
        water_margin = forecast["water_days"] - target_days
        food_margin = forecast["projected_food_days"] - target_days
        rows.append(f"Route coverage: {target_days:.1f} days at "
                    f"{CLEAN_WATER_L_PER_CREW_DAY:.1f} L and {FOOD_KG_PER_CREW_DAY:.2f} kg per crew-day; "
                    f"water margin {water_margin:+.1f} d, food margin {food_margin:+.1f} d. "
                    "Contrary winds can extend the passage beyond the estimate.")
        if water_margin < 0:
            rows.append("Next: buy or fill potable water, shorten the route, or plan for rationing.")
        if food_margin < 0:
            rows.append("Next: buy ready food, or assign cook labor with ingredients and water aboard.")

    spoilage = []
    for row in ship.cooked_food_freshness():
        spoilage.append((row["spoils_in_seconds"],
                         f"{ship.food_name(row['food_id'])} {row['grams'] / 1000:.2f} kg"))
    for row in ship.fresh_fruit_freshness():
        spoilage.append((row["spoils_in_seconds"], f"fresh fruit {row['grams'] / 1000:.2f} kg"))
    if spoilage:
        seconds, label = min(spoilage)
        if seconds >= 3600:
            when = f"{seconds // 3600} h"
        elif seconds >= 60:
            when = f"{seconds // 60} min"
        else:
            when = "under a minute"
        rows.append(f"Earliest spoilage: {label} in {when}.")
        if seconds < 24 * 3600:
            rows.append("Next: eat, cook or preserve the soonest-spoiling lot.")

    galley = forecast["galley_status"]
    if galley["can_cook"]:
        rows.append(f"Galley: {galley['mode']}, {galley['cook_laborers']} cook(s) can reach the installed galley.")
    else:
        rows.append(f"Galley: {galley['cause']}.")
        rows.append(f"Next: {galley['remedy']}.")

    from pirates.economy import CATALOG
    worn = []
    for sid, slots in world.economy.gear_condition.items():
        for slot, condition in slots.items():
            item_id = world.economy.gear.get(sid, {}).get(slot)
            if item_id and CATALOG.get(item_id, {}).get("category") == "clothing" and condition < 100:
                sailor = ship.sailor(sid)
                worn.append((condition, f"{sailor.name if sailor else sid}'s {CATALOG[item_id]['name']}"))
    if worn:
        worn.sort()
        condition, label = worn[0]
        rows.append(f"Apparel: {label} at {condition}% condition"
                    + (f"; {len(worn)} garments worn." if len(worn) > 1 else "."))
        if condition <= 40:
            kit = world.economy.cargo.get("sewing_kit", 0)
            cloth = world.economy.cargo.get("sailcloth", 0)
            rows.append("Next: mend worn clothing at the hold with the sewing kit and sailcloth."
                        if kit and cloth else
                        "Next: buy a sewing kit and sailcloth to mend worn clothing.")

    if ship.light_on:
        fuel = (f"{ship.light_fuel_ml} mL oil" if ship.light_type == "lantern"
                else f"{ship.candle_fuel_seconds // 60} min candle")
        rows.append(f"Light: {ship.light_type} lit with {fuel} remaining.")
    else:
        rows.append(f"Light: out; aboard {ship.stores['lanterns']} lantern(s), "
                    f"{ship.stores['lamp_oil_ml']} mL lamp oil, {ship.stores['candles']} candle(s).")
        if ship.stores["lanterns"] and ship.light_fuel_ml <= 0 and not ship.stores["lamp_oil_ml"]:
            rows.append("Next: buy lamp oil before relying on the lantern in dark holds.")

    cargo_units = sum(world.economy.cargo.values())
    if cargo_units:
        cargo_mass_g = sum(CATALOG[item_id]["weight_g"] * count
                           for item_id, count in world.economy.cargo.items())
        rows.append(f"Trade cargo: {cargo_units} unallocated unit(s), {cargo_mass_g / 1000:.1f} kg, "
                    "is not counted above.")
        rows.append("Next: allocate provisions at the hold before departure.")
    return rows
