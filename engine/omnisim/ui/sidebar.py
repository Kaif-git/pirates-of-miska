"""Narrator sidebar — pure functions, no tcod. Returns segment lines.

A Line is a list of (text, fg, bg) runs. The renderer paints runs
left-to-right, so the sidebar reads as panels, key chips and solid bars —
not terminal soup. Tests and smoke use plain()/plain_profile().
"""
from __future__ import annotations
import textwrap

from omnisim.core.clock import day_of, hhmm_of, phase_of
from omnisim.entities.items import item_name, pack_of, pack_summary
from omnisim.systems.schedule import NOTEWORTHY
from omnisim.systems.skills import skill_xp_pct
from omnisim.world import tile as T
from omnisim.world.places import compass, describe_location

GOLD = (230, 200, 120)
DIM = (140, 140, 140)
TEXT = (220, 220, 220)
GREEN = (120, 220, 120)
YELLOW = (230, 220, 130)
RED = (230, 120, 120)
LOG = (215, 210, 170)
BLACK = (0, 0, 0)
PANEL = (14, 14, 24)       # sidebar body background: one calm slate
BAND = (30, 30, 62)        # section header bands
CHIP_FG = (20, 20, 20)     # key chips: dark on gold
TRACK = (45, 45, 55)       # empty bar track
WATCH_BG = (70, 60, 20)    # watched-NPC line highlight

# Icon glyphs: every one verified against the loaded font (Consolas) with a
# PIL tofu-check (see tests/test_react.py). Do NOT add icons without checking.
HP_ICON = "♥"
TOP_ICON = "▲"
WATCH_ICON = "●"
HOME_ICON = "⌂"
BULLET = "·"

Seg = tuple[str, tuple[int, int, int], tuple[int, int, int]]
Line = list[Seg]


def hp_status(hp: int, max_hp: int) -> tuple[str, tuple[int, int, int]]:
    if hp <= 0:
        return "Dying!", RED
    pct = hp / max_hp
    if pct >= 0.7:
        return "Healthy", GREEN
    if pct >= 0.35:
        return "Hurt", YELLOW
    return "Bloodied", RED


def hp_bar(hp: int, max_hp: int, width: int) -> Line:
    """Solid HP bar: filled cells in status color, the rest dark track."""
    _, status_color = hp_status(hp, max_hp)
    numbers = f"{hp}/{max_hp}"
    bar_len = width - len(HP_ICON) - 1 - 1 - len(numbers)
    if bar_len < 3:
        return [(f"{HP_ICON} {numbers}", status_color, PANEL)]
    fill = round(max(0, min(1, hp / max_hp)) * bar_len)
    return [(f"{HP_ICON} ", status_color, PANEL),
            (" " * fill, BLACK, status_color),
            (" " * (bar_len - fill), BLACK, TRACK),
            (f" {numbers}", status_color, PANEL)]


def xp_bar(pct: int, cells: int = 6) -> Line:
    """Mini solid XP bar (watch panel, profile cards)."""
    fill = round(max(0, min(100, pct)) / 100 * cells)
    return [(" " * fill, BLACK, (90, 180, 90)),
            (" " * (cells - fill), BLACK, TRACK)]


def visible_npcs(village) -> list:
    gm = village.game_map
    px, py = village.player.x, village.player.y
    out = []
    for e in village.npcs:
        if gm.in_bounds(e.x, e.y) and gm.visible[e.y, e.x]:
            dist = max(abs(e.x - px), abs(e.y - py))
            out.append((dist, e))
    out.sort(key=lambda t: (t[0], t[1].name))
    return out


def visible_places(village, here: str, limit: int = 4) -> list[tuple[str, str]]:
    """(place name, compass dir) for seen places other than where you stand."""
    gm = village.game_map
    px, py = village.player.x, village.player.y
    out = []
    for p in village.places:
        if p.name == here:
            continue
        seen = False
        for y in range(max(0, p.y1), min(gm.height, p.y2 + 1)):
            row = gm.visible[y, max(0, p.x1):min(gm.width, p.x2 + 1)]
            if row.any():
                seen = True
                break
        if seen:
            cx, cy = p.center()
            out.append((p.name, compass(cx - px, cy - py)))
    return out[:limit]


def nearest_stall_obj(village, px: int, py: int, reach: int = 2) -> dict | None:
    """The closest stall dict within reach, else None (M5: tills live here)."""
    best, best_d = None, reach + 1
    for s in village.stalls:
        d = max(abs(s["x"] - px), abs(s["y"] - py))
        if d <= reach and d < best_d:
            best, best_d = s, d
    return best


def nearest_stall(village, px: int, py: int, reach: int = 2) -> dict | None:
    """Stock dict of the closest stall within reach, else None."""
    s = nearest_stall_obj(village, px, py, reach)
    return s["stock"] if s is not None else None


def here_hints(village, limit: int = 2) -> list[str]:
    """Immediate, under-your-boots options, key first. Work lives in work_hint."""
    px, py = village.player.x, village.player.y
    gm = village.game_map
    out: list[str] = []
    here = gm.tile_id(px, py) if gm.in_bounds(px, py) else T.GRASS
    if here == T.BED:
        out.append("Z Sleep till dawn (bed)")
    if (px, py) in village.ground:
        out.append("g Pick up loot (at your feet)")
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            tx, ty = px + dx, py + dy
            if gm.in_bounds(tx, ty) and gm.tile_id(tx, ty) == T.DOOR and not gm.doors[ty, tx]:
                out.append("Bump the closed door open")
                break
    for e in village.npcs:
        if max(abs(e.x - px), abs(e.y - py)) == 1:
            out.append(f"Bump {e.name} to greet")
            break
    return out[:limit]


MENU_RADIUS = 8  # how far the action menu looks for opportunities


def _adjacent_tile(gm, px: int, py: int, tid: int) -> bool:
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if gm.in_bounds(px + dx, py + dy) and gm.tile_id(px + dx, py + dy) == tid:
                return True
    return False


def _nearby_tile(gm, px: int, py: int, tid: int, radius: int):
    """Closest (dist, dx, dy) tile of type within radius, skipping underfoot."""
    best = None
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            d = max(abs(dx), abs(dy))
            if d == 0 or not gm.in_bounds(px + dx, py + dy):
                continue
            if gm.tile_id(px + dx, py + dy) == tid and (best is None or d < best[0]):
                best = (d, dx, dy)
    return best


def work_hint(village) -> str | None:
    """One W-work suggestion: here-actions first, else nearest site in range."""
    from omnisim.entities.items import pack_of
    from omnisim.systems.skills import skill_level
    px, py = village.player.x, village.player.y
    gm = village.game_map
    pack = pack_of(village.player)
    full_cook = pack.get("wheat", 0) >= 2 and pack.get("bread", 0) >= 1
    half_cook = (not full_cook and skill_level(village.player, "Cooking") >= 4
                 and pack.get("wheat", 0) >= 1 and pack.get("bread", 0) >= 1)
    cooks = full_cook or half_cook
    if gm.tile_id(px, py) == T.FARM:
        return "W Farm wheat (here)"
    if _adjacent_tile(gm, px, py, T.ANVIL):
        from omnisim.systems.recipes import craftable, forge_line
        ready = craftable(village, "anvil")
        if ready:
            return forge_line(village.player, ready[0])
        return "W Forge at the anvil"
    if _adjacent_tile(gm, px, py, T.WORKBENCH):
        from omnisim.systems.recipes import craftable as _cb
        made = _cb(village, "workbench")
        if made:
            from omnisim.entities.items import item_name
            need = ", ".join(f"{q} {item_name(i)}" for i, q in (made[0].get("inputs", {}) or {}).items())
            return f"W Craft {made[0].get('name')} ({need})"
        return "W Mend tools (bench)"
    if any(p.name == "Smithy" and p.contains(px, py) for p in village.places):
        return "W Smith (here)"
    if nearest_stall(village, px, py) is not None:
        return "W Help a stallholder (+1c)"
    if _adjacent_tile(gm, px, py, T.TREE):
        from omnisim.systems.perks import perk_value
        from omnisim.systems.skills import skill_level as _sl
        from omnisim.systems.tools import best_tool as _bt
        if _bt("Woodcutting", village.player) < 1:
            return "W Gather branches (axe fells trunks)"
        if (perk_value(_sl(village.player, "Woodcutting"), "Woodcutting", "hardwood", False)
                and _bt("Woodcutting", village.player) < 3):
            return "W Fell a tree (hardwood wants axe q3+)"
        return "W Fell a tree"
    if _adjacent_tile(gm, px, py, T.WATER):
        from omnisim.systems.perks import perk_value as _pv
        from omnisim.systems.skills import skill_level as _sl2
        from omnisim.systems.tools import best_tool as _bt2
        if (_pv(_sl2(village.player, "Fishing"), "Fishing", "golden", False)
                and _bt2("Fishing", village.player) < 4):
            return "W Fish (golden wants rod q4+)"
        return "W Fish"
    if _adjacent_tile(gm, px, py, T.HEARTH) and cooks:
        return "W Cook a meal"
    from omnisim.systems.recipes import craftable as _cp
    if any(r["id"] == "poultice" for r in _cp(village)):
        return "W Brew poultice (2 apple)"
    cands = []  # (dist, verb, dx, dy)
    for tid, verb in ((T.FARM, "Farm wheat"), (T.ANVIL, "Forge"),
                      (T.WORKBENCH, "Mend tools"), (T.TREE, "Fell trees"),
                      (T.WATER, "Fish")):
        hit = _nearby_tile(gm, px, py, tid, MENU_RADIUS)
        if hit:
            cands.append((hit[0], verb, hit[1], hit[2]))
    if cooks:
        hit = _nearby_tile(gm, px, py, T.HEARTH, MENU_RADIUS)
        if hit:
            cands.append((hit[0], "Cook a meal", hit[1], hit[2]))
    for s in village.stalls:
        d = max(abs(s["x"] - px), abs(s["y"] - py))
        if 2 < d <= MENU_RADIUS:
            cands.append((d, "Help a stall (+1c)", s["x"] - px, s["y"] - py))
    if not cands:
        return None
    cands.sort(key=lambda c: (c[0], c[1]))
    d, verb, dx, dy = cands[0]
    return f"W {verb} ({compass(dx, dy)} {d})"


def trade_hints(village) -> list[str]:
    """T-buy and shift+T-sell lines when a stall is in reach."""
    from omnisim.core.actions import BUY_ORDER, SELLABLE, sell_price_for, trade_price
    from omnisim.entities.items import coin_str, lore_q, pack_of, unit_of
    from omnisim.systems.perks import perk_value
    from omnisim.systems.pricing import scarcity_of, trend_word
    from omnisim.systems.skills import skill_level
    stall = nearest_stall_obj(village, village.player.x, village.player.y)
    if stall is None:
        return []
    stock = stall["stock"]
    trading = skill_level(village.player, "Trading")
    trends = perk_value(trading, "Trading", "trends", False)  # L5 reads exact mults
    out = []
    for item_id in BUY_ORDER:
        qty, _ = stock.get(item_id, (0, 0))
        if qty > 0:
            price = trade_price(item_id, trading, True, stall, village=village)
            tag = ""
            word = trend_word(stall, item_id)
            if word:
                tag = f" ({word} x{scarcity_of(stall, item_id):.1f})" if trends else f" ({word})"
            out.append(f"T Buy {item_name(item_id)} {coin_str(price)} per {unit_of(item_id)}{tag}")
            break
    pack = pack_of(village.player)
    total, bits = 0, []
    for item_id in SELLABLE:
        q = pack.get(item_id, 0)
        if q > 0:
            price = sell_price_for(village.player, item_id, trading, stall, village=village)
            total += price * q
            tag = f" q{lore_q(village.player, item_id)}" if lore_q(village.player, item_id) > 1 else ""
            word = trend_word(stall, item_id)
            if word and trends:
                tag += f" {word}"
            bits.append(f"{item_name(item_id)}×{q}{tag}")
    if bits:
        out.append(f"Shift+T Sell {', '.join(bits)} +{coin_str(total)}")
    return out


def build_hints(village) -> list[str]:
    """M8: campfire/cabin/chest lines when the ground and pack allow."""
    from omnisim.entities.items import pack_of
    from omnisim.world import tile as T
    px, py = village.player.x, village.player.y
    gm = village.game_map
    if not gm.in_bounds(px, py):
        return []
    out = []
    from omnisim.systems.ownership import land_of
    land = land_of(village, px, py)
    if land is not None:
        good, owner = land
        out.append(f"{owner}'s ground — 1 in 4 stays with the land." if owner
                   else "Common ground — keep all you carry.")
    pack = pack_of(village.player)
    if gm.tile_id(px, py) == T.GRASS:
        if pack.get("firewood", 0) >= 4:
            out.append("b Build campfire (4 firewood)")
        cells = [(px + dx, py + dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
        clear = all(gm.in_bounds(x, y) and gm.tile_id(x, y) == T.GRASS for x, y in cells)
        if clear and pack.get("beam", 0) >= 8 and pack.get("firewood", 0) >= 20:
            out.append("B Build cabin (8 beams + 20 firewood)")
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            x, y = px + dx, py + dy
            if gm.in_bounds(x, y) and gm.tile_id(x, y) == T.CHEST and (x, y) in village.chests:
                if any(q > 0 for i, q in pack.items() if i != "coin"):
                    out.append("c Stash pack in chest")
                elif village.chests[(x, y)]["pack"]:
                    out.append("c Take from chest")
                break
    return out


def needs_of(village) -> list[tuple[str, float, str]]:  # type: ignore[no-untyped-def]
    """CHAIN: shelf truth per staple — (item, worst scarcity, trend word). Scarce first."""
    from omnisim.entities.items import item_name
    from omnisim.systems.schedule import scarcity_of_good
    from omnisim.systems.pricing import trend_word
    staples = ["bread", "meal", "wheat", "apple", "fish"]
    rows = []
    for item_id in staples:
        mult = scarcity_of_good(village, item_id)
        word = ""
        for s in village.stalls:
            if item_id in s["stock"]:
                word = trend_word(s, item_id)
                if word:
                    break
        rows.append((item_name(item_id), mult, word or "fair"))
    rows.sort(key=lambda r: -r[1])
    return rows


def village_board(village) -> list[str]:
    """CHAIN: the M key — wants, today's labor, market movement, where the work is."""
    from omnisim.core.clock import fmt_clock
    lines = [f"{village.name} — {fmt_clock(village.clock_pts)}", ""]
    lines.append("The village wants:")
    for name, mult, word in needs_of(village)[:3]:
        lines.append(f"  {name} ×{mult:.1f} ({word})")
    lines.append("")
    try:  # WEATHER: the sky on the board — cold changes what dinner costs
        from omnisim.systems.weather import chill_line as _chill
        _cl = _chill(village)
        if _cl:
            lines.append(_cl)
            lines.append("")
    except Exception:
        pass
    lines.append("Whose land:")
    plots = getattr(village, "plots", {}) or {}
    from omnisim.systems.ownership import LAND_NAMES
    for good, owner in plots.items():
        lines.append(f"  {LAND_NAMES.get(good, good)} — {owner if owner else 'commons'}")
    try:  # LAND: the Crown's office — deeds for sale, pond as biology
        from omnisim.entities.items import coin_str as _cs2
        from omnisim.systems.fishery import state_line as _pond
        from omnisim.systems.ownership import parcels_of as _parcels
        lines.append(f"  {_pond(village)}")
        for p in _parcels(village):
            if p.get("owner") == "Crown":
                lines.append(f"  {p['name']} — Crown's, {_cs2(int(p['price']))} ('buy {p['id']}')")
    except Exception:
        pass
    from omnisim.core.clock import day_of as _day
    from omnisim.systems.crops import field_line
    lines.append(field_line(village, _day(village.clock_pts)))
    lines.append("")
    lines.append("Today's labor:")
    if village.made_today:
        for item_id, rec in sorted(village.made_today.items(), key=lambda kv: -kv[1]["qty"]):
            from omnisim.entities.items import item_name
            lines.append(f"  +{rec['qty']} {item_name(item_id)} ({', '.join(rec['by'])})")
    else:
        lines.append("  nothing yet — the day is young")
    if village.sold_today:
        from omnisim.entities.items import item_name
        moved = ", ".join(f"{item_name(i)} ×{q}" for i, q in sorted(village.sold_today.items()))
        lines.append(f"Market moved: {moved}")
    trades = getattr(village, "trades", []) or []
    if trades:
        from omnisim.entities.items import coin_str as _cs
        from omnisim.entities.items import item_name as _in2
        from omnisim.entities.items import unit_of as _u2
        lines.append("Tape (latest first):")
        for t in trades[-3:][::-1]:
            lines.append(f"  {t['seller']} → {t['buyer']}: {t['qty']} {_in2(t['item'])} @ {_cs(t['price'])} per {_u2(t['item'])}")
    try:  # V1 Foundations (GDD 10.2): ledger queries on the board
        from omnisim.systems.ledger import ledger_lines
        lines.append("")
        lines.extend(ledger_lines(village))
    except Exception:
        pass
    lines.append("")
    lines.append("Where the work is:")
    px, py = village.player.x, village.player.y
    for s in nearest_work_sites(village):
        lines.append(f"  {s['name']} — {s['dir']} ({s['verb']})")
    lines.append("")
    lines.append("M/any key: back to the village")
    return lines


def nearest_work_sites(village) -> list[dict]:  # type: ignore[no-untyped-def]
    """Workplaces with compass directions (native twin of the web Places tab)."""
    from omnisim.world import tile as T
    gm = village.game_map
    px, py = village.player.x, village.player.y
    found = []

    def centroid(tid, name, verb):
        pts = [(x, y) for y in range(gm.height) for x in range(gm.width)
               if int(gm.tiles[y, x]) == tid]
        if pts:
            cx = sum(p[0] for p in pts) // len(pts)
            cy = sum(p[1] for p in pts) // len(pts)
            found.append({"name": name, "x": cx, "y": cy, "verb": verb,
                          "dir": compass(cx - px, cy - py)})

    def first(tid, name, verb):
        for y in range(gm.height):
            for x in range(gm.width):
                if int(gm.tiles[y, x]) == tid:
                    found.append({"name": name, "x": x, "y": y, "verb": verb,
                                  "dir": compass(x - px, y - py)})
                    return

    centroid(T.FARM, "Fields", "W farm wheat")
    from omnisim.core.clock import day_of as _day
    from omnisim.systems.crops import ensure_plots as _plots, ripe as _ripe
    _ps = _plots(village)
    _now = _day(village.clock_pts)
    _best = next((_p for _p in _ps if _ripe(_p, _now)), _ps[0] if _ps else None)
    if _best is not None:  # boots land on worked soil, ripe first
        for _f in found:
            if _f.get("name") == "Fields":
                _f["x"], _f["y"] = _best["x"], _best["y"]
                _f["dir"] = compass(_best["x"] - px, _best["y"] - py)
    posts = village.work_posts or {}
    if "firewood" in posts:  # a real clearing with an axe's reach of trees
        ax, ay = posts["firewood"]["at"]
        found.append({"name": "Wilds", "x": ax, "y": ay, "verb": "W fell trees for firewood",
                      "dir": compass(ax - px, ay - py)})
    else:
        centroid(T.TREE, "Wilds", "W fell trees for firewood")
    posts = village.work_posts or {}
    if "fish" in posts:  # the shore, not open water — legs can't swim
        ax, ay = posts["fish"]["at"]
        found.append({"name": "Stillpond shore", "x": ax, "y": ay, "verb": "W fish",
                      "dir": compass(ax - px, ay - py)})
    else:
        centroid(T.WATER, "Stillpond", "W fish")
    first(T.ANVIL, "Smithy anvil", "W forge / mend")
    first(T.WORKBENCH, "Store workbench", "W build beams")
    first(T.HEARTH, "Boar's hearth", "W cook meals")
    for p in village.places:
        if "tavern" in p.name.lower() or "boar" in p.name.lower():
            found.append({"name": p.name, "x": (p.x1 + p.x2) // 2,
                          "y": (p.y1 + p.y2) // 2, "verb": "beds, hearth, meals",
                          "dir": ""})
    posts = village.work_posts or {}
    for good, post in posts.items():
        from omnisim.entities.items import item_name
        ax, ay = post["at"]
        found.append({"name": f"{item_name(good)} post", "x": ax, "y": ay,
                      "verb": f"villagers work {post['skill'].lower()} here",
                      "dir": compass(ax - px, ay - py)})
    return found


def available_actions(village, limit: int = 6) -> list[str]:
    """The never-empty action menu: key + verb + target, closest first."""
    from omnisim.entities.items import best_food, open_of, pack_of
    from omnisim.systems.perks import unlocked_perks
    seen = set(village.player.comps.get("perk_seen", []))
    news = [p for p in unlocked_perks(village.player, min_level=2) if p not in seen]
    if news:
        village.player.comps["perk_seen"] = sorted(seen | set(news))
    out = [f"New perk: {p}" for p in news[:2]]
    out.extend(here_hints(village, limit=9))
    w = work_hint(village)
    if w is not None:
        out.append(w)
    from omnisim.core.actions import _adjacent_npc
    mark = _adjacent_npc(village)
    if mark is not None:  # closest first: the soul beside you outranks shopping
        if any(q > 0 for q in (mark.get("pocket", {}) or {}).values()):
            out.append(f"S Steal from {mark.name} (Sneaking)")
        _pp = pack_of(village.player)
        _po = open_of(village.player)
        if best_food(_pp, _po) is not None or _pp.get("coin", 0) > 0:
            out.append(f"V Give to {mark.name}")
    food = best_food(pack_of(village.player), open_of(village.player))  # dinner outranks shopping too
    if food is not None:
        out.append(f"e Eat {item_name(food)}")
    out.extend(trade_hints(village))
    from omnisim.systems.recipes import locked
    out.extend(locked(village)[:1])  # nearest grind target; watch must survive
    out.extend(build_hints(village))  # M8: campfire, cabin, chest
    px, py = village.player.x, village.player.y
    gm = village.game_map
    best, best_d = None, MENU_RADIUS + 1
    for n in village.npcs:
        if not gm.in_bounds(n.x, n.y) or not gm.visible[n.y, n.x]:
            continue
        d = max(abs(n.x - px), abs(n.y - py))
        if d < best_d:
            best, best_d = n, d
    if best is not None:
        out.append(f"i Inspect {best.name} ({compass(best.x - px, best.y - py)} {best_d})")
    else:
        out.append("i Inspect yourself")
    ids = [n.id for n in village.npcs if not n.comps.get("dead")]
    nxt = None
    if ids:
        nxt = ids[(ids.index(village.watch_id) + 1) % len(ids)] if village.watch_id in ids else ids[0]
    if nxt is not None:
        out.append(f"Tab Watch {next(n.name for n in village.npcs if n.id == nxt)}")
    watch = out[-1] if out and out[-1].startswith("Tab Watch") else None
    out = out[:limit]
    if watch is not None and watch not in out:
        for i, a in enumerate(out):
            if a.startswith(("Locked:", "S Steal", "V Give", "b Build", "B Build",
                             "c Stash", "c Take")):
                out[i] = watch  # situational lines yield so Watch survives
                break
    return out


def _hhmm(mins: int) -> str:
    return f"{(mins // 60) % 24:02d}:{mins % 60:02d}"


def profile_lines(village, npc) -> list[Line]:  # type: ignore[no-untyped-def]
    """Full inspect card: profession, home, now, every skill + xp bar, pocket, schedule."""
    from omnisim.systems.perks import unlocked_perks
    from omnisim.systems.skills import ALL_SKILLS, skill_level, skill_xp_pct, top_skill
    lines: list[Line] = []
    role = npc.get("role", "?")
    if role == "player":
        lines.append([(f"{npc.name} — traveller of Thornhollow", GOLD, BAND)])
        _top, _lvl = top_skill(npc)
        lines.append([(f"Profession: drifter {BULLET} {_top} {_lvl}", TEXT, PANEL)])
        lines.append([(f"Here: {describe_location(village, npc.x, npc.y)}", TEXT, PANEL)])
    else:
        lines.append([(f"{npc.name} the {role}", GOLD, BAND)])
        lines.append([(f"{npc.get('gender', '?')}, {npc.get('age', '?')}", TEXT, PANEL)])
        work_skill = npc.get("work_skill")
        if work_skill:
            try:  # V1 Foundations (GDD 7.1): emergent label from activity
                from omnisim.systems.professions import profession_of
                label = profession_of(npc)
            except Exception:
                label = str(role)
            lines.append([(f"Profession: {label} {BULLET} {work_skill} {skill_level(npc, work_skill)}", TEXT, PANEL)])
        home = next((b["at"] for b in npc.comps.get("sched", []) if b["act"] == "sleep"), None)
        if home is not None:
            lines.append([(f"{HOME_ICON} lives {describe_location(village, home[0], home[1])}", TEXT, PANEL)])
        lines.append([(f"Now: {npc.get('activity', 'idle')} @ {describe_location(village, npc.x, npc.y)}", TEXT, PANEL)])
        if npc.comps.get("dead"):
            lines.append([("Dead.", RED, PANEL)])
        elif float(npc.get("hunger", 0)) >= 100:
            lines.append([("Starving!", RED, PANEL)])
        elif float(npc.get("hunger", 0)) > 70:
            lines.append([("Hungry", YELLOW, PANEL)])
        if not npc.comps.get("dead") and float(npc.get("fatigue", 0)) > 70:
            lines.append([("Weary", YELLOW, PANEL)])
        if not npc.comps.get("dead"):  # water and washing, legible
            try:
                _th = float(npc.get("thirst", 0))
                if _th >= 100:
                    lines.append([("Parched!", RED, PANEL)])
                elif _th > 70:
                    lines.append([("Thirsty", YELLOW, PANEL)])
                _fl = float(npc.get("filth", 0))
                if _fl >= 70:
                    lines.append([("Filthy", YELLOW, PANEL)])
                elif _fl >= 40:
                    lines.append([("Grubby", DIM, PANEL)])
            except Exception:
                pass
        if not npc.comps.get("dead"):  # V1 Wants (GDD 3.2): legible hearts
            try:
                _soc = float(npc.get("social", 70.0))
                _est = float(npc.get("esteem", 60.0))
                if _soc < 30:
                    lines.append([("Lonely", YELLOW, PANEL)])
                elif _soc < 55:
                    lines.append([("Wistful", DIM, PANEL)])
                if _est >= 85:
                    lines.append([("Esteemed", GOLD, PANEL)])
                elif _est < 30:
                    lines.append([("Disheartened", DIM, PANEL)])
            except Exception:
                pass
    lines.append([("Skills:", GOLD, BAND)])
    for name in ALL_SKILLS:
        _pct = skill_xp_pct(npc, name)
        lines.append([(f"  {name} {skill_level(npc, name)} ", DIM, PANEL),
                      *xp_bar(_pct), (f" {_pct}%", DIM, PANEL)])
    for p in unlocked_perks(npc, min_level=2):
        lines.append([(f"  {BULLET} {p}", DIM, PANEL)])
    pocket = npc.get("pocket", {}) or {}
    bits = ", ".join(f"{item_name(i)} ×{q}" for i, q in sorted(pocket.items()) if q > 0)
    lines.append([(f"Pocket: {bits or 'empty'}", DIM, PANEL)])
    from omnisim.entities.items import lore_line, pack_of
    from omnisim.systems.tools import kit_line
    kit = kit_line(npc)
    if kit:
        lines.append([(kit, DIM, PANEL)])
    goods = pocket if role != "player" else pack_of(npc)
    for i, q in sorted(goods.items()):
        if q > 0:
            ll = lore_line(npc, i, q)
            if ll:
                lines.append([(f"  {ll}", DIM, PANEL)])
    return lines


def inspectable_pocket(npc) -> list[tuple[str, int]]:  # type: ignore[no-untyped-def]
    """Steal-scan rows: (item, qty) in lift order. Letters are assigned by the overlay."""
    from omnisim.core.actions import _steal_order
    pocket = npc.get("pocket", {}) or {}
    order = _steal_order()
    rows = [(i, pocket[i]) for i in order if pocket.get(i, 0) > 0]
    rows += sorted((i, q) for i, q in pocket.items() if q > 0 and i not in order)
    return rows


def pack_tags(pack: dict, entity=None) -> str:  # type: ignore[no-untyped-def]
    """Inventory with key tags: [e] edible, [T] sellable at a stall, q3+ quality."""
    from omnisim.core.actions import SELLABLE
    from omnisim.entities.items import defs, lore_q
    items = defs()["items"]
    bits = []
    for item_id, qty in sorted(pack.items()):
        if qty <= 0:
            continue
        if item_id == "coin":
            bits.append(f"coin {qty}")
            continue
        tag = ""
        if items.get(item_id, {}).get("food", 0) > 0:
            tag += " [e]"
        if item_id in SELLABLE:
            tag += " [T]"
        if entity is not None and lore_q(entity, item_id) > 1:
            tag += f" q{lore_q(entity, item_id)}"
        bits.append(f"{item_name(item_id)}×{qty}{tag}")
    return ", ".join(bits) if bits else "empty"


def _split_key(action: str) -> tuple[str | None, str]:
    """Menu 'KEY rest' -> (KEY, rest). Keyless lines (bump-notes) -> (None, all)."""
    key, _, rest = action.partition(" ")
    if len(key) == 1 or key in ("Tab", "Shift+T"):
        return key, rest
    return None, action


def build_sidebar(village, width: int, height: int) -> list[Line]:
    lines: list[Line] = []

    def add(text: str, fg: tuple[int, int, int] = TEXT, bg: tuple[int, int, int] = PANEL) -> None:
        for part in textwrap.wrap(text, width) or [""]:
            lines.append([(part, fg, bg)])

    def addsegs(*segs: Seg) -> None:
        lines.append(list(segs))

    def band(text: str, fg: tuple[int, int, int] = GOLD) -> None:
        lines.append([(text.ljust(width), fg, BAND)])

    def add_menu(action: str) -> None:
        key, rest = _split_key(action)
        if key is None:
            add(f"» {rest}", YELLOW)
            return
        chip = f" {key} "
        for i, part in enumerate(textwrap.wrap(rest, width - len(chip) - 1) or [""]):
            if i == 0:
                lines.append([(chip, CHIP_FG, GOLD), (" " + part, YELLOW, PANEL)])
            else:
                lines.append([(" " * (len(chip) + 1) + part, YELLOW, PANEL)])

    px, py = village.player.x, village.player.y
    hp = int(village.player.get("hp", 10))
    max_hp = int(village.player.get("max_hp", 10))
    status, status_color = hp_status(hp, max_hp)
    where = describe_location(village, px, py)
    under = T.tile_name(village.game_map.tile_id(px, py)) if village.game_map.in_bounds(px, py) else "void"

    band(village.name)
    add(f"Day {day_of(village.clock_pts)} · {hhmm_of(village.clock_pts)}", GOLD)
    add(phase_of(village.clock_pts), DIM)
    band("You")
    addsegs(*hp_bar(hp, max_hp, width))
    add(status, status_color)
    add(f"ATK {village.player.get('atk', 1)}   SPD {village.player.get('speed', 100)}", TEXT)
    from omnisim.systems.skills import top_skill
    _pskills = village.player.get("skills", {}) or {}
    _pxp = village.player.get("skillxp", {}) or {}
    if _pskills:
        _tops = sorted(_pskills.items(), key=lambda kv: (kv[1], _pxp.get(kv[0], 0)), reverse=True)[:3]
        add(f"{TOP_ICON} " + " · ".join(f"{n} {l}" for n, l in _tops), DIM)
    else:
        _top, _lvl = top_skill(village.player)
        add(f"{TOP_ICON} {_top} {_lvl}", DIM)
    hunger = float(village.player.get("hunger", 0))
    if hunger >= 100:
        add("Starving!", RED)
    elif hunger > 70:
        add("Hungry", YELLOW)
    elif hunger > 25:
        add("Peckish", DIM)
    thirst = float(village.player.get("thirst", 0))
    if thirst >= 100:
        add("Parched!", RED)
    elif thirst > 70:
        add("Thirsty", YELLOW)
    elif thirst > 25:
        add("Dry", DIM)
    try:
        _pfilth = float(village.player.get("filth", 0))
        if _pfilth >= 70:
            add("Filthy", YELLOW)
    except Exception:
        pass
    try:  # V1 Wants (GDD 3.2): your heart, legible
        _psoc = float(village.player.get("social", 70.0))
        _pest = float(village.player.get("esteem", 60.0))
        if _psoc < 30:
            add("Lonely", YELLOW)
        if _pest >= 85:
            add("Esteemed", GOLD)
    except Exception:
        pass
    add(f"Pack: {pack_tags(pack_of(village.player), village.player)}", DIM)
    watched = next((n for n in village.npcs if n.id == village.watch_id), None)
    if watched is not None:
        add(f"{WATCH_ICON} {watched.name} the {watched.get('role', '?')}", GOLD)
        add(f"{watched.get('activity', 'idle')} @ {describe_location(village, watched.x, watched.y)}", TEXT)
        _skills = watched.get("skills", {}) or {}
        _xp = watched.get("skillxp", {}) or {}
        for _n, _l in sorted(_skills.items(), key=lambda kv: (kv[1], _xp.get(kv[0], 0)), reverse=True)[:3]:
            _pct = skill_xp_pct(watched, _n)
            _label = f"{_n} {_l} "
            if len(_label) + 6 + len(f" {_pct}%") <= width:
                lines.append([(_label, DIM, PANEL), *xp_bar(_pct), (f" {_pct}%", DIM, PANEL)])
            else:
                add(f"{_n} {_l} {_pct}%", DIM)
        _hunger = float(watched.get("hunger", 0))
        add("Starving!" if _hunger >= 100 else "Hungry" if _hunger > 70 else "Peckish" if _hunger > 25 else "Full", DIM)
    band("Where")
    add(where, TEXT)
    add(f"Under: {under}", DIM)
    stall = nearest_stall(village, px, py)
    if stall is not None:
        from omnisim.entities.items import coin_str as _cs2
        goods = ", ".join(f"{item_name(i)} {_cs2(p)}" for i, (q, p) in sorted(stall.items()) if q > 0)
        add(f"Stall: {goods} (T buys)", DIM)
    band("You can:")
    for a in available_actions(village):
        add_menu(a)
    band("You see:")
    seen = visible_npcs(village)
    if not seen:
        add("No one in sight.", DIM)
    for dist, e in seen[:6]:
        _act = e.get("activity", "idle")
        _suffix = f" · {_act}" if _act in NOTEWORTHY else ""
        _text = f"{e.name} the {e.get('role', '?')} ({compass(e.x - px, e.y - py)}, {dist}){_suffix}"
        if e.id == village.watch_id:
            lines.append([(_text.ljust(width), TEXT, WATCH_BG)])
        else:
            add(_text)
    if len(seen) > 6:
        add(f"+{len(seen) - 6} more...", DIM)
    for name, direction in visible_places(village, where):
        add(f"{name} ({direction})", DIM)

    band("Log:")
    for msg in village.log[-99:]:
        for part in textwrap.wrap(msg, width) or [""]:
            lines.append([(part, LOG, PANEL)])

    # newest log last, but keep the top: trim oldest log lines to fit.
    if len(lines) > height:
        texts = ["".join(s[0] for s in line) for line in lines]
        keep_head = next(i for i, t in enumerate(texts) if t.strip() == "Log:")
        lines = lines[:keep_head + 1] + lines[-(height - keep_head - 1):]
    while len(lines) < height:
        lines.append([("", TEXT, PANEL)])
    return lines[:height]


def plain(village, width: int, height: int) -> list[str]:
    """Sidebar as plain strings (tests, smoke, screenshots-grep)."""
    return ["".join(seg[0] for seg in line) for line in build_sidebar(village, width, height)]


def plain_profile(village, npc) -> list[str]:  # type: ignore[no-untyped-def]
    """Profile card as plain strings."""
    return ["".join(seg[0] for seg in line) for line in profile_lines(village, npc)]




