import copy
import json
from pathlib import Path

import pytest

from sim.core.world import WorldState
from sim.systems.economy import (
    ECONOMY_PATH,
    GOODS_PATH,
    EconomySystem,
    load_economy_data,
    validate_economy_data,
)
from sim.systems.geography import WorldMap


ROOT = Path(__file__).resolve().parents[1]


def data_copy():
    goods, economy = load_economy_data()
    return copy.deepcopy(goods), copy.deepcopy(economy)


def test_goods_and_island_economy_catalogs_cover_required_data():
    goods, economy = load_economy_data(GOODS_PATH, ECONOMY_PATH)
    geography = WorldMap.load()
    validate_economy_data(goods, economy, set(geography.islands))

    assert 12 <= len(goods["goods"]) <= 20
    ids = {row["id"] for row in goods["goods"]}
    assert {"grain", "water", "rum", "iron", "timber", "sailcloth", "powder",
            "medicine", "luxury_goods", "spices", "contraband", "rare_relics"} <= ids
    assert {row["island_id"] for row in economy["islands"]} == set(geography.islands)
    assert len(economy["profiles"]) >= 5
    for profile in economy["profiles"].values():
        assert sum(profile["population_classes_basis_points"].values()) == 10000


@pytest.mark.parametrize("corrupt", ["missing_good", "wrong_class_sum", "missing_island", "negative_stock"])
def test_economy_loader_rejects_invalid_catalog_shapes(corrupt):
    goods, economy = data_copy()
    if corrupt == "missing_good":
        goods["goods"] = [row for row in goods["goods"] if row["id"] != "grain"]
    elif corrupt == "wrong_class_sum":
        economy["profiles"]["farming"]["population_classes_basis_points"]["laborers"] -= 1
    elif corrupt == "missing_island":
        economy["islands"].pop()
    elif corrupt == "negative_stock":
        economy["profiles"]["port"]["stockpile"]["grain"] = -1

    with pytest.raises(ValueError):
        validate_economy_data(goods, economy, set(WorldMap.load().islands))


def test_daily_production_consumption_prices_and_treasury_are_deterministic():
    first = WorldState.create("economy-determinism")
    second = WorldState.create("economy-determinism")
    first.run_until(24 * 90)
    second.run_until(24 * 90)

    assert first.economy.snapshot() == second.economy.snapshot()
    assert first.event_log.to_jsonl() == second.event_log.to_jsonl()
    assert first.economy.last_updated_day == 90
    assert len(first.event_log.by_type("ISLAND_ECONOMY_UPDATED")) == len(first.geography.islands) * 90
    for event in first.event_log.by_type("ISLAND_ECONOMY_UPDATED"):
        assert event.data["day"] >= 1
        assert event.data["production_milli"]
        assert event.data["consumption_milli"]
        assert event.caused_by
    first.event_log.validate_causality()


def test_daily_stock_flow_matches_production_and_consumption_and_prices_smooth():
    world = WorldState.create("economy-stock-flow")
    state = world.economy.islands["isle_001"]
    stock_before = state["stockpile_milli"]["grain"]
    price_before = state["prices_milli"]["grain"]
    world.run_until(24)
    event = next(event for event in world.event_log.by_type("ISLAND_ECONOMY_UPDATED")
                 if event.location == "isle_001")

    expected = (stock_before + event.data["production_milli"].get("grain", 0)
                - event.data["consumption_milli"]["grain"])
    assert state["stockpile_milli"]["grain"] == expected
    assert state["prices_milli"]["grain"] != price_before
    assert state["prices_milli"]["grain"] > 0
    assert 0 <= state["treasury_silver"]
    assert event.data["tax_income_silver"] >= event.data["tax_collected_silver"]
    assert event.data["upkeep_paid_silver"] >= 0


def test_aggregate_silver_stays_conserved_through_tax_upkeep_and_market_transfer():
    world = WorldState.create("silver-transfer")

    def total_silver():
        return sum(
            state["treasury_silver"] + state["population_silver"]
            + state["garrison_silver"] + state["port_service_silver"]
            for state in world.economy.islands.values()
        ) + sum(merchant["silver"] for merchant in world.economy.merchants.values())

    opening = total_silver()
    world.run_until(24)
    assert total_silver() == opening

    merchant_id, created_event = world.create_merchant(
        "Tomas Vale", "isle_006", silver=1000, capacity_milli=10000)
    after_endowment = total_silver()
    assert after_endowment == opening + 1000
    trade = world.trade(merchant_id, "isle_006", "grain", "buy", 1000,
                        caused_by=created_event)
    assert total_silver() == after_endowment
    world.trade(merchant_id, "isle_006", "grain", "sell", 1000,
                caused_by=trade["id"])
    assert total_silver() == after_endowment


def test_global_good_quantity_changes_only_by_logged_production_and_consumption():
    world = WorldState.create("good-conservation")
    tracked = ("grain", "fish", "water", "medicine")
    before = {goods_id: world.economy._total_goods()[goods_id] for goods_id in tracked}
    world.run_until(24)
    events = world.event_log.by_type("ISLAND_ECONOMY_UPDATED")
    ship_consumption = world.event_log.by_type("SHIP_SUPPLIES_CONSUMED")
    for goods_id in tracked:
        produced = sum(event.data["production_milli"].get(goods_id, 0) for event in events)
        consumed = sum(event.data["consumption_milli"].get(goods_id, 0) for event in events)
        consumed += sum(event.data["consumed_milli"].get(goods_id, 0)
                        for event in ship_consumption)
        after = world.economy._total_goods()[goods_id]
        assert after == before[goods_id] + produced - consumed


def test_island_shortage_persists_into_famine_unrest_and_lower_productivity():
    world = WorldState.create("economy-shortage")
    state = world.economy.islands["isle_001"]
    removed = {goods_id: quantity for goods_id, quantity in state["stockpile_milli"].items()
               if quantity}
    state["stockpile_milli"] = {goods_id: 0 for goods_id in state["stockpile_milli"]}
    sink_event = world.event_log.append(
        tick=0, event_type="DIRECTOR_INJECTION", location="isle_001",
        data={"scenario": "test_stock_depletion",
              "ledger_sink": {"silver": 0, "goods": removed}},
        causes_tags=["test_scenario", "goods_destruction"], magnitude=0.1,
    )
    world.economy.ledger.record_sink(sink_event.id, goods=removed)
    state["unrest"] = 50
    before_production = state["produces_per_day"]["grain"] * 1000
    world.run_until(24 * 7)

    assert state["famine_level"] > 0
    assert state["unrest"] > 50
    first_day = next(event for event in world.event_log.by_type("ISLAND_ECONOMY_UPDATED")
                     if event.location == "isle_001")
    assert first_day.data["production_milli"]["grain"] < before_production
    assert first_day.data["shortage_milli"]


def test_economy_state_roundtrips_and_resumes_midrun(tmp_path):
    uninterrupted = WorldState.create("economy-resume")
    split = WorldState.create("economy-resume")
    uninterrupted.run_until(24 * 20)
    split.run_until(24 * 20)
    resumed = WorldState.load(split.save(tmp_path / "economy.json"))

    uninterrupted.run_until(24 * 60)
    resumed.run_until(24 * 60)
    assert resumed.to_snapshot() == uninterrupted.to_snapshot()
    assert resumed.event_log.to_jsonl() == uninterrupted.event_log.to_jsonl()


def test_economy_data_digest_prevents_incompatible_resume(tmp_path):
    world = WorldState.create("economy-digest")
    snapshot = world.to_snapshot()
    snapshot["economy"]["digest"] = "0" * 64
    with pytest.raises(ValueError, match="Economy data changed"):
        WorldState.from_snapshot(snapshot)


def test_snapshot_restore_rejects_unbalanced_silver_or_goods_ledgers():
    world = WorldState.create("ledger-tamper")
    world.run_until(24 * 5)
    balanced = world.to_snapshot()

    changed_goods = copy.deepcopy(balanced)
    changed_goods["economy"]["islands"]["isle_001"]["stockpile_milli"]["grain"] += 1
    with pytest.raises(ValueError, match="Global goods conservation"):
        WorldState.from_snapshot(changed_goods)

    changed_silver = copy.deepcopy(balanced)
    changed_silver["economy"]["islands"]["isle_001"]["treasury_silver"] += 1
    with pytest.raises(ValueError, match="Global silver conservation"):
        WorldState.from_snapshot(changed_silver)


def test_market_orders_transfer_finite_goods_and_silver_with_partial_fill():
    world = WorldState.create("finite-market")
    merchant_id, created_event = world.create_merchant(
        "Mara Venn", "isle_030", silver=100, capacity_milli=5000,
        travel_cost_silver_per_day=0, risk_cost_basis_points=0,
    )
    market = world.economy.islands["isle_030"]
    # Isolate this regression to finite treasury buying power; household cash
    # stays in the global ledger but belongs to another market.
    world.economy.islands["isle_029"]["population_silver"] += market["population_silver"]
    market["population_silver"] = 0
    stock_before = market["stockpile_milli"]["grain"]

    purchase = world.trade(merchant_id, "isle_030", "grain", "buy", 100000,
                           caused_by=created_event)
    merchant = world.economy.merchants[merchant_id]
    assert purchase["data"]["quantity_milli"] == 5000
    assert market["stockpile_milli"]["grain"] == stock_before - 5000
    assert merchant["cargo_milli"]["grain"] == 5000
    assert merchant["silver"] == 80
    assert market["treasury_silver"] == 320

    second_id, second_event = world.create_merchant(
        "Pella Wren", "isle_030", silver=0, capacity_milli=100000,
        cargo_milli={"grain": 100000}, travel_cost_silver_per_day=0,
        risk_cost_basis_points=0)
    sale = world.trade(second_id, "isle_030", "grain", "sell", 100000,
                       caused_by=purchase["id"])
    assert sale["data"]["quantity_milli"] == 80000
    assert sale["data"]["silver_transferred"] == 320
    assert world.economy.merchants[second_id]["silver"] == 320
    assert market["treasury_silver"] == 0
    assert world.economy.merchants[second_id]["cargo_milli"]["grain"] == 20000
    world.event_log.validate_causality()


def test_market_trade_can_use_finite_household_cash_and_logs_account_debit():
    world = WorldState.create("household-market-cash")
    market = world.economy.islands["isle_030"]
    world.economy.islands["isle_029"]["treasury_silver"] += market["treasury_silver"]
    market["treasury_silver"] = 0
    merchant_id, created_event = world.create_merchant(
        "Sella Venn", "isle_030", silver=0, capacity_milli=100000,
        cargo_milli={"grain": 100000}, travel_cost_silver_per_day=0,
        risk_cost_basis_points=0,
    )
    household_cash_before = market["population_silver"]
    sale = world.trade(merchant_id, "isle_030", "grain", "sell", 100000,
                       caused_by=created_event)
    assert sale["data"]["island_account_debits"] == {
        "population_silver": sale["data"]["silver_transferred"]}
    assert 0 < sale["data"]["silver_transferred"] <= household_cash_before
    assert market["population_silver"] == household_cash_before - sale["data"]["silver_transferred"]
    world.economy.ledger.assert_balanced(world.economy)


def test_merchant_without_an_active_ship_cannot_trade():
    world = WorldState.create("merchant-no-ship")
    merchant_id, created_event = world.create_merchant(
        "Shipless Merchant", "isle_030", silver=100, capacity_milli=10000)
    world.economy.merchants[merchant_id]["ship_id"] = None

    with pytest.raises(ValueError, match="active ship to trade"):
        world.trade(merchant_id, "isle_030", "grain", "buy", 1000,
                    caused_by=created_event)


def test_merchant_route_ai_uses_beliefs_and_completes_finite_trade(tmp_path):
    world = WorldState.create("merchant-route")
    merchant_id, created_event = world.create_merchant(
        "Brann Ash", "isle_006", silver=100, capacity_milli=5000,
        travel_cost_silver_per_day=0, risk_cost_basis_points=10000,
        risk_beliefs_ppm={"route_001": 900000},
    )
    source = world.economy.islands["isle_006"]
    destination = world.economy.islands["isle_012"]
    source["prices_milli"]["grain"] = 1000
    destination["prices_milli"]["grain"] = 100000

    plan = world.economy.best_trade_route(world, merchant_id)
    assert plan is not None
    assert plan["destination"] == "isle_012"
    assert plan["risk_belief_ppm"] == 0  # no high-risk route in this shortest path
    assert plan["expected_profit_silver"] > 0
    voyage_id = world.economy.dispatch_trade_route(world, merchant_id, plan)
    transit = world.travel.transits[world.economy.merchants[merchant_id]["ship_id"]]
    world.run_until(transit["arrival_tick"])

    assert world.event_log.get(voyage_id).type == "VOYAGE_STARTED"
    assert world.economy.merchants[merchant_id]["cargo_milli"]["grain"] == 0
    assert world.economy.merchants[merchant_id]["silver"] > 95
    assert destination["stockpile_milli"]["grain"] > 0
    assert world.event_log.by_type("MARKET_TRADE")
    world.event_log.validate_causality()


def test_merchant_reposition_reuses_leg_duration_across_candidate_paths(monkeypatch):
    world = WorldState.create("merchant-reposition-cache")
    merchant_id, _ = world.create_merchant(
        "Rill Vane", "isle_006", silver=100, capacity_milli=5000,
        travel_cost_silver_per_day=0, risk_cost_basis_points=0)
    world.economy.islands["isle_006"]["prices_milli"]["grain"] = 1000
    world.economy.islands["isle_012"]["prices_milli"]["grain"] = 100000
    original = world.travel._leg_hours
    calls = 0
    original_speed = world.weather.speed_permille
    speed_calls = 0

    def counted_leg_hours(route, vessel, condition):
        nonlocal calls
        calls += 1
        return original(route, vessel, condition)

    def counted_speed_permille(region_id):
        nonlocal speed_calls
        speed_calls += 1
        return original_speed(region_id)

    monkeypatch.setattr(world.travel, "_leg_hours", counted_leg_hours)
    monkeypatch.setattr(world.weather, "speed_permille", counted_speed_permille)
    world.economy.best_reposition(world, merchant_id)
    first_call_count = calls
    first_speed_call_count = speed_calls
    world.economy.best_reposition(world, merchant_id)

    assert 0 < calls <= len(world.geography.routes)
    assert calls == first_call_count
    assert 0 < speed_calls <= 2 * len(world.geography.routes) + len(world.geography.regions)
    assert speed_calls == first_speed_call_count
    world.clock.advance(24)
    world.economy.best_reposition(world, merchant_id)
    assert world.economy._route_duration_cache_tick == world.clock.tick
    assert calls > first_call_count
    assert speed_calls > first_speed_call_count


def test_merchant_midroute_snapshot_preserves_ledger_and_future_trades(tmp_path):
    original = WorldState.create("merchant-midroute")
    merchant_id, _ = original.create_merchant(
        "Sela Norr", "isle_006", silver=100, capacity_milli=5000,
        travel_cost_silver_per_day=0, risk_cost_basis_points=0)
    origin = original.economy.islands["isle_006"]
    destination = original.economy.islands["isle_012"]
    origin["prices_milli"]["grain"] = 1000
    destination["prices_milli"]["grain"] = 100000
    plan = original.economy.best_trade_route(original, merchant_id)
    assert plan is not None
    original.economy.dispatch_trade_route(original, merchant_id, plan)
    ship_id = original.economy.merchants[merchant_id]["ship_id"]
    transit = original.travel.transits[ship_id]
    halfway = transit["start_tick"] + max(1, (transit["arrival_tick"] - transit["start_tick"]) // 2)
    original.run_until(halfway)
    restored = WorldState.load(original.save(tmp_path / "merchant-at-sea.json"))
    arrival_tick = restored.travel.transits[ship_id]["arrival_tick"]

    original.run_until(arrival_tick)
    restored.run_until(arrival_tick)
    assert restored.to_snapshot() == original.to_snapshot()


def test_unprofitable_or_unaffordable_order_is_refused_without_mutation():
    world = WorldState.create("refused-market")
    merchant_id, created_event = world.create_merchant(
        "Pell Orin", "isle_006", silver=0, capacity_milli=1000)
    before = world.to_snapshot()
    with pytest.raises(ValueError, match="cannot fill"):
        world.trade(merchant_id, "isle_006", "grain", "buy", 1000,
                    caused_by=created_event)
    assert world.to_snapshot() == before


def test_merchant_risk_beliefs_can_suppress_an_otherwise_profitable_route():
    world = WorldState.create("merchant-risk-belief")
    merchant_id, _ = world.create_merchant(
        "Nera Vale", "isle_006", silver=100, capacity_milli=5000,
        travel_cost_silver_per_day=0, risk_cost_basis_points=10000,
    )
    source = world.economy.islands["isle_006"]
    destination = world.economy.islands["isle_012"]
    source["prices_milli"]["grain"] = 1000
    destination["prices_milli"]["grain"] = 100000

    assert world.economy.best_trade_route(world, merchant_id) is not None
    world.economy.merchants[merchant_id]["risk_beliefs_ppm"] = {
        route_id: 1_000_000 for route_id in world.geography.routes
    }
    assert world.economy.best_trade_route(world, merchant_id) is None


def test_route_blockade_disrupts_trade_and_raises_destination_prices_and_unrest():
    baseline = WorldState.create("blockade-response")
    blockaded = WorldState.create("blockade-response")
    route = blockaded.geography.routes_between("isle_006")[0]
    cause = blockaded.event_log.by_type("WORLD_GENERATED")[0].id
    blockade_id = blockaded.economy.blockade_route(
        blockaded, route.id, "isle_006", True, caused_by=cause,
    )
    assert route.id in blockaded.travel.closed_routes
    assert route.id in blockaded.economy.islands["isle_006"]["blockaded_routes"]

    baseline.run_until(24 * 30)
    blockaded.run_until(24 * 30)
    normal_market = baseline.economy.islands["isle_006"]
    disrupted_market = blockaded.economy.islands["isle_006"]
    assert disrupted_market["prices_milli"]["water"] > normal_market["prices_milli"]["water"]
    assert disrupted_market["unrest"] > normal_market["unrest"]
    assert blockaded.event_log.get(blockade_id).type == "ROUTE_BLOCKADED"
    assert blockaded.economy.ledger.last_day == 30
    restored = WorldState.from_snapshot(blockaded.to_snapshot())
    assert restored.travel.closed_routes == {route.id}
    assert restored.economy.blockades == blockaded.economy.blockades

    lift_id = blockaded.economy.blockade_route(
        blockaded, route.id, "isle_006", False, caused_by=blockade_id,
    )
    assert route.id not in blockaded.travel.closed_routes
    assert blockaded.event_log.get(lift_id).type == "ROUTE_BLOCKADE_LIFTED"
    blockaded.to_snapshot()
