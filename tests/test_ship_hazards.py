import pytest

from pirates.game import World
from pirates.ship_hazards import ShipHazards
from pirates.ship_map import ORIGIN
from pirates.ship_physics import calculate
from pirates.ship_building import queue


def exterior_wall(world):
    return next(c for c in world.ship_model.components
                if c.kind == "wall" and ShipHazards._outer_wall(world, c.x, c.y, c.z))


def test_breach_opens_real_outer_wall_and_floods_neighboring_compartment():
    world = World()
    world.ship.hull = 100  # isolate the new opening from legacy seepage.
    wall = exterior_wall(world)
    initial = world.hazards.total_water_kg()
    key = world.hazards.breach(world, wall.x, wall.y, wall.z)
    assert all(c.id != wall.id for c in world.ship_model.components)
    assert key in world.hazards.breaches
    assert world.game_map.is_walkable(ORIGIN[0] + wall.x, ORIGIN[1] + wall.y)
    for _ in range(30):
        world.hazards.advance(world)
    assert world.hazards.total_water_kg() > initial + 15
    assert len(world.hazards.water) > 1
    assert world.ship.bilge == pytest.approx(world.hazards.total_water_kg() / 10)


def test_pumping_removes_local_water_and_updates_bilge_and_load():
    world = World()
    world.ship.hull = 100
    wall = exterior_wall(world)
    world.hazards.breach(world, wall.x, wall.y)
    for _ in range(300):
        world.hazards.advance(world)
    before = world.hazards.total_water_kg()
    mass_before = calculate(world)["mass_kg"]["floodwater"]
    removed = world.hazards.pump(120, world)
    world.ship.bilge = world.hazards.total_water_kg() / 10
    assert removed == pytest.approx(120)
    assert world.hazards.total_water_kg() == pytest.approx(before - 120)
    assert calculate(world)["mass_kg"]["floodwater"] == pytest.approx(mass_before - 120)
    assert world.ship.bilge == pytest.approx(world.hazards.total_water_kg() / 10)


def test_breach_and_flooding_roundtrip_and_legacy_bilge_migration(tmp_path):
    world = World()
    wall = exterior_wall(world)
    world.hazards.breach(world, wall.x, wall.y)
    for _ in range(15):
        world.hazards.advance(world)
    path = tmp_path / "flooded.json"
    world.save(path)
    restored = World.load(path)
    assert restored.hazards.snapshot() == world.hazards.snapshot()
    assert restored.ship.bilge == pytest.approx(world.ship.bilge)
    assert calculate(restored) == calculate(world)

    data = __import__("json").loads(path.read_text())
    data.pop("ship_hazards")
    path.write_text(__import__("json").dumps(data))
    migrated = World.load(path)
    assert migrated.hazards.total_water_kg() == pytest.approx(migrated.ship.bilge * 10)


def test_invalid_breach_state_rejected_and_only_exterior_wall_can_be_breached():
    world = World()
    interior = next(c for c in world.ship_model.components
                    if c.kind == "wall" and not ShipHazards._outer_wall(world, c.x, c.y, c.z))
    with pytest.raises(ValueError, match="exterior"):
        world.hazards.breach(world, interior.x, interior.y, interior.z)
    data = world.hazards.snapshot()
    data["water"] = [[interior.x, interior.y, interior.z, float("nan")]]
    with pytest.raises(ValueError, match="breach/flood"):
        ShipHazards.restore(data, world.ship_model)


def test_physical_hull_patch_rebuilds_wall_and_seals_breach():
    world = World()
    for sailor in world.ship.crew:
        sailor.labors = {key: 0 for key in sailor.labors}
        sailor.hunger = sailor.thirst = sailor.fatigue = sailor.loneliness = sailor.boredom = 0
        sailor.intent = ""
    worker = world.ship.crew[0]
    worker.labors["repair"] = 1
    for job in world.ship.jobs:
        world.ship.cancel(job.id)
    world.ship.hull = 100
    world.hazards.water.clear()
    world.ship.bilge = 0
    breach_wall = exterior_wall(world)
    world.hazards.breach(world, breach_wall.x, breach_wall.y)
    job = queue(world, (ORIGIN[0] + breach_wall.x, ORIGIN[1] + breach_wall.y),
                "build", "wall", z=0)
    for _ in range(300):
        if job.state == "done":
            break
        world.advance(100)
    assert job.state == "done"
    assert not world.hazards.breaches
    assert any(c.kind == "wall" and (c.x, c.y, c.z) ==
               (breach_wall.x, breach_wall.y, breach_wall.z)
               for c in world.ship_model.components)


def test_ship_ledger_reports_live_breaches_and_flooding():
    from pirates.management import ship_rows
    world = World()
    wall = exterior_wall(world)
    world.hazards.breach(world, wall.x, wall.y)
    rows = "\n".join(ship_rows(world))
    assert "Flood control: 1 open breaches" in rows
    assert "WARNING open hull breach" in rows


def test_fire_damages_real_structure_and_creates_spreading_persistent_smoke(tmp_path):
    world = World()
    floor = next(c for c in world.ship_model.components if c.kind == "floor" and c.material != "iron"
                 and any(any(p.kind == "floor" for p in world.ship_model.at(c.x + dx, c.y + dy, c.z))
                         and not ShipHazards._passage_closed(world, (c.x, c.y, c.z),
                                                             (c.x + dx, c.y + dy, c.z))
                         for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1))))
    world.ship.hull = 100
    world.hazards.ignite(world, floor.x, floor.y, floor.z)
    for _ in range(12):
        world.hazards.advance(world)
    damaged = next(c for c in world.ship_model.components if c.id == floor.id)
    assert damaged.condition < 100
    assert len(world.hazards.smoke) > 1
    assert world.hazards.fires
    path = tmp_path / "fire.json"
    world.save(path)
    restored = World.load(path)
    assert restored.hazards.snapshot() == world.hazards.snapshot()
    assert restored.ship_model.snapshot() == world.ship_model.snapshot()


def test_fire_causes_a_deterministic_persistent_burn_and_work_slowdown(tmp_path):
    from pirates.management import profile_rows

    world = World()
    sailor = world.ship.crew[0]
    floors = [c for c in world.ship_model.components if c.kind == "floor" and c.material != "iron"]
    occupied = {(c.x, c.y, c.z) for c in world.ship.crew[1:]}
    occupied.add((world.player.x, world.player.y, world.player.z))
    floor = next(c for c in floors if any(
        world.game_map.is_walkable(wx, wy) and (wx, wy, c.z) not in occupied
        for wx, wy in ((c.x + 68, c.y + 12), (c.x + 69, c.y + 11),
                       (c.x + 70, c.y + 12), (c.x + 69, c.y + 13))))
    sailor.x, sailor.y, sailor.z = next(
        (wx, wy, floor.z) for wx, wy in ((floor.x + 68, floor.y + 12),
                                          (floor.x + 69, floor.y + 11),
                                          (floor.x + 70, floor.y + 12),
                                          (floor.x + 69, floor.y + 13))
        if world.game_map.is_walkable(wx, wy) and (wx, wy, floor.z) not in occupied)
    world.hazards.ignite(world, floor.x, floor.y, floor.z)
    world.hazards.advance(world)
    assert len(sailor.injuries) == 1
    burn = sailor.injuries[0]
    assert burn["kind"] == "burn"
    assert burn["cause"] == f"fire at deck {floor.z}, tile ({floor.x},{floor.y})"
    assert sailor.health < 100
    assert any("burn" in row for row in profile_rows(sailor))
    # The same deterministic fire pulse cannot duplicate an unresolved injury.
    world.hazards.advance(world)
    assert len(sailor.injuries) == 1
    path = tmp_path / "injured-crew.json"
    world.save(path)
    restored = World.load(path)
    assert restored.ship.crew[0].injuries == sailor.injuries
    assert restored.ship.crew[0].injury_serial == sailor.injury_serial


def test_injury_save_validation_rejects_forged_records_and_legacy_rows_migrate(tmp_path):
    import json

    world = World()
    path = tmp_path / "healthy-crew.json"
    world.save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    legacy = json.loads(json.dumps(data))
    for row in legacy["ship"]["crew"]:
        row.pop("injuries", None)
        row.pop("injury_serial", None)
    legacy_path = tmp_path / "legacy-crew.json"
    legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
    assert all(not sailor.injuries for sailor in World.load(legacy_path).ship.crew)

    data["ship"]["crew"][0]["injuries"] = [{
        "id": "mara-injury-1", "kind": "burn", "severity": 3,
        "cause": "test fire", "recovery": 101,
    }]
    data["ship"]["crew"][0]["injury_serial"] = 1
    invalid_path = tmp_path / "invalid-injury.json"
    invalid_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        World.load(invalid_path)


def test_injury_work_penalty_is_deterministic_bounded_and_recovers():
    from pirates.ship import injury_work_multiplier

    injuries = [{"severity": 2, "recovery": 0.0}, {"severity": 3, "recovery": 50.0}]
    assert injury_work_multiplier(injuries) == 0.4
    injuries[1]["recovery"] = 100.0
    assert injury_work_multiplier(injuries) == 0.7
    injuries[0]["recovery"] = 100.0
    assert injury_work_multiplier(injuries) == 1.0


def test_fire_requires_combustible_structure_and_old_hazard_saves_migrate():
    world = World()
    with pytest.raises(ValueError, match="combustible"):
        world.hazards.ignite(world, 0, 0, 0)
    data = world.hazards.snapshot()
    data["version"] = 1
    data.pop("fires")
    data.pop("smoke")
    restored = ShipHazards.restore(data, world.ship_model)
    assert not restored.fires and not restored.smoke


def test_crew_generates_physical_fire_suppression_job_using_spatial_water():
    world = World()
    floors = [c for c in world.ship_model.components if c.kind == "floor" and c.material != "iron"]
    floor = max(floors, key=lambda c: min(
        abs(s.x - (c.x + 69)) + abs(s.y - (c.y + 12)) for s in world.ship.crew))
    key = world.hazards.ignite(world, floor.x, floor.y, floor.z)
    for job in tuple(world.ship.jobs):
        world.ship.cancel(job.id)
    for sailor in world.ship.crew:
        sailor.labors = {labor: 0 for labor in sailor.labors}
        sailor.labors["repair"] = 1
        sailor.hunger = sailor.thirst = sailor.fatigue = sailor.loneliness = sailor.boredom = 0
        sailor.intent = ""
    world.ship.crew = [world.ship.crew[0]]
    water_before = world.ship.stores["clean_water_ml"]
    world.ship.generate_jobs(world)
    job = next(j for j in world.ship.jobs if j.key == f"fire:{floor.x},{floor.y},{floor.z}")
    assert world.ship.job_cost(job) == {"clean_water_ml": 1000}
    # This regression isolates physical suppression/one-unit water accounting;
    # fire spread and crew burns are covered separately above.
    world.hazards.advance = lambda _world: None
    for _ in range(250):
        if job.state == "done":
            break
        world.advance(100)
    assert job.state == "done"
    assert key not in world.hazards.fires
    assert world.ship.stores["clean_water_ml"] == water_before - 1000
    assert job.supply_plan and job.supply_plan[0]["bundle"] == {"clean_water_ml": 1000}
    assert all(row["collected"] for row in job.supply_plan)
