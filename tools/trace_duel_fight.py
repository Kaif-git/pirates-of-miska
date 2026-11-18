"""Print a compact event timeline for one seeded M10 calibration fight."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pirates.duel_system.balance import run_fight


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first", default="ScriptedPunisherBot")
    parser.add_argument("--second", default="Ronin")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--level-gap", type=int, default=0)
    args = parser.parse_args()
    print(json.dumps(run_fight(args.first, args.second, seed=args.seed,
                               level_gap=args.level_gap, trace=True), indent=2))


if __name__ == "__main__":
    main()
