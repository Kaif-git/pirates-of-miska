"""Explainable strategic force estimates for chart-scale NPC decisions.

These estimates are not tactical-combat statistics. They summarize current
crew, vessel, command, and organizational readiness for strategic choices.
"""
from __future__ import annotations


def _bounded(value, default=50):
    if type(value) not in (int, float):
        value = default
    return max(0, min(100, float(value)))


def vessel_strength_breakdown(ship, captain=None, organization=None):
    """Return a 0..100 strategic vessel estimate and its inspectable inputs.

    Reputation is only a compatibility fallback for authored captains that do
    not have generated command stats; it is not added on top of capability.
    """
    if not isinstance(ship, dict):
        raise ValueError("Strategic strength requires a vessel record")
    crew = ship.get("crew", 0)
    if type(crew) is not int or crew < 0:
        raise ValueError("Strategic strength requires a non-negative crew count")

    crew_readiness = _bounded(crew * 10, 0)
    hull_condition = _bounded(ship.get("condition", 100), 100)
    captain = captain if isinstance(captain, dict) else {}
    organization = organization if isinstance(organization, dict) else {}
    command = _bounded(captain.get("command_power", captain.get("reputation", 50)))
    skills = captain.get("skills", {})
    if isinstance(skills, dict):
        combat_skill = (_bounded(skills.get("tactics", 50))
                        + _bounded(skills.get("seamanship", 50))) / 2
    else:
        combat_skill = 50.0
    cohesion = _bounded(organization.get("cohesion", 50))

    components = {
        "crew_readiness": crew_readiness,
        "hull_condition": hull_condition,
        "command": command,
        "combat_skill": combat_skill,
        "organization_cohesion": cohesion,
    }
    score = round(
        crew_readiness * 0.35
        + hull_condition * 0.25
        + command * 0.20
        + combat_skill * 0.10
        + cohesion * 0.10
    )
    return {"score": max(0, min(100, score)), "components": components}


def organization_strength(ship_breakdowns):
    """Summarize eligible vessels without treating every ship as a full fleet.

    Callers must pass only ships that can actually support the encounter (for
    example, co-located vessels). The strongest ship contributes fully and
    each additional vessel contributes one quarter of its estimate, up to 300.
    """
    scores = []
    for row in ship_breakdowns:
        score = row.get("score") if isinstance(row, dict) else None
        if type(score) is not int or not 0 <= score <= 100:
            raise ValueError("Invalid vessel strength breakdown")
        scores.append(score)
    if not scores:
        return {"score": 0, "vessel_count": 0, "vessel_scores": []}
    scores.sort(reverse=True)
    score = min(300, scores[0] + round(sum(scores[1:]) * 0.25))
    return {"score": score, "vessel_count": len(scores), "vessel_scores": scores}
