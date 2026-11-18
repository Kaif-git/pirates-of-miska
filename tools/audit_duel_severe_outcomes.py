"""Seeded descriptive P6 severe-outcome sample; not a broad balance claim."""
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


P6_EVENTS = {"SEVER", "WEAPON_STRESS", "WEAPON_DROP", "WEAPON_BREAK", "WEAPON_PICKUP",
             "EYE_INJURY", "DECAPITATION", "INCAPACITATED"}


def audit_matchup(name, *, fights, seed, config):
    first, second = MATCHUPS[name]
    rows = [run_fight(first, second, seed=seed+i, config=config,
                      level_gap=config["balance"]["level_gap"] if name == "level_gap" else 0,
                      mirror=name == "mirror", swap=name == "mirror" and i % 2 == 1,
                      trace=True) for i in range(fights)]
    events = Counter()
    by_actor = defaultdict(Counter)
    per_fight = Counter()
    stress = defaultdict(list)
    contact_wear = Counter()
    for row in rows:
        seen = set()
        for event in row["trace"]:
            kind, data = event["type"], event["deltas"]
            if kind in P6_EVENTS:
                events[kind] += 1
                by_actor[event.get("actor") or "unknown"][kind] += 1
                seen.add(kind)
            if kind == "WEAPON_STRESS":
                material, contact = data["material"], data["contact"]
                stress[material].append(data["condition_after"])
                contact_wear[f"{material}:{contact}"] += 1
        per_fight.update(seen)
    return {
        "first_bot": first,
        "second_bot": second,
        "fights": fights,
        "wins_first": sum(row["winner"] == "first" for row in rows),
        "wins_second": sum(row["winner"] == "second" for row in rows),
        "draws_or_timeouts": sum(row["winner"] is None for row in rows),
        "terminal_reasons": dict(Counter(row["reason"] for row in rows)),
        "p6_event_counts": dict(events),
        "fights_with_event": dict(per_fight),
        "p6_events_by_actor": {actor: dict(counts) for actor, counts in by_actor.items()},
        "weapon_stress_by_material_contact": dict(contact_wear),
        "weapon_condition_after_contact_median_by_material": {
            material: statistics.median(values) for material, values in stress.items()},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fights", type=int, default=100)
    parser.add_argument("--seed", type=int, default=7300)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--matchup", choices=tuple(MATCHUPS), action="append")
    args = parser.parse_args()
    if args.fights <= 0:
        parser.error("--fights must be positive")
    config = load_config()
    reports = [audit_matchup(name, fights=args.fights, seed=args.seed, config=config)
               for name in (args.matchup or ["punisher", "spam_light", "random", "mirror"])]
    payload = {"scope": "descriptive seeded P6 event sample; controlled tests establish reachability; not broad balance",
               "seed": args.seed, "config_sha256": fingerprint(config), "reports": reports}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
