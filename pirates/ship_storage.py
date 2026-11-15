"""Spatial supply counts, source reservations and capacity-aware hauling.

Available totals are a mapping view, never a second authoritative inventory.
Reservations leave a pile's available contents but retain their source identity.
"""
from collections.abc import MutableMapping
from dataclasses import dataclass, asdict

from pirates.ship_map import ORIGIN, WIDTH, HEIGHT

RESOURCES = ("raw_food", "water", "planks", "shot")
UNIT_MASS = {"raw_food": 1, "water": 1, "planks": 20, "shot": 5}
HAUL_CAPACITY_KG = 50


def bundle_mass(bundle):
    return sum(UNIT_MASS[k] * n for k, n in bundle.items())


def add_bundle(target, bundle, sign=1):
    for key, number in bundle.items():
        value = target.get(key, 0) + sign * number
        if value < 0:
            raise ValueError("Supply bundle would become negative")
        if value:
            target[key] = value
        else:
            target.pop(key, None)


@dataclass
class Stockpile:
    id: str
    x: int
    y: int
    z: int
    fixture_id: str | None
    capacity_kg: int
    contents: dict


class Totals(MutableMapping):
    def __init__(self, storage):
        self.storage = storage

    def __iter__(self):
        return iter(RESOURCES)

    def __len__(self):
        return len(RESOURCES)

    def __getitem__(self, key):
        if key not in RESOURCES:
            raise KeyError(key)
        return sum(p.contents.get(key, 0) for p in self.storage.piles.values())

    def __setitem__(self, key, value):
        if key not in RESOURCES or type(value) is not int or value < 0:
            raise ValueError("Invalid available supply total")
        delta = value - self[key]
        if delta >= 0:
            pile = self.storage.piles["water" if key == "water" else "hold"]
            if bundle_mass(pile.contents) + UNIT_MASS[key] * delta > pile.capacity_kg:
                raise ValueError("Stockpile capacity exceeded")
            pile.contents[key] = pile.contents.get(key, 0) + delta
        else:
            remaining = -delta
            for pile in self.storage.piles.values():
                count = min(remaining, pile.contents.get(key, 0))
                add_bundle(pile.contents, {key: count}, -1)
                remaining -= count

    def __delitem__(self, key):
        raise ValueError("Supply kinds cannot be deleted")

    def copy(self):
        return dict(self)


class SpatialStorage:
    def __init__(self, piles=None):
        self.piles = {p.id: p for p in piles or []}

    @classmethod
    def starter(cls, stations):
        piles = []
        for key, capacity in (("hold", 5000), ("water", 250)):
            x, y = stations[key]
            piles.append(Stockpile(key, x - ORIGIN[0], y - ORIGIN[1], 0, key, capacity, {}))
        return cls(piles)

    @property
    def totals(self):
        return Totals(self)

    def position(self, pile_id, layout):
        pile = self.piles[pile_id]
        fixture = layout.get(pile.fixture_id) if layout and pile.fixture_id else None
        return layout.world_position(fixture) if fixture else (pile.x + ORIGIN[0], pile.y + ORIGIN[1])

    def available(self, pile_id, layout):
        pile = self.piles[pile_id]
        return not pile.fixture_id or not layout or layout.available(pile.fixture_id)

    def fixture_mass(self, fid):
        return sum(bundle_mass(p.contents) for p in self.piles.values() if p.fixture_id == fid)

    def reserved_at(self, pile_id, resource, jobs):
        return sum(row["bundle"].get(resource, 0) for job in jobs
                   if job.state not in ("done", "cancelled")
                   for row in job.supply_plan
                   if row["pile"] == pile_id and not row["collected"] and not row["returned"])

    def reserve_capacity(self, pile_id, bundle, reservations=()):
        pile = self.piles[pile_id]
        reserved = sum(bundle_mass(row["bundle"]) for row in reservations
                       if row.get("destination") == pile_id and not row.get("delivered", False))
        return bundle_mass(pile.contents) + reserved + bundle_mass(bundle) <= pile.capacity_kg

    def designate(self, model, fixture_overlay, wx, wy, capacity_kg=1000):
        if (type(wx) is not int or type(wy) is not int or type(capacity_kg) is not int
                or not 1 <= capacity_kg <= 100000):
            raise ValueError("Invalid stockpile designation")
        x, y = wx - ORIGIN[0], wy - ORIGIN[1]
        if not (0 <= x < WIDTH and 0 <= y < HEIGHT):
            raise ValueError("Stockpiles must be aboard the current deck")
        if any(p.fixture_id is None and p.x == x and p.y == y for p in self.piles.values()):
            raise ValueError("A stockpile is already designated here")
        parts = model.at(x, y, 0)
        if (not any(c.kind == "floor" for c in parts)
                or any(c.kind in ("wall", "door") for c in parts) or (x, y) in fixture_overlay):
            raise ValueError("Stockpiles need a clear, empty deck tile")
        pid = f"pile:{x},{y}"
        if pid in self.piles:
            raise ValueError("Stockpile identity already exists")
        self.piles[pid] = Stockpile(pid, x, y, 0, None, capacity_kg, {})
        return self.piles[pid]

    def und_designate(self, pile_id, jobs=()):
        pile = self.piles.get(pile_id)
        if pile is None or pile.fixture_id is not None:
            raise ValueError("Only empty floor stockpiles can be removed")
        reserved = any(j.state not in ("done", "cancelled") and
                       ((j.transfer and pile_id in (j.transfer["source"], j.transfer["destination"]))
                        or any(r["pile"] == pile_id and not r["returned"] for r in j.supply_plan))
                       for j in jobs)
        if pile.contents or reserved:
            raise ValueError("Stockpile must be empty and unreserved")
        del self.piles[pile_id]

    def transfer_job(self, world, source, destination, bundle):
        if source not in self.piles or destination not in self.piles or source == destination:
            raise ValueError("Choose two different existing stockpiles")
        if not isinstance(bundle, dict) or not bundle or any(
                k not in RESOURCES or type(n) is not int or n < 1 for k, n in bundle.items()):
            raise ValueError("Invalid transfer bundle")
        if bundle_mass(bundle) > HAUL_CAPACITY_KG:
            raise ValueError(f"A single crew load is limited to {HAUL_CAPACITY_KG} kg")
        if not self.available(source, world.fixtures) or not self.available(destination, world.fixtures):
            raise ValueError("A stockpile container is being relocated")
        active_transfers = [j.transfer for j in world.ship.jobs if j.transfer
                            and j.state not in ("done", "cancelled")]
        if not self.reserve_capacity(destination, bundle, active_transfers):
            raise ValueError("Destination stockpile lacks capacity")
        for key, amount in bundle.items():
            reserved_source = sum(j.transfer["bundle"].get(key, 0) for j in world.ship.jobs
                                  if j.transfer and j.state not in ("done", "cancelled")
                                  and j.transfer["source"] == source)
            if amount + reserved_source > self.piles[source].contents.get(key, 0):
                raise ValueError("Source stockpile does not contain that unreserved amount")
        job = world.ship.add_job(f"transfer:{world.ship.next_id}", "repair",
                                 self.position(destination, world.fixtures), priority=3, work=1)
        if job is None:
            raise ValueError("Transfer order is on cooldown")
        job.transfer = {"source": source, "destination": destination, "bundle": dict(bundle)}
        return job

    def plan(self, world, sailor, cost, only=None):
        """Choose reachable sources without mutating counts; reserve only on claim."""
        remaining = dict(cost)
        candidates = []
        for pile in self.piles.values():
            if (only is not None and pile.id != only) or not self.available(pile.id, world.fixtures):
                continue
            route = world.ship.path(world, sailor, self.position(pile.id, world.fixtures))
            if route is not None:
                candidates.append((len(route), pile.id))
        allocations = []
        for _, pid in sorted(candidates):
            pile = self.piles[pid]
            bundle = {k: min(n, pile.contents.get(k, 0)) for k, n in remaining.items()
                      if n and pile.contents.get(k, 0)}
            if bundle:
                allocations.append({"pile": pid, "bundle": bundle, "collected": False, "returned": False})
                for k, n in bundle.items():
                    remaining[k] -= n
        return allocations if not any(remaining.values()) else None

    def reserve(self, allocations):
        for row in allocations:
            add_bundle(self.piles[row["pile"]].contents, row["bundle"], -1)

    def release_uncollected(self, job):
        for row in job.supply_plan:
            if not row["collected"] and not row["returned"]:
                add_bundle(self.piles[row["pile"]].contents, row["bundle"])
                row["returned"] = True
        job.reserved = {}

    def snapshot(self):
        return [asdict(self.piles[key]) for key in sorted(self.piles)]

    @classmethod
    def restore(cls, rows, model, layout):
        if not isinstance(rows, list) or not 2 <= len(rows) <= WIDTH * HEIGHT:
            raise ValueError("Invalid stockpile list")
        result, occupied = cls(), set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"id", "x", "y", "z", "fixture_id", "capacity_kg", "contents"}:
                raise ValueError("Invalid stockpile record")
            pile = Stockpile(**row)
            if (not isinstance(pile.id, str) or not pile.id or pile.id in result.piles
                    or any(type(v) is not int for v in (pile.x, pile.y, pile.z, pile.capacity_kg))
                    or pile.z != 0 or not 1 <= pile.capacity_kg <= 100000
                    or not isinstance(pile.contents, dict)
                    or any(k not in RESOURCES or type(n) is not int or n <= 0 for k, n in pile.contents.items())
                    or bundle_mass(pile.contents) > pile.capacity_kg):
                raise ValueError("Invalid stockpile identity, contents or capacity")
            if pile.fixture_id is not None:
                if pile.fixture_id not in ("hold", "water") or pile.id != pile.fixture_id:
                    raise ValueError("Invalid stockpile fixture binding")
                fixture = layout.get(pile.fixture_id)
                x, y = fixture.x, fixture.y
            else:
                if not pile.id.startswith("pile:"):
                    raise ValueError("Invalid floor-stockpile identity")
                x, y = pile.x, pile.y
            parts = model.at(x, y, 0)
            if (not any(c.kind == "floor" for c in parts)
                    or any(c.kind in ("wall", "door") for c in parts) or (x, y) in occupied):
                raise ValueError("Invalid stockpile geometry")
            if pile.fixture_id is None and (x, y) in layout.overlay:
                raise ValueError("Floor stockpile overlaps equipment")
            occupied.add((x, y))
            result.piles[pile.id] = pile
        if not {"hold", "water"} <= result.piles.keys():
            raise ValueError("Missing starter storage identities")
        return result


def collect(world, sailor, job):
    storage = world.ship.storage
    row = next((r for r in job.supply_plan if not r["collected"] and not r["returned"]), None)
    if row is None:
        job.phase = "work"
        return
    if not storage.available(row["pile"], world.fixtures):
        job.reason = "Supply container is being relocated"
        return
    sailor.activity = f"Collecting supplies at {row['pile']}: {world.ship.job_label(job)}"
    if world.ship.walk(world, sailor, storage.position(row["pile"], world.fixtures)):
        add_bundle(sailor.cargo, row["bundle"])
        add_bundle(job.reserved, row["bundle"], -1)
        row["collected"] = True
        if not job.reserved:
            job.phase = "work"
        world.ship.record("supplies_collected", sailor, job, f"{row['pile']}: {row['bundle']}")


def transfer_capacity_errors(ship):
    errors = []
    for pile in ship.storage.piles.values():
        reserved = sum(bundle_mass(j.transfer["bundle"]) for j in ship.jobs
                       if j.transfer and j.state not in ("done", "cancelled")
                       and j.transfer["destination"] == pile.id)
        if bundle_mass(pile.contents) + reserved > pile.capacity_kg:
            errors.append(f"Stockpile {pile.id}: destination capacity over-reserved")
    return errors


def return_cargo(world, sailor, job):
    ship, storage = world.ship, world.ship.storage
    row = next((r for r in job.supply_plan if r["collected"] and not r["returned"]), None)
    if row is not None:
        sailor.activity = "Returning supplies to " + row["pile"]
        if not storage.available(row["pile"], world.fixtures):
            job.reason = "Waiting for returning-supply container installation"
            return
        if ship.walk(world, sailor, storage.position(row["pile"], world.fixtures)):
            add_bundle(storage.piles[row["pile"]].contents, row["bundle"])
            add_bundle(sailor.cargo, row["bundle"], -1)
            row["returned"] = True
        return
    if sailor.cargo:
        raise ValueError("Cargo has no spatial return source")
    sailor.job_id = None
    job.state, job.worker = ("cancelled" if job.cancel_requested else "pending"), None
    job.release_requested = False
    job.supply_plan = []
    job.reason = "Supplies returned to original stockpiles"
    ship.record("supplies_returned", sailor, job, job.reason)


def validate_allocations(job, storage):
    if not isinstance(job.supply_plan, list) or len(job.supply_plan) > len(storage.piles) or len({r.get("pile") for r in job.supply_plan if isinstance(r, dict)}) != len(job.supply_plan):
        raise ValueError("Invalid spatial supply allocation list")
    for row in job.supply_plan:
        if (not isinstance(row, dict) or set(row) != {"pile", "bundle", "collected", "returned"}
                or not isinstance(row["pile"], str) or row["pile"] not in storage.piles
                or type(row["collected"]) is not bool or type(row["returned"]) is not bool
                or not isinstance(row["bundle"], dict) or not row["bundle"]
                or any(k not in RESOURCES or type(n) is not int or n < 1 for k, n in row["bundle"].items())):
            raise ValueError("Invalid spatial supply allocation")


def allocation_errors(ship, job, sailor):
    reserved, carried = {}, {}
    for row in job.supply_plan:
        if row["returned"]:
            continue
        add_bundle(carried if row["collected"] else reserved, row["bundle"])
    errors = []
    if reserved != job.reserved or carried != sailor.cargo:
        errors.append(f"Job {job.id}: spatial reservations/cargo do not match source allocations")
    if any(reserved.get(key, 0) and carried.get(key, 0) for key in RESOURCES):
        errors.append(f"Job {job.id}: supplies reserved and carried twice")
    if any(job.reserved.get(key, 0) and sailor.cargo.get(key, 0) for key in RESOURCES):
        errors.append(f"Job {job.id}: supplies reserved and carried twice")
    if not (job.cancel_requested or job.release_requested):
        combined = dict(reserved)
        add_bundle(combined, carried)
        if combined != ship.job_cost(job):
            errors.append(f"Job {job.id}: spatial cost is missing or duplicated")
    return errors


def validate_transfer(job, storage):
    plan = job.transfer
    if (not isinstance(plan, dict) or set(plan) != {"source", "destination", "bundle"}
            or plan["source"] not in storage.piles or plan["destination"] not in storage.piles
            or plan["source"] == plan["destination"] or job.kind != "repair"
            or not isinstance(plan["bundle"], dict) or not plan["bundle"]
            or any(k not in RESOURCES or type(v) is not int or v < 1 for k, v in plan["bundle"].items())
            or bundle_mass(plan["bundle"]) > HAUL_CAPACITY_KG
            or not job.key.startswith("transfer:")):
        raise ValueError("Invalid stockpile transfer job")
    if job.state not in ("done", "cancelled"):
        if job.target != storage.position(plan["destination"], None) and storage.piles[plan["destination"]].fixture_id is None:
            raise ValueError("Transfer destination no longer matches stockpile position")


def queue_transfer(world, source, destination, bundle):
    return world.ship.storage.transfer_job(world, source, destination, bundle)
    source = storage.piles[plan["source"]]
    # The supply plan/reservations are checked separately; the order's destination must remain specific.
