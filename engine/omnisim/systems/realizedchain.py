"""Realized chain value (flag realized_chain_value, default off).

Two-link test ONLY (wheat -> flour -> bread), no horizon discount (this is
the deliberate departure from derived-demand, stated here and in the
commit message).

  chain_value(g) = realized_price(o) x y

realized_price(o) = trailing mean settled-trade price of o over W=7 days
(from village.trades, actuals only). No trades in window -> fallback to
current valuation (expected_sale_price with the flag bypassed) + logged.
Applied inside expected_sale_price for flour (via bread) so the miller's
buy-side (R2 _gain2) and run-side (expected_margin rev) margins use one
number. quern_flour/bake_bread read the same flour number on their input
side via _input_cost market asks; bake_bread's output side (bread) is a
final good and keeps tape x saleability.
"""
from __future__ import annotations

WINDOW_DAYS = 7


def on():
    try:
        from omnisim.systems.production import _load as _ld
        return bool(((_ld("economy") or {}).get("flags", {}) or {}).get(
            "realized_chain_value", False))
    except Exception:
        return False


def _day_of(village):
    try:
        from omnisim.core.clock import day_of as _d
        return int(_d(village.clock_pts))
    except Exception:
        return 0


def realized_price(village, good):
    """Trailing mean settled price over W days, or None."""
    try:
        day = _day_of(village)
        px = [float(t.get("price", 0)) / max(1, int(t.get("qty", 1)))
              for t in (getattr(village, "trades", []) or [])
              if t.get("item") == good and t.get("item") != "tab"
              and int(day - int(t.get("day", day))) < int(WINDOW_DAYS)
              and float(t.get("price", 0)) > 0]
        if not px:
            return None
        return sum(px) / len(px)
    except Exception:
        return None


# downstream map: intermediate -> (output, yield per input unit).
CHAIN = {"flour": ("bread", 1.0)}


def chain_value(village, good):
    """(value, used_fallback). Flag-gated; None when flag off."""
    if not on():
        return None, False
    try:
        link = CHAIN.get(good)
        if link is None:
            return None, False
        out, y = link
        rp = realized_price(village, out)
        if rp is not None:
            return float(rp) * float(y), False
        from omnisim.systems.production import expected_sale_price as _esp
        import omnisim.systems.production as _P
        # fallback: current valuation with OUR hook bypassed (avoid recursion).
        _prev = _P._RCV_BYPASS if hasattr(_P, "_RCV_BYPASS") else False
        try:
            _P._RCV_BYPASS = True
            fb = float(_esp(village, out)) * float(y)
        finally:
            _P._RCV_BYPASS = _prev
        try:
            if not hasattr(village, "rcv_fallbacks"):
                village.rcv_fallbacks = []
            village.rcv_fallbacks.append(
                {"day": _day_of(village), "good": good, "out": out})
        except Exception:
            pass
        return fb, True
    except Exception:
        return None, False
