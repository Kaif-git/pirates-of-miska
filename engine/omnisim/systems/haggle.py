"""UAILS part 1 — emergent prices (behind the uails flag, default off).

No price tags: every soul remembers an ASK (selling) and a WILL (buying)
per good, in copper per unit. Deals haggle — ask <= will meets in the
middle; refusals teach both sides (sellers down toward cost, buyers up
toward need). Seeds come from cost memory (inputs at remembered prices
plus a copper for the hands and one for mastery), never from yaml.
Competition rides the village tape: the last deal price anchors the next
haggle, so sole sellers probe upward and rivals undercut. A dropped magic
book opens on a guess and learns its price within days.
"""
from __future__ import annotations


def enabled() -> bool:
    """The flag lives in data (drives.yaml: uails) — off till proven."""
    try:
        from omnisim.systems.valuation import drives as _drives
        return bool(_drives().get("uails", False))
    except Exception:
        return False


def _mem(entity, key: str) -> dict:  # type: ignore[no-untyped-def]
    try:
        mem = entity.get(key, None)
    except Exception:
        return {}
    if not isinstance(mem, dict):
        mem = {}
        try:
            entity.comps[key] = mem
        except Exception:
            pass
    return mem


LOT_CAP = 20  # lots remembered per good: old seasons fall off the page


def note_lot(entity, item_id: str, qty: int, price: int) -> None:  # type: ignore[no-untyped-def]
    """FIFO lot in: qty units acquired at price copper each (a purchase,
    a harvest's seed cost, a batch's made cost). Makers and merchants
    both keep books; floors read the oldest unfinished lot first."""
    try:
        qty, price = int(qty), max(1, int(price))
        if qty <= 0:
            return
        lots = _mem(entity, "lots")
        book = lots.setdefault(item_id, [])
        if not isinstance(book, list):
            book = lots[item_id] = []
        book.append([qty, price])
        del book[:-LOT_CAP]
    except Exception:
        pass


def lot_cost(entity, item_id: str):  # type: ignore[no-untyped-def]
    """Oldest unfinished lot's unit cost (None = no book — guess instead)."""
    try:
        book = _mem(entity, "lots").get(item_id) or []
        if book:
            return max(1, int(book[0][1]))
    except Exception:
        pass
    return None


def pop_lots(entity, item_id: str, qty: int) -> tuple:  # type: ignore[no-untyped-def]
    """FIFO lots out: consume qty oldest-first. Returns (units covered,
    total copper cost). Uncovered units cost nothing here (the old guess
    prices them downstream)."""
    covered, cost = 0, 0
    try:
        qty = int(qty)
        lots = _mem(entity, "lots")
        book = lots.get(item_id) or []
        while qty > 0 and book:
            q, p = int(book[0][0]), int(book[0][1])
            take = min(q, qty)
            covered += take
            cost += take * p
            q -= take
            qty -= take
            if q <= 0:
                book.pop(0)
            else:
                book[0][0] = q
    except Exception:
        pass
    return covered, cost


def _recipe_inputs(item_id: str):  # type: ignore[no-untyped-def]
    """Step-1 recipe rows behind one unit: [(candidate goods, qty)]. Tags
    resolve to member goods; gather rows yield no inputs (hands only)."""
    try:
        from omnisim.systems.production import recipes as _recs
        from omnisim.systems.production import goods_with_tag as _members
        for r in _recs().values():
            if any(o.get("good") == item_id for o in r.get("outputs", []) or []):
                out = []
                for inp in r.get("inputs", []) or []:
                    if "good" in inp:
                        out.append(([inp["good"]], inp.get("qty", 1)))
                    elif "tag" in inp:
                        out.append((_members(inp["tag"]), inp.get("qty", 1)))
                return out
    except Exception:
        pass
    return None


def _guess_need(maker: str):  # type: ignore[no-untyped-def]
    """Inputs behind one batch, for cost guesses: WORK_NEEDS where the
    tick consumes them, plus the episodic batches (kiln clay, brew herbs,
    saw billets) that never pass through the need block. Guesses that
    can't see inputs price pots at wishes."""
    try:
        from omnisim.systems.schedule import WORK_NEEDS as _WN
        need = (_WN or {}).get(maker)
        if need:
            return need
    except Exception:
        pass
    return {"Pottery": ("clay", 2), "Herbalism": ("herbs", 2),
            "Carpentry": ("firewood", 3)}.get(maker)


def _maker_skill(item_id: str) -> str:
    """Whose hands make it (from labor rates, else the made_by map for
    counter-banked finished goods and forged iron) — costs follow the trade."""
    try:
        from omnisim.systems.schedule import WORK_GOODS as _WG
        for skill, goods in (_WG or {}).items():
            if goods and goods[0] == item_id:
                return str(skill)
    except Exception:
        pass
    try:
        from omnisim.systems.tools import FORGE_ORDER as _FO
        if item_id in (_FO or ()):
            return "Smithing"
    except Exception:
        pass
    try:
        import yaml as _yaml
        from pathlib import Path as _P
        p = _P(__file__).resolve().parents[3] / "data" / "labor.yaml"
        doc = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        made = (doc.get("made_by", {}) or {}).get(item_id)
        if made:
            return str(made)
    except Exception:
        pass
    return ""


def cost_of(entity, item_id: str, _seen=None) -> int:  # type: ignore[no-untyped-def]
    """What one unit cost this soul. FIFO lots first: the oldest unfinished
    lot's unit cost IS the floor (the flour this loaf ate, not a blended
    memory). No book? The old guess: maker's inputs at remembered prices,
    a copper for the hands, one more per two skill levels. Found things
    cost the walk (1c) — the guess every fortune starts from."""
    try:
        lot = lot_cost(entity, item_id)
        if lot is not None:
            return max(1, lot)
    except Exception:
        pass
    try:
        from omnisim.systems.schedule import WORK_NEEDS as _WN
        from omnisim.systems.skills import skill_level
    except Exception:
        return 2
    _seen = _seen or set()
    if item_id in _seen:
        return 1
    _seen.add(item_id)
    maker = _maker_skill(item_id)
    try:  # STEP 2: recipe rows first (gather goods cost hands only — the
        # old maker-map charged dug clay its own kiln input, twice over)
        _rins = _recipe_inputs(item_id)
    except Exception:
        _rins = None
    total = 0
    if _rins is not None:
        for _cands, _q in _rins:
            _best = None
            for _g in _cands:
                try:
                    _c = cost_of(entity, _g, _seen)
                except Exception:
                    continue
                _best = _c if _best is None else min(_best, _c)
            if _best is None:
                _best = 1
            total += int(_q) * _best
    else:
        need = _guess_need(maker)
        if need:
            try:
                input_id, qty = need
                total += int(qty) * will_of(entity, input_id, _seen)
            except Exception:
                pass
    try:
        total += 1 + int(skill_level(entity, maker)) // 2
    except Exception:
        total += 1
    return max(1, total)


def fresh_cost(entity, item_id: str, _seen=None) -> int:  # type: ignore[no-untyped-def]
    """A buyer's honest imagination: the maker's inputs at remembered
    prices plus hands — journeyman hands for finished goods, a full
    extra copper for wild goods with no inputs (ore is broken, not
    found: the walk plus the working). No mastery (strangers don't
    pay for a name they haven't tasted)."""
    try:
        from omnisim.systems.schedule import WORK_NEEDS as _WN
    except Exception:
        return 1
    _seen = _seen or set()
    if item_id in _seen:
        return 1
    _seen.add(item_id)
    total = 0
    try:
        _rins = _recipe_inputs(item_id)
    except Exception:
        _rins = None
    if _rins is not None:
        for _cands, _q in _rins:
            _best = None
            for _g in _cands:
                try:
                    _c = cost_of(entity, _g, _seen)
                except Exception:
                    continue
                _best = _c if _best is None else min(_best, _c)
            if _best is None:
                _best = 1
            total += int(_q) * _best
        return max(1, total + 1)
    try:
        need = _guess_need(_maker_skill(item_id))
    except Exception:
        need = None
    if need:
        try:
            input_id, qty = need
            total += int(qty) * will_of(entity, input_id, _seen)
        except Exception:
            pass
        return max(1, total + 1)
    if _maker_skill(item_id):
        return max(2, total + 2)  # worked wild goods: sweat has a price
    return max(1, total + 1)


def will_of(entity, item_id: str, _seen=None) -> int:  # type: ignore[no-untyped-def]
    """What this soul will pay: remembered, else its journeyman's guess
    at the maker's cost, else a copper of curiosity."""
    mem = _mem(entity, "wills")
    if item_id in mem:
        try:
            return max(1, int(mem[item_id]))
        except Exception:
            pass
    guess = 1
    try:
        guess = fresh_cost(entity, item_id, _seen)
    except Exception:
        pass
    mem[item_id] = guess
    return guess


def ask_of(entity, item_id: str, _seen=None) -> int:  # type: ignore[no-untyped-def]
    """What this soul asks: remembered, else cost plus margin (cost covers
    the hands; the extra copper is hope)."""
    mem = _mem(entity, "asks")
    if item_id in mem:
        try:
            return max(1, int(mem[item_id]))
        except Exception:
            pass
    try:
        ask = cost_of(entity, item_id, _seen) + 1
    except Exception:
        ask = 3
    mem[item_id] = ask
    return ask


def last_price(village, item_id: str):  # type: ignore[no-untyped-def]
    """The village tape: what one unit last fetched here (None if never)."""
    try:
        tape = getattr(village, "price_mem", None)
        if not isinstance(tape, dict):
            return None
        v = tape.get(item_id)
        return int(v) if v is not None else None
    except Exception:
        return None


def _note_price(village, item_id: str, price: int) -> None:  # type: ignore[no-untyped-def]
    try:
        tape = getattr(village, "price_mem", None)
        if not isinstance(tape, dict):
            tape = {}
            village.price_mem = tape
        tape[item_id] = max(1, int(price))
    except Exception:
        pass


def merchant_of(village):  # type: ignore[no-untyped-def]
    """Bram: invincible infrastructure with a memory that never dies."""
    try:
        for n in village.npcs:
            if n.comps.get("invincible") and not n.comps.get("dead"):
                return n
    except Exception:
        pass
    return None


def deal_price(village, seller, buyer, item_id: str, fallback: int = 1) -> tuple:  # type: ignore[no-untyped-def]
    """Haggle one unit: (price, dealt). Flag off → fallback, no memory.
    Flag on → a counter visit haggles: sellers bleed one copper a round
    toward cost; buyers stretch by need (a full belly doesn't budge, and
    doesn't linger — rounds scale with hunger, so sated feet walk past
    dear goods while starving ones close). The tape anchors every deal."""
    try:
        fallback = max(1, int(fallback))
    except Exception:
        fallback = 1
    if not enabled():
        return fallback, True
    try:  # STEP 2: asks/bids from the pricing engine (mc seeds, copper rounds)
        from omnisim.systems.pricing import engine_on as _peng
        if _peng():
            import math as _math
            from omnisim.systems.pricing import (ask_mc as _askmc, bid_mc as _bidmc,
                                                 cost_floor_mc as _floor_mc,
                                                 settle as _settle)
            try:
                from omnisim.entities.items import lore_q as _lq
                _qq = max(1, int(_lq(seller, item_id)))
            except Exception:
                _qq = 1
            ask = _settle(seller, _askmc(village, seller, item_id, _qq))
            will = _settle(buyer, _bidmc(village, buyer, item_id, _qq))
            try:
                from omnisim.systems.pricing import _is_merchant as _ismerch
                _mtm = bool(_ismerch(seller))
            except Exception:
                _mtm = False
            floor = max(1, int(_math.ceil(
                _floor_mc(village, seller, item_id, _mtm) / 1000.0)))
            step = _need_step(buyer, item_id, village)
            for _ in range(2 + step):
                if ask <= will:
                    break
                ask = max(floor, ask - 1)
                # STEP 2: stretch concedes up TO the ask, never past it —
                # overshoot used to split the difference above every ask.
                will = min(will + step, max(ask, will), 50)
            if ask <= will:
                price = max(floor, (ask + will) // 2)
                try:  # STEP 2 circuit breaker: no deal prints more than the
                    # daily cap above fair (intraday hunger convexity spiked
                    # bread 3c to 8c in an afternoon). Costs above the wire
                    # always pass — the wire binds margin spikes, not flour
                    # that honestly cost more than yesterday's fair.
                    from omnisim.systems.pricing import (engine_params as _pparams,
                                                         fair_mc as _fairmc2,
                                                         MC as _MC2)
                    _wire = max(floor, int(_math.ceil(
                        _fairmc2(village, item_id, _qq) *
                        (1 + float(_pparams().get("tape_daily_cap", 0.15))) / _MC2)))
                    if price > _wire:
                        price = _wire
                except Exception:
                    pass
                _mem(seller, "asks")[item_id] = price
                _mem(buyer, "wills")[item_id] = price
                _mem(buyer, "paid")[item_id] = price
                _note_price(village, item_id, price)
                try:  # Part A: feed every settled deal into the realized log.
                    from omnisim.systems.pricing import note_realized_sale as _nrs
                    _nrs(village, item_id, price, _qq)
                except Exception:
                    pass
                try:  # STEP 5: log the settled deal into soul memory
                    from omnisim.systems.memory import on as _m5_on
                    from omnisim.systems.memory import remember_deal as _m5_rd
                    if _m5_on():
                        try:
                            from omnisim.systems.pricing import MC as _m5_MC
                            from omnisim.systems.pricing import fair_mc as _m5_fair
                            _m5_f = max(price, int(round(
                                _m5_fair(village, item_id, _qq) / _m5_MC)))
                        except Exception:
                            _m5_f = price
                        _m5_rd(village, buyer, seller, item_id, _qq,
                               price, _m5_f, observed_q=_qq)
                except Exception:
                    pass
                try:  # STEP 6: seller's ask learning (instant = no concession)
                    from omnisim.systems.decision import on as _d6_on
                    from omnisim.systems.decision import reservation_update as _d6_ru
                    if _d6_on():
                        _d6_ru(seller, item_id,
                               "instant" if price >= ask else "sold")
                except Exception:
                    pass
                return price, True
            _mem(seller, "asks")[item_id] = ask
            _mem(buyer, "wills")[item_id] = will
            try:  # STEP 6: refusal teaches sellers down
                from omnisim.systems.decision import on as _d6_onf
                from omnisim.systems.decision import reservation_update as _d6_ruf
                if _d6_onf():
                    _d6_ruf(seller, item_id, "failed")
            except Exception:
                pass
            try:  # Part B: refusal markdown (own flag, needs a village handle).
                from omnisim.systems.decision import note_refusal as _dB_nr
                _dB_nr(village, seller, item_id)
            except Exception:
                pass
            return ask, False
    except Exception:
        pass
    try:
        ask = ask_of(seller, item_id)
        will = will_of(buyer, item_id)
        try:
            floor = cost_of(seller, item_id)
        except Exception:
            floor = 1
        step = _need_step(buyer, item_id, village)
        for _ in range(2 + step):
            if ask <= will:
                break
            ask = max(floor, ask - 1)
            will = min(will + step, 50)  # fortunes cap here
        if ask <= will:
            price = max(floor, (ask + will) // 2)  # the middle, never below cost
            _mem(seller, "asks")[item_id] = price
            _mem(buyer, "wills")[item_id] = price
            _mem(buyer, "paid")[item_id] = price
            _note_price(village, item_id, price)
            try:  # STEP 5: legacy-path deal memory (fair unknown → fair=price)
                from omnisim.systems.memory import on as _m5_on2
                from omnisim.systems.memory import remember_deal as _m5_rd2
                if _m5_on2():
                    try:
                        from omnisim.entities.items import lore_q as _m5_lq2
                        _m5_q2 = max(1, int(_m5_lq2(seller, item_id)))
                    except Exception:
                        _m5_q2 = 1
                    _m5_rd2(village, buyer, seller, item_id, _m5_q2,
                            price, price, observed_q=_m5_q2)
            except Exception:
                pass
            try:  # STEP 6: seller's ask learning (legacy path: sold)
                from omnisim.systems.decision import on as _d6_on2
                from omnisim.systems.decision import reservation_update as _d6_ru2
                if _d6_on2():
                    _d6_ru2(seller, item_id, "sold")
            except Exception:
                pass
            return price, True
        _mem(seller, "asks")[item_id] = ask
        _mem(buyer, "wills")[item_id] = will
        try:  # STEP 6: refusal teaches sellers down
            from omnisim.systems.decision import on as _d6_onf2
            from omnisim.systems.decision import reservation_update as _d6_ruf2
            if _d6_onf2():
                _d6_ruf2(seller, item_id, "failed")
        except Exception:
            pass
        return ask, False
    except Exception:
        return fallback, True


def urgency_of(buyer, item_id: str, village=None, _hunger=None) -> float:
    """Pain relieved per copper, max over channels. Cold, thirst and
    rest count the way hunger does: freezing feet close on garments,
    not only hungry ones on food. _hunger pins the value the tick
    read at plan time (gauges ebb between planning and dealing)."""
    try:
        if _hunger is not None:
            try:
                from omnisim.systems.needs3 import relief_units as _u_rel2
                from omnisim.systems.needs3 import kappa_mc as _u_kap2
                _rel2 = float(_u_rel2(buyer, item_id, village, _hunger_override=float(_hunger)))
                if _rel2 > 0:
                    try:
                        from omnisim.systems.pricing import fair_mc as _u_fair2
                        _pr2 = max(1.0, float(_u_fair2(village, item_id, 1)) / 1000.0)
                    except Exception:
                        _pr2 = 1.0
                    try:
                        _k2 = float(_u_kap2(village, buyer))
                    except Exception:
                        _k2 = 130.0
                    return max(0.0, _rel2 * _k2 / 1000.0 / _pr2)
            except TypeError:
                pass
            except Exception:
                pass
    except Exception:
        pass
    try:
        from omnisim.systems.needs3 import on as _u_on
        from omnisim.systems.needs3 import relief_units as _u_rel
        from omnisim.systems.needs3 import kappa_mc as _u_kap
        if _u_on():
            _rel = float(_u_rel(buyer, item_id, village))
            if _rel > 0:
                try:
                    from omnisim.systems.pricing import fair_mc as _u_fair
                    _pr = max(1.0, float(_u_fair(village, item_id, 1)) / 1000.0)
                except Exception:
                    _pr = 1.0
                try:
                    _k = float(_u_kap(village, buyer))
                except Exception:
                    _k = 130.0
                return max(0.0, _rel * _k / 1000.0 / _pr)
    except Exception:
        pass
    try:
        from omnisim.entities.items import defs as _u_defs
        if float((_u_defs()["items"].get(item_id, {}) or {}).get("food", 0)) > 0:
            from omnisim.systems.valuation import urgency as _u_urg
            return float(_u_urg(float(buyer.get("hunger", 0)), "hunger"))
    except Exception:
        pass
    return 0.0


def _need_step(buyer, item_id: str, village=None, _hunger=None) -> int:  # type: ignore[no-untyped-def]
    try:
        _u = urgency_of(buyer, item_id, village if "village" in dir() else None, _hunger)
        if _u >= 20.0:
            return 3
        if _u >= 5.0:
            return 2
    except Exception:
        pass
    """Copper per round this buyer stretches: hunger prices food (sated
    feet don't budge); tools and raw want a steady copper of interest."""
    try:
        from omnisim.entities.items import defs as _defs
        if float((_defs()["items"].get(item_id, {}) or {}).get("food", 0)) > 0:
            from omnisim.systems.valuation import urgency as _urg
            hunger = float(buyer.get("hunger", 0))
            step = max(0, min(3, int(float(_urg(hunger, "hunger")) * 3)))
            if hunger >= 50:
                step = max(1, step)  # hungry hands find one copper; sated
            return step  # feet still walk past gouging
    except Exception:
        pass
    return 1


def merchant_ask(village, merchant, item_id: str, stall=None) -> int:  # type: ignore[no-untyped-def]
    """What the counter asks: what it last paid, plus margin that swells
    with bare shelves (replacement costs, honestly). No tape yet? Cost
    guess. The haggle still has to close —     margin is posted, not taken. Step 2: fair bands when the engine is on."""
    try:  # STEP 2: the counter quotes fair +/- margin (till-aware width)
        from omnisim.systems.pricing import (engine_on as _peng2,
                                             merchant_quote_mc as _band,
                                             settle as _settle2)
        if _peng2():
            ask = _settle2(merchant, _band(village, stall, item_id, "sell"))
            try:
                _mem(merchant, "asks")[item_id] = ask
            except Exception:
                pass
            return max(1, ask)
    except Exception:
        pass
    try:
        ask = ask_of(merchant, item_id)
    except Exception:
        ask = 3
    try:
        paid = _mem(merchant, "paid").get(item_id)
        base = int(paid) if paid is not None else ask
    except Exception:
        base = ask
    margin = 1
    try:
        if stall is not None:
            from omnisim.systems.pricing import scarcity_of as _scar
            _stock = (stall.get("stock", {}) or {}).get(item_id, [0, 0])[0]
            _par = (stall.get("restock", {}) or {}).get(item_id, [6, 0])[0]
            if _stock < _par // 2:  # true dearth prices scarcity; plenty
                margin = max(1, int(float(_scar(stall, item_id))))  # costs plus one
    except Exception:
        pass
    ask = max(ask, base + margin)
    try:
        _mem(merchant, "asks")[item_id] = ask
    except Exception:
        pass
    return max(1, ask)


def probe_up(village, seller, item_id: str, stall=None) -> None:  # type: ignore[no-untyped-def]
    """Sole sellers test the wind: bare shelves nudge asks up toward the
    last deal (never past it +1 — gouging is probed, not posted)."""
    if not enabled():
        return
    try:
        stock = (stall.get("stock", {}) or {}).get(item_id, [0, 0])[0] \
            if stall else 0
        par = (stall.get("restock", {}) or {}).get(item_id, [6, 0])[0] \
            if stall else 6
        if stock >= par // 2:
            return  # full shelves keep sellers honest
        last = last_price(village, item_id)
        mem = _mem(seller, "asks")
        ask = int(mem.get(item_id, ask_of(seller, item_id)))
        ceiling = (last + 1) if last else (ask + 1)
        if ask < ceiling:
            mem[item_id] = ask + 1
    except Exception:
        pass


def expected_price(village, entity, item_id: str, fallback: int = 1) -> int:  # type: ignore[no-untyped-def]
    """What feet expect before walking: the tape, else their own will."""
    if not enabled():
        return fallback
    try:
        last = last_price(village, item_id)
        if last:
            return last
        return will_of(entity, item_id)
    except Exception:
        return fallback
