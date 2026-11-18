"""Save-free native preview of real social evidence and grievance concern UI."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

import play
from PIL import Image
from pirates.management import crew_roster_rows, profile_rows
from pirates.ship import LABORS


def pair_near_mess(world):
    ship = world.ship
    mess = ship.stations["mess"]
    candidates = []
    for dx in range(-2, 3):
        for dy in range(-2, 3):
            if abs(dx) + abs(dy) > 2:
                continue
            position = (mess[0] + dx, mess[1] + dy)
            if not world.game_map.is_walkable(*position) or position == (world.player.x, world.player.y):
                continue
            a = ship.crew[0]
            a.x, a.y = position
            if ship.path(world, a, mess) == []:
                candidates.append(position)
    for a_pos in candidates:
        for b_pos in candidates:
            if a_pos != b_pos and abs(a_pos[0] - b_pos[0]) + abs(a_pos[1] - b_pos[1]) <= 2:
                return a_pos, b_pos
    raise RuntimeError("No nearby walkable mess positions found")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    world = play.World()
    for sailor in world.ship.crew:
        sailor.labors = {key: 0 for key in LABORS}
        sailor.job_id = None
    a, b = world.ship.crew[:2]
    (a.x, a.y), (b.x, b.y) = pair_near_mess(world)
    renderer = play.PiratesRenderer(settings=play.RenderSettings(
        world_font_size=20, world_cell_width=20, world_cell_height=24))
    frames = []
    initial_ticks, initial_seconds = world.clock.total_ticks, world.ship.seconds
    try:
        renderer.init_for(100, 64)

        def capture(name, title, rows):
            renderer.render(world)
            renderer.render_list(world, title, rows, "Save-free crew-events UI preview", selectable=False)
            renderer._present_layers()
            Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(args.output / f"{name}.png")
            frames.append({"name": name, "rows": len(rows), "clock_ticks": world.clock.total_ticks,
                           "ship_seconds": world.ship.seconds})

        # Exercise the normal completion handler three times; only its existing
        # relationship change is applied and logged.
        for _ in range(3):
            a.intent, a.action_seconds = "social", 15
            world.ship.satisfy(world, a, "social")
        capture("01-repeated-companionship", f"{a.name}: shared activity", profile_rows(a, world))

        # A controlled state fixture exercises the actual threshold/lifecycle
        # method for native layout. It is presentation evidence, not a voyage.
        a.grievance, a.morale, a.stress = 59.99, 25, 70
        world.ship._update_grievance(world, a)
        capture("02-open-concern-roster", "Crew roster", crew_roster_rows(world))
        capture("03-open-concern-detail", f"{a.name}: crew concern", profile_rows(a, world))

        a.grievance, a.morale, a.stress = 35.01, 50, 20
        a.hunger = a.thirst = 20
        world.ship._update_grievance(world, a)
        capture("04-resolved-concern", f"{a.name}: concern follow-through", profile_rows(a, world))

        assert len(world.ship.social_events) == 3
        assert world.ship.social_events[-1]["kind"] == "shared_story"
        assert world.ship.crew_concerns[0]["status"] == "resolved"
        assert world.ship.crew_concerns[0]["resolved_second"] is not None
        assert world.clock.total_ticks == initial_ticks and world.ship.seconds == initial_seconds
        report = {"passed": True, "presentation_fixture": True,
                  "note": "Native UI layout only; controlled concern state is not ordinary-gameplay frequency evidence.",
                  "social_events": len(world.ship.social_events),
                  "relationship_score": a.relationships[b.id],
                  "relationship_gains_for_pair": world.ship.relationship_story_gains.get(
                      "::".join(sorted((a.id, b.id))), 0),
                  "concern_status": world.ship.crew_concerns[0]["status"],
                  "clock_unchanged": True, "frames": frames}
        (args.output / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Crew social-event UI preview captured {len(frames)} frames in {args.output}")
    finally:
        renderer.shutdown()


if __name__ == "__main__":
    main()
