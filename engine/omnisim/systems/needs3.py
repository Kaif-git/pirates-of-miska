"""Step 3: real needs and wants.

Need channels (hunger, thirst, rest, hp exist; cold is new) share one pain
curve: pain_n(L) = w_n * 100 * (L/100)^2. Marginal value of a good is the
pain it relieves, converted to milli-copper by kappa (wealth equalizer:
richer purses pay more per unit of relief).

Cold model: season ambient minus insulation (worn garments + shelter +
lit home hearth) drives the cold gauge; cold burns hunger faster and
shortens sleep. Hearths burn firewood per household while someone is home.
Worn garments wear out with use. Wants (variety, quality, comfort, status)
saturate and nudge bids; comfort/status have no goods to buy yet, so they
are tracked and exposed for Step 4.

Flag-gated: everything here is inert unless economy.yaml flags.step_3 is
true. Default season is autumn (load 2, covered by cabin shelter), so the
flag alone changes nothing measurable until winter or a snap.
"""
from __future__ import annotations

import math

_NEEDS3: dict = {}


def on() -> bool:
    try:
        from omnisim.systems.production import flag_on
        return bool(flag_on("step_3"))
    except Exception:
        return False


def _cfg() -> dict:
    global _NEEDS3
    if not _NEEDS3:
        try:
            from omnisim.systems.production import _load
            _NEEDS3 = dict(_load("economy").get("needs3", {}) or {})
        except Exception:
            _NEEDS3 = {}
    return _NEEDS3


def reset_cache() -> None:
    global _NEEDS3
    _NEEDS3 = {}


def weights() -> dict:
    return dict(_cfg().get("weights", {}) or {})


def weight(need: str) -> float:
    return float(weights().get(need, 1.0))


def pain(need: str, level: float) -> float:
    """Convex pain: small discomfort is cheap, severe is expensive."""
    L = max(0.0, min(100.0, float(level)))
    return weight(need) * 100.0 * (L / 100.0) ** 2


def level_of(npc, need: str) -> float:
    """Gauge 0..100 for each channel (0 fine). Rest reads fatigue."""
    try:
        if need == "rest":
            return max(0.0, min(100.0, float(npc.get("fatigue", 0))))
        if need == "hp":
            mx = max(1.0, float(npc.get("max_hp", 10)))
            return max(0.0, min(100.0, (mx - float(npc.get("hp", mx))) / mx * 100.0))
        return max(0.0, min(100.0, float(npc.get(need, 0))))
    except Exception:
        return 0.0


def satisfies_of(good: str) -> dict:
    try:
        from omnisim.systems.production import goods
        return dict((goods().get(good, {}) or {}).get("satisfies", {}) or {})
    except Exception:
        return {}


def provides_of(good: str) -> dict:
    try:
        from omnisim.systems.production import goods
        return dict((goods().get(good, {}) or {}).get("provides", {}) or {})
    except Exception:
        return {}


def _sat_relief(npc, need: str, amount: float, _level=None) -> float:
    L = float(_level) if _level is not None else level_of(npc, need)
    return pain(need, L) - pain(need, max(0.0, L - amount))


def relief_units(npc, good: str, village=None, _hunger_override=None) -> float:
    """Pain relief one more unit buys, in pain units.

    satisfies.* applies directly (hunger/thirst/rest/hp). provides.warmth
    (hearth fuel) relieves cold through the hearth math; provides.insulation
    (garments) relieves cold as a durable stream (Step 4 refines discounting
    with patience; 0.7 flat until then).
    """
    sat = satisfies_of(good)
    total = 0.0
    for need, amt in sat.items():
        if need in ("hunger", "thirst", "rest", "hp", "cold", "heal"):
            _nn = "hp" if need == "heal" else need
            _lv = _hunger_override if (_hunger_override is not None and _nn == "hunger") else None
            total += _sat_relief(npc, _nn, float(amt), _lv)
    prov = provides_of(good)
    if prov.get("warmth"):
        total += _hearth_relief(npc, village)
    if prov.get("insulation"):
        total += _garment_stream(npc, village, good)
    if prov.get("fertility"):
        total += _field_relief(npc, village)
    return max(0.0, total)


def _field_relief(npc, village=None) -> float:
    """Dinner the compost grows: +1 heart of earth is +1 sheaf per cut
    (harvest_yield = crop yield + fert), and each sheaf is hunger relief
    at the farmer's own gauge. Generic: any good providing fertility
    reads this way, never a named-good branch."""
    try:
        from omnisim.systems.crops import ensure_plots as _epf
        from omnisim.systems.crops import spec_of as _spf
        from omnisim.entities.items import food_value as _fvf
        _plots = _epf(village) if village is not None else []
        _hungry = [pl for pl in _plots if int(pl.get("fert", 0)) < 3]
        if not _hungry:
            return 0.0
        _hunger = level_of(npc, "hunger")
        # one fed heart: +1 sheaf each harvest until spent. Value it as
        # one crop-share of dinner relief at a WORKING hunger (the field
        # test buys at hunger 0: sheaves still feed, so price the relief
        # a midday belly feels). Raw grain has no food value; bread does.
        _rel = 0.0
        try:
            _at = _hunger if _hunger > 0 else 50.0
            _rel = max(0.0, pain("hunger", _at)
                       - pain("hunger", max(0.0, _at - float(_fvf(npc, "bread", 1)))))
        except Exception:
            pass
        return max(0.0, _rel)
    except Exception:
        return 0.0


def _hearth_relief(npc, village=None) -> float:
    """Cold pain one firewood avoids: one burn (40 ticks) of hearth bonus
    against today's deficit."""
    try:
        bonus = float(_cfg().get("hearth_bonus", 5))
        burn = float(_cfg().get("hearth_burn_ticks", 40))
        deficit = max(0.0, cold_load(village) - _dry_insulation(npc, village))
        dL = 0.05 * min(bonus, deficit) * burn
        L = level_of(npc, "cold")
        return pain("cold", L) - pain("cold", max(0.0, L - dL))
    except Exception:
        return 0.0


def _garment_stream(npc, village, good: str) -> float:
    """Stream of relief over remaining life: daily pain avoided times
    remaining days times a flat 0.7 (Step 4 patience refines this)."""
    try:
        ins = float(provides_of(good).get("insulation", 0))
        if ins <= 0:
            return 0.0
        life = _lifespan_days(good)
        worn = (npc.get("worn", {}) or {}) if hasattr(npc, "get") else {}
        cond = float(worn.get(good, 100.0))
        remaining = max(0.0, cond / 100.0) * life
        if remaining <= 0:
            return 0.0
        L = level_of(npc, "cold")
        dPain_dL = 2 * weight("cold") * L / 100.0
        daily = 0.05 * ins * 144 * dPain_dL
        return daily * remaining * 0.7
    except Exception:
        return 0.0


def _lifespan_days(good: str) -> float:
    try:
        from omnisim.systems.production import goods
        return float(((goods().get(good, {}) or {}).get("decay", {}) or {}).get(
            "lifespan_days", 50))
    except Exception:
        return 50.0


# --- kappa: pain units to milli-copper --------------------------------------

def purse_ref(village) -> float:
    """Wealth anchor: village median purse (coin on backs)."""
    try:
        purses = sorted(float((n.get("pocket", {}) or {}).get("coin", 0))
                        for n in (getattr(village, "npcs", []) or [])
                        if not (n.comps.get("dead") if hasattr(n, "comps") else False))
        if purses:
            return max(1.0, purses[len(purses) // 2])
    except Exception:
        pass
    return 20.0


def kappa_mc(village, npc) -> float:
    """mc per pain unit. sqrt(1+purse) term: coin pools stop being inert.
    STEP 4: thrift lowers kappa (misers bid less per unit of relief)."""
    try:
        k0 = float(_cfg().get("kappa0", 130))
        purse = float((npc.get("pocket", {}) or {}).get("coin", 0))
        ref = purse_ref(village)
        k = k0 * math.sqrt(1.0 + max(0.0, purse) / max(1.0, ref))
        try:
            from omnisim.systems.mood import kappa_factor as _kf
            k *= _kf(npc)
        except Exception:
            pass
        return k
    except Exception:
        return 130.0


def marginal_value_mc(village, buyer, good: str, q: int = 1) -> float:
    """Step 3 bid basis: need relief plus want premiums, in milli-copper."""
    base = relief_units(buyer, good, village) * kappa_mc(village, buyer)
    base += _want_premium_mc(village, buyer, good, q)
    if base > 0:
        return base
    try:  # CHAIN-CLEARING (flagged): intermediates value downstream
        from omnisim.systems.chainvalue import on as _cc_on
        from omnisim.systems.chainvalue import chain_value as _ccv
        if _cc_on():
            _cv = _ccv(village, buyer, good, q)
            if _cv is not None and _cv > 0:
                return _cv
    except Exception:
        pass
    try:  # nothing to relieve: a slight premium over fair (old behavior)
        from omnisim.systems.pricing import fair_mc
        return fair_mc(village, good, q) * 1.2
    except Exception:
        return 0.0


# --- wants ------------------------------------------------------------------

def _want_params(channel: str) -> tuple:
    try:
        node = (_cfg().get("wants", {}) or {}).get(channel, {}) or {}
        return float(node.get("W", 10)), float(node.get("s", 2.0))
    except Exception:
        return 10.0, 2.0


def want_U(channel: str, x: float) -> float:
    """Saturating utility: diminishing returns on every channel."""
    W, s = _want_params(channel)
    return W * (1.0 - math.exp(-max(0.0, x) / max(0.1, s)))


def _recent_qualities(npc) -> list:
    try:
        rq = npc.get("recent_qualities", None) if hasattr(npc, "get") else None
        return list(rq) if isinstance(rq, list) else []
    except Exception:
        return []


def note_quality(npc, q: int) -> None:
    """Parallel ledger to recent_meals: quality of food actually eaten."""
    try:
        rq = npc.get("recent_qualities", None)
        if not isinstance(rq, list):
            rq = []
            npc.comps["recent_qualities"] = rq
        rq.append(int(q))
        del rq[:-6]
    except Exception:
        pass


def want_x(npc, channel: str, village=None) -> float:
    try:
        if channel == "variety":
            recent = npc.get("recent_meals", []) if hasattr(npc, "get") else []
            return float(len(set(recent[-6:]))) if isinstance(recent, list) else 0.0
        if channel == "quality":
            rq = _recent_qualities(npc)
            return sum(rq) / len(rq) if rq else 1.0
        if channel == "comfort":
            x = 0.0
            try:
                if hearth_lit(village, npc):
                    x += 1.0
            except Exception:
                pass
            if shelter_of(npc) >= 3:
                x += 1.0
            return x
        if channel == "status":
            worn = (npc.get("worn", {}) or {}) if hasattr(npc, "get") else {}
            x = float(len(worn))
            try:
                from omnisim.systems.production import goods_with_tag
                pocket = npc.get("pocket", {}) or {}
                for g in goods_with_tag("trophy"):
                    if pocket.get(g, 0) > 0:
                        x += 1.0
            except Exception:
                pass
            return x
    except Exception:
        pass
    return 0.0


def npc_wants(npc, village=None) -> dict:
    """Tracked want state, for Step 4 mood and tests."""
    return {ch: {"x": want_x(npc, ch, village), "U": want_U(ch, want_x(npc, ch, village))}
            for ch in ("variety", "quality", "comfort", "status")}


def _want_premium_mc(village, buyer, good: str, q: int) -> float:
    """Discretionary nudge: untried foods and finer-than-usual quality cost
    extra when the matching want is unsaturated. Needs dominate (W ~10 vs
    meal relief ~30); wants whisper."""
    try:
        from omnisim.entities.items import _items
        if float((_items().get(good, {}) or {}).get("food", 0)) <= 0:
            return 0.0
    except Exception:
        return 0.0
    try:
        k = kappa_mc(village, buyer)
        prem = 0.0
        recent = buyer.get("recent_meals", []) if hasattr(buyer, "get") else []
        recent = recent if isinstance(recent, list) else []
        if good not in recent[-6:]:
            x = float(len(set(recent[-6:])))
            prem += want_U("variety", x + 1) - want_U("variety", x)
        rq = _recent_qualities(buyer)
        avg = sum(rq) / len(rq) if rq else 1.0
        if q > avg:
            W, s = _want_params("quality")
            dU = W / max(0.1, s) * math.exp(-avg / max(0.1, s)) * (q - avg) / 7.0
            prem += max(0.0, dU)
        try:  # STEP 4: luxury pays for refinement (the lavish bid higher)
            from omnisim.systems.mood import want_factor as _wf
            prem *= _wf(buyer)
        except Exception:
            pass
        return prem * k
    except Exception:
        return 0.0


# --- cold model ---------------------------------------------------------------

def season_of(village) -> str:
    try:
        s = getattr(village, "season", None) or "autumn"
        return str(s)
    except Exception:
        return "autumn"


def ambient_of(village) -> float:
    try:
        amb = (_cfg().get("ambient", {}) or {})
        a = float(amb.get(season_of(village), 8))
    except Exception:
        a = 8.0
    try:  # snaps bite 4 warmth units off any season
        from omnisim.systems.weather import chill_of
        if chill_of(village) > 0:
            a -= float(_cfg().get("snap_delta", 4))
    except Exception:
        pass
    return a


def cold_load(village) -> float:
    try:
        return max(0.0, float(_cfg().get("comfort_threshold", 10)) - ambient_of(village))
    except Exception:
        return 0.0


def shelter_of(npc) -> float:
    try:
        return float(npc.get("shelter", _cfg().get("shelter_default", 3)))
    except Exception:
        return 3.0


def worn_insulation(npc) -> float:
    """Worn garments: ins scaled by condition (0.5 + 0.5*cond/100)."""
    try:
        worn = (npc.get("worn", {}) or {}) if hasattr(npc, "get") else {}
        total = 0.0
        for good, cond in worn.items():
            ins = float(provides_of(good).get("insulation", 0))
            if ins > 0:
                total += ins * (0.5 + 0.5 * max(0.0, min(100.0, float(cond))) / 100.0)
        return total
    except Exception:
        return 0.0


def _household_id(npc) -> tuple:
    try:
        anch = (npc.get("anchors", {}) or {}).get("home", None)
        if anch is not None:
            return ("home", int(anch[0]), int(anch[1]))
    except Exception:
        pass
    try:
        return ("soul", str(npc.get("name", "?")))
    except Exception:
        return ("soul", "?")


def _households(village) -> dict:
    out: dict = {}
    try:
        for n in (getattr(village, "npcs", []) or []):
            try:
                if n.comps.get("dead"):
                    continue
            except Exception:
                pass
            out.setdefault(_household_id(n), []).append(n)
    except Exception:
        pass
    return out


def _near_home(npc) -> bool:
    try:
        anch = (npc.get("anchors", {}) or {}).get("home", None)
        if anch is None:
            return True
        if str(npc.comps.get("activity", "")).startswith("sleep"):
            return True
        return max(abs(npc.x - anch[0]), abs(npc.y - anch[1])) <= 10
    except Exception:
        return True


def hearth_lit(village, npc) -> bool:
    """Home hearth is lit: cold outside, someone home, wood to burn."""
    try:
        state = getattr(village, "hearth_state", None) or {}
        return bool(state.get(_household_id(npc), {}).get("lit", False))
    except Exception:
        return False


def hearth_tick(village, mins: float) -> None:
    """Household hearths: burn firewood while cold and someone is home.

    One hearth per household (grouped by home anchor), 1 firewood per 40
    ticks lit. Burns from the pocket of a member who is home; a woodless
    household sits cold. This is the standing winter demand for cutters."""
    if cold_load(village) <= 0:
        try:
            village.hearth_state = {}
        except Exception:
            pass
        return
    try:
        burn_ticks = float(_cfg().get("hearth_burn_ticks", 40))
    except Exception:
        burn_ticks = 40.0
    try:
        state = getattr(village, "hearth_state", None)
        if not isinstance(state, dict):
            state = {}
            village.hearth_state = state
    except Exception:
        state = {}
    for hh, members in _households(village).items():
        rec = state.get(hh) if isinstance(state.get(hh), dict) else {}
        home = [m for m in members if _near_home(m)]
        if not home:
            rec = {"lit": False, "acc": 0.0}
            state[hh] = rec
            continue
        acc = float(rec.get("acc", 0.0)) + max(0.0, mins) / 10.0
        lit = any(int((m.get("pocket", {}) or {}).get("firewood", 0)) > 0
                  for m in home)
        while acc >= burn_ticks and lit:
            acc -= burn_ticks
            donor = next((m for m in home
                          if int((m.get("pocket", {}) or {}).get("firewood", 0)) > 0),
                         None)
            if donor is None:
                lit = False
                acc = 0.0
                break
            pk = donor.get("pocket", {}) or {}
            pk["firewood"] -= 1
            if pk["firewood"] <= 0:
                pk.pop("firewood", None)
            lit = any(int((m.get("pocket", {}) or {}).get("firewood", 0)) > 0
                      for m in home)
        state[hh] = {"lit": lit, "acc": acc}


def _dry_insulation(npc, village) -> float:
    """Insulation without the hearth (for hearth-relief math)."""
    try:
        bonus = float(_cfg().get("hearth_bonus", 5)) if hearth_lit(village, npc) else 0.0
        return worn_insulation(npc) + shelter_of(npc) + bonus
    except Exception:
        return worn_insulation(npc) + shelter_of(npc)


def insulation_of(village, npc) -> float:
    try:
        bonus = float(_cfg().get("hearth_bonus", 5)) if hearth_lit(village, npc) else 0.0
        return worn_insulation(npc) + shelter_of(npc) + bonus
    except Exception:
        return 0.0


def _wear_garments(npc, village, mins: float) -> None:
    """Auto-wear insulation from the pocket; wear it down while cold."""
    try:
        pocket = npc.get("pocket", {}) or {}
        worn = npc.get("worn", None)
        if not isinstance(worn, dict):
            worn = {}
            npc.comps["worn"] = worn
        from omnisim.systems.production import goods
        for gid, g in goods().items():
            if float((g.get("provides", {}) or {}).get("insulation", 0)) <= 0:
                continue
            if pocket.get(gid, 0) <= 0:
                worn.pop(gid, None)
                continue
            worn.setdefault(gid, 100.0)
        if cold_load(village) <= 0:
            return
        for gid in list(worn.keys()):
            life = _lifespan_days(gid)
            worn[gid] = float(worn[gid]) - (100.0 / (max(1.0, life) * 144.0)) * max(0.0, mins) / 10.0
            if worn[gid] <= 0:
                if pocket.get(gid, 0) > 0:  # worn through: next unit off the pile
                    pocket[gid] -= 1
                    if pocket[gid] <= 0:
                        pocket.pop(gid, None)
                if pocket.get(gid, 0) > 0:
                    worn[gid] = 100.0
                else:
                    worn.pop(gid, None)
    except Exception:
        pass


def cold_tick(npc, mins: float, village=None) -> float:
    """Advance the cold gauge. Returns the delta."""
    try:
        load = cold_load(village)
        deficit = load - insulation_of(village, npc)
        cur = level_of(npc, "cold")
        if deficit > 0:
            delta = float(_cfg().get("cold_gain", 0.05)) * deficit * max(0.0, mins) / 10.0
        else:
            delta = -float(_cfg().get("cold_recovery", 1.0)) * max(0.0, mins) / 10.0
        new = max(0.0, min(100.0, cur + delta))
        npc.comps["cold"] = new
        _wear_garments(npc, village, mins)
        return new - cur
    except Exception:
        return 0.0


# --- validator ---------------------------------------------------------------

def validate_goods() -> list[str]:
    """Every good either satisfies a need/want, provides a stream, or is an
    input to a recipe whose output does (A8: one mouth, one signal — demand
    must trace to a need, never to a shelf)."""
    errs: list[str] = []
    try:
        from omnisim.systems.production import goods, recipes, trades
        g = goods()
        recs = recipes()
        input_goods: set[str] = set()
        input_tags: set[str] = set()
        for r in recs.values():
            for inp in r.get("inputs", []) or []:
                if "good" in inp:
                    input_goods.add(inp["good"])
                elif "tag" in inp:
                    input_tags.add(inp["tag"])
        tool_tags: set[str] = set()
        for t in trades().values():
            if t.get("tool_tag"):
                tool_tags.add(t["tool_tag"])
            for up in t.get("tick_upkeep", []) or []:  # forge fuel, lamp oil...
                if up.get("tag"):
                    input_tags.add(up["tag"])
        for gid, row in g.items():
            if row.get("satisfies"):
                continue
            if row.get("provides"):
                continue
            if gid in input_goods:
                continue
            if gid in tool_tags:
                continue
            if set(row.get("tags", []) or []) & input_tags:
                continue
            errs.append(f"goods.yaml: {gid} traces to no need, want, stream, or recipe")
    except Exception as e:
        errs.append(f"validator failed: {e}")
    return errs
