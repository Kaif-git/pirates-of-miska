"""Packs, ground loot, and the item defs (data/items.yaml).

Packs are plain {id: qty} dicts living in entity comps["pack"] — JSON-saveable
by construction. Ground loot is Village.ground: {(x, y): {id: qty}}.
"""
from __future__ import annotations
from pathlib import Path
import yaml

_DEFS: dict | None = None


def defs() -> dict:
    """Item defs, loaded once. Falls back to a minimal table if YAML missing."""
    global _DEFS
    if _DEFS is None:
        path = Path(__file__).resolve().parents[3] / "data" / "items.yaml"
        if path.is_file():
            _DEFS = yaml.safe_load(path.read_text(encoding="utf-8"))
        else:
            _DEFS = {"items": {"bread": {"name": "bread", "food": 40, "heal": 1}},
                     "eat_order": ["bread"], "stalls": {}}
    return _DEFS


def item_name(item_id: str) -> str:
    return defs()["items"].get(item_id, {}).get("name", item_id)


def pack_of(entity) -> dict:  # type: ignore[no-untyped-def]
    pack = entity.get("pack")
    if not isinstance(pack, dict):
        pack = {}
        entity.comps["pack"] = pack
    return pack


def add_item(pack: dict, item_id: str, qty: int) -> None:
    pack[item_id] = pack.get(item_id, 0) + qty


CARRY_CAP = 30  # ECON: the baseline back — strong backs hold more (see cap_of)


def cap_of(holder) -> int:  # type: ignore[no-untyped-def]
    """ECON: backs grow with Endurance — 26 + 2 per level. Training matters
    when the harvest is heavy and the market is far."""
    from omnisim.systems.skills import skill_level
    return 26 + 2 * skill_level(holder, "Endurance")


def bulk_of(item_id: str) -> int:
    if item_id == "coin":
        return 0
    return int(defs()["items"].get(item_id, {}).get("bulk", 1))


def load_of(pack: dict) -> int:
    return sum(bulk_of(i) * q for i, q in pack.items() if q > 0)


def stow(pack: dict, item_id: str, qty: int, cap: int = CARRY_CAP) -> int:
    """Cap-aware add. Returns the leftover that would not fit."""
    room = max(0, cap - load_of(pack))
    take = min(qty, room // max(1, bulk_of(item_id)))
    if take > 0:
        pack[item_id] = pack.get(item_id, 0) + take
    return qty - take


def take_item(pack: dict, item_id: str, qty: int) -> bool:
    if pack.get(item_id, 0) < qty:
        return False
    pack[item_id] -= qty
    if pack[item_id] <= 0:
        del pack[item_id]
    return True


def pack_summary(pack: dict, open: dict | None = None) -> str:
    if not pack and not open:
        return "empty"
    bits = []
    seen = set()
    for i, q in sorted((pack or {}).items()):
        seen.add(i)
        if q <= 0 and not (open or {}).get(i, 0):
            continue
        bits.append(_serving_bit(i, q, (open or {}).get(i, 0)))
    for i, q in sorted((open or {}).items()):
        if i not in seen and q > 0:
            bits.append(_serving_bit(i, 0, q))
    return ", ".join(bits) if bits else "empty"


def _serving_bit(item_id: str, qty: int, opened: int) -> str:
    """'bread x2 loaves (4 meals)' — backs, bellies, and open bottles."""
    total = max(0, int(opened)) + max(0, int(qty)) * servings_of(item_id)
    base = f"{item_name(item_id)} x{qty}" if qty > 0 else item_name(item_id)
    if total > 1 and servings_of(item_id) > 1:
        return f"{base} ({total} meals)"
    if total > 0 and qty <= 0:
        return f"{base} ({total} left open)"
    return base


def best_food(pack: dict, open: dict | None = None) -> str | None:
    for item_id in eat_order():
        if servings_in(pack, open, item_id) > 0:
            return item_id
    return None


def servings_of(item_id: str) -> int:
    """Dinners in one unit: a fish feeds four, a loaf two, an apple one.
    New foods declare it in data; everything else is one."""
    try:
        return max(1, int(_items().get(item_id, {}).get("servings", 1)))
    except Exception:
        return 1


def open_of(entity) -> dict:  # type: ignore[no-untyped-def]
    """Open units mid-meal {item: servings left} — like an open bottle.
    JSON-saveable by construction (plain strings and ints)."""
    try:
        opened = entity.comps.get("open")
    except Exception:
        return {}
    if not isinstance(opened, dict):
        opened = {}
        try:
            entity.comps["open"] = opened
        except Exception:
            pass
    return opened


def servings_in(pocket: dict, open: dict | None, item_id: str) -> int:
    """Edible servings at hand: open servings plus whole units behind them."""
    try:
        units = max(0, int((pocket or {}).get(item_id, 0)))
    except Exception:
        units = 0
    try:
        opened = max(0, int((open or {}).get(item_id, 0)))
    except Exception:
        opened = 0
    return opened + units * servings_of(item_id)


def take_serving(pocket: dict, open: dict | None, item_id: str) -> bool:
    """Eat one serving: from the open unit first, else open a fresh one.
    Returns False when nothing edible was at hand."""
    if open is None:
        open = {}
    try:
        if int(open.get(item_id, 0)) > 0:
            open[item_id] -= 1
            if open[item_id] <= 0:
                del open[item_id]
            return True
    except Exception:
        pass
    try:
        if int((pocket or {}).get(item_id, 0)) > 0:
            pocket[item_id] -= 1
            if pocket[item_id] <= 0:
                del pocket[item_id]
            left = servings_of(item_id) - 1
            if left > 0:
                open[item_id] = int(open.get(item_id, 0)) + left
            return True
    except Exception:
        pass
    return False


STAPLE_FOOD = 20  # food value at which a good counts a dinner staple


def _items() -> dict:
    return defs().get("items", {}) or {}


def is_food(item_id: str) -> bool:
    """Anything with nutrition — the table, not a name list."""
    try:
        return float(_items().get(item_id, {}).get("food", 0)) > 0
    except Exception:
        return False


def is_staple(item_id: str) -> bool:
    """Hearty enough to live on: famine math, dinner reserves, urgent bites."""
    try:
        return float(_items().get(item_id, {}).get("food", 0)) >= STAPLE_FOOD
    except Exception:
        return False


_LISTS: dict | None = None


def invalidate_lists() -> None:
    """Drop memoized item lists (defs table changed: drops/tests)."""
    global _LISTS
    _LISTS = None


def _lists() -> dict:
    global _LISTS
    if _LISTS is None:
        try:
            _tbl = _items()
            _foods = [i for i in _tbl if float((_tbl.get(i, {}) or {}).get("food", 0)) > 0]
            _stap = sorted([i for i in _tbl if float((_tbl.get(i, {}) or {}).get("food", 0)) >= STAPLE_FOOD],
                           key=lambda i: (float(_tbl[i].get("price", 0)), i))
            _LISTS = {"foods": _foods, "staples": _stap, "sellable": [i for i in _tbl if i != "coin"]}
        except Exception:
            _LISTS = {"foods": [], "staples": [], "sellable": []}
    return _LISTS


def foods() -> list:
    """Every edible good, richest first. New foods seat themselves."""
    return list(_lists()["foods"])


def staples() -> list:
    """Dinner staples, cheapest first (the daily bread leads)."""
    return list(_lists()["staples"])


def eat_order() -> list:
    """Best-first eating: richest first, quickest to spoil first (less waste).
    Poultice and herbs trail as food of last resort, as before."""
    def _key(i):
        node = _items().get(i, {})
        return (-float(node.get("food", 0)),
                int(node.get("shelf", 99)) if node.get("shelf") is not None else 99,
                i)
    return sorted(foods(), key=_key)


def sellable() -> list:
    """Everything but coin trades — wild berries sell like wheat does."""
    return list(_lists()["sellable"])


def dinner_reserve() -> list:
    """Dinners held back from sale: flagged goods, default 2 for staples."""
    out = []
    for i in _items():
        node = _items()[i]
        flag = node.get("reserve", None)
        if flag is None:
            flag = 2 if is_staple(i) else 0
        if int(flag) > 0:
            out.append(i)
    return out


def reserve_of(item_id: str) -> int:
    node = _items().get(item_id, {})
    flag = node.get("reserve", None)
    if flag is None:
        flag = 2 if is_staple(item_id) else 0
    return int(flag)


def shelf_of(item_id: str) -> int | None:
    """Dawns a stack keeps; None keeps forever (grain is patient)."""
    node = _items().get(item_id, {})
    shelf = node.get("shelf", None)
    return None if shelf is None else int(shelf)


def cheapest_staple() -> str | None:
    s = staples()
    return s[0] if s else None


def tools_for(skill: str) -> list:
    """Implements behind a skill, from item flags (best-in-pack applies)."""
    return [i for i, node in _items().items()
            if skill in (node.get("tool_for") or [])]


def unit_of(item_id: str) -> str:
    """What one of it is: sheaf, sack, catch, loaf. Asks mean per-unit."""
    try:
        return str(_items().get(item_id, {}).get("unit", "piece"))
    except Exception:
        return "piece"


COPPER_PER_SILVER = 10  # village reckoning: ten copper the silver,
SILVER_PER_GOLD = 10  # ten silver the gold. Coin is counted in copper;
# purses are SHOWN split (4c a fish, 1s 2c a hammer) so nobody mistakes
# coppers for gold. Physical change-making waits on deflation day.


def coin_str(copper) -> str:
    """'4c', '1s 2c', '2g 3s 1c' — purses read split, math stays copper."""
    try:
        c = int(copper)
    except Exception:
        return "0c"
    if c < 0:
        c = 0
    g, rest = divmod(c, COPPER_PER_SILVER * SILVER_PER_GOLD)
    s, c = divmod(rest, COPPER_PER_SILVER)
    bits = []
    if g:
        bits.append(f"{g}g")
    if s:
        bits.append(f"{s}s")
    if c or not bits:
        bits.append(f"{c}c")
    return " ".join(bits)


Q_PRICE = (1.0, 1.5, 2.0, 3.0, 5.0)  # sell tiers q1..q5 (M3 provenance)


def q_mult(q: int) -> float:
    """Sell-price tier for quality."""
    return Q_PRICE[max(1, min(5, q)) - 1]


def food_mult(q: int) -> float:
    """Satiety tier: +25% hunger value per quality above 1."""
    return 1.0 + 0.25 * (max(1, q) - 1)


def food_value(holder, item_id: str, quality: int = 1,
               age_days: float = 0.0) -> float:
    """V1 Foundations (GDD 3.1): dynamic fulfillment = Nutrition x Quality
    x Freshness x Metabolism x DiminishingReturns. Fresh first-time meals
    reproduce the legacy food number exactly."""
    base = float(defs()["items"].get(item_id, {}).get("food", 0))
    try:
        from omnisim.systems.needs import fulfillment
        return float(fulfillment(holder, item_id, base, quality, age_days))
    except Exception:
        return base * food_mult(quality)


def yield_q(skill_level: int, tool_q: int = 0) -> int:
    """Gathered quality: 1 + skill//3 + tool//2, capped at 5 (M3)."""
    return max(1, min(5, 1 + skill_level // 3 + tool_q // 2))


def lore_of(entity, item_id: str) -> dict:  # type: ignore[no-untyped-def]
    """Last-source-wins provenance {q, by, origin, day, stolen}."""
    return (entity.get("lore", {}) or {}).get(item_id, {})


def lore_q(entity, item_id: str) -> int:  # type: ignore[no-untyped-def]
    return int(lore_of(entity, item_id).get("q", 1))


def stamp_lore(entity, item_id: str, q: int, by: str, origin: str, day: int,  # type: ignore[no-untyped-def]
               stolen: bool = False, extra: dict | None = None) -> None:
    lore = entity.get("lore")
    if not isinstance(lore, dict):
        lore = {}
        entity.comps["lore"] = lore
    rec = {"q": max(1, min(5, q)), "by": by, "origin": origin,
           "day": day, "stolen": stolen}
    if extra:
        rec.update(extra)  # seeds carry gen/traits; selection work reads these
    lore[item_id] = rec


def lore_line(entity, item_id: str, qty: int) -> str:  # type: ignore[no-untyped-def]
    """'wheat x2 q3 · the fields D1' for profiles; '' when unstamped."""
    lore = lore_of(entity, item_id)
    if not lore:
        return ""
    flag = " (stolen!)" if lore.get("stolen") else ""
    return (f"{item_name(item_id)} x{qty} q{lore.get('q', 1)} · "
            f"{lore.get('origin', '?')} D{lore.get('day', 1)}{flag}")
