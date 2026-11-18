"""Port conversations, transparent quotes, cargo and useful crew equipment."""
from pirates.economy import (CATALOG, MERCHANTS, PORTS, TRADE_MASS_G, TRADE_BULK_ML,
                             MAX_ACTIVE_CONTRACTS, DELIVERY_FEE, PASSAGE_FEE, RULES,
                             CONTRACT_TEMPLATES)
from pirates.management import choose, panel
from pirates.ship_storage import resource_quantity


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
            f"Purchases: {DELIVERY_FEE} Marks per order + 1 minute for porters to deliver to trade cargo.", ""]
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
        goods = [key for key, n in source.items() if n > 0 and mid in CATALOG[key]["market_buyers"]]
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
            "Confirm transaction", "Inspect item facts / utility", "Cancel"],
            "Total includes stock-sensitive unit prices" + (f" and {DELIVERY_FEE} Marks delivery; advances 1 minute." if buying else "; advances 10 seconds."))
        if confirmed == 1:
            panel(renderer, world, item["name"], item_detail_rows(key))
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
        rows.append(f"{c.name}: " + (", ".join(
            f"{slot}: {CATALOG[key]['name']} ({economy.gear_condition.get(c.id, {}).get(slot, 100)}%"
            + (" — WEAR" if economy.gear_condition.get(c.id, {}).get(slot, 100) <= 25 else "") + ")"
            for slot, key in kit.items()) or "No issued kit"))
        rows.append(f"  Weapon practice: {economy.weapon_xp.get(c.id, 0)}; combat not implemented")
    return rows


def item_detail_rows(item_id):
    item = CATALOG[item_id]
    production = item["production"]
    found = [f"{PORTS[row['port']]['name']} — {MERCHANTS[row['merchant']]['name']}"
             for row in item["found_at"]]
    imports = [PORTS[port]["name"] for port in item.get("demand_ports", [])]
    rows = [item["description"], f"Each: {item['weight_g']} g / {item['bulk_ml']} ml",
            f"Produced: {production['method']}",
            "Inputs: " + (", ".join(production["inputs"]) or "not listed") +
            (" (lore only; production is not simulated)" if not production["simulated"] else ""),
            "Found at: " + "; ".join(found)]
    if imports:
        rows.append("Regional buyers: " + ", ".join(imports))
    return rows


def cargo_menu(renderer, world):
    economy = world.economy
    while True:
        index = choose(renderer, world, f"Trade cargo / crew equipment | {economy.money} Marks", [
            "Inspect cargo and equipment", "Allocate food, clean water, wood, ammunition or containers",
            "Issue clothing, tool or weapon to a sailor", "Return crew equipment to cargo",
            "Move a satchel stack into trade cargo", "Move trade cargo into satchel",
            "Arms drill (up to 5 minutes; needs can interrupt)", "Mend an issued garment",
            "Trade history"])
        if index is None:
            return
        if index == 0:
            panel(renderer, world, "Cargo / kit", cargo_rows(world))
        elif index in (1, 2, 5):
            goods = [key for key, n in economy.cargo.items() if n > 0 and (
                index == 5 or (("supply" in CATALOG[key] or "container" in CATALOG[key])
                                if index == 1 else "slot" in CATALOG[key]))]
            if not goods:
                panel(renderer, world, "Cargo", ["No suitable goods in trade cargo."])
                continue
            selected = choose(renderer, world, "Choose cargo", [
                (f"{CATALOG[k]['name']} x{economy.cargo[k]} — "
                 f"{resource_quantity(CATALOG[k]['supply'], CATALOG[k]['yield'])} each"
                 if "supply" in CATALOG[k] else
                 f"{CATALOG[k]['name']} x{economy.cargo[k]} — empty tare {CATALOG[k]['container']['tare_g']} g, "
                 f"capacity {CATALOG[k]['container']['capacity_ml'] / 1000:.1f} L"
                 if "container" in CATALOG[k] else f"{CATALOG[k]['name']} x{economy.cargo[k]}")
                for k in goods])
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
            sailors = [c for c in world.ship.crew
                       if any(CATALOG[item].get("category") == "clothing"
                              and economy.gear_condition.get(c.id, {}).get(slot, 100) < 100
                              for slot, item in economy.gear.get(c.id, {}).items())]
            if not sailors:
                panel(renderer, world, "No worn garments", ["Issued clothing is currently in good condition."])
                continue
            who = choose(renderer, world, "Select a sailor with worn clothing", [c.name for c in sailors])
            if who is None:
                continue
            sailor = sailors[who]
            worn = [(slot, item) for slot, item in economy.gear[sailor.id].items()
                    if CATALOG[item].get("category") == "clothing"
                    and economy.gear_condition.get(sailor.id, {}).get(slot, 100) < 100]
            chosen = choose(renderer, world, "Mend which garment? One bolt sailcloth + reusable kit; 10 minutes",
                            [f"{CATALOG[item]['name']} — {economy.gear_condition[sailor.id][slot]}%"
                             for slot, item in worn])
            if chosen is not None:
                attempt(renderer, world, lambda: economy.mend_garment(world, sailor.id, worn[chosen][0]))
        elif index == 8:
            panel(renderer, world, "Trade / equipment history", economy.history or ["No trades yet."])


def port_menu(renderer, world):
    while True:
        index = choose(renderer, world, f"{PORTS[world.economy.port]['name']} — islands and coastal passage", [
            "Island guide / merchant locations", "Merchant contracts / trader reputation", "Meet crew prospects / recruit",
            f"Book passage to the other island ({PASSAGE_FEE} Marks, 30 minutes)"])
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
        elif index == 1:
            contract_menu(renderer, world)
        elif index == 2:
            candidates = world.economy.recruitment.at_port(world.economy.port)
            if not candidates:
                panel(renderer, world, "No crew prospects here", [
                    "Everyone currently seeking a berth at this port has already left or joined another crew.",
                    "The candidate roster is finite; no one appears merely because time passed."])
                continue
            selected = choose(renderer, world, "Crew prospects — an introduction is not a guarantee", [
                f"{candidate['name']} | age {candidate['age']} | {candidate['background']} | advance {candidate['asking_price']} Marks"
                for candidate in candidates])
            if selected is None:
                continue
            candidate = candidates[selected]
            rows = [f"{candidate['name']} — {candidate['background']}, age {candidate['age']}",
                    candidate["summary"], "", "Known ship skills:"]
            rows += [f"  {kind.title()}: {candidate['skills'][kind]}" for kind in candidate["skills"]]
            rows += ["", "Mental / command profile (0–100):"]
            rows += [f"  {key.replace('_', ' ').title()}: {value}"
                     for key, value in candidate["mental"].items()]
            rows += ["", f"Asking advance: {candidate['asking_price']} Marks",
                     "The recruit brings a hammock; it will be installed on a clear ship-deck tile.",
                     "Recruiting does not require a minimum sailing crew.",
                     "Their potential is a promise, not a guarantee of future skill or loyalty."]
            panel(renderer, world, "Crew prospect", rows)
            confirm = choose(renderer, world, f"Recruit {candidate['name']}?", [
                f"Offer the berth and pay {candidate['asking_price']} Marks",
                "Leave the offer open", "Decline this prospect"])
            if confirm == 0:
                attempt(renderer, world, lambda: world.economy.recruit_candidate(world, candidate["id"]))
                return
            if confirm == 2:
                world.economy.recruitment.decline(candidate["id"], world.economy.port)
                world.message(f"You declined {candidate['name']}'s offer to join.")
        else:
            destination = next(key for key in PORTS if key != world.economy.port)
            confirm = choose(renderer, world, f"Passage to {PORTS[destination]['name']}", [f"Confirm {PASSAGE_FEE} Marks / 30 minutes", "Cancel"],
                             "Crew needs and work advance during travel. This is not naval sailing simulation.")
            if confirm == 0:
                attempt(renderer, world, lambda: world.economy.travel(world, destination))
                return


def contract_menu(renderer, world):
    economy = world.economy
    while True:
        offers = economy.contract_offers(economy.port, world.clock.day)
        active = [row for row in economy.contracts if row["status"] == "active"]
        choices = []
        for offer in offers:
            item = CATALOG[offer["item"]]
            choices.append(f"Accept: {item['name']} x{offer['quantity']} → {PORTS[offer['destination']]['name']} | {offer['reward']} Marks | due day {offer['deadline_day']}")
        for contract in active:
            item = CATALOG[contract["item"]]
            choices.append(f"Deliver: {item['name']} x{contract['quantity']} at {PORTS[contract['destination']]['name']} | {contract['reward']} Marks")
        offer_count = len(offers)
        active_count = len(active)
        choices += [f"Trader ledger — reputation {economy.trader_reputation}; active {active_count}/{MAX_ACTIVE_CONTRACTS}", "Back"]
        selected = choose(renderer, world, "Merchant contract board — escrow-funded deliveries", choices)
        if selected is None or selected == len(choices) - 1:
            return
        if selected < offer_count:
            offer = offers[selected]
            item = CATALOG[offer["item"]]
            panel(renderer, world, "Delivery offer", [
                f"{item['name']} x{offer['quantity']}",
                f"{PORTS[offer['origin']]['name']} → {PORTS[offer['destination']]['name']}",
                f"Reward: {offer['reward']} Marks; issuer escrows the full reward when accepted.",
                f"Due by the end of day {offer['deadline_day']}. Undelivered orders expire and refund the issuer.",
                "The cargo remains yours to buy and carry; delivery consumes it. No ship supplies or equipment are required.",
            ])
            if choose(renderer, world, "Accept delivery contract?", ["Accept; reserve issuer escrow", "Cancel"]) == 0:
                attempt(renderer, world, lambda: economy.accept_contract(world, offer["offer_id"]))
            continue
        if selected < offer_count + active_count:
            contract = active[selected - offer_count]
            item = CATALOG[contract["item"]]
            prompt = choose(renderer, world, "Complete delivery?", [
                f"Deliver {contract['quantity']} {item['name']} for {contract['reward']} Marks",
                "Cancel"],
                f"Destination: {PORTS[contract['destination']]['name']}; deadline day {contract['deadline_day']}. Delivery consumes cargo and pays escrow once.")
            if prompt == 0:
                attempt(renderer, world, lambda: economy.deliver_contract(world, contract["offer_id"]))
            continue
        completed = sum(row["status"] == "completed" for row in economy.contracts)
        expired = sum(row["status"] == "expired" for row in economy.contracts)
        next_unlocks = [row["min_reputation"] for row in CONTRACT_TEMPLATES
                        if row["min_reputation"] > economy.trader_reputation]
        next_unlock = min(next_unlocks) if next_unlocks else None
        panel(renderer, world, "Trader ledger", [
            f"Trader reputation: {economy.trader_reputation}",
            f"Active delivery contracts: {active_count}/{MAX_ACTIVE_CONTRACTS}",
            f"Completed in retained ledger: {completed}", f"Expired in retained ledger: {expired}",
            (f"New orders unlock at reputation {next_unlock}." if next_unlock is not None
             else "All currently authored reputation tiers are unlocked."),
            f"Each completed delivery earns {RULES['contracts']['reputation_per_delivery']} trader reputation.",
            "Each day's offer can be accepted once; contracts are funded from finite merchant purses.",
        ])
