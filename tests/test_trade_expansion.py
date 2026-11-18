import json

import pytest

from pirates.economy import CATALOG, CONTRACT_TEMPLATES, MERCHANTS, PORTS, PASSAGE_FEE, RULES, build_port
from pirates.game import World
from pirates.ship import LABORS
from pirates.trading_ui import contract_menu, item_detail_rows


def quiet_world():
    world = World()
    for sailor in world.ship.crew:
        sailor.labors = {kind: 0 for kind in LABORS}
        sailor.hunger = sailor.thirst = sailor.fatigue = sailor.loneliness = sailor.boredom = 0
        sailor.sleep_debt = 20
    return world


def at_office(world):
    world.player.x, world.player.y, world.player.z = 42, 23, 0
    world.refresh()


def test_trade_only_fantasy_goods_have_distinct_profitable_regional_markets():
    world = quiet_world()
    e = world.economy
    goods = [item_id for item_id, item in CATALOG.items() if "origin" in item]
    assert goods
    assert {item["origin"] for item_id, item in CATALOG.items() if item_id in goods} == set(PORTS)
    for item_id in goods:
        item = CATALOG[item_id]
        assert "supply" not in item and "slot" not in item
        origin_seller = next(mid for mid, merchant in MERCHANTS.items()
                             if merchant["port"] == item["origin"]
                             and merchant["stock"].get(item_id, 0) > 0)
        buyer_port = item["demand_ports"][0]
        buyer = next(mid for mid, merchant in MERCHANTS.items()
                     if merchant["port"] == buyer_port and item["category"] in merchant["accepts"])
        assert e.price(origin_seller, item_id, True) < e.price(buyer, item_id, False), item_id
        assert item["regional_factors"][buyer_port] > item["regional_factors"][item["origin"]]


def test_trade_only_cargo_buys_and_sells_without_becoming_ship_utility():
    world = quiet_world()
    e = world.economy
    e.money = 1000
    start = e.money
    ada = MERCHANTS["ada"]["at"]
    world.player.x, world.player.y = ada[0] + 1, ada[1]
    world.refresh()
    purse_total = e.money + e.treasury + sum(market["money"] for market in e.markets.values())
    e.trade(world, "ada", "bluegrain", 4, True)
    assert e.cargo["bluegrain"] == 4
    e.port = "cinder"
    build_port(world)
    dara = MERCHANTS["dara"]["at"]
    world.player.x, world.player.y = dara[0] + 1, dara[1]
    world.refresh()
    e.trade(world, "dara", "bluegrain", 4, False)
    assert "bluegrain" not in e.cargo
    assert e.money > start
    assert e.money + e.treasury + sum(market["money"] for market in e.markets.values()) == purse_total


def test_delivery_contract_escrow_cargo_reputation_and_save_roundtrip(tmp_path):
    world = quiet_world()
    e = world.economy
    at_office(world)
    offer = next(row for row in e.contract_offers("verdant", world.clock.day)
                 if row["item"] == "bluegrain")
    source_purse = e.markets[offer["issuer"]]["money"]
    starting_money = e.money
    contract = e.accept_contract(world, offer["offer_id"])
    assert contract["status"] == "active" and contract["escrow"] == offer["reward"]
    assert e.markets[offer["issuer"]]["money"] == source_purse - offer["reward"]
    second_offer = next(row for row in e.contract_offers("verdant", world.clock.day)
                        if row["offer_id"] != offer["offer_id"])
    e.accept_contract(world, second_offer["offer_id"])
    third_offer = next(row for row in e.contract_offers("verdant", world.clock.day)
                       if row["offer_id"] not in (offer["offer_id"], second_offer["offer_id"]))
    with pytest.raises(ValueError, match="active delivery contracts"):
        e.accept_contract(world, third_offer["offer_id"])

    world.player.x, world.player.y = MERCHANTS[offer["issuer"]]["at"][0] + 1, MERCHANTS[offer["issuer"]]["at"][1]
    world.refresh()
    e.trade(world, offer["issuer"], offer["item"], offer["quantity"], True)
    path = tmp_path / "active-delivery.json"
    world.save(path)
    restored = World.load(path)
    e = restored.economy
    assert e.contracts == world.economy.contracts
    assert e.trader_reputation == 0

    contract = e.contracts[0]
    e.port = contract["destination"]
    build_port(restored)
    at_office(restored)
    before = e.money
    reward = e.deliver_contract(restored, contract["offer_id"])
    assert reward == contract["reward"]
    assert e.money == before + reward
    assert e.money - PASSAGE_FEE > starting_money
    assert e.trader_reputation == 1
    assert e.cargo.get(contract["item"], 0) == 0
    assert contract["status"] == "completed" and contract["escrow"] == 0
    with pytest.raises(ValueError, match="not found"):
        e.deliver_contract(restored, contract["offer_id"])
    assert all(row["offer_id"] != offer["offer_id"] for row in e.contract_offers("verdant", restored.clock.day))


def test_active_contract_cargo_is_reserved_against_sale_and_generic_removal():
    world = quiet_world()
    e = world.economy
    at_office(world)
    offer = next(row for row in e.contract_offers("verdant", world.clock.day)
                 if row["item"] == "bluegrain")
    contract = e.accept_contract(world, offer["offer_id"])
    merchant = MERCHANTS[offer["issuer"]]
    world.player.x, world.player.y = merchant["at"][0] + 1, merchant["at"][1]
    world.refresh()
    e.trade(world, offer["issuer"], offer["item"], offer["quantity"] + 2, True)

    assert e.reserved_cargo(offer["item"]) == offer["quantity"]
    assert e.available_cargo(offer["item"]) == 2
    with pytest.raises(ValueError, match="unissued cargo"):
        e.trade(world, offer["issuer"], offer["item"], 3, False)
    with pytest.raises(ValueError, match="committed to an active delivery"):
        e.remove(offer["item"], offer["quantity"] + 1)

    e.trade(world, offer["issuer"], offer["item"], 2, False)
    assert e.cargo[offer["item"]] == contract["quantity"]
    assert e.available_cargo(offer["item"]) == 0


def test_contract_settlement_rejects_wrong_port_missing_cargo_and_sea_without_mutation():
    world = quiet_world()
    e = world.economy
    at_office(world)
    offer = next(row for row in e.contract_offers("verdant", world.clock.day)
                 if row["item"] == "bluegrain")
    contract = e.accept_contract(world, offer["offer_id"])
    accepted_money = e.money

    with pytest.raises(ValueError, match="Cinder Quay"):
        e.deliver_contract(world, contract["offer_id"])
    assert contract["status"] == "active" and contract["escrow"] == offer["reward"]
    assert e.money == accepted_money

    e.port = "cinder"
    build_port(world)
    at_office(world)
    with pytest.raises(ValueError, match="requires 6"):
        e.deliver_contract(world, contract["offer_id"])
    assert contract["status"] == "active" and e.money == accepted_money

    world.at_sea = True
    with pytest.raises(ValueError, match="harbor office"):
        e.deliver_contract(world, contract["offer_id"])
    assert contract["status"] == "active" and contract["escrow"] == offer["reward"]


def test_expired_contract_refunds_issuer_escrow_and_reputation_gates_larger_orders():
    world = quiet_world()
    e = world.economy
    at_office(world)
    base_offer = next(row for row in e.contract_offers("verdant", world.clock.day)
                      if row["item"] == "bluegrain")
    issuer = base_offer["issuer"]
    purse = e.markets[issuer]["money"]
    contract = e.accept_contract(world, base_offer["offer_id"])
    e.update_day(contract["deadline_day"] + 1)
    assert contract["status"] == "expired" and contract["escrow"] == 0
    assert e.markets[issuer]["money"] == purse
    assert e.trader_reputation == 0

    e.trader_reputation = 2
    advanced = e.contract_offers("verdant", world.clock.day)
    eligible = [item_id for item_id, item in CATALOG.items()
                if item.get("contract_offer", {}).get("min_reputation", 0) == 2
                and item.get("origin") == "verdant"]
    assert eligible and any(row["item"] in eligible for row in advanced)


def test_old_economy_save_defaults_contract_progress_and_rejects_forged_contract(tmp_path):
    world = quiet_world()
    path = tmp_path / "old-market.json"
    world.save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["economy"].pop("trader_reputation")
    data["economy"].pop("contracts")
    for market in data["economy"]["markets"].values():
        for item_id in ("bluegrain", "mooniron", "reef_porcelain"):
            market["stock"].pop(item_id, None)
    path.write_text(json.dumps(data), encoding="utf-8")
    restored = World.load(path)
    assert restored.economy.trader_reputation == 0 and restored.economy.contracts == []
    assert restored.economy.markets["ada"]["stock"]["bluegrain"] == 24
    assert restored.economy.markets["brann"]["stock"]["mooniron"] == 16

    at_office(world)
    offer = world.economy.contract_offers("verdant", world.clock.day)[0]
    world.economy.accept_contract(world, offer["offer_id"])
    world.save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["economy"]["contracts"][0]["reward"] += 1
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="reward"):
        World.load(path)


def test_contract_templates_cover_only_catalogued_regional_routes():
    assert all({"production", "found_at", "market_stock", "regional_factors"} <= item.keys()
               for item in CATALOG.values())
    expected_ids = {item["contract_offer"]["id"] for item in CATALOG.values()
                    if "contract_offer" in item}
    assert {row["id"] for row in CONTRACT_TEMPLATES} == expected_ids
    for template in CONTRACT_TEMPLATES:
        item = CATALOG[template["item"]]
        assert item["origin"] == template["origin"]
        assert template["destination"] in item["demand_ports"]
        assert item["contract_offer"]["id"] == template["id"]
        assert item["contract_offer"]["issuer"] == template["issuer"]
    for item_id, item in CATALOG.items():
        for merchant_id, quantity in item["market_stock"].items():
            assert MERCHANTS[merchant_id]["stock"][item_id] == quantity
    world = quiet_world()
    for template in CONTRACT_TEMPLATES:
        purchase = world.economy.quote(template["issuer"], template["item"], template["quantity"], True)
        payout = (CATALOG[template["item"]]["base_price"] * template["quantity"]
                  * RULES["contracts"]["reward_base_price_multiplier"]
                  + RULES["contracts"]["passage_allowance"])
        assert payout > purchase + PASSAGE_FEE


def test_contract_board_ui_discloses_offer_and_accepts_at_harbor_office():
    class Scripted:
        def __init__(self):
            self.answers = iter([0, 0, 4])
            self.titles = []

        def render_list(self, world, title, *args, **kwargs):
            self.titles.append(title)

        def choose(self, count):
            return next(self.answers)

        def wait_any(self):
            pass

    world = quiet_world()
    at_office(world)
    renderer = Scripted()
    contract_menu(renderer, world)
    assert world.economy.contracts[0]["status"] == "active"
    assert "Delivery offer" in renderer.titles
    assert "Merchant contract board — escrow-funded deliveries" in renderer.titles


def test_item_details_render_json_production_and_found_at_metadata():
    rows = item_detail_rows("bluegrain")
    assert any(row.startswith("Produced: Terrace-grown") for row in rows)
    assert any(row == "Found at: Verdant Cay — Ada Reed" for row in rows)
    assert any(row == "Regional buyers: Cinder Quay" for row in rows)
    assert any("production is not simulated" in row for row in rows)
