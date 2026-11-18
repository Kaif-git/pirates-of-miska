from pirates.game import World
from pirates.ship_logistics import inspect


def test_inspector_measures_current_work_routes_overlap_and_recent_throughput():
    world = World()
    workers = world.ship.crew[:2]
    for index, (worker, at) in enumerate(zip(workers, ((77, 25), (77, 27)))):
        worker.x, worker.y, worker.z = *at, 0
        worker.labors["repair"] = 1
        job = world.ship.add_job(f"inspect:{index}", "repair", world.ship.stations["repair"], work=20)
        job.state, job.worker, worker.job_id = "claimed", worker.id, job.id
    world.ship.seconds = 700
    world.ship.audit.append({"second": 650, "event": "completed", "crew": workers[0].id,
                             "job": 1, "reason": "test completion"})
    report = inspect(world)
    assert len(report["planned_routes"]) == 2
    assert report["traffic"]
    assert report["completed_last_10m"] == 1
    assert any("shared route tiles" in row for row in report["rows"])
    assert any("~" in row and "tiles" in row for row in report["rows"])


def test_inspector_estimates_nearest_fire_response_and_flags_unreachable_labor():
    world = World()
    floor = next(c for c in world.ship_model.components if c.kind == "floor" and c.material != "iron")
    world.hazards.ignite(world, floor.x, floor.y, floor.z)
    world.ship.crew[0].x, world.ship.crew[0].y, world.ship.crew[0].z = 77, 25, 0
    world.ship.crew[0].labors["repair"] = 1
    report = inspect(world)
    assert any(row.startswith("FIRE at") and "travel" in row for row in report["rows"])

    for sailor in world.ship.crew:
        sailor.labors["repair"] = 0
    report = inspect(world)
    assert any("NO REPAIR-LABOR ROUTE" in row for row in report["rows"])
