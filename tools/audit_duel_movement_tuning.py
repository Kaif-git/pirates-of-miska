"""Compare seeded duel diagnostics before/after movement-timing tuning.

This is a scoped regression signal, not a universal balance or winnability proof.
Run from the project root; results include the full configuration fingerprints.
"""
from pathlib import Path
import argparse
import json
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pirates.duel_system.balance import run_matchup
from pirates.duel_system.config import load_config


def summarize(first_bot, fights, seed, config):
    report = run_matchup(first_bot, "Ronin", fights=fights, seed=seed, config=config)
    results = report["results"]
    return {
        "first_bot": first_bot,
        "fights": fights,
        "wins": report["wins"],
        "losses": report["losses"],
        "draws": report["draws"],
        "first_win_rate": report["win_rate"],
        "end_reasons": report["end_reasons"],
        "median_ticks": statistics.median(item["ticks"] for item in results),
        "median_total_decisions": report["median_total_decisions"],
        "first_actions": report["first_actions"],
        "second_actions": report["second_actions"],
        "timeouts": sum(item["reason"] == "timeout" for item in results),
        "config_sha256": report["config_sha256"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fights", type=int, default=40)
    parser.add_argument("--seed", type=int, default=2300)
    parser.add_argument("--bots", nargs="+", default=[
        "ScriptedPunisherBot", "SpamLightBot", "SpamHeavyBot", "KiteBot", "RandomBot",
    ])
    args = parser.parse_args()
    candidate = load_config()
    baseline = json.loads(json.dumps(candidate))
    baseline["actions"]["DASH"]["per_tile"] = 2
    baseline["actions"]["DASH_LIGHT"]["per_tile"] = 2
    baseline["actions"]["RUN"]["per_tile"] = 4
    result = {
        "scope": "Seeded 1v1 Ronin-fixture comparison only; not universal balance/winnability proof.",
        "seed_range": [args.seed, args.seed + args.fights - 1],
        "fights_per_bot": args.fights,
        "baseline_timing": {key: baseline["actions"][key]["per_tile"] for key in ("DASH", "DASH_LIGHT", "RUN")},
        "candidate_timing": {key: candidate["actions"][key]["per_tile"] for key in ("DASH", "DASH_LIGHT", "RUN")},
        "same_seed_comparisons": {},
    }
    for bot in args.bots:
        before = summarize(bot, args.fights, args.seed, baseline)
        after = summarize(bot, args.fights, args.seed, candidate)
        result["same_seed_comparisons"][bot] = {
            "baseline": before,
            "candidate": after,
            "win_rate_delta": after["first_win_rate"] - before["first_win_rate"],
            "median_tick_delta": after["median_ticks"] - before["median_ticks"],
            "decision_delta": after["median_total_decisions"] - before["median_total_decisions"],
        }
        print(json.dumps({bot: result["same_seed_comparisons"][bot]}), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
