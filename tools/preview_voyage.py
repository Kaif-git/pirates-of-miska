"""Save-free native voyage/sea-chart walkthrough."""
from pathlib import Path
import argparse
from copy import deepcopy
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

import play
from PIL import Image
from pirates.management import sea_map_rows
from pirates.ship import LABORS
from pirates.voyage import SEA_NODES


def quiet_world():
    world = play.World()
    for sailor in world.ship.crew:
        sailor.labors = {kind: 0 for kind in LABORS}
        sailor.hunger = sailor.thirst = sailor.fatigue = sailor.loneliness = sailor.boredom = 0
        sailor.sleep_debt = 20
    world.npcs.sighted.update(ship["id"] for ship in world.npcs.ships)
    return world


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    world = quiet_world()
    geometry = world.ship_model.snapshot()
    renderer = play.PiratesRenderer(settings=play.RenderSettings(
        world_font_size=20, world_cell_width=20, world_cell_height=24))
    report = {"frames": [], "start_port": world.economy.port}
    try:
        renderer.init_for(100, 64)
        def capture(name, title=None, rows=None, shown_world=None):
            shown_world = shown_world or world
            renderer.render(shown_world)
            if title:
                renderer.render_list(shown_world, title, rows, "Save-free sea-voyage walkthrough", selectable=False)
            renderer._present_layers()
            Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(args.output / f"{name}.png")
            report["frames"].append({"name": name,
                                      "status": shown_world.voyages.voyage["status"] if shown_world.voyages.voyage else "moored",
                                      "location": shown_world.voyages.position, "ship_seconds": shown_world.ship.seconds,
                                      "ship_order_invariants_clear": not bool(shown_world.ship.check_orders())})

        route_options = world.voyages.route_options(world.voyages.position, "cinder")
        route_rows = [f"{' → '.join(SEA_NODES[node]['name'] for node in option['route'])} | "
                      f"{option['distance']:.2f} chart units | no prior lookout report"
                      for option in route_options]
        capture("00-route-choice", "Choose a chart route", route_rows + [
            "Equal-distance routes use different chart nodes and recorded sightings.",
            "Reports can be stale; no report is not evidence of safety."])
        plan = world.voyages.plan(world, "cinder")
        capture("00-departure-review", "Review voyage order", [
            "Captain's order: Verdant Reach → Cinder Quay",
            "Navigator's route: " + " → ".join(SEA_NODES[node]["name"] for node in plan["route"]),
            f"Estimated {plan['duration_hours']} hours; wind: {plan['wind']['name']}.",
            f"Quartermaster: {plan['water_liters']} L clean water, {plan['food_kg']} kg food.",
            "Confirm to sail. Daily ship work and needs continue."])
        plan = world.voyages.confirm(world, "cinder")
        # Keep this walkthrough focused on ordinary arrival; dedicated tests
        # exercise the persisted squall decision and both responses.
        storm = world.voyages.voyage.get("storm_event")
        if storm:
            storm["trigger_roll"] = storm["chance_percent"]
            storm["triggered"] = False
        # Sailing forces scale progress below the nominal duration. A 2,400-second
        # synthetic route arrives within the 3,600 seconds advanced below.
        world.voyages.voyage["duration_seconds"] = 2400
        world.voyages.voyage["initial_estimate_seconds"] = 2400
        world.voyages.assign_role(world, "captain", "tavi")
        world.voyages.set_captain_action("study_charts")
        world.voyages.set_crew_order(world, "brawling_drill", "tavi", ["tavi", "ivo", "nell"])
        world.enter_sea()
        capture("00-advance-choice", "Advance the simulation", [
            "10 minutes", "1 hour", "6 hours", "Until the next day boundary",
            "Until the next actionable event", "Until arrival",
            "All modes use canonical simulation; ship sightings and arrival interrupt."])
        capture("01-underway", "The Wayward underway", [
            f"Route: {' → '.join(plan['route'])}", f"Navigator: {plan['navigator']} | Quartermaster: {plan['quartermaster']}",
            f"Appointed crew captain: {world.ship.sailor(world.voyages.roles['captain']).name}",
            "Daily chores and needs continue off-screen.", "Captain studies charts; Tavi leads a brawling team."])
        capture("02-sea-chart", "Overworld sea chart", sea_map_rows(world))
        world.advance(900 * 100)
        capture("03-watching-the-route", "After fifteen minutes at sea", sea_map_rows(world))
        world.advance((3600 - 900) * 100)
        capture("04-arrival", "Arrival at Cinder Quay", sea_map_rows(world))
        capture("05-arrival-report", "Voyage arrival report", world.voyages.arrival_report(world))
        assert world.voyages.voyage["status"] == "arrived"
        assert not world.at_sea and world.economy.port == "cinder"
        assert world.ship.seconds > 900  # arrival interrupts the remaining fast-forward
        assert not world.ship.check_orders()
        assert world.ship_model.snapshot() == geometry

        # Independent controlled fixtures preview contact, aid and storm panels
        # without changing the ordinary underway-to-arrival walkthrough.
        contact_world = quiet_world()
        target = next(ship for ship in contact_world.npcs.ships if ship["id"] == "government_1")
        target["condition"] = 40
        contact_world.voyages.confirm(contact_world, "deepwater")
        contact_world.voyages.voyage["status"] = "encounter"
        contact_world.voyages.voyage["encounter"] = deepcopy(target)
        contact_world.enter_sea()
        capture("06-sighting-choice", f"Nearby ship: {target['name']}", [
            "Begin a ship-to-ship cannon exchange", "Hold course and resume this route",
            "Turn back to the nearest port", "Hail and exchange signals (1 minute)",
            "Public lookout information only. Aid costs one uncommitted iron; pirates remain hostile."],
            contact_world)
        contact_start_ticks = contact_world.clock.total_ticks
        contact_start_seconds = contact_world.ship.seconds
        contact_world.npcs.hail_ship(contact_world, target["id"])
        hail_ticks = contact_world.clock.total_ticks - contact_start_ticks
        hail_ship_seconds = contact_world.ship.seconds - contact_start_seconds
        blocked_start_ticks = contact_world.clock.total_ticks
        blocked_start_condition = target["condition"]
        try:
            contact_world.npcs.assist_ship(contact_world, target["id"])
        except ValueError as error:
            blocked_rows = [str(error), "No cargo, condition or time changes on rejection."]
        else:
            raise AssertionError("the preview's distant hold position should block repair aid")
        assert contact_world.clock.total_ticks == blocked_start_ticks
        assert target["condition"] == blocked_start_condition
        capture("07-blocked-aid", "Assistance unavailable", blocked_rows, contact_world)
        hold_x, hold_y = contact_world.ship.stations["hold"]
        contact_world.player.x, contact_world.player.y, contact_world.player.z = hold_x - 1, hold_y, 0
        contact_world.economy.cargo["iron"] = 1
        capture("08-aid-choice", "Offer repair assistance", [
            "One iron cargo unit; up to +20 observed vessel condition.",
            "Requires hold access and a visibly distressed non-hostile contact.",
            "One minute passes through canonical simulation; no favor is promised."], contact_world)
        aid_start_ticks = contact_world.clock.total_ticks
        aid_start_seconds = contact_world.ship.seconds
        repaired = contact_world.npcs.assist_ship(contact_world, target["id"])
        aid_ticks = contact_world.clock.total_ticks - aid_start_ticks
        aid_ship_seconds = contact_world.ship.seconds - aid_start_seconds
        assert aid_ticks == 6000
        assert aid_ship_seconds == 60
        assert contact_world.economy.cargo.get("iron", 0) == 0
        assert repaired == 20 and target["condition"] == 60
        capture("09-aid-outcome", "Repair aid delivered", sea_map_rows(contact_world), contact_world)

        storm_world = quiet_world()
        storm_world.voyages.confirm(storm_world, "cinder", "shoals")
        storm_world.enter_sea()
        storm_voyage = storm_world.voyages.voyage
        storm_voyage["leg"] = 1
        storm_voyage["leg_progress"] = 0.0
        storm_world.voyages.position = storm_voyage["storm_event"]["node"]
        storm_voyage["storm_event"].update({"triggered": True, "trigger_roll": 8})
        storm_world.voyages.step_second(storm_world)
        storm = storm_voyage["storm_event"]
        current_route = storm_voyage["route"][storm_voyage["leg"]:]
        current_distance = sum(
            ((SEA_NODES[b]["xy"][0] - SEA_NODES[a]["xy"][0]) ** 2
             + (SEA_NODES[b]["xy"][1] - SEA_NODES[a]["xy"][1]) ** 2) ** 0.5
            for a, b in zip(current_route, current_route[1:]))
        prior_nodes = set(storm_voyage["route"][:storm_voyage["leg"]])
        detours = [row for row in storm_world.voyages.route_options(
            storm_world.voyages.position, storm_voyage["destination"], include_detours=True)
            if row["route"][:2] != storm["storm_lane"]
            and not any(node in prior_nodes for node in row["route"][1:])]
        completed_route = storm_voyage["route"][:storm_voyage["leg"] + 1]
        completed_distance = sum(
            ((SEA_NODES[b]["xy"][0] - SEA_NODES[a]["xy"][0]) ** 2
             + (SEA_NODES[b]["xy"][1] - SEA_NODES[a]["xy"][1]) ** 2) ** 0.5
            for a, b in zip(completed_route, completed_route[1:]))
        storm_rows = []
        for row in detours:
            revised_duration = max(storm_voyage["elapsed_seconds"] + 1,
                                   round(storm_voyage["duration_seconds"]
                                         * (completed_distance + row["distance"])
                                         / max(0.01, storm_voyage["distance"])))
            extra_hours = max(0, revised_duration - storm_voyage["duration_seconds"]) / 3600
            storm_rows.append(
                f"Avoid via {' → '.join(SEA_NODES[node]['name'] for node in row['route'])} | "
                f"{row['distance']:.2f} chart units ({row['distance'] - current_distance:+.2f}); "
                f"ETA +{extra_hours:.1f}h est.")
        storm_rows += ["Press on through the moderate squall (hull -5, rigging -8).",
                       "Low-confidence watch only; a storm is not guaranteed."]
        capture("10-squall-choice", "Squall response decision", storm_rows, storm_world)
        storm_hull, storm_rigging = storm_world.ship.hull, storm_world.ship.rigging
        storm_world.voyages.resolve_storm(storm_world, "press_on")
        assert (storm_world.ship.hull, storm_world.ship.rigging) == (storm_hull - 5, storm_rigging - 8)
        capture("11-squall-outcome", "Pressed on through squall", sea_map_rows(storm_world), storm_world)

        report.update({"passed": True, "route": plan["route"], "geometry_unchanged": world.ship_model.snapshot() == geometry,
                       "officer_roles": world.voyages.roles, "captain_chart_notes": world.voyages.voyage.get("chart_notes", 0),
                       "npc_event_sample": world.npcs.events[-8:],
                       "route_options": [{"route": row["route"], "distance": row["distance"]}
                                         for row in route_options],
                       "passage": {"actual_seconds": world.voyages.voyage["elapsed_seconds"],
                                   "estimate_seconds": world.voyages.voyage["initial_estimate_seconds"],
                                   "ship_seconds": world.ship.seconds},
                       "decision_scenarios": {
                           "contact_id": target["id"], "hail_ticks": hail_ticks,
                           "hail_ship_seconds": hail_ship_seconds,
                           "blocked_action": "hold access rejected before time or condition mutation",
                           "aid_repair": repaired, "aid_ticks": aid_ticks,
                           "aid_ship_seconds": aid_ship_seconds,
                           "aid_iron_remaining": contact_world.economy.cargo.get("iron", 0),
                           "squall_hull_damage": storm_hull - storm_world.ship.hull,
                           "squall_rigging_damage": storm_rigging - storm_world.ship.rigging}})
        (args.output / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Native sea-voyage walkthrough passed: {len(report['frames'])} screenshots; no save touched.")
    finally:
        renderer.shutdown()


if __name__ == "__main__":
    main()
