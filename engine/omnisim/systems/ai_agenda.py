"""Living-NPC brains — V1 Foundations Utility AI (GDD 8.1).

Score = Sum(Need/Want Fulfillment x Weight x Priority) - Cost.
Schedules remain as fallback anchors; this scores the 5 verbs
(sleep/eat/work/buy/idle) so critical needs override routine blocks.
"""
from __future__ import annotations
from omnisim.entities.entity import Entity

VERBS = ("sleep", "eat", "work", "buy", "idle")


def _num(entity, key: str, default: float = 0.0) -> float:
    try:
        return float(entity.get(key, default))
    except Exception:
        return default


def _traits(entity) -> set[str]:
    try:
        raw = entity.get("traits", {})
    except Exception:
        return set()
    if isinstance(raw, dict):
        return {str(k).upper() for k, v in raw.items() if v}
    if isinstance(raw, (list, tuple, set)):
        return {str(t).upper() for t in raw}
    return set()


def score_verb(entity: Entity, verb: str, world=None) -> float:
    """Smooth utility per verb — urgency curves, never need thresholds.
    Personality weights circumstance (GDD 8.1); valuation prices options."""
    traits = _traits(entity)
    w_wealth = 1.0 + (0.6 if "GREEDY" in traits or "AMBITIOUS" in traits else 0.0)
    try:  # V1 Wants: the lonely seek company; the content drift
        _soc = max(0.0, min(100.0, float(entity.get("social", 70.0))))
    except Exception:
        _soc = 70.0
    w_social = 0.6 + (100.0 - _soc) / 100.0
    pocket = entity.get("pocket", {}) if hasattr(entity, "get") else {}
    coins = float(pocket.get("coin", 0)) if isinstance(pocket, dict) else 0.0
    if verb == "eat":
        try:  # real bites in hand, real hunger curve — no food-name lists
            from omnisim.systems.valuation import bite_option
            _item, value = bite_option(entity)
            return value
        except Exception:
            return 0.0
    if verb == "sleep":
        try:
            from omnisim.systems.valuation import rest_utility
            return rest_utility(entity) - 5.0
        except Exception:
            return 0.0
    if verb == "work":
        # work pays when pockets are light and body can stand it
        hunger, fatigue = _num(entity, "hunger", 0), _num(entity, "fatigue", 0)
        need_money = max(0.0, 1.0 - coins / 20.0)
        body_ok = 1.0 - min(1.0, max(hunger, fatigue) / 150.0)
        return need_money * 40 * w_wealth * max(0.2, body_ok) - 5.0
    if verb == "buy":
        try:  # the market's real bid, not a hunger cliff and a coin cliff
            from omnisim.systems.valuation import buy_option
            _s, _i, value = buy_option(world, entity)
            return value
        except Exception:
            return 0.0
    # idle: social/rest fallback, always viable but rarely best
    return 10.0 * w_social - 2.0


def decide(entity: Entity, world=None) -> str:  # type: ignore[no-untyped-def]
    """Highest-scoring verb; falls back to agenda goals only on surprise."""
    try:
        return max(VERBS, key=lambda v: score_verb(entity, v, world))
    except Exception:
        pass
    agenda = entity.get("agenda")
    goals = agenda.goals if agenda else ["survive"]
    return goals[0]


# -- Day planning (P2): full days from needs, not YAML -----------------------
# plan_day() builds the same {start, end, act, at} block shape the walker
# already follows — but times and places come from live state: hunger
# forecast, fatigue, coin, traits, sociability, and a deterministic jitter
# so no two days (or villagers) read alike. YAML stays as fallback.

def _ptraits(entity) -> set[str]:
    try:
        raw = entity.get("traits", {})
    except Exception:
        return set()
    if isinstance(raw, dict):
        return {str(k).upper() for k, v in raw.items() if v}
    if isinstance(raw, (list, tuple, set)):
        return {str(t).upper() for t in raw}
    return set()


def _pnum(entity, key: str, default: float = 0.0) -> float:
    try:
        return float(entity.get(key, default))
    except Exception:
        return default


def _day_verb(entity, shift: str) -> str:
    """What this soul does with a work shift. Role sets the trade;
    guards walk, kids wander, day-laborers idle till afternoon."""
    role = str(entity.get("role", "villager"))
    if role == "guard":
        return "patrol"
    if role == "kid":
        return "idle"
    if role == "villager" and shift == "morning":
        return "idle"  # day labor: mornings at the market, afternoons hired
    return "work"


def plan_day(entity: Entity, world=None) -> list[dict]:  # type: ignore[no-untyped-def]
    """Generate today's blocks from needs. Gapless 24h, anchors resolved.
    Falls back to comps['fallback_sched'] on any surprise."""
    try:
        from omnisim.systems.schedule import NAMED
    except Exception:
        NAMED = {"market": (40, 20), "tavern": (18, 11)}
    try:
        anchors = entity.comps.get("anchors", {}) or {}
        home = tuple(anchors.get("home", (entity.x, entity.y)))
        work_at = entity.comps.get("work_at") or anchors.get("work")
        work = tuple(work_at) if work_at else home
        patrol = anchors.get("patrol")
        if patrol and isinstance(patrol, list):
            patrol = [tuple(p) for p in patrol]
        else:
            patrol = None
    except Exception:
        home = (entity.x, entity.y)
        work, patrol = home, None
    market, tavern = NAMED["market"], NAMED["tavern"]
    rng = getattr(world, "rng", None)

    def jit(span: int = 15) -> int:
        try:
            return rng.randint(-span, span) if rng else 0
        except Exception:
            return 0

    traits = _ptraits(entity)
    hunger = _pnum(entity, "hunger", 0)
    try:
        _pocket = entity.get("pocket", {})
        coin = float(_pocket.get("coin", 0)) if isinstance(_pocket, dict) else 0.0
    except Exception:
        coin = 0.0
    social = _pnum(entity, "social", 70.0)
    try:  # noon hunger forecast at a working burn (GDD: plan from state)
        from omnisim.systems.needs import decay_rate
        forecast = hunger + decay_rate(entity, "working", world) * 360.0
    except Exception:
        forecast = hunger + 15.0

    role = str(entity.get("role", "villager"))
    kid = role == "kid"
    early = role in ("farmer", "herbalist", "kid")  # field folk keep field hours
    # Wake is EXACT (six bells, seven for kids): jitter here straddled the
    # 06:00 dawn replan once and served breakfast twice. Bedtime keeps ±10.
    wake = 6 * 60 + (60 if kid else 0)
    bedtime = (21 * 60 if early else 22 * 60) + jit(10)
    blocks: list[dict] = []
    blocks.append({"start": bedtime, "end": wake, "act": "sleep", "at": home})
    t = wake
    # breakfast at home
    blocks.append({"start": t, "end": t + 60, "act": "eat", "at": home})
    t += 60
    # morning shift runs to noon sharp — the village eats at twelve
    mverb = _day_verb(entity, "morning")
    mat = {"work": work, "patrol": patrol or home, "idle": market}[mverb]
    blocks.append({"start": t, "end": 12 * 60, "act": mverb, "at": mat})
    t = 12 * 60
    # Midday split, learned the hard way (a starved baker proved it):
    # MAKERS (oven, forge) shop at noon — one trip, fair prices, full bins
    # for the afternoon — then dine at seven. A second evening shop bleeds
    # them on famine-priced bread and endless pots. Everyone else eats at
    # twelve and keeps the market hour at six.
    try:
        from omnisim.systems.schedule import WORK_NEEDS as _WN
        maker = (entity.get("work_skill") or "") in (_WN or {})
    except Exception:
        maker = False
    if maker:
        blocks.append({"start": t, "end": t + 60, "act": "buy", "at": market})
        t += 60
    else:
        # noon dinner: hollow bellies linger over a second hour; social eat out
        lunch_at = tavern if (social < 55 and coin >= 3) else home
        lunch_len = 120 if forecast >= 75 else 60
        blocks.append({"start": t, "end": t + lunch_len, "act": "eat",
                       "at": lunch_at})
        t += lunch_len
    # Market hours are culture, fixed sharp: day-laborers and kids shop at
    # five (ahead of the six-o'clock rush); everyone else at six. Slot times
    # stay fixed so the village keeps one rhythm; only content varies.
    buy_hour = 17 * 60 if role in ("villager", "kid") else 18 * 60
    if t < buy_hour:
        averb = _day_verb(entity, "afternoon")
        aat = {"work": work, "patrol": patrol or home, "idle": market}[averb]
        blocks.append({"start": t, "end": buy_hour, "act": averb, "at": aat})
        t = buy_hour
    if maker:
        # dinner when the shift ends, at home or out by purse and loneliness
        dinner_at = tavern if (social < 50 and coin >= 2) else home
        if t < 20 * 60:
            blocks.append({"start": t, "end": min(t + 60, 20 * 60),
                           "act": "eat", "at": dinner_at})
            t = min(t + 60, 20 * 60)
    else:
        # market hour, then the evening
        blocks.append({"start": t, "end": t + 60, "act": "buy", "at": market})
        t += 60
    # evening: the ambitious work late; the shy go home; then bed
    if t < bedtime:
        if "GREEDY" in traits or "AMBITIOUS" in traits:
            everb, eat_ = ("patrol", patrol or home) \
                if str(entity.get("role", "")) == "guard" else ("work", work)
            if kid:
                everb, eat_ = "idle", home
        elif "INTROVERTED" in traits or kid:
            everb, eat_ = "idle", home
        else:
            everb, eat_ = "idle", market
        blocks.append({"start": t, "end": bedtime, "act": everb, "at": eat_})
    return blocks
