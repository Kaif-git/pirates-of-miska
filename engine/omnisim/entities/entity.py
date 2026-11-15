"""Entity = id + position + glyph + components dict.

Groups (200x) vs individuals (200x) distinction:
- Group: one Entity with GroupComp(count=6) — ticks as ONE actor (cheap).
- Individual: full comps (stats/skills/agenda) — LOD ticked (near=every turn,
  far=every N). For now village NPCs stand still; AI arrives in Phase 2.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Entity:
    id: int
    name: str
    x: int = 0
    y: int = 0
    glyph: str = "@"
    color: tuple[int, int, int] = (255, 255, 255)
    blocks: bool = True
    comps: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.comps.get(key, default)
