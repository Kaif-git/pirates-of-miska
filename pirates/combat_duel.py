"""Pure deterministic rules for close-range duels.

This module is intentionally separate from the legacy boarding-combat
scheduler while the new duel contract is validated. It has no RNG, world/UI
dependencies, mutation, or actor-ID tie-breaking.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def ceil_div(numerator: int, denominator: int) -> int:
    if numerator < 0 or denominator <= 0:
        raise ValueError("ceil_div requires a nonnegative numerator and positive denominator")
    return (numerator + denominator - 1) // denominator


def tile_distance(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Chebyshev distance: diagonal and cardinal neighbors are one tile apart."""
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


@dataclass(frozen=True)
class DuelStats:
    speed: int = 100
    swing_skill: int = 100
    accuracy: int = 70
    strength: int = 0
    stamina: int = 100
    max_stamina: int = 100
    fatigue: int = 0
    balance: int = 100
    torso_structure: int = 100
    left_arm_structure: int = 100
    right_arm_structure: int = 100
    severed_arms: tuple[str, ...] = ()

    def __post_init__(self):
        if not 25 <= self.speed <= 200:
            raise ValueError("speed must be 25..200")
        if not 50 <= self.swing_skill <= 150:
            raise ValueError("swing_skill must be 50..150")
        if not 0 <= self.accuracy <= 100 or not 0 <= self.strength <= 8:
            raise ValueError("accuracy must be 0..100 and strength 0..8")
        if self.max_stamina <= 0 or not 0 <= self.stamina <= self.max_stamina:
            raise ValueError("stamina must be within a positive max_stamina")
        if not 0 <= self.fatigue <= 100 or not 0 <= self.balance <= 100:
            raise ValueError("fatigue and balance must be 0..100")
        if any(not 0 <= part <= 100 for part in
               (self.torso_structure, self.left_arm_structure, self.right_arm_structure)):
            raise ValueError("body structure must be 0..100")
        if any(arm not in {"left", "right"} for arm in self.severed_arms):
            raise ValueError("severed_arms entries must be left/right")


@dataclass(frozen=True)
class DuelWeapon:
    reach_tiles: int = 1
    handling: int = 100
    mass: int = 1

    def __post_init__(self):
        if not 1 <= self.reach_tiles <= 4:
            raise ValueError("reach_tiles must be 1..4")
        if not 50 <= self.handling <= 150:
            raise ValueError("handling must be 50..150")
        if not 1 <= self.mass <= 8:
            raise ValueError("mass must be 1..8")


class ActionKind(str, Enum):
    LIGHT = "light"
    HEAVY = "heavy"
    PARRY = "parry"
    DASH = "dash"
    DASH_STRIKE = "dash_strike"
    WAIT = "wait"


@dataclass(frozen=True, order=True)
class DuelAction:
    kind: ActionKind
    dx: int = 0
    dy: int = 0
    steps: int = 0


@dataclass(frozen=True)
class DuelCommitment:
    action: DuelAction
    accepted_tick: int
    impact_tick: int
    ready_tick: int
    target_pos: tuple[int, int] | None = None
    path: tuple[tuple[int, int], ...] = ()
    path_ticks: tuple[int, ...] = ()
    next_path_step: int = 0
    parry_start: int | None = None
    parry_end: int | None = None
    base_force: int = 0
    counter: bool = False


@dataclass(frozen=True)
class Duelant:
    """Hashable decision-search actor; no hidden AI belief fields."""
    stats: DuelStats
    weapon: DuelWeapon
    x: int
    y: int
    facing: tuple[int, int] = (1, 0)
    vitality: int = 100
    ready_at: int = 0
    intent: DuelCommitment | None = None
    counter_until: int = -1
    resource_tick: int = 0

    def __post_init__(self):
        if (not 0 <= self.vitality <= 100 or self.ready_at < 0
                or self.counter_until < -1 or self.resource_tick < 0):
            raise ValueError("invalid vitality, readiness, counter, or resource tick")
        if max(abs(self.facing[0]), abs(self.facing[1])) != 1:
            raise ValueError("facing must be a normalized 8-way direction")

    @property
    def pos(self) -> tuple[int, int]:
        return self.x, self.y


@dataclass(frozen=True)
class DuelDecisionState:
    """Finite two-actor decision snapshot used by the bounded policy search.

    `to_act` is explicit so the oracle's perfect-information turn ordering is
    inspectable. Runtime NPCs must not receive this oracle state.
    """
    tick: int
    left: Duelant
    right: Duelant
    to_act: int = 0
    plies: int = 0
    max_plies: int = 40

    def __post_init__(self):
        if self.tick < 0 or self.to_act not in (0, 1):
            raise ValueError("invalid tick or to_act")
        if not 0 <= self.plies <= self.max_plies:
            raise ValueError("plies must be within the search horizon")


def duel_state_from_data(data: dict) -> DuelDecisionState:
    """Decode/validate a JSON-compatible guided snapshot for safe continuation."""
    def record(value, fields, label):
        if not isinstance(value, dict) or set(value) != set(fields):
            raise ValueError(f"invalid {label} fields")
        return value

    def integer(value, label):
        if type(value) is not int:
            raise ValueError(f"{label} must be an integer")
        return value

    def pair(value, label):
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ValueError(f"{label} must be a two-integer coordinate")
        return tuple(integer(part, label) for part in value)

    def sequence(value, label):
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"{label} must be a sequence")
        return tuple(value)

    def decode_action(raw):
        raw = record(raw, ("kind", "dx", "dy", "steps"), "action")
        return DuelAction(ActionKind(raw["kind"]), integer(raw["dx"], "dx"),
                          integer(raw["dy"], "dy"), integer(raw["steps"], "steps"))

    def decode_stats(raw):
        fields = ("speed", "swing_skill", "accuracy", "strength", "stamina",
                  "max_stamina", "fatigue", "balance", "torso_structure",
                  "left_arm_structure", "right_arm_structure", "severed_arms")
        raw = record(raw, fields, "stats")
        values = {key: raw[key] for key in fields if key != "severed_arms"}
        values = {key: integer(value, key) for key, value in values.items()}
        arms = sequence(raw["severed_arms"], "severed_arms")
        values["severed_arms"] = tuple(arms)
        return DuelStats(**values)

    def decode_weapon(raw):
        raw = record(raw, ("reach_tiles", "handling", "mass"), "weapon")
        return DuelWeapon(**{key: integer(value, key) for key, value in raw.items()})

    def decode_commitment(raw):
        if raw is None:
            return None
        fields = ("action", "accepted_tick", "impact_tick", "ready_tick", "target_pos",
                  "path", "path_ticks", "next_path_step", "parry_start", "parry_end",
                  "base_force", "counter")
        raw = record(raw, fields, "commitment")
        target = None if raw["target_pos"] is None else pair(raw["target_pos"], "target_pos")
        path = tuple(pair(pos, "path tile") for pos in sequence(raw["path"], "path"))
        path_ticks = tuple(integer(value, "path tick")
                           for value in sequence(raw["path_ticks"], "path_ticks"))
        optional_ints = {key: (None if raw[key] is None else integer(raw[key], key))
                         for key in ("parry_start", "parry_end")}
        if type(raw["counter"]) is not bool:
            raise ValueError("counter must be boolean")
        return DuelCommitment(
            action=decode_action(raw["action"]),
            accepted_tick=integer(raw["accepted_tick"], "accepted_tick"),
            impact_tick=integer(raw["impact_tick"], "impact_tick"),
            ready_tick=integer(raw["ready_tick"], "ready_tick"), target_pos=target,
            path=path, path_ticks=path_ticks,
            next_path_step=integer(raw["next_path_step"], "next_path_step"),
            parry_start=optional_ints["parry_start"], parry_end=optional_ints["parry_end"],
            base_force=integer(raw["base_force"], "base_force"), counter=raw["counter"],
        )

    def decode_duelist(raw):
        fields = ("stats", "weapon", "x", "y", "facing", "vitality", "ready_at",
                  "intent", "counter_until", "resource_tick")
        raw = record(raw, fields, "duelist")
        return Duelant(
            stats=decode_stats(raw["stats"]), weapon=decode_weapon(raw["weapon"]),
            x=integer(raw["x"], "x"), y=integer(raw["y"], "y"),
            facing=pair(raw["facing"], "facing"),
            vitality=integer(raw["vitality"], "vitality"),
            ready_at=integer(raw["ready_at"], "ready_at"),
            intent=decode_commitment(raw["intent"]),
            counter_until=integer(raw["counter_until"], "counter_until"),
            resource_tick=integer(raw["resource_tick"], "resource_tick"),
        )

    raw = record(data, ("tick", "left", "right", "to_act", "plies", "max_plies"),
                 "duel state")
    return DuelDecisionState(
        tick=integer(raw["tick"], "tick"), left=decode_duelist(raw["left"]),
        right=decode_duelist(raw["right"]), to_act=integer(raw["to_act"], "to_act"),
        plies=integer(raw["plies"], "plies"), max_plies=integer(raw["max_plies"], "max_plies"),
    )


class TerminalResult(str, Enum):
    LEFT_WIN = "left_win"
    RIGHT_WIN = "right_win"
    DRAW_MUTUAL_KO = "draw_mutual_ko"
    DRAW_HORIZON = "draw_horizon"
    DRAW_CYCLE = "draw_cycle"


def terminal_result(state: DuelDecisionState,
                    ancestors: tuple[DuelDecisionState | tuple, ...] = ()) -> TerminalResult | None:
    """Return exact terminal/draw status for the bounded search tree."""
    if state.left.vitality <= 0 and state.right.vitality <= 0:
        return TerminalResult.DRAW_MUTUAL_KO
    if state.left.vitality <= 0:
        return TerminalResult.RIGHT_WIN
    if state.right.vitality <= 0:
        return TerminalResult.LEFT_WIN
    key = state_cycle_key(state)
    if any(state_cycle_key(ancestor) == key if isinstance(ancestor, DuelDecisionState)
           else ancestor == key for ancestor in ancestors):
        return TerminalResult.DRAW_CYCLE
    if state.plies >= state.max_plies:
        return TerminalResult.DRAW_HORIZON
    return None


def _commitment_cycle_key(intent: DuelCommitment | None, tick: int):
    if intent is None:
        return None
    return (intent.action, intent.accepted_tick - tick, intent.impact_tick - tick,
            intent.ready_tick - tick, intent.target_pos, intent.path,
            tuple(event_tick - tick for event_tick in intent.path_ticks),
            intent.next_path_step,
            None if intent.parry_start is None else intent.parry_start - tick,
            None if intent.parry_end is None else intent.parry_end - tick,
            intent.base_force, intent.counter)


def _fighter_cycle_key(fighter: Duelant, tick: int):
    return (fighter.stats, fighter.weapon, fighter.x, fighter.y, fighter.facing,
            fighter.vitality, fighter.ready_at - tick,
            _commitment_cycle_key(fighter.intent, tick),
            (-1 if fighter.counter_until < 0 else fighter.counter_until - tick),
            fighter.resource_tick - tick)


def state_cycle_key(state: DuelDecisionState) -> tuple:
    """State identity excluding absolute clock/ply so recurring fights draw."""
    return (state.to_act, _fighter_cycle_key(state.left, state.tick),
            _fighter_cycle_key(state.right, state.tick))


ACTION_COSTS = {
    ActionKind.LIGHT: 40,
    ActionKind.HEAVY: 70,
    ActionKind.PARRY: 20,
    ActionKind.DASH: 35,
    ActionKind.DASH_STRIKE: 60,
    ActionKind.WAIT: 0,
}
ACTION_BASE_WINDUP = {ActionKind.LIGHT: 30, ActionKind.HEAVY: 75,
                      ActionKind.PARRY: 10, ActionKind.DASH_STRIKE: 50}
ACTION_BASE_RECOVERY = {ActionKind.LIGHT: 45, ActionKind.HEAVY: 90,
                        ActionKind.PARRY: 45, ActionKind.DASH: 120,
                        ActionKind.DASH_STRIKE: 110}
ACTION_FATIGUE = {ActionKind.LIGHT: 4, ActionKind.HEAVY: 8,
                  ActionKind.PARRY: 2, ActionKind.DASH: 16,
                  ActionKind.DASH_STRIKE: 12, ActionKind.WAIT: 0}


def legal_actions(state: DuelDecisionState) -> tuple[DuelAction, ...]:
    """Enumerate stable, finite actions for an open-lane bounded duel state."""
    actor = state.left if state.to_act == 0 else state.right
    target = state.right if state.to_act == 0 else state.left
    if (actor.vitality <= 0 or target.vitality <= 0 or actor.intent is not None
            or actor.ready_at > state.tick):
        return ()
    # Waiting is one decision with an explicitly chosen delay, which permits
    # timed parries against long windups without hidden auto-guarding.
    actions = [DuelAction(ActionKind.WAIT, steps=ticks) for ticks in range(1, 101)]
    if actor.stats.stamina >= ACTION_COSTS[ActionKind.PARRY]:
        actions.append(DuelAction(ActionKind.PARRY))
    if can_reach(attacker_pos=(actor.x, actor.y),
                 target_pos=(target.x, target.y), weapon=actor.weapon):
        if actor.stats.stamina >= ACTION_COSTS[ActionKind.LIGHT]:
            actions.append(DuelAction(ActionKind.LIGHT))
        if (actor.stats.stamina >= ACTION_COSTS[ActionKind.HEAVY]
                and can_reach((actor.x, actor.y), (target.x, target.y), actor.weapon,
                              reach_bonus=1)):
            actions.append(DuelAction(ActionKind.HEAVY))
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == dy == 0:
                continue
            for steps in range(1, 4):
                end = actor.x + dx * steps, actor.y + dy * steps
                path = tuple((actor.x + dx * n, actor.y + dy * n)
                             for n in range(1, steps + 1))
                if (target.pos not in path
                        and actor.stats.stamina >= ACTION_COSTS[ActionKind.DASH]):
                    actions.append(DuelAction(ActionKind.DASH, dx, dy, steps))
            if (tile_distance(actor.pos, target.pos) >= 2
                    and actor.stats.stamina >= ACTION_COSTS[ActionKind.DASH_STRIKE]):
                for steps in (1, 2):
                    end = actor.x + dx * steps, actor.y + dy * steps
                    path = tuple((actor.x + dx * n, actor.y + dy * n)
                                 for n in range(1, steps + 1))
                    if (end != (target.x, target.y)
                            and target.pos not in path
                            and can_reach(end, (target.x, target.y), actor.weapon)):
                        actions.append(DuelAction(ActionKind.DASH_STRIKE, dx, dy, steps))
    return tuple(sorted(set(actions)))


def fatigue_factor(stats: DuelStats) -> int:
    return max(50, 100 - stats.fatigue // 2)


def stamina_factor(stats: DuelStats) -> int:
    if stats.stamina * 10 >= stats.max_stamina * 3:
        return 100
    return clamp(75 + (250 * stats.stamina) // (3 * stats.max_stamina), 75, 99)


def effective_swing_speed(stats: DuelStats, weapon: DuelWeapon) -> int:
    value = (stats.speed * stats.swing_skill * weapon.handling
             * fatigue_factor(stats) * stamina_factor(stats) // 100_000_000)
    return clamp(value, 25, 300)


def action_duration(base_ticks: int, stats: DuelStats, weapon: DuelWeapon) -> int:
    if base_ticks < 0:
        raise ValueError("base_ticks must be nonnegative")
    return max(1, ceil_div(base_ticks * 100, effective_swing_speed(stats, weapon)))


def body_stability(stats: DuelStats) -> int:
    left = 0 if "left" in stats.severed_arms else stats.left_arm_structure
    right = 0 if "right" in stats.severed_arms else stats.right_arm_structure
    return (stats.torso_structure + left + right) // 3


def attack_force(base_force: int, stats: DuelStats, weapon: DuelWeapon,
                 *, completed_dash_approach: bool = False) -> int:
    if base_force < 0:
        raise ValueError("base_force must be nonnegative")
    return (base_force + 2 * stats.strength + weapon.mass
            + (10 if completed_dash_approach else 0))


def parry_capacity(stats: DuelStats, weapon: DuelWeapon) -> int:
    return (2 * stats.strength + body_stability(stats) // 10
            + stats.balance // 5 + weapon.handling // 5)


def attack_control(stats: DuelStats, weapon: DuelWeapon, *, attacking_while_moving=False) -> int:
    return (stats.accuracy + weapon.handling // 4 - stats.fatigue // 4
            - (20 if stats.stamina * 10 < stats.max_stamina * 3 else 0)
            - (15 if attacking_while_moving else 0))


def evasion_score(stats: DuelStats, *, moving_at_contact=False) -> int:
    return 20 + stats.speed // 4 + (25 if moving_at_contact else 0) + stats.balance // 10


@dataclass(frozen=True)
class ContactResult:
    outcome: str
    attack_control: int
    target_evasion: int
    force: int
    parry_capacity: int

    @property
    def connected(self) -> bool:
        return self.outcome in {"hit", "overpowered", "wrong-facing"}

    @property
    def parried(self) -> bool:
        return self.outcome == "parried"


def resolve_contact(attacker: DuelStats, attacker_weapon: DuelWeapon,
                    defender: DuelStats, defender_weapon: DuelWeapon, *,
                    base_force: int, lane_contact: bool = True,
                    defender_moving=False, attacker_moving=False,
                    parry_active=False, parry_facing=True,
                    completed_dash_approach=False) -> ContactResult:
    """Resolve one already-scheduled strike without mutating either combatant."""
    control = attack_control(attacker, attacker_weapon,
                             attacking_while_moving=attacker_moving)
    evasion = evasion_score(defender, moving_at_contact=defender_moving)
    force = attack_force(base_force, attacker, attacker_weapon,
                         completed_dash_approach=completed_dash_approach)
    capacity = parry_capacity(defender, defender_weapon)
    if not lane_contact:
        outcome = "committed-lane-miss"
    elif control < evasion:
        outcome = "precision-miss"
    elif parry_active and not parry_facing:
        outcome = "wrong-facing"
    elif parry_active and force <= capacity:
        outcome = "parried"
    elif parry_active and force > capacity:
        outcome = "overpowered"
    else:
        outcome = "hit"
    return ContactResult(outcome, control, evasion, force, capacity)


def can_reach(attacker_pos: tuple[int, int], target_pos: tuple[int, int],
              weapon: DuelWeapon, *, reach_bonus=0) -> bool:
    if reach_bonus < 0:
        raise ValueError("reach_bonus must be nonnegative")
    return tile_distance(attacker_pos, target_pos) <= weapon.reach_tiles + reach_bonus


def counter_is_eligible(*, ticks_since_parry: int, defender_speed: int,
                        opponent_speed: int, in_reach: bool, in_lane: bool) -> bool:
    return (0 <= ticks_since_parry < 30 and defender_speed >= opponent_speed
            and in_reach and in_lane)


@dataclass(frozen=True)
class DuelEvent:
    tick: int
    actor: int
    kind: str
    outcome: str
    target: int | None = None
    damage: int = 0
    reason: str = ""
    force: int = 0
    parry_capacity: int = 0
    control: int = 0
    evasion: int = 0


@dataclass(frozen=True)
class DuelTransition:
    state: DuelDecisionState
    events: tuple[DuelEvent, ...]


def _actor(state: DuelDecisionState, side: int) -> Duelant:
    return state.left if side == 0 else state.right


def _replace_actor(state: DuelDecisionState, side: int, actor: Duelant) -> DuelDecisionState:
    return replace(state, **({"left": actor} if side == 0 else {"right": actor}))


def _face(source: tuple[int, int], target: tuple[int, int]) -> tuple[int, int]:
    dx, dy = target[0] - source[0], target[1] - source[1]
    return (dx > 0) - (dx < 0), (dy > 0) - (dy < 0)


def _in_forward_cone(source: tuple[int, int], target: tuple[int, int],
                     facing: tuple[int, int]) -> bool:
    dx, dy = _face(source, target)
    dot = dx * facing[0] + dy * facing[1]
    cross = dx * facing[1] - dy * facing[0]
    return dot > 0 and abs(cross) <= dot


def _action_force(kind: ActionKind, actor: Duelant, *, dash_completed=False) -> int:
    base = 14 if kind == ActionKind.HEAVY else 8
    return attack_force(base, actor.stats, actor.weapon,
                        completed_dash_approach=dash_completed)


def commit_action(state: DuelDecisionState, action: DuelAction) -> DuelDecisionState:
    """Commit one ready actor action and freeze its target, timing, and path."""
    if terminal_result(state) is not None:
        raise ValueError("duel is terminal")
    legal = legal_actions(state)
    if action not in legal:
        raise ValueError(f"illegal duel action: {action}")
    side = state.to_act
    actor, target = _actor(state, side), _actor(state, 1 - side)
    stats = replace(
        actor.stats,
        stamina=actor.stats.stamina - ACTION_COSTS[action.kind],
        fatigue=min(100, actor.stats.fatigue + ACTION_FATIGUE[action.kind]),
    )
    actor = replace(actor, stats=stats, resource_tick=state.tick)
    facing = actor.facing
    if action.kind in {ActionKind.LIGHT, ActionKind.HEAVY, ActionKind.PARRY}:
        facing = _face(actor.pos, target.pos)
    elif action.kind == ActionKind.DASH_STRIKE:
        facing = (action.dx, action.dy)
    actor = replace(actor, facing=facing)
    speed = effective_swing_speed(stats, actor.weapon)
    path: tuple[tuple[int, int], ...] = ()
    path_ticks: tuple[int, ...] = ()
    parry_start = parry_end = None
    base_force = 0
    is_counter = False
    if action.kind == ActionKind.LIGHT:
        opponent_speed = effective_swing_speed(target.stats, target.weapon)
        ready_speed = effective_swing_speed(_actor(state, side).stats, actor.weapon)
        is_counter = (actor.counter_until > state.tick
                      and counter_is_eligible(
                          ticks_since_parry=state.tick - (actor.counter_until - 30),
                          defender_speed=ready_speed, opponent_speed=opponent_speed,
                          in_reach=can_reach(actor.pos, target.pos, actor.weapon),
                          in_lane=True,
                      ))
        impact = state.tick + action_duration(15 if is_counter else 30,
                                              stats, actor.weapon)
        ready = impact + action_duration(45, stats, actor.weapon)
        target_pos = target.pos
        base_force = 8
    elif action.kind == ActionKind.HEAVY:
        impact = state.tick + action_duration(75 + 20, stats, actor.weapon)
        ready = impact + action_duration(90, stats, actor.weapon)
        target_pos = target.pos
        base_force = 14
    elif action.kind == ActionKind.PARRY:
        parry_start = state.tick + action_duration(10, stats, actor.weapon)
        parry_end = parry_start + action_duration(35, stats, actor.weapon)
        impact = parry_end
        ready = parry_end + action_duration(45, stats, actor.weapon)
        target_pos = target.pos
    elif action.kind in {ActionKind.DASH, ActionKind.DASH_STRIKE}:
        path = tuple((actor.x + action.dx * n, actor.y + action.dy * n)
                     for n in range(1, action.steps + 1))
        travel_base = 80 if action.kind == ActionKind.DASH else 50
        travel = action_duration(travel_base, stats, actor.weapon)
        path_ticks = tuple(state.tick + ceil_div(travel * n, action.steps)
                           for n in range(1, action.steps + 1))
        if action.kind == ActionKind.DASH:
            impact = path_ticks[-1]
            ready = impact + action_duration(ACTION_BASE_RECOVERY[action.kind], stats, actor.weapon)
            target_pos = None
        else:
            light_windup = action_duration(30, stats, actor.weapon)
            impact = path_ticks[-1] + light_windup
            ready = impact + action_duration(ACTION_BASE_RECOVERY[action.kind], stats, actor.weapon)
            target_pos = target.pos
            base_force = 8
    elif action.kind == ActionKind.WAIT:
        impact = state.tick + action.steps
        ready = impact
        target_pos = None
    else:  # pragma: no cover - exhaustive enum guard
        raise ValueError(f"unsupported action: {action.kind}")
    commitment = DuelCommitment(
        action=action, accepted_tick=state.tick, impact_tick=impact,
        ready_tick=ready, target_pos=target_pos, path=path,
        path_ticks=path_ticks, parry_start=parry_start, parry_end=parry_end,
        base_force=base_force, counter=is_counter,
    )
    actor = replace(actor, ready_at=ready, intent=commitment,
                    counter_until=-1 if is_counter else actor.counter_until)
    next_state = _replace_actor(state, side, actor)
    return replace(next_state, to_act=1 - side, plies=state.plies + 1)


def _refresh_recovery(actor: Duelant, tick: int) -> Duelant:
    if actor.intent is not None or actor.vitality <= 0 or tick <= actor.resource_tick:
        return actor
    stamina_steps = tick // 10 - actor.resource_tick // 10
    fatigue_steps = tick // 50 - actor.resource_tick // 50
    stats = replace(
        actor.stats,
        stamina=min(actor.stats.max_stamina, actor.stats.stamina + stamina_steps),
        fatigue=max(0, actor.stats.fatigue - fatigue_steps),
    )
    return replace(actor, stats=stats, resource_tick=tick)


def _next_intent_event(actor: Duelant) -> int | None:
    intent = actor.intent
    if intent is None:
        return None
    if intent.next_path_step < len(intent.path_ticks):
        return min(intent.path_ticks[intent.next_path_step], intent.impact_tick)
    return intent.impact_tick


def _eligible_sides(state: DuelDecisionState) -> tuple[int, ...]:
    return tuple(side for side in (0, 1)
                 if (fighter := _actor(state, side)).vitality > 0
                 and fighter.intent is None and fighter.ready_at <= state.tick)


def _process_event_tick(state: DuelDecisionState, tick: int) -> DuelTransition:
    before = state
    events: list[DuelEvent] = []
    # Resource recovery accrues only while an actor has no live commitment.
    left = _refresh_recovery(before.left, tick)
    right = _refresh_recovery(before.right, tick)
    current = replace(before, tick=tick, left=left, right=right)
    contact_snapshot = current

    # Resolve all attacks from the same pre-movement, pre-damage snapshot.
    contacts: list[tuple[int, int, ContactResult]] = []
    for side in (0, 1):
        attacker, defender = _actor(contact_snapshot, side), _actor(contact_snapshot, 1 - side)
        intent = attacker.intent
        if (intent is None or intent.action.kind not in
                {ActionKind.LIGHT, ActionKind.HEAVY, ActionKind.DASH_STRIKE}
                or intent.impact_tick != tick):
            continue
        completed_dash = (intent.action.kind == ActionKind.DASH_STRIKE
                          and bool(intent.path)
                          and attacker.pos == intent.path[-1]
                          and intent.next_path_step == len(intent.path))
        target_pos = intent.target_pos
        lane_contact = (target_pos == defender.pos
                        and can_reach(attacker.pos, defender.pos, attacker.weapon,
                                      reach_bonus=1 if intent.action.kind == ActionKind.HEAVY else 0)
                        and _in_forward_cone(attacker.pos, defender.pos, attacker.facing))
        defender_intent = defender.intent
        parry_active = (defender_intent is not None
                        and defender_intent.action.kind == ActionKind.PARRY
                        and defender_intent.parry_start is not None
                        and defender_intent.parry_end is not None
                        and defender_intent.parry_start <= tick < defender_intent.parry_end)
        defender_moving = (defender_intent is not None and bool(defender_intent.path)
                           and defender_intent.next_path_step < len(defender_intent.path))
        result = resolve_contact(
            attacker.stats, attacker.weapon, defender.stats, defender.weapon,
            base_force=intent.base_force, lane_contact=lane_contact,
            defender_moving=defender_moving, parry_active=parry_active,
            parry_facing=_in_forward_cone(defender.pos, attacker.pos, defender.facing),
            completed_dash_approach=completed_dash,
        )
        contacts.append((side, 1 - side, result))

    # Apply all outcomes only after every same-tick contact was evaluated.
    updated = [current.left, current.right]
    for attacker_side, defender_side, result in contacts:
        attacker = updated[attacker_side]
        defender = updated[defender_side]
        intent = _actor(contact_snapshot, attacker_side).intent
        damage = min(defender.vitality, result.force) if result.connected else 0
        if damage:
            defender = replace(defender, vitality=defender.vitality - damage)
        if result.parried:
            balance_loss = ceil_div(result.force, 2)
            defender = replace(
                defender,
                stats=replace(defender.stats,
                              balance=max(0, defender.stats.balance - balance_loss)),
                ready_at=tick,
                intent=None,
                counter_until=tick + 30,
                resource_tick=tick,
            )
        attacker = replace(attacker, intent=None, resource_tick=tick)
        updated[attacker_side] = attacker
        updated[defender_side] = defender
        reason = {
            "hit": (f"control {result.attack_control} met evasion {result.target_evasion}; "
                    f"force {result.force}"),
            "precision-miss": (f"control {result.attack_control} fell below "
                               f"evasion {result.target_evasion}"),
            "committed-lane-miss": "target left the committed lane or reach",
            "wrong-facing": "defender's parry faced outside the incoming lane",
            "parried": f"force {result.force} was within parry capacity {result.parry_capacity}",
            "overpowered": (f"force {result.force} exceeded parry capacity "
                            f"{result.parry_capacity}"),
        }.get(result.outcome, result.outcome)
        opposing_intent = _actor(contact_snapshot, defender_side).intent
        if (opposing_intent is not None and opposing_intent.action.kind in
                {ActionKind.LIGHT, ActionKind.HEAVY, ActionKind.DASH_STRIKE}):
            if opposing_intent.impact_tick == tick:
                reason = f"simultaneous contact; {reason}"
            elif opposing_intent.impact_tick > tick:
                delay = opposing_intent.impact_tick - tick
                prefix = "first contact landed" if result.connected else "first contact attempt resolved"
                reason = (f"{prefix} before opposing {opposing_intent.action.kind.value} "
                          f"(due in {delay} ticks); {reason}")
        events.append(DuelEvent(
            tick, attacker_side, intent.action.kind.value, result.outcome,
            defender_side, damage, reason, result.force, result.parry_capacity,
            result.attack_control, result.target_evasion,
        ))
    current = replace(current, left=updated[0], right=updated[1])

    # Advance each due dash one tile after contacts, so equal-tick attacks see
    # the positions captured at the start of this simulation tick.
    for side in (0, 1):
        fighter = _actor(current, side)
        intent = fighter.intent
        if (intent is None or intent.next_path_step >= len(intent.path)
                or intent.path_ticks[intent.next_path_step] != tick):
            continue
        destination = intent.path[intent.next_path_step]
        other = _actor(current, 1 - side)
        if other.vitality > 0 and destination == other.pos:
            fighter = replace(fighter, intent=None,
                              ready_at=tick + action_duration(
                                  ACTION_BASE_RECOVERY[intent.action.kind],
                                  fighter.stats, fighter.weapon),
                              resource_tick=tick)
            events.append(DuelEvent(tick, side, intent.action.kind.value, "blocked"))
        else:
            next_step = intent.next_path_step + 1
            fighter = replace(fighter, x=destination[0], y=destination[1])
            if (intent.action.kind == ActionKind.DASH
                    and next_step == len(intent.path)):
                fighter = replace(
                    fighter, intent=None,
                    ready_at=tick + action_duration(120, fighter.stats, fighter.weapon),
                    resource_tick=tick,
                )
            else:
                fighter = replace(fighter, intent=replace(intent, next_path_step=next_step))
            events.append(DuelEvent(tick, side, intent.action.kind.value, "step"))
        current = _replace_actor(current, side, fighter)

    # Close non-attack commitments whose scheduled event has arrived.
    for side in (0, 1):
        fighter = _actor(current, side)
        intent = fighter.intent
        if intent is None or intent.impact_tick != tick:
            continue
        kind = intent.action.kind
        if kind in {ActionKind.LIGHT, ActionKind.HEAVY, ActionKind.DASH_STRIKE}:
            # A dash-strike whose approach was blocked cannot still connect.
            if kind == ActionKind.DASH_STRIKE and intent.next_path_step < len(intent.path):
                events.append(DuelEvent(tick, side, kind.value, "approach-stopped"))
                fighter = replace(fighter, intent=None, resource_tick=tick)
        elif kind == ActionKind.WAIT:
            duration = tick - intent.accepted_tick
            passive_stamina = duration // 10
            passive_fatigue = duration // 50
            fighter = replace(
                fighter,
                stats=replace(fighter.stats,
                              stamina=min(fighter.stats.max_stamina,
                                          fighter.stats.stamina + passive_stamina
                                          + (12 * duration // 100)),
                              fatigue=max(0, fighter.stats.fatigue - passive_fatigue
                                          - (2 * duration // 100))),
                intent=None, ready_at=tick, resource_tick=tick,
            )
            events.append(DuelEvent(tick, side, kind.value, "recovered"))
        elif kind == ActionKind.PARRY:
            fighter = replace(fighter, intent=None, resource_tick=tick)
            events.append(DuelEvent(tick, side, kind.value, "window-ended"))
        current = _replace_actor(current, side, fighter)

    return DuelTransition(current, tuple(events))


def advance_to_decision(state: DuelDecisionState) -> DuelTransition:
    """Advance exact events until at least one fighter can choose an action."""
    current = state
    events: list[DuelEvent] = []
    while terminal_result(current) is None:
        ready = _eligible_sides(current)
        if ready:
            side = current.to_act if current.to_act in ready else ready[0]
            current = replace(current, to_act=side)
            return DuelTransition(current, tuple(events))
        future = []
        for side in (0, 1):
            fighter = _actor(current, side)
            intent_event = _next_intent_event(fighter)
            if intent_event is not None and intent_event > current.tick:
                future.append(intent_event)
            if (fighter.vitality > 0 and fighter.intent is None
                    and fighter.ready_at > current.tick):
                future.append(fighter.ready_at)
        if not future:
            return DuelTransition(current, tuple(events))
        next_tick = min(future)
        transition = _process_event_tick(current, next_tick)
        current = transition.state
        events.extend(transition.events)
    return DuelTransition(current, tuple(events))


def apply_action(state: DuelDecisionState, action: DuelAction) -> DuelTransition:
    """Commit a legal action, then advance only until the next decision point."""
    committed = commit_action(state, action)
    return advance_to_decision(committed)


@dataclass(frozen=True)
class SearchResult:
    action: DuelAction | None
    score: int
    depth: int
    nodes: int
    principal_variation: tuple[DuelAction, ...]
    complete: bool


@dataclass(frozen=True)
class OraclePolicyConfig:
    depth: int = 3
    node_budget: int = 100_000
    require_complete: bool = True

    def __post_init__(self):
        if self.depth < 1 or self.node_budget < 1:
            raise ValueError("oracle depth and node budget must be positive")


class IncompleteOracleSearch(RuntimeError):
    """Raised rather than silently using an unproven partial search result."""


class OraclePolicy:
    """Omniscient policy interface; intentionally accepts only search states.

    Runtime NPC adapters must build a separate observation-limited interface
    and must never pass hidden live Actor/Combat objects through this class.
    """
    profile_id = "duel_oracle"

    def __init__(self, config: OraclePolicyConfig | None = None):
        self.config = config or OraclePolicyConfig()

    def analyze(self, state: DuelDecisionState) -> SearchResult:
        if not isinstance(state, DuelDecisionState):
            raise TypeError("OraclePolicy requires a bounded DuelDecisionState")
        return search_best_action(
            state, depth=self.config.depth, node_budget=self.config.node_budget,
        )

    def choose_action(self, state: DuelDecisionState) -> DuelAction:
        result = self.analyze(state)
        if self.config.require_complete and not result.complete:
            raise IncompleteOracleSearch(
                f"search incomplete at depth {result.depth} after {result.nodes} nodes"
            )
        if result.action is None:
            raise ValueError("no action exists for this terminal or non-decision state")
        return result.action


WIN_SCORE = 1_000_000


def evaluate_state(state: DuelDecisionState) -> int:
    """Deterministic open-model heuristic from left seat's perspective."""
    if state.left.vitality <= 0 and state.right.vitality <= 0:
        return 0
    if state.left.vitality <= 0:
        return -WIN_SCORE
    if state.right.vitality <= 0:
        return WIN_SCORE

    def fighter_value(fighter: Duelant, opponent: Duelant) -> int:
        speed = effective_swing_speed(fighter.stats, fighter.weapon)
        distance = tile_distance(fighter.pos, opponent.pos)
        ready_offset = max(0, fighter.ready_at - state.tick)
        value = (fighter.vitality * 10_000 + fighter.stats.stamina * 40
                 + speed * 10 + fighter.stats.balance * 5
                 - fighter.stats.fatigue * 20 - ready_offset * 4)
        if can_reach(fighter.pos, opponent.pos, fighter.weapon):
            value += 2_000
        elif distance <= fighter.weapon.reach_tiles + 2:
            value += 500
        intent = fighter.intent
        if intent and intent.action.kind in {
                ActionKind.LIGHT, ActionKind.HEAVY, ActionKind.DASH_STRIKE}:
            remaining = max(0, intent.impact_tick - state.tick)
            if intent.target_pos == opponent.pos:
                value += max(0, 2_000 - remaining * 20)
        return value

    return fighter_value(state.left, state.right) - fighter_value(state.right, state.left)


def _ordered_actions(state: DuelDecisionState) -> tuple[DuelAction, ...]:
    actions = legal_actions(state)
    actor = _actor(state, state.to_act)
    opponent = _actor(state, 1 - state.to_act)
    threat = opponent.intent

    def order(action: DuelAction):
        rank = {
            ActionKind.LIGHT: 0,
            ActionKind.HEAVY: 1,
            ActionKind.PARRY: 2 if threat else 5,
            ActionKind.DASH_STRIKE: 3,
            ActionKind.DASH: 4,
            ActionKind.WAIT: 6,
        }[action.kind]
        wait_distance = 0
        if action.kind == ActionKind.WAIT and threat:
            wait_distance = abs(action.steps - max(1, threat.impact_tick - state.tick - 20))
        toward_dot = action.dx * (opponent.x - actor.x) + action.dy * (opponent.y - actor.y)
        return rank, wait_distance, -toward_dot, action.steps, action.dx, action.dy

    return tuple(sorted(actions, key=order))


def search_best_action(state: DuelDecisionState, *, depth=4,
                       node_budget=100_000) -> SearchResult:
    """Alpha-beta oracle search over the exact bounded duel state machine.

    This is an omniscient test/policy oracle. It must not be passed to normal
    runtime NPCs, whose observations are narrower. Scores use left-seat
    perspective. `complete=False` means the node budget interrupted the
    requested horizon; it is not a proof result.
    """
    if depth < 1 or node_budget < 1:
        raise ValueError("depth and node_budget must be positive")
    nodes = 0
    complete = True

    def visit(node: DuelDecisionState, remaining: int, alpha: int, beta: int,
              ancestors: tuple[tuple, ...]):
        nonlocal nodes, complete
        if nodes >= node_budget:
            complete = False
            return evaluate_state(node), ()
        nodes += 1
        terminal = terminal_result(node, ancestors)
        if terminal is not None:
            if terminal == TerminalResult.LEFT_WIN:
                return WIN_SCORE + remaining * 1_000 - node.tick, ()
            if terminal == TerminalResult.RIGHT_WIN:
                return -WIN_SCORE - remaining * 1_000 + node.tick, ()
            return 0, ()
        if remaining == 0:
            return evaluate_state(node), ()
        actions = _ordered_actions(node)
        if not actions:
            transition = advance_to_decision(node)
            if transition.state == node:
                return evaluate_state(node), ()
            return visit(transition.state, remaining - 1, alpha, beta,
                         ancestors + (state_cycle_key(node),))
        maximizing = node.to_act == 0
        best_score = -WIN_SCORE * 2 if maximizing else WIN_SCORE * 2
        best_pv: tuple[DuelAction, ...] = ()
        for action in actions:
            child = apply_action(node, action).state
            score, child_pv = visit(
                child, remaining - 1, alpha, beta,
                ancestors + (state_cycle_key(node),),
            )
            if ((maximizing and score > best_score)
                    or (not maximizing and score < best_score)):
                best_score = score
                best_pv = (action,) + child_pv
            if maximizing:
                alpha = max(alpha, best_score)
            else:
                beta = min(beta, best_score)
            if alpha >= beta or nodes >= node_budget:
                if nodes >= node_budget:
                    complete = False
                break
        return best_score, best_pv

    terminal = terminal_result(state)
    if terminal is not None:
        score, _ = visit(state, depth, -WIN_SCORE * 2, WIN_SCORE * 2, ())
        return SearchResult(None, score, depth, nodes, (), complete)
    root_actions = _ordered_actions(state)
    if not root_actions:
        return SearchResult(None, evaluate_state(state), depth, nodes, (), complete)
    best_score = -WIN_SCORE * 2 if state.to_act == 0 else WIN_SCORE * 2
    best_action = None
    best_pv: tuple[DuelAction, ...] = ()
    alpha, beta = -WIN_SCORE * 2, WIN_SCORE * 2
    for action in root_actions:
        child = apply_action(state, action).state
        score, pv = visit(child, depth - 1, alpha, beta,
                          (state_cycle_key(state),))
        improves = ((state.to_act == 0 and score > best_score)
                    or (state.to_act == 1 and score < best_score))
        if improves or best_action is None:
            best_score, best_action, best_pv = score, action, (action,) + pv
        if state.to_act == 0:
            alpha = max(alpha, best_score)
        else:
            beta = min(beta, best_score)
        if alpha >= beta or nodes >= node_budget:
            if nodes >= node_budget:
                complete = False
            break
    return SearchResult(best_action, best_score, depth, nodes, best_pv, complete)
