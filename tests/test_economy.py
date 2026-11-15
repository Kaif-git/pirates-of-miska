import json
from copy import deepcopy

import pytest

from pirates.game import World
from pirates.economy import CATALOG, MERCHANTS, PORTS, Economy, TRADE_MASS_G, harbor_tiles
from pirates.ship import LABORS
from pirates.ship_storage import bundle_mass, UNIT_MASS
from pirates.trading_ui import merchant_menu, cargo_menu, port_menu
from pirates.management import context_menu
from omnisim.world import tile as T


def quiet_world():
    world = World()
    for c in world.ship.crew:
        c.labors = {k: 0 for k in LABORS}
        c.hunger = c.thirst = c.fatigue = c.loneliness = c.boredom = 0
        c.sleep_debt = 20
    return world


def at_merchant(world, mid):
    x, y = MERCHANTS[mid]["at"]
    world.player.x, world.player.y = x + 1, y
    world.refresh()


def at_hold(world):
    x, y = world.ship.stations["hold"]
    world.player.x, world.player.y = x - 1, y
    world.refresh()


def passage(world, port):
    world.player.x, world.player.y = 42, 23
    world.refresh()
    world.economy.travel(world, port)


def purse_total(economy):
    return economy.money + economy.treasury + sum(m["money"] for m in economy.markets.values())


def test_two_islands_have_character_merchants_and_reciprocal_prices():
    world = World()
    e = world.economy
    assert len(e.merchants()) == 3
    assert len({m["name"] for m in MERCHANTS.values()}) == 6
    assert e.price("ada", "grain", True) < e.price("dara", "grain", True)
    assert e.price("brann", "carpenter_tools", True) < e.price("lina", "carpenter_tools", True)
    assert e.price("dara", "grain", False) > e.price("ada", "grain", True)
    assert e.price("lina", "carpenter_tools", False) > e.price("brann", "carpenter_tools", True)
    assert (harbor_tiles("verdant") != harbor_tiles("cinder")).any()
    assert harbor_tiles("verdant")[33, 14] == T.FARM
    assert harbor_tiles("cinder")[33, 14] == T.HEARTH


def test_supply_item_weights_match_spatial_supply_units():
    for item in CATALOG.values():
        resource = item.get("supply")
        if resource in UNIT_MASS:
            assert item["weight_g"] == item["yield"] * UNIT_MASS[resource] * 1000


def test_buy_and_sell_conserve_money_stock_and_cargo():
    world = quiet_world()
    e = world.economy
    at_merchant(world, "ada")
    total = purse_total(e)
    stock = e.markets["ada"]["stock"]["grain"]
    quoted = e.quote("ada", "grain", 5, True)
    charged = e.trade(world, "ada", "grain", 5, True)
    assert charged == quoted and e.money == 600 - quoted
    assert e.cargo["grain"] == 5 and e.markets["ada"]["stock"]["grain"] == stock - 5
    assert purse_total(e) == total and e.treasury == 2
    assert world.ship.seconds == 60
    e.trade(world, "ada", "grain", 5, False)
    assert "grain" not in e.cargo
    assert e.markets["ada"]["stock"]["grain"] == stock
    assert e.money < 600  # no same-port buy/sell exploit
    assert purse_total(e) == total


@pytest.mark.parametrize("quantity", [0, -1, True, 1.5, 1000])
def test_invalid_quantities_are_atomic(quantity):
    world = quiet_world()
    at_merchant(world, "ada")
    before = world.economy.snapshot(world)
    clock = world.clock.total_ticks
    with pytest.raises(ValueError):
        world.economy.trade(world, "ada", "grain", quantity, True)
    assert world.economy.snapshot(world) == before
    assert world.clock.total_ticks == clock


@pytest.mark.parametrize("problem", ["money", "capacity", "stock", "remote", "category", "wrong_island", "merchant_funds", "no_cargo"])
def test_failed_transactions_leave_everything_unchanged(problem):
    world = quiet_world()
    e = world.economy
    at_merchant(world, "ada")
    mid, key, quantity, buying = "ada", "grain", 1, True
    if problem == "money":
        e.money = 0
    elif problem == "capacity":
        e.cargo = {"water_cask": TRADE_MASS_G // 10000}
    elif problem == "stock":
        quantity = 99
    elif problem == "remote":
        world.player.x = 76
    elif problem == "category":
        key = "cutlass"
    elif problem == "wrong_island":
        mid = "brann"
    elif problem == "merchant_funds":
        e.cargo["grain"] = 1
        e.markets["ada"]["money"] = 0
        buying = False
    elif problem == "no_cargo":
        buying = False
    before = e.snapshot(world)
    clock = world.clock.total_ticks
    with pytest.raises(ValueError):
        e.trade(world, mid, key, quantity, buying)
    assert e.snapshot(world) == before
    assert world.clock.total_ticks == clock


def test_full_trade_route_is_profitable_after_passage_and_delivery_costs():
    world = quiet_world()
    e = world.economy
    starting = e.money
    at_merchant(world, "ada")
    e.trade(world, "ada", "grain", 10, True)
    geometry = world.ship_model.snapshot()
    passage(world, "cinder")
    at_merchant(world, "dara")
    e.trade(world, "dara", "grain", 10, False)
    at_merchant(world, "brann")
    e.trade(world, "brann", "carpenter_tools", 5, True)
    passage(world, "verdant")
    at_merchant(world, "lina")
    e.trade(world, "lina", "carpenter_tools", 5, False)
    assert e.money > starting
    assert not e.cargo
    assert world.ship_model.snapshot() == geometry
    assert not world.ship.check_orders()


def test_allocate_real_food_water_wood_and_ammunition_once():
    world = quiet_world()
    e = world.economy
    at_hold(world)
    e.cargo = {"grain": 1, "water_cask": 1, "timber": 1, "shot": 1, "biscuit": 1}
    before = dict(world.ship.stores)
    meals = world.ship.meals
    for key in list(e.cargo):
        e.allocate(world, key, 1)
    assert not e.cargo
    assert world.ship.stores["raw_food"] == before["raw_food"] + 4
    assert world.ship.stores["water"] == before["water"] + 10
    assert world.ship.stores["planks"] == before["planks"] + 1
    assert world.ship.stores["shot"] == before["shot"] + 1
    assert world.ship.meals == meals + 1
    with pytest.raises(ValueError):
        e.allocate(world, "grain", 1)


def test_supply_allocation_respects_actual_stockpile_capacity():
    world = quiet_world()
    at_hold(world)
    world.economy.cargo["grain"] = 1
    pile = world.ship.storage.piles["hold"]
    pile.capacity_kg = bundle_mass(pile.contents)
    before = world.economy.snapshot(world)
    stores = dict(world.ship.stores)
    with pytest.raises(ValueError, match="capacity"):
        world.economy.allocate(world, "grain", 1)
    assert world.economy.snapshot(world) == before and dict(world.ship.stores) == stores


def test_allocation_leaves_space_for_both_incoming_transfers_and_job_returns():
    world = quiet_world()
    at_hold(world)
    c = world.ship.crew[0]
    c.labors["cook"] = 1
    world.ship.claim(world, c)
    assert world.ship.job(c.job_id).kind == "cook"
    storage = world.ship.storage
    source = storage.designate(world.ship_model, world.ship_fixtures, 78, 27, 100)
    source.contents["raw_food"] = 5
    storage.transfer_job(world, source.id, "hold", {"raw_food": 5})
    pile = storage.piles["hold"]
    pile.capacity_kg = bundle_mass(pile.contents) + 10  # return 2 + transfer 5 + new grain 4 = 11
    world.economy.cargo["grain"] = 1
    stores = dict(world.ship.stores)
    with pytest.raises(ValueError, match="capacity"):
        world.economy.allocate(world, "grain", 1)
    assert world.economy.cargo["grain"] == 1 and dict(world.ship.stores) == stores


def test_cannot_allocate_or_issue_equipment_remotely():
    world = quiet_world()
    world.economy.cargo = {"grain": 1, "mop": 1}
    with pytest.raises(ValueError):
        world.economy.allocate(world, "grain", 1)
    with pytest.raises(ValueError):
        world.economy.equip(world, "mara", "mop")


def test_trading_requires_current_visibility_not_just_distance():
    world = quiet_world()
    at_merchant(world, "ada")
    x, y = MERCHANTS["ada"]["at"]
    world.game_map.visible[y, x] = False
    before = world.economy.snapshot(world)
    with pytest.raises(ValueError):
        world.economy.trade(world, "ada", "grain", 1, True)
    assert world.economy.snapshot(world) == before


def test_equipment_replaces_returns_and_never_duplicates():
    world = quiet_world()
    at_hold(world)
    e = world.economy
    e.cargo = {"mop": 1, "carpenter_tools": 1, "work_shirt": 1, "boots": 1, "cutlass": 1}
    load = e.load()
    e.equip(world, "mara", "mop")
    e.equip(world, "mara", "carpenter_tools")
    assert e.cargo["mop"] == 1
    for key in ("work_shirt", "boots", "cutlass"):
        e.equip(world, "mara", key)
    assert len(e.gear["mara"]) == 4 and e.load() == load
    e.unequip(world, "mara", "tool")
    assert e.cargo["carpenter_tools"] == 1 and e.load() == load
    assert e.bonus("mara", "fatigue_multiplier") == 0.85
    assert e.bonus("mara", "movement_multiplier") == 0.65


def test_equipped_tools_and_clothing_have_real_work_effects():
    a, b = quiet_world(), quiet_world()
    at_hold(b)
    b.economy.cargo = {"mop": 1, "work_shirt": 1}
    b.economy.equip(b, "mara", "mop")
    b.economy.equip(b, "mara", "work_shirt")
    a.advance(2000)
    for w in (a, b):
        c = w.ship.crew[0]
        c.x, c.y = 76, 26
        c.labors["clean"] = 1
        job = next(j for j in w.ship.jobs if j.kind == "clean")
        w.ship.assign(job.id, c.id)
        w.advance(500)
    ja = next(j for j in a.ship.jobs if j.kind == "clean")
    jb = next(j for j in b.ship.jobs if j.kind == "clean")
    assert jb.progress > ja.progress
    assert b.ship.crew[0].fatigue < a.ship.crew[0].fatigue


def test_pack_and_cargo_transfers_respect_weight_and_preserve_stacks():
    world = quiet_world()
    at_hold(world)
    e = world.economy
    e.cargo = {"biscuit": 3, "timber": 2}
    e.pack_transfer(world, "biscuit", True, 2)
    assert e.cargo["biscuit"] == 1
    assert world.inventory[0]["quantity"] == 2
    e.pack_transfer(world, 0, False, 2)
    assert e.cargo["biscuit"] == 3 and not world.inventory
    with pytest.raises(ValueError, match="satchel"):
        e.pack_transfer(world, "timber", True, 2)
    assert e.cargo["timber"] == 2


def test_pack_transfer_does_not_launder_nonstandard_or_damaged_goods():
    world = quiet_world()
    at_hold(world)
    world.inventory.add({"id": "rope", "name": "Damaged rope", "quantity": 1,
                         "weight_g": CATALOG["rope"]["weight_g"], "bulk_ml": CATALOG["rope"]["bulk_ml"], "condition": 20})
    before = deepcopy(world.inventory.items)
    with pytest.raises(ValueError, match="undamaged"):
        world.economy.pack_transfer(world, 0, False, 1)
    assert world.inventory.items == before and not world.economy.cargo


def test_armed_drills_are_real_practice_not_simultaneous_jobs():
    world = quiet_world()
    at_hold(world)
    e = world.economy
    e.cargo = {"flintlock": 1}
    e.equip(world, "mara", "flintlock")
    c = world.ship.crew[0]
    shots = world.ship.stores["shot"]
    e.drill(world, "mara")
    assert e.weapon_xp["mara"] == 120
    assert world.ship.stores["shot"] == shots - 1
    assert c.completed == 0 and c.job_id is None
    assert c.energy < 100 and c.boredom < 1
    assert e.drilling is None


def test_drill_refuses_busy_or_needy_sailor_without_consuming_ammo():
    world = quiet_world()
    at_hold(world)
    world.economy.gear = {"mara": {"weapon": "flintlock"}}
    world.ship.crew[0].thirst = 90
    shots = world.ship.stores["shot"]
    with pytest.raises(ValueError):
        world.economy.drill(world, "mara")
    assert world.ship.stores["shot"] == shots and not world.economy.weapon_xp


def test_port_travel_preserves_separate_ground_and_exploration():
    world = quiet_world()
    world.game_map.explored[35, 15] = True
    original_ground = deepcopy(world.ground_items[(31, 23)])
    passage(world, "cinder")
    assert (31, 23) not in world.ground_items
    assert not world.game_map.explored[35, 15]
    world.game_map.explored[12, 12] = True
    world.ground_items[(32, 30)] = [deepcopy(original_ground[0])]
    passage(world, "verdant")
    assert world.ground_items[(31, 23)] == original_ground
    assert (32, 30) not in world.ground_items
    assert world.game_map.explored[35, 15]
    passage(world, "cinder")
    assert (32, 30) in world.ground_items and world.game_map.explored[12, 12]


def test_travel_conditions_are_enforced_without_geometry_mutation():
    world = quiet_world()
    before = world.economy.snapshot(world)
    geometry = world.ship_model.snapshot()
    with pytest.raises(ValueError):
        world.economy.travel(world, "cinder")
    assert world.economy.snapshot(world) == before and world.ship_model.snapshot() == geometry
    world.player.x, world.player.y = 42, 23
    world.economy.money = 0
    with pytest.raises(ValueError):
        world.economy.travel(world, "cinder")


def test_daily_regional_production_and_import_consumption_are_bounded():
    e = Economy()
    e.markets["ada"]["stock"]["grain"] = 0
    e.markets["brann"]["stock"]["carpenter_tools"] = 0
    e.markets["dara"]["stock"]["grain"] = 10
    e.update_day(2)
    assert e.markets["ada"]["stock"]["grain"] > 0
    assert e.markets["brann"]["stock"]["carpenter_tools"] > 0
    assert e.markets["dara"]["stock"]["grain"] == 8
    e.update_day(100000)
    assert e.markets["ada"]["stock"]["grain"] <= 60
    assert e.markets["dara"]["stock"]["grain"] == 0


@pytest.mark.parametrize("port", ["verdant", "cinder"])
def test_economy_roundtrip_preserves_money_markets_cargo_gear_and_ports(tmp_path, port):
    world = quiet_world()
    e = world.economy
    at_merchant(world, "ada")
    e.trade(world, "ada", "grain", 2, True)
    if port == "cinder":
        passage(world, port)
    at_hold(world)
    e.cargo["mop"] = 1
    e.equip(world, "mara", "mop")
    path = tmp_path / "trade.json"
    world.save(path)
    restored = World.load(path)
    assert restored.economy.snapshot(restored) == e.snapshot(world)
    assert (restored.game_map.tiles == world.game_map.tiles).all()
    assert not restored.ship.check_orders()


@pytest.mark.parametrize("bad", ["money", "stock", "item", "slot", "owner", "port", "memory", "wall_pile"])
def test_bad_economy_save_is_rejected_atomically(tmp_path, bad):
    world = quiet_world()
    path = tmp_path / "bad.json"
    world.save(path)
    before = world.economy.snapshot(world)
    data = json.loads(path.read_text())
    e = data["economy"]
    if bad == "money":
        e["money"] = -1
    elif bad == "stock":
        e["markets"]["ada"]["stock"]["grain"] = -1
    elif bad == "item":
        e["cargo"]["nonsense"] = 1
    elif bad == "slot":
        e["gear"]["mara"] = {"weapon": "work_shirt"}
    elif bad == "owner":
        e["gear"]["nobody"] = {"tool": "mop"}
    elif bad == "port":
        e["port"] = "nonsense"
    elif bad == "memory":
        e["harbors"]["cinder"]["explored"] = []
    elif bad == "wall_pile":
        e["harbors"]["cinder"]["ground"]["19,11"] = [{"id": "rope", "name": "Rope", "weight_g": 1, "bulk_ml": 1, "quantity": 1}]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        World.load(path)
    assert world.economy.snapshot(world) == before


def test_old_save_gains_economy_and_migrates_new_stall_safely(tmp_path):
    world = quiet_world()
    path = tmp_path / "old.json"
    world.save(path)
    data = json.loads(path.read_text())
    data.pop("economy")
    data["player"] = [30, 20]
    path.write_text(json.dumps(data))
    restored = World.load(path)
    assert restored.economy.port == "verdant" and restored.economy.money == 600
    assert restored.game_map.is_walkable(restored.player.x, restored.player.y)


class Scripted:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.titles = []
    def render_list(self, world, title, *args, **kwargs):
        self.titles.append(title)
    def choose(self, count):
        return next(self.answers)
    def wait_any(self):
        pass


def test_browsing_multiple_merchants_and_cargo_never_ticks_time():
    world = quiet_world()
    before = world.clock.total_ticks
    at_merchant(world, "ada")
    merchant_menu(Scripted([0, 3, None]), world, "ada")
    at_merchant(world, "bo")
    merchant_menu(Scripted([0, None]), world, "bo")
    cargo_menu(Scripted([0, None]), world)
    port_menu(Scripted([0, None]), world)
    assert world.clock.total_ticks == before


def test_buy_ui_confirms_the_real_quote():
    world = quiet_world()
    at_merchant(world, "ada")
    quote = world.economy.quote("ada", "grain", 1, True)
    merchant_menu(Scripted([1, 0, 0, 0, None]), world, "ada")
    assert world.economy.cargo["grain"] == 1
    assert world.economy.money == 600 - quote


def test_enter_reaches_the_nearby_merchant_without_changing_existing_ship_options():
    world = quiet_world()
    at_merchant(world, "ada")
    renderer = Scripted([8, 0, None])  # first merchant follows the eight stable context actions
    context_menu(renderer, world)
    assert any("Ada Reed" in title for title in renderer.titles)
