"""Procurement trips for producers (flag procurement, default off).

Part A: seller memory. Each holder records, from its own completed
trades, sellers it bought each input from (seller, last price, last day).
Bounded at 32 sellers (SOUL_CAP, memory.py) with salience eviction
(familiarity + 0.1*deals, same key as _touch_soul). No global market
knowledge: only traded-with sellers are known.
Part B: trigger. Cover = pocket_units / rated_daily_use (rated_use_per_day
from recipebuy = grain-ops audit formula). Below P=2 days -> procurement
need. Scored option: expected_output_value x units_enabled - purchase_cost
- time_cost (time_value_mc_per_tick x travel ticks). Must outscore routine
(routine_bid floor) when cover < P and score > 0; must not outscore urgent
needs (eat/drink/sleep veto via desperate()).
Part C: return follows from the routine anchor (no separate option).
"""
from __future__ import annotations

PROCURE_P_DAYS = 2.0
BATCH_C_DAYS = 5.0
SELLER_CAP = 32


def on():
    try:
        from omnisim.systems.production import _load as _ld
        return bool(((_ld("economy") or {}).get("flags", {}) or {}).get(
            "procurement", False))
    except Exception:
        return False


def _day_of(village):
    try:
        from omnisim.core.clock import day_of as _d
        return int(_d(village.clock_pts))
    except Exception:
        return 0


def sellers_of(npc):
    try:
        m = npc.comps.get("proc_sellers")
        return dict(m) if isinstance(m, dict) else {}
    except Exception:
        return {}


def remember_seller(npc, village, good, seller, price):
    """Record one completed buy: (seller -> {good, price, day, deals})."""
    try:
        m = npc.comps.get("proc_sellers")
        if not isinstance(m, dict):
            m = {}
            npc.comps["proc_sellers"] = m
        key = "%s|%s" % (seller, good)
        row = m.get(key)
        if not isinstance(row, dict):
            if len(m) >= SELLER_CAP:
                drop = min(m.items(), key=lambda kv: (
                    0.1 * int((kv[1] or {}).get("deals", 0))))[0]
                m.pop(drop, None)
            row = {}
            m[key] = row
        row["seller"] = seller
        row["good"] = good
        row["price"] = float(price)
        row["day"] = _day_of(village)
        row["deals"] = int(row.get("deals", 0)) + 1
    except Exception:
        pass


def known_seller(npc, good):
    """Best (cheapest last price) remembered seller for good, or None."""
    best = None
    try:
        for key, row in sellers_of(npc).items():
            if not isinstance(row, dict) or row.get("good") != good:
                continue
            if best is None or float(row.get("price", 1e9)) < float(
                    best.get("price", 1e9)):
                best = row
    except Exception:
        pass
    return best


def cover_days(npc, good):
    try:
        from omnisim.systems.recipebuy import rated_use_per_day as _rate
        rate = float(_rate(npc, good))
        if rate <= 0:
            return float("inf")
        have = float((npc.get("pocket", {}) or {}).get(good, 0) or 0)
        return have / rate
    except Exception:
        return float("inf")


def need_inputs(npc):
    """Inputs below P days cover, for recipes the holder's trade can run."""
    out = []
    try:
        from omnisim.systems.production import recipes as _rr
        from omnisim.systems.production import trade_of as _tow
        from omnisim.systems.production import goods_with_tag as _gwt
        tw = _tow(npc) or {}
        rids = set(tw.get("recipes", []) or [])
        for r in _rr().values():
            if r.get("skill", "") != (npc.get("work_skill") or ""):
                continue
            if r.get("id", "") not in rids and rids:
                continue
            if "service" in (r.get("flags", []) or []):
                continue
            for inp in (r.get("inputs", []) or []):
                goods = [inp["good"]] if "good" in inp else list(
                    _gwt(inp.get("tag", "")))
                for gd in goods:
                    if cover_days(npc, gd) < PROCURE_P_DAYS:
                        if gd not in out:
                            out.append(gd)
    except Exception:
        pass
    return out


def procure_option(village, npc):
    """Scored procurement bid: (value, dest, flag) or None. Flag-gated."""
    if not on():
        return None
    try:
        from omnisim.systems.valuation import desperate as _desp
        if _desp(npc, "hunger") or _desp(npc, "thirst"):
            return None  # urgent needs win; procurement never outscores them
    except Exception:
        pass
    try:
        needs = need_inputs(npc)
        if not needs:
            return None
        from omnisim.systems.production import expected_sale_price as _esp
        from omnisim.systems.production import recipes as _rr
        from omnisim.systems.production import trade_of as _tow
        from omnisim.systems.production import goods_with_tag as _gwt
        from omnisim.systems.schedule import standable_near, gm_of, bfs_path
        from omnisim.systems.realizedtv import tv_for as _tvm
        tw = _tow(npc) or {}
        rids = set(tw.get("recipes", []) or [])
        try:  # RADIUS RULE (flag local_view): trips score only for
            # sellers within R of the workplace; farther rows stay memorized
            # but bid nothing (logged out_of_radius by the caller).
            from omnisim.systems.localview import on as _lv_on
            from omnisim.systems.localview import within_radius as _lv_in
            from omnisim.systems.localview import workplace_of as _lv_wp
            _lv = bool(_lv_on())
        except Exception:
            _lv = False
        best = None
        for gd in needs:
            row = known_seller(npc, gd)
            if row is None:
                continue  # only remembered sellers: no global knowledge
            seller = row.get("seller")
            # seller stock + location: stall shelf or NPC pocket
            stock = 0
            dest = None
            seller_xy = None
            for s in (village.stalls or []):
                if s.get("name") == seller:
                    stock = (s.get("stock", {}) or {}).get(gd, [0, 0])[0]
                    dest = standable_near(village.game_map, s["x"], s["y"])
                    seller_xy = (s["x"], s["y"])
                    break
            if dest is None:
                other = next((n for n in (village.npcs or [])
                              if n.name == seller
                              and not n.comps.get("dead")), None)
                if other is None:
                    continue
                stock = float((other.get("pocket", {}) or {}).get(gd, 0))
                dest = (other.x, other.y)
                seller_xy = (other.x, other.y)
            try:
                if _lv and seller_xy is not None and not _lv_in(
                        npc, seller_xy):
                    try:
                        (npc.comps.setdefault("proc_out_of_radius", [])
                         .append({"day": _day_of(village), "seller": seller,
                                  "good": gd}))
                    except Exception:
                        pass
                    continue
            except Exception:
                pass
            if stock <= 0:
                continue
            price = float(row.get("price", 1))
            # units enabled: cheapest recipe output value per input unit
            best_val = 0.0
            for r in _rr().values():
                if r.get("skill", "") != (npc.get("work_skill") or ""):
                    continue
                if r.get("id", "") not in rids and rids:
                    continue
                hit = any((i.get("good") == gd) or (
                    "tag" in i and gd in _gwt(i["tag"]))
                    for i in (r.get("inputs", []) or []))
                if not hit:
                    continue
                in_qty = 1.0
                for i in (r.get("inputs", []) or []):
                    if i.get("good") == gd or (
                            "tag" in i and gd in _gwt(i["tag"])):
                        in_qty = max(1.0, float(i.get("qty", 1) or 1))
                        break
                for o in (r.get("outputs", []) or []):
                    try:
                        ov = float(_esp(village, o.get("good")))
                        vpu = ov * float(o.get("qty", 1) or 1) / in_qty
                        best_val = max(best_val, vpu)
                    except Exception:
                        pass
            if best_val <= 0:
                continue
            # BATCH (Part A): units to reach C days cover, capped by stock
            # and by affordability after the food reserve (R4: recipe inputs
            # may not dip into dinner money).
            try:
                from omnisim.systems.recipebuy import rated_use_per_day as _brx
                from omnisim.systems.decision import food_reserve as _frx
                _ratex = float(_brx(npc, gd))
                _havex = float((npc.get("pocket", {}) or {}).get(gd, 0) or 0)
                _wantx = max(0.0, BATCH_C_DAYS * _ratex - _havex)
                _coinx = float((npc.get("pocket", {}) or {}).get("coin", 0))
                _resx = float(_frx(npc, village))
                _spendx = max(0.0, _coinx - _resx)
                _affordx = max(0, int(_spendx // max(0.01, price)))
                units = min(int(stock), int(_wantx), _affordx)
            except Exception:
                units = 0
            if units <= 0:
                continue
                continue
            try:
                path = bfs_path(gm_of(village), (npc.x, npc.y), dest)
                ticks = max(1, len(path or []))
            except Exception:
                ticks = 10
            try:
                tv = float(_tvm(npc, village)) / 1000.0
            except Exception:
                tv = 1.0
            value = best_val * units - price * units - tv * ticks * 2
            if value <= 0:
                continue
            if best is None or value > best[0]:
                best = (value, dest, "procure_trip", gd, units, price,
                        ticks)
        if best is None:
            return None
        return (best[0], best[1], best[2])
    except Exception:
        return None


def procure_detail(village, npc):
    """Full bid detail for logging: (value, dest, good, units, price,
    ticks) or None."""
    if not on():
        return None
    try:
        from omnisim.systems.valuation import desperate as _desp
        if _desp(npc, "hunger") or _desp(npc, "thirst"):
            return None
    except Exception:
        pass
    try:
        needs = need_inputs(npc)
        if not needs:
            return None
        from omnisim.systems.production import expected_sale_price as _esp
        from omnisim.systems.production import recipes as _rr
        from omnisim.systems.production import trade_of as _tow
        from omnisim.systems.production import goods_with_tag as _gwt
        from omnisim.systems.schedule import standable_near, gm_of, bfs_path
        from omnisim.systems.realizedtv import tv_for as _tvm
        tw = _tow(npc) or {}
        rids = set(tw.get("recipes", []) or [])
        best = None
        for gd in needs:
            row = known_seller(npc, gd)
            if row is None:
                continue
            seller = row.get("seller")
            stock = 0
            dest = None
            for s in (village.stalls or []):
                if s.get("name") == seller:
                    stock = (s.get("stock", {}) or {}).get(gd, [0, 0])[0]
                    dest = standable_near(village.game_map, s["x"], s["y"])
                    break
            if dest is None:
                other = next((n for n in (village.npcs or [])
                              if n.name == seller
                              and not n.comps.get("dead")), None)
                if other is None:
                    continue
                stock = float((other.get("pocket", {}) or {}).get(gd, 0))
                dest = (other.x, other.y)
            if stock <= 0:
                continue
            price = float(row.get("price", 1))
            best_val = 0.0
            for r in _rr().values():
                if r.get("skill", "") != (npc.get("work_skill") or ""):
                    continue
                if r.get("id", "") not in rids and rids:
                    continue
                hit = any((i.get("good") == gd) or (
                    "tag" in i and gd in _gwt(i["tag"]))
                    for i in (r.get("inputs", []) or []))
                if not hit:
                    continue
                in_qty = 1.0
                for i in (r.get("inputs", []) or []):
                    if i.get("good") == gd or (
                            "tag" in i and gd in _gwt(i["tag"])):
                        in_qty = max(1.0, float(i.get("qty", 1) or 1))
                        break
                for o in (r.get("outputs", []) or []):
                    try:
                        ov = float(_esp(village, o.get("good")))
                        vpu = ov * float(o.get("qty", 1) or 1) / in_qty
                        best_val = max(best_val, vpu)
                    except Exception:
                        pass
            if best_val <= 0:
                continue
            # BATCH (Part A): units to reach C days cover, capped by stock
            # and by affordability after the food reserve (R4: recipe inputs
            # may not dip into dinner money).
            try:
                from omnisim.systems.recipebuy import rated_use_per_day as _brx
                from omnisim.systems.decision import food_reserve as _frx
                _ratex = float(_brx(npc, gd))
                _havex = float((npc.get("pocket", {}) or {}).get(gd, 0) or 0)
                _wantx = max(0.0, BATCH_C_DAYS * _ratex - _havex)
                _coinx = float((npc.get("pocket", {}) or {}).get("coin", 0))
                _resx = float(_frx(npc, village))
                _spendx = max(0.0, _coinx - _resx)
                _affordx = max(0, int(_spendx // max(0.01, price)))
                units = min(int(stock), int(_wantx), _affordx)
            except Exception:
                units = 0
            if units <= 0:
                continue
                continue
            try:
                path = bfs_path(gm_of(village), (npc.x, npc.y), dest)
                ticks = max(1, len(path or []))
            except Exception:
                ticks = 10
            try:
                tv = float(_tvm(npc, village)) / 1000.0
            except Exception:
                tv = 1.0
            value = best_val * units - price * units - tv * ticks * 2
            if value <= 0:
                continue
            if best is None or value > best[0]:
                best = (value, dest, row.get("seller"), gd, units, price,
                        ticks)
        return best
    except Exception:
        return None
