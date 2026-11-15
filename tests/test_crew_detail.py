import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "engine")]

from pirates.game import World
from pirates.ship import LABORS, STATIONS
from pirates.management import chores_menu, job_explanation, work_rows


def controlled_world():
    world = World()
    for c in world.ship.crew:
        c.labors = {k: 0 for k in LABORS}
        c.hunger = c.thirst = c.fatigue = c.loneliness = c.boredom = 0
        c.sleep_debt = 25
    return world


def job_of(world, kind):
    return next(j for j in world.ship.jobs if j.kind == kind)


def materials(ship, key):
    return ship.stores[key] + sum(j.reserved.get(key, 0) for j in ship.jobs) + sum(c.cargo.get(key, 0) for c in ship.crew)


def test_short_rest_recovers_energy_but_not_sleep_debt():
    world = controlled_world()
    c = world.ship.crew[0]
    c.energy, c.fatigue, c.sleep_debt = 10, 70, 30
    world.advance(18000)
    assert c.energy >= 85 and c.fatigue <= 36
    assert c.sleep_debt >= 30
    assert any("breather" in t for t in c.thoughts)
    assert not any("Slept" in t for t in c.thoughts)


def test_sleep_recovery_requires_reaching_own_bunk():
    world = controlled_world()
    c = world.ship.crew[0]
    c.sleep_debt = 80
    world.advance(100)
    assert c.sleep_debt > 80  # still travelling
    world.advance(20000)
    assert c.sleep_debt < 21
    assert any("own bunk" in t for t in c.thoughts)


def test_sleep_windows_differ_between_early_bird_and_night_owl():
    world = controlled_world()
    a, b = world.ship.crew[0], world.ship.crew[3]
    a.sleep_debt = b.sleep_debt = 50
    assert world.ship.choose_need(a, 22) == "sleep"
    assert world.ship.choose_need(b, 22) == ""
    assert world.ship.choose_need(b, 1) == "sleep"
    assert world.ship.choose_need(a, 8) == ""


def test_personality_traits_change_needs_and_decisions():
    world = controlled_world()
    mara, ivo, nell, orin = world.ship.crew[:4]
    world.advance(10000)
    assert ivo.hunger > mara.hunger * 1.3
    assert ivo.loneliness > orin.loneliness
    ivo.loneliness = orin.loneliness = 50
    assert world.ship.choose_need(ivo) == "social"
    assert world.ship.choose_need(orin) == ""
    nell.boredom = 50
    assert world.ship.choose_need(nell) == "lookout"


def test_mood_memory_fades_and_stress_has_mechanical_consequences():
    world = controlled_world()
    c = world.ship.crew[0]
    c.think("A satisfying meal.", 5)
    world.advance(100)
    first = c.morale
    world.advance(10000)
    assert c.morale < first
    assert c.memories[0]["remaining"] == 1099
    c.stress = 90
    world.advance(100)
    assert c.morale < first - 20
    assert c.intent == "rest"


def test_direct_assignment_selects_only_named_crew_and_is_audited():
    world = controlled_world()
    a, b = world.ship.crew[:2]
    a.labors["repair"] = b.labors["repair"] = 1
    job = job_of(world, "repair")
    world.ship.assign(job.id, b.id)
    world.advance(100)
    assert job.worker == b.id and a.job_id is None
    assert any(r["event"] == "assigned" and r["crew"] == b.id for r in world.ship.audit)
    assert any(r["event"] == "claimed" and r["crew"] == b.id for r in world.ship.audit)
    assert not world.ship.check_orders()


def test_direct_order_respects_labor_off_and_urgent_needs():
    world = controlled_world()
    c = world.ship.crew[0]
    job = job_of(world, "repair")
    world.ship.assign(job.id, c.id)
    world.advance(100)
    assert job.worker is None and "OFF" in job_explanation(world, job)
    world.ship.set_labor(c.id, "repair", 1)
    c.thirst = 90
    world.advance(100)
    assert job.worker is None and "drink" in job_explanation(world, job)
    world.advance(12000)
    assert job.state == "done"
    assert c.thirst < 25
    assert not world.ship.check_orders()


def test_reassignment_returns_carried_materials_then_new_worker_takes_over(tmp_path):
    world = controlled_world()
    a, b = world.ship.crew[:2]
    a.labors["repair"] = b.labors["repair"] = 1
    job = job_of(world, "repair")
    world.ship.assign(job.id, a.id)
    before = materials(world.ship, "planks")
    for _ in range(100):
        world.advance(100)
        if a.cargo:
            break
    assert a.cargo == {"planks": 1}
    world.ship.assign(job.id, b.id)
    assert job.release_requested and job.worker == a.id
    assert materials(world.ship, "planks") == before
    assert not world.ship.check_orders()
    path = tmp_path / "reassign.json"
    world.save(path)
    world = World.load(path)
    world.advance(14000)
    assert world.ship.job(job.id).state == "done"
    assert any(r["event"] == "supplies_returned" for r in world.ship.audit)
    assert any(r["event"] == "claimed" and r["crew"] == b.id for r in world.ship.audit)
    assert not world.ship.check_orders()


def test_practice_only_grows_while_actually_working():
    world = controlled_world()
    c = world.ship.crew[4]  # quick learner
    c.labors["clean"] = 1
    job = job_of(world, "clean")
    world.ship.assign(job.id, c.id)
    world.advance(100)
    assert c.skill_xp["clean"] == 0  # travelling
    world.advance(6000)
    assert c.skill_xp["clean"] > 0
    assert c.skills["clean"] > 1
    assert any(r["event"] == "work_started" for r in world.ship.audit)


def test_compliance_audit_detects_a_broken_claim_instead_of_blind_pass():
    world = controlled_world()
    c = world.ship.crew[0]
    c.labors["repair"] = 1
    world.advance(100)
    assert not world.ship.check_orders()
    c.labors["repair"] = 0  # deliberate invariant violation (bypass public setter)
    assert any("disabled" in e for e in world.ship.check_orders())
    assert "FAIL" in work_rows(world)[0]


def test_compliance_detects_double_reserved_and_orphan_cargo():
    world = controlled_world()
    c = world.ship.crew[0]
    c.labors["repair"] = 1
    world.advance(100)
    assert world.ship.job(c.job_id).phase == "collect"
    c.cargo = {"planks": 1}
    assert any("twice" in e for e in world.ship.check_orders())
    c.cargo = {}
    other = world.ship.crew[1]
    other.cargo = {"shot": 1}
    assert any("no claim" in e for e in world.ship.check_orders())


def test_rest_sleep_and_assignment_save_compatibility(tmp_path):
    world = World()
    world.advance(5000)
    path = tmp_path / "crew.json"
    world.save(path)
    restored = World.load(path)
    assert json.dumps(world.ship.snapshot(), sort_keys=True) == json.dumps(restored.ship.snapshot(), sort_keys=True)
    data = json.loads(path.read_text())
    data["ship"].pop("audit")
    for row in data["ship"]["crew"]:
        for key in ("personality", "traits", "energy", "sleep_debt", "stress", "memories", "skill_xp"):
            row.pop(key)
    for row in data["ship"]["jobs"]:
        for key in ("assigned_to", "release_requested"):
            row.pop(key)
    path.write_text(json.dumps(data))
    legacy = World.load(path)
    legacy.advance(100)
    assert all(c.personality and c.traits for c in legacy.ship.crew)


@pytest.mark.parametrize("field,value", [("energy", -1), ("sleep_debt", 101), ("stress", float("nan")),
                                         ("personality", {}), ("traits", ["made_up"])])
def test_corrupt_detailed_needs_rejected(tmp_path, field, value):
    world = World()
    path = tmp_path / "bad.json"
    world.save(path)
    data = json.loads(path.read_text())
    data["ship"]["crew"][0][field] = value
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        World.load(path)


def test_assignment_ui_is_an_order_not_a_teleport_or_time_tick():
    class Scripted:
        def __init__(self):
            self.answers = iter([0, 3, 2, None])
        def render_list(self, *args, **kwargs):
            pass
        def choose(self, n):
            return next(self.answers)
    world = World()
    job = min(world.ship.jobs, key=lambda j: (j.priority, j.id))
    before = world.clock.total_ticks
    chores_menu(Scripted(), world)
    assert job.assigned_to == world.ship.crew[1].id
    assert job.worker is None and world.clock.total_ticks == before


def test_midnight_clock_chunking_is_deterministic():
    from tales_sim.clock import TICKS_PER_HOUR
    a, b = controlled_world(), controlled_world()
    a.clock.total_ticks = b.clock.total_ticks = 21 * TICKS_PER_HOUR - 500
    a.ship.crew[0].sleep_debt = b.ship.crew[0].sleep_debt = 50
    a.advance(2000)
    for _ in range(20):
        b.advance(100)
    assert a.ship.snapshot() == b.ship.snapshot()
    assert a.ship.crew[0].intent == "sleep"
