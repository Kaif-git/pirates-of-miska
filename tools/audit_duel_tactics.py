"""Seeded descriptive P5 action-use and outcome sample; not a universal balance test."""
from collections import Counter, defaultdict
from pathlib import Path
import argparse
import json
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pirates.duel_system.balance import MATCHUPS, fingerprint, run_fight
from pirates.duel_system.config import load_config


TACTICS = {"FEINT", "SHOVE", "DISENGAGE", "THRUST", "DISARM", "ADRENALINE"}


def audit_matchup(name, *, fights, seed, config):
    first, second = MATCHUPS[name]
    rows = [run_fight(first, second, seed=seed+i, config=config,
                      level_gap=config["balance"]["level_gap"] if name == "level_gap" else 0,
                      mirror=name == "mirror", swap=name == "mirror" and i % 2 == 1,
                      trace=True) for i in range(fights)]
    action_counts, event_counts, outcome_counts = Counter(), Counter(), Counter()
    action_counts_by_actor = defaultdict(Counter)
    outcomes_by_actor = defaultdict(Counter)
    guard_stamina_drained = fatigue_debt_due = fatigue_debt_applied = hand_hits = falls = 0
    for row in rows:
        for event in row["trace"]:
            kind, deltas = event["type"], event["deltas"]
            event_counts[kind] += 1
            if kind == "ACTION_START" and deltas.get("kind") in TACTICS:
                action_counts[deltas["kind"]] += 1
                action_counts_by_actor[event.get("actor") or "unknown"][deltas["kind"]] += 1
            elif kind == "HIT" and event.get("zone") == "HAND":
                hand_hits += 1
            elif kind == "FALL":
                falls += 1
            elif kind == "FEINT":
                outcome_counts[f"FEINT:{deltas.get('outcome', 'UNKNOWN')}"] += 1
                outcomes_by_actor[event.get("actor") or "unknown"][f"FEINT:{deltas.get('outcome', 'UNKNOWN')}"] += 1
                guard_stamina_drained += deltas.get("guard_stamina_drained", 0)
            elif kind == "SHOVE":
                outcome_counts[f"SHOVE:{deltas.get('outcome', 'UNKNOWN')}"] += 1
                outcomes_by_actor[event.get("actor") or "unknown"][f"SHOVE:{deltas.get('outcome', 'UNKNOWN')}"] += 1
            elif kind == "DISARM":
                outcome_counts[f"DISARM:{deltas.get('outcome', 'UNKNOWN')}"] += 1
                outcomes_by_actor[event.get("actor") or "unknown"][f"DISARM:{deltas.get('outcome', 'UNKNOWN')}"] += 1
            elif kind == "ADRENALINE":
                outcome_counts[f"ADRENALINE:{deltas.get('outcome', 'UNKNOWN')}"] += 1
                outcomes_by_actor[event.get("actor") or "unknown"][f"ADRENALINE:{deltas.get('outcome', 'UNKNOWN')}"] += 1
                fatigue_debt_due += deltas.get("debt_due", 0) if deltas.get("outcome") == "ACTIVATED" else 0
                fatigue_debt_applied += deltas.get("debt_applied", 0)
    return {
        "first_bot": first,
        "second_bot": second,
        "fights": fights,
        "wins_first": sum(row["winner"] == "first" for row in rows),
        "wins_second": sum(row["winner"] == "second" for row in rows),
        "draws_or_timeouts": sum(row["winner"] is None for row in rows),
        "median_ticks": statistics.median(row["ticks"] for row in rows),
        "median_decisions": statistics.median(row["decisions"] for row in rows),
        "action_use": dict(action_counts),
        "action_use_by_actor": {actor: dict(counts) for actor, counts in action_counts_by_actor.items()},
        "outcomes": dict(outcome_counts),
        "outcomes_by_actor": {actor: dict(counts) for actor, counts in outcomes_by_actor.items()},
        "hand_hits": hand_hits,
        "falls": falls,
        "guard_stamina_drained_by_feints": round(guard_stamina_drained, 3),
        "adrenaline_fatigue_debt_due": round(fatigue_debt_due, 3),
        "adrenaline_fatigue_debt_applied": round(fatigue_debt_applied, 3),
        "adrenaline_fatigue_debt_pending_at_end": round(
            sum(row["adrenaline_debt_pending"] for row in rows), 3),
        "event_counts": dict(event_counts),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fights", type=int, default=100)
    parser.add_argument("--seed", type=int, default=6100)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--matchup", choices=tuple(MATCHUPS), action="append")
    args = parser.parse_args()
    if args.fights <= 0:
        parser.error("--fights must be positive")
    config = load_config()
    reports = [audit_matchup(name, fights=args.fights, seed=args.seed, config=config)
               for name in (args.matchup or ["punisher", "spam_light", "random"])]
    payload = {"scope": "descriptive seeded P5 prototype sample; not a broad balance claim",
               "seed": args.seed, "config_sha256": fingerprint(config),
               "ai_tactic_scores": config["tactics"]["ai_scores"], "reports": reports}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
