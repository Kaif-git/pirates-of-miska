"""V1 Foundations — The Ledger: traceability queries (GDD 10.2).

The tape (village.trades, capped 500) plus made_today/sold_today already
record every deal. This module answers the player's questions:
price history, top wealthy, production chains, hunger roll-call.
All dynamic — computed from live tape + live pockets, never stored constants.
"""
from __future__ import annotations


def price_history(village, item_id: str, days: int = 7) -> list[dict]:
    """Trades for item_id over the last `days` days, oldest first."""
    try:
        from omnisim.core.clock import day_of
        today = day_of(village.clock_pts)
    except Exception:
        today = 10**9
    out = [t for t in (getattr(village, "trades", []) or [])
           if t.get("item") == item_id and today - int(t.get("day", today)) < days]
    return out


def avg_price(village, item_id: str, days: int = 7) -> float | None:
    hist = price_history(village, item_id, days)
    if not hist:
        return None
    qty = sum(max(1, int(t.get("qty", 1))) for t in hist)
    val = sum(int(t.get("price", 0)) * max(1, int(t.get("qty", 1))) for t in hist)
    return val / max(1, qty)


def price_line(village, item_id: str) -> str:
    """'bread: 6 deals, avg 3.2c (7d)' or 'bread: no sales on tape'."""
    from omnisim.entities.items import item_name
    hist = price_history(village, item_id)
    if not hist:
        return f"{item_name(item_id)}: no sales on tape"
    avg = avg_price(village, item_id) or 0
    return (f"{item_name(item_id)}: {len(hist)} deals, "
            f"avg {avg:.1f}c (7d), last @ {hist[-1].get('price')}c")


def top_wealthy(village, n: int = 5) -> list[tuple[str, int]]:
    """NPC pockets ranked by coin, richest first."""
    rows = []
    for npc in (getattr(village, "npcs", []) or []):
        try:
            coin = int((npc.get("pocket", {}) or {}).get("coin", 0))
            rows.append((npc.name, coin))
        except Exception:
            continue
    rows.sort(key=lambda kv: -kv[1])
    return rows[:max(1, n)]


def production_chain(village, item_id: str, limit: int = 5) -> list[str]:
    """Who made it lately (made_today + tape sellers)."""
    out: list[str] = []
    try:
        rec = (getattr(village, "made_today", {}) or {}).get(item_id, {})
        for name in (rec.get("by", []) or [])[:limit]:
            out.append(f"made by {name}")
    except Exception:
        pass
    for t in price_history(village, item_id)[-limit:]:
        out.append(f"{t.get('seller')} → {t.get('buyer')} @ {t.get('price')}c")
    return out


def hungry_rollcall(village) -> list[str]:
    """NPCs at hunger >= 70, hungriest first."""
    rows = []
    for npc in (getattr(village, "npcs", []) or []):
        try:
            h = float(npc.get("hunger", 0))
            if h >= 70:
                rows.append((npc.name, h))
        except Exception:
            continue
    rows.sort(key=lambda kv: -kv[1])
    return [name for name, _ in rows]


def ledger_lines(village) -> list[str]:
    """Board-ready digest: prices, wealth, hunger."""
    lines = ["Ledger (tape, 7d):"]
    seen = []
    for t in (getattr(village, "trades", []) or [])[::-1]:
        if t.get("item") not in seen:
            seen.append(t.get("item"))
        if len(seen) >= 4:
            break
    for item_id in seen:
        lines.append("  " + price_line(village, item_id))
    wealthy = top_wealthy(village, 3)
    if wealthy:
        lines.append("Richest: " + ", ".join(f"{n} {c}c" for n, c in wealthy))
    hungry = hungry_rollcall(village)
    if hungry:
        lines.append(f"Hungry ({len(hungry)}): " + ", ".join(hungry[:5]))
    return lines
