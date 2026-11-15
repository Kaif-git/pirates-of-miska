"""V1 Foundations — Dynamic Needs & Wants (GDD 3.0).

Nothing is hardcoded. Decay and fulfillment are formulas over world state:

  Decay Rate = Base x Trait x Activity x Environment x World x PrevState
               x (1 - Skill Mitigation)

  Fulfillment = Nutrition x Quality x Freshness x Metabolism x DiminishingReturns

  Happiness = sum(State_i x W_i) with dynamic weights (personality x
              circumstance x recency).

All modifiers default to 1.0 so legacy content (no traits, no climate)
reproduces the old numbers exactly. New content hooks in via comps:
  traits: {GLUTTONOUS: bool, ASCETIC: bool, HARDY: bool, ...} or list
  recent_meals: [item_id, ...] (last 3, for diminishing returns)
  activity: current block act string
  happiness/weights derived on demand — no stored constants.
"""
from __future__ import annotations

BASE_HUNGER_PER_MIN = 1.0 / 25.0  # legacy base; real rate = base x modifiers
BASE_THIRST_PER_MIN = 1.0 / 18.0  # water runs out faster than supper does
THIRST_HP_MIN = 30.0  # -1 HP per 30 min parched (gentler than starving)

# Activity multipliers (GDD 3.1 "Current Activity"). Work costs more than
# sitting. Keys are prefixes of the real activity strings the sim writes
# ("sleeping", "eating", "working", "buying", "hauling wheat", ...).
ACTIVITY_MULT = {
    "work": 1.25,
    "patrol": 1.25,
    "buy": 1.1,
    "walk": 1.1,
    "haul": 1.1,
    "trad": 1.1,
    "gather": 1.1,
    "idle": 1.0,
    "rest": 1.0,
    "eat": 0.9,
    "sleep": 0.6,
}

TRAIT_DECAY = {
    "GLUTTONOUS": 1.3,
    "ASCETIC": 0.7,
    "HARDY": 0.85,
    "CONTENT": 1.0,
}


def _traits_of(entity) -> set[str]:
    raw = entity.get("traits", {}) if hasattr(entity, "get") else {}
    if isinstance(raw, dict):
        return {str(k).upper() for k, v in raw.items() if v}
    if isinstance(raw, (list, tuple, set)):
        return {str(t).upper() for t in raw}
    return set()


def trait_mult(entity) -> float:
    mult = 1.0
    for t in _traits_of(entity):
        mult *= TRAIT_DECAY.get(t, 1.0)
    return mult


def activity_mult(activity: str | None) -> float:
    if not activity:
        return 1.0
    act = str(activity).lower()
    for prefix, mult in ACTIVITY_MULT.items():
        if act.startswith(prefix):
            return mult
    return 1.0


def _staples() -> tuple:
    """Dinner staples from the item table — new hearty foods join famine math."""
    try:
        from omnisim.entities.items import staples as _st
        return tuple(_st())
    except Exception:
        return ("bread", "meal", "apple", "fish", "potted_meal")


STAPLES = _staples()


def village_mood(world) -> dict:
    """Derive famine/festival from live shelf state (GDD 3.1 World State).

    Bare staple shelves = famine stress: every belly knots tighter.
    Returns {"famine": 0..1, "festival": 0..0}.     Festival stays 0 until
    events exist; explicit comps['world_mood'] overrides when present.
    STEP 4 (A13): with the flag on, famine delegates to the mood system —
    bellies, never shelves. The old shelf math stays as the flag-off path.
    """
    try:
        from omnisim.systems.mood import on as _m4_on
        if _m4_on():
            from omnisim.core.clock import day_of as _day
            from omnisim.systems.mood import village_famine as _vf
            if int(_day(world.clock_pts)) <= 1:
                return {"famine": 0.0, "festival": 0.0}  # same grace as below
            return {"famine": _vf(world), "festival": 0.0}
    except Exception:
        pass
    try:
        if hasattr(world, "get"):
            mood = world.get("world_mood", None)
            if isinstance(mood, dict) and mood:
                return mood
    except Exception:
        pass
    famine = 0.0
    try:
        # Grace: shelves start bare until Bram's cart first tops them at dawn.
        # Crying famine on an unopened market would starve Day 1 by math.
        from omnisim.core.clock import day_of
        if int(day_of(world.clock_pts)) <= 1:
            return {"famine": 0.0, "festival": 0.0}
    except Exception:
        pass
    shelf_famine = 0.0
    try:
        stalls = getattr(world, "stalls", None)
        if stalls:
            qty, par = 0, 0
            for stall in stalls:
                stock = stall.get("stock", {}) or {}
                restock = stall.get("restock", {}) or {}
                for item_id in STAPLES:
                    # Only shelves that actually stock a staple count toward
                    # its par — ghost defaults for unstocked goods would cry
                    # famine on a full market.
                    if item_id not in restock:
                        continue
                    entry = stock.get(item_id, [0, 0])
                    qty += entry[0] if isinstance(entry, (list, tuple)) else 0
                    rentry = restock.get(item_id, [1, 0])
                    par += rentry[0] if isinstance(rentry, (list, tuple)) else 0
            if par > 0:
                shelf_famine = max(0.0, min(1.0, 1.0 - qty / par))
    except Exception:
        pass
    try:  # BELLIES, NOT SHELVES: empty bins with full bellies are a hot
        # market (flow feeds all) — famine is mouths going unfed. Aid reads
        # this meter; shelf panic alone must never bolt the safety net.
        _npcs = [n for n in getattr(world, "npcs", []) if not n.comps.get("dead")]
        if _npcs:
            _avg = sum(float(n.get("hunger", 0)) for n in _npcs) / len(_npcs)
            belly_famine = max(0.0, min(1.0, _avg / 80.0))
        else:
            belly_famine = 0.0
    except Exception:
        belly_famine = 0.0
    famine = max(0.0, min(1.0, max(shelf_famine * 0.3, belly_famine)))
    return {"famine": famine, "festival": 0.0}


def skill_mitigation(entity, skill: str = "Endurance") -> float:
    """High Survival/Endurance slows decay. Returns 0..0.45 mitigation."""
    try:
        from omnisim.systems.skills import skill_level
        lvl = int(skill_level(entity, skill))
    except Exception:
        lvl = 1
    return min(0.45, max(0.0, 0.02 * (lvl - 1)))


def environment_mult(world=None) -> float:
    """Cold/season stress hook. World may carry comps['climate'] = {cold: 0..1}."""
    if world is None:
        return 1.0
    try:
        climate = world.get("climate", {}) if hasattr(world, "get") else {}
        cold = float(climate.get("cold", 0.0)) if isinstance(climate, dict) else 0.0
        return 1.0 + 0.25 * max(0.0, min(1.0, cold))
    except Exception:
        return 1.0


def world_state_mult(world=None) -> float:
    """Famine/festival stress (GDD 3.1). Derived live from shelf state via
    village_mood() — bare staples knot every belly. Full famine: up to 1.3x."""
    if world is None:
        return 1.0
    try:
        mood = village_mood(world)
        famine = float(mood.get("famine", 0.0))
        festival = float(mood.get("festival", 0.0))
        return (1.0 + 0.3 * max(0.0, min(1.0, famine))) * \
               (1.0 - 0.15 * max(0.0, min(1.0, festival)))
    except Exception:
        return 1.0


def prev_state_mult(entity) -> float:
    """Death-spiral hook: yesterday's starvation weakens today's body."""
    try:
        was_low = float(entity.get("was_starving", 0)) if hasattr(entity, "get") else 0
        return 1.0 + 0.2 * max(0.0, min(1.0, was_low))
    except Exception:
        return 1.0


def decay_rate(entity, activity: str | None = None, world=None,
               base: float = BASE_HUNGER_PER_MIN) -> float:
    """Full GDD 3.1 decay formula. All factors dynamic, defaults 1.0."""
    rate = base
    rate *= trait_mult(entity)
    rate *= activity_mult(activity)
    rate *= environment_mult(world)
    rate *= world_state_mult(world)
    rate *= prev_state_mult(entity)
    rate *= (1.0 - skill_mitigation(entity))
    return max(0.0, rate)


def hunger_tick(entity, mins: float, activity: str | None = None, world=None) -> float:
    """Apply dynamic hunger decay. Returns amount added."""
    delta = decay_rate(entity, activity, world) * mins
    try:  # STEP 3: shivering is hungry work (flag-gated; warm souls unaffected)
        from omnisim.systems.needs3 import level_of as _n3_level
        from omnisim.systems.needs3 import on as _n3_on
        if _n3_on():
            delta *= 1.0 + 0.3 * _n3_level(entity, "cold") / 100.0
    except Exception:
        pass
    try:
        entity.comps["hunger"] = float(entity.get("hunger", 0)) + delta
    except Exception:
        pass
    return delta


def thirst_tick(entity, mins: float, activity: str | None = None) -> float:
    """Water runs out faster than supper; labor sweats, sleep sips.
    Reuses the activity curve (working 1.25x, sleeping 0.6x)."""
    try:
        base = BASE_THIRST_PER_MIN * activity_mult(activity)
        entity.comps.setdefault("thirst", 0.0)
        entity.comps["thirst"] = float(entity.get("thirst", 0)) + base * mins
        return base * mins
    except Exception:
        return 0.0


def ensure_filth(entity) -> None:
    """Seed the cleanliness ledger. Washing is next tranche's payoff."""
    try:
        entity.comps.setdefault("filth", 0.0)
    except Exception:
        pass


def filth_tick(entity, mins: float, world=None) -> None:
    """Living is dirtying: soil work and rot contact stain fast, days stain
    slow. Washing at the well resets. (Disease reads this next tranche.)"""
    ensure_filth(entity)
    try:
        rate = 2.0 / 1440.0  # +2 a day just living
        if entity.comps.get("got_dirty"):
            rate += 15.0 / 1440.0  # soil under nails, compost on hands
            entity.comps["got_dirty"] = False
        entity.comps["filth"] = min(
            100.0, float(entity.get("filth", 0)) + rate * mins)
    except Exception:
        pass


def wash(entity) -> None:
    """Cold well water, clean hands. Disease will care; already hearts do."""
    ensure_filth(entity)
    try:
        entity.comps["filth"] = 0.0
        socialize(entity, 1.0)
    except Exception:
        pass


def freshness_mult(item_id: str, age_days: float = 0.0) -> float:
    """Freshness 1.0 -> 0.2 as food ages past its shelf life (GDD 4.2)."""
    try:
        from omnisim.systems.rot import SHELF_LIFE
        shelf = float(SHELF_LIFE.get(item_id, 3))
    except Exception:
        shelf = 3.0
    if age_days <= 0:
        return 1.0
    ratio = age_days / max(1.0, shelf)
    if ratio <= 0.5:
        return 1.0
    if ratio >= 1.5:
        return 0.2
    # linear falloff 1.0 -> 0.2 between 0.5x and 1.5x shelf
    return max(0.2, 1.0 - 0.8 * ((ratio - 0.5) / 1.0))


def diminishing_mult(entity, item_id: str) -> float:
    """Eating the same food repeatedly gives less (GDD 3.1). 1.0, 0.85, 0.7..."""
    try:
        recent = entity.get("recent_meals", []) if hasattr(entity, "get") else []
    except Exception:
        recent = []
    if not isinstance(recent, list):
        return 1.0
    repeats = sum(1 for m in recent[-3:] if m == item_id)
    return max(0.5, 1.0 - 0.15 * repeats)


def metabolism_mult(entity) -> float:
    """Entity metabolism from traits (GDD 3.1 fulfillment)."""
    traits = _traits_of(entity)
    mult = 1.0
    if "GLUTTONOUS" in traits:
        mult *= 0.9  # burns through meals faster = less restored
    if "ASCETIC" in traits:
        mult *= 1.1
    return mult


def clean_mult(entity) -> float:
    """Clean hands feed better: full value washed, three-fifths grimed.
    Soil, compost, and rot stain every shift — the well forgives."""
    try:
        filth = max(0.0, min(100.0, float(entity.get("filth", 0))))
    except Exception:
        return 1.0
    return 1.0 - 0.4 * (filth / 100.0)


def fulfillment(entity, item_id: str, base_nutrition: float, quality: int = 1,
                age_days: float = 0.0) -> float:
    """GDD 3.1: Hunger Restored = Nutrition x Quality x Freshness x
    Metabolism x DiminishingReturns."""
    try:
        from omnisim.entities.items import food_mult
        qm = float(food_mult(quality))
    except Exception:
        qm = 1.0
    return (float(base_nutrition) * qm * freshness_mult(item_id, age_days)
            * metabolism_mult(entity) * diminishing_mult(entity, item_id)
            * clean_mult(entity))


def record_meal(entity, item_id: str) -> None:
    try:
        recent = entity.get("recent_meals", None)
        if not isinstance(recent, list):
            recent = []
            entity.comps["recent_meals"] = recent
        recent.append(item_id)
        del recent[:-6]  # keep last 6, only last 3 matter
    except Exception:
        pass


def ensure_wants(entity) -> None:
    """Seed the Wants state. Social 70 (most folk start content), Esteem 60."""
    try:
        entity.comps.setdefault("social", 70.0)
        entity.comps.setdefault("esteem", 60.0)
    except Exception:
        pass


def _company_near(entity, world, radius: int = 6) -> int:
    """How many other souls stand close enough to talk to."""
    try:
        others = []
        if hasattr(world, "npcs"):
            others.extend(world.npcs)
        if hasattr(world, "player") and world.player is not entity:
            others.append(world.player)
        ex, ey = entity.x, entity.y
        return sum(1 for o in others
                   if o is not entity and not o.comps.get("dead")
                   and max(abs(o.x - ex), abs(o.y - ey)) <= radius)
    except Exception:
        return 0


def tick_wants(entity, mins: float, world=None) -> None:
    """V1 Wants tick (GDD 3.2). Social rots in isolation, holds in company;
    Esteem drifts toward 50 (hedonic treadmill) between triumphs.
    Never touches hunger/fatigue. Gentle: a full lonely day costs ~24."""
    ensure_wants(entity)
    try:
        activity = str(entity.comps.get("activity", "idle")).lower()
        if not activity.startswith("sleep"):
            traits = _traits_of(entity)
            rate = 0.02  # per minute alone (~19 waking hours void of souls)
            if "INTROVERTED" in traits:
                rate *= 0.6  # solitude feeds them, slowly
            if world is not None and _company_near(entity, world) > 0:
                rate *= 0.3  # company sustains
            entity.comps["social"] = max(
                0.0, float(entity.get("social", 70.0)) - rate * mins)
        est = float(entity.get("esteem", 60.0))
        entity.comps["esteem"] = max(
            0.0, min(100.0, est + (50.0 - est) * 0.008 * mins))
    except Exception:
        pass


def socialize(entity, amount: float = 8.0) -> None:
    """A greeting, a deal closed with words, a meal among friends."""
    ensure_wants(entity)
    try:
        entity.comps["social"] = min(
            100.0, float(entity.get("social", 70.0)) + amount)
    except Exception:
        pass


def honor(entity, amount: float = 8.0) -> None:
    """Mastery, praise, standing — Esteem rises with triumph."""
    ensure_wants(entity)
    try:
        entity.comps["esteem"] = min(
            100.0, float(entity.get("esteem", 60.0)) + amount)
    except Exception:
        pass


def happiness(entity) -> float:
    """GDD 3.x dynamic weighted happiness 0..100. Weights shift with
    personality/circumstance/recency — never a flat average."""
    def num(key: str, default: float) -> float:
        try:
            return float(entity.get(key, default))
        except Exception:
            return default
    hunger = num("hunger", 0)  # 0 good, 100 bad
    fatigue = num("fatigue", 0)
    traits = _traits_of(entity)
    # Base satisfaction per axis (inverse of deprivation)
    satiety = max(0.0, 100.0 - hunger)
    rest = max(0.0, 100.0 - fatigue)
    social = max(0.0, min(100.0, num("social", 70.0)))
    esteem = max(0.0, min(100.0, num("esteem", 60.0)))
    mood = (social + esteem) / 2.0
    # Dynamic weights
    w_food, w_rest, w_mood = 1.0, 0.8, 0.6
    if "AMBITIOUS" in traits:
        w_mood += 0.4
    if hunger >= 70:  # danger focuses the mind (GDD: circumstance weight)
        w_food += 0.8
    if fatigue >= 70:
        w_rest += 0.8
    if social < 30:  # loneliness weighs (GDD: circumstance weight)
        w_mood += 0.5
    total = w_food + w_rest + w_mood
    return (satiety * w_food + rest * w_rest + mood * w_mood) / max(1e-6, total)


def happiness_xp_mult(entity) -> float:
    """Happy entities learn better (GDD 6.1). 0.8 .. 1.25."""
    h = happiness(entity)
    if h >= 70:
        return 1.25
    if h >= 50:
        return 1.0
    if h >= 30:
        return 0.9
    return 0.8
