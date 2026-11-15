"""Move-point clock, CDDA-style. 100 pts = 1 game minute.

Every action pays points; conditions multiply the price via action_cost(),
the single choke point (hungry/sick/slow/burdened all hook in here).
The clock is absolute points; display derives day + HH:MM + phase.
Dawn (06:00 crossing) tidies the village: doors re-close, stalls restock (M2).
"""
from __future__ import annotations
from omnisim.world import tile as T

PTS_PER_MIN = 100
MIN_PER_DAY = 1440
DAY_START_MIN = 360  # 06:00 — every day (and Day 1) begins here
DAWN_MIN = 360

COST_STEP = 100   # one tile walked
COST_OPEN = 100   # heaving a door open
COST_WAIT = 1000  # leaning on a post, 10 min
COST_EAT = 150    # a meal, unhurried
COST_GET = 50     # scooping things up
COST_WORK = 300   # M3
HUNGER_PER_MIN = 1 / 25  # +1 hunger per 25 min
STARVE_HP_MIN = 20       # −1 HP per 20 min while starving


def action_cost(base_pts: int, comps: dict, kind: str = "walk") -> int:
    """Price of an action in points. comps = entity comps dict.

    V1 Foundations (GDD 3.0): single choke point, but multipliers are now
    dynamic — hunger/activity/traits/skill all flow through needs.py.
    Legacy numbers preserved when no traits present.
    """
    mult = 1.0
    hunger = comps.get("hunger", 0) if isinstance(comps, dict) else 0
    if hunger and hunger >= 100:
        mult *= 1.3  # starving: everything is hard (M2 hunger values)
    elif hunger and hunger > 70 and kind == "walk":
        mult *= 1.25  # hungry trudge
    thirst = comps.get("thirst", 0) if isinstance(comps, dict) else 0
    if thirst and thirst >= 100:
        mult *= 1.2  # parched: everything drags
    elif thirst and thirst > 70 and kind == "walk":
        mult *= 1.1  # dry trudge
    if kind == "walk":
        skills = comps.get("skills", {}) if isinstance(comps, dict) else {}
        if isinstance(skills, dict) and "Endurance" in skills:
            endurance = skills.get("Endurance", 1)
        else:
            endurance = comps.get("Endurance", comps.get("endurance", 1)) \
                if isinstance(comps, dict) else 1
        mult *= max(0.5, 1.0 - 0.02 * (int(endurance) - 1))
        # Trait hook (GLUTTONOUS/HARDY etc.): defaults 1.0, keeps legacy tests green
        try:
            from omnisim.systems.needs import trait_mult
            fake = type("E", (), {"get": lambda self, k, d=None:
                                  comps.get(k, d)})()
            mult *= trait_mult(fake)  # type: ignore[arg-type]
        except Exception:
            pass
    return max(1, int(round(base_pts * mult)))


def day_of(clock_pts: int) -> int:
    return clock_pts // (MIN_PER_DAY * PTS_PER_MIN) + 1


def hhmm_of(clock_pts: int) -> str:
    mins = (clock_pts // PTS_PER_MIN) % MIN_PER_DAY
    return f"{mins // 60:02d}:{mins % 60:02d}"


def phase_of(clock_pts: int) -> str:
    hour = (clock_pts // PTS_PER_MIN) % MIN_PER_DAY // 60
    if 5 <= hour < 7:
        return "Dawn"
    if 7 <= hour < 11:
        return "Morning"
    if 11 <= hour < 14:
        return "Midday"
    if 14 <= hour < 17:
        return "Dusk"
    if 17 <= hour < 22:
        return "Evening"
    return "Night"


def fmt_clock(clock_pts: int) -> str:
    return f"Day {day_of(clock_pts)} · {hhmm_of(clock_pts)} · {phase_of(clock_pts)}"


def advance_clock(village, pts: int) -> None:
    """Push the clock; hunger gnaws, and dawn tidies the village."""
    if pts <= 0:
        return
    mins = pts / PTS_PER_MIN
    _hunger_tick(village, mins)
    day_len = MIN_PER_DAY * PTS_PER_MIN
    dawns_before = (village.clock_pts - DAWN_MIN * PTS_PER_MIN) // day_len
    village.clock_pts += pts
    from omnisim.systems.schedule import tick_npcs
    tick_npcs(village, pts)
    dawns_after = (village.clock_pts - DAWN_MIN * PTS_PER_MIN) // day_len
    if dawns_after > dawns_before:
        village.game_map.doors[:, :] = False
        try:
            from omnisim.systems.schedule import invalidate_bfs_cache as _ib
            _ib()
        except Exception:
            pass
        from omnisim.systems.schedule import flex_at_dawn
        flex_at_dawn(village)  # CHAIN: read closing shelves BEFORE the cart refills
        try:  # LAND: the Crown's office opens — one deed moves, at most
            from omnisim.systems.land import on as _land9_on
            if _land9_on():
                from omnisim.systems.land import office as _land9_office
                _land9_office(village)
            else:
                from omnisim.systems.ownership import land_office
                land_office(village)
        except Exception:
            pass
        from omnisim.systems.crops import advance_dawn
        advance_dawn(village, day_of(village.clock_pts))  # CROPS: earth rests, seed counts
        from omnisim.systems.rot import rot_dawn
        rot_dawn(village, day_of(village.clock_pts))  # ROT: soft stacks return to earth
        for fx, fy in village.felled:  # the forest creeps back overnight
            if village.game_map.tile_id(fx, fy) == T.GRASS:
                village.game_map.tiles[fy, fx] = T.TREE
                try:
                    from omnisim.systems.schedule import invalidate_bfs_cache as _ib2
                    _ib2()
                except Exception:
                    pass
        village.felled.clear()
        try:  # picked meadows fruit again: the wild table resets at dawn
            if isinstance(getattr(village, "foraged", None), set):
                village.foraged.clear()
        except Exception:
            pass
        try:  # FORAGE-REGROWTH (flag forage_regrowth, default off):
            # logistic patch regrowth (pond form, no Allee term).
            from omnisim.systems.forageregrow import on as _fr_don
            if _fr_don():
                from omnisim.systems.forageregrow import recruit as _fr_rec
                _fr_rec(village)
        except Exception:
            pass
        try:  # the pond recruits: logistic spawning, Allee-throttled —
            from omnisim.systems.fishery import recruit  # overfishing collapses it
            recruit(village)  # for a season, not a dawn
        except Exception:
            pass
        try:  # WEATHER: the sky rolls cold snaps; hearths eat firewood
            from omnisim.systems.weather import burn_hearths, dawn_chill
            dawn_chill(village)
            burn_hearths(village)
        except Exception:
            pass
        # MERCHANT: one Market, one reserve. No cart wholesale, no float, no
        # wages, no exports — producers stock these shelves, makers and
        # mouths empty them. Surplus rots or gets eaten; the coin stays in
        # the village (closed supply — denominations can split it smaller
        # if it ever pools too deep).
        # No watch, no wages: Thornhollow keeps no guard and no government.
        # Free villagers feed themselves from work and trade (and the aid
        # below when truly broke). The kid charity stays — family, not state.
        for kid in [n for n in village.npcs if n.get("role") == "kid"]:
            for stall in village.stalls:  # ECON: the village feeds its own —
                try:  # two dawn dinners, whatever food is there (staples first)
                    from omnisim.entities.items import staples as _st
                    from omnisim.entities.items import foods as _foods
                    _order = list(_st()) + [f for f in _foods() if f not in _st()]
                    _staple = next((f for f in _order
                                    if stall.get("stock", {}).get(f, [0, 0])[0] >= 2),
                                   None)
                except Exception:
                    _staple = None
                if _staple is None:  # for small haulers; hunger doesn't haggle
                    continue
                shelf = stall.get("stock", {}).get(_staple, [0, 0])
                if shelf[0] >= 2:
                    shelf[0] -= 2
                    kid.get("pocket", {})[_staple] = \
                        kid.get("pocket", {}).get(_staple, 0) + 2
                    break
        try:  # P2: surplus-only aid — the village feeds its broke and hungry
            # from PLENTY, never from famine (famine-mood shelves stay shut,
            # so the no-baker ruling still bites). Makers draw raw stock
            # (flour/fuel = capital); everyone else draws a loaf.
            # STEP 2: plenty means FOOD EXISTS (any cover), hunger means
            # bellies say so — aid opens on the mismatch (full shelves,
            # empty bellies = distribution failure) and stays shut when the
            # bins are truly bare. Bellies-only locked aid during real
            # plenty (raw mountains, no makers) and starved the village.
            from omnisim.systems.needs import village_mood
            from omnisim.systems.schedule import WORK_NEEDS
            _belly = 0.0
            try:
                _hungers = [float(m.get("hunger", 0)) for m in village.npcs
                            if not m.comps.get("dead")]
                _belly = sum(_hungers) / max(1, len(_hungers)) / 80.0
            except Exception:
                pass
            _plenty = 0.0
            try:
                from omnisim.systems.pricing import cover_of as _cover_a
                for _fg in ("bread", "meal", "fish", "wheat", "flour",
                            "barley", "turnip", "berries", "apple"):
                    _plenty = max(_plenty, _cover_a(village, _fg))
            except Exception:
                pass
            _famine_shut = village_mood(village).get("famine", 0.0) >= 0.35 \
                and _plenty <= 1.0
            if not _famine_shut and (_belly > 0.35 or
                    village_mood(village).get("famine", 0.0) < 0.35):
                for npc in village.npcs:
                    if npc.comps.get("dead"):
                        continue
                    pocket = npc.get("pocket", {})
                    # STEP 2 restart width: makers draw raw stock while merely
                    # poor (5c) and peckish (40+) — a 2c baker with no grain
                    # restarts nothing. Dinners keep the tighter gate.
                    _maker_need = (WORK_NEEDS.get(npc.get("work_skill") or "")
                                   or (None,))[0]
                    if _maker_need is not None:
                        if pocket.get("coin", 0) > 5 or \
                                float(npc.get("hunger", 0)) < 40:
                            continue
                    elif pocket.get("coin", 0) > 2 or \
                            float(npc.get("hunger", 0)) < 50:
                        continue
                    need = (WORK_NEEDS.get(npc.get("work_skill") or "")
                            or (None,))[0]
                    try:  # dinner before capital: the starving draw food, not
                        # flour — the oven-tender eats first, grinds after.
                        from omnisim.systems.valuation import desperate as _desp
                        if _desp(npc):
                            need = None
                    except Exception:
                        pass
                    # Flour is never shelved: the oven draws grain to grind.
                    from omnisim.systems.schedule import RAW_OF as _RAW
                    if need is not None:
                        _have = any(stall.get("stock", {}).get(need, [0, 0])[0] > 0
                                    for stall in village.stalls)
                        if not _have and need in _RAW:
                            need = _RAW[need]
                    item = need or "bread"
                    qty = 2 if (need and item != "bread") else 1
                    try:  # the desperate eat twice: one loaf can't catch a
                        # three-day transition (career changes are hungry)
                        if not need and float(npc.get("hunger", 0)) >= 80:
                            qty = 2
                    except Exception:
                        pass
                    fullest = None
                    for stall in village.stalls:
                        shelf = stall.get("stock", {}).get(item, [0, 0])
                        if shelf[0] >= qty and (
                                fullest is None or shelf[0] > fullest[0]):
                            fullest = (shelf[0], stall)
                    if fullest is None:
                        # Last resort: any stocked food, staples first —
                        # charity of what's there (berries count when bread's gone).
                        if item != "fish":
                            try:
                                from omnisim.entities.items import staples as _st
                                from omnisim.entities.items import foods as _foods
                                _order = [s for s in _st()] + \
                                    [f for f in _foods() if f not in _st()] + ["fish"]
                            except Exception:
                                _order = ["fish"]
                            for _alt in _order:
                                for stall in village.stalls:
                                    shelf = stall.get("stock", {}).get(_alt, [0, 0])
                                    if shelf[0] >= 1 and (
                                            fullest is None or shelf[0] > fullest[0]):
                                        fullest = (shelf[0], stall)
                                if fullest is not None:
                                    item, qty = _alt, 1
                                    break
                    if fullest is None:
                        continue
                    fullest[1]["stock"][item][0] -= qty
                    pocket[item] = pocket.get(item, 0) + qty
                    try:  # STEP 4: aid lands as a remembered kindness (+6)
                        from omnisim.systems.mood import on as _m4a_on
                        from omnisim.systems.mood import add_event as _m4a_ev
                        if _m4a_on():
                            _m4a_ev(npc, village, 6.0, "aid")
                    except Exception:
                        pass
                    try:  # STEP 5: aid remembered as a market kindness
                        from omnisim.systems.memory import on as _m5a_on
                        from omnisim.systems.memory import log_event as _m5a_ev
                        if _m5a_on():
                            _m5a_ev(npc, village, "aid received", 0.3, subject="market")
                    except Exception:
                        pass
                    if need:  # STEP 2: grants book at fair, not a copper of
                        # found-cost — free seed that prices itself 1c grows
                        # 1c bread and the restart never floats. Aid is a
                        # grant of VALUE; the book respects it.
                        try:
                            from omnisim.systems.haggle import note_lot as _nl7
                            from omnisim.systems.pricing import fair_mc as _fair7
                            from omnisim.systems.pricing import MC as _MC7
                            _nl7(npc, item, qty,
                                 max(1, int(round(_fair7(village, item) / _MC7))))
                        except Exception:
                            pass
                    if need:
                        from omnisim.entities.items import item_name as _in
                        village.say(f"{npc.name} draws {_in(item)} from "
                                    f"{fullest[1].get('name', 'the market')}.")
                    else:
                        village.say(f"{npc.name} takes the charity loaf.")
        except Exception:
            pass
        # No house stipend, no wages: Bram's purse moves only on trade.
        village.say(f"Dawn breaks over {village.name} (Day {day_of(village.clock_pts)}).")
        try:  # V1 Foundations (GDD 8.3): relationships drift to neutral
            from omnisim.systems.relationships import decay_toward_neutral
            for _n in list(village.npcs) + [village.player]:
                decay_toward_neutral(_n, 1.0)
        except Exception:
            pass
        try:  # STEP 5: salience decay on bounded life events
            from omnisim.systems.memory import on as _m5d_on
            from omnisim.systems.memory import decay_memories as _m5d
            if _m5d_on():
                _m5d(village)
        except Exception:
            pass
        try:  # STEP 6: weekly intent-board clear (switch bids re-argue fresh)
            from omnisim.systems.decision import clear_board as _d6cb
            from omnisim.systems.decision import on as _d6cb_on
            if _d6cb_on() and int(day_of(village.clock_pts)) % 7 == 0:
                _d6cb(village)
        except Exception:
            pass
        village.made_today = {}  # CHAIN: yesterday's ledger closes, today's opens
        village.sold_today = {}
        fallen = [n for n in village.npcs if n.comps.get("dead")]
        if fallen:  # M6: the night takes the fallen; their loot stays behind
            # STEP 6 GATE (A-population): the register remembers every
            # founder: name, trade, last wealth, cause, day. Burial removes
            # the body from the roster, never from the books.
            try:
                from omnisim.core.clock import day_of as _fall_day
                _reg = getattr(village, "fallen_log", None)
                if not isinstance(_reg, list):
                    _reg = village.fallen_log = []
                _known = {r.get("name") for r in _reg if isinstance(r, dict)}
                for _f in fallen:
                    if _f.name not in _known:
                        _last_w = 0.0
                        try:
                            from omnisim.systems.metrics import npc_wealth as _fw
                            _last_w = float(_fw(village, _f))
                        except Exception:
                            pass
                        _reg.append({"name": _f.name,
                                       "trade": _f.get("work_skill") or _f.get("role") or "?",
                                       "wealth": round(_last_w, 1),
                                       "cause": _f.comps.get("death_cause", "hunger"),
                                       "day": int(_fall_day(village.clock_pts))})
                        _known.add(_f.name)
            except Exception:
                pass
            village.npcs = [n for n in village.npcs if not n.comps.get("dead")]
            village.say("The fallen were carried off at dawn.")


def _hunger_tick(village, mins: float) -> None:
    """Player metabolism — dynamic (GDD 3.1): base x trait x activity x
    environment x world x prev-state x (1 - skill). Base reproduces 1/25.
    Thirst and filth tick alongside (water runs faster than supper)."""
    from omnisim.systems.needs import (
        THIRST_HP_MIN, filth_tick, hunger_tick, thirst_tick, tick_wants)
    p = village.player
    was_starving = float(p.get("hunger", 0)) >= 100
    was_parched = float(p.get("thirst", 0)) >= 100
    activity = p.comps.get("activity", "idle")
    hunger_tick(p, mins, activity=activity, world=village)
    thirst_tick(p, mins, activity=activity)
    filth_tick(p, mins, village)
    tick_wants(p, mins, village)
    if p.comps.get("thirst", 0) >= 100 and not was_parched:
        village.say("Your throat closes. You are PARCHED.")
    if p.comps.get("thirst", 0) >= 100:
        p.comps["thirst_acc"] = float(p.get("thirst_acc", 0)) + mins
        while p.comps["thirst_acc"] >= THIRST_HP_MIN:
            p.comps["thirst_acc"] -= THIRST_HP_MIN
            if int(p.get("hp", 1)) > 1:
                p.comps["hp"] = int(p.get("hp", 1)) - 1
                village.say("Thirst hammers you! (-1 HP)")
            else:
                village.say("You dream of the well. Drink something!")
                break
    if p.comps["hunger"] >= 100 and not was_starving:
        village.say("Your belly screams. You are STARVING.")
    if p.comps["hunger"] >= 100:
        from omnisim.systems.perks import perk_value
        from omnisim.systems.skills import skill_level
        floor_hp = perk_value(skill_level(p, "Endurance"), "Endurance", "starve_floor", 1)
        p.comps["starve_acc"] = float(p.get("starve_acc", 0)) + mins
        while p.comps["starve_acc"] >= STARVE_HP_MIN:
            p.comps["starve_acc"] -= STARVE_HP_MIN
            if int(p.get("hp", 1)) > floor_hp:
                p.comps["hp"] = int(p.get("hp", 1)) - 1
                village.say("Starvation gnaws you! (-1 HP)")
            else:
                village.say("You refuse to die in a ditch. Eat something!")
                break




