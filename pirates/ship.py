"""Authored sloop, functional stations and autonomous, turn-based ship life.

All simulation time enters through ShipLife.advance; menus never tick the world.
Jobs are exclusive, resumable claims. Supplies are reserved in the hold, then
physically carried by the assigned sailor before any work can complete.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path

from omnisim.world import tile as T
from pirates.ship_storage import (RESOURCES, SpatialStorage, add_bundle, allocation_errors,
                                 migrate_bundle, migrate_store_mapping, resource_quantity)

LABORS = ("repair", "bail", "cook", "clean", "rig", "load")
MAX_STORY_RELATIONSHIP_GAINS_PER_PAIR_PER_DAY = 2
LABELS = {"repair": "Patch hull", "bail": "Bail bilge", "cook": "Cook a meal batch",
          "clean": "Swab deck", "rig": "Tend rigging", "load": "Load cannon"}
FOOD_RECIPES = json.loads((Path(__file__).resolve().parents[1] / "content/food_recipes.json").read_text(encoding="utf-8"))
FOOD_PRESERVATION = json.loads((Path(__file__).resolve().parents[1] / "content/food_preservation.json").read_text(encoding="utf-8"))


def validate_food_recipes():
    outputs = set()
    for recipe_id, recipe in FOOD_RECIPES.items():
        if (not isinstance(recipe_id, str) or not recipe_id
                or not isinstance(recipe, dict)
                or not isinstance(recipe.get("name"), str) or not recipe["name"].strip()
                or not isinstance(recipe.get("output_food_id"), str) or not recipe["output_food_id"].strip()
                or recipe["output_food_id"] in outputs
                or not isinstance(recipe.get("description"), str) or not recipe["description"].strip()
                or type(recipe.get("output_g")) is not int or recipe["output_g"] < 1
                or not isinstance(recipe.get("ingredients"), dict) or not recipe["ingredients"]):
            raise ValueError(f"Invalid food recipe definition: {recipe_id}")
        if any(key not in RESOURCES or not (key.endswith("_g") or key == "clean_water_ml")
               or type(amount) is not int or amount < 1
               for key, amount in recipe["ingredients"].items()):
            raise ValueError(f"Invalid measured ingredients in recipe: {recipe_id}")
        input_mass_g = sum(recipe["ingredients"].values())
        if input_mass_g != recipe["output_g"]:
            raise ValueError(f"Recipe mass is not conserved: {recipe_id}")
        outputs.add(recipe["output_food_id"])


validate_food_recipes()
for _preservation_id, _preservation in FOOD_PRESERVATION.items():
    if (not isinstance(_preservation.get("name"), str) or not _preservation["name"].strip()
            or type(_preservation.get("work")) is not int or _preservation["work"] < 1
            or not isinstance(_preservation.get("input"), dict)
            or not isinstance(_preservation.get("output"), dict)
            or not isinstance(_preservation.get("description"), str)
            or any(key not in RESOURCES or not key.endswith("_g") or type(value) is not int or value < 1
                   for key, value in (_preservation["input"] | _preservation["output"]).items())
            or sum(_preservation["input"].values()) != sum(_preservation["output"].values())):
        raise ValueError(f"Invalid or non-conserving preservation recipe: {_preservation_id}")
READY_FOODS = ("biscuit_g", "fruit_g", "salted_fruit_g", "hard_cheese_g", "dried_fish_g")
FOOD_RESOURCE_KEYS = frozenset({"grain_g", "beans_g", "rice_g", "oats_g", "potatoes_g", "onions_g",
                                "salted_pork_g", "hard_cheese_g", "dried_fish_g", "fruit_g",
                                "salted_fruit_g", "biscuit_g"})
COOKED_FOOD_FRESH_SECONDS = 3 * 24 * 60 * 60
FRESH_FRUIT_SECONDS = 2 * 24 * 60 * 60
READY_FOOD_IDS = {"biscuit_g": "biscuit", "fruit_g": "fruit", "hard_cheese_g": "hard_cheese",
                  "salted_fruit_g": "salted_fruit", "dried_fish_g": "dried_fish"}
FOOD_IDS = set(READY_FOOD_IDS.values()) | {recipe["output_food_id"] for recipe in FOOD_RECIPES.values()} | {"legacy_cooked_food"}
FOOD_SERVING_G = 250
DRINK_SERVING_ML = 250
HUNGER_INCREASE_PER_SECOND = 0.0015
THIRST_INCREASE_PER_SECOND = 0.002
THIRST_RELIEF_PER_LITER = 75
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
    "obedient": "Will follow a direct ordinary work order despite very low morale",
}


def learning_multiplier(potential):
    """Bounded learning-potential modifier, neutral at the ordinary midpoint."""
    return 0.8 + max(0, min(100, potential)) * 0.004


def injury_work_multiplier(injuries):
    """Stack active injury penalties, bounded so an injured sailor can still work."""
    severity = sum(row["severity"] for row in injuries if row["recovery"] < 100)
    return max(0.4, 1 - 0.15 * severity)
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
    z: int = 0
    hunger: float = 25
    thirst: float = 30
    food_consumed_today_g: int = 0
    water_consumed_today_ml: int = 0
    consumption_day: int = 1
    food_preference: str = "variety"
    recent_meals: list = field(default_factory=list)
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
    mental: dict = field(default_factory=dict)
    traits: list = field(default_factory=list)
    energy: float = 100
    sleep_debt: float = 25
    stress: float = 0
    memories: list = field(default_factory=list)
    skill_xp: dict = field(default_factory=dict)
    age: int = 30
    age_origin: int | None = None
    age_origin_day: int = 1
    retirement_notice_day: int | None = None
    background: str = "Sailor"
    learning_potential: float = 50
    injuries: list = field(default_factory=list)
    injury_serial: int = 0
    medical_visit_treated: bool = False
    grievance: float = 0
    desertion_warning_hour: int | None = None
    betrayal_warning_tick: int | None = None
    retention_offer_day: int | None = None
    critical_since_tick: int | None = None
    critical_cause: str = ""
    wetness: float = 0
    cleanliness: float = 100
    last_shared_meal_slot: int = -1
    shared_meal_slot: int = -1

    def get(self, key, default=None):
        return "crew" if key == "role" else getattr(self, key, default)

    def think(self, text, mood=0):
        if not self.thoughts or self.thoughts[-1] != text:
            self.thoughts = (self.thoughts + [text])[-8:]
            self.morale = max(0, min(100, self.morale + mood))
            if mood:
                self.memories = (self.memories + [{"text": text, "mood": mood, "remaining": 1200}])[-12:]

    def suffer_injury(self, kind, severity, cause):
        if kind not in ("burn", "sprain", "fracture") or type(severity) is not int or not 1 <= severity <= 3:
            raise ValueError("Invalid crew injury")
        if not isinstance(cause, str) or not cause:
            raise ValueError("Crew injury needs a cause")
        if any(row["kind"] == kind and row["cause"] == cause and row["recovery"] < 100
               for row in self.injuries):
            return None
        self.injury_serial += 1
        self.medical_visit_treated = False
        injury = {"id": f"{self.id}-injury-{self.injury_serial}", "kind": kind,
                  "severity": severity, "cause": cause, "recovery": 0.0}
        self.injuries = (self.injuries + [injury])[-8:]
        self.health = max(0, self.health - 15 * severity)
        self.stress = min(100, self.stress + 8 * severity)
        self.think(f"I was hurt: {kind.replace('_', ' ')} from {cause}.", -2)
        return injury

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
    reserve_override: bool = False
    fresh_food_lots: list = field(default_factory=list)


class ShipLife:
    def __init__(self):
        self.layout = None  # Bound by World; standalone/legacy instances use templates.
        self.storage = SpatialStorage.starter(STATIONS)
        self.seconds = 0
        self.remainder = 0
        self.next_id = 1
        self.stores = {"grain_g": 18000, "beans_g": 4000, "rice_g": 0, "oats_g": 0,
                       "potatoes_g": 0, "onions_g": 0, "salted_pork_g": 0, "hard_cheese_g": 0,
                       "dried_fish_g": 3000, "fruit_g": 2500, "salted_fruit_g": 0, "salt_g": 0,
                       "biscuit_g": 1500, "food_waste_g": 0,
                       "clean_water_ml": 60000, "questionable_water_ml": 0, "firewood_g": 0,
                       "planks": 16, "shot": 16, "medicine": 3, "bandages": 0, "soap": 0,
                       "lanterns": 0, "lamp_oil_ml": 0, "candles": 0}
        self.cooked_food_batches = [{"id": 1, "food_id": "legacy_cooked_food",
                                     "recipe_id": None, "grams": 1000, "made_at_seconds": 0}]
        self.next_food_batch_id = 2
        self.fresh_food_lots = [{"id": 1, "resource": "fruit_g", "grams": 2500, "received_at_seconds": 0}]
        self.next_fresh_food_lot_id = 2
        self.containers = []
        self.next_container_id = 1
        self.rain_day = 1
        self.rain_collected_today_ml = 0
        self.rain_remainder = 0
        self.light_type = None
        self.light_on = False
        self.light_fuel_ml = 0
        self.candle_fuel_seconds = 0
        self.light_burn_remainder = 0
        self.meal_window_config = {"hours": [9, 15, 21], "group_size": 2,
                                   "stagger_seconds": 600, "grace_seconds": 900}
        self.shared_meal_social_slots = []
        self.social_events = []
        self.next_social_event_id = 1
        self.relationship_story_day = 1
        self.relationship_story_gains = {}
        self.crew_concerns = []
        self.next_crew_concern_id = 1
        self.consumption_day = 1
        self.food_consumed_today_g = 0
        self.water_consumed_today_ml = 0
        self.galley_policy = {"mode": "automatic", "preferred_recipe": None,
                              "ready_food_target_g": 3000, "water_reserve_ml": 0,
                              "emergency_biscuit_reserve_g": 1000}
        self.hull = 72.0
        self.bilge = 18.0
        self.rigging = 62.0
        self.dirt = {"77,26": 70.0, "86,23": 55.0, "79,31": 60.0}
        self.ammo = {"port_cannon": 0, "starboard_cannon": 0}
        self.captives = []
        self.crew_history = []  # one-way records for people no longer aboard
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
        food_preferences = ("fish_chowder", "bean_stew", "grain_porridge", "biscuit", "fruit", "dried_fish")
        for i, (sid, name, glyph, x, y, trait, preference, specialty) in enumerate(definitions):
            labors = {kind: (1 if kind == specialty else 4) for kind in LABORS}
            skills = {kind: (3 if kind == specialty else 1) for kind in LABORS}
            self.crew.append(Sailor(sid, name, glyph, x, y, (130 + i * 18, 205, 195 - i * 12),
                                    trait, preference, i, labors, skills,
                                    food_preference=food_preferences[i]))
        personalities = [
            ({"diligence": 85, "sociability": 35, "resilience": 65, "curiosity": 50}, ["perfectionist", "early_bird"]),
            ({"diligence": 65, "sociability": 90, "resilience": 55, "curiosity": 65}, ["hearty_appetite", "social_butterfly"]),
            ({"diligence": 55, "sociability": 60, "resilience": 45, "curiosity": 90}, ["restless", "light_sleeper"]),
            ({"diligence": 75, "sociability": 20, "resilience": 70, "curiosity": 55}, ["shy", "night_owl"]),
            ({"diligence": 80, "sociability": 45, "resilience": 85, "curiosity": 70}, ["patient", "quick_learner"]),
            ({"diligence": 90, "sociability": 80, "resilience": 60, "curiosity": 40}, ["hard_worker", "social_butterfly"]),
        ]
        mental_profiles = [
            {"intelligence": 70, "discipline": 88, "leadership": 67, "tactical_awareness": 55},
            {"intelligence": 67, "discipline": 70, "leadership": 72, "tactical_awareness": 45},
            {"intelligence": 76, "discipline": 52, "leadership": 58, "tactical_awareness": 70},
            {"intelligence": 65, "discipline": 78, "leadership": 60, "tactical_awareness": 85},
            {"intelligence": 82, "discipline": 80, "leadership": 84, "tactical_awareness": 79},
            {"intelligence": 64, "discipline": 88, "leadership": 90, "tactical_awareness": 66},
        ]
        for sailor, (personality, traits), mental in zip(self.crew, personalities, mental_profiles):
            sailor.personality, sailor.traits, sailor.mental = personality, traits, mental
            sailor.skill_xp = {k: 0.0 for k in LABORS}
            sailor.age_origin, sailor.age_origin_day = sailor.age, 1
        self.crew[1].hunger = 68
        self.crew[2].thirst = 72
        self.crew[4].fatigue = 74
        self.crew[4].sleep_debt = 75
        self.crew[5].loneliness = 66

    def add_recruit(self, world, candidate):
        """Install a recruit-provided hammock and place one named sailor aboard."""
        from pirates.crew_recruitment import sailor_from_candidate

        recruit_id = f"hire:{candidate['id']}"
        if (self.sailor(recruit_id) is not None
                or any(row["id"] == recruit_id for row in self.crew_history)):
            raise ValueError("That person is already aboard or in the former-crew ledger")
        bunk_index = len(self.bunks)
        ship_floors = [c for c in world.ship_model.components if c.kind == "floor" and c.z == 0]
        used_fixtures = {(f.x, f.y, f.z) for f in world.fixtures.fixtures}
        used_actors = {(c.x, c.y, c.z) for c in self.crew} | {
            (world.player.x, world.player.y, world.player.z)}
        bunk_tiles = [(c.x + 69, c.y + 12) for c in self.layout.fixtures if c.kind == "bunk"]
        available = []
        for component in ship_floors:
            wx, wy = component.x + 69, component.y + 12
            if ((component.x, component.y, 0) in used_fixtures
                    or (wx, wy, 0) in used_actors
                    or not world.game_map.is_walkable(wx, wy)):
                continue
            available.append((wx, wy))
        if len(available) < 2:
            raise ValueError("There is no clear deck space for a hammock and a new crewmate")
        bunk_at = min(available, key=lambda at: (min(abs(at[0] - x) + abs(at[1] - y) for x, y in bunk_tiles), at))
        layout = world.fixtures.with_bunk(*bunk_at)
        available.remove(bunk_at)
        start_at = min(available, key=lambda at: (abs(at[0] - bunk_at[0]) + abs(at[1] - bunk_at[1]), at))
        sailor = sailor_from_candidate(candidate["id"], *start_at, 0, bunk_index,
                                       age_origin_day=world.clock.day)
        world.bind_fixtures(layout)
        self.crew.append(sailor)
        for other in self.crew:
            if other is not sailor:
                sailor.relationships[other.id] = 50
                other.relationships[sailor.id] = 50
        self.events = (self.events + [f"{sailor.name} joined the crew at {candidate['port']}."])[-30:]
        return sailor

    def rollback_recruit(self, world, layout, crew, events, sailor_id):
        """Undo a failed hire without leaving a bunk, sailor or relationship behind."""
        for other in crew:
            other.relationships.pop(sailor_id, None)
        self.crew = crew
        self.events = events
        world.bind_fixtures(layout)

    def release_crew_member(self, world, sailor_id):
        """Record the captain's explicit dismissal as a conserved former-crew state."""
        if getattr(world, "at_sea", False) or world.player.z != 0 or world.player.x >= 45:
            raise ValueError("Crew can only be released while the ship is in port")
        return self._depart_crew_member(
            world, sailor_id, "released", "released by the captain at port", require_shore=True)

    def honor_retirement(self, world, sailor_id):
        """Accept an elderly sailor's recorded wish to retire ashore."""
        if getattr(world, "at_sea", False) or world.player.z != 0 or world.player.x >= 45:
            raise ValueError("Retirement arrangements require the captain to be ashore in port")
        sailor = self.sailor(sailor_id)
        if (sailor is None or sailor.retirement_notice_day is None or sailor.age < 65
                or world.clock.day < sailor.retirement_notice_day + 30):
            raise ValueError("That sailor has not asked to retire")
        return self._depart_crew_member(
            world, sailor_id, "retired", "captain honored the retirement request ashore after the 30-day notice", require_shore=True)

    def _depart_crew_member(self, world, sailor_id, status, cause, require_shore=False):
        sailor = self.sailor(sailor_id)
        if sailor is None:
            raise ValueError("That sailor is no longer aboard")
        if status not in ("released", "retired", "deserted", "betrayed", "dead"):
            raise ValueError("Invalid former-crew status")
        if (require_shore and (getattr(world, "at_sea", False) or world.player.z != 0 or world.player.x >= 45)):
            raise ValueError("Crew can only be released while the ship is in port")
        if status == "dead":
            job = self.job(sailor.job_id)
            if job and job.relocation:
                raise ValueError("A carried fixture must be recovered before recording this death")
            if job:
                self.storage.release_uncollected(job)
                for allocation in job.supply_plan:
                    if allocation["collected"] and not allocation["returned"]:
                        add_bundle(self.storage.piles[allocation["pile"]].contents, allocation["bundle"])
                        add_bundle(sailor.cargo, allocation["bundle"], -1)
                        allocation["returned"] = True
                job.supply_plan = []
                job.reserved = {}
                job.state, job.worker, job.cancel_requested, job.release_requested = "cancelled", None, False, False
                job.reason = "Cancelled and supplies recovered after crew death"
                sailor.job_id = None
            for order in self.jobs:
                if order.assigned_to == sailor.id:
                    order.assigned_to = None
        if sailor.job_id is not None or sailor.cargo or any(
                job.worker == sailor.id or job.assigned_to == sailor.id
                for job in self.jobs if job.state not in ("done", "cancelled")):
            raise ValueError("Their active work or carried supplies must be safely returned first")
        if len(self.crew_history) >= 32:
            raise ValueError("The ship's former-crew ledger is full")
        record = {"id": sailor.id, "name": sailor.name, "status": status,
                  "day": world.clock.day, "cause": cause}
        self.crew_history.append(record)
        for concern in self.crew_concerns:
            if concern["crew"] == sailor.id and concern["status"] == "open":
                concern["status"] = "archived"
                concern["resolved_second"] = self.seconds
        economy = getattr(world, "economy", None)
        if economy is not None:
            # Settle the completed day while the sailor is still aboard, so an
            # on-time release at the destination still honours a passage promise.
            economy.employment.settle_day(world, world.clock.day)
            economy.employment.settle_undertakings(world)
        self.crew.remove(sailor)
        for other in self.crew:
            other.relationships.pop(sailor.id, None)
        if economy is not None:
            closed = economy.employment.close(
                sailor.id, world.clock.day, cause,
                "failed" if status == "dead" else "withdrawn")
            if closed is not None:
                economy.employment.announce_undertaking(world, sailor.id)
        self.events = (self.events + [f"{sailor.name} left the active crew ({status}): {cause}."])[-30:]
        if getattr(world, "voyages", None):
            for role, assigned in world.voyages.roles.items():
                if assigned == sailor.id:
                    world.voyages.roles[role] = None
            order = world.voyages.crew_order
            members = [sid for sid in order["members"] if sid != sailor.id]
            if members:
                order["members"] = members
                if order["leader"] == sailor.id:
                    order["leader"] = members[0]
            else:
                order["members"] = []
                order["leader"] = None
        world.message(f"{sailor.name} is no longer aboard ({status}). Their status is recorded in the former-crew ledger.")
        return record

    def _update_grievance(self, world, sailor):
        """Accumulate warning-level discontent from sustained hardship, never a random roll."""
        hour = world.clock.total_ticks // 360000
        injured = any(row["recovery"] < 100 for row in sailor.injuries)
        hardship = (sailor.morale <= 30 and
                    (sailor.stress >= 65 or sailor.hunger >= 75 or sailor.thirst >= 75 or injured))
        if hardship:
            sailor.grievance = min(100, sailor.grievance + 0.05)
        elif (sailor.morale >= 42 and sailor.stress < 55 and sailor.hunger < 60
              and sailor.thirst < 60 and not injured):
            sailor.grievance = max(0, sailor.grievance - 0.04)

        self._update_crew_concern(world, sailor, hardship)

        if sailor.desertion_warning_hour is None and sailor.grievance >= 70 and sailor.morale <= 30:
            sailor.desertion_warning_hour = hour
            text = (f"{sailor.name} is considering leaving after sustained hardship "
                    f"(grievance {sailor.grievance:.0f}, morale {sailor.morale:.0f}).")
            self.event(text)
            world.message(text)
        elif (sailor.desertion_warning_hour is not None and sailor.grievance < 35
              and sailor.morale >= 42):
            sailor.desertion_warning_hour = None
            text = f"{sailor.name} has put aside thoughts of leaving after conditions improved."
            self.event(text)
            world.message(text)

        self._update_betrayal(world, sailor)
        if self.sailor(sailor.id) is not sailor:
            return

        at_port = not getattr(world, "at_sea", False)
        warning_expired = (sailor.desertion_warning_hour is not None
                           and hour >= sailor.desertion_warning_hour + 24)
        can_leave = (sailor.job_id is None and not sailor.cargo and not any(
            job.worker == sailor.id or job.assigned_to == sailor.id
            for job in self.jobs if job.state not in ("done", "cancelled")))
        if (at_port and warning_expired and can_leave and sailor.grievance >= 90
                and sailor.morale <= 25):
            cause = (f"deserted after a day-long warning; grievance {sailor.grievance:.0f}, "
                     f"morale {sailor.morale:.0f}, stress {sailor.stress:.0f}")
            self._depart_crew_member(world, sailor.id, "deserted", cause)

    def _update_crew_concern(self, world, sailor, hardship):
        active = next((row for row in reversed(self.crew_concerns)
                       if row["crew"] == sailor.id and row["status"] == "open"), None)
        if active:
            if sailor.grievance < 35 and sailor.morale >= 42:
                active["status"] = "resolved"
                active["resolved_second"] = self.seconds
                text = f"{sailor.name}'s hardship concern eased after conditions improved."
                self.event(text)
                self.record("crew_concern_resolved", sailor, None,
                            "Existing grievance fell below 35 and morale recovered to at least 42.")
                world.message(text)
            return
        if sailor.grievance < 60 or not hardship:
            return
        signals = []
        if sailor.stress >= 65:
            signals.append("stress")
        if sailor.hunger >= 75:
            signals.append("hunger")
        if sailor.thirst >= 75:
            signals.append("thirst")
        if any(row["recovery"] < 100 for row in sailor.injuries):
            signals.append("injury")
        if not signals:
            return
        if len(self.crew_concerns) >= 64:
            closed_index = next((i for i, row in enumerate(self.crew_concerns)
                                 if row["status"] != "open"), None)
            if closed_index is not None:
                self.crew_concerns.pop(closed_index)
            else:
                return
        concern = {"id": self.next_crew_concern_id, "crew": sailor.id,
                   "opened_second": self.seconds, "status": "open",
                   "signals": signals, "resolved_second": None}
        self.next_crew_concern_id += 1
        self.crew_concerns.append(concern)
        text = (f"{sailor.name} has raised a hardship concern after sustained "
                f"{', '.join(signals)} and low morale.")
        self.event(text)
        self.record("crew_concern_opened", sailor, None,
                    f"Grievance {sailor.grievance:.0f}; morale {sailor.morale:.0f}; signals {', '.join(signals)}.")
        world.message(text)

    def _update_betrayal(self, world, sailor):
        """Resolve a warned, motivated port theft only when the sailor has a real opportunity."""
        at_port = (not getattr(world, "at_sea", False) and world.player.z == 0
                   and world.player.x < 45)
        free = (sailor.job_id is None and not sailor.cargo and not any(
            job.worker == sailor.id or job.assigned_to == sailor.id
            for job in self.jobs if job.state not in ("done", "cancelled")))
        motive = sailor.grievance >= 85 and sailor.morale <= 20 and sailor.stress >= 70
        if sailor.betrayal_warning_tick is None and at_port and free and motive and world.economy.money >= 1:
            sailor.betrayal_warning_tick = world.clock.total_ticks
            text = (f"You have reason to suspect {sailor.name} may steal from the purse and leave; "
                    "they are free ashore and their grievance remains unresolved.")
            self.event(text)
            world.message(text)
        elif sailor.betrayal_warning_tick is not None and (not motive or not at_port):
            # The warning is logged, but its deadline is tied to a real shore-side opportunity.
            sailor.betrayal_warning_tick = None

        warning_expired = (sailor.betrayal_warning_tick is not None
                           and world.clock.total_ticks >= sailor.betrayal_warning_tick + 12 * 360000)
        if (warning_expired and at_port and free and motive and world.economy.money >= 1):
            amount = min(50, world.economy.money)
            world.economy.money -= amount
            cause = (f"betrayed the crew by stealing {amount} Marks after a 12-hour warning; "
                     f"grievance {sailor.grievance:.0f}, morale {sailor.morale:.0f}, stress {sailor.stress:.0f}")
            self._depart_crew_member(world, sailor.id, "betrayed", cause)
            text = f"{sailor.name} stole {amount} Marks from the ship's purse and fled ashore."
            self.event(text)
            world.message(text)

    def _update_critical_condition(self, world, sailor):
        """Give a collapsed sailor a persistent rescue window before a causal death transition."""
        if sailor.health > 0:
            if sailor.critical_since_tick is not None:
                text = f"{sailor.name} recovered from the critical emergency after receiving care."
                self.event(text)
                world.message(text)
            sailor.critical_since_tick = None
            sailor.critical_cause = ""
            return
        if sailor.critical_since_tick is None:
            sailor.critical_since_tick = world.clock.total_ticks
            sailor.medical_visit_treated = False
            injury = next((row for row in reversed(sailor.injuries) if row["recovery"] < 100), None)
            if injury:
                sailor.critical_cause = f"critical {injury['kind']} injury caused by {injury['cause']}"
            elif sailor.hunger >= 95 or sailor.thirst >= 95:
                sailor.critical_cause = "prolonged untreated hunger or thirst"
            else:
                sailor.critical_cause = "critical collapse with no recorded injury"
            hours = 6
            text = (f"MEDICAL EMERGENCY: {sailor.name} has collapsed ({sailor.critical_cause}). "
                    f"Reach an infirmary or restore their health within {hours} game hours.")
            self.event(text)
            world.message(text)
            return

        deadline = sailor.critical_since_tick + 6 * 360000
        if world.clock.total_ticks < deadline:
            return
        job = self.job(sailor.job_id)
        if job and job.relocation:
            return  # never strand real ship equipment by removing its carrier
        cause = (f"died after six game hours in an untreated critical state; {sailor.critical_cause}")
        self._depart_crew_member(world, sailor.id, "dead", cause)

    def offer_retention_bonus(self, world, sailor_id):
        sailor = self.sailor(sailor_id)
        if sailor is None:
            raise ValueError("That sailor is no longer aboard")
        if sailor.retention_offer_day == world.clock.day:
            raise ValueError("A retention bonus has already been offered to this sailor today")
        if sailor.grievance < 60 and sailor.desertion_warning_hour is None:
            raise ValueError("This sailor has not raised serious concerns")
        cost = 25
        if world.economy.money < cost:
            raise ValueError(f"A retention bonus costs {cost} Marks")
        world.economy.money -= cost
        sailor.retention_offer_day = world.clock.day
        sailor.grievance = 0
        sailor.stress = max(0, sailor.stress - 20)
        sailor.desertion_warning_hour = None
        sailor.betrayal_warning_tick = None
        sailor.think("The captain listened and paid a retention bonus.", 5)
        self.event(f"The captain offered {sailor.name} a 25-Mark retention bonus; their leaving warning was withdrawn.")
        world.economy.record(f"Paid {sailor.name} a 25-Mark retention bonus.")
        world.message(f"You spent 25 Marks to keep {sailor.name} aboard and address their grievance.")
        world.advance(6000)

    def _update_age_and_retirement(self, world, sailor):
        elapsed_days = world.clock.day - sailor.age_origin_day
        sailor.age = min(100, sailor.age_origin + elapsed_days // 365)
        if sailor.age >= 65 and sailor.retirement_notice_day is None:
            sailor.retirement_notice_day = world.clock.day
            text = f"{sailor.name} has asked to retire ashore after a long career (age {sailor.age})."
            self.event(text)
            world.message(text)
        if (sailor.retirement_notice_day is not None
                and world.clock.day >= sailor.retirement_notice_day + 30
                and not getattr(world, "at_sea", False) and world.player.z == 0 and world.player.x < 45
                and sailor.job_id is None and not sailor.cargo
                and not any(job.worker == sailor.id or job.assigned_to == sailor.id
                            for job in self.jobs if job.state not in ("done", "cancelled"))):
            cause = (f"retired after asking at age {sailor.age}; captain's 30-day notice was honored")
            self._depart_crew_member(world, sailor.id, "retired", cause)

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
    def prepared_food_g(self):
        return sum(batch["grams"] for batch in self.cooked_food_batches)

    @prepared_food_g.setter
    def prepared_food_g(self, value):
        """Compatibility adapter for tests/older callers assigning pooled food mass."""
        if type(value) is not int or value < 0:
            raise ValueError("Prepared-food grams must be a non-negative integer")
        current = getattr(self, "cooked_food_batches", [])
        total = sum(batch["grams"] for batch in current)
        if value < total:
            remaining = value
            kept = []
            for batch in current:
                amount = min(remaining, batch["grams"])
                if amount:
                    kept.append(batch | {"grams": amount})
                    remaining -= amount
            self.cooked_food_batches = kept
        elif value > total:
            self.cooked_food_batches = list(current) + [{
                "id": getattr(self, "next_food_batch_id", 1), "food_id": "legacy_cooked_food",
                "recipe_id": None, "grams": value - total, "made_at_seconds": getattr(self, "seconds", 0)}]
            self.next_food_batch_id = getattr(self, "next_food_batch_id", 1) + 1

    @property
    def meals(self):
        """Compatibility view: legacy one-meal units map to 250 g portions."""
        return self.prepared_food_g // FOOD_SERVING_G

    @meals.setter
    def meals(self, value):
        if type(value) is not int or value < 0:
            raise ValueError("Prepared-food portion count must be non-negative")
        self.prepared_food_g = value * FOOD_SERVING_G

    def food_stock_g(self):
        return self.prepared_food_g + sum(self.stores[key] for key in FOOD_RESOURCE_KEYS)

    def cooked_food_summary(self):
        names = {recipe["output_food_id"]: recipe["name"] for recipe in FOOD_RECIPES.values()}
        totals = {}
        for batch in self.cooked_food_batches:
            totals[batch["food_id"]] = totals.get(batch["food_id"], 0) + batch["grams"]
        return [(names.get(food_id, "Unidentified cooked provisions"), grams)
                for food_id, grams in totals.items()]

    def cooked_food_freshness(self):
        rows = []
        for batch in self.cooked_food_batches:
            age = max(0, self.seconds - batch["made_at_seconds"])
            condition = max(0, min(100, round(100 * (1 - age / COOKED_FOOD_FRESH_SECONDS))))
            rows.append({"batch_id": batch["id"], "food_id": batch["food_id"],
                         "grams": batch["grams"], "age_seconds": age,
                         "condition": condition, "spoils_in_seconds": max(0, COOKED_FOOD_FRESH_SECONDS - age)})
        return rows

    def fresh_fruit_freshness(self):
        return [{"lot_id": lot["id"], "grams": lot["grams"],
                 "age_seconds": max(0, self.seconds - lot["received_at_seconds"]),
                 "condition": max(0, min(100, round(100 * (1 - max(
                     0, self.seconds - lot["received_at_seconds"]) / FRESH_FRUIT_SECONDS)))),
                 "spoils_in_seconds": max(0, FRESH_FRUIT_SECONDS
                                           - (self.seconds - lot["received_at_seconds"]))}
                for lot in self.fresh_food_lots]

    def spoil_cooked_food(self):
        fresh, spoiled = [], 0
        for batch in self.cooked_food_batches:
            if self.seconds - batch["made_at_seconds"] >= COOKED_FOOD_FRESH_SECONDS:
                spoiled += batch["grams"]
            else:
                fresh.append(batch)
        if spoiled:
            self.cooked_food_batches = fresh
            self.stores["food_waste_g"] += spoiled
            self.record("food_spoiled", reason=f"{spoiled} g cooked food converted to waste; mass retained.")
            self.event(f"{spoiled / 1000:.2f} kg of cooked food spoiled and was moved to waste.")
        return spoiled

    def add_fresh_food_lot(self, resource, grams):
        if resource != "fruit_g" or type(grams) is not int or grams < 1:
            raise ValueError("Unsupported fresh-food lot")
        self.fresh_food_lots.append({"id": self.next_fresh_food_lot_id, "resource": resource,
                                     "grams": grams, "received_at_seconds": self.seconds})
        self.next_fresh_food_lot_id += 1

    def sync_fresh_food_lots(self):
        total = sum(lot["grams"] for lot in self.fresh_food_lots)
        stock = self.stores["fruit_g"]
        if total < stock:
            self.add_fresh_food_lot("fruit_g", stock - total)
        elif total > stock:
            remove = total - stock
            for lot in self.fresh_food_lots:
                used = min(remove, lot["grams"])
                lot["grams"] -= used
                remove -= used
                if not remove:
                    break
            self.fresh_food_lots = [lot for lot in self.fresh_food_lots if lot["grams"]]

    def consume_fresh_food_lot(self, resource, grams):
        remaining = grams
        for lot in self.fresh_food_lots:
            if lot["resource"] != resource or not lot["grams"]:
                continue
            used = min(remaining, lot["grams"])
            lot["grams"] -= used
            remaining -= used
            if not remaining:
                break
        self.fresh_food_lots = [lot for lot in self.fresh_food_lots if lot["grams"]]
        if remaining:
            # Compatibility for test/tools that directly seed canonical stock.
            self.add_fresh_food_lot(resource, remaining)

    def spoil_fresh_food(self):
        self.sync_fresh_food_lots()
        kept, spoiled = [], 0
        for lot in self.fresh_food_lots:
            if self.seconds - lot["received_at_seconds"] >= FRESH_FRUIT_SECONDS:
                spoiled += lot["grams"]
            else:
                kept.append(lot)
        for job in self.jobs:
            if (job.fresh_food_lots
                    and any(self.seconds - lot["received_at_seconds"] >= FRESH_FRUIT_SECONDS
                            for lot in job.fresh_food_lots)):
                job.cancel_requested = True
                job.reason = "Fresh fruit expired while reserved; returning it before spoilage."
        if spoiled:
            self.fresh_food_lots = kept
            self.stores["fruit_g"] -= spoiled
            self.stores["food_waste_g"] += spoiled
            self.record("food_spoiled", reason=f"{spoiled} g fruit converted to waste; mass retained.")
            self.event(f"{spoiled / 1000:.2f} kg of fruit spoiled and was moved to waste.")
        return spoiled

    def dispose_food_waste(self, amount_g):
        if type(amount_g) is not int or amount_g < 1 or amount_g > self.stores["food_waste_g"]:
            raise ValueError("Choose a positive amount of available food waste")
        self.stores["food_waste_g"] -= amount_g
        self.record("food_waste_disposed", reason=f"{amount_g} g discarded overboard; explicit mass sink.")
        self.event(f"Discarded {amount_g / 1000:.2f} kg of food waste overboard.")

    def ready_food_g(self):
        return self.prepared_food_g + sum(self.stores[key] for key in READY_FOODS)

    def galley_water_reserve_ml(self):
        return (self.galley_policy["water_reserve_ml"] or
                len(self.crew) * 2200)

    def edible_food_g(self, *, emergency=False):
        biscuit = self.stores["biscuit_g"]
        if not emergency:
            biscuit = max(0, biscuit - self.galley_policy["emergency_biscuit_reserve_g"])
        return self.prepared_food_g + sum(self.stores[key] for key in READY_FOODS if key != "biscuit_g") + biscuit

    def consume_food_details(self, amount_g, *, emergency=False,
                             preferred_food_id=None, recent_food_ids=()):
        if type(amount_g) is not int or amount_g < 1 or self.edible_food_g(emergency=emergency) < amount_g:
            return None
        remaining = amount_g
        consumed = []
        batches = list(self.cooked_food_batches)
        if preferred_food_id and any(batch["food_id"] == preferred_food_id for batch in batches):
            batches.sort(key=lambda batch: batch["food_id"] != preferred_food_id)
        for batch in batches:
            if not remaining:
                break
            used = min(remaining, batch["grams"])
            consumed.append((batch["food_id"], used))
            batch["grams"] -= used
            remaining -= used
            if batch["grams"] == 0:
                self.cooked_food_batches.remove(batch)
        ready_order = list(READY_FOODS)
        if preferred_food_id:
            favored_key = next((key for key, food_id in READY_FOOD_IDS.items()
                                if food_id == preferred_food_id and self.stores[key] > 0), None)
            if favored_key:
                ready_order.remove(favored_key)
                ready_order.insert(0, favored_key)
        elif recent_food_ids:
            novel_keys = [key for key in ready_order if self.stores[key] > 0
                          and READY_FOOD_IDS[key] not in recent_food_ids[-2:]]
            ready_order = novel_keys + [key for key in ready_order if key not in novel_keys]
        for key in ready_order:
            available = self.stores[key]
            if key == "biscuit_g" and not emergency:
                available = max(0, available - self.galley_policy["emergency_biscuit_reserve_g"])
            used = min(remaining, available)
            self.stores[key] -= used
            if used:
                consumed.append((READY_FOOD_IDS[key], used))
                if key == "fruit_g":
                    self.consume_fresh_food_lot(key, used)
            remaining -= used
            if not remaining:
                break
        return consumed

    def consume_food(self, amount_g, *, emergency=False):
        return self.consume_food_details(amount_g, emergency=emergency) is not None

    def apply_meal_preference(self, sailor, consumed):
        if not consumed:
            return
        food_id = max(enumerate(consumed), key=lambda row: (row[1][1], -row[0]))[1][0]
        if sailor.food_preference == food_id:
            sailor.think(f"A favorite meal: {self.food_name(food_id)}.", 1)
        elif len(sailor.recent_meals) >= 2 and all(item == food_id for item in sailor.recent_meals[-2:]):
            sailor.think(f"The same {self.food_name(food_id).lower()} again; a change would be welcome.", -1)
        elif food_id not in sailor.recent_meals[-2:]:
            sailor.think(f"A welcome change of fare: {self.food_name(food_id)}.", 1)
        else:
            sailor.think(f"Ate {self.food_name(food_id)} at the mess.", 0)
        sailor.recent_meals = (sailor.recent_meals + [food_id])[-4:]

    @staticmethod
    def food_name(food_id):
        names = {"biscuit": "Ship's biscuit", "fruit": "Island fruit", "salted_fruit": "Salted fruit",
                 "dried_fish": "Salted fish"}
        names.update({recipe["output_food_id"]: recipe["name"] for recipe in FOOD_RECIPES.values()})
        return names.get(food_id, "Unidentified provisions")

    def record_consumption(self, world, sailor, *, food_g=0, water_ml=0):
        """Roll daily counters forward and record only actually consumed units."""
        day = world.clock.day
        if day != self.consumption_day:
            self.consumption_day = day
            self.food_consumed_today_g = self.water_consumed_today_ml = 0
        if sailor.consumption_day != day:
            sailor.consumption_day = day
            sailor.food_consumed_today_g = sailor.water_consumed_today_ml = 0
        self.food_consumed_today_g += food_g
        self.water_consumed_today_ml += water_ml
        sailor.food_consumed_today_g += food_g
        sailor.water_consumed_today_ml += water_ml

    def add_container(self, item_id, capacity_ml, tare_g, owner=None):
        if (not isinstance(item_id, str) or not item_id
                or type(capacity_ml) is not int or capacity_ml < 1
                or type(tare_g) is not int or tare_g < 1
                or (owner is not None and (not isinstance(owner, str) or not self.sailor(owner)))):
            raise ValueError("Invalid reusable water container")
        row = {"id": self.next_container_id, "item_id": item_id, "capacity_ml": capacity_ml,
               "tare_g": tare_g, "contents_ml": 0, "quality": None, "owner": owner}
        self.next_container_id += 1
        self.containers.append(row)
        return row

    def container_water_ml(self):
        return sum(row["contents_ml"] for row in self.containers if row["quality"] == "clean")

    def clean_water_total_ml(self):
        return self.stores["clean_water_ml"] + self.container_water_ml()

    def container(self, container_id):
        return next((row for row in self.containers if row["id"] == container_id), None)

    def fill_container(self, container_id, amount_ml):
        vessel = self.container(container_id)
        if (not vessel or type(amount_ml) is not int or amount_ml < 1
                or amount_ml > vessel["capacity_ml"] - vessel["contents_ml"]
                or amount_ml > self.stores["clean_water_ml"]):
            raise ValueError("Container fill exceeds available clean water or vessel capacity")
        self.stores["clean_water_ml"] -= amount_ml
        vessel["contents_ml"] += amount_ml
        vessel["quality"] = "clean"
        return vessel

    def decant_container(self, container_id, amount_ml):
        vessel = self.container(container_id)
        if (not vessel or vessel["quality"] != "clean" or type(amount_ml) is not int
                or amount_ml < 1 or amount_ml > vessel["contents_ml"]):
            raise ValueError("Invalid clean-water decant")
        storage = self.storage
        incoming = {"clean_water_ml": amount_ml}
        if not storage.reserve_capacity("water", incoming, self.jobs):
            raise ValueError("Water stockpile lacks unreserved capacity for this decant")
        self.stores["clean_water_ml"] += amount_ml
        vessel["contents_ml"] -= amount_ml
        if not vessel["contents_ml"]:
            vessel["quality"] = None
        return vessel

    def consume_clean_water(self, sailor, amount_ml):
        if type(amount_ml) is not int or amount_ml < 1:
            return False
        from_stores = min(amount_ml, self.stores["clean_water_ml"])
        remaining = amount_ml - from_stores
        eligible = [vessel for vessel in self.containers
                    if vessel["quality"] == "clean" and (vessel["owner"] is None or vessel["owner"] == sailor.id)]
        if sum(vessel["contents_ml"] for vessel in eligible) < remaining:
            return False
        self.stores["clean_water_ml"] -= from_stores
        for vessel in eligible:
            used = min(remaining, vessel["contents_ml"])
            vessel["contents_ml"] -= used
            remaining -= used
            if not vessel["contents_ml"]:
                vessel["quality"] = None
            if not remaining:
                break
        return True

    @staticmethod
    def is_raining(day, hour):
        """Deterministic coarse weather cycle; only selected actual hours rain."""
        return (day * 7 + hour * 11) % 24 < 4

    def collect_rain_minute(self, world):
        day = world.clock.day
        if day != self.rain_day:
            self.rain_day = day
            self.rain_collected_today_ml = 0
        fixture = world.fixtures.get("rain_collector")
        if (fixture is None or fixture.carrier is not None
                or not self.is_raining(day, world.clock.hour_minute[0])
                or self.rain_collected_today_ml >= 5000
                or self.stores["questionable_water_ml"] >= 250000):
            return 0
        # 120 mL/game-hour catchment: 2 mL per game minute, capped at 5 L/day.
        from pirates.ship_storage import bundle_mass
        pile = self.storage.piles["water"]
        free_capacity_ml = max(0, int((pile.capacity_kg - bundle_mass(pile.contents)) * 1000))
        amount = min(2, 5000 - self.rain_collected_today_ml,
                     250000 - self.stores["questionable_water_ml"], free_capacity_ml)
        if amount <= 0:
            return 0
        self.stores["questionable_water_ml"] += amount
        self.rain_collected_today_ml += amount
        return amount

    def exposed_to_rain(self, sailor, world):
        """Rain reaches a sailor unless a real built deck floor is overhead."""
        local = (sailor.x - 69, sailor.y - 12)
        return (self.is_raining(world.clock.day, world.clock.hour_minute[0])
                and not any(component.kind == "floor" and component.z == sailor.z + 1
                            and (component.x, component.y) == local
                            for component in world.ship_model.components))

    def update_wetness(self, sailor, world):
        """Coarse garment wetting/drying; not a temperature or health model."""
        if self.exposed_to_rain(sailor, world):
            economy = getattr(world, "economy", None)
            gear = getattr(economy, "gear", {}).get(sailor.id, {})
            oilskin_slot = next((slot for slot, item in gear.items() if item == "oilskin"), None)
            condition = getattr(economy, "gear_condition", {}).get(sailor.id, {}).get(oilskin_slot, 100)
            wet_gain = 4 - round(3 * condition / 100) if oilskin_slot else 4
            sailor.wetness = min(100, sailor.wetness + wet_gain)
        else:
            covered = any(component.kind == "floor" and component.z == sailor.z + 1
                          and (component.x, component.y) == (sailor.x - 69, sailor.y - 12)
                          for component in world.ship_model.components)
            sailor.wetness = max(0, sailor.wetness - (4 if covered else 2))

    @staticmethod
    def wear_wet_garments(sailor, economy):
        """Persist one point/hour of wear only for clothing kept severely wet."""
        if sailor.wetness < 80:
            return
        conditions = getattr(economy, "gear_condition", {}).get(sailor.id, {})
        equipment = getattr(economy, "gear", {}).get(sailor.id, {})
        from pirates.economy import CATALOG
        for slot in tuple(conditions):
            if CATALOG[equipment[slot]].get("category") == "clothing":
                conditions[slot] = max(0, conditions[slot] - 1)

    def apply_bandage(self, sailor):
        injury = next((row for row in sailor.injuries if row["recovery"] < 100), None)
        if injury is None:
            raise ValueError("That sailor has no active injury to dress")
        if self.stores["bandages"] < 1:
            raise ValueError("No clean bandage rolls are aboard")
        self.stores["bandages"] -= 1
        injury["recovery"] = min(100.0, injury["recovery"] + 20)
        sailor.think(f"Dressed my {injury['kind']} with a clean bandage.", 1)
        self.record("bandage_used", sailor, None,
                    f"One bandage roll applied to {injury['kind']}; injury recovery advanced by 20 points.")
        return injury

    def wash_with_soap(self, sailor):
        if self.stores["soap"] < 1:
            raise ValueError("No shipboard soap bars are aboard")
        if sailor.cleanliness >= 100:
            raise ValueError("That sailor is already clean")
        if not self.consume_clean_water(sailor, 250):
            raise ValueError("Washing needs 250 mL potable water")
        self.stores["soap"] -= 1
        sailor.cleanliness = min(100, sailor.cleanliness + 40)
        self.record("crew_washed", sailor, None,
                    "Consumed one soap bar and 250 mL clean water; cleanliness improved by 40 points.")
        return 250

    def request_shared_meal(self, world, sailor, job):
        """Offer small, staggered meals only to unassigned sailors; work is never interrupted."""
        if (job is not None or sailor.intent or sailor.health <= 0
                or not self.layout or not self.layout.available("mess")):
            return False
        index = self.crew.index(sailor)
        size = self.meal_window_config["group_size"]
        group = index // size
        window_index = next((i for i, hour in enumerate(self.meal_window_config["hours"])
                             if hour == world.clock.hour_minute[0]), None)
        if window_index is None:
            return False
        due = group * self.meal_window_config["stagger_seconds"]
        within = world.clock.seconds_of_day - world.clock.hour_minute[0] * 3600
        if not due <= within < due + self.meal_window_config["grace_seconds"]:
            return False
        slot = world.clock.day * 100000 + window_index * 1000 + group
        if sailor.last_shared_meal_slot == slot:
            return False
        if self.path(world, sailor, self.stations["mess"]) is None:
            sailor.last_shared_meal_slot = slot
            self.record("shared_meal_skipped", sailor, None, "Mess route is blocked; no duty was interrupted.")
            return False
        sailor.last_shared_meal_slot = slot
        sailor.shared_meal_slot = slot
        sailor.intent = "eat"
        sailor.action_seconds = 0
        sailor.activity = "Called to staggered watch-group meal"
        return True

    def record_shared_meal_attendance(self, sailor, world):
        slot = sailor.shared_meal_slot
        if slot < 0 or slot in self.shared_meal_social_slots:
            return False
        mess = self.stations["mess"]
        attendees = [row for row in self.crew if row is not sailor and row.shared_meal_slot == slot
                     and row.intent == "eat" and abs(row.x - mess[0]) + abs(row.y - mess[1]) <= 2]
        if not attendees:
            return False
        partner = min(attendees, key=lambda row: row.id)
        self.shared_meal_social_slots = (self.shared_meal_social_slots + [slot])[-64:]
        for participant in (sailor, partner):
            participant.morale = min(100, participant.morale + 2)
            participant.think("Shared a scheduled meal with a shipmate.", 2)
        self.record("shared_meal", sailor, None,
                    f"{sailor.name} and {partner.name} ate together at the mess; their individual rations were consumed.")
        self.record_social_event("shared_meal", (sailor, partner),
                                 "Both crew members reached the mess for the same scheduled meal.")
        return True

    def toggle_ship_light(self, kind):
        if kind not in ("lantern", "candle"):
            raise ValueError("Choose the enclosed lantern or a candle")
        if self.light_on:
            self.light_on = False
            self.record("light_extinguished", reason=f"Extinguished {self.light_type}; remaining fuel retained.")
            return False
        if self.light_type == kind and ((kind == "lantern" and self.light_fuel_ml > 0)
                                        or (kind == "candle" and self.candle_fuel_seconds > 0)):
            self.light_on = True
            return True
        if kind == "lantern":
            if self.stores["lanterns"] < 1:
                raise ValueError("No shipboard lantern is aboard")
            if self.light_fuel_ml <= 0:
                if self.stores["lamp_oil_ml"] < 250:
                    raise ValueError("Lighting the lantern needs 250 mL lamp oil")
                self.stores["lamp_oil_ml"] -= 250
                self.light_fuel_ml = 250
        else:
            if self.candle_fuel_seconds <= 0:
                if self.stores["candles"] < 1:
                    raise ValueError("No candles are aboard")
                self.stores["candles"] -= 1
                self.candle_fuel_seconds = 3600
        self.light_type, self.light_on = kind, True
        self.light_burn_remainder = 0
        self.record("light_lit", reason=f"Lit {kind}; shielded flame has no modeled fire ignition risk.")
        return True

    def queue_water_treatment(self):
        world_fixture = getattr(self, "layout", None)
        if world_fixture is None:
            raise ValueError("A reachable galley and cook labor are required to boil water")
        if not world_fixture.available("galley") or not any(s.labors.get("cook", 0) for s in self.crew):
            raise ValueError("A reachable galley and cook labor are required to boil water")
        if self.stores["questionable_water_ml"] < 1000 or self.stores["firewood_g"] < 250:
            raise ValueError("Boiling needs 1 L questionable water and 250 g firewood")
        if self.stores["clean_water_ml"] + 900 > 250000:
            raise ValueError("Clean-water stockpile lacks room for 900 mL treated water")
        if any(job.key == "boil:rainwater" and job.state not in ("done", "cancelled")
               for job in self.jobs):
            raise ValueError("A rainwater boiling batch is already underway")
        return self.add_job("boil:rainwater", "cook", self.stations["galley"], 2, 12)

    def queue_fruit_preservation(self, recipe_id="salted_fruit"):
        recipe = FOOD_PRESERVATION.get(recipe_id)
        if not recipe or not self.layout or not self.layout.available("galley"):
            raise ValueError("A supported preservation recipe and reachable galley are required")
        if not any(s.labors.get("cook", 0) for s in self.crew):
            raise ValueError("No sailor has cook labor enabled")
        if not all(self.stores.get(key, 0) >= amount for key, amount in recipe["input"].items()):
            raise ValueError("Preservation needs 1 kg fresh fruit and 100 g salt")
        if any(job.key == f"preserve:{recipe_id}" and job.state not in ("done", "cancelled")
               for job in self.jobs):
            raise ValueError("A preservation batch is already queued")
        return self.add_job(f"preserve:{recipe_id}", "cook", self.stations["galley"], 2, recipe["work"])

    def reserve_fresh_food_lots(self, job, grams):
        available = sum(lot["grams"] for lot in self.fresh_food_lots)
        if available < grams:
            raise ValueError("Fresh-fruit lots do not cover the reserved preservation input")
        remaining = grams
        reserved, kept = [], []
        for lot in self.fresh_food_lots:
            if not remaining:
                kept.append(lot)
                continue
            take = min(remaining, lot["grams"])
            reserved.append(lot | {"grams": take})
            remaining -= take
            if take < lot["grams"]:
                kept.append(lot | {"id": self.next_fresh_food_lot_id,
                                   "grams": lot["grams"] - take})
                self.next_fresh_food_lot_id += 1
        self.fresh_food_lots = kept
        job.fresh_food_lots = reserved

    def restore_fresh_food_lots(self, job):
        if job.fresh_food_lots:
            self.fresh_food_lots.extend(job.fresh_food_lots)
            self.fresh_food_lots.sort(key=lambda lot: (lot["received_at_seconds"], lot["id"]))
            job.fresh_food_lots = []

    def available_cook_recipe(self, recipe_id=None, *, override_reserve=False):
        candidates = [recipe_id] if recipe_id else list(FOOD_RECIPES)
        for candidate in candidates:
            recipe = FOOD_RECIPES.get(candidate)
            if recipe is None or not all(self.stores.get(key, 0) >= amount
                                         for key, amount in recipe["ingredients"].items()):
                continue
            water_after = self.stores["clean_water_ml"] - recipe["ingredients"].get("clean_water_ml", 0)
            if not override_reserve and water_after < self.galley_water_reserve_ml():
                continue
            return candidate
        return None

    def set_galley_policy(self, **updates):
        allowed = {"mode", "preferred_recipe", "ready_food_target_g", "water_reserve_ml",
                   "emergency_biscuit_reserve_g"}
        if not updates or set(updates) - allowed:
            raise ValueError("Unknown or empty galley policy update")
        candidate = self.galley_policy | updates
        if candidate["mode"] not in ("automatic", "preferred", "maintain", "paused"):
            raise ValueError("Invalid galley policy mode")
        recipe = candidate["preferred_recipe"]
        if recipe is not None and recipe not in FOOD_RECIPES:
            raise ValueError("Unknown preferred recipe")
        for key, maximum in (("ready_food_target_g", 100000), ("water_reserve_ml", 200000),
                             ("emergency_biscuit_reserve_g", 20000)):
            value = candidate[key]
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError(f"Invalid galley policy value: {key}")
        self.galley_policy = candidate

    def queue_galley_once(self, recipe_id, *, emergency_override=False):
        if not self.available_cook_recipe(recipe_id, override_reserve=emergency_override):
            raise ValueError("Recipe ingredients are unavailable or protected water reserve would be breached")
        job = self.add_job(f"cook:{recipe_id}", "cook", self.stations["galley"], 2, 24)
        if emergency_override:
            job.reserve_override = True
            self.event(f"Emergency galley override: {recipe_id} may use protected drinking water.")
            self.record("galley_reserve_override", reason=recipe_id)
        return job

    @property
    def bunks(self):
        return self.layout.bunks if self.layout is not None else BUNKS

    def event(self, text):
        self.events = (self.events + [text])[-30:]

    def record(self, event, sailor=None, job=None, reason=""):
        self.audit = (self.audit + [{"second": self.seconds, "event": event,
                     "crew": sailor.id if sailor else None, "job": job.id if job else None,
                     "reason": reason}])[-100:]

    def record_social_event(self, kind, participants, reason, relationship_delta=0):
        """Persist bounded, attributable social evidence without changing scores."""
        if (kind not in ("shared_meal", "shared_story") or len(participants) != 2
                or participants[0] is participants[1]
                or any(self.sailor(participant.id) is not participant for participant in participants)
                or type(relationship_delta) is not int
                or relationship_delta not in ((0, 1) if kind == "shared_story" else (0,))
                or not isinstance(reason, str) or not reason or len(reason) > 300):
            raise ValueError("Invalid ship social event")
        self.social_events = (self.social_events + [{
            "id": self.next_social_event_id,
            "second": self.seconds,
            "kind": kind,
            "participants": sorted((participants[0].id, participants[1].id)),
            "relationship_delta": relationship_delta,
            "reason": reason,
        }])[-64:]
        self.next_social_event_id += 1

    def sailor(self, sid):
        return next((c for c in self.crew if c.id == sid), None)

    def job(self, jid):
        return next((j for j in self.jobs if j.id == jid), None)

    def occupied(self, world, exclude=None):
        return {(c.x - 69, c.y - 12, getattr(c, "z", 0))
                for c in world.all_entities() if c.id != exclude}

    def goals(self, world, target):
        x, y = target[:2]
        z = target[2] if len(target) > 2 else 0
        return {(x + dx, y + dy, z) for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0))
                if any(c.z == z and c.kind in ("floor", "door")
                       and (c.x + 69, c.y + 12) == (x + dx, y + dy)
                       for c in world.ship_model.components)
                and not any(c.z == z and c.kind == "wall"
                            and (c.x + 69, c.y + 12) == (x + dx, y + dy)
                            for c in world.ship_model.components)}

    def path(self, world, sailor, target, exact=False):
        """BFS through real geometry, with bodies and the captain as obstacles."""
        from pirates.ship_navigation import route, route_to_worksite
        start = sailor.x - 69, sailor.y - 12, sailor.z
        goal = (target[0] - 69, target[1] - 12, target[2] if len(target) > 2 else 0)
        geometry = world.ship_model.navigation_geometry()
        floors = geometry["floor"]
        target_on_ship = 69 <= target[0] < 92 and 12 <= target[1] < 44
        if start not in floors or not target_on_ship:
            # Ship navigation is deliberately restricted to built deck geometry.
            # The Captain also needs an ordinary tile route between that deck and
            # shore markets; route on the live map, never through water or actors.
            return self._world_path(world, sailor, target, exact)
        occupied = self.occupied(world, sailor.id)
        blocked = set(occupied)
        blocked.update((f.x, f.y, f.z) for f in world.fixtures.fixtures
                       if f.carrier is None)
        if world.active_deck == 0:
            for x, y, z in floors:
                wx, wy = x + 69, y + 12
                if (z == 0 and not world.game_map.is_walkable(wx, wy)
                        and world.game_map.tile_id(wx, wy) != T.DOOR):
                    blocked.add((x, y, 0))
        blocked.update(getattr(world, "locked_doors", ()))
        if exact:
            if goal in occupied:
                return None
            # Exact sleep destinations are installed bunks: their fixture tile
            # is intentionally blocked to ordinary walking but remains a valid
            # final destination for the assigned sleeper.
            blocked.discard(goal)
            path = route(world.ship_model, start, goal, blocked)
        else:
            path = route_to_worksite(world.ship_model, start, goal, blocked)
        return None if path is None else [(x + 69, y + 12, z) for x, y, z in path[1:]]

    @staticmethod
    def _world_path(world, actor, target, exact=False):
        """Find a normal four-way route over current walkable map tiles."""
        from collections import deque
        from omnisim.world import tile as T

        start = (actor.x, actor.y)
        target = (target[0], target[1])
        entities = [entity for entity in world.all_entities() if entity is not actor]
        occupied = {(entity.x, entity.y) for entity in entities
                    if getattr(entity, "z", 0) == getattr(actor, "z", 0)}

        def landable(at):
            x, y = at
            if (at in occupied or not world.game_map.in_bounds(x, y)
                    or world.game_map.tile_id(x, y) == T.WATER):
                return False
            if world.game_map.tile_id(x, y) == T.DOOR:
                return (x - 69, y - 12, getattr(actor, "z", 0)) not in world.locked_doors
            return world.game_map.is_walkable(x, y)

        if exact:
            goals = {target} if landable(target) else set()
        else:
            goals = {target} if landable(target) else set()
            goals.update(at for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0))
                         if landable(at := (target[0] + dx, target[1] + dy)))
        if start in goals:
            return []

        def passable(at):
            return landable(at)

        parent = {start: None}
        todo = deque([start])
        found = None
        while todo:
            current = todo.popleft()
            if current in goals:
                found = current
                break
            x, y = current
            for neighbor in ((x, y - 1), (x + 1, y), (x, y + 1), (x - 1, y)):
                if neighbor not in parent and (neighbor in goals or passable(neighbor)):
                    parent[neighbor] = current
                    todo.append(neighbor)
        if found is None:
            return None
        path = []
        while found is not None:
            path.append(found)
            found = parent[found]
        return list(reversed(path))[1:]

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
        x, y, z = route[0]
        local = (x - 69, y - 12, z)
        is_door = local in world.ship_model.navigation_geometry()["door"]
        door_state = world.door_state(z)
        if is_door and not door_state[y, x]:
            door_state[y, x] = True
            sailor.activity = "Opening a ship door"
            return False
        sailor.x, sailor.y, sailor.z = x, y, z
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

    def generate_jobs(self, world=None):
        if self.hull < 100:
            self.add_job("repair", "repair", self.stations["repair"], 1, 30)
        if self.bilge >= 3:
            self.add_job("bail", "bail", self.stations["bilge"], 1, 15)
        if world is not None and hasattr(world, "hazards"):
            active = set()
            for key in sorted(world.hazards.fires):
                x, y, z = world.hazards.cell(key)
                job_key = f"fire:{x},{y},{z}"
                active.add(job_key)
                self.add_job(job_key, "repair", (x + 69, y + 12, z), 0, 8)
            for job in self.jobs:
                if (job.key.startswith("fire:") and job.key not in active
                        and job.state in ("pending", "blocked")):
                    job.state, job.reason = "cancelled", "Fire no longer present"
        policy = self.galley_policy
        if (policy["mode"] != "paused"
                and self.edible_food_g() < policy["ready_food_target_g"]
                and not any(job.kind == "cook" and job.state not in ("done", "cancelled") for job in self.jobs)):
            recipe_id = self.available_cook_recipe(
                policy["preferred_recipe"] if policy["mode"] == "preferred" else None)
            if recipe_id:
                self.add_job(f"cook:{recipe_id}", "cook", self.stations["galley"], 2, 24)
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
        return {"repair": {"planks": 1},
                "load": {"shot": 1}}.get(kind, {})

    def job_cost(self, job):
        if job.transfer:
            return dict(job.transfer["bundle"])
        if job.relocation:
            return {}
        if job.key.startswith("fire:"):
            return {"clean_water_ml": 1000}
        if job.key == "boil:rainwater":
            return {"questionable_water_ml": 1000, "firewood_g": 250}
        if job.key.startswith("preserve:"):
            recipe = FOOD_PRESERVATION.get(job.key.removeprefix("preserve:"))
            return dict(recipe["input"]) if recipe else {}
        if job.kind == "cook":
            recipe_id = job.key.removeprefix("cook:")
            recipe = FOOD_RECIPES.get(recipe_id)
            if recipe is None:
                return {}
            return dict(recipe["ingredients"])
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
        if job.key.startswith("fire:"):
            return "Suppress fire"
        if job.kind == "cook":
            if job.key == "boil:rainwater":
                return "Boil 1 L questionable rainwater"
            if job.key.startswith("preserve:"):
                recipe = FOOD_PRESERVATION.get(job.key.removeprefix("preserve:"), {})
                return "Preserve " + recipe.get("name", "food")
            recipe = FOOD_RECIPES.get(job.key.removeprefix("cook:"), {})
            return "Cook " + recipe.get("name", "food")
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
            if (sailor.morale < 20 and job.priority > 1 and job.kind != "cook"
                    and not (job.assigned_to == sailor.id and "obedient" in sailor.traits)):
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
        if job.key.startswith("preserve:"):
            fruit_grams = FOOD_PRESERVATION[job.key.removeprefix("preserve:")]["input"].get("fruit_g", 0)
            if fruit_grams:
                self.reserve_fresh_food_lots(job, fruit_grams)
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

    def finish(self, job, sailor, world=None):
        if job.transfer:
            add_bundle(self.storage.piles[job.transfer["destination"]].contents, sailor.cargo)
        elif job.construction or job.relocation:
            pass  # Geometry is committed by work_job before supplies are consumed.
        elif job.kind == "repair":
            if job.key.startswith("fire:") and world is not None:
                x, y, z = world.hazards.cell(job.key[5:])
                world.hazards.extinguish(x, y, z)
            else:
                self.hull = min(100, self.hull + (16 if "perfectionist" in sailor.traits else 14))
        elif job.kind == "bail":
            if world is not None and hasattr(world, "hazards"):
                world.hazards.pump(120, world)
                self.bilge = min(100.0, round(
                    world.hazards.total_water_kg() /
                    world.hazards.LEGACY_BILGE_KG_PER_POINT, 9))
            else:
                self.bilge = max(0, self.bilge - 12)
        elif job.kind == "cook":
            if job.key == "boil:rainwater":
                # Check destination capacity before creating potable output.
                from pirates.ship_storage import bundle_mass
                pile = self.storage.piles["water"]
                if bundle_mass(pile.contents) + 0.9 > pile.capacity_kg:
                    raise ValueError("Water stockpile has no room for treated output")
                self.stores["clean_water_ml"] += 900
                self.record("water_boiled", sailor, job,
                            "1,000 mL questionable rainwater + 250 g firewood → 900 mL clean water; 100 mL evaporated.")
                self.event("Boiled rainwater; treatment does not desalinate seawater or remove chemical hazards.")
            elif job.key.startswith("preserve:"):
                recipe = FOOD_PRESERVATION[job.key.removeprefix("preserve:")]
                job.fresh_food_lots = []
                for resource, amount in recipe["output"].items():
                    self.stores[resource] += amount
                self.record("food_preserved", sailor, job, recipe["description"])
                self.event("Fresh fruit preserved with measured salt; brine/trim was recorded as waste.")
            else:
                recipe_id = job.key.removeprefix("cook:")
                recipe = FOOD_RECIPES[recipe_id]
                self.cooked_food_batches.append({
                    "id": self.next_food_batch_id, "food_id": recipe["output_food_id"],
                    "recipe_id": recipe_id, "grams": recipe["output_g"],
                    "made_at_seconds": self.seconds})
                self.next_food_batch_id += 1
                self.record("food_cooked", sailor, job, recipe["description"])
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

    def mentor_for(self, world, sailor, job):
        """Find a nearby, actively working senior crewmate for hands-on practice."""
        if sailor.skills[job.kind] >= 10:
            return None
        for mentor in self.crew:
            if mentor is sailor or mentor.skills[job.kind] < sailor.skills[job.kind] + 2:
                continue
            if (getattr(mentor, "z", 0) != getattr(sailor, "z", 0)
                    or abs(mentor.x - sailor.x) + abs(mentor.y - sailor.y) > 3):
                continue
            mentor_job = self.job(mentor.job_id)
            if (mentor_job and mentor_job.kind == job.kind and mentor_job.state == "claimed"
                    and mentor_job.phase == "work" and mentor.activity == self.job_label(mentor_job)):
                return mentor
        return None

    def work_job(self, world, sailor, job):
        if job.relocation:
            from pirates.ship_relocation import work
            work(world, sailor, job)
            return
        if job.cancel_requested or job.release_requested:
            if job.supply_plan:
                from pirates.ship_storage import return_cargo
                return_cargo(world, sailor, job)
                if not job.supply_plan and job.state in ("pending", "cancelled"):
                    self.restore_fresh_food_lots(job)
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
                self.restore_fresh_food_lots(job)
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
        work_target = (*job.target, job.construction.get("z", 0)) if job.construction else job.target
        if (job.construction and job.construction["kind"] == "floor"
                and work_target[2] > 0
                and self.path(world, sailor, work_target) is None
                and any(c.kind == "floor" and (c.x, c.y, c.z) ==
                        (work_target[0] - 69, work_target[1] - 12, work_target[2] - 1)
                        for c in world.ship_model.components)):
            # Bootstrap the first elevated floor from the reachable lower landing;
            # a physical stair can be installed once the upper floor exists.
            work_target = (*job.target, work_target[2] - 1)
        if job.construction and job.construction["kind"] == "beam":
            if self.path(world, sailor, work_target) is None:
                tx, ty = job.target
                z = work_target[2]
                approaches = [(tx + dx, ty + dy, z)
                              for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0))]
                reachable = [(len(path), target) for target in approaches
                             if (path := self.path(world, sailor, target)) is not None]
                if not reachable:
                    job.reason = "No reachable adjacent work site for this beam"
                    return
                work_target = min(reachable)[1]
        if self.walk(world, sailor, work_target):
            mentor = self.mentor_for(world, sailor, job)
            sailor.activity = (f"{self.job_label(job)} (learning from {mentor.name})"
                               if mentor is not None else self.job_label(job))
            if job.progress == 0:
                self.record("work_started", sailor, job, "At work site with required supplies")
                if mentor is not None:
                    text = f"{sailor.name} is learning {job.kind} from {mentor.name} while both work nearby."
                    self.record("mentored", sailor, job, text)
                    self.event(text)
            pace = (1 + sailor.skills[job.kind] * 0.2) * (0.65 + 0.35 * sailor.energy / 100)
            pace *= injury_work_multiplier(sailor.injuries)
            pace *= 0.9 if "perfectionist" in sailor.traits else 1.1 if "hard_worker" in sailor.traits else 1
            pace *= world.economy.bonus(sailor.id, "labor", job.kind)
            job.progress = min(job.work, job.progress + pace)
            sailor.fatigue = min(100, sailor.fatigue + 0.025 * world.economy.bonus(sailor.id, "fatigue_multiplier"))
            sailor.energy = max(0, sailor.energy - 0.18)
            xp = (1.4 if "quick_learner" in sailor.traits else 1) * learning_multiplier(sailor.learning_potential)
            if mentor is not None:
                # Mentorship improves learning, not output pace; its 25% gain is
                # deterministic and applies only during real, nearby same-labor work.
                xp = min(1.8, xp * 1.25)
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
                self.finish(job, sailor, world)

    def choose_need(self, sailor, hour=8, world=None):
        # Critical needs preempt even a lower-priority need already underway.
        if sailor.thirst >= 85 and self.clean_water_total_ml() > 0:
            return "drink"
        if sailor.hunger >= 85 and self.edible_food_g(emergency=True) >= FOOD_SERVING_G:
            return "eat"
        if (world is not None and any(row["recovery"] < 100 for row in sailor.injuries)
                and self._infirmary_available(world, sailor)):
            return "infirmary"
        if world is not None and sailor.health <= 65 and self._infirmary_available(world, sailor):
            return "infirmary"
        if sailor.sleep_debt >= 90 or sailor.fatigue >= 95:
            return "sleep"
        if sailor.intent:
            return sailor.intent
        if (sailor.thirst >= 65
                and self.clean_water_total_ml() > self.galley_water_reserve_ml()):
            return "drink"
        if world is not None and sailor.health <= 80 and self._infirmary_available(world, sailor):
            return "infirmary"
        if (sailor.hunger >= (55 if "hearty_appetite" in sailor.traits else 65)
                and self.edible_food_g() >= FOOD_SERVING_G):
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

    def _infirmary_available(self, world, sailor):
        if self.layout is None or not self.layout.available("medicine_chest"):
            return False
        at = self.layout.stations.get("medicine_chest")
        space = world.spaces.find(world.ship_model, at[0], at[1]) if at else None
        return bool(space and "infirmary" in world.spaces.purposes(space))

    def brig_ready(self, world):
        if self.layout is None or not self.layout.available("brig_cage"):
            return False
        at = self.layout.stations.get("brig_cage")
        room = world.spaces.find(world.ship_model, at[0], at[1]) if at else None
        if not room or "brig" not in world.spaces.purposes(room) or not room.doors:
            return False
        return all((x, y, 0) in world.locked_doors for x, y in room.doors)

    def capture_boarders(self, enemies, world, capacity=3):
        """Intern surrendered raiders only in the equipped, designated, locked brig."""
        if not self.brig_ready(world):
            return ()
        slots = max(0, capacity - len(self.captives))
        prisoners = []
        for actor in sorted(enemies, key=lambda a: a.id)[:slots]:
            prisoner_id = f"captured:{actor.id}"
            if any(row["id"] == prisoner_id for row in self.captives):
                continue
            row = {"id": prisoner_id, "name": actor.name, "faction": actor.faction}
            self.captives.append(row)
            prisoners.append(prisoner_id)
        if prisoners:
            self.record("captives_secured", None, None,
                        f"{len(prisoners)} boarder(s) secured in the locked brig")
        return tuple(prisoners)

    def release_captive(self, captive_id):
        prisoner = next((row for row in self.captives if row["id"] == captive_id), None)
        if prisoner is None:
            raise ValueError("Unknown captive")
        self.captives.remove(prisoner)
        return prisoner

    def job_station_available(self, job):
        if self.layout is None:
            return True
        key = ("galley" if job.kind == "cook" else "rigging" if job.kind == "rig"
               else job.key.split(":", 1)[1] if job.kind == "load" else None)
        return ((key is None or self.layout.available(key))
                and (not job.transfer or self.storage.available(job.transfer["destination"], self.layout)))

    def satisfy(self, world, sailor, intent):
        if intent != sailor.intent:
            if intent == "infirmary" or sailor.intent == "infirmary":
                sailor.medical_visit_treated = False
            sailor.intent, sailor.action_seconds = intent, 0
        if intent == "infirmary":
            fixture_id = "medicine_chest"
            if not self._infirmary_available(world, sailor):
                sailor.intent, sailor.action_seconds = "", 0
                sailor.activity = "No accessible designated infirmary"
                return
            target = self.layout.stations[fixture_id]
            sailor.activity = "Seeking infirmary"
            if not self.layout.available(fixture_id) or not self.walk(world, sailor, target):
                sailor.action_seconds = 0
                return
            if not sailor.medical_visit_treated:
                if self.stores["medicine"] <= 0:
                    sailor.activity = "Waiting for medical supplies"
                    return
                self.stores["medicine"] -= 1
                sailor.medical_visit_treated = True
                sailor.health = min(100, sailor.health + 10)
                sailor.stress = max(0, sailor.stress - 5)
                self._update_critical_condition(world, sailor)
                self.record("medical_treatment", sailor, self.job(sailor.job_id),
                            "Used one medicine dose; treatment begun in the designated infirmary")
            sailor.activity = "Receiving infirmary care"
            sailor.action_seconds += 1
            sailor.health = min(100, sailor.health + 0.12)
            sailor.stress = max(0, sailor.stress - 0.04)
            recovering = [row for row in sailor.injuries if row["recovery"] < 100]
            for injury in recovering:
                injury["recovery"] = min(100.0, injury["recovery"] + 0.25 / injury["severity"])
                if injury["recovery"] >= 100:
                    sailor.think(f"Recovered from {injury['kind'].replace('_', ' ')}.", 2)
            if sailor.health >= 95 and not any(row["recovery"] < 100 for row in sailor.injuries):
                sailor.think("Recovered with care in the infirmary.", 3)
                self.record("need_satisfied", sailor, self.job(sailor.job_id), "infirmary care")
                sailor.intent = ""
                sailor.medical_visit_treated = False
            return
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
                           "drink": "Drinking clean water", "social": "Waiting for company",
                           "relax": "Relaxing on deck", "rest": "Resting (not sleeping)", "lookout": "Watching the sea"}[intent]
        sailor.action_seconds += 1
        if intent in ("drink", "eat"):
            emergency = intent == "drink" and sailor.thirst >= 85
            if intent == "drink":
                available_ml = self.clean_water_total_ml()
                if not emergency:
                    available_ml = max(0, available_ml - self.galley_water_reserve_ml())
                available = available_ml > 0
            else:
                available = self.edible_food_g(emergency=sailor.hunger >= 85) >= FOOD_SERVING_G
            if not available:
                shortage = ("Clean water is being held for the drinking reserve."
                            if intent == "drink" and self.stores["clean_water_ml"] > 0
                            else "The clean-water tank is empty." if intent == "drink"
                            else "No ready-to-eat food is left.")
                sailor.think(shortage, -2)
                sailor.intent = ""
                sailor.shared_meal_slot = -1
                return
            if sailor.action_seconds >= 6:
                if intent == "eat" and sailor.shared_meal_slot >= 0:
                    mess = self.stations["mess"]
                    partners = [row for row in self.crew if row is not sailor
                                and row.shared_meal_slot == sailor.shared_meal_slot and row.intent == "eat"
                                and abs(row.x - mess[0]) + abs(row.y - mess[1]) <= 2]
                    if not partners and sailor.action_seconds < 120:
                        sailor.activity = "Waiting at mess for staggered watch group"
                        return
                if intent == "drink":
                    consumed_ml = min(DRINK_SERVING_ML, self.clean_water_total_ml())
                    before_ml = self.clean_water_total_ml()
                    if consumed_ml and sailor.thirst >= 85 and before_ml - consumed_ml < self.galley_water_reserve_ml():
                        self.record("drinking_reserve_override", sailor, self.job(sailor.job_id),
                                    f"Critical thirst used {consumed_ml} mL from protected water.")
                    if not self.consume_clean_water(sailor, consumed_ml):
                        raise RuntimeError("Clean-water stock changed during consumption")
                    sailor.thirst = max(0, sailor.thirst - THIRST_RELIEF_PER_LITER * consumed_ml / 1000)
                    self.record_consumption(world, sailor, water_ml=consumed_ml)
                    sailor.think("Fresh water, at last.", 3)
                    self.record("need_satisfied", sailor, self.job(sailor.job_id), intent)
                else:
                    consumed_foods = self.consume_food_details(
                        FOOD_SERVING_G, emergency=sailor.hunger >= 85,
                        preferred_food_id=sailor.food_preference if sailor.food_preference != "variety" else None,
                        recent_food_ids=sailor.recent_meals)
                    sailor.hunger = max(0, sailor.hunger - 75)
                    self.record_consumption(world, sailor, food_g=FOOD_SERVING_G)
                    self.apply_meal_preference(sailor, consumed_foods)
                    meal_name = self.food_name(max(consumed_foods, key=lambda item: item[1])[0])
                    self.record("need_satisfied", sailor, self.job(sailor.job_id), f"ate {meal_name}")
                    self.record_shared_meal_attendance(sailor, world)
                sailor.stress = max(0, sailor.stress - (3 if intent == "drink" else 5))
                sailor.intent = ""
                sailor.shared_meal_slot = -1
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
                    day = world.clock.day
                    if self.relationship_story_day != day:
                        self.relationship_story_day = day
                        self.relationship_story_gains = {}
                    pair_key = "::".join(sorted((sailor.id, partner.id)))
                    gains_today = self.relationship_story_gains.get(pair_key, 0)
                    score_applied = (gains_today < MAX_STORY_RELATIONSHIP_GAINS_PER_PAIR_PER_DAY
                                     and sailor.relationships.get(partner.id, 0) < 100
                                     and partner.relationships.get(sailor.id, 0) < 100)
                    if score_applied:
                        sailor.relationships[partner.id] = min(100, sailor.relationships.get(partner.id, 0) + 1)
                        partner.relationships[sailor.id] = min(100, partner.relationships.get(sailor.id, 0) + 1)
                        self.relationship_story_gains[pair_key] = gains_today + 1
                    self.record_social_event(
                        "shared_story", (sailor, partner),
                        ("Completed a nearby 15-second story together; existing relationship rose by one."
                         if score_applied else
                         "Completed a nearby 15-second story together; daily relationship-gain ceiling reached."),
                        relationship_delta=1 if score_applied else 0)
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
        self._world = world
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
        if self.relationship_story_day != world.clock.day:
            self.relationship_story_day = world.clock.day
            self.relationship_story_gains = {}
        economy = getattr(world, "economy", None)
        if economy is not None:
            economy.employment.settle_day(world, world.clock.day)
        self.spoil_fresh_food()
        self.spoil_cooked_food()
        if self.seconds % 60 == 0:
            self.collect_rain_minute(world)
        if self.light_on:
            if self.light_type == "lantern":
                self.light_burn_remainder += 1
            if self.light_type == "lantern" and self.light_burn_remainder >= 10:
                self.light_burn_remainder -= 10
                self.light_fuel_ml = max(0, self.light_fuel_ml - 1)
                if self.light_fuel_ml == 0:
                    self.light_on = False
                    self.record("light_out", reason="Lantern exhausted its measured oil fuel.")
            elif self.light_type == "candle":
                self.candle_fuel_seconds = max(0, self.candle_fuel_seconds - 1)
                if self.candle_fuel_seconds == 0:
                    self.light_on = False
                    self.record("light_out", reason="Candle burned down.")
        # Wear and filth are slow; existing leaks cause actual bilge ingress.
        self.bilge = min(100, self.bilge + max(0, 100 - self.hull) * 0.0004)
        if hasattr(world, "hazards"):
            world.hazards.advance(world)
        self.rigging = max(0, self.rigging - 0.001)
        for key in self.dirt:
            self.dirt[key] = min(100, self.dirt[key] + 0.003)
        self.generate_jobs(world)
        ordered = (self.crew[self.seconds % len(self.crew):] + self.crew[:self.seconds % len(self.crew)]
                   if self.crew else [])
        for sailor in ordered:
            self._update_age_and_retirement(world, sailor)
            if self.sailor(sailor.id) is not sailor:
                continue
            for memory in sailor.memories:
                memory["remaining"] -= 1
            sailor.memories = [m for m in sailor.memories if m["remaining"] > 0]
            for key, rate in (("hunger", HUNGER_INCREASE_PER_SECOND * (1.35 if "hearty_appetite" in sailor.traits else 1)),
                              ("thirst", THIRST_INCREASE_PER_SECOND), ("fatigue", 0.004), ("sleep_debt", 0.006),
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
            elif sailor.health > 0:
                sailor.health = min(100, sailor.health + 0.01)
            self._update_critical_condition(world, sailor)
            if self.sailor(sailor.id) is not sailor:
                continue
            self._update_grievance(world, sailor)
            if self.sailor(sailor.id) is not sailor:
                continue
            if self.seconds % 60 == 0:
                self.update_wetness(sailor, world)
            if self.seconds % 3600 == 0:
                self.wear_wet_garments(sailor, getattr(world, "economy", None))
                sailor.cleanliness = max(0, sailor.cleanliness - 0.05)
            job = self.job(sailor.job_id)
            scheduled_meal = self.request_shared_meal(world, sailor, job)
            intent = self.choose_need(sailor, world.clock.hour_minute[0], world)
            if intent is None and scheduled_meal:
                intent = "eat"
            elif intent not in (None, "eat"):
                sailor.shared_meal_slot = -1
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
                if (sailor.morale < 20 and job.priority > 1 and job.kind != "cook"
                        and not (job.assigned_to == sailor.id and "obedient" in sailor.traits)):
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
        from pirates.ship_support import advance as advance_support
        advance_support(world)
        if hasattr(world, "on_ship_second"):
            world.on_ship_second()

    def snapshot(self):
        # Keep lots reconciled when tools/tests have edited the canonical totals
        # directly; live market allocation and eating update them transactionally.
        self.sync_fresh_food_lots()
        return {"seconds": self.seconds, "remainder": self.remainder, "next_id": self.next_id,
                "stores": dict(self.stores), "prepared_food_g": self.prepared_food_g,
                "cooked_food_batches": [dict(batch) for batch in self.cooked_food_batches],
                "next_food_batch_id": self.next_food_batch_id,
                "fresh_food_lots": [dict(lot) for lot in self.fresh_food_lots],
                "next_fresh_food_lot_id": self.next_fresh_food_lot_id,
                "containers": [dict(container) for container in self.containers],
                "next_container_id": self.next_container_id,
                "rain_day": self.rain_day, "rain_collected_today_ml": self.rain_collected_today_ml,
                "lighting": {"type": self.light_type, "on": self.light_on,
                             "fuel_ml": self.light_fuel_ml, "candle_seconds": self.candle_fuel_seconds,
                             "remainder": self.light_burn_remainder},
                "meal_windows": {"config": self.meal_window_config,
                                  "social_slots": self.shared_meal_social_slots},
                "social_events": [dict(row, participants=list(row["participants"]))
                                  for row in self.social_events],
                "next_social_event_id": self.next_social_event_id,
                "relationship_story_day": self.relationship_story_day,
                "relationship_story_gains": dict(self.relationship_story_gains),
                "crew_concerns": [dict(row, signals=list(row["signals"])) for row in self.crew_concerns],
                "next_crew_concern_id": self.next_crew_concern_id,
                "consumption_day": self.consumption_day,
                "food_consumed_today_g": self.food_consumed_today_g,
                "water_consumed_today_ml": self.water_consumed_today_ml,
                "galley_policy": dict(self.galley_policy), "hull": self.hull,
                "bilge": self.bilge, "rigging": self.rigging, "dirt": dict(self.dirt),
                "ammo": dict(self.ammo), "cooldowns": dict(self.cooldowns),
                "crew": [asdict(c) for c in self.crew], "jobs": [asdict(j) for j in self.jobs],
                "crew_history": [dict(row) for row in self.crew_history],
                "events": list(self.events), "audit": list(self.audit), "stockpiles": self.storage.snapshot(),
                "captives": [dict(row) for row in self.captives]}

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
        old_resources = {"raw_food", "water", "planks", "shot"}
        old_resources_with_medicine = old_resources | {"medicine"}
        prior_canonical = {"grain_g", "beans_g", "dried_fish_g", "fruit_g", "biscuit_g",
                           "clean_water_ml", "planks", "shot", "medicine"}
        prior_canonical_without_medicine = prior_canonical - {"medicine"}
        previous_resources = set(RESOURCES) - {"salt_g", "salted_fruit_g", "bandages", "soap",
                                               "lanterns", "lamp_oil_ml", "candles"}
        if set(data["stores"]) not in (set(RESOURCES), old_resources, old_resources_with_medicine,
                                         prior_canonical, prior_canonical_without_medicine,
                                         previous_resources):
            raise ValueError("Invalid hold stores")
        saved_stores = migrate_store_mapping(data["stores"])
        if "stockpiles" in data:
            ship.storage = SpatialStorage.restore(data["stockpiles"], world.ship_model, world.fixtures)
            if dict(ship.stores) != saved_stores:
                raise ValueError("Stockpile contents disagree with available supply summary")
        else:
            # Migrate old aggregate counts into piles attached to current fixture positions.
            ship.storage = SpatialStorage.starter(ship.stations)
            for resource, amount in saved_stores.items():
                ship.stores[resource] = amount
        if "cooked_food_batches" in data:
            batches = data["cooked_food_batches"]
            if not isinstance(batches, list) or len(batches) > 10000:
                raise ValueError("Invalid cooked food batches")
            valid_food_ids = {recipe["output_food_id"] for recipe in FOOD_RECIPES.values()} | {"legacy_cooked_food"}
            restored_batches = []
            batch_ids = set()
            for batch in batches:
                if (not isinstance(batch, dict)
                        or set(batch) != {"id", "food_id", "recipe_id", "grams", "made_at_seconds"}
                        or type(batch["id"]) is not int or batch["id"] < 1 or batch["id"] in batch_ids
                        or batch["food_id"] not in valid_food_ids
                        or (batch["recipe_id"] is not None and batch["recipe_id"] not in FOOD_RECIPES)
                        or type(batch["grams"]) is not int or batch["grams"] < 1
                        or type(batch["made_at_seconds"]) is not int
                        or not 0 <= batch["made_at_seconds"] <= ship.seconds):
                    raise ValueError("Invalid cooked food batch")
                if batch["recipe_id"] is not None and FOOD_RECIPES[batch["recipe_id"]]["output_food_id"] != batch["food_id"]:
                    raise ValueError("Cooked batch recipe/output mismatch")
                batch_ids.add(batch["id"])
                restored_batches.append(dict(batch))
            ship.cooked_food_batches = restored_batches
            ship.next_food_batch_id = integer(data.get("next_food_batch_id", max(batch_ids, default=0) + 1), 1)
            if ship.next_food_batch_id <= max(batch_ids, default=0):
                raise ValueError("Invalid next food batch ID")
            if "prepared_food_g" in data and integer(data["prepared_food_g"]) != ship.prepared_food_g:
                raise ValueError("Prepared-food summary disagrees with cooked batches")
        else:
            # Prior gram-count snapshots had no recipe identity; preserve rather
            # than guessing which dish produced their already-cooked food.
            legacy_grams = integer(data.get("prepared_food_g", data.get("meals", 0) * FOOD_SERVING_G))
            ship.cooked_food_batches = ([{"id": 1, "food_id": "legacy_cooked_food", "recipe_id": None,
                                          "grams": legacy_grams, "made_at_seconds": ship.seconds}]
                                        if legacy_grams else [])
            ship.next_food_batch_id = 2
        lots = data.get("fresh_food_lots")
        if lots is None:
            legacy_fruit = ship.stores["fruit_g"]
            ship.fresh_food_lots = ([{"id": 1, "resource": "fruit_g", "grams": legacy_fruit,
                                      "received_at_seconds": ship.seconds}] if legacy_fruit else [])
            ship.next_fresh_food_lot_id = 2
        else:
            if not isinstance(lots, list) or len(lots) > 10000:
                raise ValueError("Invalid fresh-food lots")
            ship.fresh_food_lots = []
            lot_ids = set()
            for lot in lots:
                if (not isinstance(lot, dict)
                        or set(lot) != {"id", "resource", "grams", "received_at_seconds"}
                        or type(lot["id"]) is not int or lot["id"] < 1 or lot["id"] in lot_ids
                        or lot["resource"] != "fruit_g"
                        or type(lot["grams"]) is not int or lot["grams"] < 1
                        or type(lot["received_at_seconds"]) is not int
                        or not 0 <= lot["received_at_seconds"] <= ship.seconds):
                    raise ValueError("Invalid fresh-food lot identity/age")
                lot_ids.add(lot["id"])
                ship.fresh_food_lots.append(dict(lot))
            ship.next_fresh_food_lot_id = integer(
                data.get("next_fresh_food_lot_id", max(lot_ids, default=0) + 1), 1)
            if ship.next_fresh_food_lot_id <= max(lot_ids, default=0):
                raise ValueError("Invalid next fresh-food lot ID")
            if sum(lot["grams"] for lot in ship.fresh_food_lots) != ship.stores["fruit_g"]:
                raise ValueError("Fresh-food lot totals disagree with fruit stock")
        ship.consumption_day = integer(data.get("consumption_day", world.clock.day), 1, world.clock.day)
        ship.food_consumed_today_g = integer(data.get("food_consumed_today_g", 0), 0, 10**9)
        ship.water_consumed_today_ml = integer(data.get("water_consumed_today_ml", 0), 0, 10**9)
        from pirates.economy import CATALOG
        containers = data.get("containers", [])
        if not isinstance(containers, list) or len(containers) > 500:
            raise ValueError("Invalid ship containers")
        ship.containers = []
        container_ids = set()
        for vessel in containers:
            if (not isinstance(vessel, dict)
                    or set(vessel) != {"id", "item_id", "capacity_ml", "tare_g", "contents_ml", "quality", "owner"}):
                raise ValueError("Invalid ship container")
            if not isinstance(vessel["item_id"], str):
                raise ValueError("Invalid ship container item ID")
            item = CATALOG.get(vessel["item_id"], {})
            canonical = item.get("container", {})
            if (type(vessel["id"]) is not int or vessel["id"] < 1 or vessel["id"] in container_ids
                    or not canonical or canonical.get("liquid") != "clean_water_ml"
                    or type(vessel["capacity_ml"]) is not int
                    or vessel["capacity_ml"] != canonical.get("capacity_ml")
                    or type(vessel["tare_g"]) is not int or vessel["tare_g"] != canonical.get("tare_g")
                    or type(vessel["contents_ml"]) is not int
                    or not 0 <= vessel["contents_ml"] <= vessel["capacity_ml"]
                    or vessel["quality"] not in (None, "clean")
                    or (vessel["contents_ml"] == 0) != (vessel["quality"] is None)
                    or (vessel["owner"] is not None and (not isinstance(vessel["owner"], str)
                        or vessel["owner"] not in {row.get("id") for row in data.get("crew", [])
                                                   if isinstance(row, dict)}))):
                raise ValueError("Invalid ship container contents/identity")
            container_ids.add(vessel["id"])
            ship.containers.append(dict(vessel))
        ship.next_container_id = integer(data.get("next_container_id", max(container_ids, default=0) + 1), 1)
        if ship.next_container_id <= max(container_ids, default=0):
            raise ValueError("Invalid next container ID")
        ship.rain_day = integer(data.get("rain_day", world.clock.day), 1, world.clock.day)
        ship.rain_collected_today_ml = integer(data.get("rain_collected_today_ml", 0), 0, 5000)
        lighting = data.get("lighting", {"type": None, "on": False, "fuel_ml": 0,
                                          "candle_seconds": 0, "remainder": 0})
        if (not isinstance(lighting, dict)
                or set(lighting) != {"type", "on", "fuel_ml", "candle_seconds", "remainder"}
                or lighting["type"] not in (None, "lantern", "candle")
                or type(lighting["on"]) is not bool
                or type(lighting["fuel_ml"]) is not int or not 0 <= lighting["fuel_ml"] <= 250
                or type(lighting["candle_seconds"]) is not int or not 0 <= lighting["candle_seconds"] <= 3600
                or type(lighting["remainder"]) is not int or not 0 <= lighting["remainder"] <= 9
                or (lighting["on"] and lighting["type"] is None)
                or (lighting["type"] is None and (lighting["fuel_ml"] or lighting["candle_seconds"]
                                                   or lighting["on"]))
                or (lighting["on"] and lighting["type"] == "lantern" and lighting["fuel_ml"] < 1)
                or (lighting["on"] and lighting["type"] == "candle" and lighting["candle_seconds"] < 1)):
            raise ValueError("Invalid persisted ship lighting/fuel state")
        ship.light_type, ship.light_on = lighting["type"], lighting["on"]
        ship.light_fuel_ml, ship.candle_fuel_seconds = lighting["fuel_ml"], lighting["candle_seconds"]
        ship.light_burn_remainder = lighting["remainder"]
        meal_windows = data.get("meal_windows", {"config": ship.meal_window_config, "social_slots": []})
        if (not isinstance(meal_windows, dict) or set(meal_windows) != {"config", "social_slots"}
                or not isinstance(meal_windows["config"], dict)
                or set(meal_windows["config"]) != {"hours", "group_size", "stagger_seconds", "grace_seconds"}
                or not isinstance(meal_windows["config"]["hours"], list)
                or not 1 <= len(meal_windows["config"]["hours"]) <= 8
                or any(type(hour) is not int or not 0 <= hour <= 23 for hour in meal_windows["config"]["hours"])
                or meal_windows["config"]["hours"] != sorted(set(meal_windows["config"]["hours"]))
                or meal_windows["config"]["group_size"] != 2
                or type(meal_windows["config"]["stagger_seconds"]) is not int
                or not 60 <= meal_windows["config"]["stagger_seconds"] <= 3600
                or type(meal_windows["config"]["grace_seconds"]) is not int
                or not 60 <= meal_windows["config"]["grace_seconds"] <= 3600
                or not isinstance(meal_windows["social_slots"], list)
                or len(meal_windows["social_slots"]) > 64
                or any(type(slot) is not int or slot < 0 for slot in meal_windows["social_slots"])
                or len(set(meal_windows["social_slots"])) != len(meal_windows["social_slots"])):
            raise ValueError("Invalid saved shared-meal schedule")
        ship.meal_window_config = deepcopy(meal_windows["config"])
        ship.shared_meal_social_slots = list(meal_windows["social_slots"])
        policy = data.get("galley_policy", ship.galley_policy)
        if (not isinstance(policy, dict)
                or set(policy) != {"mode", "preferred_recipe", "ready_food_target_g",
                                   "water_reserve_ml", "emergency_biscuit_reserve_g"}
                or policy["mode"] not in ("automatic", "preferred", "maintain", "paused")
                or (policy["preferred_recipe"] is not None and policy["preferred_recipe"] not in FOOD_RECIPES)):
            raise ValueError("Invalid galley policy")
        ship.set_galley_policy(**policy)
        for key in ("hull", "bilge", "rigging"):
            setattr(ship, key, number(data[key]))
        if set(data["dirt"]) != set(ship.dirt) or set(data["ammo"]) != set(ship.ammo):
            raise ValueError("Invalid ship sites")
        ship.dirt = {k: number(v) for k, v in data["dirt"].items()}
        ship.ammo = {k: integer(v, 0, 1) for k, v in data["ammo"].items()}
        captives = data.get("captives", [])
        if (not isinstance(captives, list) or len(captives) > 100
                or any(not isinstance(row, dict) or set(row) != {"id", "name", "faction"}
                       or any(not isinstance(row[k], str) or not row[k] for k in ("id", "name", "faction"))
                       for row in captives)
                or len({row["id"] for row in captives}) != len(captives)):
            raise ValueError("Invalid captive registry")
        ship.captives = [dict(row) for row in captives]
        ship.cooldowns = {str(k): integer(v) for k, v in data["cooldowns"].items()}
        ship.events = strings(data["events"], 30)
        history = data.get("crew_history", [])
        if (not isinstance(history, list) or len(history) > 32
                or any(not isinstance(row, dict) or set(row) != {"id", "name", "status", "day", "cause"}
                       or not isinstance(row["id"], str) or not isinstance(row["name"], str)
                       or not row["name"] or row["status"] not in ("released", "retired", "deserted", "betrayed", "dead")
                       or type(row["day"]) is not int or not 1 <= row["day"] <= world.clock.day
                       or not isinstance(row["cause"], str) or not row["cause"]
                       for row in history)
                or len({row["id"] for row in history}) != len(history)):
            raise ValueError("Invalid former-crew ledger")
        historical_ids = {row["id"] for row in history}
        ship.crew_history = [dict(row) for row in history]
        social_events = data.get("social_events", [])
        if not isinstance(social_events, list) or len(social_events) > 64:
            raise ValueError("Invalid ship social-event history")
        known_social_ids = {row.get("id") for row in data["crew"] if isinstance(row, dict)} | historical_ids
        seen_social_ids = set()
        ship.social_events = []
        for row in social_events:
            if (not isinstance(row, dict)
                    or set(row) != {"id", "second", "kind", "participants", "relationship_delta", "reason"}
                    or type(row["id"]) is not int or row["id"] < 1 or row["id"] in seen_social_ids
                    or type(row["second"]) is not int or not 0 <= row["second"] <= ship.seconds
                    or row["kind"] not in ("shared_meal", "shared_story")
                    or not isinstance(row["participants"], list) or len(row["participants"]) != 2
                    or any(not isinstance(sid, str) or sid not in known_social_ids
                           for sid in row["participants"])
                    or row["participants"][0] == row["participants"][1]
                    or type(row["relationship_delta"]) is not int
                    or row["relationship_delta"] not in ((0, 1) if row["kind"] == "shared_story" else (0,))
                    or not isinstance(row["reason"], str) or not row["reason"] or len(row["reason"]) > 300):
                raise ValueError("Invalid ship social-event record")
            seen_social_ids.add(row["id"])
            ship.social_events.append({**row, "participants": list(row["participants"])})
        ship.next_social_event_id = integer(data.get("next_social_event_id", max(seen_social_ids, default=0) + 1), 1)
        if ship.next_social_event_id <= max(seen_social_ids, default=0):
            raise ValueError("Invalid next ship social-event ID")
        ship.relationship_story_day = integer(data.get("relationship_story_day", world.clock.day), 1)
        story_gains = data.get("relationship_story_gains", {})
        if not isinstance(story_gains, dict) or len(story_gains) > 2016:
            raise ValueError("Invalid daily relationship-story gains")
        ship.relationship_story_gains = {}
        for pair_key, count in story_gains.items():
            pair = pair_key.split("::") if isinstance(pair_key, str) else []
            if (len(pair) != 2 or pair[0] == pair[1] or pair != sorted(pair)
                    or any(sid not in known_social_ids for sid in pair)
                    or type(count) is not int or not 1 <= count <= MAX_STORY_RELATIONSHIP_GAINS_PER_PAIR_PER_DAY):
                raise ValueError("Invalid daily relationship-story gain record")
            ship.relationship_story_gains[pair_key] = count
        if (ship.relationship_story_gains and ship.relationship_story_day != world.clock.day
                or ship.relationship_story_day > world.clock.day):
            raise ValueError("Daily relationship-story gains are out of date")
        concerns = data.get("crew_concerns", [])
        if not isinstance(concerns, list) or len(concerns) > 64:
            raise ValueError("Invalid crew concern history")
        active_ids = {row.get("id") for row in data["crew"] if isinstance(row, dict)}
        known_ids = active_ids | historical_ids
        seen_concern_ids, open_concern_crews = set(), set()
        ship.crew_concerns = []
        for row in concerns:
            if (not isinstance(row, dict)
                    or set(row) != {"id", "crew", "opened_second", "status", "signals", "resolved_second"}
                    or type(row["id"]) is not int or row["id"] < 1 or row["id"] in seen_concern_ids
                    or not isinstance(row["crew"], str) or row["crew"] not in known_ids
                    or type(row["opened_second"]) is not int
                    or not 0 <= row["opened_second"] <= ship.seconds
                    or row["status"] not in ("open", "resolved", "archived")
                    or not isinstance(row["signals"], list) or not 1 <= len(row["signals"]) <= 4
                    or any(signal not in ("stress", "hunger", "thirst", "injury") for signal in row["signals"])
                    or len(set(row["signals"])) != len(row["signals"])):
                raise ValueError("Invalid crew concern record")
            resolved_second = row["resolved_second"]
            if (row["status"] == "open" and (resolved_second is not None or row["crew"] not in active_ids
                                               or row["crew"] in open_concern_crews)):
                raise ValueError("Invalid open crew concern lifecycle")
            if (row["status"] == "archived" and row["crew"] not in historical_ids):
                raise ValueError("Invalid archived crew concern")
            if row["status"] != "open" and (type(resolved_second) is not int
                                              or not row["opened_second"] <= resolved_second <= ship.seconds):
                raise ValueError("Invalid closed crew concern time")
            seen_concern_ids.add(row["id"])
            if row["status"] == "open":
                open_concern_crews.add(row["crew"])
            ship.crew_concerns.append({**row, "signals": list(row["signals"])})
        ship.next_crew_concern_id = integer(
            data.get("next_crew_concern_id", max(seen_concern_ids, default=0) + 1), 1)
        if ship.next_crew_concern_id <= max(seen_concern_ids, default=0):
            raise ValueError("Invalid next crew concern ID")
        ship.audit = data.get("audit", [])
        if not isinstance(ship.audit, list) or len(ship.audit) > 100:
            raise ValueError("Invalid work audit")
        for entry in ship.audit:
            if not isinstance(entry, dict) or set(entry) != {"second", "event", "crew", "job", "reason"}:
                raise ValueError("Invalid audit record")
            integer(entry["second"], 0, ship.seconds)
            if not isinstance(entry["event"], str) or not isinstance(entry["reason"], str):
                raise ValueError("Invalid audit text")
            if (entry["crew"] is not None and not ship.sailor(entry["crew"])
                    and entry["crew"] not in historical_ids):
                raise ValueError("Invalid audit sailor")
            if entry["job"] is not None:
                integer(entry["job"], 1, ship.next_id - 1)
        if not isinstance(data["crew"], list) or len(data["crew"]) > 32:
            raise ValueError("Invalid crew roster")
        identities = {c.id: c for c in ship.crew}
        saved_crew_ids = {row.get("id") for row in data["crew"] if isinstance(row, dict)}
        if saved_crew_ids & historical_ids:
            raise ValueError("Crew member is both aboard and in former-crew ledger")
        if not set(identities) <= saved_crew_ids | historical_ids:
            raise ValueError("Crew identity is missing from both active and former roster")
        from pirates.crew_recruitment import CANDIDATES
        for row in history:
            if row["id"] in identities:
                canonical_name = identities[row["id"]].name
            elif row["id"].startswith("hire:") and row["id"][5:] in CANDIDATES:
                canonical_name = CANDIDATES[row["id"][5:]]["name"]
            else:
                raise ValueError("Unknown former-crew identity")
            if row["name"] != canonical_name:
                raise ValueError("Former-crew identity/name mismatch")
        crew = []
        assigned_bunks = set()
        occupied = {(world.player.x, world.player.y, world.player.z)}
        for row in data["crew"]:
            row = dict(row)
            row["cargo"] = migrate_bundle(row.get("cargo", {}))
            if row["id"] in identities:
                canonical = identities.pop(row["id"])
            elif isinstance(row["id"], str) and row["id"].startswith("hire:"):
                from pirates.crew_recruitment import sailor_from_candidate
                canonical = sailor_from_candidate(row["id"][5:], row.get("x"), row.get("y"),
                                                   row.get("z", 0), row.get("bunk"))
            else:
                raise ValueError("Unknown crew identity")
            # Pre-aging v3 saves have a current age but no reference epoch. Rebase
            # at load rather than aging the sailor retroactively from world day 1.
            if "age_origin" not in row or "age_origin_day" not in row:
                row.setdefault("age_origin", row.get("age", canonical.age))
                row["age_origin_day"] = world.clock.day
            sailor = Sailor(**(asdict(canonical) | row))
            # Age is progression state; authored identity is the person's name/profile, not their current age.
            identity_fields = ("name", "glyph", "trait", "preference", "bunk", "background")
            if any(getattr(sailor, k) != getattr(canonical, k) for k in identity_fields):
                raise ValueError("Invalid sailor identity")
            sailor.color = tuple(canonical.color)
            if (sailor.personality != canonical.personality or sailor.traits != canonical.traits
                    or sailor.learning_potential != canonical.learning_potential
                    or sailor.mental != canonical.mental):
                raise ValueError("Invalid personality / traits")
            if (not isinstance(sailor.food_preference, str)
                    or sailor.food_preference not in FOOD_IDS | {"variety"}
                    or not isinstance(sailor.recent_meals, list) or len(sailor.recent_meals) > 4
                    or any(not isinstance(food_id, str) or food_id not in FOOD_IDS
                           for food_id in sailor.recent_meals)):
                raise ValueError("Invalid sailor food preference/history")
            if (type(sailor.bunk) is not int or not 0 <= sailor.bunk < len(ship.bunks)
                    or sailor.bunk in assigned_bunks):
                raise ValueError("Invalid or duplicate assigned bunk")
            assigned_bunks.add(sailor.bunk)
            integer(sailor.x, 0, 99)
            integer(sailor.y, 0, 63)
            integer(sailor.z, 0, 8)
            local = (sailor.x - 69, sailor.y - 12, sailor.z)
            parts = world.ship_model.at(*local)
            transfer_visit = getattr(world, "_restore_deck_visit", None)
            from pirates.sea_transfer import in_deck
            valid_away_position = (
                isinstance(transfer_visit, dict)
                and isinstance(transfer_visit.get("participants"), list)
                and sailor.id in transfer_visit["participants"]
                and sailor.z == 0 and in_deck(sailor.x, sailor.y))
            valid_home_position = (
                any(c.kind == "floor" for c in parts)
                and not any(c.kind == "wall" for c in parts)
                and (sailor.z != world.active_deck
                     or world.game_map.is_walkable(sailor.x, sailor.y)))
            if (not (valid_away_position or valid_home_position)
                    or (sailor.x, sailor.y, sailor.z) in occupied):
                raise ValueError("Invalid / overlapping crew position")
            occupied.add((sailor.x, sailor.y, sailor.z))
            for key in ("hunger", "thirst", "fatigue", "loneliness", "boredom", "morale", "health", "energy", "sleep_debt", "stress", "wetness", "cleanliness"):
                number(getattr(sailor, key))
            integer(sailor.last_shared_meal_slot, -1)
            integer(sailor.shared_meal_slot, -1)
            if type(sailor.medical_visit_treated) is not bool:
                raise ValueError("Invalid medical treatment state")
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
            if (not isinstance(sailor.mental, dict)
                    or set(sailor.mental) != {"intelligence", "discipline", "leadership", "tactical_awareness"}):
                raise ValueError("Invalid mental attributes")
            for value in sailor.mental.values():
                number(value, 0, 100)
            for v in sailor.labors.values():
                integer(v, 0, 5)
            for v in sailor.skills.values():
                number(v, 0, 10)
            sailor.cargo = materials(sailor.cargo)
            integer(sailor.haul_wait, 0, 100)
            strings(sailor.thoughts, 8)
            if sailor.intent not in ("", "eat", "drink", "rest", "sleep", "social", "relax", "lookout", "infirmary"):
                raise ValueError("Invalid personal intent")
            if not isinstance(sailor.activity, str):
                raise ValueError("Invalid crew activity")
            integer(sailor.action_seconds)
            integer(sailor.completed)
            integer(sailor.food_consumed_today_g, 0, 10**8)
            integer(sailor.water_consumed_today_ml, 0, 10**8)
            integer(sailor.consumption_day, 1, world.clock.day)
            integer(sailor.age, 14, 100)
            if not isinstance(sailor.background, str) or not sailor.background:
                raise ValueError("Invalid sailor background")
            number(sailor.learning_potential)
            number(sailor.grievance)
            if sailor.desertion_warning_hour is not None:
                integer(sailor.desertion_warning_hour, 0, world.clock.total_ticks // 360000)
                if sailor.grievance < 70:
                    raise ValueError("Desertion warning lacks warning-level grievance")
            if sailor.betrayal_warning_tick is not None:
                integer(sailor.betrayal_warning_tick, 0, world.clock.total_ticks)
                if sailor.grievance < 70:
                    raise ValueError("Betrayal warning lacks warning-level grievance")
            if sailor.retention_offer_day is not None:
                integer(sailor.retention_offer_day, 1, world.clock.day)
            if sailor.critical_since_tick is not None:
                integer(sailor.critical_since_tick, 0, world.clock.total_ticks)
                if sailor.health > 0 or not sailor.critical_cause:
                    raise ValueError("Invalid critical crew state")
            elif sailor.critical_cause:
                raise ValueError("Invalid critical crew cause")
            if not isinstance(sailor.critical_cause, str) or len(sailor.critical_cause) > 240:
                raise ValueError("Invalid critical crew cause")
            if sailor.age_origin is not None:
                integer(sailor.age_origin, 0, 100)
            integer(sailor.age_origin_day, 1, world.clock.day)
            if sailor.retirement_notice_day is not None:
                integer(sailor.retirement_notice_day, sailor.age_origin_day, world.clock.day)
                if sailor.age < 65:
                    raise ValueError("Retirement notice requires an elderly sailor")
            integer(sailor.injury_serial)
            if not isinstance(sailor.injuries, list) or len(sailor.injuries) > 8:
                raise ValueError("Invalid crew injury history")
            injury_ids = set()
            for injury in sailor.injuries:
                if (not isinstance(injury, dict)
                        or set(injury) != {"id", "kind", "severity", "cause", "recovery"}
                        or not isinstance(injury["id"], str) or injury["id"] in injury_ids
                        or not injury["id"].startswith(f"{sailor.id}-injury-")
                        or injury["kind"] not in ("burn", "sprain", "fracture")
                        or not isinstance(injury["cause"], str) or not injury["cause"]):
                    raise ValueError("Invalid crew injury record")
                injury_ids.add(injury["id"])
                integer(injury["severity"], 1, 3)
                number(injury["recovery"])
                try:
                    serial = int(injury["id"].rsplit("-", 1)[1])
                except (ValueError, IndexError) as error:
                    raise ValueError("Invalid crew injury identity") from error
                if serial <= 0 or serial > sailor.injury_serial:
                    raise ValueError("Invalid crew injury sequence")
            if sailor.job_id is not None:
                integer(sailor.job_id, 1)
            if any(k not in saved_crew_ids or k == sailor.id for k in sailor.relationships):
                raise ValueError("Invalid relationship")
            for v in sailor.relationships.values():
                integer(v, 0, 100)
            crew.append(sailor)
        ship.crew = crew
        if not isinstance(data["jobs"], list) or len(data["jobs"]) > 100:
            raise ValueError("Invalid job board")
        jobs, ids, active_keys = [], set(), set()
        for row in data["jobs"]:
            row = dict(row)
            row["reserved"] = migrate_bundle(row.get("reserved", {}))
            if row.get("key") == "cook":
                row["key"] = "cook:grain_porridge"
            if isinstance(row.get("supply_plan"), list):
                row["supply_plan"] = [dict(allocation, bundle=migrate_bundle(allocation["bundle"]))
                                      for allocation in row["supply_plan"]]
            if isinstance(row.get("transfer"), dict) and row["transfer"].get("bundle") is not None:
                row["transfer"] = dict(row["transfer"], bundle=migrate_bundle(row["transfer"]["bundle"]))
            job = Job(**row)
            if type(job.reserve_override) is not bool:
                raise ValueError("Invalid galley reserve override")
            if not isinstance(job.fresh_food_lots, list) or len(job.fresh_food_lots) > 100:
                raise ValueError("Invalid job fresh-food lot reservations")
            for lot in job.fresh_food_lots:
                if (not isinstance(lot, dict)
                        or set(lot) != {"id", "resource", "grams", "received_at_seconds"}
                        or type(lot["id"]) is not int or lot["id"] < 1
                        or lot["resource"] != "fruit_g"
                        or type(lot["grams"]) is not int or lot["grams"] < 1
                        or type(lot["received_at_seconds"]) is not int
                        or not 0 <= lot["received_at_seconds"] <= ship.seconds):
                    raise ValueError("Invalid job fresh-food lot record")
            if job.fresh_food_lots:
                preservation = FOOD_PRESERVATION.get(job.key.removeprefix("preserve:"))
                expected_lots = preservation["input"].get("fruit_g", 0) if preservation else 0
                if (job.kind != "cook" or job.state in ("done", "cancelled")
                        or sum(lot["grams"] for lot in job.fresh_food_lots) != expected_lots):
                    raise ValueError("Fresh-fruit lot references do not match active preservation job")
            integer(job.id, 1, ship.next_id - 1)
            integer(job.priority, 1, 5)
            integer(job.work, 1, 1000)
            number(job.progress, 0, job.work + 10)
            if (job.id in ids or job.kind not in LABORS or not isinstance(job.key, str)
                    or job.state not in ("pending", "blocked", "claimed", "paused", "returning", "done", "cancelled")):
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
                if (not isinstance(plan, dict) or set(plan) not in (
                            {"action", "kind", "component_id"},
                            {"action", "kind", "component_id", "z"})
                        or plan["action"] not in ("build", "dismantle")
                        or plan["kind"] not in ("floor", "wall", "door", "stair", "beam", "pillar") or job.kind != "repair"
                        or len(job.target) != 2
                        or any(type(v) is not int for v in job.target)):
                    raise ValueError("Invalid construction job")
                if "z" in plan:
                    integer(plan["z"], 0, 8)
                if not (ORIGIN[0] <= job.target[0] < ORIGIN[0] + WIDTH
                        and ORIGIN[1] <= job.target[1] < ORIGIN[1] + HEIGHT):
                    raise ValueError("Invalid construction site")
                cid = plan["component_id"]
                if ((plan["action"] == "build" and cid is not None)
                        or (plan["action"] == "dismantle" and (type(cid) is not int or cid < 1))
                        or job.key not in (f"construction:{job.target[0]},{job.target[1]}",
                                           f"construction:{job.target[0]},{job.target[1]}:{plan['kind']}",
                                           f"construction:{job.target[0]},{job.target[1]}:{plan.get('z', 0)}",
                                           f"construction:{job.target[0]},{job.target[1]}:{plan['kind']}:{plan.get('z', 0)}")):
                    raise ValueError("Invalid construction designation")
            expected = {"repair": ship.stations["repair"], "bail": ship.stations["bilge"],
                        "cook": ship.stations["galley"], "rig": ship.stations["rigging"]}
            allowed = [expected[job.kind]] if job.kind in expected else (
                [ship.stations[k] for k in ship.ammo] if job.kind == "load" else
                [tuple(map(int, key.split(","))) for key in ship.dirt])
            fire_job = job.kind == "repair" and job.key.startswith("fire:")
            if fire_job:
                from pirates.ship_map import ORIGIN
                try:
                    fx, fy, fz = map(int, job.key[5:].split(","))
                except (TypeError, ValueError):
                    raise ValueError("Invalid fire-suppression job") from None
                if (job.target != (fx + ORIGIN[0], fy + ORIGIN[1], fz)
                        or not (0 <= fx < 23 and 0 <= fy < 32 and 0 <= fz <= 8)
                        or not any(c.kind == "floor" and (c.x, c.y, c.z) == (fx, fy, fz)
                                   for c in world.ship_model.components)):
                    raise ValueError("Invalid fire-suppression site")
                allowed.append(job.target)
            if job.state in ("done", "cancelled"):
                # Completed jobs are historical: their station may since have moved.
                if (len(job.target) == 2 and all(type(v) is int for v in job.target)
                        and world.game_map.in_bounds(*job.target)):
                    allowed.append(job.target)
            if ((not job.construction and not job.relocation and not job.transfer and not fire_job
                 and job.target not in allowed)
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
