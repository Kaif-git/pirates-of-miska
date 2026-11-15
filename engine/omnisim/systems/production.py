"""Step 1: data-driven production engine.

Recipes live in data/production.yaml; behavior comes from rows + this
generic engine. No `if good == ...`, no `% N` counters. Until Step 6 the
chooser is a stub: highest margin among runnable recipes, where revenue
uses expected sale price (tape × saleability; reference value before any
trade) and glut-skips like the old pauses (mouths still override).

Flag-gated: schedule._bank_production dispatches here when
economy.yaml flags.step_1 is true; the old path stays until parity.
"""
from __future__ import annotations

_DOCS: dict[str, dict] = {}


def _load(name: str) -> dict:
    if name not in _DOCS:
        try:
            import yaml
            from pathlib import Path
            p = (Path(__file__).resolve().parents[3] / "data" / f"{name}.yaml")
            _DOCS[name] = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:
            _DOCS[name] = {}
    return _DOCS[name]


_TABLES: dict[str, object] = {}


def _invalidate_tables() -> None:
    _TABLES.clear()


def set_doc(name: str, doc: dict) -> None:
    """Test override (content drops add rows with no code changes)."""
    _DOCS[name] = doc
    _REF_CACHE.clear()
    _TABLES.clear()


def reset_docs() -> None:
    _DOCS.clear()
    _REF_CACHE.clear()
    _TABLES.clear()


def flag_on(step: str = "step_1") -> bool:
    try:
        return bool(_load("economy").get("flags", {}).get(step, False))
    except Exception:
        return False


def goods() -> dict:
    hit = _TABLES.get("goods")
    if hit is None:
        hit = {g["id"]: g for g in _load("goods").get("goods", []) if "id" in g}
        _TABLES["goods"] = hit
    return hit


def stations() -> dict:
    doc = _load("stations")
    rows = doc if isinstance(doc, list) else doc.get("stations", [])
    hit = _TABLES.get("stations")
    if hit is None:
        hit = {s["id"]: s for s in rows if "id" in s}
        _TABLES["stations"] = hit
    return hit


def trades() -> dict:
    hit = _TABLES.get("trades")
    if hit is None:
        hit = {t["id"]: t for t in _load("trades").get("trades", []) if "id" in t}
        _TABLES["trades"] = hit
    return hit


def recipes() -> dict:
    hit = _TABLES.get("recipes")
    if hit is None:
        hit = {r["id"]: r for r in _load("production").get("recipes", [])
               if "id" in r}
        _TABLES["recipes"] = hit
    return hit


def goods_with_tag(tag: str) -> list[str]:
    key = "tag:" + tag
    hit = _TABLES.get(key)
    if hit is None:
        hit = [gid for gid, g in goods().items() if tag in (g.get("tags") or [])]
        _TABLES[key] = hit
    return hit


# --- validation -----------------------------------------------------------

def durable_calibration() -> list[str]:
    """Durable ask vs N days of median income (planning calibration)."""
    out: list[str] = []
    try:
        _n = float((_load("economy") or {}).get("durable_price_days", 4))
        _tv = float((_load("economy") or {}).get("time_value_copper_per_tick", 1))
        _day_income = _tv * 10.0
        _cap = _n * _day_income
        for gid, g in goods().items():
            try:
                _prov = (g.get("provides", {}) or {}).get("insulation")
                if not _prov:
                    continue
                _ref = reference_value(gid)
                if _ref != float("inf") and _ref > _cap:
                    out.append(f"{gid}: ref {_ref:.1f}c > {_n:.0f}d income ({_cap:.1f}c)")
            except Exception:
                continue
    except Exception:
        pass
    return out


def validate() -> list[str]:
    """Data consistency. Content drops must pass this with no code changes."""
    errs: list[str] = []
    try:
        from omnisim.entities.items import _items
        item_ids = set(_items())
    except Exception:
        item_ids = set()
    g = goods()
    for gid in g:
        if item_ids and gid not in item_ids:
            errs.append(f"goods.yaml: {gid} not in items.yaml")
    st = stations()
    recs = recipes()
    trs = trades()
    for rid, r in recs.items():
        if r.get("station") not in st:
            errs.append(f"recipe {rid}: unknown station {r.get('station')}")
        for inp in r.get("inputs", []) or []:
            if "good" in inp:
                if item_ids and inp["good"] not in item_ids:
                    errs.append(f"recipe {rid}: unknown input good {inp['good']}")
            elif "tag" in inp:
                if not goods_with_tag(inp["tag"]):
                    errs.append(f"recipe {rid}: tag {inp['tag']} resolves to nothing")
            else:
                errs.append(f"recipe {rid}: input needs good or tag")
        for out in r.get("outputs", []) or []:
            if item_ids and out.get("good") not in item_ids:
                errs.append(f"recipe {rid}: unknown output {out.get('good')}")
        for m in r.get("yield_modifiers", []) or []:
            if m.get("type") not in ("tool_q_div", "skill_div", "soil_hearts",
                                     "stock_density", "perk", "station_condition"):
                errs.append(f"recipe {rid}: unknown modifier {m.get('type')}")
    for tid, t in trs.items():
        for rid in t.get("recipes", []) or []:
            if rid not in recs:
                errs.append(f"trade {tid}: unknown recipe {rid}")
    errs.extend(_cycle_check(recs))
    try:  # STEP 3: every good traces to a mouth or to decay (one mouth, one signal)
        from omnisim.systems.needs3 import validate_goods as _vg3
        errs.extend(_vg3())
    except Exception:
        pass
    return errs


def _cycle_check(recs: dict) -> list[str]:
    """No recipe cycle with positive yield (infinite-money check)."""
    errs: list[str] = []
    try:
        graph: dict[str, list[tuple[str, float]]] = {}
        for rid, r in recs.items():
            for out in r.get("outputs", []) or []:
                for inp in r.get("inputs", []) or []:
                    ingood = inp.get("good")
                    if ingood is None:
                        continue  # tags resolve at runtime; skip
                    graph.setdefault(out["good"], []).append(
                        (ingood, out.get("qty", 1) / max(1, inp.get("qty", 1))))
        for start in graph:
            stack = [(start, 1.0, [start])]
            seen = 0
            while stack and seen < 200:
                node, gain, path = stack.pop()
                seen += 1
                for nxt, ratio in graph.get(node, []):
                    if nxt == start and gain * ratio > 1.0:
                        errs.append(f"positive-yield cycle: {' -> '.join(path + [nxt])}")
                    elif nxt not in path:
                        stack.append((nxt, gain * ratio, path + [nxt]))
    except Exception:
        pass
    return errs


# --- reference value (prices a brand-new good before any trade) -------------

_REF_CACHE: dict[str, float] = {}


def reference_value(good: str, _seen: tuple = ()) -> float:
    """Min over recipes of (inputs + time) / outputs. Tape wins once trading.
    Static data: memoized (reset_docs/set_doc clear it)."""
    if good in _REF_CACHE and not _seen:
        return _REF_CACHE[good]
    if good in _seen:
        return float("inf")
    best = float("inf")
    try:
        tv = float(_load("economy").get("time_value_copper_per_tick", 1))
    except Exception:
        tv = 1.0
    best = _crop_reference(good, tv, _seen)
    for r in recipes().values():
        for out in r.get("outputs", []) or []:
            if out.get("good") != good:
                continue
            cost = 0.0
            ok = True
            for inp in r.get("inputs", []) or []:
                q = inp.get("qty", 1)
                if "good" in inp:
                    c = reference_value(inp["good"], _seen + (good,))
                else:
                    cands = [reference_value(g2, _seen + (good,))
                             for g2 in goods_with_tag(inp["tag"])]
                    c = min(cands) if cands else float("inf")
                if c == float("inf"):
                    ok = False
                    break
                cost += c * q
            if not ok:
                continue
            cost += tv * int(r.get("active_ticks", 1))
            best = min(best, cost / max(1, out.get("qty", 1)))
    if best == float("inf"):
        # no recipe and no crop: wild, found, or hand-mined baseline
        if good in goods() or _is_item(good):
            best = tv
    if not _seen:
        _REF_CACHE[good] = best
    return best


def _is_item(good: str) -> bool:
    try:
        from omnisim.entities.items import _items
        return good in _items()
    except Exception:
        return False


_CROPS_CACHE: dict | None = None


def _crop_reference(good: str, tv: float, _seen: tuple) -> float:
    """Grain/roots: seed + two hand-ticks (sow + cut) over base yield."""
    global _CROPS_CACHE
    try:
        if _CROPS_CACHE is None:
            import yaml
            from pathlib import Path
            p = Path(__file__).resolve().parents[3] / "data" / "crops.yaml"
            _CROPS_CACHE = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("crops", {})
        crops = _CROPS_CACHE
    except Exception:
        return float("inf")
    best = float("inf")
    for _cid, c in (crops or {}).items():
        if c.get("name") != good:
            continue
        seed = c.get("seed")
        seed_ref = reference_value(seed, _seen + (good,)) if seed else tv
        if seed_ref == float("inf"):
            seed_ref = tv
        best = min(best, (seed_ref + 2 * tv) / max(1, c.get("yield", 1)))
    return best


# --- expected price + saleability (stub for Step 6) --------------------------

def _traded_lately(village, good: str, days: int = 30) -> bool:
    try:
        from omnisim.core.clock import day_of
        today = day_of(village.clock_pts)
        return any(t.get("item") == good and today - int(t.get("day", today)) <= days
                   for t in (getattr(village, "trades", []) or []))
    except Exception:
        return False


def _wanted_without_supply(village, good: str) -> bool:
    """Souls asked, shelves stood bare (the village posted its need)."""
    try:
        from omnisim.systems.metrics import unmet_summary
        return any(k == f"demand:{good}" and v > 0
                   for k, v in unmet_summary(village).items())
    except Exception:
        return False


def expected_price(village, good: str) -> float:
    """Step 2 engine owns fair price when its flag is on (tape EMA, else the
    reference build-up); otherwise the tape average as before."""
    try:
        from omnisim.systems.pricing import engine_on, fair_mc
        if engine_on():
            return fair_mc(village, good) / 1000.0
    except Exception:
        pass
    if _traded_lately(village, good):
        try:
            from omnisim.systems.metrics import _tape_price
            return float(_tape_price(village, good))
        except Exception:
            pass
    if _wanted_without_supply(village, good):
        try:
            from omnisim.entities.items import _items
            return float(_items().get(good, {}).get("price", 1) or 1)
        except Exception:
            pass
    ref = reference_value(good)
    if ref != float("inf"):
        return ref
    try:
        from omnisim.entities.items import _items
        return float(_items().get(good, {}).get("price", 1) or 1)
    except Exception:
        return 1.0


def _supply_ema(village, good: str) -> float:
    """Village production flow, decayed on read (no dawn hook needed)."""
    try:
        from omnisim.core.clock import day_of
        today = day_of(village.clock_pts)
        flow = getattr(village, "prod_supply", None) or {}
        day, ema = flow.get(good, (today, 0.0))
        return ema * (0.5 ** max(0, today - day))
    except Exception:
        return 0.0


def note_demand_flow(village, good: str, qty: int) -> None:
    """Record consumed units into the demand-side flow EMA (use, not make)."""
    try:
        from omnisim.core.clock import day_of
        today = day_of(village.clock_pts)
        flow = getattr(village, "prod_demand", None)
        if flow is None:
            flow = {}
            village.prod_demand = flow
        day, ema = flow.get(good, (today, 0.0))
        ema = ema * (0.5 ** max(0, today - day)) + qty
        flow[good] = (today, ema)
    except Exception:
        pass


def note_supply(village, good: str, qty: int) -> None:
    """Record banked units into the supply EMA."""
    try:
        from omnisim.core.clock import day_of
        today = day_of(village.clock_pts)
        flow = getattr(village, "prod_supply", None)
        if flow is None:
            flow = {}
            village.prod_supply = flow
        day, ema = flow.get(good, (today, 0.0))
        ema = ema * (0.5 ** max(0, today - day)) + qty
        flow[good] = (today, ema)
    except Exception:
        pass


def saleability(village, good: str) -> float:
    """Demand flow over demand + supply, clamped. New goods sell at 1.0."""
    try:
        from omnisim.systems.metrics import unmet_summary
        dem = sum(v for k, v in unmet_summary(village).items()
                  if k == f"demand:{good}") / 2.0
    except Exception:
        dem = 0.0
    sup = _supply_ema(village, good)
    if dem <= 0 and sup <= 0:
        return 1.0
    return max(0.3, min(1.0, dem / max(0.001, dem + sup)))


_RCV_BYPASS = False


def expected_sale_price(village, good: str) -> float:
    try:  # REALIZED-CHAIN (flag realized_chain_value, default off):
        # two-link test: flour reads bread's trailing realized price x
        # yield, no horizon discount (deliberate departure from derived
        # demand, stated in realizedchain.py + commit message).
        if not _RCV_BYPASS:
            from omnisim.systems.realizedchain import on as _rcv_on
            if _rcv_on() and good == "flour":
                from omnisim.systems.realizedchain import chain_value as _rcv
                _cv, _fb = _rcv(village, good)
                if _cv is not None:
                    return float(_cv)
    except Exception:
        pass
    try:  # DERIVED-DEMAND (flag derived_demand, default off):
        # intermediates read one derived number (ask and bid share
        # it); final goods keep tape x saleability. Rows only.
        from omnisim.systems.deriveddemand import value_of as _ddv
        _dd = _ddv(village, good)
        if _dd is not None:
            return float(_dd)
    except Exception:
        pass
    return expected_price(village, good) * saleability(village, good)


# --- choice + execution ---------------------------------------------------

def trade_of(npc) -> dict | None:
    skill = npc.get("work_skill") or ""
    for t in trades().values():
        if t.get("skill") == skill:
            return t
    return None


def _market_input_cost(village, good: str) -> tuple[str | None, float]:
    """Market replacement for one unit: ask when trading, else reference.
    Lets the scanner price chains nobody holds yet (part-time entry)."""
    try:
        from omnisim.systems.pricing import ask_mc as _amc
        from omnisim.systems.pricing import engine_on as _eon
        from omnisim.systems.pricing import fair_mc as _fmc
        from omnisim.systems.pricing import MC as _MC
        if _eon():
            from omnisim.systems.pricing import _tape_store as _ts
            try:
                _has = bool((_ts(village) or {}).get(good))
            except Exception:
                _has = False
            if _has:
                return good, float(_amc(village, None, good, 1)) / float(_MC)
            return good, float(_fmc(village, good, 1)) / float(_MC)
    except Exception:
        pass
    try:
        return good, float(reference_value(good))
    except Exception:
        return None, 0.0


def _input_cost(village, npc, inp: dict, market_ok: bool = False) -> tuple[str | None, float]:
    """Which owned stack a tag-input eats (prefer list first, else cheapest
    owned), and its unit cost. Returns (good_id or None, unit_cost).
    market_ok: price missing inputs at market (scanner/part-time entry)."""
    from omnisim.systems.haggle import cost_of
    pocket = npc.get("pocket", {}) or {}
    cands: list[str] = []
    if "good" in inp:
        if pocket.get(inp["good"], 0) > 0:
            cands = [inp["good"]]
        elif market_ok:
            return _market_input_cost(village, inp["good"])
    else:
        owned = [g for g in goods_with_tag(inp["tag"]) if pocket.get(g, 0) > 0]
        prefer = inp.get("prefer", []) or []
        cands = [g for g in prefer if g in owned]
        if not cands and owned:
            cands = sorted(owned, key=lambda g: cost_of(npc, g))[:1]
        if not cands and market_ok:
            _all = goods_with_tag(inp["tag"])
            if _all:
                _best, _bp = None, float("inf")
                for _g in _all:
                    _gg, _cc = _market_input_cost(village, _g)
                    if _gg is not None and _cc < _bp:
                        _best, _bp = _gg, _cc
                if _best is not None:
                    return _best, _bp
    if not cands:
        return None, 0.0
    return cands[0], float(cost_of(npc, cands[0]))


def runnable(village, npc, recipe: dict) -> bool:
    """Inputs in pocket (partial batches only if allowed)."""
    if "service" in (recipe.get("flags", []) or []):
        return True
    try:  # STEP 8: min_skill gates recipes (data rows, rust-adjusted level)
        from omnisim.systems.mastery import on as _m8r_on
        from omnisim.systems.mastery import effective_skill as _m8eff
        if _m8r_on():
            req = int(recipe.get("min_skill", 1) or 1)
            if _m8eff(npc, recipe.get("skill", "")) < req:
                return False
    except Exception:
        pass
    pocket = npc.get("pocket", {}) or {}
    for inp in recipe.get("inputs", []) or []:
        need = inp.get("qty", 1)
        if "good" in inp:
            have = pocket.get(inp["good"], 0)
        else:
            have = sum(pocket.get(g, 0) for g in goods_with_tag(inp["tag"]))
        if have < need and not recipe.get("allow_partial"):
            return False
        if have <= 0:
            return False
    return True


def _cover(village, good: str) -> float:
    """Canonical cover lives in pricing (Step 2); the stub reads it here."""
    try:
        from omnisim.systems.pricing import cover_of
        return cover_of(village, good)
    except Exception:
        return 0.0


def _scarcity(village, good: str) -> float:
    """Step 2 formula, pulled forward: scarce outputs pay more. Staples
    (the daily mouthful) call louder than feasts: cap 3.0 vs 1.8."""
    try:
        target = float(goods().get(good, {}).get("target_cover", 5))
        tags = goods().get(good, {}).get("tags", []) or []
    except Exception:
        target, tags = 5.0, []
    cap = 3.0 if "staple" in tags else 1.8
    return max(0.7, min(cap, (target / max(_cover(village, good), 0.25)) ** 0.5))


def expected_margin(village, npc, recipe: dict) -> float:
    """Stub (Step 6 replaces): expected sale revenue (price × saleability ×
    scarcity) minus input lot cost, plus pile relief: inputs heaped past
    their cover target push toward processing (the kiln eats the clay
    mountain). Relief scales with output saleability — unwanted outputs
    get no push."""
    outs = recipe.get("outputs", []) or []
    sale = 1.0
    if outs:
        sale = min(saleability(village, o.get("good")) for o in outs)
    rev = sum(expected_sale_price(village, o.get("good")) * o.get("qty", 1)
              * _scarcity(village, o.get("good")) for o in outs)
    cost = 0.0
    relief = 0.0
    try:
        from omnisim.systems.metrics import unmet_summary
        refused = {k.split(":", 1)[1]: v for k, v in unmet_summary(village).items()
                   if k.startswith("supply:")}
    except Exception:
        refused = {}
    for inp in recipe.get("inputs", []) or []:
        gid, unit = _input_cost(village, npc, inp)
        if gid is None:
            try:  # CHAIN-CLEARING parity: price missing inputs at chain
                # value (same side as the bid), not pocket absence.
                from omnisim.systems.chainvalue import on as _cc_on2
                if _cc_on2():
                    _gn = inp.get("good")
                    if _gn is None and "tag" in inp:
                        _cands = goods_with_tag(inp.get("tag"))
                        _gn = _cands[0] if _cands else None
                    if _gn is not None:
                        from omnisim.systems.chainvalue import (
                            chain_value as _ccv2)
                        from omnisim.systems.pricing import (
                            fair_mc as _fmc2, MC as _MC2)
                        _cv2 = _ccv2(village, npc, _gn, 1)
                        _fb2 = float(_fmc2(village, _gn, 1)) / float(_MC2)
                        unit = max(float(_cv2) / 1000.0, _fb2) if (
                            _cv2 is not None and _cv2 > 0) else _fb2
                        gid = _gn
            except Exception:
                pass
        if gid is None:
            return float("-inf")
        q = inp.get("qty", 1)
        cost += unit * q
        # pile relief: inputs the shelf refused push toward processing
        # (units refused today/yesterday = surplus nobody takes).
        pile = min(3.0, refused.get(gid, 0) / 4.0)
        relief += pile * unit * q * sale
    return rev - cost + relief


def _over_cover(village, recipe: dict) -> bool:
    """Stub cover limit: the old glut pause, finished-good basis."""
    try:
        from omnisim.systems.schedule import _market_glut
        for o in recipe.get("outputs", []) or []:
            if _market_glut(village, o.get("good")):
                return True
    except Exception:
        pass
    return False


def _desperate(npc) -> bool:
    try:
        from omnisim.systems.valuation import desperate
        return bool(desperate(npc))
    except Exception:
        return False


def _edible_output(recipe: dict) -> bool:
    try:
        from omnisim.entities.items import _items
        defs = _items()
        return any(defs.get(o.get("good"), {}).get("food", 0) > 0
                   for o in recipe.get("outputs", []) or [])
    except Exception:
        return False


def _daily_used(village, npc, recipe: dict) -> int:
    try:
        from omnisim.core.clock import day_of
        day = day_of(village.clock_pts)
        st = getattr(village, "prod_used", None) or {}
        return int(st.get((npc.name, recipe["id"], day), 0))
    except Exception:
        return 0


def _daily_bump(village, npc, recipe: dict) -> None:
    try:
        from omnisim.core.clock import day_of
        day = day_of(village.clock_pts)
        st = getattr(village, "prod_used", None)
        if st is None:
            st = {}
            village.prod_used = st
        st[(npc.name, recipe["id"], day)] = _daily_used(village, npc, recipe) + 1
        for k in [kk for kk in st if kk[2] < day - 1]:
            del st[kk]
    except Exception:
        pass


def choose_recipe(village, npc, cands: list[dict]) -> dict | None:
    """Highest stub margin; per-day caps; glut-skipped unless starving
    hands need food (then best edible runnable wins, margin or no)."""
    best, best_m = None, float("-inf")
    for r in cands:
        cap = r.get("per_day_max")
        if cap is not None and _daily_used(village, npc, r) >= int(cap):
            continue
        # don't harvest what rots unsold: heaped past 2x cover with no
        # buyers is a pause, not a plan (the herb-pile lesson).
        outs = r.get("outputs", []) or []
        if outs and all(_cover(village, o.get("good")) >
                        2 * float(goods().get(o.get("good"), {}).get("target_cover", 14))
                        and saleability(village, o.get("good")) <= 0.5
                        for o in outs):
            continue
        try:
            from omnisim.systems.decision import on as _d6_on
            from omnisim.systems.decision import score_produce as _d6_score
            _use6 = bool(_d6_on())
        except Exception:
            _use6 = False
        m = _d6_score(village, npc, r) if _use6 else expected_margin(village, npc, r)
        if m <= float("-inf"):
            continue
        if _over_cover(village, r) and not (_desperate(npc) and _edible_output(r)):
            continue
        if best is None or m > best_m:
            best, best_m = r, m
    if best is not None and best_m < 0 and not (
            _desperate(npc) and _edible_output(best)):
        # STEP 6: score is mc/hr with time priced at time_value, so a lone
        # runnable recipe scores negative whenever its gain trails the walk-
        # away rate — yet the old margin still says the work feeds. The
        # negative gate was tuned for margin-copper; under Step 6 the lone
        # candidate fishes anyway (idleness pays nothing, and needs3 already
        # prices hunger's pull through the walk decision).
        try:
            from omnisim.systems.decision import on as _d6g_on
            _g6 = bool(_d6g_on())
        except Exception:
            _g6 = False
        if _g6:
            return best
        return None  # nothing pays — hands pause, unburnt
    return best


def _consume_inputs(village, npc, recipe: dict, scale: float = 1.0) -> float:
    """Eat inputs oldest-lots-first. Returns batch copper cost."""
    from omnisim.systems.haggle import pop_lots
    pocket = npc.get("pocket", {}) or {}
    batch_cost = 0.0
    for inp in recipe.get("inputs", []) or []:
        qty = max(1, int(round(inp.get("qty", 1) * scale)))
        gid, _u = _input_cost(village, npc, inp)
        if gid is None:
            continue
        take = min(qty, pocket.get(gid, 0))
        if take <= 0:
            continue
        pocket[gid] -= take
        if pocket[gid] <= 0:
            del pocket[gid]
        try:
            cov, cost = pop_lots(npc, gid, take)
            batch_cost += cost + (take - cov)
        except Exception:
            batch_cost += take
    return batch_cost


def _yield_qty(village, npc, recipe: dict, base_qty: int) -> int:
    """Workstation condition, sharp iron (+1 per 3q), then perk bonuses."""
    from omnisim.systems.skills import skill_level
    qty = base_qty
    try:
        from omnisim.systems.workspaces import yield_mult as _ym
        qty = max(1, int(round(qty * _ym(village, recipe.get("skill", "")))))
    except Exception:
        pass
    for m in recipe.get("yield_modifiers", []) or []:
        t = m.get("type")
        try:
            if t == "tool_q_div":
                from omnisim.systems.tools import best_tool as _btq
                qty += int(_btq(recipe.get("skill", ""), npc)) // int(m.get("n", 3))
            elif t == "perk":
                from omnisim.systems.perks import perk_value as _pv
                qty += int(_pv(skill_level(npc, recipe.get("skill", "")),
                               recipe.get("skill", ""), m.get("id", ""), 0))
        except Exception:
            pass
    return qty


def _quality(village, npc, recipe: dict) -> int:
    rule = recipe.get("quality_rule", {}) or {}
    base = int(rule.get("base", 1))
    sd = int(rule.get("skill_div", 3))
    td = int(rule.get("tool_div", 2))
    cap = int(rule.get("cap", 5))
    try:
        from omnisim.systems.skills import skill_level
        from omnisim.systems.tools import best_tool
        lvl = skill_level(npc, recipe.get("skill", ""))
        q = base + lvl // max(1, sd) + int(best_tool(recipe.get("skill", ""), npc)) // max(1, td)
        q = max(1, min(cap, q))
        try:  # STEP 4: despair caps craft (below -50: -1); joy sometimes
            # gilds it (above 60: +10% chance of +1). Deterministic roll.
            from omnisim.systems.mood import on as _m4q_on
            from omnisim.systems.mood import mood_of as _m4q_m
            if _m4q_on():
                _m = _m4q_m(npc)
                if _m <= -50.0:
                    q = max(1, min(q, cap - 1))
                elif _m > 60.0:
                    _rng = getattr(village, "rng", None)
                    _roll = _rng.random() if _rng is not None else 0.99
                    if _roll < 0.10:
                        q = min(cap, q + 1)
        except Exception:
            pass
        try:  # STEP 8: skill caps quality (L2 makes q2, L8 makes q5) —
            # AFTER mood: mastery bounds the hands, mood spends the moment.
            # No skill entry (tests, strangers): the old formula stands —
            # caps bind practicing hands, not every hypothetical.
            from omnisim.systems.mastery import on as _m8q_on
            from omnisim.systems.mastery import effective_skill as _m8eff2
            from omnisim.systems.mastery import q_cap as _m8cap
            if _m8q_on() and recipe.get("skill", "") in (npc.get("skills", {}) or {}):
                q = max(1, min(q, _m8cap(_m8eff2(npc, recipe.get("skill", "")))))
        except Exception:
            pass
        return q
    except Exception:
        return base


def run_auto(village, npc, trade: dict) -> None:
    """Auto recipes (the baker's quern): grind while the bin lacks flour."""
    recs = recipes()
    pocket = npc.get("pocket", {}) or {}
    for rid in trade.get("recipes", []) or []:
        r = recs.get(rid)
        if not r or "auto" not in (r.get("flags", []) or []):
            continue
        lacks = r.get("auto_lacks")
        if lacks and pocket.get(lacks, 0) >= 1:
            continue
        if not runnable(village, npc, r):
            continue
        execute(village, npc, r)


def run_production_tick(village, npc) -> bool:
    """One shift tick through data. True = handled (incl. intentional idle)."""
    from omnisim.systems import schedule as _s
    skill = npc.get("work_skill") or ""
    trade = trade_of(npc)
    if trade is None:
        return False
    for up in trade.get("tick_upkeep", []) or []:  # cold forge = service only
        gid, _u = _input_cost(village, npc, {"tag": up.get("tag"),
                                             "qty": up.get("qty", 1)})
        pocket = npc.get("pocket", {}) or {}
        if gid is None or pocket.get(gid, 0) < up.get("qty", 1):
            if _run_service(village, npc, trade):
                return True
            _s.seen_say(village, npc.x, npc.y,
                         f"{npc.name} has no fuel for the {skill.lower()} forge.")
            npc.comps["activity"] = "no-flour"
            return True
        pocket[gid] -= up.get("qty", 1)
        if pocket[gid] <= 0:
            del pocket[gid]
    run_auto(village, npc, trade)
    try:
        recs = recipes()
    except Exception:
        recs = {}
    try:  # GOODS BEFORE SERVICE: heats before mending. Service-first
        # starved the village of new iron (Sella mended 12x in 4 days, forged
        # never; Garr starved beside a full ore shelf). When a goods recipe
        # is runnable and under its day cap, skip service and fall through
        # to the normal scored pick below.
        _grecs = [recs[rid] for rid in trade.get("recipes", []) or []
                   if rid in recs and "service" not in (recs[rid].get("flags", []) or [])
                   and "auto" not in (recs[rid].get("flags", []) or [])
                   and runnable(village, npc, recs[rid])
                   and _daily_used(village, npc, recs[rid]) < int(recs[rid].get("per_day_max", 3))]
        _goods_open = bool(_grecs)
    except Exception:
        _goods_open = False
    if not _goods_open:
        if _run_service(village, npc, trade):
            return True
    if skill == "Farming":
        return _run_farm(village, npc, trade)
    recs = recipes()
    cands = [recs[rid] for rid in trade.get("recipes", []) or []
             if rid in recs and "service" not in (recs[rid].get("flags", []) or [])
             and "auto" not in (recs[rid].get("flags", []) or [])
             and runnable(village, npc, recs[rid])]
    if not cands:
        if skill == "Smithing":
            npc.comps["activity"] = "sparks"
            return True
        return False
    pick = choose_recipe(village, npc, cands)
    # STEP 6: demand answers first — when a candidate's output is posted
    # wanted (unmet demand on the tape) and runnable, it outranks the score:
    # clay the shelf refused pushes toward the kiln, pots the town asked for
    # fire before more mud digs. Scored choice governs the rest.
    try:
        from omnisim.systems.decision import on as _d6d_on
        _dd6 = bool(_d6d_on())
    except Exception:
        _dd6 = False
    if _dd6 and pick is not None:
        try:
            from omnisim.systems.metrics import unmet_summary as _u6
            _dem6 = {k.split(":", 1)[1] for k, vv in _u6(village).items()
                     if k.startswith("demand:") and vv > 0}
        except Exception:
            _dem6 = set()
        if _dem6:
            _po6 = [o.get("good") for o in pick.get("outputs", []) or []]
            if not any(g in _dem6 for g in _po6):
                _bm6, _bp6 = float("-inf"), None
                for _r in cands:
                    _ro6 = [o.get("good") for o in _r.get("outputs", []) or []]
                    if not any(g in _dem6 for g in _ro6):
                        continue
                    try:
                        _m6 = expected_margin(village, npc, _r)
                    except Exception:
                        continue
                    if _m6 > _bm6:
                        _bm6, _bp6 = _m6, _r
                if _bp6 is not None and _bm6 > float("-inf"):
                    pick = _bp6
    if pick is None:
        # STEP 6: score-cone fallback — when the scored pick dies in the
        # hands (bench math said glean, pond says otherwise), the best
        # stub-margin runnable still feeds. Scored choice first, old nose
        # for the work second.
        try:
            from omnisim.systems.decision import on as _d6f_on
            _f6 = bool(_d6f_on())
        except Exception:
            _f6 = False
        if _f6:
            _bm, _bp = float("-inf"), None
            for _r in cands:
                try:
                    _m0 = expected_margin(village, npc, _r)
                except Exception:
                    continue
                if _m0 > _bm:
                    _bm, _bp = _m0, _r
            if _bp is not None and _bm > float("-inf"):
                execute(village, npc, _bp)
                return True
        if npc.comps.get("activity") != "glutted":
            npc.comps["activity"] = "glutted"
        return True
    execute(village, npc, pick)
    return True


def _run_service(village, npc, trade: dict) -> bool:
    """Service recipes trigger themselves (the whetstone calls)."""
    recs = recipes()
    for rid in trade.get("recipes", []) or []:
        r = recs.get(rid)
        if not r or "service" not in (r.get("flags", []) or []):
            continue
        if r["id"] == "mend_tool" and _mend(village, npc, r):
            return True
        if "mend" in (r.get("flags", []) or []) and _mend_garment(village, npc, r):
            return True
    try:  # STEP 8: masters teach between jobs — learners with coin and a
        # gap worth closing buy a lesson from the best idle hand nearby
        from omnisim.systems.mastery import on as _m8l_on
        from omnisim.systems.mastery import buy_lesson as _m8buy
        from omnisim.systems.mastery import lesson_price as _m8price
        from omnisim.systems.skills import skill_level as _m8sl
        if _m8l_on():
            for _sk in ("Cooking", "Smithing", "Herbalism", "Fishing",
                        "Farming", "Carpentry"):
                _mine = int(_m8sl(npc, _sk))
                _best, _blvl = None, _mine + 2
                for _m in getattr(village, "npcs", []) or []:
                    if _m is npc or _m.comps.get("dead"):
                        continue
                    try:
                        _lv = int(_m8sl(_m, _sk))
                    except Exception:
                        continue
                    if _lv >= _blvl and abs(_m.x - npc.x) + abs(_m.y - npc.y) <= 12:
                        _best, _blvl = _m, _lv
                if _best is not None:
                    try:
                        if float((npc.get("pocket", {}) or {}).get("coin", 0)) < float(_m8price(village, _best, _sk)):
                            continue
                    except Exception:
                        pass
                    # DINNER BEFORE TUITION: lessons never touch food money.
                    # A 41c lesson on a 54c purse left the miner too broke to
                    # eat; he starved with new skills (Garr, week tests). The
                    # reserve gate is the same one guarding non-food buys.
                    try:
                        from omnisim.systems.decision import can_afford_nonfood as _l4ok
                        if not _l4ok(npc, float(_m8price(village, _best, _sk)), village):
                            continue
                    except Exception:
                        pass
                    if _m8buy(village, npc, _best, _sk):
                        return True
    except Exception:
        pass
    return False


def _mend_garment(village, npc, recipe: dict) -> bool:
    """Garment repair: worn insulation low + thread in hand = service fee."""
    try:
        from omnisim.systems.production import goods as _gg
        _ins = [gid for gid, g in _gg().items()
                if float((g.get("provides", {}) or {}).get("insulation", 0)) > 0]
        if not _ins:
            return False
        _pocket = npc.get("pocket", {}) or {}
        _thread = [g for g in _gg() if "spun" in ((_gg().get(g, {}) or {}).get("tags", []) or [])
                   and _pocket.get(g, 0) > 0]
        if not _thread:
            return False
        _best, _owner = None, None
        for _m in getattr(village, "npcs", []) or []:
            if _m is npc or _m.comps.get("dead"):
                continue
            try:
                _w = (_m.get("worn", {}) or {}) if hasattr(_m, "get") else {}
            except Exception:
                continue
            for _gid in _ins:
                try:
                    _cd = float(_w.get(_gid, -1))
                except Exception:
                    continue
                if 0 <= _cd < 60.0:
                    _best, _owner = _gid, _m
                    break
            if _best is not None:
                break
        if _best is None or _owner is None:
            return False
        try:
            from omnisim.systems.pricing import fair_mc as _fmc
            _fee = max(2, int(round(float(_fmc(village, _best, 1)) / 1000.0 / 6.0)))
        except Exception:
            _fee = 2
        try:
            if float((_owner.get("pocket", {}) or {}).get("coin", 0)) < _fee:
                return False
        except Exception:
            return False
        _thr = _thread[0]
        if _pocket.get(_thr, 0) <= 0:
            return False
        _pocket[_thr] -= 1
        if _pocket[_thr] <= 0:
            _pocket.pop(_thr, None)
        (_owner.get("pocket", {}) or {})["coin"] = float((_owner.get("pocket", {}) or {}).get("coin", 0)) - _fee
        _pocket["coin"] = float(_pocket.get("coin", 0)) + _fee
        try:
            _w2 = _owner.get("worn", {}) or {}
            _w2[_best] = min(100.0, float(_w2.get(_best, 0)) + 50.0)
        except Exception:
            pass
        from omnisim.systems import schedule as _s
        from omnisim.entities.items import coin_str as _csm, item_name as _in4
        _s.seen_say(village, npc.x, npc.y,
                    f"{npc.name} patches {_owner.name}'s {_in4(_best)} (+{_csm(_fee)}).")
        _s._record_trade(village, npc.name, _owner.name, _best, 1, _fee,
                         tape=False)
        npc.comps["activity"] = "mending"
        return True
    except Exception:
        return False


def _mend(village, npc, recipe: dict) -> bool:
    """Today's whetstone logic, unchanged: dullest tool, fee, both learn."""
    from omnisim.systems import schedule as _s
    from omnisim.systems.tools import MEND_Q_MAX, dullest_tool, mend_price, wear_tick
    if _daily_used(village, npc, recipe) >= int(recipe.get("per_day_max", 3)):
        return False
    found = dullest_tool(village, npc)
    if found is None:
        return False
    fee = mend_price(village)
    owner, tool, q = found
    owner.get("pocket", {})["coin"] -= fee
    npc.get("pocket", {})["coin"] = npc.get("pocket", {}).get("coin", 0) + fee
    owner.comps.setdefault("tools", {})[tool] = min(q + 1, MEND_Q_MAX)
    from omnisim.entities.items import coin_str as _csm, item_name as _in4
    _s.seen_say(village, npc.x, npc.y,
                f"{npc.name} trues {owner.name}'s {_in4(tool)} (+{_csm(fee)}).")
    _s._record_trade(village, npc.name, owner.name, tool, 1, fee, tape=False)
    try:
        from omnisim.systems.relationships import adjust_both as _ab
        _ab(npc, owner, 2.0, 2.0)
    except Exception:
        pass
    wear_tick(village, npc, "Smithing")
    _daily_bump(village, npc, recipe)
    npc.comps["activity"] = "mending"
    return True


def _run_farm(village, npc, trade: dict) -> bool:
    """Rows stay rows: the walk calls existing sow/harvest (crops.yaml data)."""
    from omnisim.systems import schedule as _s
    farmed = _s._farm_shift(village, npc)
    if farmed is None:
        return True
    item_id, qty = farmed
    _s.bank_goods(village, npc, "Farming", item_id, qty, 0.0)
    note_supply(village, item_id, qty)
    # XP belongs to the tick loop (schedule awards 1 + tool//2 per bank,
    # bank or no — the engine never double-pays). xp: rows are Step 8 data.
    return True


def execute(village, npc, recipe: dict) -> None:
    """Consume inputs, apply modifiers, bank outputs. (XP: tick loop.)"""
    from omnisim.systems import schedule as _s
    skill = recipe.get("skill", "")
    pocket = npc.get("pocket", {}) or {}

    if recipe.get("source") == "water":
        base = sum(o.get("qty", 1) for o in recipe.get("outputs", []) or [])
        qty = _yield_qty(village, npc, recipe, base)
        try:
            from omnisim.systems.fishery import take as _take
            qty = _take(village, qty)
        except Exception:
            pass
        if qty <= 0:
            # thin water, thin nets: the shift still counts (hands worked),
            # but nothing banks — note the skunk so the books stay honest.
            try:
                note_supply(village, recipe["outputs"][0]["good"], 0)
            except Exception:
                pass
            npc.comps["activity"] = "fished-out"
            return
        out_good = recipe["outputs"][0]["good"]
        _s.bank_goods(village, npc, skill, out_good, qty, 0.0,
                      quality=_quality(village, npc, recipe))
        note_supply(village, out_good, qty)
        return

    if recipe.get("source") == "trunk":
        felled = _s._wood_shift(village, npc)
        if felled is None:
            return
        item_id, qty = felled
        _s.bank_goods(village, npc, skill, item_id, qty, 0.0,
                      quality=_quality(village, npc, recipe))
        note_supply(village, item_id, qty)
        return

    scale = 1.0
    if recipe.get("allow_partial"):  # grind what the bin holds
        haves = []
        for inp in recipe.get("inputs", []) or []:
            if "tag" in inp:
                haves.append(sum(pocket.get(g, 0) for g in goods_with_tag(inp["tag"])))
            else:
                haves.append(pocket.get(inp.get("good", ""), 0))
        need = max([inp.get("qty", 1) for inp in recipe.get("inputs", []) or [1]])
        scale = min(1.0, min(haves) / max(1, need))

    _daily_bump(village, npc, recipe)
    batch_cost = _consume_inputs(village, npc, recipe, scale)
    try:  # CONSUMPTION EMA: every eaten input notes demand-side flow, so
        # cover_of divides stock by USE, not arrivals. Supply-only flow read
        # glut as scarcity (ore: 12/day mined, ~0 used; cover 4, ask 8 vs
        # will 3; the miner starved on refused sales beside a full shelf).
        from omnisim.systems.production import note_demand_flow as _ndf
        for _ci in recipe.get("inputs", []) or []:
            _cg = _ci.get("good", "")
            if _cg:
                _ndf(village, _cg, int(round(float(_ci.get("qty", 1)) * scale)))
    except Exception:
        pass
    for o in recipe.get("outputs", []) or []:
        qty = max(1, int(round(o.get("qty", 1) * scale)))
        qty = _yield_qty(village, npc, recipe, qty)
        _s.bank_goods(village, npc, skill, o["good"], qty, batch_cost,
                      quality=_quality(village, npc, recipe))
        note_supply(village, o["good"], qty)
