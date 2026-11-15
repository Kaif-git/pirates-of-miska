"""Combat stub — Phase 1: simple deterministic hit. Phase 2: skills/position/morale."""
from __future__ import annotations


def attack_damage(attacker_skill: int, weapon: int = 1) -> int:
    return max(1, attacker_skill // 10 + weapon)
