"""V1 Foundations — NPC Relationships (GDD 8.3 foundation).

Relationship Value -100 (Nemesis) .. +100 (Soulmate).
Drivers: fair trade +1, gift +5 (scaled by value), caught steal -15,
social +1; decay toward 0 at dawn when unmaintained.
Effects: price x (1 - rel*0.002), willingness gates for trade/help.
"""
from __future__ import annotations


def rel_of(entity, other: str) -> float:
    try:
        rel = entity.get("rel", {}) if hasattr(entity, "get") else {}
        if isinstance(rel, dict):
            return float(rel.get(other, 0.0))
    except Exception:
        pass
    return 0.0


def adjust(entity, other: str, delta: float) -> float:
    try:
        rel = entity.get("rel", None)
        if not isinstance(rel, dict):
            rel = {}
            entity.comps["rel"] = rel
        rel[other] = max(-100.0, min(100.0, float(rel.get(other, 0.0)) + delta))
        return rel[other]
    except Exception:
        return 0.0


def adjust_both(a, b, delta_a_to_b: float, delta_b_to_a: float | None = None) -> None:
    an = a.name if hasattr(a, "name") else str(a)
    bn = b.name if hasattr(b, "name") else str(b)
    adjust(a, bn, delta_a_to_b)
    adjust(b, an, delta_b_to_a if delta_b_to_a is not None else delta_a_to_b)


def decay_toward_neutral(entity, amount: float = 1.0) -> None:
    try:
        rel = entity.get("rel", {}) if hasattr(entity, "get") else {}
        if not isinstance(rel, dict):
            return
        for k in list(rel.keys()):
            v = float(rel[k])
            if v > 0:
                rel[k] = max(0.0, v - amount)
            elif v < 0:
                rel[k] = min(0.0, v + amount)
    except Exception:
        pass


def price_mult(rel_value: float) -> float:
    """Friend discount / rival markup (also mirrored in pricing.py)."""
    return max(0.8, min(1.2, 1.0 - float(rel_value) * 0.002))


def willing_to_trade(rel_value: float) -> bool:
    return float(rel_value) > -80.0
