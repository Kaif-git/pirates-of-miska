"""Elapsed-time food aging and freshness labels for item stacks."""
from __future__ import annotations


def freshness_label(item: dict) -> str | None:
    shelf_life = item.get("shelf_life_ticks")
    if not isinstance(shelf_life, int) or shelf_life <= 0:
        return None
    age = max(0, int(item.get("age_ticks", 0)))
    if age >= shelf_life:
        return "Rotten"
    remaining = max(0, round((1.0 - age / shelf_life) * 100))
    if remaining <= 35:
        return f"Near spoilage · {remaining}% life"
    return f"Fresh · {remaining}% life"


def freshness_stacks_compatible(existing: dict, incoming: dict) -> bool:
    left_life, right_life = existing.get("shelf_life_ticks"), incoming.get("shelf_life_ticks")
    if left_life != right_life:
        return False
    if left_life is None:
        return True
    return int(existing.get("age_ticks", 0)) == int(incoming.get("age_ticks", 0))


def age_food(view, ticks: int) -> None:
    """Age perishable stacks wherever they are stored in the current world."""
    elapsed = max(0, int(ticks))
    if not elapsed:
        return
    item_lists = [view.inventory.items]
    item_lists.extend(view.containers.values())
    item_lists.extend(view.surface_items.values())
    item_lists.extend(view.ground_items.values())
    for stacks in item_lists:
        for item in stacks:
            if item.get("shelf_life_ticks"):
                item["age_ticks"] = max(0, int(item.get("age_ticks", 0))) + elapsed
