import json

import pytest

from omnisim.world import tile as T
from pirates.game import World
from pirates.ship_relocation import queue


def relocation_world():
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
    return world, world.ship.crew[0]


def until(world, predicate, limit=400):
    for _ in range(limit):
        if predicate():
            return
        world.advance(100)
    assert predicate(), [(j.state, j.reason, j.relocation) for j in world.ship.jobs]


def test_pickup_haul_install_real_geometry_and_identity():
    world, sailor = relocation_world()
    before = world.ship.stores.copy()
    job = queue(world, "galley", (78, 27))
    ticks = world.clock.total_ticks
    assert world.fixtures.world_position(world.fixtures.get("galley")) == (72, 36)
    assert world.clock.total_ticks == ticks
    until(world, lambda: job.relocation["stage"] == "haul")
    assert world.fixtures.get("galley").carrier == sailor.id
    assert world.game_map.tile_id(72, 36) == T.FLOOR
    assert world.game_map.tile_id(78, 27) == T.FLOOR
    assert world.ship.check_orders() == []
    until(world, lambda: job.state == "done")
    assert world.ship.stations["galley"] == (78, 27)
    assert world.game_map.tile_id(78, 27) == T.HEARTH
    assert world.fixtures.get("galley").carrier is None
    assert world.ship.stores == before
    assert world.ship.hull == 100
    assert world.ship.check_orders() == []


def test_midhaul_save_continues_or_returns_on_cancel(tmp_path):
    world, sailor = relocation_world()
    job = queue(world, "bunk:0", (78, 38))
    until(world, lambda: job.relocation["stage"] == "haul")
    path = tmp_path / "ship.json"
    world.save(path)
    completed = World.load(path)
    until(completed, lambda: completed.ship.job(job.id).state == "done")
    assert completed.ship.bunks[0] == (78, 38)
    assert completed.game_map.tile_id(72, 39) == T.FLOOR
    cancelled = World.load(path)
    cancelled.ship.cancel(job.id)
    assert cancelled.ship.job(job.id).state == "returning"
    assert cancelled.fixtures.get("bunk:0").carrier == sailor.id
    until(cancelled, lambda: cancelled.ship.job(job.id).state == "cancelled")
    assert cancelled.ship.bunks[0] == (72, 39)
    assert cancelled.game_map.tile_id(72, 39) == T.BED
    assert cancelled.game_map.tile_id(78, 38) == T.FLOOR
    assert cancelled.ship.check_orders() == []


def test_cancel_before_pickup_leaves_equipment():
    world, _ = relocation_world()
    job = queue(world, "galley", (78, 27))
    world.ship.cancel(job.id)
    assert job.state == "cancelled"
    assert world.game_map.tile_id(72, 36) == T.HEARTH
    assert world.fixtures.get("galley").carrier is None


def test_moving_equipment_is_not_usable():
    world, sailor = relocation_world()
    job = queue(world, "water", (78, 27))
    until(world, lambda: job.relocation["stage"] == "haul")
    other = world.ship.crew[1]
    before = world.ship.stores["water"]
    other.x, other.y = 74, 29
    other.thirst = 90
    for _ in range(8):
        world.ship.satisfy(world, other, "drink")
    assert "Waiting for relocated equipment" in other.activity
    assert world.ship.stores["water"] == before
    assert other.thirst == 90


def test_destination_occupied_after_pickup_waits_without_loss():
    world, _ = relocation_world()
    job = queue(world, "galley", (78, 27))
    until(world, lambda: job.relocation["stage"] == "haul")
    world.player.x, world.player.y = 78, 27
    world.advance(10000)
    assert job.state != "done"
    assert world.fixtures.get("galley").carrier is not None
    world.player.x, world.player.y = 76, 25
    until(world, lambda: job.state == "done")


def test_labor_off_returns_equipment_before_releasing_claim(tmp_path):
    world, sailor = relocation_world()
    job = queue(world, "galley", (78, 27))
    until(world, lambda: job.relocation["stage"] == "haul")
    world.ship.set_labor(sailor.id, "repair", 0)
    assert job.release_requested
    path = tmp_path / "ship.json"
    world.save(path)
    world = World.load(path)
    job = world.ship.job(job.id)
    until(world, lambda: job.worker is None)
    assert world.fixtures.get("galley").carrier is None
    assert world.ship.stations["galley"] == (72, 36)
    assert job.state in ("pending", "blocked")
    assert world.ship.check_orders() == []


def test_active_station_job_retargets_after_relocation(tmp_path):
    world, _ = relocation_world()
    world.ship.meals = 0
    world.ship.generate_jobs()
    cook = next(j for j in world.ship.jobs if j.kind == "cook")
    move = queue(world, "galley", (78, 27))
    until(world, lambda: move.state == "done")
    assert cook.target == (78, 27)
    path = tmp_path / "ship.json"
    world.save(path)
    assert World.load(path).ship.job(cook.id).target == (78, 27)


@pytest.mark.parametrize("fid,target", [
    ("unknown", (78, 27)), ("galley", (88, 40)),
    ("galley", (78, 20)), ("galley", (10, 10)),
    ("galley", (72, 36)),
])
def test_invalid_relocations(fid, target):
    world, _ = relocation_world()
    with pytest.raises(ValueError):
        queue(world, fid, target)


def test_duplicate_reservations_and_construction_conflict():
    from pirates.ship_building import queue as build
    world, _ = relocation_world()
    queue(world, "galley", (78, 27))
    with pytest.raises(ValueError):
        queue(world, "water", (78, 27))
    with pytest.raises(ValueError):
        queue(world, "galley", (79, 27))
    with pytest.raises(ValueError):
        build(world, (78, 27), "build")


def test_stocked_hold_moves_with_its_contents_and_mass():
    world, sailor = relocation_world()
    before = world.ship.stores.copy()
    job = queue(world, "hold", (78, 27))
    assert world.ship.storage.fixture_mass("hold") > 0
    until(world, lambda: job.relocation["stage"] == "haul")
    assert not world.ship.storage.available("hold", world.fixtures)
    until(world, lambda: job.state == "done", limit=3000)
    assert world.ship.stations["hold"] == (78, 27)
    assert world.ship.storage.position("hold", world.fixtures) == (78, 27)
    assert world.ship.stores == before
    assert not world.ship.check_orders()


def test_orphan_equipment_carrier_save_rejected(tmp_path):
    world, sailor = relocation_world()
    path = tmp_path / "ship.json"
    world.save(path)
    data = json.loads(path.read_text())
    data["fixtures"][0]["carrier"] = sailor.id
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="orphaned equipment"):
        World.load(path)


def test_heavy_equipment_hauling_is_mass_scaled():
    world, sailor = relocation_world()
    sailor.x, sailor.y = 89, 28
    job = queue(world, "starboard_cannon", (85, 28))
    until(world, lambda: job.relocation["stage"] == "haul")
    before = sailor.x, sailor.y
    until(world, lambda: (sailor.x, sailor.y) != before)
    assert job.relocation["move_wait"] == 23  # ceil(700 / 30) - 1
    position = sailor.x, sailor.y
    world.advance(2300)
    assert (sailor.x, sailor.y) == position
    assert job.relocation["move_wait"] == 0
    until(world, lambda: job.state == "done", limit=600)
    assert world.ship.stations["starboard_cannon"] == (85, 28)


def test_need_pause_returns_equipment_before_using_it():
    world, sailor = relocation_world()
    job = queue(world, "bunk:0", (78, 38))
    until(world, lambda: job.relocation["stage"] == "haul")
    sailor.sleep_debt = 95
    world.advance(100)
    assert job.release_requested
    until(world, lambda: world.fixtures.get("bunk:0").carrier is None)
    assert world.ship.bunks[0] == (72, 39)
    assert job.state in ("pending", "blocked")
    assert world.ship.check_orders() == []


def test_terminal_job_history_survives_later_equipment_moves(tmp_path):
    world, _ = relocation_world()
    world.ship.meals = 0
    world.ship.generate_jobs()
    cook = next(j for j in world.ship.jobs if j.kind == "cook")
    # Terminal history is read-only and retains the original work position.
    cook.state = "done"
    first = queue(world, "galley", (78, 27))
    until(world, lambda: first.state == "done")
    second = queue(world, "galley", (79, 27))
    until(world, lambda: second.state == "done")
    assert cook.target == (72, 36)
    path = tmp_path / "ship.json"
    world.save(path)
    assert World.load(path).ship.job(cook.id).target == (72, 36)


def test_relocation_menu_is_paused_and_queues_adjacent_destination():
    from pirates.management import relocation_menu
    world, _ = relocation_world()
    world.player.x, world.player.y = 78, 28
    fixtures = list(world.fixtures.fixtures)
    selected = next(i for i, f in enumerate(fixtures) if f.id == "galley")

    class Renderer:
        choices = iter([selected, 0])

        def render_list(self, *args, **kwargs):
            pass

        def choose(self, count):
            return next(self.choices)

    ticks = world.clock.total_ticks
    relocation_menu(Renderer(), world)
    job = next(j for j in world.ship.jobs if j.relocation)
    assert job.target == (78, 27)
    assert world.clock.total_ticks == ticks
