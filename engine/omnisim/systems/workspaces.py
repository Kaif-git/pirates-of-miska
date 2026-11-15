"""V1 Foundations — Workspaces & Production (GDD 5.0).

Workspace Efficiency = Base Efficiency x (Condition / 100).
Condition decays with use (skilled users wear it slower, master-built
stations decay slower); output yield and DC scale with efficiency.
Repair via Smithing/Carpentry. Stations without a condition entry default
to 100 (legacy content keeps full yield).
"""
from __future__ import annotations


def state_of(village, key: str) -> dict:
    ws = getattr(village, "workspaces", None)
    if not isinstance(ws, dict):
        try:
            ws = village.comps.setdefault("workspaces", {})
        except Exception:
            ws = village.__dict__.setdefault("workspaces", {})
    st = ws.setdefault(key, {"condition": 100.0})
    return st


def efficiency(village, key: str) -> float:
    try:
        cond = float(state_of(village, key).get("condition", 100.0))
    except Exception:
        cond = 100.0
    return max(0.0, min(1.0, cond / 100.0))


def use(village, key: str, entity=None, amount: float = 2.0) -> float:
    """Wear a station by use. Skilled hands are gentler. Returns efficiency."""
    wear = float(amount)
    try:
        if entity is not None:
            from omnisim.systems.skills import skill_level
            lvl = 1
            for s in ("Smithing", "Carpentry", "Crafting", "Farming"):
                try:
                    lvl = max(lvl, int(skill_level(entity, s)))
                except Exception:
                    pass
            wear *= max(0.4, 1.0 - 0.05 * (lvl - 1))
    except Exception:
        pass
    st = state_of(village, key)
    st["condition"] = max(0.0, float(st.get("condition", 100.0)) - wear)
    return efficiency(village, key)


def repair(village, key: str, amount: float = 25.0) -> float:
    st = state_of(village, key)
    st["condition"] = min(100.0, float(st.get("condition", 100.0)) + amount)
    return efficiency(village, key)


def yield_mult(village, key: str) -> float:
    """Output multiplier: 0.25 at 0% condition, 1.0 at 100%."""
    eff = efficiency(village, key)
    return 0.25 + 0.75 * eff
