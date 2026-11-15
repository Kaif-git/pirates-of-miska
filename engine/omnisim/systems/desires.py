"""Desires: scored bids for the brain loop. No thresholds, only arithmetic.

Two channels, no if-chains in the tick path:

- EXCLUSIVE feet: commitments first (rest-halt collapse veto, loaded
  hauls), then scored trips — market, wilds, well — highest value wins,
  then kid detours, then routine. Values come from systems/valuation:
  urgency curves times relief minus price pain and miles. A broke hungry
  soul walks to the meadow; a flush one shops — same formula, no rule.
- AMBIENT fires alongside (bites, repairs). A bite lands whenever its
  value beats what the hour defends: midnight hunger wakes, mid-shift
  hunger pauses, nibbles never interrupt.

New behavior = one scorer + one table line. Nothing here names a need
level; tuning lives in data/drives.yaml.
"""
from __future__ import annotations

# -- exclusive channel: who owns the feet --------------------------------

HALTED = "halted"
ROUTED = "routed"


def rest_halt(village, npc, blk):  # type: ignore[no-untyped-def]
    """Collapse veto, not a choice: at full exhaustion the body sits down
    where it stands. The line is physiology data (halt_line), not a rule
    about when rest is preferred - preference is rest_utility vs waking.
    Survival breaks the halt: the thirsty rise and walk (Wren died resting
    at thirst 116 with the well 16 tiles off - rest held, feet never bid)."""
    try:
        from omnisim.systems.valuation import drives, rest_utility
        try:
            from omnisim.systems.valuation import desperate as _desp2
            if _desp2(npc, "thirst") or _desp2(npc, "hunger"):
                if npc.comps.get("resting"):
                    npc.comps["resting"] = False
                return None
        except Exception:
            pass
        if rest_utility(npc) > float(drives().get("halt_line", 100.0)) \
                and not npc.comps.get("resting"):
            from omnisim.systems.schedule import seen_say
            npc.comps["resting"] = True
            npc.comps["dest"] = (npc.x, npc.y)
            npc.comps["dest_kind"] = "rest"
            npc.comps["path"] = []
            npc.comps["activity"] = "resting"
            seen_say(village, npc.x, npc.y,
                     f"{npc.name} sways with exhaustion and sits down.")
            return HALTED
    except Exception:
        pass
    return None
def errand_route(village, npc, blk):  # type: ignore[no-untyped-def]
    """Loaded pockets walk to market (existing errand state drives).
    Arm's-length handoffs hold even when survival calls (the coin buys
    dinner); only the walk suspends for thirst/hunger — goods kept, errand
    resumes after."""
    from omnisim.systems.schedule import ERRAND_ACTS, bfs_path, gm_of, standable_near
    errand = npc.comps.get("errand")
    if errand is None or blk["act"] not in ERRAND_ACTS:
        return None
    st = next((s for s in village.stalls
               if (s["x"], s["y"]) == (errand["x"], errand["y"])), None)
    if st is None:
        npc.comps["errand"] = None
        return None
    if max(abs(st["x"] - npc.x), abs(st["y"] - npc.y)) <= 3:
        # Arm's-length handoff: hold for this tick's sale even when survival
        # calls — the coin buys dinner. But only what the shelf still wants:
        # a refused haul clears, and feet go find dinner instead.
        from omnisim.systems.schedule import _market_appetite as _app
        try:
            if _app(village, npc) <= 0:
                npc.comps["errand"] = None
                return None
        except Exception:
            pass
        npc.comps["dest"] = (npc.x, npc.y)
        npc.comps["dest_kind"] = "errand"
        npc.comps["path"] = []
        return ROUTED
    try:
        from omnisim.systems.valuation import desperate as _desp
        if _desp(npc, "thirst") or _desp(npc, "hunger"):
            return None
    except Exception:
        pass
    dest = standable_near(village.game_map, st["x"], st["y"])
    npc.comps["dest"] = dest  # commit: the shared mover reads comps
    npc.comps["dest_kind"] = "errand"
    npc.comps["path"] = bfs_path(gm_of(village), (npc.x, npc.y), dest)
    return ROUTED


def _sleep_ok(npc) -> bool:
    """Sleep holds all night — unless survival breaks it. The starving wake
    and walk; the merely peckish sleep till morning."""
    try:
        from omnisim.systems.valuation import desperate as _desp
        return bool(_desp(npc, "hunger") or _desp(npc, "thirst"))
    except Exception:
        return False


def buy_trip(village, npc, blk):  # type: ignore[no-untyped-def]
    """Market bid: (value, dest, flag). Feet follow coin and closeness.
    Steer suspends hauls when survival calls — bidders never check errands."""
    if blk["act"] not in ("idle", "eat", "buy") and not _sleep_ok(npc):
        return None
    try:
        from omnisim.systems.valuation import buy_option
        from omnisim.systems.schedule import standable_near
        stall, _item, value = buy_option(village, npc)
        if stall is None or value <= 0:
            return None
        dest = standable_near(village.game_map, stall["x"], stall["y"])
        return (value, dest, "buy_trip")
    except Exception:
        return None


def forage_trip(village, npc, blk):  # type: ignore[no-untyped-def]
    """Wild-table bid: (value, dest, flag). Free food walks far."""
    if blk["act"] not in ("idle", "eat", "buy") and not _sleep_ok(npc):
        return None
    try:
        from omnisim.systems.valuation import forage_option
        dest, value = forage_option(village, npc)
        if dest is None or value <= 0:
            return None
        return (value, dest, "forage_trip")
    except Exception:
        return None


def drink_trip(village, npc, blk):  # type: ignore[no-untyped-def]
    """Well bid: (value, dest, flag). Water is free — distance argues.
    Parched feet outbid everything by arithmetic alone; no veto needed."""
    try:
        from omnisim.systems.valuation import drink_option
        dest, value = drink_option(village, npc)
        if dest is None or value <= 0:
            return None
        return (value, dest, "drink_trip")
    except Exception:
        return None


def kid_loot(village, npc, blk):  # type: ignore[no-untyped-def]
    """Small legs detour for spill worth hauling — dinner first when hungry."""
    from omnisim.systems.schedule import ERRAND_ACTS, _kid_loot_near, _sellable_total
    if npc.get("role") != "kid" or blk["act"] not in ERRAND_ACTS:
        return None
    try:
        from omnisim.systems.valuation import desperate as _desp
        _starving = bool(_desp(npc))
    except Exception:
        _starving = False
    from omnisim.systems.schedule import ERRAND_AT
    if _sellable_total(npc) >= ERRAND_AT and not _starving:
        return None
    dest = _kid_loot_near(village, npc, food_only=_starving)  # small legs, big job
    if dest is None and _starving:
        # No dinner on the ground: ordinary spill still pays.
        dest = _kid_loot_near(village, npc)
    if dest is None:
        return None
    from omnisim.systems.schedule import bfs_path, gm_of
    npc.comps["dest"] = dest
    npc.comps["dest_kind"] = "trip"
    npc.comps["path"] = bfs_path(gm_of(village), (npc.x, npc.y), dest)
    return ROUTED


def routine(village, npc, blk):  # type: ignore[no-untyped-def]
    """The day's intentions: idle wander or the block anchor. Always bids."""
    from omnisim.systems.schedule import _dest_for, _dest_for_idle, bfs_path, gm_of
    if blk["act"] == "idle":
        ax, ay = _dest_for_idle(village, npc)
        dest = (ax, ay)
    else:
        dest = _dest_for(village, npc, blk) or (npc.x, npc.y)
    npc.comps["dest"] = dest
    npc.comps["dest_kind"] = "routine"
    npc.comps["path"] = bfs_path(gm_of(village), (npc.x, npc.y), dest)
    return ROUTED


def procure_trip(village, npc, blk):  # type: ignore[no-untyped-def]
    """Procurement bid: cover below P days walks to a remembered seller.
    Flag procurement (default off). Urgent hunger/thirst veto inside;
    routine floor enforced by steer (brain.py), same as every trip."""
    try:
        from omnisim.systems.procurement import procure_option as _po
        res = _po(village, npc)
        if res is None:
            return None
        return (res[0], res[1], "procure_trip")
    except Exception:
        return None


SCORED = (buy_trip, forage_trip, drink_trip, procure_trip)


def routine_floor() -> float:
    try:
        from omnisim.systems.valuation import drives
        return float(drives().get("routine_bid", 1.0))
    except Exception:
        return 1.0


EXCLUSIVE = (rest_halt, errand_route, kid_loot, routine)


# -- ambient channel: fires alongside, never preempts ----------------------

def eat_bite(village, npc, blk):  # type: ignore[no-untyped-def]
    """One bite whenever its value beats what the hour defends — any block,
    midnight and mid-shift included. Loaded haulers don't nibble the
    merchandise short of desperation. Replaces every hunger threshold."""
    if npc.comps.get("errand") is not None:
        try:
            from omnisim.systems.valuation import desperate as _desp
            if not _desp(npc):
                return False
        except Exception:
            return False
    try:
        from omnisim.systems.valuation import bite_option, keep_of_block
        from omnisim.systems.valuation import drives as _eo_dr
        from omnisim.systems.production import _load as _eo_ld
        item_id, value = bite_option(npc)
        # EAT-OUTRANKS (flag eat_outranks_at_high_hunger, default off):
        # at or above the configured hunger, a held bite with positive
        # value outranks sleep and work (the keep check is skipped).
        # Below threshold, with no food, or flag off: HEAD behavior.
        try:
            _eo_on = bool(((_eo_ld("economy") or {}).get("flags", {})
                           or {}).get("eat_outranks_at_high_hunger", False))
            _eo_thr = float((_eo_dr() or {}).get("eat_outrank_hunger", 80.0))
            _eo_hunger = float(npc.get("hunger", 0))
            _outrank = bool(_eo_on and _eo_hunger >= _eo_thr
                            and item_id is not None and float(value) > 0)
        except Exception:
            _outrank = False
        if not _outrank and (item_id is None
                             or value <= keep_of_block(blk["act"])):
            return False
    except Exception:
        return False
    from omnisim.entities.items import food_value, item_name, lore_q, open_of, take_serving
    from omnisim.systems.needs import record_meal
    from omnisim.systems.schedule import seen_say
    pocket = npc.get("pocket", {})
    if not take_serving(pocket, open_of(npc), item_id):
        return False
    npc.comps["hunger"] = max(
        0.0, float(npc.get("hunger", 0)) - food_value(npc, item_id, lore_q(npc, item_id)))
    record_meal(npc, item_id)
    try:  # STEP 3: quality-want ledger (what fineness was actually eaten)
        from omnisim.systems.needs3 import note_quality as _nq
        _nq(npc, lore_q(npc, item_id))
    except Exception:
        pass
    npc.comps["activity"] = "eating"
    seen_say(village, npc.x, npc.y,
             f"{npc.name} stops for a bite ({item_name(item_id)}).")
    return True


def repair_opportunity(village, npc, blk):  # type: ignore[no-untyped-def]
    """Hands that know wood or iron true a failing shop mid-day — not just
    at dawn. Smiths and carpenters at rest fix it (once daily, fuel if carried)."""
    from omnisim.core.clock import day_of
    if npc.get("work_skill") not in ("Smithing", "Carpentry"):
        return False
    if int(npc.get("repair_day", -1)) == day_of(village.clock_pts):
        return False
    if str(npc.comps.get("activity", "")) not in (
            "resting", "glutted", "idle", "no-flour", "cold-forge", "broke-stall",
            "waiting", "eating"):
        return False
    from omnisim.systems.schedule import seen_say
    from omnisim.systems.workspaces import repair
    ws = getattr(village, "workspaces", {}) or {}
    worn = [(k, float(s.get("condition", 100.0))) for k, s in ws.items()
            if isinstance(s, dict) and float(s.get("condition", 100.0)) < 50.0]
    if not worn:
        return False
    key = min(worn, key=lambda kv: kv[1])[0]
    if npc.get("pocket", {}).get("firewood", 0) >= 1:
        npc.get("pocket", {})["firewood"] -= 1
        if npc.get("pocket", {})["firewood"] <= 0:
            del npc.get("pocket", {})["firewood"]
        repair(village, key, 25.0)
    else:
        repair(village, key, 10.0)  # no fuel: hammer straight
    npc.comps["repair_day"] = day_of(village.clock_pts)
    seen_say(village, npc.x, npc.y, f"{npc.name} trues the {key.lower()} shop.")
    return True


def direct_deal(village, npc, blk):  # type: ignore[no-untyped-def]
    """Neighbors trade over the fence: hungry mouths and dry bins buy one
    unit from adjacent surplus at haggled asks — coin pocket to pocket, no
    merchant in the middle. UAILS only (flag off: the counter serves all).
    One deal per tick; refusals still teach."""
    try:
        from omnisim.systems.haggle import deal_price, enabled
        if not enabled():
            return False
    except Exception:
        return False
    try:
        hunger = float(npc.get("hunger", 0))
    except Exception:
        hunger = 0.0
    pocket = npc.get("pocket", {}) or {}
    wants: list = []
    try:  # MAKER FIRST: dry bins cry before hungry bellies. A smith with no
        # ore walks past bread to buy stone; dinner waits one tick. Hunger
        # still gates the DEAL below (reserve kept); this only orders wants.
        from omnisim.systems.schedule import WORK_NEEDS as _WN0
        _need0 = (_WN0 or {}).get(npc.get("work_skill") or "")
        if _need0 and pocket.get(_need0[0], 0) < 1:
            wants.append(_need0[0])
    except Exception:
        pass
    if hunger >= 50:
        try:
            from omnisim.entities.items import eat_order
            wants += [f for f in eat_order()
                      if pocket.get(f, 0) < 1]
        except Exception:
            pass
    try:
        from omnisim.systems.schedule import WORK_NEEDS as _WN
        need = (_WN or {}).get(npc.get("work_skill") or "")
        if need and pocket.get(need[0], 0) < 1:
            wants.append(need[0])
    except Exception:
        pass
    if not wants or pocket.get("coin", 0) < 1:
        return False
    try:
        from omnisim.entities.items import coin_str as _csd
        from omnisim.entities.items import item_name as _in0
        from omnisim.systems.schedule import keep_of, seen_say
        from omnisim.systems.schedule import _record_trade as _rec
    except Exception:
        return False
    cands: list = []
    for other in village.npcs:
        if other is npc or other.comps.get("dead"):
            continue
        dist = max(abs(other.x - npc.x), abs(other.y - npc.y))
        if dist > 2:
            continue
        opocket = other.get("pocket", {}) or {}
        for want in wants:
            try:
                surplus = int(opocket.get(want, 0)) - int(keep_of(other, want))
            except Exception:
                continue
            if surplus < 1:
                continue
            try:
                from omnisim.systems.pricing import ask_mc as _amc
                from omnisim.systems.pricing import engine_on as _eon
                from omnisim.systems.pricing import settle as _stl
                est = _stl(npc, _amc(village, other, want, 1)) if _eon() else 1
            except Exception:
                est = 1
            try:
                from omnisim.systems.memory import on as _m5o
                from omnisim.systems.memory import partner_score as _m5s
                score = _m5s(npc, other.name, want, est, village, dist) if _m5o() else est
            except Exception:
                score = est
            cands.append((score, other, want))
    if not cands:
        return False
    _best = min(cands, key=lambda t: t[0])
    other = _best[1]
    want = _best[2]
    opocket = other.get("pocket", {}) or {}
    try:
        price, dealt = deal_price(village, other, npc, want, 1)
    except Exception:
        return False
    if not dealt or pocket.get("coin", 0) < price:
        return False  # one counter visit a tick, shut or open
    pocket["coin"] -= price
    opocket["coin"] = float(opocket.get("coin", 0)) + price  # CONSERVATION: never truncate copper receipts
    opocket[want] -= 1
    if opocket[want] <= 0:
        del opocket[want]
    pocket[want] = int(pocket.get(want, 0)) + 1
    _rec(village, other.name, npc.name, want, 1, price)
    try:  # every bought dinner counts: peer deals move buys too
        npc.comps["buys"] = int(npc.get("buys", 0)) + 1
    except Exception:
        pass
    try:  # STEP 6: fence-deal spend tracking (budget EMA in decision engine)
        from omnisim.systems.decision import note_spend as _d6ns2
        from omnisim.systems.decision import on as _d6ns2_on
        if _d6ns2_on():
            _d6ns2(npc, float(price))
    except Exception:
        pass
    try:  # FIFO LOTS: the fence moves book with goods, both sides
        from omnisim.systems.haggle import note_lot as _nld, pop_lots as _pld
        _pld(other, want, 1)
        _nld(npc, want, 1, price)
    except Exception:
        pass
    npc.comps["activity"] = "trading"
    try:  # STEP 5: gossip the fence deal (seeded RNG, never loud)
        from omnisim.systems.memory import gossip as _m5gos
        from omnisim.systems.memory import on as _m5g
        from omnisim.systems.mood import _t as _m5t
        _rng5 = getattr(village, "rng", None) or __import__("random")
        if _m5g() and float(_rng5.random()) < float(_m5t(npc, "sociability")):
            _m5gos(npc, other, village)
    except Exception:
        pass
    seen_say(village, npc.x, npc.y,
             f"{npc.name} buys {_in0(want)} from {other.name} "
             f"({_csd(price)} over the fence).")
    return True


AMBIENT = (eat_bite, repair_opportunity, direct_deal)
