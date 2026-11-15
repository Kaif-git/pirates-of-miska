"""Step 5: memory and relationships.

Per-NPC bounded memory: souls (up to 32: trust, familiarity, deals,
cheats, favors), quality beliefs (up to 64 (seller,good) pairs, est_q
EMA), life events (up to 64, salience decays ~5pct/day). Price memory
(asks/wills/paid/lots/tape) already exists in haggle.py + pricing.py.

Uses: partner choice ranks sellers by price/(est_q_value*(0.7+0.3*trust))
with a distance term (A5: fence deals need adjacency); aid/gifts scale
with trust x generosity x pain; favors are logged below-fair distress
sales, never a second ledger (A9: tabs stay the settlement system).

Quality beliefs start at the market mean and update 0.3 per
observation. Seller reputation (village level, lazy) is the
familiarity-weighted mean of buyers' est_q and feeds rep_premium in the
Step-2 ask (1.0 until Step 5). Gossip swaps top-3 beliefs at 0.5 weight,
gated by sociability.
"""
from __future__ import annotations

SOUL_CAP = 32
BELIEF_CAP = 64
EVENT_CAP = 64
EST_ALPHA = 0.3
GOSSIP_WEIGHT = 0.5


def _econ():
    try:
        from omnisim.systems.production import _load as _ld
        return _ld("economy") or {}
    except Exception:
        return {}


def on():
    try:
        return bool((_econ().get("flags", {}) or {}).get("step_5", False))
    except Exception:
        return False


def _day_of(village):
    try:
        from omnisim.core.clock import day_of as _d
        return int(_d(village.clock_pts))
    except Exception:
        try:
            return int(getattr(village, "clock_pts", 0)) // 144000
        except Exception:
            return 0


def _name(e):
    try:
        return str(getattr(e, "name", "?"))
    except Exception:
        return "?"


def souls_of(npc):
    """Soul ledger, bounded at SOUL_CAP (evict lowest salience)."""
    try:
        m = npc.comps.get("souls") if hasattr(npc, "comps") else None
    except Exception:
        return {}
    return dict(m) if isinstance(m, dict) else {}


def beliefs_of(npc):
    """Quality beliefs keyed seller|good, bounded at BELIEF_CAP."""
    try:
        m = npc.comps.get("qbeliefs") if hasattr(npc, "comps") else None
    except Exception:
        return {}
    return dict(m) if isinstance(m, dict) else {}


def _belief_key(seller, good):
    return "%s|%s" % (seller, good)


def market_mean_q(village, good):
    """Prior for unseen (seller, good): mean q on recent deals, else 1."""
    try:
        qs = [float(t.get("q", 1) or 1) for t in (getattr(village, "trades", []) or [])
              if t.get("item") == good]
        if qs:
            return sum(qs) / len(qs)
    except Exception:
        pass
    return 1.0


def quality_belief_of(npc, seller, good, village=None):
    """EMA of observed quality; prior = market mean."""
    try:
        b = beliefs_of(npc).get(_belief_key(seller, good))
        if isinstance(b, dict) and b.get("est_q") is not None:
            return float(b["est_q"])
    except Exception:
        pass
    try:
        if village is not None:
            return float(market_mean_q(village, good))
    except Exception:
        pass
    return 1.0


def trust_of(npc, other):
    try:
        s = souls_of(npc).get(str(other))
        if isinstance(s, dict) and s.get("trust") is not None:
            return max(-1.0, min(1.0, float(s["trust"])))
    except Exception:
        pass
    return 0.0


def familiarity_of(npc, other):
    try:
        s = souls_of(npc).get(str(other))
        if isinstance(s, dict) and s.get("familiarity") is not None:
            return max(0.0, min(1.0, float(s["familiarity"])))
    except Exception:
        pass
    return 0.0


def _touch_soul(npc, other, village=None):
    """Get-or-create a soul row, evicting the lowest-salience row at cap."""
    try:
        m = npc.comps.get("souls")
        if not isinstance(m, dict):
            m = {}
            npc.comps["souls"] = m
        s = m.get(str(other))
        if not isinstance(s, dict):
            if len(m) >= SOUL_CAP:
                drop = min(m.items(), key=lambda kv: (
                    float((kv[1] or {}).get("familiarity", 0))
                    + 0.1 * int((kv[1] or {}).get("deals", 0))))[0]
                m.pop(drop, None)
            s = {"trust": 0.0, "familiarity": 0.0,
                 "last_seen": _day_of(village) if village is not None else 0,
                 "deals": 0, "cheats": 0,
                 "favors_given": 0, "favors_received": 0}
            m[str(other)] = s
        return s
    except Exception:
        return {}


def remember_deal(village, buyer, seller, good, q=1,
                  price=1, fair=None, observed_q=None, cheat=False):
    """Log one settled deal into both souls' memories."""
    try:
        bname, sname = _name(buyer), _name(seller)
        if bname == sname:
            return
        day = _day_of(village)
        try:
            bmem = buyer.comps.get("qbeliefs")
            if not isinstance(bmem, dict):
                bmem = {}
                buyer.comps["qbeliefs"] = bmem
            key = _belief_key(sname, str(good))
            row = bmem.get(key)
            if not isinstance(row, dict):
                if len(bmem) >= BELIEF_CAP:
                    drop = min(bmem.items(),
                               key=lambda kv: int((kv[1] or {}).get("n", 0)))[0]
                    bmem.pop(drop, None)
                row = {"est_q": market_mean_q(village, str(good)), "n": 0}
                bmem[key] = row
            obs = float(observed_q if observed_q is not None else (q or 1))
            row["est_q"] = float(row.get("est_q", 1.0)) + EST_ALPHA * (obs - float(row.get("est_q", 1.0)))
            row["n"] = int(row.get("n", 0)) + 1
        except Exception:
            pass
        pairs = ((buyer, sname), (seller, bname))
        for npc, other in pairs:
            try:
                s = _touch_soul(npc, other, village)
                if not s:
                    continue
                s["deals"] = int(s.get("deals", 0)) + 1
                s["familiarity"] = min(1.0, float(s.get("familiarity", 0)) + 0.05)
                s["last_seen"] = day
                if cheat and npc is buyer:
                    s["cheats"] = int(s.get("cheats", 0)) + 1
                    s["trust"] = max(-1.0, float(s.get("trust", 0)) - 0.3)
                elif npc is buyer:
                    try:
                        _f = float(fair) if fair else float(price)
                        _ok = float(price) <= _f * 1.1
                    except Exception:
                        _ok = True
                    bump = 0.05 if _ok else -0.05
                    s["trust"] = max(-1.0, min(1.0, float(s.get("trust", 0)) + bump))
                else:
                    s["trust"] = max(-1.0, min(1.0, float(s.get("trust", 0)) + 0.03))
            except Exception:
                pass
        if cheat:
            try:
                log_event(buyer, village, "cheated by %s" % sname, -0.5, subject=sname)
            except Exception:
                pass
    except Exception:
        pass


def log_event(npc, village, kind, salience, subject=""):
    """Bounded life-event ledger; salience decays ~5pct/day."""
    try:
        evs = npc.comps.get("mevents")
        if not isinstance(evs, list):
            evs = []
            npc.comps["mevents"] = evs
        evs.append({"day": _day_of(village), "kind": str(kind),
                    "salience": float(salience), "subject": str(subject)})
        if len(evs) > EVENT_CAP:
            indexed = []
            for i, e in enumerate(evs):
                try:
                    sal = float(e.get("salience", 0)) if isinstance(e, dict) else 0.0
                except Exception:
                    sal = 0.0
                indexed.append((sal, i))
            indexed.sort()
            keep_idx = set(i for _, i in indexed[len(indexed) - EVENT_CAP:])
            evs[:] = [e for i, e in enumerate(evs) if i in keep_idx]
    except Exception:
        pass


def decay_memories(village):
    """Dawn decay: prune salience-spent event rows."""
    try:
        day = _day_of(village)
        for npc in list(getattr(village, "npcs", []) or []):
            try:
                if npc.comps.get("dead"):
                    continue
                evs = npc.comps.get("mevents")
                if isinstance(evs, list):
                    keep = []
                    for e in evs:
                        if not isinstance(e, dict):
                            continue
                        age = max(0, day - int(e.get("day", day)))
                        if float(e.get("salience", 0)) * (0.95 ** age) > 0.05:
                            keep.append(e)
                    npc.comps["mevents"] = keep[-EVENT_CAP:]
            except Exception:
                continue
    except Exception:
        pass


def rep_of(village, seller, good):
    """Seller reputation: familiarity-weighted mean of buyers' est_q."""
    try:
        num, den = 0.0, 0.0
        for npc in getattr(village, "npcs", []) or []:
            try:
                if npc.comps.get("dead") or _name(npc) == str(seller):
                    continue
                key = _belief_key(str(seller), str(good))
                b = beliefs_of(npc).get(key)
                if not isinstance(b, dict) or int(b.get("n", 0)) <= 0:
                    continue
                w = 0.2 + 0.8 * familiarity_of(npc, str(seller))
                num += float(b.get("est_q", 1.0)) * w
                den += w
            except Exception:
                continue
        if den > 0:
            return num / den
    except Exception:
        pass
    return 1.0


def rep_premium(village, seller, good):
    """1 + 0.5 x (rep - market_mean_q)/4. Neutral 1.0 until Step 5."""
    if not on():
        return 1.0
    try:
        mean = float(market_mean_q(village, str(good)))
    except Exception:
        mean = 1.0
    try:
        rep = float(rep_of(village, _name(seller), str(good)))
        return max(0.5, 1.0 + 0.5 * (rep - mean) / 4.0)
    except Exception:
        return 1.0


def est_q_value(q):
    """Quality worth mirrors quality_mult 1:1 2:1.5 3:2 4:3 5:5."""
    try:
        table = {1: 1.0, 2: 1.5, 3: 2.0, 4: 3.0, 5: 5.0}
        qi = max(1, min(5, int(round(float(q)))))
        return table[qi]
    except Exception:
        return 1.0


def partner_score(buyer, seller_name, good, price, village=None, dist=0.0):
    """Lower wins: price per trusted quality + walk-cost term (A5)."""
    try:
        est = quality_belief_of(buyer, str(seller_name), str(good), village)
        trust = trust_of(buyer, str(seller_name))
        denom = est_q_value(est) * (0.7 + 0.3 * trust)
        if denom <= 0:
            denom = 0.7
        score = float(price) / denom
        try:
            score += float(dist) * 0.1
        except Exception:
            pass
        return score
    except Exception:
        return float(price)


def gossip(a, b, village=None):
    """Swap top-3 quality beliefs at 0.5 weight (call sites gate on sociability)."""
    try:
        for src, dst in ((a, b), (b, a)):
            try:
                sb = beliefs_of(src)
                if not sb:
                    continue
                ranked = sorted(sb.items(),
                                key=lambda kv: int((kv[1] or {}).get("n", 0)),
                                reverse=True)[:3]
                if not ranked:
                    continue
                dm = dst.comps.get("qbeliefs")
                if not isinstance(dm, dict):
                    dm = {}
                    dst.comps["qbeliefs"] = dm
                for key, row in ranked:
                    if not isinstance(row, dict):
                        continue
                    cur = dm.get(key)
                    if not isinstance(cur, dict):
                        if len(dm) >= BELIEF_CAP:
                            continue
                        try:
                            prior = market_mean_q(village, key.split("|", 1)[1]) if village is not None and "|" in key else 1.0
                        except Exception:
                            prior = 1.0
                        cur = {"est_q": prior, "n": 0}
                        dm[key] = cur
                    cur["est_q"] = float(cur.get("est_q", 1.0)) + GOSSIP_WEIGHT * (float(row.get("est_q", 1.0)) - float(cur.get("est_q", 1.0)))
            except Exception:
                continue
    except Exception:
        pass


def favor_ok(seller, buyer, price, fair, village=None):
    """May this seller cut a distress price? Trust x generosity x pain."""
    if not on():
        return False
    try:
        from omnisim.systems.mood import _t as _trait
        gen = _trait(seller, "generosity")
    except Exception:
        gen = 0.5
    try:
        trust = (trust_of(seller, _name(buyer)) + 1.0) / 2.0
    except Exception:
        trust = 0.5
    try:
        from omnisim.systems.needs3 import level_of as _lvl
        pain = max(float(_lvl(buyer, c)) for c in ("hunger", "thirst", "cold")) / 100.0
    except Exception:
        try:
            pain = float(buyer.get("hunger", 0)) / 100.0
        except Exception:
            pain = 0.0
    try:
        cut = 1.0 - float(price) / max(1.0, float(fair))
    except Exception:
        return False
    if cut <= 0:
        return True
    return (trust * gen * (0.3 + pain)) >= cut * 0.5


def log_favor(seller, buyer, village=None):
    try:
        s = _touch_soul(seller, _name(buyer), village)
        if s:
            s["favors_given"] = int(s.get("favors_given", 0)) + 1
        t = _touch_soul(buyer, _name(seller), village)
        if t:
            t["favors_received"] = int(t.get("favors_received", 0)) + 1
            t["trust"] = max(-1.0, min(1.0, float(t.get("trust", 0)) + 0.1))
    except Exception:
        pass
