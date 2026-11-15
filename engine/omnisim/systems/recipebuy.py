"""Generic buy options from recipe inputs + horizon-bounded reserve
exemption (flag recipe_buy_options, default off). No good-specific code.

Part A: recipe_inputs_of(village, npc) returns every input good of every
  runnable-by-trade recipe of the holder's trade (rows, incl tag inputs
  via goods_with_tag). valuation.buy_option appends these to the staple
  candidate set with chain-value relief (chainvalue.chain_value, mc->coin).
Part B: exempt_spend(village, npc, good, price): for inputs of
  food-producing recipes (any output with food>0), the R4 reserve does not
  block purchases while the holder's stock of that input is below H days
  of rated use. Rated use/day = per_day_max of the input-using recipes x
  input qty (rows; grain-ops audit: labor anchor x active ticks x inputs).
  H (=3) lives in data/economy.yaml pricing.recipe_buy_horizon_days.
"""
from __future__ import annotations


def on():
    try:
        from omnisim.systems.production import _load as _ld
        return bool(((_ld("economy") or {}).get("flags", {}) or {}).get(
            "recipe_buy_options", False))
    except Exception:
        return False


def horizon_days() -> float:
    try:
        from omnisim.systems.production import _load as _ld
        return float(((_ld("economy") or {}).get("pricing", {}) or {}).get(
            "recipe_buy_horizon_days", 3))
    except Exception:
        return 3.0


def recipe_inputs_of(village, npc) -> dict:
    """{good: max chain relief} over own-trade recipe inputs. {} if off."""
    out: dict = {}
    if not on():
        return out
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
                if "good" in inp:
                    goods = [inp["good"]]
                else:
                    try:
                        goods = list(_gwt(inp["tag"]))
                    except Exception:
                        continue
                for g in goods:
                    try:
                        from omnisim.systems.chainvalue import chain_value
                        from omnisim.systems.pricing import MC
                        cv = chain_value(village, npc, g, 1)
                        relief = float(cv) / float(MC) if cv else 0.0
                    except Exception:
                        relief = 0.0
                    if relief > out.get(g, 0.0):
                        out[g] = relief
    except Exception:
        pass
    return out


def _is_food(good: str) -> bool:
    """Food by either table: satisfies.hunger>0, food>0, or food tag."""
    try:
        from omnisim.systems.production import goods as _goods
        node = _goods().get(good, {}) or {}
        if float((node.get("satisfies", {}) or {}).get("hunger", 0)) > 0:
            return True
        if float(node.get("food", 0) or 0) > 0:
            return True
        if "food" in (node.get("tags", []) or []):
            return True
    except Exception:
        pass
    return False


def food_inputs_of(npc) -> set:
    """Inputs of own-trade recipes whose chain ends in food.

    Direct food outputs count; otherwise an input counts when any of its
    downstream recipe outputs (one level through the rows) is food — so
    mill wheat counts via bake_bread/bake_meal flour use. Rows only.
    """
    out = set()
    try:
        from omnisim.systems.production import recipes as _rr
        from omnisim.systems.production import trade_of as _tow
        from omnisim.systems.production import goods_with_tag as _gwt
        from omnisim.systems.production import goods as _goods
        tw = _tow(npc) or {}
        rids = set(tw.get("recipes", []) or [])
        for r in _rr().values():
            if r.get("skill", "") != (npc.get("work_skill") or ""):
                continue
            if r.get("id", "") not in rids and rids:
                continue
            if "service" in (r.get("flags", []) or []):
                continue
            try:
                outs = [o.get("good") for o in (r.get("outputs", []) or [])]
                food_out = any(_is_food(og) for og in outs)
                if not food_out:
                    # one level downstream: an output eaten by a
                    # food-output recipe makes this input food-chain.
                    from omnisim.systems.production import recipes as _rr2
                    for _r2 in _rr2().values():
                        try:
                            _ins2 = [i.get("good") for i in
                                     (_r2.get("inputs", []) or [])
                                     if "good" in i]
                            _tags2 = [i.get("tag") for i in
                                      (_r2.get("inputs", []) or [])
                                      if "tag" in i]
                        except Exception:
                            continue
                        _uses = any(
                            (og in _ins2) or any(
                                og in _gwt(_t) for _t in _tags2)
                            for og in outs)
                        if not _uses:
                            continue
                        try:
                            if any(_is_food(o.get("good"))
                                   for o in (_r2.get("outputs", []) or [])):
                                food_out = True
                                break
                        except Exception:
                            pass
            except Exception:
                food_out = False
            if not food_out:
                continue
            for inp in (r.get("inputs", []) or []):
                if "good" in inp:
                    out.add(inp["good"])
                else:
                    try:
                        out.update(_gwt(inp["tag"]))
                    except Exception:
                        pass
    except Exception:
        pass
    return out


def rated_use_per_day(npc, good: str) -> float:
    """Units of good the holder's trade eats per day at rated use."""
    total = 0.0
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
                if "good" in inp:
                    hit = (inp["good"] == good)
                else:
                    try:
                        hit = good in _gwt(inp["tag"])
                    except Exception:
                        hit = False
                if hit:
                    try:
                        cap = float(r.get("per_day_max", 3) or 3)
                    except Exception:
                        cap = 3.0
                    total += cap * float(inp.get("qty", 1) or 1)
    except Exception:
        pass
    return total


def exempt_spend(village, npc, good: str, price: float) -> bool:
    """True when this input buy draws inside the horizon exemption."""
    if not on():
        return False
    try:
        if good not in food_inputs_of(npc):
            return False
        rate = rated_use_per_day(npc, good)
        if rate <= 0:
            return False
        have = float((npc.get("pocket", {}) or {}).get(good, 0) or 0)
        horizon_need = float(horizon_days()) * rate
        if have >= horizon_need:
            return False  # beyond H days: reserve applies
        return True
    except Exception:
        return False
