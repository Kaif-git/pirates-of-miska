"""NPC daily life: schedule blocks, BFS feet, honest appetites.

Each NPC owns a block list [{start, end (day-minutes), act, at}] parsed at
worldgen. Every clock advance feeds them time budget; they walk (4-dir BFS,
doors auto-opened) toward the block anchor, then sleep/eat/work/buy/idle.
Needs are abstract counters; pockets hold bread + coin. Deterministic wander
via village.rng — the same day replays the same day.
"""
from __future__ import annotations
from collections import deque

from omnisim.core.clock import day_of
from omnisim.entities.items import defs as item_defs
from omnisim.systems.skills import gain_xp
from omnisim.world import tile as T

NAMED = {"market": (40, 20), "tavern": (18, 11)}  # market square proper —
# the stall itself stands at (40, 21); feet gather beside it
TILE_COST = 120
WORK_XP_MIN = 30  # 1 xp per 30 min of honest work
STARVE_HP_MIN = 120  # M6: a starving NPC loses 1 HP per 2 hours. Nothing scripted.

# M5: every 30-min shift tick banks real goods into the pocket.
# (skill -> (item, qty); coin needs no lore, everything else is stamped.)
# All three tables live in data/labor.yaml — code holds only formulas.
# A new trade (orchardist banking apples) is a data row, not a patch.
def _labor_doc() -> dict:
    try:
        import yaml as _yaml
        from pathlib import Path as _P
        p = _P(__file__).resolve().parents[3] / "data" / "labor.yaml"
        if p.is_file():
            return _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        pass
    return {}


def _work_goods() -> dict:
    try:
        rates = _labor_doc().get("rates_per_tick", {}) or {}
        out = {}
        for skill, goods in rates.items():
            if isinstance(goods, dict) and goods:
                item = next(iter(goods))
                out[skill] = (item, int(goods[item]))
        if out:
            return out
    except Exception:
        pass
    return {"Farming": ("wheat", 2), "Fishing": ("fish", 1),
            "Woodcutting": ("firewood", 2), "Cooking": ("bread", 1),
            "Herbalism": ("herbs", 2), "Pottery": ("clay", 2)}
    # No Smithing, no Trading: the forge banks iron episodically (below),
    # the merchant keeps margin — neither draws a wage from any till.


def _work_needs() -> dict:
    """Finished goods cost raw: no flour, no loaf; no fuel, no sparks."""
    try:
        needs = _labor_doc().get("needs_per_unit", {}) or {}
        out = {}
        for skill, goods in needs.items():
            if isinstance(goods, dict) and goods:
                item = next(iter(goods))
                out[skill] = (item, int(goods[item]))
        if out:
            return out
    except Exception:
        pass
    return {"Cooking": ("flour", 1), "Smithing": ("firewood", 1)}


def _raw_of() -> dict:
    try:
        raw = _labor_doc().get("raw_of", {}) or {}
        if isinstance(raw, dict) and raw:
            return dict(raw)
    except Exception:
        pass
    return {"flour": "wheat"}


WORK_GOODS = _work_goods()
# Smithing pays: three sparks for a billet of fuel and the skill to use it.
# The forge must clear its fuel and its keeper's dinner with margin to spare,
# or the iron goes cold and the smith with it (the audit buried her twice).
# CHAIN+: finished goods cost raw — the oven eats wheat, the forge eats wood.
# No flour, no loaf; no fuel, no sparks. Shortages cascade on their own.
# The oven needs FLOUR (wheat grinds first — raw grain is not food and
# never was); the forge needs firewood. Grind stock below in _bank_production.
WORK_NEEDS = _work_needs()
# Raw inputs behind finished needs (aid and shopping fall back to these).
RAW_OF = _raw_of()
ERRAND_AT = 4  # pocket sellables that trigger a haul-to-market errand
# Errands redirect honest hours — and dinner, which the hungry haul through
# (the meal was eaten on entry; the road to market still pays).
ERRAND_ACTS = ("work", "idle", "eat", "buy")  # the buy block unloads too:
# hands that reach the square with backs full sell before they shop (Wren
# stood thirsty-through-buy beside the stall with 3 days of herbs unsold).

NOTEWORTHY = ("sleeping", "eating", "working", "buying", "hungry")

FATIGUE_WORK = 0.4  # M9: 4 honest hours tire a worker out
FATIGUE_REST = 1.0  # sitting, idling, and meals undo it twice as fast
REST_AT = 100.0
RESUME_AT = 30.0


def seen_say(village, x: int, y: int, msg: str) -> None:  # type: ignore[no-untyped-def]
    """M9: the log reports what you could actually see. Nothing from thin air."""
    gm = village.game_map
    if gm.in_bounds(x, y) and gm.visible[y, x]:
        village.say(msg)


def parse_hhmm(s: str) -> int:
    h, m = s.split(":")
    return (int(h) * 60 + int(m)) % 1440


def resolve_anchor(name: str, spawn: dict):
    if name == "home":
        return tuple(spawn["home"])
    if name == "work":
        return tuple(spawn["work"])
    if name == "patrol":
        return [tuple(p) for p in spawn["patrol"]]
    if name in NAMED:
        return NAMED[name]
    raise ValueError(f"unknown anchor: {name}")


def parse_schedule(spawn: dict) -> list[dict]:
    out = []
    for b in spawn.get("schedule", []):
        out.append({"start": parse_hhmm(b["start"]), "end": parse_hhmm(b["end"]),
                    "act": b["act"], "at": resolve_anchor(b["at"], spawn)})
    return out


def current_block(sched: list[dict], tod_min: int) -> int:
    for i, b in enumerate(sched):
        s, e = b["start"], b["end"]
        if (s <= e and s <= tod_min < e) or (s > e and (tod_min >= s or tod_min < e)):
            return i
    return 0


def passable(game_map, x: int, y: int) -> bool:
    if not game_map.in_bounds(x, y):
        return False
    tid = int(game_map.tiles[y, x])
    return tid == T.DOOR or T.BY_ID[tid].walkable


_BFS_CACHE: dict = {}
_BFS_GEN: int = 0


def invalidate_bfs_cache() -> None:
    """Bump the path-cache generation (tile writes change the grid)."""
    global _BFS_GEN
    _BFS_GEN += 1
    if len(_BFS_CACHE) > 20000:
        _BFS_CACHE.clear()


def bfs_path(game_map, start: tuple[int, int], goal: tuple[int, int]) -> list[tuple[int, int]]:
    if start == goal:
        return []
    try:
        _key = (_BFS_GEN, start, goal)
        _hit = _BFS_CACHE.get(_key)
        if _hit is not None:
            return list(_hit)
    except Exception:
        _key = None
    prev: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nxt = (cur[0] + dx, cur[1] + dy)
            if nxt in prev or not passable(game_map, *nxt):
                continue
            prev[nxt] = cur
            if nxt == goal:
                path = [nxt]
                while prev[path[-1]] is not None:
                    path.append(prev[path[-1]])  # type: ignore[arg-type]
                _ret = path[::-1][1:]
                try:
                    if _key is not None:
                        _BFS_CACHE[_key] = list(_ret)
                except Exception:
                    pass
                return _ret
            queue.append(nxt)
    try:
        if _key is not None:
            _BFS_CACHE[_key] = []
    except Exception:
        pass
    return []


def standable_near(game_map, x: int, y: int) -> tuple[int, int]:
    if passable(game_map, x, y):
        return (x, y)
    best, best_d = (x, y), 10**9
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            nx, ny = x + dx, y + dy
            if passable(game_map, nx, ny) and abs(dx) + abs(dy) < best_d:
                best, best_d = (nx, ny), abs(dx) + abs(dy)
    return best


def food_stalls(village):  # type: ignore[no-untyped-def]
    """Stalls with any staple on the shelf (freshest first pick for hungry errands)."""
    try:
        from omnisim.entities.items import staples as _st
        _foods = tuple(_st())
    except Exception:
        _foods = ("bread",)
    return [s for s in village.stalls
            if any(s["stock"].get(f, [0, 0])[0] > 0 for f in _foods)]


def bread_stalls(village):  # type: ignore[no-untyped-def]
    """Compat alias: buy blocks route to dinner, whatever it is today."""
    return food_stalls(village)


def _staple_shelf(village):
    """Staple ids for till-aware feet (cheap data read, safe default)."""
    try:
        from omnisim.entities.items import staples as _st
        return tuple(_st())
    except Exception:
        return ("bread", "fish")


def _dest_for(village, npc, blk: dict):  # type: ignore[no-untyped-def]
    at = blk["at"]
    if blk["act"] == "buy":
        cands = food_stalls(village) or village.stalls
        _shelf = _staple_shelf(village)
        best, best_d = None, 10**9
        for s in cands:
            # Till-aware feet: a bare till sells nothing — hungry errands
            # route to a stall that can actually fill the basket, not the
            # nearest empty shelf. Food on wood beats coin in the drawer.
            try:
                _stk = s.get("stock", {}) or {}
                _staples = sum((_stk.get(f, [0, 0]))[0] for f in _shelf)
                _wild = sum((_stk.get(f, [0, 0]))[0] for f in
                            ("berries", "herbs", "apple"))
            except Exception:
                _staples, _wild = 1, 0
            if _staples <= 0 and _wild <= 0:
                continue
            d = max(abs(s["x"] - npc.x), abs(s["y"] - npc.y))
            if d < best_d:
                best, best_d = s, d
        if best is None:  # every shelf stands bare: nearest stall anyway
            for s in village.stalls:
                d = max(abs(s["x"] - npc.x), abs(s["y"] - npc.y))
                if d < best_d:
                    best, best_d = s, d
        if best is None:
            return (npc.x, npc.y)
        return standable_near(village.game_map, best["x"], best["y"])
    if isinstance(at, list) and blk["act"] == "patrol":  # patrol waypoints: ping-pong
        wp = npc.comps.get("wp", 0) % len(at)
        return at[wp]
    if blk["act"] == "idle":
        return None  # wander target chosen on arrival
    return at


def _enter_block(village, npc, bi: int) -> None:  # type: ignore[no-untyped-def]
    npc.comps["block_idx"] = bi
    npc.comps["path"] = []
    npc.comps["dest"] = None
    npc.comps["dest_kind"] = None
    npc.comps["bought"] = False
    blk = npc.comps["sched"][bi]
    resting = npc.comps.get("resting", False)
    if blk["act"] == "eat":
        npc.comps["resting"] = False
        npc.comps["fatigue"] = max(0.0, float(npc.get("fatigue", 0)) - 30.0)
        pocket = npc.get("pocket", {})
        from omnisim.entities.items import best_food, item_name, open_of as _oo
        fid = best_food(pocket, _oo(npc))  # ECON: fish and apples count — not bread alone
        if fid is not None:
            from omnisim.entities.items import food_value as _npc_food
            from omnisim.entities.items import open_of as _open_of
            from omnisim.entities.items import take_serving as _take_serving
            # Eat like a living thing: dinner ends at full, not at three.
            from omnisim.systems.valuation import bite_option as _bite
            _open = _open_of(npc)
            units = 0
            first = fid
            while units < 8:  # dinner ends when the belly is full, not when
                fid, _val = _bite(npc)  # a nibble looks weak — weak foods
                if fid is None:  # feed full dinners too (berries by the
                    break  # handful, not one ceremonial bite)
                if units > 0 and float(npc.get("hunger", 0)) <= 0:
                    break  # full — the rest keeps for tomorrow
                from omnisim.entities.items import lore_q as _lq
                ate = _npc_food(npc, fid, _lq(npc, fid))
                if not _take_serving(pocket, _open, fid):
                    break
                npc.comps["hunger"] = max(0.0, float(npc.get("hunger", 0)) - ate)
                units += 1
                try:
                    from omnisim.systems.needs import record_meal as _rec
                    _rec(npc, fid)
                except Exception:
                    pass
                fid = best_food(pocket, _open)
            gain_xp(npc, "Healing", 1)
            npc.comps["activity"] = "eating"
            seen_say(village, npc.x, npc.y, f"{npc.name} sits down to eat ({item_name(first)}).")
        else:
            npc.comps["activity"] = "hungry"
            seen_say(village, npc.x, npc.y, f"{npc.name} has nothing to eat.")
        if int(npc.get("hp", 10)) < int(npc.get("max_hp", 10)) \
                and pocket.get("remedy", 0) > 0:  # HERBS: supper, then the cure
            cured = int(item_defs()["items"].get("remedy", {}).get("heal", 0))
            pocket["remedy"] -= 1
            if pocket["remedy"] <= 0:
                del pocket["remedy"]
            npc.comps["hp"] = min(int(npc.get("max_hp", 10)),
                                  int(npc.get("hp", 10)) + cured)
            gain_xp(npc, "Healing", 1)
            seen_say(village, npc.x, npc.y,
                     f"{npc.name} quaffs a remedy ({item_name('remedy')}).")
    elif blk["act"] == "sleep":
        npc.comps["resting"] = False
        try:  # STEP 3: cold shortens sleep (flag-gated; warm beds restore fully)
            from omnisim.systems.needs3 import level_of as _n3_level
            from omnisim.systems.needs3 import on as _n3_on
            if _n3_on():
                _cold = _n3_level(npc, "cold")
                npc.comps["fatigue"] = float(npc.get("fatigue", 0)) * 0.5 * _cold / 100.0
            else:
                npc.comps["fatigue"] = 0.0
        except Exception:
            npc.comps["fatigue"] = 0.0
        npc.comps["activity"] = "sleeping"
        seen_say(village, npc.x, npc.y, f"{npc.name} turns in for the night.")
    elif blk["act"] == "work":
        if not resting:
            seen_say(village, npc.x, npc.y, f"{npc.name} sets to work.")
        try:  # LOCAL VIEW (flag local_view, default off): work-block
            # start observes sellers with stock inside R of the workplace.
            from omnisim.systems.localview import on as _lv_on
            from omnisim.systems.localview import observe as _lv_obs
            if _lv_on():
                _lv_obs(village, npc)
        except Exception:
            pass
    elif blk["act"] == "buy":
        npc.comps["resting"] = False
        seen_say(village, npc.x, npc.y, f"{npc.name} goes to market.")


def _step(village, npc) -> None:  # type: ignore[no-untyped-def]
    gm = village.game_map
    path = npc.comps["path"]
    if not path:
        return
    nx, ny = path[0]
    if int(gm.tiles[ny, nx]) == T.DOOR and not gm.doors[ny, nx]:
        gm.doors[ny, nx] = True
        try:
            invalidate_bfs_cache()
        except Exception:
            pass
        if gm.visible[ny, nx]:
            village.say(f"{npc.name} opens the door.")
    npc.x, npc.y = nx, ny
    npc.comps["path"] = path[1:]
    npc.comps["move_budget"] -= TILE_COST


def _rtv_shift_tick(village) -> None:  # type: ignore[no-untyped-def]
    """REALIZED-TV feed (flag realized_time_value, default off): count one
    on-shift tick per NPC currently in a work block."""
    try:
        from omnisim.systems.realizedtv import on as _rtv_on
        if not _rtv_on():
            return
        from omnisim.systems.realizedtv import record_shift as _rs
        tod = (village.clock_pts // 100) % 1440
        for npc in (village.npcs or []):
            try:
                if npc.comps.get("dead"):
                    continue
                bi = current_block(npc.comps.get("sched", []), tod)
                if ((npc.comps.get("sched", []) or [])[bi].get("act")
                        == "work"):
                    _rs(npc, village, 1)
            except Exception:
                pass
    except Exception:
        pass


def tick_npcs(village, pts: int) -> None:  # type: ignore[no-untyped-def]
    mins = pts / 100
    _rtv_shift_tick(village)
    tod = (village.clock_pts // 100) % 1440
    try:  # STEP 3: household hearths burn before souls feel the cold
        from omnisim.systems.needs3 import hearth_tick as _ht
        from omnisim.systems.needs3 import on as _n3_on
        if _n3_on():
            _ht(village, mins)
    except Exception:
        pass
    for npc in village.npcs:
        sched = npc.comps.get("sched")
        if not sched:
            continue
        if npc.comps.get("invincible"):
            continue  # the merchant stands outside need — pure infrastructure
        from omnisim.systems.needs import hunger_tick as _hunger_tick
        from omnisim.systems.needs import thirst_tick as _thirst_tick
        from omnisim.systems.needs import filth_tick as _filth_tick
        from omnisim.systems.needs import tick_wants as _tick_wants
        _hunger_tick(npc, mins,
                     activity=str(npc.comps.get("activity", "idle")),
                     world=village)
        try:  # STEP 3: cold gnaws alongside hunger (flag-gated, autumn-neutral)
            from omnisim.systems.needs3 import cold_tick as _cold_tick
            from omnisim.systems.needs3 import on as _n3_on
            if _n3_on():
                _cold_tick(npc, mins, village)
        except Exception:
            pass
        try:  # STEP 4: mood drifts toward raw (wants minus pains)
            from omnisim.systems.mood import on as _m4_on
            from omnisim.systems.mood import tick_mood as _tick_mood
            if _m4_on():
                _tick_mood(village, npc, mins)
        except Exception:
            pass
        _thirst_tick(npc, mins, activity=str(npc.comps.get("activity", "idle")))
        _filth_tick(npc, mins, village)  # living dirties; wells forgive
        _tick_wants(npc, mins, village)  # V1 Wants: company sustains
        if float(npc.get("thirst", 0)) >= 100 and not npc.comps.get("dead"):
            from omnisim.systems.needs import THIRST_HP_MIN as _THIRST_MIN
            npc.comps["thirst_acc"] = float(npc.get("thirst_acc", 0)) + mins
            while npc.comps["thirst_acc"] >= _THIRST_MIN \
                    and not npc.comps.get("dead"):
                npc.comps["thirst_acc"] -= _THIRST_MIN
                npc.comps["hp"] = int(npc.get("hp", 10)) - 1
                if npc.comps["hp"] <= 0:
                    _die(village, npc, how="thirst")
        if npc.comps["hunger"] >= 100:  # M6: the bare clock. No food, no mercy.
            npc.comps["starve_acc"] = float(npc.get("starve_acc", 0)) + mins
            while npc.comps["starve_acc"] >= STARVE_HP_MIN and not npc.comps.get("dead"):
                npc.comps["starve_acc"] -= STARVE_HP_MIN
                npc.comps["hp"] = int(npc.get("hp", 10)) - 1
                if npc.comps["hp"] <= 0:
                    _die(village, npc)
        if npc.comps.get("dead"):
            continue
        bi = current_block(sched, tod)
        if bi != npc.comps.get("block_idx", -1):
            _enter_block(village, npc, bi)
        blk = sched[bi]
        # P2 brain loop: urgent needs veto; work trains; hauls commit;
        # ambient opportunities fire (grazers skip loaded errands).
        from omnisim.systems import brain as _brain
        _brain.urgent(village, npc, blk)
        _relief_skip = False
        try:  # STEP 4 anti-spiral: below -60 with pain unmet, a soul with
            # dinner in pocket eats and takes the tick off instead of working
            # into the ground. Empty pockets keep working — work is the only
            # road to dinner, and idling without relief only hastens the end.
            # Aid remains the floor.
            from omnisim.systems.mood import on as _m4s_on
            from omnisim.systems.mood import mood_of as _m4m
            from omnisim.systems.needs3 import level_of as _m4L
            if _m4s_on() and _m4m(npc) < -60.0:
                _worst = max(float(_m4L(npc, _ch)) for _ch in
                             ("hunger", "thirst", "cold"))
                if _worst > 40.0 and float(npc.get("hunger", 0)) > 50.0:
                    try:
                        from omnisim.entities.items import foods as _m4F
                        _pocket = npc.get("pocket", {}) or {}
                        _foods = set(_m4F())
                        if any(_pocket.get(_f, 0) > 0 for _f in _foods):
                            from omnisim.systems import desires as _m4D
                            _m4D.eat_bite(village, npc, blk)
                            npc.comps["activity"] = "seeking relief"
                            _relief_skip = True
                    except Exception:
                        pass
        except Exception:
            pass
        if blk["act"] in ("work", "patrol") and pts > 0 and not npc.comps.get("resting") and not _relief_skip:  # INLINE: training body (queued: work-act)
            skill = npc.get("work_skill")  # the shift itself is the training
            if skill:
                from omnisim.systems.tools import best_tool
                gain = 1 + best_tool(skill, npc) // 2  # good tools teach faster
                try:  # STEP 4: mood scales output and learning; diligence
                    from omnisim.systems.mood import on as _m4w_on
                    from omnisim.systems.mood import perf_of as _m4p
                    from omnisim.systems.mood import worked_frac as _m4f
                    _perf = _m4p(npc) if _m4w_on() else 1.0
                    _frac = _m4f(npc) if _m4w_on() else 1.0
                except Exception:
                    _perf, _frac = 1.0, 1.0
                gain = max(1, int(round(gain * _perf)))
                npc.comps["work_acc"] = float(npc.get("work_acc", 0)) + pts * _frac
                _gain_base = gain  # STEP 8: halving applies per bank, never compounds
                while npc.comps["work_acc"] >= WORK_XP_MIN * 100:
                    npc.comps["work_acc"] -= WORK_XP_MIN * 100
                    _bank_production(village, npc, skill)  # M5: bank at the skill that worked
                    try:  # STEP 8: rust clock resets on use; below-tier practice teaches half
                        from omnisim.systems.mastery import on as _m8x_on
                        from omnisim.systems.mastery import touch as _m8touch
                        if _m8x_on():
                            _m8touch(village, npc, skill)
                            try:
                                from omnisim.systems.production import trade_of as _tof8
                                from omnisim.systems.production import recipes as _recs8
                                _tr8 = _tof8(npc)
                                _rids8 = (_tr8.get("recipes", []) or []) if _tr8 else []
                                _mine8 = [(_recs8().get(_r, {}) or {}) for _r in _rids8]
                                _mine8 = [int(_r.get("min_skill", 1) or 1) for _r in _mine8
                                          if str(_r.get("skill", "")) == skill]
                                if not _mine8:
                                    _mine8 = [1]
                                _req8 = max(_mine8)
                                from omnisim.systems.mastery import effective_skill as _sl8
                                _gain2 = _gain_base
                                if int(_sl8(npc, skill)) >= _req8 + 3:
                                    _gain2 = max(1, int(round(_gain_base * 0.5)))
                                gain = _gain2
                            except Exception:
                                pass
                    except Exception:
                        pass
                    gain_xp(npc, skill, gain)
        if npc.comps.get("errand") is None and village.stalls:  # INLINE: haul bid (queued)
            from omnisim.core.actions import SELLABLE
            from omnisim.systems.pricing import scarcity_of
            best = max(village.stalls, key=lambda s: s.get("till", 0))
            pocket = npc.get("pocket", {})
            crashed = any(pocket.get(i, 0) > 0 and scarcity_of(best, i) <= 0.6
                          for i in SELLABLE)
            # Desperation sells smoothly: need falls from a full cart (4)
            # toward dinner money (2) as hunger urgency and broke-ness rise.
            from omnisim.systems.valuation import urgency as _urg2
            try:
                _hung = float(npc.get("hunger", 0))
            except Exception:
                _hung = 0.0
            _broke = max(0.0, 1.0 - pocket.get("coin", 0) / 6.0)
            _need = 2 if crashed else max(
                2, round(ERRAND_AT - _urg2(_hung, "hunger") * 2.5 - _broke * 2))
            try:  # INPUT-PRIORITY (flag input_priority, default off):
                # dry bins yield the errand seat to the input buy trip.
                from omnisim.systems.inputpriority import errand_yield as _ipy
                _ip_yield = bool(_ipy(village, npc))
            except Exception:
                _ip_yield = False
            if (_market_appetite(village, npc) >= min(_need, 2) \
                    or _carries_iron(npc)) and not _ip_yield:  # M7: dump gluts early; iron walks at 1
                npc.comps["errand"] = {"x": best["x"], "y": best["y"]}
                npc.comps["dest"] = None
            npc.comps["dest_kind"] = None  # reroute below
        _brain.ambient(village, npc, blk)
        if blk["act"] == "work":  # M9: honest hours tire; tired hands rest
            tired = float(npc.get("fatigue", 0))
            if npc.comps.get("resting"):
                tired = max(0.0, tired - mins * FATIGUE_REST)
                npc.comps["fatigue"] = tired
                if tired < RESUME_AT:
                    npc.comps["resting"] = False
            else:
                tired = tired + mins * FATIGUE_WORK
                npc.comps["fatigue"] = tired
                if tired >= REST_AT:
                    npc.comps["resting"] = True
                    npc.comps["dest"] = (npc.x, npc.y)
                    npc.comps["dest_kind"] = "rest"
                    npc.comps["path"] = []
                    seen_say(village, npc.x, npc.y,
                             f"{npc.name} sits down to catch their breath.")
        elif blk["act"] in ("idle", "buy"):  # INLINE: rest body (queued: rest-act)
            npc.comps["fatigue"] = max(0.0, float(npc.get("fatigue", 0)) - mins * FATIGUE_REST)
        _brain.steer(village, npc, blk)  # feet: rest > parch > haul > forage > thirst > loot > routine
        dest = npc.comps.get("dest")
        # Legs don't bank hours: each tick grants fresh stride, unspent
        # stride evaporates (no hoard-and-teleport after standing pinned).
        npc.comps["move_budget"] = pts * float(npc.get("speed", 0.85))
        arrived = (npc.x, npc.y) == tuple(dest)
        if not arrived:
            npc.comps["activity"] = "patrolling" if blk["act"] == "patrol" else "walking"
            while npc.comps["move_budget"] >= TILE_COST and npc.comps["path"]:
                _step(village, npc)
            if (npc.x, npc.y) == tuple(dest):
                _arrive(village, npc, blk)
        else:
            _do_activity(village, npc, blk, pts)
        if arrived and npc.comps.pop("drink_trip", None) is not None:
            # INLINE: drink-act (queued) — reached water, drink deep; the
            # filthy wash while here.
            npc.comps["thirst"] = 0.0
            npc.comps["dest"] = None
            npc.comps["dest_kind"] = None
            npc.comps["path"] = []
            seen_say(village, npc.x, npc.y, f"{npc.name} drinks deeply.")
            try:
                if float(npc.get("filth", 0)) >= 60:
                    from omnisim.systems.needs import wash as _wash
                    _wash(npc)
                    seen_say(village, npc.x, npc.y,
                             f"{npc.name} scrubs clean.")
            except Exception:
                pass
        if arrived and npc.comps.pop("forage_trip", None) is not None:
            # INLINE: forage-act (queued) — reached the wilds, pick clean.
            from omnisim.systems.forage import do_forage as _forage
            _forage(village, npc)
            npc.comps["dest"] = None
            npc.comps["dest_kind"] = None
            npc.comps["path"] = []
        if arrived and npc.comps.pop("buy_trip", None) is not None:
            # INLINE: shop-act (queued) — feet reached the market, shop.
            _try_buy(village, npc)
            npc.comps["dest"] = None
            npc.comps["dest_kind"] = None
            npc.comps["path"] = []
        errand = npc.comps.get("errand")  # M5: close enough to unload?
        if errand is not None and blk["act"] in ERRAND_ACTS:
            st = next((s for s in village.stalls
                       if (s["x"], s["y"]) == (errand["x"], errand["y"])), None)
            if st is None:
                npc.comps["errand"] = None
            elif max(abs(st["x"] - npc.x), abs(st["y"] - npc.y)) <= 3:
                _do_sale(village, npc, st)
            else:
                from omnisim.core.actions import SELLABLE
                from omnisim.entities.items import item_name
                first = next((i for i in SELLABLE if npc.get("pocket", {}).get(i, 0) > 0),
                             "goods")
                npc.comps["activity"] = f"hauling {item_name(first)}"
        if npc.comps.get("resting"):  # M9: tired hands sit — no hauling claims
            npc.comps["activity"] = "resting"  # ...even with a market walk owed
        if npc.get("role") == "kid" and not npc.comps.get("dead"):
            _kid_gather(village, npc)  # ECON: small hands grab what feet find


def gm_of(village):  # type: ignore[no-untyped-def]
    return village.game_map


def well_xy(village):  # type: ignore[no-untyped-def]
    """The town well: drink/wash anchor. Field-stamped, else tile-scanned."""
    well = getattr(village, "well_xy", None)
    if well is not None:
        return well
    try:
        from omnisim.world import tile as T
        gm = village.game_map
        for y in range(gm.height):
            for x in range(gm.width):
                if gm.tile_id(x, y) == T.WELL:
                    village.well_xy = (x, y)
                    return (x, y)
    except Exception:
        pass
    return None


def _dest_for_idle(village, npc) -> tuple[int, int]:  # type: ignore[no-untyped-def]
    ax, ay = npc.comps.get("idle_anchor", (npc.x, npc.y))
    for _ in range(12):
        nx = ax + village.rng.randint(-4, 4)
        ny = ay + village.rng.randint(-4, 4)
        if passable(village.game_map, nx, ny):
            return (nx, ny)
    return (npc.x, npc.y)


def _arrive(village, npc, blk: dict) -> None:  # type: ignore[no-untyped-def]
    npc.comps["move_budget"] = 0.0
    if blk["act"] == "patrol":
        at = blk["at"]
        npc.comps["wp"] = (npc.get("wp", 0) + 1) % len(at)
        npc.comps["dest"] = None
        npc.comps["dest_kind"] = None  # head to the next waypoint
        npc.comps["activity"] = "patrolling"
    elif blk["act"] == "idle":
        npc.comps["dest"] = None
        npc.comps["dest_kind"] = None  # pick a fresh wander soon
        npc.comps["activity"] = "idle"
    else:
        _do_activity(village, npc, blk, 0)


def _do_activity(village, npc, blk: dict, pts: int) -> None:  # type: ignore[no-untyped-def]
    act = blk["act"]
    if act == "sleep":
        npc.comps["activity"] = "sleeping"
    elif act == "eat":
        if npc.comps.get("activity") != "hungry":
            npc.comps["activity"] = "eating"
            try:  # shared tables sustain ~2/hour (GDD 3.2 social locations)
                from omnisim.systems.needs import socialize as _soc6
                _soc6(npc, 2.0 * (pts / 100.0) / 60.0)
            except Exception:
                pass
    elif act == "work":
        npc.comps["activity"] = "working"
        try:  # LOCAL VIEW arrival: at the workplace, look at near shelves.
            from omnisim.systems.localview import on as _lv_on2
            from omnisim.systems.localview import observe as _lv_obs2
            from omnisim.systems.localview import workplace_of as _lv_wp2
            if _lv_on2():
                _wx, _wy = _lv_wp2(npc)
                if max(abs(_wx - npc.x), abs(_wy - npc.y)) <= 4:
                    _lv_obs2(village, npc)
        except Exception:
            pass
    elif act == "buy":
        npc.comps["activity"] = "buying"
        try:  # market crowds sustain ~2/hour, even window-shopping
            from omnisim.systems.needs import socialize as _soc7
            _soc7(npc, 2.0 * (pts / 100.0) / 60.0)
        except Exception:
            pass
        if not npc.comps.get("bought"):
            _try_buy(village, npc)
    elif act == "idle":
        npc.comps["activity"] = "idle"
        npc.comps["idle_anchor"] = (npc.x, npc.y)
        npc.comps["dest"] = None
        npc.comps["dest_kind"] = None
        # Hungry idlers shop via scored buy trips (feet route, arrival buys)
        # — no separate in-place rule; one bidding path for every belly.
    elif act == "patrol":
        if npc.comps.get("activity") == "walking":
            npc.comps["activity"] = "patrolling"


def _kid_gather(village, npc) -> None:  # type: ignore[no-untyped-def]
    """ECON: kids haul the world's spill — ground goods to small backs to stalls."""
    from omnisim.core.actions import SELLABLE
    from omnisim.entities.items import cap_of, item_name, load_of, stow
    key = (npc.x, npc.y)
    loot = village.ground.get(key, {})
    pocket = npc.get("pocket", {})
    took = 0
    for item_id in sorted(loot):
        if item_id not in SELLABLE or item_id == "coin":
            continue
        left = stow(pocket, item_id, loot[item_id], cap_of(npc))
        got = loot[item_id] - left
        if got > 0:
            took += got
            if left > 0:
                loot[item_id] = left
            else:
                del loot[item_id]
            village.ground_lore.get(key, {}).pop(item_id, None)
    if not loot:
        village.ground.pop(key, None)
    if took > 0:
        npc.comps["activity"] = "gathering"
        seen_say(village, npc.x, npc.y, f"{npc.name} gathers the spill (+{took}).")


def _kid_loot_near(village, npc, radius: int = 12, food_only: bool = False):  # type: ignore[no-untyped-def]
    """Nearest tile with hauling-worthy goods, kid legs willing. Starving
    kids look for dinner specifically — compost can wait."""
    from omnisim.core.actions import SELLABLE
    try:
        from omnisim.entities.items import is_food as _is_food
    except Exception:
        def _is_food(i): return False
    best, best_d = None, radius + 1
    for (lx, ly), loot in village.ground.items():
        if food_only:
            if not any(_is_food(i) and q > 0 for i, q in loot.items()):
                continue
        elif not any(i in SELLABLE and q > 0 for i, q in loot.items()):
            continue
        d = max(abs(lx - npc.x), abs(ly - npc.y))
        if d < best_d and passable(village.game_map, lx, ly):
            best, best_d = (lx, ly), d
    return best


def _die(village, npc, how: str = "hunger") -> None:  # type: ignore[no-untyped-def]
    """M6: need 100+ drained the last HP. Loot drops, the log remembers."""
    if npc.comps.get("invincible"):
        return  # infrastructure with a name — the Market does not die
    npc.comps["dead"] = True
    npc.comps["activity"] = "dead"
    npc.comps["death_cause"] = how
    npc.comps["dest"] = None
    npc.comps["dest_kind"] = None
    npc.comps["path"] = []
    npc.comps["errand"] = None
    npc.blocks = False
    npc.glyph = "%"
    pocket = npc.get("pocket", {})
    dropped = {i: q for i, q in pocket.items() if q > 0}
    if dropped:
        ground = village.ground.setdefault((npc.x, npc.y), {})
        for item_id, qty in dropped.items():
            ground[item_id] = ground.get(item_id, 0) + qty
        village.ground_lore.setdefault((npc.x, npc.y), {}).update(
            {i: {"by": npc.name, "origin": "the fallen"} for i in dropped})
        pocket.clear()
    if how == "thirst":
        village.say(f"{npc.name} went dry in the dark.")
    else:
        village.say(f"{npc.name} starved in the dark.")
    try:  # STEP 4: the village marks its dead (-10 to every witness)
        from omnisim.systems.mood import on as _m4d_on
        from omnisim.systems.mood import add_event as _m4d_ev
        if _m4d_on():
            for _wit in (getattr(village, "npcs", []) or []):
                if _wit is not npc and not _wit.comps.get("dead"):
                    _m4d_ev(_wit, village, -10.0, "death")
    except Exception:
        pass


def _market_glut(village, item_id: str) -> bool:  # type: ignore[no-untyped-def]
    """True when the whole market drowns in it — the shared glut line.
    Shelves that never carried it don't vote; one thin shelf must not gag
    the village while another stands empty."""
    tot_par, tot_qty = 0, 0
    for s in village.stalls:
        if item_id in s.get("restock", {}):
            tot_par += s["restock"][item_id][0]
        tot_qty += s.get("stock", {}).get(item_id, [0, 0])[0]
    if tot_par <= 0 and tot_qty <= 0:
        return False
    return tot_qty >= _glut_line(village, item_id)


def _market_scarcity(village, item_id: str) -> float:  # type: ignore[no-untyped-def]
    """Market-wide want: listed par over total stock (floor par 6 for seeded
    goods). The flex gate reads this — one thin shelf must not pin a worker
    while the market drowns."""
    tot_par, tot_qty = 0, 0
    for s in village.stalls:
        if item_id in s.get("restock", {}):
            tot_par += s["restock"][item_id][0]
        tot_qty += s.get("stock", {}).get(item_id, [0, 0])[0]
    return max(tot_par, 6) / max(1, tot_qty)


def scarcity_of_good(village, item_id: str) -> float:  # type: ignore[no-untyped-def]
    """CHAIN: worst shelf carrying it. Goods no stall lists read as par (no market)."""
    from omnisim.systems.pricing import scarcity_of
    vals = [scarcity_of(s, item_id) for s in village.stalls if item_id in s["stock"]]
    return max(vals) if vals else 1.0


def _flex_goods(posts: dict) -> tuple:
    """Every trade with a post can be asked for — new posts join the roll."""
    return tuple(posts)


STATIC_ROLES = ("kid", "shopkeeper")  # smallfolk keep their days; the
# merchant keeps the Market — no guard exists to keep, no wages to pay


def _shelf_sum(village, good: str) -> int:
    """Units of a good on all shelves — zero is a hole, not noise."""
    try:
        return sum(int((s.get("stock", {}) or {}).get(good, [0, 0])[0])
                   for s in village.stalls)
    except Exception:
        return 0


def flex_at_dawn(village) -> None:  # type: ignore[no-untyped-def]
    try:  # Part B dawn recovery: +2% toward 1.0 unless refused today.
        from omnisim.systems.decision import recover_village as _dB_rec
        _dB_rec(village)
    except Exception:
        pass

    try:  # closing shelves: intake tomorrow reads these, not the rush count.
        from omnisim.core.clock import day_of as _close_day
        for _st in (getattr(village, "stalls", []) or []):
            try:
                _cl = {k: [int(v[0]), int(v[1])] for k, v in ( _st.get("stock", {}) or {}).items()}
                _cl["_day"] = int(_close_day(village.clock_pts))
                _st["_closing"] = _cl
            except Exception:
                pass
    except Exception:
        pass
    """CHAIN: workers read yesterday's closing shelves and take up what's needed.
    Retrains work_skill + work blocks when their own shelf gluts (<=0.8) and
    another trade cries out (>=2.0). Said aloud — the whole village hears."""
    posts = village.work_posts or {}
    FLEX_GOODS = _flex_goods(posts)

    def _commute(npc, at) -> float:
        """Tiles from home to a post, priced into scarcity (scar − d/50):
        villagers take near work; only real need sends them cross-map."""
        try:
            home = (npc.comps.get("anchors", {}) or {}).get("home") \
                or (npc.x, npc.y)
            return max(abs(at[0] - home[0]), abs(at[1] - home[1])) / 50.0
        except Exception:
            return 0.0

    def _dearth(good, post) -> float:
        """0 when the trade can work its post, up to 3 when the land/larder
        can't feed it. Shelf scarcity says what the village lacks; dearth
        says what can actually be worked — no one fishes an empty pond or
        bakes without grain. Slow dawn logic, never panic."""
        try:
            skill = (post or {}).get("skill", "")
            if skill == "Fishing":
                from omnisim.systems.fishery import stock_of as _sot, pond_params as _pp
                K, _ = _pp(village)
                return 3.0 * max(0.0, 1.0 - _sot(village) / K)
            if skill == "Cooking":
                mouths = max(1, sum(1 for m in village.npcs
                                    if not m.comps.get("dead")))
                grain = sum(n.get("pocket", {}).get("flour", 0)
                            + n.get("pocket", {}).get("wheat", 0) / 2
                            + n.get("pocket", {}).get("barley", 0) / 2
                            for n in village.npcs if not n.comps.get("dead"))
                return 3.0 * max(0.0, 1.0 - grain / (2.0 * mouths))
        except Exception:
            pass
        return 0.0

    for npc in village.npcs:
        if npc.comps.get("dead") or npc.get("role") in STATIC_ROLES:
            continue
        # STEP 6 SEAM B (switch_bid): when flags.step_6 is on, the bid
        # replaces the avg-scarcity + profit-pull + needy_unstaffed pick
        # below. Belt + suspenders: the shared tail (grubstake, 2/dawn herd
        # cap, argue-3, flex_day, baker/food-base holds) still re-validates.
        # Flag off: bit-identical old path (this block no-ops).
        try:
            from omnisim.systems.decision import on as _d6f_on
            _use6 = bool(_d6f_on())
        except Exception:
            _use6 = False
        if _use6:
            try:
                from omnisim.systems.decision import switch_bid as _d6_bid
                _bid = _d6_bid(village, npc)
            except Exception:
                _bid = None
            if _bid is not None and not _bid.get("hold"):
                _tgt = _bid.get("target_skill")
                # map target skill -> post good: find posts entry with skill == target
                _post = next((g for g, p in posts.items() if (p or {}).get("skill") == _tgt), None)
                if _post is not None and _post in posts:
                    # R5: the margin bid has already argued (3 weekly wins in
                    # switch_bid); the tail re-argue is the OLD scarcity path.
                    # Jump it: run only herd-cap + flip-flop + food-hold, then switch.
                    _skip_argue = True
                    best, scar = _post, 9.0
                    _profit_msg = ""
                    _profit_best = None
                else:
                    continue  # no post for that skill: stay
            else:
                continue  # stay (or sole-supplier hold)

        if not locals().get("_skip_argue"):
            options = [(g, scarcity_of_good(village, g)) for g in FLEX_GOODS if g in posts]
            if not options:
                continue
            # Dawn shelves are noise (post-rush, pre-cart): judge trades by the
            # 3-dawn average scarcity, not this morning's panic.
            try:
                _mem = getattr(village, "scar_mem", None)
                if not isinstance(_mem, dict):
                    _mem = village.scar_mem = {}
                _avg = {}
                for _g, _s in options:
                    _hist = _mem.setdefault(_g, [])
                    _hist.append(float(_s))
                    del _hist[:-3]
                    _avg[_g] = sum(_hist) / len(_hist)
            except Exception:
                _avg = {g: s for g, s in options}
            # Dumber belly, voted: hunger never retrains. Hungry hands eat from
            # the pocket, shop the market, or walk to the wilds (scored trips,
            # every tick) — dawn moves only on sustained shelf signals below.
            # No wages, no till-floats: the forge banks iron episodically (heats
            # below), and its flex identity is representative iron, not a wage.
            _FLEX_PRODUCT = {"Smithing": "hammer"}
            _prod = (WORK_GOODS.get(npc.get("work_skill")) or (None,))[0] \
                or _FLEX_PRODUCT.get(npc.get("work_skill") or "")
            cur = _prod
            if cur is not None and cur not in FLEX_GOODS:
                continue  # settled trades with no post (the forge) hold
            if not locals().get("_skip_argue") and _avg.get(cur, 9.0) > 0.8 and cur is not None:
                continue  # own trade still wanted (smoothed, not one thin shelf)
            # Tradeless hands read their own shelf as bare: dawn employs them.
            best, scar = max(options, key=lambda t: _avg.get(t[0], t[1]) - _commute(
                npc, posts.get(t[0], {}).get("at", (0, 0)))
                - _dearth(t[0], posts.get(t[0], {})))
            scar = _avg.get(best, scar)
            # Staffed by SKILL, not goods: the potter makes clay, so goods-based
            # staffing never saw her — and every glut bannered for the kiln.
            staffed = {WORK_GOODS.get(n.get("work_skill"), (None,))[0]
                       for n in village.npcs if not n.comps.get("dead")}
            staffed_skills = {n.get("work_skill")
                              for n in village.npcs if not n.comps.get("dead")}
            hungry_bins = set()  # makers running dry ARE the demand no shelf shows
            for m in village.npcs:
                if m.comps.get("dead"):
                    continue
                md = WORK_NEEDS.get(m.get("work_skill") or "")
                if md is not None and m.get("pocket", {}).get(md[0], 0) < 2:
                    hungry_bins.add(md[0])
            # Full bellies hold hands steady: bare shelves mean shortage only
            # when mouths cry too. Bars rise as hunger falls (feast: 3.5/4.0,
            # famine: 1.5/2.0) — stability in plenty, rotation in crisis.
            try:
                from omnisim.systems.valuation import urgency as _urg2
                _hungers = [float(m.get("hunger", 0)) for m in village.npcs
                            if not m.comps.get("dead")]
                _belly = sum(float(_urg2(h, "hunger")) for h in _hungers) \
                    / max(1, len(_hungers))
                _belly = max(0.0, min(1.0, _belly))
            except Exception:
                _belly = 0.5
            _need_bar = 1.5 + (1.0 - _belly) * 2.0
            _settle_bar = 2.0 + (1.0 - _belly) * 2.0
            needy_unstaffed = [(g2, _avg.get(g2, s2)) for g2, s2 in options
                               if g2 not in staffed
                               and posts.get(g2, {}).get("skill") not in staffed_skills
                               and (_avg.get(g2, s2) >= _need_bar or g2 in hungry_bins)]
            # UAILS: gaps pay — among wanted unstaffed trades, the fattest honest
            # day's wage pulls (tape price × yield vs current trade). A fisher
            # nets five where wheat nets two? Hands follow coin, not just echoes.
            _profit_best, _profit_msg = None, ""
            try:
                from omnisim.systems.haggle import enabled as _ue2
                from omnisim.systems.haggle import expected_price as _ep2
                if _ue2():
                    def _day_wage(good):
                        _qty = 1
                        try:
                            for _sk, _gd in (WORK_GOODS or {}).items():
                                if _gd and _gd[0] == good:
                                    _qty = max(1, int(_gd[1]))
                            return max(1, int(_ep2(village, npc, good, 1))) * _qty
                        except Exception:
                            return 1
                    _cur_wage = _day_wage(cur) if cur else 1
                    _cands = [(g2, _day_wage(g2)) for g2, s2 in needy_unstaffed]
                    if _cands:
                        _pg, _pw = max(_cands, key=lambda t: t[1])
                        if _pw >= 2 * max(1, _cur_wage):
                            _profit_best = _pg
                            from omnisim.entities.items import item_name as _in0
                            _profit_msg = f" — {_in0(_pg)} pays better than " \
                                f"{_in0(cur) if cur else 'idle hands'}"
            except Exception:
                pass
            if _profit_best is not None:
                best = _profit_best  # coin outranks echoes when the gap is real
                try:
                    scar = _avg.get(best, 1.5)
                except Exception:
                    scar = 1.5
            elif needy_unstaffed:  # CHAIN+: an empty wanted trade pulls, crisis or not
                best, scar = max(needy_unstaffed,  # near need beats far need
                                  key=lambda t: t[1] - _commute(
                                      npc, posts.get(t[0], {}).get("at", (0, 0)))
                                  - _dearth(t[0], posts.get(t[0], {})))
            elif best == cur or (scar < _settle_bar and _shelf_sum(village, best) > 0):
                continue  # idle hands take any half-wanted trade; settled ones
                # wait — unless the shelf is literally bare (a real hole pulls
                # even when bellies are full; thin shelves alone don't)
            if (npc.get("work_skill") == "Cooking" and posts[best]["skill"] != "Cooking"):
                continue  # the oven must smoke — the baker never retrains away
            # FOOD BASE HOLDS: hands that feed a hungry village don't leave —
            # no exit while your chain's shelves stand thinner than target.
            try:
                from omnisim.systems.production import trade_of as _tof2
                from omnisim.systems.production import recipes as _recs2
                from omnisim.systems.pricing import _chain_good as _chain2
                from omnisim.systems.pricing import cover_of as _cover2
                _trade0 = _tof2(npc)
                _hold = False
                if _trade0 is not None:
                    for _rid in _trade0.get("recipes", []) or []:
                        _r0 = _recs2().get(_rid, {})
                        _outs = [o.get("good") for o in _r0.get("outputs", []) or []]
                        if not _outs and _r0.get("plot"):
                            _outs = ["wheat", "barley", "turnip"]  # rows feed all three
                        for _g0 in _outs:
                            if _g0 and _chain2(_g0):
                                from omnisim.systems.production import goods as _gs2
                                _tgt0 = float((_gs2().get(_g0, {}) or {}).get("target_cover", 5))
                                if _cover2(village, _g0) < _tgt0:
                                    _hold = True
                                    break
                        if _hold:
                            break
                if _hold:
                    continue
            except Exception:
                pass
        # STEP 2 stabilizer (Step 6 writes the full rules): one price spike
        # moves no one — the same target must argue three dawns running.
        # Plus a villageherd cap: two switches a dawn, or the whole field
        # leaves on the same morning and nobody sows.
        try:
            _fired = getattr(village, "flex_fired", None)
            _today = day_of(village.clock_pts)
            if not isinstance(_fired, list) or not _fired or _fired[0] != _today:
                _fired = village.flex_fired = [_today, 0]
            if _fired[1] >= 2:
                continue
        except Exception:
            pass
        if locals().get("_skip_argue"):
            pass  # R5: margin bid argued 3 weekly wins already; no re-argue
        else:
         try:
            _argue = getattr(village, "flex_argue", None)
            if not isinstance(_argue, dict):
                _argue = village.flex_argue = {}
            _tgt = posts[best]["skill"]
            if _tgt != (npc.get("work_skill") or ""):
                _pt, _ct = _argue.get(npc.name, (None, 0))
                _ct = _ct + 1 if _pt == _tgt else 1
                _argue[npc.name] = (_tgt, _ct)
                if _ct < 3:
                    continue
         except Exception:
            pass
        # Abandoned trades pull a replacement next dawn (unstaffed-first) —
        # a one-day gap is rotation; a pinned worker is a frozen village.
        if day_of(village.clock_pts) - int(npc.get("flex_day", -99)) < 3:
            continue  # CHAIN+: no flip-flopping — a new trade gets three days
        if posts[best]["skill"] == "Farming" and npc.get("work_skill") != "Farming" \
                and int(npc.get("pocket", {}).get("coin", 0)) < 20:
            continue  # GRUBSTAKE: crops pay in five to nine dawns, not tonight —
            # founding acres takes a week's dinners in pocket (backing, not
            # wages). Broke hands take day-pay trades and leave the slow
            # acres to souls who can afford the hungry wait
        npc.comps["work_skill"] = posts[best]["skill"]
        npc.comps["flex_day"] = day_of(village.clock_pts)
        try:  # STEP 8: new hands start at half their best related skill
            from omnisim.systems.mastery import on as _m8s_on
            from omnisim.systems.mastery import seed_skill as _m8seed
            if _m8s_on():
                _m8seed(npc, posts[best]["skill"])
        except Exception:
            pass
        try:  # STEP 6: stamp per-trade switch day + npc cooldown anchor
            from omnisim.systems.decision import on as _d6s_on
            if _d6s_on():
                _today6 = day_of(village.clock_pts)
                _newskill6 = posts[best]["skill"]
                try:
                    _tsw = getattr(village, "trade_switched", None)
                    if not isinstance(_tsw, dict):
                        _tsw = village.trade_switched = {}
                    _tsw[_newskill6] = _today6
                except Exception:
                    pass
                npc.comps["switch_day"] = _today6
        except Exception:
            pass
        try:
            village.flex_fired[1] += 1
        except Exception:
            pass
        ax, ay = posts[best]["at"]
        npc.comps["work_at"] = (ax, ay)  # P2: the planner walks here tomorrow
        for b in npc.comps.get("sched", []):
            if b.get("act") == "work":
                b["at"] = (ax, ay)
        npc.comps["path"] = []
        npc.comps["dest"] = None
        npc.comps["dest_kind"] = None
        from omnisim.entities.items import item_name
        village.say(f"{npc.name} takes up {posts[best]['skill'].lower()} — "
                    f"the shelves cry out for {item_name(best)}.{_profit_msg}")
    # V1 Foundations (GDD 5.2): smith or carpenter trues the worst-worn
    # shop at dawn — iron hands and wood hands both keep the village true.
    try:
        from omnisim.systems.workspaces import repair as _wrepair
        ws = getattr(village, "workspaces", {}) or {}
        worn = [(k, float(s.get("condition", 100.0))) for k, s in ws.items()
                if isinstance(s, dict) and float(s.get("condition", 100.0)) < 60.0]
        if worn:
            smith = next((n for n in village.npcs
                          if n.get("work_skill") in ("Smithing", "Carpentry")
                          and not n.comps.get("dead")), None)
            if smith is not None:
                key = min(worn, key=lambda kv: kv[1])[0]
                _wrepair(village, key, 30.0)
                village.say(f"{smith.name} trues the {key.lower()} shop "
                            f"({int(worn[0][1])}% → "
                            f"{int(ws[key].get('condition', 100.0))}%).")
    except Exception:
        pass
    try:  # STEP 8: idle skills rust past 14 days (flag-gated)
        from omnisim.systems.mastery import on as _m8d_on
        from omnisim.systems.mastery import rust_tick as _m8rust
        if _m8d_on():
            _m8rust(village)
    except Exception:
        pass
    # STEP 7: buildings breathe at dawn — slots work, upkeep bills,
    # and one patient soul a week runs the scanner and breaks ground.
    try:
        from omnisim.systems.buildings import on as _b7_on
        if _b7_on():
            from omnisim.systems.buildings import passive_tick as _b7p
            from omnisim.systems.buildings import upkeep_tick as _b7u
            from omnisim.systems.buildings import scan as _b7s
            from omnisim.systems.buildings import start_project as _b7sp
            from omnisim.systems.buildings import work_on as _b7w
            _b7p(village)
            _b7u(village)
            for _npc7 in village.npcs:
                if _npc7.comps.get("dead"):
                    continue
                try:
                    _b = next((x for x in getattr(village, "buildings", []) or []
                               if x.get("status") == "under_construction"
                               and (_npc7.name in (x.get("builder"), x.get("owner")))), None)
                    if _b is not None:
                        _b7w(village, _b, ticks=10)
                except Exception:
                    pass
            try:
                from omnisim.core.clock import day_of as _d7
                if int(_d7(village.clock_pts)) % 7 == 0:
                    for _npc7 in village.npcs:
                        if _npc7.comps.get("dead") or _npc7.comps.get("active_build"):
                            continue
                        try:
                            _cands = _b7s(village, _npc7)
                        except Exception:
                            _cands = []
                        if _cands:
                            _b7sp(village, _npc7, _cands[0]["blueprint"])
                            break  # one groundbreaking per week, village-wide
            except Exception:
                pass
    except Exception:
        pass
    # P2 Utility AI: every dawn, every soul plans their day from needs.
    # YAML blocks survive in fallback_sched if planning ever fails.
    try:
        from omnisim.systems.ai_agenda import plan_day as _plan_day
        _tod = (village.clock_pts // 100) % 1440
        _missed = []
        for npc in village.npcs:
            if npc.comps.get("dead") or not npc.comps.get("sched"):
                continue
            try:
                planned = _plan_day(npc, village)
            except Exception:
                _missed.append(npc.name)
                continue
            if not planned:
                _missed.append(npc.name)
                continue
            old = npc.comps.get("sched", [])
            old_act = old[current_block(old, _tod)]["act"] if old else None
            new_bi = current_block(planned, _tod)
            npc.comps["fallback_sched"] = old
            npc.comps["sched"] = planned
            npc.comps["bought"] = False
            if old_act == planned[new_bi]["act"]:
                # Same labor past dawn: keep walking it, don't re-enter
                # (re-entering would eat a second breakfast).
                npc.comps["block_idx"] = new_bi
            else:
                npc.comps["block_idx"] = -1
                npc.comps["dest"] = None
                npc.comps["dest_kind"] = None
                npc.comps["path"] = []
        if _missed:
            village.say(f"The old routine holds for: {', '.join(_missed)}.")
    except Exception:
        pass


def _wood_shift(village, npc):
    """WOOD: one tree is a winter of firewood — but it takes an axe, a
    strong back, and the walk to the trunk. Axeless hands gather deadfall
    (branches, not trunks); no trunk in reach means branches too."""
    from omnisim.world import tile as T
    from omnisim.systems.tools import best_tool
    from omnisim.systems.skills import skill_level
    lvl = skill_level(npc, "Woodcutting")
    axe = best_tool("Woodcutting", npc)
    if axe >= 1:
        gm = village.game_map
        for r in range(1, 5):
            for dx in range(-r, r + 1):
                for dy in (-r, r):
                    tx, ty = npc.x + dx, npc.y + dy
                    if gm.in_bounds(tx, ty) and int(gm.tiles[ty, tx]) == T.TREE:
                        gm.tiles[ty, tx] = T.GRASS
                        try:
                            invalidate_bfs_cache()
                        except Exception:
                            pass
                        village.felled.append((tx, ty))
                        qty = 8 + lvl // 2 + axe // 2
                        npc.comps["activity"] = "felling"
                        npc.comps["got_dirty"] = True  # bark and sweat
                        seen_say(village, npc.x, npc.y,
                                 f"{npc.name} fells a tree (+{qty} firewood).")
                        return ("firewood", qty)
            for dy in range(-r + 1, r):
                for dx in (-r, r):
                    tx, ty = npc.x + dx, npc.y + dy
                    if gm.in_bounds(tx, ty) and int(gm.tiles[ty, tx]) == T.TREE:
                        gm.tiles[ty, tx] = T.GRASS
                        try:
                            invalidate_bfs_cache()
                        except Exception:
                            pass
                        village.felled.append((tx, ty))
                        qty = 8 + lvl // 2 + axe // 2
                        npc.comps["activity"] = "felling"
                        npc.comps["got_dirty"] = True
                        seen_say(village, npc.x, npc.y,
                                 f"{npc.name} fells a tree (+{qty} firewood).")
                        return ("firewood", qty)
    npc.comps["activity"] = "gathering"
    return ("firewood", 1 + lvl // 4)  # deadfall: branches, honest and meager


def _farm_shift(village, npc):  # type: ignore[no-untyped-def]
    """CROPS: walk the rows — cut what's ripe, sow what's empty, tend the rest.
    Returns (item_id, qty) to bank, or None for a husbandry hour. Seed corn
    stays in the pocket (stamped gen 1) — the land tithes the sheaf, not
    the future."""
    from omnisim.core.clock import day_of
    from omnisim.systems.crops import (choose_crop, ensure_plots, harvest,
                                       kind_of, ripe, seed_of, sow)
    day = day_of(village.clock_pts)
    plots = ensure_plots(village)
    from omnisim.systems.schedule import _market_glut
    for p in plots:
        if ripe(p, day):
            kind = kind_of(p)
            # CROPS: full shelves cut nothing — unless survival breaks
            # routine. Subsistence first, market second: no farmer starves
            # watching ripe rows stand (same rule as the glut pause below).
            from omnisim.systems.valuation import desperate as _desp
            if _market_glut(village, kind) and not _desp(npc):
                npc.comps["activity"] = "waiting"
                return None
            from omnisim.systems.perks import perk_value
            from omnisim.systems.skills import skill_level
            _kind, qty, seeds = harvest(village, p)
            qty += perk_value(skill_level(npc, "Farming"), "Farming", "harvest_bonus", 0)
            try:  # a sharp hoe cuts cleaner sheaves
                from omnisim.systems.tools import best_tool as _bth
                qty += int(_bth("Farming", npc)) // 3
            except Exception:
                pass
            if seeds > 0:  # the kept seed corn: gen 1, traits unwritten
                from omnisim.entities.items import cap_of as _cap, stamp_lore as _sl, stow as _stow
                _seed = seed_of(kind)
                _pocket = npc.get("pocket", {})
                _left = _stow(_pocket, _seed, seeds, _cap(npc))
                _sl(npc, _seed, 1, npc.name, "the fields", day,
                    extra={"gen": 1, "traits": {}})
            npc.comps["activity"] = "harvesting"
            npc.comps["got_dirty"] = True  # soil under nails
            return (kind, qty)
    pocket = npc.get("pocket", {})
    if pocket.get("compost", 0) >= 1:  # ROT: the hungry earth eats first
        from omnisim.systems.crops import feed as _feed
        hungry = [p for p in plots if int(p.get("fert", 0)) < 3]
        if hungry:
            hungry.sort(key=lambda p: p.get("fert", 0))
            pocket["compost"] -= 1
            if pocket["compost"] <= 0:
                del pocket["compost"]
            _feed(village, hungry[0])
            npc.comps["activity"] = "feeding"
            npc.comps["got_dirty"] = True  # compost on hands
            return None
    from omnisim.systems.crops import kinds as _kinds, min_fert_of as _mf
    _seeds = {seed_of(k): k for k in _kinds()}
    sowable = [(s, k) for s, k in _seeds.items() if pocket.get(s, 0) >= 1]
    if sowable:
        cropped = sum(1 for p in plots if p["crop"] is not None)
        empties = sorted((p for p in plots if p["crop"] is None),
                         key=lambda p: -p.get("fert", 0))
        _cap10 = 10
        try:
            from omnisim.systems.crops import kinds as _k10
            _cap10 = max(10, len(ensure_plots(village)))
        except Exception:
            pass
        if cropped < _cap10 and empties:
            p = empties[0]  # CROPS: best soil first, two plots always resting
            want = choose_crop(village, p)  # hungry mouths, tired earth decide
            seed_item = seed_of(want)
            if pocket.get(seed_item, 0) < 1:  # no pinch of the needful: any seed
                seed_item = sowable[0][0]
                want = dict(_seeds)[seed_item]
            if int(p.get("fert", 0)) >= _mf(want):
                pocket[seed_item] -= 1
                if pocket[seed_item] <= 0:
                    del pocket[seed_item]
                sow(village, p, day, want)
                npc.comps["activity"] = "sowing"
                npc.comps["got_dirty"] = True  # earth under nails
                return None
    npc.comps["activity"] = "tending"
    return None


def _bank_production(village, npc, skill: str) -> None:  # type: ignore[no-untyped-def]
    """Shift tick: real goods into the pocket (what fits), stamped with maker.

    ECON: backs hold CARRY_CAP — the rest lands at the worker's feet for
    haulers. Owned ground takes its due first (see ownership.pay_tithe).
    """
    # STEP 1: production runs through data (production.py). The old
    # counters path was deleted here: frequencies emerge from margins.
    from omnisim.systems.production import run_production_tick as _run1
    _run1(village, npc)
    return


def bank_goods(village, npc, skill: str, item_id: str, qty: int,  # type: ignore[no-untyped-def]
               batch_cost: float = 0.0, quality: int | None = None) -> None:
    """Shared banking: lots, tithe, pocket/overflow, made_today, wear, lore.
    The Step 1 data engine banks here.
    STEP 4: mood scales the banked yield (0.7 floor) — hardship thins every
    shift's output, including farm and gather, at this single choke."""
    from omnisim.systems.ownership import drop_at, pay_tithe
    try:
        from omnisim.systems.mood import perf_of as _bank_perf
        _bperf = _bank_perf(npc)
        if _bperf != 1.0 and qty > 0:
            qty = max(1, int(round(qty * _bperf)))
    except Exception:
        pass
    try:  # FIFO LOTS: the batch's book closes — inputs plus a copper a unit
        # for the hands. Mastery does NOT go in the book: it rides the ask
        # (hope), never the floor (need) — a strange name charges no premium
        # on an empty belly, and floors that clear keep the village fed.
        # Raw ticks popped nothing (cost 0, found goods keep the walk's 1c).
        if batch_cost > 0 and qty > 0:
            from omnisim.systems.haggle import note_lot as _nlb
            _nlb(npc, item_id, qty, max(1, int(round(batch_cost / qty)) + 1))
    except Exception:
        pass
    pay_tithe(village, npc, item_id, qty)  # the land eats before the pocket
    from omnisim.entities.items import cap_of, item_name, stow
    pocket = npc.get("pocket", {})
    left = stow(pocket, item_id, qty, cap_of(npc))
    if left > 0:
        drop_at(village, npc.x, npc.y, item_id, left, npc.name, "spillover")
        seen_say(village, npc.x, npc.y,
                 f"{npc.name}'s back is full — +{left} {item_name(item_id)} "
                 f"lands at their feet.")
    made = village.made_today.setdefault(item_id, {"qty": 0, "by": []})
    made["qty"] += qty
    if npc.name not in made["by"]:
        made["by"].append(npc.name)
    from omnisim.systems.tools import wear_tick
    wear_tick(village, npc, skill)  # TOOLS: honest work dulls iron
    if skill in ("Fishing", "Smithing", "Herbalism"):
        npc.comps["got_dirty"] = True  # guts, soot, and green stains
    try:  # V1 Foundations: emergent ledger + workspace wear (GDD 5.2/7.1)
        from omnisim.systems.professions import record as _prec
        _prec(npc, skill, 0.5)
        from omnisim.systems.workspaces import use as _wsuse
        _wsuse(village, skill, npc)
    except Exception:
        pass
    if item_id == "coin":
        # No wages anywhere: coin never banks from work. (Merchant margin and
        # direct trade move coin now — see _do_sale and mend fees.)
        return
    from omnisim.core.clock import day_of
    from omnisim.entities.items import stamp_lore, yield_q
    from omnisim.systems.skills import skill_level
    from omnisim.systems.tools import best_tool
    from omnisim.world.places import describe_location
    if quality is None:
        quality = yield_q(skill_level(npc, skill), best_tool(skill, npc))
    try:  # worn shops make coarse goods: sour ovens, uneven loaves
        from omnisim.systems.workspaces import efficiency as _wseff
        _eff = float(_wseff(village, skill))
        if _eff < 0.4:
            quality = min(quality, 2)
        elif _eff < 0.75:
            quality = min(quality, 3)
    except Exception:
        pass
    stamp_lore(npc, item_id, quality,
               npc.name, describe_location(village, npc.x, npc.y), day_of(village.clock_pts))


_KEEP_BASE: dict = {}


def _sustenance_reserve_on() -> bool:
    """SUSTENANCE INVARIANT leg 2 gate (SCENARIO-LOCAL, default off).

    The canonical config ships the dinner reserve off so the canonical gate and
    the abundant controls keep their 83ccbed fixed points; only
    data/scenarios/sustenance.yaml turns it on.
    """
    try:
        from omnisim.systems.production import _load as _ld
        return bool(((_ld("economy") or {}).get("flags", {}) or {}).get(
            "sustenance_reserve", False))
    except Exception:
        return False


def _reserve_day_hunger() -> float:
    """SUSTENANCE INVARIANT: one day's hunger climb, in relief units."""
    try:
        from omnisim.systems.valuation import drives as _drv
        return max(1.0, float((_drv() or {}).get("reserve_day_hunger",
                                                70.0)))
    except Exception:
        return 70.0


def keep_base_of(item_id: str) -> int:
    """Skill-blind floor: reserve flag + firewood box. With the SCENARIO-LOCAL
    SUSTENANCE INVARIANT leg 2 on (flag `sustenance_reserve`; memoized on item +
    day-hunger knob) dinner goods (food >= 12: bread down to turnip, berries and
    potted meals included; poultice/herbs stay medicine) hold a full day's
    dinners counted in mouthfuls — ceil(day/serving) servings over
    servings-per-unit — never fewer than the old flag; sales take only surplus
    above tomorrow. With the flag off this is byte-identical to 83ccbed."""
    _on = _sustenance_reserve_on()
    _day = 0.0
    if _on:
        try:
            _day = _reserve_day_hunger()
        except Exception:
            _day = 70.0
    _key = (item_id, _day) if _on else item_id
    try:
        _h = _KEEP_BASE.get(_key)
        if _h is not None:
            return _h
    except Exception:
        pass
    from omnisim.entities.items import reserve_of as _reserve_of
    keep = 0
    try:
        keep = max(keep, int(_reserve_of(item_id)))
    except Exception:
        try:
            from omnisim.core.actions import DINNER_RESERVE
            if item_id in DINNER_RESERVE:
                keep = max(keep, 2)
        except Exception:
            pass
    if item_id == "firewood":
        keep = max(keep, 2)
    if _on:
        try:  # INVARIANT leg 2: a day's dinners in mouthfuls, never fewer.
            from omnisim.entities.items import defs as _defs2
            from omnisim.entities.items import servings_of as _serv2
            _fv = float((_defs2()["items"].get(item_id, {}) or {}).get(
                "food", 0))
            if _fv >= 12:
                import math as _math
                _need = int(_math.ceil(_day / _fv))
                _per = max(1, int(_serv2(item_id) or 1))
                keep = max(keep, int(_math.ceil(_need / _per)))
        except Exception:
            pass
    try:
        _KEEP_BASE[_key] = keep
    except Exception:
        pass
    return keep


def keep_of(npc, item_id: str) -> int:  # type: ignore[no-untyped-def]
    """Hands sell no dinner, no tomorrow's flour/fuel, no seed corn."""
    keep = keep_base_of(item_id)
    if (npc.get("work_skill") or "") == "Cooking" and item_id == "wheat":
        keep = max(keep, 6)  # the bin: grain to grind before the oven eats
    if (npc.get("work_skill") or "") == "Cooking" and item_id == "barley":
        keep = max(keep, 6)  # barley grinds too — the bin holds both grains
    need = WORK_NEEDS.get(npc.get("work_skill") or "")
    if need is not None and item_id == need[0]:
        keep = max(keep, 6)
    if (npc.get("work_skill") or "") == "Milling" and item_id == "barley":
        keep = max(keep, 6)  # the mill's second grain: hands off the barley bin
    if (npc.get("work_skill") or "") == "Mining" and item_id == "iron_ore":
        # THE MINER eats from his back: ore is his dinner money. His keep
        # is one unit, not six (six was the FORGE's bin, keyed to the
        # wrong skill: it pinned every sale while the shelf read glut).
        keep = max(keep, 1)
    if (npc.get("work_skill") or "") == "Smithing" and item_id == "iron_ore":
        keep = max(keep, 6)  # the forge's stone: no iron, no tools, no village

    if (npc.get("work_skill") or "") == "Farming" and \
            str(item_id).endswith("_seed"):
        from omnisim.systems.crops import SEED_KEEP
        keep = max(keep, SEED_KEEP)  # seed pinches are next season, not goods
    if (npc.get("work_skill") or "") == "Farming" and item_id in ("wheat", "barley"):
        keep = max(keep, 2)  # household grist: a loaf-to-be stays home, the
        # rest rides to market (deep bins starve the miller's stones)
    if (npc.get("work_skill") or "") == "Pottery" and item_id == "clay":
        keep = max(keep, 2)  # POTS: two mud for the kiln before the market
    return keep


def _sellable_total(npc) -> int:  # type: ignore[no-untyped-def]
    from omnisim.core.actions import SELLABLE
    pocket = npc.get("pocket", {})
    return sum(max(0, pocket.get(i, 0) - keep_of(npc, i)) for i in SELLABLE)


def _glut_line(village, item_id: str) -> int:  # type: ignore[no-untyped-def]
    """How much of a good the Market holds before production pauses: 1.5x
    par for perishables (floored 8), 2.5x for durables (floored 15). One
    line, shared by the merchant's appetite and the makers' pause."""
    try:
        par = 0
        for s in village.stalls:
            par += (s.get("restock", {}) or {}).get(item_id, [0, 0])[0]
        try:
            from omnisim.systems.rot import SHELF_LIFE as _SHELF
            soft = item_id in _SHELF
        except Exception:
            soft = False
        if soft:
            return max(int(par * 1.5), 8)
        try:  # ORE TRAP: unstaffed-input par sits at seed level (6) while
            # staffed makers bank 10+/shift: floor 15 == seeded shelf 15,
            # so the line reads glut on day 3 and the miner starves beside
            # full shelves. Scale the durable floor by STAFFED makers:
            # each staffed maker of an input-using trade adds 10 units.
            from omnisim.systems.production import trades as _trs2
            from omnisim.systems.production import recipes as _recs2
            _staff = sum(1 for _n in (getattr(village, "npcs", []) or [])
                         if not _n.comps.get("dead"))
            _users = 0
            for _t in _trs2().values():  # count only STAFFED trades: an
                # empty forge burns no ore. Unstaffed input demand is paper.
                _sk = str(_t.get("skill", "") or "")
                if _sk and not any(not _n.comps.get("dead") and _n.get("work_skill") == _sk
                                   for _n in (getattr(village, "npcs", []) or [])):
                    continue
                _ins = {(_i.get("good") or "") for _i in
                        sum([(_recs2().get(_r, {}) or {}).get("inputs", []) or []
                             for _r in (_t.get("recipes", []) or [])], [])}
                if item_id in _ins:
                    _users += 1
            # Staffing is not demand: the line must stay BELOW what makers
            # actually EAT. One smith burns ~2 ore/day into tools the village
            # buys; a line of 25 lets the shelf hold 12 days of iron while
            # the miner starves. Floor 15 stands (seed 15); the fix is not
            # a bigger line but makers who BUY (maker-first wants).
            _floor = 15
        except Exception:
            _floor = 15
        return max(int(par * 2.5), _floor)
    except Exception:
        return 8


def _market_appetite(village, npc) -> int:  # type: ignore[no-untyped-def]
    """Units the Market will actually take right now: pocket surplus up to
    the glut line. Intake and the production pause share the line, so makers
    fill shelves exactly to the pause and stop — no pocket mountains, no
    refused hauls mid-walk. Tabs stay bounded by the line itself."""
    try:
        from omnisim.core.actions import SELLABLE
    except Exception:
        return 0
    if not village.stalls:
        return 0
    stall = village.stalls[0]
    pocket = npc.get("pocket", {}) or {}
    stock = stall.get("stock", {}) or {}
    try:  # past the trust line, goods stay on backs — tabs have limits
        from omnisim.systems.valuation import drives as _drv
        _trust = float(_drv().get("trust_line", 40.0))
        if float((stall.get("credit", {}) or {}).get(npc.name, 0)) >= _trust:
            return 0
    except Exception:
        pass
    total = 0
    for item_id in SELLABLE:
        surplus = pocket.get(item_id, 0) - keep_of(npc, item_id)
        if surplus <= 0:
            continue
        # Feet read ROOM plus a day wage: surplus walks when the shelf has
        # room (glut line, yesterday closing like the counter) OR the load
        # is dinner money (ERRAND_AT units). Feet never read the cover cap:
        # that number is the counter buffer for the day rush, not a walk
        # signal - reading it drained the till in 3 days (1500c flood).
        try:  # intake reads yesterday closing, same as the counter.
            from omnisim.core.clock import day_of as _apday
            _cl2 = (stall.get("_closing", {}) or {})
            if _apday(village.clock_pts) == int(_cl2.get("_day", -1)):
                shelf = (_cl2.get(item_id, [0, 0]))[0]
            else:
                shelf = (stock.get(item_id, [0, 0]))[0]
        except Exception:
            shelf = (stock.get(item_id, [0, 0]))[0]
        _room2 = _glut_line(village, item_id) - shelf
        # Single-customer producers (ore, clay) strand behind their own
        # morning deliveries: same-day shelf counts their own load. One
        # spare unit of room covers the day rush; below it, dinner money
        # (ERRAND_AT) still walks so makers never starve on full shelves.
        if _room2 >= 1:
            total += max(0, min(surplus, _room2))
        elif surplus >= ERRAND_AT:
            total += ERRAND_AT
    return total


def _carries_iron(npc) -> bool:  # type: ignore[no-untyped-def]
    """TOOLS: a forged implement is worth the walk on its own."""
    from omnisim.systems.tools import FORGE_ORDER
    pocket = npc.get("pocket", {})
    return any(pocket.get(t, 0) > 0 for t in FORGE_ORDER)


def stock_here(stall: dict) -> dict:
    """The shelf as it stands (never None)."""
    return stall.get("stock", {})


def _record_trade(village, seller: str, buyer: str, item_id: str, qty: int, price: int,  # type: ignore[no-untyped-def]
                  q: int = 1, tape: bool = True) -> None:
    """ECON: every deal lands in the village ledger — who, what, how much.
    Step 2: goods deals also fold into the price tape (quality-normalized);
    service fees (mending) and tab settlements stay out of goods prices."""
    from omnisim.core.clock import day_of, fmt_clock
    village.trades.append({"day": day_of(village.clock_pts),
                           "time": fmt_clock(village.clock_pts),
                           "seller": seller, "buyer": buyer,
                           "item": item_id, "qty": qty, "price": price})
    del village.trades[:-500]
    try:  # REALIZED-TV feed: production sales accrue to the seller's window.
        from omnisim.systems.realizedtv import on as _rtv_on2
        from omnisim.systems.realizedtv import record_sale as _rtv_rec
        if _rtv_on2() and item_id != "tab":
            for npc in (village.npcs or []):
                try:
                    if npc.name == seller and not npc.comps.get("dead"):
                        _rtv_rec(npc, village, price)
                        break
                except Exception:
                    pass
    except Exception:
        pass
    if tape and item_id != "tab":
        try:
            from omnisim.systems.pricing import note_trade_price as _tape
            _tape(village, item_id, price, q)
        except Exception:
            pass
        try:  # Part A: tape-exempt paths (tabs) stay out; goods feed realized.
            if item_id != "tab":
                from omnisim.systems.pricing import note_realized_sale as _nrs2
                _nrs2(village, item_id, price, q)
        except Exception:
            pass


def _settle_credit(village, stall: dict) -> None:  # type: ignore[no-untyped-def]
    """Merchant tabs settle FIFO from till inflow: the reserve pays what it
    owes, oldest first. Debt service IS circulation — creditors spend it back.
    The permanent float holds here too: settlements never breach 20%."""
    try:
        credit = stall.get("credit", {}) or {}
        if not credit:
            return
        try:
            _float_s = stall.get("float", 1500) or 1500
            _floor_s = 0.2 * float(_float_s)
        except Exception:
            _floor_s = 300.0
        for seller in list(credit.keys()):
            owed = float(credit.get(seller, 0) or 0)
            if owed <= 0:
                credit.pop(seller, None)
                continue
            till = float(stall.get("till", 0) or 0)
            if till <= _floor_s:
                break  # the float endures: tabs wait for inflow
            pay = min(owed, till - float(_floor_s))
            if pay <= 0:
                break
            stall["till"] = till - pay
            if pay >= owed:
                credit.pop(seller, None)
            else:
                credit[seller] = owed - pay
            if seller == "You":
                from omnisim.entities.items import pack_of as _pack_of
                pack = _pack_of(village.player)
                pack["coin"] = pack.get("coin", 0) + pay
            else:
                recip = next((n for n in village.npcs if n.name == seller), None)
                if recip is not None and not recip.comps.get("dead"):
                    recip.get("pocket", {})["coin"] = \
                        recip.get("pocket", {}).get("coin", 0) + pay
                else:
                    # ESTATE (coin conservation): the dead are paid into
                    # their pocket/ground coin via settle_estate, never
                    # skipped. Shortfalls land in the named ledger.
                    try:
                        from omnisim.systems.estate import (
                            settle_estate as _se)
                        _se(village, seller)
                        _rec2 = next(
                            (n for n in village.npcs if n.name == seller),
                            None)
                        if _rec2 is not None:
                            _rec2.get("pocket", {})["coin"] = \
                                _rec2.get("pocket", {}).get("coin", 0) + pay
                        else:
                            stall["till"] = float(stall.get("till", 0) or 0) + pay
                    except Exception:
                        stall["till"] = float(stall.get("till", 0) or 0) + pay
            _record_trade(village, stall.get("name", "the Market"), seller,
                          "tab", pay, 1)
    except Exception:
        pass


def _owninput_keep(npc, item_id):
    try:
        from omnisim.systems.owninput import keep_extra as _oke
        return int(_oke(npc, item_id))
    except Exception:
        return 0


def _do_sale(village, npc, stall: dict) -> None:  # type: ignore[no-untyped-def]
    """Unload pocket sellables onto the Market shelf. Coin now clears first;
    the rest goes on the merchant's tab (credit, backed by the goods, repaid
    FIFO from till inflow) — bulk harvests never bounce for want of a float."""
    from omnisim.core.actions import DINNER_RESERVE, SELLABLE, trade_price
    from omnisim.entities.items import item_name, lore_q, q_mult
    from omnisim.systems.skills import skill_level
    pocket = npc.get("pocket", {})
    trading = skill_level(npc, "Trading")
    tot, earned, tabbed = 0, 0, 0
    sname = stall.get("name", "the Market")
    try:  # tabs have limits: past the trust line, goods stay on backs
        from omnisim.systems.valuation import drives as _drv
        _trust = float(_drv().get("trust_line", 40.0))
    except Exception:
        _trust = 40.0
    _owed = float((stall.get("credit", {}) or {}).get(npc.name, 0))
    for item_id in SELLABLE:
        qty = pocket.get(item_id, 0)
        keep = max(keep_of(npc, item_id), _owninput_keep(npc, item_id))  # ECON: nobody sells their own dinner, flour, seed
        qty = max(0, qty - keep)
        if qty <= 0:
            continue
        if item_id in stock_here(stall):  # a listed good rides the shelf's weather
            unit = trade_price(item_id, trading, buying=False, stall=stall, village=village)
        else:  # ECON: a new good seeds the shelf at a fair price, not famine rates
            unit = trade_price(item_id, trading, buying=False, stall=None, village=village)
        try:  # UAILS: asks meet the merchant's wills (flag off: base stands)
            from omnisim.systems.haggle import deal_price, merchant_of, probe_up
            _merch = merchant_of(village)
            if _merch is not None:
                probe_up(village, npc, item_id, stall)
                unit, _dealt = deal_price(village, npc, _merch, item_id, unit)
                if not _dealt:
                    continue  # refused today — stays on the back, both learned
        except Exception:
            pass
        price = max(1, int(round(unit * q_mult(lore_q(npc, item_id)))))  # M7: same formula
        try:
            _sale_q = max(1, int(lore_q(npc, item_id)))
        except Exception:
            _sale_q = 1
        # MERCHANT: takes to appetite — cover-scaled when the Step 2 engine
        # runs (smooth buffer, not a wall), else the old glut line.
        try:
            try:  # intake reads YESTERDAY closing shelf: today sales already
                # freed room; the rush refills it by dusk. Same-day counts
                # strand single-customer producers (ore, clay) behind their
                # own morning deliveries.
                from omnisim.core.clock import day_of as _have_day
                _have = (stall.get("_closing", {}) or {}).get(item_id, [0, 0])[0]
                if _have_day(village.clock_pts) != int((stall.get("_closing", {}) or {}).get("_day", -1)):
                    _have = (stock_here(stall).get(item_id, [0, 0]))[0]
            except Exception:
                _have = (stock_here(stall).get(item_id, [0, 0]))[0]
            _offered = qty
            try:
                from omnisim.systems.pricing import appetite_cap as _appcap
                from omnisim.systems.pricing import engine_on as _peng3
                _cap = _appcap(village, item_id) if _peng3() else None
            except Exception:
                _cap = None
            _line = _cap if _cap is not None else _glut_line(village, item_id)
            if _cap is not None:
                # shopkeeper's shelf: restock to par, two over today's shelf
                # minimum (no-trade goods start trading on the minimum),
                # appetite caps manage everything above par.
                try:
                    from omnisim.systems.pricing import par_of as _par_of
                    _par = _par_of(stall, item_id)
                except Exception:
                    _par = 4
                _line = max(_line, _have + 2, _par)
            qty = max(0, min(qty, _line - _have))
            if _offered > qty:  # STEP 0: supply the shelf refused
                try:  # WHY-DIARY: one logged reason per refused unit.
                    _wd = getattr(village, "why_denied", None)
                    if not isinstance(_wd, dict):
                        _wd = village.why_denied = {}
                    from omnisim.core.clock import day_of as _wd_day
                    _wk = (_wd_day(village.clock_pts), npc.name, item_id)
                    _wd[_wk] = "glut: shelf %d vs line %d" % (int(_have), int(_line))
                except Exception:
                    pass
                _mbump(village, "declined_supply", item_id, _offered, npc=npc.name)
        except Exception:
            pass
        if qty <= 0:
            continue
        try:  # PERMANENT FLOAT: below 20% of reserve the counter stops
            # spending coin — everything goes on bounded trust tabs, settled
            # FIFO from inflow. A market-maker that spends its float to zero
            # freezes the market it makes; tabs bridge, coin endures.
            _float = stall.get("float", 1500) or 1500
            _floor = 0.2 * float(_float)
        except Exception:
            _floor = 300.0
        try:  # TABS RETIRED (flag tabs_enabled, default off): cash-only
            from omnisim.systems.production import _load as _tld
            _tabs = bool(((_tld("economy") or {}).get("flags", {}) or {}).get("tabs_enabled", False))
        except Exception:
            _tabs = False
        afford = min(qty, stall.get("till", 0) // price)
        if float(stall.get("till", 0)) - afford * price < _floor:
            afford = 0  # below the float: tabs, not coin
        tab = (qty - afford) if _tabs else 0  # the merchant's tab: goods now, coin from inflow
        if tab > 0:
            room = max(0.0, _trust - (_owed + tabbed))
            tab = min(tab, int(room // price))  # past the line: stays on the back
        if afford <= 0 and tab <= 0:
            continue
        pocket[item_id] -= (afford + tab)
        if pocket[item_id] <= 0:
            del pocket[item_id]
        stock = stall["stock"]
        stock[item_id] = [stock.get(item_id, [0, 0])[0] + afford + tab, price]
        stall["till"] -= afford * price
        pocket["coin"] = pocket.get("coin", 0) + afford * price
        tot += afford + tab
        earned += afford * price
        if tab > 0:
            tabbed += tab * price
            credit = stall.setdefault("credit", {})
            credit[npc.name] = int(credit.get(npc.name, 0)) + tab * price
        village.sold_today[item_id] = village.sold_today.get(item_id, 0) + afford + tab
        _record_trade(village, npc.name, sname, item_id, afford + tab, price,
                      q=_sale_q)
        try:  # STEP 0: sold units offset the day's refused offer
            from omnisim.systems.metrics import bump as _mbump_s
            _mbump_s(village, "sold_merchant", item_id, afford + tab, npc=npc.name)
        except Exception:
            pass
        try:  # FIFO LOTS: the seller's oldest lots leave; the merchant's book takes them in
            from omnisim.systems.haggle import merchant_of as _mo3, note_lot as _nl, pop_lots as _pl
            _pl(npc, item_id, afford + tab)
            _m3 = merchant_of(village)
            if _m3 is not None:
                _nl(_m3, item_id, afford + tab, price)
        except Exception:
            pass
    npc.comps["errand"] = None
    npc.comps["dest"] = None
    npc.comps["dest_kind"] = None
    npc.comps["path"] = []
    if tot > 0:
        gain_xp(npc, "Trading", 1)
        try:  # V1 (GDD 8.3): an honest unload builds the stall bond
            from omnisim.systems.relationships import adjust as _rel2
            _rel2(npc, sname, 1.0)
            from omnisim.systems.needs import socialize as _soc4
            _soc4(npc, 3.0)
        except Exception:
            pass
        npc.comps["activity"] = "trading"
        from omnisim.entities.items import coin_str as _cs3
        tab_note = f" (+{_cs3(tabbed)} on the tab)" if tabbed > 0 else ""
        village.say(f"{npc.name} unloads {tot} goods to {sname} (+{_cs3(earned)}){tab_note}.")
        try:  # STEP 6: income tracking (per-sale EMA, approximation of daily income)
            from omnisim.systems.decision import on as _d6ie_on
            if _d6ie_on() and tot > 0:
                _old6 = float(npc.comps.get("income_ema", None)
                              if npc.comps.get("income_ema", None) is not None
                              else float(earned))
                npc.comps["income_ema"] = 0.9 * _old6 + 0.1 * float(earned)
        except Exception:
            pass
    else:
        npc.comps["activity"] = "broke-stall"


def _try_buy(village, npc) -> None:  # type: ignore[no-untyped-def]
    from omnisim.core.actions import trade_price
    from omnisim.systems.skills import skill_level
    from omnisim.ui.sidebar import nearest_stall_obj
    stalls = [s for s in village.stalls  # CHAIN+: shoppers compare — full shelves
              if abs(s["x"] - npc.x) + abs(s["y"] - npc.y) <= 12]  # the whole square hears; beat thin-shelf gouging
    if not stalls:
        npc.comps["bought"] = True
        return
    trading = skill_level(npc, "Trading")
    pocket = npc.get("pocket", {})
    need = WORK_NEEDS.get(npc.get("work_skill") or "")
    from omnisim.systems.tools import FORGE_ORDER, SKILL_TOOL
    from omnisim.entities.items import staples as _staples
    try:
        _all = _staples()
        _prices = {s: float(item_defs()["items"].get(s, {}).get("price", 0))
                   for s in _all}
        _daily = _all[0]  # cheapest staple = the daily bread, whatever it is
        # Feast = costs twice the daily fare (notably dear, not daily).
        # Mid staples (bread, fish today) fill bins deep, ungated.
        _feast = [s for s in _all[1:] if _prices.get(s, 0) >= 2 * _prices.get(_daily, 1)]
        _mid = [s for s in _all[1:] if s not in _feast]
    except Exception:
        _daily, _mid, _feast = "bread", ["fish"], ["apple", "meal", "potted_meal"]
    my_tool = SKILL_TOOL.get(npc.get("work_skill") or "")
    wants = [w for w in ((need[0] if need else None), _daily) if w]
    hurt = int(npc.get("hp", 10)) < int(npc.get("max_hp", 10))
    if hurt:  # HERBS: pain outranks dinner — the hurting buy cures first
        wants.append("remedy")
    try:  # WEATHER: cold hearths outrank dinner — the freezing buy wood first.
        # Mild days buy NO wood: a standing wood want taxed dinner coin and
        # killed (day 7, twice). Woodpiles want haulers, not shoppers — later.
        from omnisim.systems.weather import chill_of as _chill
        if _chill(village) > 0 and npc.get("pocket", {}).get("firewood", 0) < 2:
            wants.insert(0, "firewood")
    except Exception:
        pass
    if (npc.get("work_skill") or "") == "Smithing":  # FUEL+ORE: heats before mending
        # The forge fires per_day_max heats (data, generic); each heat eats
        # its ore AND fuel qty. Want the whole day at dawn: heats x qty.
        # Fuel covers hearth (1) + heats: without it the forge sits idle and
        # mending (no inputs) eats the shift while the miner starves.
        try:
            from omnisim.systems.production import recipes as _ror
            _ore_ph = 1
            _fuel_ph = 1
            _heats = 2
            for _rr in _ror().values():
                if _rr.get("skill") == "Smithing" and "service" not in (_rr.get("flags", []) or []):
                    for _ii in (_rr.get("inputs", []) or []):
                        if _ii.get("good") == "iron_ore":
                            _ore_ph = max(_ore_ph, int(_ii.get("qty", 1)))
                        if _ii.get("good") == "firewood":
                            _fuel_ph = max(_fuel_ph, int(_ii.get("qty", 1)))
                    _heats = max(_heats, int(_rr.get("per_day_max", 2)))
            _day_need = _ore_ph * _heats
            _fuel_need = _fuel_ph * _heats + 1
        except Exception:
            _day_need = 4
            _fuel_need = 5
        if pocket.get("iron_ore", 0) < _day_need:
            wants.append("iron_ore")  # before feasting - no iron, no tools
        if pocket.get("firewood", 0) < _fuel_need:
            wants.insert(0, "firewood")  # heats + hearth before dinner
    if my_tool and my_tool not in (npc.get("tools", {}) or {}):
        wants.append(my_tool)  # TOOLS: bare hands buy iron before feasting
    if (npc.get("work_skill") or "") == "Farming":  # ROT: hungry soil orders compost
        from omnisim.systems.crops import ensure_plots as _ep
        if any(int(p.get("fert", 0)) < 3 for p in _ep(village)):
            wants.append("compost")
    if (npc.get("work_skill") or "") == "Farming":  # SEED: the chain seed
        try:
            from omnisim.systems.crops import choose_crop as _cc
            from omnisim.systems.crops import ensure_plots as _ep2
            from omnisim.systems.crops import seed_of as _so2
            from omnisim.systems.crops import ripe as _ripe2
            from omnisim.core.clock import day_of as _day2
            _plots2 = [p for p in _ep2(village)
                         if p.get("crop") is None or not _ripe2(p, _day2(village.clock_pts))]
            if _plots2:
                _want2 = _cc(village, _plots2[0])
                _seed2 = _so2(_want2)
                if pocket.get(_seed2, 0) < 1:
                    wants.insert(0, _seed2)
        except Exception:
            pass
    if (npc.get("work_skill") or "") == "Cooking":  # POTS: dinners that keep need pots
        # Trade tools ride FIRST: the oven cannot serve without its pot, the
        # way it cannot bake without flour. Foods break the loop on dearness,
        # so tools appended after the feast never get attempted (R4 keeps the
        # reserve; the gate order is need > tools > dinner).
        wants.insert(0, "pot")
    try:  # STEP 10: cold bodies want garments — any good whose data row
        # provides.insulation, cheapest first (never a named good).
        from omnisim.systems.needs3 import level_of as _n3_def
        from omnisim.systems.needs3 import on as _n3_on10
        from omnisim.systems.production import goods as _n3_goods10
        if _n3_on10() and float(_n3_def(npc, "cold")) > 0:
            _worn10 = (npc.get("worn", {}) or {}) if hasattr(npc, "get") else {}
            _have10 = set(_worn10.keys()) | {k for k, q in pocket.items() if q > 0}
            _gowns10 = sorted(
                (gid for gid, g in _n3_goods10().items()
                 if float((g.get("provides", {}) or {}).get("insulation", 0)) > 0
                 and gid not in _have10),
                key=lambda gid: float(item_defs()["items"].get(gid, {}).get("price", 0)))
            wants[0:0] = _gowns10
            # spares: one extra garment in the chest — replacements when
            # the worn one wears through (founders' stocks are finite)
            try:
                _worn_count10 = sum(1 for _g in _have10
                                    if float(((_n3_goods10().get(_g, {}) or {}).get("provides", {}) or {}).get("insulation", 0)) > 0)
                if _worn_count10 >= 1:
                    _cheap10 = sorted(
                        (gid for gid, g in _n3_goods10().items()
                         if float((g.get("provides", {}) or {}).get("insulation", 0)) > 0),
                        key=lambda gid: float(item_defs()["items"].get(gid, {}).get("price", 0)))
                    if _cheap10 and pocket.get(_cheap10[0], 0) < 1:
                        wants.insert(len(_gowns10), _cheap10[0])
            except Exception:
                pass
    except Exception:
        pass
    if (npc.get("work_skill") or "") == "Cooking":
        # The oven eats flour, ground or bought: shelved flour bakes today,
        # grain grinds first thing. Flour first (no quern tick), grain behind.
        wants.insert(0, "flour")
        wants.insert(1, "wheat")
        if npc.get("pocket", {}).get("wheat", 0) < 2:
            wants.insert(2, "barley")  # the second grain only when wheat runs thin
    wants += _mid + _feast
    shopping = wants
    # No wealth cliffs, no famine waits: pain must not exceed the value of
    # one unit. Hungry bellies value food highly and buy through dear
    # shelves; full ones walk past gouging. Same rule, every staple.
    from omnisim.systems.valuation import (price_pain as _pain,
                                           urgency as _urg)
    try:
        _hunger = float(npc.get("hunger", 0))
    except Exception:
        _hunger = 0.0
    _foods = set(_all) | {"berries", "herbs", "poultice"}
    for want in shopping:  # CHAIN+: makers buy their raw first, dinner second,
        try:  # HUNGER-FIRST (flagged): hungry holders skip non-food, non-input buys
            from omnisim.systems.hungerfirst import blocks_nonfood as _hfnb
            if _hfnb(npc, want):
                continue
        except Exception:
            pass
        if want in FORGE_ORDER:  # one good iron is a lifetime; bins and tables refill
            target = 1
        elif want == "remedy":  # HERBS: one cure in the pocket against the hurt
            target = 1 + (1 if int(npc.get("hp", 10)) <= 4 else 0)
        elif want == "compost":  # ROT: two handfuls feed the week
            target = 2
        elif want == "pot":  # POTS: two on the shelf against the sixth batch
            target = 2
        elif want in _feast:  # the dear stuff scales with the purse, smoothly
            target = min(2 + pocket.get("coin", 0) // 20, 8)
        else:
            target = 2 if want == _daily else 6  # the daily two; bins otherwise fill deep
        if pocket.get(want, 0) >= target:
            continue
        from omnisim.entities.items import bulk_of, cap_of, load_of
        deals = [(trade_price(want, trading, buying=True, stall=s, village=village), s) for s in stalls]
        deals = [(p, s) for p, s in deals if s["stock"].get(want, [0, 0])[0] > 0]
        if not deals:
            try:  # STEP 0: wanted goods, bare shelves — unmet demand
                from omnisim.systems.metrics import bump as _mbump2
                _mbump2(village, "declined_demand", want,
                         max(0, target - pocket.get(want, 0)), npc=npc.name)
            except Exception:
                pass
            continue
        try:  # STEP 5: trusted sellers win the pick (price/distance ride along)
            from omnisim.systems.memory import on as _m5p_on
            from omnisim.systems.memory import partner_score as _m5ps
            if _m5p_on():
                def _m5dist(_s):
                    try:
                        return max(abs(_s["x"] - npc.x), abs(_s["y"] - npc.y))
                    except Exception:
                        return 0.0
                price, stall = min(
                    deals,
                    key=lambda t: (_m5ps(npc, t[1].get("name", ""), want,
                                        t[0], village, _m5dist(t[1])), t[0]))
            else:
                price, stall = min(deals, key=lambda t: (t[0], t[1]["x"]))
        except Exception:
            price, stall = min(deals, key=lambda t: (t[0], t[1]["x"]))
        try:  # UAILS: the merchant's ask meets this soul's will (flag off: base)
            from omnisim.systems.haggle import deal_price as _dp2
            from omnisim.systems.haggle import merchant_ask as _ma2
            from omnisim.systems.haggle import merchant_of as _mo2
            _merch2 = _mo2(village)
            if _merch2 is not None:
                _ma2(village, _merch2, want, stall)  # replacement cost + margin
                price, _dealt2 = _dp2(village, _merch2, npc, want, price)
                if not _dealt2:
                    continue  # too dear today — walks past, both learned
        except Exception:
            pass
        stock = stall["stock"]
        try:  # urgency gate: pain relieved per copper, every channel.
            # garments pass on cold the way dinners pass on hunger.
            from omnisim.systems.haggle import urgency_of as _uof
            _uw = _uof(npc, want, village, _hunger)
        except Exception:
            _uw = 0.0
        try:  # R2 maker raw: own-trade recipe inputs at market ask.
            # All three conditions, generic (no named goods): the recipe
            # skill is your work_skill, its margin at market prices is >= 0
            # (R3 market-priced inputs), and the purse keeps dinner (R4).
            _is_raw = False
            from omnisim.systems.production import recipes as _rrw
            from omnisim.systems.production import trade_of as _tow
            from omnisim.systems.production import expected_sale_price as _esp2
            from omnisim.systems.production import _input_cost as _ic2
            from omnisim.systems.decision import can_afford_nonfood as _raf
            _tw = _tow(npc) or {}
            _rids = set(_tw.get("recipes", []) or [])
            for _r in _rrw().values():
                if _r.get("skill", "") != (npc.get("work_skill") or ""):
                    continue
                if _r.get("id", "") not in _rids and _rids:
                    continue
                _hit = any(_inp.get("good") == want for _inp in (_r.get("inputs", []) or []))
                if not _hit:
                    continue
                try:  # material margin at market asks: output value less
                    # input cost. Time cost deliberately EXCLUDED: a maker
                    # who must eat today and sell tomorrow still needs raw
                    # today even when her full-day mc-per-hour runs thin.
                    _gain2 = 0.0
                    for _o in (_r.get("outputs", []) or []):
                        _gain2 += float(_esp2(village, _o.get("good"))) * float(_o.get("qty", 1))
                    _cost2 = 0.0
                    _ok2 = True
                    for _inp in (_r.get("inputs", []) or []):
                        _gid2, _unit2 = _ic2(village, npc, _inp, market_ok=True)
                        if _gid2 is None:
                            _ok2 = False
                            break
                        _cost2 += float(_unit2) * float(_inp.get("qty", 1))
                    _mgn = (_gain2 - _cost2) if _ok2 else float("-inf")
                except Exception:
                    _mgn = float("-inf")
                try:  # R4 makers dip: own-trade inputs may spend INTO the
                    # reserve down to ONE meal (R4 text: "Needs may dip").
                    # Non-makers keep the full-reserve rule.
                    from omnisim.systems.decision import food_reserve as _fr2
                    _need2 = [(_r.get("skill", ""), None)][0][0]
                    _aff = bool(_raf(npc, price, village))
                    if not _aff:
                        try:
                            _one2 = float(_fr2(npc, village)) / 3.0
                            _aff = (float((npc.get("pocket", {}) or {}).get("coin", 0)) - float(price)) >= _one2
                        except Exception:
                            pass
                except Exception:
                    _aff = False
                try:
                    from omnisim.systems.tools import mend_price as _mp2
                    _tol2 = float(_mp2(village))
                except Exception:
                    _tol2 = 0.0
                if _mgn >= -_tol2 and _aff:
                    _is_raw = True
                    break
        except Exception:
            _is_raw = False
        if want not in _foods and _uw < 1.0 and not _is_raw:
            try:
                from omnisim.systems.production import goods as _wg
                _prov = ((_wg().get(want, {}) or {}).get("provides", {}) or {})
                if not _prov:
                    from omnisim.systems.crops import seed_of as _so3
                    from omnisim.systems.crops import kinds as _k3
                    _seeds3 = {_so3(k) for k in _k3()}
                    if want not in _seeds3:
                        continue  # no need behind it: dear durables wait
            except Exception:
                pass
        if want in _foods:  # overpaying needs hunger behind it — marginal call.
            # Dinner-for-later counts too (pantry floor, dearness-discounted):
            # sated shoppers stock fair shelves and walk past gouging.
            try:
                from omnisim.entities.items import food_value as _fv
                from omnisim.systems.pricing import scarcity_of as _scar
                from omnisim.systems.valuation import drives as _drv
                try:
                    _scarce = float(_scar(stall, want))
                except Exception:
                    _scarce = 1.0
                _floor = float(_drv().get("pantry_floor", 0.35)) / max(1.0, _scarce)
                _worth = max(_urg(_hunger, "hunger"), _floor) * float(_fv(npc, want, 1))
            except Exception:
                _worth = 0.0
            if _pain(price, pocket.get("coin", 0)) > _worth:
                break  # the rest of the list is dearer still — walk past
        try:  # R4: non-food buys keep the food reserve. Needs may dip
            # into it; wants and recipe inputs may not. Foods skip this.
            # Soil inputs (fertility providers) are NEEDS: hungry earth
            # orders before dinner the way dry bins do (R2 maker-first).
            _is_food_want = want in _foods
            if not _is_food_want:
                from omnisim.systems.decision import can_afford_nonfood as _r4ok
                from omnisim.systems.decision import on as _r4on
                try:
                    from omnisim.systems.production import goods as _wg4
                    _soil4 = bool(((_wg4().get(want, {}) or {}).get("provides", {}) or {}).get("fertility", 0))
                except Exception:
                    _soil4 = False
                try:  # RECIPE-BUY horizon exemption (same flag):
                    # food-recipe inputs up to H days rated use draw
                    # inside the reserve (rows, bounded, no credit).
                    from omnisim.systems.recipebuy import exempt_spend as _rb_ex
                    _rb_exempt = bool(_rb_ex(village, npc, want, price))
                except Exception:
                    _rb_exempt = False
                if _r4on() and not _soil4 and not _rb_exempt and not _r4ok(npc, price, village):
                    continue  # dinner first: the reserve is not for spending
        except Exception:
            pass
        bought = 0
        while pocket.get(want, 0) < target:  # bulk haul: one trip feeds days
            if load_of(pocket) + bulk_of(want) > cap_of(npc):
                break  # ECON: backs hold what training allows — shopping respects the spine
            if pocket.get("coin", 0) < price or stock.get(want, [0, 0])[0] <= 0:
                break
            try:  # R4 inside the haul: each extra unit re-checks it.
                # Soil inputs ride the outer bypass: hungry earth eats first.
                if not _is_food_want and not _soil4:
                    from omnisim.systems.decision import can_afford_nonfood as _r4ok2
                    if not _r4ok2(npc, price, village):
                        break
            except Exception:
                pass
            pocket["coin"] -= price
            stock[want][0] -= 1
            pocket[want] = pocket.get(want, 0) + 1
            stall["till"] = stall.get("till", 0) + price  # M5: coin reaches the till
            npc.comps["buys"] = int(npc.get("buys", 0)) + 1
            bought += 1
        if bought > 0:
            sname = stall.get("name", "a market stall")
            from omnisim.entities.items import coin_str as _cs4
            from omnisim.entities.items import item_name
            if want in FORGE_ORDER:  # TOOLS: bought iron leaves the pocket for the belt
                pocket.pop(want, None)
                npc.comps.setdefault("tools", {})[want] = 1  # crude, stall-bought, earned
                seen_say(village, npc.x, npc.y,
                         f"{npc.name} fits the {item_name(want)} to their hand.")
            seen_say(village, npc.x, npc.y,
                     f"{npc.name} buys {item_name(want)} from {sname} ({_cs4(price)}).")
            _record_trade(village, sname, npc.name, want, bought, price)
            try:  # STEP 6: spend tracking (budget EMA in decision engine)
                from omnisim.systems.decision import note_spend as _d6ns
                from omnisim.systems.decision import on as _d6ns_on
                if _d6ns_on():
                    _d6ns(npc, float(price) * float(bought))
            except Exception:
                pass
            try:  # FIFO LOTS: the merchant's oldest lots leave; the buyer's book takes them in
                from omnisim.systems.haggle import merchant_of as _mo4, note_lot as _nl2, pop_lots as _pl2
                _m4 = _mo4(village)
                if _m4 is not None:
                    _pl2(_m4, want, bought)
                _nl2(npc, want, bought, price)
            except Exception:
                pass
            try:  # V1 (GDD 8.3): fair trade builds relationship; chat feeds Social
                from omnisim.systems.relationships import adjust as _rel
                _rel(npc, sname, 1.0)
                from omnisim.systems.needs import socialize as _soc3
                _soc3(npc, 3.0)
            except Exception:
                pass
            from omnisim.core.clock import day_of
            from omnisim.entities.items import stamp_lore
            stamp_lore(npc, want, 1, "a stallholder", "a market stall", day_of(village.clock_pts))
            gain_xp(npc, "Trading", 1)
    try:  # PROCUREMENT (flag procurement, default off): remember
        # own completed buys (seller, price) for cover-triggered trips.
        from omnisim.systems.procurement import on as _pon
        from omnisim.systems.procurement import remember_seller as _rs
        if _pon() and bought > 0:
            _rs(npc, village, want, stall.get("name", "?"), price)
    except Exception:
        pass
    for _s in village.stalls:  # purchases fund the merchant's tabs first
        _settle_credit(village, _s)
    npc.comps["bought"] = True



