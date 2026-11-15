"""Hunger-first spend (flag hunger_first_spend, default off).

When holder hunger >= THRESHOLD, non-food buys that are not own food-chain
inputs are blocked. Food = goods.yaml tags include food. Own food-chain
input = input to any recipe of the holder's trade whose outputs include
food. Generic: no named goods.
"""
from __future__ import annotations

THRESHOLD = 40


def on() -> bool:
    try:
        from omnisim.systems.production import _load as _ld
        return bool(((_ld("economy") or {}).get("flags", {}) or {}).get(
            "hunger_first_spend", False))
    except Exception:
        return False


def _is_food(good: str) -> bool:
    try:
        from omnisim.systems.production import goods
        return "food" in ((goods().get(good, {}) or {}).get("tags", []) or [])
    except Exception:
        return False



def _eventual_food(good, depth=0):
    # True when good is food or an input away from food (transitive).
    try:
        from omnisim.systems.production import recipes, goods
        try:
            tags = ((goods().get(good, {}) or {}).get("tags", []) or [])
        except Exception:
            tags = []
        if "food" in tags:
            return True
        if depth > 3:
            return False
        for r in recipes().values():
            ins = r.get("inputs", []) or []
            hit = any(i.get("good") == good for i in ins)
            if not hit:
                try:
                    from omnisim.systems.production import goods_with_tag
                    hit = any("tag" in i and good in goods_with_tag(i.get("tag"))
                              for i in ins)
                except Exception:
                    hit = False
            if not hit:
                continue
            for o in (r.get("outputs", []) or []):
                if _eventual_food(o.get("good"), depth + 1):
                    return True
        return False
    except Exception:
        return False


def _is_own_food_input(npc, good: str) -> bool:
    try:
        from omnisim.systems.production import recipes, trade_of, goods
        trade = trade_of(npc)
        if trade is None:
            return False
        for rid in trade.get("recipes", []) or []:
            r = recipes().get(rid)
            if r is None:
                continue
            outs = r.get("outputs", []) or []
            makes_food = any(
                _eventual_food(o.get("good")) for o in outs)
            if not makes_food:
                continue
            for inp in r.get("inputs", []) or []:
                if inp.get("good") == good:
                    return True
                if "tag" in inp:
                    try:
                        from omnisim.systems.production import goods_with_tag
                        if good in goods_with_tag(inp.get("tag")):
                            return True
                    except Exception:
                        pass
        return False
    except Exception:
        return False


def blocks_nonfood(npc, good: str) -> bool:
    """True = this buy is blocked by hunger priority. False otherwise."""
    if not on():
        return False
    try:
        if float(npc.get("hunger", 0)) < THRESHOLD:
            return False
    except Exception:
        return False
    if _is_food(good):
        return False
    if _is_own_food_input(npc, good):
        return False
    return True
