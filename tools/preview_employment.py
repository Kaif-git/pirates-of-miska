"""Save-free native capture of the release-one employment loop.

Renders the same rows the port, crew and voyage screens use, through the real
tcod renderer at three window sizes. This is scripted native verification, not
an unaided playtest: it proves text, wrapping and control-flow order, not
comprehension or ordinary frequency.
"""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import play
from PIL import Image
from pirates.management import (crew_commitment_rows, departure_rows, employment_rows,
                                profile_rows, review_offer_rows)
from pirates.trading_ui import candidate_terms_rows, prospect_rows

SIZES = ((1280, 800), (960, 700), (640, 600))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    world = play.World()
    world.player.x, world.player.y, world.player.z = 32, 22, 0
    world.npcs.sighted.update(ship["id"] for ship in world.npcs.ships)
    renderer = play.PiratesRenderer(settings=play.RenderSettings(
        world_font_size=20, world_cell_width=20, world_cell_height=24))
    frames = []
    try:
        renderer.init_for(world.game_map.width, world.game_map.height)
        window = renderer.context.sdl_window
        window.min_size = (640, 600)

        def capture(name, title, rows, scene=None):
            scene = scene or world
            for width, height in SIZES:
                window.size = (width, height)
                renderer.render(scene)
                renderer.render_list(scene, title, rows, "Save-free employment preview",
                                     selectable=False)
                renderer._present_layers()
                path = args.output / f"{width}x{height}-{name}.png"
                Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(path)
            frames.append({"name": name, "title": title, "rows": len(rows),
                           "sizes": [f"{w}x{h}" for w, h in SIZES]})

        capture("01-port-prospects", "Crew prospects — Verdant Cay", prospect_rows(world))
        maelin = next(row for row in world.economy.recruitment.at_port("verdant")
                      if row["id"] == "maelin_orr")
        capture("02-terms-maelin", "Crew prospect", candidate_terms_rows(maelin, world.clock.day))
        capture("03-confirm-hire", "Recruit Maelin Orr?", [
            f"Accept these terms and pay {maelin['asking_price']} Marks",
            "Leave the offer open", "Decline this prospect"])

        money_before = world.economy.money
        sailor = world.economy.recruit_candidate(world, "maelin_orr")
        assert world.economy.money == money_before - maelin["asking_price"]
        capture("04-profile-terms", sailor.name, profile_rows(sailor, world))

        plan = world.voyages.plan(world, "cinder")
        capture("05-voyage-commitments", "Review voyage order — crew commitments",
                crew_commitment_rows(world, plan) + [
                    "Confirm to sail. Daily wages and the undertaking stay active underway."])

        world.voyages.confirm(world, "cinder")
        storm = world.voyages.voyage.get("storm_event")
        if storm:
            storm["trigger_roll"] = storm["chance_percent"]
            storm["triggered"] = False
        world.voyages.voyage["duration_seconds"] = 25
        world.voyages.voyage["initial_estimate_seconds"] = 25
        world.enter_sea()
        world.advance(100 * 600)
        assert world.voyages.voyage["status"] == "arrived"
        assert world.economy.employment.record(sailor.id)["undertaking"]["status"] == "open"
        capture("06-arrival-report-open", "Arrival report — undertaking open",
                world.voyages.arrival_report(world))

        world.at_sea = False
        world.player.x, world.player.y, world.player.z = 32, 22, 0
        world.advance(0)
        record = world.economy.employment.record(sailor.id)
        assert record["undertaking"]["status"] == "satisfied"
        assert record["review"] == {"status": "offered", "day": world.clock.day}
        capture("07-arrival-report-satisfied", "Arrival report — undertaking satisfied",
                world.voyages.arrival_report(world))

        offer = world.economy.employment.review_offer(sailor.id)
        capture("08-review-offer", f"{sailor.name} — employment review",
                review_offer_rows(sailor, offer) + ["", "Accept the review",
                                                    "Keep the current terms", "Decide later"])
        world.economy.employment.accept_review(world, sailor.id)
        record = world.economy.employment.record(sailor.id)
        assert record["review"]["status"] == "accepted" and record["wage_per_day"] == 3
        capture("09-profile-reviewed", f"{sailor.name} — reviewed terms",
                profile_rows(sailor, world))

        capture("10-release-consequences", f"Release {sailor.name} — consequences",
                departure_rows(sailor, world) + ["", "Confirm release at the current port",
                                                 "Keep them aboard"])

        # Second world: the negative and underfunded paths the plan's native
        # walkthrough names, driven through the same authorities.
        short = play.World()
        short.player.x, short.player.y, short.player.z = 32, 22, 0
        short.economy.money = 0
        try:
            short.economy.recruit_candidate(short, "maelin_orr")
        except ValueError as error:
            refusal = str(error)
        else:
            raise AssertionError("A hire with no captain funds must fail")
        assert short.economy.money == 0
        assert short.economy.employment.record("hire:maelin_orr") is None
        assert short.economy.recruitment.statuses["maelin_orr"] == "available"
        capture("11-failed-hire", "Cannot recruit", [
            refusal, "No funds, pool slot, berth, roster row or agreement changed."],
            scene=short)

        short.economy.money = 300
        short_sailor = short.economy.recruit_candidate(short, "maelin_orr")
        short_second = short.economy.recruit_candidate(short, "pippin_ash")
        short.economy.money = 1
        short.economy.employment.settle_day(short, 2)
        short_record = short.economy.employment.record(short_sailor.id)
        assert (short_record["paid_total"], short_record["arrears"]) == (1, 1)
        assert short.economy.employment.record(short_second.id)["arrears"] == 1
        notices = [row for row in short.economy.history if "unpaid wages" in row]
        assert len(notices) == 2
        capture("12-payroll-shortfall", "Underfunded payday — employment ledger",
                ["EMPLOYMENT TERMS", *employment_rows(short_sailor, short), "",
                 f"Captain funds after the day settled: {short.economy.money} Mark.",
                 "Only one Mark was available for three Marks of earned wages."],
                scene=short)
        capture("13-wages-notice", "Unpaid wages recorded", notices + [
            "", f"Settle {short_sailor.name}'s outstanding wages "
                f"({short_record['arrears']} Mark)",
            f"Settle {short_second.name}'s outstanding wages "
            f"({short.economy.employment.record(short_second.id)['arrears']} Mark)"],
            scene=short)

        result = {"passed": True,
                  "sizes": [f"{w}x{h}" for w, h in SIZES],
                  "frames": frames,
                  "frames_per_size": len(frames),
                  "screenshots": len(frames) * len(SIZES),
                  "record": {key: record[key] for key in
                             ("role", "wage_per_day", "signing_payment", "paid_total",
                              "arrears", "review")},
                  "undertaking": record["undertaking"]["status"],
                  "failed_hire": refusal,
                  "shortfall": {"paid": short_record["paid_total"],
                                "arrears": short_record["arrears"],
                                "second_arrears": short.economy.employment.record(
                                    short_second.id)["arrears"],
                                "notices": notices},
                  "money": world.economy.money,
                  "crew_count": len(world.ship.crew),
                  "note": ("Scripted native render of real menu rows; not an unaided "
                           "playtest and not balance evidence.")}
        (args.output / "verification.json").write_text(json.dumps(result, indent=2),
                                                       encoding="utf-8")
        print(f"Native employment walkthrough passed: {len(frames)} screens "
              f"x {len(SIZES)} sizes = {len(frames) * len(SIZES)} screenshots.")
    finally:
        renderer.shutdown()


if __name__ == "__main__":
    main()
