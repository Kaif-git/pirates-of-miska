"""Evidence-only, deterministic knowledge for ordinary duel opponents.

Beliefs are updated from public observations, never from a live opponent Actor
or the omniscient search state. Campaign persistence is intentionally left to
the encounter adapter.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from pirates.combat_duel import DuelWeapon, ceil_div


@dataclass(frozen=True)
class IntInterval:
    low: int
    high: int

    def __post_init__(self):
        if self.low > self.high:
            raise ValueError("interval low must be <= high")

    def intersect(self, low: int, high: int) -> "IntInterval | None":
        narrowed_low, narrowed_high = max(self.low, low), min(self.high, high)
        if narrowed_low > narrowed_high:
            return None
        return IntInterval(narrowed_low, narrowed_high)


@dataclass(frozen=True)
class PublicObservation:
    """Minimal public facts; do not populate from private Actor fields."""
    event_id: str
    kind: str
    tick: int
    weapon: DuelWeapon | None = None
    base_ticks: int | None = None
    elapsed_ticks: int | None = None
    stamina_cost: int | None = None
    fatigue_cost: int | None = None
    available: bool | None = None
    action_kind: str | None = None
    completed_dash: bool = False
    observed_force: int | None = None
    balance_value: int | None = None
    parry_force: int | None = None
    parry_outcome: str | None = None
    precision_evasion: int | None = None
    precision_modifier: int | None = None
    precision_hit: bool | None = None

    def __post_init__(self):
        if not self.event_id or self.tick < 0:
            raise ValueError("observation needs a stable event ID and nonnegative tick")


@dataclass(frozen=True)
class BeliefEvidence:
    event_id: str
    kind: str
    intervals: tuple[tuple[str, IntInterval], ...]
    inconsistency: str | None = None


@dataclass(frozen=True)
class CombatBelief:
    weapon_reach: IntInterval = IntInterval(1, 4)
    weapon_handling: IntInterval = IntInterval(50, 150)
    weapon_mass: IntInterval = IntInterval(1, 8)
    effective_swing_speed: IntInterval = IntInterval(25, 300)
    accuracy: IntInterval = IntInterval(0, 100)
    strength: IntInterval = IntInterval(0, 8)
    body_stability: IntInterval = IntInterval(0, 100)
    balance: IntInterval = IntInterval(0, 100)
    stamina: IntInterval = IntInterval(0, 100)
    fatigue: IntInterval = IntInterval(0, 100)
    parry_capacity: IntInterval = IntInterval(10, 76)
    observed_event_ids: tuple[str, ...] = ()
    evidence: tuple[BeliefEvidence, ...] = ()
    inconsistencies: tuple[str, ...] = ()

    def observe(self, observation: PublicObservation) -> "CombatBelief":
        """Return a new belief narrowed only by facts in the public event."""
        if not isinstance(observation, PublicObservation):
            raise TypeError("belief updates require a PublicObservation event")
        if observation.event_id in self.observed_event_ids:
            return self
        belief = self
        changed_fields: set[str] = set()
        conflicts: list[str] = []

        def narrow(field_name: str, low: int, high: int):
            nonlocal belief
            current = getattr(belief, field_name)
            result = current.intersect(low, high)
            if result is None:
                conflicts.append(f"{observation.event_id}:{field_name}:{low}..{high}")
            elif result != current:
                belief = replace(belief, **{field_name: result})
                changed_fields.add(field_name)

        if observation.kind == "weapon_seen" and observation.weapon is not None:
            narrow("weapon_reach", observation.weapon.reach_tiles,
                   observation.weapon.reach_tiles)
            narrow("weapon_handling", observation.weapon.handling,
                   observation.weapon.handling)
            narrow("weapon_mass", observation.weapon.mass, observation.weapon.mass)

        elif observation.kind == "action_started":
            cost, fatigue = observation.stamina_cost, observation.fatigue_cost
            if cost is None or fatigue is None or cost < 0 or fatigue < 0:
                conflicts.append(f"{observation.event_id}:incomplete_action_started")
            elif observation.available is False:
                # Rejected before payment: current stamina was below the cost.
                narrow("stamina", 0, cost - 1)
            else:
                # Accepted means the pre-cost pool covered the cost. Report the
                # post-cost bound, without pretending to know the exact amount.
                if cost > 100:
                    conflicts.append(f"{observation.event_id}:cost_out_of_range")
                else:
                    narrow("stamina", 0, 100 - cost)
                    narrow("fatigue", min(100, fatigue), 100)

        elif observation.kind == "timed_action":
            base, elapsed = observation.base_ticks, observation.elapsed_ticks
            if base is None or elapsed is None or base <= 0 or elapsed <= 0:
                conflicts.append(f"{observation.event_id}:invalid_timing")
            else:
                # ceil(base*100/speed)==elapsed gives a closed integer interval.
                low = ceil_div(base * 100, elapsed)
                high = (300 if elapsed == 1
                        else ceil_div(base * 100, elapsed - 1) - 1)
                low, high = max(25, low), min(300, high)
                if low <= high:
                    narrow("effective_swing_speed", low, high)
                else:
                    conflicts.append(f"{observation.event_id}:timing_out_of_range")

        elif observation.kind == "parry_comparison":
            force, outcome = observation.parry_force, observation.parry_outcome
            if force is None or force < 0 or outcome not in {"parried", "overpowered"}:
                conflicts.append(f"{observation.event_id}:invalid_parry_evidence")
            elif outcome == "parried":
                narrow("parry_capacity", force, 76)
            else:
                narrow("parry_capacity", 10, force - 1)

        elif observation.kind == "force_seen":
            force = observation.observed_force
            base_force = {"light": 8, "heavy": 14, "dash_strike": 8}.get(
                observation.action_kind)
            if force is None or force < 0 or base_force is None:
                conflicts.append(f"{observation.event_id}:invalid_force_evidence")
            else:
                momentum = 10 if observation.completed_dash else 0
                mass = belief.weapon_mass
                min_strength_numerator = force - base_force - momentum - mass.high
                low_strength = max(
                    0, -((-min_strength_numerator) // 2),
                )
                high_strength = min(
                    8, (force - base_force - momentum - mass.low) // 2,
                )
                narrow("strength", low_strength, high_strength)

        elif observation.kind == "balance_seen":
            if observation.balance_value is None:
                conflicts.append(f"{observation.event_id}:invalid_balance_evidence")
            else:
                narrow("balance", observation.balance_value,
                       observation.balance_value)

        elif observation.kind == "precision_check":
            evasion = observation.precision_evasion
            modifier = observation.precision_modifier
            hit = observation.precision_hit
            if evasion is None or modifier is None or hit is None:
                conflicts.append(f"{observation.event_id}:invalid_precision_evidence")
            elif hit:
                narrow("accuracy", evasion - modifier, 100)
            else:
                narrow("accuracy", 0, evasion - modifier - 1)

        elif observation.kind not in {"waiting", "observing", "recovery", "movement"}:
            conflicts.append(f"{observation.event_id}:unknown_public_event")

        # Capacity is composite. Only narrow visible body stability when the
        # currently supported strength, balance and handling intervals allow
        # a range calculation; never assign an exact hidden body value.
        if observation.kind in {"parry_comparison", "force_seen", "balance_seen", "weapon_seen"}:
            possible = []
            for stability in range(belief.body_stability.low,
                                   belief.body_stability.high + 1):
                capacity_low = (2 * belief.strength.low + stability // 10
                                + belief.balance.low // 5
                                + belief.weapon_handling.low // 5)
                capacity_high = (2 * belief.strength.high + stability // 10
                                 + belief.balance.high // 5
                                 + belief.weapon_handling.high // 5)
                if (capacity_low <= belief.parry_capacity.high
                        and capacity_high >= belief.parry_capacity.low):
                    possible.append(stability)
            if possible:
                narrow("body_stability", min(possible), max(possible))
            else:
                conflicts.append(f"{observation.event_id}:capacity_has_no_body_solution")

        return replace(
            belief,
            observed_event_ids=belief.observed_event_ids + (observation.event_id,),
            evidence=belief.evidence + (BeliefEvidence(
                event_id=observation.event_id,
                kind=observation.kind,
                intervals=tuple((name, getattr(belief, name))
                                for name in sorted(changed_fields)),
                inconsistency=conflicts[0] if conflicts else None,
            ),),
            inconsistencies=belief.inconsistencies + tuple(conflicts),
        )
