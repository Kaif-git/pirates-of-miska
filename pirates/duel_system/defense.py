"""Observable-information selector for the single contextual Defend command."""
from dataclasses import dataclass

from .geometry import attack_cells, bearing_from
from .model import ActionKind, EventType
from .stats import effective_stats, phase_ticks, stamina_cost, weapon_reach


@dataclass(frozen=True)
class DefensePlan:
    choice: object
    response: str
    reason: str
    safe: bool
    arrival_tick: int | None = None
    cost: float = 0.0


_ATTACKS = {ActionKind.LIGHT, ActionKind.HEAVY, ActionKind.DASH_LIGHT,
            ActionKind.THRUST, ActionKind.DISARM}
_MOVES = (ActionKind.WALK, ActionKind.RUN, ActionKind.DASH)
_DIRECTIONS = ((0, -1), (1, -1), (1, 0), (1, 1),
               (0, 1), (-1, 1), (-1, 0), (-1, -1))


def _observed_attack(state, defender, attacker):
    action = attacker.action
    if (action is None or action.kind not in _ATTACKS or action.strike_tick is None
            or action.strike_tick < state.tick):
        return False
    if not state.can_see(defender, attacker):
        return False
    if len(state.fighters) <= 2:
        return action.target_id == defender.id
    started = next((event for event in reversed(state.event_log)
                    if event.type == EventType.ACTION_START and event.actor == attacker.id
                    and event.tick == action.start_tick), None)
    return bool(started and defender.id in started.witnesses
                and started.deltas.get("target_id") == defender.id)


def _position_at(actor, tick):
    position = actor.pos
    action = actor.action
    if action:
        for move_tick, destination in action.path:
            if move_tick < tick:
                position = destination
    return position


def _target_position_at(defender, path, tick):
    position = defender.pos
    for move_tick, destination in path:
        if move_tick < tick:
            position = destination
    return position


def _threat_bearing(state, defender, attacker, target_path):
    action = attacker.action
    if action.bearing_lock_tick is not None and action.bearing_lock_tick <= state.tick:
        return action.attack_bearing
    lock_tick = action.bearing_lock_tick or state.tick
    return bearing_from(_position_at(attacker, lock_tick),
                        _target_position_at(defender, target_path, lock_tick))


def _covered_at_impact(state, defender, attacker, target_path):
    action = attacker.action
    bearing = _threat_bearing(state, defender, attacker, target_path)
    origin = _position_at(attacker, action.strike_tick)
    target = _target_position_at(defender, target_path, action.strike_tick)
    return target in attack_cells(origin, bearing, action.kind)


def choose_defend(state, fighter_id):
    """Choose one timely response from observed commitments and legal destinations.

    Unknown or unobserved choices are intentionally ignored. With no known threat,
    Defend makes a bounded anticipatory blade commitment rather than predicting AI.
    """
    from .engine import Choice, legality, commit_action

    defender = state.fighter(fighter_id)
    threats = [enemy for enemy in state.hostiles_for(fighter_id)
               if _observed_attack(state, defender, enemy)]
    if not threats:
        return DefensePlan(Choice(ActionKind.GUARD, contextual_defense=True),
                           "blade-ready", "No committed threat is visible; prepare a bounded blade defense.",
                           False)

    # A timely supported blade response is preferable to moving. Heavy is treated
    # as likely to overload a novice brace unless a safe route is unavailable.
    stats = effective_stats(defender, state.config, state.tick)
    guard_ready = state.tick + phase_ticks(
        state.config["guard"]["defend_raise"], stats.speed, state.config)
    guard_window = state.config["guard"]["defend_parry_window"]
    if all(enemy.action.kind != ActionKind.HEAVY for enemy in threats):
        if all(guard_ready <= enemy.action.strike_tick < guard_ready + guard_window
               for enemy in threats):
            return DefensePlan(Choice(ActionKind.GUARD, contextual_defense=True),
                               "parry", "The visible contact fits the prepared parry window.", True,
                               min(enemy.action.strike_tick for enemy in threats))

    safe_moves = []
    for kind in _MOVES:
        for direction in _DIRECTIONS:
            choice = Choice(kind, direction=direction, tiles=1)
            error = legality(state, defender.id, choice)
            if error:
                continue
            simulation = state.clone()
            move = commit_action(simulation, defender.id, choice)
            path = move.path
            if not path:
                continue
            safe = True
            deadlines = []
            for enemy in threats:
                impact = enemy.action.strike_tick
                first_step = path[0][0]
                if first_step >= impact or _covered_at_impact(state, defender, enemy, path):
                    safe = False
                    break
                deadlines.append(impact)
            if safe:
                safe_moves.append((move.cost_paid, kind.value, direction, choice,
                                   path[0][0], min(deadlines)))
    if safe_moves:
        cost, kind, direction, choice, arrival, impact = min(
            safe_moves, key=lambda row: (row[0], row[4], row[1],
                                         _DIRECTIONS.index(row[2])))
        direction_name = {(0, -1): "north", (1, -1): "northeast", (1, 0): "east",
                          (1, 1): "southeast", (0, 1): "south", (-1, 1): "southwest",
                          (-1, 0): "west", (-1, -1): "northwest"}[direction]
        return DefensePlan(choice, f"{kind.lower()} {direction_name}",
                           f"Leaves every visible attack line at t{arrival}, before impact t{impact}; least stamina cost.",
                           True, arrival, cost)

    return DefensePlan(Choice(ActionKind.GUARD, contextual_defense=True),
                       "brace", "No legal step leaves all visible threat lines before impact; use best-effort blade defense.",
                       False, min(enemy.action.strike_tick for enemy in threats))
