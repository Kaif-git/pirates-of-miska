"""Local shelf view for first contact (flag local_view, default off).

Part A: a holder observes sellers with stock within R=8 tiles of its
workplace (work_at or schedule work anchor). Observation = memory entry
(seller, good, observed price, tick) under the existing SELLER_CAP and
eviction rule. Fires at work-block start (_enter_block) and on workplace
arrival (_arrive during work, or _do_activity work). Never beyond R.
Part B: radius rule for procurement (under the procurement flag):
procure_option/detail skip remembered sellers whose travel distance from
the workplace exceeds R; skipped rows log as out_of_radius.
"""
from __future__ import annotations

from omnisim.systems import procurement as _base

VIEW_RADIUS = 8.0
for _k in ("PROCURE_P_DAYS", "SELLER_CAP", "on", "sellers_of",
           "remember_seller", "known_seller", "cover_days", "need_inputs",
           "procure_option", "procure_detail",
           "remember_seller", "_day_of"):
    try:
        globals()[_k] = getattr(_base, _k)
    except Exception:
        pass

OUT_OF_RADIUS = []


def on():
    try:
        from omnisim.systems.production import _load as _ld
        return bool(((_ld("economy") or {}).get("flags", {}) or {}).get(
            "local_view", False))
    except Exception:
        return False


def workplace_of(npc):
    try:
        wa = npc.comps.get("work_at")
        if isinstance(wa, (list, tuple)) and len(wa) == 2:
            return (float(wa[0]), float(wa[1]))
    except Exception:
        pass
    try:
        for b in (npc.comps.get("sched", []) or []):
            if b.get("act") == "work" and isinstance(b.get("at"), (
                    list, tuple)) and len(b["at"]) == 2:
                return (float(b["at"][0]), float(b["at"][1]))
    except Exception:
        pass
    try:
        return (float(npc.x), float(npc.y))
    except Exception:
        return (0.0, 0.0)


def _dist(a, b):
    try:
        return max(abs(a[0] - b[0]), abs(a[1] - b[1]))
    except Exception:
        return 1e9


def observe(village, npc):
    """One observation sweep: sellers with stock inside R of workplace.
    Returns list of (seller, good, price) seen. Flag-gated."""
    if not on():
        return []
    seen = []
    try:
        wp = workplace_of(npc)
        # recipe inputs the holder's trade can run (same set as need_inputs,
        # but observed regardless of cover: first contact precedes need).
        from omnisim.systems.production import recipes as _rr
        from omnisim.systems.production import trade_of as _tow
        from omnisim.systems.production import goods_with_tag as _gwt
        tw = _tow(npc) or {}
        rids = set(tw.get("recipes", []) or [])
        wants = set()
        for r in _rr().values():
            if r.get("skill", "") != (npc.get("work_skill") or ""):
                continue
            if r.get("id", "") not in rids and rids:
                continue
            if "service" in (r.get("flags", []) or []):
                continue
            for inp in (r.get("inputs", []) or []):
                if "good" in inp:
                    wants.add(inp["good"])
                else:
                    try:
                        for gd in _gwt(inp.get("tag", "")):
                            wants.add(gd)
                    except Exception:
                        pass
        if not wants:
            return []
        from omnisim.systems.schedule import standable_near as _sn
        for s in (village.stalls or []):
            d = _dist(wp, (s.get("x", 0), s.get("y", 0)))
            if d > VIEW_RADIUS:
                continue
            for gd in sorted(wants):
                try:
                    stock = (s.get("stock", {}) or {}).get(gd, [0, 0])[0]
                except Exception:
                    stock = 0
                if stock <= 0:
                    continue
                try:
                    from omnisim.systems.pricing import ask_mc as _amc, MC as _MC
                    price = float(_amc(village, None, gd, 1)) / float(_MC)
                except Exception:
                    price = 1.0
                remember_seller(npc, village, gd, s.get("name", "?"), price)
                try:
                    (npc.comps.setdefault("proc_observed", []).append(
                        {"day": _day_of(village), "seller": s.get(
                            "name", "?"), "good": gd,
                         "price": round(price, 2)}))
                except Exception:
                    pass
                seen.append((s.get("name", "?"), gd, price))
        # NPC pockets inside R (peer sellers, e.g. farmers holding wheat).
        for other in (village.npcs or []):
            if other is npc or other.comps.get("dead"):
                continue
            d = _dist(wp, (other.x, other.y))
            if d > VIEW_RADIUS:
                continue
            for gd in sorted(wants):
                try:
                    have = float((other.get("pocket", {}) or {}).get(gd, 0))
                except Exception:
                    have = 0
                if have < 2:
                    continue
                remember_seller(npc, village, gd, other.name, 1.0)
                try:
                    (npc.comps.setdefault("proc_observed", []).append(
                        {"day": _day_of(village), "seller": other.name,
                         "good": gd, "price": 1.0}))
                except Exception:
                    pass
                seen.append((other.name, gd, 1.0))
    except Exception:
        pass
    return seen


def within_radius(npc, seller_xy):
    try:
        return _dist(workplace_of(npc), seller_xy) <= VIEW_RADIUS
    except Exception:
        return False
