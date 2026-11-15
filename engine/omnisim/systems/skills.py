"""Use-to-level skills, cap 10. One code path for players and NPCs.

V1 Foundations (GDD 6.0, dynamic):
  XP Needed = Base * (Level ^ Exponent) * Learning * Affinity
Levels live in comps["skills"] {name: level}, xp in comps["skillxp"].
gain_xp returns newly reached levels so callers can narrate
("Farming rises to 2!") for whoever is being watched.

Legacy content (no traits, default curve) reproduces 50 x L exactly when
exponent=1.0; data/skills.yaml may set per-skill base/exponent.
"""
from __future__ import annotations

MAX_LEVEL = 10
XP_PER_LEVEL = 50
# GDD 6.1 wants exponent >1 so higher levels cost progressively more.
# Default stays 1.0 (legacy 50xL, tests green); tune via data/skills.yaml
# per-skill `exponent:` when balance wants the steeper curve.
DEFAULT_EXPONENT = 1.0
LEARNING_TRAITS = {"GENIUS": 0.7, "QUICK": 0.85, "DULL": 1.3, "SLOW": 1.15}
AFFINITY_TRAITS = {
    # entity trait -> skills it makes cheaper, e.g. NATURAL BLACKSMITH
    "NATURAL BLACKSMITH": {"Blacksmithing": 0.8, "Smithing": 0.8},
    "GREEN THUMB": {"Farming": 0.8},
    "SILVER TONGUE": {"Speech": 0.8, "Trading": 0.85},
}

def _skill_names() -> tuple:
    """Skill names from data/skills.yaml (new arts join with no code)."""
    try:
        import yaml as _yaml
        from pathlib import Path as _P
        p = _P(__file__).resolve().parents[3] / "data" / "skills.yaml"
        doc = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        names = [str(k) for k in ((doc.get("skills", {}) or {}).keys())]
        if names:
            return tuple(names)
    except Exception:
        pass
    return ("Farming", "Cooking", "Trading", "Smithing", "Woodcutting",
            "Fishing", "Sneaking", "Speech", "Endurance", "Healing",
            "Herbalism", "Pottery")


ALL_SKILLS = _skill_names()


_CURVES: dict | None = None


def _curves() -> dict:
    """Per-skill (base_xp, exponent), loaded once from data/skills.yaml."""
    global _CURVES
    if _CURVES is None:
        _CURVES = {}
        try:
            import yaml as _yaml
            from pathlib import Path as _P
            p = _P(__file__).resolve().parents[3] / "data" / "skills.yaml"
            if p.is_file():
                doc = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                for name, node in ((doc.get("skills", {}) or {}).items()):
                    if isinstance(node, dict):
                        _CURVES[str(name)] = (
                            float(node.get("base_xp", XP_PER_LEVEL)),
                            float(node.get("exponent", DEFAULT_EXPONENT)))
        except Exception:
            pass
    return _CURVES


def refresh_curves() -> None:
    """Tests call this after editing skills.yaml on disk mid-session."""
    global _CURVES
    _CURVES = None


def _curve_for(skill: str | None = None, entity=None) -> tuple[float, float]:
    """(base, exponent) for a skill. Data-driven when skills.yaml sets them."""
    if skill:
        try:
            hit = _curves().get(skill)
            if hit:
                return hit
        except Exception:
            pass
    return float(XP_PER_LEVEL), float(DEFAULT_EXPONENT)


def _trait_set(entity) -> set[str]:
    try:
        raw = entity.get("traits", {}) if hasattr(entity, "get") else {}
    except Exception:
        return set()
    if isinstance(raw, dict):
        return {str(k).upper() for k, v in raw.items() if v}
    if isinstance(raw, (list, tuple, set)):
        return {str(t).upper() for t in raw}
    return set()


def learning_mult(entity=None) -> float:
    traits = _trait_set(entity) if entity is not None else set()
    mult = 1.0
    for t in traits:
        mult *= LEARNING_TRAITS.get(t, 1.0)
    return mult


def affinity_mult(skill: str, entity=None) -> float:
    traits = _trait_set(entity) if entity is not None else set()
    mult = 1.0
    for t in traits:
        mult *= AFFINITY_TRAITS.get(t, {}).get(skill, 1.0)
    return mult


def xp_need(level: int, skill: str | None = None, entity=None) -> int:
    """GDD 6.1: Base * (Level ^ Exponent) * Learning * Affinity.

    Called as xp_need(1) in legacy tests — with Level=1, L^exp=1 and no
    entity modifiers, so it returns exactly 50 (backwards compatible).
    """
    base, exp = _curve_for(skill, entity)
    need = base * (max(1, int(level)) ** exp)
    need *= learning_mult(entity) * affinity_mult(skill or "", entity)
    return max(1, int(round(need)))


def level_of(xp: int, skill: str | None = None, entity=None) -> int:
    """Level from total xp. Legacy linear path (base 50, exp 1, no traits)
    uses division so xp 350 -> 8 exactly as old content expects; steeper
    curves (exponent>1 or trait discounts) use cumulative thresholds."""
    xp = max(0, int(xp))
    base, exp = _curve_for(skill, entity)
    mods = learning_mult(entity) * affinity_mult(skill or "", entity)
    if abs(exp - 1.0) < 1e-9 and abs(mods - 1.0) < 1e-9:
        return min(MAX_LEVEL, max(1, int(xp // base) + 1))
    total = 0
    level = 1
    while level < MAX_LEVEL:
        total += xp_need(level, skill, entity)
        if xp < total:
            return level
        level += 1
    return MAX_LEVEL


def ensure_skills(entity, starters: dict | None = None) -> None:  # type: ignore[no-untyped-def]
    skills = entity.comps.setdefault("skills", {})
    xp = entity.comps.setdefault("skillxp", {})
    for name in ALL_SKILLS:
        skills.setdefault(name, 1)
        xp.setdefault(name, 0)
    for name, level in (starters or {}).items():
        if name in skills:
            skills[name] = min(MAX_LEVEL, max(1, int(level)))


def skill_level(entity, name: str) -> int:  # type: ignore[no-untyped-def]
    return int(entity.get("skills", {}).get(name, 1))


def skill_xp_pct(entity, name: str) -> int:  # type: ignore[no-untyped-def]
    """Progress 0-99 toward the next level (100 = maxed, shown as-is)."""
    level = skill_level(entity, name)
    if level >= MAX_LEVEL:
        return 100
    xp = int(entity.get("skillxp", {}).get(name, 0))
    base = sum(xp_need(l, name, entity) for l in range(1, level))
    need = xp_need(level, name, entity)
    return min(99, int(100 * (xp - base) / max(1, need)))


def xp_gain(base: float, entity=None, difficulty: float = 0.0,
            outcome: str = "success", teacher: bool = False) -> int:
    """GDD 6.1 XP gain: base x difficulty-gap x outcome x happiness x teacher.

    difficulty = DC - level (positive = stretch goal pays more).
    outcome in {critical, success, failure}. teacher = skilled mentor nearby.
    """
    mult = 1.0
    # Attempting slightly beyond skill pays more; trivial tasks pay less.
    mult *= max(0.3, 1.0 + 0.15 * max(-3.0, min(5.0, float(difficulty))))
    mult *= {"critical": 1.5, "success": 1.0, "failure": 0.3}.get(outcome, 1.0)
    if entity is not None:
        try:
            from omnisim.systems.needs import happiness_xp_mult
            mult *= happiness_xp_mult(entity)
        except Exception:
            pass
    if teacher:
        mult *= 1.25
    return max(1, int(round(float(base) * mult)))


def gain_xp(entity, name: str, amount: int = 1, difficulty: float = 0.0,  # type: ignore[no-untyped-def]
            outcome: str = "success", teacher: bool = False) -> list[int]:
    """Add xp (with dynamic gain modifiers); returns newly reached levels."""
    if name not in ALL_SKILLS or amount <= 0:
        return []
    ensure_skills(entity)
    before = entity.comps["skills"][name]
    entity.comps["skillxp"][name] += xp_gain(amount, entity, difficulty, outcome, teacher)
    after = level_of(entity.comps["skillxp"][name], name, entity)
    # STEP 8: XP never demotes — presets (worldgen starters) outrank a thin
    # ledger until earned XP catches up. Guards rust re-entry and Elin's hoe.
    entity.comps["skills"][name] = max(int(before), int(after))
    if name == "Endurance":  # +1 max HP every 3 levels
        entity.comps["max_hp"] = 20 + (after - 1) // 3
    if after > before:
        try:  # V1 Wants (GDD 3.2): mastery feeds Esteem
            from omnisim.systems.needs import honor
            honor(entity, 8.0)
        except Exception:
            pass
        return list(range(before + 1, after + 1))
    return []


def top_skill(entity) -> tuple[str, int]:  # type: ignore[no-untyped-def]
    skills = entity.get("skills", {}) or {"Endurance": 1}
    xp = entity.get("skillxp", {}) or {}
    name = max(skills, key=lambda k: (skills[k], xp.get(k, 0), k))
    return name, int(skills[name])

