"""Deterministic, headless combat. UI and AI consume the same immutable intents.

Time is integer ticks. An action's energy cost is converted to a next-ready
deadline using actor speed; windup is real telegraph time, never speed-shortened.
The scheduler orders resolve events before ready events, then stable actor IDs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from copy import deepcopy
from collections import deque
from pathlib import Path
import heapq
import json
import math

from pirates.combat_duel import DuelStats, DuelWeapon

DATA = json.loads((Path(__file__).resolve().parents[1] / "content/combat_arena.json").read_text(encoding="utf-8"))
COMBAT_RULESET_VERSION = 3
PATTERNS = DATA["patterns"]
WEAPONS = DATA["weapons"]
ARCHETYPES = DATA["archetypes"]
PERSONALITIES = DATA["personalities"]
MOVEMENT = DATA["movement"]
STAMINA_PENALTIES = DATA["stamina_penalties"]
BALANCE_RULES = DATA["balance"]
WOUNDS = DATA["wounds"]
MORALE = DATA["morale"]
AI_RULES = DATA["ai"]
PLAYER_DECISION_TICKS = 100


def telegraph_audit():
    """Static fairness gate: every damaging pattern declares a timed counter."""
    rows = {}
    counter_ticks = {"parry": 50, "block": 50, "sidestep": MOVEMENT["sidestep"]["time"],
                     "leave_cone": MOVEMENT["sidestep"]["time"],
                     "break_los": MOVEMENT["sidestep"]["time"],
                     "dash": MOVEMENT["dash"]["time"], "close": MOVEMENT["walk_forward"]["time"]}
    for pattern_id, pattern in PATTERNS.items():
        damaging = pattern["damage"] > 0
        counters = pattern.get("counters", ())
        minimum = min((counter_ticks[counter] for counter in counters if counter in counter_ticks),
                      default=math.inf)
        rows[pattern_id] = {
            "damaging": damaging,
            "windup": pattern["windup"],
            "minimum_counter_ticks": minimum if damaging else 0,
            "counters": list(counters),
            "passes": (bool(counters) and pattern["windup"] >= minimum if damaging
                       else bool(pattern.get("feint") and pattern["damage"] == 0)),
        }
    for weapon in WEAPONS.values():
        for pattern_id in weapon["patterns"]:
            rows.setdefault("weapon_refs", {})[pattern_id] = pattern_id in PATTERNS
    return {"passed": all(row["passes"] for row in rows.values() if isinstance(row, dict) and "passes" in row)
                     and all(rows["weapon_refs"].values()),
            "player_decision_ticks": PLAYER_DECISION_TICKS,
            "patterns": {k:v for k,v in rows.items() if k != "weapon_refs"},
            "weapon_references_valid": rows["weapon_refs"]}


def sign(value):
    return (value > 0) - (value < 0)


def facing(start, end):
    dx, dy = end[0] - start[0], end[1] - start[1]
    return sign(dx), sign(dy)


def distance(start, end):
    return abs(start[0] - end[0]) + abs(start[1] - end[1])


def movement_speed(actor, tick=0):
    """Deterministic movement rate after skill, fatigue, load, and leg wounds."""
    skill = max(0.0, min(100.0, float(actor.movement_skill)))
    fatigue = max(0.0, min(100.0, float(actor.fatigue)))
    capacity = max(1.0, float(actor.carry_capacity_kg))
    load_ratio = max(0.0, float(actor.carried_weight_kg)) / capacity
    skill_factor = 1.0 + skill / 500.0
    fatigue_factor = max(0.5, 1.0 - fatigue / 200.0)
    load_factor = max(0.4, 1.0 - min(1.2, load_ratio) * 0.5)
    wounded_leg = any(actor.statuses.get(status, 0) > tick
                      for status in ("crippled", "crippled_leg"))
    wound_factor = 0.65 if wounded_leg else 1.0
    return max(25, min(200, int(actor.speed * skill_factor * fatigue_factor * load_factor * wound_factor)))


def duel_stats_for_actor(actor):
    """Build the documented melee-duel stat view from current live actor state."""
    structures = actor.body_parts or {}
    return DuelStats(
        speed=max(25, min(200, int(actor.speed))),
        swing_skill=100,
        accuracy=70,
        strength=max(0, min(8, int(actor.strength))),
        stamina=max(0, min(100, int(actor.stamina) * 100 // max(1, int(actor.max_stamina)))),
        max_stamina=100,
        fatigue=max(0, min(100, int(actor.fatigue))),
        balance=max(0, min(100, int(actor.balance))),
        torso_structure=max(0, min(100, int(structures.get("torso", {}).get("structure", 100)))),
        left_arm_structure=max(0, min(100, int(
            structures.get("left_arm", {}).get("structure", 100)))),
        right_arm_structure=max(0, min(100, int(
            structures.get("right_arm", {}).get("structure", 100)))),
        severed_arms=tuple(side for side, part in (
            ("left", "left_arm"), ("right", "right_arm"))
            if part in actor.severed_parts),
    )


def duel_weapon_for_actor(actor):
    """Return a melee weapon's explicit duel record; reject ranged weapons."""
    weapon = WEAPONS.get(actor.weapon)
    if weapon is None or weapon.get("reload") or "duel" not in weapon:
        raise ValueError(f"{actor.weapon} has no close-duel weapon profile")
    values = weapon["duel"]
    return DuelWeapon(
        reach_tiles=int(values["reach_tiles"]),
        handling=int(values["handling"]),
        mass=int(values["mass"]),
    )


def stamina_ratio(actor):
    return max(0.0, float(actor.stamina)) / max(1.0, float(actor.max_stamina))


def step_cost(actor, direction, mode="walk", target=None, tick=0):
    """Return (ticks, stamina) for one directional step, without mutating state."""
    dx, dy = direction
    if abs(dx) + abs(dy) != 1:
        raise ValueError("step_cost requires one cardinal direction")
    if mode == "run":
        movement = MOVEMENT["run"]
    elif mode == "dash":
        movement = MOVEMENT["dash"]
    else:
        toward = (target[0] - actor.x, target[1] - actor.y) if target else actor.facing
        dot = dx * toward[0] + dy * toward[1]
        cross = dx * toward[1] - dy * toward[0]
        target_facing = facing(actor.pos, target) if target else actor.facing
        kind = ("walk_forward" if dot > 0 and cross == 0 else
                "backstep" if target and actor.facing == target_facing
                and dot < 0 and cross == 0 else
                "flee" if dot < 0 and cross == 0 else
                "sidestep" if dot == 0 else "flee")
        movement = MOVEMENT[kind]
    base = movement["time"]
    if stamina_ratio(actor) < STAMINA_PENALTIES["low_threshold"]:
        base *= STAMINA_PENALTIES["step_time_mult"]
    ticks = max(1, math.ceil(base * 100 / movement_speed(actor, tick)))
    return ticks, movement["stamina"]


def counter_step_ticks(actor, tick=0):
    base = MOVEMENT["sidestep"]["time"]
    if stamina_ratio(actor) < STAMINA_PENALTIES["low_threshold"]:
        base *= STAMINA_PENALTIES["step_time_mult"]
    return max(1, math.ceil(base * 100 / movement_speed(actor, tick)))


def canonical_part(part):
    return {"arm": "left_arm", "hand": "left_arm", "leg": "left_leg"}.get(part, part)


def severity(damage_type, quality, part, armor=0, heavy=False):
    """Data-driven wound tier (0 graze .. 3 severe), reduced by armor coverage."""
    table = WOUNDS["severity_base"]
    if damage_type not in table or quality not in table[damage_type]:
        raise ValueError(f"Unknown wound type/quality: {damage_type}/{quality}")
    normalized = canonical_part(part)
    if normalized not in WOUNDS["part_modifier"]:
        raise ValueError(f"Unknown body part: {part}")
    tier = (table[damage_type][quality] + WOUNDS["part_modifier"][normalized]
            + int(bool(heavy)))
    tier -= max(0, int(armor)) * WOUNDS["armor_reduction_per_tier"]
    return max(0, min(3, int(tier)))


def drift_pressure(actor):
    chance = max(0.0, (WOUNDS["drift"]["off_balance_threshold"] - actor.balance)
                 * WOUNDS["drift"]["per_balance_point"])
    if stamina_ratio(actor) < STAMINA_PENALTIES["low_threshold"]:
        chance += STAMINA_PENALTIES.get("aim_drift_add", 0.0)
    return min(0.95, chance)


def deterministic_contact_part(intended_part, attacker, defender, *, drifted):
    """Resolve left/right ambiguity spatially; never draw a random body part."""
    incoming = facing(defender.pos, attacker.pos)
    cross = defender.facing[0] * incoming[1] - defender.facing[1] * incoming[0]
    side = ("left" if cross < 0 else "right" if cross > 0 else
            "left" if attacker.pos < defender.pos else "right")
    if not drifted:
        if intended_part in {"arm", "hand"}:
            return f"{side}_arm"
        if intended_part == "leg":
            return f"{side}_leg"
        return intended_part
    adjacent = {
        "head": ("left_arm", "right_arm"),
        "torso": ("left_arm", "right_arm", "left_leg", "right_leg"),
        "arm": ("left_leg", "right_leg"),
        "leg": ("left_arm", "right_arm"),
        "left_arm": ("torso", "left_leg"),
        "right_arm": ("torso", "right_leg"),
        "left_leg": ("torso", "left_arm"),
        "right_leg": ("torso", "right_arm"),
    }
    choices = adjacent.get(intended_part, ("torso",))
    return choices[0 if side == "left" else -1]


def decisive_contact(actor, victim, pattern, part, quality, tier):
    """Strength/imbalance/damage cross an explicit severe-wound threshold."""
    key = (f"clean_heavy_{part}"
           if quality == "clean" and pattern.get("heavy") and part in {"head", "torso"}
           else None)
    threshold = WOUNDS["decisive_threshold"].get(key) if key else None
    if tier < 3 or threshold is None:
        return False
    momentum = (10 * max(0, actor.strength)
                + max(0, 100 - int(victim.balance)) // 2
                + max(0, int(pattern.get("damage", 0)) - 20))
    return momentum >= threshold


def decisive_is_unconscious(victim):
    """Healthy/balanced victims survive decisive trauma unconscious; weak ones die."""
    blood_percent = max(0.0, min(100.0, victim.blood * 100 / max(1, victim.max_hp)))
    survival_score = (blood_percent + max(0.0, min(100.0, victim.balance))) / 2
    return survival_score >= WOUNDS["decisive_unconscious_threshold"]


def contact_quality(intent, attacker, defender):
    """Classify a committed contact using its frozen geometry and current positions."""
    if defender.pos not in intent.target_tiles:
        return "graze"
    if facing(defender.pos, attacker.pos) != defender.facing:
        return "back"
    if defender.braced:
        return "glancing"
    pattern = PATTERNS[intent.kind]
    if intent.target_origin is not None and defender.pos != intent.target_origin:
        return "graze"
    low, high = pattern.get("sweet_spot", (1, pattern["reach"]))
    gap = distance(attacker.pos, defender.pos)
    if not low <= gap <= high:
        return "graze"
    return "clean"


def ray(start, end):
    x, y = start
    dx, dy = abs(end[0] - x), abs(end[1] - y)
    sx, sy = sign(end[0] - x), sign(end[1] - y)
    error = dx - dy
    result = []
    while (x, y) != end:
        twice = 2 * error
        if twice > -dy:
            error -= dy
            x += sx
        if twice < dx:
            error += dx
            y += sy
        result.append((x, y))
    return result


@dataclass(frozen=True)
class Intent:
    actor_id: str
    kind: str
    target_tiles: tuple[tuple[int, int], ...]
    start_tick: int
    resolves_at_tick: int
    interruptible: bool = True
    target_part: str = "torso"
    attacker_origin: tuple[int, int] | None = None
    target_origin: tuple[int, int] | None = None


@dataclass
class Actor:
    id: str
    name: str
    x: int
    y: int
    weapon: str = "sword"
    secondary_weapon: str | None = None
    personality: str = "patient"
    tactics: str = "standard"
    hp: int = 20
    max_hp: int = 20
    speed: int = 100
    movement_skill: float = 0.0
    stamina: int = 100
    max_stamina: int = 100
    balance: float = 100.0
    fatigue: float = 0.0
    carried_weight_kg: float = 0.0
    carry_capacity_kg: float = 24.0
    movement_mode: str = "walk"
    move_mode: str = "walk"
    facing_locked_to: str | None = None
    injuries: dict[str, int] = field(default_factory=dict)
    blood: float | None = None
    bleed_rate: float = 0.0
    shock: float = 0.0
    body_parts: dict[str, dict] = field(default_factory=dict)
    severed_parts: list[str] = field(default_factory=list)
    unconscious: bool = False
    unconscious_tick: int | None = None
    surrendered: bool = False
    surrendered_tick: int | None = None
    morale: float = 100.0
    movement_path: tuple[tuple[int, int], ...] = ()
    movement_step_ticks: int = 0
    movement_step_base: float = 0.0
    movement_recovery_ticks: int = 0
    perception: int = 10
    strength: int = 0
    armor: int = 0
    faction: str = "enemy"
    glyph: str = "d"
    facing: tuple[int, int] = (-1, 0)
    target_part: str = "torso"
    home_pos: tuple[int, int] | None = None
    last_seen_tick: int = -10000
    behavior: str = "idle"
    bleed_source: str | None = None
    feint_cooldown_until: int = 0
    fled: bool = False
    fled_tick: int | None = None
    retreat_steps: int = 0
    state: str = "ready"
    intent: Intent | None = None
    next_ready_tick: int = 0
    aim_tiles: tuple[tuple[int, int], ...] = ()
    aim_pattern: str | None = None
    statuses: dict[str, int] = field(default_factory=dict)
    ammo: int = 1
    spare_shots: int = 6
    smoke_charges: int = 0
    braced: bool = False
    shield: bool = False
    shield_broken_until: int = 0
    last_seen: tuple[int, int] | None = None
    utilities: dict[str, float] = field(default_factory=dict)
    token: int = 0

    def __post_init__(self):
        if self.blood is None:
            self.blood = float(self.hp)
        if not self.body_parts:
            self.body_parts = {
                part: {"structure": float(WOUNDS["part_structure"]), "damage": 0.0}
                for part in ("head", "torso", "left_arm", "right_arm", "left_leg", "right_leg")
            }

    def sync_legacy_hp(self):
        """Keep pre-wound consumers and saves coherent with the blood pool."""
        self.hp = max(0, min(self.max_hp, math.ceil(float(self.blood or 0.0))))

    @property
    def active(self):
        return self.hp > 0 and not self.unconscious and not self.fled and not self.surrendered

    @property
    def pos(self):
        return self.x, self.y

    @property
    def color(self):
        return {"windup": (255, 210, 70), "recovery": (80, 220, 230),
                "staggered": (100, 120, 255), "dead": (110, 100, 100)}.get(self.state, (225, 100, 85))

    def get(self, key, default=None):
        return "hostile" if key == "role" else getattr(self, key, default)


class Combat:
    def __init__(self, walkable, seed=1, *, doors=None, hazards=None):
        self.walkable = set(walkable)
        self.doors = dict(doors or {})  # position -> open bool
        self.hazards = dict(hazards or {})  # position -> hazard id
        self.smoke: dict[tuple[int, int], int] = {}
        self.seed = seed
        self.tick = 0
        self.actors: dict[str, Actor] = {}
        self.queue = []
        self.log = ["Combat waits for your input. Highlighted tiles are exact threats."]
        self.feedback = []
        self.feedback_serial = 0
        self.replay = []
        self.audit = []
        self.metrics = {"hits": 0, "whiffs": 0, "parries": 0, "punishes": 0,
                        "player_damage": 0, "kills": 0, "feints": 0,
                        "damage_dealt": 0, "damage_taken": 0, "attacks": 0,
                        "fled": 0, "surrenders": 0}
        self.actor_metrics: dict[str, dict[str, int]] = {}
        self.kill_events = []
        self.start_tick = 0
        self.starting_state = []
        self.guided_duel_state = None

    def say(self, text):
        self.log.append(f"[{self.tick}] {text}")
        self.log = self.log[-100:]

    def show_feedback(self, actor, text, event_type, *, source=None, recipient=None,
                      body_part=None, outcome=None, consequences=None):
        """Record a deterministic outcome fact; screen timing/style live in the adapter."""
        self.feedback_serial += 1
        self.feedback.append({"event_id": self.feedback_serial, "actor_id": actor.id,
                              "source_id": (source or actor).id,
                              "recipient_id": recipient.id if recipient else None,
                              "position": list(actor.pos), "text": text,
                              "type": event_type, "body_part": body_part,
                              "outcome": outcome or event_type,
                              "consequences": deepcopy(consequences or {}),
                              "tick": self.tick})
        self.feedback = self.feedback[-200:]

    def add(self, actor):
        if actor.id in self.actors or actor.pos not in self.walkable or self.at(actor.pos):
            raise ValueError("Invalid or occupied combat spawn")
        if not 25 <= actor.speed <= 200 or not 0 <= actor.armor <= 2:
            raise ValueError("Speed must be 25..200 and armor 0..2")
        self.actors[actor.id] = actor
        self.actor_metrics[actor.id] = {"damage_dealt": 0, "damage_taken": 0, "hits": 0,
                                        "whiffs": 0, "punishes": 0, "parries": 0,
                                        "attacks": 0, "kills": 0, "actions": 0}
        if actor.home_pos is None:
            actor.home_pos = actor.pos
        self.starting_state.append(asdict(actor))
        if actor.id != "captain":
            self.schedule(actor, 100)

    def at(self, pos):
        return next((a for a in self.actors.values()
                     if a.hp > 0 and not a.fled and a.pos == pos), None)

    def preview(self, action, target=None, actor_id="captain"):
        """Read-only action preview for UI/input adapters; never mutates simulation or replay."""
        actor = self.actors.get(actor_id)
        if not actor:
            return {"valid": False, "error": "Unknown actor"}
        error = self.blocker(actor, action, target)
        result = {"action": action, "valid": not bool(error), "error": error or None,
                  "start_tick": self.tick, "destination": target,
                  "path": (), "ticks": 0, "stamina_cost": 0,
                  "enemy_acts_first": False, "safe_disengage": None}
        if error:
            return result
        if action in {"move", "run", "dash"}:
            dx, dy = sign(target[0] - actor.x), sign(target[1] - actor.y)
            path = []
            for _ in range(max(abs(target[0] - actor.x), abs(target[1] - actor.y))):
                pos = actor.x + dx * (len(path) + 1), actor.y + dy * (len(path) + 1)
                if not self.open_tile(pos) or self.at(pos):
                    break
                path.append(pos)
            mode = ("run" if action == "run" or
                    (action == "move" and actor.move_mode == "run") else action)
            simulated = deepcopy(actor)
            if mode == "run":
                stamina_cost = MOVEMENT["run"]["stamina"] * len(path)
                simulated.stamina -= stamina_cost
                simulated.fatigue = min(100.0, simulated.fatigue + 8.0 * len(path))
            elif mode == "dash":
                stamina_cost = MOVEMENT["dash"]["stamina"]
                simulated.stamina -= stamina_cost
                simulated.fatigue = min(100.0, simulated.fatigue + 16.0)
            else:
                stamina_cost = -min(2, simulated.max_stamina - simulated.stamina)
                simulated.stamina = min(simulated.max_stamina, simulated.stamina + 2)
                simulated.fatigue = max(0.0, simulated.fatigue - 1.0)
            if mode == "dash":
                step_base = MOVEMENT["dash"]["time"] / max(1, len(path))
            elif mode == "run":
                step_base = MOVEMENT["run"]["time"]
            else:
                locked = self.actors.get(actor.facing_locked_to) if actor.facing_locked_to else None
                toward = (locked.pos if locked and locked.active else
                          next((foe.pos for foe in self.actors.values()
                                if foe.faction != actor.faction and foe.active), None))
                step_base = MOVEMENT["walk_forward"]["time"]
                if path:
                    direction = dx, dy
                    if toward:
                        vx, vy = toward[0] - actor.x, toward[1] - actor.y
                    else:
                        vx, vy = actor.facing
                    dot, cross = direction[0] * vx + direction[1] * vy, direction[0] * vy - direction[1] * vx
                    target_facing = facing(actor.pos, toward) if toward else actor.facing
                    kind = ("walk_forward" if dot > 0 and cross == 0 else
                            "backstep" if dot < 0 and toward and simulated.facing == target_facing and cross == 0 else
                            "flee" if dot < 0 and cross == 0 else
                            "sidestep" if dot == 0 else "flee")
                    step_base = MOVEMENT[kind]["time"]
            per_step = []
            simulated_tick = self.tick
            for _ in path:
                ticks = self._movement_ticks(simulated, step_base)
                per_step.append(ticks)
                simulated_tick += ticks
            total = sum(per_step)
            if action == "dash":
                total += math.ceil(MOVEMENT["dash"]["recovery"] * 100 /
                                   movement_speed(simulated, simulated_tick))
            elif action == "run" and actor.move_mode != "run":
                total += math.ceil(MOVEMENT["run"]["stop_recovery"] * 100 /
                                   movement_speed(simulated, simulated_tick))
            result.update({"destination": path[-1] if path else actor.pos,
                           "path": tuple(path), "step_ticks": tuple(per_step),
                           "ticks": total, "arrival_tick": self.tick + sum(per_step),
                           "stamina_cost": stamina_cost})
            enemies = [foe for foe in self.actors.values()
                       if foe.faction != actor.faction and foe.active]
            if enemies:
                enemy = min(enemies, key=lambda foe: (distance(actor.pos, foe.pos), foe.id))
                ready = enemy.intent.resolves_at_tick if enemy.intent else enemy.next_ready_tick
                marked = (enemy.intent.target_tiles if enemy.intent else
                          self.tiles(enemy, WEAPONS[enemy.weapon]["patterns"][0],
                                     result["destination"]))
                result["enemy_acts_first"] = ready <= result["arrival_tick"]
                result["safe_disengage"] = (result["arrival_tick"] <= ready or
                                               result["destination"] not in marked)
                active_intents = [foe.intent for foe in enemies if foe.intent]
                if active_intents:
                    safe = True
                    for intent in active_intents:
                        if actor.pos not in intent.target_tiles:
                            continue
                        arrival = self.tick
                        escaped = False
                        for tile, ticks in zip(path, per_step):
                            arrival += ticks
                            if arrival >= intent.resolves_at_tick:
                                break  # tied impacts resolve before movement-arrival events
                            if tile not in intent.target_tiles:
                                escaped = True
                                break
                        if not escaped and intent.resolves_at_tick <= arrival:
                            safe = False
                    result["safe_disengage"] = safe
        elif action in PATTERNS:
            pattern = PATTERNS[action]
            result.update({"target_tiles": self.tiles(actor, action, target),
                           "ticks": self.telegraph_windup(actor, pattern),
                           "arrival_tick": self.tick + self.telegraph_windup(actor, pattern),
                           "stamina_cost": pattern.get("stamina", 0),
                           "drift_pressure": drift_pressure(actor)})
        elif action == "fire" and actor.aim_pattern:
            pattern = PATTERNS[actor.aim_pattern]
            ticks = self.telegraph_windup(actor, pattern)
            result.update({"target_tiles": actor.aim_tiles, "ticks": ticks,
                           "arrival_tick": self.tick + ticks,
                           "drift_pressure": drift_pressure(actor)})
        else:
            cost = ({"aim": 50, "brace": 50, "wait": 50, "switch": 50,
                     "reload": WEAPONS[actor.weapon]["reload"], "bandage": 100,
                     "door": 50, "smoke": 80}.get(action, 0))
            if action == "throw":
                cost = PATTERNS["throw"]["cost"] + self.telegraph_windup(actor, PATTERNS["throw"])
            result.update({"ticks": cost, "arrival_tick": self.tick + cost,
                           "target_tiles": self.tiles(actor, WEAPONS[actor.weapon]["patterns"][0], target)
                           if action == "aim" and target else ()})
        return result

    def los(self, start, end):
        return all(p in self.walkable and self.doors.get(p, True)
                   and self.smoke.get(p, 0) <= self.tick for p in ray(start, end))

    def open_tile(self, pos):
        return pos in self.walkable and self.doors.get(pos, True)

    def apply_hazard(self, actor, pos):
        hazard = self.hazards.get(pos)
        if not hazard or actor.hp <= 0:
            return
        damage = {"spikes": 8, "fire": 6}.get(hazard, 0)
        if not damage:
            return
        actual = self.apply_damage(None, actor, damage)
        self.say(f"{actor.name} takes {actual} from {hazard} at {pos}.")
        if hazard == "spikes" and actor.hp:
            actor.statuses["crippled_leg"] = self.tick + 500
        if actor.hp <= 0:
            self.mark_dead(actor, None, f"{hazard} at {pos}")
            self.say(f"{actor.name} dies on {hazard} at {pos}.")

    def apply_damage(self, source, victim, amount):
        # hp is the compatibility surface used by older saves/tests; reconcile
        # direct legacy edits before using the newer fractional blood pool.
        if victim.blood is None or math.ceil(victim.blood) != victim.hp:
            victim.blood = float(victim.hp)
        actual = min(max(0.0, float(amount)), float(victim.hp))
        victim.blood = max(0.0, float(victim.blood) - actual)
        victim.sync_legacy_hp()
        source_id = source.id if isinstance(source, Actor) else "environment"
        if source_id != "environment":
            self.actor_metrics[source_id]["damage_dealt"] += actual
            self.metrics["damage_dealt"] += actual
        self.actor_metrics[victim.id]["damage_taken"] += actual
        self.metrics["damage_taken"] += actual
        if victim.id == "captain":
            self.metrics["player_damage"] += actual
        return actual

    def mark_dead(self, victim, source, cause):
        if victim.state == "dead":
            return False
        victim.blood = 0.0
        victim.hp = 0
        victim.intent = None
        victim.movement_path = ()
        victim.movement_step_ticks = 0
        victim.aim_tiles, victim.aim_pattern = (), None
        victim.token += 1
        victim.state = "dead"
        self.metrics["kills"] += 1
        source_id = source.id if isinstance(source, Actor) else "environment"
        if source_id in self.actor_metrics:
            self.actor_metrics[source_id]["kills"] += 1
        self.kill_events.append({"tick": self.tick, "actor_id": victim.id,
                                 "killer_id": source_id, "cause": cause})
        self._morale_ally_down(victim)
        return True

    def mark_unconscious(self, victim, source=None, cause="shock"):
        if not victim.active:
            return False
        victim.unconscious = True
        victim.unconscious_tick = self.tick
        victim.intent = None
        victim.movement_path = ()
        victim.movement_step_ticks = 0
        victim.aim_tiles, victim.aim_pattern = (), None
        victim.token += 1
        victim.state = "unconscious"
        self.say(f"{victim.name} falls unconscious from {cause}.")
        self._morale_ally_down(victim)
        return True

    def _morale_ally_down(self, downed):
        for ally in self.actors.values():
            if ally.id != downed.id and ally.faction == downed.faction and ally.active:
                self.change_morale(ally, MORALE["ally_down"], "an ally fell")

    def change_morale(self, actor, amount, reason):
        actor.morale = max(0.0, min(100.0, actor.morale - amount))
        personality = PERSONALITIES.get(actor.personality, {})
        threshold = personality.get("break_threshold", 0)
        behavior = personality.get("break_behavior", "fight")
        if actor.morale > threshold or behavior == "fight" or not actor.active:
            return False
        actor.intent = None
        actor.movement_path = ()
        actor.movement_step_ticks = 0
        actor.token += 1
        if behavior == "surrender":
            actor.surrendered = True
            actor.surrendered_tick = self.tick
            actor.state = "surrendered"
            self.metrics["surrenders"] += 1
            self.say(f"{actor.name} surrenders after morale breaks ({reason}).")
        else:
            actor.fled = True
            actor.fled_tick = self.tick
            actor.state = "fled"
            self.metrics["fled"] += 1
            self.say(f"{actor.name} flees after morale breaks ({reason}).")
        return True

    def apply_morale_pressure(self, actor, quality, tier):
        if tier >= 2:
            self.change_morale(actor, MORALE["deep_wound"], "a deep wound")
        if quality == "back":
            self.change_morale(actor, MORALE["back_hit"], "a back hit")
        if actor.hp / max(1, actor.max_hp) <= MORALE["low_blood_ratio"]:
            self.change_morale(actor, MORALE["low_blood"], "low blood")
        flankers = [foe for foe in self.actors.values()
                    if foe.active and foe.faction != actor.faction
                    and distance(foe.pos, actor.pos) == 1]
        if len({facing(actor.pos, foe.pos) for foe in flankers}) >= 2:
            self.change_morale(actor, MORALE["flanked"], "being flanked")

    def schedule(self, actor, cost, delay=0, speed=None):
        actor.token += 1
        actor.next_ready_tick = self.tick + delay + math.ceil(cost * 100 / (speed or actor.speed))
        heapq.heappush(self.queue, (actor.next_ready_tick, 1, actor.id, actor.token))

    def telegraph_windup(self, actor, pattern):
        """Allow one declared counter after readiness, rather than guaranteeing escape."""
        windup = pattern["windup"]
        player = self.actors.get("captain")
        if actor.id != "captain" and player and player.active and pattern["damage"] > 0:
            reaction_delay = max(0, player.next_ready_tick - self.tick)
            counters = pattern.get("counters", ())
            if any(counter in counters for counter in ("parry", "block")):
                response_cost = 50
            elif any(counter in counters for counter in ("sidestep", "leave_cone", "break_los")):
                response_cost = counter_step_ticks(player, self.tick)
            elif "dash" in counters:
                response_cost = max(1, math.ceil(MOVEMENT["dash"]["time"] * 100 /
                                                  movement_speed(player, self.tick)))
            else:
                response_cost = 0
            windup = max(windup, reaction_delay + response_cost + 1)
        if stamina_ratio(actor) < STAMINA_PENALTIES["low_threshold"]:
            windup = math.ceil(windup * STAMINA_PENALTIES["windup_mult"])
        return windup

    def _movement_ticks(self, actor, base_cost):
        if stamina_ratio(actor) < STAMINA_PENALTIES["low_threshold"]:
            base_cost *= STAMINA_PENALTIES["step_time_mult"]
        return max(1, math.ceil(base_cost * 100 / movement_speed(actor, self.tick)))

    def begin_movement(self, actor, action, target):
        """Schedule each traversed tile at its real movement tick, not instantly."""
        dx, dy = sign(target[0] - actor.x), sign(target[1] - actor.y)
        path = []
        for _ in range(max(abs(target[0] - actor.x), abs(target[1] - actor.y))):
            nxt = (actor.x + dx * (len(path) + 1), actor.y + dy * (len(path) + 1))
            if not self.open_tile(nxt) or self.at(nxt):
                break
            path.append(nxt)
        if not path:
            return "Movement path is blocked"

        opponents = [a for a in self.actors.values()
                     if a.faction != actor.faction and a.active]
        target_actor = self.actors.get(actor.facing_locked_to) if actor.facing_locked_to else None
        if target_actor not in opponents:
            target_actor = min(opponents, key=lambda a: (distance(actor.pos, a.pos), a.id), default=None)
        actor.facing_locked_to = target_actor.id if target_actor else None
        combat_target = target_actor.pos if target_actor else None
        direction = sign(target[0] - actor.x), sign(target[1] - actor.y)
        if combat_target and direction != (-sign(combat_target[0] - actor.x),
                                            -sign(combat_target[1] - actor.y)):
            actor.facing = facing(actor.pos, combat_target)
        mode = ("run" if action == "run" or (action == "move" and actor.move_mode == "run")
                else "walk" if action == "move" else action)
        if action == "dash":
            base_step_cost = MOVEMENT["dash"]["time"] / len(path)
            base_step_ticks = self._movement_ticks(actor, base_step_cost)
            recovery = math.ceil(MOVEMENT["dash"]["recovery"] * 100 /
                                 movement_speed(actor, self.tick))
        else:
            base_step_ticks, _ = step_cost(actor, direction, mode, combat_target, self.tick)
            movement_data = MOVEMENT["run"] if mode == "run" else None
            if movement_data:
                base_step_cost = movement_data["time"]
            else:
                towards = ((combat_target[0] - actor.x, combat_target[1] - actor.y)
                           if combat_target else actor.facing)
                dot = direction[0] * towards[0] + direction[1] * towards[1]
                cross = direction[0] * towards[1] - direction[1] * towards[0]
                kind = ("walk_forward" if dot > 0 and cross == 0 else
                        "backstep" if combat_target and dot < 0 and cross == 0 else
                        "flee" if dot < 0 and cross == 0 else
                        "sidestep" if dot == 0 else "flee")
                base_step_cost = MOVEMENT[kind]["time"]
            recovery = (math.ceil(MOVEMENT["run"]["stop_recovery"] * 100 /
                                  movement_speed(actor, self.tick))
                        if action == "run" and actor.move_mode != "run" else 0)
        actor.movement_path = tuple(path)
        actor.movement_mode = mode
        actor.state = "moving"
        actor.movement_recovery_ticks = recovery
        actor.movement_step_base = base_step_cost
        if mode == "walk":
            actor.stamina = min(actor.max_stamina, actor.stamina + 2)
            actor.fatigue = max(0.0, actor.fatigue - 1.0)
            actor.balance = min(100.0, actor.balance + BALANCE_RULES["walk_recovery"])
        elif mode == "run":
            stamina_cost = MOVEMENT["run"]["stamina"] * len(path)
            actor.stamina -= stamina_cost
            actor.fatigue = min(100.0, actor.fatigue + 8.0 * len(path))
        else:
            actor.stamina -= MOVEMENT["dash"]["stamina"]
            actor.fatigue = min(100.0, actor.fatigue + 16.0)
        if stamina_ratio(actor) < STAMINA_PENALTIES["low_threshold"]:
            actor.balance = max(0.0, actor.balance - BALANCE_RULES["low_stamina_loss"])
        actor.movement_step_ticks = self._movement_ticks(actor, base_step_cost)
        actor.token += 1
        first_step_tick = self.tick + actor.movement_step_ticks
        actor.next_ready_tick = first_step_tick + actor.movement_step_ticks * (len(path) - 1)
        actor.next_ready_tick += recovery
        heapq.heappush(self.queue, (first_step_tick, 2, actor.id, actor.token))
        self.say(f"{actor.name} starts {action} ({len(path)} tile(s), {actor.movement_step_ticks} ticks per tile).")
        return ""

    def movement_step(self, actor):
        if actor.state != "moving" or not actor.movement_path:
            return
        destination = actor.movement_path[0]
        if not self.open_tile(destination) or self.at(destination):
            actor.movement_path = ()
            actor.movement_step_ticks = 0
            actor.state = "recovery"
            self.say(f"{actor.name}'s movement is stopped by a blocked tile at {destination}.")
            self.schedule(actor, 0)
            return
        actor.x, actor.y = destination
        actor.movement_path = actor.movement_path[1:]
        self.apply_hazard(actor, destination)
        if actor.hp <= 0:
            return
        if actor.movement_path:
            actor.movement_step_ticks = self._movement_ticks(actor, actor.movement_step_base)
            actor.next_ready_tick = self.tick + actor.movement_step_ticks * len(actor.movement_path)
            heapq.heappush(self.queue, (self.tick + actor.movement_step_ticks, 2,
                                        actor.id, actor.token))
            return
        actor.movement_step_ticks = 0
        actor.state = "recovery"
        self.say(f"{actor.name} finishes {actor.movement_mode} at {actor.pos}.")
        if actor.movement_mode == "dash":
            actor.balance = max(0.0, actor.balance - BALANCE_RULES["dash_recovery_loss"])
        elif actor.movement_mode == "run" and actor.move_mode != "run":
            actor.balance = max(0.0, actor.balance - BALANCE_RULES["run_stop_loss"])
        self.schedule(actor, actor.movement_recovery_ticks)
        actor.movement_recovery_ticks = 0

    def tiles(self, actor, pattern, target):
        p = PATTERNS[pattern]
        if p.get("feint"):
            return ()
        dx, dy = facing(actor.pos, target)
        if not (dx or dy):
            dx, dy = actor.facing
        result = []
        shape = p["shape"]
        reach = p["reach"] + p.get("lunge", 0)
        for n in range(p.get("min_range", 1), reach + 1):
            if shape == "ray":
                end = (actor.x + sign(target[0] - actor.x) * reach,
                       actor.y + sign(target[1] - actor.y) * reach)
                # Extend the exact aim vector, not a retargeting beam.
                length = max(abs(target[0] - actor.x), abs(target[1] - actor.y), 1)
                end = (actor.x + round((target[0] - actor.x) * reach / length),
                       actor.y + round((target[1] - actor.y) * reach / length))
                for tile in ray(actor.pos, end):
                    if tile not in self.walkable:
                        break
                    result.append(tile)
                break
            if shape == "point":
                if max(abs(actor.x - target[0]), abs(actor.y - target[1])) <= p["reach"] and self.los(actor.pos, target):
                    result.append(target)
                break
            center = actor.x + dx * n, actor.y + dy * n
            spread = range(-n, n + 1) if shape == "cone" else range(-1, 2) if shape == "arc" else (0,)
            for side in spread:
                tile = center[0] - dy * side, center[1] + dx * side
                if tile in self.walkable and self.los(actor.pos, tile):
                    result.append(tile)
            if shape == "line":
                occupant = self.at(center)
                if occupant is not None and occupant is not actor:
                    break
        return tuple(dict.fromkeys(result))

    def blocker(self, actor, action, target=None):
        if actor.unconscious:
            return "Actor is unconscious"
        if actor.hp <= 0:
            return "Actor is dead"
        if actor.intent or actor.next_ready_tick > self.tick:
            return "Still committed/recovering"
        if any(actor.statuses.get(s, 0) > self.tick for s in ("paralyzed", "stunned")):
            return "Unable to act until status expires"
        if action in {"move", "run", "dash"}:
            if not isinstance(target, tuple) or len(target) != 2:
                return "Movement needs a destination tile"
            dx, dy = target[0] - actor.x, target[1] - actor.y
            steps = max(abs(dx), abs(dy))
            limit = {"move": 1, "run": 2, "dash": MOVEMENT["dash"]["max_tiles"]}[action]
            if steps < 1 or steps > limit or (dx and dy and abs(dx) != abs(dy)):
                return f"{action.title()} follows a straight 8-way path up to {limit} tile(s)"
            run_toggle_step = action == "move" and actor.move_mode == "run"
            run_cost = MOVEMENT["run"]["stamina"] * (steps if action == "run" else 1)
            if (action == "run" or run_toggle_step) and actor.stamina < run_cost:
                return f"Too exhausted to run (need {run_cost} stamina)"
            if action == "dash" and actor.stamina < 35:
                return "Too exhausted to dash (need 35 stamina)"
            first = (actor.x + sign(dx), actor.y + sign(dy))
            if first in self.doors and not self.doors[first]:
                return "Door is closed: open it first"
            if not self.open_tile(first) or self.at(first):
                return "First movement tile is blocked"
        if action in PATTERNS:
            if PATTERNS[action].get("heavy") and actor.stamina <= MOVEMENT["stamina_lockout"]:
                return "Too exhausted for a heavy attack"
            if action not in WEAPONS[actor.weapon]["patterns"] and action not in {"shove", "throw"}:
                return "Pattern not on this weapon"
            if (WEAPONS[actor.weapon].get("two_handed")
                    and (actor.statuses.get("crippled_arm", 0) > self.tick
                         or any(part in actor.severed_parts for part in ("left_arm", "right_arm")))):
                return "Crippled arm: cannot use a two-handed weapon"
            if WEAPONS[actor.weapon]["reload"] and action not in {"shove", "throw"}:
                return "Aim first, then fire"
            if target is None:
                return "Out of range/cover"
            if PATTERNS[action].get("feint"):
                if distance(actor.pos, target) > PATTERNS[action]["reach"] or not self.los(actor.pos, target):
                    return "Out of range/cover"
            elif target not in self.tiles(actor, action, target):
                return "Out of range/cover"
            if WEAPONS[actor.weapon]["reload"] and action not in {"shove", "throw"} and not actor.ammo:
                return "Empty: reload with T"
            if action == "throw" and actor.statuses.get("bottles", 0) <= 0:
                return "No bottle carried"
        if action == "reload":
            if not WEAPONS[actor.weapon]["reload"]:
                return "This weapon does not reload"
            if actor.ammo:
                return "Already loaded"
            if actor.spare_shots <= 0:
                return "No spare shots"
            if (actor.statuses.get("crippled_arm", 0) > self.tick
                    or any(part in actor.severed_parts for part in ("left_arm", "right_arm"))):
                return "Crippled arm: cannot reload"
        if action == "aim":
            if not WEAPONS[actor.weapon]["reload"]:
                return "This weapon does not aim"
            if (actor.statuses.get("crippled_arm", 0) > self.tick
                    or any(part in actor.severed_parts for part in ("left_arm", "right_arm"))):
                return "Crippled arm: cannot aim"
            if not actor.ammo:
                return "Empty: reload with T"
            pattern = WEAPONS[actor.weapon]["patterns"][0]
            if target is None or target not in self.tiles(actor, pattern, target):
                return "Out of range/cover"
        if action == "fire":
            if not actor.aim_tiles or not actor.aim_pattern:
                return "Aim first with R"
            if not actor.ammo:
                return "Empty: reload with T"
            if actor.statuses.get("crippled_arm", 0) > self.tick:
                return "Crippled arm: cannot fire"
        if action == "door":
            if target not in self.doors or distance(actor.pos, target) != 1:
                return "No adjacent door"
            if self.at(target):
                return "An actor blocks the doorway"
        if action == "smoke":
            if actor.smoke_charges <= 0:
                return "No smoke charges"
            if target not in self.walkable or distance(actor.pos, target) > 4:
                return "Smoke target out of range"
        return ""

    def act(self, actor_id, action, target=None):
        a = self.actors[actor_id]
        if not a.intent and a.next_ready_tick <= self.tick and a.state == "reloading":
            a.ammo = 1
            a.state = "ready"
        reason = self.blocker(a, action, target)
        if reason:
            return reason
        if action == "target_part":
            if target not in {"head", "torso", "arm", "hand", "leg"}:
                return "Unknown body part"
            a.target_part = target
            self.say(f"{a.name} targets {target}.")
            return ""
        if action not in PATTERNS and action not in {"move", "run", "dash", "toggle_run", "brace", "wait", "reload", "bandage", "switch", "aim", "fire", "door", "smoke"}:
            return "Unknown action"
        if action == "switch" and target not in WEAPONS:
            return "Unknown weapon"
        self.actor_metrics[a.id]["actions"] = self.actor_metrics[a.id].get("actions", 0) + 1
        if action not in {"aim", "fire"}:
            a.aim_tiles, a.aim_pattern = (), None
        if action not in {"move", "run", "dash", "wait"}:
            a.movement_mode = "walk"
        a.braced = False
        if isinstance(target, tuple) and action not in {"move", "run", "dash"}:
            a.facing = facing(a.pos, target)
        if action == "toggle_run":
            if a.move_mode == "walk":
                a.move_mode = "run"
                self.say(f"{a.name} starts running; each step costs {MOVEMENT['run']['stamina']} stamina.")
            else:
                a.move_mode = "walk"
                a.balance = max(0.0, a.balance - BALANCE_RULES["run_stop_loss"])
                a.state = "recovery"
                self.say(f"{a.name} stops running, off-balance for {MOVEMENT['run']['stop_recovery']} ticks.")
                self.schedule(a, MOVEMENT["run"]["stop_recovery"])
            return ""
        if action == "aim":
            a.aim_pattern = WEAPONS[a.weapon]["patterns"][0]
            a.aim_tiles = self.tiles(a, a.aim_pattern, target)
            a.state = "aiming"
            self.schedule(a, 50)
            self.say(f"{a.name} aims {WEAPONS[a.weapon]['name']} along {list(a.aim_tiles)}; move to break the line.")
        elif action in PATTERNS or action == "fire":
            pattern = action
            if action == "fire":
                pattern = a.aim_pattern
            p = PATTERNS[pattern]
            windup = self.telegraph_windup(a, p)
            targets = a.aim_tiles if action == "fire" else self.tiles(a, action, target)
            if action == "fire":
                a.aim_tiles, a.aim_pattern = (), None
                a.ammo = 0
            intended_victim = self.at(target) if isinstance(target, tuple) else None
            target_origin = intended_victim.pos if intended_victim and intended_victim is not a else target
            a.intent = Intent(a.id, action, targets, self.tick, self.tick + windup,
                              target_part=a.target_part, attacker_origin=a.pos,
                              target_origin=target_origin)
            if action == "fire":
                a.intent = Intent(a.id, pattern, targets, self.tick, self.tick + windup,
                                  target_part=a.target_part, attacker_origin=a.pos,
                                  target_origin=target_origin)
            a.state = "windup"
            self.metrics["attacks"] += 1
            self.actor_metrics[a.id]["attacks"] += 1
            stop_recovery = MOVEMENT["run"]["stop_recovery"] if a.move_mode == "run" else 0
            if stop_recovery:
                a.move_mode = "walk"
                a.balance = max(0.0, a.balance - BALANCE_RULES["run_stop_loss"])
            self.schedule(a, p["cost"] + stop_recovery, windup + p["recovery"])
            heapq.heappush(self.queue, (a.intent.resolves_at_tick, 0, a.id, a.token))
            if WEAPONS[a.weapon]["reload"] and action not in {"shove", "throw", "fire"}:
                a.ammo = 0
            if action == "throw":
                a.statuses["bottles"] -= 1
            self.say(f"{a.name} prepares {p['name']}; hits marked tiles at tick {a.intent.resolves_at_tick}.")
        elif action in {"move", "run", "dash"}:
            a.aim_tiles, a.aim_pattern = (), None
            return self.begin_movement(a, action, target)
        else:
            a.state = "recovery"
            cost = 50
            if action == "brace":
                a.braced = True
                self.say(f"{a.name} braces facing {a.facing}; holds until next action.")
            elif action == "reload":
                cost = WEAPONS[a.weapon]["reload"]
                # The reload completes at readiness, not at its beginning.
                a.state = "reloading"
                a.spare_shots -= 1
                self.say(f"{a.name} starts reloading ({cost} energy).")
            elif action == "switch":
                if a.secondary_weapon == target:
                    a.secondary_weapon = a.weapon
                a.weapon = target
                self.say(f"{a.name} equips {WEAPONS[target]['name']}.")
            elif action == "bandage":
                cost = 100
                a.statuses.pop("bleeding", None)
                a.bleed_source = None
                a.bleed_rate = 0.0
                self.say(f"{a.name} stops bleeding.")
            elif action == "door":
                self.doors[target] = not self.doors[target]
                self.say(f"{a.name} {'opens' if self.doors[target] else 'closes'} the door at {target}.")
            elif action == "smoke":
                a.smoke_charges -= 1
                for pos in self.walkable:
                    if distance(pos, target) <= 1:
                        self.smoke[pos] = self.tick + 500
                cost = 80
                self.say(f"{a.name} fills the area around {target} with smoke; it lasts 500 ticks.")
            elif action == "wait":
                a.stamina = min(a.max_stamina, a.stamina + 18)
                a.fatigue = max(0.0, a.fatigue - 8.0)
                a.balance = min(100.0, a.balance + BALANCE_RULES["wait_recovery"])
                a.movement_mode = "rest"
                self.say(f"{a.name} catches breath: stamina {a.stamina}/{a.max_stamina}, fatigue {a.fatigue:.0f}.")
            self.schedule(a, cost)
        return ""

    def interrupt(self, actor, duration=100):
        if actor.intent and not actor.intent.interruptible:
            return
        actor.intent = None
        actor.movement_path = ()
        actor.movement_step_ticks = 0
        actor.aim_tiles, actor.aim_pattern = (), None
        actor.state = "staggered"
        self.schedule(actor, duration)

    def resolve(self, actor):
        intent = actor.intent
        actor.intent = None
        actor.state = "recovery"
        p = PATTERNS[intent.kind]
        self.audit.append(asdict(intent))
        if p.get("feint"):
            self.metrics["feints"] += 1
            actor.feint_cooldown_until = self.tick + 500
            self.say(f"{actor.name} reveals a feint; it deals no damage. Hold your response, then punish recovery.")
            self.show_feedback(actor, "FEINT", "feint", outcome="no_damage_feint")
            return
        victims = [v for v in self.actors.values() if v.id != actor.id and v.active
                   and v.pos in intent.target_tiles]
        if not victims:
            self.metrics["whiffs"] += 1
            self.actor_metrics[actor.id]["whiffs"] += 1
            self.say(f"{actor.name}'s {p['name']} whiffs; committed tiles were empty. Recovery until {actor.next_ready_tick}.")
            self.show_feedback(actor, "WHIFF", "miss", outcome="empty_committed_tiles")
        for victim in sorted(victims, key=lambda v: v.id):
            # Cover placed/closed after commitment still stops a projectile.
            if not self.los(actor.pos, victim.pos):
                self.metrics["whiffs"] += 1
                self.actor_metrics[actor.id]["whiffs"] += 1
                self.say(f"Cover stops {actor.name}'s {p['name']}.")
                self.show_feedback(actor, "COVER BLOCK", "cover_block", source=actor,
                                   recipient=victim, outcome="blocked_by_cover")
                continue
            frontal = facing(victim.pos, actor.pos) == victim.facing
            if victim.braced and frontal and p["parryable"]:
                victim.braced = False
                self.interrupt(actor, 150)
                self.metrics["parries"] += 1
                self.actor_metrics[victim.id]["parries"] += 1
                self.say(f"{victim.name} parries {p['name']}; {actor.name} staggered for 150 energy.")
                self.show_feedback(victim, "PARRY", "parry", source=victim,
                                   recipient=actor, outcome="parry")
                self.show_feedback(actor, "STAGGERED", "stagger", source=victim,
                                   recipient=actor, outcome="staggered")
                break
            if victim.shield and self.tick >= victim.shield_broken_until and frontal and intent.kind != "shove":
                self.metrics["whiffs"] += 1
                self.actor_metrics[actor.id]["whiffs"] += 1
                self.say(f"{victim.name}'s shield blocks the frontal hit; flank or shove.")
                self.show_feedback(victim, "BLOCK", "block", source=actor,
                                   recipient=victim, outcome="shield_block")
                self.show_feedback(actor, "BLOCKED", "blocked", source=victim,
                                   recipient=actor, outcome="attack_blocked")
                continue
            was_recovering = victim.state in {"recovery", "staggered", "reloading"}
            quality = contact_quality(intent, actor, victim)
            intended_part = intent.target_part
            drift = drift_pressure(actor)
            # Drift is deterministic pressure now: it displaces aim only when
            # poor balance/low stamina crosses the documented 0.50 threshold.
            part = deterministic_contact_part(
                intended_part, actor, victim, drifted=drift >= 0.50)
            tier = severity(p.get("damage_type", "edge"), quality, part,
                            victim.armor, bool(p.get("heavy")))
            decisive = decisive_contact(actor, victim, p, part, quality, tier)
            if tier >= 3 and not decisive:
                tier = 2  # a non-decisive severe head/torso contact is a deep wound
            blood_loss = WOUNDS["blood_loss_by_tier"][tier]
            if tier:
                blood_loss += max(0, actor.strength)
            shock = WOUNDS["shock_by_tier"][tier]
            if part == "head" and tier:
                shock += 20
            victim.shock = min(200.0, victim.shock + shock)
            part_state = victim.body_parts.setdefault(
                part, {"structure": float(WOUNDS["part_structure"]), "damage": 0.0})
            part_state["damage"] = min(part_state["structure"],
                                        part_state["damage"] + tier * 20 + (1 if tier == 0 else 0))
            part_state["structure"] = max(0.0, part_state["structure"] - tier * 20)
            # A clean edge hit that fully destroys an arm's structure severs it.
            # This contact and its body effects are a pure stat/timing result.
            severed = (part.endswith("_arm") and p.get("damage_type", "edge") == "edge"
                       and quality == "clean" and tier >= 1)
            if severed:
                if part not in victim.severed_parts:
                    victim.severed_parts.append(part)
            damage = self.apply_damage(actor, victim, blood_loss)
            victim.injuries[intended_part] = max(victim.injuries.get(intended_part, -1), tier)
            self.metrics["hits"] += 1
            self.actor_metrics[actor.id]["hits"] += 1
            self.metrics["punishes"] += int(was_recovering)
            self.actor_metrics[actor.id]["punishes"] += int(was_recovering)
            status = p.get("status")
            bleed_kind = ("graze" if tier == 0 else "cut" if tier == 1 else
                          "deep" if tier == 2 else "severe")
            victim.statuses["bleeding"] = max(victim.statuses.get("bleeding", 0), self.tick + 600)
            victim.bleed_source = actor.id
            victim.bleed_rate = max(victim.bleed_rate, WOUNDS["bleed_per_tick"][bleed_kind])
            if intent.kind == "shove":
                victim.shield_broken_until = self.tick + 600
                victim.balance = max(0.0, victim.balance - BALANCE_RULES["shove_loss"])
                direction = facing(actor.pos, victim.pos)
                dest = victim.x + direction[0], victim.y + direction[1]
                if self.open_tile(dest) and not self.at(dest):
                    victim.x, victim.y = dest
                    self.apply_hazard(victim, dest)
            if decisive and victim.hp > 0:
                if decisive_is_unconscious(victim):
                    self.mark_unconscious(victim, actor, f"severe {quality} wound")
                else:
                    self.mark_dead(victim, actor, f"severe {quality} wound")
            if victim.hp <= 0:
                self.mark_dead(victim, actor, p["name"])
                self.say(f"{victim.name} killed by {actor.name}'s {p['name']} to the {part} ({quality}, severity {tier}; resolved {self.tick}).")
                consequence = {"severity": tier, "blood_loss": damage, "shock": shock,
                               "body_part": part}
                self.show_feedback(actor, "KILL", "kill", source=actor,
                                   recipient=victim, body_part=part, outcome="kill",
                                   consequences=consequence)
                self.show_feedback(victim, "DOWN", "down", source=actor,
                                   recipient=victim, body_part=part, outcome="killed",
                                   consequences=consequence)
                continue
            if victim.unconscious:
                continue
            self.apply_morale_pressure(victim, quality, tier)
            if not victim.active:
                continue
            outcome = "SEVERS" if severed else "hits"
            self.say(f"{actor.name}'s {p['name']} {quality} {outcome} {victim.name}'s {part}: severity {tier}, blood loss {damage:g}, shock {shock:g}.")
            consequences = {"severity": tier, "blood_loss": damage, "shock": shock,
                            "quality": quality, "severed": severed}
            self.show_feedback(actor, "CLEAN HIT" if quality == "clean" else "HIT", "hit",
                               source=actor, recipient=victim, body_part=part,
                               outcome=quality, consequences=consequences)
            self.show_feedback(victim, f"{'ARM SEVERED' if severed else 'HIT · ' + part.upper()}",
                               "sever" if severed else "wound", source=actor,
                               recipient=victim, body_part=part,
                               outcome="severed" if severed else "wounded",
                               consequences=consequences)
            if status == "paralyzed" and not was_recovering:
                status = "staggered"  # Paralysis requires a punished opening.
            if status and tier >= 1:
                victim.statuses[status] = self.tick + (400 if status == "bleeding" else 200)
            if part.endswith("_arm") and tier >= 2:
                victim.statuses["crippled_arm"] = self.tick + 500
            elif part.endswith("_leg") and tier >= 2:
                victim.statuses["crippled_leg"] = self.tick + 500
            if victim.shock >= WOUNDS["shock_unconscious"]:
                self.mark_unconscious(victim, actor, "shock"); continue
            if status in {"staggered", "paralyzed", "stunned"} or tier >= 2:
                self.interrupt(victim, 200 if status == "paralyzed" else 100)

    def advance_time(self, tick):
        self.smoke = {pos: until for pos, until in self.smoke.items() if until > tick}
        for actor in self.actors.values():
            until = actor.statuses.get("bleeding", 0)
            elapsed = max(0, min(tick, until) - self.tick) if until > self.tick else 0
            amount = (actor.bleed_rate * elapsed / 100.0 if actor.bleed_rate else
                      max(0, min(tick, until) // 100 - self.tick // 100)
                      if until > self.tick else 0)
            if amount and actor.hp > 0:
                source = self.actors.get(actor.bleed_source) if actor.bleed_source else None
                actual = self.apply_damage(source, actor, amount)
                actor.shock = min(200.0, actor.shock + actual * WOUNDS["shock_per_blood_lost"])
                self.say(f"{actor.name} bleeds for {amount:g}.")
                if actor.hp == 0:
                    self.mark_dead(actor, source, "bleeding")
                    self.say(f"{actor.name} dies from untreated bleeding.")
                elif actor.shock >= WOUNDS["shock_unconscious"]:
                    self.mark_unconscious(actor, source, "blood loss shock")
        self.tick = tick

    def run_until_player(self, single_event=False):
        """Never run AI while the captain is ready and waiting for input."""
        player = self.actors.get("captain")
        processed = 0
        while self.queue:
            if player and (not player.active or (not player.intent and player.next_ready_tick <= self.tick
                                              and self.queue[0][0] > self.tick)):
                if player.active:
                    if player.state == "reloading":
                        player.ammo = 1
                    player.state = "aiming" if player.aim_tiles else "ready"
                break
            tick, kind, aid, token = heapq.heappop(self.queue)
            a = self.actors[aid]
            if token != a.token or not a.active:
                continue
            self.advance_time(tick)
            if not a.active:
                continue
            if kind == 0 and a.intent:
                self.resolve(a)
            elif kind == 1:
                if a.state == "reloading":
                    a.ammo = 1
                a.state = "aiming" if a.aim_tiles else "ready"
                if a.id != "captain":
                    self.ai(a)
            elif kind == 2:
                self.movement_step(a)
            processed += 1
            if single_event or processed > 10000:
                break
        return processed

    def command(self, action, target=None, single_event=False):
        error = self.act("captain", action, target)
        if error:
            self.say(error + "; no time spent.")
            return False
        player = self.actors.get("captain")
        self.replay.append({"tick": self.tick, "action": action, "target": target,
                            "origin": player.pos if player else None})
        if action == "target_part":
            return True
        self.run_until_player(single_event)
        return True

    def step_events(self, count=1):
        """Advance a bounded number of scheduled events, stopping at player-ready."""
        processed = 0
        player = self.actors.get("captain")
        while processed < max(1, count):
            if player and (not player.active or (not player.intent and player.next_ready_tick <= self.tick
                                              and self.queue and self.queue[0][0] > self.tick)):
                break
            advanced = self.run_until_player(single_event=True)
            if not advanced:
                break
            processed += advanced
        return processed

    def advance_debug_ticks(self, ticks=1):
        """Advance at most N global ticks, resolving due events and stopping at player-ready."""
        ticks = max(1, int(ticks))
        player = self.actors.get("captain")
        if (player and player.active and not player.intent and player.next_ready_tick <= self.tick
                and (not self.queue or self.queue[0][0] > self.tick)):
            return 0
        end_tick = self.tick + ticks
        processed = 0
        while self.queue and self.queue[0][0] <= end_tick:
            if (player and player.active and not player.intent and player.next_ready_tick <= self.tick
                    and self.queue[0][0] > self.tick):
                break
            tick, kind, aid, token = heapq.heappop(self.queue)
            actor = self.actors[aid]
            if token != actor.token or not actor.active:
                continue
            self.advance_time(tick)
            if not actor.active:
                continue
            if kind == 0 and actor.intent:
                self.resolve(actor)
            elif kind == 1:
                if actor.state == "reloading":
                    actor.ammo = 1
                actor.state = "aiming" if actor.aim_tiles else "ready"
                if actor.id != "captain":
                    self.ai(actor)
            elif kind == 2:
                self.movement_step(actor)
            processed += 1
            if player and player.active and not player.intent and player.next_ready_tick <= self.tick:
                # Keep deterministic same-tick ordering, then stop before later events.
                if not self.queue or self.queue[0][0] > self.tick:
                    return processed
        if self.tick < end_tick:
            self.advance_time(end_tick)
        return processed

    def step_toward(self, actor, target):
        queue = deque([(actor.pos, None)])
        seen = {actor.pos}
        while queue:
            pos, first = queue.popleft()
            if distance(pos, target) == 1:
                return first
            for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
                nxt = pos[0] + dx, pos[1] + dy
                if nxt in seen or nxt not in self.walkable or self.at(nxt):
                    continue
                seen.add(nxt)
                queue.append((nxt, nxt if first is None else first))
        return None

    def path_toward(self, actor, target):
        """Stable lowest-risk cardinal route ending adjacent to an occupied target."""
        queue = [(0, actor.pos)]
        previous = {actor.pos: None}
        cost_to = {actor.pos: 0}
        goal = None
        while queue:
            cost, pos = heapq.heappop(queue)
            if cost != cost_to[pos]:
                continue
            if distance(pos, target) == 1:
                goal = pos
                break
            for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
                nxt = pos[0] + dx, pos[1] + dy
                occupant = self.at(nxt)
                if (nxt not in self.walkable or not self.doors.get(nxt, True)
                        or (occupant and occupant.id != actor.id)):
                    continue
                hazard_cost = {"spikes": 100, "fire": 80}.get(self.hazards.get(nxt), 0)
                new_cost = cost + 10 + hazard_cost
                if new_cost >= cost_to.get(nxt, math.inf):
                    continue
                previous[nxt] = pos
                cost_to[nxt] = new_cost
                heapq.heappush(queue, (new_cost, nxt))
        if goal is None:
            return ()
        path = []
        while goal != actor.pos:
            path.append(goal)
            goal = previous[goal]
        return tuple(reversed(path))

    def straight_stride(self, actor, path, steps):
        """Only advertise a run/dash when the planned route matches its cardinal input."""
        if len(path) < steps:
            return None
        delta = path[0][0] - actor.x, path[0][1] - actor.y
        if abs(delta[0]) + abs(delta[1]) != 1:
            return None
        for index in range(1, steps):
            if (path[index][0] - path[index - 1][0],
                    path[index][1] - path[index - 1][1]) != delta:
                return None
        return path[steps - 1]

    def step_to(self, actor, target):
        """Return the first cardinal step on a stable shortest route to a tile."""
        queue = deque([(actor.pos, None)])
        seen = {actor.pos}
        while queue:
            pos, first = queue.popleft()
            if pos == target:
                return first
            for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
                nxt = pos[0] + dx, pos[1] + dy
                if nxt in seen or nxt not in self.walkable or self.at(nxt):
                    continue
                seen.add(nxt)
                queue.append((nxt, nxt if first is None else first))
        return None

    def flank_tile(self, actor, target, allies):
        if actor.tactics != "flanker" and (len(allies) < 2 or PERSONALITIES[actor.personality]["cunning"] < .75):
            return None
        fx, fy = target.facing
        options = [((target.x - fx, target.y - fy), 60),
                   ((target.x + fy, target.y - fx), 40),
                   ((target.x - fy, target.y + fx), 40)]
        scored = []
        for pos, base in options:
            occupant = self.at(pos)
            if not self.open_tile(pos) or (occupant and occupant.id != actor.id):
                continue
            if any(a.id != actor.id and a.pos == pos for a in allies):
                continue
            distance_cost = distance(actor.pos, pos) * 4
            scored.append((base - distance_cost, pos))
        return max(scored, default=(0, None), key=lambda row: (row[0], str(row[1])))[1]

    def ai(self, actor):
        foes = [a for a in self.actors.values() if a.faction != actor.faction and a.active]
        visible = [a for a in foes if distance(actor.pos, a.pos) <= actor.perception and self.los(actor.pos, a.pos)]
        target = min(visible, key=lambda a: (distance(actor.pos, a.pos), a.id)) if visible else None
        if target:
            actor.last_seen = target.pos
            actor.last_seen_tick = self.tick
        elif self.tick - actor.last_seen_tick > AI_RULES["chase_memory_ticks"]:
            actor.last_seen = None
        if not actor.last_seen:
            self.act(actor.id, "wait")
            return
        personality = PERSONALITIES[actor.personality]
        def terrain_risk(path):
            penalty = 0.0
            for tile in path:
                hazard = self.hazards.get(tile)
                penalty += {"spikes": 85.0, "fire": 65.0}.get(hazard, 0.0)
            return penalty

        allies = [a for a in self.actors.values() if a.faction == actor.faction and a.active]
        threatened = any(foe.intent and actor.pos in foe.intent.target_tiles for foe in foes)
        recent = [row["action"] for row in self.replay[-8:]]
        retreat_habit = sum(action in {"move", "run", "dash"} for action in recent) >= 3
        brace_habit = recent.count("brace") >= 2
        attack_habit = sum(action in PATTERNS or action in {"fire", "attack"}
                           for action in recent) >= 3
        low_hp = actor.hp <= max(6, actor.max_hp * .35)
        outnumbered = len(foes) > len(allies)
        suppressed = actor.statuses.get("suppressed", 0) > self.tick
        fearful = low_hp or outnumbered or suppressed
        if low_hp and PERSONALITIES[actor.personality]["caution"] >= .6 and self.walkable:
            min_x = min(x for x, _ in self.walkable)
            max_x = max(x for x, _ in self.walkable)
            min_y = min(y for _, y in self.walkable)
            max_y = max(y for _, y in self.walkable)
            if actor.x in {min_x, max_x} or actor.y in {min_y, max_y}:
                actor.fled = True
                actor.fled_tick = self.tick
                actor.state = "fled"
                actor.intent = None
                actor.aim_tiles, actor.aim_pattern = (), None
                actor.token += 1
                self.metrics["fled"] = self.metrics.get("fled", 0) + 1
                self.say(f"{actor.name} escapes the arena at {actor.pos} while badly wounded.")
                return
        # Stable utility ranking; no randomness in resolution or decision ties.
        candidates = [(1.0, "wait", None)]
        if actor.statuses.get("bleeding", 0) > self.tick:
            candidates.append((90.0, "bandage", None))
        if target:
            actor.facing = facing(actor.pos, target.pos)
            attack_patterns = [pattern for pattern in WEAPONS[actor.weapon]["patterns"]
                               if not PATTERNS[pattern].get("feint")
                               and not WEAPONS[actor.weapon]["reload"]]
            attackable_now = any(not self.blocker(actor, pattern, target.pos)
                                 for pattern in attack_patterns)
            if actor.secondary_weapon:
                wants_ranged = distance(actor.pos, target.pos) > 2
                if bool(WEAPONS[actor.secondary_weapon]["reload"]) == wants_ranged:
                    candidates.append((78.0, "switch", actor.secondary_weapon))
            for pattern in WEAPONS[actor.weapon]["patterns"]:
                if (not PATTERNS[pattern].get("feint")
                        and not WEAPONS[actor.weapon]["reload"]
                        and not self.blocker(actor, pattern, target.pos)):
                    p = PATTERNS[pattern]
                    utility = 30 + 15 * personality["aggression"] + p["damage"] * personality["recklessness"]
                    if target.state in {"recovery", "staggered", "reloading"}:
                        utility += 35 + 20 * personality["patience"]
                    can_evade_before_hit = target.next_ready_tick <= self.tick + p["windup"]
                    if can_evade_before_hit and p["windup"]:
                        utility -= (20 * personality["caution"] + 10 * personality["patience"])
                    if target.braced and p["parryable"]:
                        utility -= 25 * personality["caution"]
                    utility -= 10 * personality["caution"] if p["windup"] > 100 else 0
                    if threatened:
                        utility -= 80
                    candidates.append((utility, pattern, target.pos))
            if (actor.weapon == "sword" and (target.braced or brace_habit)
                    and self.tick >= actor.feint_cooldown_until
                    and not self.blocker(actor, "feint", target.pos)):
                feint_utility = 74 + 20 * personality["cunning"] - 15 * personality["recklessness"]
                if brace_habit and not target.braced:
                    feint_utility -= 12
                candidates.append((feint_utility, "feint", target.pos))
            if WEAPONS[actor.weapon]["reload"]:
                if actor.aim_tiles and not self.blocker(actor, "fire"):
                    candidates.append((78 if target.pos in actor.aim_tiles else 42 - 20 * personality["caution"], "fire", None))
                elif not actor.aim_tiles and not self.blocker(actor, "aim", target.pos):
                    aim_utility = 68 + 10 * personality["patience"]
                    if distance(actor.pos, target.pos) <= 2:
                        aim_utility -= 30
                    candidates.append((aim_utility, "aim", target.pos))
            if not self.blocker(actor, "reload"):
                candidates.append((60, "reload", None))
            incoming = [a for a in foes if a.intent and actor.pos in a.intent.target_tiles
                        and PATTERNS[a.intent.kind].get("parryable")]
            if incoming:
                candidates.append((35 + 25 * personality["caution"], "brace", incoming[0].pos))
            for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
                tile = actor.x + dx, actor.y + dy
                if tile not in self.walkable or self.at(tile):
                    continue
                if tile in self.doors and not self.doors[tile]:
                    continue
                move_preview = self.preview("move", tile, actor.id)
                safe = move_preview.get("safe_disengage", True)
                utility = 0.0
                utility -= terrain_risk((tile,))
                if threatened and safe:
                    utility = 45 + 30 * personality["caution"]
                elif threatened:
                    utility -= 70
                if (actor.hp <= 8 or actor.statuses.get("suppressed", 0) > self.tick) and safe:
                    utility += 20 * personality["caution"] if distance(tile, target.pos) > distance(actor.pos, target.pos) else 0
                distance_gain = distance(tile, target.pos) - distance(actor.pos, target.pos)
                if fearful and distance_gain > 0:
                    utility += (45 * personality["caution"] + 35 * (1 - personality["nerve"])
                                + (20 if outnumbered else 0) + (15 if low_hp else 0))
                elif fearful and distance_gain < 0:
                    utility -= 20 * personality["caution"] + 20 * (1 - personality["nerve"])
                if WEAPONS[actor.weapon]["reload"] and distance(actor.pos, target.pos) < 3:
                    utility += 20 if distance(tile, target.pos) > distance(actor.pos, target.pos) else 0
                # Reposition into usable distance/line when a direct attack is
                # unavailable (e.g. spear minimum range or a diagonal line).
                old_x, old_y, old_facing = actor.x, actor.y, actor.facing
                actor.x, actor.y = tile
                actor.facing = facing(tile, target.pos)
                attackable_after = any(
                    distance(tile, target.pos) >= PATTERNS[pattern].get("min_range", 1)
                    and target.pos in self.tiles(actor, pattern, target.pos)
                    for pattern in attack_patterns
                )
                actor.x, actor.y, actor.facing = old_x, old_y, old_facing
                if attackable_after and not attackable_now:
                    utility += 90
                home = actor.home_pos or actor.pos
                if distance(tile, home) > 6:
                    utility -= (distance(tile, home) - 6) * 5 * personality["discipline"]
                candidates.append((utility, "move", tile))
            if threatened:
                for mode, max_steps, required_stamina, max_fatigue in (
                        ("run", 2, MOVEMENT["run"]["stamina"] * 2, 79),
                        ("dash", 3, MOVEMENT["dash"]["stamina"], 69)):
                    if actor.stamina < required_stamina or actor.fatigue > max_fatigue:
                        continue
                    for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
                        dest = actor.x + dx * max_steps, actor.y + dy * max_steps
                        preview = self.preview(mode, dest, actor.id)
                        if not preview["valid"] or not preview.get("safe_disengage"):
                            continue
                        path_risk = terrain_risk(preview.get("path", ()))
                        utility = (68 + 25 * personality["caution"]
                                   + 12 * (1 - personality["nerve"])
                                   - preview["ticks"] / 12
                                   - actor.fatigue * (0.22 if mode == "run" else 0.35)
                                   - path_risk)
                        candidates.append((utility, mode, dest))
            flank = self.flank_tile(actor, target, allies)
            flank_step = self.step_to(actor, flank) if flank else None
            if flank_step and (target.shield or target.braced or not attackable_now):
                utility = 50 + 12 * personality["cunning"] + 8 * personality["discipline"]
                candidates.append((utility, "move", flank_step))
            if target and not fearful:
                path = self.path_toward(actor, target.pos)
                aggression = personality["aggression"]
                run_destination = self.straight_stride(actor, path, 2)
                dash_destination = self.straight_stride(actor, path, 3)
                if (run_destination and actor.stamina >= 20
                        and stamina_ratio(actor) >= STAMINA_PENALTIES["low_threshold"]
                        and actor.fatigue < 80
                        and (distance(actor.pos, target.pos) >= 4
                             or retreat_habit and distance(actor.pos, target.pos) >= 2)):
                    run_path = path[:2]
                    run_preview = self.preview("run", run_destination, actor.id)
                    fatigue_cost = actor.fatigue * 0.25 + (20 if actor.statuses.get("crippled_leg", 0) > self.tick else 0)
                    run_utility = (25 + 25 * aggression - 10 * personality["caution"]
                                   - fatigue_cost - terrain_risk(run_path)
                                   - (run_preview["ticks"] / 40 if run_preview["valid"] else 100))
                    if threatened and not run_preview.get("safe_disengage"):
                        run_utility -= 80
                    if retreat_habit:
                        run_utility += 16 + 10 * personality["cunning"]
                    if attack_habit:
                        run_utility -= 8 * personality["recklessness"]
                    candidates.append((run_utility, "run", run_destination))
                if (dash_destination and actor.stamina >= 50 and stamina_ratio(actor) >= .5
                        and actor.fatigue < 70
                        and (aggression >= .8 or retreat_habit)
                        and (distance(actor.pos, target.pos) >= 4
                             or retreat_habit and distance(actor.pos, target.pos) >= 2)):
                    dash_utility = (25 + 40 * aggression - 10 * personality["caution"]
                                    - actor.fatigue * 0.4
                                    - (30 if actor.statuses.get("crippled_leg", 0) > self.tick else 0)
                                    - terrain_risk(path[:3]))
                    dash_preview = self.preview("dash", dash_destination, actor.id)
                    if threatened and not dash_preview.get("safe_disengage"):
                        dash_utility -= 80
                    if retreat_habit:
                        dash_utility += 22 + 12 * personality["cunning"]
                    candidates.append((dash_utility, "dash", dash_destination))
        step = self.step_toward(actor, actor.last_seen)
        if step:
            if step in self.doors and not self.doors[step] and distance(actor.pos, step) == 1:
                candidates.append((55 + 10 * personality["aggression"], "door", step))
            else:
                candidates.append((15 + 10 * personality["aggression"] - terrain_risk((step,)), "move", step))
                safer_path = self.path_toward(actor, actor.last_seen)
                if terrain_risk((step,)) > 0 and safer_path and safer_path[0] != step:
                    candidates.append((38 + 20 * personality["caution"], "move", safer_path[0]))
            if target is None:
                search_path = self.path_toward(actor, actor.last_seen)
                if (len(search_path) >= 2 and actor.stamina >= MOVEMENT["run"]["stamina"] * 2
                        and stamina_ratio(actor) >= AI_RULES["sprint_stamina_ratio"]
                        and actor.fatigue <= AI_RULES["max_run_fatigue"]):
                    run_dest = self.straight_stride(actor, search_path, 2)
                    if run_dest:
                        preview = self.preview("run", run_dest, actor.id)
                        utility = (42 + 18 * personality["aggression"] + 10 * personality["cunning"]
                                   - actor.fatigue * 0.2 - terrain_risk(search_path[:2])
                                   - (preview["ticks"] / 50 if preview["valid"] else 100))
                        candidates.append((utility, "run", run_dest))
                if (len(search_path) >= 3 and actor.stamina >= MOVEMENT["dash"]["stamina"]
                        and stamina_ratio(actor) >= .5
                        and actor.fatigue <= AI_RULES["max_dash_fatigue"]):
                    dash_dest = self.straight_stride(actor, search_path, 3)
                    if dash_dest:
                        preview = self.preview("dash", dash_dest, actor.id)
                        utility = (40 + 22 * personality["aggression"] + 8 * personality["cunning"]
                                   - actor.fatigue * 0.3 - terrain_risk(search_path[:3])
                                   - (preview["ticks"] / 50 if preview["valid"] else 100))
                        candidates.append((utility, "dash", dash_dest))
        threatened_now = any(a.intent and actor.pos in a.intent.target_tiles for a in foes)
        if not threatened_now and (actor.stamina < 35 or actor.fatigue >= 70):
            recovery_utility = 45.0 + 10 * personality["caution"]
            recovery_utility += 10 if actor.fatigue >= 70 else 0
            candidates.append((recovery_utility, "wait", None))
        actor.utilities = {f"{act}:{pos}": round(score, 2) for score, act, pos in candidates}
        best_utility = max(candidate[0] for candidate in candidates)
        near_best = sorted((candidate for candidate in candidates
                            if best_utility - candidate[0] <= 4.0),
                           key=lambda c: (c[1], str(c[2])))
        selected = near_best[0]
        _, action, pos = selected
        actor.behavior = "fleeing" if fearful and action == "move" else "engaging" if target else "searching"
        retreating = bool(fearful and action == "move")
        if retreating and actor.retreat_steps >= 7:
            actor.fled = True
            actor.fled_tick = self.tick
            actor.state = "fled"
            actor.intent = None
            actor.aim_tiles, actor.aim_pattern = (), None
            actor.token += 1
            self.metrics["fled"] = self.metrics.get("fled", 0) + 1
            self.say(f"{actor.name} withdraws from the fight after {actor.retreat_steps + 1} retreating steps.")
            return
        if self.act(actor.id, action, pos):
            self.act(actor.id, "wait")
        elif retreating:
            actor.retreat_steps += 1
        elif not fearful:
            actor.retreat_steps = 0

    def snapshot(self):
        return {"tick": self.tick, "actors": [asdict(self.actors[k]) for k in sorted(self.actors)],
                "guided_duel": deepcopy(self.guided_duel_state),
                "doors": [[list(pos), opened] for pos, opened in sorted(self.doors.items())],
                "hazards": [[list(pos), hazard] for pos, hazard in sorted(self.hazards.items())],
                "smoke": [[list(pos), until] for pos, until in sorted(self.smoke.items())],
                "metrics": dict(self.metrics), "actor_metrics": deepcopy(self.actor_metrics),
                "kill_events": deepcopy(self.kill_events), "audit": deepcopy(self.audit),
                "log": list(self.log), "feedback": deepcopy(self.feedback),
                "feedback_serial": self.feedback_serial}

    def report(self, flawless_possible=None):
        captain = self.actors.get("captain")
        enemies = [a for a in self.actors.values() if captain and a.faction != captain.faction]
        won = bool(captain and enemies and captain.active and all(not a.active for a in enemies))
        enemy_ids = {a.id for a in enemies}
        kill_ticks = [event["tick"] for event in self.kill_events if event["actor_id"] in enemy_ids]
        resolved_ticks = (kill_ticks + [a.fled_tick for a in enemies if a.fled and a.fled_tick is not None]
                          + [a.surrendered_tick for a in enemies if a.surrendered
                             and a.surrendered_tick is not None]
                          + [a.unconscious_tick for a in enemies if a.unconscious
                             and a.unconscious_tick is not None])
        return {**self.metrics, "combat_ruleset_version": COMBAT_RULESET_VERSION,
                "ticks": self.tick,
                "winner": "captain" if won else "enemy" if captain and not captain.active else None,
                "time_to_kill_ticks": max(resolved_ticks) - self.start_tick if won and resolved_ticks else None,
                "punish_percent": round(100 * self.metrics["punishes"] / max(1, self.metrics["hits"]), 1),
                "damage_by_actor": {k: v["damage_dealt"] for k,v in self.actor_metrics.items()},
                "damage_taken_by_actor": {k: v["damage_taken"] for k,v in self.actor_metrics.items()},
                "actor_metrics": {k: dict(v) for k,v in self.actor_metrics.items()},
                "kill_events": list(self.kill_events),
                "flawless": won and self.metrics["player_damage"] == 0,
                "flawless_possible": flawless_possible,
                "seed": self.seed, "inputs": list(self.replay)}
