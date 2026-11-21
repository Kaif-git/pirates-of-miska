import pytest

from omnisim.world import tile as T
from pirates.game import World
from pirates.ship_building import candidate, queue, queue_blueprint
from pirates.ship_model import ShipModel
from pirates.ship_map import ORIGIN
from pirates.management import job_explanation
from pirates.ship_physics import calculate
from pirates.ship_storage import queue_transfer


def carpenter_world():
    world = World()
    # Isolate player-ordered work from automatic chores and personal needs.
    for c in world.ship.crew:
        c.labors = {k: 0 for k in c.labors}
        c.hunger = c.thirst = c.fatigue = c.loneliness = c.boredom = 0
        c.intent = ""
    sailor = world.ship.crew[0]
    sailor.labors["repair"] = 1
    for job in world.ship.jobs:
        world.ship.cancel(job.id)
    world.ship.hull = world.ship.rigging = 100
    world.ship.bilge = 0
    return world, sailor


def run_until(world, predicate, seconds=300):
    for _ in range(seconds):
        if predicate():
            return
        world.advance(100)
    assert predicate(), [(j.state, j.reason, j.phase) for j in world.ship.jobs]


def test_build_hauls_consumes_then_changes_collision():
    world, sailor = carpenter_world()
    before = world.ship.stores["planks"]
    ticks = world.clock.total_ticks
    job = queue(world, (78, 27), "build")
    assert world.clock.total_ticks == ticks
    assert world.game_map.is_walkable(78, 27)
    run_until(world, lambda: bool(sailor.cargo))
    assert sailor.cargo == {"planks": 1}
    assert world.game_map.is_walkable(78, 27)
    run_until(world, lambda: job.state == "done")
    assert world.game_map.tile_id(78, 27) == T.WALL_WOOD
    assert world.ship.stores["planks"] == before - 1
    assert sailor.cargo == {}
    # Building a wall must not perform an unrelated aggregate hull repair.
    assert world.ship.hull == 100


def test_dismantle_and_rebuild_door(tmp_path):
    world, sailor = carpenter_world()
    before = world.ship.stores["planks"]
    job = queue(world, (78, 20), "dismantle")
    run_until(world, lambda: job.state == "done")
    assert world.game_map.is_walkable(78, 20)
    assert world.ship.stores["planks"] == before
    job = queue(world, (78, 20), "build", "door")
    run_until(world, lambda: job.state == "done")
    assert world.game_map.tile_id(78, 20) == T.DOOR
    assert not world.game_map.doors[20, 78]
    path = tmp_path / "ship.json"
    world.save(path)
    assert World.load(path).game_map.tile_id(78, 20) == T.DOOR


def test_midhaul_save_resume_and_cancellation_conserve_material(tmp_path):
    world, sailor = carpenter_world()
    before = world.ship.stores["planks"]
    job = queue(world, (78, 27), "build", "door")
    run_until(world, lambda: bool(sailor.cargo))
    path = tmp_path / "ship.json"
    world.save(path)
    restored = World.load(path)
    restored_job = restored.ship.job(job.id)
    restored.ship.cancel(job.id)
    run_until(restored, lambda: restored_job.state == "cancelled")
    assert restored.ship.stores["planks"] == before
    assert restored.game_map.tile_id(78, 27) == T.FLOOR
    # The same mid-haul save also completes correctly without cancelling.
    restored = World.load(path)
    run_until(restored, lambda: restored.ship.job(job.id).state == "done")
    assert restored.game_map.tile_id(78, 27) == T.DOOR
    assert restored.ship.stores["planks"] == before - 1


def test_connected_floor_construction_midcarry_save_load_keeps_reachable_worksite(tmp_path):
    world, sailor = carpenter_world()
    target = (72, 13)  # Edge extension has one reachable adjacent work tile.
    before = world.ship.stores["planks"]
    world.player.x, world.player.y = target[0], target[1] + 1
    world.refresh()
    job = queue(world, target, "build", "floor")
    assert world.move(0, 1)  # Clear the sole work tile through normal movement.
    run_until(world, lambda: bool(sailor.cargo))
    assert job.state == "claimed" and job.phase == "work"
    assert world.ship.path(world, sailor, (*job.target, 0)) is not None

    path = tmp_path / "floor-midcarry.json"
    world.save(path)
    restored = World.load(path)
    restored_job = restored.ship.job(job.id)
    restored_sailor = restored.ship.sailor(sailor.id)
    assert restored_sailor.cargo == {"planks": 1}
    assert restored_job.state == "claimed" and restored_job.phase == "work"
    assert restored.ship.path(restored, restored_sailor, (*restored_job.target, 0)) is not None

    run_until(restored, lambda: restored_job.state == "done")
    assert any(component.kind == "floor" and (component.x, component.y, component.z) == (3, 1, 0)
               for component in restored.ship_model.components)
    assert restored.ship.stores["planks"] == before - 1
    assert not restored_sailor.cargo
    assert restored.ship.check_orders() == []


def test_missing_supplies_and_duplicate_order():
    world, sailor = carpenter_world()
    world.ship.stores["planks"] = 0
    job = queue(world, (78, 27), "build")
    world.advance(100)
    assert job.state == "blocked"
    assert "Missing" in job.reason
    with pytest.raises(ValueError, match="already exists"):
        queue(world, (78, 27), "build")
    assert not sailor.cargo


def test_direct_and_blueprint_orders_share_one_active_construction_site(tmp_path):
    world, _ = carpenter_world()
    direct = queue(world, (78, 27), "build", "wall")
    world.blueprint.set(9, 15, 0, "build", "wall")
    jobs_before = len(world.ship.jobs)
    with pytest.raises(ValueError, match="already exists"):
        queue_blueprint(world)
    assert len(world.ship.jobs) == jobs_before
    assert world.ship.job(direct.id) is direct
    assert world.blueprint.changes

    world, _ = carpenter_world()
    world.blueprint.set(9, 15, 0, "build", "wall")
    jobs = queue_blueprint(world)
    assert len(jobs) == 1
    path = tmp_path / "blueprint-work.json"
    world.save(path)
    restored = World.load(path)
    with pytest.raises(ValueError, match="already exists"):
        queue(restored, (78, 27), "build", "wall")
    assert len([job for job in restored.ship.jobs if job.construction]) == 1


def test_construction_tool_bonus_changes_real_assigned_worker_progress():
    ordinary, ordinary_worker = carpenter_world()
    equipped, equipped_worker = carpenter_world()
    hold_x, hold_y = equipped.ship.stations["hold"]
    equipped.player.x, equipped.player.y = hold_x - 1, hold_y
    equipped.economy.cargo = {"carpenter_tools": 1}
    equipped.economy.equip(equipped, equipped_worker.id, "carpenter_tools")
    ordinary.advance(1000)

    ordinary_job = queue(ordinary, (78, 27), "build", "wall")
    equipped_job = queue(equipped, (78, 27), "build", "wall")
    ordinary.ship.assign(ordinary_job.id, ordinary_worker.id)
    equipped.ship.assign(equipped_job.id, equipped_worker.id)
    ordinary.advance(6000)
    equipped.advance(6000)

    assert equipped.economy.bonus(equipped_worker.id, "labor", "repair") == 1.2
    assert equipped_job.progress > ordinary_job.progress


def test_missing_repair_labor_explains_and_recovers_construction_order():
    world, sailor = carpenter_world()
    for crew in world.ship.crew:
        crew.labors["repair"] = 0
    job = queue(world, (78, 27), "build", "wall")
    world.advance(100)
    assert job.state == "blocked"
    assert "No crew member has this labor enabled" in job_explanation(world, job)

    world.ship.set_labor(sailor.id, "repair", 1)
    run_until(world, lambda: bool(sailor.cargo))
    assert job.state == "claimed"
    assert sailor.cargo == {"planks": 1}


def test_built_deck_extension_adds_real_storage_and_preserves_transferred_stock():
    world, sailor = carpenter_world()
    base_floors = world.ship_model.deck_geometry(0)["tiles"]
    base_displacement = calculate(world)["displacement_limit_kg"]
    base_storage_capacity = sum(pile.capacity_kg for pile in world.ship.storage.piles.values())
    planks_before = world.ship.stores["planks"]

    target = (72, 13)  # Verified connected one-tile starter-deck extension.
    floor_job = queue(world, target, "build", "floor")
    run_until(world, lambda: floor_job.state == "done")
    expanded_floors = world.ship_model.deck_geometry(0)["tiles"]
    expanded_displacement = calculate(world)["displacement_limit_kg"]
    assert expanded_floors == base_floors + 1
    assert expanded_displacement > base_displacement

    pile = world.ship.storage.designate(world.ship_model, world.ship_fixtures,
                                        *target, capacity_kg=750)
    assert sum(p.capacity_kg for p in world.ship.storage.piles.values()) == base_storage_capacity + 750
    transfer = queue_transfer(world, "hold", pile.id, {"planks": 1})
    run_until(world, lambda: transfer.state == "done")
    assert world.ship.storage.piles["hold"].contents["planks"] == planks_before - 2
    assert pile.contents == {"planks": 1}
    assert world.ship.stores["planks"] == planks_before - 1
    assert not sailor.cargo
    assert world.ship.check_orders() == []


@pytest.mark.parametrize("target,action", [
    ((69, 25), "dismantle"),  # outer hull
    ((80, 15), "build"),      # helm
    ((10, 10), "build"),      # harbor
    ((72, 39), "build"),      # bunk
])
def test_protected_sites(target, action):
    world, _ = carpenter_world()
    with pytest.raises(ValueError):
        queue(world, target, action)


def test_dismantling_a_floor_bridge_cannot_split_deck_geometry():
    world, _ = carpenter_world()
    model = ShipModel()
    model.place(5, 5, 0, "floor")
    bridge = model.place(6, 5, 0, "floor")
    model.place(7, 5, 0, "floor")
    world.ship_model = model
    target = (ORIGIN[0] + 6, ORIGIN[1] + 5)
    with pytest.raises(ValueError, match="split the deck"):
        candidate(world, target, {"action": "dismantle", "kind": "floor",
                                 "component_id": bridge.id})
    assert [c.id for c in world.ship_model.components] == [1, 2, 3]


def test_occupancy_blocks_completion_without_losing_supplies():
    world, sailor = carpenter_world()
    job = queue(world, (78, 27), "build")
    run_until(world, lambda: bool(sailor.cargo))
    world.player.x, world.player.y = 78, 27
    world.advance(15000)
    assert job.state != "done"
    assert sailor.cargo == {"planks": 1}
    assert world.game_map.is_walkable(78, 27)
    world.player.x, world.player.y = 76, 25
    run_until(world, lambda: job.state == "done")


def test_upper_deck_wall_build_uses_stairs_and_persists(tmp_path):
    world, sailor = carpenter_world()
    for x in range(3, 6):
        world.ship_model.place(x, 2, 1, "floor")
    world.ship_model.place(3, 2, 0, "stair")
    target = (ORIGIN[0] + 5, ORIGIN[1] + 2)
    job = queue(world, target, "build", "wall", z=1)
    run_until(world, lambda: bool(sailor.cargo))
    save_path = tmp_path / "upper-construction.json"
    world.save(save_path)
    world = World.load(save_path)
    job = world.ship.job(job.id)
    sailor = world.ship.sailor("mara")
    run_until(world, lambda: job.state == "done")
    assert any(c.kind == "wall" and (c.x, c.y, c.z) == (5, 2, 1)
               for c in world.ship_model.components)
    world.set_active_deck(1)
    assert world.game_map.tile_id(*target) == T.WALL_WOOD


def test_upper_deck_work_order_requires_stair_reachability():
    world, _ = carpenter_world()
    world.ship_model.place(3, 2, 1, "floor")
    world.ship_model.place(4, 2, 1, "floor")
    target = (ORIGIN[0] + 4, ORIGIN[1] + 2)
    with pytest.raises(ValueError, match="stair-connected"):
        queue(world, target, "build", "wall", z=1)


def test_stair_is_a_physical_job_and_opens_saved_cross_deck_path(tmp_path):
    world, sailor = carpenter_world()
    world.ship_model.place(3, 2, 1, "floor")
    target = (ORIGIN[0] + 3, ORIGIN[1] + 2)
    job = queue(world, target, "build", "stair", z=0)
    run_until(world, lambda: job.state == "done")
    assert any(c.kind == "stair" and (c.x, c.y, c.z) == (3, 2, 0)
               for c in world.ship_model.components)
    path = tmp_path / "stair-build.json"
    world.save(path)
    restored = World.load(path)
    assert any(c.kind == "stair" and (c.x, c.y, c.z) == (3, 2, 0)
               for c in restored.ship_model.components)


def test_first_upper_floor_can_be_raised_from_lower_landing_then_staired():
    world, sailor = carpenter_world()
    x, y = 5, 5
    target = (ORIGIN[0] + x, ORIGIN[1] + y)
    job = queue(world, target, "build", "floor", z=1)
    run_until(world, lambda: job.state == "done")
    assert any(c.kind == "floor" and (c.x, c.y, c.z) == (x, y, 1)
               for c in world.ship_model.components)
    stair_job = queue(world, target, "build", "stair", z=0)
    run_until(world, lambda: stair_job.state == "done")
    assert any(c.kind == "stair" and (c.x, c.y, c.z) == (x, y, 0)
               for c in world.ship_model.components)
    assert world.ship.path(world, sailor, (target[0] + 1, target[1], 1)) is not None


def test_no_entombment_by_combined_orders():
    world, _ = carpenter_world()
    # Restrict the captain's only two exits using existing corner walls.
    world.player.x, world.player.y = 70, 21
    first = queue(world, (71, 21), "build")
    run_until(world, lambda: first.state == "done")
    with pytest.raises(ValueError, match="strand"):
        queue(world, (70, 22), "build")


def test_beam_is_a_physical_construction_order_with_live_projection():
    world, _ = carpenter_world()
    before = world.ship.stores["planks"]
    job = queue(world, (78, 27), "build", "beam")
    run_until(world, lambda: job.state == "done")
    beam = next(c for c in world.ship_model.components
                if c.kind == "beam" and (c.x, c.y, c.z) == (9, 15, 0))
    assert beam.material == "oak"
    assert world.game_map.is_walkable(78, 27)
    assert world.ship.stores["planks"] == before - 1
    job = queue(world, (78, 27), "dismantle", "beam")
    run_until(world, lambda: job.state == "done")
    assert not any(c.kind == "beam" for c in world.ship_model.components)


def test_beam_can_bridge_an_empty_lower_cell_but_needs_a_bearing():
    world, _ = carpenter_world()
    model = ShipModel()
    model.place(5, 5, 0, "floor")
    world.ship_model = model
    target = (ORIGIN[0] + 6, ORIGIN[1] + 5, 0)
    result = candidate(world, target,
                       {"action": "build", "kind": "beam", "component_id": None})
    assert any(c.kind == "beam" and (c.x, c.y, c.z) == (6, 5, 0)
               for c in result.components)
    with pytest.raises(ValueError, match="connect"):
        candidate(world, (ORIGIN[0] + 10, ORIGIN[1] + 5, 0),
                  {"action": "build", "kind": "beam", "component_id": None})
