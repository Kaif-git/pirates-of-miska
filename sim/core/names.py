"""Shared display-name registry for living characters and ships."""

from __future__ import annotations


def normalize_display_name(name: str) -> str:
    return " ".join(name.split()).casefold()


def living_name_in_use(world, name: str, *, exclude_id: str | None = None) -> bool:
    normalized = normalize_display_name(name)
    if not normalized:
        return True
    for entity_id, entity in world.entities.items():
        if entity_id == exclude_id or entity.get("kind") not in {"character", "ship"}:
            continue
        current = entity.get("display_name") if entity["kind"] == "character" else entity.get("name")
        if isinstance(current, str) and normalize_display_name(current) == normalized:
            return True
    return False


def validate_living_names(world) -> None:
    names: dict[str, str] = {}
    for entity_id, entity in sorted(world.entities.items()):
        kind = entity.get("kind")
        if kind not in {"character", "ship"}:
            continue
        name = entity.get("display_name") if kind == "character" else entity.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Living {kind} has no display name: {entity_id}")
        normalized = normalize_display_name(name)
        if normalized in names:
            raise ValueError(f"Duplicate living display name: {entity_id} and {names[normalized]}")
        names[normalized] = entity_id
