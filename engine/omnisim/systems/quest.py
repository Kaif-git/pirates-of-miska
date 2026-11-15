"""Quest stub — data-driven chain: data/quests/*.yaml -> stages with triggers.
Supports: fetch/artifact hunt, delivery, sabotage (rob employer), kingly ascension."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Quest:
    id: str
    title: str
    stage: int = 0
    stages: list[str] = field(default_factory=list)

    def advance(self) -> bool:
        if self.stage < len(self.stages) - 1:
            self.stage += 1
            return True
        return False  # quest complete
