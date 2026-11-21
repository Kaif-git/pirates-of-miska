"""Serializable, independently cloneable state for COMBAT SPEC sections 3/19.

Objects are mutable only through explicit simulation calls on an owned state.
There is no process RNG, clock, game world, or renderer dependency.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from enum import Enum
import math
import random

from .config import (_legacy_fear_susceptibility, load_config, upgrade_config,
                     validate_config)


class Zone(str, Enum):
    HEAD = "HEAD"
    EYE = "EYE"
    TORSO = "TORSO"
    ARM = "ARM"
    HAND = "HAND"
    LEG = "LEG"


class Severity(str, Enum):
    BRUISE = "BRUISE"
    GRAZE = "GRAZE"
    CUT = "CUT"
    DEEP = "DEEP"
    SEVERED = "SEVERED"


class ActionKind(str, Enum):
    LIGHT = "LIGHT"
    HEAVY = "HEAVY"
    GUARD = "GUARD"
    DASH = "DASH"
    DASH_LIGHT = "DASH_LIGHT"
    DASH_SLASH = "DASH_SLASH"
    WALK = "WALK"
    RUN = "RUN"
    CRAWL = "CRAWL"
    DISENGAGE = "DISENGAGE"
    RISE = "RISE"
    FEINT = "FEINT"
    SHOVE = "SHOVE"
    THRUST = "THRUST"
    DISARM = "DISARM"
    ADRENALINE = "ADRENALINE"
    WAIT = "WAIT"
    BANDAGE = "BANDAGE"
    EAT = "EAT"
    DRINK = "DRINK"
    PICKUP = "PICKUP"
    YIELD = "YIELD"


class EventType(str, Enum):
    ACTION_START = "ACTION_START"
    ACTION_END = "ACTION_END"
    ATTACK_LOCK = "ATTACK_LOCK"
    MOVE = "MOVE"
    WHIFF = "WHIFF"
    HIT = "HIT"
    CLASH = "CLASH"
    BLOCK = "BLOCK"
    PARRY = "PARRY"
    GUARD_BREAK = "GUARD_BREAK"
    STAGGER = "STAGGER"
    WOUND = "WOUND"
    INJURY_RESPONSE = "INJURY_RESPONSE"
    STABILITY = "STABILITY"
    FALL = "FALL"
    RISE = "RISE"
    FEINT = "FEINT"
    SHOVE = "SHOVE"
    DISARM = "DISARM"
    ADRENALINE = "ADRENALINE"
    SEVER = "SEVER"
    EYE_INJURY = "EYE_INJURY"
    DECAPITATION = "DECAPITATION"
    INCAPACITATED = "INCAPACITATED"
    WEAPON_STRESS = "WEAPON_STRESS"
    WEAPON_DROP = "WEAPON_DROP"
    WEAPON_BREAK = "WEAPON_BREAK"
    WEAPON_PICKUP = "WEAPON_PICKUP"
    INTERRUPT = "INTERRUPT"
    EXHAUST = "EXHAUST"
    RECOVER = "RECOVER"
    POOL_CHANGE = "POOL_CHANGE"
    STATUS = "STATUS"
    ANGER_CRASH = "ANGER_CRASH"
    FEAR_BREAK = "FEAR_BREAK"
    FIGHT_END = "FIGHT_END"
    DECISION = "DECISION"
    TERMINAL_OUTCOME = "TERMINAL_OUTCOME"
    LEVEL_UP = "LEVEL_UP"
    XP_GAIN = "XP_GAIN"


@dataclass
class Wound:
    zone: Zone
    severity: Severity
    bleed_per_turn: float
    bandaged: bool = False
    initial_blood_loss: float = 0
    depth: str | None = None
    structure: str | None = None
    damage_type: str = "CUTTING"
    site: str | None = None
    side: str | None = None
    vital_outcome: str | None = None

    def __post_init__(self):
        self.zone, self.severity = Zone(self.zone), Severity(self.severity)
        if not math.isfinite(self.bleed_per_turn) or self.bleed_per_turn < 0:
            raise ValueError("Bleeding must be finite and nonnegative")
        if not math.isfinite(self.initial_blood_loss) or self.initial_blood_loss < 0:
            raise ValueError("Initial blood loss must be finite and nonnegative")
        self.damage_type = str(self.damage_type).upper()
        if self.damage_type not in {"CUTTING", "PIERCING", "BLUNT"}:
            raise ValueError("Unknown wound damage type")
        if self.depth is None:
            self.depth = {
                Severity.BRUISE: "CONTUSION", Severity.GRAZE: "SUPERFICIAL",
                Severity.CUT: "MUSCLE", Severity.DEEP: "DEEP",
                Severity.SEVERED: "STRUCTURAL",
            }[self.severity]
        if self.structure is None:
            self.structure = wound_structure(self.zone, self.severity, self.damage_type)
        if not isinstance(self.depth, str) or not self.depth or not isinstance(self.structure, str) or not self.structure:
            raise ValueError("Wound depth and structure must be nonempty labels")
        if self.site is None:
            self.site = self.zone.value
        if not isinstance(self.site, str) or not self.site:
            raise ValueError("Wound site must be a nonempty label")
        if self.side not in {None, "LEFT", "RIGHT", "CENTER"}:
            raise ValueError("Wound side must be LEFT, RIGHT, CENTER or unspecified")
        if self.vital_outcome not in {None, "HEART_FATAL", "HEART_BLEED", "LUNG_BLEED", "LIVER_BLEED"}:
            raise ValueError("Unknown persisted vital-injury outcome")


def wound_structure(zone, severity, damage_type="CUTTING"):
    """Coarse, non-organ-specific structure labels for persisted legacy zones."""
    zone, severity, damage_type = Zone(zone), Severity(severity), str(damage_type).upper()
    if severity == Severity.BRUISE:
        return "CONTUSION"
    if severity == Severity.GRAZE:
        return "CONTUSION" if damage_type == "BLUNT" else "SKIN"
    if damage_type == "BLUNT":
        if severity == Severity.CUT:
            return "MUSCLE_CONTUSION"
        return {
            Zone.HEAD: "CONCUSSION", Zone.EYE: "EYE_CONTUSION", Zone.TORSO: "RIBS",
            Zone.ARM: "BONE_CONTUSION", Zone.HAND: "BONE_CONTUSION",
            Zone.LEG: "BONE_CONTUSION",
        }[zone]
    if damage_type == "PIERCING" and severity in {Severity.CUT, Severity.DEEP}:
        return "EYE" if zone == Zone.EYE else "PENETRATING_SOFT_TISSUE" if zone != Zone.TORSO else "TORSO_WALL"
    if severity == Severity.SEVERED:
        return "HAND_INTEGRITY" if zone == Zone.HAND else "STRUCTURAL_TISSUE"
    if severity == Severity.DEEP:
        return {
            Zone.HEAD: "CONCUSSION", Zone.EYE: "EYE", Zone.TORSO: "TORSO_WALL",
            Zone.ARM: "TENDON", Zone.HAND: "TENDON", Zone.LEG: "TENDON",
        }[zone]
    if severity == Severity.CUT:
        return "EYE" if zone == Zone.EYE else "MUSCLE"
    return "SOFT_TISSUE"


@dataclass
class Status:
    name: str
    until_tick: int | None = None
    speed_mult: float = 1.0
    strength_mult: float = 1.0
    accuracy_mult: float = 1.0
    parry_penalty: int = 0
    no_dash: bool = False

    def active(self, tick):
        return self.until_tick is None or tick < self.until_tick


@dataclass(init=False)
class Weapon:
    edge: float
    reach: int
    weight: float
    speed_mod: float
    damage_type: str
    item_id: str
    name: str
    material: str
    condition: float

    def __init__(self, edge=None, reach=None, weight=None, speed_mod=None, damage_type=None,
                 item_id=None, name=None, material=None, condition=None):
        defaults = load_config()["gear"]["weapon"]
        for key, value in (("edge", edge), ("reach", reach), ("weight", weight),
                           ("speed_mod", speed_mod)):
            setattr(self, key, defaults[key] if value is None else value)
        self.damage_type = ("CUTTING" if damage_type is None else str(damage_type)).upper()
        self.item_id = "" if item_id is None else str(item_id)
        self.name = defaults["name"] if name is None else str(name)
        self.material = defaults["material"] if material is None else str(material).upper()
        self.condition = defaults["condition"] if condition is None else condition
        if (any(not math.isfinite(v) for v in (self.edge, self.reach, self.weight, self.speed_mod))
                or self.edge < 0 or self.reach < 1 or self.weight < 0 or self.speed_mod <= 0
                or self.damage_type not in {"CUTTING", "PIERCING", "BLUNT"}
                or not self.name or self.material not in {"WOOD", "IRON", "STEEL"}
                or isinstance(self.condition, bool) or not isinstance(self.condition, (int, float))
                or not math.isfinite(self.condition) or not 0 <= self.condition <= 100):
            raise ValueError("Invalid weapon")


@dataclass
class GroundWeapon:
    weapon: Weapon
    pos: tuple[int, int]

    def __post_init__(self):
        self.pos = tuple(self.pos)
        if (not isinstance(self.weapon, Weapon) or not self.weapon.item_id
                or len(self.pos) != 2
                or any(isinstance(value, bool) or not isinstance(value, int) for value in self.pos)):
            raise ValueError("Invalid ground weapon")


@dataclass
class ArmorPiece:
    value: float
    weight: float
    zones: tuple[Zone, ...] = ()

    def __post_init__(self):
        self.zones = tuple(Zone(zone) for zone in self.zones)
        if any(not math.isfinite(v) or v < 0 for v in (self.value, self.weight)):
            raise ValueError("Invalid armor")


@dataclass
class Equipment:
    weapon: Weapon | None = field(default_factory=Weapon)
    armor: dict[Zone, ArmorPiece] = field(default_factory=dict)

    @property
    def total_weight(self):
        return (self.weapon.weight if self.weapon is not None else 0) + sum(
            piece.weight for piece in self.armor.values())


@dataclass
class PermanentInjuries:
    hand_severed: bool = False
    eyes_damaged: int = 0
    severed_hands: list[str] = field(default_factory=list)
    severed_ears: list[str] = field(default_factory=list)
    eye_trauma: float = 0

    def __post_init__(self):
        if not isinstance(self.hand_severed, bool):
            raise ValueError("Permanent hand injury must be boolean")
        if (isinstance(self.eyes_damaged, bool) or not isinstance(self.eyes_damaged, int)
                or not 0 <= self.eyes_damaged <= 2):
            raise ValueError("Permanent eye injury count must be 0, 1 or 2")
        if (isinstance(self.eye_trauma, bool) or not isinstance(self.eye_trauma, (int, float))
                or not math.isfinite(self.eye_trauma) or self.eye_trauma < 0):
            raise ValueError("Cumulative eye trauma must be a finite nonnegative number")
        if (not isinstance(self.severed_hands, list)
                or any(side not in {"LEFT", "RIGHT"} for side in self.severed_hands)
                or len(self.severed_hands) != len(set(self.severed_hands))):
            raise ValueError("Severed hand sides must be a unique list of LEFT/RIGHT")
        if (not isinstance(self.severed_ears, list)
                or any(side not in {"LEFT", "RIGHT"} for side in self.severed_ears)
                or len(self.severed_ears) != len(set(self.severed_ears))):
            raise ValueError("Severed ear sides must be a unique list of LEFT/RIGHT")


@dataclass
class PreFightState:
    fatigue: float = 0
    stamina: float | None = None
    blood: float | None = None
    injuries: list[Wound] = field(default_factory=list)
    nervousness: float = 0
    permanent_injuries: PermanentInjuries = field(default_factory=PermanentInjuries)


@dataclass
class Personality:
    archetype: str
    weights: dict[str, float]
    base: dict[str, float]
    reaction_delay: int
    zone_pref: list[str]
    composure: float = 1.0
    courage: float = 0
    traits: dict[str, float] = field(default_factory=lambda: {
        "aggression": 0.8, "discipline": 0.4, "confidence": 0.7,
        "patience": 0.25, "adaptability": 0.35,
    })
    score_bias: dict[str, float] = field(default_factory=dict)
    anger_tolerance: float = 0.5
    pain_tolerance: float = 0.5
    fear_gain_susceptibility: float = 1.0

    def __post_init__(self):
        dimensions = {"aggression", "discipline", "confidence", "patience", "adaptability"}
        if set(self.traits) != dimensions or any(
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not 0 <= value <= 1
                for value in self.traits.values()):
            raise ValueError("Personality traits must contain five finite values in [0, 1]")
        if (isinstance(self.composure, bool) or not math.isfinite(self.composure)
                or not 0 <= self.composure <= 1):
            raise ValueError("Personality composure must be in [0, 1]")
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) or not 0 <= value <= 1
               for value in (self.anger_tolerance, self.pain_tolerance)):
            raise ValueError("Mental tolerances must be in [0, 1]")
        if (isinstance(self.fear_gain_susceptibility, bool)
                or not isinstance(self.fear_gain_susceptibility, (int, float))
                or not math.isfinite(self.fear_gain_susceptibility)
                or not 0.5 <= self.fear_gain_susceptibility <= 1.5):
            raise ValueError("Fear-gain susceptibility must be finite and in [0.5, 1.5]")
        if isinstance(self.reaction_delay, bool) or not isinstance(self.reaction_delay, int) or self.reaction_delay < 0:
            raise ValueError("Personality reaction delay must be a nonnegative integer")
        if any(not isinstance(key, str) or isinstance(value, bool)
               or not isinstance(value, (int, float)) or not math.isfinite(value)
               for key, value in self.score_bias.items()):
            raise ValueError("Personality score biases must be finite numbers")


@dataclass(frozen=True)
class EffStats:
    speed: float
    strength: float
    accuracy: float
    endurance: float
    fatigue_penalty: float
    pain_penalty: float
    blood_penalty: float


@dataclass(frozen=True)
class Phase:
    name: str
    start: int
    end: int | None


@dataclass
class ActiveAction:
    kind: ActionKind
    aim: Zone | None
    direction: tuple[int, int] | None
    start_tick: int
    phases: list[Phase]
    strike_tick: int | None
    cost_paid: float
    locked_stats: EffStats
    end_tick: int | None
    path: list[tuple[int, tuple[int, int]]] = field(default_factory=list)
    windup_len: int = 0
    power_mult: float = 0
    acc_mod: float = 0
    guard_ready_tick: int | None = None
    parry_window: int = 0
    released: bool = False
    item_id: str | None = None
    target_id: str | None = None
    attack_bearing: tuple[int, int] | None = None
    bearing_lock_tick: int | None = None
    contextual_defense: bool = False
    tactical_resolved: bool = False
    clash_resolved: bool = False

    def __post_init__(self):
        if not isinstance(self.contextual_defense, bool):
            raise ValueError("Contextual defense flag must be boolean")
        if not isinstance(self.tactical_resolved, bool):
            raise ValueError("Tactical resolution flag must be boolean")
        if not isinstance(self.clash_resolved, bool):
            raise ValueError("Clash resolution flag must be boolean")
        if self.attack_bearing is not None and (
                tuple(self.attack_bearing) not in {
                    (0, -1), (1, -1), (1, 0), (1, 1),
                    (0, 1), (-1, 1), (-1, 0), (-1, -1)}):
            raise ValueError("Invalid locked attack bearing")
        if self.bearing_lock_tick is not None and (
                isinstance(self.bearing_lock_tick, bool)
                or not isinstance(self.bearing_lock_tick, int)
                or self.bearing_lock_tick < self.start_tick):
            raise ValueError("Invalid attack bearing lock tick")

    def phase_at(self, tick):
        for phase in self.phases:
            if tick >= phase.start and (phase.end is None or tick < phase.end):
                return phase.name
        return "IDLE"


@dataclass
class Fighter:
    id: str
    name: str
    level: int
    strength: float
    speed: float
    accuracy: float
    stamina_stat: float
    endurance: float
    stamina: float | None = None
    fatigue: float | None = None
    blood: float | None = None
    pain: float | None = None
    anger: float | None = None
    morale: float | None = None
    fear_meter: float | None = None
    pos: tuple[int, int] | None = None
    action: ActiveAction | None = None
    last_action_cat: str = "NONE"
    previous_action_cat: str = "NONE"
    last_strike_whiffed: bool = False
    exhausted: bool = False
    wounds: list[Wound] = field(default_factory=list)
    statuses: list[Status] = field(default_factory=list)
    equipment: Equipment = field(default_factory=Equipment)
    permanent_injuries: PermanentInjuries = field(default_factory=PermanentInjuries)
    weapon_hand: str = "RIGHT"
    personality: Personality | None = None
    notebook: dict | None = None
    prefight: PreFightState | None = None
    nervousness: float | None = None
    unconscious: bool = False
    yielded: bool = False
    fled: bool = False
    disarmed: bool = False
    decapitated: bool = False
    stagger_until: int = 0
    riposte: bool = False
    idle_since: int = 0
    first_exchange_tick: int | None = None
    last_hit_tick: int | None = None
    hits_without_return: int = 0
    fear_decisions: int = 0
    anger_history: list[tuple[int, float]] = field(default_factory=list)
    blood_thresholds: list[float] = field(default_factory=list)
    action_history: list[str] = field(default_factory=list)
    ai_experience: list[dict] = field(default_factory=list)
    ai_stagnant_decisions: int = 0
    ai_current_category: str | None = None
    last_recoil_tick: int | None = None
    stability: float | None = None
    grounded: bool = False
    grounded_since: int | None = None
    last_fast_direction: tuple[int, int] | None = None
    last_fast_move_tick: int | None = None
    adrenaline_used: bool = False
    adrenaline_until_tick: int | None = None
    adrenaline_debt_pending: bool = False
    adrenaline_debt_due: float = 0
    bandages: int = 0
    food: int = 0
    water: int = 0
    xp: float = 0
    team_id: str | None = None
    contact_memory: dict = field(default_factory=dict)
    morale_reacted_exchange: int | None = None
    terminal_reason: str | None = None

    def __post_init__(self):
        cfg = load_config()
        default = cfg["defaults"]
        if not self.id or not self.name:
            raise ValueError("Fighter requires id and name")
        if self.team_id is not None and (not isinstance(self.team_id, str) or not self.team_id):
            raise ValueError("Team ID must be nonempty text when provided")
        if (not isinstance(self.contact_memory, dict) or len(self.contact_memory) > 64
                or any(not isinstance(identity, str) or not identity or not isinstance(row, dict)
                       or set(row) != {"position", "last_seen_tick", "last_heard_tick", "bearing"}
                       for identity, row in self.contact_memory.items())):
            raise ValueError("Invalid bounded contact memory")
        if (self.morale_reacted_exchange is not None
                and (isinstance(self.morale_reacted_exchange, bool)
                     or not isinstance(self.morale_reacted_exchange, int)
                     or self.morale_reacted_exchange < 0)):
            raise ValueError("Invalid group-morale exchange marker")
        if self.terminal_reason not in {None, "death", "unconscious", "yield", "flee"}:
            raise ValueError("Invalid terminal combat outcome")
        for row in self.contact_memory.values():
            if row["position"] is not None:
                row["position"] = tuple(row["position"])
                if len(row["position"]) != 2 or any(not isinstance(value, int) for value in row["position"]):
                    raise ValueError("Invalid last-known contact position")
            for key in ("last_seen_tick", "last_heard_tick"):
                value = row[key]
                if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                    raise ValueError("Invalid contact-memory tick")
            if row["bearing"] is not None:
                row["bearing"] = tuple(row["bearing"])
                if len(row["bearing"]) != 2 or any(value not in {-1, 0, 1} for value in row["bearing"]):
                    raise ValueError("Invalid sound bearing")
        if isinstance(self.level, bool) or not isinstance(self.level, int) or not default["level_min"] <= self.level <= default["level_max"]:
            raise ValueError("Invalid fighter level")
        for key in ("strength", "speed", "accuracy", "stamina_stat", "endurance"):
            value = getattr(self, key)
            if isinstance(value, bool) or not math.isfinite(value) or value < default["stat_min"]:
                raise ValueError(f"Invalid {key}")
        if self.speed <= 0 or self.stamina_stat <= 0:
            raise ValueError("Speed and stamina stat must be positive")
        cap = self.stamina_stat * (1 + cfg["level"]["stat_per_level"] * (self.level - 1)) * cfg["stamina"]["pool_per_stat"]
        for key in ("fatigue", "pain", "anger", "morale", "nervousness"):
            if getattr(self, key) is None:
                setattr(self, key, default[key])
        if self.fear_meter is None:
            confidence = (self.personality.traits["confidence"] if self.personality
                          else default["confidence"])
            self.fear_meter = default["pool_max"] * (1 - confidence)
        if self.stamina is None:
            self.stamina = cap
        if self.blood is None:
            self.blood = cfg["blood"]["start"]
        if self.stability is None:
            self.stability = cfg["stability"]["start"]
        if self.pos is None:
            self.pos = tuple(default["pos"])
        else:
            self.pos = tuple(self.pos)
        if len(self.pos) != 2 or any(isinstance(v, bool) or not isinstance(v, int) for v in self.pos):
            raise ValueError("Position must contain two integers")
        if isinstance(self.permanent_injuries, dict):
            self.permanent_injuries = PermanentInjuries(**self.permanent_injuries)
        if self.weapon_hand not in {"LEFT", "RIGHT"}:
            raise ValueError("Weapon hand must be LEFT or RIGHT")
        if self.weapon_hand in self.permanent_injuries.severed_hands:
            self.permanent_injuries.hand_severed = True
        if self.equipment.weapon is not None and not self.equipment.weapon.item_id:
            self.equipment.weapon.item_id = f"{self.id}:weapon"
        if self.last_fast_direction is not None:
            self.last_fast_direction = tuple(self.last_fast_direction)
        if self.prefight is not None:
            self.fatigue = self.prefight.fatigue
            if self.prefight.stamina is not None:
                self.stamina = self.prefight.stamina
            if self.prefight.blood is not None:
                self.blood = self.prefight.blood
            self.wounds = deepcopy(self.prefight.injuries)
            self.permanent_injuries = deepcopy(self.prefight.permanent_injuries)
            confidence = (self.personality.traits["confidence"] if self.personality
                          else default["confidence"])
            self.fear_meter = min(default["pool_max"],
                                  default["pool_max"] * (1 - confidence)
                                  + self.prefight.nervousness * default["pool_max"])
        # Kept as a compatibility field for old state payloads only; current
        # mental pressure is represented by fear_meter.
        self.nervousness = 0
        if self.equipment.weapon is None:
            self.disarmed = True
        if not isinstance(self.decapitated, bool):
            raise ValueError("Decapitation state must be boolean")
        for key in ("fatigue", "blood", "pain", "anger", "morale", "fear_meter", "stamina", "nervousness"):
            value = getattr(self, key)
            upper = cap if key == "stamina" else 1 if key == "nervousness" else default["pool_max"]
            if not math.isfinite(value) or not default["pool_min"] <= value <= upper:
                raise ValueError(f"Invalid {key} pool")
        if isinstance(self.xp, bool) or not isinstance(self.xp, (int, float)) or not math.isfinite(self.xp) or self.xp < 0:
            raise ValueError("XP must be finite and nonnegative")
        if (isinstance(self.ai_stagnant_decisions, bool) or not isinstance(self.ai_stagnant_decisions, int)
                or self.ai_stagnant_decisions < 0):
            raise ValueError("Invalid AI decision streak")
        if self.last_recoil_tick is not None and (
                isinstance(self.last_recoil_tick, bool) or not isinstance(self.last_recoil_tick, int)
                or self.last_recoil_tick < 0):
            raise ValueError("Invalid last recoil tick")
        if not isinstance(self.adrenaline_used, bool) or not isinstance(self.adrenaline_debt_pending, bool):
            raise ValueError("Adrenaline flags must be boolean")
        if self.adrenaline_until_tick is not None and (
                isinstance(self.adrenaline_until_tick, bool)
                or not isinstance(self.adrenaline_until_tick, int) or self.adrenaline_until_tick < 0):
            raise ValueError("Invalid adrenaline expiry tick")
        if (isinstance(self.adrenaline_debt_due, bool)
                or not isinstance(self.adrenaline_debt_due, (int, float))
                or not math.isfinite(self.adrenaline_debt_due) or self.adrenaline_debt_due < 0
                or self.adrenaline_debt_pending != (self.adrenaline_debt_due > 0)
                or self.adrenaline_debt_pending and self.adrenaline_until_tick is None):
            raise ValueError("Invalid pending adrenaline debt")
        if (self.adrenaline_until_tick is not None or self.adrenaline_debt_pending) and not self.adrenaline_used:
            raise ValueError("Active adrenaline must have consumed its once-per-duel use")
        if (isinstance(self.stability, bool) or not isinstance(self.stability, (int, float))
                or not math.isfinite(self.stability) or not 0 <= self.stability <= 100):
            raise ValueError("Stability must be finite and in [0, 100]")
        if not isinstance(self.grounded, bool):
            raise ValueError("Grounded must be boolean")
        if self.grounded_since is not None and (
                isinstance(self.grounded_since, bool) or not isinstance(self.grounded_since, int)
                or self.grounded_since < 0):
            raise ValueError("Invalid grounded tick")
        if self.grounded != (self.grounded_since is not None) or self.grounded and self.stability != 0:
            raise ValueError("Grounded state must latch at zero stability with a grounded tick")
        if self.last_fast_direction is not None and (
                len(self.last_fast_direction) != 2
                or any(isinstance(value, bool) or not isinstance(value, int) or abs(value) > 1
                       for value in self.last_fast_direction)
                or self.last_fast_direction == (0, 0)):
            raise ValueError("Invalid last fast-movement direction")
        for key in ("last_fast_move_tick",):
            value = getattr(self, key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError(f"Invalid {key}")
        if len(self.ai_experience) > 12 or any(
                not isinstance(row, dict) or set(row) != {"category", "outcome"}
                or not isinstance(row["category"], str)
                or row["outcome"] not in {"COUNTERED", "SUCCESS"}
                for row in self.ai_experience):
            raise ValueError("Invalid bounded AI experience history")

    def can_act(self, tick=0):
        return (self.blood > 0 and not (self.unconscious or self.yielded or self.fled or self.decapitated)
                and tick >= self.stagger_until)

    @property
    def hand_severed(self):
        return self.permanent_injuries.hand_severed

    @hand_severed.setter
    def hand_severed(self, value):
        self.permanent_injuries.hand_severed = bool(value)


@dataclass
class Event:
    tick: int
    type: EventType
    actor: str | None = None
    target: str | None = None
    zone: Zone | None = None
    severity: Severity | None = None
    deltas: dict = field(default_factory=dict)
    cause_tags: tuple[str, ...] = ()
    witnesses: tuple[str, ...] = ()
    hearers: tuple[str, ...] = ()


def _fighter_from_data(data):
    values = deepcopy(data)
    if "fear_meter" not in values:
        # Legacy saves represented fear as a morale/anger derivation and stored
        # nervousness separately. Fold both into the new persisted fear pool.
        cfg = load_config()
        legacy = max(0, (cfg["fear"]["morale_center"] - values.get("morale", cfg["defaults"]["morale"]))
                     * cfg["fear"]["morale_scale"]
                     - cfg["fear"]["anger_offset"] * values.get("anger", cfg["defaults"]["anger"]))
        values["fear_meter"] = min(cfg["defaults"]["pool_max"],
                                   legacy + values.get("nervousness", 0) * cfg["defaults"]["pool_max"])
    # The separate nervousness field remains load-compatible only.
    values["nervousness"] = 0
    old_hand_severed = values.pop("hand_severed", False)
    permanent = values.get("permanent_injuries")
    if permanent is None:
        values["permanent_injuries"] = PermanentInjuries(hand_severed=old_hand_severed)
    else:
        permanent.setdefault("hand_severed", old_hand_severed)
        values["permanent_injuries"] = PermanentInjuries(**permanent)
    equipment = values["equipment"]
    values["equipment"] = Equipment(Weapon(**equipment["weapon"]) if equipment["weapon"] else None, {
        Zone(zone): ArmorPiece(**piece) for zone, piece in equipment["armor"].items()})
    values["wounds"] = [Wound(**item) for item in values["wounds"]]
    values["statuses"] = [Status(**item) for item in values["statuses"]]
    if values["personality"]:
        values["personality"].setdefault(
            "fear_gain_susceptibility",
            _legacy_fear_susceptibility(values["personality"]))
        values["personality"] = Personality(**values["personality"])
    for row in values.get("contact_memory", {}).values():
        if row.get("position") is not None:
            row["position"] = tuple(row["position"])
        if row.get("bearing") is not None:
            row["bearing"] = tuple(row["bearing"])
    prefight = values["prefight"]
    # Restoring a snapshot must not reapply the pre-fight values to live pools.
    values["prefight"] = None
    action = values["action"]
    if action:
        action["kind"] = ActionKind(action["kind"])
        action["aim"] = Zone(action["aim"]) if action["aim"] else None
        action["direction"] = tuple(action["direction"]) if action["direction"] else None
        action["attack_bearing"] = (tuple(action["attack_bearing"])
                                    if action.get("attack_bearing") else None)
        action["phases"] = [Phase(**item) for item in action["phases"]]
        action["locked_stats"] = EffStats(**action["locked_stats"])
        action["path"] = [(tick, tuple(pos)) for tick, pos in action["path"]]
        values["action"] = ActiveAction(**action)
    fighter = Fighter(**values)
    if prefight:
        prefight["injuries"] = [Wound(**item) for item in prefight["injuries"]]
        prefight.setdefault("permanent_injuries", {})
        prefight["permanent_injuries"] = PermanentInjuries(**prefight["permanent_injuries"])
        fighter.prefight = PreFightState(**prefight)
    fighter.anger_history = [tuple(item) for item in fighter.anger_history]
    return fighter


def _plain(value):
    """Stable JSON-shaped representation, including tuple paths and enum keys."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {_plain(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


@dataclass
class CombatState:
    fighters: list[Fighter]
    rng_seed: int = 0
    config: dict = field(default_factory=load_config)
    tick: int = 0
    event_log: list[Event] = field(default_factory=list)
    rng_draws: int = 0
    end_reason: str | None = None
    winner: str | None = None
    walkable: set[tuple[int, int]] | None = None
    exits: set[tuple[int, int]] = field(default_factory=set)
    pending_interrupts: list[str] = field(default_factory=list)
    pending_injury_responses: list[dict] = field(default_factory=list)
    ground_weapons: list[GroundWeapon] = field(default_factory=list)
    guide_mode: str = "off"
    leaders: dict[str, str] = field(default_factory=dict)
    _rng: random.Random = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        self.config = deepcopy(validate_config(self.config))
        if len(self.fighters) < 2 or len({fighter.id for fighter in self.fighters}) != len(self.fighters):
            raise ValueError("Combat requires at least two distinct fighters")
        if len(self.fighters) == 2:
            for index, fighter in enumerate(self.fighters):
                if fighter.team_id is None:
                    fighter.team_id = f"side-{index}"
        elif any(fighter.team_id is None for fighter in self.fighters):
            raise ValueError("Multi-actor combat requires explicit team IDs for every fighter")
        if len({fighter.team_id for fighter in self.fighters}) < 2:
            raise ValueError("Combat requires at least two teams")
        if not isinstance(self.leaders, dict):
            raise ValueError("Group leaders must map teams to actor IDs")
        by_id = {fighter.id: fighter for fighter in self.fighters}
        teams = {fighter.team_id for fighter in self.fighters}
        if set(self.leaders) - teams:
            raise ValueError("Leader map includes an unknown team")
        for team_id in sorted(teams):
            self.leaders.setdefault(team_id, next(fighter.id for fighter in self.fighters
                                                   if fighter.team_id == team_id))
        if any(actor_id not in by_id or by_id[actor_id].team_id != team_id
               for team_id, actor_id in self.leaders.items()):
            raise ValueError("Each group leader must belong to its team")
        if not isinstance(self.rng_seed, int) or not isinstance(self.tick, int) or self.tick < 0:
            raise ValueError("Seed and tick must be integers, tick nonnegative")
        self._rng = random.Random(self.rng_seed)
        for _ in range(self.rng_draws):
            self._rng.random()
        for fighter in self.fighters:
            if fighter.hand_severed or fighter.equipment.weapon is None:
                fighter.disarmed = True
            if fighter.disarmed and fighter.equipment.weapon is not None:
                self.ground_weapons.append(GroundWeapon(fighter.equipment.weapon, fighter.pos))
                fighter.equipment.weapon = None
        item_ids = [fighter.equipment.weapon.item_id for fighter in self.fighters
                    if fighter.equipment.weapon is not None]
        item_ids.extend(item.weapon.item_id for item in self.ground_weapons)
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("Weapon item identity cannot be duplicated")

    def clone(self):
        return deepcopy(self)

    def uniform(self, lower, upper):
        self.rng_draws += 1
        return lower + (upper - lower) * self._rng.random()

    def other(self, fighter):
        if len(self.fighters) > 2:
            visible = self.visible_hostiles_for(fighter.id)
            return visible[0] if visible else None
        return (self.primary_opponent(fighter.id, active_only=True)
                or self.primary_opponent(fighter.id, active_only=False))

    def fighter(self, fighter_id):
        return next(item for item in self.fighters if item.id == fighter_id)

    @staticmethod
    def combat_active(fighter):
        return (fighter.blood > 0 and not
                (fighter.unconscious or fighter.yielded or fighter.fled or fighter.decapitated))

    def hostiles_for(self, fighter_id, *, active_only=True):
        actor = self.fighter(fighter_id)
        return [item for item in self.fighters
                if item.team_id != actor.team_id
                and (not active_only or self.combat_active(item))]

    def allies_for(self, fighter_id, *, include_self=False):
        actor = self.fighter(fighter_id)
        return [item for item in self.fighters
                if item.team_id == actor.team_id and (include_self or item.id != actor.id)]

    def primary_opponent(self, fighter_id, target_id=None, *, active_only=True):
        actor = self.fighter(fighter_id) if isinstance(fighter_id, str) else fighter_id
        if target_id is not None:
            try:
                target = self.fighter(target_id)
            except StopIteration:
                return None
            if target.team_id == actor.team_id or active_only and not self.combat_active(target):
                return None
            return target
        candidates = self.hostiles_for(actor.id, active_only=active_only)
        if not candidates:
            return None
        return min(candidates, key=lambda item: (max(abs(actor.pos[0]-item.pos[0]),
                                                       abs(actor.pos[1]-item.pos[1])), item.id))

    def occupies_tile(self, fighter):
        return not fighter.fled

    def can_see(self, observer, target):
        distance = max(abs(observer.pos[0]-target.pos[0]), abs(observer.pos[1]-target.pos[1]))
        vision_range = self.config["group"]["vision_range"]
        if observer.permanent_injuries.eyes_damaged >= 2:
            vision_range = min(vision_range, self.config["injury"]["bilateral_vision_range"])
        if distance > vision_range:
            return False
        for pos in self.line_cells(observer.pos, target.pos):
            if self.walkable is not None and pos not in self.walkable:
                return False
            if any(fighter.id not in {observer.id, target.id}
                   and self.occupies_tile(fighter) and fighter.pos == pos
                   for fighter in self.fighters):
                return False
        return True

    def visible_hostiles_for(self, fighter_id):
        observer = self.fighter(fighter_id)
        return sorted((target for target in self.hostiles_for(fighter_id)
                       if self.can_see(observer, target)),
                      key=lambda target: (max(abs(observer.pos[0]-target.pos[0]),
                                              abs(observer.pos[1]-target.pos[1])), target.id))

    def perceived_hostile_position(self, fighter_id):
        observer = self.fighter(fighter_id)
        visible = self.visible_hostiles_for(fighter_id)
        if visible:
            return visible[0].id, visible[0].pos, True
        now = self.tick
        ttl = (self.config["group"]["contact_memory_exchanges"]
               * self.config["time"]["ticks_per_turn"])
        memories = [(identity, row) for identity, row in observer.contact_memory.items()
                    if row["position"] is not None and row["last_seen_tick"] is not None
                    and now-row["last_seen_tick"] <= ttl
                    and (row["last_heard_tick"] is None
                         or row["last_seen_tick"] >= row["last_heard_tick"])
                    and any(fighter.id == identity and self.combat_active(fighter)
                            for fighter in self.fighters)]
        if memories:
            identity, row = min(memories, key=lambda item: (max(abs(observer.pos[0]-item[1]["position"][0]),
                                                                 abs(observer.pos[1]-item[1]["position"][1])),
                                                            item[0]))
            return identity, tuple(row["position"]), False
        heard = [(identity, row) for identity, row in observer.contact_memory.items()
                 if row["bearing"] is not None and row["last_heard_tick"] is not None
                 and now-row["last_heard_tick"] <= ttl
                 and any(fighter.id == identity and self.combat_active(fighter)
                         for fighter in self.fighters)]
        if not heard:
            return None
        identity, row = max(heard, key=lambda item: (item[1]["last_heard_tick"], item[0]))
        bearing = row["bearing"]
        # Direction only: use a conservative edge-of-hearing estimate, never the
        # off-screen actor's current or event-time exact location.
        estimate = (observer.pos[0] + bearing[0] * self.config["group"]["hearing_range"],
                    observer.pos[1] + bearing[1] * self.config["group"]["hearing_range"])
        return identity, estimate, False

    @staticmethod
    def line_cells(start, end):
        """Integer Bresenham cells between endpoints, excluding both endpoints."""
        x0, y0 = start
        x1, y1 = end
        dx, dy = abs(x1-x0), abs(y1-y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        error = dx-dy
        cells = []
        while (x0, y0) != (x1, y1):
            twice = 2*error
            if twice > -dy:
                error -= dy
                x0 += sx
            if twice < dx:
                error += dx
                y0 += sy
            if (x0, y0) != (x1, y1):
                cells.append((x0, y0))
        return tuple(cells)

    def line_blocker(self, attacker, target):
        for pos in self.line_cells(attacker.pos, target.pos):
            if self.walkable is not None and pos not in self.walkable:
                return ("ARENA_BLOCKED", pos)
            occupant = next((fighter for fighter in self.fighters
                             if fighter.id not in {attacker.id, target.id}
                             and self.occupies_tile(fighter) and fighter.pos == pos), None)
            if occupant:
                return (occupant.id, pos)
        return None

    def emit(self, kind, actor=None, target=None, zone=None, severity=None,
             deltas=None, cause_tags=()):
        witnesses = ()
        hearers = ()
        if len(self.fighters) > 2:
            actors = {fighter.id: fighter for fighter in self.fighters}
            sources = [actors[identity] for identity in (actor, target) if identity in actors]
            event_type = EventType(kind)
            direct_target_events = {EventType.HIT, EventType.CLASH, EventType.WOUND, EventType.BLOCK, EventType.PARRY,
                                    EventType.GUARD_BREAK, EventType.STAGGER, EventType.INJURY_RESPONSE,
                                    EventType.FALL, EventType.RISE, EventType.SHOVE, EventType.DISARM,
                                    EventType.INCAPACITATED, EventType.SEVER, EventType.EYE_INJURY,
                                    EventType.DECAPITATION, EventType.TERMINAL_OUTCOME}
            actor_only_events = {EventType.ACTION_START, EventType.ACTION_END, EventType.MOVE,
                                 EventType.DECISION, EventType.STATUS, EventType.POOL_CHANGE}
            visible_sources = ([actors[actor]] if event_type in actor_only_events and actor in actors
                               else sources)
            witnesses = tuple(fighter.id for fighter in self.fighters
                              if fighter.id == actor
                              or event_type in direct_target_events and fighter.id == target
                              or visible_sources and all(source.id == fighter.id or self.can_see(fighter, source)
                                                         for source in visible_sources))
            audible = {EventType.ACTION_START, EventType.MOVE, EventType.WHIFF, EventType.HIT, EventType.CLASH, EventType.WOUND,
                       EventType.BLOCK, EventType.PARRY, EventType.FALL, EventType.SHOVE,
                       EventType.INCAPACITATED, EventType.SEVER, EventType.EYE_INJURY,
                       EventType.DECAPITATION, EventType.TERMINAL_OUTCOME, EventType.FEAR_BREAK,
                       EventType.FIGHT_END}
            if (event_type in audible or event_type == EventType.STATUS
                    and "MOVEMENT_BLOCKED" in cause_tags) and actor in actors:
                radius = self.config["group"]["hearing_range"]
                sound_sources = [actors[identity] for identity in (actor, target) if identity in actors]
                def source_position(source):
                    return tuple((deltas or {}).get("to", source.pos)) if source.id == actor else source.pos

                hearers = tuple(fighter.id for fighter in self.fighters
                                if any(fighter.id != source.id and
                                       max(abs(fighter.pos[0]-source_position(source)[0]),
                                           abs(fighter.pos[1]-source_position(source)[1])) <= radius
                                       for source in sound_sources))
        event = Event(self.tick, EventType(kind), actor, target,
                      Zone(zone) if zone else None, Severity(severity) if severity else None,
                      deepcopy(deltas or {}), tuple(cause_tags), witnesses, hearers)
        self.event_log.append(event)
        return event

    def to_data(self):
        fighters = [asdict(fighter) for fighter in self.fighters]
        for fighter, serialized in zip(self.fighters, fighters):
            weapon = serialized["equipment"]["weapon"]
            if weapon is not None and not weapon["item_id"]:
                weapon["item_id"] = f"{fighter.id}:weapon"
        return _plain({"schema_version": self.config["schema_version"],
                       "fighters": fighters,
                       "rng_seed": self.rng_seed, "rng_draws": self.rng_draws,
                       "config": deepcopy(self.config), "tick": self.tick,
                       "event_log": [asdict(event) for event in self.event_log],
                       "end_reason": self.end_reason, "winner": self.winner,
                       "walkable": sorted(self.walkable) if self.walkable is not None else None,
                        "exits": sorted(self.exits), "pending_interrupts": self.pending_interrupts[:],
                        "pending_injury_responses": deepcopy(self.pending_injury_responses),
                        "ground_weapons": [asdict(item) for item in self.ground_weapons],
                        "guide_mode": self.guide_mode, "leaders": deepcopy(self.leaders)})

    @classmethod
    def from_data(cls, data):
        values = deepcopy(data)
        version = values.pop("schema_version")
        if version in set(range(1, 27)):
            values["config"] = upgrade_config(values["config"])
            prior_bleed = {
                "TORSO": {"CUT": 3, "DEEP": 6},
                "HEAD": {"CUT": 3, "DEEP": 6},
                "EYE": {"CUT": 3, "DEEP": 6},
                "LEG": {"CUT": 2, "DEEP": 4},
                "ARM": {"CUT": 2, "DEEP": 4},
                "HAND": {"CUT": 2, "DEEP": 4, "SEVERED": 8},
            }
            for fighter in values["fighters"]:
                wound_rows = fighter.get("wounds", [])
                prefight = fighter.get("prefight") or {}
                wound_rows += prefight.get("injuries", [])
                for wound in wound_rows:
                    if wound.get("severity") == Severity.GRAZE.value:
                        wound["bleed_per_turn"] = 0
                        wound.setdefault("initial_blood_loss", 0)
                        continue
                    zone, severity = wound.get("zone"), wound.get("severity")
                    old_rate = prior_bleed.get(zone, {}).get(severity)
                    if (old_rate is not None and wound.get("bleed_per_turn") == old_rate):
                        wound["bleed_per_turn"] = values["config"]["wounds"][zone][severity]["bleed"]
        elif version != load_config()["schema_version"]:
            raise ValueError("Unsupported combat snapshot version")
        elif values["config"].get("schema_version") != version:
            raise ValueError("Combat snapshot/config version mismatch")
        values["fighters"] = [_fighter_from_data(item) for item in values["fighters"]]
        values.setdefault("ground_weapons", [])
        values["ground_weapons"] = [GroundWeapon(Weapon(**item["weapon"]), tuple(item["pos"]))
                                     for item in values["ground_weapons"]]
        for fighter in values["fighters"]:
            if fighter.disarmed and fighter.equipment.weapon is not None:
                values["ground_weapons"].append(GroundWeapon(fighter.equipment.weapon, fighter.pos))
                fighter.equipment.weapon = None
        values["event_log"] = [Event(item["tick"], EventType(item["type"]),
                                       item["actor"], item["target"],
                                       Zone(item["zone"]) if item["zone"] else None,
                                       Severity(item["severity"]) if item["severity"] else None,
                                       item["deltas"], tuple(item["cause_tags"]),
                                       tuple(item.get("witnesses", ())), tuple(item.get("hearers", ())))
                                for item in values["event_log"]]
        if values["walkable"] is not None:
            values["walkable"] = {tuple(pos) for pos in values["walkable"]}
        values["exits"] = {tuple(pos) for pos in values["exits"]}
        state = cls(**values)
        # Older active attacks had only a selected target and could home until
        # impact. Pin their current observed bearing on restore so resumed saves
        # remain deterministic under the new geometry contract.
        from .geometry import bearing_from
        for fighter in state.fighters:
            action = fighter.action
            if action and action.target_id and action.bearing_lock_tick is None:
                target = state.primary_opponent(fighter.id, action.target_id, active_only=False)
                if target is not None:
                    action.attack_bearing = bearing_from(fighter.pos, target.pos)
                    action.bearing_lock_tick = state.tick
        return state
