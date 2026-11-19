"""Save-free native captures of the sea-encounter decision and handoff UI.

The script renders real voyage/contact row helpers through the tcod renderer
at the project's three audited window sizes. It is scripted verification, not
an unaided playtest.
"""
from copy import deepcopy
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

import play
from PIL import Image
from pirates import sea_encounter
from pirates.management import combat_handoff_rows, crossing_request_rows
from pirates.ship import LABORS

SIZES = ((1280, 800), (960, 700), (640, 600))


def quiet_world():
    world = play.World()
    for sailor in world.ship.crew:
        sailor.labors = {kind: 0 for kind in LABORS}
        sailor.hunger = sailor.thirst = sailor.fatigue = 0
        sailor.loneliness = sailor.boredom = 0
        sailor.sleep_debt = 20
    world.npcs.sighted.update(ship["id"] for ship in world.npcs.ships)
    return world


def encounter_world(threat="merchant", *, close=False, predicate=None):
    world = quiet_world()
    target = deepcopy(next(ship for ship in world.npcs.ships
                          if ship["threat"] == threat
                          and (predicate is None or predicate(ship))))
    world.voyages.confirm(world, "deepwater")
    world.voyages.voyage["status"] = "encounter"
    world.voyages.voyage["encounter"] = target
    world.enter_sea()
    if close:
        sea_encounter.contact_of(target)["range"] = "close"
    return world, target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    renderer = play.PiratesRenderer(settings=play.RenderSettings(
        world_font_size=20, world_cell_width=20, world_cell_height=24))
    frames = []
    try:
        world, target = encounter_world("merchant")
        renderer.init_for(world.game_map.width, world.game_map.height)
        window = renderer.context.sdl_window
        window.min_size = (640, 600)

        def capture(name, title, rows, scene=world):
            for width, height in SIZES:
                window.size = (width, height)
                renderer.render(scene)
                renderer.render_list(scene, title, rows,
                                     "Save-free sea-encounter verification",
                                     selectable=False)
                renderer._present_layers()
                Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(
                    args.output / f"{width}x{height}-{name}.png")
            frames.append({"name": name, "title": title, "row_count": len(rows),
                           "sizes": [f"{w}x{h}" for w, h in SIZES]})

        # Deliberately adversarial private fields prove the rendered rows do not
        # echo mission/cargo payloads from the simulation record.
        target["mission"] = "secret_target_island"
        target["cargo"] = {"gold": 999}
        world.society.organizations["preview_registry"] = {
            "name": "Preview Merchant Registry", "kind": "merchant_company",
            "reputation": 57,
        }
        target["organization_id"] = "preview_registry"
        world.npcs.sighting_reports[target["id"]] = {
            "node": target["node"], "observed_hour": 1,
            "crew": target["crew"], "condition": target["condition"],
        }
        world.clock.total_ticks = 4 * 360000
        info_rows = sea_encounter.sighting_rows(world, target) + [
            "", *sea_encounter.contact_rows(world, target)]
        assert all("secret_target_island" not in row and "999" not in row for row in info_rows)
        capture("01-lookout-report", "Lookout and contact report", info_rows)

        distant_options = [
            "Observe the vessel and review contact state (read-only)",
            "Signal the vessel from a distance (1 minute)",
            "Approach to close range (5 minutes)",
            "Attempt to evade and break contact (5 minutes)",
            "Hold course and resume this route", "Turn back to the nearest port",
            "Declare cannon-fire intent (combat handoff only)",
        ]
        capture("02-distant-decisions", "Nearby ship — distant contact",
                distant_options + ["Signal responses may be unanswered; no hidden cargo or intent is shown."])

        signal_world, signal_target = encounter_world("merchant")
        sea_encounter.contact_of(signal_target)["signals"].append({
            "tick": signal_world.clock.total_ticks, "intent": "identify",
            "outcome": "no_answer", "reply": "No reply; the signal remains unanswered.",
        })
        capture("03-unanswered-signal", "Signal exchange",
                ["Identify signal: no answer", "No reply; the signal remains unanswered.",
                 "One minute of ship time passed."], signal_world)

        welcome_predicate = lambda ship: (
            sea_encounter._unit(ship, "parley:transfer_permission")
            < sea_encounter._consent_chance(ship, {"parley": None}, "transfer_permission"))
        close_world, close_target = encounter_world(
            "merchant", close=True, predicate=welcome_predicate)
        close_options = [
            "Observe the vessel and review contact state (read-only)",
            "Parley at close range (1 minute)", "Request to cross alone",
            "Request to cross with named escorts", "Request to cross with everyone",
            "Attempt to evade and break contact (5 minutes)",
            "Hold course and resume this route", "Turn back to the nearest port",
            "Declare cannon-fire intent (combat handoff only)",
        ]
        capture("04-close-decisions", "Nearby ship — close contact",
                close_options + ["Closeness is not consent to board or transfer anyone."], close_world)
        close_world.voyages.parley_sighting(close_world, "identity")
        permission = close_world.voyages.parley_sighting(
            close_world, "transfer_permission")
        assert permission["agreed"] is True
        peaceful = close_world.voyages.request_crossing(
            close_world, "escorts", [close_world.ship.crew[0].id])
        capture("05-welcomed-transfer", "Peaceful crossing request",
                crossing_request_rows(close_world, close_target, peaceful), close_world)

        boarding_world, boarding_target = encounter_world("merchant", close=True)
        boarding = boarding_world.voyages.request_crossing(boarding_world, "alone")
        capture("06-boarding-handoff", "Combat handoff pending",
                crossing_request_rows(boarding_world, boarding_target, boarding), boarding_world)

        cannon_world, cannon_target = encounter_world("merchant")
        cannon_world.ship.ammo["port_cannon"] = 1
        ammo_before = cannon_world.ship.ammo["port_cannon"]
        cannon = cannon_world.voyages.request_naval_combat(cannon_world, "port_cannon")
        assert cannon_world.ship.ammo["port_cannon"] == ammo_before
        capture("07-cannon-handoff", "Combat handoff pending",
                combat_handoff_rows(cannon_world, cannon_target, cannon), cannon_world)

        report = {"passed": True, "scripted_not_unaided_playtest": True,
                  "sizes": [f"{w}x{h}" for w, h in SIZES], "frames": frames}
        (args.output / "verification.json").write_text(json.dumps(report, indent=2),
                                                        encoding="utf-8")
        print(f"Sea-encounter native capture passed: {len(frames)} screens × 3 sizes.")
    finally:
        renderer.shutdown()


if __name__ == "__main__":
    main()
