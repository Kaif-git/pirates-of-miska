"""Step 7: buildings and investment (owner-operator, own purse only).

Buildings add station SLOTS (passive throughput) on owned/Crown-null land.
Investment: payback <= horizon x risk_adjust, purse kept above reserve,
one active project per NPC, 7-day same-blueprint dedup. All inert when
flags.step_7 is off.
"""
from __future__ import annotations

_DEFAULTS = {"scan_days": 7, "horizon_mult": 1.0, "risk_adjust": 0.8,
             "min_gain": 0.5, "upkeep_base": 1.0, "decay_per_day": 0.5,
             "derelict_days": 30, "escrow_pct": 0.5, "batches_per_slot": 6}

_BP_CACHE: dict | None = None


def _econ():
    try:
        from omnisim.systems.production import _load as _ld
        return _ld("economy") or {}
    except Exception:
        return {}


def on():
    try:
        return bool((_econ().get("flags", {}) or {}).get("step_7", False))
    except Exception:
        return False


def _cfg(key):
    try:
        return (_econ().get("buildings", {}) or {}).get(key, _DEFAULTS[key])
    except Exception:
        return _DEFAULTS[key]


def blueprints():
    global _BP_CACHE
    if _BP_CACHE is not None:
        return _BP_CACHE
    try:
        import yaml
        from pathlib import Path
        p = Path(__file__).resolve().parents[3] / "data" / "blueprints.yaml"
        _BP_CACHE = {b["id"]: b for b in
                      (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("blueprints", [])}
    except Exception:
        _BP_CACHE = {}
    return _BP_CACHE


def buildings_of(village):
    try:
        b = getattr(village, "buildings", None)
        if not isinstance(b, list):
            b = []
            village.buildings = b
        return b
    except Exception:
        return []


def _day_of(village):
    try:
        from omnisim.core.clock import day_of as _d
        return int(_d(village.clock_pts))
    except Exception:
        return 0


def _parcels():
    try:
        from omnisim.systems.production import _load as _ld
        import yaml
        from pathlib import Path
        p = Path(__file__).resolve().parents[3] / "data" / "land.yaml"
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return {x["id"]: x for x in doc.get("parcels", [])}
    except Exception:
        return {}


def _live_owner(village, pid):
    """STEP 9: deeds move — build rights follow the live owner, not genesis."""
    try:
        from omnisim.systems.land import on as _l9_on
        from omnisim.systems.land import parcels_of as _l9_parcels
        if _l9_on():
            for p in _l9_parcels(village) or []:
                if p.get("id") == pid:
                    return str(p.get("owner", ""))
    except Exception:
        pass
    return None


def can_place(village, blueprint_id, npc):
    """Owner's own parcel, or Crown land with null stream (build rights)."""
    try:
        bp = blueprints().get(blueprint_id)
        if bp is None:
            return False, "unknown blueprint"
        name = getattr(npc, "name", "?")
        for pid, par in _parcels().items():
            owner = _live_owner(village, pid)
            if owner is None:
                owner = str(par.get("owner", ""))
            if owner == name:
                return True, pid
            if owner == "Crown" and par.get("good") is None:
                return True, pid
        return False, "no open ground"
    except Exception:
        return False, "error"


def build_cost(village, npc, bp):
    """Materials at market + own-labor builder fee at time_value."""
    total = 0.0
    try:
        from omnisim.systems.pricing import ask_mc as _amc
        from omnisim.systems.pricing import engine_on as _eon
        from omnisim.systems.pricing import fair_mc as _fmc
        from omnisim.systems.pricing import MC as _MC
        from omnisim.systems.production import reference_value as _ref
        for inp in bp.get("inputs", []) or []:
            g = inp.get("good")
            q = float(inp.get("qty", 1))
            if not g:
                continue
            try:
                if _eon():
                    from omnisim.systems.pricing import settle as _stl
                    c = float(_stl(npc, _amc(village, npc, g, 1)))
                else:
                    c = float(_fmc(village, g, 1)) / float(_MC)
            except Exception:
                c = float(_ref(g))
            total += c * q
    except Exception:
        pass
    try:
        from omnisim.systems.mood import time_value_mc as _tvm
        total += float(_tvm(npc, village)) / 1000.0 * float(bp.get("labor_ticks", 80))
    except Exception:
        total += float(bp.get("labor_ticks", 80))
    return total


def shadow_price(village, good):
    """Unmet-demand value capped at 3x fair (spec: kappa x relief bound)."""
    try:
        from omnisim.systems.production import expected_sale_price as _esp
        base = float(_esp(village, good))
    except Exception:
        base = 1.0
    try:
        from omnisim.systems.metrics import unmet_summary as _u
        from omnisim.systems.production import expected_price as _ep
        dem = sum(vv for k, vv in _u(village).items() if k == "demand:%s" % good)
        fair = max(0.5, float(_ep(village, good)))
        return min(fair * 3.0, max(base, base + float(dem) * 1.0))
    except Exception:
        return base


def _station_good(station):
    """Signature good per station: first recipe output using it."""
    try:
        from omnisim.systems.production import recipes as _recs
        for r in _recs().values():
            if str(r.get("station", "")) == station:
                outs = r.get("outputs", []) or []
                if outs:
                    return outs[0].get("good")
    except Exception:
        pass
    return None


def scan(village, npc):
    """Opportunity scanner: blueprints ranked by ROI (payback vs horizon)."""
    out = []
    if not on():
        return out
    try:
        from omnisim.systems.mood import horizon_days as _hz
        from omnisim.systems.decision import budget_of as _bo
        horizon = float(_hz(npc)) * float(_cfg("horizon_mult"))
        risk = float(_cfg("risk_adjust"))
        built = {b.get("type") for b in buildings_of(village)
                 if b.get("status") in ("operational", "under_construction")}
        try:
            intents = getattr(village, "build_intents", {}) or {}
            announced = {k for k, v in intents.items() if v}
        except Exception:
            announced = set()
        cur = 1.0
        try:
            cur = max(0.5, float(npc.comps.get("income_ema", 1.0)))
        except Exception:
            pass
        purse = float((npc.get("pocket", {}) or {}).get("coin", 0))
        reserve = float(_bo(village, npc).get("reserve", 0.0))
        for bpid, bp in blueprints().items():
            try:
                if bp.get("builds", {}).get("type") in built:
                    continue
                if bpid in announced:
                    continue
                ok, _ = can_place(village, bpid, npc)
                if not ok:
                    continue
                stations = (bp.get("builds", {}) or {}).get("stations", []) or []
                slots = int((bp.get("builds", {}) or {}).get("slots", 1))
                units = slots * float(_cfg("batches_per_slot"))
                from omnisim.systems.production import expected_sale_price as _esp2
                from omnisim.systems.pricing import _tape_store as _tstore
                price = 0.0
                for st in stations:
                    g = _station_good(st)
                    if not g:
                        continue
                    try:
                        _has_tape = bool((_tstore(village) or {}).get(g))
                    except Exception:
                        _has_tape = False
                    if _has_tape:
                        p = float(_esp2(village, g))
                    else:
                        try:
                            from omnisim.systems.metrics import unmet_summary as _uu
                            _dem = sum(vv for k, vv in _uu(village).items()
                                       if k == "demand:%s" % g)
                        except Exception:
                            _dem = 0
                        # downstream garments wanted but intermediates never
                        # shelved: finished-good demand counts for the chain
                        # (cloth wanted for tunics prices the loom).
                        if _dem <= 0:
                            try:
                                from omnisim.systems.production import recipes as _rr
                                from omnisim.systems.production import goods_with_tag as _mem
                                _down = set()
                                for r in _rr().values():
                                    _ins = set()
                                    for inp in (r.get("inputs", []) or []):
                                        if "good" in inp:
                                            _ins.add(inp["good"])
                                        elif "tag" in inp:
                                            _ins.update(_mem(inp["tag"]))
                                    if g in _ins:
                                        for o in (r.get("outputs", []) or []):
                                            if o.get("good"):
                                                _down.add(o.get("good"))
                                _chain = set(_down)
                                for _ in range(3):
                                    for r in _rr().values():
                                        _ins = set()
                                        for inp in (r.get("inputs", []) or []):
                                            if "good" in inp:
                                                _ins.add(inp["good"])
                                            elif "tag" in inp:
                                                try:
                                                    _ins.update(_mem(inp["tag"]))
                                                except Exception:
                                                    pass
                                        if _ins & _chain:
                                            for o in (r.get("outputs", []) or []):
                                                if o.get("good"):
                                                    _chain.add(o.get("good"))
                                _dem = sum(
                                    vv for k, vv in _uu(village).items()
                                    if k.startswith("demand:")
                                    and k.split(":", 1)[1] in _chain)
                            except Exception:
                                pass
                        if _dem <= 0:
                            continue
                        p = shadow_price(village, g)
                    price = max(price, p)
                cost = build_cost(village, npc, bp)
                upkeep = float(bp.get("upkeep_per_day", 1))
                delta = units * max(0.0, price) - upkeep - cur
                if delta < float(_cfg("min_gain")):
                    continue
                payback = cost / max(0.001, delta)
                if payback <= horizon * risk and purse - cost >= reserve:
                    out.append({"blueprint": bpid, "delta": delta,
                                "payback": payback,
                                "roi": delta / max(0.001, cost)})
            except Exception:
                continue
        out.sort(key=lambda d: d["roi"], reverse=True)
    except Exception:
        pass
    return out


def start_project(village, npc, blueprint_id, builder=None):
    """Escrow materials, announce intent, one project per NPC."""
    try:
        if npc.comps.get("active_build"):
            return None
        bp = blueprints().get(blueprint_id)
        if bp is None:
            return None
        day = _day_of(village)
        try:
            bi = getattr(village, "build_intents", None)
            if not isinstance(bi, dict):
                bi = village.build_intents = {}
            recent = bi.get(blueprint_id, [])
            if isinstance(recent, list) and len(recent) > 0:
                last_day = int(getattr(village, "build_intent_day", {}).get(blueprint_id, -999))
                if day - last_day < 7:
                    return None
        except Exception:
            pass
        cost = build_cost(village, npc, bp)
        from omnisim.systems.decision import budget_of as _bo2
        reserve = float(_bo2(village, npc).get("reserve", 0.0))
        purse = float((npc.get("pocket", {}) or {}).get("coin", 0))
        if purse - cost < reserve:
            return None
        escrow = cost * float(_cfg("escrow_pct"))
        try:
            npc.get("pocket", {})["coin"] = purse - escrow
        except Exception:
            return None
        ok, parcel = can_place(village, blueprint_id, npc)
        b = {"id": "%s-%s" % (blueprint_id, getattr(npc, "name", "?")),
             "type": (bp.get("builds", {}) or {}).get("type", blueprint_id),
             "blueprint": blueprint_id, "owner": getattr(npc, "name", "?"),
             "plot": parcel if ok else None, "condition": 100.0,
             "built_day": day, "upkeep_per_day": float(bp.get("upkeep_per_day", 1)),
             "stations": list((bp.get("builds", {}) or {}).get("stations", []) or []),
             "slots": int((bp.get("builds", {}) or {}).get("slots", 1)),
             "status": "under_construction", "progress": 0,
             "labor_ticks": int(bp.get("labor_ticks", 80)),
             "escrow": escrow, "cost": cost,
             "builder": getattr(builder, "name", None) if builder else None,
             "buyer": getattr(npc, "name", "?")}
        buildings_of(village).append(b)
        npc.comps["active_build"] = b["id"]
        try:
            bi = getattr(village, "build_intents", None)
            if not isinstance(bi, dict):
                bi = village.build_intents = {}
            lst = bi.setdefault(blueprint_id, [])
            if getattr(npc, "name", "?") not in lst:
                lst.append(getattr(npc, "name", "?"))
            dd = getattr(village, "build_intent_day", None)
            if not isinstance(dd, dict):
                dd = village.build_intent_day = {}
            dd[blueprint_id] = day
        except Exception:
            pass
        return b
    except Exception:
        return None


def work_on(village, building, ticks=1):
    """Builder labor advances progress; completion pays escrow out."""
    try:
        if building.get("status") != "under_construction":
            return building.get("status")
        building["progress"] = int(building.get("progress", 0)) + int(ticks)
        if building["progress"] >= int(building.get("labor_ticks", 80)):
            building["status"] = "operational"
            building["built_day"] = _day_of(village)
            # remainder settles: buyer pays cost-escrow, builder earns all
            try:
                names = {n.name: n for n in (getattr(village, "npcs", []) or [])}
                buyer = names.get(building.get("buyer"))
                builder = names.get(building.get("builder"))
                rest = float(building.get("cost", 0)) - float(building.get("escrow", 0))
                if building.get("builder") is None and buyer is not None and buyer.name == building.get("buyer"):
                    # SELF-BUILD: own labor, no coin moves. Value
                    # already left the purse as escrow; completion
                    # keeps it in the structure (nothing new paid).
                    rest = 0.0
                if buyer is not None and builder is not None and rest > 0:
                    purse = float((buyer.get("pocket", {}) or {}).get("coin", 0))
                    pay = min(rest, max(0.0, purse))
                    buyer.get("pocket", {})["coin"] = purse - pay
                    rest -= pay
                if builder is not None:
                    got = float(building.get("escrow", 0)) + (float(building.get("cost", 0)) - float(building.get("escrow", 0)) - rest)
                    builder.get("pocket", {})["coin"] = float((builder.get("pocket", {}) or {}).get("coin", 0)) + got
                elif rest > 0:
                    # ESTATE/CONSERVATION: builderless self-build keeps
                    # its remainder in the structure (escrow field),
                    # never deleted. Next work_on call re-offers it.
                    building["escrow"] = float(building.get("escrow", 0)) + rest
                if buyer is not None:
                    try:
                        if buyer.comps.get("active_build") == building.get("id"):
                            del buyer.comps["active_build"]
                    except Exception:
                        pass
            except Exception:
                pass
        return building.get("status")
    except Exception:
        try:
            return building.get("status")
        except Exception:
            return None


def passive_tick(village):
    """Dawn: operational slots work while owners sleep (inputs from owner)."""
    if not on():
        return 0
    n = 0
    try:
        from omnisim.systems.production import recipes as _recs2
        from omnisim.systems.production import runnable as _run2
        from omnisim.systems.production import execute as _exec2
        names = {nn.name: nn for nn in (getattr(village, "npcs", []) or [])}
        for b in buildings_of(village):
            if b.get("status") != "operational":
                continue
            owner = names.get(b.get("owner"))
            if owner is None:
                continue
            for st in b.get("stations", []) or []:
                try:
                    sig = next((r for r in _recs2().values()
                                if str(r.get("station", "")) == st
                                and "service" not in (r.get("flags", []) or [])), None)
                    if sig is None:
                        continue
                    for _ in range(int(b.get("slots", 1))):
                        if not _run2(village, owner, sig):
                            break
                        _exec2(village, owner, sig)
                        n += 1
                    try:
                        from omnisim.systems.workspaces import use as _use
                        _use(village, st)
                    except Exception:
                        pass
                except Exception:
                    continue
    except Exception:
        pass
    return n


def _forfeit_escrow(village, building):
    """Derelict escrow conserves: dead-owner coin refunds to the estate
    (settle_estate, same path as tabs); a living owner is repaid to the
    purse; no owner/movement possible lands in the named ledger
    (escrow_forfeit_at_derelict, counted by ledger_total)."""
    try:
        amt = float(building.get("escrow", 0) or 0)
        if amt <= 0:
            building["escrow"] = 0.0
            return
        building["escrow"] = 0.0
        names = {n.name: n for n in (getattr(village, "npcs", []) or [])}
        owner = names.get(building.get("owner"))
        if owner is not None and not owner.comps.get("dead"):
            owner.get("pocket", {})["coin"] = float((owner.get("pocket", {}) or {}).get("coin", 0)) + amt
            return
        # dead or gone: the estate settles first (pocket+ground coin),
        # then the escrow value joins the same purse-or-ground path.
        try:
            from omnisim.systems.estate import settle_estate as _se
            _se(village, building.get("owner"))
        except Exception:
            pass
        rec = next((n for n in (getattr(village, "npcs", []) or []) if n.name == building.get("owner")), None)
        if rec is not None:
            rec.get("pocket", {})["coin"] = float((rec.get("pocket", {}) or {}).get("coin", 0)) + amt
            return
        try:
            from omnisim.systems.estate import _ledger as _leg
            _leg(village).append({"cause": "escrow_forfeit_at_derelict", "who": building.get("owner"), "amount": amt, "building": building.get("id")})
        except Exception:
            pass
    except Exception:
        pass


def upkeep_tick(village):
    """Dawn: condition decays, upkeep bills, derelicts rot."""
    if not on():
        return
    try:
        day = _day_of(village)
        names = {n.name: n for n in (getattr(village, "npcs", []) or [])}
        for b in buildings_of(village):
            try:
                if b.get("status") == "under_construction":
                    if day - int(b.get("built_day", day)) > int(_cfg("derelict_days")):
                        b["status"] = "derelict"
                        _forfeit_escrow(village, b)
                    continue
                if b.get("status") != "operational":
                    continue
                b["condition"] = float(b.get("condition", 100.0)) - float(_cfg("decay_per_day"))
                owner = names.get(b.get("owner"))
                up = float(b.get("upkeep_per_day", 1))
                if owner is not None:
                    try:
                        purse = float((owner.get("pocket", {}) or {}).get("coin", 0))
                        if purse >= up:
                            owner.get("pocket", {})["coin"] = purse - up
                            try:  # CONSERVATION: upkeep returns to
                                # the reserve (till), never deleted.
                                (village.stalls or [None])[0]["till"] = float((village.stalls or [None])[0].get("till", 0)) + up
                            except Exception:
                                pass
                        else:
                            b["condition"] -= 2.0  # unpaid upkeep rots faster
                    except Exception:
                        b["condition"] -= 2.0
                if float(b.get("condition", 0)) <= 0:
                    b["status"] = "derelict"
                    _forfeit_escrow(village, b)
            except Exception:
                continue
    except Exception:
        pass


def repair(village, station_key, amount=25.0):
    try:
        from omnisim.systems.workspaces import repair as _rep
        return _rep(village, station_key, amount)
    except Exception:
        return 0.0
