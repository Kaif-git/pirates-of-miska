"""Step 6: unified decision engine (scored options, budgets, switching).

score(option) = (gain - cost - time_cost - risk) / hours, all in mc.
Replaces production.expected_margin's stub body at the choose_recipe seam
and flex_at_dawn's scarcity compare at the switch seam (wiring lands
separately; this module is import-safe with the flag off).

Precedence (A7): keep_of (pocket units) wins on stocking/sell quantities;
budget (purse coin) wins on spend authorization; never sum them.

Travel (A5): travel_mc prices tiles walked in mc via time_value.
Roles (A2): SWITCH_RULES / SWITCH_COSTS are data, not code.
"""
from __future__ import annotations

SWITCH_RULES = {"kid": False, "shopkeeper": False, "Cooking": "hold"}
SWITCH_COSTS = {"grubstake_coin": 20, "grubstake_skill": "Farming"}

_DEFAULTS = {"theta": 0.25, "argue_weeks": 3, "eval_days": 7,
             "per_trade_days": 10, "npc_cooldown_days": 30,
             "grubstake_coin": 20, "markup_bound": 0.3, "markup_rate": 0.02,
             "spend_ema_alpha": 0.1, "fallback_daily_expense": 3.0}


def _econ():
    try:
        from omnisim.systems.production import _load as _ld
        return _ld("economy") or {}
    except Exception:
        return {}


def on():
    try:
        return bool((_econ().get("flags", {}) or {}).get("step_6", False))
    except Exception:
        return False


def _cfg(key):
    try:
        return (_econ().get("decision", {}) or {}).get(key, _DEFAULTS[key])
    except Exception:
        return _DEFAULTS[key]


def _day_of(village):
    try:
        from omnisim.core.clock import day_of as _d
        return int(_d(village.clock_pts))
    except Exception:
        try:
            return int(getattr(village, "clock_pts", 0)) // 144000
        except Exception:
            return 0


def travel_mc(npc, tiles, village=None):
    """Walk cost in mc: tiles x 3 work-ticks x time_value (A5)."""
    try:
        from omnisim.systems.realizedtv import tv_for as _tvm
        return float(tiles) * 3.0 * float(_tvm(npc, village))
    except Exception:
        return float(tiles) * 3000.0


def saleability_projected(village, good):
    """demand / (demand + supply_EMA + intent-board supply), clamp .3-1."""
    if not on():
        try:
            from omnisim.systems.production import saleability as _s
            return float(_s(village, good))
        except Exception:
            return 1.0
    try:
        from omnisim.systems.metrics import unmet_summary
        _dem_all = unmet_summary(village)
        dem = sum(v for k, v in _dem_all.items()
                  if k == "demand:%s" % good) / 2.0
        try:  # R3 shadow demand: unmet cold pain is demand for every
            # good whose provides.insulation covers it (generic: any need
            # channel with pain, any good whose provides/satisfies touch it).
            from omnisim.systems import needs3 as _n3sh
            from omnisim.systems.production import goods as _gsh
            _sh_tot = 0.0
            for _n in (getattr(village, "npcs", []) or []):
                if _n.comps.get("dead"):
                    continue
                try:
                    _worn = (_n.get("worn", {}) or {}) if hasattr(_n, "get") else {}
                    _has = any(float((((_gsh().get(_gg, {}) or {}).get("provides", {})) or {}).get("insulation", 0)) > 0 for _gg in _worn)
                except Exception:
                    _has = False
                if _has:
                    continue
                try:
                    _cold = float(_n3sh.level_of(_n, "cold"))
                except Exception:
                    _cold = 0.0
                if _cold >= 40.0:
                    _sh_tot += 1.0
            if _sh_tot > 0:
                try:
                    _ins = float(((_gsh().get(good, {}) or {}).get("provides", {}) or {}).get("insulation", 0))
                except Exception:
                    _ins = 0.0
                if _ins > 0:
                    dem += _sh_tot * min(2.0, _ins / 6.0)
        except Exception:
            pass
        try:  # chain demand: finished-good want walks to inputs.
            from omnisim.systems.production import recipes as _rrp
            _want = {k.split(":", 1)[1] for k in _dem_all
                      if k.startswith("demand:")}
            _chain = set(_want)
            for _ in range(4):
                for r in _rrp().values():
                    _outs = {o.get("good")
                             for o in (r.get("outputs", []) or [])}
                    if _outs & _chain:
                        for inp in (r.get("inputs", []) or []):
                            if "good" in inp:
                                _chain.add(inp["good"])
            if good in _chain:
                dem = max(dem, sum(v for k, v in _dem_all.items()
                                   if k.startswith("demand:")) / 2.0
                              / max(1, len(_chain)))
        except Exception:
            pass
    except Exception:
        dem = 0.0
    try:
        from omnisim.systems.production import _supply_ema
        sup = float(_supply_ema(village, good))
    except Exception:
        sup = 0.0
    try:  # intended switchers count as future supply (anti-hog)
        from omnisim.systems.production import recipes as _recs
        from omnisim.systems.production import trades as _trs
        board = board_of(village)
        for skill, names in board.items():
            if not names:
                continue
            for t in _trs().values():
                if t.get("skill") != skill:
                    continue
                for rid in t.get("recipes", []) or []:
                    r = _recs().get(rid, {}) or {}
                    for o in r.get("outputs", []) or []:
                        if o.get("good") == good:
                            sup += len(names) * 10.0 * float(o.get("qty", 1))
                            raise StopIteration
    except StopIteration:
        pass
    except Exception:
        pass
    if dem <= 0 and sup <= 0:
        return 1.0
    return max(0.3, min(1.0, dem / max(0.001, dem + sup)))


def cover_falloff(village, good):
    """Smooth glut brake: 1.0 until 2x target, then linear to 0 at 3x."""
    try:
        from omnisim.systems.pricing import cover_of as _cov
        from omnisim.systems.production import goods as _gs
        cover = float(_cov(village, good))
        target = float((_gs().get(good, {}) or {}).get("target_cover", 5))
    except Exception:
        return 1.0
    try:  # R3 zero-supply: no MADE units means no glut. Seeded shelves
        # are endowment, not output: only the production flow counts here.
        from omnisim.systems.production import _supply_ema as _se3
        if float(_se3(village, good)) <= 0:
            return 1.0
    except Exception:
        pass
    if cover <= 2 * target:
        return 1.0
    return max(0.0, 1.0 - (cover - 2 * target) / max(0.001, target))


def score_produce(village, npc, recipe):
    """mc per hour of running this recipe once. -inf when inputs lack."""
    try:
        from omnisim.systems.production import _input_cost
        from omnisim.systems.production import expected_sale_price
        outs = recipe.get("outputs", []) or []
        gain = 0.0
        for o in outs:
            g = o.get("good")
            try:
                base = float(expected_sale_price(village, g))
            except Exception:
                base = 0.0
            if on() and base > 0:
                try:
                    from omnisim.systems.production import saleability as _s0
                    old_s = max(0.3, float(_s0(village, g)))
                    base = base * saleability_projected(village, g) / old_s
                except Exception:
                    pass
            gain += base * float(o.get("qty", 1)) * 1000.0
        cost = 0.0
        for inp in recipe.get("inputs", []) or []:
            gid, unit = _input_cost(village, npc, inp, market_ok=True)
            if gid is None:
                return float("-inf")
            cost += float(unit) * float(inp.get("qty", 1)) * 1000.0
        ticks = max(1, int(recipe.get("active_ticks", 1)))
        try:
            from omnisim.systems.realizedtv import tv_for as _tvm
            tv = float(_tvm(npc, village))
        except Exception:
            tv = 1000.0
        time_cost = ticks * tv
        try:
            from omnisim.systems.mood import _t as _trait
            risk = float(_trait(npc, "risk"))
        except Exception:
            risk = 0.5
        risk_penalty = (1.0 - risk) * 0.1 * max(0.0, gain)
        fall = 1.0
        if on():
            for o in outs:
                try:
                    fall = min(fall, cover_falloff(village, o.get("good")))
                except Exception:
                    pass
        hours = ticks / 6.0
        return ((gain - cost - time_cost - risk_penalty) * fall) / max(0.001, hours)
    except Exception:
        return float("-inf")


def make_vs_buy(village, npc, good):
    """Cheapest own recipe cost vs market ask (copper)."""
    own, ask = float("inf"), float("inf")
    try:
        from omnisim.systems.production import recipes as _recs
        from omnisim.systems.production import _input_cost
        from omnisim.systems.realizedtv import tv_for as _tvm
        tv = float(_tvm(npc, village))
        for r in _recs().values():
            try:
                if not any(o.get("good") == good for o in r.get("outputs", []) or []):
                    continue
                from omnisim.systems.production import runnable
                if not runnable(village, npc, r):
                    continue
                c = 0.0
                ok = True
                for inp in r.get("inputs", []) or []:
                    gid, unit = _input_cost(village, npc, inp)
                    if gid is None:
                        ok = False
                        break
                    c += float(unit) * float(inp.get("qty", 1))
                if not ok:
                    continue
                c += tv / 1000.0 * max(1, int(r.get("active_ticks", 1)))
                own = min(own, c)
            except Exception:
                continue
    except Exception:
        pass
    try:
        from omnisim.systems.pricing import ask_mc as _amc
        from omnisim.systems.pricing import engine_on as _eon
        from omnisim.systems.pricing import fair_mc as _fmc
        from omnisim.systems.pricing import MC as _MC
        if _eon():
            from omnisim.systems.pricing import settle as _stl
            ask = float(_stl(npc, _amc(village, npc, good, 1)))
        else:
            ask = float(_fmc(village, good, 1)) / float(_MC)
    except Exception:
        ask = float("inf")
    choice = "buy"
    if own < ask:
        choice = "make"
    return {"own_cost": own, "ask": ask, "choice": choice}


def note_spend(npc, amount):
    """Fold observed spend into the EMA (alpha from config)."""
    try:
        alpha = float(_cfg("spend_ema_alpha"))
        prev = float((npc.comps.get("spend_ema", None)
                      if hasattr(npc, "comps") else None) or 0.0)
        npc.comps["spend_ema"] = (1 - alpha) * prev + alpha * float(amount)
    except Exception:
        pass


def daily_expenses(npc):
    try:
        e = float(npc.get("spend_ema", 0)) if hasattr(npc, "get") else 0.0
        if e > 0:
            return e
    except Exception:
        pass
    return float(_cfg("fallback_daily_expense"))


def food_reserve(npc, village=None):
    """R4: minimum purse after any non-food purchase. Needs may dip into
    the reserve; wants and recipe inputs may not. Generic: dearest staple
    meal (a real dinner: bread-price floor 3c, never foraged scraps) times
    meal fair price times meals-until-next-income (time for hunger to reach
    100 from now, floored 1 meal, capped 3). A hungry soul keeps dinner
    money, not a fortnight of feasts."""
    try:
        from omnisim.entities.items import staples as _st
        from omnisim.entities.items import defs as _defs
        _all = _st()
    except Exception:
        _all = ["bread"]
    try:
        _hung = max(0.0, float(npc.get("hunger", 0)))
    except Exception:
        _hung = 0.0
    try:
        from omnisim.systems.needs import BASE_HUNGER_PER_MIN as _base
        _rate = abs(float(_base))
    except Exception:
        _rate = 1.0 / 25.0
    _meals = 1.0
    try:
        _mins_left = max(0.0, 100.0 - _hung) / max(0.005, _rate)
        _meals = min(3.0, max(1.0, _mins_left / 720.0))
    except Exception:
        _meals = 1.0
    try:
        _dear = 0.0
        for _s in _all:
            try:
                if village is not None:
                    from omnisim.systems.pricing import engine_on as _eon
                    from omnisim.systems.pricing import fair_mc as _fmc
                    from omnisim.systems.pricing import MC as _MC
                    if _eon():
                        _p = max(1.0, float(_fmc(village, _s, 1)) / float(_MC))
                    else:
                        raise ValueError("engine off")
                else:
                    raise ValueError("no village")
            except Exception:
                try:
                    _p = float((_defs()["items"].get(_s, {}) or {}).get("price", 3))
                except Exception:
                    _p = 3.0
            _p = max(_p, 3.0)
            _dear = max(_dear, _p)
        return max(3.0, _dear) * _meals
    except Exception:
        return 12.0



def can_afford_nonfood(npc, cost, village=None):
    """R4 gate: purse minus cost must keep the food reserve covered."""
    try:
        _coin = float((npc.get("pocket", {}) or {}).get("coin", 0))
        return _coin - float(cost) >= food_reserve(npc, village)
    except Exception:
        return False



def budget_of(village, npc):
    """Purse-level reserves (A7: keeps never enter this math)."""
    try:
        from omnisim.systems.mood import reserve_days as _rd
        from omnisim.systems.mood import spend_propensity as _sp
        coin = float((npc.get("pocket", {}) or {}).get("coin", 0))
        exp = daily_expenses(npc)
        reserve = float(_rd(npc)) * exp
        disc = max(0.0, coin - reserve)
        return {"reserve": reserve, "discretionary": disc,
                "daily_expenses": exp,
                "allow_want": bool(disc > 0) and float(_sp(npc)) > 0}
    except Exception:
        return {"reserve": 0.0, "discretionary": 0.0,
                "daily_expenses": 3.0, "allow_want": False}


def board_of(village):
    try:
        b = getattr(village, "intent_board", None)
        if not isinstance(b, dict):
            b = {}
            village.intent_board = b
        return b
    except Exception:
        return {}


def add_intent(village, npc, skill):
    try:
        b = board_of(village)
        lst = b.get(skill)
        if not isinstance(lst, list):
            lst = []
            b[skill] = lst
        name = getattr(npc, "name", "?")
        if name not in lst:
            lst.append(name)
    except Exception:
        pass


def clear_board(village):
    try:
        village.intent_board = {}
    except Exception:
        pass


def _current_income(village, npc):
    try:
        e = npc.get("income_ema", None)
        if e is not None:
            return max(0.5, float(e))
    except Exception:
        pass
    return 1.0


def _best_recipe_margin(village, npc, skill):
    """Projected mc/day for a skill: best runnable recipe score x 10 ticks."""
    try:
        from omnisim.systems.production import recipes as _recs
        from omnisim.systems.production import runnable
        best = None
        for r in _recs().values():
            try:
                if str(r.get("skill", "")) != skill:
                    continue
                s = score_produce(village, npc, r)
                if s == float("-inf"):
                    continue
                if best is None or s > best:
                    best = s
            except Exception:
                continue
        if best is None:
            return 0.0
        return max(0.0, best) * 10.0 / 6.0
    except Exception:
        return 0.0


def switch_bid(village, npc):
    """Weekly trade-switch bid with all six anti-hog gates. Returns None
    (stay) or a bid dict {target_skill, projected_margin, current_income,
    reason} / {hold: True, reason} for sole-supplier holds."""
    try:
        day = _day_of(village)
        role = str(npc.get("role", "") or "")
        if SWITCH_RULES.get(role) is False:
            return None
        cur_skill = str(npc.get("work_skill", "") or "")
        if cur_skill == "Cooking" and SWITCH_RULES.get("Cooking") == "hold":
            pass  # baker evaluated like others but can never target away
        eval_days = int(_cfg("eval_days"))
        last_eval = int(npc.comps.get("last_switch_eval", -999))
        if day - last_eval < eval_days:
            return None
        npc.comps["last_switch_eval"] = day
        cooldown = int(_cfg("npc_cooldown_days"))
        last_switch = int(npc.comps.get("switch_day", -999))
        if day - last_switch < cooldown:
            return None
        cur_income = _current_income(village, npc)
        try:
            from omnisim.systems.mood import switch_mult as _sm
            theta = float(_cfg("theta")) * float(_sm(npc))
        except Exception:
            theta = float(_cfg("theta"))
        cands = []
        try:
            from omnisim.systems.production import trades as _trs
            for t in _trs().values():
                skill = str(t.get("skill", "") or "")
                if not skill or skill == cur_skill:
                    continue
                if cur_skill == "Cooking":
                    continue  # the baker never leaves (A2)
                if role and SWITCH_RULES.get(role) is False:
                    continue
                if skill == SWITCH_COSTS.get("grubstake_skill"):
                    try:
                        coin = float((npc.get("pocket", {}) or {}).get("coin", 0))
                    except Exception:
                        coin = 0.0
                    if coin < float(_cfg("grubstake_coin")):
                        continue
                try:
                    switched = getattr(village, "trade_switched", None) or {}
                    if day - int(switched.get(skill, -999)) < int(_cfg("per_trade_days")):
                        continue
                except Exception:
                    pass
                proj = _best_recipe_margin(village, npc, skill)
                cands.append((skill, proj))
        except Exception:
            pass
        if not cands:
            return None
        try:  # unstaffed-skill priority: an empty wanted trade outranks
            from omnisim.systems.production import trades as _trs2
            _live_skills = {str(n.get("work_skill", "") or "")
                            for n in (getattr(village, "npcs", []) or [])
                            if not n.comps.get("dead")}
            _unstaffed = [t for t in cands
                          if t[0] not in _live_skills
                          and t[1] > cur_income]
            if _unstaffed:
                _unstaffed.sort(key=lambda t: t[1], reverse=True)
                cands = _unstaffed + [t for t in cands if t not in _unstaffed]
        except Exception:
            pass
        # best margin wins: an unstaffed leader keeps its priority seat;
        # otherwise the whole list sorts by projected margin (dict order
        # is production.yaml order, not a ranking: Milling-first must not
        # beat Fishing-89k).
        try:
            _live3 = {str(n.get("work_skill", "") or "")
                      for n in (getattr(village, "npcs", []) or [])
                      if not n.comps.get("dead")}
            _un0 = (cands[0][0] not in _live3) if cands else False
        except Exception:
            _un0 = False
        if _un0:
            _first = cands[0]
            _rest = sorted(cands[1:], key=lambda t: t[1], reverse=True)
            cands = [_first] + _rest
        else:
            cands = sorted(cands, key=lambda t: t[1], reverse=True)
        target, proj = cands[0]
        try:  # sole-supplier hold (A6 never-drop-below-viable)
            live = [n for n in (getattr(village, "npcs", []) or [])
                    if not n.comps.get("dead")
                    and str(n.get("work_skill", "")) == cur_skill]
            if len(live) <= 1 and cur_skill:
                try:
                    from omnisim.systems.pricing import cover_of as _cov2
                    from omnisim.systems.production import goods as _gs2
                    from omnisim.systems.production import recipes as _rc2
                    from omnisim.systems.production import trade_of as _to2
                    t0 = _to2(npc) or {}
                    thin = False
                    for rid in t0.get("recipes", []) or []:
                        for o in (_rc2().get(rid, {}) or {}).get("outputs", []) or []:
                            g = o.get("good")
                            if not g:
                                continue
                            tgt = float((_gs2().get(g, {}) or {}).get("target_cover", 5))
                            if float(_cov2(village, g)) < tgt:
                                thin = True
                    if thin:
                        return {"hold": True, "target_skill": cur_skill,
                                "projected_margin": proj,
                                "current_income": cur_income,
                                "reason": "hold: sole supplier"}
                except Exception:
                    pass
        except Exception:
            pass
        if proj <= cur_income * (1.0 + theta):
            try:
                npc.comps["switch_argue"] = {}
            except Exception:
                pass
            return None
        try:  # N consecutive weekly wins required (argue, Step-6 style)
            argue = npc.comps.get("switch_argue")
            if not isinstance(argue, dict):
                argue = {}
                npc.comps["switch_argue"] = argue
            argue[target] = int(argue.get(target, 0)) + 1
            if argue[target] < int(_cfg("argue_weeks")):
                add_intent(village, npc, target)
                return None
        except Exception:
            return None
        add_intent(village, npc, target)
        return {"target_skill": target, "projected_margin": proj,
                "current_income": cur_income, "reason": "margin"}
    except Exception:
        return None


def _markdown_on() -> bool:
    try:
        return bool((_econ().get("flags", {}) or {}).get("refusal_markdown", False))
    except Exception:
        return False


def _markdown_store(npc) -> dict:
    try:
        m = npc.comps.get("refusal_mark")
        if not isinstance(m, dict):
            m = {}
            npc.comps["refusal_mark"] = m
        return m
    except Exception:
        return {}


def _refusal_day_store(npc) -> dict:
    try:
        d = npc.comps.get("refusal_day")
        if not isinstance(d, dict):
            d = {}
            npc.comps["refusal_day"] = d
        return d
    except Exception:
        return {}


def _today(village) -> int:
    try:
        from omnisim.core.clock import day_of as _d
        return int(_d(village.clock_pts))
    except Exception:
        return 0


def note_refusal(village, npc, good: str) -> None:
    """Part B: one refusal lowers this seller's per-good multiplier 5%,
    floored at 0.90. Flag-gated (off => no-op)."""
    if not _markdown_on():
        return
    try:
        m = _markdown_store(npc)
        m[str(good)] = max(0.90, float(m.get(str(good), 1.0)) * 0.95)
        try:
            _refusal_day_store(npc)[str(good)] = _today(village)
        except Exception:
            pass
    except Exception:
        pass


def recover_markdown(village, npc, good: str) -> None:
    """Part B: +2% recovery toward 1.0 (one call = one refusal-free day)."""
    if not _markdown_on():
        return
    try:
        m = _markdown_store(npc)
        cur = float(m.get(str(good), 1.0))
        if cur < 1.0:
            m[str(good)] = min(1.0, cur * 1.02)
    except Exception:
        pass


def recover_village(village) -> None:
    """Part B dawn hook: every seller's marks recover +2% unless refused
    today. Flag-gated inside recover_markdown."""
    try:
        today = _today(village)
    except Exception:
        today = 0
    try:
        for npc in (getattr(village, "npcs", []) or []):
            try:
                if npc.comps.get("dead"):
                    continue
                marks = npc.comps.get("refusal_mark")
                if not isinstance(marks, dict) or not marks:
                    continue
                days = npc.comps.get("refusal_day")
                if not isinstance(days, dict):
                    days = {}
                for good in list(marks.keys()):
                    try:
                        if int(days.get(good, -1)) >= today:
                            continue
                    except Exception:
                        pass
                    recover_markdown(village, npc, good)
                    try:
                        if float(marks.get(good, 1.0)) >= 1.0:
                            del marks[good]
                            if good in days:
                                del days[good]
                    except Exception:
                        pass
            except Exception:
                continue
    except Exception:
        pass


def reservation_mult(npc, good=None):
    """REMOVED Part B: dead path (zero readers in committed tree).
    Kept as a shim returning 1.0 so legacy tests stay green until the
    owning test file is updated; ask path no longer reads ask_markup."""
    return 1.0


def markdown_mult(village, npc, good: str) -> float:
    """Part B reader: 1.0 when the flag is off (byte-identical asks)."""
    if not _markdown_on():
        return 1.0
    try:
        return max(0.90, min(1.0, float(_markdown_store(npc).get(str(good), 1.0))))
    except Exception:
        return 1.0


def reservation_update(npc, good, outcome):
    """Trait-weighted ask-markup learning, bounded. Outcomes: sold (decay
    to 0), failed (down), instant (up). The failure path is SUPERSEDED by
    Part B refusal markdown (flag refusal_markdown, comps refusal_mark):
    haggle feeds note_refusal directly; this keeps legacy trait math."""
    try:
        bound = float(_cfg("markup_bound"))
        rate = float(_cfg("markup_rate"))
        try:
            from omnisim.systems.mood import _t as _trait
            amb = float(_trait(npc, "ambition"))
        except Exception:
            amb = 0.5
        m = npc.comps.get("ask_markup")
        if not isinstance(m, dict):
            m = {}
            npc.comps["ask_markup"] = m
        cur = float(m.get(str(good), 0.0))
        if outcome == "failed":
            cur -= rate * (0.5 + amb)
        elif outcome == "instant":
            cur += rate * (0.5 + amb)
        else:
            cur -= 0.01 * (1 if cur > 0 else -1 if cur < 0 else 0)
        m[str(good)] = max(-bound, min(bound, cur))
    except Exception:
        pass
