"""Tools (GRIND M2): five passive implements, quality 1-5.

Best-in-pack applies silently — no equip UI. Player ownership = pack item
(quality from comps["tools"], default q1); NPC ownership = comps["tools"]
(they own their kit outright). M4 recipes raise quality via Smithing caps.
"""
from __future__ import annotations

def _skill_tool() -> dict:
    """Implement behind each skill, from item tool_for flags."""
    try:
        from omnisim.entities.items import _items, tools_for
        skills: dict[str, str] = {}
        seen: set[str] = set()
        for item_id, node in _items().items():
            for skill in node.get("tool_for") or []:
                if skill not in skills:
                    skills[skill] = item_id
                seen.add(skill)
        if skills:
            return skills
    except Exception:
        pass
    return {"Farming": "hoe", "Woodcutting": "axe", "Fishing": "rod",
            "Smithing": "hammer", "Cooking": "knife", "Herbalism": "knife"}


def _forge_order() -> tuple:
    """Forge queue = forge_* recipes in file order (new tools join the line)."""
    try:
        import yaml as _yaml
        from pathlib import Path as _P
        p = _P(__file__).resolve().parents[3] / "data" / "recipes.yaml"
        doc = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        order = []
        for r in doc.get("recipes", []) or []:
            if not isinstance(r, dict):
                continue
            rid = str(r.get("id", ""))
            outs = r.get("outputs", {}) or {}
            if rid.startswith("forge_") and outs:
                order.append(next(iter(outs)))
        if order:
            return tuple(order)
    except Exception:
        pass
    return ("hoe", "axe", "rod", "knife", "hammer")


SKILL_TOOL = _skill_tool()
FORGE_ORDER = _forge_order()
_REFRESHED_DROPS: set = set()


def refresh_derived() -> None:
    """Rebuild import-time tables after drops land (drop tool_for rows merge
    at boot; the import snapshot predates them). Idempotent per drop set."""
    global SKILL_TOOL, FORGE_ORDER
    try:
        from omnisim.systems.production import _load as _ld
        _names = ((_ld("economy") or {}).get("content_drops", {}) or {}).get("load", []) or []
    except Exception:
        _names = []
    _key = tuple(sorted(str(_n) for _n in _names))
    if _key in _REFRESHED_DROPS:
        return
    SKILL_TOOL = _skill_tool()
    FORGE_ORDER = _forge_order()
    _REFRESHED_DROPS.add(_key)
FORGE_COST_WOOD = 3


def tool_q(entity, tool: str) -> int:  # type: ignore[no-untyped-def]
    """Owned quality, else 0."""
    owned = tool in ((entity.get("tools", {}) or {}))
    if entity.get("role") == "player":
        owned = (entity.get("pack", {}) or {}).get(tool, 0) > 0
    if not owned:
        return 0
    return int((entity.get("tools", {}) or {}).get(tool, 1))


def best_tool(skill: str, entity) -> int:  # type: ignore[no-untyped-def]
    """Quality of the implement behind a skill, 0 for bare hands."""
    return tool_q(entity, SKILL_TOOL.get(skill, ""))


def next_forge(pack: dict) -> str | None:
    """First missing tool in forge order, for the anvil menu."""
    for tool in FORGE_ORDER:
        if pack.get(tool, 0) == 0:
            return tool
    return None


def scarce_tool(village) -> str | None:  # type: ignore[no-untyped-def]
    """TOOLS: the implement fewest hands hold — kits, pockets, and shelves
    all count. The forge makes what the village lacks, not what's next."""
    counts = {}
    for tool in FORGE_ORDER:
        n = sum(1 for m in village.npcs
                if not m.comps.get("dead") and tool in (m.get("tools", {}) or {}))
        n += sum(m.get("pocket", {}).get(tool, 0) for m in village.npcs)
        n += sum(s.get("stock", {}).get(tool, [0, 0])[0] for s in village.stalls)
        counts[tool] = n
    return min(FORGE_ORDER, key=lambda t: counts[t])


def kit_line(entity) -> str:  # type: ignore[no-untyped-def]
    """'Kit: hammer q3' for profiles; '' when tool-less."""
    from omnisim.entities.items import item_name
    tools = entity.get("tools", {}) or {}
    if entity.get("role") == "player":
        pack = entity.get("pack", {}) or {}
        tools = {t: tools.get(t, 1) for t in FORGE_ORDER if pack.get(t, 0) > 0}
    if not tools:
        return ""
    return "Kit: " + ", ".join(f"{item_name(t)} q{q}" for t, q in tools.items())


WEAR_AT = 40  # honest uses before iron dulls a notch
MEND_PRICE = 2  # coin for Sella's time at the whetstone (base; indexed below)
MEND_Q_MAX = 3  # crude, honest, sharp enough — never masterwork


def mend_price(village=None) -> int:  # type: ignore[no-untyped-def]
    """Service wages track food: a whetstone hour buys a like share of
    dinner whether loaves cost 3 or 6 (fixed fees starve the smith when
    goods inflate — goods float, services must float too). Gentle steps,
    never famine gouge."""
    try:
        tape = (getattr(village, "price_mem", None) or {}).get("bread", 3)
        return max(2, 2 + (max(3, int(tape)) - 3) // 3)
    except Exception:
        return MEND_PRICE


def wear_tick(village, npc, skill: str) -> None:  # type: ignore[no-untyped-def]
    """TOOLS: work dulls iron. Every WEAR_AT uses with the skill's tool in
    hand, quality drops one; at 0 the tool gives out (bare hands buy new)."""
    from omnisim.entities.items import item_name
    tool = SKILL_TOOL.get(skill or "")
    if not tool:
        return
    kit = npc.get("tools", {}) or {}
    if kit.get(tool, 0) <= 0:
        return
    uses = npc.comps.setdefault("wear", {}).get(tool, 0) + 1
    if uses < WEAR_AT:
        npc.comps["wear"][tool] = uses
        return
    npc.comps["wear"][tool] = 0
    kit[tool] -= 1
    if kit[tool] <= 0:
        del kit[tool]
        village.say(f"{npc.name}'s {item_name(tool)} gives out at last.")
    else:
        village.say(f"{npc.name}'s {item_name(tool)} dulls (q{kit[tool]}).")


def dullest_tool(village, smith):  # type: ignore[no-untyped-def]
    """TOOLS: the worst-kept iron in other hands ((owner, tool, q) or None)."""
    best = None
    try:
        _fee = mend_price(village)
    except Exception:
        _fee = MEND_PRICE
    for m in village.npcs:
        if m is smith or m.comps.get("dead"):
            continue
        for tool, q in (m.get("tools", {}) or {}).items():
            if q < MEND_Q_MAX and m.get("pocket", {}).get("coin", 0) >= _fee:
                if best is None or q < best[2]:
                    best = (m, tool, q)
    return best
