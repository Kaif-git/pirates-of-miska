"""Port conversations, transparent quotes, cargo and useful crew equipment."""
from pirates.economy import CATALOG, MERCHANTS, PORTS, TRADE_MASS_G, TRADE_BULK_ML
from pirates.management import choose, panel


def quantity(renderer, world, maximum):
    amounts = sorted({n for n in (1, 5, 10, min(999, maximum)) if 0 < n <= maximum})
    if not amounts:
        return None
    index = choose(renderer, world, "Quantity", [str(n) for n in amounts] + ["Custom amount"])
    if index is None:
        return None
    if index == len(amounts):
        return renderer.wait_transfer_quantity(world, min(999, maximum))
    return amounts[index]


def attempt(renderer, world, action):
    try:
        action()
    except ValueError as error:
        world.message(str(error))
        panel(renderer, world, "Cannot complete transaction", [str(error), "No partial transaction was performed."])


def market_rows(world, mid):
    economy = world.economy
    merchant = MERCHANTS[mid]
    rows = [f"{merchant['name']} — {merchant['title']}", PORTS[merchant['port']]["name"],
            f"Your purse: {economy.money} Marks | Merchant purse: {economy.markets[mid]['money']} Marks",
            "Ask = you buy; bid = you sell. Prices change with local stock.",
            "Purchases: 2 Marks per order + 1 minute for porters to deliver to trade cargo.", ""]
    for key, stock in economy.markets[mid]["stock"].items():
        rows.append(f"{CATALOG[key]['name']}: stock {stock} | ask {economy.price(mid, key, True)} | bid {economy.price(mid, key, False)}")
    return rows


def merchant_menu(renderer, world, mid):
    try:
        world.economy.require_merchant(world, mid)
    except ValueError as error:
        panel(renderer, world, "Merchant unavailable", [str(error)])
        return
    merchant = MERCHANTS[mid]
    while True:
        index = choose(renderer, world, f"{merchant['name']} — {merchant['title']} | {world.economy.money} Marks", [
            "Talk: local life and trade needs", "Buy (delivered to ship cargo)", "Sell from ship trade cargo",
            "Inspect price list and merchant funds", "Leave"])
        if index is None or index == 4:
            return
        if index == 0:
            panel(renderer, world, merchant["name"], merchant["dialogue"] + ["", PORTS[merchant['port']]["description"]])
            continue
        if index == 3:
            panel(renderer, world, "Prices / trading terms", market_rows(world, mid))
            continue
        buying = index == 1
        source = world.economy.markets[mid]["stock"] if buying else world.economy.cargo
        goods = [key for key, n in source.items() if n > 0 and CATALOG[key]["category"] in merchant["accepts"]]
        if not goods:
            panel(renderer, world, "No goods", ["No suitable goods are available.", "Each merchant deals only in their listed categories."])
            continue
        selected = choose(renderer, world, "Buy" if buying else "Sell", [
            f"{CATALOG[k]['name']} | available {source[k]} | {'ask' if buying else 'bid'} {world.economy.price(mid, k, buying)} Marks/unit"
            for k in goods])
        if selected is None:
            continue
        key = goods[selected]
        amount = quantity(renderer, world, source[key])
        if amount is None:
            continue
        total = world.economy.quote(mid, key, amount, buying)
        item = CATALOG[key]
        confirmed = choose(renderer, world, f"{'Buy' if buying else 'Sell'} {amount} {item['name']}: {total} Marks", [
            "Confirm transaction", "Inspect item utility", "Cancel"],
            "Total includes stock-sensitive unit prices" + (" and 2 Marks delivery; advances 1 minute." if buying else "; advances 10 seconds."))
        if confirmed == 1:
            panel(renderer, world, item["name"], [item["description"], f"Each: {item['weight_g']} g / {item['bulk_ml']} ml"])
        elif confirmed == 0:
            attempt(renderer, world, lambda: world.economy.trade(world, mid, key, amount, buying))


def cargo_rows(world):
    economy = world.economy
    mass, bulk = economy.load()
    rows = [f"Your purse: {economy.money} Marks", f"Trade cargo + issued kit: {mass}/{TRADE_MASS_G} g | {bulk}/{TRADE_BULK_ML} ml",
            "Trade cargo is separate from satchel and allocated working provisions.",
            "Stand beside the hold to transfer supplies or issue equipment.", ""]
    for key, n in economy.cargo.items():
        rows += [f"{CATALOG[key]['name']} x{n}", "  " + CATALOG[key]["description"]]
    if not economy.cargo:
        rows.append("No trade cargo. Find merchants on shore; Enter opens conversations.")
    rows += ["", "CREW EQUIPMENT"]
    for c in world.ship.crew:
        kit = economy.gear.get(c.id, {})
        rows.append(f"{c.name}: " + (", ".join(f"{slot}: {CATALOG[key]['name']}" for slot, key in kit.items()) or "No issued kit"))
        rows.append(f"  Weapon practice: {economy.weapon_xp.get(c.id, 0)}; combat not implemented")
    return rows


def cargo_menu(renderer, world):
    economy = world.economy
    while True:
        index = choose(renderer, world, f"Trade cargo / crew equipment | {economy.money} Marks", [
            "Inspect cargo and equipment", "Allocate food / water / wood / ammunition to ship supplies",
            "Issue clothing, tool or weapon to a sailor", "Return crew equipment to cargo",
            "Move a satchel stack into trade cargo", "Move trade cargo into satchel",
            "Arms drill (up to 5 minutes; needs can interrupt)", "Trade history"])
        if index is None:
            return
        if index == 0:
            panel(renderer, world, "Cargo / kit", cargo_rows(world))
        elif index in (1, 2, 5):
            goods = [key for key, n in economy.cargo.items() if n > 0 and (
                index == 5 or ("supply" in CATALOG[key] if index == 1 else "slot" in CATALOG[key]))]
            if not goods:
                panel(renderer, world, "Cargo", ["No suitable goods in trade cargo."])
                continue
            selected = choose(renderer, world, "Choose cargo", [f"{CATALOG[k]['name']} x{economy.cargo[k]}" for k in goods])
            if selected is None:
                continue
            key = goods[selected]
            if index == 2:
                who = choose(renderer, world, "Issue to whom? Replaces the same slot safely.", [c.name for c in world.ship.crew])
                if who is not None:
                    attempt(renderer, world, lambda: economy.equip(world, world.ship.crew[who].id, key))
            else:
                amount = quantity(renderer, world, economy.cargo[key])
                if amount is not None:
                    attempt(renderer, world, lambda: economy.allocate(world, key, amount) if index == 1 else economy.pack_transfer(world, key, True, amount))
        elif index == 3:
            sailors = [c for c in world.ship.crew if economy.gear.get(c.id)]
            if not sailors:
                panel(renderer, world, "Crew kit", ["No equipment has been issued."])
                continue
            who = choose(renderer, world, "Whose equipment?", [c.name for c in sailors])
            if who is None:
                continue
            slots = list(economy.gear[sailors[who].id])
            slot = choose(renderer, world, "Return which item?", [f"{s}: {CATALOG[economy.gear[sailors[who].id][s]]['name']}" for s in slots])
            if slot is not None:
                attempt(renderer, world, lambda: economy.unequip(world, sailors[who].id, slots[slot]))
        elif index == 4:
            stacks = [(i, s) for i, s in enumerate(world.inventory) if s["id"] in CATALOG]
            if not stacks:
                panel(renderer, world, "Satchel", ["No catalogued trade goods in your satchel."])
                continue
            selected = choose(renderer, world, "Transfer which stack?", [f"{s['name']} x{s['quantity']}" for _, s in stacks])
            if selected is not None:
                i, stack = stacks[selected]
                amount = quantity(renderer, world, stack["quantity"])
                if amount is not None:
                    attempt(renderer, world, lambda: economy.pack_transfer(world, i, False, amount))
        elif index == 6:
            who = choose(renderer, world, "Select a rested, unoccupied, armed sailor", [c.name for c in world.ship.crew])
            if who is not None:
                attempt(renderer, world, lambda: economy.drill(world, world.ship.crew[who].id))
        elif index == 7:
            panel(renderer, world, "Trade / equipment history", economy.history or ["No trades yet."])


def port_menu(renderer, world):
    while True:
        index = choose(renderer, world, f"{PORTS[world.economy.port]['name']} — islands and coastal passage", [
            "Island guide / merchant locations", "Book passage to the other island (15 Marks, 30 minutes)"])
        if index is None:
            return
        if index == 0:
            rows = []
            for key, port in PORTS.items():
                rows += [port["name"] + (" (current harbor)" if key == world.economy.port else ""), port["tagline"], port["description"]]
                rows += [f"  {m['name']} — {m['title']} at {m['at']}" for m in MERCHANTS.values() if m["port"] == key]
                rows.append("")
            rows += ["Verdant exports food, water, wood and cloth; imports tools, metal and weapons.",
                     "Cinder exports manufactured goods; imports provisions, wood and cloth.",
                     "Production and local consumption update daily; purses and stocks are finite.",
                     "Coastal passage is abstracted, not simulated sailing.",
                     "Book it beside the helm or at the harbor office (42,23)."]
            panel(renderer, world, "Island guide", rows)
        else:
            destination = next(key for key in PORTS if key != world.economy.port)
            confirm = choose(renderer, world, f"Passage to {PORTS[destination]['name']}", ["Confirm 15 Marks / 30 minutes", "Cancel"],
                             "Crew needs and work advance during travel. This is not naval sailing simulation.")
            if confirm == 0:
                attempt(renderer, world, lambda: world.economy.travel(world, destination))
                return
