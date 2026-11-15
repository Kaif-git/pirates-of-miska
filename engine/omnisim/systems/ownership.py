"""ECON: who owns what land, and the due honest work pays.

Deeds live in data/land.yaml: the Fields belong to Elin, the Boar's oven
to Mira, shore and wilds to the Crown until some soul buys them. Whoever
works owned ground — villager or traveller — leaves every fourth handful
for the owner. Bram acts as the Crown's agent: land coin flows through
the Market reserve. No deeds screen, no lawyers: the log says it.
"""
from __future__ import annotations

import os

CROWN = "Crown"
BUYBACK = 0.7  # the Crown buys back at seventy cents on the copper
TITHE_EVERY = 4

_LAND = None


def land_spec():  # type: ignore[no-untyped-def]
    """data/land.yaml, loaded once (village parcels carry live ownership)."""
    global _LAND
    if _LAND is None:
        import yaml
        here = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        for cand in (os.path.join(here, "data", "land.yaml"),
                     os.path.join(os.getcwd(), "data", "land.yaml")):
            if os.path.exists(cand):
                with open(cand, encoding="utf-8") as fh:
                    _LAND = yaml.safe_load(fh) or {}
                break
        else:
            _LAND = {"parcels": []}
    return _LAND


def parcels_of(village):  # type: ignore[no-untyped-def]
    """Live deeds: village-carried so saves and sales persist."""
    live = getattr(village, "land_parcels", None)
    if live:
        return live
    import copy
    return copy.deepcopy(land_spec().get("parcels", []))


def sync_plots(village) -> None:  # type: ignore[no-untyped-def]
    """The board's good -> owner map follows the deeds."""
    plots = {}
    for p in parcels_of(village):
        if p.get("good"):
            plots[p["good"]] = p.get("owner")
    try:
        village.plots = plots
    except Exception:
        pass

LAND_NAMES = {"wheat": "the Fields", "bread": "the oven plot",
              "fish": "Stillpond water", "firewood": "the Old Woodlot"}

FIELD_GOODS = {"wheat", "barley", "turnip"}  # one deed, many crops


def fields_owner(village=None):  # type: ignore[no-untyped-def]
    """Who holds the Fields deed — every field crop tithes to them."""
    return plot_owner("wheat", village)


def plot_owner(good: str, village=None) -> str | None:  # type: ignore[no-untyped-def]
    """Deed-following owner: village deeds first, yaml genesis second."""
    if village is not None:
        for p in parcels_of(village):
            if p.get("good") == good:
                return p.get("owner")
        return None
    for p in land_spec().get("parcels", []):
        if p.get("good") == good:
            return p.get("owner")
    return None


def land_of(village, x: int, y: int):  # type: ignore[no-untyped-def]
    """What ground is this — (good, owner|None). None = nobody's land."""
    from omnisim.world import tile as T
    gm = village.game_map
    if not gm.in_bounds(x, y):
        return None
    if int(gm.tiles[y, x]) == T.FARM:
        _kind, _owner = "wheat", plot_owner("wheat", village)
        try:  # the tithe follows the CROP (barley tithes barley to the Fields)
            from omnisim.systems.crops import kind_of as _kok, plot_at as _pat
            _p = _pat(village, x, y)
            if _p is not None and kind_of(_p) in FIELD_GOODS:
                _kind = kind_of(_p)
        except Exception:
            pass
        return (_kind, _owner)
    posts = village.work_posts or {}
    for good in ("fish", "firewood"):
        post = posts.get(good)
        if post and max(abs(post["at"][0] - x), abs(post["at"][1] - y)) <= 3:
            return (good, plot_owner(good, village))
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if gm.in_bounds(x + dx, y + dy) and int(gm.tiles[y + dy, x + dx]) == T.HEARTH:
                return ("bread", plot_owner("bread", village))
    return None


def drop_at(village, x: int, y: int, item_id: str, qty: int, by: str, origin: str) -> None:  # type: ignore[no-untyped-def]
    """What won't fit goes on the floor — stamped, so haulers know it."""
    if qty <= 0:
        return
    from omnisim.core.clock import day_of
    ground = village.ground.setdefault((x, y), {})
    ground[item_id] = ground.get(item_id, 0) + qty
    village.ground_lore.setdefault((x, y), {})[item_id] = {
        "by": by, "origin": origin, "day": day_of(village.clock_pts)}


def pay_tithe(village, worker, good: str, qty: int) -> None:  # type: ignore[no-untyped-def]
    """Every fourth handful stays with the land. Owner pockets it or it lands."""
    from omnisim.entities.items import item_name, load_of, stow, CARRY_CAP
    from omnisim.systems.schedule import seen_say
    owner_name = plot_owner(good, village)
    if owner_name is None and good in FIELD_GOODS:
        owner_name = fields_owner(village)  # all field crops pay the Fields
    if owner_name is None or owner_name == CROWN or worker.name == owner_name or qty <= 0:
        return
    acc = int(worker.get("tithe_acc", 0)) + qty
    due, acc = acc // TITHE_EVERY, acc % TITHE_EVERY
    worker.comps["tithe_acc"] = acc
    worker.comps["tithe_paid"] = int(worker.get("tithe_paid", 0)) + due
    if due <= 0:
        return
    owner = next((n for n in village.npcs if n.name == owner_name), None)
    if owner is None and getattr(village, "player", None) is not None \
            and village.player.name == owner_name:
        owner = village.player  # the traveller's own ground pays the traveller
    opocket = owner.get("pocket", {}) if owner is not None else {}
    from omnisim.entities.items import cap_of
    left = stow(opocket, good, due, cap_of(owner) if owner is not None else CARRY_CAP)
    if left > 0:
        drop_at(village, worker.x, worker.y, good, left, worker.name,
                f"{owner_name}'s due")
    _yours = owner is not None and owner is getattr(village, "player", None)
    seen_say(village, worker.x, worker.y,
             f"Your due: +{due} {item_name(good)} "
             f"(from {worker.name}'s labor)." if _yours else
             f"{owner_name} takes their due: +{due} {item_name(good)} "
             f"(from {'your' if worker.get('role') == 'player' else worker.name + chr(39) + 's'} labor).")


def match_parcel(village, word):  # type: ignore[no-untyped-def]
    """A deed by id or name fragment ('stillpond', 'fields', 'meadow')."""
    if not word:
        return None
    w = str(word).strip().lower().replace("the ", "")
    for p in parcels_of(village):
        if p["id"] == w or w in p["name"].lower().replace("the ", ""):
            return p
    return None


def _market_stall(village):  # type: ignore[no-untyped-def]
    for stall in village.stalls or []:
        return stall
    return None


def buy_parcel(village, buyer_pocket: dict, buyer_name: str, parcel) -> str:  # type: ignore[no-untyped-def]
    """Buy a Crown deed at par: coin into the reserve, ground changes hands."""
    from omnisim.entities.items import coin_str
    if parcel.get("owner") != CROWN:
        return f"{parcel['name']} is {parcel['owner']}'s ground — not the Crown's to sell."
    price = int(parcel["price"])
    if int(buyer_pocket.get("coin", 0)) < price:
        return f"{parcel['name']} costs {coin_str(price)} — beyond your purse."
    stall = _market_stall(village)
    buyer_pocket["coin"] = float(buyer_pocket.get("coin", 0)) - price  # CONSERVATION: exact copper
    if stall is not None:
        stall["till"] = float(stall.get("till", 0) or 0) + price
    parcel["owner"] = buyer_name
    sync_plots(village)
    return f"{buyer_name} buys {parcel['name']} from the Crown for {coin_str(price)}."


def sell_parcel(village, seller_pocket: dict, seller_name: str, parcel) -> str:  # type: ignore[no-untyped-def]
    """Sell a deed back to the Crown at seventy cents on the copper."""
    from omnisim.entities.items import coin_str
    if parcel.get("owner") != seller_name:
        return f"{parcel['name']} isn't yours to sell."
    price = max(1, int(round(int(parcel["price"]) * BUYBACK)))
    stall = _market_stall(village)
    if stall is not None and int(stall.get("till", 0)) < price:
        return "The Crown's purse is light — no buyback today."
    if stall is not None:
        stall["till"] = float(stall.get("till", 0) or 0) - price
    seller_pocket["coin"] = float(seller_pocket.get("coin", 0)) + price  # CONSERVATION: exact copper
    parcel["owner"] = CROWN
    sync_plots(village)
    return f"{seller_name} sells {parcel['name']} back to the Crown for {coin_str(price)}."


def land_office(village) -> None:  # type: ignore[no-untyped-def]
    """Dawn at the Crown's office: one deed moves, at most. Rich souls buy
    income; desperate souls sell ground to eat. Land moves slow."""
    from omnisim.systems.schedule import seen_say
    live = [n for n in village.npcs
            if not n.comps.get("dead") and n.get("role") != "shopkeeper"]
    # Desperate sales first: hungry and light-pursed, dearest deed first.
    for n in sorted(live, key=lambda m: -int(m.get("hunger", 0))):
        if int(n.get("hunger", 0)) >= 70 and int(n.get("pocket", {}).get("coin", 0)) < 20:
            mine = [p for p in parcels_of(village) if p.get("owner") == n.name]
            if mine:
                parcel = max(mine, key=lambda p: int(p["price"]))
                seen_say(village, n.x, n.y, sell_parcel(village, n.get("pocket", {}), n.name, parcel))
                return
    # Rich buyers: coin to spare, cheapest Crown deed first.
    crown = sorted((p for p in parcels_of(village) if p.get("owner") == CROWN),
                   key=lambda p: int(p["price"]))
    if not crown:
        return
    for n in sorted(live, key=lambda m: -int(m.get("pocket", {}).get("coin", 0))):
        purse = int(n.get("pocket", {}).get("coin", 0))
        for parcel in crown:
            if purse >= 2.5 * int(parcel["price"]):
                seen_say(village, n.x, n.y,
                         buy_parcel(village, n.get("pocket", {}), n.name, parcel))
                return
