"""Fixed-seed 1v1 consequence distribution; comparative evidence, not broad balance proof."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import argparse
import json
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pirates.duel_system.balance import fingerprint, run_fight
from pirates.duel_system.config import load_config

CASES = {
    "punisher": ("ScriptedPunisherBot", "Ronin", 0, {}),
    "spam_heavy": ("SpamHeavyBot", "Ronin", 0, {}),
    "random": ("RandomBot", "Ronin", 0, {}),
    "kite": ("KiteBot", "Ronin", 0, {}),
    "level_gap": ("ScriptedPunisherBot", "Ronin", 3, {}),
    "long_light_gear": ("ScriptedPunisherBot", "Ronin", 0,
                        {"reach": 2, "edge": 3, "weight": 2, "name": "Spear"}),
    "heavy_short_gear": ("SpamHeavyBot", "Ronin", 0,
                         {"reach": 1, "edge": 6, "weight": 5, "name": "Mace"}),
}


def _union_ticks(intervals):
    total, end = 0, -1
    for start, stop in sorted((max(0, a), max(a, b)) for a, b in intervals):
        if stop <= end:
            continue
        total += stop-max(start, end)
        end = stop
    return total


def summarize(result):
    trace, end_tick = result["trace"], result["ticks"]
    shoves, hits, head_hits, bleed_loss = Counter(), Counter(), Counter(), 0.0
    stability = Counter()
    bandages = bandage_starts = bandage_interrupts = falls = rises = 0
    incapacitations = head_incapacitations = 0
    stagger = {}
    grounded = {}
    wounds = Counter()
    for event in trace:
        kind, actor, target, delta = event["type"], event["actor"], event["target"], event["deltas"]
        if kind == "SHOVE":
            shoves[delta.get("outcome", "UNKNOWN")] += 1
            shoves["requested_steps"] += delta.get("requested_steps", 0)
            shoves["actual_steps"] += delta.get("actual_steps", 0)
        elif kind == "HIT":
            hits["total"] += 1
            hits["impact_hits"] += delta.get("impact_applied", 0) > 0
            hits["impact_stability"] += delta.get("impact_applied", 0)
            if event["zone"] in {"HEAD", "EYE"}:
                head_hits[event["severity"] or "UNKNOWN"] += 1
                head_hits["total"] += 1
        elif kind == "WOUND":
            wounds[event["severity"] or "UNKNOWN"] += 1
        elif kind == "POOL_CHANGE":
            bleed_loss += delta.get("bleed_loss", 0)
        elif kind == "STABILITY":
            stability["loss"] += delta.get("loss", 0)
            stability["gain"] += delta.get("gain", 0)
        elif kind == "STATUS" and "BANDAGE_COMPLETED" in event["causes"]:
            bandages += 1
        elif kind == "ACTION_START" and delta.get("kind") == "BANDAGE":
            bandage_starts += 1
        elif kind == "INTERRUPT" and delta.get("kind") == "BANDAGE":
            bandage_interrupts += 1
        elif kind == "STAGGER":
            stagger.setdefault(actor, []).append((event["tick"], delta.get("until_tick", event["tick"])))
        elif kind == "FALL":
            falls += 1
            grounded.setdefault(actor, []).append([event["tick"], end_tick])
        elif kind == "RISE":
            rises += 1
            active = next((interval for interval in reversed(grounded.get(actor, []))
                           if interval[1] == end_tick), None)
            if active is not None:
                active[1] = event["tick"]
        elif kind == "INCAPACITATED":
            incapacitations += 1
            head_incapacitations += event["zone"] in {"HEAD", "EYE"}
    actor_ids = set(stagger) | set(grounded)
    stagger_ticks = sum(_union_ticks(stagger.get(actor, ())) for actor in actor_ids)
    disabled_by_fighter = sum(_union_ticks(
        list(stagger.get(actor, ())) + [tuple(row) for row in grounded.get(actor, ())])
        for actor in actor_ids)
    disabled_clock_ticks = _union_ticks(
        [interval for rows in stagger.values() for interval in rows]
        + [tuple(row) for rows in grounded.values() for row in rows])
    return {"winner": result["winner"], "reason": result["reason"], "ticks": end_tick,
            "decisions": result["decisions"], "shoves": dict(shoves), "hits": dict(hits),
            "head_hits": dict(head_hits), "wounds": dict(wounds), "bleed_loss": round(bleed_loss, 4),
            "stability": dict(stability), "incapacitations": incapacitations,
            "head_incapacitations": head_incapacitations,
            "bandage_starts": bandage_starts, "bandage_interrupts": bandage_interrupts,
            "bandages_completed": bandages, "falls": falls, "rises": rises,
            "stagger_lock_fighter_ticks": stagger_ticks,
            "stagger_or_grounded_fighter_ticks": disabled_by_fighter,
            "stagger_or_grounded_clock_ticks": disabled_clock_ticks}


def audit(fights, seed, matchups):
    current = load_config()
    no_hit_impact = deepcopy(current)
    no_hit_impact["injury"]["impact_per_net"] = 0
    reports = {}
    for name in matchups:
        first, second, level_gap, weapon = CASES[name]
        case_config = deepcopy(current)
        case_config["gear"]["weapon"].update(weapon)
        case_control = deepcopy(no_hit_impact)
        case_control["gear"]["weapon"].update(weapon)
        paired = {"current": [], "hit_impact_disabled": []}
        for offset in range(fights):
            current_result = run_fight(first, second, seed=seed+offset, config=case_config,
                                       level_gap=level_gap, trace=True)
            control_result = run_fight(first, second, seed=seed+offset, config=case_control,
                                       level_gap=level_gap, trace=True)
            paired["current"].append(summarize(current_result))
            paired["hit_impact_disabled"].append(summarize(control_result))
        reports[name] = {"first_bot": first, "second_bot": second, "level_gap": level_gap,
                         "weapon_profile": weapon or "default_cutlass", "fights": fights,
                         "current": aggregate(paired["current"]),
                         "hit_impact_disabled": aggregate(paired["hit_impact_disabled"]),
                         "paired_results": paired}
    return {"scope": "Fixed-seed synthetic 1v1 bots only; impact-disabled control isolates hit-impact tuning, not all §30 changes.",
            "seed": seed, "config_sha256": fingerprint(current),
            "matchups": reports}


def aggregate(rows):
    metric_keys = ("ticks", "decisions", "bleed_loss", "bandage_starts", "bandage_interrupts",
                   "bandages_completed", "falls", "rises",
                   "incapacitations", "head_incapacitations",
                   "stagger_lock_fighter_ticks", "stagger_or_grounded_fighter_ticks",
                   "stagger_or_grounded_clock_ticks")
    results = {key: statistics.median(row[key] for row in rows) for key in metric_keys}
    results["end_reasons"] = dict(Counter(row["reason"] for row in rows))
    results["wins_by_side"] = dict(Counter(row["winner"] or "draw" for row in rows))
    for metric in ("shoves", "hits", "head_hits", "wounds"):
        results[metric] = dict(sum((Counter(row[metric]) for row in rows), Counter()))
    results["stability"] = {key: sum(row["stability"].get(key, 0) for row in rows)
                            for key in {key for row in rows for key in row["stability"]}}
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fights", type=int, default=100)
    parser.add_argument("--seed", type=int, default=8100)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--matchup", choices=tuple(CASES), action="append")
    args = parser.parse_args()
    if args.fights <= 0:
        parser.error("--fights must be positive")
    report = audit(args.fights, args.seed,
                   args.matchup or list(CASES))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: {"current": value["current"],
                            "hit_impact_disabled": value["hit_impact_disabled"]}
                      for key, value in report["matchups"].items()}, indent=2))


if __name__ == "__main__":
    main()
