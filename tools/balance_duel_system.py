"""Seeded COMBAT SPEC balance report. Default 2000 real fights per matchup."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pirates.duel_system.balance import MATCHUPS, assess, run_matchup
from pirates.duel_system.config import load_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fights", type=int)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--matchup", choices=tuple(MATCHUPS), action="append")
    args = parser.parse_args()
    cfg = load_config(args.config)
    reports = {}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for name in args.matchup or MATCHUPS:
        first, second = MATCHUPS[name]
        report = run_matchup(first, second, fights=args.fights or cfg["balance"]["fights_per_matchup"],
                              seed=args.seed, config=cfg, mirror=name == "mirror",
                              level_gap=cfg["balance"]["level_gap"] if name == "level_gap" else 0)
        reports[name] = report
        data = {"reports": reports, "assessment": assess(reports, cfg), "config": cfg}
        args.output.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(json.dumps({"matchup": name, **{k: v for k, v in report.items() if k != "results"}}), flush=True)
    print(json.dumps(data["assessment"]), flush=True)


if __name__ == "__main__":
    main()
