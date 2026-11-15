"""Forage: the wild table.

When pockets are empty and shelves are bare (or coin is short), hungry
hands walk out to the grass and pick what grows — berries today, whatever
data/labor.yaml `wild_sources` lists tomorrow. No item names in code:
each source names its good, its tiles, its skill, and its rate. NPCs see
what the land offers and act accordingly — famine makes foragers, and a
fisherman with a full net the richest soul in town.
"""
from __future__ import annotations

_SOURCES: list | None = None


def sources() -> list:
    """Wild-source rows from data. A new row needs no code."""
    global _SOURCES
    if _SOURCES is None:
        try:
            import yaml as _yaml
            from pathlib import Path as _P
            p = _P(__file__).resolve().parents[3] / "data" / "labor.yaml"
            doc = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            rows = doc.get("wild_sources", []) or []
            _SOURCES = [dict(r) for r in rows if isinstance(r, dict)]
        except Exception:
            _SOURCES = []
        if not _SOURCES:
            _SOURCES = [{"good": "berries", "tiles": ["grass"],
                         "skill": "Herbalism", "per_tick": 1}]
    return _SOURCES


def _tile_ids(names) -> set:
    """Tile-name strings (data) -> tile ids (code). Unknown names are inert."""
    try:
        from omnisim.world import tile as T
        out = set()
        for n in names or []:
            tid = getattr(T, str(n).upper(), None)
            if isinstance(tid, int):
                out.add(tid)
        return out
    except Exception:
        return set()


def _foraged(village) -> set:
    """Picked-clean tiles, regrown at dawn (mirrors felled stumps)."""
    s = getattr(village, "foraged", None)
    if not isinstance(s, set):
        s = village.foraged = set()
    return s


def _destination_on() -> bool:
    """STOCK-DESTINATION (flag stock_destination, default off)."""
    try:
        from omnisim.systems.production import _load as _ld
        return bool(((_ld("economy") or {}).get("flags", {}) or {}).get(
            "stock_destination", False))
    except Exception:
        return False


def _spot_stock(village, npc, radius: int, want: set):  # type: ignore[no-untyped-def]
    """Fullest-first destination: score every in-reach unpicked wild tile
    as stock_factor(tile) x relief - distance_cost, pick the highest.
    Same stock factor and distance cost the forage option uses; tiles
    with no stock above zero are not candidates. Ties hold ring order
    (strict improvement only), matching HEAD determinism."""
    try:
        from omnisim.systems.schedule import passable as _pass
    except Exception:
        return None
    try:
        from omnisim.systems.valuation import miles as _miles
        from omnisim.entities.items import defs as _defs
        from omnisim.systems.skills import skill_level as _sl
        from omnisim.systems.skills import ALL_SKILLS as _SK
        from omnisim.systems import forageregrow as _g
        from omnisim.systems.production import _load as _ld
    except Exception:
        return None
    try:
        _frac = float(((_ld("economy") or {}).get("stock_aware", {}) or {})
                        .get("fraction", 0.5))
    except Exception:
        _frac = 0.5
    try:
        _items = (_defs()["items"] or {})
    except Exception:
        _items = {}
    try:
        _stocks = _g._stocks(village)
    except Exception:
        _stocks = {}
    gm = village.game_map
    picked = _foraged(village)
    try:
        _by_tile = {}
        for _src in sources():
            for _tid in _tile_ids(_src.get("tiles")):
                _by_tile.setdefault(_tid, _src)
    except Exception:
        _by_tile = {}
    best, best_v = None, None
    for r in range(0, radius + 1):
        _ring = []
        for dx in range(-r, r + 1):
            _ring.extend([(dx, -r), (dx, r)] if r else [(0, 0)])
        for dy in range(-r + 1, r):
            _ring.extend([(-r, dy), (r, dy)])
        for dx, dy in _ring:
            x, y = npc.x + dx, npc.y + dy
            if (x, y) in picked:
                continue
            if not gm.in_bounds(x, y):
                continue
            try:
                _tid = int(gm.tiles[y, x])
            except Exception:
                continue
            if _tid not in want or not _pass(gm, x, y):
                continue
            _src = _by_tile.get(_tid) or {}
            try:
                _good = str(_src.get("good", ""))
                _K, _r = _g.params_of(_src)
                _S = float(_stocks.get(((x, y), _good), _K))
            except Exception:
                continue
            if _S <= 0:
                continue
            try:
                _per = max(1, int(_src.get("per_tick", 1)))
                _sk = str(_src.get("skill", ""))
                _lvl = _sl(npc, _sk) if _sk in _SK else 1
                _relief = float(_items.get(_good, {}).get("food", 0)) \
                    * (_per + _lvl // 3)
            except Exception:
                _relief = 15.0
            try:
                _f = min(1.0, max(0.0, _S) / max(1e-9, _frac * _K))
            except Exception:
                _f = 1.0
            try:
                _v = max(0.0, _relief * _f) - _miles(max(abs(dx), abs(dy)))
            except Exception:
                continue
            if best_v is None or _v > best_v:
                best, best_v = (x, y), _v
    return best


def spot_near(village, npc, radius: int = 25):  # type: ignore[no-untyped-def]
    """Nearest unpicked wild tile: rings out from the hungry feet."""
    try:
        from omnisim.systems.schedule import passable
    except Exception:
        return None
    gm = village.game_map
    picked = _foraged(village)
    want: set = set()
    for src in sources():
        want |= _tile_ids(src.get("tiles"))
    if not want:
        return None
    if _destination_on():
        return _spot_stock(village, npc, radius, want)
    for r in range(0, radius + 1):
        for dx in range(-r, r + 1):
            for dy in (-r, r):
                x, y = npc.x + dx, npc.y + dy
                if (x, y) in picked:
                    continue
                if gm.in_bounds(x, y) and int(gm.tiles[y, x]) in want \
                        and passable(gm, x, y):
                    return (x, y)
        for dy in range(-r + 1, r):
            for dx in (-r, r):
                x, y = npc.x + dx, npc.y + dy
                if (x, y) in picked:
                    continue
                if gm.in_bounds(x, y) and int(gm.tiles[y, x]) in want \
                        and passable(gm, x, y):
                    return (x, y)
    return None


def do_forage(village, npc) -> bool:  # type: ignore[no-untyped-def]
    """Pick the ground underfoot. Banks the source good, trains the skill,
    picks the tile clean till dawn. Returns True when something grew."""
    from omnisim.systems.schedule import seen_say
    tid = None
    try:
        tid = int(village.game_map.tiles[npc.y, npc.x])
    except Exception:
        return False
    src = next((s for s in sources() if tid in _tile_ids(s.get("tiles"))), None)
    if src is None:
        return False
    good = str(src.get("good", ""))
    skill = str(src.get("skill", ""))
    if not good:
        return False
    from omnisim.systems.skills import ALL_SKILLS, gain_xp, skill_level
    from omnisim.entities.items import item_name, stamp_lore, stow, cap_of
    from omnisim.core.clock import day_of
    from omnisim.world.places import describe_location
    lvl = skill_level(npc, skill) if skill in ALL_SKILLS else 1
    qty = max(1, int(src.get("per_tick", 1)) + lvl // 3)
    try:  # FORAGE-REGROWTH (flag forage_regrowth, default off): patch
        # stock gates yield; empty patches give nothing. Flag off: HEAD
        # behavior (full yield on any unpicked tile).
        from omnisim.systems.forageregrow import on as _fr_on
        from omnisim.systems.forageregrow import params_of as _fr_p
        from omnisim.systems.forageregrow import _stocks as _fr_s
        if _fr_on():
            _K, _r = _fr_p(src)
            _key = ((npc.x, npc.y), good)
            _st = _fr_s(village)
            if _key not in _st:
                _st[_key] = float(_K)
            _have = max(0.0, float(_st[_key]))
            if _have <= 0:
                npc.comps["activity"] = "picked-out"
                return False
            qty = min(qty, max(1, int(_have)))
    except Exception:
        pass
    pocket = npc.get("pocket", {})
    left = stow(pocket, good, qty, cap_of(npc))
    got = qty - left
    if got <= 0:
        return False
    _foraged(village).add((npc.x, npc.y))
    try:  # FORAGE-REGROWTH: harvest draws the patch stock down.
        from omnisim.systems.forageregrow import on as _fr_on2
        from omnisim.systems.forageregrow import _stocks as _fr_s2
        if _fr_on2():
            _key2 = ((npc.x, npc.y), good)
            _st2 = _fr_s2(village)
            _K2 = float((src or {}).get("patch_K", 8))
            _st2[_key2] = max(0.0, min(float(_K2),
                                       float(_st2.get(_key2, float(_K2)))
                                       - float(got)))
    except Exception:
        pass
    try:
        from omnisim.systems.tools import best_tool
        stamp_lore(npc, good, max(1, min(5, 1 + lvl // 3 + best_tool(skill, npc) // 2)),
                   npc.name, describe_location(village, npc.x, npc.y),
                   day_of(village.clock_pts))
    except Exception:
        pass
    if skill in ALL_SKILLS:
        try:
            gain_xp(npc, skill, 1)
        except Exception:
            pass
    seen_say(village, npc.x, npc.y,
             f"{npc.name} forages {item_name(good)} from the wilds.")
    return True
