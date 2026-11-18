"""Audit deterministic opening choices across authored profiles and emotion states.

This is a controlled opening-distribution check, not full-fight balance evidence.
Run from the project root. Every profile shares the same geometry and paired seeds.
"""
from collections import Counter
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pirates.duel_system.ai import choose_action
from pirates.duel_system.config import load_config
from pirates.duel_system.session import DuelSession


def observe_openings(profile, seeds, pressured):
    counts = Counter()
    samples = []
    for seed in seeds:
        session = DuelSession.new(seed=seed, archetype=profile, guide_mode="off")
        enemy = session.enemy
        # Hold geometry, resources, and emotion conditions fixed within each arm.
        enemy.anger = 0
        enemy.morale = 70
        enemy.pain = 0
        enemy.nervousness = 0
        enemy.fatigue = 0
        if pressured:
            enemy.morale, enemy.pain, enemy.nervousness = 10, 60, 0.6
        decision = choose_action(session.state, enemy.id)
        counts[decision.category] += 1
        samples.append({"seed": seed, "category": decision.category})
    total = len(seeds)
    return {
        "count": total,
        "action_counts": dict(sorted(counts.items())),
        "action_shares": {key: value / total for key, value in sorted(counts.items())},
        "sample_first_10": samples[:10],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fights", type=int, default=200)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    if args.fights < 2:
        parser.error("--fights must be at least 2")
    config = load_config()
    seeds = list(range(args.seed, args.seed + args.fights))
    result = {
        "scope": "Paired opening-decision distributions only; no full-fight balance or universal-winnability claim.",
        "seed_range": [seeds[0], seeds[-1]],
        "paired_seeds_per_profile_and_emotion": args.fights,
        "emotion_arms": {"calm": {"anger": 0, "morale": 70, "pain": 0, "nervousness": 0},
                         "pressured": {"anger": 0, "morale": 10, "pain": 60, "nervousness": 0.6}},
        "profiles": {},
    }
    for profile in config["personality"]["roster"]:
        result["profiles"][profile] = {
            "calm": observe_openings(profile, seeds, False),
            "pressured": observe_openings(profile, seeds, True),
        }
        print(json.dumps({profile: {
            mode: result["profiles"][profile][mode]["action_counts"]
            for mode in ("calm", "pressured")
        }}, ensure_ascii=False))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
