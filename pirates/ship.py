"""Authored sloop, functional stations and autonomous, turn-based ship life.

All simulation time enters through ShipLife.advance; menus never tick the world.
Jobs are exclusive, resumable claims. Supplies are reserved in the hold, then
physically carried by the assigned sailor before any work can complete.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
import math

from omnisim.world import tile as T
from pirates.ship_storage import SpatialStorage, add_bundle, allocation_errors

LABORS = ("repair", "bail", "cook", "clean", "rig", "load")
LABELS = {"repair": "Patch hull", "bail": "Bail bilge", "cook": "Cook a meal batch",
          "clean": "Swab deck", "rig": "Tend rigging", "load": "Load cannon"}
RESOURCES = ("raw_food", "water", "planks", "shot")
TRAIT_EFFECTS = {
    "perfectionist": "10% slower work; hull patches restore 16 instead of 14 condition",
    "early_bird": "Prefers sleep between 21:00 and 05:00",
    "night_owl": "Prefers sleep between 00:00 and 08:00",
    "hearty_appetite": "Hunger rises 35% faster; seeks meals sooner",
    "social_butterfly": "Seeks company at lower loneliness",
    "restless": "Seeks variety sooner",
    "light_sleeper": "Sleep recovery is 20% slower",
    "shy": "Tolerates more solitude before seeking company",
    "patient": "Unmet needs generate 30% less stress",
    "quick_learner": "Gains 40% more practice experience",
    "hard_worker": "Works 10% faster",
}
STATIONS = {
    "helm": (80, 15), "galley": (72, 36), "water": (74, 30),
    "hold": (88, 40), "mess": (73, 28), "leisure": (74, 27),
    "workshop": (88, 32), "rigging": (84, 25), "lookout": (85, 17),
    "port_cannon": (70, 28), "starboard_cannon": (90, 28),
    "repair": (91, 31), "bilge": (87, 37),
}
BUNKS = [(72, 39), (74, 39), (76, 39), (78, 39), (72, 41), (74, 41)]
ROOMS = [("Forecastle / helm", (69, 12, 92, 20)),
         ("Working deck / mess", (69, 20, 92, 34)),
         ("Galley / crew berths", (69, 34, 82, 44)),
         ("Cargo hold", (82, 34, 92, 44))]


def build_ship(tiles):
    """Keep the original port connection, replace the rectangle with a sloop."""
    tiles[12:44, 69:92] = T.FLOOR
    tiles[12, 69:92] = tiles[43, 69:92] = T.WALL_WOOD
    tiles[12:44, 69] = tiles[12:44, 91] = T.WALL_WOOD
    # Tapered bow; solid hull, not freely walkable ocean.
    for row, inset in ((12, 6), (13, 4), (14, 2)):
        tiles[row, 69:69 + inset] = T.WATER
        tiles[row, 92 - inset:92] = T.WATER
        tiles[row, 69 + inset] = tiles[row, 91 - inset] = T.WALL_WOOD
    for row in (20, 34):
        tiles[row, 70:91] = T.WALL_WOOD
        tiles[row, 76] = tiles[row, 85] = T.DOOR
    tiles[35:43, 82] = T.WALL_WOOD
    tiles[38, 82] = T.DOOR
    tiles[23:25, 69] = T.FLOOR
    station_tiles = {"helm": T.TABLE, "galley": T.HEARTH, "water": T.WELL,
                     "hold": T.CHEST, "mess": T.TABLE, "leisure": T.CHAIR,
                     "workshop": T.WORKBENCH, "rigging": T.TREE,
                     "port_cannon": T.ANVIL, "starboard_cannon": T.ANVIL}
    for key, tile in station_tiles.items():
        x, y = STATIONS[key]
        tiles[y, x] = tile
    for x, y in BUNKS:
        tiles[y, x] = T.BED


def room_name(x, y):
    return next((name for name, (x1, y1, x2, y2) in ROOMS
                 if x1 <= x < x2 and y1 <= y < y2), "Harbor")


@dataclass
class Sailor:
    id: str
    name: str
    glyph: str
    x: int
    y: int
    color: tuple
    trait: str
    preference: str
    bunk: int
    labors: dict
    skills: dict
    hunger: float = 25
    thirst: float = 30
    fatigue: float = 20
    loneliness: float = 20
    boredom: float = 20
    morale: float = 65
    health: float = 100
    activity: str = "idle"
    intent: str = ""
    action_seconds: int = 0
    job_id: int | None = None
    cargo: dict = field(default_factory=dict)
    haul_wait: int = 0
    thoughts: list = field(default_factory=list)
    relationships: dict = field(default_factory=dict)
    completed: int = 0
    personality: dict = field(default_factory=dict)
    traits: list = field(default_factory=list)
    energy: float = 100
    sleep_debt: float = 25
    stress: float = 0
    memories: list = field(default_factory=list)
    skill_xp: dict = field(default_factory=dict)

    def get(self, key, default=None):
        return "crew" if key == "role" else getattr(self, key, default)

    def think(self, text, mood=0):
        if not self.thoughts or self.thoughts[-1] != text:
            self.thoughts = (self.thoughts + [text])[-8:]
            self.morale = max(0, min(100, self.morale + mood))
            if mood:
                self.memories = (self.memories + [{"text": text, "mood": mood, "remaining": 1200}])[-12:]

    @property
    def mood_label(self):
        return "Content" if self.morale >= 65 else "Steady" if self.morale >= 45 else "Low" if self.morale >= 25 else "Miserable"


@dataclass
class Job:
    id: int
    key: str
    kind: str
    target: tuple
    priority: int = 3
    state: str = "pending"
    worker: str | None = None
    progress: float = 0
    work: int = 20
    phase: str = "work"
    reserved: dict = field(default_factory=dict)
    reason: str = ""
    cancel_requested: bool = False
    assigned_to: str | None = None
    release_requested: bool = False
    construction: dict = field(default_factory=dict)
    relocation: dict = field(default_factory=dict)
    supply_plan: list = field(default_factory=list)
    transfer: dict = field(default_factory=dict)


class ShipLife:
    def __init__(self):
        self.layout = None  # Bound by World; standalone/legacy instances use templates.
        self.storage = SpatialStorage.starter(STATIONS)
        self.seconds = 0
        self.remainder = 0
        self.next_id = 1
        self.stores = {"raw_food": 30, "water": 60, "planks": 16, "shot": 16}
        self.meals = 4
        self.hull = 72.0
        self.bilge = 18.0
        self.rigging = 62.0
        self.dirt = {"77,26": 70.0, "86,23": 55.0, "79,31": 60.0}
        self.ammo = {"port_cannon": 0, "starboard_cannon": 0}
        self.cooldowns = {}
        self.jobs = []
        self.events = ["The crew is aboard the Wayward: work, rest and provisions are shared."]
        self.audit = []
        definitions = [
            ("mara", "Mara", "M", 77, 25, "Meticulous carpenter", "craft", "repair"),
            ("ivo", "Ivo", "I", 84, 29, "Gregarious cook", "social", "cook"),
            ("nell", "Nell", "N", 75, 32, "Restless deckhand", "lookout", "clean"),
            ("orin", "Orin", "O", 87, 26, "Quiet gunner", "craft", "load"),
            ("suri", "Suri", "S", 79, 29, "Patient rigger", "lookout", "rig"),
            ("tavi", "Tavi", "T", 86, 31, "Companionable boatswain", "social", "bail"),
        ]
        self.crew = []
        for i, (sid, name, glyph, x, y, trait, preference, specialty) in enumerate(definitions):
            labors = {kind: (1 if kind == specialty else 4) for kind in LABORS}
            skills = {kind: (3 if kind == specialty else 1) for kind in LABORS}
            self.crew.append(Sailor(sid, name, glyph, x, y, (130 + i * 18, 205, 195 - i * 12),
                                    trait, preference, i, labors, skills))
        personalities = [
            ({"diligence": 85, "sociability": 35, "resilience": 65, "curiosity": 50}, ["perfectionist", "early_bird"]),
            ({"diligence": 65, "sociability": 90, "resilience": 55, "curiosity": 65}, ["hearty_appetite", "social_butterfly"]),
            ({"diligence": 55, "sociability": 60, "resilience": 45, "curiosity": 90}, ["restless", "light_sleeper"]),
            ({"diligence": 75, "sociability": 20, "resilience": 70, "curiosity": 55}, ["shy", "night_owl"]),
            ({"diligence": 80, "sociability": 45, "resilience": 85, "curiosity": 70}, ["patient", "quick_learner"]),
            ({"diligence": 90, "sociability": 80, "resilience": 60, "curiosity": 40}, ["hard_worker", "social_butterfly"]),
        ]
        for sailor, (personality, traits) in zip(self.crew, personalities):
            sailor.personality, sailor.traits = personality, traits
            sailor.skill_xp = {k: 0.0 for k in LABORS}
        self.crew[1].hunger = 68
        self.crew[2].thirst = 72
        self.crew[4].fatigue = 74
        self.crew[4].sleep_debt = 75
        self.crew[5].loneliness = 66

    @property
    def stations(self):
        return self.layout.stations if self.layout is not None else STATIONS

    @property
    def stores(self):
        return self.storage.totals

    @stores.setter
    def stores(self, value):
        for key in RESOURCES:
            self.storage.totals[key] = value[key]

    @property
    def bunks(self):
        return self.layout.bunks if self.layout is not None else BUNKS

    def event(self, text):
        self.events = (self.events + [text])[-30:]

    def record(self, event, sailor=None, job=None, reason=""):
        self.audit = (self.audit + [{"second": self.seconds, "event": event,
                     "crew": sailor.id if sailor else None, "job": job.id if job else None,
                     "reason": reason}])[-100:]

    def sailor(self, sid):
        return next((c for c in self.crew if c.id == sid), None)

    def job(self, jid):
        return next((j for j in self.jobs if j.id == jid), None)

    def occupied(self, world, exclude=None):
        return {(c.x, c.y) for c in world.entities() if c.id != exclude}

    def goals(self, world, target):
        x, y = target
        gm = world.game_map
        return {(x + dx, y + dy) for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0))
                if gm.in_bounds(x + dx, y + dy)
                and (gm.is_walkable(x + dx, y + dy) or gm.tile_id(x + dx, y + dy) == T.DOOR)}

    def path(self, world, sailor, target, exact=False):
        """BFS through real geometry, with bodies and the captain as obstacles."""
        gm = world.game_map
        start = sailor.x, sailor.y
        goals = {tuple(target)} if exact else self.goals(world, target)
        if start in goals:
            return []
        occupied = self.occupied(world, sailor.id)
        todo = deque([start])
        parent = {start: None}
        while todo:
            x, y = todo.popleft()
            for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
                at = x + dx, y + dy
                if at in parent or at in occupied or not gm.in_bounds(*at):
                    continue
                if not gm.is_walkable(*at) and gm.tile_id(*at) != T.DOOR:
                    continue
                parent[at] = (x, y)
                if at in goals:
                    route = [at]
                    while parent[route[-1]] != start:
                        route.append(parent[route[-1]])
                    return list(reversed(route))
                todo.append(at)
        return None

    def walk(self, world, sailor, target, exact=False):
        if sailor.cargo and sailor.haul_wait:
            sailor.haul_wait -= 1
            sailor.activity = "Straining under a heavy load"
            return False
        route = self.path(world, sailor, target, exact)
        if route is None:
            sailor.activity = "Route blocked; waiting"
            job = self.job(sailor.job_id)
            if job:
                job.reason = "Route occupied or work site inaccessible; retrying"
            return False
        if not route:
            return True
        x, y = route[0]
        gm = world.game_map
        if gm.tile_id(x, y) == T.DOOR and not gm.doors[y, x]:
            gm.doors[y, x] = True
            sailor.activity = "Opening a ship door"
            return False
        sailor.x, sailor.y = x, y
        if sailor.cargo:
            from pirates.ship_storage import bundle_mass
            mass = bundle_mass(sailor.cargo)
            sailor.haul_wait = max(0, math.ceil(mass / 20) - 1)
        sailor.energy = max(0, sailor.energy - (0.05 + 0.001 * sum(sailor.cargo.values()))
                            * world.economy.bonus(sailor.id, "movement_multiplier"))
        sailor.fatigue = min(100, sailor.fatigue + 0.01)
        return False

    def add_job(self, key, kind, target, priority=3, work=20):
        if self.cooldowns.get(key, 0) > self.seconds:
            return None
        existing = next((j for j in self.jobs if j.key == key and j.state not in ("done", "cancelled")), None)
        if existing:
            return existing
        job = Job(self.next_id, key, kind, tuple(target), priority=priority, work=work)
        self.next_id += 1
        self.jobs.append(job)
        # Keep active work, plus a bounded history.
        history = [j for j in self.jobs if j.state in ("done", "cancelled")][-40:]
        self.jobs = [j for j in self.jobs if j.state not in ("done", "cancelled")] + history
        return job

    def generate_jobs(self):
        if self.hull < 100:
            self.add_job("repair", "repair", self.stations["repair"], 1, 30)
        if self.bilge >= 3:
            self.add_job("bail", "bail", self.stations["bilge"], 1, 15)
        if self.meals < 8:
            self.add_job("cook", "cook", self.stations["galley"], 2, 24)
        if self.rigging < 90:
            self.add_job("rig", "rig", self.stations["rigging"], 3, 25)
        for key, value in self.dirt.items():
            if value >= 20:
                self.add_job(f"clean:{key}", "clean", tuple(map(int, key.split(","))), 4, 18)
        for cannon, ammo in self.ammo.items():
            if ammo < 1:
                self.add_job(f"load:{cannon}", "load", self.stations[cannon], 3, 15)
        for job in self.jobs:
            if job.state in ("pending", "blocked") and not any(c.labors[job.kind] for c in self.crew):
                job.state, job.reason = "blocked", "No crew member has this labor enabled"
            elif job.state in ("pending", "blocked") and job.assigned_to:
                assigned = self.sailor(job.assigned_to)
                if not assigned.labors[job.kind]:
                    job.state, job.reason = "blocked", f"Assigned sailor {assigned.name} has this labor OFF"

    @staticmethod
    def cost(kind):
        return {"repair": {"planks": 1}, "cook": {"raw_food": 2, "water": 1},
                "load": {"shot": 1}}.get(kind, {})

    def job_cost(self, job):
        if job.transfer:
            return dict(job.transfer["bundle"])
        if job.relocation:
            return {}
        if job.construction:
            return {"planks": 1} if job.construction["action"] == "build" else {}
        return self.cost(job.kind)

    @staticmethod
    def job_label(job):
        if job.transfer:
            return f"Haul supplies {job.transfer['source']} -> {job.transfer['destination']}"
        if job.relocation:
            return "Relocate " + job.relocation["fixture_id"].replace("_", " ")
        if job.construction:
            return f"{job.construction['action'].title()} {job.construction['kind']}"
        return LABELS[job.kind]

    def claim(self, world, sailor):
        options = []
        for job in self.jobs:
            if job.state not in ("pending", "blocked") or sailor.labors[job.kind] == 0:
                continue
            if job.assigned_to and job.assigned_to != sailor.id:
                continue
            if not job.relocation and not self.job_station_available(job):
                job.state, job.reason = "blocked", "Required equipment is being relocated"
                continue
            if sailor.morale < 20 and job.priority > 1 and job.kind != "cook":
                job.reason = f"{sailor.name} is too distressed for ordinary work"
                continue
            cost = self.job_cost(job)
            if any(self.stores[k] < amount for k, amount in cost.items()):
                job.state, job.reason = "blocked", "Missing " + ", ".join(k for k in cost if self.stores[k] < cost[k])
                continue
            allocations = self.storage.plan(world, sailor, cost,
                                           only=job.transfer["source"] if job.transfer else None)
            if allocations is None:
                job.state, job.reason = "blocked", "No reachable stocked supply source"
                continue
            target = (tuple(job.relocation["source"]) if job.relocation else
                      self.storage.position(allocations[0]["pile"], world.fixtures) if allocations else job.target)
            route = self.path(world, sailor, target)
            if route is None or self.path(world, sailor, job.target) is None:
                job.state, job.reason = "blocked", "No reachable work site / supply route"
                continue
            options.append(((0 if job.assigned_to == sailor.id else 1,
                             job.priority, sailor.labors[job.kind], len(route), job.id), job, allocations))
        if not options:
            return
        _, job, allocations = min(options, key=lambda pair: pair[0])
        job.state, job.worker, job.reason = "claimed", sailor.id, ""
        sailor.job_id = job.id
        job.reserved = dict(self.job_cost(job))
        job.supply_plan = allocations
        self.storage.reserve(allocations)
        job.phase = "collect" if job.reserved else "work"
        self.record("claimed", sailor, job, "Direct order" if job.assigned_to else "Eligible labor and priority")

    def release(self, job):
        """Return a claim to the board, retaining work progress and direct orders."""
        self.storage.release_uncollected(job)
        sailor = self.sailor(job.worker)
        if sailor and (sailor.cargo or (job.relocation and job.relocation["stage"] == "haul")):
            job.release_requested = True
            job.state, job.reason = "returning", "Reassignment: returning carried supplies"
        else:
            if sailor:
                sailor.job_id = None
            job.state, job.worker, job.reason = "pending", None, "Waiting for an eligible sailor"
            job.phase = "work"
        self.record("released", sailor, job, job.reason)

    def assign(self, job_id, sid):
        job = self.job(job_id)
        if not job or job.state in ("done", "cancelled") or job.cancel_requested:
            raise ValueError("Only active chores can be assigned")
        if sid is not None and not self.sailor(sid):
            raise ValueError("Unknown sailor")
        job.assigned_to = sid
        if job.worker and sid and job.worker != sid:
            self.release(job)
        self.record("assigned" if sid else "unassigned", self.sailor(sid), job,
                    "Direct orders respect labor OFF, urgent needs and supply/route constraints")

    def check_orders(self):
        """Independent live invariant audit, also used by the captain's report."""
        errors, workers = [], set()
        for job in self.jobs:
            if job.state not in ("claimed", "paused", "returning"):
                if job.worker is not None or job.reserved:
                    errors.append(f"Job {job.id}: orphaned worker / hold reservation")
                continue
            sailor = self.sailor(job.worker)
            if not sailor or sailor.job_id != job.id or job.worker in workers:
                errors.append(f"Job {job.id}: invalid / duplicate crew claim")
                continue
            workers.add(job.worker)
            if job.supply_plan:
                errors.extend(allocation_errors(self, job, sailor))
            if job.cancel_requested or job.release_requested:
                if job.reserved or sailor.cargo != self.job_cost(job):
                    errors.append(f"Job {job.id}: invalid supply return bundle")
            elif job.phase == "collect" and sailor.cargo and not job.supply_plan:
                errors.append(f"Job {job.id}: supplies reserved and carried twice")
            if not (job.cancel_requested or job.release_requested):
                if not sailor.labors[job.kind]:
                    errors.append(f"{sailor.name}: working a disabled labor")
                if job.assigned_to and job.assigned_to != sailor.id:
                    errors.append(f"Job {job.id}: wrong assignee")
                if not job.supply_plan and (job.reserved if job.phase == "collect" else sailor.cargo) != self.job_cost(job):
                    errors.append(f"Job {job.id}: missing / duplicate supply bundle")
        for sailor in self.crew:
            job = self.job(sailor.job_id)
            if sailor.job_id is not None and (not job or job.worker != sailor.id):
                errors.append(f"{sailor.name}: orphaned order")
            if sailor.cargo and not job:
                errors.append(f"{sailor.name}: carried supplies have no claim")
        from pirates.ship_relocation import invariant_errors
        errors.extend(invariant_errors(self))
        from pirates.ship_storage import transfer_capacity_errors
        errors.extend(transfer_capacity_errors(self))
        return errors

    def cancel(self, job_id):
        job = self.job(job_id)
        if not job or job.state in ("done", "cancelled"):
            return
        job.release_requested = False
        self.storage.release_uncollected(job)
        self.cooldowns[job.key] = self.seconds + 120
        sailor = self.sailor(job.worker)
        if sailor and (sailor.cargo or (job.relocation and job.relocation["stage"] == "haul")):
            job.cancel_requested = True
            job.state, job.reason = "returning", "Returning carried supplies to hold"
        else:
            if sailor:
                sailor.job_id = None
            job.state, job.worker, job.reason = "cancelled", None, "Cancelled by captain (2 min cooldown)"
        self.record("cancelled", sailor, job, job.reason)

    def set_labor(self, sid, kind, value):
        if kind not in LABORS or type(value) is not int or not 0 <= value <= 5:
            raise ValueError("Labor priority must be 0 (off) or 1–5")
        sailor = self.sailor(sid)
        if sailor is None:
            raise ValueError("Unknown sailor")
        sailor.labors[kind] = value
        job = self.job(sailor.job_id)
        if value == 0 and job and job.kind == kind:
            # Unclaim, without losing supplies already on the sailor.
            self.release(job)
        self.record("labor_changed", sailor, reason=f"{kind}: {value}")

    def finish(self, job, sailor):
        if job.transfer:
            add_bundle(self.storage.piles[job.transfer["destination"]].contents, sailor.cargo)
        elif job.construction or job.relocation:
            pass  # Geometry is committed by work_job before supplies are consumed.
        elif job.kind == "repair":
            self.hull = min(100, self.hull + (16 if "perfectionist" in sailor.traits else 14))
        elif job.kind == "bail":
            self.bilge = max(0, self.bilge - 12)
        elif job.kind == "cook":
            self.meals += 6
        elif job.kind == "clean":
            self.dirt[f"{job.target[0]},{job.target[1]}"] = 0
        elif job.kind == "rig":
            self.rigging = min(100, self.rigging + 25)
        elif job.kind == "load":
            cannon = next(k for k in self.ammo if self.stations[k] == tuple(job.target))
            self.ammo[cannon] = 1
        sailor.cargo = {}
        sailor.completed += 1
        sailor.skills[job.kind] = min(10, sailor.skills[job.kind] + 0.05)
        sailor.think(f"Finished: {self.job_label(job).lower()}.", 2 if sailor.preference == "craft" else 1)
        if sailor.preference == "craft":
            sailor.boredom = max(0, sailor.boredom - 20)
        self.event(f"{sailor.name}: {self.job_label(job).lower()} completed.")
        self.record("completed", sailor, job, f"Work {job.progress:.1f}/{job.work}; supplies consumed")
        job.state, job.worker = "done", None
        sailor.job_id = None

    def work_job(self, world, sailor, job):
        if job.relocation:
            from pirates.ship_relocation import work
            work(world, sailor, job)
            return
        if job.cancel_requested or job.release_requested:
            if job.supply_plan:
                from pirates.ship_storage import return_cargo
                return_cargo(world, sailor, job)
                return
            sailor.activity = "Returning supplies to hold"
            if self.walk(world, sailor, self.stations["hold"]):
                for resource, amount in sailor.cargo.items():
                    self.stores[resource] += amount
                sailor.cargo = {}
                sailor.job_id = None
                job.state, job.worker = ("cancelled" if job.cancel_requested else "pending"), None
                job.release_requested = False
                job.reason = "Supplies returned; waiting for assignment" if not job.cancel_requested else "Cancelled; supplies returned"
                self.record("supplies_returned", sailor, job, job.reason)
            return
        if job.state == "paused":
            self.record("resumed", sailor, job, "Personal need resolved")
        job.state = "claimed"
        if not self.job_station_available(job):
            job.reason = "Required equipment is being relocated"
            sailor.activity = job.reason
            return
        if job.transfer:
            if not self.storage.available(job.transfer["destination"], world.fixtures):
                job.reason = "Destination storage is being relocated"
                return
            job.target = self.storage.position(job.transfer["destination"], world.fixtures)
        if job.phase == "collect":
            if job.supply_plan:
                from pirates.ship_storage import collect
                collect(world, sailor, job)
                return
            sailor.activity = f"Collecting supplies: {self.job_label(job)}"
            if self.walk(world, sailor, self.stations["hold"]):
                sailor.cargo = dict(job.reserved)
                job.reserved = {}
                job.phase = "work"
                self.record("supplies_collected", sailor, job, str(sailor.cargo))
            return
        sailor.activity = ("Carrying supplies: " if sailor.cargo else "Going to: ") + self.job_label(job)
        if self.walk(world, sailor, job.target):
            sailor.activity = self.job_label(job)
            if job.progress == 0:
                self.record("work_started", sailor, job, "At work site with required supplies")
            pace = (1 + sailor.skills[job.kind] * 0.2) * (0.65 + 0.35 * sailor.energy / 100)
            pace *= 0.9 if "perfectionist" in sailor.traits else 1.1 if "hard_worker" in sailor.traits else 1
            pace *= world.economy.bonus(sailor.id, "labor", job.kind)
            job.progress = min(job.work, job.progress + pace)
            sailor.fatigue = min(100, sailor.fatigue + 0.025 * world.economy.bonus(sailor.id, "fatigue_multiplier"))
            sailor.energy = max(0, sailor.energy - 0.18)
            xp = 1.4 if "quick_learner" in sailor.traits else 1
            sailor.skill_xp[job.kind] += xp
            sailor.skills[job.kind] = min(10, sailor.skills[job.kind] + 0.002 * xp)
            if job.progress >= job.work:
                if job.construction:
                    from pirates.ship_building import complete
                    try:
                        complete(world, job)
                    except ValueError as error:
                        job.reason = str(error)
                        sailor.activity = "Construction blocked; " + str(error)
                        return
                self.finish(job, sailor)

    def choose_need(self, sailor, hour=8):
        # Critical needs preempt even a lower-priority need already underway.
        if sailor.thirst >= 85 and self.stores["water"]:
            return "drink"
        if sailor.hunger >= 85 and self.meals:
            return "eat"
        if sailor.sleep_debt >= 90 or sailor.fatigue >= 95:
            return "sleep"
        if sailor.intent:
            return sailor.intent
        if sailor.thirst >= 65 and self.stores["water"]:
            return "drink"
        if sailor.hunger >= (55 if "hearty_appetite" in sailor.traits else 65) and self.meals:
            return "eat"
        sleep_start, sleep_end = (0, 8) if "night_owl" in sailor.traits else (21, 5) if "early_bird" in sailor.traits else (22, 6)
        night = (sleep_start <= hour < sleep_end) if sleep_start < sleep_end else (hour >= sleep_start or hour < sleep_end)
        if sailor.sleep_debt >= 70 or (night and sailor.sleep_debt >= 40):
            return "sleep"
        if sailor.energy <= 35 or sailor.fatigue >= 65 or sailor.stress >= 70:
            return "rest"
        working = self.job(sailor.job_id) is not None
        social_threshold = 45 if "social_butterfly" in sailor.traits else 75 if "shy" in sailor.traits else 60
        if working and sailor.personality["diligence"] >= 75:
            social_threshold += 15
        if sailor.loneliness >= social_threshold:
            return "social"
        if sailor.boredom >= (45 if "restless" in sailor.traits else 60) + (15 if working else 0):
            return "lookout" if sailor.preference == "lookout" else "relax"
        return ""

    def job_station_available(self, job):
        if self.layout is None:
            return True
        key = ("galley" if job.kind == "cook" else "rigging" if job.kind == "rig"
               else job.key.split(":", 1)[1] if job.kind == "load" else None)
        return ((key is None or self.layout.available(key))
                and (not job.transfer or self.storage.available(job.transfer["destination"], self.layout)))

    def satisfy(self, world, sailor, intent):
        if intent != sailor.intent:
            sailor.intent, sailor.action_seconds = intent, 0
        targets = {"eat": "mess", "drink": "water", "social": "mess",
                    "relax": "leisure", "rest": "leisure", "lookout": "lookout"}
        fixture_id = f"bunk:{sailor.bunk}" if intent == "sleep" else targets[intent]
        if self.layout is not None and not self.layout.available(fixture_id):
            sailor.activity = "Waiting for relocated equipment: " + fixture_id
            sailor.action_seconds = 0
            return
        target = self.bunks[sailor.bunk] if intent == "sleep" else self.stations[targets[intent]]
        sailor.activity = f"Seeking {intent}"
        if not self.walk(world, sailor, target, exact=intent == "sleep"):
            sailor.action_seconds = 0
            return
        sailor.activity = {"sleep": "Sleeping in own bunk", "eat": "Eating at mess",
                           "drink": "Drinking fresh water", "social": "Waiting for company",
                           "relax": "Relaxing on deck", "rest": "Resting (not sleeping)", "lookout": "Watching the sea"}[intent]
        sailor.action_seconds += 1
        if intent in ("drink", "eat"):
            available = self.stores["water"] if intent == "drink" else self.meals
            if not available:
                sailor.think("The water cask is empty." if intent == "drink" else "No prepared meal is left.", -2)
                sailor.intent = ""
                return
            if sailor.action_seconds >= 6:
                if intent == "drink":
                    self.stores["water"] -= 1
                    sailor.thirst = max(0, sailor.thirst - 75)
                else:
                    self.meals -= 1
                    sailor.hunger = max(0, sailor.hunger - 75)
                sailor.think("Fresh water, at last." if intent == "drink" else "A warm meal in the mess.", 3)
                sailor.stress = max(0, sailor.stress - (3 if intent == "drink" else 5))
                self.record("need_satisfied", sailor, self.job(sailor.job_id), intent)
                sailor.intent = ""
        elif intent == "sleep":
            quality = 0.8 if "light_sleeper" in sailor.traits else 1
            sailor.fatigue = max(0, sailor.fatigue - 0.5 * quality)
            sailor.sleep_debt = max(0, sailor.sleep_debt - 0.4 * quality)
            sailor.energy = min(100, sailor.energy + 0.6 * quality)
            sailor.stress = max(0, sailor.stress - 0.1)
            if sailor.fatigue <= 15 and sailor.sleep_debt <= 20:
                sailor.think("Slept in my own bunk.", 4)
                self.record("need_satisfied", sailor, self.job(sailor.job_id), "sleep in assigned bunk")
                sailor.intent = ""
        elif intent == "rest":
            sailor.energy = min(100, sailor.energy + 0.9)
            sailor.fatigue = max(0, sailor.fatigue - 0.3)
            sailor.stress = max(0, sailor.stress - 0.4)
            # A breather restores stamina, but does NOT repay sleep debt.
            if sailor.energy >= 85 and sailor.fatigue <= 35 and sailor.stress <= 30:
                sailor.think("A proper breather restored my strength.", 2)
                self.record("need_satisfied", sailor, self.job(sailor.job_id), "short rest; sleep still required later")
                sailor.intent = ""
        elif intent == "social":
            partners = [c for c in self.crew if c is not sailor and c.intent != "sleep"
                        and abs(c.x - sailor.x) + abs(c.y - sailor.y) <= 2]
            if partners:
                partner = min(partners, key=lambda c: c.id)
                sailor.activity = f"Chatting with {partner.name}"
                sailor.loneliness = max(0, sailor.loneliness - 2)
                partner.loneliness = max(0, partner.loneliness - 1)
                if sailor.action_seconds >= 15:
                    sailor.relationships[partner.id] = min(100, sailor.relationships.get(partner.id, 0) + 1)
                    partner.relationships[sailor.id] = min(100, partner.relationships.get(sailor.id, 0) + 1)
                    sailor.think(f"Shared a story with {partner.name}.", 4)
                    partner.think(f"Shared a story with {sailor.name}.", 2)
                    sailor.stress = max(0, sailor.stress - 6)
                    sailor.loneliness = max(0, sailor.loneliness - 25)
                    sailor.intent = ""
            elif sailor.action_seconds >= 20:
                sailor.think("Nobody had time for a story.", -1)
                sailor.intent = ""
                # Try work for a while before seeking company again.
                sailor.loneliness = max(0, sailor.loneliness - 5)
        elif sailor.action_seconds >= 20:
            sailor.boredom = max(0, sailor.boredom - 55)
            sailor.think("The sea breeze cleared my head." if intent == "lookout" else "A quiet moment on deck.", 4)
            sailor.intent = ""

    def advance(self, world, ticks):
        while ticks:
            part = min(ticks, 100 - self.remainder)
            world.clock.advance(part)
            self.remainder += part
            ticks -= part
            if self.remainder == 100:
                self.remainder = 0
                self.step(world)
                if getattr(getattr(world, "voyages", None), "interrupt_current_advance", lambda: False)():
                    break

    def step(self, world):
        self.seconds += 1
        # Wear and filth are slow; existing leaks cause actual bilge ingress.
        self.bilge = min(100, self.bilge + max(0, 100 - self.hull) * 0.0004)
        self.rigging = max(0, self.rigging - 0.001)
        for key in self.dirt:
            self.dirt[key] = min(100, self.dirt[key] + 0.003)
        self.generate_jobs()
        ordered = self.crew[self.seconds % len(self.crew):] + self.crew[:self.seconds % len(self.crew)]
        for sailor in ordered:
            for memory in sailor.memories:
                memory["remaining"] -= 1
            sailor.memories = [m for m in sailor.memories if m["remaining"] > 0]
            for key, rate in (("hunger", 0.008 * (1.35 if "hearty_appetite" in sailor.traits else 1)),
                              ("thirst", 0.015), ("fatigue", 0.004), ("sleep_debt", 0.006),
                              ("loneliness", 0.004 + sailor.personality["sociability"] * 0.00012),
                              ("boredom", 0.004 + sailor.personality["curiosity"] * 0.00008)):
                setattr(sailor, key, min(100, getattr(sailor, key) + rate))
            pressure = sum(max(0, v - 65) for v in (sailor.hunger, sailor.thirst, sailor.sleep_debt))
            sailor.stress = min(100, max(0, sailor.stress + pressure * 0.0005 * (0.7 if "patient" in sailor.traits else 1)
                                      - sailor.personality["resilience"] * 0.00005))
            sailor.morale = max(0, min(100, 65 + (sailor.personality["resilience"] - 50) * 0.1
                                + sum(m["mood"] * m["remaining"] / 1200 for m in sailor.memories)
                                - sailor.stress * 0.4 - pressure * 0.15))
            if sailor.hunger >= 95 or sailor.thirst >= 95:
                sailor.health = max(0, sailor.health - 0.015)
                sailor.morale = max(0, sailor.morale - 0.005)
                sailor.think("I need food or fresh water urgently!", -1)
            else:
                sailor.health = min(100, sailor.health + 0.01)
            job = self.job(sailor.job_id)
            intent = self.choose_need(sailor, world.clock.hour_minute[0])
            if intent:
                if job and job.relocation and job.relocation["stage"] == "haul":
                    # A carrier must physically set equipment back before a need trip.
                    job.release_requested = True
                    job.reason = "Returning equipment before attending to " + intent
                    self.work_job(world, sailor, job)
                    continue
                if job:
                    if job.state != "paused" and not (job.cancel_requested or job.release_requested):
                        self.record("paused", sailor, job, f"Personal need: {intent}")
                    job.state = "paused" if not (job.cancel_requested or job.release_requested) else "returning"
                    job.reason = f"{sailor.name} is attending to {intent}"
                self.satisfy(world, sailor, intent)
                continue
            if job and (job.cancel_requested or job.release_requested):
                self.work_job(world, sailor, job)
                continue
            if sailor.health <= 0:
                sailor.activity = "Collapsed: needs provisions"
                continue
            if world.economy.drill_tick(world, sailor):
                continue
            if not job:
                self.claim(world, sailor)
                job = self.job(sailor.job_id)
            if job:
                if sailor.morale < 20 and job.priority > 1 and job.kind != "cook":
                    job.state, job.reason = "paused", "Too distressed for ordinary work; needs relief"
                    sailor.activity = job.reason
                    continue
                job.reason = ""
                self.work_job(world, sailor, job)
            else:
                sailing_order = getattr(getattr(world, "voyages", None), "crew_activity", None)
                if not sailing_order or not sailing_order(world, sailor):
                    sailor.activity = "Off duty / no eligible work"
                    # Free time is used for personal interests, not scripted patrols.
                    sailor.boredom = min(100, sailor.boredom + 0.04)
                    if sailor.boredom >= 40:
                        sailor.intent = "social" if sailor.personality["sociability"] >= 75 and sailor.loneliness >= 30 else "lookout" if sailor.preference == "lookout" else "relax"
        if hasattr(world, "on_ship_second"):
            world.on_ship_second()

    def snapshot(self):
        return {"seconds": self.seconds, "remainder": self.remainder, "next_id": self.next_id,
                "stores": dict(self.stores), "meals": self.meals, "hull": self.hull,
                "bilge": self.bilge, "rigging": self.rigging, "dirt": dict(self.dirt),
                "ammo": dict(self.ammo), "cooldowns": dict(self.cooldowns),
                "crew": [asdict(c) for c in self.crew], "jobs": [asdict(j) for j in self.jobs],
                "events": list(self.events), "audit": list(self.audit), "stockpiles": self.storage.snapshot()}

    @classmethod
    def restore(cls, world, data):
        """Validate identities, ranges, geometry, materials and reciprocal claims."""
        ship = cls()
        ship.layout = world.fixtures
        def integer(value, lo=0, hi=10**12):
            if type(value) is not int or not lo <= value <= hi:
                raise ValueError("Invalid ship integer")
            return value
        def number(value, lo=0, hi=100):
            if type(value) not in (int, float) or not math.isfinite(value) or not lo <= value <= hi:
                raise ValueError("Invalid ship number")
            return value
        def materials(value):
            if not isinstance(value, dict) or any(k not in RESOURCES for k in value):
                raise ValueError("Invalid supply bundle")
            return {k: integer(v, 1) for k, v in value.items()}
        def strings(value, limit):
            if not isinstance(value, list) or len(value) > limit or any(not isinstance(v, str) for v in value):
                raise ValueError("Invalid thought/event list")
            return value
        ship.seconds = integer(data["seconds"])
        ship.remainder = integer(data["remainder"], 0, 99)
        ship.next_id = integer(data["next_id"], 1)
        if set(data["stores"]) != set(RESOURCES):
            raise ValueError("Invalid hold stores")
        saved_stores = {k: integer(v) for k, v in data["stores"].items()}
        if "stockpiles" in data:
            ship.storage = SpatialStorage.restore(data["stockpiles"], world.ship_model, world.fixtures)
            if dict(ship.stores) != saved_stores:
                raise ValueError("Stockpile contents disagree with available supply summary")
        else:
            # Migrate old aggregate counts into piles attached to current fixture positions.
            ship.storage = SpatialStorage.starter(ship.stations)
            for resource, amount in saved_stores.items():
                ship.stores[resource] = amount
        ship.meals = integer(data["meals"])
        for key in ("hull", "bilge", "rigging"):
            setattr(ship, key, number(data[key]))
        if set(data["dirt"]) != set(ship.dirt) or set(data["ammo"]) != set(ship.ammo):
            raise ValueError("Invalid ship sites")
        ship.dirt = {k: number(v) for k, v in data["dirt"].items()}
        ship.ammo = {k: integer(v, 0, 1) for k, v in data["ammo"].items()}
        ship.cooldowns = {str(k): integer(v) for k, v in data["cooldowns"].items()}
        ship.events = strings(data["events"], 30)
        ship.audit = data.get("audit", [])
        if not isinstance(ship.audit, list) or len(ship.audit) > 100:
            raise ValueError("Invalid work audit")
        for entry in ship.audit:
            if not isinstance(entry, dict) or set(entry) != {"second", "event", "crew", "job", "reason"}:
                raise ValueError("Invalid audit record")
            integer(entry["second"], 0, ship.seconds)
            if not isinstance(entry["event"], str) or not isinstance(entry["reason"], str):
                raise ValueError("Invalid audit text")
            if entry["crew"] is not None and not ship.sailor(entry["crew"]):
                raise ValueError("Invalid audit sailor")
            if entry["job"] is not None:
                integer(entry["job"], 1, ship.next_id - 1)
        if not isinstance(data["crew"], list) or len(data["crew"]) != len(ship.crew):
            raise ValueError("Invalid crew roster")
        identities = {c.id: c for c in ship.crew}
        crew = []
        occupied = {(world.player.x, world.player.y)}
        for row in data["crew"]:
            if row["id"] not in identities:
                raise ValueError("Unknown crew identity")
            canonical = identities.pop(row["id"])
            sailor = Sailor(**(asdict(canonical) | row))
            if any(getattr(sailor, k) != getattr(canonical, k) for k in ("name", "glyph", "trait", "preference", "bunk")):
                raise ValueError("Invalid sailor identity")
            sailor.color = tuple(canonical.color)
            if sailor.personality != canonical.personality or sailor.traits != canonical.traits:
                raise ValueError("Invalid personality / traits")
            integer(sailor.x, 0, 99)
            integer(sailor.y, 0, 63)
            if not world.game_map.is_walkable(sailor.x, sailor.y) or (sailor.x, sailor.y) in occupied:
                raise ValueError("Invalid / overlapping crew position")
            occupied.add((sailor.x, sailor.y))
            for key in ("hunger", "thirst", "fatigue", "loneliness", "boredom", "morale", "health", "energy", "sleep_debt", "stress"):
                number(getattr(sailor, key))
            if set(sailor.skill_xp) != set(LABORS):
                raise ValueError("Invalid skill experience")
            for value in sailor.skill_xp.values():
                number(value, 0, 10**12)
            if not isinstance(sailor.memories, list) or len(sailor.memories) > 12:
                raise ValueError("Invalid mood memories")
            for memory in sailor.memories:
                if not isinstance(memory, dict) or set(memory) != {"text", "mood", "remaining"} or not isinstance(memory["text"], str):
                    raise ValueError("Invalid mood memory")
                number(memory["mood"], -10, 10)
                integer(memory["remaining"], 1, 1200)
            if set(sailor.labors) != set(LABORS) or set(sailor.skills) != set(LABORS):
                raise ValueError("Invalid labor table")
            for v in sailor.labors.values():
                integer(v, 0, 5)
            for v in sailor.skills.values():
                number(v, 0, 10)
            sailor.cargo = materials(sailor.cargo)
            integer(sailor.haul_wait, 0, 100)
            strings(sailor.thoughts, 8)
            if sailor.intent not in ("", "eat", "drink", "rest", "sleep", "social", "relax", "lookout"):
                raise ValueError("Invalid personal intent")
            if not isinstance(sailor.activity, str):
                raise ValueError("Invalid crew activity")
            integer(sailor.action_seconds)
            integer(sailor.completed)
            if sailor.job_id is not None:
                integer(sailor.job_id, 1)
            if any(k not in {c.id for c in ship.crew} or k == sailor.id for k in sailor.relationships):
                raise ValueError("Invalid relationship")
            for v in sailor.relationships.values():
                integer(v, 0, 100)
            crew.append(sailor)
        ship.crew = crew
        if not isinstance(data["jobs"], list) or len(data["jobs"]) > 100:
            raise ValueError("Invalid job board")
        jobs, ids, active_keys = [], set(), set()
        for row in data["jobs"]:
            job = Job(**row)
            integer(job.id, 1, ship.next_id - 1)
            integer(job.priority, 1, 5)
            integer(job.work, 1, 1000)
            number(job.progress, 0, job.work + 10)
            if job.id in ids or job.kind not in LABORS or job.state not in ("pending", "blocked", "claimed", "paused", "returning", "done", "cancelled"):
                raise ValueError("Invalid chore identity/state")
            ids.add(job.id)
            job.target = tuple(job.target)
            if job.transfer:
                from pirates.ship_storage import validate_transfer
                validate_transfer(job, ship.storage)
            if job.relocation:
                from pirates.ship_relocation import validate_job
                validate_job(job, ship.layout)
            if job.construction:
                from pirates.ship_map import ORIGIN, WIDTH, HEIGHT
                plan = job.construction
                if (not isinstance(plan, dict) or set(plan) != {"action", "kind", "component_id"}
                        or plan["action"] not in ("build", "dismantle")
                        or plan["kind"] not in ("floor", "wall", "door") or job.kind != "repair"
                        or len(job.target) != 2
                        or any(type(v) is not int for v in job.target)):
                    raise ValueError("Invalid construction job")
                if not (ORIGIN[0] <= job.target[0] < ORIGIN[0] + WIDTH
                        and ORIGIN[1] <= job.target[1] < ORIGIN[1] + HEIGHT):
                    raise ValueError("Invalid construction site")
                cid = plan["component_id"]
                if ((plan["action"] == "build" and cid is not None)
                        or (plan["action"] == "dismantle" and (type(cid) is not int or cid < 1))
                        or job.key not in (f"construction:{job.target[0]},{job.target[1]}",
                                           f"construction:{job.target[0]},{job.target[1]}:{plan['kind']}")):
                    raise ValueError("Invalid construction designation")
            expected = {"repair": ship.stations["repair"], "bail": ship.stations["bilge"],
                        "cook": ship.stations["galley"], "rig": ship.stations["rigging"]}
            allowed = [expected[job.kind]] if job.kind in expected else (
                [ship.stations[k] for k in ship.ammo] if job.kind == "load" else
                [tuple(map(int, key.split(","))) for key in ship.dirt])
            if job.state in ("done", "cancelled"):
                # Completed jobs are historical: their station may since have moved.
                if (len(job.target) == 2 and all(type(v) is int for v in job.target)
                        and world.game_map.in_bounds(*job.target)):
                    allowed.append(job.target)
            if ((not job.construction and not job.relocation and not job.transfer and job.target not in allowed)
                    or not isinstance(job.key, str) or not isinstance(job.reason, str)):
                raise ValueError("Invalid chore site")
            if (job.phase not in ("collect", "work") or type(job.cancel_requested) is not bool
                    or type(job.release_requested) is not bool
                    or (job.assigned_to is not None and not ship.sailor(job.assigned_to))):
                raise ValueError("Invalid chore phase")
            job.reserved = materials(job.reserved)
            if assigned := job.state in ("claimed", "paused", "returning"):
                if not job.supply_plan and (job.reserved or (ship.sailor(job.worker) and ship.sailor(job.worker).cargo)):
                    # Old saves had one anonymous source at the authored hold.
                    bundle = job.reserved or dict(ship.sailor(job.worker).cargo)
                    job.supply_plan = [{"pile": "hold", "bundle": dict(bundle),
                                        "collected": not bool(job.reserved), "returned": False}]
            from pirates.ship_storage import validate_allocations
            validate_allocations(job, ship.storage)
            if job.supply_plan and "stockpiles" not in data:
                for row in job.supply_plan:
                    if not row["collected"]:
                        try:
                            ship.storage.reserve([row])
                        except ValueError as error:
                            raise ValueError("Legacy job reservation exceeds saved stores") from error
            if job.state not in ("done", "cancelled"):
                if job.key in active_keys:
                    raise ValueError("Duplicate active chore")
                active_keys.add(job.key)
            worker = ship.sailor(job.worker)
            assigned = job.state in ("claimed", "paused", "returning")
            if assigned != (worker is not None) or (worker and worker.job_id != job.id):
                raise ValueError("Invalid reciprocal job claim")
            if not assigned and (job.worker is not None or job.reserved):
                raise ValueError("Orphaned chore supplies")
            if assigned and not job.supply_plan:
                cost = ship.job_cost(job)
                if job.cancel_requested or job.release_requested:
                    if job.reserved or worker.cargo != cost or (not worker.cargo and not job.relocation):
                        raise ValueError("Invalid returning supplies")
                elif (job.reserved if job.phase == "collect" else worker.cargo) != cost:
                    raise ValueError("Invalid reserved/carried supplies")
                if job.phase == "collect" and worker.cargo:
                    raise ValueError("Supplies exist twice")
            jobs.append(job)
        ship.jobs = jobs
        for sailor in ship.crew:
            job = ship.job(sailor.job_id)
            if sailor.job_id is not None and (not job or job.worker != sailor.id):
                raise ValueError("Orphaned crew claim")
            if sailor.cargo and not job:
                raise ValueError("Orphaned carried supplies")
        if ship.check_orders():
            raise ValueError("Work-order invariant violation: " + "; ".join(ship.check_orders()))
        return ship
