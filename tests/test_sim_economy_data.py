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


def provision_test_merchant(world, merchant_id: str, caused_by: str) -> str:
    return world.crew_ships.provision_recruited_merchant(
        world, merchant_id, caused_by=caused_by)


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
    provision_test_merchant(world, merchant_id, created_event)
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
    provision_test_merchant(world, merchant_id, created_event)
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
    provision_test_merchant(world, second_id, second_event)
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
    provision_test_merchant(world, merchant_id, created_event)
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
    provision_test_merchant(world, merchant_id, created_event)
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
    provision_test_merchant(world, merchant_id, world.economy.merchants[merchant_id]["last_event_id"])
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
    provision_test_merchant(
        original, merchant_id, original.economy.merchants[merchant_id]["last_event_id"])
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
    provision_test_merchant(world, merchant_id, created_event)
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
    provision_test_merchant(world, merchant_id, world.economy.merchants[merchant_id]["last_event_id"])
    source = world.economy.islands["isle_006"]
    destination = world.economy.islands["isle_012"]
    source["prices_milli"]["grain"] = 1000
    destination["prices_milli"]["grain"] = 100000

    assert world.economy.best_trade_route(world, merchant_id) is not None
    world.economy.merchants[merchant_id]["risk_beliefs_ppm"] = {
        route_id: 1_000_000 for route_id in world.geography.routes
    }
    assert world.economy.best_trade_route(world, merchant_id) is None


def test_merchant_blocker_telemetry_records_real_unprofitable_state_without_forcing_activity():
    world = WorldState.create("merchant-blocker-telemetry", director_enabled=False)
    candidates = [merchant_id for merchant_id, merchant in world.economy.merchants.items()
                  if merchant["ship_id"] not in world.travel.transits
                  and world.ship_is_commandable(merchant["ship_id"])]
    assert candidates
    merchant_id = candidates[0]
    for market in world.economy.islands.values():
        for good in world.economy.goods:
            market["prices_milli"][good] = 1000
    for other_id, merchant in world.economy.merchants.items():
        merchant["last_route_attempt_day"] = world.economy.last_updated_day
    merchant = world.economy.merchants[merchant_id]
    merchant["last_route_attempt_day"] = -7
    before_trades = len(world.event_log.by_type("MARKET_TRADE"))
    before_voyages = len(world.event_log.by_type("VOYAGE_STARTED"))

    world.economy._make_merchant_decisions(world)

    blocked = [event for event in world.event_log.by_type("MERCHANT_ACTIVITY_BLOCKED")
               if event.data["merchant_id"] == merchant_id]
    assert blocked
    assert blocked[-1].data["blocker_reason"] == \
        "no_positive_market_spread_or_buyer_liquidity"
    assert len(world.event_log.by_type("MARKET_TRADE")) == before_trades
    assert len(world.event_log.by_type("VOYAGE_STARTED")) == before_voyages
    assert world.economy.merchants[merchant_id]["activity_blocker_reason"] == \
        blocked[-1].data["blocker_reason"]
    snapshot = world.to_snapshot()
    assert WorldState.from_snapshot(snapshot).to_snapshot() == snapshot


def test_underway_merchant_is_not_misreported_as_blocked_or_reviewed():
    world = WorldState.create("merchant-underway-not-blocked", director_enabled=False)
    merchant_id = sorted(world.economy.merchants)[0]
    merchant = world.economy.merchants[merchant_id]
    ship_id = merchant["ship_id"]
    destination = next(island_id for island_id in world.economy.islands
                       if island_id != world.entities[ship_id]["location"]["id"])
    world.start_voyage(ship_id, destination, caused_by=merchant["last_event_id"])
    for row in world.economy.merchants.values():
        row["last_route_attempt_day"] = 0
    merchant["last_route_attempt_day"] = -7
    event_count = len(world.event_log)

    world.economy._make_merchant_decisions(world)

    assert len(world.event_log) == event_count
    assert merchant["activity_blocker_reason"] is None
    assert not world.event_log.by_type("MERCHANT_ACTIVITY_BLOCKED")


def test_captured_merchant_vessel_closes_firm_without_transferring_or_destroying_assets():
    world = WorldState.create("merchant-title-transfer-closes-firm", director_enabled=False)
    merchant_id = next(
        merchant_id for merchant_id, merchant in sorted(world.economy.merchants.items())
        if merchant.get("status", "active") == "active"
        and world.entities[merchant["ship_id"]].get("controller_crew_id") in world.crew_ships.crews)
    merchant = world.economy.merchants[merchant_id]
    ship_id = merchant["ship_id"]
    ship = world.entities[ship_id]
    controller = ship["controller_crew_id"]
    island_id = world.location_of(ship_id)["id"]
    grain_available = world.economy.islands[island_id]["stockpile_milli"]["grain"]
    assert grain_available > 0
    world.trade(merchant_id, island_id, "grain", "buy", min(1000, grain_available),
                caused_by=merchant["last_event_id"])
    previous_owner = dict(ship["legal_owner"])
    silver_before = world.economy._total_silver()
    goods_before = world.economy._total_goods()
    retained_silver = merchant["silver"]
    retained_cargo = dict(merchant["cargo_milli"])
    assert retained_cargo["grain"] > 0

    acquisition = world.event_log.append(
        tick=world.clock.tick, event_type="SHIP_ACQUIRED",
        location=world.location_of(ship_id)["id"],
        actors=[{"role": "acquiring_crew", "entity_id": controller}],
        targets=[{"role": "acquired_ship", "entity_id": ship_id}],
        data={"method": "capture", "previous_owner": previous_owner,
              "previous_controller_crew_id": controller,
              "legal_owner_kind": "crew", "legal_owner_id": controller,
              "controller_crew_id": controller},
        caused_by=[merchant["last_event_id"]],
        causes_tags=["test_capture", "ownership_change"], magnitude=0.5)
    world.assign_ship_title(
        ship_id, owner_kind="crew", owner_id=controller,
        controller_crew_id=controller, event_id=acquisition.id)

    assert merchant["status"] == "closed"
    closure = world.event_log.get(merchant["closed_event_id"])
    assert closure.type == "MERCHANT_CLOSED"
    assert closure.caused_by == (acquisition.id,)
    assert closure.data["retained_silver"] == retained_silver
    assert closure.data["retained_cargo_milli"] == retained_cargo
    assert closure.data["account_disposition"] == "retained_as_unresolved_merchant_claim"
    assert world.economy._total_silver() == silver_before
    assert world.economy._total_goods() == goods_before
    assert world.economy.best_trade_route(world, merchant_id) is None
    assert world.economy.best_reposition(world, merchant_id) is None
    with pytest.raises(ValueError, match="Closed merchant accounts"):
        world.trade(merchant_id, world.location_of(ship_id)["id"], "grain", "sell", 1,
                    caused_by=closure.id)

    event_count = len(world.event_log)
    for other in world.economy.merchants.values():
        other["last_route_attempt_day"] = world.economy.last_updated_day
    world.economy._make_merchant_decisions(world)
    assert len(world.event_log) == event_count

    world.encounters._sink_ship(world, ship_id, closure.id)
    assert ship_id not in world.entities
    assert world.economy.merchants[merchant_id]["status"] == "closed"
    assert not any(event.data.get("old_ship_id") == ship_id
                   for event in world.event_log.by_type("MERCHANT_VESSEL_REPLACED"))
    snapshot = world.to_snapshot()
    restored = WorldState.from_snapshot(snapshot)
    assert restored.to_snapshot() == snapshot


def test_sunk_merchant_vessel_closes_account_without_free_replacement_hull():
    world = WorldState.create("merchant-sinking-is-not-free-replacement", director_enabled=False)
    merchant_id, merchant = next(
        (merchant_id, row) for merchant_id, row in sorted(world.economy.merchants.items())
        if row.get("status", "active") == "active"
        and world.entities[row["ship_id"]].get("legal_owner") == {
            "kind": "merchant", "id": merchant_id})
    ship_id = merchant["ship_id"]
    vessel_count_before = sum(entity.get("kind") == "ship"
                              for entity in world.entities.values())
    silver_before = world.economy._total_silver()
    goods_before = world.economy._total_goods()

    world.encounters._sink_ship(world, ship_id, merchant["last_event_id"])

    assert ship_id in world.archive and ship_id not in world.entities
    assert sum(entity.get("kind") == "ship" for entity in world.entities.values()) == vessel_count_before - 1
    assert merchant["status"] == "closed"
    closure = world.event_log.get(merchant["closed_event_id"])
    assert closure.data["reason"] == "vessel_sunk"
    assert closure.caused_by == (world.archive[ship_id]["last_event_id"],)
    assert world.economy._total_silver() == silver_before
    assert world.economy._total_goods() == goods_before
    assert not world.event_log.by_type("MERCHANT_VESSEL_REPLACED")
    assert WorldState.from_snapshot(world.to_snapshot()).to_snapshot() == world.to_snapshot()


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


def test_shortage_relief_negotiation_uses_a_finite_market_trade_once():
    world = WorldState.create("shortage-relief-negotiation")
    merchant = next(row for row in world.economy.merchants.values()
                    if world.entities[row["ship_id"]]["location"]["id"]
                    in world.politics.island_rulers)
    merchant_id = merchant["id"]
    island_id = world.entities[merchant["ship_id"]]["location"]["id"]
    market = world.economy.islands[island_id]
    buy = world.economy.trade(
        world, merchant_id, island_id, "grain", "buy", 10_000,
        caused_by=merchant["last_event_id"])
    parent = world.event_log.get(buy["id"])
    pressure = world.event_log.append(
        tick=world.clock.tick, event_type="ISLAND_ECONOMY_UPDATED", location=island_id,
        targets=[{"role": "island", "entity_id": island_id}],
        data={"day": 0, "shortage_milli": {**{good: 0 for good in world.economy.goods},
                                              "grain": 15_000}},
        caused_by=[parent.id], causes_tags=["shortage_fixture"], magnitude=0.2,
    )
    stock_before = market["stockpile_milli"]["grain"]
    cash_before = merchant["silver"]
    cargo_before = merchant["cargo_milli"]["grain"]
    silver_total_before = world.economy._total_silver()
    result_id = world.economy.negotiate_shortage_relief(
        world, merchant_id, island_id, "grain", 10_000,
        pressure_event_id=pressure.id)
    result = world.event_log.get(result_id)

    assert result.type == "SHORTAGE_RELIEF_NEGOTIATED"
    assert result.data["materially_settled"] is True
    assert result.data["quantity_milli"] == 10_000
    assert merchant["cargo_milli"]["grain"] == cargo_before - 10_000
    assert market["stockpile_milli"]["grain"] == stock_before + 10_000
    assert merchant["silver"] > cash_before
    assert world.economy._total_silver() == silver_total_before
    assert world.event_log.get(result.data["trade_event_id"]).caused_by == (pressure.id,)
    snapshot_after = world.to_snapshot()
    assert world.economy.negotiate_shortage_relief(
        world, merchant_id, island_id, "grain", 10_000,
        pressure_event_id=pressure.id) == result_id
    other_merchant = next(key for key in world.economy.merchants if key != merchant_id)
    assert world.economy.negotiate_shortage_relief(
        world, other_merchant, island_id, "grain", 10_000,
        pressure_event_id=pressure.id) == result_id
    assert world.to_snapshot() == snapshot_after
    world.economy.ledger.assert_balanced(world.economy, validate_history=False)


def test_contraband_inspection_confiscates_goods_and_conserves_fine():
    world = WorldState.create("contraband-enforcement")
    merchant = world.economy.merchants["merchant_000003"]
    ship_id = merchant["ship_id"]
    source_island = world.entities[ship_id]["location"]["id"]
    authority_island, authority_faction = next(iter(world.politics.island_rulers.items()))
    bought = world.economy.trade(
        world, merchant["id"], source_island, "contraband", "buy", 10_000,
        caused_by=merchant["last_event_id"])
    world.entities[ship_id]["location"] = {"kind": "island", "id": authority_island}
    sold = world.economy.trade(
        world, merchant["id"], authority_island, "contraband", "sell", 10_000,
        caused_by=bought["id"])
    trade_event = world.event_log.get(sold["id"])
    authority = world.politics.factions[authority_faction]
    action = world.event_log.append(
        tick=world.clock.tick, event_type="ENFORCEMENT_ACTION", location=authority_island,
        actors=[{"role": "enforcement_officer", "entity_id": authority["leader_id"]}],
        targets=[{"role": "suspected_seller", "entity_id": merchant["id"]}],
        data={"action_id": "inspect_contraband", "authority_faction_id": authority_faction,
              "trade_event_id": trade_event.id},
        caused_by=[trade_event.id], causes_tags=["lawful_market_inspection"], magnitude=0.05,
    )
    market = world.economy.islands[authority_island]
    stock_before = market["stockpile_milli"]["contraband"]
    crew_id = world.entities[ship_id]["controller_crew_id"]
    crew = world.crew_ships.crews[crew_id]
    crew_silver_before = crew["treasury_silver"]
    market_silver_before = market["treasury_silver"]
    total_silver_before = world.economy._total_silver()
    total_goods_before = world.economy._total_goods()

    seized_id = world.economy.enforce_contraband_trade(
        world, trade_event.id, authority_faction, caused_by=action.id)
    seized = world.event_log.get(seized_id)

    assert seized.type == "CONTRABAND_SEIZED"
    assert seized.data["quantity_milli"] == 10_000
    assert seized.data["fine_silver"] == 10
    assert market["stockpile_milli"]["contraband"] == stock_before - 10_000
    assert crew["treasury_silver"] == crew_silver_before - 10
    assert market["treasury_silver"] == market_silver_before + 10
    assert world.economy._total_silver() == total_silver_before
    total_goods_after = world.economy._total_goods()
    assert total_goods_after["contraband"] == total_goods_before["contraband"] - 10_000
    assert all(total_goods_after[good] == total_goods_before[good]
               for good in total_goods_before if good != "contraband")
    assert world.economy.ledger.goods_sinks["contraband"] == 10_000
    snapshot_after = world.to_snapshot()
    assert world.economy.enforce_contraband_trade(
        world, trade_event.id, authority_faction, caused_by=action.id) == seized_id
    assert world.to_snapshot() == snapshot_after
    assert WorldState.from_snapshot(snapshot_after).to_snapshot() == snapshot_after
    world.economy.validate_world(world)


def test_two_profitable_market_sellers_create_one_causal_rivalry_record():
    world = WorldState.create("profitable-market-rivalry")
    first = world.economy.merchants["merchant_000001"]
    second_id, created = world.create_merchant(
        "Second Isle Company", "isle_002", silver=100,
        capacity_milli=5_000, travel_cost_silver_per_day=0,
        risk_cost_basis_points=0)
    merchant_crew_id = provision_test_merchant(world, second_id, created)
    pirate_ship_id, pirate_created = world.create_vessel("Rival Coalition Ship", "isle_002")
    pirate_crew_id = world.crew_ships.provision_recruited_vessel(
        world, pirate_ship_id, "pirate", caused_by=pirate_created)
    founder = world.entities[world.crew_ships.crews[pirate_crew_id]["captain_id"]]
    founder["traits"]["ambition"] = max(
        founder["traits"]["ambition"],
        world.politics.data["rules"]["faction_founder_min_ambition"])
    founder["stats"]["leadership"] = max(
        founder["stats"]["leadership"],
        world.politics.data["rules"]["faction_founder_min_leadership"])
    for crew_id in (merchant_crew_id, pirate_crew_id):
        world.crew_ships.crews[crew_id]["ideology_tags"] = ["profit"]
    world.politics.found_faction(
        world, member_crew_ids=[merchant_crew_id, pirate_crew_id],
        founder_id=founder["id"], anchorage_island_id="isle_002",
        purpose="lawful profitable trade", name=f"{founder['display_name']} Company",
        kind="merchant_coalition",
        caused_by=[world.crew_ships.crews[merchant_crew_id]["last_event_id"],
                   world.crew_ships.crews[pirate_crew_id]["last_event_id"]])
    source_ids, destination_id = ("isle_001", "isle_002"), "isle_003"
    world.economy.islands[source_ids[0]]["prices_milli"]["grain"] = 1_000
    world.economy.islands[source_ids[1]]["prices_milli"]["grain"] = 1_000
    world.economy.islands[destination_id]["prices_milli"]["grain"] = 100_000
    first_buy = world.economy.trade(
        world, first["id"], source_ids[0], "grain", "buy", 5_000,
        caused_by=first["last_event_id"])
    second = world.economy.merchants[second_id]
    second_buy = world.economy.trade(
        world, second_id, source_ids[1], "grain", "buy", 5_000,
        caused_by=created)
    for merchant in (first, second):
        world.entities[merchant["ship_id"]]["location"] = {
            "kind": "island", "id": destination_id}
    before_silver = world.economy._total_silver()
    before_goods = world.economy._total_goods()
    captain_ids = [world.crew_ships.crews[
        world.entities[merchant["ship_id"]]["controller_crew_id"]]["captain_id"]
        for merchant in (first, second)]
    stats_before = {captain_id: dict(world.entities[captain_id]["stats"])
                    for captain_id in captain_ids}
    first_sale = world.economy.trade(
        world, first["id"], destination_id, "grain", "sell", 5_000,
        caused_by=first_buy["id"])
    second_sale = world.economy.trade(
        world, second_id, destination_id, "grain", "sell", 5_000,
        caused_by=second_buy["id"])

    rivalries = world.event_log.by_type("PROFITABLE_TRADE_RIVALRY")
    assert len(rivalries) == 1
    rivalry = rivalries[0]
    assert set(rivalry.data["trade_event_ids"]) == {first_sale["id"], second_sale["id"]}
    assert all(value > 0 for value in rivalry.data["profits_silver"])
    assert set(rivalry.caused_by) == {first_sale["id"], second_sale["id"]}
    relation_event = world.event_log.by_type("TRADE_RIVALRY_RELATION_CHANGED")[-1]
    assert relation_event.data["rivalry_event_id"] == rivalry.id
    assert relation_event.caused_by == (rivalry.id,)
    for merchant in (first, second):
        captain_id = world.crew_ships.crews[
            world.entities[merchant["ship_id"]]["controller_crew_id"]]["captain_id"]
        assert world.entities[captain_id]["reputation"]["public_renown"] > 0
        assert "profitable_trade" in world.entities[captain_id]["reputation"]["paths"]
        assert world.entities[captain_id]["stats"] == stats_before[captain_id]
    assert world.economy._total_silver() == before_silver
    assert world.economy._total_goods() == before_goods
    world.event_log.validate_causality()
    world.to_snapshot()


def test_profitable_trade_reroutes_around_a_blockaded_route():
    world = WorldState.create("blockade-profitable-reroute")
    merchant_id, created = world.create_merchant(
        "Reroute Company", "isle_001", silver=100,
        capacity_milli=5_000, travel_cost_silver_per_day=0,
        risk_cost_basis_points=0)
    provision_test_merchant(world, merchant_id, created)
    for market in world.economy.islands.values():
        for goods_id in world.economy.goods:
            market["prices_milli"][goods_id] = 1_000
    world.economy.islands["isle_002"]["prices_milli"]["grain"] = 100_000
    plan = world.economy.best_trade_route(world, merchant_id)
    assert plan is not None and plan["destination"] == "isle_002"
    assert "route_001" in plan["route_ids"]
    cause = world.event_log.by_type("WORLD_GENERATED")[0].id
    blockade = world.economy.blockade_route(
        world, "route_001", "isle_001", True, caused_by=cause)

    rerouted = world.economy.best_trade_route(world, merchant_id)
    assert rerouted is not None and rerouted["destination"] == "isle_002"
    assert "route_001" not in rerouted["route_ids"]
    voyage_id = world.economy.dispatch_trade_route(
        world, merchant_id, rerouted, caused_by=blockade)
    transit = world.travel.transits[world.economy.merchants[merchant_id]["ship_id"]]
    assert transit["voyage_event_id"] == voyage_id
    assert "route_001" not in transit["route_ids"]
    assert world.economy.ledger.assert_balanced(world.economy, validate_history=False) is None
    world.event_log.validate_causality()
