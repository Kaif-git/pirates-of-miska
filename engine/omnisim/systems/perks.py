"""Skill perks (GRIND M1): thresholds in data/skill_perks.yaml.

perk_value(): the named key from the highest row at-or-below the level —
level-1 rows are the bases, so all tuning lives in YAML.
unlocked_perks(): display texts for profiles and the menu news feed.
"""
from __future__ import annotations
from pathlib import Path

import yaml

from omnisim.systems.skills import skill_level

_PERKS: dict | None = None


def table() -> dict:
    global _PERKS
    if _PERKS is None:
        path = Path(__file__).resolve().parents[3] / "data" / "skill_perks.yaml"
        _PERKS = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("perks", {})
    return _PERKS


def perk_rows(skill: str) -> list[dict]:
    return table().get(skill, [])


def perk_value(level: int, skill: str, key: str, default):
    """Highest-threshold row at-or-below level that defines key, else default."""
    val, best = default, 0
    for row in perk_rows(skill):
        lv = int(row.get("level", 99))
        if lv <= level and key in row and lv >= best:
            val, best = row[key], lv
    return val


def unlocked_perks(entity, min_level: int = 1) -> list[str]:  # type: ignore[no-untyped-def]
    """'Skill LVL: text' for every reached perk row at-or-above min_level."""
    out = []
    for skill in table():
        lvl = skill_level(entity, skill)
        for row in perk_rows(skill):
            rl = int(row.get("level", 99))
            if min_level <= rl <= lvl and row.get("text"):
                out.append((rl, skill, row["text"]))
    out.sort()
    return [f"{s} {l}: {t}" for l, s, t in out]
