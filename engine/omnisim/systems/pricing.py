"""Dynamic stall pricing — V1 Foundations (GDD 4.1).

Final Value = Base x Quality x Origin x Novelty x Scarcity x Demand
              x Reputation x Relationship x Skill

Scarcity runs 0.5 (glut) to 3.0 (bare shelf) around par (restock qty, else 4).
Every other modifier defaults to 1.0 when its data is absent, so legacy
stalls reproduce the old base x scarcity x skill numbers exactly. NPCs trade
on the same numbers — no cheating.
"""
from __future__ import annotations
from omnisim.entities.items import defs as item_defs


def quality_mod(q: int = 1) -> float:
    try:
        from omnisim.entities.items import q_mult
        return float(q_mult(q))
    except Exception:
        return 1.0


def origin_mod(origin: str | None = None, town_rep: float = 0.0) -> float:
    """Town reputation for a craft raises all its goods (GDD 4.1 Origin)."""
    if not origin:
        return 1.0
    return max(0.5, 1.0 + 0.05 * float(town_rep))


def novelty_mod(stall: dict, item_id: str, lore: dict | None = None) -> float:
    """First-of-its-kind on a shelf pays more; the tenth does not (GDD Novelty)."""
    if not lore:
        return 1.0
    try:
        same = sum(1 for k in (stall.get("stock", {}) or {}) if k == item_id)
        maker = str(lore.get("by", ""))
        # crude proxy: saturated shelf + known maker = less novel
        if same >= 5 and maker:
            return 0.9
    except Exception:
        pass
    return 1.0


def demand_mod(village=None, item_id: str = "") -> float:
    """Desire separate from scarcity: hungry towns pay more for food,
    threatened towns more for arms (GDD 4.1 Demand)."""
    if village is None:
        return 1.0
    try:
        hungry = 0
        pop = 0
        for n in getattr(village, "npcs", []):
            pop += 1
            if float(n.get("hunger", 0)) > 70:
                hungry += 1
        if pop and item_id in ("bread", "apple", "meal", "fish", "wheat",
                               "potted_meal"):
            return 1.0 + 0.3 * (hungry / max(1, pop))
    except Exception:
        pass
    return 1.0


def reputation_mod(entity=None) -> float:
    """Creator's personal reputation (GDD 4.1). Stored as comps['rep']."""
    if entity is None:
        return 1.0
    try:
        rep = float(entity.get("rep", 0)) if hasattr(entity, "get") else 0.0
        return max(0.7, 1.0 + 0.02 * rep)
    except Exception:
        return 1.0


def relationship_mod(buyer=None, seller_name: str | None = None) -> float:
    """Friends get a discount, rivals a markup (GDD 4.1 Relationship)."""
    if buyer is None or not seller_name:
        return 1.0
    try:
        rel = buyer.get("rel", {}) if hasattr(buyer, "get") else {}
        v = float(rel.get(seller_name, 0)) if isinstance(rel, dict) else 0.0
        return max(0.8, min(1.2, 1.0 - v * 0.002))
    except Exception:
        return 1.0


def par_of(stall: dict, item_id: str) -> int:
    restock = stall.get("restock", {})
    if item_id in restock:
        return max(1, restock[item_id][0])
    return 4


def scarcity_of(stall: dict, item_id: str) -> float:
    par = par_of(stall, item_id)
    qty = stall.get("stock", {}).get(item_id, [0, 0])[0]
    return max(0.5, min(3.0, par / max(1, qty)))


def trend_word(stall: dict, item_id: str) -> str:
    m = scarcity_of(stall, item_id)
    if m >= 2.5:
        return "famine"
    if m >= 1.5:
        return "scarce"
    if m <= 0.6:
        return "glut"
    return ""


def skill_move(trading: int, buying: bool) -> float:
    step = 0.05 * (max(1, trading) - 1)
    return (1 - step) if buying else (1 + step)


# --- Step 2: pricing engine (spec thornhollow-economy-spec.md Step 2) --------
# Money computes in milli-copper (1c = 1000mc) and settles to integer copper
# with per-NPC carry remainders, so cheap goods stop eating rounding.
# tape[good] is an EMA (alpha per trade) of quality-normalized prices with a
# per-day move cap; fair = tape, else reference build-up. Asks blend cost
# floors with cover-scarcity; bids blend need value with fair bands; the
# merchant quotes fair +/- margin with till-aware width and cover appetite.

MC = 1000


_DOC_CACHE: dict = {}
_DOC_MTIME: float | None = None


def _econ_path():
    from pathlib import Path
    return Path(__file__).resolve().parents[3] / "data" / "economy.yaml"


def _econ_doc() -> dict:
    global _DOC_CACHE, _DOC_MTIME
    try:
        from omnisim.systems.production import _load as _pload
        return _pload("economy")
    except Exception:
        pass
    try:
        import yaml
        p = _econ_path()
        mt = p.stat().st_mtime
        if _DOC_MTIME is None or mt != _DOC_MTIME:
            _DOC_MTIME = mt
            _DOC_CACHE = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return _DOC_CACHE
    except Exception:
        return _DOC_CACHE or {}


def refresh_econ_doc() -> None:
    """Tests call this after editing economy.yaml on disk mid-session."""
    global _DOC_CACHE, _DOC_MTIME
    _DOC_MTIME = None
    _DOC_CACHE = {}
    try:
        from omnisim.systems.production import reset_docs
        reset_docs()
    except Exception:
        pass


def engine_params() -> dict:
    try:
        return _econ_doc().get("pricing", {})
    except Exception:
        return {}


def engine_on() -> bool:
    try:
        return bool(_econ_doc().get("flags", {}).get("step_2", False))
    except Exception:
        return False


def _tape_store(village) -> dict:
    try:
        t = getattr(village, "price_tape", None)
        if not isinstance(t, dict):
            t = {}
            village.price_tape = t
        return t
    except Exception:
        return {}


def note_trade_price(village, good: str, price_copper: float, q: int = 1) -> None:
    """Fold one deal into the tape: quality-normalized, EMA alpha, always
    capped at +/- cap from the last close. Intraday runs DON'T get a free
    ratchet — convex evening hunger plus same-day feedback spiked bread
    3c to 8c in one afternoon and poisoned every book in the village."""
    try:
        from omnisim.core.clock import day_of
        from omnisim.entities.items import q_mult
        today = day_of(village.clock_pts)
    except Exception:
        today = 0
    try:
        p = engine_params()
        alpha = float(p.get("tape_alpha", 0.2))
        cap = float(p.get("tape_daily_cap", 0.15))
        norm = max(1.0, float(price_copper)) / max(1.0, float(q_mult(q)))
        store = _tape_store(village)
        rec = store.get(good)
        if not rec:
            store[good] = {"mc": norm * MC, "day": today, "close": norm * MC}
            return
        if int(rec.get("day", today)) < today:
            rec["close"] = rec["mc"]  # yesterday's close anchors the cap
            rec["day"] = today
        ema = alpha * norm * MC + (1 - alpha) * float(rec["mc"])
        close = float(rec.get("close", ema))
        lo, hi = close * (1 - cap), close * (1 + cap)
        rec["mc"] = max(lo, min(hi, ema))
    except Exception:
        pass


def _cap_on() -> bool:
    try:
        return bool(_econ_doc().get("flags", {}).get("ask_cap_realized", False))
    except Exception:
        return False


def _realized_params() -> tuple:
    try:
        prm = engine_params()
        W = max(1, int(prm.get("realized_window", 7)))
    except Exception:
        W = 7
    try:
        k = float(engine_params().get("realized_mult", 1.5))
    except Exception:
        k = 1.5
    return W, k


def _realized_store(village) -> dict:
    try:
        r = getattr(village, "realized_sales", None)
        if not isinstance(r, dict):
            r = {}
            village.realized_sales = r
        return r
    except Exception:
        return {}


def note_realized_sale(village, good: str, price_copper: float, q: int = 1) -> None:
    """Part A: village-level log of last W settled quality-normalized prices
    per good. Fed from every settled deal (haggle + schedule paths feed
    through the same helper). Tape untouched."""
    try:
        from omnisim.entities.items import q_mult
        norm = max(1.0, float(price_copper)) / max(1.0, float(q_mult(q)))
    except Exception:
        try:
            norm = max(1.0, float(price_copper))
        except Exception:
            return
    try:
        W, _k = _realized_params()
        store = _realized_store(village)
        log = store.get(good)
        if not isinstance(log, list):
            log = []
            store[good] = log
        log.append(float(norm))
        del log[:-W]
    except Exception:
        pass


def realized_wtp(village, good: str):
    """Trailing mean of the realized log (copper, quality-normalized), or
    None before any sale."""
    try:
        log = _realized_store(village).get(good) or []
        if not log:
            return None
        return sum(float(x) for x in log) / max(1, len(log))
    except Exception:
        return None


def realized_count(village, good: str) -> int:
    try:
        log = _realized_store(village).get(good) or []
        return len(log)
    except Exception:
        return 0


_TALLY_REASONS = {
    "flag_off": "flag_off",
    "thin_window": "insufficient_history",
    "no_bind": "not_binding",
    "capped": "cap_binding",
    "floor_wins": "floor_over_cap",
}


def _tally_store(village) -> dict:
    try:
        d = getattr(village, "bind_tally_store", None)
        if not isinstance(d, dict):
            d = {}
            village.bind_tally_store = d
        return d
    except Exception:
        return {}


def _note_bind_tally(village, good: str, reason: str) -> None:
    """Measurement only: one counter per (good, tally-bucket). Runs with
    the flag on AND off; never changes an ask."""
    try:
        bucket = _TALLY_REASONS.get(str(reason), "error")
        d = _tally_store(village)
        g = d.get(str(good))
        if not isinstance(g, dict):
            g = {}
            d[str(good)] = g
        g[bucket] = int(g.get(bucket, 0)) + 1
    except Exception:
        pass


def bind_tally(village) -> dict:
    """Read the deal-level bind tally: {good: {bucket: count}}."""
    try:
        return {g: dict(c) for g, c in
                _tally_store(village).items()}
    except Exception:
        return {}


def _bump_cap_stat(village, key: str) -> None:
    try:
        from omnisim.systems.metrics import bump as _bump
        _bump(village, key, None, 1)
    except Exception:
        pass


def apply_realized_cap(village, seller, good: str, ask_copper: float,
                       floor: float = 1) -> tuple:
    """Part A: ask = min(ask, 1.5 x realized_wtp); floor wins below floor.
    Flag off => identical ask + reason flag_off. <3 sales => thin_window.
    Counts land in metrics_counters (cap_bound / cap_thin / cap_floor /
    cap_flag_off) so the paired run can report cap/floor days."""
    try:
        ask = float(ask_copper)
    except Exception:
        return ask_copper, {"reason": "bad_ask"}
    try:
        fl = max(1.0, float(floor))
    except Exception:
        fl = 1.0
    if not _cap_on():
        _note_bind_tally(village, good, "flag_off")
        return ask, {"reason": "flag_off"}
    try:
        W, k = _realized_params()
        log = _realized_store(village).get(good) or []
        if len(log) < 3:
            _bump_cap_stat(village, "cap_thin")
            _note_bind_tally(village, good, "thin_window")
            return ask, {"reason": "thin_window", "n": len(log), "window": W}
        mean = sum(float(x) for x in log) / max(1, len(log))
        cap = max(1.0, k * mean)
        if ask <= cap:
            _note_bind_tally(village, good, "no_bind")
            return ask, {"reason": "no_bind", "mean": mean, "cap": cap,
                         "n": len(log)}
        _bump_cap_stat(village, "cap_bound")
        if fl >= cap:
            _bump_cap_stat(village, "cap_floor")
            _note_bind_tally(village, good, "floor_wins")
            return fl, {"reason": "floor_wins", "floor_over_cap": True,
                        "bound": True, "mean": mean, "cap": cap, "n": len(log)}
        _note_bind_tally(village, good, "capped")
        return cap, {"reason": "capped", "bound": True, "mean": mean,
                     "cap": cap, "n": len(log)}
    except Exception:
        return ask, {"reason": "error"}


def tape_mc(village, good: str):
    """EMA value in mc, or None before any trade — or after a week silent
    (stale tapes don't price; the build-up takes over until trade resumes)."""
    try:
        from omnisim.core.clock import day_of
        today = day_of(village.clock_pts)
    except Exception:
        today = 0
    try:
        rec = _tape_store(village).get(good)
        if rec and today - int(rec.get("day", today)) <= 7:
            return max(1.0, float(rec["mc"]))
    except Exception:
        pass
    return None


def fair_mc(village, good: str, q: int = 1) -> float:
    """Tape once trading; the reference build-up times quality before that.
    Chain guard: when inputs trade above their build-up (panic grain), the
    fallback prices replacement cost, not history — flour never lists below
    the wheat it ate, or the chain freezes between two true prices."""
    t = tape_mc(village, good)
    try:
        from omnisim.entities.items import q_mult
        qm = max(1.0, float(q_mult(q)))
    except Exception:
        qm = 1.0
    if t is not None:
        return t * qm
    try:
        from omnisim.systems.production import reference_value, recipes, goods_with_tag
        ref = reference_value(good)
        best = ref
        try:
            tv = 1.0
            from omnisim.systems.production import _load as _pload
            tv = float(_pload("economy").get("time_value_copper_per_tick", 1))
        except Exception:
            pass
        for r in recipes().values():
            outs = [o for o in r.get("outputs", []) or [] if o.get("good") == good]
            if not outs:
                continue
            cost, ok = 0.0, True
            for inp in r.get("inputs", []) or []:
                qq = inp.get("qty", 1)
                if "good" in inp:
                    cands = [inp["good"]]
                else:
                    cands = goods_with_tag(inp["tag"])
                if not cands:
                    ok = False
                    break
                c = min(_input_replacement(village, g2) for g2 in cands)
                if c == float("inf"):
                    ok = False
                    break
                cost += c * qq
            if not ok:
                continue
            cost += tv * int(r.get("active_ticks", 1))
            # cheapest replacement NOW; the fallback is the higher of history
            # (build-up) and today (replacement) — never lists a loaf below
            # the panic grain it ate, nor fine work below its calm cost.
            best = max(best, cost / max(1, outs[0].get("qty", 1)))
        if best != float("inf"):
            return max(1.0, best) * MC * qm
    except Exception:
        pass
    try:
        from omnisim.entities.items import _items
        return max(1.0, float(_items().get(good, {}).get("price", 1) or 1)) * MC * qm
    except Exception:
        return 1.0 * MC * qm


def _input_replacement(village, good: str) -> float:
    """What one more unit costs right now: tape when trading, else build-up."""
    t = tape_mc(village, good)
    if t is not None:
        return t / MC
    try:
        from omnisim.systems.production import reference_value
        return reference_value(good)
    except Exception:
        return float("inf")


def cover_of(village, good: str) -> float:
    """Days of cover: village stock over expected daily flow (mouths, never
    bins — the canonical cover; production delegates here)."""
    try:
        from omnisim.systems.metrics import village_total, unmet_summary
        stock = village_total(village, good)
    except Exception:
        stock = 0
    flow = 0.1
    try:
        fl = getattr(village, "prod_supply", None) or {}
        from omnisim.core.clock import day_of
        today = day_of(village.clock_pts)
        for g, (d, ema) in fl.items():
            if g == good:
                flow = max(flow, ema * (0.5 ** max(0, today - d)))
        # USE divides cover, not arrivals: demand-side EMA (eaten inputs,
        # meals) sets the flow when something actually eats the good; mute
        # supply-only flow reads glut as scarcity. A stocked shelf nobody
        # eats from is deep cover (glut pricing), however fast it arrives.
        try:
            _dfl = getattr(village, "prod_demand", None) or {}
            for _g, (_dd, _dema) in _dfl.items():
                if _g == good:
                    _use = _dema * (0.5 ** max(0, today - _dd))
                    if _use > 0:
                        flow = max(0.1, stock / max(1.0, _use))
        except Exception:
            pass
        try:
            _stock_now = float(village_total(village, good))
        except Exception:
            _stock_now = 1.0
        # Unmet demand is future mouths, not present cover: it counts only
        # when the village holds NOTHING (true shortage). Counting it beside
        # stocked shelves faked famine on full bins (ore: shelf 49, cover 4,
        # ask 8 vs will 3; the miner starved on refused sales).
        dem = sum(v for k, v in unmet_summary(village).items()
                  if k == f"demand:{good}" and _stock_now <= 0) / 2.0
        flow = max(flow, dem, 0.1)
    except Exception:
        pass
    return stock / max(0.001, flow)


def scarcity_mult(village, good: str) -> float:
    """Cover-scarcity: bare shelves pay up to 1.8x, gluts sink to 0.7x —
    and past 5x cover the floor drops to 0.3x (fire sale). Mountains must
    clear: a 0.7 clamp on 100-day wheat props asks at 3x harvest cost and
    the mill starves beside the mountain."""
    try:
        from omnisim.systems.production import goods
        target = float(goods().get(good, {}).get("target_cover", 5))
    except Exception:
        target = 5.0
    try:
        p = engine_params()
        lo = float(p.get("scarcity_lo", 0.7))
        hi = float(p.get("scarcity_hi", 1.8))
        exp = float(p.get("scarcity_exp", 0.5))
    except Exception:
        lo, hi, exp = 0.7, 1.8, 0.5
    cover = cover_of(village, good)
    if cover > 5 * target:
        lo = min(lo, 0.3)
    try:
        from omnisim.systems.metrics import village_total as _vt
        _stock = float(_vt(village, good))
    except Exception:
        _stock = 1.0
    if _stock <= 0:
        try:
            hi = max(hi, float(p.get("scarcity_hi_nosupply", hi)))
        except Exception:
            pass
    return max(lo, min(hi, (target / max(cover, 0.25)) ** exp))


def cost_floor_mc(village, seller, good: str, mark_to_market: bool = False) -> float:
    """FIFO oldest lot, else the cost guess; overhead rides Step 7.
    Market-makers mark to market in glut (cover past 2x target): panic-bought
    shelves clear at fair instead of freezing the village behind book cost."""
    try:
        from omnisim.systems.haggle import cost_of
        book = max(1.0, float(cost_of(seller, good))) * MC
    except Exception:
        book = 1.0 * MC
    if mark_to_market:
        try:
            from omnisim.systems.production import goods
            target = float(goods().get(good, {}).get("target_cover", 14))
            if cover_of(village, good) > 2 * target:
                return min(book, fair_mc(village, good))
        except Exception:
            pass
    return book


def _is_merchant(entity) -> bool:
    try:
        return bool(entity.get("invincible"))
    except Exception:
        return False


def merchant_margin(village, stall, good: str, side: str) -> float:
    """Base m = max(8%, 1c/fair); till-poor widens buys, till-rich narrows."""
    fair = fair_mc(village, good)
    m = max(0.08, MC / max(1.0, fair))
    m = min(m, 0.5)  # granularity floor never inverts the quote (sub-2c
    # fairs would price buys negative); copper settles the last mile
    try:
        till = float((stall or {}).get("till", 0))
        reserve = float((stall or {}).get("float", 1500) or 1500)
        frac = till / max(1.0, reserve)
        p = engine_params()
        poor = float(p.get("till_poor_frac", 0.5))
        rich = float(p.get("till_rich_frac", 0.9))
        if frac < poor and side == "buy":
            return m * 1.5
        if frac > rich:
            return m * (0.7 if side == "sell" else 0.8)
    except Exception:
        pass
    return m


def merchant_quote_mc(village, stall, good: str, side: str, q: int = 1) -> float:
    """Market-maker bands: buy at fair*(1-m), sell at fair*(1+m)."""
    fair = fair_mc(village, good, q)
    m = merchant_margin(village, stall, good, side)
    return fair * (1 - m) if side == "buy" else fair * (1 + m)


def marginal_value_mc(village, buyer, good: str, q: int = 1) -> float:
    """What one more unit is worth to this buyer, pre-haggle. Food: hunger
    urgency x food value (the existing bite logic); cures: full fair when
    hurt; everything else: fair with a slight premium (Step 3 refines)."""
    try:  # STEP 3: pain-relief marginal value + want premiums (flag-gated)
        from omnisim.systems.needs3 import marginal_value_mc as _n3_mv
        from omnisim.systems.needs3 import on as _n3_on
        if _n3_on():
            return _n3_mv(village, buyer, good, q)
    except Exception:
        pass
    fair = fair_mc(village, good, q)
    try:
        from omnisim.entities.items import _items, food_value as _fv
        node = _items().get(good, {}) or {}
        if float(node.get("food", 0)) > 0:
            from omnisim.systems.valuation import urgency as _urg, drives as _drv
            from omnisim.systems.pricing import scarcity_of as _scar
            try:
                hunger = float(buyer.get("hunger", 0))
            except Exception:
                hunger = 0.0
            try:
                _floor = float(_drv().get("pantry_floor", 0.35))
            except Exception:
                _floor = 0.35
            worth = max(float(_urg(hunger, "hunger")), _floor) * float(_fv(buyer, good, 1))
            return max(0.0, worth) * MC
        if float(node.get("heal", 0)) > 0:
            try:
                hurt = int(buyer.get("hp", 10)) < int(buyer.get("max_hp", 10))
            except Exception:
                hurt = False
            if hurt:
                return fair * 1.6
    except Exception:
        pass
    return fair * 1.2


def ask_mc(village, seller, good: str, q: int = 1) -> float:
    """max(cost floor x margin, fair x scarcity x rep). Merchants quote bands."""
    if _is_merchant(seller):
        try:
            stall = (village.stalls or [None])[0]
        except Exception:
            stall = None
        return merchant_quote_mc(village, stall, good, "sell", q)
    floor = cost_floor_mc(village, seller, good)
    try:
        mm = 1.0 + float(engine_params().get("min_margin", 0.05))
    except Exception:
        mm = 1.05
    try:  # STEP 5: earned reputation moves the ask (1.0 until Step 5)
        from omnisim.systems.memory import rep_premium as _m5_rep
        _rep5 = float(_m5_rep(village, seller, good))
    except Exception:
        _rep5 = 1.0
    raw = max(floor * mm, fair_mc(village, good, q) * scarcity_mult(village, good) * _rep5)
    try:  # Part A+B: markdown first, then the realized cap (cap wins).
        _marked, _ = ask_with_markdown(village, seller, good, raw / MC, (floor * mm) / MC)
        raw = max(1.0, float(_marked)) * MC
    except Exception:
        pass
    return raw


def bid_mc(village, buyer, good: str, q: int = 1) -> float:
    """min(need value, fair x 1.6, what the purse holds). Merchants bid bands."""
    if _is_merchant(buyer):
        try:
            stall = (village.stalls or [None])[0]
        except Exception:
            stall = None
        return merchant_quote_mc(village, stall, good, "buy", q)
    fair = fair_mc(village, good, q)
    try:
        coin = float((buyer.get("pocket", {}) or {}).get("coin", 0))
    except Exception:
        coin = 0.0
    return min(marginal_value_mc(village, buyer, good, q), fair * 1.6, max(0.0, coin) * MC)


def ask_with_markdown(village, seller, good: str, ask_copper: float,
                        floor: float = 1) -> tuple:
    """Part B seam: markdown first (never below floor or 0.9x fair), then
    the Part A cap. Cap wins. Flag-gated on both sides; both off =>
    identical ask."""
    try:
        ask = float(ask_copper)
    except Exception:
        return ask_copper, {"reason": "bad_ask"}
    marr = {"mult": 1.0, "reason": "flag_off"}
    try:
        from omnisim.systems.decision import markdown_mult as _mm
        mult = max(0.90, min(1.0, float(_mm(village, seller, good))))
        marr = {"mult": mult}
        if mult < 1.0:
            try:
                fair = float(fair_mc(village, good)) / MC
            except Exception:
                fair = 0.0
            try:
                fl = max(1.0, float(floor))
            except Exception:
                fl = 1.0
            floor_bound = max(fl, 0.9 * fair if fair > 0 else fl)
            marked = max(ask * mult, floor_bound)
            if marked < ask:
                ask = marked
                marr["reason"] = "marked_down"
                try:
                    from omnisim.systems.metrics import bump as _bump
                    _bump(village, "markdown_applied", None, 1)
                except Exception:
                    pass
            else:
                marr["reason"] = "floor_bound"
        else:
            marr["reason"] = "no_mark"
    except Exception:
        pass
    try:
        fl = max(1.0, float(floor))
    except Exception:
        fl = 1.0
    capped, info = apply_realized_cap(village, seller, good, ask, fl)
    try:
        info = dict(info or {})
        info["markdown"] = marr
    except Exception:
        pass
    return capped, info


def settle(entity, mc: float) -> int:
    """Milli-copper to integer coin with per-NPC carry remainder."""
    try:
        total = float(mc) + float((entity.get("mc_rem", 0) or 0))
    except Exception:
        total = float(mc)
    copper = max(1, int(round(total / MC)))
    try:
        entity.comps["mc_rem"] = total - copper * MC
    except Exception:
        pass
    return copper


def _chain_good(good: str) -> bool:
    """Staple-chain goods: edible foods plus grain/flour that become them.
    The market-maker always clears these — mouths don't wait out a glut."""
    try:
        from omnisim.entities.items import _items
        if float((_items().get(good, {}) or {}).get("food", 0)) > 0:
            return True
    except Exception:
        pass
    try:
        from omnisim.systems.production import goods
        tags = set((goods().get(good, {}) or {}).get("tags", []) or [])
        return bool(tags & {"grain", "flour", "milled", "staple", "baked"})
    except Exception:
        return False


def appetite_cap(village, good: str) -> int:
    """How many units the merchant takes: cover-scaled appetite, smooth."""
    try:
        from omnisim.systems.production import goods
        target = float(goods().get(good, {}).get("target_cover", 14))
    except Exception:
        target = 14.0
    try:
        k = float(engine_params().get("appetite_k", 1.0))
    except Exception:
        k = 1.0
    cover = cover_of(village, good)
    appetite = max(0.0, min(1.0, 1 - cover / max(0.001, target) * k))
    if _chain_good(good):
        appetite = max(appetite, 0.2)  # staples always clear: the counter
        # never strands the food base behind a full shelf
        try:  # hungry restart: ovens need volume, not trickle — when bellies
            # growl, the counter buys bread-meals-flour up to real lots and
            # the aid shelf turns them into mouths (soup-kitchen wholesale).
            _hungers = [float(m.get("hunger", 0)) for m in village.npcs
                        if not m.comps.get("dead")]
            _belly = sum(_hungers) / max(1, len(_hungers))
            if _belly > 50 and good in ("bread", "meal", "flour", "fish",
                                         "wheat", "barley", "turnip"):
                appetite = max(appetite, 0.8)
        except Exception:
            pass
    try:
        from omnisim.systems.metrics import village_total  # noqa
        flow = 1.0
        fl = getattr(village, "prod_supply", None) or {}
        from omnisim.core.clock import day_of
        today = day_of(village.clock_pts)
        for g, (d, ema) in fl.items():
            if g == good:
                flow = max(flow, ema * (0.5 ** max(0, today - d)))
    except Exception:
        flow = 1.0
    import math
    cap = max(0, int(math.ceil(appetite * target * max(0.5, flow))))
    if _chain_good(good):
        cap = max(cap, 2)  # staples always clear a little (above)
    # TILL-SCALED appetite (spec: shrink appetite when poor): buying fades
    # as the reserve thins, so the till finds balance instead of a floor.
    # Unscaled buying drained 1500c in 25 days, froze the market at zero,
    # and starved the poorest weeks later. The merchant tightens its belt
    # like everyone else; a 0.3 floor keeps sampling alive at empty till.
    try:
        _st0 = (village.stalls or [{}])[0]
        _frac = float(_st0.get("till", 0)) / max(1.0, float(_st0.get("float", 1500) or 1500))
        cap = max(2 if _chain_good(good) else 0,
                  int(cap * (0.3 + 0.7 * min(1.0, _frac / 0.5))))
    except Exception:
        pass
    # sampling minimum: two units even with no flow signal — no-trade
    # goods can never start trading otherwise (no flow, no appetite,
    # no deals, no flow). The counter tastes everything once.
    return max(cap, 2)


def shelf_buy(stall: dict, item_id: str, trading: int, buyer=None,
              village=None, quality: int = 1) -> int:
    base = item_defs()["items"][item_id]["price"]
    price = (base * quality_mod(quality)
             * origin_mod((stall.get("origin") if isinstance(stall, dict) else None))
             * novelty_mod(stall, item_id)
             * scarcity_of(stall, item_id)
             * demand_mod(village, item_id)
             * relationship_mod(buyer, stall.get("keeper") if isinstance(stall, dict) else None)
             * skill_move(trading, True))
    return max(1, int(round(price)))


def shelf_sell(stall: dict, item_id: str, trading: int, seller=None,
               village=None, quality: int = 1) -> int:
    base = item_defs()["items"][item_id]["price"]
    price = (base * quality_mod(quality)
             * origin_mod((stall.get("origin") if isinstance(stall, dict) else None))
             * novelty_mod(stall, item_id)
             * scarcity_of(stall, item_id)
             * demand_mod(village, item_id)
             * reputation_mod(seller)
             * skill_move(trading, False))
    return max(1, int(round(price)))
