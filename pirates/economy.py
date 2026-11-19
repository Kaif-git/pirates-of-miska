"""Two regional economies, real merchants, finite goods and useful crew kit.

Market porters deliver purchases to trade cargo for a disclosed fee/time cost.
Coastal passage is an explicit travel abstraction, not a naval handling model.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np
from omnisim.world import tile as T
from pirates.ship_storage import UNIT_MASS, resource_quantity
from pirates.crew_recruitment import RecruitmentPool
from pirates.employment import EmploymentLedger

CATALOG = json.loads((Path(__file__).resolve().parents[1] / "content/trade_items.json").read_text(encoding="utf-8"))
RULES = json.loads((Path(__file__).resolve().parents[1] / "content/trade_rules.json").read_text(encoding="utf-8"))
# A bought material cannot become heavier/lighter merely by allocating it.
# The construction/storage module owns standardized material charge masses.
for _item in CATALOG.values():
    if _item.get("supply") in UNIT_MASS:
        _item["weight_g"] = round(_item["yield"] * UNIT_MASS[_item["supply"]] * 1000)
CATEGORIES = {item["category"] for item in CATALOG.values()}
PORTS = {
    "verdant": {"name": "Verdant Cay", "tagline": "Orchards, bean fields, fresh springs and timber yards.",
                "description": "Rich soil feeds the archipelago, but every axe and nail arrives by sea."},
    "cinder": {"name": "Cinder Quay", "tagline": "Foundries, smoke-black stone and crowded workshops.",
               "description": "Iron and tools leave the furnaces daily. Thin volcanic soil cannot feed the workers."},
}
MERCHANTS = {
    "ada": {"name": "Ada Reed", "title": "Growers' cooperative", "port": "verdant", "at": (30, 21),
            "dialogue": ["Our fields grow enough for three islands, but our sickles are worn thin.", "Cinder's workers pay well for food. Bring us tools and iron on your return."]},
    "bo": {"name": "Bo Tidewell", "title": "Fishery and spring keeper", "port": "verdant", "at": (35, 27),
            "dialogue": ["Spring water and salted fish. Neither is cheap in the foundry islands.", "A bucket and a sound knife last longer than any promise from a broker."]},
    "lina": {"name": "Lina Moss", "title": "Weaver and timber factor", "port": "verdant", "at": (25, 28),
            "dialogue": ["We turn forest timber and island flax into cargo the foundries need.", "Good clothes ease a working day. A better tool is worth importing."]},
    "dara": {"name": "Dara Flint", "title": "Foundry workers' provisioner", "port": "cinder", "at": (30, 21),
             "dialogue": ["A furnace cannot feed a family. We import almost every sack and water keg.", "Verdant's harvest and timber keep this island alive; our iron pays for them."]},
    "brann": {"name": "Brann Ash", "title": "Smith and arms maker", "port": "cinder", "at": (35, 27),
               "dialogue": ["Iron, sharp steel and sound tools. The farms need all three.", "My tool rolls help carpenters finish sooner. Don't mistake a loaded pistol for a trained sailor."]},
    "petra": {"name": "Petra Bell", "title": "Outfitter and tinsmith", "port": "cinder", "at": (25, 28),
              "dialogue": ["Work kit, not ornaments: buckets, cook pots, boots and coats.", "I need imported cloth. My customers need the food that arrives on your deck."]},
}
for _merchant in MERCHANTS.values():
    _merchant["stock"] = {}
    _merchant["accepts"] = set()
for _item_id, _item in CATALOG.items():
    for _merchant_id, _quantity in _item.get("market_stock", {}).items():
        MERCHANTS[_merchant_id]["stock"][_item_id] = _quantity
    for _merchant_id in _item.get("market_buyers", []):
        MERCHANTS[_merchant_id]["accepts"].add(_item["category"])
SLOTS = ("tool", "clothing", "coat", "trousers", "headwear", "feet", "weapon")
DELIVERY_FEE = RULES["fees"]["delivery_order"]
PASSAGE_FEE = RULES["fees"]["coastal_passage"]
TRADE_MASS_G = 250000
TRADE_BULK_ML = 350000
CONTRACT_DURATION_DAYS = RULES["contracts"]["duration_days"]
MAX_ACTIVE_CONTRACTS = RULES["contracts"]["max_active"]


def validate_catalog():
    contract_ids = set()
    for item_id, item in CATALOG.items():
        if (not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip()
                or not isinstance(item.get("description"), str) or not item["description"].strip()
                or not isinstance(item.get("category"), str) or item["category"] not in CATEGORIES
                or any(type(item.get(field)) is not int or item[field] < 1
                       for field in ("base_price", "weight_g", "bulk_ml"))
                or not isinstance(item.get("production"), dict)
                or not isinstance(item["production"].get("method"), str) or not item["production"]["method"].strip()
                or not isinstance(item["production"].get("inputs"), list)
                or any(not isinstance(value, str) or not value.strip() for value in item["production"]["inputs"])
                or type(item["production"].get("simulated")) is not bool):
            raise ValueError(f"Invalid trade catalog item: {item_id}")
        if "container" in item:
            vessel = item["container"]
            if (item["category"] != "containers" or not isinstance(vessel, dict)
                    or set(vessel) != {"capacity_ml", "tare_g", "liquid"}
                    or type(vessel["capacity_ml"]) is not int or vessel["capacity_ml"] < 1
                    or type(vessel["tare_g"]) is not int or vessel["tare_g"] != item["weight_g"]
                    or vessel["liquid"] != "clean_water_ml" or "supply" in item or "slot" in item):
                raise ValueError(f"Invalid reusable container definition: {item_id}")
        elif item["category"] == "containers":
            raise ValueError(f"Container category requires capacity/tare metadata: {item_id}")
        if not isinstance(item.get("market_stock"), dict) or not item["market_stock"]:
            raise ValueError(f"Every item needs a market stock location: {item_id}")
        buyers = item.get("market_buyers")
        if (not isinstance(buyers, list) or not buyers
                or any(not isinstance(mid, str) or mid not in MERCHANTS for mid in buyers)
                or len(set(buyers)) != len(buyers)
                or not set(item["market_stock"]) <= set(buyers)):
            raise ValueError(f"Every item needs valid market buyers, including its source merchants: {item_id}")
        if (not isinstance(item.get("found_at"), list) or not item["found_at"]
                or len(item["found_at"]) != len(item["market_stock"])):
            raise ValueError(f"Every item needs found-at provenance matching its source markets: {item_id}")
        found = set()
        for row in item["found_at"]:
            if (not isinstance(row, dict) or set(row) != {"port", "merchant"}
                    or row["merchant"] not in MERCHANTS or row["port"] not in PORTS
                    or MERCHANTS[row["merchant"]]["port"] != row["port"]
                    or row["merchant"] not in item["market_stock"]):
                raise ValueError(f"Invalid found-at location: {item_id}")
            found.add((row["port"], row["merchant"]))
        if found != {(MERCHANTS[mid]["port"], mid) for mid in item["market_stock"]}:
            raise ValueError(f"Found-at locations disagree with market stock: {item_id}")
        for mid, quantity in item["market_stock"].items():
            if mid not in MERCHANTS or type(quantity) is not int or quantity < 1:
                raise ValueError(f"Invalid item market stock: {item_id}")
        factors = item.get("regional_factors")
        if (not isinstance(factors, dict)
                or set(factors) != set(PORTS)
                or any(type(value) is not int or value < 1 for value in factors.values())):
            raise ValueError(f"Invalid per-item regional factors: {item_id}")
        if "origin" in item:
            origin, demand = item["origin"], item.get("demand_ports")
            if (not isinstance(origin, str) or origin not in PORTS or not isinstance(demand, list) or not demand
                    or any(not isinstance(port, str) or port not in PORTS or port == origin for port in demand)
                    or len(set(demand)) != len(demand)
                    or not any(MERCHANTS[mid]["port"] == origin for mid in item["market_stock"])):
                raise ValueError(f"Invalid source/import regions: {item_id}")
            expected = {port for port, value in factors.items() if port != origin and value > factors[origin]}
            if set(demand) != expected:
                raise ValueError(f"Trade demand must match higher-price import regions: {item_id}")
            for port in demand:
                if not any(MERCHANTS[mid]["port"] == port for mid in buyers):
                    raise ValueError(f"Trade good has no buyer in {port}: {item_id}")
        elif "demand_ports" in item:
            raise ValueError(f"Regional demand requires an origin: {item_id}")
        offer = item.get("contract_offer")
        if offer is not None:
            if (not isinstance(offer, dict) or set(offer) != {"id", "issuer", "destination", "quantity", "min_reputation"}
                    or not isinstance(offer["id"], str) or not offer["id"]
                    or offer["id"] in contract_ids
                    or not isinstance(offer["issuer"], str) or not isinstance(offer["destination"], str)
                    or offer["issuer"] not in item["market_stock"]
                    or "origin" not in item or MERCHANTS[offer["issuer"]]["port"] != item["origin"]
                    or offer["destination"] not in item.get("demand_ports", [])
                    or type(offer["quantity"]) is not int or offer["quantity"] < 1
                    or item["market_stock"][offer["issuer"]] < offer["quantity"]
                    or type(offer["min_reputation"]) is not int or offer["min_reputation"] < 0):
                raise ValueError(f"Invalid item contract offer: {item_id}")
            contract_ids.add(offer["id"])
    if (not isinstance(RULES, dict) or not isinstance(RULES.get("fees"), dict)
            or not isinstance(RULES.get("markets"), dict) or not isinstance(RULES.get("contracts"), dict)):
        raise ValueError("Invalid trade rules data")
    required_rules = {
        "fees": ("delivery_order", "coastal_passage"),
        "markets": ("initial_merchant_funds", "scarcity_reference_stock", "scarcity_base_percent",
                    "scarcity_per_unit_percent", "scarcity_min_percent", "scarcity_max_percent",
                    "regional_factor_reference_percent", "price_divisor",
                    "buy_price_percent", "sell_price_percent", "export_restock_cap_multiplier",
                    "export_restock_minimum_per_day", "export_restock_divisor", "import_consumption_per_day"),
        "contracts": ("duration_days", "max_active", "reward_base_price_multiplier", "passage_allowance",
                      "reputation_per_delivery", "max_reputation", "ledger_limit"),
    }
    if any(type(RULES[group].get(key)) is not int or RULES[group][key] < 1
           for group, keys in required_rules.items() for key in keys):
        raise ValueError("Trade rules must contain positive integer values")
    if RULES["markets"]["scarcity_min_percent"] > RULES["markets"]["scarcity_max_percent"]:
        raise ValueError("Trade scarcity bounds are inverted")


validate_catalog()
CONTRACT_TEMPLATES = tuple(
    {"id": item["contract_offer"]["id"], "origin": item["origin"],
     "destination": item["contract_offer"]["destination"], "issuer": item["contract_offer"]["issuer"],
     "item": item_id, "quantity": item["contract_offer"]["quantity"],
     "min_reputation": item["contract_offer"]["min_reputation"]}
    for item_id, item in CATALOG.items() if "contract_offer" in item
)


@dataclass
class Merchant:
    id: str
    name: str
    x: int
    y: int
    glyph: str = "$"
    color: tuple = (240, 205, 120)
    activity: str = "trading"

    def get(self, key, default=None):
        return "merchant" if key == "role" else getattr(self, key, default)


def harbor_tiles(port):
    tiles = np.full((64, 45), T.WATER, dtype=np.int8)
    tiles[8:56, 8:45] = T.GRASS if port == "verdant" else T.FLOOR
    tiles[10:54, 40:45] = T.ROAD
    tiles[22:26, 12:45] = T.ROAD
    tiles[24:34, 24:38] = T.ROAD
    tiles[11:20, 19:32] = T.FLOOR
    tiles[11, 19:32] = tiles[19, 19:32] = T.WALL_STONE
    tiles[11:20, 19] = tiles[11:20, 31] = T.WALL_STONE
    tiles[19, 25] = T.DOOR
    tiles[13, 21] = T.CHEST
    if port == "verdant":
        tiles[31:38, 12:19] = T.FARM
        for x, y in ((12, 12), (15, 30), (21, 40), (36, 15), (35, 47)):
            tiles[y, x] = T.TREE
    else:
        for x, y in ((14, 33), (17, 33), (14, 36), (17, 36)):
            tiles[y, x] = T.HEARTH
        tiles[12:18, 35:38] = T.WALL_STONE
    # Market fronts are stable, approachable landmarks on both islands.
    for merchant in MERCHANTS.values():
        if merchant["port"] == port:
            x, y = merchant["at"]
            tiles[y - 1, x] = T.STALL
    return tiles


def build_port(world):
    """Only touch harbor terrain; never edit the parallel ship construction grid."""
    world.game_map.tiles[:, :45] = harbor_tiles(world.economy.port)
    world.game_map.tiles[22:26, 45:69] = T.ROAD
    for at in list(world.objects_at):
        world.objects_at[at] = [o for o in world.objects_at[at] if not o.get("port_object")]
        if not world.objects_at[at]:
            del world.objects_at[at]
    for merchant in world.economy.merchants():
        world.objects_at.setdefault((merchant.x, merchant.y), []).append({
            "name": f"{merchant.name} — {MERCHANTS[merchant.id]['title']}", "port_object": True})
    world.objects_at.setdefault((42, 23), []).append({"name": "Harbor office: coastal passage and island guide", "port_object": True})


class Economy:
    def __init__(self, day=1):
        self.port = "verdant"
        self.money = 600
        self.treasury = 0
        self.cargo = {}
        self.gear = {}
        self.gear_condition = {}
        self.gear_ids = {}
        self.next_gear_id = 1
        self.weapon_xp = {}
        self.last_day = day
        self.markets = {key: {"stock": dict(m["stock"]), "money": RULES["markets"]["initial_merchant_funds"]}
                        for key, m in MERCHANTS.items()}
        self.history = []
        self.trader_reputation = 0
        self.contracts = []
        self.drilling = None  # Synchronous drill session; never background simulation.
        self.recruitment = RecruitmentPool()
        self.employment = EmploymentLedger(day)
        self.harbors = {key: {"explored": np.zeros((64, 45), bool).tolist(),
                              "doors": np.zeros((64, 45), bool).tolist(), "ground": {}} for key in PORTS}

    def merchants(self):
        return [Merchant(key, m["name"], *m["at"]) for key, m in MERCHANTS.items() if m["port"] == self.port]

    def record(self, text):
        self.history = (self.history + [text])[-40:]

    def update_day(self, day):
        if day <= self.last_day:
            return
        elapsed = day - self.last_day
        for mid, market in self.markets.items():
            for item_id in list(market["stock"]):
                item = CATALOG[item_id]
                target = MERCHANTS[mid]["stock"].get(item_id, 0)
                regional = item["regional_factors"][MERCHANTS[mid]["port"]]
                if target and regional <= RULES["markets"]["regional_factor_reference_percent"]:
                    cap = target * RULES["markets"]["export_restock_cap_multiplier"]
                    daily = max(RULES["markets"]["export_restock_minimum_per_day"],
                                target // RULES["markets"]["export_restock_divisor"])
                    market["stock"][item_id] = min(cap, market["stock"][item_id] + daily * elapsed)
                elif regional > RULES["markets"]["regional_factor_reference_percent"]:
                    consume = RULES["markets"]["import_consumption_per_day"] * elapsed
                    market["stock"][item_id] = max(0, market["stock"][item_id] - consume)
        self.last_day = day
        for contract in self.contracts:
            if contract["status"] == "active" and day > contract["deadline_day"]:
                self.markets[contract["issuer"]]["money"] += contract["escrow"]
                contract["escrow"] = 0
                contract["status"] = "expired"
                self.record(f"Delivery contract {contract['template_id']} expired; its escrow returned to the issuer.")
        self._trim_contracts()

    def require_contract_office(self, world):
        if (getattr(world, "at_sea", False) or world.player.z != 0 or world.player.x >= 45
                or abs(world.player.x - 42) + abs(world.player.y - 23) > 2):
            raise ValueError("Merchant contracts are handled at the harbor office (42,23)")

    def _trim_contracts(self):
        active = [row for row in self.contracts if row["status"] == "active"]
        limit = RULES["contracts"]["ledger_limit"]
        terminal = [row for row in self.contracts if row["status"] != "active"][-(limit - len(active)):]
        self.contracts = terminal + active

    def contract_offers(self, port, day):
        if port not in PORTS or type(day) is not int or day < 1:
            return []
        seen = {row["offer_id"] for row in self.contracts if row["offer_day"] == day}
        offers = []
        for template in CONTRACT_TEMPLATES:
            if template["origin"] != port or template["min_reputation"] > self.trader_reputation:
                continue
            offer_id = f"{day}:{template['id']}"
            if offer_id in seen:
                continue
            market = self.markets[template["issuer"]]
            if market["stock"].get(template["item"], 0) < template["quantity"]:
                continue
            contract_rules = RULES["contracts"]
            reward = (CATALOG[template["item"]]["base_price"] * template["quantity"]
                      * contract_rules["reward_base_price_multiplier"] + contract_rules["passage_allowance"])
            if market["money"] < reward:
                continue
            offers.append({**template, "offer_id": offer_id, "reward": reward,
                           "deadline_day": day + CONTRACT_DURATION_DAYS})
        return offers

    def accept_contract(self, world, offer_id):
        self.require_contract_office(world)
        if not isinstance(offer_id, str):
            raise ValueError("Choose a current delivery offer")
        if sum(row["status"] == "active" for row in self.contracts) >= MAX_ACTIVE_CONTRACTS:
            raise ValueError(f"You may carry at most {MAX_ACTIVE_CONTRACTS} active delivery contracts")
        offer = next((row for row in self.contract_offers(self.port, world.clock.day)
                      if row["offer_id"] == offer_id), None)
        if offer is None:
            raise ValueError("That delivery offer is no longer available")
        market = self.markets[offer["issuer"]]
        if market["money"] < offer["reward"] or market["stock"].get(offer["item"], 0) < offer["quantity"]:
            raise ValueError("The issuer can no longer fund or supply this order")
        market["money"] -= offer["reward"]
        self.contracts.append({
            "offer_id": offer["offer_id"], "template_id": offer["id"], "offer_day": world.clock.day,
            "origin": offer["origin"], "destination": offer["destination"], "issuer": offer["issuer"],
            "item": offer["item"], "quantity": offer["quantity"], "reward": offer["reward"],
            "escrow": offer["reward"], "deadline_day": offer["deadline_day"], "status": "active",
        })
        self.record(f"Accepted {offer['quantity']} {CATALOG[offer['item']]['name']} delivery to {PORTS[offer['destination']]['name']}; {offer['reward']} Marks escrowed.")
        return self.contracts[-1]

    def deliver_contract(self, world, offer_id):
        self.require_contract_office(world)
        contract = next((row for row in self.contracts
                         if row["offer_id"] == offer_id and row["status"] == "active"), None)
        if contract is None:
            raise ValueError("That active delivery contract was not found")
        if self.port != contract["destination"]:
            raise ValueError(f"Deliver this order at {PORTS[contract['destination']]['name']}")
        if world.clock.day > contract["deadline_day"]:
            raise ValueError("This delivery contract has expired")
        if self.available_cargo(contract["item"], excluding_contract=offer_id) < contract["quantity"]:
            raise ValueError(f"The order requires {contract['quantity']} {CATALOG[contract['item']]['name']}")
        reward = contract["escrow"]
        if reward < 1 or self.money + reward > 10**12:
            raise ValueError("Contract escrow is invalid")
        self.remove(contract["item"], contract["quantity"], contract_offer_id=offer_id)
        self.money += reward
        contract["escrow"] = 0
        contract["status"] = "completed"
        reputation_gain = RULES["contracts"]["reputation_per_delivery"]
        self.trader_reputation = min(RULES["contracts"]["max_reputation"], self.trader_reputation + reputation_gain)
        self.record(f"Delivered {contract['quantity']} {CATALOG[contract['item']]['name']} to {PORTS[self.port]['name']}; earned {reward} Marks and {reputation_gain} trader reputation.")
        world.advance(1000)
        world.message(f"Delivery accepted: {reward} Marks paid; trader reputation is {self.trader_reputation}.")
        return reward

    def price(self, mid, item_id, buying, stock=None):
        merchant = MERCHANTS[mid]
        item = CATALOG[item_id]
        stock = self.markets[mid]["stock"].get(item_id, 0) if stock is None else stock
        market_rules = RULES["markets"]
        target = merchant["stock"].get(item_id, market_rules["scarcity_reference_stock"])
        scarcity = max(market_rules["scarcity_min_percent"], min(
            market_rules["scarcity_max_percent"],
            market_rules["scarcity_base_percent"]
            + (target - stock) * market_rules["scarcity_per_unit_percent"]))
        regional = item["regional_factors"][merchant["port"]]
        trade_percent = market_rules["buy_price_percent"] if buying else market_rules["sell_price_percent"]
        return max(1, item["base_price"] * regional * scarcity * trade_percent // market_rules["price_divisor"])

    def quote(self, mid, item_id, quantity, buying):
        if mid not in MERCHANTS or item_id not in CATALOG or type(quantity) is not int or not 1 <= quantity <= 999 or type(buying) is not bool:
            raise ValueError("Choose a known item and quantity from 1 to 999")
        if mid not in CATALOG[item_id]["market_buyers"]:
            raise ValueError("This merchant does not buy or sell that item")
        stock = self.markets[mid]["stock"].get(item_id, 0)
        if buying and stock < quantity:
            raise ValueError("Merchant has insufficient stock")
        total = sum(self.price(mid, item_id, buying, stock + (-i if buying else i)) for i in range(quantity))
        return total + (DELIVERY_FEE if buying else 0)

    def load(self):
        counts = dict(self.cargo)
        for equipment in self.gear.values():
            for item_id in equipment.values():
                counts[item_id] = counts.get(item_id, 0) + 1
        return (sum(CATALOG[k]["weight_g"] * n for k, n in counts.items()),
                sum(CATALOG[k]["bulk_ml"] * n for k, n in counts.items()))

    def require_merchant(self, world, mid):
        if mid not in MERCHANTS or MERCHANTS[mid]["port"] != self.port:
            raise ValueError("That merchant is on another island")
        x, y = MERCHANTS[mid]["at"]
        if abs(world.player.x - x) + abs(world.player.y - y) > 2 or not world.game_map.visible[y, x]:
            raise ValueError("Walk within two tiles of the merchant to trade")

    def recruit_candidate(self, world, candidate_id):
        """Hire a finite port candidate and transfer them onto the player ship."""
        if getattr(world, "at_sea", False) or world.player.z != 0 or world.player.x >= 45:
            raise ValueError("Recruitment is only available while ashore at a port")
        candidate = self.recruitment.candidate(candidate_id)
        if candidate is None or candidate["port"] != self.port:
            raise ValueError("That candidate is no longer available at this port")
        if self.money < candidate["asking_price"]:
            raise ValueError(f"Not enough Marks for {candidate['name']}'s {candidate['asking_price']}-Mark signing advance")
        agreement = self.employment.prepare(candidate, world.clock.day)
        sailor_id = f"hire:{candidate_id}"
        old_layout, old_crew = world.fixtures, list(world.ship.crew)
        old_events, old_money = world.ship.events, self.money
        try:
            sailor = world.ship.add_recruit(world, candidate)
            self.recruitment.mark_recruited(candidate_id, self.port)
            self.employment.commit(agreement)
            self.money -= candidate["asking_price"]
        except Exception:
            self.money = old_money
            self.employment.remove(sailor_id)
            self.recruitment.statuses[candidate_id] = "available"
            world.ship.rollback_recruit(world, old_layout, old_crew, old_events, sailor_id)
            raise
        self.record(f"Recruited {sailor.name} at {PORTS[self.port]['name']} for {candidate['asking_price']} Marks.")
        world.message(f"{sailor.name} joined the crew. A hammock berth is now installed aboard.")
        return sailor

    def trade(self, world, mid, item_id, quantity, buying):
        self.require_merchant(world, mid)
        total = self.quote(mid, item_id, quantity, buying)
        market = self.markets[mid]
        if buying:
            if self.money < total:
                raise ValueError("Not enough Marks (delivery costs 2 Marks per order)")
            mass, bulk = self.load()
            item = CATALOG[item_id]
            if mass + item["weight_g"] * quantity > TRADE_MASS_G or bulk + item["bulk_ml"] * quantity > TRADE_BULK_ML:
                raise ValueError("Trade cargo capacity exceeded; sell or allocate supplies first")
        else:
            if self.available_cargo(item_id) < quantity:
                raise ValueError("Not enough unissued cargo to sell")
            if market["money"] < total:
                raise ValueError("Merchant cannot afford this sale; try fewer goods or another buyer")
        # All checks precede mutation. No partial trades or hidden credit.
        if buying:
            self.money -= total
            market["money"] += total - DELIVERY_FEE
            self.treasury += DELIVERY_FEE
            market["stock"][item_id] -= quantity
            self.cargo[item_id] = self.cargo.get(item_id, 0) + quantity
        else:
            self.money += total
            market["money"] -= total
            market["stock"][item_id] = market["stock"].get(item_id, 0) + quantity
            self.remove(item_id, quantity)
        text = f"{'Bought' if buying else 'Sold'} {quantity} {CATALOG[item_id]['name']} for {total} Marks at {MERCHANTS[mid]['name']}."
        self.record(text)
        world.advance(6000 if buying else 1000)
        world.message(text + (" Porters delivered it to trade cargo (2 Marks, 1 minute)." if buying else ""))
        return total

    def reserved_cargo(self, item_id, excluding_contract=None):
        return sum(
            contract["quantity"] for contract in self.contracts
            if contract["status"] == "active" and contract["item"] == item_id
            and contract["offer_id"] != excluding_contract
        )

    def available_cargo(self, item_id, excluding_contract=None):
        """Fungible cargo not backing an active delivery promise."""
        return max(0, self.cargo.get(item_id, 0) - self.reserved_cargo(item_id, excluding_contract))

    def remove(self, item_id, quantity, *, contract_offer_id=None):
        if type(quantity) is not int or quantity < 1:
            raise ValueError("Cargo removal must be a positive whole quantity")
        if contract_offer_id is not None:
            contract = next((row for row in self.contracts
                             if row["offer_id"] == contract_offer_id and row["status"] == "active"), None)
            if (contract is None or contract["item"] != item_id
                    or quantity > contract["quantity"]):
                raise ValueError("Only the matching active delivery may consume its reserved cargo")
        if self.available_cargo(item_id, excluding_contract=contract_offer_id) < quantity:
            raise ValueError("That cargo is committed to an active delivery contract")
        left = self.cargo[item_id] - quantity
        if left:
            self.cargo[item_id] = left
        else:
            del self.cargo[item_id]

    def require_hold(self, world):
        if not world.fixtures.available("hold"):
            raise ValueError("The hold fixture is being moved")
        x, y = world.ship.stations["hold"]
        if abs(world.player.x - x) + abs(world.player.y - y) > 1:
            raise ValueError("Stand beside the ship's hold to allocate cargo or issue equipment")

    def allocate(self, world, item_id, quantity):
        self.require_hold(world)
        if (item_id not in CATALOG or type(quantity) is not int or quantity < 1
                or self.available_cargo(item_id) < quantity):
            raise ValueError("Not enough cargo")
        item = CATALOG[item_id]
        if "container" in item:
            metadata = item["container"]
            owner = metadata.get("owner")
            if (metadata.get("liquid") != "clean_water_ml"
                    or type(metadata.get("capacity_ml")) is not int or metadata["capacity_ml"] < 1
                    or type(metadata.get("tare_g")) is not int or metadata["tare_g"] != item["weight_g"]):
                raise ValueError("Reusable container catalog data is invalid")
            if owner is not None and not world.ship.sailor(owner):
                raise ValueError("Reusable container owner is not aboard")
            for _ in range(quantity):
                world.ship.add_container(item_id, metadata["capacity_ml"], metadata["tare_g"], owner)
            self.remove(item_id, quantity)
            self.record(f"Allocated {quantity} empty {item['name']} container(s) aboard.")
            world.advance(1000)
            world.message(f"Stowed {quantity} empty {item['name']} container(s) at the water station.")
            return
        if "supply" not in item:
            raise ValueError("This item is equipment or a trade commodity, not a consumable ship supply")
        amount = item["yield"] * quantity
        # Respect physical pile capacity and space needed by outstanding
        # transfers / returned reservations from the spatial-storage model.
        from pirates.ship_storage import bundle_mass
        storage = world.ship.storage
        resource = item["supply"]
        pid = "water" if resource == "clean_water_ml" else "hold"
        if not storage.available(pid, world.fixtures):
            raise ValueError("Destination supply fixture is being relocated")
        incoming = {resource: amount}
        pending = [j.transfer | {"delivered": j.state in ("done", "cancelled")}
                   for j in world.ship.jobs if j.transfer]
        return_mass = sum(bundle_mass(row["bundle"]) for j in world.ship.jobs
                          if j.state not in ("done", "cancelled") for row in j.supply_plan
                          if row["pile"] == pid and not row["returned"])
        transfer_mass = sum(bundle_mass(j.transfer["bundle"]) for j in world.ship.jobs
                            if j.transfer and j.state not in ("done", "cancelled")
                            and j.transfer["destination"] == pid)
        pile = storage.piles[pid]
        if (not storage.reserve_capacity(pid, incoming, pending)
                or bundle_mass(pile.contents) + bundle_mass(incoming) + return_mass + transfer_mass > pile.capacity_kg):
            raise ValueError("Destination stockpile lacks unreserved capacity")
        world.ship.stores[resource] += amount
        if resource == "fruit_g":
            world.ship.add_fresh_food_lot(resource, amount)
        self.remove(item_id, quantity)
        self.record(f"Allocated {quantity} {item['name']} -> {resource_quantity(resource, amount)}.")
        world.advance(1000)
        world.message(f"Allocated {resource_quantity(resource, amount)} {item['name']} to ship stores.")

    def equip(self, world, sid, item_id):
        self.require_hold(world)
        sailor = world.ship.sailor(sid)
        item = CATALOG.get(item_id, {})
        if not sailor or item.get("slot") not in SLOTS or self.available_cargo(item_id) < 1:
            raise ValueError("Choose an available piece of equipment and an existing sailor")
        equipment = self.gear.setdefault(sid, {})
        old = equipment.get(item["slot"])
        if old and self.gear_condition.get(sid, {}).get(item["slot"], 100) < 100:
            raise ValueError("Repair the worn garment before replacing it")
        self.remove(item_id, 1)
        if old:
            self.cargo[old] = self.cargo.get(old, 0) + 1
            self.gear_condition.get(sid, {}).pop(item["slot"], None)
            self.gear_ids.get(sid, {}).pop(item["slot"], None)
        equipment[item["slot"]] = item_id
        self.gear_condition.setdefault(sid, {})[item["slot"]] = 100
        self.gear_ids.setdefault(sid, {})[item["slot"]] = f"garment-{self.next_gear_id}"
        self.next_gear_id += 1
        sailor.think("The captain issued useful new equipment.", 2)
        world.advance(1000)
        world.message(f"Issued {item['name']} to {sailor.name}; old kit returned to cargo.")

    def unequip(self, world, sid, slot):
        self.require_hold(world)
        if slot not in self.gear.get(sid, {}):
            raise ValueError("That equipment slot is empty")
        item_id = self.gear[sid].pop(slot)
        if self.gear_condition.get(sid, {}).get(slot, 100) < 100:
            self.gear[sid][slot] = item_id
            raise ValueError("Repair the worn garment before returning it to fungible cargo")
        self.gear_condition.get(sid, {}).pop(slot, None)
        self.gear_ids.get(sid, {}).pop(slot, None)
        self.cargo[item_id] = self.cargo.get(item_id, 0) + 1
        world.advance(1000)

    def mend_garment(self, world, sid, slot):
        self.require_hold(world)
        item_id = self.gear.get(sid, {}).get(slot)
        if not item_id or CATALOG[item_id].get("category") != "clothing":
            raise ValueError("Choose an issued garment to mend")
        condition = self.gear_condition.get(sid, {}).get(slot, 100)
        if condition >= 100:
            raise ValueError("That garment is already in good condition")
        if self.available_cargo("sewing_kit") < 1 or self.available_cargo("sailcloth") < 1:
            raise ValueError("Mending needs a reusable sewing kit and one bolt of sailcloth")
        self.remove("sailcloth", 1)
        self.gear_condition[sid][slot] = 100
        self.record(f"Mended {CATALOG[item_id]['name']} for {sid}; consumed one bolt of sailcloth.")
        world.advance(60000)
        world.message(f"Mended {CATALOG[item_id]['name']} to 100% condition; one bolt of sailcloth was consumed.")

    def pack_transfer(self, world, index, to_pack=False, quantity=1):
        self.require_hold(world)
        if type(quantity) is not int or quantity < 1:
            raise ValueError("Choose a positive quantity")
        if to_pack:
            item_id = index
            if self.available_cargo(item_id) < quantity:
                raise ValueError("Not enough cargo")
            item = CATALOG[item_id]
            stack = {"id": item_id, "name": item["name"], "weight_g": item["weight_g"], "bulk_ml": item["bulk_ml"], "quantity": quantity}
            if not world.inventory.add(stack):
                raise ValueError("Your satchel cannot fit that stack")
            self.remove(item_id, quantity)
        else:
            if type(index) is not int or not 0 <= index < len(world.inventory):
                raise ValueError("Invalid inventory selection")
            stack = world.inventory[index]
            item_id = stack["id"]
            if item_id not in CATALOG or quantity > stack["quantity"]:
                raise ValueError("Only catalogued goods can be transferred to trade cargo")
            canonical = CATALOG[item_id]
            if (stack.get("weight_g") != canonical["weight_g"] or stack.get("bulk_ml") != canonical["bulk_ml"]
                    or stack.get("condition", 100) != 100 or stack.get("quality", 50) != 50):
                raise ValueError("Only standard, undamaged goods can join fungible trade cargo")
            mass, bulk = self.load()
            if (mass + CATALOG[item_id]["weight_g"] * quantity > TRADE_MASS_G
                    or bulk + CATALOG[item_id]["bulk_ml"] * quantity > TRADE_BULK_ML):
                raise ValueError("Trade cargo is full")
            self.cargo[item_id] = self.cargo.get(item_id, 0) + quantity
            stack["quantity"] -= quantity
            if not stack["quantity"]:
                world.inventory.pop(index)
        world.advance(1000)

    def bonus(self, sid, key, labor=None):
        equipment = self.gear.get(sid, {})
        items = [(CATALOG[item_id], self.gear_condition.get(sid, {}).get(slot, 100))
                 for slot, item_id in equipment.items()]
        if key == "labor":
            return 1 + sum(i.get("labor_bonus", {}).get(labor, 0) * condition / 100
                           for i, condition in items)
        return min([1 + (i.get(key, 1) - 1) * condition / 100 for i, condition in items] or [1])

    def drill(self, world, sid):
        self.require_hold(world)
        sailor = world.ship.sailor(sid)
        weapon_id = self.gear.get(sid, {}).get("weapon")
        if not sailor or not weapon_id:
            raise ValueError("Issue a weapon to this sailor first")
        if sailor.job_id is not None or sailor.intent or sailor.energy < 40 or max(sailor.hunger, sailor.thirst, sailor.sleep_debt) >= 65:
            raise ValueError("Sailor is busy or needs care; resolve work/needs before training")
        shot = CATALOG[weapon_id].get("practice_shot", 0)
        if world.ship.stores["shot"] < shot:
            raise ValueError("Firearm practice requires a ship shot charge")
        self.drilling = {"sid": sid, "elapsed": 0, "shot": shot, "started": False}
        session = self.drilling
        try:
            # Includes reaching the workshop; urgent needs interrupt rather than
            # silently awarding an entire drill while somebody eats or sleeps.
            for _ in range(300):
                if sailor.intent or max(sailor.hunger, sailor.thirst, sailor.sleep_debt) >= 65:
                    break
                world.advance(100)
                if session["elapsed"] >= 120:
                    break
        finally:
            self.drilling = None
        if session["elapsed"]:
            sailor.think("Practised with my own weapon on deck.", 2)
        text = f"{sailor.name}: arms drill {session['elapsed']}/120 seconds; practice gained only while training. Combat is not implemented."
        self.record(text)
        world.message(text)

    def drill_tick(self, world, sailor):
        session = self.drilling
        if not session or session["sid"] != sailor.id:
            return False
        sailor.activity = "Going to arms practice"
        if not world.fixtures.available("workshop") or not world.ship.walk(world, sailor, world.ship.stations["workshop"]):
            return True
        if not session["started"]:
            if world.ship.stores["shot"] < session["shot"]:
                sailor.activity = "Arms practice blocked: ammunition unavailable"
                return True
            world.ship.stores["shot"] -= session["shot"]
            session["started"] = True
        sailor.activity = "Arms practice at workshop"
        session["elapsed"] += 1
        sailor.energy = max(0, sailor.energy - 8 / 120)
        sailor.boredom = max(0, sailor.boredom - 25 / 120)
        self.weapon_xp[sailor.id] = self.weapon_xp.get(sailor.id, 0) + 1
        return True

    def near_passage(self, world):
        return any(abs(world.player.x - x) + abs(world.player.y - y) <= 2
                   for x, y in ((42, 23), world.ship.stations["helm"]))

    def remember_harbor(self, world):
        return {"explored": world.game_map.explored[:, :45].tolist(),
                "doors": world.game_map.doors[:, :45].tolist(),
                "ground": {f"{at[0]},{at[1]}": deepcopy(items)
                           for at, items in world.ground_items.items()
                           if len(at) == 2 and at[0] < 45}}

    def travel(self, world, destination):
        if destination not in PORTS or destination == self.port:
            raise ValueError("Choose the other island")
        if not self.near_passage(world):
            raise ValueError("Arrange coastal passage at the harbor office (42,23) or beside the helm")
        if not world.fixtures.available("helm"):
            raise ValueError("The helm is being relocated")
        if self.money < PASSAGE_FEE or world.ship.hull < 25:
            raise ValueError("Passage requires 15 Marks and at least 25 hull condition")
        occupied = {(c.x, c.y) for c in world.ship.crew}
        candidates = [(x, y) for y in range(21, 34) for x in range(70, 91)
                      if world.game_map.is_walkable(x, y) and (x, y) not in occupied]
        if not candidates:
            raise ValueError("No safe place aboard for the captain")
        self.harbors[self.port] = self.remember_harbor(world)
        self.money -= PASSAGE_FEE
        self.treasury += PASSAGE_FEE
        self.port = destination
        build_port(world)
        memory = self.harbors[destination]
        world.game_map.explored[:, :45] = memory["explored"]
        world.game_map.doors[:, :45] = memory["doors"]
        world.game_map.visible[:, :45] = False
        world.ground_items = {at: items for at, items in world.ground_items.items() if at[0] >= 45}
        world.ground_items.update({tuple(map(int, key.split(','))): deepcopy(items) for key, items in memory["ground"].items()})
        world.player.x, world.player.y = min(candidates, key=lambda at: (abs(at[0] - 76) + abs(at[1] - 25), at))
        world.advance(180000)
        self.record(f"Coastal passage to {PORTS[destination]['name']}: 15 Marks, 30 minutes.")
        world.message(f"Arrived at {PORTS[destination]['name']}. Coastal passage cost 15 Marks / 30 minutes; sailing is abstracted.")

    def snapshot(self, world):
        harbors = deepcopy(self.harbors)
        if not getattr(world, "at_sea", False):
            harbors[self.port] = self.remember_harbor(world)
        return {"port": self.port, "money": self.money, "treasury": self.treasury,
                "cargo": deepcopy(self.cargo), "gear": deepcopy(self.gear),
                "gear_condition": deepcopy(self.gear_condition), "gear_ids": deepcopy(self.gear_ids),
                "next_gear_id": self.next_gear_id, "weapon_xp": dict(self.weapon_xp),
                "last_day": self.last_day, "markets": deepcopy(self.markets), "history": list(self.history), "harbors": harbors,
                "recruitment": self.recruitment.snapshot(), "employment": self.employment.snapshot(),
                "trader_reputation": self.trader_reputation, "contracts": deepcopy(self.contracts)}

    @classmethod
    def restore(cls, data, world):
        economy = cls(world.clock.day)
        if data is None:
            return economy
        def integer(value, lo=0, hi=10**12):
            if type(value) is not int or not lo <= value <= hi:
                raise ValueError("Invalid economy integer")
            return value
        def goods(value):
            if not isinstance(value, dict) or any(k not in CATALOG for k in value):
                raise ValueError("Invalid item catalogue reference")
            return {k: integer(v, 0, 100000) for k, v in value.items()}
        if data["port"] not in PORTS or set(data["markets"]) != set(MERCHANTS):
            raise ValueError("Invalid island / merchant roster")
        economy.port = data["port"]
        economy.money = integer(data["money"])
        economy.treasury = integer(data["treasury"])
        economy.last_day = integer(data["last_day"], 1, world.clock.day)
        economy.cargo = goods(data["cargo"])
        economy.gear = data["gear"]
        economy.gear_condition = deepcopy(data.get("gear_condition", {}))
        economy.gear_ids = deepcopy(data.get("gear_ids", {}))
        economy.next_gear_id = integer(data.get("next_gear_id", 1), 1, 10**9)
        identities = {c.id for c in world.ship.crew}
        if not isinstance(economy.gear, dict) or any(sid not in identities for sid in economy.gear):
            raise ValueError("Invalid equipment owner")
        for equipment in economy.gear.values():
            if not isinstance(equipment, dict):
                raise ValueError("Invalid crew equipment slot")
            # Older v3 saves placed the oilskin in the shared clothing slot.
            if equipment.get("clothing") == "oilskin" and "coat" not in equipment:
                equipment["coat"] = equipment.pop("clothing")
            if any(slot not in SLOTS or item_id not in CATALOG
                   or CATALOG[item_id].get("slot") != slot for slot, item_id in equipment.items()):
                raise ValueError("Invalid crew equipment slot")
        if not isinstance(economy.gear_condition, dict) or any(sid not in identities for sid in economy.gear_condition):
            raise ValueError("Invalid garment condition owner")
        for sid, conditions in economy.gear_condition.items():
            if (not isinstance(conditions, dict)
                    or any(slot not in economy.gear.get(sid, {}) or type(value) is not int
                           or not 0 <= value <= 100 for slot, value in conditions.items())):
                raise ValueError("Invalid individual garment condition")
        if (not isinstance(economy.gear_ids, dict)
                or any(sid not in identities for sid in economy.gear_ids)):
            raise ValueError("Invalid individual garment identity owner")
        for sid, equipment in economy.gear.items():
            for slot in equipment:
                economy.gear_condition.setdefault(sid, {}).setdefault(slot, 100)
                economy.gear_ids.setdefault(sid, {}).setdefault(slot, f"legacy-{sid}-{slot}")
        seen_gear_ids = set()
        for sid, identities_by_slot in economy.gear_ids.items():
            if (not isinstance(identities_by_slot, dict)
                    or any(slot not in economy.gear.get(sid, {}) or not isinstance(gear_id, str)
                           or not gear_id or gear_id in seen_gear_ids
                           for slot, gear_id in identities_by_slot.items())):
                raise ValueError("Invalid individual garment identity")
            seen_gear_ids.update(identities_by_slot.values())
        if any(set(equipment) != set(economy.gear_ids.get(sid, {}))
               for sid, equipment in economy.gear.items()):
            raise ValueError("Issued equipment is missing an individual identity")
        economy.weapon_xp = {sid: integer(value) for sid, value in data["weapon_xp"].items()}
        if any(sid not in identities for sid in economy.weapon_xp):
            raise ValueError("Invalid weapon practice owner")
        for mid, market in data["markets"].items():
            stock = dict(MERCHANTS[mid]["stock"])
            stock.update(goods(market["stock"]))
            if any(mid not in CATALOG[k]["market_buyers"] for k in stock):
                raise ValueError("Invalid merchant goods")
            economy.markets[mid] = {"stock": stock, "money": integer(market["money"])}
        economy.history = data["history"]
        if not isinstance(economy.history, list) or len(economy.history) > 40 or any(not isinstance(v, str) for v in economy.history):
            raise ValueError("Invalid trade history")
        economy.trader_reputation = integer(data.get("trader_reputation", 0), 0, RULES["contracts"]["max_reputation"])
        rows = data.get("contracts", [])
        if not isinstance(rows, list) or len(rows) > RULES["contracts"]["ledger_limit"]:
            raise ValueError("Invalid merchant contract ledger")
        templates = {row["id"]: row for row in CONTRACT_TEMPLATES}
        seen_offers = set()
        economy.contracts = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Invalid merchant contract")
            template = templates.get(row.get("template_id"))
            required = {"offer_id", "template_id", "offer_day", "origin", "destination", "issuer",
                        "item", "quantity", "reward", "escrow", "deadline_day", "status"}
            if (set(row) != required or template is None or not isinstance(row["offer_id"], str)
                    or type(row["offer_day"]) is not int
                    or row["offer_id"] != f"{row['offer_day']}:{row['template_id']}"
                    or row["offer_id"] in seen_offers
                    or row["origin"] != template["origin"] or row["destination"] != template["destination"]
                    or row["issuer"] != template["issuer"] or row["item"] != template["item"]
                    or row["quantity"] != template["quantity"]
                    or row["deadline_day"] != row["offer_day"] + CONTRACT_DURATION_DAYS
                    or row["status"] not in ("active", "completed", "expired")):
                raise ValueError("Invalid merchant contract details")
            integer(row["offer_day"], 1, world.clock.day)
            integer(row["deadline_day"], 1)
            reward = integer(row["reward"], 1)
            contract_rules = RULES["contracts"]
            expected_reward = (CATALOG[row["item"]]["base_price"] * row["quantity"]
                               * contract_rules["reward_base_price_multiplier"]
                               + contract_rules["passage_allowance"])
            if reward != expected_reward:
                raise ValueError("Invalid merchant contract reward")
            escrow = integer(row["escrow"], 0, reward)
            if escrow != (reward if row["status"] == "active" else 0):
                raise ValueError("Merchant contract escrow disagrees with its status")
            seen_offers.add(row["offer_id"])
            economy.contracts.append(deepcopy(row))
        if sum(row["status"] == "active" for row in economy.contracts) > MAX_ACTIVE_CONTRACTS:
            raise ValueError("Too many active merchant contracts")
        economy.harbors = data["harbors"]
        economy.recruitment = RecruitmentPool.restore(data.get("recruitment"))
        recruited = {f"hire:{candidate_id}" for candidate_id, status in economy.recruitment.statuses.items()
                     if status == "recruited"}
        active = {s.id for s in world.ship.crew if s.id.startswith("hire:")}
        former = {row["id"] for row in world.ship.crew_history if row["id"].startswith("hire:")}
        if recruited != active | former:
            raise ValueError("Recruitment pool disagrees with active/former ship crew")
        economy.employment = EmploymentLedger.restore(data.get("employment"), world.clock.day)
        for sailor_id, agreement in economy.employment.records.items():
            if sailor_id not in active | former:
                raise ValueError("Employment agreement has no matching sailor")
            if agreement["status"] == "active" and sailor_id not in active:
                raise ValueError("Active employment agreement is not on the roster")
            if agreement["status"] == "closed" and sailor_id not in former:
                raise ValueError("Closed employment agreement is still on the roster")
        if not isinstance(economy.harbors, dict) or set(economy.harbors) != set(PORTS):
            raise ValueError("Invalid harbor memories")
        for port, harbor in economy.harbors.items():
            terrain = harbor_tiles(port)
            for field in ("explored", "doors"):
                grid = np.asarray(harbor[field])
                if grid.shape != (64, 45) or grid.dtype != np.bool_:
                    raise ValueError("Invalid harbor grid")
            if not isinstance(harbor["ground"], dict):
                raise ValueError("Invalid harbor ground")
            for key, stacks in harbor["ground"].items():
                x, y = map(int, key.split(','))
                if not 8 <= x < 45 or not 8 <= y < 56 or not isinstance(stacks, list):
                    raise ValueError("Invalid harbor pile")
                tile_id = int(terrain[y, x])
                if not (T.BY_ID[tile_id].walkable and (tile_id != T.DOOR or harbor["doors"][y][x])):
                    raise ValueError("Harbor pile is not on walkable terrain")
                for stack in stacks:
                    if not isinstance(stack, dict) or not all(isinstance(stack.get(k), str) for k in ("id", "name")):
                        raise ValueError("Invalid harbor item")
                    for k in ("weight_g", "bulk_ml", "quantity"):
                        integer(stack[k], 1 if k == "quantity" else 0)
        mass, bulk = economy.load()
        if mass > TRADE_MASS_G or bulk > TRADE_BULK_ML:
            raise ValueError("Saved trade cargo exceeds capacity")
        return economy
