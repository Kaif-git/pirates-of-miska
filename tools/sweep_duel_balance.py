"""Small reproducible Ronin-fixture sweep for M10 calibration before the full gate."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pirates.duel_system.balance import run_matchup
from pirates.duel_system.config import load_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fights", type=int, default=80)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--multipliers", type=float, nargs="+", default=[1, 1.2, 1.5, 2])
    parser.add_argument("--stat-axes", choices=("strength", "speed", "accuracy"), nargs="+",
                        default=["strength", "speed", "accuracy"])
    parser.add_argument("--patience-weights", type=float, nargs="+", default=[0.1])
    parser.add_argument("--reading-weights", type=float, nargs="+", default=[0.6])
    parser.add_argument("--rage-weights", type=float, nargs="+", default=[1.3])
    parser.add_argument("--bandage-bases", type=float, nargs="+", default=[-20])
    parser.add_argument("--light-powers", type=float, nargs="+", default=[1])
    parser.add_argument("--weapon-edges", type=float, nargs="+", default=[4])
    parser.add_argument("--deep-thresholds", type=float, nargs="+", default=[16])
    parser.add_argument("--bots", nargs="+", default=["ScriptedPunisherBot", "SpamLightBot", "SpamHeavyBot"])
    args = parser.parse_args()
    baseline = load_config()
    results = []
    for multiplier in args.multipliers:
        for patience in args.patience_weights:
            for reading in args.reading_weights:
                for rage in args.rage_weights:
                  for bandage in args.bandage_bases:
                    for light_power in args.light_powers:
                      for weapon_edge in args.weapon_edges:
                        for deep in args.deep_thresholds:
                                config = load_config()
                                for stat in args.stat_axes:
                                    config["fixtures"]["ronin"][stat] = baseline["fixtures"]["ronin"][stat] * multiplier
                                config["ronin"]["weights"]["patience"] = patience
                                config["ronin"]["weights"]["reading"] = reading
                                config["ronin"]["weights"]["rage"] = rage
                                config["ronin"]["base"]["BANDAGE"] = bandage
                                config["actions"]["LIGHT"]["power"] = light_power
                                config["actions"]["DASH_LIGHT"]["power"] = light_power
                                config["gear"]["weapon"]["edge"] = weapon_edge
                                config["wound_net"]["deep"] = deep
                                for bot in args.bots:
                                    report = run_matchup(bot, "Ronin", fights=args.fights, seed=args.seed, config=config)
                                    results.append({"ronin_stat_multiplier": multiplier, "patience_weight": patience,
                                                    "reading_weight": reading,
                                                    "rage_weight": rage,
                                                    "bandage_base": bandage, "light_power": light_power,
                                                    "weapon_edge": weapon_edge, "deep_threshold": deep, **{
                                        key: report[key] for key in ("first_bot", "fights", "wins", "losses", "win_rate",
                                                                      "median_player_decisions", "median_total_decisions",
                                                                      "end_reasons", "config_sha256")}})
                                    print(json.dumps(results[-1]), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
