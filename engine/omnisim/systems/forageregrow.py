"""Forage regrowth (flag forage_regrowth, default off). Owner approval:
instruction of 2026-10-13 (multi-source food milestone follow-up).

Each wild-source row gets patch stock S (capacity K per patch, regrowth
rate r, both from data with marked assumptions where uncited). Harvest
reduces S by units taken; regrowth per dawn is r x S x (1 - S/K), the
same logistic form as fishery.recruit (no Allee term: bushes are not
fish). S at 0 yields nothing. Steady-state max per patch rK/4.

Marked assumptions (open calibrations, in-row): berry K=8 r=0.3
(~0.6/d/patch); apple K=6 r=0.2 (~0.3/d/patch); wild turnip K=5 r=0.4
(~0.5/d/patch); meadow herbs K=6 r=0.25 (~0.375/d/patch). No cited
figures found; each row carries assumption:true.

Flag off: do_forage/spot_near behave exactly as HEAD (tile-binary
picked-clean set). Flag on: patch stocks gate yield.
"""
from __future__ import annotations

def on():
    try:
        from omnisim.systems.production import _load as _ld
        return bool(((_ld("economy") or {}).get("flags", {}) or {}).get(
            "forage_regrowth", False))
    except Exception:
        return False


def _stocks(village):
    s = getattr(village, "forage_stocks", None)
    if not isinstance(s, dict):
        s = {}
        village.forage_stocks = s
    return s


def params_of(src):
    try:
        K = float(src.get("patch_K", 8))
    except Exception:
        K = 8.0
    try:
        r = float(src.get("patch_r", 0.3))
    except Exception:
        r = 0.3
    return max(1.0, K), max(0.01, min(1.0, r))


def stock_of(village, tile, good):
    return float(_stocks(village).get((tile, good), None)
                 if (tile, good) in _stocks(village) else float("nan"))


def recruit(village) -> None:
    """Dawn regrowth for every tracked patch (logistic, pond form)."""
    try:
        from omnisim.systems.forage import sources as _srcs
        for (tile, good), S in list(_stocks(village).items()):
            src = next((s for s in _srcs()
                        if str(s.get("good", "")) == good), None)
            if src is None:
                continue
            K, r = params_of(src)
            S = max(0.0, min(K, S + r * max(0.0, S) * (1.0 - S / K)))
            _stocks(village)[(tile, good)] = S
    except Exception:
        pass


def msy_of(src, food_value):
    K, r = params_of(src)
    return r * K / 4.0 * float(food_value)
