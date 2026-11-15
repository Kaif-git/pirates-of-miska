"""Player actions — pure logic, no tcod. Tested headlessly.

Every action pays move points on the shared clock (see core/clock.py).
Bumping a closed door spends the step to heave it open; the next step
walks through. NPC greetings are free — words are cheap.
"""
from __future__ import annotations
from omnisim.core.clock import (
    COST_EAT, COST_GET, COST_OPEN, COST_STEP, COST_WAIT,
    action_cost, advance_clock, day_of,
)
from omnisim.core.clock import COST_WORK
from omnisim.entities.items import (
    add_item, best_food, defs as item_defs, food_mult, item_name, lore_of, lore_q,
    pack_of, q_mult, stamp_lore, stow, take_item, yield_q,
)
from omnisim.systems.skills import gain_xp, skill_level
from omnisim.world import tile as T
from omnisim.world.worldgen_scripted import Village


def bank_player(v: Village, item_id: str, qty: int, origin: str) -> int:
    """ECON: the traveller's back holds what training allows; the land takes due.

    Returns the leftover that landed at the player's feet."""
    from omnisim.systems.ownership import drop_at, pay_tithe
    pay_tithe(v, v.player, item_id, qty)
    from omnisim.entities.items import cap_of
    left = stow(pack_of(v.player), item_id, qty, cap_of(v.player))
    if left > 0:
        drop_at(v, v.player.x, v.player.y, item_id, left, "You", origin)
    return left


def try_move(v: Village, dx: int, dy: int) -> str:
    """Move player by (dx, dy). Returns a log message (empty = quiet move)."""
    nx, ny = v.player.x + dx, v.player.y + dy
    if not v.game_map.in_bounds(nx, ny):
        return "You cannot leave the world. Not yet."
    tid = v.game_map.tile_id(nx, ny)
    if tid == T.DOOR and not v.game_map.is_open_door(nx, ny):
        v.game_map.doors[ny, nx] = True
        try:
            from omnisim.systems.schedule import invalidate_bfs_cache as _ib3
            _ib3()
        except Exception:
            pass
        advance_clock(v, action_cost(COST_OPEN, v.player.comps, "walk"))
        v.turn += 1
        return "You heave the door open."
    if not v.game_map.is_walkable(nx, ny):
        return f"Blocked by {T.tile_name(tid)}."
    other = v.entity_at(nx, ny)
    if other is not None and other is not v.player:
        for lv in gain_xp(v.player, "Speech", 1):
            v.say(f"Speech rises to {lv}!")
        try:  # V1 Wants (GDD 3.2): greetings feed Social, both ways
            from omnisim.systems.needs import socialize as _soc
            _soc(v.player, 8.0)
            _soc(other, 8.0)
        except Exception:
            pass
        return f"You greet {other.name} the {other.get('role', 'someone')}. (They stand still... for now.)"
    v.player.x, v.player.y = nx, ny
    v.turn += 1
    v.player.comps["activity"] = "walking"  # GDD 3.1: legs burn hotter
    for lv in gain_xp(v.player, "Endurance", 1):
        v.say(f"Endurance rises to {lv}!")
    if not any(max(abs(n.x - nx), abs(n.y - ny)) <= 6 for n in v.npcs):
        from omnisim.systems.perks import perk_value
        from omnisim.systems.skills import skill_level as _sl
        sneak_gain = 2 if perk_value(_sl(v.player, "Sneaking"), "Sneaking", "solitude_x2", False) else 1
        for lv in gain_xp(v.player, "Sneaking", sneak_gain):
            v.say(f"Sneaking rises to {lv}!")
    advance_clock(v, action_cost(COST_STEP, v.player.comps, "walk"))
    return ""


def wait(v: Village) -> str:
    v.turn += 1
    v.player.comps["activity"] = "idle"
    advance_clock(v, COST_WAIT)
    return "You wait a while (10 min)."


def eat(v: Village, prefer: str | None = None) -> str:
    """Eat the best food in the pack (or your pick, from the pack screen)."""
    pack = pack_of(v.player)
    max_hp = int(v.player.get("max_hp", 20))
    if prefer == "remedy" or (prefer is None and pack.get("remedy", 0) > 0
                              and int(v.player.get("hp", 20)) <= max_hp // 2):
        cured = int(item_defs()["items"].get("remedy", {}).get("heal", 0))
        take_item(pack, "remedy", 1)
        v.player.comps["hp"] = min(max_hp, int(v.player.get("hp", 20)) + cured)
        advance_clock(v, COST_EAT)
        v.turn += 1
        return f"You quaff the brewed remedy. (+{cured} HP)"
    from omnisim.entities.items import open_of as _open_of
    from omnisim.entities.items import servings_in as _servings_in
    from omnisim.entities.items import take_serving as _take_serving
    _open = _open_of(v.player)
    _has_prefer = prefer is not None and _servings_in(pack, _open, prefer) > 0
    fid = prefer if _has_prefer else best_food(pack, _open)
    if fid is None:
        return "You have nothing edible." if prefer is None else f"No {prefer} to eat."
    if float(v.player.get("hunger", 0)) <= 0:
        return "You are full."
    food = item_defs()["items"][fid]
    _take_serving(pack, _open, fid)
    from omnisim.entities.items import food_value as _food_value
    ate = _food_value(v.player, fid, lore_q(v.player, fid))
    v.player.comps["hunger"] = max(0.0, float(v.player.get("hunger", 0)) - ate)
    try:
        from omnisim.systems.needs import record_meal as _record_meal
        _record_meal(v.player, fid)
    except Exception:
        pass
    cooking = int(v.player.get("Cooking", 1))
    from omnisim.systems.tools import best_tool
    healed = (food.get("heal", 0) + cooking // 2 + best_tool("Cooking", v.player) // 2
              + ((lore_q(v.player, fid) - 1) if food.get("heal", 0) > 0 else 0))
    if healed:
        v.player.comps["hp"] = min(int(v.player.get("max_hp", 20)),
                                   int(v.player.get("hp", 20)) + healed)
    advance_clock(v, COST_EAT)
    v.player.comps["activity"] = "eating"  # set after the tick: this bite cost idle time
    v.turn += 1
    return f"You eat the {food['name']}. (-{ate:g} hunger, +{healed} HP)"


def get_items(v: Village) -> str:
    """Scoop up everything lying at your feet."""
    key = (v.player.x, v.player.y)
    loot = v.ground.get(key)
    if not loot:
        return "Nothing here to pick up."
    pack = pack_of(v.player)
    bits, heavy = [], []
    from omnisim.entities.items import cap_of
    for item_id, qty in sorted(loot.items()):
        left = stow(pack, item_id, qty, cap_of(v.player))
        got = qty - left
        if got > 0:
            gl = v.ground_lore.get(key, {}).get(item_id, {})
            stamp_lore(v.player, item_id, 1, gl.get("by", "unknown"), gl.get("origin", "the wilds"),
                       day_of(v.clock_pts))
            bits.append(f"{got} {item_name(item_id)}")
        if left > 0:
            loot[item_id] = left
            heavy.append(f"{left} {item_name(item_id)}")
        else:
            loot.pop(item_id, None)
            v.ground_lore.get(key, {}).pop(item_id, None)
    if not loot:
        del v.ground[key]
        v.ground_lore.pop(key, None)
    advance_clock(v, COST_GET)
    v.turn += 1
    msg = "You pick up: " + (", ".join(bits) if bits else "nothing — your back is full") + "."
    if heavy:
        msg += " Too heavy to carry: " + ", ".join(heavy) + "."
    return msg

WORK_CD_MIN = 5  # a worked spot needs a breather


def _cooled(v: Village, key: tuple) -> bool:
    return v.clock_pts >= v.work_cd.get(key, 0)


def _adjacent_tile(gm, px: int, py: int, tid: int) -> bool:
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if gm.in_bounds(px + dx, py + dy) and gm.tile_id(px + dx, py + dy) == tid:
                return True
    return False


def _stamp_cd(v: Village, key: tuple) -> None:
    _stamp_cd_mins(v, key, WORK_CD_MIN)


def _stamp_cd_mins(v: Village, key: tuple, mins: float) -> None:
    from omnisim.core.clock import PTS_PER_MIN
    v.work_cd[key] = v.clock_pts + mins * PTS_PER_MIN


def _fresh_since_dawn(v: Village, key: tuple, cd_mins: float) -> bool:
    """True if the site hasn't been worked since 06:00 (M1 double rows)."""
    from omnisim.core.clock import PTS_PER_MIN
    dawn = (v.clock_pts // (24 * 60 * PTS_PER_MIN)) * (24 * 60 * PTS_PER_MIN) + 6 * 60 * PTS_PER_MIN
    return v.work_cd.get(key, 0) - cd_mins * PTS_PER_MIN < dawn


def _reward(v: Village, skill: str, text: str) -> str:
    return _reward_cost(v, skill, text, COST_WORK)


def _reward_cost(v: Village, skill: str, text: str, cost: int) -> str:
    """XP + clock for honest work. Endurance L5 second wind lightens it."""
    from omnisim.systems.perks import perk_value
    from omnisim.systems.skills import skill_level
    discount = perk_value(skill_level(v.player, "Endurance"), "Endurance", "work_discount", 0)
    for lv in gain_xp(v.player, skill, 1):
        v.say(f"{skill} rises to {lv}!")
    site = _site_label(v)  # WEB: rhythm combos — same site, chained work
    if site == v.player.get("rhythm_site"):
        v.player.comps["rhythm_n"] = int(v.player.get("rhythm_n", 0)) + 1
    else:
        v.player.comps["rhythm_site"] = site
        v.player.comps["rhythm_n"] = 1
    if v.player.get("rhythm_n", 1) % 4 == 0:
        for lv in gain_xp(v.player, skill, 1):
            v.say(f"{skill} rises to {lv}!")
        text += " Flow pays off! (+1 bonus XP)"
    elif v.player.get("rhythm_n", 0) == 2:
        v.say("You're finding a flow. (Chain tasks here — every 4th banks +1 XP. Sleep breaks it.)")
    advance_clock(v, max(100, cost - discount))
    v.player.comps["activity"] = "working"  # after the tick: next shift burns hotter
    v.turn += 1
    return text


def _site_label(v: Village) -> str:
    """Where the hands are: one label per work branch (WEB rhythm + panels)."""
    px, py = v.player.x, v.player.y
    gm = v.game_map
    if gm.in_bounds(px, py) and gm.tile_id(px, py) == T.FARM:
        return "field"
    if _adjacent_tile(gm, px, py, T.ANVIL):
        return "anvil"
    if _adjacent_tile(gm, px, py, T.WORKBENCH):
        return "workbench"
    for p in v.places:
        if p.name == "Smithy" and p.contains(px, py):
            return "smithy"
    from omnisim.ui.sidebar import nearest_stall
    if nearest_stall(v, px, py) is not None:
        return "stall"
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            tx, ty = px + dx, py + dy
            if gm.in_bounds(tx, ty) and gm.tile_id(tx, ty) == T.TREE:
                return "wilds"
            if gm.in_bounds(tx, ty) and gm.tile_id(tx, ty) == T.WATER:
                return "pond"
    if _adjacent_tile(gm, px, py, T.HEARTH):
        return "hearth"
    return "odd-jobs"


def work_options(v: Village) -> list[dict]:
    """WEB: every honest task available where you stand, with reqs and yields."""
    from omnisim.systems.perks import perk_value
    from omnisim.systems.skills import skill_level
    from omnisim.systems.tools import best_tool
    from omnisim.ui.sidebar import nearest_stall
    px, py = v.player.x, v.player.y
    gm = v.game_map
    pack = pack_of(v.player)
    out = []
    if gm.in_bounds(px, py) and gm.tile_id(px, py) == T.FARM:
        fl = skill_level(v.player, "Farming")
        out.append({"id": "farm", "site": "field", "skill": "Farming",
                    "label": "Tend the rows",
                    "detail": f"+{perk_value(fl, 'Farming', 'yield_min', 1)}-{perk_value(fl, 'Farming', 'yield_max', 2)} grain, hoe q{best_tool('Farming', v.player)}",
                    "ready": _cooled(v, ("field", px, py))})
    if _adjacent_tile(gm, px, py, T.ANVIL):
        from omnisim.systems.recipes import craftable
        for r in craftable(v, "anvil"):
            out.append({"id": f"craft:{r['id']}", "site": "anvil", "skill": r.get("skill", "Smithing"),
                        "label": f"Forge: {r['name']}",
                        "detail": _recipe_detail(r), "ready": True})
        out.append({"id": "mend", "site": "anvil", "skill": "Smithing",
                    "label": "Mend a hinge", "detail": "+1-2 coin",
                    "ready": _cooled(v, ("smithy",))})
        try:  # GDD 5.2: worn shops can be trued (1 firewood, Smithing hands)
            from omnisim.systems.workspaces import state_of as _wst
            worn = [(k, float(_wst(v, k).get("condition", 100.0)))
                    for k in ("Smithing", "Carpentry", "Cooking", "Farming")]
            worst = min(worn, key=lambda kv: kv[1])
            if worst[1] < 100.0:
                out.append({"id": "repair", "site": "anvil", "skill": "Smithing",
                            "label": f"True the {worst[0].lower()} shop",
                            "detail": f"{int(worst[1])}% → +repair (1 firewood)",
                            "ready": pack.get("firewood", 0) >= 1})
        except Exception:
            pass
    if _adjacent_tile(gm, px, py, T.WORKBENCH):
        from omnisim.systems.recipes import craftable
        for r in craftable(v, "workbench"):
            out.append({"id": f"craft:{r['id']}", "site": "workbench", "skill": r.get("skill", "Smithing"),
                        "label": f"Build: {r['name']}",
                        "detail": _recipe_detail(r), "ready": True})
        out.append({"id": "mend-tools", "site": "workbench", "skill": "Trading",
                    "label": "Mend tools for a neighbour", "detail": "+1 coin", "ready": True})
    if nearest_stall(v, px, py) is not None:
        out.append({"id": "hawk", "site": "stall", "skill": "Trading",
                    "label": "Hawk goods for a stallholder", "detail": "+1 coin tip",
                    "ready": _cooled(v, ("stall",))})
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            tx, ty = px + dx, py + dy
            if gm.in_bounds(tx, ty) and gm.tile_id(tx, ty) == T.TREE:
                out.append({"id": "fell", "site": "wilds", "skill": "Woodcutting",
                            "label": "Fell a tree",
                            "detail": f"+{perk_value(skill_level(v.player, 'Woodcutting'), 'Woodcutting', 'yield_min', 1)}+ firewood, axe q{best_tool('Woodcutting', v.player)}",
                            "ready": True})
                break
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            tx, ty = px + dx, py + dy
            if gm.in_bounds(tx, ty) and gm.tile_id(tx, ty) == T.WATER:
                out.append({"id": "fish", "site": "pond", "skill": "Fishing",
                            "label": "Cast a line",
                            "detail": f"+{perk_value(skill_level(v.player, 'Fishing'), 'Fishing', 'catch', 1)} fish, rod q{best_tool('Fishing', v.player)}",
                            "ready": True})
                break
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            tx, ty = px + dx, py + dy
            if gm.in_bounds(tx, ty) and gm.tile_id(tx, ty) == T.GRASS:
                out.append({"id": "gather", "site": "meadow", "skill": "Herbalism",
                            "label": "Gather wild herbs",
                            "detail": f"+{2 + perk_value(skill_level(v.player, 'Herbalism'), 'Herbalism', 'gather_bonus', 0)} herbs, knife q{best_tool('Herbalism', v.player)}",
                            "ready": True})
                break
    if _adjacent_tile(gm, px, py, T.HEARTH) and pack.get("bread", 0) >= 1 \
            and pack.get("wheat", 0) + pack.get("flour", 0) >= 1:
        out.append({"id": "cook", "site": "hearth", "skill": "Cooking",
                    "label": "Cook a hot meal",
                    "detail": "grain + 1 bread (thin pot at Cooking 4)", "ready": True})
    if _adjacent_tile(gm, px, py, T.HEARTH) and pack.get("wheat", 0) >= 1 \
            and not (pack.get("bread", 0) >= 1
                     and pack.get("wheat", 0) + pack.get("flour", 0) >= 1):
        out.append({"id": "grind", "site": "hearth", "skill": "Cooking",
                    "label": "Grind wheat to flour",
                    "detail": "raw grain is not food — mill it first", "ready": True})
    return out


def _recipe_detail(r: dict) -> str:
    bits = ", ".join(f"{q} {n}" for n, q in (r.get("inputs", {}) or {}).items())
    outs = ", ".join(f"{q} {n}" for n, q in (r.get("outputs", {}) or {}).items())
    return f"{bits} -> {outs}" if bits else outs


def work(v: Village) -> str:
    """Do the honest thing wherever you stand: farm, forge, stall, axe, rod,
    or cook a meal from wheat + bread. Shift+W. Costs 3 min on success."""
    from omnisim.systems.perks import perk_value
    from omnisim.systems.skills import skill_level
    from omnisim.systems.tools import best_tool
    from omnisim.ui.sidebar import nearest_stall
    px, py = v.player.x, v.player.y
    gm = v.game_map
    pack = pack_of(v.player)

    if gm.tile_id(px, py) == T.FARM:
        from omnisim.core.clock import day_of as _day
        from omnisim.systems.crops import (choose_crop, harvest, kind_of,
                                           plot_at, ripe, seed_of, sow)
        fl = skill_level(v.player, "Farming")
        hoe = best_tool("Farming", v.player)
        day = _day(v.clock_pts)
        p = plot_at(v, px, py)
        if p is None:
            return "This earth rests — the worked plots lie among the rows."
        if ripe(p, day):
            kind, qty, seeds = harvest(v, p)
            qty += hoe // 2  # good iron cuts clean
            qty += perk_value(fl, "Farming", "harvest_bonus", 0)  # deep furrows pay
            _ = bank_player(v, kind, qty, "the fields")
            from omnisim.entities.items import item_name as _inm
            if seeds > 0:  # kept seed corn, gen 1, traits unwritten
                _seed = seed_of(kind)
                add_item(pack, _seed, seeds)
                from omnisim.entities.items import stamp_lore as _sl2
                _sl2(v.player, _seed, 1, "You", "the fields",
                     day_of(v.clock_pts), extra={"gen": 1, "traits": {}})
            stamp_lore(v.player, kind, yield_q(fl, hoe), "You", "the fields",
                       day_of(v.clock_pts))
            v.player.comps["got_dirty"] = True  # soil under nails
            return _reward(v, "Farming", f"You cut the ripe rows. (+{qty} {kind})")
        if p["crop"] is not None:
            if pack.get("compost", 0) >= 1 and int(p.get("fert", 0)) < 3:
                from omnisim.systems.crops import feed as _feed
                pack["compost"] -= 1
                if pack["compost"] <= 0:
                    del pack["compost"]
                _feed(v, p)
                v.player.comps["got_dirty"] = True  # compost on hands
                return _reward(v, "Farming", "You feed the hungry soil with compost.")
            from omnisim.systems.crops import days_of as _days
            left = _days(kind_of(p)) - (day - p["crop"]["sown"])
            return f"Green yet — {max(left, 1)} dawns to the cut."
        if pack.get("compost", 0) >= 1 and int(p.get("fert", 0)) < 3 \
                and not any(str(k).endswith("_seed") and q > 0 for k, q in pack.items()):
            from omnisim.systems.crops import feed as _feed2
            pack["compost"] -= 1
            if pack["compost"] <= 0:
                del pack["compost"]
            _feed2(v, p)
            return _reward(v, "Farming", "No seed — but the soil eats compost first.")
        want = choose_crop(v, p)  # the earth and the bellies choose
        seed_item = seed_of(want)
        if pack.get(seed_item, 0) < 1:
            have = [s for s in pack if str(s).endswith("_seed") and pack.get(s, 0) > 0]
            if not have:
                return "No seed in the pack — buy pinches at the Market."
            from omnisim.systems.crops import kinds as _kinds
            seed_item = have[0]
            want = next((k for k in _kinds() if seed_of(k) == seed_item), "wheat")
        saver = perk_value(fl, "Farming", "seed_saver", False)
        sown_n = int(v.player.get("sow_n", 0))
        if saver and sown_n % 2 == 1:
            v.player.comps["sow_n"] = sown_n + 1
            sow(v, p, day, want)  # seed saver: the apron hid a pinch
            return _reward(v, "Farming", "You sow from the saved pinch.")
        pack[seed_item] -= 1
        if pack[seed_item] <= 0:
            del pack[seed_item]
        v.player.comps["sow_n"] = sown_n + 1
        sow(v, p, day, want)
        v.player.comps["got_dirty"] = True  # earth under nails
        from omnisim.entities.items import item_name as _inm2
        return _reward(v, "Farming", f"You sow {want} for the earth.")

    if _adjacent_tile(gm, px, py, T.ANVIL):
        from omnisim.systems.recipes import craftable, do_craft
        from omnisim.systems.tools import best_tool
        from omnisim.systems.workspaces import efficiency as _eff, use as _use
        _use(v, "Smithing", v.player)  # GDD 5.2: honest work wears the shop
        ready = craftable(v, "anvil")
        if ready:
            if not _cooled(v, ("smithy",)):
                return "The forge needs to breathe."
            _stamp_cd(v, ("smithy",))
            return do_craft(v, ready[0])
        if not _cooled(v, ("smithy",)):
            return "The forge needs to breathe."
        _stamp_cd(v, ("smithy",))
        return _reward(v, "Smithing", "Sparks fly at the anvil. (No commission — forge iron to earn.)")

    if _adjacent_tile(gm, px, py, T.WORKBENCH):
        from omnisim.systems.recipes import craftable, do_craft
        from omnisim.systems.workspaces import use as _use2
        _use2(v, "Carpentry", v.player)
        key = ("bench", px, py)
        made = craftable(v, "workbench")
        if made:
            if not _cooled(v, key):
                return "No jobs on the bench right now."
            _stamp_cd(v, key)
            return do_craft(v, made[0])
        if not _cooled(v, key):
            return "No jobs on the bench right now."
        _stamp_cd(v, key)
        from omnisim.systems.tools import dullest_tool as _dull
        found = _dull(v, v.player)  # the neighbour pays from their own pocket
        if found is not None and found[0].get("pocket", {}).get("coin", 0) >= 1:
            found[0].get("pocket", {})["coin"] -= 1
            add_item(pack, "coin", 1)
            return _reward(v, "Trading", "You mend tools for a neighbour. (+1 coin)")
        return _reward(v, "Trading", "You mend tools for a neighbour. (Their purse is light.)")

    for p in v.places:
        if p.name == "Smithy" and p.contains(px, py):
            key = ("smithy",)
            if not _cooled(v, key):
                return "The forge needs to breathe."
            _stamp_cd(v, key)
            from omnisim.systems.tools import best_tool
            coins = 1 + (1 if best_tool("Smithing", v.player) >= 3 else 0)
            add_item(pack, "coin", coins)  # travellers carry outside coin —
            return _reward(v, "Smithing", f"You mend a hinge for a traveller. (+{coins} coin)")

    if nearest_stall(v, px, py) is not None:
        key = ("stall",)
        if not _cooled(v, key):
            return "No customers right now."
        _stamp_cd(v, key)
        from omnisim.ui.sidebar import nearest_stall_obj
        stall = nearest_stall_obj(v, px, py)  # Bram tips from the Market reserve
        if stall is not None and stall.get("till", 0) >= 1:
            stall["till"] -= 1
            add_item(pack, "coin", 1)
            return _reward(v, "Trading", "You hawk goods for the merchant. (+1 coin tip)")
        return _reward(v, "Trading", "You hawk goods for the merchant. (The reserve is light.)")

    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            tx, ty = px + dx, py + dy
            if gm.in_bounds(tx, ty) and gm.tile_id(tx, ty) == T.TREE:
                wl = skill_level(v.player, "Woodcutting")
                axe = best_tool("Woodcutting", v.player)
                if axe < 1:  # WOOD: trunks need iron — bare hands gather branches
                    _ = bank_player(v, "firewood", 1, "the wilds")
                    stamp_lore(v.player, "firewood", 1, "You", "the wilds",
                               day_of(v.clock_pts))
                    return _reward_cost(v, "Woodcutting",
                                        "No axe: you gather fallen branches. (+1 firewood)",
                                        COST_WORK)
                # One tree is a winter of firewood — and a morning of labor.
                qty = 8 + wl // 2 + axe // 2
                gm.tiles[ty, tx] = T.GRASS
                try:
                    from omnisim.systems.schedule import invalidate_bfs_cache as _ib4
                    _ib4()
                except Exception:
                    pass
                v.felled.append((tx, ty))
                _ = bank_player(v, "firewood", qty, "the wilds")
                stamp_lore(v.player, "firewood", yield_q(wl, axe), "You", "the wilds", day_of(v.clock_pts))
                hw = ""
                if (perk_value(wl, "Woodcutting", "hardwood", False) and axe >= 3
                        and (v.clock_pts // 100 + tx + ty) % 5 == 0):
                    _ = bank_player(v, "hardwood", 1, "the wilds")
                    stamp_lore(v.player, "hardwood", min(5, 3 + axe // 3), "You", "the wilds", day_of(v.clock_pts))
                    hw = " Old heartwood! (+1 hardwood)"
                cost = COST_WORK * 4  # felling is a morning's labor, not a tap
                return _reward_cost(v, "Woodcutting", f"Timber! You fell a tree. (+{qty} firewood){hw}", cost)

    clay_bank = ((v.work_posts or {}).get("clay") or {}).get("at")  # POTS: the bank
    if clay_bank is not None and (px, py) == (int(clay_bank[0]), int(clay_bank[1])):
        if not _cooled(v, ("clay", px, py)):
            return "Dug out — this bank needs time to settle."
        _stamp_cd(v, ("clay", px, py))
        from omnisim.systems.perks import perk_value as _pv4
        pl = skill_level(v.player, "Pottery")
        qty = 2 + _pv4(pl, "Pottery", "dig_bonus", 0)
        _ = bank_player(v, "clay", qty, "the riverbank")
        stamp_lore(v.player, "clay", yield_q(pl, 0), "You", "the riverbank",
                   day_of(v.clock_pts))
        return _reward(v, "Pottery", f"You dig clean river clay. (+{qty} clay)")

    ore_cut = ((v.work_posts or {}).get("iron_ore") or {}).get("at")  # IRON: the cut
    if ore_cut is not None and max(abs(px - int(ore_cut[0])), abs(py - int(ore_cut[1]))) <= 1:
        if not _cooled(v, ("ore", px, py)):
            return "Worked out — this face needs time to show fresh stone."
        _stamp_cd(v, ("ore", px, py))
        ml = skill_level(v.player, "Mining")
        pick = best_tool("Mining", v.player)
        qty = 2 + (1 if pick >= 2 else 0) + (ml // 3)  # iron for sweat
        _ = bank_player(v, "iron_ore", qty, "the grey teeth")
        stamp_lore(v.player, "iron_ore", yield_q(ml, pick), "You", "the grey teeth",
                   day_of(v.clock_pts))
        return _reward(v, "Mining", f"You break black stone from the teeth. (+{qty} iron ore)")

    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            tx, ty = px + dx, py + dy
            if gm.in_bounds(tx, ty) and gm.tile_id(tx, ty) == T.WATER:
                if (v.clock_pts // 100 + px + py) % 3 == 0:
                    advance_clock(v, COST_WORK)
                    v.turn += 1
                    return "Nibbles... nothing bites."
                cl = skill_level(v.player, "Fishing")
                rod = best_tool("Fishing", v.player)
                catch = perk_value(cl, "Fishing", "catch", 1) + rod // 3
                # The pond is biology: thin water, thin nets (same rule as NPCs).
                from omnisim.systems.fishery import take as _take, stock_of as _sot
                if _sot(v) <= 0:
                    advance_clock(v, COST_WORK)
                    v.turn += 1
                    return "Fished out — the pond needs a season to recover."
                catch = _take(v, catch)
                fishn, goldn = 0, 0
                if perk_value(cl, "Fishing", "golden", False) and rod >= 4:
                    caught = int(v.player.get("fish_caught", 0))
                    for _ in range(catch):
                        caught += 1
                        if caught % 8 == 0:
                            goldn += 1
                        else:
                            fishn += 1
                    v.player.comps["fish_caught"] = caught
                else:
                    fishn = catch
                _ = bank_player(v, "fish", fishn, "Stillpond")
                fish_q = yield_q(cl, rod)
                stamp_lore(v.player, "fish", fish_q, "You", "Stillpond", day_of(v.clock_pts))
                bits = f"+{fishn} fish"
                if goldn:
                    _ = bank_player(v, "goldenfish", goldn, "Stillpond")
                    stamp_lore(v.player, "goldenfish", 5, "You", "Stillpond", day_of(v.clock_pts))
                    bits += f" +{goldn} GOLDEN fish!"
                return _reward(v, "Fishing", f"You land a river fish! ({bits})")

    # Meal: bread + cooked grain (wheat berries or ground flour — raw grain
    # is not food, but the pot fixes that). Flour first (use the milled).
    grain = pack.get("wheat", 0) + pack.get("flour", 0)
    if pack.get("bread", 0) >= 1 and grain >= 1:
        full = grain >= 2
        half = not full and skill_level(v.player, "Cooking") >= 4  # M1 camp cook
        if not full and not half:
            pass  # not enough skill for a thin pot: fall through to poultice/idle
        else:
            near_hearth = any(
                gm.in_bounds(px + dx, py + dy) and gm.tile_id(px + dx, py + dy) == T.HEARTH
                for dx in (-1, 0, 1) for dy in (-1, 0, 1))
            if not near_hearth:
                return "You need a hearth to cook. (The Boar's is always warm.)"
            fire_xy = next(((px + dx, py + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                            if gm.in_bounds(px + dx, py + dy)
                            and gm.tile_id(px + dx, py + dy) == T.HEARTH
                            and (px + dx, py + dy) in v.campfires), None)
            need = 2 if full else 1
            take_flour = min(pack.get("flour", 0), need)
            if take_flour:
                take_item(pack, "flour", take_flour)
            take_item(pack, "wheat", need - take_flour)
            take_item(pack, "bread", 1)
            add_item(pack, "meal", 1)
            cooking_lvl = skill_level(v.player, "Cooking")
            _lores = [lore_q(v.player, "bread"), 1 + cooking_lvl // 2]
            if take_flour:
                _lores.append(lore_q(v.player, "flour"))
            if need - take_flour:
                _lores.append(lore_q(v.player, "wheat"))
            meal_q = min(_lores)
            stolen = bool(lore_of(v.player, "wheat").get("stolen")
                          or lore_of(v.player, "flour").get("stolen")
                          or lore_of(v.player, "bread").get("stolen"))
            stamp_lore(v.player, "meal", meal_q, "You", "a warm hearth", day_of(v.clock_pts), stolen=stolen)
            ash = ""
            if fire_xy is not None:  # M8: campfires burn down
                v.campfires[fire_xy] -= 1
                if v.campfires[fire_xy] <= 0:
                    del v.campfires[fire_xy]
                    gm.tiles[fire_xy[1], fire_xy[0]] = T.GRASS
                    try:
                        from omnisim.systems.schedule import invalidate_bfs_cache as _ib5
                        _ib5()
                    except Exception:
                        pass
                    ash = " The campfire burns out."
            if half:
                return _reward(v, "Cooking", "You stretch the pot thin. (+1 hot meal)") + ash
            return _reward(v, "Cooking", "You work the hearth. (+1 hot meal)") + ash

    if pack.get("wheat", 0) >= 1 and _adjacent_tile(gm, px, py, T.HEARTH):
        grind = min(pack["wheat"], 4)
        take_item(pack, "wheat", grind)
        add_item(pack, "flour", grind)
        stamp_lore(v.player, "flour", yield_q(skill_level(v.player, "Cooking"), 0),
                   "You", "the quern-stone", day_of(v.clock_pts))
        return _reward(v, "Cooking", f"You grind wheat to flour. (+{grind} flour)")

    from omnisim.systems.recipes import craftable as _craftable, do_craft as _do_craft
    pot = [r for r in _craftable(v) if r["id"] == "poultice"]
    if pot:
        return _do_craft(v, pot[0])

    for dx in (-1, 0, 1):  # HERBS: the meadow gives to patient hands — after
        for dy in (-1, 0, 1):  # hearth, rows, and forge have had their say
            tx, ty = px + dx, py + dy
            if gm.in_bounds(tx, ty) and gm.tile_id(tx, ty) == T.GRASS:
                if not _cooled(v, ("herbs", tx, ty)):
                    return "Picked clean — this patch needs time to recover."
                _stamp_cd(v, ("herbs", tx, ty))
                from omnisim.systems.perks import perk_value as _pv3
                hl = skill_level(v.player, "Herbalism")
                knife = best_tool("Herbalism", v.player)
                qty = 2 + _pv3(hl, "Herbalism", "gather_bonus", 0) + knife // 3
                _ = bank_player(v, "herbs", qty, "the meadow")
                stamp_lore(v.player, "herbs", yield_q(hl, knife), "You", "the meadow",
                           day_of(v.clock_pts))
                return _reward(v, "Herbalism", f"You cut clean stems. (+{qty} wild herbs)")

    return "No work here. (Fields, smithy, stalls, trees, pond — or carry 2 wheat + 1 bread to a hearth.)"


def repair_station(v: Village) -> str:
    """True the worn shop: 1 firewood + Smithing hands restore the most
    degraded workspace. Costs 3 min. (GDD 5.2: condition is repairable.)"""
    from omnisim.core.clock import COST_WORK, advance_clock
    from omnisim.systems.skills import skill_level
    from omnisim.systems.workspaces import efficiency, repair, state_of
    pack = pack_of(v.player)
    ws = getattr(v, "workspaces", None)
    if not isinstance(ws, dict):
        ws = {}
    worn = [(k, float(s.get("condition", 100.0))) for k, s in ws.items()
            if isinstance(s, dict)]
    worn = [(k, c) for k, c in worn if c < 100.0]
    if not worn:
        return "Every shop stands true — nothing to repair."
    if pack.get("firewood", 0) < 1:
        return "You need 1 firewood to brace the works."
    key = min(worn, key=lambda kv: kv[1])[0]
    take_item(pack, "firewood", 1)
    lvl = skill_level(v.player, "Smithing")
    repair(v, key, 15.0 + 2.0 * lvl)  # skilled hands true it faster
    advance_clock(v, COST_WORK)
    v.turn += 1
    from omnisim.systems.skills import gain_xp
    for lv in gain_xp(v.player, "Smithing", 2):
        v.say(f"Smithing rises to {lv}!")
    return (f"You true the {key.lower()} shop ({int(worn[0][1])}% → "
            f"{int(efficiency(v, key) * 100)}%).")


def _water_near(v: Village, reach: int = 2) -> bool:
    """Well or open water within reach (Chebyshev). The well never moves;
    ponds serve in a pinch."""
    from omnisim.world import tile as T
    px, py = v.player.x, v.player.y
    gm = v.game_map
    for dy in range(-reach, reach + 1):
        for dx in range(-reach, reach + 1):
            tx, ty = px + dx, py + dy
            if gm.in_bounds(tx, ty) and gm.tile_id(tx, ty) in (T.WELL, T.WATER):
                return True
    return False


def drink(v: Village) -> str:
    """Drink deep at the well or pondside. Thirst 0. Costs 1 min."""
    from omnisim.core.clock import COST_GET, advance_clock
    if not _water_near(v):
        return "No water in reach. (The well stands by the market; the pond lies east.)"
    v.player.comps["thirst"] = 0.0
    advance_clock(v, COST_GET)
    v.turn += 1
    return "You drink deeply. (Thirst gone.)"


def wash(v: Village) -> str:
    """Scrub clean at the well or pondside. Filth 0. Costs 1 min."""
    from omnisim.core.clock import COST_GET, advance_clock
    from omnisim.systems.needs import wash as _wash
    if not _water_near(v):
        return "No water in reach to wash with."
    _wash(v.player)
    advance_clock(v, COST_GET)
    v.turn += 1
    return "You scrub clean. (Filth gone.)"


def trade_price(item_id: str, trading_level: int, buying: bool, stall=None, village=None) -> int:
    """Stall price: base x scarcity x Trading ±5%/level. No stall, no scarcity.
    Step 2: with a village, the merchant's fair bands anchor both sides
    (till-aware width); Trading skill still moves the player's coin."""
    if village is not None:
        try:
            from omnisim.systems.pricing import engine_on as _peng4
            if _peng4():
                from omnisim.systems.pricing import merchant_quote_mc as _bandq
                from omnisim.systems.pricing import MC as _MC
                side = "sell" if buying else "buy"  # you buy = counter sells
                mc = _bandq(village, stall, item_id, side)
                step = 0.05 * (max(1, trading_level) - 1)
                move = (1 - step) if buying else (1 + step)
                return max(1, int(round(mc / _MC * move)))
        except Exception:
            pass
    if stall is not None:
        from omnisim.systems.pricing import shelf_buy, shelf_sell
        return shelf_buy(stall, item_id, trading_level) if buying \
            else shelf_sell(stall, item_id, trading_level)
    base = item_defs()["items"][item_id]["price"]
    step = 0.05 * (max(1, trading_level) - 1)
    price = base * (1 - step) if buying else base * (1 + step)
    return max(1, int(round(price)))


def sell_price_for(player, item_id: str, trading: int, stall=None, village=None) -> int:
    """One unit's fetch: Trading move x shelf x quality tier x Cooking fame (meal)."""
    import math
    from omnisim.systems.perks import perk_value
    listed = stall is None or item_id in stall.get("stock", {})
    price = trade_price(item_id, trading, buying=False,
                        stall=stall if listed else None,
                        village=village)  # ECON: seed fair, not famine
    price = max(1, int(round(price * q_mult(lore_q(player, item_id)))))
    if item_id == "meal":
        mult = perk_value(skill_level(player, "Cooking"), "Cooking", "meal_sell_mult", 1.0)
        if mult > 1.0:
            price = int(math.ceil(price * mult))
    return price


def _trade_tables() -> tuple:
    """Trade lists derive from the item table (new goods trade on arrival)."""
    try:
        from omnisim.entities.items import (dinner_reserve as _dr,
                                            sellable as _se, staples as _st)
        return tuple(_st()), tuple(_se()), tuple(_dr())
    except Exception:
        return ((("bread", "apple", "meal"),
                 ("wheat", "flour", "firewood", "fish", "meal", "hardwood",
                  "goldenfish", "bread", "apple", "hoe", "axe", "rod",
                  "knife", "hammer", "compost", "herbs", "remedy", "clay",
                  "pot", "potted_meal"),
                 ("bread", "apple", "fish", "meal")))


BUY_ORDER, SELLABLE, DINNER_RESERVE = _trade_tables()


def _steal_order() -> list:
    """Hungry thieves lift dinner first (best bite on top), coin next,
    then valuables — no names, just the table."""
    try:
        from omnisim.entities.items import eat_order as _eo
        foods = [i for i in _eo()]
    except Exception:
        foods = ["bread", "meal", "apple"]
    try:
        from omnisim.entities.items import _items
        rest = sorted([i for i in SELLABLE if i not in foods and i != "coin"],
                      key=lambda i: -float(_items().get(i, {}).get("price", 0)))
    except Exception:
        rest = [i for i in SELLABLE if i not in foods and i != "coin"]
    return foods + ["coin"] + rest


def buy(v: Village, prefer: str | None = None) -> str:
    """Buy the humblest staple on the nearest stall — or a Crown deed
    ('buy stillpond'). T. Costs 1 min."""
    from omnisim.core.clock import PTS_PER_MIN
    from omnisim.ui.sidebar import nearest_stall_obj
    stall = nearest_stall_obj(v, v.player.x, v.player.y)
    if stall is None:
        return "No stall in reach. (The market square hums to the north.)"
    if prefer:  # LAND: deeds first — 'buy stillpond' buys ground, not fish
        from omnisim.systems.ownership import buy_parcel, match_parcel
        parcel = match_parcel(v, prefer)
        if parcel is not None:
            advance_clock(v, PTS_PER_MIN)
            v.turn += 1
            return buy_parcel(v, pack_of(v.player), "You", parcel)
    stock = stall["stock"]
    pack = pack_of(v.player)
    trading = skill_level(v.player, "Trading")
    _staples = [i for i in BUY_ORDER if stock.get(i, (0, 0))[0] > 0]
    want = [prefer] if prefer in _staples else []
    want += [i for i in BUY_ORDER if i != prefer and i in _staples]
    for item_id in want:
        qty, _ = stock.get(item_id, (0, 0))
        if qty > 0:
            price = trade_price(item_id, trading, buying=True, stall=stall, village=v)
            haggled = ""
            from omnisim.systems.perks import perk_value
            from omnisim.systems.pricing import scarcity_of
            if (perk_value(skill_level(v.player, "Speech"), "Speech", "haggle", False)
                    and scarcity_of(stall, item_id) >= 1.5
                    and v.player.get("haggle_day") != day_of(v.clock_pts)):
                # STEP 2: talk down from the band price (list is nostalgia —
                # bands already price below it). Silver tongue, daily token.
                _disc = max(1, int(price * 0.05 * max(1, trading)))
                talked = max(1, price - _disc)
                if talked < price:
                    v.player.comps["haggle_day"] = day_of(v.clock_pts)
                    from omnisim.entities.items import coin_str as _csh
                    haggled = f" (talked down from {_csh(price)})"
                    price = talked
            if pack.get("coin", 0) < price:
                from omnisim.entities.items import coin_str as _csb
                return (f"You need {_csb(price)} for {item_name(item_id)} "
                        f"(have {_csb(pack.get('coin', 0))}).")
            pack["coin"] -= price
            stock[item_id][0] -= 1
            stall["till"] = stall.get("till", 0) + price  # M5: coin reaches the till
            add_item(pack, item_id, 1)
            from omnisim.systems.schedule import _record_trade
            _record_trade(v, stall.get("name", "a market stall"), "You", item_id, 1, price)
            from omnisim.world.places import describe_location
            stamp_lore(v.player, item_id, 1, "a stallholder",
                       describe_location(v, v.player.x, v.player.y), day_of(v.clock_pts))
            for lv in gain_xp(v.player, "Trading", 1):
                v.say(f"Trading rises to {lv}!")
            try:  # market chat sustains; honest coin builds the bond (GDD 3.2/8.3)
                from omnisim.systems.needs import socialize as _soc2
                _soc2(v.player, 3.0)
                from omnisim.systems.relationships import adjust as _rel4
                _rel4(v.player, stall.get("name", "a market stall"), 1.0)
            except Exception:
                pass
            advance_clock(v, PTS_PER_MIN)
            v.turn += 1
            from omnisim.systems.schedule import _settle_credit as _settle
            for _s in v.stalls:  # purchases fund the merchant's tabs first
                _settle(v, _s)
            from omnisim.entities.items import coin_str as _csb3
            return (f"You buy {item_name(item_id)} from "
                    f"{stall.get('name', 'a market stall')} for {_csb3(price)}.{haggled}")
    from omnisim.core.clock import day_of as _day
    if _day(v.clock_pts) <= 1 and not v.trades:
        return ("The stall is bare. (The Market stocks what villagers sell — "
                "work the fields till then.)")
    return "The stall is bare."


def sell(v: Village, prefer: str | None = None) -> str:
    """Sell all goods at Trading-moved prices — or a deed back to the Crown
    ('sell stillpond'). Shift+T. Costs 1 min.
    Coin now clears first; the rest goes on the merchant's tab (repaid
    from till inflow) — bulk harvests never bounce for want of a float."""
    from omnisim.core.clock import PTS_PER_MIN
    from omnisim.ui.sidebar import nearest_stall_obj
    stall = nearest_stall_obj(v, v.player.x, v.player.y)
    if stall is None:
        return "No stall in reach."
    if prefer:  # LAND: 'sell stillpond' sells ground back, not fish
        from omnisim.systems.ownership import match_parcel, sell_parcel
        parcel = match_parcel(v, prefer)
        if parcel is not None:
            advance_clock(v, PTS_PER_MIN)
            v.turn += 1
            return sell_parcel(v, pack_of(v.player), "You", parcel)
    pack = pack_of(v.player)
    trading = skill_level(v.player, "Trading")
    sname = stall.get("name", "a market stall")
    total, bits = 0, []
    for item_id in SELLABLE:
        qty = pack.get(item_id, 0)
        if qty > 0:
            price = sell_price_for(v.player, item_id, trading, stall, village=v)
            total += price * qty
            bits.append(f"{qty} {item_name(item_id)}")
    if not bits:
        return "You have nothing worth selling. (Wheat, firewood, fish.)"
    note = ""
    bonus = 0  # Smithing fame: polished goods earn a premium, paid in coin
    from omnisim.systems.perks import perk_value
    if perk_value(skill_level(v.player, "Smithing"), "Smithing", "resale", False):
        from omnisim.core.clock import day_of
        if v.resale_day != day_of(v.clock_pts):
            v.resale_day = day_of(v.clock_pts)
            bonus = 2
            from omnisim.entities.items import coin_str as _cs0
            note = f" (polished goods +{_cs0(2)})"
    till = stall.get("till", 0)
    cash = min(total, till)  # coin now, tab for the rest (within trust)
    tab = total - cash
    if tab > 0:
        try:
            from omnisim.systems.valuation import drives as _drv
            _trust = float(_drv().get("trust_line", 40.0))
        except Exception:
            _trust = 40.0
        _owed_you = float((stall.get("credit", {}) or {}).get("You", 0))
        tab = min(tab, max(0, int(_trust - _owed_you)))
    stall["till"] = till - cash
    from omnisim.systems.schedule import _record_trade
    _cash_left, _tab_left = cash, tab  # spend coin first, then trust, keep rest
    for item_id in SELLABLE:
        while pack.get(item_id, 0) > 0:
            price = sell_price_for(v.player, item_id, trading, stall, village=v)
            if _cash_left >= price:
                _cash_left -= price
            elif _tab_left >= price:
                _tab_left -= price
            else:
                break  # past coin and trust: stays in your pack
            _record_trade(v, "You", sname, item_id, 1, price)
            take_item(pack, item_id, 1)
    _moved_cash, _moved_tab = cash - _cash_left, tab - _tab_left
    pack["coin"] = pack.get("coin", 0) + _moved_cash
    from omnisim.entities.items import coin_str as _cs2
    if _moved_tab > 0:
        credit = stall.setdefault("credit", {})
        credit["You"] = int(credit.get("You", 0)) + _moved_tab
        note += f" (+{_cs2(_moved_tab)} on the merchant's tab)"
    _refused = total - _moved_cash - _moved_tab
    if _refused > 0:
        note += f" ({_cs2(_refused)} stays — past trust)"
    if bonus > 0 and (_moved_cash + _moved_tab) > 0:  # premium on coin moved
        _prem = min(bonus, stall.get("till", 0))
        stall["till"] -= _prem
        pack["coin"] = pack.get("coin", 0) + _prem
    for lv in gain_xp(v.player, "Trading", 1):
        v.say(f"Trading rises to {lv}!")
    try:  # V1 (GDD 8.3): honest dealing builds the stall bond; chat sustains
        from omnisim.systems.relationships import adjust as _rel3
        _rel3(v.player, sname, 1.0)
        from omnisim.systems.needs import socialize as _soc5
        _soc5(v.player, 3.0)
    except Exception:
        pass
    from omnisim.entities.items import coin_str as _cs2
    advance_clock(v, PTS_PER_MIN)
    v.turn += 1
    return f"You sell {', '.join(bits)} to {sname} for {_cs2(cash)}.{note}"


def _adjacent_npc(v: Village):
    """Closest living NPC within 1 tile, else None."""
    best, best_d = None, 2
    for n in v.npcs:
        if n.comps.get("dead"):
            continue
        d = max(abs(n.x - v.player.x), abs(n.y - v.player.y))
        if d < best_d:
            best, best_d = n, d
    return best


def steal(v: Village, item: str | None = None) -> str:
    """Lift 1 unit from an adjacent pocket (bread first). Shift+S. Sneaking XP either way."""
    from omnisim.core.clock import COST_GET
    npc = _adjacent_npc(v)
    if npc is None:
        return "No pockets in reach."
    pocket = npc.get("pocket", {})
    order = _steal_order()
    if item is not None and pocket.get(item, 0) > 0:
        target = item
    else:
        target = next((i for i in order if pocket.get(i, 0) > 0), None)
    if target is None:
        return f"{npc.name} has nothing worth taking." if item is None else \
            f"{npc.name} has no {item_name(item)} to take."
    level = skill_level(v.player, "Sneaking")
    p = max(0.0, 0.35 - 0.07 * level)  # the only dice in town (seeded)
    caught = v.rng.random() < p
    for lv in gain_xp(v.player, "Sneaking", 1):
        v.say(f"Sneaking rises to {lv}!")
    advance_clock(v, COST_GET)
    v.turn += 1
    if caught:
        try:  # V1 Foundations (GDD 8.3): caught theft burns relationship
            from omnisim.systems.relationships import adjust_both
            adjust_both(npc, v.player, -15.0, -15.0)
        except Exception:
            pass
        try:  # STEP 5: theft remembered by the victim (trust hit rides rel)
            from omnisim.systems.memory import on as _m5s_on
            from omnisim.systems.memory import log_event as _m5s_ev
            if _m5s_on():
                _m5s_ev(npc, v, "stolen from", -0.5,
                        subject=getattr(v.player, "name", "you"))
        except Exception:
            pass
        return f"{npc.name} catches your wrist! Nothing taken, and the market saw."
    pocket[target] -= 1
    if pocket[target] <= 0:
        del pocket[target]
    pack = pack_of(v.player)
    add_item(pack, target, 1)
    vlore = lore_of(npc, target)
    stamp_lore(v.player, target, int(vlore.get("q", 1)), vlore.get("by", "unknown"),
               vlore.get("origin", "?"), day_of(v.clock_pts), stolen=True)
    return f"You lift {item_name(target)} from {npc.name}. (stolen!)"


def give(v: Village, item: str | None = None) -> str:
    """Press best food (else 1 coin) on an adjacent NPC. Shift+V. Kindness trains Speech."""
    from omnisim.core.clock import COST_GET
    npc = _adjacent_npc(v)
    if npc is None:
        return "No one in reach to feed."
    pack = pack_of(v.player)
    if item is not None and pack.get(item, 0) > 0:
        fid = item
    else:
        fid = best_food(pack)
        if fid is None and pack.get("coin", 0) <= 0:
            return "Empty hands. (Carry food or coin to give.)"
        fid = fid or "coin"
    take_item(pack, fid, 1)
    pocket = npc.get("pocket", {})
    pocket[fid] = pocket.get(fid, 0) + 1
    desperate = float(npc.get("hunger", 0)) >= 100
    if fid != "coin":
        food = item_defs()["items"].get(fid, {})
        npc.comps["hunger"] = max(0.0, float(npc.get("hunger", 0)) - food.get("food", 0) * 0.5)
        stamp_lore(npc, fid, lore_q(v.player, fid), "You", "a gift", day_of(v.clock_pts))
    for lv in gain_xp(v.player, "Speech", 1):
        v.say(f"Speech rises to {lv}!")
    try:  # V1 Foundations (GDD 8.3): gifts build relationship
        from omnisim.systems.relationships import adjust_both
        adjust_both(v.player, npc, 5.0, 5.0)
    except Exception:
        pass
    advance_clock(v, COST_GET)
    v.turn += 1
    thanks = "hope rekindles behind their eyes" if desperate and fid != "coin" else "they thank you"
    return f"You press {item_name(fid)} on {npc.name} ({thanks})."


def build_campfire(v: Village) -> str:
    """Raise a campfire at your boots: 4 firewood, cooks like a hearth, 3 charges. Key b."""
    from omnisim.core.clock import COST_WORK
    px, py = v.player.x, v.player.y
    gm = v.game_map
    if gm.tile_id(px, py) != T.GRASS:
        return "You need open grass under your boots. (Campfire: 4 firewood.)"
    pack = pack_of(v.player)
    if pack.get("firewood", 0) < 4:
        return f"You need 4 firewood (have {pack.get('firewood', 0)})."
    take_item(pack, "firewood", 4)
    gm.tiles[py, px] = T.HEARTH
    try:
        from omnisim.systems.schedule import invalidate_bfs_cache as _ib6
        _ib6()
    except Exception:
        pass
    v.campfires[(px, py)] = 3
    advance_clock(v, COST_WORK)
    v.turn += 1
    return "You raise a campfire. (3 charges; cook beside it with W.)"


def build_cabin(v: Village) -> str:
    """Raise a 3x3 cabin around you: 8 beams + 20 firewood. Door, bed, own chest. Shift+B."""
    from omnisim.core.clock import COST_WORK
    px, py = v.player.x, v.player.y
    gm = v.game_map
    cells = [(px + dx, py + dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
    if any(not gm.in_bounds(x, y) or gm.tile_id(x, y) != T.GRASS for x, y in cells):
        return "You need open ground. (A cabin wants 3x3 grass.)"
    if any(v.entity_at(x, y) is not None and (x, y) != (px, py) for x, y in cells):
        return "Someone's standing in the floor plan. (Shove them along first.)"
    pack = pack_of(v.player)
    if pack.get("beam", 0) < 8 or pack.get("firewood", 0) < 20:
        return (f"You need 8 beams + 20 firewood "
                f"(have {pack.get('beam', 0)} + {pack.get('firewood', 0)}).")
    take_item(pack, "beam", 8)
    take_item(pack, "firewood", 20)
    layout = {(0, -1): T.CHEST, (-1, -1): T.WALL_WOOD, (1, -1): T.WALL_WOOD,
              (-1, 0): T.WALL_WOOD, (0, 0): T.BED, (1, 0): T.FLOOR,
              (-1, 1): T.WALL_WOOD, (0, 1): T.DOOR, (1, 1): T.WALL_WOOD}
    for (dx, dy), tid in layout.items():
        gm.tiles[py + dy, px + dx] = tid
        try:
            from omnisim.systems.schedule import invalidate_bfs_cache as _ib7
            _ib7()
        except Exception:
            pass
    v.chests[(px, py - 1)] = {"pack": {}, "lore": {}}
    advance_clock(v, COST_WORK * 4)
    v.turn += 1
    return "You raise a cabin: door, bed, and your own chest. (Z sleeps, c stashes.)"


def chest_use(v: Village, item: str | None = None, take: bool = False) -> str:
    """Stash the pack (minus coin) or, empty-handed, take it all back. Key c.
    item set: stash/take one unit of that item instead of everything."""
    from omnisim.core.clock import COST_GET
    px, py = v.player.x, v.player.y
    gm = v.game_map
    spot = next(((px + dx, py + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                 if gm.in_bounds(px + dx, py + dy)
                 and gm.tile_id(px + dx, py + dy) == T.CHEST), None)
    if spot is None:
        return "No chest in reach."
    if spot not in v.chests:
        return "Someone else's chest. Hands off."
    chest = v.chests[spot]
    pack = pack_of(v.player)
    if item is not None:  # WEB: single-item moves for list UIs
        if take:
            if chest["pack"].get(item, 0) <= 0:
                return f"No {item_name(item)} in the chest."
            chest["pack"][item] -= 1
            if chest["pack"][item] <= 0:
                del chest["pack"][item]
            add_item(pack, item, 1)
            if item in chest.get("lore", {}):
                v.player.comps.setdefault("lore", {})[item] = dict(chest["lore"][item])
        else:
            if pack.get(item, 0) <= 0 or item == "coin":
                return f"No {item_name(item)} to stash."
            take_item(pack, item, 1)
            chest["pack"][item] = chest["pack"].get(item, 0) + 1
            if item in (v.player.get("lore", {}) or {}):
                chest["lore"][item] = dict(v.player.get("lore", {})[item])
        advance_clock(v, COST_GET)
        v.turn += 1
        verb = "take" if take else "stash"
        return f"You {verb} 1 {item_name(item)} {'from' if take else 'in'} the chest."
    stashable = {i: q for i, q in pack.items() if q > 0 and i != "coin"}
    if stashable:
        n = 0
        for i, q in stashable.items():
            chest["pack"][i] = chest["pack"].get(i, 0) + q
            if i in (v.player.get("lore", {}) or {}):
                chest["lore"][i] = dict(v.player.get("lore", {})[i])
            take_item(pack, i, q)
            n += q
        advance_clock(v, COST_GET)
        v.turn += 1
        return f"You stash {n} goods in the chest. (c empty-handed takes back.)"
    if chest["pack"]:
        n = 0
        for i, q in list(chest["pack"].items()):
            add_item(pack, i, q)
            if i in chest.get("lore", {}):
                v.player.comps.setdefault("lore", {})[i] = dict(chest["lore"][i])
            n += q
        chest["pack"].clear()
        chest["lore"].clear()
        advance_clock(v, COST_GET)
        v.turn += 1
        return f"You take {n} goods from the chest."
    return "Dust and echoes."


def sleep_till_dawn(v: Village) -> str:
    """Sleep on a bed tile; the world fast-forwards to 06:00. Shift+Z."""
    from omnisim.core.clock import MIN_PER_DAY, PTS_PER_MIN, DAWN_MIN
    if v.game_map.tile_id(v.player.x, v.player.y) != T.BED:
        return "You need a bed to sleep. (The Boar has bunks.)"
    day_len = MIN_PER_DAY * PTS_PER_MIN
    target = ((v.clock_pts - DAWN_MIN * PTS_PER_MIN) // day_len + 1) * day_len
    target += DAWN_MIN * PTS_PER_MIN
    v.player.comps["activity"] = "sleeping"  # GDD 3.1: sleep burns slower
    while v.clock_pts < target:
        advance_clock(v, min(60 * PTS_PER_MIN, target - v.clock_pts))
    v.player.comps["hp"] = int(v.player.get("max_hp", 20))
    for _ in range(3):
        for lv in gain_xp(v.player, "Healing", 1):
            v.say(f"Healing rises to {lv}!")
    v.turn += 1
    return "You sleep till dawn. (HP restored.)"
