"""Part A: chain_value for intermediates (flag chain_clearing, default off).

chain_value(village, buyer, good): discounted downstream value through
recipe rows. Recursion mirrors reference_value BUT at each downstream
output uses live marginal_value/tape when present:
  value(good) = max over recipes using good as input of
    (value(output) x out_qty - other_input_costs - time) / in_qty x discount
  value(output) = marginal_value_mc(output) if relief>0 else tape/fair.
Discount: patience-derived horizon share hz/90 capped 1.0, where
  hz = mood.horizon_days(npc) = 15 + 75*patience (existing Step 4 model).
  discount = min(1.0, hz/90). No new constant.
Terminal: food/final goods use needs3 marginal_value_mc (relief-based);
  when relief is 0, fair (NOT fair x 1.2 - the fallback being replaced).
When flag off: needs3 keeps fair x 1.2 fallback (untouched).
When flag on: fallback replaced by chain_value; same function feeds
  bid side (marginal_value_mc) and documents ask-side parity.
"""
from __future__ import annotations


def on():
    try:
        from omnisim.systems.production import _load as _ld
        return bool(((_ld("economy") or {}).get("flags", {}) or {}).get(
            "chain_clearing", False))
    except Exception:
        return False


def discount_of(npc):
    try:
        from omnisim.systems.mood import horizon_days as _hz
        hz = float(_hz(npc))
    except Exception:
        hz = 15.0
    return min(1.0, hz / 90.0)


def _matches(good, inp):
    if inp.get("good") == good:
        return True
    if "tag" in inp:
        try:
            from omnisim.systems.production import goods_with_tag
            return good in goods_with_tag(inp.get("tag"))
        except Exception:
            return False
    return False


def chain_value(village, buyer, good, q=1, _seen=()):
    try:
        from omnisim.systems.production import (recipes, reference_value,
                                                goods)
        from omnisim.systems.pricing import fair_mc
        if good in _seen:
            return fair_mc(village, good, q)
        disc = discount_of(buyer)
        best = None
        for r in recipes().values():
            ins = r.get("inputs", []) or []
            hit = [i for i in ins if _matches(good, i)]
            if not hit:
                continue
            outs = r.get("outputs", []) or []
            if not outs:
                continue
            in_qty = sum(float(i.get("qty", 1)) for i in hit)
            # downstream output live value
            out_val = 0.0
            ok = True
            for o in outs:
                og = o.get("good")
                oq = float(o.get("qty", 1))
                try:
                    from omnisim.systems.needs3 import relief_units
                    from omnisim.systems.needs3 import kappa_mc
                    rel = relief_units(buyer, og, village)
                    if rel > 0:
                        ov = rel * kappa_mc(village, buyer)
                    else:
                        # recurse when intermediate, else fair (no 1.2)
                        if og in _seen or og == good:
                            ov = fair_mc(village, og, 1)
                        else:
                            sub = chain_value(village, buyer, og,
                                              1, _seen + (good,))
                            ov = sub if sub and sub > 0 else fair_mc(
                                village, og, 1)
                except Exception:
                    ov = fair_mc(village, og, 1)
                out_val += ov * oq
            # other input costs at reference
            other = 0.0
            for i in ins:
                if _matches(good, i):
                    continue
                try:
                    if "good" in i:
                        c = reference_value(i["good"])
                    else:
                        from omnisim.systems.production import goods_with_tag
                        cands = [reference_value(g2)
                                 for g2 in goods_with_tag(i.get("tag"))]
                        c = min(cands) if cands else 0.0
                    other += float(c) * float(i.get("qty", 1)) * 1000.0
                except Exception:
                    pass
            try:
                tv = float((recipes() and 1) or 1) * 1000.0
                from omnisim.systems.production import _load as _ld
                tv = float((_ld("economy") or {}).get(
                    "time_value_copper_per_tick", 1)) * 1000.0
            except Exception:
                tv = 1000.0
            per_unit = ((out_val - other - tv) / max(1.0, in_qty)) * disc
            if best is None or per_unit > best:
                best = per_unit
        if best is None:
            return None
        return max(0.0, best * float(q))
    except Exception:
        return None
