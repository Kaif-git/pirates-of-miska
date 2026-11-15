"""WEATHER: cold snaps. Every so often the north wind finds Thornhollow
for 2-4 dawns, and every hearth in the village eats firewood. Souls burn
from their own pockets; the woodless gnaw cold (hunger +8 — shivering
is hungry work). Firewood stops being the smith's fuel and becomes
everyone's warmth: demand appears on its own, and prices follow.
"""
from __future__ import annotations

SNAP_CHANCE = 1.0 / 8.0  # a snap most weeks, not every week


def chill_of(village) -> int:  # type: ignore[no-untyped-def]
    """Dawns of cold remaining (0 = mild)."""
    return max(0, int(getattr(village, "chill_days", 0) or 0))


def dawn_chill(village) -> int:  # type: ignore[no-untyped-def]
    """Roll the sky at dawn. Returns the new chill."""
    chill = chill_of(village)
    if chill > 0:
        chill -= 1
    else:
        try:
            roll = village.rng.random() if getattr(village, "rng", None) else 0.99
        except Exception:
            roll = 0.99
        if roll < SNAP_CHANCE:
            import random as _r
            try:
                chill = 2 + int(village.rng.randint(0, 2))
            except Exception:
                chill = 2 + _r.randint(0, 2)
            village.say("A cold snap bites down from the north — hearths will eat wood.")
    village.chill_days = chill
    return chill


def burn_hearths(village) -> str:  # type: ignore[no-untyped-def]
    """Every living soul feeds their hearth (or shivers). One line, not twenty."""
    try:  # STEP 3: per-household hearths burn per-tick instead (hearth_tick);
        # leaving this on would double-charge every winter household.
        from omnisim.systems.needs3 import on as _n3_on
        if _n3_on():
            return ""
    except Exception:
        pass
    if chill_of(village) <= 0:
        return ""
    burned, cold = 0, 0
    for n in list(village.npcs) + [village.player]:
        if n is not village.player and n.comps.get("dead"):
            continue
        pocket = n.get("pocket", {}) if hasattr(n, "get") else {}
        if int(pocket.get("firewood", 0)) >= 1:
            pocket["firewood"] -= 1
            if pocket["firewood"] <= 0:
                del pocket["firewood"]
            burned += 1
        else:
            try:
                n.comps["hunger"] = float(n.get("hunger", 0)) + 4.0  # a cold night,
            except Exception:  # not starvation (they buy wood the next dusk)
                pass
            cold += 1
    line = f"Hearths eat {burned} firewood against the cold"
    if cold:
        line += f" — {cold} soul{'s' if cold != 1 else ''} shiver woodless"
    line += "."
    village.say(line)
    return line


def chill_line(village) -> str:  # type: ignore[no-untyped-def]
    """Board weather: what the sky is doing to dinner."""
    chill = chill_of(village)
    if chill > 0:
        return f"COLD SNAP: {chill} dawn{'s' if chill != 1 else ''} left — hearths eat firewood."
    return ""
