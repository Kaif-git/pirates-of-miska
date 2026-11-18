"""Seeded P4 stability-event distribution audit, not a universal balance claim."""
from pathlib import Path
import argparse
from collections import Counter
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pirates.duel_system.balance import MATCHUPS, run_fight
from pirates.duel_system.config import load_config


def audit(fights, seed, matchups):
    reports = {}
    for name in matchups:
        first, second = MATCHUPS[name]
        sources, events = Counter(), Counter()
        per_fight = []
        for offset in range(fights):
            result = run_fight(first, second, seed=seed + offset,
                               config=load_config(), mirror=name == "mirror", trace=True)
            trace = result["trace"]
            counts = Counter(row["type"] for row in trace)
            events.update(counts)
            fight_sources = Counter(row["deltas"].get("source") for row in trace
                                    if row["type"] == "STABILITY" and "loss" in row["deltas"])
            sources.update(fight_sources)
            per_fight.append({"seed": seed + offset, "reason": result["reason"], "ticks": result["ticks"],
                              "stability_losses": dict(fight_sources), "falls": counts["FALL"],
                              "rises": counts["RISE"]})
        reports[name] = {"first_bot": first, "second_bot": second, "fights": fights,
                         "event_counts": dict(events), "losses_by_source": dict(sources),
                         "fighters_with_falls": sum(row["falls"] > 0 for row in per_fight),
                         "total_falls": sum(row["falls"] for row in per_fight),
                         "fighters_with_rises": sum(row["rises"] > 0 for row in per_fight),
                         "total_rises": sum(row["rises"] for row in per_fight),
                         "max_falls_in_one_fight": max((row["falls"] for row in per_fight), default=0),
                         "per_fight": per_fight}
    return {"scope": "Seeded 1v1 bot fixtures only; distributions do not prove broad balance or universal winnability.",
            "seed": seed, "config": load_config(), "matchups": reports}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fights", type=int, default=100)
    parser.add_argument("--seed", type=int, default=4100)
    parser.add_argument("--matchup", choices=tuple(MATCHUPS), action="append")
    args = parser.parse_args()
    if args.fights <= 0:
        parser.error("--fights must be positive")
    report = audit(args.fights, args.seed, args.matchup or ["punisher", "spam_heavy", "random", "kite"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({name: {key: value for key, value in row.items() if key not in {"per_fight"}}
                      for name, row in report["matchups"].items()}, indent=2))


if __name__ == "__main__":
    main()
