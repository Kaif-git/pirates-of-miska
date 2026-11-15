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
from pirates.ship_storage import UNIT_MASS

CATALOG = json.loads((Path(__file__).resolve().parents[1] / "content/trade_items.json").read_text(encoding="utf-8"))
# A bought material cannot become heavier/lighter merely by allocating it.
# The construction/storage module owns standardized material charge masses.
for _item in CATALOG.values():
    if _item.get("supply") in UNIT_MASS:
        _item["weight_g"] = _item["yield"] * UNIT_MASS[_item["supply"]] * 1000
CATEGORIES = {item["category"] for item in CATALOG.values()}
PORTS = {
    "verdant": {"name": "Verdant Cay", "tagline": "Orchards, bean fields, fresh springs and timber yards.",
                "description": "Rich soil feeds the archipelago, but every axe and nail arrives by sea.",
                "factors": {"food": 55, "water": 55, "wood": 65, "textile": 90, "clothing": 100, "tools": 200, "weapons": 200, "metal": 230, "ammunition": 200}},
    "cinder": {"name": "Cinder Quay", "tagline": "Foundries, smoke-black stone and crowded workshops.",
               "description": "Iron and tools leave the furnaces daily. Thin volcanic soil cannot feed the workers.",
               "factors": {"food": 210, "water": 200, "wood": 200, "textile": 180, "clothing": 90, "tools": 65, "weapons": 65, "metal": 60, "ammunition": 65}},
}
MERCHANTS = {
    "ada": {"name": "Ada Reed", "title": "Growers' cooperative", "port": "verdant", "at": (30, 21),
            "accepts": ["food", "tools", "metal"], "stock": {"grain": 30, "beans": 24, "fruit": 16},
            "dialogue": ["Our fields grow enough for three islands, but our sickles are worn thin.", "Cinder's workers pay well for food. Bring us tools and iron on your return."]},
    "bo": {"name": "Bo Tidewell", "title": "Fishery and spring keeper", "port": "verdant", "at": (35, 27),
           "accepts": ["food", "water", "tools", "ammunition"], "stock": {"water_cask": 18, "dried_fish": 24, "biscuit": 30},
           "dialogue": ["Spring water and salted fish. Neither is cheap in the foundry islands.", "A bucket and a sound knife last longer than any promise from a broker."]},
    "lina": {"name": "Lina Moss", "title": "Weaver and timber factor", "port": "verdant", "at": (25, 28),
             "accepts": ["wood", "textile", "clothing", "tools", "weapons"], "stock": {"timber": 16, "planks": 12, "sailcloth": 14, "work_shirt": 8, "rope": 8},
             "dialogue": ["We turn forest timber and island flax into cargo the foundries need.", "Good clothes ease a working day. A better tool is worth importing."]},
    "dara": {"name": "Dara Flint", "title": "Foundry workers' provisioner", "port": "cinder", "at": (30, 21),
             "accepts": ["food", "water", "wood", "textile"], "stock": {"biscuit": 8, "beans": 4, "water_cask": 5},
             "dialogue": ["A furnace cannot feed a family. We import almost every sack and water keg.", "Verdant's harvest and timber keep this island alive; our iron pays for them."]},
    "brann": {"name": "Brann Ash", "title": "Smith and arms maker", "port": "cinder", "at": (35, 27),
              "accepts": ["metal", "tools", "weapons", "wood", "ammunition"], "stock": {"iron": 24, "coal": 24, "shot": 24, "carpenter_tools": 10, "cutlass": 8, "flintlock": 5},
              "dialogue": ["Iron, sharp steel and sound tools. The farms need all three.", "My tool rolls help carpenters finish sooner. Don't mistake a loaded pistol for a trained sailor."]},
    "petra": {"name": "Petra Bell", "title": "Outfitter and tinsmith", "port": "cinder", "at": (25, 28),
              "accepts": ["clothing", "tools", "textile", "food"], "stock": {"cookware": 10, "bucket": 12, "mop": 12, "oilskin": 8, "boots": 10, "rope": 8},
              "dialogue": ["Work kit, not ornaments: buckets, cook pots, boots and coats.", "I need imported cloth. My customers need the food that arrives on your deck."]},
}
SLOTS = ("tool", "clothing", "feet", "weapon")
DELIVERY_FEE = 2
PASSAGE_FEE = 15
TRADE_MASS_G = 250000
TRADE_BULK_ML = 350000


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
        self.weapon_xp = {}
        self.last_day = day
        self.markets = {key: {"stock": dict(m["stock"]), "money": 1500} for key, m in MERCHANTS.items()}
        self.history = []
        self.drilling = None  # Synchronous drill session; never background simulation.
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
            port = PORTS[MERCHANTS[mid]["port"]]
            for item_id in list(market["stock"]):
                item = CATALOG[item_id]
                target = MERCHANTS[mid]["stock"].get(item_id, 0)
                if target and port["factors"][item["category"]] <= 100:
                    market["stock"][item_id] = min(target * 2, market["stock"][item_id] + max(1, target // 3) * elapsed)
                elif port["factors"][item["category"]] > 100:
                    market["stock"][item_id] = max(0, market["stock"][item_id] - 2 * elapsed)
        self.last_day = day

    def price(self, mid, item_id, buying, stock=None):
        merchant = MERCHANTS[mid]
        item = CATALOG[item_id]
        stock = self.markets[mid]["stock"].get(item_id, 0) if stock is None else stock
        target = merchant["stock"].get(item_id, 10)
        scarcity = max(80, min(150, 100 + (target - stock) * 3))
        regional = PORTS[merchant["port"]]["factors"][item["category"]]
        return max(1, item["base_price"] * regional * scarcity * (120 if buying else 65) // 1000000)

    def quote(self, mid, item_id, quantity, buying):
        if mid not in MERCHANTS or item_id not in CATALOG or type(quantity) is not int or not 1 <= quantity <= 999 or type(buying) is not bool:
            raise ValueError("Choose a known item and quantity from 1 to 999")
        if CATALOG[item_id]["category"] not in MERCHANTS[mid]["accepts"]:
            raise ValueError("This merchant does not deal in that category")
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
            if self.cargo.get(item_id, 0) < quantity:
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

    def remove(self, item_id, quantity):
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
                or self.cargo.get(item_id, 0) < quantity):
            raise ValueError("Not enough cargo")
        item = CATALOG[item_id]
        if "supply" not in item:
            raise ValueError("This item is equipment or a trade commodity, not a consumable ship supply")
        amount = item["yield"] * quantity
        if item["supply"] == "meals":
            world.ship.meals += amount
        else:
            # Respect physical pile capacity and space needed by outstanding
            # transfers / returned reservations from the parallel storage model.
            from pirates.ship_storage import bundle_mass
            storage = world.ship.storage
            resource = item["supply"]
            pid = "water" if resource == "water" else "hold"
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
            world.ship.stores[item["supply"]] += amount
        self.remove(item_id, quantity)
        self.record(f"Allocated {quantity} {item['name']} -> {amount} {item['supply']}.")
        world.advance(1000)
        world.message(f"Allocated {amount} {item['supply']} to crew supplies. Cargo is no longer available for sale.")

    def equip(self, world, sid, item_id):
        self.require_hold(world)
        sailor = world.ship.sailor(sid)
        item = CATALOG.get(item_id, {})
        if not sailor or item.get("slot") not in SLOTS or not self.cargo.get(item_id):
            raise ValueError("Choose an available piece of equipment and an existing sailor")
        equipment = self.gear.setdefault(sid, {})
        old = equipment.get(item["slot"])
        self.remove(item_id, 1)
        if old:
            self.cargo[old] = self.cargo.get(old, 0) + 1
        equipment[item["slot"]] = item_id
        sailor.think("The captain issued useful new equipment.", 2)
        world.advance(1000)
        world.message(f"Issued {item['name']} to {sailor.name}; old kit returned to cargo.")

    def unequip(self, world, sid, slot):
        self.require_hold(world)
        if slot not in self.gear.get(sid, {}):
            raise ValueError("That equipment slot is empty")
        item_id = self.gear[sid].pop(slot)
        self.cargo[item_id] = self.cargo.get(item_id, 0) + 1
        world.advance(1000)

    def pack_transfer(self, world, index, to_pack=False, quantity=1):
        self.require_hold(world)
        if type(quantity) is not int or quantity < 1:
            raise ValueError("Choose a positive quantity")
        if to_pack:
            item_id = index
            if self.cargo.get(item_id, 0) < quantity:
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
        items = [CATALOG[k] for k in self.gear.get(sid, {}).values()]
        if key == "labor":
            return 1 + sum(i.get("labor_bonus", {}).get(labor, 0) for i in items)
        return min([i.get(key, 1) for i in items] or [1])

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
                "ground": {f"{x},{y}": deepcopy(items) for (x, y), items in world.ground_items.items() if x < 45}}

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
                "cargo": deepcopy(self.cargo), "gear": deepcopy(self.gear), "weapon_xp": dict(self.weapon_xp),
                "last_day": self.last_day, "markets": deepcopy(self.markets), "history": list(self.history), "harbors": harbors}

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
        identities = {c.id for c in world.ship.crew}
        if not isinstance(economy.gear, dict) or any(sid not in identities for sid in economy.gear):
            raise ValueError("Invalid equipment owner")
        for equipment in economy.gear.values():
            if not isinstance(equipment, dict) or any(slot not in SLOTS or item_id not in CATALOG
                    or CATALOG[item_id].get("slot") != slot for slot, item_id in equipment.items()):
                raise ValueError("Invalid crew equipment slot")
        economy.weapon_xp = {sid: integer(value) for sid, value in data["weapon_xp"].items()}
        if any(sid not in identities for sid in economy.weapon_xp):
            raise ValueError("Invalid weapon practice owner")
        for mid, market in data["markets"].items():
            stock = goods(market["stock"])
            if any(CATALOG[k]["category"] not in MERCHANTS[mid]["accepts"] for k in stock):
                raise ValueError("Invalid merchant goods")
            economy.markets[mid] = {"stock": stock, "money": integer(market["money"])}
        economy.history = data["history"]
        if not isinstance(economy.history, list) or len(economy.history) > 40 or any(not isinstance(v, str) for v in economy.history):
            raise ValueError("Invalid trade history")
        economy.harbors = data["harbors"]
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
