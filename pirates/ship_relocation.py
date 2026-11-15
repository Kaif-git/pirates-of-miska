"""Exclusive equipment pickup, mass-scaled hauling, installation and returns."""
from math import ceil

from pirates.ship_map import ORIGIN
from pirates.ship_building import validate_access


def destination_layout(world, fid, target):
    if any((e.x, e.y) == tuple(target) for e in world.entities()):
        raise ValueError("An actor occupies the installation tile; waiting for it to clear")
    layout = world.fixtures.relocated(fid, *target)
    lx, ly = target[0] - ORIGIN[0], target[1] - ORIGIN[1]
    if any(p.x == lx and p.y == ly and p.fixture_id != fid for p in world.ship.storage.piles.values()):
        raise ValueError("Destination is reserved by a stockpile")
    validate_access(world, world.ship_model, layout)
    # Also protect the fixed repair/bilge work sites from equipment occupation.
    if tuple(target) in world.fixtures.reference_points.values():
        raise ValueError("Destination is reserved for damage-control access")
    return layout


def queue(world, fid, target):
    if not isinstance(target, (list, tuple)) or len(target) != 2:
        raise ValueError("Invalid equipment destination")
    target = tuple(target)
    fixture = world.fixtures.get(fid)
    if fixture is None:
        raise ValueError("Unknown equipment")
    if fixture.carrier is not None:
        raise ValueError("Equipment is already being hauled")
    if any(j.state not in ("done", "cancelled") and
           ((j.transfer and fid in (j.transfer["source"], j.transfer["destination"]))
            or any(row["pile"] == fid and not row["returned"] for row in j.supply_plan))
           for j in world.ship.jobs):
        raise ValueError("An active job reserves this equipment's stockpile")
    source = world.fixtures.world_position(fixture)
    if source == target:
        raise ValueError("Choose a different destination")
    for job in world.ship.jobs:
        if job.state in ("done", "cancelled"):
            continue
        if job.relocation and (job.relocation["fixture_id"] == fid or tuple(job.target) == target
                               or tuple(job.relocation["source"]) == target):
            raise ValueError("Another relocation reserves this equipment or site")
        if job.construction and tuple(job.target) in (source, target):
            raise ValueError("A construction order reserves the source or destination")
    destination_layout(world, fid, target)
    job = world.ship.add_job(f"relocate:{fid}", "repair", target, priority=2, work=20)
    if job is None:
        raise ValueError("Equipment is on cancellation cooldown")
    job.relocation = {"fixture_id": fid, "source": list(source), "stage": "lift", "move_wait": 0}
    world.message(f"Queued relocation of {fid} to {target}; pickup, hauling and installation required.")
    return job


def retarget_jobs(ship):
    for job in ship.jobs:
        if job.state in ("done", "cancelled") or job.construction or job.relocation:
            continue
        key = ("galley" if job.kind == "cook" else "rigging" if job.kind == "rig" else
               job.key.split(":", 1)[1] if job.kind == "load" else None)
        if key in ship.stations:
            job.target = ship.stations[key]


def work(world, sailor, job):
    ship, plan = world.ship, job.relocation
    fixture = world.fixtures.get(plan["fixture_id"])
    if fixture is None:
        job.reason = "Equipment identity is missing"
        return
    returning = job.cancel_requested or job.release_requested
    target = tuple(plan["source"]) if returning else tuple(job.target)
    if plan["stage"] == "lift":
        sailor.activity = "Unfastening " + fixture.id
        if not ship.walk(world, sailor, plan["source"]):
            return
        if any((e.x, e.y) == tuple(plan["source"]) for e in world.entities()):
            job.reason = "Equipment is occupied; waiting for it to clear"
            return
        job.state = "claimed"
        job.progress = min(job.work, job.progress + 1 + sailor.skills["repair"] * 0.2)
        sailor.skill_xp["repair"] += 1
        if job.progress < job.work:
            return
        # Revalidate before pickup: an order may have become unsafe while waiting.
        try:
            destination_layout(world, fixture.id, job.target)
            world.bind_fixtures(world.fixtures.carried(fixture.id, sailor.id))
        except ValueError as error:
            job.reason = str(error)
            return
        plan["stage"], job.progress = "haul", 0
        ship.record("equipment_picked_up", sailor, job, fixture.id)
        return
    if fixture.carrier != sailor.id:
        job.reason = "Equipment carrier does not match exclusive job claim"
        return
    job.state = "returning" if returning else "claimed"
    sailor.activity = ("Returning equipment: " if returning else "Hauling equipment: ") + fixture.id
    # Heavy equipment is moved slowly with tackle/rollers; not teleported or consumed.
    # Every movement step still uses normal actor collision and door operation.
    if plan["move_wait"]:
        plan["move_wait"] -= 1
        return
    before = sailor.x, sailor.y
    if not ship.walk(world, sailor, target):
        if (sailor.x, sailor.y) != before:
            mass = fixture.mass_kg + ship.storage.fixture_mass(fixture.id)
            plan["move_wait"] = max(0, ceil(mass / 30) - 1)
            sailor.energy = max(0, sailor.energy - 0.1)
        return
    try:
        layout = destination_layout(world, fixture.id, target)
    except ValueError as error:
        job.reason = str(error)
        return
    sailor.activity = "Refastening " + fixture.id
    job.progress = min(job.work, job.progress + 1 + sailor.skills["repair"] * 0.2)
    sailor.skill_xp["repair"] += 1
    if job.progress < job.work:
        return
    world.bind_fixtures(layout)
    retarget_jobs(ship)
    ship.record("equipment_returned" if returning else "equipment_installed", sailor, job,
                f"{fixture.id} at {target}")
    plan["stage"], plan["move_wait"] = "lift", 0
    if returning:
        job.state, job.worker = ("cancelled" if job.cancel_requested else "pending"), None
        job.release_requested = False
        job.progress = 0
        sailor.job_id = None
        job.reason = "Equipment returned to original position"
    else:
        ship.finish(job, sailor)


def validate_job(job, layout):
    plan = job.relocation
    if (not isinstance(plan, dict) or set(plan) != {"fixture_id", "source", "stage", "move_wait"}
            or not isinstance(plan["fixture_id"], str) or layout.get(plan["fixture_id"]) is None
            or job.kind != "repair" or job.construction
            or job.key != "relocate:" + plan["fixture_id"]
            or plan["stage"] not in ("lift", "haul")
            or type(plan["move_wait"]) is not int or not 0 <= plan["move_wait"] <= 100
            or not isinstance(plan["source"], (list, tuple)) or len(plan["source"]) != 2
            or len(job.target) != 2
            or any(type(v) is not int for v in (*plan["source"], *job.target))):
        raise ValueError("Invalid equipment relocation job")
    from pirates.ship_map import WIDTH, HEIGHT
    for x, y in (plan["source"], job.target):
        if not (ORIGIN[0] <= x < ORIGIN[0] + WIDTH and ORIGIN[1] <= y < ORIGIN[1] + HEIGHT):
            raise ValueError("Equipment relocation site outside ship")


def invariant_errors(ship):
    if ship.layout is None:
        return []
    errors, claimed_equipment, sites = [], set(), set()
    for job in ship.jobs:
        if not job.relocation or job.state in ("done", "cancelled"):
            continue
        fid = job.relocation["fixture_id"]
        if fid in claimed_equipment or tuple(job.target) in sites:
            errors.append(f"Job {job.id}: duplicate equipment or destination reservation")
        claimed_equipment.add(fid)
        sites.add(tuple(job.target))
        fixture = ship.layout.get(fid)
        if fixture is None:
            errors.append(f"Job {job.id}: missing equipment")
        elif job.relocation["stage"] == "haul":
            if fixture.carrier != job.worker or job.worker is None:
                errors.append(f"Job {job.id}: invalid equipment carrier")
        elif fixture.carrier is not None:
            errors.append(f"Job {job.id}: equipment carried before pickup")
        if fixture is not None and ship.layout.world_position(fixture) != tuple(job.relocation["source"]):
            errors.append(f"Job {job.id}: source reservation does not match equipment")
    for fixture in ship.layout.fixtures:
        if fixture.carrier is not None:
            sailor = ship.sailor(fixture.carrier)
            job = ship.job(sailor.job_id) if sailor else None
            if (not job or not job.relocation or job.relocation["fixture_id"] != fixture.id
                    or job.relocation["stage"] != "haul" or job.worker != fixture.carrier):
                errors.append(f"{fixture.id}: orphaned equipment carrier")
    return errors
