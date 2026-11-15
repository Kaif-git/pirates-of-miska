"""Derived demand for intermediates (flag derived_demand, default off).

Part A: dependency order over recipe rows (consumer good -> outputs).
  build_order() returns goods in reverse dependency order (final goods
  first); raises ValueError on a positive-yield cycle in both directions.
Part B: derived_value(village, good, output_value_of): for intermediates,
  max over consuming recipes of
    (output_value(out) x out_qty / in_qty) x discount
  output_value = tape (via expected_price) if traded lately else already-
  computed derived value else reference fallback. discount = existing
  horizon model min(1, horizon_days/90) with a neutral holder (patience
  0.5 -> hz 52.5 -> 0.583); exact formula stated (mood.py:76 + chainvalue
  discount_of). Applied inside expected_sale_price for intermediates when
  flag on: replaces esp x saleability with the derived value. Both ask
  (fair_mc via expected_price path) and bid (marginal via chain when on)
  read the same number through one function: derived_demand.value_of.
"""
from __future__ import annotations

DISCOUNT_NEUTRAL = min(1.0, (15.0 + 75.0 * 0.5) / 90.0)  # 0.5833


def on():
    try:
        from omnisim.systems.production import _load as _ld
        return bool(((_ld("economy") or {}).get("flags", {}) or {}).get(
            "derived_demand", False))
    except Exception:
        return False


def edges():
    """{consumed_good: set(output_goods)} from recipe rows (tags expanded)."""
    from omnisim.systems.production import recipes as _rr
    from omnisim.systems.production import goods_with_tag as _gwt
    out: dict[str, set] = {}
    for r in _rr().values():
        outs = [o.get("good") for o in (r.get("outputs", []) or [])
                if o.get("good")]
        if not outs:
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
                out.setdefault(g, set()).update(outs)
    return out


def build_order() -> list:
    """Reverse dependency order: downstream-valued goods first.

    Raises ValueError on a positive-yield two-way cycle.
    """
    from omnisim.systems.production import recipes as _rr
    from omnisim.systems.production import goods_with_tag as _gwt
    # positive-yield cycle check: A consumed to make B and B consumed to
    # make A, both with output qty >= 1.
    yields: dict = {}
    for r in _rr().values():
        try:
            outs = [(o.get("good"), float(o.get("qty", 1)))
                    for o in (r.get("outputs", []) or []) if o.get("good")]
        except Exception:
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
                for og, oq in outs:
                    yields.setdefault((g, og), 0.0)
                    yields[(g, og)] = max(yields[(g, og)], oq)
    for (a, b), qab in list(yields.items()):
        if a == b:
            continue
        qba = yields.get((b, a), 0.0)
        if qab >= 1.0 and qba >= 1.0:
            raise ValueError("positive-yield cycle: %s <-> %s" % (a, b))
    e = edges()
    # self-loops (service/mend rows consuming their own output) are not
    # dependency cycles: drop self edges globally before ordering.
    for _g in list(e):
        if _g in e[_g]:
            e[_g] = set(x for x in e[_g] if x != _g)
    order: list = []
    seen: set = set()

    def visit(g, stack=()):
        if g in seen:
            return
        if g in stack:
            raise ValueError("cycle: %s" % ("->".join(stack + (g,))))
        for d in sorted(e.get(g, ())):
            visit(d, stack + (g,))
        seen.add(g)
        order.append(g)

    for g in sorted(e):
        visit(g)
    return order


def output_value_of(village, good: str, derived: dict) -> float:
    """Downstream value: tape esp if traded lately, else derived, else ref."""
    try:
        from omnisim.systems.production import _traded_lately
        from omnisim.systems.production import expected_price
        if _traded_lately(village, good):
            return float(expected_price(village, good))
    except Exception:
        pass
    if good in derived:
        return float(derived[good])
    try:
        from omnisim.systems.production import reference_value
        rv = reference_value(good)
        if rv != float("inf"):
            return float(rv)
    except Exception:
        pass
    return 0.0


def derived_table(village) -> dict:
    """{intermediate good: derived value} in reverse dependency order."""
    table: dict = {}
    try:
        from omnisim.systems.production import recipes as _rr
        from omnisim.systems.production import goods_with_tag as _gwt
        order = build_order()
        for g in order:
            best = None
            for r in _rr().values():
                outs = [o for o in (r.get("outputs", []) or [])
                        if o.get("good")]
                if not outs:
                    continue
                for inp in (r.get("inputs", []) or []):
                    if "good" in inp:
                        hit = (inp["good"] == g)
                    else:
                        try:
                            hit = g in _gwt(inp["tag"])
                        except Exception:
                            hit = False
                    if not hit:
                        continue
                    try:
                        in_qty = max(1.0, float(inp.get("qty", 1) or 1))
                    except Exception:
                        in_qty = 1.0
                    for o in outs:
                        ov = output_value_of(village, o.get("good"), table)
                        if ov <= 0:
                            continue
                        v = (ov * float(o.get("qty", 1) or 1) / in_qty
                             * DISCOUNT_NEUTRAL)
                        if best is None or v > best:
                            best = v
            if best is not None:
                table[g] = best
    except ValueError:
        raise
    except Exception:
        pass
    return table


def value_of(village, good: str) -> float | None:
    """One number for intermediates when flag on; None otherwise."""
    if not on():
        return None
    try:
        return derived_table(village).get(good)
    except Exception:
        return None
