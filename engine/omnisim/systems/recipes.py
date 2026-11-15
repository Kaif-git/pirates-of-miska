"""Recipe engine (GRIND M4): data/recipes.yaml drives W-crafting.

craftable(v) lists fully-buildable recipes at reachable stations; locked(v)
lists near-misses as menu hints so grinding has visible targets; do_craft(v, r)
consumes, stamps lore, grants XP and advances the clock.
"""
from __future__ import annotations
from pathlib import Path

import yaml

from omnisim.entities.items import add_item, item_name, lore_q, pack_of, stamp_lore, take_item
from omnisim.systems.skills import skill_level

_RECIPES: list | None = None


def table() -> list:
    global _RECIPES
    if _RECIPES is None:
        path = Path(__file__).resolve().parents[3] / "data" / "recipes.yaml"
        _RECIPES = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("recipes", [])
    return _RECIPES


def reachable(village, station: str) -> bool:  # type: ignore[no-untyped-def]
    """Station in working reach of the player."""
    from omnisim.world import tile as T
    if station == "anywhere":
        return True
    px, py = village.player.x, village.player.y
    gm = village.game_map
    tid = {"anvil": T.ANVIL, "workbench": T.WORKBENCH, "hearth": T.HEARTH}.get(station)
    if tid is None:
        return False
    return any(gm.in_bounds(px + dx, py + dy) and gm.tile_id(px + dx, py + dy) == tid
               for dx in (-1, 0, 1) for dy in (-1, 0, 1))


def _tool_target(player, tool: str) -> int:  # type: ignore[no-untyped-def]
    """Forged quality: one step toward min(Smithing cap, 3)."""
    from omnisim.systems.perks import perk_value
    from omnisim.systems.tools import tool_q
    cap = min(perk_value(skill_level(player, "Smithing"), "Smithing", "tool_cap", 1), 3)
    cur = tool_q(player, tool)
    return min(cap, cur + 1) if cur else 1


def _inputs_ok(player, recipe: dict) -> bool:  # type: ignore[no-untyped-def]
    pack = pack_of(player)
    min_q = recipe.get("min_q", {}) or {}
    for item_id, qty in (recipe.get("inputs", {}) or {}).items():
        if pack.get(item_id, 0) < qty:
            return False
        if item_id in min_q and lore_q(player, item_id) < min_q[item_id]:
            return False
    if recipe.get("tool"):
        from omnisim.systems.tools import tool_q
        cur = tool_q(player, recipe["tool"])
        from omnisim.systems.perks import perk_value
        cap = min(perk_value(skill_level(player, "Smithing"), "Smithing", "tool_cap", 1), 3)
        if cur >= cap:
            return False
    return True


def craftable(village, station: str | None = None) -> list[dict]:  # type: ignore[no-untyped-def]
    """Recipes ready to build right now (station filter optional)."""
    out = []
    for r in table():
        if station is not None and r.get("station") != station:
            continue
        if not reachable(village, r.get("station", "anywhere")):
            continue
        if skill_level(village.player, r.get("skill", "")) < int(r.get("level", 1)):
            continue
        if not _inputs_ok(village.player, r):
            continue
        out.append(r)
    return out


def locked(village) -> list[str]:  # type: ignore[no-untyped-def]
    """'(Skill N) name' for reachable recipes missing skill or inputs.

    Skill-ready projects (just add materials) sort first — they are the
    nearest grind targets.
    """
    ready, gated = [], []
    for r in table():
        if not reachable(village, r.get("station", "anywhere")):
            continue
        if skill_level(village.player, r.get("skill", "")) >= int(r.get("level", 1)) \
                and _inputs_ok(village.player, r):
            continue
        line = f"Locked: {r.get('name')} ({r.get('skill')} {r.get('level')})"
        if skill_level(village.player, r.get("skill", "")) >= int(r.get("level", 1)):
            ready.append(line)
        else:
            gated.append(line)
    return ready + gated


def forge_line(player, recipe: dict) -> str:  # type: ignore[no-untyped-def]
    """Menu text for a tool recipe: forge new or reforge step."""
    from omnisim.systems.tools import tool_q
    cur = tool_q(player, recipe["tool"])
    if cur:
        return f"W Reforge {item_name(recipe['tool'])} q{cur}→q{cur + 1} (3 wood)"
    return f"W Forge {item_name(recipe['tool'])} (3 wood)"


def do_craft(village, recipe: dict) -> str:  # type: ignore[no-untyped-def]
    """Consume inputs, stamp outputs, grant skill XP, advance the clock."""
    from omnisim.core.clock import day_of
    from omnisim.core.actions import _reward_cost
    player = village.player
    pack = pack_of(player)
    for item_id, qty in (recipe.get("inputs", {}) or {}).items():
        take_item(pack, item_id, qty)
    outs = []
    for item_id, qty in (recipe.get("outputs", {}) or {}).items():
        add_item(pack, item_id, qty)
        q = 1
        if recipe.get("tool"):
            q = _tool_target(player, recipe["tool"])
            player.comps.setdefault("tools", {})[recipe["tool"]] = q
        elif recipe.get("id") == "beam":
            q = min([lore_q(player, i) for i in (recipe.get("inputs", {}) or {})] or [1])
        elif recipe.get("id") == "poultice":
            q = min(5, 1 + skill_level(player, "Healing") // 3)
        stamp_lore(player, item_id, q, "You", _origin(recipe.get("station", "")),
                   day_of(village.clock_pts))
        outs.append(f"{qty} {item_name(item_id)}")
    msg = f"You craft {', '.join(outs)}."
    return _reward_cost(village, recipe.get("skill", ""), msg, int(recipe.get("cost", 300)))


def _origin(station: str) -> str:
    return {"anvil": "the smithy", "workbench": "a workbench",
            "hearth": "a warm hearth"}.get(station, "your hands")
