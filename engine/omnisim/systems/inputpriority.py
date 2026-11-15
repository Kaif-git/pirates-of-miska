"""Input-priority scheduling rule (flag input_priority, default off).

When a producer's own-trade recipe has no inputs in pocket and a seller
with stock of a declared input is reachable, the buy trip outranks the
sale-errand: no new haul errand is formed while the bin is dry, so the
scored buy trip (not the loaded haul) owns the feet. No good-specific
code: reads recipe input rows + stall stock. Needs (rest-halt, thirst,
hunger veto) still preempt — this only yields the errand seat.
"""
from __future__ import annotations


def on():
    try:
        from omnisim.systems.production import _load as _ld
        return bool(((_ld("economy") or {}).get("flags", {}) or {}).get(
            "input_priority", False))
    except Exception:
        return False


def dry_inputs(village, npc) -> list:
    """Declared own-trade recipe inputs missing from pocket. [] = not dry."""
    try:
        from omnisim.systems.production import recipes as _rr
        from omnisim.systems.production import trade_of as _tow
        from omnisim.systems.production import goods_with_tag as _gwt
        tw = _tow(npc) or {}
        rids = set(tw.get("recipes", []) or [])
        missing = []
        pocket = npc.get("pocket", {}) or {}
        for r in _rr().values():
            if r.get("skill", "") != (npc.get("work_skill") or ""):
                continue
            if r.get("id", "") not in rids and rids:
                continue
            if "service" in (r.get("flags", []) or []):
                continue
            for inp in (r.get("inputs", []) or []):
                if "good" in inp:
                    goods = [inp["good"]]
                else:
                    try:
                        goods = list(_gwt(inp["tag"]))
                    except Exception:
                        continue
                need = int(inp.get("qty", 1) or 1)
                have = sum(int(pocket.get(g, 0) or 0) for g in goods)
                if have < need:
                    for g in goods:
                        if stock_near(village, npc, g):
                            missing.append(g)
                            break
        return sorted(set(missing))
    except Exception:
        return []


def stock_near(village, npc, good: str, reach: int = 30) -> bool:
    """Any stall with stock of good within reach of the holder."""
    try:
        for s in village.stalls or []:
            if abs(s["x"] - npc.x) + abs(s["y"] - npc.y) > reach:
                continue
            if (s.get("stock", {}) or {}).get(good, [0, 0])[0] > 0:
                return True
    except Exception:
        pass
    return False


def errand_yield(village, npc) -> bool:
    """True when the haul errand must yield to the input buy trip."""
    if not on():
        return False
    try:
        if (npc.get("role") or "") == "kid":
            return False
        return len(dry_inputs(village, npc)) > 0
    except Exception:
        return False
