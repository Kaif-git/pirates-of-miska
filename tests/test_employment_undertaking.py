"""The single authored destination undertaking and its arrival/day closure."""
from pirates.game import World
from tales_sim.clock import TICKS_PER_DAY, TICKS_PER_HOUR


def ashore(world, port=None):
    if port is not None:
        world.economy.port = port
    world.at_sea = False
    world.player.x, world.player.y, world.player.z = 32, 22, 0


def set_day(world, day, hour=9):
    world.clock.total_ticks = (day - 1) * TICKS_PER_DAY + hour * TICKS_PER_HOUR


def hire_maelin(world):
    ashore(world, "verdant")
    return world.economy.recruit_candidate(world, "maelin_orr")


def undertaking(world):
    return world.economy.employment.record("hire:maelin_orr")["undertaking"]


def messages(world, needle):
    return [row for row in world.economy.history if needle in row]


def clear_squall(world):
    storm = world.voyages.voyage.get("storm_event")
    if storm:
        storm["trigger_roll"] = storm["chance_percent"]
        storm["triggered"] = False


def test_shore_at_cinder_on_the_deadline_satisfies_the_authored_undertaking():
    world = World()
    hire_maelin(world)
    assert undertaking(world)["deadline_day"] == 4
    set_day(world, 4, 20)
    ashore(world, "cinder")
    world.advance(0)
    row = undertaking(world)
    assert row["status"] == "satisfied" and row["closed_day"] == 4
    assert len(messages(world, "kept the undertaking")) == 1


def test_one_day_late_is_late_not_satisfied():
    world = World()
    hire_maelin(world)
    set_day(world, 5, 9)
    ashore(world, "cinder")
    world.advance(0)
    row = undertaking(world)
    assert row["status"] == "late" and row["closed_day"] == 5
    assert messages(world, "after the day 4 deadline")


def test_wrong_port_or_still_aboard_does_not_satisfy_and_deadline_closes_late():
    wrong = World()
    hire_maelin(wrong)
    set_day(wrong, 4, 20)
    ashore(wrong, "verdant")
    wrong.advance(0)
    assert undertaking(wrong)["status"] == "open"
    set_day(wrong, 5, 6)
    wrong.advance(0)
    assert undertaking(wrong)["status"] == "late"
    assert undertaking(wrong)["closed_day"] == 5

    aboard = World()
    hire_maelin(aboard)
    set_day(aboard, 4, 20)
    aboard.economy.port = "cinder"
    aboard.at_sea = False
    aboard.player.x, aboard.player.y, aboard.player.z = 76, 25, 0
    aboard.advance(0)
    assert undertaking(aboard)["status"] == "open"


def test_satisfaction_runs_even_after_payroll_already_settled_the_day():
    world = World()
    hire_maelin(world)
    set_day(world, 4, 20)
    world.economy.employment.settle_day(world, 4)
    assert world.economy.employment.settled_day == 4
    ashore(world, "cinder")
    world.advance(0)
    assert undertaking(world)["status"] == "satisfied"


def test_voyage_arrival_report_shows_the_promise_open_then_satisfied():
    world = World()
    hire_maelin(world)
    world.voyages.confirm(world, "cinder")
    clear_squall(world)
    world.voyages.voyage["duration_seconds"] = 25
    world.enter_sea()
    world.advance(100 * 600)
    assert world.voyages.voyage["status"] == "arrived"
    assert world.economy.port == "cinder" and not world.at_sea
    report = world.voyages.arrival_report(world)
    assert "Crew undertakings:" in report
    assert any("Maelin Orr" in line and "open" in line for line in report)

    ashore(world, "cinder")
    world.advance(0)
    report = world.voyages.arrival_report(world)
    assert any("Maelin Orr" in line and "satisfied on day 1" in line for line in report)
    assert any("Maelin Orr kept the undertaking" in text
               for text in world.voyages.voyage["outcomes"])


def test_release_before_arrival_closes_once_as_withdrawn_and_never_satisfies(tmp_path):
    world = World()
    sailor = hire_maelin(world)
    world.ship.release_crew_member(world, sailor.id)
    row = undertaking(world)
    assert row["status"] == "withdrawn" and row["closed_day"] == 1
    assert len(messages(world, "was not completed")) == 1

    set_day(world, 2, 12)
    ashore(world, "cinder")
    world.advance(0)
    assert undertaking(world)["status"] == "withdrawn"
    assert undertaking(world)["closed_day"] == 1
    assert len(messages(world, "was not completed")) == 1

    path = tmp_path / "withdrawn.json"
    world.save(path)
    loaded = World.load(path)
    loaded.economy.employment.settle_undertakings(loaded)
    assert loaded.economy.employment.record(sailor.id)["undertaking"]["status"] == "withdrawn"
    assert len(messages(loaded, "was not completed")) == 1


def test_death_closes_the_undertaking_as_failed():
    world = World()
    sailor = hire_maelin(world)
    world.ship._depart_crew_member(world, sailor.id, "dead", "test death")
    row = undertaking(world)
    assert row["status"] == "failed" and row["closed_day"] == 1
    assert len(messages(world, "was not completed")) == 1


def test_repeated_settlement_and_reload_do_not_duplicate_a_satisfied_promise(tmp_path):
    world = World()
    hire_maelin(world)
    set_day(world, 4, 20)
    ashore(world, "cinder")
    world.advance(0)
    assert len(messages(world, "kept the undertaking")) == 1

    world.economy.employment.settle_undertakings(world)
    world.economy.employment.settle_undertakings(world)
    assert len(messages(world, "kept the undertaking")) == 1

    path = tmp_path / "satisfied.json"
    world.save(path)
    loaded = World.load(path)
    loaded.economy.employment.settle_day(loaded, 8)
    row = loaded.economy.employment.record("hire:maelin_orr")["undertaking"]
    assert row["status"] == "satisfied" and row["closed_day"] == 4
    assert len(messages(loaded, "kept the undertaking")) == 1
