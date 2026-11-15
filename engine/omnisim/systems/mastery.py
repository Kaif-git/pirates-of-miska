"""Step 8: skill, quality, and progression (mastery pays, rust taxes, lessons teach).

- min_skill gates recipes (data rows, not code).
- q_cap = min(5, 1 + skill//2) layers over the existing quality formula.
- Rust: 14 idle days, then -1%/day to -30%; re-learning recovers 3x.
- New skills start at half the best related skill (data map).
- Apprenticeship: lesson service, master earns fee, learner 2x XP.
All inert when flags.step_8 is off.
"""
from __future__ import annotations

_DEFAULTS = {"rust_grace_days": 14, "rust_per_day": 0.01, "rust_max": 0.30,
             "relearn_mult": 3.0, "related_frac": 0.5, "lesson_xp_mult": 2.0,
             "below_tier_xp": 0.5}

_RELATED = {"Farming": ["Cooking", "Herbalism"], "Cooking": ["Farming", "Healing"],
            "Smithing": ["Carpentry", "Mining"], "Carpentry": ["Smithing", "Woodcutting"],
            "Mining": ["Smithing", "Endurance"], "Fishing": ["Cooking", "Sneaking"],
            "Healing": ["Herbalism", "Cooking"], "Herbalism": ["Healing", "Farming"],
            "Trading": ["Speech", "Sneaking"], "Milling": ["Farming", "Cooking"],
            "Pottery": ["Mining", "Cooking"], "Woodcutting": ["Carpentry", "Endurance"]}


def _econ():
    try:
        from omnisim.systems.production import _load as _ld
        return _ld("economy") or {}
    except Exception:
        return {}


def on():
    try:
        return bool((_econ().get("flags", {}) or {}).get("step_8", False))
    except Exception:
        return False


def _cfg(key):
    try:
        return (_econ().get("mastery", {}) or {}).get(key, _DEFAULTS[key])
    except Exception:
        return _DEFAULTS[key]


def related_of(skill):
    try:
        extra = (_econ().get("mastery", {}) or {}).get("related", {}) or {}
        return list(extra.get(skill, _RELATED.get(skill, [])))
    except Exception:
        return list(_RELATED.get(skill, []))


def _day_of(village):
    try:
        from omnisim.core.clock import day_of as _d
        return int(_d(village.clock_pts))
    except Exception:
        return 0


def _rust_state(npc):
    try:
        r = npc.comps.get("skill_rust")
        if not isinstance(r, dict):
            r = npc.comps["skill_rust"] = {}
        return r
    except Exception:
        return {}


def effective_skill(npc, skill):
    """Rust-adjusted level (flag off: raw level, bit-identical)."""
    try:
        from omnisim.systems.skills import skill_level as _sl
        base = int(_sl(npc, skill))
    except Exception:
        base = 1
    if not on():
        return base
    try:
        rust = float(_rust_state(npc).get(skill, 0.0))
        return max(1, int(round(base * (1.0 - min(float(_cfg("rust_max")), max(0.0, rust))))))
    except Exception:
        return base


def touch(village, npc, skill):
    """Mark a skill used today (resets the rust clock)."""
    if not on():
        return
    try:
        _rust_state(npc)["last_%s" % skill] = _day_of(village)
    except Exception:
        pass


def rust_tick(village):
    """Dawn: idle skills past grace accrue rust; used skills hold."""
    if not on():
        return
    try:
        day = _day_of(village)
        grace = int(_cfg("rust_grace_days"))
        rate = float(_cfg("rust_per_day"))
        cap = float(_cfg("rust_max"))
        for npc in getattr(village, "npcs", []) or []:
            if npc.comps.get("dead"):
                continue
            try:
                st = _rust_state(npc)
                skills = npc.get("skills", {}) or {}
                for sk in skills:
                    last = st.get("last_%s" % sk, day)
                    try:
                        idle = day - int(last)
                    except Exception:
                        idle = 0
                    if idle > grace:
                        st[sk] = min(cap, float(st.get(sk, 0.0)) + rate)
                    elif idle <= 1:
                        st[sk] = max(0.0, float(st.get(sk, 0.0)) - rate * float(_cfg("relearn_mult")))
            except Exception:
                continue
    except Exception:
        pass


def q_cap(skill_level_n):
    return min(5, 1 + int(skill_level_n) // 2)


def seed_skill(npc, skill):
    """New skills start at half the best related skill (spec: 0.5 x max)."""
    try:
        from omnisim.systems.skills import skill_level as _sl2
        from omnisim.systems.skills import ensure_skills as _es
        _es(npc)
        cur = int(_sl2(npc, skill))
        if cur > 1:
            return cur
        best = 1
        for rel in related_of(skill):
            try:
                best = max(best, int(_sl2(npc, rel)))
            except Exception:
                pass
        seed = max(1, int(best * float(_cfg("related_frac"))))
        npc.comps["skills"][skill] = seed
        return seed
    except Exception:
        return 1


def below_tier_mult(npc, recipe):
    """0.5x XP when practicing below your tier (flag off: 1.0)."""
    if not on():
        return 1.0
    try:
        req = int(recipe.get("min_skill", 1) or 1)
        from omnisim.systems.skills import skill_level as _sl3
        lvl = int(_sl3(npc, recipe.get("skill", "")))
        if lvl >= req + 3:
            return float(_cfg("below_tier_xp"))
        return 1.0
    except Exception:
        return 1.0


def lesson_price(village, master, skill):
    """A lesson costs the master's day-wage fraction (data-free pricing)."""
    try:
        from omnisim.systems.mood import time_value_mc as _tv
        return max(1.0, float(_tv(master, village)) / 1000.0 * 12.0)
    except Exception:
        return 5.0


def buy_lesson(village, learner, master, skill):
    """Fee moves, learner banks 2x XP credit, trust gates willingness."""
    if not on():
        return False
    try:
        willing = True
        try:
            from omnisim.systems.memory import on as _m5
            from omnisim.systems.memory import trust_of as _tr
            if _m5():
                willing = float(_tr(master, learner.name)) >= -2.0
        except Exception:
            pass
        if not willing:
            return False
        fee = lesson_price(village, master, skill)
        lp = learner.get("pocket", {}) or {}
        if float(lp.get("coin", 0)) < fee:
            return False
        lp["coin"] = float(lp.get("coin", 0)) - fee
        mp = master.get("pocket", {}) or {}
        mp["coin"] = float(mp.get("coin", 0)) + fee
        try:
            from omnisim.systems.skills import gain_xp as _gx
            from omnisim.systems.skills import skill_level as _sl4
            lvl = int(_sl4(master, skill))
            amt = max(1, int(round((1 + lvl // 2) * float(_cfg("lesson_xp_mult")))))
            _gx(learner, skill, amt)
        except Exception:
            pass
        touch(village, learner, skill)
        return True
    except Exception:
        return False
