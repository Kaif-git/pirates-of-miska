import pytest

from pirates.game import World
from tales_sim.clock import TICKS_PER_DAY, TICKS_PER_HOUR


def ashore(world):
    world.player.x, world.player.y, world.player.z = 32, 22, 0


def hire(world, candidate_id):
    ashore(world)
    return world.economy.recruit_candidate(world, candidate_id)


def set_day(world, day, hour=8):
    world.clock.total_ticks = (day - 1) * TICKS_PER_DAY + hour * TICKS_PER_HOUR


def test_step_hook_settles_the_first_completed_day_and_pays_once():
    world = World()
    hire(world, "pippin_ash")
    starting_money = world.economy.money
    world.clock.total_ticks = TICKS_PER_DAY - 100  # one second before day 2
    world.advance(100)
    record = world.economy.employment.record("hire:pippin_ash")
    assert world.clock.day == 2
    assert world.economy.employment.settled_day == 2
    assert record["paid_total"] == 1 and record["arrears"] == 0
    assert world.economy.money == starting_money - 1


def test_defensive_advance_pass_catches_a_clock_that_already_crossed_midnight():
    world = World()
    hire(world, "pippin_ash")
    starting_money = world.economy.money
    world.clock.total_ticks += TICKS_PER_DAY
    world.advance(0)  # no ship steps; the post-advance pass must still settle
    record = world.economy.employment.record("hire:pippin_ash")
    assert world.clock.day == 2
    assert record["paid_total"] == 1
    assert world.economy.money == starting_money - 1


def test_chunked_and_single_settlement_are_equivalent_and_idempotent():
    one = World()
    hire(one, "pippin_ash")
    one.economy.employment.settle_day(one, 5)

    many = World()
    hire(many, "pippin_ash")
    for day in range(2, 6):
        many.economy.employment.settle_day(many, day)

    assert one.economy.money == many.economy.money
    assert one.economy.employment.snapshot() == many.economy.employment.snapshot()
    before = one.economy.money
    again = one.economy.employment.settle_day(one, 5)
    assert again == {"days": 0, "accrued": 0, "paid": 0}
    assert one.economy.money == before


def test_underfunding_pays_proportionally_and_keeps_conserved_arrears():
    world = World()
    hire(world, "pippin_ash")
    hire(world, "elira_voss")
    hire(world, "maelin_orr")
    world.economy.money = 3
    world.economy.employment.settle_day(world, 2)
    records = world.economy.employment.records
    # Due 1 + 3 + 2 = 6; available 3. Largest remainders break the tie by sailor id.
    assert {key: row["paid_total"] for key, row in records.items()} == {
        "hire:pippin_ash": 0, "hire:elira_voss": 2, "hire:maelin_orr": 1}
    assert {key: row["arrears"] for key, row in records.items()} == {
        "hire:pippin_ash": 1, "hire:elira_voss": 1, "hire:maelin_orr": 1}
    assert world.economy.money == 0
    assert any("unpaid wages" in row for row in world.economy.history)

    world.economy.money = 10
    paid = world.economy.employment.pay_arrears(world)
    assert paid == 3 and world.economy.money == 7
    assert all(row["arrears"] == 0 for row in records.values())
    assert sum(row["paid_total"] for row in records.values()) == 6


def test_manual_settlement_requires_funds_and_does_not_double_charge():
    world = World()
    hire(world, "pippin_ash")
    world.economy.employment.settle_day(world, 2)
    world.economy.money = 0
    world.economy.employment.settle_day(world, 3)  # one unpaid day
    record = world.economy.employment.record("hire:pippin_ash")
    assert record["arrears"] == 1
    with pytest.raises(ValueError, match="Not enough Marks"):
        world.economy.employment.pay_arrears(world)
    unchanged = world.economy.money
    world.economy.money = 5
    assert world.economy.employment.pay_arrears(world) == 1
    assert world.economy.money == 4
    assert world.economy.employment.pay_arrears(world) == 0
    assert world.economy.money == 4
    assert record["arrears"] == 0 and record["paid_total"] == 2
    assert unchanged == 0


def test_arrears_notice_is_recorded_once_while_the_shortfall_continues():
    world = World()
    hire(world, "pippin_ash")
    world.economy.money = 0
    world.economy.employment.settle_day(world, 2)
    world.economy.employment.settle_day(world, 5)
    notices = [row for row in world.economy.history if "unpaid wages" in row]
    assert len(notices) == 1
    assert "from day 2" in notices[0]
    record = world.economy.employment.record("hire:pippin_ash")
    assert record["arrears"] == 4 and record["unpaid_since_day"] == 2


def test_mid_period_hire_is_not_charged_retroactively(tmp_path):
    world = World()
    set_day(world, 3)
    hire(world, "pippin_ash")
    starting_money = world.economy.money
    set_day(world, 4)
    world.economy.employment.settle_day(world, 4)
    record = world.economy.employment.record("hire:pippin_ash")
    assert record["paid_total"] == 1
    assert world.economy.money == starting_money - 1
    assert record["unpaid_since_day"] is None

    path = tmp_path / "mid-period.json"
    world.save(path)
    loaded = World.load(path)
    assert loaded.economy.employment.record("hire:pippin_ash")["paid_total"] == 1


def test_legacy_starter_crew_accrue_no_wages():
    world = World()
    starting_money = world.economy.money
    world.economy.employment.settle_day(world, 7)
    assert world.economy.money == starting_money
    assert world.economy.employment.records == {}


def test_death_closes_the_agreement_without_charging_a_partial_day():
    world = World()
    sailor = hire(world, "pippin_ash")
    world.economy.employment.settle_day(world, 2)
    starting_money = world.economy.money
    world.ship._depart_crew_member(world, sailor.id, "dead", "test death")
    record = world.economy.employment.record(sailor.id)
    assert record["status"] == "closed" and record["closed_day"] == 1
    assert record["paid_total"] == 1 and record["arrears"] == 0
    world.economy.employment.settle_day(world, 5)
    assert record["paid_total"] == 1
    assert world.economy.money == starting_money


def test_departure_keeps_unpaid_wages_payable_after_save_and_load(tmp_path):
    world = World()
    sailor = hire(world, "pippin_ash")
    world.economy.money = 0
    world.economy.employment.settle_day(world, 2)
    set_day(world, 3)
    world.ship.release_crew_member(world, sailor.id)
    record = world.economy.employment.record(sailor.id)
    assert record["status"] == "closed" and record["arrears"] == 2

    path = tmp_path / "former-debt.json"
    world.save(path)
    loaded = World.load(path)
    loaded.economy.money = 10
    assert loaded.economy.employment.pay_arrears(loaded) == 2
    assert loaded.economy.money == 8
    assert loaded.economy.employment.record(sailor.id)["arrears"] == 0
