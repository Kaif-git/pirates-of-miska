"""Save-free native walkthrough of a named NPC clash becoming sea news."""
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
    for sailor in world.ship.crew:
        sailor.labors = {kind: 0 for kind in sailor.labors}
        sailor.hunger = sailor.thirst = sailor.fatigue = sailor.loneliness = sailor.boredom = 0
        sailor.sleep_debt = 20
    world.npcs.sighted.update(ship["id"] for ship in world.npcs.ships)
    pirate = next(ship for ship in world.npcs.ships if ship["id"] == "raider_1")
    marine = next(ship for ship in world.npcs.ships if ship["id"] == "government_1")
    pirate["node"] = pirate["destination"] = "navy_lane"
    marine["node"] = marine["destination"] = "navy_lane"
    renderer = play.PiratesRenderer(settings=play.RenderSettings(
        world_font_size=20, world_cell_width=20, world_cell_height=24))
    report = {"frames": [], "starting_headcount": 40}
    try:
        renderer.init_for(100, 64)

        def capture(name, title, rows):
            renderer.render(world)
            renderer.render_list(world, title, rows, "Save-free NPC society walkthrough", selectable=False)
            renderer._present_layers()
            Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(args.output / f"{name}.png")
            report["frames"].append(name)

        cast = []
        for org_id, organization in world.society.organizations.items():
            leader = world.society.people[organization["leader"]]
            cast.append(f"{organization['name']} — led by {leader['title']} {leader['name']}")
            cast.append(f"  Ambition: {organization['ambition']} | target: {organization['target']}")
        capture("01-the-sea-cast", "The people behind the ships", cast)
        capture("02-before-the-clash", "Patrol and pirate converge", sea_map_rows(world))

        world.advance(3600 * 100)
        clash = next(item for item in reversed(world.society.news) if item["kind"] == "npc_clash")
        capture("03-the-news", "News from the Miska sea lanes", [
            clash["headline"], clash["body"], f"Cause: {clash['cause']}", f"Outcome: {clash['outcome']}",
            "", *world.society.recent_headlines(8)])
        capture("04-world-state", "Ships, captains and current activity", sea_map_rows(world))

        headcount = (world.npcs.market["enslaved_people"]
                     + sum(ship["crew"] + ship["captives"] for ship in world.npcs.ships))
        assert headcount == report["starting_headcount"]
        assert world.society.hour == world.npcs.hour == world.clock.total_ticks // 360000
        assert clash["participants"] and clash["location"] == "navy_lane"
        assert not world.ship.check_orders()
        report.update({"passed": True, "headcount": headcount, "society_hour": world.society.hour,
                       "clash": clash, "relationships": world.society.relationships,
                       "ships": [{"id": ship["id"], "node": ship["node"], "condition": ship["condition"]}
                                 for ship in world.npcs.ships]})
        (args.output / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Native NPC society walkthrough passed: {len(report['frames'])} screenshots; no save touched.")
    finally:
        renderer.shutdown()


if __name__ == "__main__":
    main()
