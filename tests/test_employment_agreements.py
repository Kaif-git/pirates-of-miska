import json

import pytest

from pirates.crew_recruitment import CANDIDATES, RecruitmentPool
from pirates.employment import EmploymentLedger, candidate_terms
from pirates.game import World
from pirates.management import profile_rows
from pirates.trading_ui import port_menu


def ashore(world):
    world.player.x, world.player.y, world.player.z = 32, 22, 0


def test_candidate_terms_are_bounded_and_maelin_has_one_authored_undertaking():
    assert set(CANDIDATES) == {"pippin_ash", "elira_voss", "brannoc_reed", "maelin_orr"}
    for candidate_id, candidate in CANDIDATES.items():
        terms = candidate_terms(candidate_id)
        assert terms["role"] and terms["role"] == candidate["role"]
        assert 1 <= terms["wage_per_day"] <= 1000
        assert terms["signing_payment"] == candidate["asking_price"] > 0
        assert terms["undertaking"] is None or candidate_id == "maelin_orr"
    undertaking = candidate_terms("maelin_orr")["undertaking"]
    assert undertaking["destination"] == "cinder"
    assert undertaking["deadline_days"] == 3


def test_recruitment_pool_adds_missing_known_candidate_but_rejects_unknown_ids():
    old = {key: "available" for key in ("pippin_ash", "elira_voss", "brannoc_reed")}
    pool = RecruitmentPool.restore({"version": 1, "statuses": old})
    assert pool.statuses["maelin_orr"] == "available"
    assert pool.statuses["pippin_ash"] == "available"
    with pytest.raises(ValueError, match="Invalid recruitment pool"):
        RecruitmentPool.restore({"version": 1, "statuses": {**old, "intruder": "available"}})


def test_hire_creates_one_active_agreement_with_exact_terms_and_persists(tmp_path):
    world = World()
    ashore(world)
    sailor = world.economy.recruit_candidate(world, "pippin_ash")
    record = world.economy.employment.record(sailor.id)
    assert record["candidate_id"] == "pippin_ash"
    assert record["role"] == CANDIDATES["pippin_ash"]["role"]
    assert record["wage_per_day"] == 1
    assert record["signing_payment"] == 35
    assert record["accepted_day"] == 1 and record["status"] == "active"
    assert record["arrears"] == record["paid_total"] == 0
    assert record["undertaking"] is None
    with pytest.raises(ValueError, match="no longer available"):
        world.economy.recruit_candidate(world, "pippin_ash")

    path = tmp_path / "agreement.json"
    world.save(path)
    loaded = World.load(path)
    assert loaded.economy.employment.snapshot() == world.economy.employment.snapshot()
    assert loaded.ship.sailor("hire:pippin_ash") is not None


def test_hire_with_undertaking_records_a_deadline_from_acceptance(tmp_path):
    world = World()
    ashore(world)
    sailor = world.economy.recruit_candidate(world, "maelin_orr")
    record = world.economy.employment.record(sailor.id)
    undertaking = record["undertaking"]
    assert undertaking["destination"] == "cinder"
    assert undertaking["deadline_day"] == 4  # day 1 + 3
    assert undertaking["status"] == "open" and undertaking["closed_day"] is None

    path = tmp_path / "undertaking.json"
    world.save(path)
    assert World.load(path).economy.employment.record(sailor.id)["undertaking"] == undertaking


def test_failed_hire_rolls_back_money_pool_roster_agreement_and_layout(monkeypatch):
    world = World()
    ashore(world)
    starting_money = world.economy.money
    starting_crew = [sailor.id for sailor in world.ship.crew]
    starting_layout = world.fixtures.snapshot()
    original_bind = world.bind_fixtures
    calls = []

    def flaky_bind(layout):
        if not calls:
            calls.append("raised")
            raise ValueError("injected berth failure")
        return original_bind(layout)

    monkeypatch.setattr(world, "bind_fixtures", flaky_bind)
    with pytest.raises(ValueError, match="injected berth failure"):
        world.economy.recruit_candidate(world, "pippin_ash")
    assert world.economy.money == starting_money
    assert world.economy.recruitment.statuses["pippin_ash"] == "available"
    assert [sailor.id for sailor in world.ship.crew] == starting_crew
    assert world.fixtures.snapshot() == starting_layout
    assert world.economy.employment.records == {}


def test_old_save_without_employment_loads_an_empty_ledger_and_legacy_label(tmp_path):
    world = World()
    ashore(world)
    sailor = world.economy.recruit_candidate(world, "pippin_ash")
    path = tmp_path / "pre-employment.json"
    world.save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["economy"]["employment"]
    path.write_text(json.dumps(data), encoding="utf-8")

    loaded = World.load(path)
    assert loaded.economy.employment.records == {}
    assert loaded.economy.employment.settled_day == loaded.clock.day
    rows = profile_rows(loaded.ship.sailor(sailor.id), loaded)
    assert "EMPLOYMENT TERMS" in rows
    assert any("Legacy/standing arrangement" in row for row in rows)


def test_candidate_menu_discloses_wage_and_undertaking_before_acceptance():
    class Scripted:
        def __init__(self):
            self.answers = iter((2, 2, 0))
            self.titles = []
            self.rows = []

        def render_list(self, _world, title, rows, *_args, **_kwargs):
            self.titles.append(title)
            self.rows.append(list(rows))

        def choose(self, _count):
            return next(self.answers)

        def wait_any(self):
            pass

    world = World()
    ashore(world)
    renderer = Scripted()
    port_menu(renderer, world)
    assert world.ship.sailor("hire:maelin_orr") is not None
    assert "Crew prospect" in renderer.titles
    rows = [row for block in renderer.rows for row in block]
    assert any("Wage: 2 Marks per completed day" in row for row in rows)
    assert any("Reach Cinder Quay by day 4" in row for row in rows)
    assert any("coastal booking or a chart voyage" in row for row in rows)


def test_release_closes_agreement_and_keeps_unpaid_wages_for_former_crew(tmp_path):
    world = World()
    ashore(world)
    sailor = world.economy.recruit_candidate(world, "pippin_ash")
    record = world.economy.employment.record(sailor.id)
    record["arrears"] = 7
    world.ship.release_crew_member(world, sailor.id)
    assert record["status"] == "closed"
    assert record["closed_day"] == 1
    assert record["close_cause"] == "released by the captain at port"
    assert record["arrears"] == 7

    path = tmp_path / "released-agreement.json"
    world.save(path)
    loaded = World.load(path)
    assert loaded.economy.employment.record(sailor.id)["arrears"] == 7
    assert loaded.ship.crew_history[0]["id"] == sailor.id


def test_release_closes_an_open_undertaking_as_withdrawn(tmp_path):
    world = World()
    ashore(world)
    sailor = world.economy.recruit_candidate(world, "maelin_orr")
    world.ship.release_crew_member(world, sailor.id)
    undertaking = world.economy.employment.record(sailor.id)["undertaking"]
    assert undertaking["status"] == "withdrawn"
    assert undertaking["closed_day"] == 1

    path = tmp_path / "withdrawn-undertaking.json"
    world.save(path)
    assert World.load(path).economy.employment.record(sailor.id)["undertaking"]["status"] == "withdrawn"


@pytest.mark.parametrize("mutate", [
    lambda data: data["economy"]["employment"]["records"]["hire:pippin_ash"].update(wage_per_day=0),
    lambda data: data["economy"]["employment"]["records"]["hire:pippin_ash"].update(candidate_id="ghost"),
    lambda data: data["economy"]["employment"]["records"]["hire:pippin_ash"].update(status="active",
                                                                                    closed_day=1,
                                                                                    close_cause="x"),
    lambda data: data["economy"]["employment"]["records"]["hire:maelin_orr"]["undertaking"].update(
        status="unknown"),
    lambda data: data["economy"]["employment"]["records"]["hire:pippin_ash"].update(
        review={"status": "unknown", "day": 1}),
])
def test_malformed_employment_save_is_rejected(tmp_path, mutate):
    world = World()
    ashore(world)
    world.economy.recruit_candidate(world, "pippin_ash")
    world.economy.recruit_candidate(world, "maelin_orr")
    path = tmp_path / "bad-employment.json"
    world.save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        World.load(path)


def test_employment_ledger_rejects_a_second_record_for_one_person():
    ledger = EmploymentLedger()
    world = World()
    ashore(world)
    record = ledger.prepare(world.economy.recruitment.candidate("pippin_ash"), 1)
    ledger.commit(record)
    with pytest.raises(ValueError, match="already has an employment record"):
        ledger.commit(record)
