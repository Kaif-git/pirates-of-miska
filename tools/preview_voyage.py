"""Save-free native voyage/sea-chart walkthrough."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

import play
from PIL import Image
from pirates.management import sea_map_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    world = play.World()
    geometry = world.ship_model.snapshot()
    for sailor in world.ship.crew:
        sailor.labors = {kind: 0 for kind in sailor.labors}
        sailor.hunger = sailor.thirst = sailor.fatigue = sailor.loneliness = sailor.boredom = 0
        sailor.sleep_debt = 20
    world.npcs.sighted.update(s["id"] for s in world.npcs.ships)
    renderer = play.PiratesRenderer(settings=play.RenderSettings(
        world_font_size=20, world_cell_width=20, world_cell_height=24))
    report = {"frames": [], "start_port": world.economy.port}
    try:
        renderer.init_for(100, 64)
        def capture(name, title=None, rows=None):
            renderer.render(world)
            if title:
                renderer.render_list(world, title, rows, "Save-free sea-voyage walkthrough", selectable=False)
            renderer._present_layers()
            Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(args.output / f"{name}.png")
            report["frames"].append({"name": name, "status": world.voyages.voyage["status"] if world.voyages.voyage else "moored",
                                     "location": world.voyages.position, "ship_seconds": world.ship.seconds,
                                     "ship_order_invariants_clear": not bool(world.ship.check_orders())})

        plan = world.voyages.confirm(world, "cinder")
        world.voyages.voyage["duration_seconds"] = 3600
        world.voyages.set_captain_action("study_charts")
        world.voyages.set_crew_order(world, "brawling_drill", "tavi", ["tavi", "ivo", "nell"])
        world.enter_sea()
        capture("01-underway", "The Wayward underway", [
            f"Route: {' → '.join(plan['route'])}", f"Navigator: {plan['navigator']} | Quartermaster: {plan['quartermaster']}",
            "Daily chores and needs continue off-screen.", "Captain studies charts; Tavi leads a brawling team."])
        capture("02-sea-chart", "Overworld sea chart", sea_map_rows(world))
        world.advance(900 * 100)
        capture("03-watching-the-route", "After fifteen minutes at sea", sea_map_rows(world))
        world.advance((3600 - 900) * 100)
        capture("04-arrival", "Arrival at Cinder Quay", sea_map_rows(world))
        assert world.voyages.voyage["status"] == "arrived"
        assert not world.at_sea and world.economy.port == "cinder"
        assert world.ship.seconds >= 3600
        assert not world.ship.check_orders()
        assert world.ship_model.snapshot() == geometry
        report.update({"passed": True, "route": plan["route"], "geometry_unchanged": world.ship_model.snapshot() == geometry,
                       "officer_roles": world.voyages.roles, "captain_chart_notes": world.voyages.voyage.get("chart_notes", 0),
                       "npc_event_sample": world.npcs.events[-8:]})
        (args.output / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Native sea-voyage walkthrough passed: {len(report['frames'])} screenshots; no save touched.")
    finally:
        renderer.shutdown()


if __name__ == "__main__":
    main()
