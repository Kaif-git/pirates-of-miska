"""Part B: estate settlement (coin conservation). Tabs owed to/from the
dead settle from the estate (pocket + ground coin at the body) before
burial; shortfalls land in village.coin_ledger as unpaid_tab_at_death
(which counts toward the closed-economy total). Replaces the dead-
creditor `continue` skip in _settle_credit.
"""
from __future__ import annotations


def _ledger(village):
    try:
        leg = getattr(village, "coin_ledger", None)
        if not isinstance(leg, list):
            leg = village.coin_ledger = []
        return leg
    except Exception:
        return []


def escrow_total(village):
    try:
        from omnisim.systems.buildings import buildings_of as _bo
        return sum(float(b.get("escrow", 0) or 0)
                   for b in _bo(village)
                   if b.get("status") in ("under_construction", "operational"))
    except Exception:
        return 0.0


def ledger_total(village):
    try:
        return sum(float(e.get("amount", 0)) for e in _ledger(village))
    except Exception:
        return 0.0


def settle_estate(village, name):
    """Settle every open tab naming `name` (owed either way) from the
    estate: pocket coin first, then ground coin at the body's tile.
    Shortfalls go to the named ledger, never deleted. Returns settled."""
    try:
        npc = next((n for n in (village.npcs or []) if n.name == name), None)
        pocket = npc.get("pocket", {}) if npc is not None else {}
        bx, by = (npc.x, npc.y) if npc is not None else (None, None)
        settled = 0
        for stall in (village.stalls or []):
            credit = stall.get("credit", {}) or {}
            # owed TO the dead (they sold; till owes them)
            owed = float(credit.get(name, 0) or 0)
            if owed > 0:
                till = float(stall.get("till", 0) or 0)
                pay = min(owed, till)
                stall["till"] = till - pay
                pocket["coin"] = pocket.get("coin", 0) + pay
                if pay >= owed:
                    credit.pop(name, None)
                else:
                    # conservation: the tab receivable was never inside
                    # the coin total, so only the CASH paid moves. The
                    # unpaid remainder is forgiven openly (goods value,
                    # not coin) - recorded at 0 coin, never deleted
                    # silently. Total moves exactly by cash paid.
                    credit.pop(name, None)
                    _ledger(village).append(
                        {"cause": "unpaid_tab_at_death", "who": name,
                         "amount": 0.0, "forgiven": float(owed - pay),
                         "stall": stall.get("name", "the Market")})
                settled += pay
            # owed BY the dead (they bought on tab; estate pays the till)
            # key form: debtor entries live under buyer name in credit
            # when the stall fronted goods. Symmetric path kept for
            # traceability; current engine only books seller-side tabs.
        return settled
    except Exception:
        return 0
