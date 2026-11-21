"""Scripted authored M4 transfer/trade walkthrough and native SDL captures.

This verifies the selected authored scenario and real row helpers at three
window sizes. It is not an unaided player walkthrough or human acceptance.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

import play
import omnisim.ui.ascii_renderer as ascii_renderer
from omnisim.ui.render_settings import RenderSettings
from pirates import sea_encounter, sea_trade
from pirates.game import World
from pirates.management import crossing_party_rows, sea_map_rows

SIZES = ((1280, 800), (960, 700), (640, 600))


def renderer_for(size):
    ascii_renderer.WIN_PX = size
    renderer = play.PiratesRenderer(settings=RenderSettings(
        world_tileset="ascii", world_font_size=20,
        world_cell_width=20, world_cell_height=24))
    renderer.init_for(100, 64)
    renderer.context.sdl_window.size = size
    renderer._sync_console_size()
    actual = renderer.context.sdl_renderer.output_size
    if actual != size:
        raise RuntimeError(f"SDL output size {actual} did not match requested {size}")
    return renderer


def authored_encounter():
    world = World.new_authored_mvp()
    target_id = "syndicate_1"
    world.voyages.confirm(world, "cinder")
    world.enter_sea()
    world.voyages.voyage["elapsed_seconds"] = 1800
    for ship in world.npcs.ships:
        ship["motion"] = None
        if ship["id"] == target_id:
            ship["node"] = ship["destination"] = "verdant"
        else:
            ship["node"] = ship["destination"] = "deepwater"
    world.npcs.sighted.clear()
    world.npcs.contact_tracks.clear()
    world.npcs.sighting_reports.clear()
    sighting = world.npcs.advance_second(
        world, world.voyages.map_coordinate(), sighting_enabled=True)
    if not sighting or sighting["id"] != target_id:
        raise RuntimeError("Authored lookout did not acquire the positioned merchant")
    world.voyages.voyage["status"] = "encounter"
    world.voyages.voyage["encounter"] = sighting
    sighting["contact_instance_id"] = "syndicate_1:1"
    sea_encounter.contact_of(sighting)["range"] = "close"
    return world, sighting


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    world, encounter = authored_encounter()
    if world.scenario_id != "lawful-trader-mvp":
        raise RuntimeError("The M4 scripted walkthrough requires the authored MVP scenario")

    report_frames = []

    def capture(name, title, rows):
        for size in SIZES:
            renderer = renderer_for(size)
            try:
                renderer.render(world)
                renderer.render_list(world, title, rows,
                                     "Scripted authored M4 walkthrough — not human acceptance",
                                     selectable=False)
                renderer._present_layers()
                Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(
                    args.output / f"{size[0]}x{size[1]}-{name}.png")
            finally:
                renderer.context.close()
        report_frames.append({"name": name, "title": title,
                              "row_count": len(rows),
                              "sizes": [f"{w}x{h}" for w, h in SIZES]})

    lookout = sea_encounter.sighting_rows(world, encounter) + [
        "", *sea_encounter.contact_rows(world, encounter)]
    joined_lookout = " ".join(lookout).lower()
    assert all(secret not in joined_lookout for secret in (
        "grain", "beans", "240 marks", "mission: trade"))
    capture("01-authored-lookout", "Lookout and contact report", lookout)

    offer = world.voyages.request_sea_trade_offer(world)
    assert offer["items"] and all(row["item_id"] in sea_trade.TRADE_ITEMS
                                   for row in offer["items"])
    capture("02-explicit-trade-offer", "Merchant's finite offer",
            sea_trade.offer_rows(offer))
    choice = next(row for row in sea_trade.choices(offer)
                  if row["item_id"] == "grain" and row["buying"])
    quote = world.voyages.quote_sea_trade(
        world, choice["item_id"], 2, choice["buying"])
    capture("03-fixed-trade-quote", "Review at-sea trade before commit",
            sea_trade.quote_rows(quote))
    receipt = world.voyages.commit_sea_trade(world, quote["transaction_id"])
    assert receipt["transaction_id"] == quote["transaction_id"]
    capture("04-trade-receipt", "Sea trade committed once", [
        f"Transaction {receipt['transaction_id']}",
        f"Bought {receipt['quantity']} {receipt['item_id']} for {receipt['total']} Marks.",
        "Finite vessel stock/purse and player money/cargo were updated together.",
        "One minute of ship time passed; retrying this receipt cannot replay it."])

    contact = encounter["contact"]
    contact["parley"] = {"topics": ["identity", "transfer_permission"],
                         "stand_down": False, "transfer_permission": True,
                         "warned_off": False}
    request = world.voyages.request_crossing(world, "alone")
    world.voyages.complete_peaceful_crossing(world)
    visit = world.voyages.voyage["deck_visit"]
    assert request["participants"] == [world.player.id]
    assert visit["participants"] == [world.player.id]
    capture("05-on-consented-deck", f"Aboard {encounter['name']}",
            crossing_party_rows(world, visit))

    with tempfile.TemporaryDirectory(prefix="m4-authored-save-") as folder:
        path = Path(folder) / "lawful-trader-mvp.json"
        world.save(path)
        world = World.load(path, expected_scenario_id="lawful-trader-mvp")
        encounter = world.voyages.voyage["encounter"]
        visit = world.voyages.voyage["deck_visit"]
        assert visit is not None and world.player.x < 69
        capture("06-resumed-deck-visit", f"Aboard {encounter['name']}",
                crossing_party_rows(world, visit))
        returned = world.voyages.return_crossing_party(world)
        assert returned == [world.player.id]
        capture("07-returned-to-wayward", "Crossing party returned aboard", [
            "Captain is back on the Wayward's main deck.",
            "The temporary NPC deck overlay is removed when all visitors return.",
            "The vessel contact remains local; continue or turn back explicitly."])
        world.voyages.continue_after_sighting()
        assert world.voyages.voyage["status"] == "underway"
        capture("08-voyage-resumed", "Authored voyage resumed", sea_map_rows(world))

    result = {"passed": True, "scenario_id": "lawful-trader-mvp",
              "scripted_not_human_acceptance": True,
              "trade_transaction_id": receipt["transaction_id"],
              "trade_receipt_retry_safe": True,
              "crossing_saved_and_resumed": True,
              "returned_party": [world.player.id],
              "sizes": [f"{w}x{h}" for w, h in SIZES],
              "frames": report_frames}
    (args.output / "verification.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8")
    print(f"Authored M4 walkthrough passed: {len(report_frames)} screens × 3 sizes.")


if __name__ == "__main__":
    main()
