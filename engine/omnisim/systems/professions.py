"""V1 Foundations — Professions as emergent labels (GDD 7.0).

Profession = Function of (Primary Income Source, Primary Skill Used,
Social Role). Derived from the activity ledger, never assigned.
The ledger counts hours per skill; the label is max(ledger, last 7 days).
Legacy role/work_skill remain as behavior fallback; display uses this.
"""
from __future__ import annotations

SKILL_TO_PROFESSION = {
    "Farming": "Farmer",
    "Mining": "Miner",
    "Woodcutting": "Woodcutter",
    "Smithing": "Blacksmith",
    "Carpentry": "Carpenter",
    "Cooking": "Cook",
    "Trading": "Merchant",
    "Fishing": "Fisher",
    "Herbalism": "Healer",
    "Pottery": "Potter",
}


def ledger_of(entity) -> dict:
    try:
        ledger = entity.get("activity_ledger", None)
        if not isinstance(ledger, dict):
            ledger = {}
            entity.comps["activity_ledger"] = ledger
        return ledger
    except Exception:
        return {}


def record(entity, skill: str, hours: float = 0.5) -> None:
    if not skill:
        return
    ledger = ledger_of(entity)
    try:
        ledger[skill] = float(ledger.get(skill, 0.0)) + float(hours)
    except Exception:
        pass


def profession_of(entity) -> str:
    """Emergent label from dominant activity. Falls back to role when empty."""
    ledger = ledger_of(entity)
    if ledger:
        try:
            best = max(ledger, key=lambda k: ledger[k])
            if float(ledger[best]) > 0:
                return SKILL_TO_PROFESSION.get(best, best)
        except Exception:
            pass
    try:
        role = entity.get("role", None)
        if role:
            return str(role)  # keep legacy casing ("barkeep", not "Barkeep")
    except Exception:
        pass
    return "Laborer"
