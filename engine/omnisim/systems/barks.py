"""Ambient barks: adjacent villagers speak, role-flavored, time-aware.

Spoken from the play loop (Game.run) only — never from headless ticks — so
tests and smoke stay deterministic. One voice per villager per 2 hours.
"""
from __future__ import annotations
from pathlib import Path

import yaml

BARK_COOLDOWN_MIN = 120

DEFAULT_BARKS = {
    "day": ["Fine weather for honest work.", "Mind the ruts on the road."],
    "night": ["Best get indoors, friend.", "Quiet night. Too quiet, some say."],
}

_BARKS: dict | None = None


def bark_table() -> dict:
    global _BARKS
    if _BARKS is None:
        path = Path(__file__).resolve().parents[3] / "data" / "archetypes.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        _BARKS = data.get("barks", {}) or {}
    return _BARKS


def pick_line(npc, night: bool, clock_pts: int) -> str:
    table = bark_table().get(npc.get("role", ""), DEFAULT_BARKS)
    lines = table.get("night" if night else "day") or DEFAULT_BARKS["day"]
    return lines[(clock_pts // 100 // 60 + npc.id) % len(lines)]


def check_barks(village) -> list[str]:  # type: ignore[no-untyped-def]
    """Speak for newly-adjacent villagers. Returns lines spoken (for tests)."""
    px, py = village.player.x, village.player.y
    hour = (village.clock_pts // 100 // 60) % 24
    night = hour >= 22 or hour < 5
    said = []
    for npc in village.npcs:
        if npc.comps.get("dead"):
            continue  # the fallen keep their silence
        if max(abs(npc.x - px), abs(npc.y - py)) > 1:
            continue
        if village.clock_pts - npc.comps.get("bark_at", -10**12) < BARK_COOLDOWN_MIN * 100:
            continue
        line = f"{npc.name}: \"{pick_line(npc, night, village.clock_pts)}\""
        village.say(line)
        npc.comps["bark_at"] = village.clock_pts
        said.append(line)
        from omnisim.systems.skills import gain_xp, skill_level
        if skill_level(village.player, "Speech") >= 7:  # M1 regular: sought out
            for lv in gain_xp(village.player, "Speech", 1):
                village.say(f"Speech rises to {lv}!")
    return said
