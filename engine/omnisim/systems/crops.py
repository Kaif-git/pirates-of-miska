"""Real crops: sow -> sprout -> green -> ripe, and soil that remembers.

Wheat takes its season (9 dawns), barley hurries hardy through poor earth
(7), turnips rush sweet roots for hungry mouths (5). Plots record WHAT
they grow: {kind, sown}. Harvest pays grain/roots plus kept seed corn —
seeds are items (sowing eats a seed, never grain), stamped gen 1 with an
empty traits map for the selection work to come.
"""
from __future__ import annotations

import os

_CROPS = None


def crop_spec():  # type: ignore[no-untyped-def]
    """data/crops.yaml, loaded once (tuning without code)."""
    global _CROPS
    if _CROPS is None:
        import yaml
        here = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        for cand in (os.path.join(here, "data", "crops.yaml"),
                     os.path.join(os.getcwd(), "data", "crops.yaml")):
            if os.path.exists(cand):
                with open(cand, encoding="utf-8") as fh:
                    _CROPS = (yaml.safe_load(fh) or {}).get("crops", {}) or {}
                break
        else:
            _CROPS = {}
    return _CROPS


def kinds() -> list:
    """Every growable kind, wheat first (the old staple leads)."""
    spec = crop_spec()
    return [k for k in ("wheat", "barley", "turnip") if k in spec] + \
        [k for k in spec if k not in ("wheat", "barley", "turnip")]


def spec_of(kind: str) -> dict:
    """Tuning for one crop; wheat's shape as fallback."""
    spec = crop_spec()
    return dict(spec.get(kind) or spec.get("wheat") or {})


def grains() -> list:
    """Grindable grains: wheat, barley, and whatever grinds hereafter."""
    return [k for k in kinds() if spec_of(k).get("grain")]


def days_of(kind: str) -> int:
    """Dawns seed to sheaf for this crop."""
    return int(spec_of(kind).get("days", 9))


def seed_of(kind: str) -> str:
    """The seed item sowing eats."""
    return str(spec_of(kind).get("seed", "wheat_seed"))


def seed_yield_of(kind: str) -> int:
    """Kept seed corn per cut, before skill."""
    return int(spec_of(kind).get("seed_yield", 1))


def min_fert_of(kind: str) -> int:
    """Weakest earth this crop takes (barley and turnips forgive)."""
    return int(spec_of(kind).get("min_fert", 0))


# The old constants, wheat's shape, for callers that never learned kinds.
GROW_DAYS = 9  # dawns from sowing to ripe (a realistic wheat season, compressed)
SEED_KEEP = 4  # the apron keeps seed pinches: hands sell no seed below this


def n_plots_for(n_tiles: int) -> int:
    """Worked plots scale with cleared field: a third of the farm tiles,
    between a garden (8) and a manor (40). New fields widen the village."""
    return max(8, min(40, n_tiles // 3))


def stage(p, day: int) -> str:
    """seed (sown) -> sprout -> green -> ripe. Nothing harvestable till ripe."""
    if p["crop"] is None:
        return "resting"
    kind = p["crop"].get("kind", "wheat") if isinstance(p["crop"], dict) else "wheat"
    days = days_of(kind)
    age = day - p["crop"]["sown"]
    if age < 0:
        return "seed"  # pre-season sowing, not yet started
    if age < max(2, int(days * 0.34)):
        return "seed"
    if age < max(3, int(days * 0.67)):
        return "sprout"
    if age < days:
        return "green"
    return "ripe"


def ensure_plots(village) -> list:  # type: ignore[no-untyped-def]
    """Stamp the worked dozen onto farm tiles (idempotent)."""
    if getattr(village, "field", None):
        return village.field
    from omnisim.world import tile as T
    gm = village.game_map
    tiles = [(x, y) for y in range(gm.tiles.shape[0]) for x in range(gm.tiles.shape[1])
             if gm.tile_id(x, y) == T.FARM]
    tiles.sort(key=lambda t: (t[1], t[0]))
    n_plots = n_plots_for(len(tiles))
    picked = [t for i, t in enumerate(tiles) if i % max(1, len(tiles) // n_plots)
              == 0][:n_plots] if tiles else []
    village.field = [{"x": x, "y": y, "fert": 2, "crop": None,
                      "fallow": 0} for x, y in picked]
    # Last season's staggered sowings: pairs ripen across days 1-5 so week
    # one harvests while fresh sowings (ripe day 10+) come up behind.
    # Wheat leads; barley and turnips joined the rotation after.
    seed_kinds = ["wheat", "wheat", "barley", "turnip"]
    for i, p in enumerate(village.field[:10]):
        kind = seed_kinds[i % len(seed_kinds)]
        p["crop"] = {"kind": kind, "sown": -(days_of(kind) - 1 - i // 2)}
    return village.field


def plot_at(village, x: int, y: int):  # type: ignore[no-untyped-def]
    """The worked plot underfoot, if any."""
    for p in ensure_plots(village):
        if p["x"] == x and p["y"] == y:
            return p
    return None


def kind_of(p) -> str:
    """What's growing here (old saves grew wheat)."""
    crop = p.get("crop") or {}
    return crop.get("kind", "wheat") if isinstance(crop, dict) else "wheat"


def ripe(p, day: int) -> bool:  # type: ignore[no-untyped-def]
    """Sown long enough to cut (each crop keeps its own season)."""
    return p["crop"] is not None and day - p["crop"]["sown"] >= days_of(kind_of(p))


def sow(village, p, day: int, kind: str = "wheat") -> bool:  # type: ignore[no-untyped-def]
    """A seed for the earth. True when it took."""
    if p["crop"] is not None:
        return False
    if kind not in crop_spec():
        kind = "wheat"
    p["crop"] = {"kind": kind, "sown": day}
    p["fallow"] = 0
    return True


def choose_crop(village, p) -> str:  # type: ignore[no-untyped-def]
    """What this earth should grow: hungry mouths get fast roots, poor
    soil gets hardy barley, good soil earns wheat. Subsistence first."""
    try:
        mouths = [n for n in village.npcs if not n.comps.get("dead")]
        if mouths:
            avg_hunger = sum(float(n.get("hunger", 0)) for n in mouths) / len(mouths)
            if avg_hunger >= 45:
                return "turnip"  # fast roots for thin bellies
    except Exception:
        pass
    try:  # fiber gap: finished-good demand walks the chain to the field.
        from omnisim.systems.metrics import unmet_summary as _uu
        _dem = _uu(village)
        from omnisim.systems.production import recipes as _rr
        _want = {k.split(":", 1)[1] for k in _dem if k.startswith("demand:")}
        _chain = set(_want)
        for _ in range(4):
            for r in _rr().values():
                _outs = {o.get("good") for o in (r.get("outputs", []) or [])}
                if _outs & _chain:
                    for inp in (r.get("inputs", []) or []):
                        if "good" in inp:
                            _chain.add(inp["good"])
        for _cand, _sp in (crop_spec() or {}).items():
            if _cand in _chain and int(p.get("fert", 0)) >= int(_sp.get("min_fert", 0)):
                return _cand
    except Exception:
        pass
    if int(p.get("fert", 0)) >= 2:
        return "wheat"
    return "barley"  # hardy grain for tired earth


def harvest_yield(p) -> int:
    """Good soil pays: the crop's sheaves and one per heart of earth."""
    return int(spec_of(kind_of(p)).get("yield", 2)) + int(p.get("fert", 0))


def harvest(village, p) -> tuple:  # type: ignore[no-untyped-def]
    """Cut it: (kind, pay, seeds) — grain for the hand, seed corn for
    next season, hunger for the soil."""
    if p["crop"] is None:
        return ("wheat", 0, 0)
    kind = kind_of(p)
    pay = harvest_yield(p)  # weigh the sheaf BEFORE the soil pays
    p["crop"] = None
    p["fert"] = max(0, int(p.get("fert", 0)) - 1)
    p["fallow"] = 0
    return (kind, pay, seed_yield_of(kind))


def feed(village, p) -> bool:  # type: ignore[no-untyped-def]
    """Compost feeds: +1 heart, to 3. (Phase 2 makes the rot.)"""
    if int(p.get("fert", 0)) >= 3:
        return False
    p["fert"] = int(p.get("fert", 0)) + 1
    return True


def advance_dawn(village, day: int) -> None:  # type: ignore[no-untyped-def]
    """Resting earth recovers: two empty dawns knit one heart back."""
    for p in ensure_plots(village):
        if p["crop"] is None and int(p.get("fert", 0)) < 3:
            p["fallow"] = int(p.get("fallow", 0)) + 1
            if p["fallow"] >= 2:
                p["fallow"] = 0
                p["fert"] = int(p.get("fert", 0)) + 1


def field_line(village, day: int) -> str:  # type: ignore[no-untyped-def]
    """'Fields: 3 ripe (2 wheat...), 5 growing, 4 resting (soil HHH)'."""
    plots = ensure_plots(village)
    ripe_ps = [p for p in plots if ripe(p, day)]
    n_grow = sum(1 for p in plots if p["crop"] is not None and not ripe(p, day))
    n_rest = len(plots) - len(ripe_ps) - n_grow
    avg = sum(int(p.get("fert", 0)) for p in plots) / max(1, len(plots))
    hearts = "H" * round(avg) + "-" * (3 - round(avg))
    if ripe_ps:
        from collections import Counter
        mix = Counter(kind_of(p) for p in ripe_ps)
        what = ", ".join(f"{c} {k}" for k, c in sorted(mix.items()))
        return (f"Fields: {len(ripe_ps)} ripe ({what}), {n_grow} growing, "
                f"{n_rest} resting (soil {hearts})")
    return (f"Fields: 0 ripe, {n_grow} growing, {n_rest} resting "
            f"(soil {hearts})")
