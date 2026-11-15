"""Brain: one loop, scored bids, no thresholds.

Each NPC tick runs three channels in fixed order (same order the old
inline code ran, so behavior is identical):

1. urgent()  — collapse veto (rest-halt) before movement commits.
2. ambient() — opportunities alongside routine (bites, repairs).
3. steer()   — feet: mid-walk commitments hold; otherwise re-bid —
   rest-halt > loaded haul (suspended when survival calls) > best
   scored trip (market / wilds / well by value) > kid loot > routine.

Body systems (metabolism, fatigue, XP, trade, movement) stay in
schedule.py; the brain only DECIDES. Queued migrations (marked INLINE):
errand trigger, fatigue model, sale proximity, kid gather.
"""
from __future__ import annotations


def urgent(village, npc, blk) -> None:  # type: ignore[no-untyped-def]
    """Collapse vetoes run pre-move (like the old override): feet stop."""
    from omnisim.systems import desires as D
    D.rest_halt(village, npc, blk)


def ambient(village, npc, blk) -> None:  # type: ignore[no-untyped-def]
    """Opportunities alongside routine: bites, mid-day repairs."""
    from omnisim.systems import desires as D
    for want in D.AMBIENT:
        try:
            want(village, npc, blk)
        except Exception:
            pass


def steer(village, npc, blk) -> None:  # type: ignore[no-untyped-def]
    """Feet: mid-walk commitments hold (trip/errand/rest with ground to
    cover); everything else re-bids every tick — routine posts included,
    so needs answer mid-block instead of freezing at the post. Trips
    preempt routine by value; commitment-hold gives the hysteresis."""
    from omnisim.systems import desires as D
    dest = npc.comps.get("dest")
    kind = npc.comps.get("dest_kind")
    if dest is not None and kind in ("trip", "errand", "rest") \
            and (npc.x, npc.y) != tuple(dest):
        return  # committed, ground to cover — hold till arrival
    try:
        if D.rest_halt(village, npc, blk) is not None:
            return
    except Exception:
        pass
    try:
        if D.errand_route(village, npc, blk) is not None:
            return
    except Exception:
        pass
    try:  # market, wilds, well — highest value wins, else routine keeps feet
        floor = D.routine_floor()
        best = None
        for bid in D.SCORED:
            try:
                res = bid(village, npc, blk)
            except Exception:
                continue
            if res is not None and res[0] > floor \
                    and (best is None or res[0] > best[0]):
                best = res
        if best is not None:
            from omnisim.systems.schedule import bfs_path, gm_of
            _value, dest, flag = best
            npc.comps["dest"] = dest
            npc.comps["dest_kind"] = "trip"
            npc.comps["path"] = bfs_path(gm_of(village), (npc.x, npc.y), dest)
            npc.comps[flag] = True
            return
    except Exception:
        pass
    try:
        if D.kid_loot(village, npc, blk) is not None:
            return
    except Exception:
        pass
    try:
        D.routine(village, npc, blk)
    except Exception:
        pass
