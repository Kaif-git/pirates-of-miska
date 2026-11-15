"""Own-input keep rule (flag own_input_keep, default off).

A producer keeps its own recipe inputs: for each input good of any recipe
its trade can run, keep rated_daily_use x HORIZON_DAYS units. Rated use
from data rows (labor.yaml anchor ~10 work-ticks/day), with downstream
pull: an intermediate inherits the finished-food rate it feeds.

- Cooking wheat: quern 2 grain -> 2 flour feeds bake 1 flour -> 1 bread
  at Cooking 1.0/tick x 10 = 10 bread/day => 10 wheat/day => keep 30.
- Cooking flour: 10/day => keep 30.
- Milling wheat: mill 2 grain -> 2 flour at Milling 2/tick x 10 = 20
  flour/day => 20 wheat/day => keep 60.
Generic: no named goods. Reads recipes + labor doc at call time.
"""
from __future__ import annotations

HORIZON_DAYS = 3
_TICKS_PER_DAY = 10


def on():
    try:
        from omnisim.systems.production import _load as _ld
        return bool(((_ld("economy") or {}).get("flags", {}) or {}).get(
            "own_input_keep", False))
    except Exception:
        return False


def _rates_per_tick(skill):
    try:
        from omnisim.systems.production import _load as _ld
        rates = ((_ld("labor") or {}).get("rates_per_tick", {}) or {}).get(
            skill, {}) or {}
        if rates:
            return float(next(iter(rates.values())))
    except Exception:
        pass
    return 1.0


def _is_food(good):
    try:
        from omnisim.systems.production import goods
        return "food" in ((goods().get(good, {}) or {}).get("tags", []) or [])
    except Exception:
        return False


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


def rated_use_per_day(npc, good):
    try:
        from omnisim.systems.production import recipes, trade_of
        trade = trade_of(npc)
        if trade is None:
            return 0.0
        skill = trade.get("skill", "")
        rpt = _rates_per_tick(skill)
        recs = recipes()
        # finished-food rate of this trade (per finished unit)
        food_rate = 0.0
        for rid in trade.get("recipes", []) or []:
            r = recs.get(rid)
            if r is None:
                continue
            outs = r.get("outputs", []) or []
            if outs and _is_food(outs[0].get("good")):
                food_rate = max(food_rate, rpt * float(outs[0].get("qty", 1)))
        use = 0.0
        for rid in trade.get("recipes", []) or []:
            r = recs.get(rid)
            if r is None:
                continue
            outs = r.get("outputs", []) or []
            if not outs:
                continue
            out_good = outs[0].get("good")
            out_qty = float(outs[0].get("qty", 1))
            for inp in r.get("inputs", []) or []:
                if not _matches(good, inp):
                    continue
                in_qty = float(inp.get("qty", 1))
                if _is_food(out_good):
                    use = max(use, rpt * out_qty * (in_qty / max(1.0, out_qty)))
                elif food_rate > 0:
                    # intermediate feeds finished food 1:1 through the chain:
                    # batches needed = food_rate / out_qty; input per batch
                    use = max(use, (food_rate / max(1.0, out_qty)) * in_qty)
                else:
                    use = max(use, rpt * out_qty * (in_qty / max(1.0, out_qty)))
        return use * _TICKS_PER_DAY
    except Exception:
        return 0.0


def keep_extra(npc, good):
    if not on():
        return 0
    try:
        use = rated_use_per_day(npc, good)
        if use <= 0:
            return 0
        return int(round(use * HORIZON_DAYS))
    except Exception:
        return 0
