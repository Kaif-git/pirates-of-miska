"""Departure closure, rehire prevention and the read-only voyage disclosure."""
import pytest

from pirates.game import World
from pirates.management import crew_commitment_rows
from tales_sim.clock import TICKS_PER_DAY


def ashore(world, port=None):
    if port is not None:
        world.economy.port = port
    world.at_sea = False
    world.player.x, world.player.y, world.player.z = 32, 22, 0


def test_released_candidate_cannot_be_rehired_or_charged_a_second_signing_payment(tmp_path):
    world = World()
    ashore(world, "verdant")
    sailor = world.economy.recruit_candidate(world, "maelin_orr")
    money = world.economy.money
    world.ship.release_crew_member(world, sailor.id)
    assert world.economy.money == money
    assert world.economy.recruitment.statuses["maelin_orr"] == "recruited"
    with pytest.raises(ValueError, match="no longer available"):
        world.economy.recruit_candidate(world, "maelin_orr")

    path = tmp_path / "released.json"
    world.save(path)
    loaded = World.load(path)
    assert loaded.economy.employment.record(sailor.id)["status"] == "closed"
    with pytest.raises(ValueError, match="no longer available"):
        loaded.economy.recruit_candidate(loaded, "maelin_orr")


def test_release_clears_officer_and_order_references_and_keeps_owed_wages():
    world = World()
    ashore(world, "verdant")
    sailor = world.economy.recruit_candidate(world, "elira_voss")
    world.voyages.roles["navigator"] = sailor.id
    world.voyages.crew_order = {"order": "rest", "leader": sailor.id,
                                "members": [sailor.id, "mara"]}
    record = world.economy.employment.record(sailor.id)
    record["arrears"] = 4
    world.ship.release_crew_member(world, sailor.id)
    assert world.voyages.roles["navigator"] is None
    assert world.voyages.crew_order["members"] == ["mara"]
    assert world.voyages.crew_order["leader"] == "mara"
    assert record["status"] == "closed" and record["arrears"] == 4
    assert any(row["id"] == sailor.id for row in world.ship.crew_history)


def test_voyage_review_discloses_daily_pay_arrears_crossings_and_the_promise():
    world = World()
    ashore(world, "verdant")
    sailor = world.economy.recruit_candidate(world, "maelin_orr")
    world.economy.employment.record(sailor.id)["arrears"] = 5
    plan = world.voyages.plan(world, "cinder")
    rows = crew_commitment_rows(world, plan)
    text = "\n".join(rows)
    assert "1 agreement, 2 Marks per day; 5 Marks currently outstanding." in text
    assert "no midnight in the planned estimate" in text
    assert "CREW UNDERTAKING:" in text
    assert "reach Cinder Quay by day 4" in text

    before = (world.economy.money, world.clock.total_ticks,
              world.economy.employment.snapshot())
    assert crew_commitment_rows(world, plan) == rows
    assert (world.economy.money, world.clock.total_ticks,
            world.economy.employment.snapshot()) == before


def test_voyage_review_counts_a_planned_midnight_crossing():
    world = World()
    ashore(world, "verdant")
    world.economy.recruit_candidate(world, "maelin_orr")
    world.clock.total_ticks = TICKS_PER_DAY - 3600 * 100  # 23:00 on day 1
    plan = world.voyages.plan(world, "cinder")
    rows = crew_commitment_rows(world, plan)
    assert any("2 Marks across 1 planned midnight" in row for row in rows)
