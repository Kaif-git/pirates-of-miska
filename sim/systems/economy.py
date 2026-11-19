"""Data-driven island production, consumption, market prices, and treasuries."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from sim.core.ownership import ship_is_commandable


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
GOODS_PATH = DATA_DIR / "goods.json"
ECONOMY_PATH = DATA_DIR / "economy.json"
PRICE_SCALE = 1000
QUANTITY_SCALE = 1000
VALUE_SCALE = PRICE_SCALE * QUANTITY_SCALE


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load economy data {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Economy data must be an object: {path}")
    return value


def load_economy_data(goods_path: str | Path = GOODS_PATH,
                      economy_path: str | Path = ECONOMY_PATH) -> tuple[dict[str, Any], dict[str, Any]]:
    goods, economy = _load_json(Path(goods_path)), _load_json(Path(economy_path))
    validate_economy_data(goods, economy)
    return goods, economy


def validate_economy_data(goods_data: dict[str, Any], economy_data: dict[str, Any],
                          island_ids: set[str] | None = None) -> None:
    if goods_data.get("schema_version") != 1 or economy_data.get("schema_version") != 1:
        raise ValueError("Unsupported economy data schema")
    goods_rows = goods_data.get("goods")
    if not isinstance(goods_rows, list) or not 12 <= len(goods_rows) <= 20:
        raise ValueError("Goods catalog must define between 12 and 20 goods")
    goods = {}
    for row in goods_rows:
        if not isinstance(row, dict):
            raise ValueError("Goods entries must be objects")
        goods_id = row.get("id")
        if not isinstance(goods_id, str) or not goods_id or goods_id in goods:
            raise ValueError("Goods IDs must be unique nonempty strings")
        if (not isinstance(row.get("name"), str) or not row["name"]
                or not isinstance(row.get("category"), str) or not row["category"]
                or type(row.get("base_price_silver")) is not int or row["base_price_silver"] <= 0
                or type(row.get("unit_mass_kg_milli")) is not int or row["unit_mass_kg_milli"] <= 0
                or type(row.get("unit_volume_l_milli")) is not int or row["unit_volume_l_milli"] <= 0
                or type(row.get("perishable")) is not bool
                or type(row.get("consumption_per_1000_per_day_milli")) is not int
                or row["consumption_per_1000_per_day_milli"] < 0
                or not isinstance(row.get("uses"), list) or not row["uses"]
                or any(not isinstance(use, str) or not use for use in row["uses"])):
            raise ValueError(f"Invalid goods definition: {goods_id}")
        goods[goods_id] = row
    required_goods = {
        "grain", "fish", "water", "rum", "iron", "timber", "sailcloth",
        "powder", "medicine", "luxury_goods", "spices", "contraband", "rare_relics",
    }
    if not required_goods.issubset(goods):
        raise ValueError(f"Goods catalog is missing required IDs: {sorted(required_goods - set(goods))}")

    classes = economy_data.get("population_classes")
    profiles = economy_data.get("profiles")
    islands = economy_data.get("islands")
    rules = economy_data.get("market_rules")
    if (not isinstance(classes, list) or not classes
            or len(set(classes)) != len(classes)
            or any(not isinstance(item, str) or not item for item in classes)
            or not isinstance(profiles, dict) or not profiles
            or not isinstance(islands, list) or not islands
            or not isinstance(rules, dict)):
        raise ValueError("Economy catalog requires classes, profiles, islands, and market rules")
    required_rules = {
        "target_stock_per_1000_population", "price_smoothing_percent",
        "price_elasticity_milli", "minimum_price_multiplier_milli",
        "maximum_price_multiplier_milli", "starting_treasury_min_silver",
        "starting_silver_per_resident",
        "tax_basis_points_per_day", "garrison_upkeep_silver_per_10_strength_per_day",
        "port_upkeep_silver_per_day", "unrest_shortage_increase_per_1000_people",
        "unrest_recovery_per_day", "blockade_productivity_permille", "famine_threshold_days",
    }
    if not required_rules.issubset(rules):
        raise ValueError("Market rules are missing required fields")
    for key, value in rules.items():
        if type(value) is not int or value < 0:
            raise ValueError(f"Market rule must be a nonnegative integer: {key}")
    if (rules["price_smoothing_percent"] > 100
            or rules["minimum_price_multiplier_milli"] <= 0
            or rules["maximum_price_multiplier_milli"] < rules["minimum_price_multiplier_milli"]
            or rules["blockade_productivity_permille"] > 1000
            or rules["famine_threshold_days"] <= 0):
        raise ValueError("Market rule ranges are invalid")
    for profile_id, profile in profiles.items():
        if not isinstance(profile, dict):
            raise ValueError(f"Invalid island profile: {profile_id}")
        shares, production, stock = (profile.get(key) for key in
                                     ("population_classes_basis_points", "produces_per_day", "stockpile"))
        if not isinstance(shares, dict) or set(shares) != set(classes):
            raise ValueError(f"Population classes do not match for profile: {profile_id}")
        if any(type(share) is not int or share < 0 for share in shares.values()) or sum(shares.values()) != 10000:
            raise ValueError(f"Population class shares must sum to 10000: {profile_id}")
        if not isinstance(production, dict) or not isinstance(stock, dict):
            raise ValueError(f"Profile production and stockpile must be objects: {profile_id}")
        for goods_id, quantity in (*production.items(), *stock.items()):
            if goods_id not in goods or type(quantity) is not int or quantity < 0:
                raise ValueError(f"Invalid profile goods quantity: {profile_id}/{goods_id}")
        if (type(profile.get("harbor_tier")) is not int or not 0 <= profile["harbor_tier"] <= 5
                or type(profile.get("garrison_strength")) is not int or profile["garrison_strength"] < 0
                or not isinstance(profile.get("services"), list)
                or any(not isinstance(item, str) for item in profile["services"])):
            raise ValueError(f"Invalid service or harbor profile: {profile_id}")
    seen_islands = set()
    for row in islands:
        if not isinstance(row, dict):
            raise ValueError("Island economy rows must be objects")
        island_id, profile_id = row.get("island_id"), row.get("profile")
        if (not isinstance(island_id, str) or not island_id or island_id in seen_islands
                or profile_id not in profiles
                or type(row.get("population")) is not int or row["population"] <= 0
                or type(row.get("treasury_silver")) is not int or row["treasury_silver"] < 0
                or type(row.get("unrest")) is not int or not 0 <= row["unrest"] <= 100
                or type(row.get("prosperity")) is not int or not 0 <= row["prosperity"] <= 100
                or type(row.get("famine_level")) is not int or not 0 <= row["famine_level"] <= 100
                or type(row.get("tax_basis_points_per_day")) is not int
                or row["tax_basis_points_per_day"] < 0):
            raise ValueError(f"Invalid island economy row: {island_id}")
        seen_islands.add(island_id)
    if island_ids is not None and seen_islands != island_ids:
        raise ValueError("Economy island rows must cover the map exactly")
    merchants = economy_data.get("initial_merchants", [])
    if not isinstance(merchants, list):
        raise ValueError("Initial merchants must be a list")
    merchant_names = set()
    for row in merchants:
        if (not isinstance(row, dict) or not isinstance(row.get("name"), str)
                or not row["name"].strip() or row["name"] in merchant_names
                or (island_ids is not None and row.get("island_id") not in island_ids)
                or type(row.get("silver")) is not int or row["silver"] < 0
                or type(row.get("capacity_milli")) is not int or row["capacity_milli"] <= 0
                or type(row.get("travel_cost_silver_per_day")) is not int
                or row["travel_cost_silver_per_day"] < 0
                or type(row.get("risk_cost_basis_points")) is not int
                or not 0 <= row["risk_cost_basis_points"] <= 10000
                or type(row.get("risk_belief_ppm")) is not int
                or not 0 <= row["risk_belief_ppm"] <= 1000000):
            raise ValueError("Invalid initial merchant profile")
        merchant_names.add(row["name"])


class ConservationLedger:
    """Daily global balance proof for all economy-owned silver and goods."""

    def __init__(self, opening_silver: int, opening_goods: dict[str, int]) -> None:
        self.opening_silver = opening_silver
        self.silver_sources = 0
        self.silver_sinks = 0
        self.opening_goods = dict(opening_goods)
        self.goods_sources = {goods_id: 0 for goods_id in opening_goods}
        self.goods_sinks = {goods_id: 0 for goods_id in opening_goods}
        self.last_day = 0
        self.daily = []
        self.source_events: list[dict[str, Any]] = []
        self.sink_events: list[dict[str, Any]] = []
        self.flow_events: list[dict[str, Any]] = []
        self.assert_valid_shape(set(opening_goods))

    def record_source(self, event_id: str, *, silver: int = 0,
                      goods: dict[str, int] | None = None) -> None:
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("Ledger source requires a causal event ID")
        goods = goods or {}
        if type(silver) is not int or silver < 0 or any(
                goods_id not in self.goods_sources or type(quantity) is not int or quantity < 0
                for goods_id, quantity in goods.items()):
            raise ValueError("Invalid ledger source quantities")
        if silver == 0 and not any(goods.values()):
            return
        self.silver_sources += silver
        for goods_id, quantity in goods.items():
            self.goods_sources[goods_id] += quantity
        self.source_events.append({"event_id": event_id, "silver": silver,
                                   "goods": {key: goods[key] for key in sorted(goods) if goods[key]}})

    def record_sink(self, event_id: str, *, silver: int = 0,
                    goods: dict[str, int] | None = None) -> None:
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("Ledger sink requires a causal event ID")
        goods = goods or {}
        if type(silver) is not int or silver < 0 or any(
                goods_id not in self.goods_sinks or type(quantity) is not int or quantity < 0
                for goods_id, quantity in goods.items()):
            raise ValueError("Invalid ledger sink quantities")
        if silver == 0 and not any(goods.values()):
            return
        self.silver_sinks += silver
        for goods_id, quantity in goods.items():
            self.goods_sinks[goods_id] += quantity
        self.sink_events.append({"event_id": event_id, "silver": silver,
                                 "goods": {key: goods[key] for key in sorted(goods) if goods[key]}})

    def commit_day(self, day: int, economy, production: dict[str, int],
                   consumption: dict[str, int], flow_events: list[str]) -> None:
        if type(day) is not int or day != self.last_day + 1:
            raise ValueError("Ledger days must be committed consecutively")
        if set(production) != set(self.goods_sources) or set(consumption) != set(self.goods_sinks):
            raise ValueError("Daily goods flows must cover the catalog")
        if any(type(value) is not int or value < 0
               for value in (*production.values(), *consumption.values())):
            raise ValueError("Daily goods flows must be nonnegative integer milli-units")
        for goods_id in sorted(self.goods_sources):
            self.goods_sources[goods_id] += production[goods_id]
            self.goods_sinks[goods_id] += consumption[goods_id]
        self.last_day = day
        self.flow_events.append({"day": day, "event_ids": list(flow_events),
                                 "goods_sources": dict(production),
                                 "goods_sinks": dict(consumption)})
        self.daily.append({
            "day": day,
            "silver_total": economy._total_silver(),
            "silver_sources": self.silver_sources,
            "silver_sinks": self.silver_sinks,
            "goods_total": economy._total_goods(),
            "goods_sources": dict(self.goods_sources),
            "goods_sinks": dict(self.goods_sinks),
        })
        self.assert_balanced(economy, validate_history=False)

    def assert_balanced(self, economy, *, validate_history: bool = True) -> None:
        if validate_history:
            self.assert_valid_shape(set(economy.goods))
        if economy._total_silver() != self.opening_silver + self.silver_sources - self.silver_sinks:
            raise ValueError("Global silver conservation ledger does not balance")
        totals = economy._total_goods()
        for goods_id in self.opening_goods:
            expected = self.opening_goods[goods_id] + self.goods_sources[goods_id] - self.goods_sinks[goods_id]
            if totals[goods_id] != expected:
                raise ValueError(f"Global goods conservation ledger does not balance for {goods_id}")

    def assert_valid_shape(self, goods_ids: set[str]) -> None:
        if (type(self.opening_silver) is not int or self.opening_silver < 0
                or type(self.silver_sources) is not int or self.silver_sources < 0
                or type(self.silver_sinks) is not int or self.silver_sinks < 0
                or set(self.opening_goods) != goods_ids
                or set(self.goods_sources) != goods_ids or set(self.goods_sinks) != goods_ids
                or any(type(value) is not int or value < 0 for value in self.opening_goods.values())
                or any(type(value) is not int or value < 0 for value in self.goods_sources.values())
                or any(type(value) is not int or value < 0 for value in self.goods_sinks.values())
                or type(self.last_day) is not int or self.last_day < 0
                or not isinstance(self.daily, list) or len(self.daily) != self.last_day
                or not isinstance(self.source_events, list)
                or not isinstance(self.sink_events, list)
                or not isinstance(self.flow_events, list)
                or len(self.flow_events) != self.last_day):
            raise ValueError("Invalid conservation ledger state")
        for index, row in enumerate(self.daily, start=1):
            if (not isinstance(row, dict) or row.get("day") != index
                    or row.get("goods_total", {}).keys() != self.opening_goods.keys()
                    or row.get("goods_sources", {}).keys() != self.opening_goods.keys()
                    or row.get("goods_sinks", {}).keys() != self.opening_goods.keys()
                    or any(type(value) is not int or value < 0 for value in row.get("goods_total", {}).values())
                    or type(row.get("silver_total")) is not int or row["silver_total"] < 0):
                raise ValueError("Invalid daily conservation checkpoint")
            if (row["silver_total"] != self.opening_silver
                    + row.get("silver_sources", -1) - row.get("silver_sinks", -1)):
                raise ValueError("Daily silver checkpoint is not balanced")
            for goods_id in goods_ids:
                if (row["goods_total"][goods_id] != self.opening_goods[goods_id]
                        + row["goods_sources"][goods_id] - row["goods_sinks"][goods_id]):
                    raise ValueError(f"Daily goods checkpoint is not balanced for {goods_id}")
        for day, flow in enumerate(self.flow_events, start=1):
            if (not isinstance(flow, dict) or flow.get("day") != day
                    or not isinstance(flow.get("event_ids"), list)
                    or not flow["event_ids"]
                    or set(flow.get("goods_sources", {})) != goods_ids
                    or set(flow.get("goods_sinks", {})) != goods_ids):
                raise ValueError("Invalid daily conservation flow events")

    def snapshot(self) -> dict[str, Any]:
        return {
            "opening_silver": self.opening_silver,
            "silver_sources": self.silver_sources,
            "silver_sinks": self.silver_sinks,
            "opening_goods": dict(self.opening_goods),
            "goods_sources": dict(self.goods_sources),
            "goods_sinks": dict(self.goods_sinks),
            "last_day": self.last_day,
            "daily": _copy_json(self.daily),
            "source_events": _copy_json(self.source_events),
            "sink_events": _copy_json(self.sink_events),
            "flow_events": _copy_json(self.flow_events),
        }

    @classmethod
    def restore(cls, snapshot: dict[str, Any], current_silver: int,
                current_goods: dict[str, int], goods_ids: set[str]) -> "ConservationLedger":
        required = {"opening_silver", "silver_sources", "silver_sinks", "opening_goods",
                    "goods_sources", "goods_sinks", "last_day", "daily", "source_events",
                    "sink_events", "flow_events"}
        if not isinstance(snapshot, dict) or set(snapshot) != required:
            raise ValueError("Invalid conservation ledger snapshot")
        ledger = cls.__new__(cls)
        for name in required:
            setattr(ledger, name, _copy_json(snapshot[name]))
        ledger.assert_valid_shape(goods_ids)
        ledger.assert_balanced_current(current_silver, current_goods)
        return ledger

    def assert_balanced_current(self, silver: int, goods: dict[str, int]) -> None:
        if silver != self.opening_silver + self.silver_sources - self.silver_sinks:
            raise ValueError("Global silver conservation ledger does not balance")
        for goods_id in self.opening_goods:
            expected = self.opening_goods[goods_id] + self.goods_sources[goods_id] - self.goods_sinks[goods_id]
            if goods.get(goods_id) != expected:
                raise ValueError(f"Global goods conservation ledger does not balance for {goods_id}")


class EconomySystem:
    """Owns island economic state; quantities are integer milli-units."""

    def __init__(self, geography, islands: dict[str, dict[str, Any]], goods_data: dict[str, Any],
                 economy_data: dict[str, Any], last_updated_day: int = 0,
                 merchants: dict[str, dict[str, Any]] | None = None,
                 ledger_state: dict[str, Any] | None = None,
                 blockades: dict[str, dict[str, str]] | None = None,
                 ship_supplies: dict[str, dict[str, int]] | None = None,
                 crew_accounts: dict[str, dict[str, Any]] | None = None,
                 government_silver: int = 0,
                 government_funded_silver: int = 0) -> None:
        self.geography = geography
        self.goods_data = goods_data
        self.economy_data = economy_data
        self.goods = {row["id"]: row for row in goods_data["goods"]}
        self.rules = economy_data["market_rules"]
        self.islands = {island_id: _copy_json(state) for island_id, state in islands.items()}
        self.merchants = {merchant_id: _copy_json(state)
                          for merchant_id, state in (merchants or {}).items()}
        self.ship_supplies = {ship_id: _copy_json(state)
                              for ship_id, state in (ship_supplies or {}).items()}
        self.crew_accounts = {crew_id: _copy_json(state)
                              for crew_id, state in (crew_accounts or {}).items()}
        self.government_silver = government_silver
        self.government_funded_silver = government_funded_silver
        self.pending_ship_consumption = {goods_id: 0 for goods_id in self.goods}
        self.pending_ship_events: list[str] = []
        self.last_updated_day = last_updated_day
        self.digest = hashlib.sha256(json.dumps(
            {"goods": goods_data, "economy": economy_data}, sort_keys=True,
            ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()
        self.ledger = ConservationLedger.restore(
            ledger_state, self._total_silver(), self._total_goods(), set(self.goods)
        ) if ledger_state is not None else ConservationLedger(
            self._total_silver(), self._total_goods())
        self.blockades = {route_id: _copy_json(row)
                          for route_id, row in (blockades or {}).items()}
        # Ephemeral: decisions are reproducible from world state, while repeated
        # route scoring in the same day can share identical vessel/route physics.
        self._route_duration_cache_tick: int | None = None
        self._route_duration_cache: dict[tuple[Any, ...], int] = {}
        self._path_duration_cache: dict[tuple[Any, ...], int] = {}
        self._path_risk_cache: dict[tuple[Any, ...], int] = {}
        self._region_speed_cache: dict[str, int] = {}
        self.validate_state()

    @classmethod
    def create(cls, geography, goods_path: str | Path = GOODS_PATH,
               economy_path: str | Path = ECONOMY_PATH) -> "EconomySystem":
        goods_data, economy_data = load_economy_data(goods_path, economy_path)
        validate_economy_data(goods_data, economy_data, set(geography.islands))
        states = {}
        for row in sorted(economy_data["islands"], key=lambda item: item["island_id"]):
            profile = economy_data["profiles"][row["profile"]]
            population = row["population"]
            class_counts = {
                class_id: population * share // 10000
                for class_id, share in profile["population_classes_basis_points"].items()
            }
            # Deterministic remainder allocation preserves the island population exactly.
            remainder = population - sum(class_counts.values())
            for class_id in sorted(class_counts)[:remainder]:
                class_counts[class_id] += 1
            stockpiles = {goods_id: profile["stockpile"].get(goods_id, 0) * QUANTITY_SCALE
                          for goods_id in sorted(
                              {row["id"] for row in goods_data["goods"]})}
            states[row["island_id"]] = {
                "id": row["island_id"], "kind": "island",
                "region_id": geography.islands[row["island_id"]].region_id,
                "population": population, "population_classes": class_counts,
                "treasury_silver": row["treasury_silver"],
                "population_silver": population * economy_data["market_rules"][
                    "starting_silver_per_resident"],
                "garrison_silver": 0,
                "government_response_silver": 0,
                "port_service_silver": 0,
                "unrest": row["unrest"], "prosperity": row["prosperity"],
                "famine_level": row["famine_level"], "consecutive_shortage_days": 0,
                "harvest_yield_permille": 1000, "disease_pressure_permille": 0,
                "blockaded_routes": [],
                "profile": row["profile"],
                "produces_per_day": dict(profile["produces_per_day"]),
                "stockpile_milli": stockpiles,
                "prices_milli": {goods_id: goods["base_price_silver"] * PRICE_SCALE
                                 for goods_id, goods in sorted(
                                     {row["id"]: row for row in goods_data["goods"]}.items())},
                "services": list(profile["services"]),
                "harbor_tier": profile["harbor_tier"],
                "garrison_strength": profile["garrison_strength"],
                "tax_basis_points_per_day": row["tax_basis_points_per_day"],
                "last_economy_event_id": "event_000001",
            }
        return cls(geography, states, goods_data, economy_data)

    def validate_state(self) -> None:
        if set(self.islands) != set(self.geography.islands):
            raise ValueError("Economy state must cover every map island exactly")
        if type(self.last_updated_day) is not int or self.last_updated_day < 0:
            raise ValueError("Economy day must be a nonnegative integer")
        if (type(self.government_silver) is not int or self.government_silver < 0
                or type(self.government_funded_silver) is not int
                or self.government_funded_silver < self.government_silver):
            raise ValueError("Invalid government response treasury")
        for island_id, state in self.islands.items():
            if (state.get("id") != island_id or state.get("kind") != "island"
                    or state.get("region_id") != self.geography.islands[island_id].region_id
                    or type(state.get("population")) is not int or state["population"] < 0
                    or sum(state.get("population_classes", {}).values()) != state["population"]
                    or type(state.get("treasury_silver")) is not int or state["treasury_silver"] < 0
                    or type(state.get("population_silver")) is not int
                    or state["population_silver"] < 0
                    or type(state.get("garrison_silver")) is not int or state["garrison_silver"] < 0
                    or type(state.get("government_response_silver")) is not int
                    or state["government_response_silver"] < 0
                    or type(state.get("port_service_silver")) is not int
                    or state["port_service_silver"] < 0
                    or type(state.get("unrest")) is not int or not 0 <= state["unrest"] <= 100
                    or type(state.get("prosperity")) is not int or not 0 <= state["prosperity"] <= 100
                    or type(state.get("famine_level")) is not int or not 0 <= state["famine_level"] <= 100
                    or type(state.get("consecutive_shortage_days")) is not int
                    or state["consecutive_shortage_days"] < 0
                    or type(state.get("harvest_yield_permille")) is not int
                    or not 1 <= state["harvest_yield_permille"] <= 2000
                    or type(state.get("disease_pressure_permille")) is not int
                    or not 0 <= state["disease_pressure_permille"] <= 1000
                    or not isinstance(state.get("blockaded_routes"), list)
                    or any(route_id not in self.geography.routes
                           for route_id in state["blockaded_routes"])
                    or state.get("profile") not in self.economy_data["profiles"]):
                raise ValueError(f"Invalid island economy state: {island_id}")
            for field in ("stockpile_milli", "prices_milli"):
                values = state.get(field)
                if not isinstance(values, dict) or set(values) != set(self.goods):
                    raise ValueError(f"Invalid {field} for island {island_id}")
                if any(type(value) is not int or value < (1 if field == "prices_milli" else 0)
                       for value in values.values()):
                    raise ValueError(f"Invalid numeric {field} for island {island_id}")
            if set(state.get("population_classes", {})) != set(self.economy_data["population_classes"]):
                raise ValueError(f"Invalid population classes for island {island_id}")
            if any(type(count) is not int or count < 0
                   for count in state["population_classes"].values()):
                raise ValueError(f"Invalid population class count for island {island_id}")
        for merchant_id, merchant in self.merchants.items():
            if (not isinstance(merchant_id, str) or not merchant_id
                    or merchant.get("id") != merchant_id
                    or not isinstance(merchant.get("name"), str) or not merchant["name"]
                    or not isinstance(merchant.get("ship_id"), str)
                    or type(merchant.get("silver")) is not int or merchant["silver"] < 0
                    or type(merchant.get("capacity_milli")) is not int
                    or merchant["capacity_milli"] <= 0
                    or merchant.get("home_island") not in self.islands
                    or type(merchant.get("last_route_attempt_day")) is not int
                    or merchant["last_route_attempt_day"] < -7):
                raise ValueError(f"Invalid merchant state: {merchant_id}")
            cargo, basis = merchant.get("cargo_milli"), merchant.get("cargo_basis_silver")
            if (not isinstance(cargo, dict) or set(cargo) != set(self.goods)
                    or any(type(value) is not int or value < 0 for value in cargo.values())
                    or sum(cargo.values()) > merchant["capacity_milli"]
                    or not isinstance(basis, dict) or set(basis) != set(self.goods)
                    or any(type(value) is not int or value < 0 for value in basis.values())):
                raise ValueError(f"Invalid merchant cargo: {merchant_id}")
            if (not isinstance(merchant.get("risk_beliefs_ppm"), dict)
                    or any(route_id not in self.geography.routes or type(value) is not int
                           or not 0 <= value <= 1_000_000
                           for route_id, value in merchant["risk_beliefs_ppm"].items())
                    or type(merchant.get("travel_cost_silver_per_day")) is not int
                    or merchant["travel_cost_silver_per_day"] < 0
                    or type(merchant.get("risk_cost_basis_points")) is not int
                    or not 0 <= merchant["risk_cost_basis_points"] <= 10_000):
                raise ValueError(f"Invalid merchant beliefs or costs: {merchant_id}")
        for ship_id, supplies in self.ship_supplies.items():
            if (not isinstance(ship_id, str) or not ship_id
                    or not isinstance(supplies, dict) or set(supplies) != set(self.goods)
                    or any(type(quantity) is not int or quantity < 0
                           for quantity in supplies.values())):
                raise ValueError(f"Invalid ship supply account: {ship_id}")
        for crew_id, account in self.crew_accounts.items():
            if (not isinstance(crew_id, str) or not crew_id or not isinstance(account, dict)
                    or set(account) != {"treasury_silver", "member_wallets"}
                    or type(account["treasury_silver"]) is not int or account["treasury_silver"] < 0
                    or not isinstance(account["member_wallets"], dict)
                    or any(not isinstance(member_id, str) or not member_id
                           or type(balance) is not int or balance < 0
                           for member_id, balance in account["member_wallets"].items())):
                raise ValueError(f"Invalid crew silver account: {crew_id}")
        if self.ledger.last_day != self.last_updated_day:
            raise ValueError("Economy and conservation ledger day counters disagree")
        self.ledger.assert_balanced(self)
        if any(route_id not in self.geography.routes
               or not isinstance(row, dict)
               or set(row) != {"island_id", "event_id"}
               or row.get("island_id") not in self.islands
               for route_id, row in self.blockades.items()):
            raise ValueError("Invalid route blockade state")
        for island_id, state in self.islands.items():
            expected = sorted(route_id for route_id, row in self.blockades.items()
                              if row["island_id"] == island_id)
            if state["blockaded_routes"] != expected:
                raise ValueError(f"Blockaded route state disagrees for island {island_id}")

    def update_day(self, day: int, world) -> list[dict[str, Any]]:
        if type(day) is not int or day != self.last_updated_day + 1:
            raise ValueError("Economy must update one consecutive day at a time")
        changes = []
        day_events = []
        production_by_good = {goods_id: 0 for goods_id in self.goods}
        consumption_by_good = {goods_id: 0 for goods_id in self.goods}
        for island_id in sorted(self.islands):
            state = self.islands[island_id]
            prior_event = state["last_economy_event_id"]
            production = self._produce(state)
            consumption, shortages = self._consume(state)
            for goods_id, quantity in production.items():
                production_by_good[goods_id] += quantity
            for goods_id, quantity in consumption.items():
                consumption_by_good[goods_id] += quantity
            self._update_social_state(state, shortages)
            price_changes = self._update_prices(state)
            treasury = self._update_treasury(state)
            metrics = {
                "day": day,
                "production_milli": production,
                "consumption_milli": consumption,
                "shortage_milli": shortages,
                "price_changes_milli": price_changes,
                "tax_income_silver": treasury[0],
                "tax_collected_silver": treasury[1],
                "upkeep_paid_silver": treasury[2],
                "famine_level": state["famine_level"],
                "unrest": state["unrest"],
                "treasury_silver": state["treasury_silver"],
                "population_silver": state["population_silver"],
                "garrison_silver": state["garrison_silver"],
                "port_service_silver": state["port_service_silver"],
                "prosperity": state["prosperity"],
            }
            event = world.event_log.append(
                tick=day * (world.config["time"]["hours_per_day"]
                            // world.config["time"]["hours_per_tick"]),
                event_type="ISLAND_ECONOMY_UPDATED",
                location=island_id,
                targets=[{"role": "island", "entity_id": island_id}],
                data=metrics,
                caused_by=[prior_event],
                causes_tags=["daily_economy", f"profile:{state['profile']}"],
                magnitude=min(1.0, max(0.05, sum(shortages.values()) / 10_000_000)),
            )
            state["last_economy_event_id"] = event.id
            day_events.append(event)
            changes.append({"island_id": island_id, **metrics})
        self.last_updated_day = day
        for goods_id, quantity in self.pending_ship_consumption.items():
            consumption_by_good[goods_id] += quantity
        self.ledger.commit_day(day, self, production_by_good, consumption_by_good,
                               [event.id for event in day_events] + self.pending_ship_events)
        self.pending_ship_consumption = {goods_id: 0 for goods_id in self.goods}
        self.pending_ship_events = []
        self._make_merchant_decisions(world)
        return changes

    def emit_initial_events(self, world) -> None:
        """Record finite world-generation endowments as structured child events."""
        if self.last_updated_day != 0:
            raise ValueError("Initial economy events must be emitted before the first day")
        for island_id in sorted(self.islands):
            state = self.islands[island_id]
            event = world.event_log.append(
                tick=0,
                event_type="ISLAND_ECONOMY_INITIALIZED",
                location=island_id,
                targets=[{"role": "island", "entity_id": island_id}],
                data={
                    "population": state["population"],
                    "population_silver": state["population_silver"],
                    "treasury_silver": state["treasury_silver"],
                    "stockpile_milli": state["stockpile_milli"],
                    "starting_source": "world_generation_endowment",
                },
                caused_by=["event_000001"],
                causes_tags=["finite_island_endowment"],
                magnitude=0.1,
            )
            state["last_economy_event_id"] = event.id

    def _total_silver(self) -> int:
        return sum(state["treasury_silver"] + state["population_silver"]
                   + state["garrison_silver"] + state["government_response_silver"]
                   + state["port_service_silver"]
                   for state in self.islands.values()) + sum(
                        merchant["silver"] for merchant in self.merchants.values()) + sum(
                        account["treasury_silver"] + sum(account["member_wallets"].values())
                         for account in self.crew_accounts.values()) + self.government_silver

    def fund_government(self, world, amount: int, *, island_id: str, caused_by: str) -> str:
        if (type(amount) is not int or amount <= 0 or island_id not in self.islands
                or amount > self.islands[island_id]["treasury_silver"]):
            raise ValueError("Government funding must be a positive transfer from island treasury")
        self.islands[island_id]["treasury_silver"] -= amount
        self.government_silver += amount
        self.government_funded_silver += amount
        event = world.event_log.append(
            tick=world.clock.tick, event_type="GOVERNMENT_BUDGET_FUNDED", location=island_id,
            targets=[{"role": "government", "entity_id": world.politics.marine_command[
                "faction_id"]}],
            data={"silver": amount, "source_island": island_id,
                  "government_balance": self.government_silver},
            caused_by=[caused_by], causes_tags=["government_budget", "silver_transfer"],
            magnitude=0.2,
        )
        return event.id

    def pay_government_bounty(self, world, crew_id: str, amount: int, *, caused_by: str,
                              target_id: str) -> str:
        account = self.crew_accounts.get(crew_id)
        warrant = world.politics.crew_bounties.get(target_id)
        if (account is None or type(amount) is not int or amount <= 0
                or amount > self.government_silver or not warrant
                or amount > warrant["amount"]):
            raise ValueError("Government bounty exceeds available response funds")
        cause = world.event_log.get(caused_by)
        if cause.tick > world.clock.tick:
            raise ValueError("Government bounty cannot cite a future cause")
        self.government_silver -= amount
        account["treasury_silver"] += amount
        if crew_id in world.crew_ships.crews:
            world.crew_ships.crews[crew_id]["treasury_silver"] = account["treasury_silver"]
        event = world.event_log.append(
            tick=world.clock.tick, event_type="BOUNTY_PAID", location=None,
            actors=[{"role": "government", "entity_id": world.politics.marine_command["faction_id"]}],
            targets=[{"role": "beneficiary_crew", "entity_id": crew_id},
                     {"role": "bounty_target", "entity_id": target_id}],
            data={"silver": amount, "reason": "government_bounty_claim",
                  "government_balance": self.government_silver},
            caused_by=[caused_by], causes_tags=["bounty", "finite_government_transfer"],
            magnitude=0.3,
        )
        return event.id

    def spend_government_response(self, world, island_id: str, amount: int, *,
                                  caused_by: str, target_crew_id: str, response: str) -> str:
        if (island_id not in self.islands or type(amount) is not int or amount <= 0
                or amount > self.government_silver
                or target_crew_id not in world.crew_ships.crews
                or response not in {"patrol", "hunter", "fleet"}):
            raise ValueError("Government response spend exceeds available funds or has invalid target")
        cause = world.event_log.get(caused_by)
        if cause.tick > world.clock.tick:
            raise ValueError("Government response cannot cite a future cause")
        self.government_silver -= amount
        self.islands[island_id]["government_response_silver"] += amount
        event = world.event_log.append(
            tick=world.clock.tick, event_type="GOVERNMENT_RESPONSE_FUNDED", location=island_id,
            actors=[{"role": "government", "entity_id": world.politics.marine_command[
                "faction_id"]}], targets=[{"role": "threatened_crew", "entity_id": target_crew_id}],
            data={"silver": amount, "response": response, "island_id": island_id,
                  "government_balance": self.government_silver},
            caused_by=[caused_by], causes_tags=["government_response", response,
                                                "finite_government_transfer"],
            magnitude=0.4,
        )
        return event.id

    def fund_crew_account(self, world, crew_id: str, merchant_id: str,
                          member_ids: list[str], amount: int, *, caused_by: str) -> tuple[str, int]:
        if crew_id in self.crew_accounts or merchant_id not in self.merchants:
            raise ValueError("Crew account already exists or funding merchant is unknown")
        if type(amount) is not int or amount < 0 or len(set(member_ids)) != len(member_ids):
            raise ValueError("Invalid crew funding amount or roster")
        self._validate_cause(world, caused_by)
        merchant = self.merchants[merchant_id]
        funded = min(amount, merchant["silver"])
        merchant["silver"] -= funded
        self.crew_accounts[crew_id] = {
            "treasury_silver": funded,
            "member_wallets": {member_id: 0 for member_id in member_ids},
        }
        event = world.event_log.append(
            tick=world.clock.tick, event_type="CREW_FUNDED",
            location=world.entities[merchant["ship_id"]].get("location", {}).get("id"),
            targets=[{"role": "crew", "entity_id": crew_id}],
            data={"crew_id": crew_id, "merchant_id": merchant_id,
                  "requested_silver": amount, "funded_silver": funded},
            caused_by=[caused_by], causes_tags=["crew_treasury", "finite_transfer"], magnitude=0.1,
        )
        return event.id, funded

    @staticmethod
    def _market_liquidity(market: dict[str, Any]) -> int:
        """Finite local buying power across treasury and resident/service purses."""
        return sum(market[key] for key in (
            "treasury_silver", "population_silver", "garrison_silver", "port_service_silver"))

    @staticmethod
    def _pay_market_order(market: dict[str, Any], amount: int) -> dict[str, int]:
        """Move finite local funds to a seller, spending treasury before household cash."""
        remaining = amount
        debits = {}
        for account in ("treasury_silver", "port_service_silver", "garrison_silver",
                        "population_silver"):
            paid = min(market[account], remaining)
            market[account] -= paid
            remaining -= paid
            if paid:
                debits[account] = paid
        if remaining:
            raise ValueError("Island market cannot afford this order")
        return debits

    def _total_goods(self) -> dict[str, int]:
        totals = {goods_id: 0 for goods_id in self.goods}
        for state in self.islands.values():
            for goods_id, quantity in state["stockpile_milli"].items():
                totals[goods_id] += quantity
        for merchant in self.merchants.values():
            for goods_id, quantity in merchant["cargo_milli"].items():
                totals[goods_id] += quantity
        for supplies in self.ship_supplies.values():
            for goods_id, quantity in supplies.items():
                totals[goods_id] += quantity
        return totals

    def record_ship_supply_transfer(self, world, ship_id: str, island_id: str,
                                    supplies: dict[str, int], *, caused_by: str) -> str:
        if ship_id in self.ship_supplies or ship_id not in world.entities:
            raise ValueError("Ship supply account is duplicate or references an unknown ship")
        if island_id not in self.islands or set(supplies) - set(self.goods):
            raise ValueError("Invalid ship supply transfer endpoints or goods")
        if any(type(quantity) is not int or quantity < 0 for quantity in supplies.values()):
            raise ValueError("Ship supply quantities must be nonnegative integer milli-units")
        self._validate_cause(world, caused_by)
        market = self.islands[island_id]
        account = {goods_id: 0 for goods_id in self.goods}
        for goods_id, quantity in supplies.items():
            if quantity > market["stockpile_milli"][goods_id]:
                raise ValueError(f"Island lacks {goods_id} for ship provisioning")
            market["stockpile_milli"][goods_id] -= quantity
            account[goods_id] = quantity
        event = world.event_log.append(
            tick=world.clock.tick, event_type="SHIP_SUPPLIED", location=island_id,
            targets=[{"role": "supplied_ship", "entity_id": ship_id}],
            data={"ship_id": ship_id, "supplies_milli": account},
            caused_by=[caused_by], causes_tags=["ship_provisioning"], magnitude=0.1,
        )
        self.ship_supplies[ship_id] = account
        market["last_economy_event_id"] = event.id
        return event.id

    def consume_ship_supplies(self, world, ship_id: str, crew_id: str,
                              requested: dict[str, int], *, caused_by: str,
                              day: int) -> str:
        if ship_id not in self.ship_supplies:
            raise ValueError(f"Unknown ship supply account: {ship_id}")
        supply = self.ship_supplies[ship_id]
        consumed = {goods_id: 0 for goods_id in self.goods}
        shortage = {goods_id: 0 for goods_id in self.goods}
        for goods_id, quantity in requested.items():
            if goods_id not in self.goods or type(quantity) is not int or quantity < 0:
                raise ValueError("Invalid daily ship supply demand")
            used = min(supply[goods_id], quantity)
            supply[goods_id] -= used
            consumed[goods_id] = used
            shortage[goods_id] = quantity - used
            self.pending_ship_consumption[goods_id] += used
        event = world.event_log.append(
            tick=day * (world.config["time"]["hours_per_day"]
                        // world.config["time"]["hours_per_tick"]),
            event_type="SHIP_SUPPLIES_CONSUMED",
            location=world.entities[ship_id].get("location", {}).get("id"),
            actors=[{"role": "crew", "entity_id": crew_id}],
            targets=[{"role": "ship", "entity_id": ship_id}],
            data={"ship_id": ship_id, "crew_id": crew_id,
                  "consumed_milli": consumed, "shortage_milli": shortage},
            caused_by=[caused_by], causes_tags=["ship_operations", "daily_provisions"],
            magnitude=0.05 if not any(shortage.values()) else 0.35,
        )
        self.pending_ship_events.append(event.id)
        return event.id

    def _record_merchant_endowment(self, event_id: str, silver: int,
                                   cargo: dict[str, int]) -> None:
        self.ledger.record_source(event_id, silver=silver, goods=cargo)
        self.ledger.assert_balanced(self, validate_history=False)

    def create_merchant(self, world, name: str, island_id: str, *, silver: int,
                        capacity_milli: int, cargo_milli: dict[str, int] | None = None,
                        risk_beliefs_ppm: dict[str, int] | None = None,
                        travel_cost_silver_per_day: int = 12,
                        risk_cost_basis_points: int = 5000) -> tuple[str, str]:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Merchant name must be nonempty")
        if any(state["name"] == name.strip() for state in self.merchants.values()):
            raise ValueError(f"Merchant name is already in use: {name.strip()}")
        if island_id not in self.islands:
            raise ValueError(f"Unknown merchant home island: {island_id}")
        if any(type(value) is not int or value < 0 for value in
               (silver, capacity_milli, travel_cost_silver_per_day, risk_cost_basis_points)):
            raise ValueError("Merchant money, capacity, and costs must be nonnegative integers")
        if capacity_milli <= 0 or risk_cost_basis_points > 10000:
            raise ValueError("Merchant capacity must be positive and risk cost at most 10000bp")
        cargo = {goods_id: 0 for goods_id in self.goods}
        for goods_id, quantity in (cargo_milli or {}).items():
            if goods_id not in cargo or type(quantity) is not int or quantity < 0:
                raise ValueError(f"Invalid merchant cargo: {goods_id}")
            cargo[goods_id] = quantity
        if sum(cargo.values()) > capacity_milli:
            raise ValueError("Starting cargo exceeds merchant hold capacity")
        beliefs = {}
        for route_id, probability in (risk_beliefs_ppm or {}).items():
            if (route_id not in self.geography.routes or type(probability) is not int
                    or not 0 <= probability <= 1_000_000):
                raise ValueError(f"Invalid route-risk belief: {route_id}")
            beliefs[route_id] = probability

        ship_id, ship_event = world.create_vessel(
            f"{name.strip()}'s ship", island_id, navigation=55)
        world.entities[ship_id]["cargo"] = dict(cargo)
        world.entities[ship_id]["cargo_capacity_milli"] = capacity_milli
        merchant_id = world.ids.next("merchant")
        event = world.event_log.append(
            tick=world.clock.tick,
            event_type="MERCHANT_CREATED",
            location=island_id,
            targets=[{"role": "merchant", "entity_id": merchant_id}],
            data={"name": name.strip(), "ship_id": ship_id, "starting_silver": silver,
                  "starting_cargo_milli": cargo, "capacity_milli": capacity_milli,
                  "starting_source": "merchant_endowment"},
            caused_by=[ship_event],
            causes_tags=["finite_merchant_creation"],
            magnitude=0.1,
        )
        self.merchants[merchant_id] = {
            "id": merchant_id, "name": name.strip(), "ship_id": ship_id,
            "silver": silver, "capacity_milli": capacity_milli,
            "cargo_milli": cargo,
            "cargo_basis_silver": {goods_id: 0 for goods_id in self.goods},
            "risk_beliefs_ppm": beliefs,
            "travel_cost_silver_per_day": travel_cost_silver_per_day,
            "risk_cost_basis_points": risk_cost_basis_points,
            "last_event_id": event.id,
            "last_route_attempt_day": -7,
            "home_island": island_id,
        }
        world.assign_ship_title(
            ship_id, owner_kind="merchant", owner_id=merchant_id,
            controller_crew_id=None, event_id=ship_event)
        self._record_merchant_endowment(event.id, silver, cargo)
        return merchant_id, event.id

    def blockade_route(self, world, route_id: str, target_island: str, active: bool, *,
                       caused_by: str) -> str:
        if route_id not in self.geography.routes:
            raise ValueError(f"Unknown route: {route_id}")
        route = self.geography.routes[route_id]
        if target_island not in (route.origin, route.destination):
            raise ValueError("Blockade target must be an endpoint of the route")
        if type(active) is not bool:
            raise ValueError("Blockade active flag must be boolean")
        self._validate_cause(world, caused_by)
        if active and route_id in self.blockades:
            raise ValueError("Route is already blockaded")
        if not active and (route_id not in self.blockades
                           or self.blockades[route_id]["island_id"] != target_island):
            raise ValueError("Route has no matching blockade to lift")
        event_type = "ROUTE_BLOCKADED" if active else "ROUTE_BLOCKADE_LIFTED"
        event = world.event_log.append(
            tick=world.clock.tick,
            event_type=event_type,
            location=route_id,
            targets=[{"role": "blockaded_island", "entity_id": target_island}],
            data={"route_id": route_id, "target_island": target_island,
                  "productivity_permille": self.rules["blockade_productivity_permille"]
                  if active else 1000},
            caused_by=[caused_by],
            causes_tags=["route_blockade", "trade_disruption"],
            magnitude=0.5,
        )
        if active:
            self.blockades[route_id] = {"island_id": target_island, "event_id": event.id}
            world.travel.closed_routes.add(route_id)
            self.islands[target_island]["blockaded_routes"].append(route_id)
            self.islands[target_island]["blockaded_routes"].sort()
        else:
            del self.blockades[route_id]
            world.travel.closed_routes.remove(route_id)
            self.islands[target_island]["blockaded_routes"].remove(route_id)
        self.islands[target_island]["last_economy_event_id"] = event.id
        return event.id

    def trade(self, world, merchant_id: str, island_id: str, goods_id: str,
              side: str, quantity_milli: int, *, caused_by: str) -> dict[str, Any]:
        merchant = self._merchant(merchant_id)
        if island_id not in self.islands or goods_id not in self.goods:
            raise ValueError("Trade requires a known island and good")
        if side not in {"buy", "sell"}:
            raise ValueError("Trade side must be 'buy' or 'sell'")
        if type(quantity_milli) is not int or quantity_milli <= 0:
            raise ValueError("Trade quantity must be a positive integer milli-unit count")
        self._validate_cause(world, caused_by)
        ship_id = merchant["ship_id"]
        ship = world.entities.get(ship_id)
        if (not isinstance(ship_id, str) or not ship or ship.get("kind") != "ship"
                or ship_id in world.archive):
            raise ValueError("Merchant must have an active ship to trade")
        if (not ship_is_commandable(world, ship_id)
                or ship.get("legal_owner") != {"kind": "merchant", "id": merchant_id}):
            raise ValueError("Merchant no longer owns or commands this vessel")
        if ship_id in world.travel.transits:
            raise ValueError("Merchant must be berthed to trade")
        location = ship.get("location", {})
        if location.get("kind") != "island" or location.get("id") != island_id:
            raise ValueError("Merchant and market must be at the same island")

        market = self.islands[island_id]
        price_milli = market["prices_milli"][goods_id]
        stock_before = market["stockpile_milli"][goods_id]
        cash_before = merchant["silver"]
        cargo_before = merchant["cargo_milli"][goods_id]
        local_cash_debits = {}
        if side == "buy":
            # Merchant buys from the island market.
            capacity_left = merchant["capacity_milli"] - sum(merchant["cargo_milli"].values())
            affordable = cash_before * VALUE_SCALE // price_milli
            quantity = min(quantity_milli, stock_before, capacity_left, affordable)
            if quantity <= 0:
                raise ValueError("Market order cannot fill from finite stock, cash, or hold capacity")
            cost = _ceil_div(quantity * price_milli, VALUE_SCALE)
            market["stockpile_milli"][goods_id] -= quantity
            merchant["cargo_milli"][goods_id] += quantity
            merchant["silver"] -= cost
            market["treasury_silver"] += cost
            merchant["cargo_basis_silver"][goods_id] += cost
            buyer_id, seller_id = merchant_id, island_id
            total_silver = cost
        else:
            # Merchant sells to the island; the island treasury is the finite buyer.
            available = merchant["cargo_milli"][goods_id]
            max_affordable = self._market_liquidity(market) * VALUE_SCALE // price_milli
            quantity = min(quantity_milli, available, max_affordable)
            if quantity <= 0:
                raise ValueError("Market order cannot fill from finite cargo or island treasury")
            total_silver = _ceil_div(quantity * price_milli, VALUE_SCALE)
            if total_silver > self._market_liquidity(market):
                quantity = self._market_liquidity(market) * VALUE_SCALE // price_milli
                if quantity <= 0:
                    raise ValueError("Island market cannot afford this order")
                total_silver = _ceil_div(quantity * price_milli, VALUE_SCALE)
            basis = merchant["cargo_basis_silver"][goods_id]
            basis_sold = basis * quantity // max(1, available)
            merchant["cargo_milli"][goods_id] -= quantity
            merchant["cargo_basis_silver"][goods_id] -= basis_sold
            market["stockpile_milli"][goods_id] += quantity
            local_cash_debits = self._pay_market_order(market, total_silver)
            merchant["silver"] += total_silver
            buyer_id, seller_id = island_id, merchant_id

        if isinstance(ship.get("cargo"), dict):
            ship["cargo"] = dict(merchant["cargo_milli"])

        event = world.event_log.append(
            tick=world.clock.tick,
            event_type="MARKET_TRADE",
            location=island_id,
            actors=[{"role": "buyer", "entity_id": buyer_id},
                    {"role": "seller", "entity_id": seller_id}],
            data={"merchant_id": merchant_id, "island_id": island_id,
                  "goods_id": goods_id, "side": side, "quantity_milli": quantity,
                  "price_milli": price_milli, "silver_transferred": total_silver,
                  "island_account_debits": local_cash_debits,
                  "stock_before_milli": stock_before,
                  "stock_after_milli": market["stockpile_milli"][goods_id],
                  "merchant_cash_before": cash_before,
                  "merchant_cash_after": merchant["silver"],
                  "merchant_cargo_before_milli": cargo_before,
                  "merchant_cargo_after_milli": merchant["cargo_milli"][goods_id]},
            caused_by=[caused_by],
            causes_tags=["market_order", f"good:{goods_id}", f"side:{side}"],
            magnitude=min(1.0, total_silver / 10000),
        )
        merchant["last_event_id"] = event.id
        market["last_economy_event_id"] = event.id
        self.ledger.assert_balanced(self, validate_history=False)
        return event.to_record()

    def best_trade_route(self, world, merchant_id: str) -> dict[str, Any] | None:
        self._prepare_route_cache(world)
        merchant = self._merchant(merchant_id)
        ship_id = merchant["ship_id"]
        ship = world.entities.get(ship_id)
        if (not ship or not ship_is_commandable(world, ship_id)
                or ship.get("legal_owner") != {"kind": "merchant", "id": merchant_id}):
            return None
        if ship_id in world.travel.transits:
            return None
        location = world.entities[ship_id].get("location", {})
        if location.get("kind") != "island":
            return None
        origin = location["id"]
        best = None
        best_key = None
        cargo_capacity = merchant["capacity_milli"] - sum(merchant["cargo_milli"].values())
        source = self.islands[origin]
        source_prices = source["prices_milli"]
        source_stock = source["stockpile_milli"]
        blocked_routes = frozenset(self.blockades)
        destination_liquidity = {
            island_id: self._market_liquidity(market)
            for island_id, market in self.islands.items()
        }
        for destination in sorted(self.islands):
            if destination == origin:
                continue
            destination_market = self.islands[destination]
            buyer_cash = destination_liquidity[destination]
            if buyer_cash <= 0:
                continue
            trade_options = []
            for goods_id in sorted(self.goods):
                available = merchant["cargo_milli"][goods_id]
                basis = merchant["cargo_basis_silver"][goods_id]
                buy_quantity = 0
                buy_cost = 0
                if available == 0:
                    buy_price = source_prices[goods_id]
                    if source_stock[goods_id] <= 0 or merchant["silver"] <= 0:
                        continue
                    sell_price = destination_market["prices_milli"][goods_id]
                    if sell_price <= buy_price:
                        continue
                    buy_quantity = min(
                        source_stock[goods_id], cargo_capacity,
                        merchant["silver"] * VALUE_SCALE // buy_price,
                    )
                    if buy_quantity <= 0:
                        continue
                    buy_cost = _ceil_div(buy_quantity * buy_price, VALUE_SCALE)
                    available, basis = buy_quantity, buy_cost
                sell_price = destination_market["prices_milli"][goods_id]
                sell_quantity = min(available, buyer_cash * VALUE_SCALE // sell_price)
                if sell_quantity <= 0:
                    continue
                proceeds = _ceil_div(sell_quantity * sell_price, VALUE_SCALE)
                basis_used = basis * sell_quantity // max(1, available)
                acquisition_cost = buy_cost if buy_quantity else basis_used
                if proceeds <= acquisition_cost:
                    continue
                trade_options.append((goods_id, buy_quantity, buy_cost, sell_quantity,
                                      proceeds, acquisition_cost))
            if not trade_options:
                continue
            try:
                path = self.geography.shortest_path(
                    origin, destination, blocked_routes=blocked_routes)
            except ValueError:
                continue
            duration_hours = sum(self._cached_leg_hours(world, ship_id, route)
                                 for route in path)
            travel_cost = _ceil_div(duration_hours * merchant["travel_cost_silver_per_day"], 24)
            risk_ppm = sum(merchant["risk_beliefs_ppm"].get(route.id, 0) for route in path)
            risk_ppm = min(1_000_000, risk_ppm)
            for (goods_id, buy_quantity, buy_cost, sell_quantity, proceeds,
                 acquisition_cost) in trade_options:
                risk_cost = _ceil_div(proceeds * risk_ppm * merchant["risk_cost_basis_points"],
                                      1_000_000 * 10_000)
                profit = proceeds - acquisition_cost - travel_cost - risk_cost
                candidate_key = (profit, -duration_hours, goods_id, destination)
                if best_key is None or candidate_key > best_key:
                    best_key = candidate_key
                    best = {
                        "merchant_id": merchant_id, "origin": origin, "destination": destination,
                        "route_ids": [route.id for route in path], "goods_id": goods_id,
                        "buy_quantity_milli": buy_quantity,
                        "sell_quantity_milli": sell_quantity,
                        "buy_cost_silver": buy_cost, "expected_revenue_silver": proceeds,
                        "travel_cost_silver": travel_cost, "risk_cost_silver": risk_cost,
                        "risk_belief_ppm": risk_ppm, "expected_profit_silver": profit,
                        "duration_hours": duration_hours,
                    }
        return best if best and best["expected_profit_silver"] > 0 else None

    def dispatch_trade_route(self, world, merchant_id: str, plan: dict[str, Any], *,
                             caused_by: str | None = None) -> str:
        merchant = self._merchant(merchant_id)
        if plan.get("merchant_id") != merchant_id or plan.get("origin") not in self.islands:
            raise ValueError("Trade route plan does not belong to this merchant")
        ship = world.entities.get(merchant["ship_id"])
        if (not ship or not ship_is_commandable(world, merchant["ship_id"])
                or ship.get("legal_owner") != {"kind": "merchant", "id": merchant_id}):
            raise ValueError("Merchant no longer owns or commands this vessel")
        current = world.entities[merchant["ship_id"]].get("location", {})
        if current.get("kind") != "island" or current.get("id") != plan["origin"]:
            raise ValueError("Merchant is not at planned route origin")
        if plan.get("expected_profit_silver", 0) <= 0:
            raise ValueError("Merchant will not dispatch an unprofitable route")
        cause = merchant["last_event_id"] if caused_by is None else caused_by
        self._validate_cause(world, cause)
        if plan["buy_quantity_milli"]:
            record = self.trade(world, merchant_id, plan["origin"], plan["goods_id"],
                                "buy", plan["buy_quantity_milli"], caused_by=cause)
            cause = record["id"]
        return world.start_voyage(merchant["ship_id"], plan["destination"], caused_by=cause)

    def best_reposition(self, world, merchant_id: str) -> dict[str, Any] | None:
        """Find a profitable future market and reposition empty to its exporter."""
        self._prepare_route_cache(world)
        merchant = self._merchant(merchant_id)
        ship_id = merchant["ship_id"]
        ship = world.entities.get(ship_id)
        if (not ship or not ship_is_commandable(world, ship_id)
                or ship.get("legal_owner") != {"kind": "merchant", "id": merchant_id}):
            return None
        if ship_id in world.travel.transits:
            return None
        current = world.entities[ship_id].get("location", {}).get("id")
        if current not in self.islands:
            return None
        hold = sum(merchant["cargo_milli"].values())
        free_capacity = merchant["capacity_milli"] - hold
        if free_capacity <= 0:
            return None
        best = None
        leg_hours: dict[str, int] = {}
        path_risk: dict[int, int] = {}
        risk_signature = tuple(sorted(merchant["risk_beliefs_ppm"].items()))
        ship = world.entities[ship_id]
        vessel_signature = (ship["speed_permille"], ship["navigation"], ship["condition"])
        blocked_routes = frozenset(self.blockades)
        destination_liquidity = {
            island_id: self._market_liquidity(market)
            for island_id, market in self.islands.items()
        }

        best_key = None
        def duration(path) -> int:
            cache_key = (id(path), *vessel_signature)
            value = self._path_duration_cache.get(cache_key)
            if value is None:
                total = 0
                for route in path:
                    if route.id not in leg_hours:
                        leg_hours[route.id] = self._cached_leg_hours(world, ship_id, route)
                    total += leg_hours[route.id]
                value = total
                self._path_duration_cache[cache_key] = value
            return value

        def risk(path) -> int:
            path_id = id(path)
            if path_id not in path_risk:
                cache_key = (path_id, risk_signature)
                value = self._path_risk_cache.get(cache_key)
                if value is None:
                    value = min(1_000_000, sum(
                        merchant["risk_beliefs_ppm"].get(route.id, 0) for route in path))
                    self._path_risk_cache[cache_key] = value
                path_risk[path_id] = value
            return path_risk[path_id]

        for source_id in sorted(self.islands):
            if source_id == current:
                continue
            source = self.islands[source_id]
            reposition = None
            reposition_attempted = False
            for destination_id in sorted(self.islands):
                if destination_id == source_id:
                    continue
                destination = self.islands[destination_id]
                buyer_liquidity = destination_liquidity[destination_id]
                if buyer_liquidity <= 0:
                    continue
                trade_options = []
                for goods_id in sorted(self.goods):
                    if merchant["cargo_milli"][goods_id] > 0:
                        continue
                    buy_price = source["prices_milli"][goods_id]
                    sell_price = destination["prices_milli"][goods_id]
                    if (source["stockpile_milli"][goods_id] <= 0
                            or merchant["silver"] <= 0 or sell_price <= buy_price):
                        continue
                    quantity = min(
                        source["stockpile_milli"][goods_id], free_capacity,
                        merchant["silver"] * VALUE_SCALE // buy_price,
                        buyer_liquidity * VALUE_SCALE // sell_price,
                    )
                    if quantity <= 0:
                        continue
                    buy_cost = _ceil_div(quantity * buy_price, VALUE_SCALE)
                    proceeds = _ceil_div(quantity * sell_price, VALUE_SCALE)
                    # Travel and risk costs are nonnegative, so a market pair with
                    # no gross arbitrage cannot yield a profitable reposition.
                    if proceeds <= buy_cost:
                        continue
                    trade_options.append((goods_id, quantity, buy_cost, proceeds))
                if not trade_options:
                    continue
                if not reposition_attempted:
                    reposition_attempted = True
                    try:
                        reposition_path = self.geography.shortest_path(
                            current, source_id, blocked_routes=blocked_routes)
                    except ValueError:
                        break
                    reposition_hours = duration(reposition_path)
                    reposition_cost = _ceil_div(
                        reposition_hours * merchant["travel_cost_silver_per_day"], 24)
                    reposition_risk_ppm = risk(reposition_path)
                    reposition = (reposition_hours, reposition_cost, reposition_risk_ppm)
                try:
                    delivery_path = self.geography.shortest_path(
                        source_id, destination_id, blocked_routes=blocked_routes)
                except ValueError:
                    continue
                delivery_hours = duration(delivery_path)
                delivery_cost = _ceil_div(
                    delivery_hours * merchant["travel_cost_silver_per_day"], 24)
                reposition_hours, reposition_cost, reposition_risk_ppm = reposition
                risk_ppm = min(1_000_000, reposition_risk_ppm + risk(delivery_path))
                for goods_id, quantity, buy_cost, proceeds in trade_options:
                    risk_cost = _ceil_div(
                        proceeds * risk_ppm * merchant["risk_cost_basis_points"],
                        1_000_000 * 10_000,
                    )
                    profit = proceeds - buy_cost - reposition_cost - delivery_cost - risk_cost
                    candidate_key = (profit, -reposition_hours, goods_id, source_id, destination_id)
                    if best_key is None or candidate_key > best_key:
                        best_key = candidate_key
                        best = {
                            "merchant_id": merchant_id,
                            "current_island": current,
                            "source_island": source_id,
                            "destination": destination_id,
                            "goods_id": goods_id,
                            "quantity_milli": quantity,
                            "expected_profit_silver": profit,
                            "reposition_hours": reposition_hours,
                        }
        return best if best and best["expected_profit_silver"] > 0 else None

    def _cached_leg_hours(self, world, ship_id: str, route) -> int:
        """Reuse deterministic route timing across same-day merchant evaluations."""
        self._prepare_route_cache(world)
        vessel = world.entities[ship_id]
        condition = vessel["condition"]
        first = self.geography.regions[
            self.geography.islands[route.origin].region_id]
        second = self.geography.regions[
            self.geography.islands[route.destination].region_id]
        key = (
            route.id, vessel["speed_permille"], vessel["navigation"], condition,
            first.season, second.season,
            self._cached_region_speed(world, first.id),
            self._cached_region_speed(world, second.id),
        )
        if key not in self._route_duration_cache:
            self._route_duration_cache[key] = world.travel._leg_hours(
                route, vessel, condition)
        return self._route_duration_cache[key]

    def _cached_region_speed(self, world, region_id: str) -> int:
        if region_id not in self._region_speed_cache:
            self._region_speed_cache[region_id] = world.weather.speed_permille(region_id)
        return self._region_speed_cache[region_id]

    def _prepare_route_cache(self, world) -> None:
        """Expire route-score caches at each clock tick before path-cache lookup."""
        tick = world.clock.tick
        if tick != self._route_duration_cache_tick:
            self._route_duration_cache_tick = tick
            self._route_duration_cache.clear()
            self._path_duration_cache.clear()
            self._path_risk_cache.clear()
            self._region_speed_cache.clear()

    def handle_scheduled_event(self, world, scheduled, event) -> None:
        if scheduled.event_type != "VOYAGE_ARRIVED":
            return
        ship_id = scheduled.payload["actors"][0]["entity_id"]
        merchant_id = next((key for key, state in self.merchants.items()
                            if state["ship_id"] == ship_id), None)
        if merchant_id is None:
            return
        merchant = self.merchants[merchant_id]
        merchant["last_event_id"] = event.id
        destination = scheduled.payload["data"]["destination"]
        for goods_id in sorted(self.goods):
            quantity = merchant["cargo_milli"][goods_id]
            if quantity <= 0:
                continue
            try:
                self.trade(world, merchant_id, destination, goods_id, "sell", quantity,
                           caused_by=event.id)
            except ValueError as exc:
                if "cannot fill" not in str(exc) and "cannot afford" not in str(exc):
                    raise

    def validate_world(self, world) -> None:
        self.validate_state()
        if world.travel.closed_routes != set(self.blockades):
            raise ValueError("Travel closures disagree with economy blockade records")
        for route_id, blockade in self.blockades.items():
            try:
                event = world.event_log.get(blockade["event_id"])
            except KeyError as exc:
                raise ValueError("Blockade references an unknown event") from exc
            if (event.type != "ROUTE_BLOCKADED"
                    or event.data.get("route_id") != route_id
                    or event.data.get("target_island") != blockade["island_id"]):
                raise ValueError("Blockade state does not match its causal event")
        initial_events = [event for event in world.event_log.by_type("ISLAND_ECONOMY_INITIALIZED")]
        if len(initial_events) != len(self.islands) or {
                event.location for event in initial_events} != set(self.islands):
            raise ValueError("Initial economy endowment events do not cover every island")
        opening_silver = 0
        opening_goods = {goods_id: 0 for goods_id in self.goods}
        for event in initial_events:
            opening_silver += event.data["population_silver"] + event.data["treasury_silver"]
            for goods_id, quantity in event.data["stockpile_milli"].items():
                opening_goods[goods_id] += quantity
        if opening_silver != self.ledger.opening_silver or opening_goods != self.ledger.opening_goods:
            raise ValueError("Opening economy ledger disagrees with world-generation endowments")

        source_silver = 0
        source_goods = {goods_id: 0 for goods_id in self.goods}
        source_ids = set()
        for source in self.ledger.source_events:
            if not isinstance(source, dict) or set(source) != {"event_id", "silver", "goods"}:
                raise ValueError("Invalid ledger source event record")
            if source["event_id"] in source_ids:
                raise ValueError("Duplicate ledger source event")
            source_ids.add(source["event_id"])
            try:
                event = world.event_log.get(source["event_id"])
            except KeyError as exc:
                raise ValueError("Ledger source references an unknown event") from exc
            if event.type != "MERCHANT_CREATED":
                raise ValueError("Unexpected silver/goods source event type")
            if (source["silver"] != event.data["starting_silver"]
                    or source["goods"] != {key: value for key, value in
                                            event.data["starting_cargo_milli"].items() if value}):
                raise ValueError("Ledger source does not match merchant endowment event")
            source_silver += source["silver"]
            for goods_id, quantity in source["goods"].items():
                if goods_id not in source_goods or type(quantity) is not int or quantity < 0:
                    raise ValueError("Invalid goods source in ledger")
                source_goods[goods_id] += quantity
        if source_silver != self.ledger.silver_sources:
            raise ValueError("Ledger silver source totals do not match their source events")

        funding_events = world.event_log.by_type("GOVERNMENT_BUDGET_FUNDED")
        payout_events = world.event_log.by_type("BOUNTY_PAID")
        response_events = world.event_log.by_type("GOVERNMENT_RESPONSE_FUNDED")
        for event in funding_events:
            if (type(event.data.get("silver")) is not int or event.data["silver"] <= 0
                    or event.data.get("source_island") != event.location
                    or event.location not in self.islands or not event.caused_by):
                raise ValueError("Invalid government budget funding event")
        for event in payout_events:
            if (type(event.data.get("silver")) is not int or event.data["silver"] <= 0
                    or not event.caused_by or not any(
                        target.get("role") == "beneficiary_crew"
                        and isinstance(target.get("entity_id"), str)
                        and target["entity_id"].startswith("crew_")
                        for target in event.targets)):
                raise ValueError("Invalid government bounty transfer")
        for event in response_events:
            if (type(event.data.get("silver")) is not int or event.data["silver"] <= 0
                    or event.location not in self.islands
                    or event.data.get("island_id") != event.location
                    or not event.caused_by):
                raise ValueError("Invalid government response transfer")
        funded = sum(event.data["silver"] for event in funding_events)
        paid = sum(event.data["silver"] for event in payout_events)
        response_spend = sum(event.data["silver"] for event in response_events)
        if (funded != self.government_funded_silver or min(paid, response_spend) < 0
                or self.government_silver != funded - paid - response_spend):
            raise ValueError("Government response treasury disagrees with its transfer history")
        if sum(state["government_response_silver"] for state in self.islands.values()) \
                != response_spend:
            raise ValueError("Island response reserves disagree with government spending history")

        ticks_per_day = (world.config["time"]["hours_per_day"]
                         // world.config["time"]["hours_per_tick"])
        sink_silver = 0
        sink_goods = {goods_id: 0 for goods_id in self.goods}
        sink_ids = set()
        for sink in self.ledger.sink_events:
            if not isinstance(sink, dict) or set(sink) != {"event_id", "silver", "goods"}:
                raise ValueError("Invalid ledger sink event record")
            if sink["event_id"] in sink_ids:
                raise ValueError("Duplicate ledger sink event")
            sink_ids.add(sink["event_id"])
            try:
                event = world.event_log.get(sink["event_id"])
            except KeyError as exc:
                raise ValueError("Ledger sink references an unknown event") from exc
            expected_sink = event.data.get("ledger_sink")
            if (not isinstance(expected_sink, dict)
                    or expected_sink.get("silver") != sink["silver"]
                    or expected_sink.get("goods") != sink["goods"]):
                raise ValueError("Ledger sink does not match its causal event")
            sink_silver += sink["silver"]
            for goods_id, quantity in sink["goods"].items():
                if goods_id not in sink_goods or type(quantity) is not int or quantity < 0:
                    raise ValueError("Invalid goods sink in ledger")
                sink_goods[goods_id] += quantity
        for day_flow in self.ledger.flow_events:
            if len(day_flow["event_ids"]) < len(self.islands):
                raise ValueError("Daily ledger must cite one update event per island")
            seen_islands = set()
            produced = {goods_id: 0 for goods_id in self.goods}
            consumed = {goods_id: 0 for goods_id in self.goods}
            for event_id in day_flow["event_ids"]:
                try:
                    event = world.event_log.get(event_id)
                except KeyError as exc:
                    raise ValueError("Daily ledger references an unknown flow event") from exc
                if event.tick != day_flow["day"] * ticks_per_day:
                    raise ValueError("Daily ledger flow event has a mismatched tick")
                if event.type == "ISLAND_ECONOMY_UPDATED":
                    if event.location not in self.islands or event.location in seen_islands:
                        raise ValueError("Invalid island flow event in daily ledger")
                    seen_islands.add(event.location)
                    for goods_id, quantity in event.data["production_milli"].items():
                        produced[goods_id] += quantity
                    for goods_id, quantity in event.data["consumption_milli"].items():
                        consumed[goods_id] += quantity
                elif event.type == "SHIP_SUPPLIES_CONSUMED":
                    if (event.data.get("ship_id") not in self.ship_supplies
                            or set(event.data.get("consumed_milli", {})) != set(self.goods)
                            or set(event.data.get("shortage_milli", {})) != set(self.goods)):
                        raise ValueError("Invalid ship-supply flow event in daily ledger")
                    for goods_id, quantity in event.data["consumed_milli"].items():
                        if type(quantity) is not int or quantity < 0:
                            raise ValueError("Invalid consumed ship supply quantity")
                        consumed[goods_id] += quantity
                else:
                    raise ValueError("Unexpected event in daily goods flow")
            if (seen_islands != set(self.islands)
                    or produced != day_flow["goods_sources"]
                    or consumed != day_flow["goods_sinks"]):
                raise ValueError("Daily ledger goods flows disagree with causal economy events")
            for goods_id, quantity in produced.items():
                source_goods[goods_id] += quantity
            for goods_id, quantity in consumed.items():
                sink_goods[goods_id] += quantity
        if source_goods != self.ledger.goods_sources:
            raise ValueError("Ledger goods source totals do not match endowment and production events")
        if sink_goods != self.ledger.goods_sinks or sink_silver != self.ledger.silver_sinks:
            raise ValueError("Ledger sink totals do not match causal sink and consumption events")

        for merchant_id, merchant in self.merchants.items():
            ship_id = merchant["ship_id"]
            if ((ship_id not in world.entities and ship_id not in world.archive)
                    or (world.entities.get(ship_id) or world.archive.get(ship_id)).get("kind") != "ship"):
                raise ValueError(f"Merchant has no active vessel: {merchant_id}")
            if world.archive.get(ship_id, {}).get("sunk_tick") is not None:
                raise ValueError(f"Sunk ship retains an active merchant account: {merchant_id}")
            try:
                last_event = world.event_log.get(merchant["last_event_id"])
            except KeyError as exc:
                raise ValueError(f"Merchant references an unknown event: {merchant_id}") from exc
            if last_event.type not in {
                    "MERCHANT_CREATED", "MARKET_TRADE", "MERCHANT_ROUTE_DECISION",
                    "VOYAGE_STARTED", "VOYAGE_ARRIVED", "MERCHANT_VESSEL_REPLACED"}:
                raise ValueError(f"Merchant references a non-merchant event: {merchant_id}")
            if ship_id in world.travel.transits:
                if "location" in world.entities[ship_id]:
                    raise ValueError(f"Merchant vessel has stale location while at sea: {merchant_id}")
            else:
                location = world.entities[ship_id].get("location", {})
                if location.get("kind") != "island" or location.get("id") not in self.islands:
                    raise ValueError(f"Merchant vessel is not at a known island: {merchant_id}")

    def _make_merchant_decisions(self, world) -> None:
        decision_day = self.last_updated_day
        for merchant_id in sorted(self.merchants):
            merchant = self.merchants[merchant_id]
            if decision_day - merchant["last_route_attempt_day"] < 7:
                continue
            merchant["last_route_attempt_day"] = decision_day
            plan = self.best_trade_route(world, merchant_id)
            if plan is not None:
                self.dispatch_trade_route(world, merchant_id, plan)
                continue
            reposition = self.best_reposition(world, merchant_id)
            if reposition is None:
                continue
            decision = world.event_log.append(
                tick=world.clock.tick,
                event_type="MERCHANT_ROUTE_DECISION",
                location=reposition["current_island"],
                actors=[{"role": "merchant", "entity_id": merchant_id}],
                data={key: value for key, value in reposition.items()
                      if key != "merchant_id"},
                caused_by=[self.merchants[merchant_id]["last_event_id"]],
                causes_tags=["profit_seeking", "reposition_for_trade"],
                magnitude=0.2,
            )
            merchant["last_event_id"] = decision.id
            world.start_voyage(
                self.merchants[merchant_id]["ship_id"],
                reposition["source_island"], caused_by=decision.id,
            )

    def _merchant(self, merchant_id: str) -> dict[str, Any]:
        try:
            return self.merchants[merchant_id]
        except KeyError as exc:
            raise ValueError(f"Unknown merchant: {merchant_id}") from exc

    @staticmethod
    def _validate_cause(world, event_id: str) -> None:
        try:
            world.event_log.get(event_id)
        except KeyError as exc:
            raise ValueError(f"Unknown trade cause event: {event_id}") from exc

    def _produce(self, state: dict[str, Any]) -> dict[str, int]:
        # Deprivation and unrest reduce output; integer permille math is stable.
        productivity = max(250, 1000 - state["unrest"] * 3 - state["famine_level"] * 5)
        productivity = productivity * (1000 - state["disease_pressure_permille"]) // 1000
        if state["blockaded_routes"]:
            productivity = productivity * self.rules["blockade_productivity_permille"] // 1000
        result = {}
        for goods_id, daily_units in sorted(state["produces_per_day"].items()):
            amount = daily_units * QUANTITY_SCALE * productivity // 1000
            if goods_id == "grain":
                amount = amount * state["harvest_yield_permille"] // 1000
            state["stockpile_milli"][goods_id] += amount
            result[goods_id] = amount
        return result

    def _consume(self, state: dict[str, Any]) -> tuple[dict[str, int], dict[str, int]]:
        consumed, shortages = {}, {}
        population = state["population"]
        for goods_id, goods in sorted(self.goods.items()):
            demand = goods["consumption_per_1000_per_day_milli"] * population // 1000
            available = state["stockpile_milli"][goods_id]
            amount = min(available, demand)
            state["stockpile_milli"][goods_id] = available - amount
            consumed[goods_id] = amount
            if demand > amount:
                shortages[goods_id] = demand - amount
        return consumed, shortages

    def _update_social_state(self, state: dict[str, Any], shortages: dict[str, int]) -> None:
        essential_shortage = sum(shortages.get(goods_id, 0) for goods_id in ("grain", "fish", "water"))
        if essential_shortage:
            state["consecutive_shortage_days"] += 1
            per_1000 = max(1, (essential_shortage + state["population"] - 1)
                           // max(1, state["population"] * QUANTITY_SCALE // 1000))
            state["unrest"] = min(100, state["unrest"] + per_1000 *
                                  self.rules["unrest_shortage_increase_per_1000_people"])
            state["famine_level"] = min(100, state["famine_level"] + 1 +
                                        int(state["consecutive_shortage_days"] >=
                                            self.rules["famine_threshold_days"]))
            state["prosperity"] = max(0, state["prosperity"] - 1)
        else:
            state["consecutive_shortage_days"] = 0
            state["unrest"] = max(0, state["unrest"] - self.rules["unrest_recovery_per_day"])
            state["famine_level"] = max(0, state["famine_level"] - 1)
            if state["unrest"] < 25:
                state["prosperity"] = min(100, state["prosperity"] + 1)

    def _update_prices(self, state: dict[str, Any]) -> dict[str, int]:
        changes = {}
        smoothing = self.rules["price_smoothing_percent"]
        elasticity = self.rules["price_elasticity_milli"]
        for goods_id, goods in sorted(self.goods.items()):
            target = (self.rules["target_stock_per_1000_population"]
                      * state["population"] * QUANTITY_SCALE // 1000)
            stock = max(1, state["stockpile_milli"][goods_id])
            # k=0.5 in integer fixed point: sqrt(target / stock).
            ratio_milli_squared = max(1, target * 1000 // stock)
            multiplier_milli = math.isqrt(ratio_milli_squared * 1000)
            # elasticity scales the deviation from the neutral 1.0 multiplier.
            multiplier_milli = 1000 + (multiplier_milli - 1000) * elasticity // 1000
            multiplier_milli = max(
                self.rules["minimum_price_multiplier_milli"],
                min(self.rules["maximum_price_multiplier_milli"], multiplier_milli),
            )
            target_price = goods["base_price_silver"] * multiplier_milli
            old_price = state["prices_milli"][goods_id]
            new_price = max(1, (old_price * (100 - smoothing) + target_price * smoothing) // 100)
            if new_price != old_price:
                changes[goods_id] = new_price - old_price
            state["prices_milli"][goods_id] = new_price
        return changes

    def _update_treasury(self, state: dict[str, Any]) -> tuple[int, int, int]:
        tax_rate = state["tax_basis_points_per_day"]
        # The aggregate resident purse pays a prosperity-adjusted daily rate;
        # the finite wallet, rather than population as an infinite mint, bounds it.
        gross = state["population_silver"] * state["prosperity"] * tax_rate // 1_000_000
        collected = min(gross, state["population_silver"])
        state["population_silver"] -= collected
        garrison_due = (state["garrison_strength"]
                        * self.rules["garrison_upkeep_silver_per_10_strength_per_day"] // 10)
        port_due = self.rules["port_upkeep_silver_per_day"] if state["harbor_tier"] > 0 else 0
        payable = state["treasury_silver"] + collected
        paid = min(garrison_due + port_due, payable)
        garrison_paid = min(garrison_due, paid)
        port_paid = min(port_due, paid - garrison_paid)
        state["treasury_silver"] += collected - paid
        state["garrison_silver"] += garrison_paid
        state["port_service_silver"] += port_paid
        return gross, collected, garrison_paid + port_paid

    def snapshot(self) -> dict[str, Any]:
        return {"digest": self.digest, "last_updated_day": self.last_updated_day,
                "islands": {key: _copy_json(self.islands[key]) for key in sorted(self.islands)},
                "merchants": {key: _copy_json(self.merchants[key])
                              for key in sorted(self.merchants)},
                "blockades": {key: _copy_json(self.blockades[key])
                              for key in sorted(self.blockades)},
                "ship_supplies": {key: _copy_json(self.ship_supplies[key])
                                  for key in sorted(self.ship_supplies)},
                "crew_accounts": {key: _copy_json(self.crew_accounts[key])
                                  for key in sorted(self.crew_accounts)},
                "government_silver": self.government_silver,
                "government_funded_silver": self.government_funded_silver,
                "ledger": self.ledger.snapshot()}

    @classmethod
    def restore(cls, geography, snapshot: dict[str, Any],
                goods_path: str | Path = GOODS_PATH,
                economy_path: str | Path = ECONOMY_PATH) -> "EconomySystem":
        if not isinstance(snapshot, dict) or set(snapshot) != {
                "digest", "last_updated_day", "islands", "merchants", "blockades",
                "ship_supplies", "crew_accounts", "government_silver",
                "government_funded_silver", "ledger"}:
            raise ValueError("Invalid economy snapshot")
        goods_data, economy_data = load_economy_data(goods_path, economy_path)
        if snapshot["digest"] != hashlib.sha256(json.dumps(
                {"goods": goods_data, "economy": economy_data}, sort_keys=True,
                ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest():
            raise ValueError("Economy data changed since snapshot creation")
        return cls(geography, snapshot["islands"], goods_data, economy_data,
                   snapshot["last_updated_day"], snapshot["merchants"], snapshot["ledger"],
                   snapshot["blockades"], snapshot["ship_supplies"], snapshot["crew_accounts"],
                   snapshot["government_silver"], snapshot["government_funded_silver"])


def _ceil_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator - 1) // denominator


def _copy_json(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))
