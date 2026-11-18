"""Save-free audit of effective Speed, rounded phase times, and breakpoints."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pirates.duel_system.config import load_config
from pirates.duel_system.session import DuelSession
from pirates.duel_system.stats import effective_stats, phase_ticks


def measure(fighter, config, *, tick=0):
    eff = effective_stats(fighter, config, tick)
    actions = config["actions"]
    weight = fighter.equipment.total_weight
    def ticks(action, phase, *, recovery=False):
        base = actions[action].get(phase, 0)
        return phase_ticks(base, eff.speed, config,
                           recovery_weight=weight if recovery else 0)
    def exact_ticks(action, phase, *, recovery=False):
        base = actions[action].get(phase, 0)
        multiplier = (1 + config["gear"]["recovery_per_weight"]*weight) if recovery else 1
        return base*config["time"]["speed_reference"] / eff.speed * multiplier
    light_parts = (ticks("LIGHT", "windup"), ticks("LIGHT", "active"),
                   ticks("LIGHT", "recovery", recovery=True))
    heavy_parts = (ticks("HEAVY", "windup"), ticks("HEAVY", "active"),
                   ticks("HEAVY", "recovery", recovery=True))
    return {
        "base_speed": fighter.speed,
        "effective_speed": eff.speed,
        "effective_accuracy": eff.accuracy,
        "fatigue": fighter.fatigue,
        "light_unrounded_windup": exact_ticks("LIGHT", "windup"),
        "light_windup": ticks("LIGHT", "windup"),
        "light_active": ticks("LIGHT", "active"),
        "light_recovery": ticks("LIGHT", "recovery", recovery=True),
        "light_ready_after": sum(light_parts),
        "heavy_unrounded_windup": exact_ticks("HEAVY", "windup"),
        "heavy_windup": ticks("HEAVY", "windup"),
        "heavy_active": ticks("HEAVY", "active"),
        "heavy_recovery": ticks("HEAVY", "recovery", recovery=True),
        "heavy_ready_after": sum(heavy_parts),
        "guard_raise": ticks("GUARD", "windup"),
        "dash_unrounded_per_tile": exact_ticks("DASH", "per_tile"),
        "dash_per_tile": ticks("DASH", "per_tile"),
        "run_unrounded_per_tile": exact_ticks("RUN", "per_tile"),
        "run_per_tile": ticks("RUN", "per_tile"),
        "walk_per_tile": ticks("WALK", "per_tile"),
        "parry_window": max(config["guard"]["parry_min_window"], round(
            config["guard"]["parry_speed_coef"] * eff.speed
            + config["guard"]["parry_acc_coef"] * eff.accuracy)),
    }


def breakpoints(config):
    result = {}
    ref = config["time"]["speed_reference"]
    for action in ("LIGHT", "HEAVY", "GUARD", "DASH", "RUN", "WALK"):
        table = config["actions"][action]
        phases = ([("windup", table.get("windup", 0), 1.0)] if "windup" in table else [])
        if "per_tile" in table:
            phases.append(("per_tile", table["per_tile"], 1.0))
        if "recovery" in table and table["recovery"]:
            phases.append(("recovery", table["recovery"],
                           1 + config["gear"]["recovery_per_weight"]
                           * (config["gear"]["weapon"]["weight"])))
        for name, base, multiplier in phases:
            if not base:
                continue
            result[f"{action}.{name}"] = {
                "formula": "ceil(base * speed_reference / effective_speed * recovery_weight_multiplier)",
                "recovery_weight_multiplier": multiplier,
                "effective_speed_for_at_most_ticks": {
                    str(tick): base * ref * multiplier / tick
                    for tick in range(1, max(2, int(base*ref*multiplier)+1))
                },
            }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--speeds", type=float, nargs="+", default=[5, 8, 10, 12, 14, 16, 18, 20, 22, 25, 30, 40])
    args = parser.parse_args()
    session = DuelSession.new(guide_mode="off")
    config = session.state.config
    result = {
        "scope": "Deterministic formula audit only; not win-rate or balance proof.",
        "speed_formula": "phase_ticks uses ceil(base * speed_reference / effective_speed), with recovery gear multiplier; minimum phase duration applies.",
        "one_action_rule": "Speed changes phase timing, not voluntary action count.",
        "fixture_comparison": {
            "player": measure(session.player, config),
            "ronin": measure(session.enemy, config),
        },
        "player_fatigue_sweep": [],
        "raw_speed_sweep": [],
        "effective_speed_breakpoints": breakpoints(config),
    }
    for fatigue in (0, 15, 35, 60, 75, 85):
        fighter = session.player
        fighter.fatigue = fatigue
        result["player_fatigue_sweep"].append(measure(fighter, config))
    session.player.fatigue = 0
    for speed in args.speeds:
        fighter = session.player
        fighter.speed = speed
        result["raw_speed_sweep"].append(measure(fighter, config))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    summary = {key: result[key] for key in ("fixture_comparison", "player_fatigue_sweep", "raw_speed_sweep")}
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
