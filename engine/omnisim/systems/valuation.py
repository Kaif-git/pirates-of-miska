"""Valuation: one scorer for every choice. No thresholds, only arithmetic.

Urgency is a smooth curve — (need/100)^exp — so a peckish worker works
through lunch, a hungry one stops mid-shift, and a starving one wakes at
midnight, all from the same formula. Costs are economics (hassle, price
pain scaled by wealth, miles walked), tuned in data/drives.yaml.

Every option returns a plain value in one currency (relief). Callers
compare values; nothing here names a need level. New goods, ponds,
cellars, and professions join by adding options, never branches.
"""
from __future__ import annotations

_DRIVES: dict | None = None


def drives() -> dict:
    """Drive weights from data, with sane fallbacks when data is missing."""
    global _DRIVES
    if _DRIVES is None:
        _DRIVES = {}
        try:
            import yaml as _yaml
            from pathlib import Path as _P
            p = _P(__file__).resolve().parents[3] / "data" / "drives.yaml"
            if p.is_file():
                doc = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                node = doc.get("drives", {}) or {}
                if isinstance(node, dict):
                    _DRIVES = dict(node)
        except Exception:
            pass
        _DRIVES.setdefault("hunger_exp", 2.0)
        _DRIVES.setdefault("thirst_exp", 2.0)
        _DRIVES.setdefault("rest_exp", 2.0)
        _DRIVES.setdefault("bite_cost", 3.0)
        _DRIVES.setdefault("sleep_keep", 12.0)
        _DRIVES.setdefault("work_keep", 8.0)
        _DRIVES.setdefault("routine_bid", 1.0)
        _DRIVES.setdefault("price_scale", 8.0)
        _DRIVES.setdefault("mile_cost", 0.15)
        _DRIVES.setdefault("wake_value", 6.0)
    return _DRIVES


def urgency(need_value: float, kind: str = "hunger") -> float:
    """0 at sated, 1 at a full gauge, past 1 when suffering overflows."""
    exp = float(drives().get(f"{kind}_exp", 2.0))
    return max(0.0, float(need_value) / 100.0) ** exp


def desperate(npc, kind: str = "hunger") -> bool:
    """Survival breaks routine: one data line, every need, no thresholds
    scattered through the code. Glut pauses and polite waits yield to it."""
    try:
        current = float(npc.get(kind, 0))
    except Exception:
        return False
    return urgency(current, kind) > float(drives().get("routine_break", 0.5))


def price_pain(price: float, coin: float) -> float:
    """A copper means nothing to a rich purse, dinner to an empty one."""
    scale = max(0.5, float(drives().get("price_scale", 8.0)))
    return float(price) / (1.0 + max(0.0, float(coin)) / scale)


def miles(tiles: float) -> float:
    """Walking costs, in relief currency."""
    return max(0.0, float(tiles)) * float(drives().get("mile_cost", 0.15))


def rest_utility(npc) -> float:
    """What the bed is worth right now: rest-halt compares this to waking."""
    try:
        fatigue = float(npc.get("fatigue", 0))
    except Exception:
        fatigue = 0.0
    return urgency(fatigue, "rest") * 100.0


def _food_value_of(npc, item_id: str) -> float:
    try:
        from omnisim.entities.items import food_value, lore_q
        return float(food_value(npc, item_id, lore_q(npc, item_id)))
    except Exception:
        try:
            from omnisim.entities.items import defs
            return float(defs()["items"].get(item_id, {}).get("food", 0))
        except Exception:
            return 0.0


def bite_option(npc) -> tuple:
    """Best bite in hand: (item_id, value) or (None, 0). Dinner reserves
    are FOR eating (sales hold them back, teeth don't) — seed corn is safe
    because grain isn't food and never was."""
    try:
        from omnisim.entities.items import best_food, open_of
        open = open_of(npc)
    except Exception:
        return None, 0.0
    try:
        hunger = float(npc.get("hunger", 0))
    except Exception:
        hunger = 0.0
    if hunger <= 0:
        return None, 0.0
    pocket = npc.get("pocket", {}) or {}
    item_id = best_food(pocket, open)
    if item_id is None:
        return None, 0.0
    cost = float(drives().get("bite_cost", 3.0))
    return item_id, urgency(hunger, "hunger") * _food_value_of(npc, item_id) - cost


def _eat_order() -> list:
    try:
        from omnisim.entities.items import eat_order
        return eat_order()
    except Exception:
        return ["bread"]


def keep_of_block(blk_act: str) -> float:
    """What the current hour defends: beds and shifts outbid nibbles."""
    d = drives()
    if blk_act == "sleep":
        return float(d.get("sleep_keep", 12.0))
    if blk_act in ("work", "patrol"):
        return float(d.get("work_keep", 8.0))
    if blk_act == "eat":
        try:  # MEAL-KEEP (flag meal_keep_wired, default off): a served
            # meal continues while the next bite beats this. Flag off:
            # byte-identical HEAD behavior (eat defends nothing).
            from omnisim.systems.production import _load as _mk_ld
            if bool(((_mk_ld("economy") or {}).get("flags", {}) or {}).get(
                    "meal_keep_wired", False)):
                return float(d.get("meal_keep", 6.0))
        except Exception:
            pass
    return 0.0


def buy_option(village, npc) -> tuple:
    """Best stocked staple within earshot: (stall, item, value) or (None,..).
    Two hungers bid: the belly now (urgency × relief) and the pantry later
    (pantry floor for bare shelves, dearness-discounted at deal time). The
    market wins when it is close and coin covers the pain; otherwise the
    feet look to the wilds. No preference lists — the table decides."""
    try:
        from omnisim.entities.items import staples as _st
        from omnisim.core.actions import trade_price
        from omnisim.systems.skills import skill_level
        options = []
        try:
            hunger = float(npc.get("hunger", 0))
        except Exception:
            hunger = 0.0
        pocket = npc.get("pocket", {}) or {}
        try:
            from omnisim.entities.items import defs as _defs
            from omnisim.entities.items import open_of as _oo2
            from omnisim.entities.items import servings_of as _serv
            _table = _defs()["items"]
            _open2 = _oo2(npc)
        except Exception:
            _table, _open2 = {}, {}
            def _serv(i): return 1
        dinners = sum(float(_table.get(i, {}).get("food", 0))
                      * (int(_open2.get(i, 0)) + q * int(_serv(i)))
                      for i, q in pocket.items()) / 40.0
        _floor = float(drives().get("pantry_floor", 0.35))
        pantry = _floor * max(0.0, 1.0 - dinners / 2.0)
        urg = urgency(hunger, "hunger")
        coin = pocket.get("coin", 0)
        trading = skill_level(npc, "Trading")
        # Broke feet don't pace the market: if no stocked staple is
        # affordable, the market cannot feed — wilds, well, or work can.
        try:
            from omnisim.systems.haggle import enabled as _ue
            from omnisim.systems.haggle import expected_price as _ep
            _uails = bool(_ue())
        except Exception:
            _uails = False
        def _plan_price(s, t):
            if _uails:
                try:
                    return _ep(village, npc, s, trade_price(s, trading, buying=True, stall=t, village=village))
                except Exception:
                    pass
            return trade_price(s, trading, buying=True, stall=t, village=village)
        try:
            from omnisim.systems.needs3 import level_of as _n3_cold
            from omnisim.systems.needs3 import on as _n3_on2
            from omnisim.systems.production import goods as _n3_goods2
            _cold_shop = bool(_n3_on2()) and float(_n3_cold(npc, "cold")) > 0
            _warm2 = [gid for gid, g in _n3_goods2().items()
                      if (g.get("provides", {}) or {}).get("insulation")] \
                if _cold_shop else []
        except Exception:
            _cold_shop, _warm2 = False, []
        try:
            _cheapest = min(
                _plan_price(s, t)
                for t in village.stalls
                if abs(t["x"] - npc.x) + abs(t["y"] - npc.y) <= 30
                for s in list(_st()) + [w for w in _warm2
                                        if t["stock"].get(w, [0, 0])[0] > 0]
                if t["stock"].get(s, [0, 0])[0] > 0)
        except Exception:
            return None, None, 0.0
        if _cheapest > coin:
            return None, None, 0.0
        for s in village.stalls:
            dist = abs(s["x"] - npc.x) + abs(s["y"] - npc.y)
            if dist > 30:  # destination shopping walks; browsing stays local
                continue
            for staple in _st():
                if s["stock"].get(staple, [0, 0])[0] <= 0:
                    continue
                price = _plan_price(staple, s)
                relief = _food_value_of(npc, staple)
                value = (urg + pantry) * relief \
                    - price_pain(price, coin) - miles(dist)
                options.append((value, s, staple))
        try:  # RECIPE-BUY (flag recipe_buy_options, default off):
            # own-trade recipe inputs ride the same valuation path
            # with chain-value relief (rows, never a per-good list).
            from omnisim.systems.recipebuy import on as _rb_on
            from omnisim.systems.recipebuy import recipe_inputs_of as _rb_ins
            if _rb_on():
                for _s2 in village.stalls:
                    _d2 = abs(_s2["x"] - npc.x) + abs(_s2["y"] - npc.y)
                    if _d2 > 30:
                        continue
                    for _g2, _rel2 in _rb_ins(village, npc).items():
                        if _s2["stock"].get(_g2, [0, 0])[0] <= 0:
                            continue
                        _p2 = _plan_price(_g2, _s2)
                        if _p2 > coin:
                            continue
                        _v2 = (urg + pantry) * _rel2 \
                            - price_pain(_p2, coin) - miles(_d2)
                        options.append((_v2, _s2, _g2))
        except Exception:
            pass
        try:  # STEP 3: cold mouths shop for warmth, not just dinner (flag-gated;
            # warm villages see no new candidates, so parity holds by construction)
            from omnisim.systems.needs3 import level_of as _n3_level
            from omnisim.systems.needs3 import on as _n3_on
            from omnisim.systems.needs3 import relief_units as _n3_relief
            if _n3_on() and _n3_level(npc, "cold") > 0:
                from omnisim.systems.production import goods as _n3_goods
                _warm = [gid for gid, g in _n3_goods().items()
                         if "fuel" in (g.get("tags", []) or [])
                         or (g.get("provides", {}) or {}).get("insulation")]
                for s in village.stalls:
                    dist = abs(s["x"] - npc.x) + abs(s["y"] - npc.y)
                    if dist > 30:
                        continue
                    for good in _warm:
                        if s["stock"].get(good, [0, 0])[0] <= 0:
                            continue
                        price = _plan_price(good, s)
                        if price > coin:
                            continue
                        value = _n3_relief(npc, good, village) \
                            - price_pain(price, coin) - miles(dist)
                        options.append((value, s, good))
        except Exception:
            pass
        if not options:
            return None, None, 0.0
        options.sort(key=lambda t: (-t[0], t[1].get("x", 0)))
        return options[0][1], options[0][2], options[0][0]
    except Exception:
        return None, None, 0.0


def forage_option(village, npc) -> tuple:
    """The wild table's bid: (dest, value) or (None, 0). Free food walks
    far — the broke and the far-from-market eat berries, not pride."""
    try:
        from omnisim.systems.forage import spot_near
        from omnisim.entities.items import defs
        from omnisim.systems.skills import skill_level, ALL_SKILLS
    except Exception:
        return None, 0.0
    try:
        hunger = float(npc.get("hunger", 0))
    except Exception:
        hunger = 0.0
    if hunger <= 0:
        return None, 0.0
    dest = spot_near(village, npc)
    if dest is None:
        return None, 0.0
    try:
        from omnisim.systems.forage import sources
        src = sources()[0] if sources() else {}
        good = str(src.get("good", ""))
        skill = str(src.get("skill", ""))
        per = max(1, int(src.get("per_tick", 1)))
        lvl = skill_level(npc, skill) if skill in ALL_SKILLS else 1
        qty = per + lvl // 3
        relief = float(defs()["items"].get(good, {}).get("food", 0)) * qty
    except Exception:
        relief = 15.0
    try:  # STOCK-AWARE FORAGE (flag stock_aware_forage, default off):
        # scale relief by the destination patch stock so the score tells
        # the truth about scarcity. Empty scores zero via max(), never
        # negative. Flag off: byte-identical HEAD behavior.
        from omnisim.systems.production import _load as _saf_ld
        if bool(((_saf_ld("economy") or {}).get("flags", {}) or {}).get(
                "stock_aware_forage", False)):
            from omnisim.systems import forageregrow as _saf_g
            _saf_K, _saf_r = _saf_g.params_of(src)
            _saf_frac = 0.5
            try:
                _saf_frac = float(
                    ((_saf_ld("economy") or {}).get("stock_aware", {}) or {})
                    .get("fraction", 0.5))
            except Exception:
                pass
            _saf_S = _saf_g._stocks(village).get((dest, good), _saf_K)
            _saf_f = min(1.0, max(0.0, float(_saf_S)) / max(
                1e-9, _saf_frac * _saf_K))
            relief = max(0.0, relief * _saf_f)
            # CROWDING (flag crowding_forage, default off; on top of
            # stock-aware only): 1 / (1 + workers / target) with target
            # = sustainable yield takes/day (r*K/4). Workers = committed
            # trips: souls with forage_trip flag whose dest is this tile.
            # Mid-walk commitments hold (brain.steer), so committed trips
            # are the live worker count. Same form every gathering good.
            # stock-aware only): 1 / (1 + workers / target) with target
            # = sustainable yield in same units (r*K/4 takes/day maps
            # to one forager share 1.0). Same form every gathering good.
            try:
                _cw_on = bool(((_saf_ld("economy") or {}).get("flags", {})
                               or {}).get("crowding_forage", False))
            except Exception:
                _cw_on = False
            if _cw_on:
                try:
                    _cw_tgt = float(((_saf_ld("economy") or {}).get(
                        "crowding", {}) or {}).get(
                        "yield_per_worker_target", _saf_r * _saf_K / 4.0))
                except Exception:
                    _cw_tgt = _saf_r * _saf_K / 4.0
                try:
                    _cw_w = sum(1 for _n in (getattr(village, "npcs", [])
                                  or [])
                                if tuple((_n.comps.get("dest") or (None,))
                                         or (None,))[:2] == tuple(dest))
                except Exception:
                    _cw_w = 0
                _cw_f = 1.0 / (1.0 + max(0, _cw_w) / max(
                    1e-9, _cw_tgt))
                relief = max(0.0, relief * _cw_f)
    except Exception:
        pass
    dist = max(abs(dest[0] - npc.x), abs(dest[1] - npc.y))
    if relief <= 0:
        return dest, 0.0
    return dest, urgency(hunger, "hunger") * relief - miles(dist)


def drink_option(village, npc) -> tuple:
    """The well's bid: (dest, value) or (None, 0). Water is free — only
    distance argues. Parched feet outbid everything by arithmetic alone."""
    try:
        from omnisim.systems.schedule import well_xy, standable_near
    except Exception:
        return None, 0.0
    try:
        thirst = float(npc.get("thirst", 0))
    except Exception:
        thirst = 0.0
    if thirst <= 0:
        return None, 0.0
    try:
        well = well_xy(village)
        if well is None:
            return None, 0.0
        dest = standable_near(village.game_map, *well)
        dist = max(abs(dest[0] - npc.x), abs(dest[1] - npc.y))
        return dest, urgency(thirst, "thirst") * thirst - miles(dist)
    except Exception:
        return None, 0.0
