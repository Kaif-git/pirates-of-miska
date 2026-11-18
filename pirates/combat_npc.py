"""Deterministic observation-limited tactical policies for duel NPCs.

This is deliberately not an adapter for the omniscient oracle. Its input DTO
contains the NPC's exact own-state, public opponent facts, evidence beliefs,
and already-legal action options only. Live runtime wiring is a separate step.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pirates.combat_beliefs import CombatBelief
from pirates.combat_duel import (
    ACTION_COSTS,
    ACTION_BASE_RECOVERY,
    ActionKind,
    DuelAction,
    DuelStats,
    DuelWeapon,
    action_duration,
    parry_capacity,
    tile_distance,
)


class IntelligenceTier(str, Enum):
    LIMITED = "limited"
    INFORMED = "informed"
    DISCIPLINED = "disciplined"


class BehaviorProfile(str, Enum):
    GENERAL = "general"
    FIRST_MOVER = "first_mover"
    DASH_AND_SLASH = "dash_and_slash"
    PATIENT_COUNTER = "patient_counter"


BEHAVIOR_TELLS = {
    BehaviorProfile.GENERAL: "Studies the distance before committing.",
    BehaviorProfile.FIRST_MOVER: "Rushes to strike first; does not parry.",
    BehaviorProfile.DASH_AND_SLASH: "Looks for a committed dash-strike opening.",
    BehaviorProfile.PATIENT_COUNTER: "Waits for a committed attack, then parries or counters.",
}


@dataclass(frozen=True)
class VisibleCommitment:
    action: ActionKind
    target_pos: tuple[int, int] | None
    impact_tick: int
    force_upper_bound: int | None
    completed_dash_approach: bool = False

    def __post_init__(self):
        if self.impact_tick < 0:
            raise ValueError("impact tick must be nonnegative")
        if self.force_upper_bound is not None and self.force_upper_bound < 0:
            raise ValueError("force upper bound must be nonnegative")


@dataclass(frozen=True)
class NPCObservation:
    tick: int
    own_pos: tuple[int, int]
    own_facing: tuple[int, int]
    own_stats: DuelStats
    own_weapon: DuelWeapon
    opponent_pos: tuple[int, int] | None
    opponent_facing: tuple[int, int] | None
    opponent_weapon: DuelWeapon | None
    visible_commitment: VisibleCommitment | None
    belief: CombatBelief
    legal_actions: tuple[DuelAction, ...]
    counter_window: bool = False

    def __post_init__(self):
        if self.tick < 0:
            raise ValueError("observation tick must be nonnegative")
        if not self.legal_actions:
            raise ValueError("NPC observation needs at least one legal action")


@dataclass(frozen=True)
class IntelligenceConfig:
    sight_radius: int
    response_delay: int
    infer_force: bool
    planning_depth: int
    parry_on_uncertain_force: bool = False

    def __post_init__(self):
        if (self.sight_radius < 0 or self.response_delay < 0
                or self.planning_depth not in (0, 1, 2)):
            raise ValueError("invalid sight, response, or planning limit")


CONFIGS = {
    IntelligenceTier.LIMITED: IntelligenceConfig(
        sight_radius=1, response_delay=25, infer_force=False, planning_depth=0,
    ),
    IntelligenceTier.INFORMED: IntelligenceConfig(
        sight_radius=4, response_delay=8, infer_force=True, planning_depth=1,
    ),
    IntelligenceTier.DISCIPLINED: IntelligenceConfig(
        sight_radius=8, response_delay=0, infer_force=True, planning_depth=2,
    ),
}


class ObservationLimitedPolicy:
    """A deterministic, inspectable policy with bounded sight/reaction quality."""

    def __init__(self, tier: IntelligenceTier,
                 behavior: BehaviorProfile = BehaviorProfile.GENERAL):
        self.tier = IntelligenceTier(tier)
        self.behavior = BehaviorProfile(behavior)
        self.config = CONFIGS[self.tier]
        self.profile_id = f"duel_{self.tier.value}_{self.behavior.value}"
        self.tell = BEHAVIOR_TELLS[self.behavior]

    def _visible(self, observation: NPCObservation) -> bool:
        return (observation.opponent_pos is not None
                and tile_distance(observation.own_pos, observation.opponent_pos)
                <= self.config.sight_radius)

    def _action(self, observation: NPCObservation, kind: ActionKind) -> DuelAction | None:
        return next((action for action in observation.legal_actions
                     if action.kind == kind), None)

    def _escape(self, observation: NPCObservation,
                deadline: int) -> DuelAction | None:
        """Choose an available dash whose first tile precedes the committed hit."""
        travel = action_duration(80, observation.own_stats, observation.own_weapon)
        choices = []
        for action in observation.legal_actions:
            if action.kind != ActionKind.DASH or action.steps < 1:
                continue
            first_tile_tick = (travel + action.steps - 1) // action.steps
            if first_tile_tick >= deadline:
                continue
            # Prefer the direction most opposed to the opponent vector, then
            # stable action fields. This is geometry, not sampled tie-breaking.
            opponent = observation.opponent_pos
            away_dot = 0
            if opponent is not None:
                away_dot = (action.dx * (opponent[0] - observation.own_pos[0])
                            + action.dy * (opponent[1] - observation.own_pos[1]))
            choices.append((away_dot, action.steps, action.dx, action.dy, action))
        return min(choices, default=(0, 0, 0, 0, None))[-1]

    def _force_upper_bound(self, observation: NPCObservation,
                           commitment: VisibleCommitment) -> int | None:
        base_force = {
            ActionKind.LIGHT: 8,
            ActionKind.HEAVY: 14,
            ActionKind.DASH_STRIKE: 8,
        }.get(commitment.action)
        if base_force is None:
            return None
        mass = (observation.opponent_weapon.mass
                if observation.opponent_weapon is not None
                else observation.belief.weapon_mass.high)
        inferred = (base_force + 2 * observation.belief.strength.high + mass
                    + (10 if commitment.completed_dash_approach else 0))
        if commitment.force_upper_bound is not None:
            return min(inferred, commitment.force_upper_bound)
        return inferred

    def _impact_delay(self, observation: NPCObservation, action: DuelAction) -> int:
        if action.kind == ActionKind.LIGHT:
            return action_duration(30, observation.own_stats, observation.own_weapon)
        if action.kind == ActionKind.HEAVY:
            return action_duration(95, observation.own_stats, observation.own_weapon)
        if action.kind == ActionKind.DASH_STRIKE:
            travel = action_duration(50, observation.own_stats, observation.own_weapon)
            approach = (travel + action.steps - 1) // action.steps
            return approach + action_duration(30, observation.own_stats,
                                               observation.own_weapon)
        if action.kind == ActionKind.DASH:
            travel = action_duration(80, observation.own_stats, observation.own_weapon)
            return (travel + action.steps - 1) // action.steps
        return action.steps

    def _offensive_action(self, observation: NPCObservation) -> DuelAction:
        priority = {ActionKind.LIGHT: 0, ActionKind.DASH_STRIKE: 1,
                    ActionKind.HEAVY: 2, ActionKind.DASH: 3}
        if self.behavior == BehaviorProfile.FIRST_MOVER:
            for kind in (ActionKind.HEAVY, ActionKind.LIGHT,
                         ActionKind.DASH_STRIKE, ActionKind.DASH):
                action = self._action(observation, kind)
                if action is not None:
                    return action
        elif self.behavior == BehaviorProfile.DASH_AND_SLASH:
            for kind in (ActionKind.DASH_STRIKE, ActionKind.DASH,
                         ActionKind.LIGHT, ActionKind.HEAVY):
                action = self._action(observation, kind)
                if action is not None:
                    return action
        actions = [action for action in observation.legal_actions
                   if action.kind in priority]
        if self.behavior == BehaviorProfile.PATIENT_COUNTER:
            actions = []
        if not actions:
            return min(observation.legal_actions,
                       key=lambda action: (action.kind.value, action.steps,
                                          action.dx, action.dy))
        if self.config.planning_depth == 0:
            return min(actions, key=lambda action: (
                priority[action.kind], action.steps, action.dx, action.dy,
            ))
        def planned_rank(action: DuelAction):
            impact = self._impact_delay(observation, action)
            if self.config.planning_depth >= 2:
                recovery = action_duration(
                    ACTION_BASE_RECOVERY[action.kind],
                    observation.own_stats, observation.own_weapon,
                )
                impact += recovery
            return (impact, ACTION_COSTS[action.kind], priority[action.kind],
                    action.steps, action.dx, action.dy)

        return min(actions, key=planned_rank)

    def choose_action(self, observation: NPCObservation) -> DuelAction:
        if not isinstance(observation, NPCObservation):
            raise TypeError("observation-limited policy requires NPCObservation")
        if (self.behavior == BehaviorProfile.PATIENT_COUNTER
                and observation.counter_window):
            light = self._action(observation, ActionKind.LIGHT)
            if light is not None:
                return light
        if not self._visible(observation):
            # A weak model cannot react to an unobserved opponent. It advances
            # deterministically with the least committal legal wait.
            wait = self._action(observation, ActionKind.WAIT)
            if wait is not None:
                return min((action for action in observation.legal_actions
                            if action.kind == ActionKind.WAIT),
                           key=lambda action: (action.steps, action.dx, action.dy))

        commitment = observation.visible_commitment if self._visible(observation) else None
        may_react = self.behavior != BehaviorProfile.FIRST_MOVER
        if (may_react and commitment is not None
                and commitment.target_pos == observation.own_pos):
            deadline = commitment.impact_tick - observation.tick - self.config.response_delay
            if deadline > 0:
                capacity = parry_capacity(observation.own_stats, observation.own_weapon)
                force_bound = self._force_upper_bound(observation, commitment)
                if self.config.infer_force and force_bound is not None:
                    if (observation.own_stats.stamina >= ACTION_COSTS[ActionKind.PARRY]
                            and capacity >= force_bound):
                        parry = self._action(observation, ActionKind.PARRY)
                        if parry is not None:
                            startup = action_duration(
                                10, observation.own_stats, observation.own_weapon,
                            )
                            if startup < deadline:
                                return parry
                dash = self._escape(observation, deadline)
                if dash is not None:
                    return dash
                parry = self._action(observation, ActionKind.PARRY)
                if (parry is not None and self.config.parry_on_uncertain_force
                        and observation.own_stats.stamina >= ACTION_COSTS[ActionKind.PARRY]):
                    return parry

        # Deliberately shallow attack selection: each tier makes the same
        # deterministic choice when it has the same facts, but limited sight
        # and reaction delay produce reproducibly exploitable mistakes.
        if self.behavior == BehaviorProfile.PATIENT_COUNTER:
            wait = [action for action in observation.legal_actions
                    if action.kind == ActionKind.WAIT]
            if wait:
                return min(wait, key=lambda action: (
                    abs(action.steps - 10), action.steps, action.dx, action.dy,
                ))
        return self._offensive_action(observation)
