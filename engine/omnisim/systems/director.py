"""Event Director — guarantees `theres always something going on`.
RimWorld-style: off-screen factions/kingdoms/animals run abstract clocks;
director injects rumors, brawls, merchant caravans, beast waves near player."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Director:
    tick: int = 0
    pending_events: list[str] = field(default_factory=list)

    def update(self) -> list[str]:
        self.tick += 1
        # TODO Phase 2: faction clocks + incident budget scaled to player wealth/notoriety.
        return []
