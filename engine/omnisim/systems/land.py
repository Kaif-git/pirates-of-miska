"""Step 9: plot-level land (deeds earn, ground has a price).

- Plots carry fertility/soil, adjacent_resources, build_slots (data rows).
- Build rights: owners build on their own plots; anyone may build on
  Crown plots with a null stream (the commons, permit-free).
- Price discovery: land_value = best-use expected net income/day x
  horizon_days (capitalized income), par deeds as starting values only.
- Office: one deed/day; rich buy at >= 2.5x capitalized value;
  desperate sell back to the Crown at 70% of capitalized value.
- Wealth counts deeds at capitalized income, not par (no phantom rank).
All inert when flags.step_9 is off (office falls back to par rules).
"""
from __future__ import annotations

_DEFAULTS = {"horizon_mult": 1.0, "buy_mult": 2.5, "buyback_frac": 0.7,
             "tolerance": 0.4}

_CROWN = "Crown"


def _econ():
    try:
        from omnisim.systems.production import _load as _ld
        return _ld("economy") or {}
    except Exception:
        return {}


def on():
    try:
        return bool((_econ().get("flags", {}) or {}).get("step_9", False))
    except Exception:
        return False


def _cfg(key):
    try:
        return (_econ().get("land", {}) or {}).get(key, _DEFAULTS[key])
    except Exception:
        return _DEFAULTS[key]


def parcels_of(village):
    """Live deeds: village-carried so sales persist (same source as office)."""
    try:
        from omnisim.systems.ownership import parcels_of as _parcels
        return _parcels(village)
    except Exception:
        return []


def best_use_income(village, parcel):
    """Copper/day the best use of this plot earns: a day's honest work on
    this ground (best recipe margin for the stream's skill), of which the
    deed takes the tithe share — fertile ground scales the harvest."""
    try:
        good = parcel.get("good")
        if not good:
            return 0.0
        from omnisim.systems.decision import _best_recipe_margin as _marg
        from omnisim.systems.decision import on as _d6on
        from omnisim.systems.production import expected_sale_price as _esp
        from omnisim.systems.production import saleability as _sal
        skill = None
        try:
            from omnisim.systems.production import recipes as _recs
            for r in _recs().values():
                outs = [o.get("good") for o in r.get("outputs", []) or []]
                if good in outs:
                    skill = r.get("skill")
                    break
        except Exception:
            pass
        day = 0.0
        if skill and _d6on():
            try:
                probe = next((n for n in getattr(village, "npcs", []) or []
                              if (n.get("work_skill") or "") == skill), None)
                if probe is not None:
                    day = max(0.0, float(_marg(village, probe, skill)))
            except Exception:
                pass
        # stream price x saleability x a day's handfuls: the same ruler for
        # every parcel, fresh village or trading one — margins join when
        # the tape gives them something honest to say (best of the two).
        price = max(0.0, float(_esp(village, good)))
        flow = max(0.0, min(1.0, float(_sal(village, good))))
        day = max(day, price * flow * 10.0)
        fert = float(parcel.get("fertility", None)
                     if parcel.get("fertility") is not None
                     else parcel.get("soil", 1.0) or 1.0)
        try:
            from omnisim.systems.ownership import TITHE_EVERY as _TE
            share = 1.0 / max(1.0, float(_TE))
        except Exception:
            share = 0.25
        return day * max(0.5, fert) * share
    except Exception:
        return 0.0


def land_value(village, parcel):
    """Capitalized income: best-use copper/day x horizon days. Par deeds
    are starting values only — the quote moves with the harvest."""
    try:
        horizon = 30.0
        try:
            from omnisim.systems.mood import horizon_days as _hz
            # patient ground, patient price: use the village median patience
            hs = []
            for n in getattr(village, "npcs", []) or []:
                try:
                    hs.append(float(_hz(n)))
                except Exception:
                    pass
            if hs:
                hs.sort()
                horizon = hs[len(hs) // 2]
        except Exception:
            pass
        horizon *= float(_cfg("horizon_mult"))
        cap = best_use_income(village, parcel) * horizon
        if cap > 0:
            return max(1.0, cap)
    except Exception:
        pass
    try:
        return max(1.0, float(parcel.get("price", 1) or 1))
    except Exception:
        return 1.0


def quote(village, parcel):
    """Asking price today: capitalized value when Step 9 is on, else par."""
    try:
        if on():
            return max(1, int(round(land_value(village, parcel))))
    except Exception:
        pass
    try:
        return max(1, int(parcel.get("price", 1) or 1))
    except Exception:
        return 1


def deed_value(village, owner_name):
    """All deeds one soul holds, at capitalized income (wealth math)."""
    try:
        total = 0.0
        for p in parcels_of(village) or []:
            if p.get("owner") == owner_name:
                total += land_value(village, p) if on() else float(p.get("price", 0) or 0)
        return total
    except Exception:
        return 0.0


def buy_parcel(village, buyer_pocket, buyer_name, parcel):
    """Buy a Crown deed at the capitalized quote: coin into the reserve."""
    from omnisim.entities.items import coin_str
    try:
        if parcel.get("owner") != _CROWN:
            return "%s is %s's ground - not the Crown's to sell." % (
                parcel.get("name"), parcel.get("owner"))
        price = quote(village, parcel)
        if int((buyer_pocket or {}).get("coin", 0)) < price:
            return "%s costs %s - beyond your purse." % (
                parcel.get("name"), coin_str(price))
        buyer_pocket["coin"] = float(buyer_pocket.get("coin", 0)) - price  # CONSERVATION: exact copper
        try:
            stall = (village.stalls or [None])[0]
            if stall is not None:
                stall["till"] = float(stall.get("till", 0) or 0) + price
        except Exception:
            pass
        parcel["owner"] = buyer_name
        try:
            parcel["paid"] = int(price)  # wealth counts paid coin, not quotes
        except Exception:
            pass
        try:
            from omnisim.systems.ownership import sync_plots
            sync_plots(village)
        except Exception:
            pass
        try:
            sales = getattr(village, "land_sales", None)
            if not isinstance(sales, list):
                sales = village.land_sales = []
            from omnisim.core.clock import day_of as _d
            sales.append({"parcel": parcel.get("id"), "price": price,
                          "value": land_value(village, parcel),
                          "day": int(_d(village.clock_pts))})
        except Exception:
            pass
        return "%s buys %s from the Crown for %s." % (
            buyer_name, parcel.get("name"), coin_str(price))
    except Exception:
        return "The office stamps nothing today."


def sell_parcel(village, seller_pocket, seller_name, parcel):
    """Sell a deed back to the Crown at 70% of capitalized value."""
    from omnisim.entities.items import coin_str
    try:
        if parcel.get("owner") != seller_name:
            return "%s isn't yours to sell." % parcel.get("name")
        price = max(1, int(round(land_value(village, parcel) * float(_cfg("buyback_frac"))))) if on() \
            else max(1, int(round(int(parcel.get("price", 1) or 1) * 0.7)))
        try:
            stall = (village.stalls or [None])[0]
            if stall is not None and int(stall.get("till", 0)) < price:
                return "The Crown's purse is light - no buyback today."
            if stall is not None:
                stall["till"] = float(stall.get("till", 0) or 0) - price
        except Exception:
            pass
        seller_pocket["coin"] = float((seller_pocket or {}).get("coin", 0)) + price  # CONSERVATION: exact copper
        parcel["owner"] = _CROWN
        try:
            from omnisim.systems.ownership import sync_plots
            sync_plots(village)
        except Exception:
            pass
        return "%s sells %s back to the Crown for %s." % (
            seller_name, parcel.get("name"), coin_str(price))
    except Exception:
        return "The office stamps nothing today."


def office(village):
    """Dawn at the Crown's office: one deed moves, at most. Desperate
    souls sell ground to eat; rich souls buy capitalized income."""
    try:
        from omnisim.systems.schedule import seen_say
    except Exception:
        def seen_say(v, x, y, msg):
            pass
    try:
        live = [n for n in village.npcs
                if not n.comps.get("dead") and n.get("role") != "shopkeeper"]
        for n in sorted(live, key=lambda m: -int(m.get("hunger", 0))):
            if int(n.get("hunger", 0)) >= 70 and int(n.get("pocket", {}).get("coin", 0)) < 20:
                mine = [p for p in parcels_of(village) or [] if p.get("owner") == n.name]
                if mine:
                    parcel = max(mine, key=lambda p: land_value(village, p))
                    seen_say(village, n.x, n.y,
                             sell_parcel(village, n.get("pocket", {}), n.name, parcel))
                    return
        crown = [p for p in parcels_of(village) or [] if p.get("owner") == _CROWN]
        if not crown:
            return
        mult = float(_cfg("buy_mult"))
        # profitable chains pull adjacent ground: parcels feeding a wanted
        # good rank ahead of bare meadows.
        def _rank(p):
            try:
                from omnisim.systems.metrics import unmet_summary as _u
                dem = sum(vv for k, vv in _u(village).items()
                          if k == "demand:%s" % (p.get("good") or ""))
                return (dem > 0, land_value(village, p))
            except Exception:
                return (False, 0.0)
        for n in sorted(live, key=lambda m: -int(m.get("pocket", {}).get("coin", 0))):
            purse = int(n.get("pocket", {}).get("coin", 0))
            for parcel in sorted(crown, key=_rank, reverse=True):
                if purse >= mult * land_value(village, parcel):
                    seen_say(village, n.x, n.y,
                             buy_parcel(village, n.get("pocket", {}), n.name, parcel))
                    return
    except Exception:
        pass
