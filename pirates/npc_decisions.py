"""Explainable strategic decision scoring for generated NPCs.

This layer influences chart-scale NPC orders only. It intentionally does not
import or modify the tactical combat arena.
"""
from __future__ import annotations

from pirates.npc_generation import TRAIT_EFFECTS, random_stream, validate_manifest, validate_person

PERSONALITY_FIT = {
    "trade": {"greed": 0.30, "patience": 0.10, "discipline": 0.05},
    "patrol": {"discipline": 0.24, "patience": 0.08, "boldness": 0.05},
    "hunt": {"boldness": 0.27, "pride": 0.10, "discipline": 0.03},
    "regroup": {"patience": 0.15, "empathy": 0.08, "discipline": 0.12},
    "negotiate": {"sociability": 0.22, "empathy": 0.14, "patience": 0.08},
}

VALUE_FIT = {
    "trade": {"wealth": 0.25},
    "patrol": {"order": 0.24, "loyalty": 0.08},
    "hunt": {"fame": 0.20, "freedom": 0.08},
    "regroup": {"family": 0.10, "loyalty": 0.08},
    "negotiate": {"loyalty": 0.10, "family": 0.08},
}


def score_action(person, action, goal=None, relationship_bonus=0):
    """Return a transparent score breakdown for one feasible strategic action."""
    validate_person(person)
    if not isinstance(action, dict) or action.get("kind") not in PERSONALITY_FIT:
        raise ValueError("Unsupported strategic action")
    for key in ("base", "goal_progress", "risk", "cost"):
        value = action.get(key, 0)
        if type(value) not in (int, float) or not 0 <= value <= 100:
            raise ValueError(f"Invalid strategic action {key}")
    if type(relationship_bonus) not in (int, float) or not -100 <= relationship_bonus <= 100:
        raise ValueError("Invalid strategic relationship contribution")

    kind = action["kind"]
    personality_fit = sum((person["personality"][axis] - 50) * coefficient
                          for axis, coefficient in PERSONALITY_FIT[kind].items())
    value_fit = sum((person["values"][axis] - 50) * coefficient
                    for axis, coefficient in VALUE_FIT[kind].items())
    effects = {trait: TRAIT_EFFECTS[trait] for trait in person["traits"]}
    risk_tolerance = person["personality"]["boldness"]
    cost_sensitivity = 1.0
    if "cautious" in effects:
        risk_tolerance += effects["cautious"]["risk_tolerance"]
    if "bold" in effects:
        risk_tolerance += effects["bold"]["risk_tolerance"]
    risk_penalty = action.get("risk", 0) * max(0.1, 0.65 - risk_tolerance / 200)
    cost_sensitivity += (person["personality"]["greed"] - 50) / 200
    if "greedy" in effects:
        cost_sensitivity -= effects["greedy"]["resource_cost_weight"] / 100
    cost_penalty = action.get("cost", 0) * max(0.1, cost_sensitivity)
    goal_bonus = action.get("goal_progress", 0) * 0.35
    if goal and isinstance(goal, dict):
        goal_bonus += 12 if action.get("goal_match") == goal.get("type") else 0
    if kind == "negotiate" and "charismatic" in effects:
        relationship_bonus += effects["charismatic"]["persuasion_weight"] * 0.2
    if kind == "hunt" and "vindictive" in effects:
        relationship_bonus += effects["vindictive"]["grievance_weight"] * 0.15
    total = action.get("base", 0) + personality_fit + value_fit + goal_bonus
    total += relationship_bonus - risk_penalty - cost_penalty
    return {
        "kind": kind,
        "base": float(action.get("base", 0)),
        "personality_fit": round(personality_fit, 4),
        "value_fit": round(value_fit, 4),
        "goal_bonus": round(goal_bonus, 4),
        "relationship_bonus": round(relationship_bonus, 4),
        "risk_penalty": round(risk_penalty, 4),
        "cost_penalty": round(cost_penalty, 4),
        "risk_tolerance": max(0, min(100, risk_tolerance)),
        "score": round(total, 4),
    }


def choose_action(person, candidates, manifest, actor_id, hour, current_kind=None, commitment=50):
    """Choose a feasible action deterministically, with a commitment switch cost.

    The caller owns feasibility and execution. Candidates are scored in stable
    order; event-keyed tie breaking consumes no shared/global random state.
    """
    manifest = validate_manifest(manifest)
    validate_person(person)
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("At least one feasible strategic action is required")
    if not isinstance(actor_id, str) or not actor_id or type(hour) is not int or hour < 0:
        raise ValueError("Invalid strategic decision identity/time")
    if type(commitment) is not int or not 0 <= commitment <= 100:
        raise ValueError("Invalid goal commitment")
    scored = [score_action(person, candidate.get("action"), candidate.get("goal"),
                           candidate.get("relationship_bonus", 0)) | {"candidate": candidate}
              for candidate in candidates]
    scored.sort(key=lambda row: (-row["score"], row["kind"]))
    best_score = scored[0]["score"]
    tied = [row for row in scored if abs(row["score"] - best_score) < 0.0001]
    if len(tied) > 1:
        rng = random_stream(manifest, "decision_tiebreak", f"{actor_id}:{hour}")
        best = tied[rng.randrange(len(tied))]
    else:
        best = tied[0]

    current = next((row for row in scored if row["kind"] == current_kind), None)
    if current and best["kind"] != current_kind:
        switch_cost = 4 + commitment * 0.08
        effects = [TRAIT_EFFECTS[trait] for trait in person["traits"]]
        switch_cost += sum(effect.get("goal_switch_penalty", 0) for effect in effects)
        if best["score"] <= current["score"] + switch_cost:
            best = current
    return {key: value for key, value in best.items() if key != "candidate"} | {
        "candidate": best["candidate"],
        "alternatives": [{key: row[key] for key in ("kind", "score")} for row in scored],
    }
