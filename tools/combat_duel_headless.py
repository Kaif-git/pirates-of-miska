"""Deterministic bounded duel-oracle matrix.

Run from the project root, for example:
    python tools/combat_duel_headless.py --depth 2 --nodes 100000

Scores are model-bounded; this tool does not establish an unbounded
"unbeatable" theorem and does not exercise the live CombatArena adapter.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pirates.combat_duel import (
    DuelDecisionState,
    DuelStats,
    DuelWeapon,
    Duelant,
    OraclePolicy,
    OraclePolicyConfig,
)


def scenarios():
    neutral = DuelStats()
    tired = DuelStats(stamina=25, fatigue=50)
    return (
        ("equal-adjacent", neutral, DuelWeapon(), (0, 0), (1, 0)),
        ("equal-diagonal-contact", neutral, DuelWeapon(), (0, 0), (1, 1)),
        ("equal-tired-adjacent", tired, DuelWeapon(), (0, 0), (1, 0)),
        ("equal-long-reach", neutral, DuelWeapon(reach_tiles=2), (0, 0), (2, 0)),
        ("equal-open-lane-kite", neutral, DuelWeapon(), (0, 0), (4, 0)),
    )


def run_matrix(*, depth=2, node_budget=100_000):
    rows = []
    policy = OraclePolicy(OraclePolicyConfig(depth=depth, node_budget=node_budget))
    for name, stats, weapon, left_pos, right_pos in scenarios():
        left = Duelant(stats, weapon, *left_pos)
        right = Duelant(stats, weapon, *right_pos)
        seats = (
            ("left-first", DuelDecisionState(0, left, right, to_act=0)),
            ("right-first-seat-swapped", DuelDecisionState(0, right, left, to_act=1)),
        )
        searches = []
        for seat_name, state in seats:
            result = policy.analyze(state)
            searches.append((seat_name, result))
            searches_json = {
                "seat": seat_name,
                "score_left_perspective": result.score,
                "action": (asdict(result.action) if result.action else None),
                "principal_variation": [asdict(action)
                                         for action in result.principal_variation],
                "nodes": result.nodes,
                "depth": result.depth,
                "complete": result.complete,
            }
            rows.append({"case": name, **searches_json})
        a, b = searches[0][1], searches[1][1]
        rows.append({
            "case": name,
            "seat_symmetry": {
                "score_negates": a.score == -b.score,
                "actions_match": a.action == b.action,
                "both_complete": a.complete and b.complete,
            },
        })
    return {
        "model": "open-lane two-fighter duel, normalized 0..100 vitality",
        "depth": depth,
        "node_budget": node_budget,
        "proof_scope": "complete rows are exact only to the requested ply depth in this model",
        "unbounded_nonloss_proven": False,
        "all_searches_complete": all(
            row["complete"] for row in rows if "complete" in row),
        "all_seat_symmetry_checks_pass": all(
            all(check.values()) for check in
            (row["seat_symmetry"] for row in rows if "seat_symmetry" in row)),
        "results": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--depth", type=int, default=2)
    parser.add_argument("--nodes", type=int, default=100_000)
    parser.add_argument("--output", help="write JSON report to this path instead of stdout")
    args = parser.parse_args()
    report = run_matrix(depth=args.depth, node_budget=args.nodes)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
