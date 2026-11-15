"""Village-measured time value (flag realized_time_value, default off).

Part A: realized_tv per producer = max(floor 0.5c, EMA-0.2/day of earned /
on_shift_ticks) over trailing W=7 days. Earned = coin from production
sales (seller tap); on-shift ticks counted per wait-tick in work blocks.
Used wherever the engine prices TRAVEL/PROCUREMENT/BUY-TRIP scoring:
decision.travel_mc, decision.score_produce, decision.make_vs_buy, and the
two procurement tv terms. Ambition time_value_mc stays for savings, risk,
reserves, buildings, mastery (untouched call sites).
"""
from __future__ import annotations

WINDOW_DAYS = 7.0
EMA_ALPHA = 0.2
FLOOR_C = 0.5


def on():
    try:
        from omnisim.systems.production import _load as _ld
        return bool(((_ld("economy") or {}).get("flags", {}) or {}).get(
            "realized_time_value", False))
    except Exception:
        return False


def _day_of(village):
    try:
        from omnisim.core.clock import day_of as _d
        return int(_d(village.clock_pts))
    except Exception:
        return 0


def record_sale(npc, village, amount):
    """Log one production sale into the trailing window."""
    try:
        log = npc.comps.get("rtv_sales")
        if not isinstance(log, list):
            log = []
            npc.comps["rtv_sales"] = log
        log.append({"day": _day_of(village), "amount": float(amount)})
        if len(log) > 200:
            del log[:len(log) - 200]
    except Exception:
        pass


def record_shift(npc, village, ticks=1):
    """Count on-shift ticks into the trailing window."""
    try:
        log = npc.comps.get("rtv_shifts")
        if not isinstance(log, list):
            log = []
            npc.comps["rtv_shifts"] = log
        day = _day_of(village)
        if log and log[-1].get("day") == day:
            log[-1]["ticks"] = int(log[-1].get("ticks", 0)) + int(ticks)
        else:
            log.append({"day": day, "ticks": int(ticks)})
        while len(log) > int(WINDOW_DAYS) + 2:
            log.pop(0)
    except Exception:
        pass


def _window_sums(npc, village):
    try:
        day = _day_of(village)
        cutoff = day - int(WINDOW_DAYS)
        earned = sum(float(e.get("amount", 0)) for e in
                     (npc.comps.get("rtv_sales", []) or [])
                     if isinstance(e, dict) and int(e.get("day", -9)) > cutoff)
        ticks = sum(int(e.get("ticks", 0)) for e in
                    (npc.comps.get("rtv_shifts", []) or [])
                    if isinstance(e, dict) and int(e.get("day", -9)) > cutoff)
        return earned, ticks
    except Exception:
        return 0.0, 0


def realized_tv(npc, village=None):
    """EMA-smoothed realized coin per on-shift tick, floored at 0.5c."""
    try:
        earned, ticks = _window_sums(npc, village)
        raw = (earned / ticks) if ticks > 0 else 0.0
        prev = npc.comps.get("rtv_ema", None)
        try:
            prev = float(prev) if prev is not None else None
        except Exception:
            prev = None
        ema = raw if prev is None else (EMA_ALPHA * raw + (
            1.0 - EMA_ALPHA) * prev)
        try:
            npc.comps["rtv_ema"] = float(ema)
        except Exception:
            pass
        return max(float(FLOOR_C), float(ema))
    except Exception:
        return float(FLOOR_C)


def tv_for(npc, village=None):
    """Drop-in for time scoring: realized when flagged, else ambition."""
    if on():
        try:
            return float(realized_tv(npc, village)) * 1000.0
        except Exception:
            pass
    try:
        from omnisim.systems.mood import time_value_mc as _amb
        return float(_amb(npc, village))
    except Exception:
        return 1000.0
