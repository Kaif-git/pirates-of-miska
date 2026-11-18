import pytest

from pirates.npc_strength import organization_strength, vessel_strength_breakdown


def test_vessel_strength_exposes_separate_capability_and_readiness_inputs():
    ship = {"crew": 8, "condition": 80}
    captain = {"command_power": 70, "reputation": 99,
               "skills": {"tactics": 60, "seamanship": 80}}
    organization = {"cohesion": 40}

    result = vessel_strength_breakdown(ship, captain, organization)

    assert result["components"] == {
        "crew_readiness": 80.0,
        "hull_condition": 80.0,
        "command": 70.0,
        "combat_skill": 70.0,
        "organization_cohesion": 40.0,
    }
    assert result["score"] == 73
    # A generated command score is used instead of reputation, not added to it.
    assert result["components"]["command"] == 70


def test_vessel_strength_responds_to_force_condition_and_leadership():
    captain = {"command_power": 50, "skills": {"tactics": 50, "seamanship": 50}}
    organization = {"cohesion": 50}
    baseline = vessel_strength_breakdown({"crew": 5, "condition": 50}, captain, organization)
    better_crew = vessel_strength_breakdown({"crew": 9, "condition": 50}, captain, organization)
    better_hull = vessel_strength_breakdown({"crew": 5, "condition": 100}, captain, organization)
    better_captain = vessel_strength_breakdown(
        {"crew": 5, "condition": 50},
        {"command_power": 100, "skills": {"tactics": 100, "seamanship": 100}},
        organization,
    )
    assert better_crew["score"] > baseline["score"]
    assert better_hull["score"] > baseline["score"]
    assert better_captain["score"] > baseline["score"]


def test_authored_records_use_safe_defaults_and_strength_stays_bounded():
    result = vessel_strength_breakdown({"crew": 50, "condition": 140}, {"reputation": 85})
    assert result["score"] == 87
    assert all(0 <= value <= 100 for value in result["components"].values())


def test_organization_strength_diminishes_support_and_requires_eligible_vessels():
    one = organization_strength([{"score": 80}])
    two = organization_strength([{"score": 80}, {"score": 60}])
    assert one == {"score": 80, "vessel_count": 1, "vessel_scores": [80]}
    assert two == {"score": 95, "vessel_count": 2, "vessel_scores": [80, 60]}
    assert organization_strength([])["score"] == 0
    with pytest.raises(ValueError):
        organization_strength([{"score": 101}])


def test_vessel_strength_rejects_invalid_crew_ledger():
    with pytest.raises(ValueError):
        vessel_strength_breakdown({"crew": -1, "condition": 100})
