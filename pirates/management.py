"""Read-only reports and explicit captain orders; browsing never advances time."""
import time
import tcod.event
from pirates.ship import FOOD_RECIPES, LABORS, LABELS, TRAIT_EFFECTS
from pirates.ship_storage import resource_quantity
from pirates.voyage import SEA_NODES


def panel(renderer, world, title, rows):
    renderer.render_list(world, title, rows, "Arrows / wheel scroll; Esc returns", selectable=False)
    renderer.wait_any()


def choose(renderer, world, title, rows, footer="Enter selects; Esc returns"):
    renderer.render_list(world, title, rows, footer)
    return renderer.choose(len(rows))


def hardship_concern_rows(sailor, concern=None):
    """Explain a persisted hardship concern using current state, without new scoring."""
    if concern is None:
        return []
    signals = []
    if sailor.stress >= 65:
        signals.append(f"stress {sailor.stress:.0f} (progression threshold 65)")
    if sailor.hunger >= 75:
        signals.append(f"hunger {sailor.hunger:.0f} (threshold 75)")
    if sailor.thirst >= 75:
        signals.append(f"thirst {sailor.thirst:.0f} (threshold 75)")
    injuries = [injury for injury in sailor.injuries if injury["recovery"] < 100]
    if injuries:
        signals.append("active injury: " + ", ".join(injury["kind"] for injury in injuries))
    opened_at = f"ship hour {concern['opened_second'] // 3600}"
    if concern["status"] == "resolved":
        return ["", f"RECENT CREW CONCERN — resolved at ship hour {concern['resolved_second'] // 3600}",
                "It eased after the existing grievance fell below 35 and morale recovered to at least 42.",
                "Recorded opening signals: " + ", ".join(concern["signals"]) + ". No relationship bonus was applied."]
    progressing = sailor.morale <= 30 and bool(signals)
    rows = ["", f"CREW CONCERN — open since {opened_at}; existing grievance, no extra score"]
    rows.append("Recorded at opening: " + ", ".join(concern["signals"]) + ".")
    rows.append("This report is read-only; opening it does not spend time or change grievance/relationships.")
    if progressing:
        rows.append("Current recorded hardship signals: " + "; ".join(signals) + ".")
        rows.append("These signals plus morale at or below 30 meet the existing grievance progression rule (+0.05/s).")
        rows.append("Captain response: review labor priorities, protect time for real sleep/rest, and address "
                    "provisions or injury as applicable. Normal ship systems determine recovery.")
    else:
        current = signals + [f"morale {sailor.morale:.0f}"]
        rows.append("The grievance is accumulated; current conditions do not meet every progression requirement.")
        rows.append("Current readings: " + "; ".join(current) + ".")
        rows.append("It eases only under existing conditions: morale at least 42, stress below 55, hunger and "
                    "thirst below 60, and no active injury.")
    return rows


def social_activity_rows(sailor, world):
    recent = [row for row in reversed(world.ship.social_events)
              if sailor.id in row["participants"]][:5]
    if not recent:
        return []
    names = {member.id: member.name for member in world.ship.crew}
    names.update({row["id"]: row["name"] for row in world.ship.crew_history})
    pair_counts = {}
    for event in world.ship.social_events:
        if sailor.id in event["participants"]:
            companion_id = next(sid for sid in event["participants"] if sid != sailor.id)
            pair_counts[companion_id] = pair_counts.get(companion_id, 0) + 1
    bonds = sorted(((count, companion_id) for companion_id, count in pair_counts.items() if count >= 3),
                   key=lambda row: (-row[0], row[1]))
    rows = []
    if bonds:
        rows += ["", "REPEATED COMPANIONSHIP (recorded shared occasions)"]
        rows += [f"{names.get(companion_id, companion_id)}: {count} occasions; "
                 f"existing relationship {sailor.relationships.get(companion_id, 0)}/100"
                 for count, companion_id in bonds[:4]]
    rows += ["", "RECENT SHARED-ACTIVITY EVIDENCE"]
    for event in recent:
        companions = [names.get(sid, sid) for sid in event["participants"] if sid != sailor.id]
        when = f"ship hour {event['second'] // 3600}"
        result = ("; +1 existing relationship point each" if event["relationship_delta"] else
                  "; no relationship score change")
        rows.append(f"{when}: {event['kind'].replace('_', ' ')} with "
                    f"{', '.join(companions)}{result}. {event['reason']}")
    return rows


def profile_rows(sailor, world=None):
    location = world.room_name(sailor.x, sailor.y, getattr(sailor, "z", 0)) if world is not None else "Ship position"
    potential = ("exceptional" if sailor.learning_potential >= 85 else
                 "strong" if sailor.learning_potential >= 70 else
                 "steady" if sailor.learning_potential >= 50 else "limited")
    garments = "No garments issued"
    if world is not None:
        kit = world.economy.gear.get(sailor.id, {})
        garments = ", ".join(
            f"{kit[slot]} {world.economy.gear_condition.get(sailor.id, {}).get(slot, 100)}%"
            + (" (WEAR)" if world.economy.gear_condition.get(sailor.id, {}).get(slot, 100) <= 25 else "")
            for slot in kit if slot != "weapon") or garments
    concern = (next((row for row in reversed(world.ship.crew_concerns)
                     if row["crew"] == sailor.id and row["status"] != "archived"), None)
               if world is not None else None)
    social_rows = social_activity_rows(sailor, world) if world is not None else []
    rows = [sailor.trait, f"Age {sailor.age} | Background: {sailor.background} | Learning outlook: {potential}",
             f"Location: {location} ({sailor.x},{sailor.y}, deck {getattr(sailor, 'z', 0)})",
             f"Now: {sailor.activity}",
             f"Rain exposure: wetness {sailor.wetness:.0f}/100 (oilskin slows wetting; overhead built deck shelters)",
             f"Issued kit condition: {garments}",
             f"Health {sailor.health:.0f}/100   Mood: {sailor.mood_label} ({sailor.morale:.0f}/100)", "",
             f"Cleanliness {sailor.cleanliness:.0f}/100 (soap + 250 mL clean water; no health claim)",
            (f"CRITICAL: {sailor.critical_cause}; rescue deadline in "
             f"{max(0, 6 - (world.clock.total_ticks - sailor.critical_since_tick) / 360000):.1f} game hours"
             if world is not None and sailor.critical_since_tick is not None else "Medical state: stable"),
            f"Crew grievance {sailor.grievance:.0f}/100" +
             (f" — leaving warning since hour {sailor.desertion_warning_hour}"
              if sailor.desertion_warning_hour is not None else ""),
             *hardship_concern_rows(sailor, concern), *social_rows,
             ("Shore-side betrayal suspicion is active; retention may address the grievance."
             if sailor.betrayal_warning_tick is not None else ""),
            (f"Retirement requested on day {sailor.retirement_notice_day}; notice ends day {sailor.retirement_notice_day + 30}."
             if sailor.retirement_notice_day is not None else "Retirement: no request"),
            "Injuries: " + ("; ".join(
                f"{row['kind']} (severity {row['severity']}, {row['recovery']:.0f}% recovered)"
                for row in sailor.injuries if row["recovery"] < 100) or "none"),
            "NEEDS / WANTS (higher means more urgent)",
            f"Hunger {sailor.hunger:.0f}   Thirst {sailor.thirst:.0f}   Fatigue {sailor.fatigue:.0f}",
            f"Food preference: {sailor.food_preference.replace('_', ' ')} | "
            f"Consumed today: {sailor.food_consumed_today_g} g food, {sailor.water_consumed_today_ml / 1000:.2f} L water",
            "Recent meals: " + (", ".join(sailor.recent_meals[-4:]) or "none recorded"),
            f"Energy {sailor.energy:.0f}/100   Sleep debt {sailor.sleep_debt:.0f}   Stress {sailor.stress:.0f}",
            "Rest restores energy; only sleep repays sleep debt.",
            f"Company {sailor.loneliness:.0f}   Variety {sailor.boredom:.0f}",
            f"Personal want: { {'craft': 'useful, skilled work', 'social': 'stories and companionship', 'lookout': 'fresh air and a sea view'}[sailor.preference]}",
            f"Own bunk: {sailor.bunk + 1}   Completed chores: {sailor.completed}",
            "Carrying: " + (", ".join(f"{n} {k}" for k, n in sailor.cargo.items()) or "nothing"),
            "", "PERSONALITY / BEHAVIORAL TRAITS",
             "MENTAL / COMMAND (0–100): " + " | ".join(
                 f"{key.replace('_', ' ').title()} {value:.0f}"
                 for key, value in sailor.mental.items()),
             " | ".join(f"{k.title()} {v}" for k, v in sailor.personality.items()),
            ", ".join(t.replace('_', ' ') for t in sailor.traits),
            *(f"{t.replace('_', ' ')}: {TRAIT_EFFECTS[t]}" for t in sailor.traits),
             "", "RECENT THOUGHTS", *(sailor.thoughts or ["A new day aboard the Wayward."]),
             "", "ACTIVE MOOD MEMORIES (fade over 20 game minutes)",
             *(f"{m['mood']:+} ({m['remaining']}s left): {m['text']}" for m in sailor.memories),
             "", "FRIENDSHIPS", *(f"{sid}: {value}" for sid, value in sailor.relationships.items()),
            "", "SKILLS (improve through actual practice)",
            *(f"{LABELS[k]}: {sailor.skills[k]:.2f} | practice {sailor.skill_xp[k]:.1f}" for k in LABORS)]
    if world is not None:
        from pirates.economy import CATALOG
        kit = world.economy.gear.get(sailor.id, {})
        rows += ["", "ISSUED EQUIPMENT"]
        rows += [f"{slot}: {CATALOG[key]['name']} — {CATALOG[key]['description']}" for slot, key in kit.items()]
        rows += [f"Weapon practice: {world.economy.weapon_xp.get(sailor.id, 0)} (combat not implemented)"]
    return rows


def crew_menu(renderer, world):
    while True:
        rows = crew_roster_rows(world)
        rows += ["Former crew — departures and outcomes", "Work compliance / who is following orders?"]
        index = choose(renderer, world, "Crew roster — select a sailor", rows)
        if index is None:
            return
        if index == len(world.ship.crew):
            former = world.ship.crew_history
            panel(renderer, world, "Former crew ledger", [
                f"{row['name']} — {row['status']} on day {row['day']}: {row['cause']}"
                for row in reversed(former)] or ["No one has left the crew."])
            continue
        if index == len(world.ship.crew) + 1:
            panel(renderer, world, "Work compliance", work_rows(world))
            continue
        sailor = world.ship.crew[index]
        while True:
            has_concern = any(row["crew"] == sailor.id and row["status"] == "open"
                              for row in world.ship.crew_concerns)
            rows = ["Crew profile and concern" if has_concern else "Needs, wants, thoughts and skills"]
            rows += [f"{LABELS[k]}: {'OFF' if sailor.labors[k] == 0 else sailor.labors[k]}" for k in LABORS]
            rows += ["Release this sailor from the crew (at port only)"]
            rows += ([f"Honor retirement request (30-day notice ends day {sailor.retirement_notice_day + 30})"]
                     if sailor.retirement_notice_day is not None else [])
            if sailor.grievance >= 60 or sailor.desertion_warning_hour is not None:
                rows += ["Offer a 25-Mark retention bonus / address their concern"]
            selected = choose(renderer, world, f"{sailor.name} — labor priorities (1 highest, 5 lowest)", rows,
                              "Select a labor to set priority; 0 opens the profile; departure and retention options are shown when relevant.")
            if selected is None:
                break
            if selected == 0:
                panel(renderer, world, sailor.name, profile_rows(sailor, world))
            elif selected == len(LABORS) + 1:
                confirm = choose(renderer, world, f"Release {sailor.name} from the crew?", [
                    "Confirm release at the current port", "Keep them aboard"])
                if confirm == 0:
                    try:
                        world.ship.release_crew_member(world, sailor.id)
                    except ValueError as error:
                        panel(renderer, world, "Cannot release sailor", [str(error)])
                    else:
                        break
            elif sailor.retirement_notice_day is not None and selected == len(LABORS) + 2:
                confirm = choose(renderer, world, f"Honor {sailor.name}'s retirement request?", [
                    "Confirm retirement ashore", "Keep them aboard"])
                if confirm == 0:
                    try:
                        world.ship.honor_retirement(world, sailor.id)
                    except ValueError as error:
                        panel(renderer, world, "Cannot honor retirement", [str(error)])
                    else:
                        break
            elif selected == len(LABORS) + 2 + int(sailor.retirement_notice_day is not None) and (sailor.grievance >= 60 or sailor.desertion_warning_hour is not None):
                try:
                    world.ship.offer_retention_bonus(world, sailor.id)
                except ValueError as error:
                    panel(renderer, world, "Cannot offer retention bonus", [str(error)])
            else:
                kind = LABORS[selected - 1]
                value = choose(renderer, world, f"{sailor.name}: {LABELS[kind]}",
                               ["OFF (do not perform)", "1 — Preferred", "2 — High", "3 — Normal", "4 — Low", "5 — Last resort"])
                if value is not None:
                    world.ship.set_labor(sailor.id, kind, value)


def crew_roster_rows(world):
    """Pure roster projection with a visible marker for active hardship concerns."""
    open_concerns = {row["crew"] for row in world.ship.crew_concerns if row["status"] == "open"}
    return [f"{c.glyph} {c.name} | {c.mood_label} {c.morale:.0f} | {c.activity}"
            + (" | CONCERN" if c.id in open_concerns else "") for c in world.ship.crew]


def chores_menu(renderer, world):
    while True:
        jobs = sorted((j for j in world.ship.jobs if j.state not in ("done", "cancelled")),
                      key=lambda j: (j.priority, j.id))
        if not jobs:
            panel(renderer, world, "Chore board", ["All current chores are complete.",
                  "Wear, filth, leaks and low meal stocks create new chores over time."])
            return
        rows = [f"P{j.priority} {world.ship.job_label(j)} | {j.state} | {j.worker or ('ordered: ' + j.assigned_to if j.assigned_to else 'open')} | {j.progress:.0f}/{j.work}"
                for j in jobs]
        selected = choose(renderer, world, "Chore board — exclusive, resumable jobs", rows)
        if selected is None:
            return
        job = jobs[selected]
        action = choose(renderer, world, f"{world.ship.job_label(job)} #{job.id}", [
            "Inspect job / blockage", "Set priority", "Cancel (return carried supplies; 2 min requeue delay)",
            "Assign to a specific sailor / return to shared board"])
        if action == 0:
            panel(renderer, world, "Chore details", [
                f"State: {job.state}   Priority: {job.priority}", f"Worker: {job.worker or 'none'}",
                f"Target: {tuple(job.target)}   Phase: {job.phase}",
                f"Work: {job.progress:.1f}/{job.work}", f"Reason: {job.reason or 'Ready / in progress'}",
                f"Reserved in hold: {job.reserved or 'none'}",
                f"Captain's assignee: {job.assigned_to or 'shared board'}",
                f"Order status: {job_explanation(world, job)}",
                "Supplies must be carried from the hold before work can finish.",
                "Urgent personal needs pause a claim without discarding progress."])
        elif action == 1:
            value = choose(renderer, world, "Chore priority", [f"{i} — {'Highest' if i == 1 else 'Lowest' if i == 5 else 'Normal'}" for i in range(1, 6)])
            if value is not None:
                job.priority = value + 1
        elif action == 2:
            world.ship.cancel(job.id)
        elif action == 3:
            sailors = world.ship.crew
            value = choose(renderer, world, "Assign chore (needs and labor OFF are respected)",
                           ["Anyone eligible (shared board)"] +
                           [f"{c.name} | labor {'OFF' if not c.labors[job.kind] else c.labors[job.kind]} | {c.activity}" for c in sailors])
            if value is not None:
                world.ship.assign(job.id, None if value == 0 else sailors[value - 1].id)


def job_explanation(world, job):
    ship = world.ship
    if job.state in ("done", "cancelled"):
        return job.state
    if job.release_requested or job.cancel_requested:
        return "Returning carried supplies before releasing the claim"
    assignee = ship.sailor(job.assigned_to)
    worker = ship.sailor(job.worker)
    if assignee and not assignee.labors[job.kind]:
        return f"BLOCKED: {assignee.name}'s {job.kind} labor is OFF"
    if worker:
        if worker.intent:
            return f"PAUSED: {worker.name} is attending to {worker.intent}; progress retained"
        return f"{worker.name}: {worker.activity} | {job.progress:.1f}/{job.work} work"
    if assignee and assignee.intent:
        return f"WAITING: {assignee.name} needs {assignee.intent} before accepting"
    if assignee and assignee.job_id is not None:
        return f"WAITING: {assignee.name} is completing chore #{assignee.job_id}"
    missing = [k for k, n in ship.job_cost(job).items() if ship.stores[k] < n]
    if missing:
        return "BLOCKED: missing " + ", ".join(
            f"{resource_quantity(key, amount)} {key.replace('_g', '').replace('_ml', '').replace('_', ' ')}"
            for key, amount in ((key, ship.job_cost(job)[key] - ship.stores[key]) for key in missing))
    if job.kind == "cook":
        from pirates.ship import FOOD_RECIPES
        recipe = FOOD_RECIPES[job.key.removeprefix("cook:")]
        inputs = " + ".join(f"{resource_quantity(key, amount)} {key.replace('_g', '').replace('_ml', '').replace('_', ' ')}"
                             for key, amount in recipe["ingredients"].items())
        return f"Galley recipe: {inputs} → {resource_quantity('stew_g', recipe['output_g'])} {recipe['name']}"
    return job.reason or (f"Waiting for {assignee.name}" if assignee else "Available to eligible crew")


def work_rows(world):
    ship = world.ship
    errors = ship.check_orders()
    rows = ["INVARIANT AUDIT: " + ("FAIL — " + "; ".join(errors) if errors else "PASS — no duplicate claims, disabled-labor violations or lost bundles"),
            "Direct orders override preferences, NOT needs, labor OFF or physical access.", "", "WHO IS DOING WHAT"]
    for c in ship.crew:
        job = ship.job(c.job_id)
        rows += [f"{c.name}: {c.activity}", f"  Mood {c.mood_label}; energy {c.energy:.0f}; sleep debt {c.sleep_debt:.0f}",
                 f"  Claim: {'#' + str(job.id) + ' ' + ship.job_label(job) if job else 'none'}; completed {c.completed}"]
    rows += ["", "DIRECT ORDERS / ACTIVE BOARD"]
    for job in sorted(ship.jobs, key=lambda j: (j.priority, j.id)):
        if job.state not in ("done", "cancelled"):
            rows += [f"#{job.id} {ship.job_label(job)} -> {job.assigned_to or 'any eligible crew'}", "  " + job_explanation(world, job)]
    return rows


def events_menu(renderer, world):
    index = choose(renderer, world, "Captain's reports", ["Crew events", "Work compliance / current orders", "Detailed work audit trail"])
    if index == 0:
        panel(renderer, world, "Crew events", world.ship.events)
    elif index == 1:
        panel(renderer, world, "Work compliance", work_rows(world))
    elif index == 2:
        rows = [f"{r['second']:>5}s | {r['crew'] or '-'} | #{r['job'] or '-'} {r['event']}: {r['reason']}" for r in reversed(world.ship.audit)]
        panel(renderer, world, "Work audit — latest first", rows or ["No work orders have been processed yet."])


def ship_rows(world):
    ship = world.ship
    geometry = world.ship_model.deck_geometry(0)
    mass = world.ship_model.mass_summary()
    from pirates.ship_support import assess, world_loads
    support = assess(world.ship_model, world_loads(world))
    floor_count = sum(c.kind == "floor" for c in world.ship_model.components)
    from pirates.ship_physics import calculate
    physics = calculate(world)
    flood = world.hazards.report()
    from pirates.ship_storage import bundle_mass
    from pirates.economy import PORTS
    from pirates.provisioning import readiness_rows
    piles = [f"{p.id} at {ship.storage.position(p.id, world.fixtures)} | "
             f"{', '.join(f'{key}: {resource_quantity(key, value)}' for key, value in p.contents.items()) or 'empty'} | "
             f"{bundle_mass(p.contents)}/{p.capacity_kg} kg" for p in ship.storage.piles.values()]
    return [f"THE WAYWARD — {'open sea' if world.at_sea else 'moored sloop'}", "",
            f"Port: {PORTS[world.economy.port]['name']} | Purse: {world.economy.money} Marks",
            f"Hull {ship.hull:.0f}/100 | Bilge water {ship.bilge:.1f}/100 | Rigging {ship.rigging:.0f}/100",
            f"Deck geometry: {geometry['tiles']} floor tiles | {geometry['perimeter']} exposed edges | {geometry['holes']} holes",
            f"Structure: {mass['structural_mass_kg']} kg | center of mass {mass['center_of_mass']}",
            f"Load kg: fixtures {physics['mass_kg']['fixtures']:.0f} | supplies {physics['mass_kg']['supplies']:.0f} | "
            f"trade cargo {physics['mass_kg']['trade_cargo']:.0f} | kit {physics['mass_kg']['crew_kit']:.0f}",
            f"Other kg: actors {physics['mass_kg']['actors']:.0f} | prepared food {physics['mass_kg']['prepared_food']:.1f} | "
            f"bilge {physics['mass_kg']['bilge']:.0f} | personal/ground "
            f"{physics['mass_kg']['personal_inventory'] + physics['mass_kg']['ground_items']:.0f} | "
            f"loaded shot {physics['mass_kg']['loaded_ammunition']:.0f}",
            f"Displacement: {physics['total_mass_kg']:.0f}/{physics['displacement_limit_kg']:.0f} kg | "
            f"draft {physics['draft_m']:.2f} m | freeboard {physics['freeboard_m']:.2f} m",
            f"Hull estimate: {physics['hull_beam_m']:.1f} m beam × {physics['hull_length_m']:.1f} m length | "
            f"waterplane {physics['waterplane_area_m2']:.1f} m² | reserve volume "
            f"{max(0.0, physics['displacement_limit_kg'] / 1025 - physics['displaced_volume_m3']):.1f} m³",
            f"Balance: trim {physics['trim_m']:+.2f} m | list {physics['list_deg']:+.1f}° | "
            f"estimated GM {physics['estimated_gm_m']:.2f} m | "
            f"loading margin {physics['loading_margin_kg']:.0f} kg",
            f"Support: {floor_count - len(support['unsupported'])}/{floor_count} floors carry load | "
            f"{len(support['beam_spans'])} beam spans | {len(support['overloaded'])} overloads",
            f"Flood control: {len(flood['breaches'])} open breaches | {flood['water_kg']:.0f} kg in "
            f"{len(flood['wet_tiles'])} wet tiles",
            f"Fire / smoke: {len(flood['fires'])} active fires | {len(flood['smoke_tiles'])} smoky tiles",
            f"Ship light: {ship.light_type or 'none'} {'lit' if ship.light_on else 'out'} | "
            f"lantern fuel {ship.light_fuel_ml} mL | candle {ship.candle_fuel_seconds} s",
            "Shared meals: watch windows " + ", ".join(f"{hour:02d}:00" for hour in ship.meal_window_config["hours"])
            + f"; staggered groups of {ship.meal_window_config['group_size']} | social meals recorded "
            + str(len(ship.shared_meal_social_slots)),
            *(f"WARNING fire at deck {at[2]} tile ({at[0]},{at[1]})" for at, _ in flood["fires"][:8]),
            *(f"WARNING unsupported floor at {at}" for at in support["unsupported"][:8]),
            *(f"WARNING beam at {row['beam']} carries {row['load_kg']}/{row['capacity_kg']} kg"
              for row in support["overloaded"][:8]),
            *(f"WARNING {warning}" for warning in physics["warnings"]),
            *(f"WARNING open hull breach at deck {at[2]} tile ({at[0]},{at[1]}), "
              f"ingress {rate:.2f} kg/s" for at, rate in flood["breaches"][:8]),
            "Cooked batches: " + (", ".join(
                f"{ship.food_name(row['food_id'])} {row['grams'] / 1000:.2f} kg "
                f"({row['condition']}% fresh, {row['spoils_in_seconds'] // 3600} h left)"
                for row in ship.cooked_food_freshness()) or "none"),
            "Fresh fruit lots: " + (", ".join(
                f"{row['grams'] / 1000:.2f} kg ({row['condition']}%, "
                f"{row['spoils_in_seconds'] // 3600} h left)"
                for row in ship.fresh_fruit_freshness()) or "none"),
            f"Food waste: {ship.stores['food_waste_g'] / 1000:.2f} kg | "
            f"Questionable rainwater: {ship.stores['questionable_water_ml'] / 1000:.2f} L",
            f"Total provisions: {ship.food_stock_g() / 1000:.2f} kg | Mean morale: {sum(c.morale for c in ship.crew) / len(ship.crew):.0f}",
            "", "SUPPLY READINESS (read-only)", *readiness_rows(world),
            "", "HOLD (available; reservations and carried bundles excluded)",
            *(f"{key.replace('_g', '').replace('_ml', '').replace('_', ' ').title()}: {resource_quantity(key, value)}"
              for key, value in ship.stores.items()),
            "", "SPATIAL STOCKPILES", *piles,
            "", "CANNONS", *(f"{key.replace('_', ' ')}: {'loaded' if value else 'empty'}" for key, value in ship.ammo.items()),
            "", "STATIONS", *(f"{key.replace('_', ' ')}: {at}" for key, at in ship.stations.items()),
            "", "Systems: finite food (grams/kg), clean water (mL/L), hauling, assigned bunks,",
            "cleaning, leaks, bailing, repairs, rigging and cannon loading.",
            "Enter: trade cargo / crew kit, merchant conversations and coastal passage.",
            "Chart legs retain the strategic route; wind, sail force, resistance and rudder affect actual progress."]


def time_menu(renderer, world):
    seconds = (10, 60, 600)
    index = choose(renderer, world, "Let the crew live / work", ["Wait 10 seconds", "Wait 1 minute", "Wait 10 minutes",
                   "Watch 1 minute of ship life (animated; Esc stops)"],
                   "Simulation is turn-based; menus freeze time. Esc cancels.")
    if index is not None:
        if index == 3:
            observe(renderer, world)
            return
        world.advance(seconds[index] * 100)
        world.message(f"{seconds[index]} seconds passed aboard the Wayward. F6 shows crew events.")


def sea_map_rows(world):
    from pirates.voyage import SEA_NODES
    voyage = world.voyages.voyage
    route = set(voyage["route"]) if voyage else set()
    sighting_reports = getattr(world.npcs, "sighting_reports", {})
    known_ship_nodes = ({ship["id"]: ship["node"] for ship in world.npcs.ships}
                        if world.society.mode != "generated"
                        else {ship_id: report["node"] for ship_id, report in sighting_reports.items()})
    visible_ships = [ship for ship in world.npcs.ships if ship["id"] in known_ship_nodes]
    fleets = set(known_ship_nodes.values())
    rows = ["SEA CHART — strategic route with provisional wind/sail force, resistance and steering.", ""]
    sx, sy = world.voyages.map_coordinate()
    for y in range(5, -1, -1):
        line = []
        for x in range(9):
            node = next((key for key, data in SEA_NODES.items() if data["xy"] == (x, y)), None)
            if round(sx) == x and round(sy) == y:
                line.append(" @ ")
            elif not node:
                line.append(" ~ ")
            elif node in fleets:
                line.append(" ! " if node in route else " e ")
            elif node in route:
                line.append(" * ")
            else:
                line.append(" o ")
        rows.append("".join(line))
    rows += ["", "@ your ship | * planned route | !/e rival vessel | o chart location | ~ open sea", ""]
    rows += [f"{key}: {item['name']} at ({item['xy'][0]},{item['xy'][1]})" for key, item in SEA_NODES.items()]
    if voyage:
        rows += ["", f"Voyage status: {voyage['status']} | {voyage['elapsed_seconds'] // 60} / {voyage['duration_seconds'] // 60} min",
                 f"Route: {' → '.join(SEA_NODES[n]['name'] for n in voyage['route'])}"]
        storm = voyage.get("storm_event")
        if storm and storm["stage"] == "forecast":
            rows.append(f"Squall watch: {storm['chance_percent']}% low-confidence eligibility near "
                        f"{SEA_NODES[storm['node']]['name']}; no storm is guaranteed.")
        elif storm and storm["stage"] == "clear":
            rows.append(f"The low-confidence squall watch passed near {SEA_NODES[storm['node']]['name']}.")
        elif storm and storm["stage"] in ("active", "resolved", "avoided"):
            rows.append(f"Squall event: {storm['stage']} near {SEA_NODES[storm['node']]['name']}"
                        + (f" | choice {storm['choice']}" if storm["choice"] else ""))
        sailing = voyage.get("sailing", {})
        if sailing:
            rows += [f"Sailing estimate: {sailing.get('speed_mps', 0) * 1.94384:.1f} kn | "
                     f"heading {sailing.get('heading_deg', 0):.0f}° | rudder {sailing.get('rudder_deg', 0):+.0f}°",
                     f"Sail drive/resistance: {sailing.get('drive_n', 0):.0f}/{sailing.get('resistance_n', 0):.0f} N | "
                     f"accel {sailing.get('acceleration_mps2', 0):+.4f} m/s² | "
                     f"cross-track {sailing.get('cross_track', 0):+.2f} chart units"]
        if voyage.get("encounter"):
            rows.append(f"SIGHTING: {voyage['encounter']['name']} ({voyage['encounter']['faction']}) — resume in the ship scene; combat not implemented.")
    for ship in visible_ships:
        known_node = known_ship_nodes[ship["id"]]
        if world.society.mode == "generated":
            report = sighting_reports[ship["id"]]
            last_seen = f"last seen at {SEA_NODES[known_node]['name']} on Day {report['observed_hour'] // 24 + 1}"
        else:
            last_seen = f"at {SEA_NODES[known_node]['name']}"
        rows.append(f"{ship['name']} [{ship['mission']}] {last_seen} — {ship['faction']}")
        contact = world.npcs.player_contacts.get(ship["id"])
        if contact:
            contact_day = contact["first_hour"] // 24 + 1
            aid = "; repair aid given" if contact["aid_count"] else ""
            rows.append(f"  Signal contact recorded on Day {contact_day}{aid}.")
        captain_id = next((pid for pid, person in world.society.people.items() if person["ship"] == ship["id"]), None)
        if captain_id:
            captain = world.society.people[captain_id]
            organization = world.society.organizations[captain["organization"]]
            activity = f"order: {ship['mission']}" if world.society.mode == "generated" else captain["activity"]
            rows.append(f"  {captain['title']} {captain['name']} | {organization['name']} | {activity}")
    if world.society.mode == "generated" and not visible_ships:
        rows.append("No rival vessel positions are currently confirmed.")
    rows += [f"Freeport captive market: {world.npcs.market['enslaved_people']} enslaved people; simulated NPC trade only."]
    if world.society.mode == "generated":
        activity_rows = world.society.recent_headlines(8, public_only=True) or ["No public activity reports yet."]
        rows += ["", "RECENT PUBLIC REPORTS", *activity_rows]
    else:
        rows += ["", "RECENT RIVAL ACTIVITY", *(world.npcs.events[-8:] or ["No recorded fleet transactions yet."])]
    rows += ["", "WORLD NEWS", *(world.society.recent_headlines(6, public_only=True)
                                   or ["No major reports yet."])]
    return rows


def generated_society_menu(renderer, world):
    """Inspect public generated identities without exposing private traits/goals."""
    people = world.society.people
    organizations = world.society.organizations
    options = ([f"{person['name']} — {person.get('title', person.get('rank', 'Crew'))}"
                for person in sorted(people.values(), key=lambda row: row["name"])]
               + [f"Organization — {organization['name']}"
                  for organization in sorted(organizations.values(), key=lambda row: row["name"])])
    while True:
        selected = choose(renderer, world, "Generated society | public records", options + ["Return"],
                          "Profiles show public identity and confirmed reports only; private traits and ambitions stay hidden.")
        if selected is None or selected == len(options):
            return
        if selected < len(people):
            person = sorted(people.values(), key=lambda row: row["name"])[selected]
            rows = generated_person_profile_rows(world, person)
            panel(renderer, world, f"Public profile — {person['name']}", rows)
        else:
            organization = sorted(organizations.values(), key=lambda row: row["name"])[selected - len(people)]
            reports = world.society.chronicle_rows(
                20, organization["organization_id"], public_only=True)
            org_id = organization["organization_id"]
            agreement_rows = [agreement for agreement in world.society.agreements
                              if org_id in agreement["participants"]]
            rows = [f"Organization: {organization['name']}", f"Type: {organization['kind'].replace('_', ' ').title()}",
                    f"Public standing: {organization['reputation']}",
                    f"Vessels: {len(organization.get('vessel_ids', organization.get('ships', [])))}",
                    f"Publicly recorded agreements: {sum(row['status'] == 'active' for row in agreement_rows)} active"]
            known_relations = []
            for report in reversed(reports):
                if report["kind"] not in ("petty_feud", "rivalry", "alliance", "diplomacy"):
                    continue
                others = [world.society.organizations[value]["name"]
                          for value in report.get("participants", [])
                          if value in world.society.organizations and value != org_id]
                if others:
                    known_relations.append(f"Reported with {', '.join(others)}: {report['headline']}")
            if known_relations:
                rows += ["", "Known relationship reports:", *known_relations[:4]]
            public_strength = []
            for ship_id in organization.get("vessel_ids", organization.get("ships", [])):
                for report in reversed(reports):
                    estimate = report.get("details", {}).get("strength", {}).get(ship_id)
                    if type(estimate) is int:
                        vessel = next((item for item in world.npcs.ships if item["id"] == ship_id), None)
                        public_strength.append(
                            f"{vessel['name'] if vessel else ship_id}: last public encounter estimate "
                            f"{estimate}/100 (Day {report['hour'] // 24 + 1}), not current")
                        break
            if public_strength:
                rows += ["", "Reported vessel strength:", *public_strength]
            rows += ["", "Recent public reports:"]
            rows += [f"• {row['headline']} (Day {row['hour'] // 24 + 1})" for row in reversed(reports[-5:])]
            if not reports:
                rows.append("No public reports recorded.")
            panel(renderer, world, f"Organization — {organization['name']}", rows)


def generated_person_profile_rows(world, person):
    """Return only public identity, confirmed sightings and published reports."""
    organization = world.society.organizations.get(person.get("organization"), {})
    ship_id = person.get("ship")
    ship = next((row for row in world.npcs.ships if row["id"] == ship_id), None)
    sighting = getattr(world.npcs, "sighting_reports", {}).get(ship_id)
    confirmed = bool(ship and sighting)
    rows = [f"Public name: {person['name']}",
            f"Rank / occupation: {person.get('title', person.get('rank', 'Crew'))} / "
            f"{person.get('occupation', 'not recorded')}",
            f"Organization: {organization.get('name', 'unknown')} (public standing {person.get('reputation', 0)})"]
    if confirmed and ship:
        rows.append(f"Last confirmed vessel: {ship['name']} at {SEA_NODES[sighting['node']]['name']} "
                    f"(Day {sighting['observed_hour'] // 24 + 1})")
    else:
        rows.append("Last confirmed vessel: no recent player sighting")
    public_reports = world.society.chronicle_rows(
        20, person.get("person_id"), public_only=True)
    if ship:
        for report in reversed(public_reports):
            estimate = report.get("details", {}).get("strength", {}).get(ship_id)
            if type(estimate) is int:
                rows.append(f"Last public encounter estimate: strategic strength {estimate}/100 "
                            f"(Day {report['hour'] // 24 + 1}); not a current reading.")
                break
    rows += ["", "Recent public reports:"]
    rows += [f"• {row['headline']} (Day {row['hour'] // 24 + 1})" for row in reversed(public_reports[-5:])]
    if not public_reports:
        rows.append("No public reports recorded.")
    rows += ["", "Private traits, values, goals and relationship judgments are not disclosed here."]
    return rows


def naval_combat_menu(renderer, world):
    """Resolve a persisted ship-to-ship artillery encounter one order at a time."""
    while True:
        voyage = world.voyages.voyage
        sighting = voyage.get("encounter") if voyage else None
        combat = sighting.get("combat") if sighting else None
        if not combat or combat.get("status") != "active":
            return
        target = next((ship for ship in world.npcs.ships if ship["id"] == sighting["id"]), None)
        if target is None:
            panel(renderer, world, "Artillery engagement ended", ["The sighted vessel is no longer in the fleet."])
            return
        guns = [key for key, loaded in world.ship.ammo.items()
                if loaded and world.ship.layout.available(key)]
        rows = [f"Exchange {combat['exchange'] + 1} | {target['name']} hull condition {target['condition']}/100",
                f"Wayward hull {world.ship.hull:.0f}/100 | rigging {world.ship.rigging:.0f}/100",
                f"Loaded Wayward charges: {sum(world.ship.ammo.values())} | target shot aboard: {target.get('cargo', {}).get('shot', 0)}",
                "Cannon orders consume a loaded charge; the opponent may return one finite shot.", "",
                *combat["log"][-6:]]
        options = [f"Fire {key.replace('_', ' ')}" for key in guns]
        fire_count = len(options)
        options += ["Hold fire — advance 30 seconds for crew work / reloading",
                    "Disengage and turn back to the nearest port", "Return to sighting options"]
        selected = choose(renderer, world, "Ship-to-ship cannon exchange", options,
                          "The encounter is saved. Crew work and reload jobs continue between exchanges.")
        if selected is None or selected == fire_count + 2:
            return
        if selected == fire_count:
            world.advance(30 * 100)
            continue
        if selected == fire_count + 1:
            world.voyages.turn_back(world)
            world.message("The captain disengages from the cannon fight and turns back for port.")
            return
        state = world.voyages.cannon_exchange(world, guns[selected])
        if state["status"] == "enemy_withdrew":
            world.voyages.continue_after_sighting()
            world.message(f"{target['name']} withdrew; the voyage may resume.")
            panel(renderer, world, "Enemy vessel withdraws", state["log"][-8:])
            return
        if state["status"] == "player_retreat":
            world.voyages.turn_back(world)
            world.message("The damaged Wayward withdraws to the nearest port for repairs.")
            panel(renderer, world, "Forced withdrawal", state["log"][-8:])
            return


def voyage_menu(renderer, world):
    from pirates.voyage import (SEA_NODES, SEA_ROLES, CAPTAIN_ACTIONS, CREW_ORDERS,
                                VoyageSystem, ticks_to_next_day_boundary)
    labels = {"rest": "Rest between watches", "study_charts": "Study charts (build chart notes)",
              "inspect_ship": "Inspect ship and stores", "talk_with_crew": "Talk with the crew"}
    while True:
        voyage = world.voyages.voyage
        status = voyage["status"] if voyage else "in port"
        options = ["Consult appointed captain, navigator, quartermaster and purser",
                   "Assign captain / navigator / quartermaster / purser",
                   "Choose the captain's free-time action",
                   "Order a crew team: sword drill, brawling or rest",
                   "Plan and order a voyage", "View sea chart and rival ships"]
        if voyage and voyage["status"] == "underway":
            options += ["Watch / advance the voyage", "Set rudder angle / course offset"]
        if voyage and voyage["status"] in ("encounter", "storm"):
            options += ["Resolve nearby-ship sighting" if voyage["status"] == "encounter"
                        else "Resolve squall decision"]
        if voyage and voyage["status"] == "arrived":
            options += ["Read arrival report and follow-up"]
        options += ["Read recent world news"]
        if world.society.mode == "generated":
            options.insert(len(options) - 1, "Inspect generated society")
        choice = choose(renderer, world, f"Voyage command | {status} | {world.clock.format()}", options,
                        "V opens this screen; ship work and needs keep running while time advances.")
        if choice is None:
            return
        if choice == 0:
            panel(renderer, world, "Officer consultation", world.voyages.role_report(world))
        elif choice == 1:
            role_i = choose(renderer, world, "Assign a shipboard role", [
                f"{role.title()}: {world.ship.sailor(world.voyages.roles[role]).name if world.ship.sailor(world.voyages.roles[role]) else 'Unassigned'}"
                for role in SEA_ROLES])
            if role_i is not None:
                role = SEA_ROLES[role_i]
                candidates = [c for c in world.ship.crew if c.id not in (v for k, v in world.voyages.roles.items() if k != role)]
                options = [f"{c.name} | leadership {c.mental['leadership']:.0f}, judgement {c.mental['intelligence']:.0f}, rig {c.skills['rig']:.1f}"
                           for c in candidates]
                options += ["Leave this role unassigned"]
                who = choose(renderer, world, f"Choose {role}", options)
                if who is not None:
                    try:
                        world.voyages.assign_role(world, role,
                                                  candidates[who].id if who < len(candidates) else None)
                    except ValueError as error:
                        world.message(str(error))
        elif choice == 2:
            action = choose(renderer, world, "Captain's free time aboard", [labels[a] for a in CAPTAIN_ACTIONS])
            if action is not None:
                world.voyages.set_captain_action(CAPTAIN_ACTIONS[action])
                world.message(f"Captain's free-time order: {labels[CAPTAIN_ACTIONS[action]]}.")
        elif choice == 3:
            order = choose(renderer, world, "Crew off-duty activity", ["Normal daily work / needs", "Sword-fighting team practice", "Brawling team practice", "Rest between watches"])
            if order is None:
                continue
            kind = CREW_ORDERS[order]
            if kind == "work_as_needed":
                world.voyages.set_crew_order(world, kind, world.voyages.roles["navigator"], [world.voyages.roles["navigator"]])
                continue
            leader_i = choose(renderer, world, "Select team leader / commander", [c.name for c in world.ship.crew])
            if leader_i is None:
                continue
            leader = world.ship.crew[leader_i]
            if kind == "sword_drill":
                candidates = [c for c in world.ship.crew if "weapon" in world.economy.gear.get(c.id, {})]
            else:
                candidates = list(world.ship.crew)
            if leader not in candidates:
                panel(renderer, world, "No suitable team", ["The leader needs an issued weapon for a sword drill."])
                continue
            sizes = [n for n in (2, 3, len(candidates)) if n <= len(candidates)] if kind in ("sword_drill", "brawling_drill") else [1]
            size_i = choose(renderer, world, "Team size", [f"First {n} available sailors" for n in sorted(set(sizes))])
            if size_i is None:
                continue
            members = [leader.id] + [c.id for c in candidates if c.id != leader.id][:sorted(set(sizes))[size_i] - 1]
            try:
                world.voyages.set_crew_order(world, kind, leader.id, members)
                world.message(f"{leader.name} leads {kind.replace('_', ' ')} for {len(members)} sailor(s) during free watches.")
            except ValueError as error:
                panel(renderer, world, "Crew order unavailable", [str(error)])
        elif choice == 4:
            if voyage and voyage["status"] in ("underway", "encounter", "storm"):
                panel(renderer, world, "Voyage in progress", ["Resolve or continue the current voyage first."])
                continue
            destinations = [key for key in SEA_NODES if key != world.voyages.position]
            selected = choose(renderer, world, "Choose destination; officers will review the plan", [SEA_NODES[k]["name"] for k in destinations])
            if selected is None:
                continue
            target = destinations[selected]
            route_options = world.voyages.route_options(world.voyages.position, target)
            route_choice = route_options[0]
            if len(route_options) > 1:
                current_hour = world.clock.total_ticks // 360000
                ship_by_id = {ship["id"]: ship for ship in world.npcs.ships}
                route_labels = []
                for option in route_options:
                    intermediate = option["route"][1:-1]
                    reports = []
                    for ship_id, report in world.npcs.sighting_reports.items():
                        if report.get("node") in intermediate and ship_id in ship_by_id:
                            reports.append(
                                f"{ship_by_id[ship_id]['name']} at {SEA_NODES[report['node']]['name']} "
                                f"({max(0, current_hour - report['observed_hour'])}h since report)"
                            )
                    route = " → ".join(SEA_NODES[node]["name"] for node in option["route"])
                    intel = "; known: " + ", ".join(reports[:2]) if reports else "; no prior lookout report"
                    route_labels.append(f"{route} | {option['distance']:.2f} chart units{intel}")
                route_pick = choose(
                    renderer, world, "Choose a chart route",
                    route_labels,
                    "Equal-distance routes differ by intermediate chart nodes and recorded sightings. "
                    "Reports can be stale; no report is not evidence of safety.",
                )
                if route_pick is None:
                    continue
                route_choice = route_options[route_pick]
            preferred_waypoint = route_choice["waypoint"]
            try:
                plan = world.voyages.plan(world, target, preferred_waypoint)
            except ValueError as error:
                panel(renderer, world, "No route", [str(error)])
                continue
            active_contracts = [contract for contract in world.economy.contracts
                                if contract["status"] == "active"]
            matching_contracts = [contract for contract in active_contracts
                                  if contract["origin"] == world.voyages.position
                                  and contract["destination"] == target]
            review = [f"Captain's order: {SEA_NODES[world.voyages.position]['name']} → {SEA_NODES[target]['name']}",
                      "Navigator's route: " + " → ".join(SEA_NODES[n]["name"] for n in plan["route"]),
                      f"Navigator {plan['navigator']}; estimated {plan['duration_hours']} hours; wind: {plan['wind']['name']}.",
                      f"Quartermaster {plan['quartermaster']}: {plan['water_liters']} L clean water ({plan['water_days']} days); "
                      f"{plan['food_kg']} kg food ({plan['food_days']} days).",
                      f"Purser {plan['purser']}: supplies are finite; ship wages/economy are not simulated.",
                      *world.voyages.role_report(world), "", "Confirm to sail. Daily ship work and needs continue."]
            if plan["storm_forecast"]:
                forecast = plan["storm_forecast"]
                review += ["", f"VOYAGE SQUALL WATCH: {forecast['chance_percent']}% authored eligibility near "
                           f"{SEA_NODES[forecast['node']]['name']} (low-confidence; no storm is guaranteed).",
                           "This is a bounded voyage hazard, not a general ocean-weather forecast."]
            if matching_contracts:
                review += ["", "DELIVERY OBJECTIVE (settled later at the destination harbor office):"]
                for contract in matching_contracts:
                    from pirates.economy import CATALOG
                    review.append(
                        f"{contract['quantity']} {CATALOG[contract['item']]['name']} due by day "
                        f"{contract['deadline_day']}; {contract['reward']} Marks escrowed. "
                        "Cargo stays fungible; the promised quantity is protected from sale/allocation."
                    )
            elif active_contracts:
                review += ["", "Active contracts do not match this route:"]
                for contract in active_contracts:
                    from pirates.economy import CATALOG
                    review.append(
                        f"{contract['quantity']} {CATALOG[contract['item']]['name']} → "
                        f"{contract['destination']} by day {contract['deadline_day']}"
                    )
            panel(renderer, world, "Review voyage order", review)
            confirm = choose(renderer, world, "Commit this voyage order?", ["Confirm voyage order", "Cancel"],
                              f"Estimated {plan['duration_hours']} hours; sighting reports may be stale.")
            if confirm == 0:
                world.voyages.confirm(world, target, preferred_waypoint)
                world.enter_sea()
                panel(renderer, world, "Underway", sea_map_rows(world))
                return
        elif choice == 5:
            panel(renderer, world, "Overworld sea chart", sea_map_rows(world))
        elif voyage and voyage["status"] == "underway" and choice == 6:
            duration = choose(renderer, world, "Advance the simulation; watch the chart and crew", [
                "10 minutes", "1 hour", "6 hours", "Until the next day boundary",
                "Until the next actionable event", "Until arrival",
            ], "All modes use the same second-by-second ship simulation. Encounters and arrival interrupt automatically.")
            if duration is not None:
                fixed_seconds = (600, 3600, 21600, None, None, None)
                if fixed_seconds[duration] is not None:
                    target_ticks = fixed_seconds[duration] * 100
                elif duration == 3:
                    # GameClock.day changes at midnight; target the exact next boundary.
                    target_ticks = ticks_to_next_day_boundary(world.clock.total_ticks)
                else:
                    # ETA is only a planning estimate. Give low-wind voyages a generous,
                    # bounded horizon; the player can pause at each minute-sized batch.
                    current = world.voyages.voyage
                    remaining = max(1, current["duration_seconds"] - current["elapsed_seconds"])
                    horizon_seconds = min(30 * 24 * 3600, max(24 * 3600, remaining * 12))
                    target_ticks = horizon_seconds * 100
                mode_label = ("next day boundary" if duration == 3 else
                              "next actionable event" if duration == 4 else
                              "arrival or actionable interruption" if duration == 5 else "the selected watch")
                world.message(f"Advancing toward {mode_label}; Esc pauses. Crew needs and ship work keep simulating.")
                start_ticks = world.clock.total_ticks
                last_rendered_ticks = 0
                while ((world.clock.total_ticks - start_ticks) < target_ticks
                       and world.voyages.voyage["status"] == "underway"):
                    # Keep input responsive while preserving canonical second-by-second
                    # updates and the existing encounter/arrival interruption hook.
                    batch_ticks = min(6000, target_ticks - (world.clock.total_ticks - start_ticks))
                    world.advance(batch_ticks)
                    elapsed_ticks = world.clock.total_ticks - start_ticks
                    interrupted = world.voyages.voyage["status"] != "underway"
                    if elapsed_ticks - last_rendered_ticks >= 60000 or interrupted or elapsed_ticks >= target_ticks:
                        renderer.render_list(world, "Sailing — sea chart", sea_map_rows(world),
                                             "Esc pauses passage; V returns to voyage orders")
                        renderer._present_layers()
                        last_rendered_ticks = elapsed_ticks
                    for event in tcod.event.get():
                        if isinstance(event, tcod.event.Quit):
                            renderer._pending_events.append(event)
                            return
                        if isinstance(event, tcod.event.KeyDown) and event.sym == tcod.event.KeySym.ESCAPE:
                            target_ticks = elapsed_ticks
                            world.message("Sea-chart passage paused. Voyage and ship time stop until your next command.")
                            break
                if (world.voyages.voyage["status"] == "underway"
                        and duration in (4, 5)
                        and world.clock.total_ticks - start_ticks >= target_ticks):
                    world.message("No actionable event or arrival within the bounded passage horizon; the voyage remains underway.")
                if world.voyages.voyage["status"] in ("encounter", "storm", "arrived"):
                    if world.voyages.voyage["status"] == "arrived":
                        panel(renderer, world, "Voyage arrival report",
                              world.voyages.arrival_report(world))
                    else:
                        panel(renderer, world, "Sea chart / voyage update", sea_map_rows(world))
                if world.voyages.voyage["status"] in ("encounter", "storm", "arrived"):
                    return
        elif voyage and voyage["status"] == "underway" and choice == 7:
            rudders = (-30, -15, 0, 15, 30)
            selected = choose(renderer, world, "Rudder order relative to the navigator's leg",
                              [f"{angle:+d}°" for angle in rudders],
                              "Sustained rudder turns the heading; off-course steering reduces route progress.")
            if selected is not None:
                world.voyages.set_rudder(rudders[selected])
                world.message(f"Rudder ordered {rudders[selected]:+d}° from the chart leg.")
        elif voyage and voyage["status"] == "encounter" and choice == 6:
            sighting = voyage["encounter"]
            battle = sighting.get("combat", {})
            cannon_label = ("Resume the ship-to-ship cannon exchange"
                            if battle and battle.get("status") == "active"
                            else "Begin a ship-to-ship cannon exchange")
            options = [cannon_label]
            actions = ["combat"]
            if sighting.get("threat") == "pirates":
                options.append("Repel a pirate boarding party")
                actions.append("boarding")
            options += ["Hold course and resume this route", "Turn back to the nearest port"]
            actions += ["continue", "turn_back"]
            if sighting.get("threat") != "pirates" and isinstance(sighting.get("id"), str):
                contact = world.npcs.player_contacts.get(sighting["id"])
                if not contact or not contact["hailed"]:
                    options.append("Hail and exchange signals (1 minute)")
                    actions.append("hail")
                live_ship = next((ship for ship in world.npcs.ships
                                  if ship["id"] == sighting["id"]), None)
                if (contact and contact["hailed"] and contact["aid_count"] == 0
                        and live_ship and live_ship["condition"] <= 60):
                    options.append("Offer one iron repair unit (+20 condition; requires hold access)")
                    actions.append("aid")
            result = choose(renderer, world, f"Nearby ship: {sighting['name']}", options,
                            "Public lookout information only. Aid costs one uncommitted iron; pirates remain hostile.")
            if result is None:
                continue
            action = actions[result]
            if action == "combat":
                try:
                    world.voyages.begin_naval_combat(world)
                except ValueError as error:
                    panel(renderer, world, "Cannot engage", [str(error)])
                    continue
                naval_combat_menu(renderer, world)
                return
            if action == "boarding":
                from pirates.ship_boarding import breach_hull_for_boarders
                count = min(5, max(1, int(sighting.get("crew", 3)) // 3))
                assault = breach_hull_for_boarders(world)
                if not assault or assault["path"] is None:
                    panel(renderer, world, "Boarding route blocked", [
                        "The raiders cannot find a traversable route from the hull to the captain.",
                        "The breach and any forced doors remain real ship damage."])
                else:
                    entry = assault["entry"]
                    world.combat.start_boarding(
                        world, (entry[0] + 69, entry[1] + 12), count=count,
                        seed=(world.clock.total_ticks // 100 + sum(map(ord, sighting["id"]))) % 2_147_483_647)
                    if not world.combat.active:
                        world.message("The boarders found a breach, but no valid fighting deck could be formed.")
                    return
            elif action == "hail":
                try:
                    world.npcs.hail_ship(world, sighting["id"])
                except ValueError as error:
                    panel(renderer, world, "Hail unavailable", [str(error)])
            elif action == "aid":
                try:
                    world.npcs.assist_ship(world, sighting["id"])
                except ValueError as error:
                    panel(renderer, world, "Assistance unavailable", [str(error)])
            elif action == "continue":
                world.voyages.continue_after_sighting()
                world.message(f"The lookout keeps {sighting['name']} in view; you hold course.")
            elif action == "turn_back":
                world.voyages.turn_back(world)
                world.message("The navigator lays in a return route to the nearest port.")
                return
        elif voyage and voyage["status"] == "storm" and choice == 6:
            storm = voyage["storm_event"]
            prior_nodes = set(voyage["route"][:voyage["leg"]])
            threatened_lane = storm["storm_lane"]
            current_suffix = voyage["route"][voyage["leg"]:]
            current_distance = sum(
                ((SEA_NODES[b]["xy"][0] - SEA_NODES[a]["xy"][0]) ** 2
                 + (SEA_NODES[b]["xy"][1] - SEA_NODES[a]["xy"][1]) ** 2) ** 0.5
                for a, b in zip(current_suffix, current_suffix[1:])
            )
            completed_distance = sum(
                ((SEA_NODES[b]["xy"][0] - SEA_NODES[a]["xy"][0]) ** 2
                 + (SEA_NODES[b]["xy"][1] - SEA_NODES[a]["xy"][1]) ** 2) ** 0.5
                for a, b in zip(voyage["route"][:voyage["leg"] + 1],
                                voyage["route"][1:voyage["leg"] + 1])
            )
            detours = [row for row in world.voyages.route_options(
                world.voyages.position, voyage["destination"], include_detours=True)
                if row["route"][:2] != threatened_lane
                and not any(node in prior_nodes for node in row["route"][1:])]
            labels = []
            for row in detours:
                revised_duration = max(
                    voyage["elapsed_seconds"] + 1,
                    round(voyage["duration_seconds"]
                          * (completed_distance + row["distance"])
                          / max(0.01, voyage["distance"])),
                )
                extra_hours = max(0, revised_duration - voyage["duration_seconds"]) / 3600
                labels.append(
                    f"Avoid via {' → '.join(SEA_NODES[n]['name'] for n in row['route'])} "
                    f"| {row['distance']:.2f} chart units "
                    f"({row['distance'] - current_distance:+.2f} from current leg); "
                    f"ETA +{extra_hours:.1f}h est."
                )
            severity = "severe" if storm["trigger_roll"] < 8 else "moderate"
            hull_damage, rigging_damage = (10, 16) if severity == "severe" else (5, 8)
            labels.append(
                f"Press on through the {severity} squall (hull -{hull_damage}, rigging -{rigging_damage})"
            )
            response = choose(
                renderer, world, f"Squall ahead near {SEA_NODES[storm['node']]['name']}", labels,
                "Avoidance follows a charted lane and adds sailing distance; pressing on damages existing hull/rigging condition.",
            )
            if response is not None:
                try:
                    if response < len(detours):
                        world.voyages.resolve_storm(world, "detour", detours[response]["route"])
                    else:
                        world.voyages.resolve_storm(world, "press_on")
                except ValueError as error:
                    panel(renderer, world, "Storm response unavailable", [str(error)])
        elif voyage and voyage["status"] == "arrived" and choice == 6:
            panel(renderer, world, "Voyage arrival report", world.voyages.arrival_report(world))
        elif choice == len(options) - 1:
            rows = []
            for item in reversed(world.society.chronicle_rows(20, public_only=True)):
                rows += [f"{item['headline']} — Day {item['hour'] // 24 + 1}, hour {item['hour'] % 24:02d}",
                         item["body"], f"Cause: {item['cause']} | Outcome: {item['outcome']}", ""]
            panel(renderer, world, "News from the Miska sea lanes", rows or ["No major news has reached port yet."])
        elif world.society.mode == "generated" and choice == len(options) - 2:
            generated_society_menu(renderer, world)


def observe(renderer, world, seconds=60, frame_delay=0.10):
    """Explicit accelerated observation, not a background real-time simulation."""
    renderer._move_repeater.cancel()
    renderer._finish_motion_cache()
    world.message("Watching the crew (accelerated). Esc stops; Q saves and quits afterwards.")
    for _ in range(seconds):
        world.advance(100)
        renderer.render(world)
        deadline = time.perf_counter() + frame_delay
        while time.perf_counter() < deadline:
            for event in tcod.event.wait(max(0, deadline - time.perf_counter())):
                if isinstance(event, tcod.event.Quit):
                    renderer._pending_events.append(event)
                    return
                if isinstance(event, tcod.event.KeyDown):
                    if event.sym == tcod.event.KeySym.ESCAPE:
                        world.message("Observation stopped. Time is paused until your next action.")
                        return
                    if event.sym == tcod.event.KeySym.Q:
                        renderer._pending_events.append(event)
                        return
    world.message("One minute of ship life observed. Time is paused again.")


def context_menu(renderer, world):
    nearby = [c for c in world.ship.crew
              if abs(c.x - world.player.x) + abs(c.y - world.player.y) <= 2
              and world.game_map.visible[c.y, c.x]]
    merchants = [m for m in world.economy.merchants()
                 if abs(m.x - world.player.x) + abs(m.y - world.player.y) <= 2
                 and world.game_map.visible[m.y, m.x]]
    rows = [f"Talk with {c.name}" for c in nearby] + ["Toggle adjacent door", "Ship ledger", "Construction orders", "Room designation / report", "Relocate equipment", "Storage / hauling",
            "Trade cargo / crew equipment", "Island guide / coastal passage"]
    rows += [f"Talk / trade with {m.name}" for m in merchants]
    rows += ["Toggle lock on nearby door",
             "Release ship lockdown" if world.lockdown else "Engage ship lockdown"]
    rows += ["Logistics / traffic inspector"]
    index = choose(renderer, world, "Nearby interactions", rows)
    if index is None:
        return
    if index < len(nearby):
        sailor = nearby[index]
        panel(renderer, world, sailor.name, profile_rows(sailor, world))
        if choose(renderer, world, sailor.name, ["Share a few words (10 seconds)", "Leave them to it"]) == 0:
            sailor.loneliness = max(0, sailor.loneliness - 25)
            sailor.think("The captain took time to hear me out.", 3)
            world.advance(1000)
            world.message(f"You shared a few words with {sailor.name}.")
    elif index == len(nearby):
        world.toggle_door()
    elif index == len(nearby) + 1:
        panel(renderer, world, "Ship ledger", ship_rows(world))
    elif index == len(nearby) + 2:
        construction_menu(renderer, world)
    elif index == len(nearby) + 3:
        room_menu(renderer, world)
    elif index == len(nearby) + 4:
        relocation_menu(renderer, world)
    elif index == len(nearby) + 5:
        storage_menu(renderer, world)
    elif index == len(nearby) + 6:
        from pirates.trading_ui import cargo_menu
        cargo_menu(renderer, world)
    elif index == len(nearby) + 7:
        from pirates.trading_ui import port_menu
        port_menu(renderer, world)
    elif index < len(nearby) + 8 + len(merchants):
        from pirates.trading_ui import merchant_menu
        merchant_menu(renderer, world, merchants[index - len(nearby) - 8].id)
    elif index == len(nearby) + 8 + len(merchants):
        world.toggle_adjacent_door_lock()
    elif index == len(nearby) + 9 + len(merchants):
        world.set_lockdown(not world.lockdown)
    else:
        from pirates.ship_logistics import inspect
        report = inspect(world)
        panel(renderer, world, "Logistics / traffic inspector", report["rows"])
        action = choose(renderer, world, "Traffic overlay", [
            "Show shared planned routes on the ship map",
            "Clear traffic overlay"],
            "Overlay marks shared crew routes with `;`; hazard and lock markers take precedence.")
        if action == 0:
            world.traffic_overlay = report["traffic"]
            world.message(f"Traffic overlay enabled: {len(report['traffic'])} shared route tiles.")
        elif action == 1:
            world.traffic_overlay.clear()
            world.message("Traffic overlay cleared.")


def storage_menu(renderer, world):
    storage = world.ship.storage
    while True:
        action = choose(renderer, world, "Spatial storage — location and contents matter", [
            "Inspect stockpiles", "Designate floor stockpile beside captain",
            "Remove empty floor stockpile beside captain", "Order physical transfer",
            "Galley policy and emergency reserves", "Manage reusable water containers",
            "Inspect/dispose food waste", "Bandage or wash a crew member",
            "Light/extinguish lantern or candle"],
            "Reservations and carried goods are separate from available pile totals. Esc returns.")
        if action is None:
            return
        if action == 0:
            from pirates.ship_storage import bundle_mass
            rows = [f"{p.id} at {storage.position(p.id, world.fixtures)} | capacity {p.capacity_kg} kg | "
                    f"contents {', '.join(f'{key}: {resource_quantity(key, amount)}' for key, amount in p.contents.items()) or 'empty'} "
                    f"({bundle_mass(p.contents):.2f} kg) | "
                    f"available {storage.available(p.id, world.fixtures)}" for p in storage.piles.values()]
            panel(renderer, world, "Stockpile ledger", rows)
        elif action == 4:
            galley_menu(renderer, world)
        elif action == 5:
            container_menu(renderer, world)
        elif action == 6:
            amount = ship.stores["food_waste_g"]
            if amount <= 0:
                panel(renderer, world, "Food waste", ["No accumulated food waste to dispose."])
                continue
            selected = choose(renderer, world, "Discard food waste overboard", [
                f"All waste: {amount / 1000:.2f} kg"])
            if selected is not None:
                ship.dispose_food_waste(amount)
                world.advance(1000)
        elif action == 7:
            try:
                world.economy.require_hold(world)
            except ValueError as error:
                panel(renderer, world, "Crew supplies unavailable", [str(error)])
                continue
            selected = choose(renderer, world, "Select crew supply action", [
                "Apply one bandage to an active injury", "Wash with one soap bar + 250 mL potable water"])
            if selected is None:
                continue
            sailor_index = choose(renderer, world, "Choose sailor", [c.name for c in ship.crew])
            if sailor_index is None:
                continue
            sailor = ship.crew[sailor_index]
            try:
                if selected == 0:
                    injury = ship.apply_bandage(sailor)
                    world.message(f"Dressed {sailor.name}'s {injury['kind']}; one roll used. Infirmary medicine remains separate.")
                else:
                    ship.wash_with_soap(sailor)
                    world.message(f"Washed {sailor.name}; 250 mL clean water and one soap bar used.")
                world.advance(1000)
            except ValueError as error:
                panel(renderer, world, "Crew supply action blocked", [str(error)])
        elif action == 8:
            try:
                world.economy.require_hold(world)
            except ValueError as error:
                panel(renderer, world, "Lighting supplies unavailable", [str(error)])
                continue
            if ship.light_on:
                ship.toggle_ship_light(ship.light_type)
                world.refresh()
                world.message("Ship light extinguished; remaining fuel was retained.")
                continue
            selected = choose(renderer, world, "Choose a light source", [
                f"Lantern | {ship.stores['lanterns']} aboard, {ship.stores['lamp_oil_ml']} mL oil",
                f"Candle | {ship.stores['candles']} aboard; {ship.candle_fuel_seconds} s remaining in current candle"])
            if selected is not None:
                try:
                    ship.toggle_ship_light("lantern" if selected == 0 else "candle")
                    world.refresh()
                    world.message("Light lit; visibility extends to 24 tiles. Shielded flame ignition is not simulated.")
                except ValueError as error:
                    panel(renderer, world, "Lighting blocked", [str(error)])
        elif action in (1, 2):
            direction = choose(renderer, world, "Select adjacent tile", ["North", "East", "South", "West"])
            if direction is None:
                continue
            dx, dy = ((0, -1), (1, 0), (0, 1), (-1, 0))[direction]
            at = world.player.x + dx, world.player.y + dy
            try:
                if action == 1:
                    if world.ship.path(world, world.player, (*at, world.player.z)) is None:
                        raise ValueError("No reachable floor route to that deck tile")
                    pile = storage.designate(world.ship_model, world.ship_fixtures, *at,
                                             z=world.player.z)
                    world.message(f"Designated {pile.id}, capacity {pile.capacity_kg} kg. No time passed.")
                else:
                    local_x, local_y = at[0] - 69, at[1] - 12
                    pid = (f"pile:{local_x},{local_y}" if world.player.z == 0 else
                           f"pile:{local_x},{local_y},{world.player.z}")
                    storage.und_designate(pid, world.ship.jobs)
                    world.message(f"Removed empty stockpile {pid}. No time passed.")
            except ValueError as error:
                world.message(f"Storage designation blocked: {error}")
                panel(renderer, world, "Stockpile blocked", [str(error)])
        elif action == 3:
            piles = list(storage.piles.values())
            source_i = choose(renderer, world, "Choose supply source", [p.id for p in piles])
            if source_i is None:
                continue
            destination_i = choose(renderer, world, "Choose destination", [p.id for p in piles])
            if destination_i is None:
                continue
            source, destination = piles[source_i], piles[destination_i]
            if source.id == destination.id:
                panel(renderer, world, "Transfer blocked", ["Choose two different stockpiles."])
                continue
            resources = [key for key, amount in source.contents.items() if amount > 0]
            if not resources:
                panel(renderer, world, "Transfer blocked", ["Source has no unreserved supplies."])
                continue
            selected = choose(renderer, world, "Choose resource", [
                f"{key}: {source.contents[key]} available" for key in resources])
            if selected is None:
                continue
            key = resources[selected]
            maximum = source.contents[key]
            from pirates.ship_storage import HAUL_CAPACITY_KG, UNIT_MASS
            maximum = min(maximum, int(HAUL_CAPACITY_KG / UNIT_MASS[key]))
            if maximum < 1:
                panel(renderer, world, "Transfer blocked", ["No available quantity fits one crew load."])
                continue
            quantities = sorted({1, min(5, maximum), maximum})
            amount = choose(renderer, world, "Choose quantity", [str(n) for n in quantities])
            if amount is None:
                continue
            try:
                from pirates.ship_storage import queue_transfer
                job = queue_transfer(world, source.id, destination.id, {key: quantities[amount]})
                world.message(f"Queued physical haul #{job.id}: {job.transfer['bundle']} {source.id} -> {destination.id}.")
            except ValueError as error:
                panel(renderer, world, "Transfer blocked", [str(error)])


def galley_menu(renderer, world):
    ship = world.ship
    while True:
        policy = ship.galley_policy
        active = next((job for job in ship.jobs if job.kind == "cook"
                       and job.state not in ("done", "cancelled")), None)
        summary = [f"Mode: {policy['mode']} | Preferred: {policy['preferred_recipe'] or 'none'}",
                   f"Ready-food target: {policy['ready_food_target_g'] / 1000:.2f} kg | "
                   f"Biscuit reserve: {policy['emergency_biscuit_reserve_g'] / 1000:.2f} kg",
                   f"Drinking-water reserve: {ship.galley_water_reserve_ml() / 1000:.1f} L "
                   f"({'one crew-day' if not policy['water_reserve_ml'] else 'custom'})",
                   f"Ready food available to routine meals: {ship.edible_food_g() / 1000:.2f} kg",
                   f"Active batch: {ship.job_label(active) if active else 'none'}"]
        action = choose(renderer, world, "Galley orders and reserves", [
            "Inspect policy", "Change automation mode", "Choose preferred recipe",
            "Set ready-food target", "Set drinking-water reserve", "Set emergency biscuit reserve",
            "Cook one recipe batch now", "Emergency recipe batch (reserve override)",
            "Preserve 1 kg fruit with salt"],
            "Emergency cooking is logged and can spend protected drinking water. Esc returns.")
        if action is None:
            return
        if action == 0:
            panel(renderer, world, "Galley policy", summary + [
                "Normal crew needs protect emergency biscuits and the drinking-water reserve.",
                "Critical hunger/thirst may use protected reserves automatically."])
        elif action == 1:
            modes = ["automatic", "preferred", "maintain", "paused"]
            selected = choose(renderer, world, "Automation mode", modes)
            if selected is not None:
                try:
                    ship.set_galley_policy(mode=modes[selected])
                    world.message(f"Galley mode set to {modes[selected]}.")
                except ValueError as error:
                    panel(renderer, world, "Policy blocked", [str(error)])
        elif action == 2:
            recipes = list(sorted(FOOD_RECIPES))
            selected = choose(renderer, world, "Preferred recipe", [
                f"{FOOD_RECIPES[key]['name']} — {FOOD_RECIPES[key]['description']}" for key in recipes])
            if selected is not None:
                try:
                    ship.set_galley_policy(preferred_recipe=recipes[selected])
                    world.message(f"Preferred galley recipe: {FOOD_RECIPES[recipes[selected]]['name']}.")
                except ValueError as error:
                    panel(renderer, world, "Policy blocked", [str(error)])
        elif action == 3:
            targets = [1000, 3000, 6000, 10000]
            selected = choose(renderer, world, "Ready-food target", [f"{value / 1000:.1f} kg" for value in targets])
            if selected is not None:
                ship.set_galley_policy(ready_food_target_g=targets[selected])
        elif action == 4:
            reserves = [0, 6000, 12000, 18000, 24000]
            labels = ["Automatic: 1 crew-day"] + [f"{value / 1000:.0f} L" for value in reserves[1:]]
            selected = choose(renderer, world, "Protected drinking-water reserve", labels)
            if selected is not None:
                ship.set_galley_policy(water_reserve_ml=reserves[selected])
        elif action == 5:
            reserves = [500, 1000, 1500, 2000]
            selected = choose(renderer, world, "Emergency biscuit reserve",
                               [f"{value / 1000:.1f} kg" for value in reserves])
            if selected is not None:
                ship.set_galley_policy(emergency_biscuit_reserve_g=reserves[selected])
        elif action in (6, 7):
            recipes = list(sorted(FOOD_RECIPES))
            selected = choose(renderer, world, "Choose one galley batch", [
                f"{FOOD_RECIPES[key]['name']} — {FOOD_RECIPES[key]['description']}" for key in recipes])
            if selected is not None:
                try:
                    emergency = action == 7
                    job = ship.queue_galley_once(recipes[selected], emergency_override=emergency)
                    world.message(("Emergency override logged; queued " if emergency else "Queued one batch: ")
                                  + ship.job_label(job) + ".")
                except ValueError as error:
                    panel(renderer, world, "Emergency cooking blocked", [str(error)])
        elif action == 8:
            try:
                job = ship.queue_fruit_preservation()
                world.message(f"Queued {ship.job_label(job)}; 1 kg fruit and 100 g salt will be hauled to the galley.")
            except ValueError as error:
                panel(renderer, world, "Preservation blocked", [str(error)])


def container_menu(renderer, world):
    ship = world.ship
    water_station = ship.stations["water"]
    if abs(world.player.x - water_station[0]) + abs(world.player.y - water_station[1]) > 2:
        panel(renderer, world, "Water station required", [
            "Fill, decant and assign containers only beside the installed water station."])
        return
    while True:
        rows = [f"#{vessel['id']} {vessel['item_id'].replace('_', ' ')} | "
                f"{vessel['contents_ml'] / 1000:.2f}/{vessel['capacity_ml'] / 1000:.2f} L | "
                f"tare {vessel['tare_g'] / 1000:.2f} kg | "
                f"quality {vessel['quality'] or 'empty'} | owner {vessel['owner'] or 'ship'}"
                for vessel in ship.containers]
        action = choose(renderer, world, "Reusable water containers", rows + [
            "Fill from clean-water store", "Decant clean water into ship store", "Assign owner / ship store",
            "Inspect rain collector and water quality", "Order boiling of 1 L questionable water"],
            "Transfers are exact; vessel capacity and hold capacity are enforced. Esc returns.")
        if action is None:
            return
        if action < len(ship.containers):
            vessel = ship.containers[action]
            panel(renderer, world, "Container details", [
                f"{vessel['item_id']} — {vessel['contents_ml']} mL / {vessel['capacity_ml']} mL",
                f"Clean water aboard in containers: {ship.container_water_ml() / 1000:.2f} L"])
            continue
        action -= len(ship.containers)
        if action == 0:
            index = choose(renderer, world, "Choose vessel to fill", [
                f"#{v['id']} ({(v['capacity_ml'] - v['contents_ml']) / 1000:.2f} L free)"
                for v in ship.containers])
            if index is None:
                continue
            vessel = ship.containers[index]
            maximum = min(vessel["capacity_ml"] - vessel["contents_ml"], ship.stores["clean_water_ml"])
            if maximum <= 0:
                panel(renderer, world, "Fill unavailable", ["No free vessel capacity or central clean water."])
                continue
            amount = choose(renderer, world, "Amount to fill", [str(n) for n in sorted({maximum, min(maximum, 250), min(maximum, 500)})])
            if amount is not None:
                quantity = sorted({maximum, min(maximum, 250), min(maximum, 500)})[amount]
                try:
                    ship.fill_container(vessel["id"], quantity)
                    world.advance(300)
                except ValueError as error:
                    panel(renderer, world, "Fill blocked", [str(error)])
        elif action == 1:
            index = choose(renderer, world, "Choose vessel to decant", [
                f"#{v['id']} ({v['contents_ml']} mL clean water)" for v in ship.containers])
            if index is None:
                continue
            vessel = ship.containers[index]
            maximum = vessel["contents_ml"]
            if maximum <= 0:
                panel(renderer, world, "Decant unavailable", ["That container is empty."])
                continue
            quantities = sorted({maximum, min(maximum, 250), min(maximum, 500)})
            amount = choose(renderer, world, "Amount to decant (mL)", [str(n) for n in quantities])
            if amount is not None:
                try:
                    ship.decant_container(vessel["id"], quantities[amount])
                    world.advance(300)
                except ValueError as error:
                    panel(renderer, world, "Decant blocked", [str(error)])
        elif action == 2:
            index = choose(renderer, world, "Choose container", [f"#{v['id']} {v['item_id']}" for v in ship.containers])
            if index is None:
                continue
            vessel = ship.containers[index]
            owners = [None] + [sailor.id for sailor in ship.crew]
            owner_index = choose(renderer, world, "Assign to", ["Ship supply", *[s.name for s in ship.crew]])
            if owner_index is not None:
                vessel["owner"] = owners[owner_index]
                world.advance(100)
        elif action == 3:
            fixture = world.fixtures.get("rain_collector")
            rain_now = ship.is_raining(world.clock.day, world.clock.hour_minute[0])
            panel(renderer, world, "Ship water supply", [
                f"Central potable water: {ship.stores['clean_water_ml'] / 1000:.2f} L",
                f"Potable water in clean containers: {ship.container_water_ml() / 1000:.2f} L",
                f"Questionable rainwater: {ship.stores['questionable_water_ml'] / 1000:.3f} L — not drinkable",
                f"Firewood: {ship.stores['firewood_g'] / 1000:.2f} kg",
                f"Collector: {'installed and active' if fixture and fixture.carrier is None else 'unavailable'} | "
                f"weather: {'rain' if rain_now else 'dry'} | collected today {ship.rain_collected_today_ml} mL",
                "Boiling treats modeled biological contamination only; it does not desalinate seawater or remove chemical hazards."])
        elif action == 4:
            try:
                job = ship.queue_water_treatment()
                world.message(f"Queued {ship.job_label(job)}; 1 L questionable water and 250 g firewood will be hauled to the galley.")
            except ValueError as error:
                panel(renderer, world, "Water treatment blocked", [str(error)])


def relocation_menu(renderer, world):
    from pirates.ship_relocation import queue
    fixtures = list(world.fixtures.fixtures)
    index = choose(renderer, world, "Relocate equipment — select identity", [
        f"{f.id} | {f.mass_kg} kg | {'carried by ' + f.carrier if f.carrier else str(world.fixtures.world_position(f))}"
        for f in fixtures], "Installed equipment must be unfastened, hauled and refastened. Esc returns.")
    if index is None:
        return
    directions = ((0, -1), (1, 0), (0, 1), (-1, 0))
    direction = choose(renderer, world, "Destination adjacent to the captain", ["North", "East", "South", "West"],
                       "Clear existing deck required. A manages jobs; F4 lets crew work.")
    if direction is None:
        return
    dx, dy = directions[direction]
    try:
        queue(world, fixtures[index].id, (world.player.x + dx, world.player.y + dy))
    except ValueError as error:
        world.message(f"Cannot relocate equipment: {error}")
        panel(renderer, world, "Relocation blocked", [str(error)])


def room_menu(renderer, world):
    from pirates.ship_spaces import PURPOSES, report_rows
    x, y = world.player.x, world.player.y
    while True:
        current = world.spaces.find(world.ship_model, x, y)
        release_allowed = bool(current and "brig" in world.spaces.purposes(current))
        options = ["Inspect room / equipment / supply access", "Designate this space",
                   "Clear designation in this space"]
        if world.ship.captives and release_allowed:
            options.append(f"Release captive ({len(world.ship.captives)} aboard)")
        index = choose(renderer, world, "Current room — geometry follows built walls", options,
            "Doors separate rooms even when open. Labels grant no bonuses. Esc returns.")
        if index is None:
            return
        if index == 0:
            panel(renderer, world, "Room report", report_rows(world, x, y))
            continue
        if index == 3 and world.ship.captives and release_allowed:
            rows = [f"{c['name']} — {c['faction']}" for c in world.ship.captives]
            selected = choose(renderer, world, "Release captive from the brig", rows)
            if selected is not None:
                captive = world.ship.release_captive(world.ship.captives[selected]["id"])
                world.message(f"{captive['name']} released from custody.")
            continue
        purpose = None
        if index == 1:
            choice = choose(renderer, world, "Designate actual connected space", [p.title() for p in PURPOSES])
            if choice is None:
                continue
            purpose = PURPOSES[choice]
        try:
            space = world.spaces.designate(world, x, y, purpose)
            world.message(f"{'Designated ' + purpose if purpose else 'Cleared designation'}: {len(space.cells)} tiles. No time passed.")
        except ValueError as error:
            panel(renderer, world, "Cannot designate room", [str(error)])


def construction_menu(renderer, world):
    """Adjacent-site designation; browsing and ordering never advance time."""
    from pirates.ship_building import queue
    directions = ((0, -1), (1, 0), (0, 1), (-1, 0))
    index = choose(renderer, world, "Construction — select adjacent site",
                   ["North", "East", "South", "West"],
                     "Walls, floors and load-bearing supports. Esc returns; time is paused.")
    if index is None:
        return
    dx, dy = directions[index]
    target = world.player.x + dx, world.player.y + dy
    choice = choose(renderer, world, f"Construction at {target}", [
        "Build oak wall — 1 plank bundle, carpentry work",
        "Build oak door — 1 plank bundle, carpentry work",
        "Dismantle interior wall/door — no material recovery yet",
        "Build connected deck floor — 1 plank bundle",
        "Dismantle interior floor — no material recovery yet",
        "Build oak beam — spans up to four tiles",
        "Build oak pillar — vertical load path",
        "Dismantle beam/pillar — no material recovery yet"],
        "Queues physical work; A manages/cancels jobs. F4 advances time.")
    if choice is None:
        return
    try:
        action = "dismantle" if choice in (2, 4, 7) else "build"
        kind = ("floor" if choice in (3, 4) else "door" if choice == 1 else "wall"
                if choice in (0, 2) else "beam" if choice == 5 else "pillar" if choice == 6
                else next((c.kind for c in world.ship_model.at(
                    world.player.x + dx - 69, world.player.y + dy - 12, world.player.z)
                           if c.kind in ("beam", "pillar")), "beam"))
        queue(world, target, action, kind)
    except ValueError as error:
        world.message(f"Cannot designate construction: {error}")
        panel(renderer, world, "Construction blocked", [str(error)])


def blueprint_menu(renderer, world):
    """Native ship-local cursor editor. Proposals stay nonphysical until queued."""
    import tcod.event
    from pirates.ship_map import ORIGIN, WIDTH, HEIGHT
    from pirates.ship_blueprint import KINDS
    from pirates.ship_building import queue_blueprint

    cursor = [max(0, min(WIDTH - 1, world.player.x - ORIGIN[0])),
              max(0, min(HEIGHT - 1, world.player.y - ORIGIN[1]))]
    modes = [("build", "floor"), ("build", "wall"), ("build", "door"), ("build", "stair"),
             ("build", "beam"), ("build", "pillar"),
             ("dismantle", "floor"), ("dismantle", "wall"), ("dismantle", "door"),
             ("dismantle", "stair"), ("dismantle", "beam"), ("dismantle", "pillar")]
    mode = 0
    selection_start = selection_end = destination = None
    mirror, rotation = False, 0
    draw_anchor = None
    symmetry = False
    def mode_change():
        return modes[mode]

    def present(message=""):
        renderer.render(world)
        cx, cy, ox, oy = renderer._active_camera
        sx, sy = cursor[0] + ORIGIN[0] - cx + ox, cursor[1] + ORIGIN[1] - cy + oy
        if 0 <= sx < renderer.console.width and 0 <= sy < renderer.console.height:
            renderer._draw_lit_cell(sx, sy, "X", fg=(255, 245, 100), bg=(65, 45, 10), sprite=None, brightness=1)
        rows = [f"Cursor: {cursor[0]},{cursor[1]} deck {world.active_deck} | Mode: {modes[mode][0]} {modes[mode][1]}",
                f"Unbuilt changes: {len(world.blueprint.changes)} | selection: {selection_start or '?'} to {selection_end or '?'} | destination: {destination or '?'}",
                f"Copy: {'mirror X' if mirror else 'no mirror'}, rotate {rotation}° | Draw anchor: {draw_anchor or '?'} | symmetry: {'on' if symmetry else 'off'}",
                "Arrows move | B mode | Space point | T anchor | L line | G rectangle | F fill",
                "Y symmetry | [ start | ] end | D destination | C copy",
                "M mirror | R rotate | Z undo | Backspace clear | Enter queue physical jobs | Esc exit"]
        if message:
            rows.append(message)
        renderer.render_list(world, "Ship blueprint — plans are not built until crew haul materials", rows,
                             "F4 advances crew work; Enter queues; Esc exits", selectable=False)
        renderer._present_layers()

    while True:
        present()
        events = tcod.event.wait()
        for event in events:
            if not isinstance(event, tcod.event.KeyDown):
                continue
            sym = event.sym
            if sym == tcod.event.KeySym.ESCAPE:
                return
            if sym in (tcod.event.KeySym.UP, tcod.event.KeySym.K):
                cursor[1] = max(0, cursor[1] - 1)
            elif sym in (tcod.event.KeySym.DOWN, tcod.event.KeySym.J):
                cursor[1] = min(HEIGHT - 1, cursor[1] + 1)
            elif sym in (tcod.event.KeySym.LEFT, tcod.event.KeySym.H):
                cursor[0] = max(0, cursor[0] - 1)
            elif sym in (tcod.event.KeySym.RIGHT, tcod.event.KeySym.L):
                cursor[0] = min(WIDTH - 1, cursor[0] + 1)
            elif sym == tcod.event.KeySym.B:
                mode = (mode + 1) % len(modes)
            elif sym == tcod.event.KeySym.SPACE:
                action, kind = modes[mode]
                key = (cursor[0], cursor[1], world.active_deck, kind)
                existing = next((row for row in world.blueprint.changes if world.blueprint.key(row) == key), None)
                if existing:
                    world.blueprint.discard(existing)
                else:
                    try:
                        world.blueprint._draw_points(world.ship_model, [tuple(cursor)], action, kind,
                                                     symmetry, world.active_deck)
                    except ValueError as error:
                        world.message(f"Blueprint: {error}")
            elif sym == tcod.event.KeySym.T:
                draw_anchor = tuple(cursor)
            elif sym in (tcod.event.KeySym.L, tcod.event.KeySym.G):
                if draw_anchor is None:
                    world.message("Set a draw anchor with T before using line/rectangle.")
                else:
                    action, kind = mode_change()
                    try:
                        if sym == tcod.event.KeySym.L:
                            world.blueprint.draw_line(world.ship_model, draw_anchor, tuple(cursor),
                                                      action, kind, symmetry=symmetry, z=world.active_deck)
                        else:
                            world.blueprint.draw_rectangle(world.ship_model, draw_anchor, tuple(cursor),
                                                            action, kind, symmetry=symmetry, z=world.active_deck)
                    except ValueError as error:
                        world.message(f"Drawing blocked: {error}")
            elif sym == tcod.event.KeySym.F:
                action, kind = mode_change()
                try:
                    world.blueprint.flood_fill(world.ship_model, tuple(cursor), action, kind,
                                               z=world.active_deck)
                except ValueError as error:
                    world.message(f"Flood fill blocked: {error}")
            elif sym == tcod.event.KeySym.Y:
                symmetry = not symmetry
            elif sym == tcod.event.KeySym.LEFTBRACKET:
                selection_start = tuple(cursor)
            elif sym == tcod.event.KeySym.RIGHTBRACKET:
                selection_end = tuple(cursor)
            elif sym == tcod.event.KeySym.D:
                destination = tuple(cursor)
            elif sym == tcod.event.KeySym.M:
                mirror = not mirror
            elif sym == tcod.event.KeySym.R:
                rotation = (rotation + 90) % 360
            elif sym == tcod.event.KeySym.Z:
                world.blueprint.undo()
            elif sym == tcod.event.KeySym.BACKSPACE:
                world.blueprint.clear()
            elif sym == tcod.event.KeySym.C:
                if selection_start is None or selection_end is None or destination is None:
                    world.message("Set [ start, ] end, and D destination before copying.")
                else:
                    try:
                        count = world.blueprint.transform_selection(
                            world.ship_model, *selection_start, *selection_end, destination,
                            mirror_x=mirror, rotate=rotation, z=world.active_deck)
                        world.message(f"Copied {count} structural components into the unbuilt plan.")
                    except ValueError as error:
                        world.message(f"Copy blocked: {error}")
            elif sym in (tcod.event.KeySym.RETURN, tcod.event.KeySym.KP_ENTER):
                try:
                    jobs = queue_blueprint(world)
                    world.message(f"Queued {len(jobs)} physical construction jobs; plans remain until completed.")
                    return
                except ValueError as error:
                    world.message(f"Blueprint queue blocked: {error}")
