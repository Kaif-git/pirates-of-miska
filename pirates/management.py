"""Read-only reports and explicit captain orders; browsing never advances time."""
import time
import tcod.event
from pirates.ship import LABORS, LABELS, TRAIT_EFFECTS


def panel(renderer, world, title, rows):
    renderer.render_list(world, title, rows, "Arrows / wheel scroll; Esc returns", selectable=False)
    renderer.wait_any()


def choose(renderer, world, title, rows, footer="Enter selects; Esc returns"):
    renderer.render_list(world, title, rows, footer)
    return renderer.choose(len(rows))


def profile_rows(sailor, world=None):
    location = world.room_name(sailor.x, sailor.y) if world is not None else "Ship position"
    rows = [sailor.trait, f"Location: {location} ({sailor.x},{sailor.y})",
            f"Now: {sailor.activity}",
            f"Health {sailor.health:.0f}/100   Mood: {sailor.mood_label} ({sailor.morale:.0f}/100)", "",
            "NEEDS / WANTS (higher means more urgent)",
            f"Hunger {sailor.hunger:.0f}   Thirst {sailor.thirst:.0f}   Fatigue {sailor.fatigue:.0f}",
            f"Energy {sailor.energy:.0f}/100   Sleep debt {sailor.sleep_debt:.0f}   Stress {sailor.stress:.0f}",
            "Rest restores energy; only sleep repays sleep debt.",
            f"Company {sailor.loneliness:.0f}   Variety {sailor.boredom:.0f}",
            f"Personal want: { {'craft': 'useful, skilled work', 'social': 'stories and companionship', 'lookout': 'fresh air and a sea view'}[sailor.preference]}",
            f"Own bunk: {sailor.bunk + 1}   Completed chores: {sailor.completed}",
            "Carrying: " + (", ".join(f"{n} {k}" for k, n in sailor.cargo.items()) or "nothing"),
            "", "PERSONALITY / BEHAVIORAL TRAITS",
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
        rows = [f"{c.glyph} {c.name} | {c.mood_label} {c.morale:.0f} | {c.activity}" for c in world.ship.crew]
        rows.append("Work compliance / who is following orders?")
        index = choose(renderer, world, "Crew roster — select a sailor", rows)
        if index is None:
            return
        if index == len(world.ship.crew):
            panel(renderer, world, "Work compliance", work_rows(world))
            continue
        sailor = world.ship.crew[index]
        while True:
            rows = ["Needs, wants, thoughts and skills"]
            rows += [f"{LABELS[k]}: {'OFF' if sailor.labors[k] == 0 else sailor.labors[k]}" for k in LABORS]
            selected = choose(renderer, world, f"{sailor.name} — labor priorities (1 highest, 5 lowest)", rows,
                              "Select a labor to set priority; 0 disables it. Esc returns.")
            if selected is None:
                break
            if selected == 0:
                panel(renderer, world, sailor.name, profile_rows(sailor, world))
            else:
                kind = LABORS[selected - 1]
                value = choose(renderer, world, f"{sailor.name}: {LABELS[kind]}",
                               ["OFF (do not perform)", "1 — Preferred", "2 — High", "3 — Normal", "4 — Low", "5 — Last resort"])
                if value is not None:
                    world.ship.set_labor(sailor.id, kind, value)


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
        return "BLOCKED: missing " + ", ".join(missing)
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
    from pirates.ship_storage import bundle_mass
    from pirates.economy import PORTS
    piles = [f"{p.id} at {ship.storage.position(p.id, world.fixtures)} | {p.contents} | "
             f"{bundle_mass(p.contents)}/{p.capacity_kg} kg" for p in ship.storage.piles.values()]
    return [f"THE WAYWARD — {'open sea' if world.at_sea else 'moored sloop'}", "",
            f"Port: {PORTS[world.economy.port]['name']} | Purse: {world.economy.money} Marks",
            f"Hull {ship.hull:.0f}/100 | Bilge water {ship.bilge:.1f}/100 | Rigging {ship.rigging:.0f}/100",
            f"Deck geometry: {geometry['tiles']} floor tiles | {geometry['perimeter']} exposed edges | {geometry['holes']} holes",
            f"Structure: {mass['structural_mass_kg']} kg | center of mass {mass['center_of_mass']}",
            f"Prepared meals: {ship.meals} | Mean morale: {sum(c.morale for c in ship.crew) / len(ship.crew):.0f}",
            "", "HOLD (available; reservations and carried bundles excluded)",
            *(f"{key.replace('_', ' ').title()}: {value}" for key, value in ship.stores.items()),
            "", "SPATIAL STOCKPILES", *piles,
            "", "CANNONS", *(f"{key.replace('_', ' ')}: {'loaded' if value else 'empty'}" for key, value in ship.ammo.items()),
            "", "STATIONS", *(f"{key.replace('_', ' ')}: {at}" for key, at in ship.stations.items()),
            "", "Systems: finite supplies, hauling, meals, water, assigned bunks,",
            "cleaning, leaks, bailing, repairs, rigging and cannon loading.",
            "Enter: trade cargo / crew kit, merchant conversations and coastal passage.",
            "Sea routes are chart-based rather than ship-handling; tactical combat is not implemented."]


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
    fleets = {ship["node"]: ship for ship in world.npcs.ships}
    rows = ["SEA CHART — strategic map; one chart step is an abstract sailing leg.", ""]
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
        if voyage.get("encounter"):
            rows.append(f"SIGHTING: {voyage['encounter']['name']} ({voyage['encounter']['faction']}) — resume in the ship scene; combat not implemented.")
    for ship in world.npcs.ships:
        rows.append(f"{ship['name']} [{ship['mission']}] at {SEA_NODES[ship['node']]['name']} — {ship['faction']}")
    rows += [f"Freeport captive market: {world.npcs.market['enslaved_people']} enslaved people; simulated NPC trade only."]
    rows += ["", "RECENT RIVAL ACTIVITY", *(world.npcs.events[-8:] or ["No recorded fleet transactions yet."])]
    return rows


def voyage_menu(renderer, world):
    from pirates.voyage import (SEA_NODES, SEA_ROLES, CAPTAIN_ACTIONS, CREW_ORDERS,
                                VoyageSystem)
    labels = {"rest": "Rest between watches", "study_charts": "Study charts (build chart notes)",
              "inspect_ship": "Inspect ship and stores", "talk_with_crew": "Talk with the crew"}
    while True:
        voyage = world.voyages.voyage
        status = voyage["status"] if voyage else "in port"
        options = ["Consult navigator, quartermaster and purser",
                   "Assign navigator / quartermaster / purser",
                   "Choose the captain's free-time action",
                   "Order a crew team: sword drill, brawling or rest",
                   "Plan and order a voyage", "View sea chart and rival ships"]
        if voyage and voyage["status"] == "underway":
            options += ["Watch / advance the voyage"]
        if voyage and voyage["status"] == "encounter":
            options += ["Resolve nearby-ship sighting"]
        choice = choose(renderer, world, f"Voyage command | {status} | {world.clock.format()}", options,
                        "V opens this screen; ship work and needs keep running while time advances.")
        if choice is None:
            return
        if choice == 0:
            panel(renderer, world, "Officer consultation", world.voyages.role_report(world))
        elif choice == 1:
            role_i = choose(renderer, world, "Assign a shipboard role", [f"{role.title()}: {world.ship.sailor(world.voyages.roles[role]).name}" for role in SEA_ROLES])
            if role_i is not None:
                role = SEA_ROLES[role_i]
                candidates = [c for c in world.ship.crew if c.id not in (v for k, v in world.voyages.roles.items() if k != role)]
                who = choose(renderer, world, f"Choose {role}", [f"{c.name} | numbers/diligence {c.personality['diligence']:.0f}, rig {c.skills['rig']:.1f}, repair {c.skills['repair']:.1f}" for c in candidates])
                if who is not None:
                    try:
                        world.voyages.assign_role(world, role, candidates[who].id)
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
            if voyage and voyage["status"] == "underway":
                panel(renderer, world, "Already underway", ["Resolve or continue the current voyage first."])
                continue
            destinations = [key for key in SEA_NODES if key != world.voyages.position]
            selected = choose(renderer, world, "Choose destination; officers will review the plan", [SEA_NODES[k]["name"] for k in destinations])
            if selected is None:
                continue
            target = destinations[selected]
            try:
                plan = world.voyages.plan(world, target)
            except ValueError as error:
                panel(renderer, world, "No route", [str(error)])
                continue
            review = [f"Captain's order: {SEA_NODES[world.voyages.position]['name']} → {SEA_NODES[target]['name']}",
                      "Navigator's route: " + " → ".join(SEA_NODES[n]["name"] for n in plan["route"]),
                      f"Navigator {plan['navigator']}; estimated {plan['duration_hours']} hours; wind: {plan['wind']['name']}.",
                      f"Quartermaster {plan['quartermaster']}: water for {plan['water_days']} days; meals for {plan['meal_days']} days.",
                      f"Purser {plan['purser']}: supplies are finite; ship wages/economy are not simulated.",
                      *world.voyages.role_report(world), "", "Confirm to sail. Daily ship work and needs continue."]
            confirm = choose(renderer, world, "Review and finalize voyage order", ["Confirm voyage order", "Cancel"],
                              f"Estimated {plan['duration_hours']} hours; a nearby ship will interrupt the voyage.")
            if confirm == 0:
                world.voyages.confirm(world, target)
                world.enter_sea()
                panel(renderer, world, "Underway", sea_map_rows(world))
                return
        elif choice == 5:
            panel(renderer, world, "Overworld sea chart", sea_map_rows(world))
        elif voyage and voyage["status"] == "underway" and choice == 6:
            duration = choose(renderer, world, "Advance the simulation; watch the chart and crew", ["10 minutes", "1 hour", "6 hours"],
                              "Menus otherwise freeze time. Movement and passage advance the same ship simulation.")
            if duration is not None:
                seconds = (600, 3600, 21600)[duration]
                world.message("Watching the sea chart; Esc pauses. Crew needs and ship work keep simulating.")
                elapsed = 0
                while elapsed < seconds and world.voyages.voyage["status"] == "underway":
                    # Draw actual route progress in short accelerated watch frames,
                    # rather than teleporting the marker to the interval's end.
                    world.advance(min(600, seconds - elapsed) * 100)
                    elapsed += min(600, seconds - elapsed)
                    renderer.render_list(world, "Sailing — sea chart", sea_map_rows(world),
                                         "Esc pauses watch; V returns to voyage orders")
                    renderer._present_layers()
                    deadline = time.perf_counter() + 0.08
                    while time.perf_counter() < deadline:
                        for event in tcod.event.get():
                            if isinstance(event, tcod.event.Quit):
                                renderer._pending_events.append(event)
                                return
                            if isinstance(event, tcod.event.KeyDown) and event.sym == tcod.event.KeySym.ESCAPE:
                                elapsed = seconds
                                world.message("Sea-chart watch paused. Voyage and ship time stop until your next command.")
                                break
                        time.sleep(0.01)
                if world.voyages.voyage["status"] in ("encounter", "arrived"):
                    panel(renderer, world, "Sea chart / voyage update", sea_map_rows(world))
                if world.voyages.voyage["status"] in ("encounter", "arrived"):
                    return
        elif voyage and voyage["status"] == "encounter":
            sighting = voyage["encounter"]
            result = choose(renderer, world, f"Nearby ship: {sighting['name']}", [
                "Hold course and resume this route", "Turn back to the nearest port"],
                "You are back in the normal ship scene. Tactical encounter / combat is not implemented.")
            if result == 0:
                world.voyages.continue_after_sighting()
                world.message(f"The lookout keeps {sighting['name']} in view; you hold course.")
            elif result == 1:
                world.voyages.turn_back(world)
                world.message("The navigator lays in a return route to the nearest port.")
                return


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
    else:
        from pirates.trading_ui import merchant_menu
        merchant_menu(renderer, world, merchants[index - len(nearby) - 8].id)


def storage_menu(renderer, world):
    storage = world.ship.storage
    while True:
        action = choose(renderer, world, "Spatial storage — location and contents matter", [
            "Inspect stockpiles", "Designate floor stockpile beside captain",
            "Remove empty floor stockpile beside captain", "Order physical transfer"],
            "Reservations and carried goods are separate from available pile totals. Esc returns.")
        if action is None:
            return
        if action == 0:
            from pirates.ship_storage import bundle_mass
            rows = [f"{p.id} at {storage.position(p.id, world.fixtures)} | capacity {p.capacity_kg} kg | "
                    f"contents {p.contents} ({bundle_mass(p.contents)} kg) | "
                    f"available {storage.available(p.id, world.fixtures)}" for p in storage.piles.values()]
            panel(renderer, world, "Stockpile ledger", rows)
        elif action in (1, 2):
            direction = choose(renderer, world, "Select adjacent tile", ["North", "East", "South", "West"])
            if direction is None:
                continue
            dx, dy = ((0, -1), (1, 0), (0, 1), (-1, 0))[direction]
            at = world.player.x + dx, world.player.y + dy
            try:
                if action == 1:
                    pile = storage.designate(world.ship_model, world.ship_fixtures, *at)
                    world.message(f"Designated {pile.id}, capacity {pile.capacity_kg} kg. No time passed.")
                else:
                    pid = f"pile:{at[0] - 69},{at[1] - 12}"
                    storage.und_designate(pid, world.ship.jobs)
                    world.message(f"Removed empty stockpile {pid}. No time passed.")
            except ValueError as error:
                world.message(f"Storage designation blocked: {error}")
                panel(renderer, world, "Stockpile blocked", [str(error)])
        else:
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
            maximum = min(maximum, HAUL_CAPACITY_KG // UNIT_MASS[key])
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
        index = choose(renderer, world, "Current room — geometry follows built walls", [
            "Inspect room / equipment / supply access", "Designate this space", "Clear designation in this space"],
            "Doors separate rooms even when open. Labels grant no bonuses. Esc returns.")
        if index is None:
            return
        if index == 0:
            panel(renderer, world, "Room report", report_rows(world, x, y))
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
                    "Walls/doors/floors. Esc returns; time is paused.")
    if index is None:
        return
    dx, dy = directions[index]
    target = world.player.x + dx, world.player.y + dy
    choice = choose(renderer, world, f"Construction at {target}", [
        "Build oak wall — 1 plank bundle, carpentry work",
        "Build oak door — 1 plank bundle, carpentry work",
        "Dismantle interior wall/door — no material recovery yet",
        "Build connected deck floor — 1 plank bundle",
        "Dismantle interior floor — no material recovery yet"],
        "Queues physical work; A manages/cancels jobs. F4 advances time.")
    if choice is None:
        return
    try:
        action = "dismantle" if choice in (2, 4) else "build"
        kind = "floor" if choice in (3, 4) else "door" if choice == 1 else "wall"
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
    modes = [("build", "floor"), ("build", "wall"), ("build", "door"),
             ("dismantle", "floor"), ("dismantle", "wall"), ("dismantle", "door")]
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
        rows = [f"Cursor: {cursor[0]},{cursor[1]} deck 0 | Mode: {modes[mode][0]} {modes[mode][1]}",
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
                key = (cursor[0], cursor[1], 0, kind)
                existing = next((row for row in world.blueprint.changes if world.blueprint.key(row) == key), None)
                if existing:
                    world.blueprint.discard(existing)
                else:
                    try:
                        world.blueprint._draw_points(world.ship_model, [tuple(cursor)], action, kind, symmetry)
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
                                                      action, kind, symmetry=symmetry)
                        else:
                            world.blueprint.draw_rectangle(world.ship_model, draw_anchor, tuple(cursor),
                                                           action, kind, symmetry=symmetry)
                    except ValueError as error:
                        world.message(f"Drawing blocked: {error}")
            elif sym == tcod.event.KeySym.F:
                action, kind = mode_change()
                try:
                    world.blueprint.flood_fill(world.ship_model, tuple(cursor), action, kind)
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
                            mirror_x=mirror, rotate=rotation)
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
