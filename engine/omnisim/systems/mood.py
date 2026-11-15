"""Step 4: NPC traits, derived stats, and mood.

Traits are nine fixed numbers in [0,1], drawn once at creation from a
seeded RNG (stable faces: same name, same traits). They live under the
``traits9`` comp key — NOT ``traits``, whose categorical readers
(GLUTTONOUS/AMBITIOUS/...) would misread numeric values as flags.

Mood is one number in [-100,+100] per NPC, an EMA toward a raw score
built from Step-3 wants (+) and pains (-), plus decaying life events.
Performance derived from mood scales yield, XP, and time worked, floored
at 0.7 so a bad week never becomes a death spiral (with the seek-relief
veto in schedule.py as the second guard).
"""

import math

TRAITS = ("ambition", "thrift", "luxury", "patience", "risk",
          "sociability", "diligence", "generosity", "resilience")


def _econ() -> dict:
    try:
        from omnisim.systems.production import _load as _ld
        return _ld("economy") or {}
    except Exception:
        return {}


def _cfg() -> dict:
    try:
        return (_econ().get("mood", {}) or {})
    except Exception:
        return {}


def on() -> bool:
    try:
        return bool((_econ().get("flags", {}) or {}).get("step_4", False))
    except Exception:
        return False


# --- traits -----------------------------------------------------------------

def assign_traits(npc, rng) -> dict:
    """Draw the nine traits once. Same seed, same soul."""
    try:
        t = {k: round(float(rng.random()), 3) for k in TRAITS}
        npc.comps["traits9"] = t
        return t
    except Exception:
        return {}


def traits_of(npc) -> dict:
    try:
        t = npc.get("traits9", {}) if hasattr(npc, "get") else {}
        return dict(t) if isinstance(t, dict) else {}
    except Exception:
        return {}


def _t(npc, key: str) -> float:
    try:
        return max(0.0, min(1.0, float(traits_of(npc).get(key, 0.5))))
    except Exception:
        return 0.5


# --- derived stats (Steps 6/7 read these; mood uses kappa/want factors now) --

def reserve_days(npc) -> float:
    return 4.0 + 16.0 * _t(npc, "thrift")


def horizon_days(npc) -> float:
    return 15.0 + 75.0 * _t(npc, "patience")


def switch_mult(npc) -> float:
    return 1.5 - 0.8 * _t(npc, "risk")


def time_value_mc(npc, village=None) -> float:
    """Ambition prices an hour; wealth raises the stakes."""
    try:
        base = float(_econ().get("time_value_copper_per_tick", 1)) * 1000.0
        amb = _t(npc, "ambition")
        mult = 0.5 + amb
        try:
            from omnisim.systems.needs3 import purse_ref
            purse = float((npc.get("pocket", {}) or {}).get("coin", 0))
            ref = max(1.0, float(purse_ref(village)))
            mult *= math.sqrt(1.0 + max(0.0, purse) / ref)
        except Exception:
            pass
        return base * mult
    except Exception:
        return 1000.0


def spend_propensity(npc) -> float:
    return (0.3 + 0.7 * _t(npc, "luxury")) * (1.0 - 0.5 * _t(npc, "thrift"))


def kappa_factor(npc) -> float:
    """Thrift lowers kappa: misers bid less per unit of relief."""
    if not on():
        return 1.0
    try:
        return 1.0 - float(_cfg().get("thrift_kappa", 0.3)) * _t(npc, "thrift")
    except Exception:
        return 1.0


def want_factor(npc) -> float:
    """Luxury multiplies want weights; thrift damps them."""
    if not on():
        return 1.0
    try:
        return ((1.0 + _t(npc, "luxury"))
                * (1.0 - float(_cfg().get("thrift_want", 0.3)) * _t(npc, "thrift")))
    except Exception:
        return 1.0


# --- mood -------------------------------------------------------------------

def _day_of(village) -> int:
    try:
        from omnisim.core.clock import day_of as _d
        return int(_d(village.clock_pts))
    except Exception:
        try:
            return int(getattr(village, "clock_pts", 0)) // 144000
        except Exception:
            return 0


def add_event(npc, village, amount: float, kind: str = "") -> None:
    """Log a life event; salience decays ~5%/day (Step 5 caps apply later)."""
    try:
        evs = npc.comps.get("mood_events")
        if not isinstance(evs, list):
            evs = []
            npc.comps["mood_events"] = evs
        evs.append({"day": _day_of(village), "amount": float(amount), "kind": str(kind)})
        del evs[:-32]
    except Exception:
        pass


def event_sum(npc, village) -> float:
    try:
        decay = float(_cfg().get("event_decay", 0.05))
        today = _day_of(village)
        total = 0.0
        evs = npc.comps.get("mood_events") if hasattr(npc, "comps") else None
        for e in (evs or []):
            if not isinstance(e, dict):
                continue
            age = max(0, today - int(e.get("day", today)))
            total += float(e.get("amount", 0)) * ((1.0 - decay) ** age)
        return total
    except Exception:
        return 0.0


def relationship_sum(npc, village=None) -> float:
    """Step 5 trust-weighted contact ledger: mean live trust x 10."""
    if village is None:
        return 0.0
    try:
        from omnisim.systems.memory import on as _m5_on
        if not _m5_on():
            return 0.0
    except Exception:
        return 0.0
    try:
        from omnisim.systems.memory import trust_of as _m5_trust
        live = [n for n in (getattr(village, "npcs", []) or []) if not n.comps.get("dead") and n is not npc][:8]
        if not live:
            return 0.0
        return sum(float(_m5_trust(npc, n.name)) for n in live) / len(live) * 10.0
    except Exception:
        return 0.0


def raw_mood(npc, village=None) -> float:
    """Wants lift (+0..40), pains drag (x0.4), events echo, Step 5 adds bonds."""
    try:
        from omnisim.systems.needs3 import npc_wants, pain, level_of
        wf = want_factor(npc)
        up = sum(float(v.get("U", 0)) for v in npc_wants(npc, village).values()) * wf
        try:
            pw = float(_cfg().get("pain_weight", 0.4))
        except Exception:
            pw = 0.4
        down = sum(pain(ch, level_of(npc, ch)) for ch in
                   ("hunger", "thirst", "rest", "cold", "hp")) * pw
        return up - down + event_sum(npc, village) + relationship_sum(npc, village)
    except Exception:
        return 0.0


def tick_mood(village, npc, mins: float) -> float:
    """EMA toward raw; resilience sets the recovery rate."""
    try:
        cur = float(npc.get("mood", 0.0))
        raw = raw_mood(npc, village)
        try:
            tau = float(_cfg().get("rate_ticks", 24)) * 10.0  # 24 ten-min ticks
        except Exception:
            tau = 240.0
        rate = max(0.0, min(1.0, float(mins) / max(1.0, tau)))
        rate *= 0.7 + 0.6 * _t(npc, "resilience")
        rate = max(0.0, min(1.0, rate))
        cur += (raw - cur) * rate
        npc.comps["mood"] = max(-100.0, min(100.0, cur))
        return float(npc.comps["mood"])
    except Exception:
        return 0.0


def mood_of(npc) -> float:
    try:
        return max(-100.0, min(100.0, float(npc.get("mood", 0.0))))
    except Exception:
        return 0.0


def perf_of(npc) -> float:
    """Output multiplier 0.7..1.15. Resilience shrinks the downside."""
    if not on():
        return 1.0
    try:
        m = mood_of(npc)
        try:
            pp = float(_cfg().get("perf_pos", 0.15))
            pn = float(_cfg().get("perf_neg", 0.30))
            fl = float(_cfg().get("perf_floor", 0.7))
        except Exception:
            pp, pn, fl = 0.15, 0.30, 0.7
        if m >= 0:
            return 1.0 + pp * m / 100.0
        return max(fl, 1.0 + pn * (1.0 - 0.5 * _t(npc, "resilience")) * m / 100.0)
    except Exception:
        return 1.0


def worked_frac(npc) -> float:
    """Diligence resists mood-driven slacking on the fraction of block used."""
    if not on():
        return 1.0
    try:
        m = mood_of(npc)
        if m >= 0:
            return 1.0
        p = perf_of(npc)
        return max(0.7, 1.0 + (p - 1.0) * (1.0 - 0.5 * _t(npc, "diligence")))
    except Exception:
        return 1.0


def village_famine(village) -> float:
    """Step 4 supersedes village_mood (A13): famine is bellies, never shelves."""
    try:
        npcs = [n for n in (getattr(village, "npcs", []) or [])
                if not (n.comps.get("dead") if hasattr(n, "comps") else False)]
        if not npcs:
            return 0.0
        avg = sum(float(n.get("hunger", 0)) for n in npcs) / len(npcs)
        return max(0.0, min(1.0, avg / 80.0))
    except Exception:
        return 0.0
