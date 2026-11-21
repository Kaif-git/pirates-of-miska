"""Observable-information selector for the single contextual Defend command."""
from dataclasses import dataclass

from .geometry import attack_cells, attack_reach, bearing_from
from .model import ActionKind, EventType, Zone
from .stats import effective_stats, phase_ticks, stamina_cost, weapon_reach


@dataclass(frozen=True)
class DefensePlan:
    choice: object
    response: str
    reason: str
    safe: bool
    arrival_tick: int | None = None
    cost: float = 0.0


_ATTACKS = {ActionKind.LIGHT, ActionKind.HEAVY, ActionKind.DASH_LIGHT, ActionKind.DASH_SLASH,
            ActionKind.THRUST, ActionKind.DISARM}
_THREATS = _ATTACKS | {ActionKind.SHOVE}
_MOVES = (ActionKind.WALK, ActionKind.RUN, ActionKind.DASH)
_DIRECTIONS = ((0, -1), (1, -1), (1, 0), (1, 1),
               (0, 1), (-1, 1), (-1, 0), (-1, -1))


def _observed_attack(state, defender, attacker):
    action = attacker.action
    if action is None or action.kind not in _THREATS:
        return False
    impact = _impact_tick(action)
    if impact is None or impact < state.tick:
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


def _impact_tick(action):
    if action.kind == ActionKind.SHOVE:
        return action.start_tick + action.windup_len
    return action.strike_tick


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
    impact = _impact_tick(action)
    origin = _position_at(attacker, impact)
    target = _target_position_at(defender, target_path, impact)
    if action.kind == ActionKind.SHOVE:
        return max(abs(origin[0]-target[0]), abs(origin[1]-target[1])) <= 1
    bearing = _threat_bearing(state, defender, attacker, target_path)
    return target in attack_cells(origin, bearing, action.kind)


def choose_defend(state, fighter_id):
    """Choose one timely response from observed commitments and legal destinations.

    Unknown or unobserved choices are intentionally ignored. With no known threat,
    Defend makes a bounded anticipatory blade commitment rather than predicting AI.
    """
    from .engine import Choice, legality, movement_path

    defender = state.fighter(fighter_id)
    guard_cost = stamina_cost(
        defender, state.config["guard"]["defend_setup_cost"], state.config)
    threats = [enemy for enemy in state.hostiles_for(fighter_id)
               if _observed_attack(state, defender, enemy)]
    if not threats:
        return DefensePlan(Choice(ActionKind.GUARD, contextual_defense=True),
                           "blade-ready", "No committed threat is visible; prepare a bounded blade defense.",
                           False, cost=guard_cost)

    # A timely, strength-supported blade response is preferable to moving.
    stats = effective_stats(defender, state.config, state.tick)
    guard_ready = state.tick + phase_ticks(
        state.config["guard"]["defend_raise"], stats.speed, state.config)
    guard_hold_end = guard_ready + phase_ticks(
        state.config["guard"]["defend_hold"], stats.speed, state.config)
    guard_window = state.config["guard"]["defend_parry_window"]
    guard_line = {Zone(zone) for zone in state.config["zones"]["guard_line"]}
    supported_parries = []
    for enemy in threats:
        action = enemy.action
        if action.kind == ActionKind.SHOVE:
            break
        impact = _impact_tick(action)
        heavy = action.kind == ActionKind.HEAVY
        window_end = guard_ready + guard_window + (
            state.config["guard"]["heavy_window_bonus"] if heavy else 0)
        strike_zone = Zone(action.aim) if action.aim else None
        covers_zone = strike_zone in guard_line or (
            action.kind == ActionKind.DISARM and strike_zone == Zone.HAND)
        ratio = state.config["guard"]["parry_str_ratio"]["heavy" if heavy else "light"]
        attacker_strength = effective_stats(enemy, state.config, state.tick).strength
        supported = (covers_zone and guard_ready <= impact < min(window_end, guard_hold_end)
                     and stats.strength >= ratio * attacker_strength)
        if not supported:
            break
        supported_parries.append(impact)
    if len(supported_parries) == len(threats):
        return DefensePlan(Choice(ActionKind.GUARD, contextual_defense=True),
                           "parry", "The visible contact fits the prepared parry window and strength ratio.",
                            True, min(supported_parries), guard_cost)

    # A visible Dash+Light/Shove commitment can be met by the same single
    # defensive action: a legal side/back Dash+Light that lands before contact.
    counter_threats = [enemy for enemy in threats
                       if enemy.action.kind in {ActionKind.DASH_LIGHT, ActionKind.SHOVE}]
    if counter_threats and len(counter_threats) == len(threats):
        for enemy in counter_threats:
            dx = (enemy.pos[0] > defender.pos[0]) - (enemy.pos[0] < defender.pos[0])
            dy = (enemy.pos[1] > defender.pos[1]) - (enemy.pos[1] < defender.pos[1])
            back = (-dx, -dy)
            directions = (back, (dy, -dx), (-dy, dx))
            for direction in directions:
                choice = Choice(ActionKind.DASH_LIGHT, Zone.TORSO, direction=direction,
                                tiles=1, target_id=enemy.id)
                if legality(state, defender.id, choice):
                    continue
                path = movement_path(state, defender, choice)
                if not path:
                    continue
                move_tick = state.tick + phase_ticks(
                    state.config["actions"][ActionKind.DASH_LIGHT.value]["per_tile"],
                    stats.speed, state.config)
                strike_tick = move_tick + phase_ticks(
                    state.config["actions"][ActionKind.DASH_LIGHT.value]["windup"],
                    stats.speed, state.config)
                threat_tick = _impact_tick(enemy.action)
                # A strike resolves before a same-tick Shove contest, but must
                # beat a sword contact rather than trading into it.
                if (strike_tick > threat_tick or
                        strike_tick == threat_tick and enemy.action.kind != ActionKind.SHOVE):
                    continue
                origin = path[-1]
                target = _position_at(enemy, strike_tick)
                gap = max(abs(origin[0]-target[0]), abs(origin[1]-target[1]))
                bearing = bearing_from(origin, target)
                if (gap > attack_reach(ActionKind.DASH_LIGHT)
                        or target not in attack_cells(origin, bearing, ActionKind.DASH_LIGHT)):
                    continue
                cost = stamina_cost(
                    defender, state.config["actions"][ActionKind.DASH_LIGHT.value]["stamina"],
                    state.config)
                return DefensePlan(
                    choice, "dash-slash " + ("back" if direction == back else "side"),
                    f"Visible {enemy.action.kind.value.replace('_', ' ').title()} is met by a legal Dash+Light; "
                    f"your contact is due t{strike_tick}, before its t{threat_tick} resolution.",
                    True, strike_tick, cost)

    safe_moves = []
    for kind in _MOVES:
        for direction in _DIRECTIONS:
            choice = Choice(kind, direction=direction, tiles=1)
            error = legality(state, defender.id, choice)
            if error:
                continue
            path = movement_path(state, defender, choice)
            if not path:
                continue
            speed = effective_stats(defender, state.config, state.tick).speed
            arrival = state.tick + phase_ticks(
                state.config["actions"][kind.value]["per_tile"], speed, state.config)
            safe = True
            deadlines = []
            for enemy in threats:
                impact = _impact_tick(enemy.action)
                timed_path = [(arrival, path[0])]
                if arrival >= impact or _covered_at_impact(state, defender, enemy, timed_path):
                    safe = False
                    break
                deadlines.append(impact)
            if safe:
                base_cost = state.config["actions"][kind.value]["stamina"] * (
                    choice.tiles if kind == ActionKind.RUN else 1)
                cost = stamina_cost(defender, base_cost, state.config)
                safe_moves.append((cost, kind.value, direction, choice,
                                   arrival, min(deadlines)))
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
                        False, min(_impact_tick(enemy.action) for enemy in threats), guard_cost)
