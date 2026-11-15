"""FISHERY: Stillpond as biology, not bathtub.

Logistic recruitment each dawn: S += r*S*(1 - S/K), throttled by a weak
Allee effect (S/(S+8)) — a pond fished to puddle barely spawns back, so
overfishing collapses it for a season. Catchability scales with density
(S/K): thin water, thin nets, and an empty pond yields exactly nothing.

K, r, and opening stock live on the pond node in world yaml, so a second
pond later just works.
"""
from __future__ import annotations

ALLEE = 8.0  # spawners need company: below ~8 fish the pond nearly stalls


def pond_params(village) -> tuple[float, float]:  # type: ignore[no-untyped-def]
    """(K, r) for Stillpond — data first, sane pond second."""
    K = float(getattr(village, "fish_K", 120) or 120)
    r = float(getattr(village, "fish_r", 0.15) or 0.15)
    return max(20.0, K), max(0.01, min(0.5, r))


def stock_of(village) -> float:  # type: ignore[no-untyped-def]
    """Living stock, never below zero."""
    return max(0.0, float(getattr(village, "fish_stock", 80) or 0))


def recruit(village) -> float:  # type: ignore[no-untyped-def]
    """Dawn spawning. Returns the new stock."""
    K, r = pond_params(village)
    S = stock_of(village)
    if S > 0:
        S = S + r * S * (1.0 - S / K) * (S / (S + ALLEE))
    village.fish_stock = max(0.0, min(K, S))
    return village.fish_stock


def catch_scaled(qty: int, village) -> int:  # type: ignore[no-untyped-def]
    """Density-thinned catch: full nets in full water, nothing in a puddle."""
    K, _ = pond_params(village)
    S = stock_of(village)
    if S <= 0 or qty <= 0:
        return 0
    return max(0, int(round(qty * min(1.0, S / K))))


def take(village, qty: int) -> int:  # type: ignore[no-untyped-def]
    """Draw the scaled catch out of the pond. Returns what the nets held."""
    got = catch_scaled(qty, village)
    village.fish_stock = max(0.0, stock_of(village) - max(0, got))
    return got


def msy(village) -> float:  # type: ignore[no-untyped-def]
    """Maximum sustainable yield per dawn — what the pond can pay forever."""
    K, r = pond_params(village)
    return r * K / 4.0


def state_line(village) -> str:  # type: ignore[no-untyped-def]
    """One honest line for the sidebar: full water, thinning, or fished out."""
    K, _ = pond_params(village)
    S = stock_of(village)
    frac = S / K if K > 0 else 0.0
    word = "full water" if frac > 0.66 else ("thinning" if frac > 0.25
                                            else ("near fished out" if S > 0 else "fished out"))
    return f"Stillpond {int(S)}/{int(K)} ({word})"
