from pirates.npc_generation import generate_society, make_manifest
from pirates.voyage import NpcFleetSystem


def test_generated_hunt_does_not_mint_legacy_merchant_raid_provisions():
    generated = generate_society(make_manifest("generated-hunt-ledger"))
    fleet = NpcFleetSystem.from_generated(generated)
    hunter = next(ship for ship in fleet.ships if ship["mission"] == "hunt")
    target = next(ship for ship in fleet.ships if ship["id"] != hunter["id"])
    hunter["node"] = target["node"] = "shoals"
    target["threat"] = "merchant"
    crew_before = target["crew"]
    cargo_before = dict(target["cargo"])
    events_before = list(fleet.events)

    fleet._activity(None, hunter)

    assert target["crew"] == crew_before
    assert target["cargo"] == cargo_before
    assert hunter["cargo"] == generated["vessels"][hunter["id"]]["cargo"]
    assert fleet.events == events_before


def test_generated_hull_repair_spends_finite_iron_and_silver_at_a_port(tmp_path):
    from pirates.game import World

    world = World.new_generated("generated-repair-ledger", {"history_days": 0})
    fleet = world.npcs
    ship = next(row for row in fleet.ships if row["mission"] == "hunt")
    ship.update(node="cinder", condition=70, silver=100)
    merchant = world.economy.markets["brann"]
    merchant["stock"]["iron"] = 1
    cost = world.economy.quote("brann", "iron", 1, True)
    stock_before = merchant["stock"]["iron"]
    funds_before = merchant["money"] + ship["silver"]

    fleet._activity(world, ship)

    assert ship["condition"] == 90
    assert merchant["stock"]["iron"] == stock_before - 1
    assert merchant["money"] + ship["silver"] == funds_before
    assert ship["silver"] == 100 - cost
    before = (ship["condition"], ship["silver"], merchant["stock"]["iron"])
    fleet._activity(world, ship)
    assert (ship["condition"], ship["silver"], merchant["stock"]["iron"]) == before

    save_path = tmp_path / "generated-repair.json"
    world.save(save_path)
    restored = World.load(save_path)
    restored_ship = next(row for row in restored.npcs.ships if row["id"] == ship["id"])
    assert restored_ship["condition"] == ship["condition"]
    assert restored_ship["silver"] == ship["silver"]
    assert restored.economy.markets["brann"]["stock"]["iron"] == merchant["stock"]["iron"]
