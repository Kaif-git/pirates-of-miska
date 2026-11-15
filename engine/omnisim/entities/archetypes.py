"""Archetype = named template loaded from data/archetypes/*.yaml (never hardcoded)."""
from __future__ import annotations
from omnisim.entities.entity import Entity

_next_id = 0


def spawn(archetype: dict, x: int, y: int) -> Entity:
    global _next_id
    _next_id += 1
    return Entity(id=_next_id, name=archetype.get("name", "nobody"),
                  x=x, y=y, glyph=archetype.get("glyph", "?"),
                  comps=dict(archetype.get("comps", {})))
