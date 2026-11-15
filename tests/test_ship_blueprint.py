import pytest

from pirates.game import World
from pirates.ship_blueprint import ShipBlueprint
from pirates.ship_building import queue_blueprint, queue
from pirates.ship_map import ORIGIN
from pirates.ship_model import ShipModel


def test_blueprint_marks_undoes_and_roundtrips_unbuilt_edits(tmp_path):
    world = World()
    blueprint = world.blueprint
    before = world.ship_model.snapshot()
    blueprint.set(9, 15, 0, "build", "wall")
    assert blueprint.apply(world.ship_model) != world.ship_model
    assert world.ship_model.snapshot() == before
    path = tmp_path / "ship.json"
    world.save(path)
    restored = World.load(path)
    assert restored.blueprint.snapshot() == blueprint.snapshot()
    assert restored.blueprint.undo()
    assert not restored.blueprint.changes


def test_copy_mirror_and_rotate_are_bounded_and_undoable():
    world = World()
    bp = world.blueprint
    # Capture an existing 2x2 piece of deck, then copy it to a clear region.
    count = bp.transform_selection(world.ship_model, 9, 8, 9, 8, (9, 15), mirror_x=True, rotate=90)
    assert count > 0
    proposed = bp.apply(world.ship_model)
    added = [c for c in proposed.components if c.id > max(c.id for c in world.ship_model.components)]
    assert added
    assert bp.undo()
    assert not bp.changes
    with pytest.raises(ValueError, match="beyond"):
        bp.transform_selection(world.ship_model, 8, 14, 9, 15, (22, 31), rotate=90)


def test_line_rectangle_and_symmetry_are_single_undoable_gestures():
    world = World()
    bp = world.blueprint
    bp.draw_line(world.ship_model, (1, 1), (4, 1), "build", "wall")
    assert {(r["x"], r["y"]) for r in bp.changes} == {(1, 1), (2, 1), (3, 1), (4, 1)}
    bp.draw_rectangle(world.ship_model, (2, 3), (4, 5), "build", "wall", symmetry=True)
    expected = {(2, 3), (3, 3), (4, 3), (2, 4), (4, 4), (2, 5), (3, 5), (4, 5)}
    expected |= {(22 - x, y) for x, y in expected}
    actual = {(r["x"], r["y"]) for r in bp.changes if r["y"] >= 3}
    assert actual == expected
    assert bp.undo()
    assert len(bp.changes) == 4
    assert bp.undo()
    assert bp.changes == []


def test_flood_fill_fills_only_enclosed_same_state_region_and_undoes():
    model = ShipModel()
    bp = ShipBlueprint()
    for x in range(3):
        for y in range(3):
            if (x, y) != (1, 1):
                model.place(x, y, 0, "floor")
    count = bp.flood_fill(model, (1, 1), "build", "floor")
    assert count == 1
    assert bp.changes == [{"x": 1, "y": 1, "z": 0, "action": "build", "kind": "floor"}]
    assert bp.undo() and bp.changes == []
    with pytest.raises(ValueError, match="outside"):
        bp.flood_fill(model, (4, 4), "build", "floor")


def test_blueprint_queue_becomes_physical_jobs_and_clears_plan():
    world = World()
    world.blueprint.set(9, 15, 0, "build", "wall")
    jobs = queue_blueprint(world)
    assert len(jobs) == 1 and jobs[0].construction["kind"] == "wall"
    assert not world.blueprint.changes
    assert not any(c.kind == "wall" for c in world.ship_model.at(9, 15, 0))


def test_blueprint_cannot_bypass_exterior_hull_dismantle_guard():
    world = World()
    # The west-side wall is adjacent to open water; a blueprint must obey the
    # same hull-opening gate as the direct construction menu.
    assert any(c.kind == "wall" for c in world.ship_model.at(0, 5, 0))
    world.blueprint.set(0, 5, 0, "dismantle", "wall")
    with pytest.raises(ValueError, match="outer hull"):
        queue_blueprint(world)
    assert world.blueprint.changes
    assert any(c.kind == "wall" for c in world.ship_model.at(0, 5, 0))


def test_floor_construction_and_save_validation(tmp_path):
    world = World()
    # This empty tile directly touches the starter deck, so it can extend the deck physically.
    target = (72, 13)
    for c in world.ship.crew:
        c.labors = {k: 0 for k in c.labors}
        c.intent = ""
        c.hunger = c.thirst = c.fatigue = c.loneliness = c.boredom = 0
    world.ship.hull = world.ship.rigging = 100
    world.ship.bilge = 0
    for active in world.ship.jobs:
        world.ship.cancel(active.id)
    carpenter = world.ship.crew[0]
    carpenter.labors["repair"] = 1
    job = queue(world, target, "build", "floor")
    world.ship.assign(job.id, carpenter.id)
    path = tmp_path / "floor.json"
    world.save(path)
    restored = World.load(path)
    assert restored.ship.job(job.id).construction["kind"] == "floor"
    for _ in range(900):
        if restored.ship.job(job.id).state == "done":
            break
        restored.advance(100)
    assert restored.ship.job(job.id).state == "done"
    assert any(c.kind == "floor" for c in restored.ship_model.at(target[0] - ORIGIN[0], target[1] - ORIGIN[1], 0))


def test_nonrectangular_floor_extension_is_queued_and_built_as_separate_hauls(tmp_path):
    world = World()
    for c in world.ship.crew:
        c.labors = {k: 0 for k in c.labors}
        c.intent = ""
        c.hunger = c.thirst = c.fatigue = c.loneliness = c.boredom = 0
    world.ship.hull = world.ship.rigging = 100
    world.ship.bilge = 0
    for active in world.ship.jobs:
        world.ship.cancel(active.id)
    worker = world.ship.crew[0]
    worker.labors["repair"] = 1
    # An L-shaped deck extension: two cells along one axis and one returning cell.
    world.blueprint.set(3, 1, 0, "build", "floor")
    world.blueprint.set(3, 0, 0, "build", "floor")
    world.blueprint.set(4, 0, 0, "build", "floor")
    before = world.ship.stores["planks"]
    before_geometry = world.ship_model.deck_geometry(0)
    jobs = queue_blueprint(world)
    assert len(jobs) == 3
    world.ship.assign(jobs[0].id, worker.id)
    save = tmp_path / "freeform.json"
    world.save(save)
    resumed = World.load(save)
    resumed_jobs = [resumed.ship.job(job.id) for job in jobs]
    for _ in range(3000):
        if all(job.state == "done" for job in resumed_jobs):
            break
        resumed.advance(100)
    assert all(job.state == "done" for job in resumed_jobs)
    assert resumed.ship.stores["planks"] == before - 3
    assert all(any(c.kind == "floor" for c in resumed.ship_model.at(
        job.target[0] - ORIGIN[0], job.target[1] - ORIGIN[1], 0)) for job in resumed_jobs)
    assert resumed.ship.check_orders() == []
    after_geometry = resumed.ship_model.deck_geometry(0)
    assert after_geometry["tiles"] == before_geometry["tiles"] + 3
    assert after_geometry["perimeter"] != before_geometry["perimeter"]


def test_blueprint_disconnect_and_material_shortfall_are_atomic():
    world = World()
    world.blueprint.set(9, 15, 0, "build", "wall")
    world.ship.stores["planks"] = 0
    before = len(world.ship.jobs)
    with pytest.raises(ValueError, match="plank"):
        queue_blueprint(world)
    assert len(world.ship.jobs) == before
    assert len(world.blueprint.changes) == 1


def test_floor_jobs_consume_planks_and_noop_copy_cannot_create_free_work():
    world = World()
    world.blueprint.set(3, 1, 0, "build", "floor")
    world.ship.stores["planks"] = 0
    with pytest.raises(ValueError, match="plank"):
        queue_blueprint(world)
    world.blueprint.changes.clear()
    world.blueprint.history.clear()
    # (2,2) is already a floor; a transform that recopies it is not an order.
    world.blueprint.set(2, 2, 0, "build", "floor")
    with pytest.raises(ValueError, match="already exists"):
        queue_blueprint(world)


def test_floor_extension_requires_a_reachable_physical_work_position():
    world = World()
    with pytest.raises(ValueError, match="reachable adjacent work site"):
        queue(world, (71, 13), "build", "floor")


def test_blueprint_schema_rejects_bad_records():
    with pytest.raises(ValueError):
        ShipBlueprint.restore({"version": 1, "changes": [{"x": 999, "y": 0, "z": 0,
                                                          "action": "build", "kind": "wall"}], "history": []})


def test_f8_is_bound_by_game_adapter_without_modifying_vendored_engine():
    import play  # noqa: F401 - installs the app-level command adapter
    from omnisim.ui.ascii_renderer import world_action_for_key
    from tcod.event import KeySym
    assert world_action_for_key(KeySym.F8) == ("blueprint",)
