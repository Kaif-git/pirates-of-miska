"""Rot (ROT 1): fresh food keeps its days, then returns to the earth.

Stacks date from their oldest unit: the first dawn a stack is seen, it is
dated; life-days later it softens into compost (half, the rest was eaten by
air). Wheat never rots — grain is patient. Stalls, pockets, packs, ground:
everything softens together at dawn, said aloud.
"""
from __future__ import annotations

def _shelf_table() -> dict:
    """Shelf lives in data/items.yaml (absent = keeps forever)."""
    try:
        from omnisim.entities.items import _items
        return {i: int(n["shelf"]) for i, n in _items().items()
                if n.get("shelf") is not None}
    except Exception:
        return {"bread": 3, "meal": 2, "fish": 3, "apple": 5,
                "herbs": 4, "flour": 6}


SHELF_LIFE = _shelf_table()
COMPOST_SHELF = 6  # stalls hold a working heap; the rest settles to earth


def _holders(village):  # type: ignore[no-untyped-def]
    """Every pocket in the village, player first."""
    yield village.player
    for n in village.npcs:
        if not n.comps.get("dead"):
            yield n


def _pack_of(holder):  # type: ignore[no-untyped-def]
    return holder.get("pocket", {}) if holder.get("role") != "player" \
        else holder.get("pack", {})


def rot_dawn(village, day: int) -> list:  # type: ignore[no-untyped-def]
    """Date undated stacks, soften the old into compost. Returns [who, what]."""
    from omnisim.entities.items import item_name
    rotted = []
    for holder in _holders(village):
        pack = _pack_of(holder)
        fresh = holder.comps.setdefault("fresh", {})
        for item in list(pack.keys()):
            qty = pack.get(item, 0)
            if qty <= 0:
                fresh.pop(item, None)
                continue
            if item not in SHELF_LIFE:
                continue
            if item not in fresh:
                fresh[item] = day  # first seen: the benefit of the doubt
                continue
            if day - fresh[item] >= SHELF_LIFE[item]:
                keep = max(1, qty // 2)  # half returns to the earth
                del pack[item]
                del fresh[item]
                pack["compost"] = pack.get("compost", 0) + keep
                rotted.append((holder.name, item, qty))
    for stall in village.stalls:
        stock = stall.get("stock", {})
        age = stall.setdefault("age", {})
        for item in list(stock.keys()):
            qty = stock[item][0] if isinstance(stock[item], list) else 0
            if qty <= 0:
                age.pop(item, None)
                continue
            if item not in SHELF_LIFE:
                continue
            if item not in age:
                age[item] = day
                continue
            if day - age[item] >= SHELF_LIFE[item]:
                keep = max(1, qty // 2)
                stock[item][0] = 0
                del age[item]
                cur = stock.get("compost", [0, 1])
                stock["compost"] = [cur[0] + keep, cur[1] if isinstance(cur, list) else 1]
                rotted.append((stall.get("name", "a stall"), item, qty))
    for tile in list(village.ground.keys()):
        pile = village.ground[tile]
        lore = village.ground_lore.setdefault(tile, {})
        for item in list(pile.keys()):
            if item not in SHELF_LIFE:
                continue
            dropped = lore.get(item, {}).get("day", day)
            if day - dropped >= SHELF_LIFE[item]:
                qty = pile[item]
                keep = max(1, qty // 2)
                del pile[item]
                lore.pop(item, None)
                pile["compost"] = pile.get("compost", 0) + keep
                lore["compost"] = {"by": "rot", "origin": "the ground", "day": day}
                rotted.append(("the ground", item, qty))
        if not pile:
            del village.ground[tile]
    for who, item, qty in rotted:
        if who == "You":
            village.say(f"Your {item_name(item)} softens ({qty} rot to compost).")
        else:
            village.say(f"{who}'s {item_name(item)} softens ({qty} rot to compost).")
    for stall in village.stalls:  # ROT: the heap settles — shelves hold a working pile
        stock = stall.get("stock", {})
        if isinstance(stock.get("compost"), list) and stock["compost"][0] > COMPOST_SHELF:
            stock["compost"][0] = COMPOST_SHELF
    return rotted
