"""Step 0: Instrumentation — village metrics dashboard.

Spec: thornhollow-economy-spec.md Step 0. Samples daily, no game-logic
changes (one read-only module + two counter hooks for unmet demand).

Consumption identity (hook-free): village_total is conserved except via
production (made_today), player trades (tape), and sinks. So:

    consumed(item, day) = total_prev + made - total_now - player_inflow

where total = stalls + pockets + ground, made = made_today qty.
Sinks (eating, inputs, fuel, rot, sowing) are exactly what we want the
flow EMA to measure. Seeds sown count as seed demand — correct.
"""
from __future__ import annotations


def _mstate(village) -> dict:
    try:
        st = getattr(village, "metrics_counters", None)
        if st is None:
            st = {}
            village.metrics_counters = st
        return st
    except Exception:
        return {}


def bump(village, key: str, item: str | None = None, qty: int = 1,
         npc: str = "") -> None:
    """Unmet-demand counters, day-scoped and de-duplicated.

    - declined_demand: same want re-asked all day = one shortfall.
      Keeps max shortfall per (day, npc, item).
    - declined_supply: same pocket re-offered all day = one refusal.
      Keeps max offered per (day, npc, item); sold units (recorded via
      key "sold_merchant") subtract at sample time.
    """
    try:
        from omnisim.core.clock import day_of
        day = day_of(village.clock_pts)
    except Exception:
        day = 0
    try:
        st = _mstate(village)
        k = f"{key}:{item}:{npc}:{day}"
        if key == "sold_merchant":
            st[k] = st.get(k, 0) + qty
        else:
            st[k] = max(st.get(k, 0), qty)
        # prune days older than yesterday (once per day, not per bump)
        try:
            _pd = getattr(village, "_metrics_pruned_day", None)
            if _pd != day:
                village._metrics_pruned_day = day
                for kk in [x for x in st if x.endswith(f":{day - 1}") or
                           (x.rsplit(":", 1)[-1].isdigit() and
                            int(x.rsplit(":", 1)[-1]) < day - 1)]:
                    del st[kk]
        except Exception:
            pass
    except Exception:
        pass


def unmet_summary(village) -> dict:
    """Net refused units per side+good over today+yesterday."""
    try:
        from omnisim.core.clock import day_of
        day = day_of(village.clock_pts)
    except Exception:
        day = 0
    offered: dict[tuple, int] = {}
    sold: dict[tuple, int] = {}
    demand: dict[str, int] = {}
    for k, v in _mstate(village).items():
        parts = k.split(":")
        if len(parts) < 4:
            continue
        key, item, npc, d = parts[0], parts[1], parts[2], parts[3]
        if not d.isdigit() or int(d) < day - 1:
            continue
        if key == "declined_demand":
            demand[item] = demand.get(item, 0) + v
        elif key == "declined_supply":
            offered[(item, npc)] = max(offered.get((item, npc), 0), v)
        elif key == "sold_merchant":
            sold[(item, npc)] = sold.get((item, npc), 0) + v
    out: dict[str, float] = {}
    for item in demand:
        out[f"demand:{item}"] = demand[item]
    sup: dict[str, int] = {}
    for (item, npc), off in offered.items():
        sup[item] = sup.get(item, 0) + max(0, off - sold.get((item, npc), 0))
    for item in sup:
        out[f"supply:{item}"] = sup[item]
    return out


def village_total(village, item: str) -> int:
    """Every unit in the village: shelves + backs + ground (+player pack)."""
    tot = 0
    try:
        for s in (getattr(village, "stalls", []) or []):
            tot += (s.get("stock", {}) or {}).get(item, [0, 0])[0]
        for n in (getattr(village, "npcs", []) or []):
            p = n.get("pocket", {}) or {}
            tot += sum(q for k, q in p.items() if k == item)
        for g in (getattr(village, "ground", {}) or {}).values():
            tot += g.get(item, 0)
        pl = getattr(village, "player", None)
        if pl is not None:
            pk = pl.get("pack", {}) or {}
            tot += pk.get(item, 0)
    except Exception:
        pass
    return tot


def _tape_price(village, item: str) -> float:
    """7-day average unit price (tape), else list price, else 1."""
    try:
        from omnisim.core.clock import day_of
        today = day_of(village.clock_pts)
        hist = [t for t in (getattr(village, "trades", []) or [])
                if t.get("item") == item and today - int(t.get("day", today)) <= 7]
        if hist:
            qty = sum(max(1, int(t.get("qty", 1))) for t in hist)
            val = sum(int(t.get("price", 0)) * max(1, int(t.get("qty", 1))) for t in hist)
            return val / max(1, qty)
    except Exception:
        pass
    try:
        from omnisim.entities.items import item_defs
        return float(item_defs()["items"].get(item, {}).get("price", 1) or 1)
    except Exception:
        return 1.0


def npc_wealth(village, npc) -> float:
    """Purse + inventory at tape price + deeds at PURCHASE price.
    Deeds count what the soul PAID, not today quote: the quote swings
    60c-124kc on tape flicker (bread esp 1.8-5.9 on saleability 0.3-1.0)
    while the paid coin never moves. Mark-to-market deeds read gini 0.87
    on a village with 0 deaths and full pockets (Odo+124k on a 700c pond,
    Mira+111k on a 250c plot)."""
    w = float((npc.get("pocket", {}) or {}).get("coin", 0))
    for k, q in (npc.get("pocket", {}) or {}).items():
        if k == "coin" or not q:
            continue
        w += q * _tape_price(village, k)
    try:  # STEP 9: deeds count purchase price (paid coin), never the
        # live capitalized quote - the quote is weather, the purse is land.
        from omnisim.systems.land import parcels_of as _parc9
        from omnisim.systems.land import on as _land9_on
        if _land9_on():
            for _p in _parc9(village) or []:
                if _p.get("owner") == getattr(npc, "name", "?"):
                    w += float(_p.get("paid", _p.get("price", 0)) or 0)
    except Exception:
        pass
    return w


def sample_day(village, state: dict) -> dict:
    """Full dashboard sample. `state` persists across days (owned by caller)."""
    from omnisim.core.clock import day_of
    today = day_of(village.clock_pts)
    # STEP 6 GATE (A-population): the books hold all 21 founders. Roster reads
    # comps (Entity.get cannot see dead: it reads comps, and comps[dead] IS
    # the flag - the old n.get(dead) filter read None forever). Living souls
    # enter at live wealth; the buried enter at LAST wealth from fallen_log
    # (ruling: the dead stay in the population at what they held, never 0).
    _reg = getattr(village, "fallen_log", None) or []
    _buried = {r.get("name"): r for r in _reg if isinstance(r, dict)}
    npcs = [n for n in (getattr(village, "npcs", []) or [])
            if not n.comps.get("dead")]
    out: dict = {"day": today}
    out["gate_population"] = len(npcs) + len(_buried)
    out["gate_roster"] = sorted([
        {"name": n.name, "status": "alive",
         "trade": n.get("work_skill") or n.get("role") or "?",
         "wealth": round(float(npc_wealth(village, n)), 1)}
        for n in npcs] + [{"name": r.get("name"), "status": "dead",
         "trade": r.get("trade", "?"), "wealth": r.get("wealth", 0.0),
         "cause": r.get("cause"), "day": r.get("day")}
        for r in _reg if isinstance(r, dict)], key=lambda r: r["name"])

    # --- wealth ---
    _live_w = [(n.name, n.get("work_skill") or n.get("role") or "?",
                npc_wealth(village, n)) for n in npcs]
    _dead_w = [(r.get("name"), r.get("trade", "?"),
                float(r.get("wealth", 0.0)))
               for r in _reg if isinstance(r, dict)]
    wealth = _live_w + _dead_w
    ws = sorted(w for _, _, w in wealth)
    n = max(1, len(ws))
    mean = sum(ws) / n
    gini = (sum(abs(a - b) for a in ws for b in ws) / (2 * n * n * mean)
            if mean > 0 else 0.0)
    by_trade: dict[str, list] = {}
    for name, trade, w in wealth:
        by_trade.setdefault(trade, []).append(w)
    out["wealth_gini"] = round(gini, 3)
    out["wealth_by_trade"] = {t: round(sorted(v)[len(v) // 2], 1)
                              for t, v in sorted(by_trade.items())}

    # --- income per trade (7d trailing, sales − purchases, from tape) ---
    inc: dict[str, list] = {}
    plyr = ""
    try:
        plyr = getattr(village, "player", {}).get("name", "") or ""
    except Exception:
        pass
    names = {n.name: (n.get("work_skill") or n.get("role") or "?") for n in npcs}
    per_npc: dict[str, float] = {}
    for t in (getattr(village, "trades", []) or []):
        if today - int(t.get("day", today)) > 7:
            continue
        amt = int(t.get("price", 0)) * max(1, int(t.get("qty", 1)))
        s, b = t.get("seller", ""), t.get("buyer", "")
        if s in names and s != plyr:
            per_npc[s] = per_npc.get(s, 0.0) + amt
        if b in names and b != plyr:
            per_npc[b] = per_npc.get(b, 0.0) - amt
    for name, net in per_npc.items():
        inc.setdefault(names[name], []).append(net / 7.0)
    out["income_per_trade"] = {t: round(sum(v) / max(1, len(v)), 1)
                               for t, v in sorted(inc.items())}

    # --- consumption flows + cover (identity method) ---
    prev = state.get("prev_total", {})
    made = getattr(village, "made_today", {}) or {}
    ema = state.setdefault("flow_ema", {})
    pin = state.setdefault("player_inflow", {})
    cur: dict[str, int] = {}
    goods: set[str] = set(prev) | set(made)
    try:
        from omnisim.entities.items import item_defs
        goods |= set(item_defs()["items"])
    except Exception:
        pass
    for item in goods:
        if item == "coin":
            continue
        tot = village_total(village, item)
        cur[item] = tot
        if item in prev:
            m = made.get(item, {}).get("qty", 0) if isinstance(
                made.get(item), dict) else 0
            consumed = prev.get(item, 0) + m - tot - pin.get(item, 0)
            consumed = max(0, consumed)
            ema[item] = 0.3 * consumed + 0.7 * ema.get(item, consumed or 0.1)
    state["prev_total"] = cur
    state["player_inflow"] = {}
    cover = {}
    for item, flow in ema.items():
        if flow and flow > 0.05:
            cover[item] = round(cur.get(item, 0) / flow, 1)
    out["days_of_cover"] = dict(sorted(cover.items(),
                                       key=lambda kv: kv[1])[:16])
    out["daily_flow"] = {k: round(v, 2) for k, v in
                         sorted(ema.items(), key=lambda kv: -kv[1])[:16]}

    # --- prices: tape + chain ratios ---
    tapes = {}
    for item in ("bread", "meal", "flour", "wheat", "barley", "fish",
                 "firewood", "iron_ore", "remedy", "pot", "beam"):
        tapes[item] = round(_tape_price(village, item), 2)
    out["tape"] = tapes
    b, f, w = tapes["bread"], tapes["flour"] or 1, tapes["wheat"] or 1
    out["price_ratio"] = {"bread/flour": round(b / (f or 1), 2),
                          "flour/wheat": round(f / (w or 1), 2)}

    # --- needs ---
    hungers = [float(n.get("hunger", 0)) for n in npcs]
    hungers.sort()
    out["need_pain_avg"] = {"hunger": round(sum(hungers) / max(1, len(hungers)), 1)}
    out["need_pain_p95"] = {"hunger": round(
        hungers[min(len(hungers) - 1, int(len(hungers) * 0.95))], 1)
        if hungers else 0}
    try:  # STEP 3: every channel on the dashboard (cold gates winter causality)
        from omnisim.systems.needs3 import level_of as _n3_level
        from omnisim.systems.needs3 import on as _n3_on
        if _n3_on():
            for _ch in ("thirst", "rest", "cold", "hp"):
                _lv = sorted(_n3_level(n, _ch) for n in npcs)
                if _lv:
                    out["need_pain_avg"][_ch] = round(sum(_lv) / len(_lv), 1)
                    out["need_pain_p95"][_ch] = round(
                        _lv[min(len(_lv) - 1, int(len(_lv) * 0.95))], 1)
    except Exception:
        pass
    out["starved_ticks"] = sum(1 for h in hungers if h >= 90)
    out["missed_meals"] = sum(1 for h in hungers if h >= 70)
    try:  # STEP 4: mood on the dashboard (flag-gated; absent = unticked)
        from omnisim.systems.mood import on as _m4m_on
        if _m4m_on():
            _moods = sorted(float(n.get("mood", 0.0)) for n in npcs)
            if _moods:
                out["mood_avg"] = round(sum(_moods) / len(_moods), 1)
                out["mood_min"] = round(_moods[0], 1)
    except Exception:
        pass

    # --- till ---
    till = sum(s.get("till", 0) for s in (getattr(village, "stalls", []) or []))
    out["till"] = till
    out["till_delta"] = int(round(till - state.get("prev_till", till)))
    state["prev_till"] = till

    # --- idle / switches / deaths ---
    idle = sum(1 for n in npcs if (n.get("activity") or "") in
               ("glutted", "idle", "waiting"))
    out["idle_now"] = idle
    prev_skill = state.setdefault("prev_skill", {})
    switches = 0
    for nn in npcs:
        s = nn.get("work_skill") or ""
        if nn.name in prev_skill and prev_skill[nn.name] and s != prev_skill[nn.name]:
            switches += 1
        prev_skill[nn.name] = s
    out["switches_today"] = switches
    out["builds_today"] = 0
    # A-population deaths: the register counts the buried (n.get(dead) read
    # None forever - same Entity.get/comps blind spot as the roster filter).
    out["deaths_total"] = len([r for r in _reg if isinstance(r, dict)])

    # --- production volumes (parity comparisons across steps) ---
    try:
        out["made"] = {k: v["qty"] for k, v in
                       (getattr(village, "made_today", {}) or {}).items()}
    except Exception:
        out["made"] = {}

    # --- unmet demand (counter hooks in schedule.py) ---
    out["unmet_demand"] = unmet_summary(village)

    # --- STEP 7: buildings standing (id, type, owner, status) ---
    try:
        out["buildings"] = [
            {"id": b.get("id"), "type": b.get("type"),
             "owner": b.get("owner"), "status": b.get("status")}
            for b in (getattr(village, "buildings", []) or [])]
    except Exception:
        out["buildings"] = []
    # --- STEP 9: deeds standing (id, owner, quote) + sales log ---
    try:
        from omnisim.systems.land import on as _l9m_on
        if _l9m_on():
            from omnisim.systems.land import quote as _l9q
            out["deeds"] = [
                {"id": p.get("id"), "owner": p.get("owner"),
                 "good": p.get("good"), "quote": _l9q(village, p)}
                for p in (getattr(village, "land_parcels", []) or [])]
            out["land_sales"] = list(getattr(village, "land_sales", []) or [])
        else:
            out["deeds"] = []
            out["land_sales"] = []
    except Exception:
        out["deeds"] = []
        out["land_sales"] = []
    return out


def dashboard_lines(samples: list[dict]) -> list[str]:
    """Human-readable summary of the latest sample + trends."""
    if not samples:
        return ["no samples"]
    s = samples[-1]
    lines = [
        f"day {s['day']}: gini {s['wealth_gini']} | till {s['till']} "
        f"(Δ {s['till_delta']:+d}) | hunger avg {s['need_pain_avg']['hunger']} "
        f"p95 {s['need_pain_p95']['hunger']} | starved {s['starved_ticks']} "
        f"missed {s['missed_meals']} | idle {s['idle_now']} "
        f"switches {s['switches_today']} deaths {s['deaths_total']}",
        f"  wealth: " + " ".join(f"{t}={v}"
                                 for t, v in s["wealth_by_trade"].items()),
        f"  income/d: " + " ".join(f"{t}={v}"
                                   for t, v in s["income_per_trade"].items()),
        f"  cover: " + " ".join(f"{k}={v}d" for k, v in
                                list(s["days_of_cover"].items())[:10]),
        f"  tape: " + " ".join(f"{k}={v}" for k, v in s["tape"].items()),
        f"  ratios: bread/flour={s['price_ratio']['bread/flour']} "
        f"flour/wheat={s['price_ratio']['flour/wheat']}",
    ]
    if s["unmet_demand"]:
        lines.append("  unmet: " + " ".join(f"{k}={v}"
                                            for k, v in s["unmet_demand"].items()))
    try:
        _b7 = s.get("buildings", []) or []
        if _b7:
            lines.append("  buildings: " + " ".join(
                f"{b.get('type')}/{b.get('owner')}/{b.get('status')}" for b in _b7))
    except Exception:
        pass
    return lines
