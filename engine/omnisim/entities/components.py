"""Component defs — plain dataclasses so save/load stays trivial JSON."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class StatsComp:
    hp: int = 10
    max_hp: int = 10
    speed: int = 100  # energy per turn; lower = faster


@dataclass
class GroupComp:
    count: int = 1        # e.g. wolf pack x5, bandit gang x6
    morale: int = 50


@dataclass
class FactionComp:
    faction: str = "unaffiliated"
    rank: int = 0


@dataclass
class AgendaComp:
    """What this NPC *wants* — utility AI scores these every decision."""
    goals: list[str] = field(default_factory=lambda: ["survive"])
    # e.g. ["get_rich", "serve_faction", "hunt_artifact", "rise_to_king"]


@dataclass
class SkillsComp:
    skills: dict[str, int] = field(default_factory=dict)  # fighting/riding/crafting/business...


@dataclass
class NeedsComp:
    hunger: int = 0
    energy: int = 100
