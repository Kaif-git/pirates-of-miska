import json

import pytest

from pirates.game import World
from pirates.ship_storage import HAUL_CAPACITY_KG, bundle_mass, queue_transfer
from pirates.management import storage_menu


def storage_world():
    world = World()
    world.ship.jobs = []
    world.ship.hull = world.ship.rigging = 100
    world.ship.bilge = 0
    world.ship.meals = 20
    world.ship.ammo = {k: 1 for k in world.ship.ammo}
    world.ship.dirt = {k: 0 for k in world.ship.dirt}
    for c in world.ship.crew:
        c.labors = {k: 0 for k in c.labors}
        for key in ("hunger", "thirst", "fatigue", "sleep_debt", "stress", "loneliness", "boredom"):
            setattr(c, key, 0)
        c.energy = 100
        c.intent = ""
    world.ship.crew[0].labors["repair"] = 1
    pile = world.ship.storage.designate(world.ship_model, world.ship_fixtures, 78, 27)
    return world, world.ship.crew[0], pile


def until(world, predicate, limit=600):
    for _ in range(limit):
        if predicate():
            return
        world.advance(100)
    assert predicate(), [(j.state, j.reason, j.phase) for j in world.ship.jobs]


def test_stockpile_transfer_physically_collects_and_delivers():
    world, sailor, destination = storage_world()
    source = world.ship.storage.piles["hold"]
    before = source.contents["planks"]
    job = queue_transfer(world, "hold", destination.id, {"planks": 1})
    assert source.contents["planks"] == before
    until(world, lambda: bool(sailor.cargo))
    assert sailor.cargo == {"planks": 1}
    assert source.contents["planks"] == before - 1
    assert destination.contents == {}
    until(world, lambda: job.state == "done")
    assert destination.contents == {"planks": 1}
    assert world.ship.stores["planks"] == before
    assert not sailor.cargo
    assert world.ship.check_orders() == []


def test_split_job_supply_sources_are_reserved_and_walked():
    world, sailor, destination = storage_world()
    storage = world.ship.storage
    storage.piles["hold"].contents["raw_food"] = 1
    source2 = storage.designate(world.ship_model, world.ship_fixtures, 79, 27, 500)
    source2.contents["raw_food"] = 1
    job = world.ship.add_job("test:split", "cook", world.ship.stations["galley"], work=1)
    job.reserved = world.ship.job_cost(job)
    sailor.labors["cook"] = 1
    allocations = storage.plan(world, sailor, job.reserved)
    assert allocations and len(allocations) == 3
    storage.reserve(allocations)
    job.supply_plan = allocations
    job.state, job.worker, sailor.job_id = "claimed", sailor.id, job.id
    job.phase = "collect"
    for _ in range(200):
        world.advance(100)
        if job.phase == "work":
            break
    assert job.phase == "work"
    assert sailor.cargo == {"raw_food": 2, "water": 1}
    assert sum(p.contents.get("raw_food", 0) for p in storage.piles.values()) == 0
    assert world.ship.check_orders() == []


def test_transfer_midhaul_save_resume_and_cancellation_returns_source(tmp_path):
    world, sailor, destination = storage_world()
    source = world.ship.storage.piles["hold"]
    original = source.contents.copy()
    job = queue_transfer(world, "hold", destination.id, {"shot": 3})
    until(world, lambda: bool(sailor.cargo))
    path = tmp_path / "ship.json"
    world.save(path)
    resumed = World.load(path)
    until(resumed, lambda: resumed.ship.job(job.id).state == "done")
    assert resumed.ship.storage.piles[destination.id].contents == {"shot": 3}
    cancelled = World.load(path)
    cancelled.ship.cancel(job.id)
    until(cancelled, lambda: cancelled.ship.job(job.id).state == "cancelled")
    assert cancelled.ship.storage.piles["hold"].contents == original
    assert cancelled.ship.storage.piles[destination.id].contents == {}
    assert cancelled.ship.check_orders() == []


def test_spatial_job_reservations_prevent_duplicate_claims():
    world, _, destination = storage_world()
    a = world.ship.add_job("test:a", "repair", world.ship.stations["repair"])
    b = world.ship.add_job("test:b", "repair", world.ship.stations["repair"])
    sailor = world.ship.crew[0]
    aa = world.ship.storage.plan(world, sailor, {"planks": 1})
    world.ship.storage.reserve(aa)
    a.reserved, a.supply_plan = {"planks": 1}, aa
    a.state, a.worker, sailor.job_id = "claimed", sailor.id, a.id
    bb = world.ship.storage.plan(world, world.ship.crew[1], {"planks": 16})
    assert bb is None
    aa = world.ship.storage.plan(world, sailor, {"raw_food": 30})
    assert aa is not None
    world.ship.storage.reserve(aa)
    a.supply_plan = aa
    a.reserved = {"planks": 1, "raw_food": 30}
    with pytest.raises(ValueError, match="unreserved"):
        queue_transfer(world, "hold", destination.id, {"raw_food": 30})


def test_transfer_capacity_and_weight_limits():
    world, _, destination = storage_world()
    destination.capacity_kg = 10
    with pytest.raises(ValueError, match="capacity"):
        queue_transfer(world, "hold", destination.id, {"shot": 3})
    destination.capacity_kg = 100
    with pytest.raises(ValueError, match="limited"):
        queue_transfer(world, "hold", destination.id, {"planks": 3})
    assert HAUL_CAPACITY_KG == 50
    assert bundle_mass({"shot": 2}) == 10


def test_designation_and_removal_validate_contents_and_reservations():
    world, _, pile = storage_world()
    world.ship.storage.und_designate(pile.id, world.ship.jobs)
    with pytest.raises(ValueError):
        world.ship.storage.und_designate(pile.id)
    world, _, pile = storage_world()
    pile.contents["shot"] = 1
    with pytest.raises(ValueError):
        world.ship.storage.und_designate(pile.id)
    pile.contents.clear()
    world.ship.storage.und_designate(pile.id)
    with pytest.raises(ValueError):
        world.ship.storage.designate(world.ship_model, world.ship_fixtures, 72, 36)
    with pytest.raises(ValueError):
        world.ship.storage.designate(world.ship_model, world.ship_fixtures, 78, 20)


def test_spatial_storage_survives_save_and_legacy_v3_migrates(tmp_path):
    world, _, pile = storage_world()
    pile.contents["shot"] = 4
    path = tmp_path / "ship.json"
    world.save(path)
    loaded = World.load(path)
    assert loaded.ship.storage.snapshot() == world.ship.storage.snapshot()
    assert dict(loaded.ship.stores) == dict(world.ship.stores)
    data = json.loads(path.read_text())
    del data["ship"]["stockpiles"]
    data["ship"]["stores"] = {"raw_food": 30, "water": 60, "planks": 16, "shot": 16}
    data["ship"]["jobs"] = []
    data["ship"]["next_id"] = 1
    data["ship"]["crew"] = [dict(c, job_id=None, cargo={}) for c in data["ship"]["crew"]]
    path.write_text(json.dumps(data))
    legacy = World.load(path)
    assert dict(legacy.ship.stores) == {"raw_food": 30, "water": 60, "planks": 16, "shot": 16}


def test_legacy_aggregate_save_places_piles_at_migrated_fixture_locations(tmp_path):
    from pirates.ship_fixtures import FixtureLayout
    world, _, _ = storage_world()
    world.fixtures = world.fixtures.relocated("hold", 78, 27)
    world.ship.layout = world.fixtures
    path = tmp_path / "legacy.json"
    world.save(path)
    data = json.loads(path.read_text())
    data["ship"].pop("stockpiles")
    path.write_text(json.dumps(data))
    restored = World.load(path)
    assert restored.ship.storage.position("hold", restored.fixtures) == (78, 27)
    assert restored.ship.stores["planks"] == 16


def test_storage_menu_designates_and_transfer_ui_is_paused():
    world, _, _ = storage_world()
    world.ship.storage.und_designate("pile:9,15")
    # Direct UI test starts without this test fixture's pile.
    world, _, _ = storage_world()
    world.ship.storage.und_designate("pile:9,15")
    world.player.x, world.player.y = 77, 27

    class Renderer:
        choices = iter([1, 0, None])

        def render_list(self, *args, **kwargs):
            pass

        def choose(self, count):
            return next(self.choices)

        def wait_any(self):
            pass

    ticks = world.clock.total_ticks
    storage_menu(Renderer(), world)
    assert "pile:8,14" in world.ship.storage.piles
    assert world.clock.total_ticks == ticks
