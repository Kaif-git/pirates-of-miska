"""Tick scheduler and commands; explicit owned state is the only mutable input."""
from dataclasses import asdict, dataclass
import math

from .model import ActionKind, ActiveAction, EventType, GroundWeapon, Phase, Zone
from .geometry import bearing_from, lock_delay
from .stats import (clamp, effective_stats, fatigue_gain, max_stamina, pay_stamina,
                    phase_ticks, refresh_exhaustion, stamina_cost, weapon_reach)


ATTACK_KINDS = {ActionKind.LIGHT, ActionKind.HEAVY, ActionKind.DASH_LIGHT,
                ActionKind.THRUST, ActionKind.DISARM}
MOVEMENT_KINDS = {ActionKind.DASH, ActionKind.DASH_LIGHT, ActionKind.WALK,
                  ActionKind.RUN, ActionKind.CRAWL, ActionKind.DISENGAGE}


@dataclass(frozen=True)
class Choice:
    kind: ActionKind
    aim: Zone | None = None
    direction: tuple[int, int] | None = None
    tiles: int = 1
    item_id: str | None = None
    target_id: str | None = None
    contextual_defense: bool = False


def drop_weapon(state, fighter, cause):
    weapon = fighter.equipment.weapon
    fighter.disarmed = True
    if weapon is None:
        return None
    fighter.equipment.weapon = None
    dropped = GroundWeapon(weapon, fighter.pos)
    state.ground_weapons.append(dropped)
    state.emit(EventType.WEAPON_DROP, fighter.id,
               deltas={"item_id": weapon.item_id, "name": weapon.name,
                       "condition": weapon.condition, "position": fighter.pos, "cause": cause},
               cause_tags=("WEAPON_DROPPED", cause))
    return dropped


def apply_weapon_stress(state, fighter, contact, *, opponent=None):
    """Apply deterministic wear only after an actual hard weapon contact."""
    weapon = fighter.equipment.weapon
    if weapon is None or fighter.disarmed:
        return
    rule = state.config["weapon_integrity"]
    base = rule["hard_contact_wear"][contact]
    before = weapon.condition
    condition_factor = 2 - before / 100
    wear = base * rule["materials"][weapon.material]["wear_mult"] * condition_factor
    weapon.condition = max(0, before - wear)
    state.emit(EventType.WEAPON_STRESS, fighter.id, opponent.id if opponent else None,
               deltas={"item_id": weapon.item_id, "name": weapon.name,
                       "contact": contact, "material": weapon.material,
                       "condition_before": before, "wear": before-weapon.condition,
                       "condition_after": weapon.condition},
               cause_tags=("WEAPON_HARD_CONTACT", contact))
    if weapon.condition == 0:
        state.emit(EventType.WEAPON_BREAK, fighter.id, opponent.id if opponent else None,
                   deltas={"item_id": weapon.item_id, "name": weapon.name,
                           "material": weapon.material, "contact": contact},
                   cause_tags=("WEAPON_BROKEN", contact))
        drop_weapon(state, fighter, "BROKEN")


def actor_for(state, fighter_id):
    return next(fighter for fighter in state.fighters if fighter.id == fighter_id)


def target_for(state, fighter, target_id=None):
    try:
        if target_id is not None:
            return state.primary_opponent(fighter.id, target_id)
        if len(state.fighters) > 2:
            visible = [target for target in state.hostiles_for(fighter.id)
                       if state.can_see(fighter, target)]
            return min(visible, key=lambda target: (
                max(abs(fighter.pos[0]-target.pos[0]), abs(fighter.pos[1]-target.pos[1])), target.id)) if visible else None
        return state.primary_opponent(fighter.id)
    except StopIteration:
        return None


def movement_path(state, fighter, choice):
    if choice.direction is None:
        return []
    dx, dy = choice.direction
    return [(fighter.pos[0] + dx * step, fighter.pos[1] + dy * step)
            for step in range(1, choice.tiles + 1)]


def adrenaline_trigger_reasons(fighter, config):
    rule = config["adrenaline"]
    reasons = []
    if fighter.blood <= rule["trigger_blood_max"]:
        reasons.append("blood")
    if fighter.pain >= rule["trigger_pain_min"]:
        reasons.append("pain")
    if fighter.fatigue >= rule["trigger_fatigue_min"]:
        reasons.append("fatigue")
    if fighter.stability <= rule["trigger_stability_max"]:
        reasons.append("stability")
    return tuple(reasons)


def legality(state, fighter_id, choice):
    """Empty string means legal; rejection reads no RNG and spends nothing."""
    fighter = actor_for(state, fighter_id)
    if state.end_reason:
        return "Fight is over"
    if not fighter.can_act(state.tick):
        return "Fighter cannot act (unconscious, defeated or staggered)"
    if fighter.action is not None:
        return "Already committed to an action"
    try:
        kind = ActionKind(choice.kind)
    except ValueError:
        return "Unknown action"
    if fighter.grounded and kind not in {ActionKind.GUARD, ActionKind.CRAWL, ActionKind.RISE}:
        return "Grounded: cover, crawl or rise"
    if not fighter.grounded and kind == ActionKind.CRAWL:
        return "Only a grounded fighter can crawl"
    if not fighter.grounded and kind == ActionKind.RISE:
        return "Only a grounded fighter can rise"
    if (fighter.exhausted or fighter.stamina <= 0) and kind in {
            ActionKind.HEAVY, ActionKind.DASH, ActionKind.DASH_LIGHT}:
        return "Exhausted: no heavy or dash"
    if fighter.disarmed and (kind in {ActionKind.HEAVY, ActionKind.DASH_LIGHT,
                                      ActionKind.THRUST, ActionKind.DISARM}
                             or kind == ActionKind.GUARD and not fighter.grounded):
        return "Disarmed: only light attacks"
    if kind in {ActionKind.DASH, ActionKind.DASH_LIGHT} and any(
            status.active(state.tick) and status.no_dash for status in fighter.statuses):
        return "Leg injury prevents dashing"
    if kind in ATTACK_KINDS:
        if choice.aim is None:
            return "Choose a target zone"
        try:
            Zone(choice.aim)
        except ValueError:
            return "Unknown target zone"
        if kind == ActionKind.DISARM and Zone(choice.aim) != Zone.HAND:
            return "Disarm must target the hand"
        if kind == ActionKind.DISARM and not fighter.riposte:
            return "Disarm requires a riposte earned by a parry"
        target = target_for(state, fighter, choice.target_id)
        if target is None:
            return "Choose a living hostile target"
        if len(state.fighters) > 2 and not state.can_see(fighter, target):
            return "Target is not currently visible"
        if Zone(choice.aim) == Zone.EYE:
            if fighter.permanent_injuries.eyes_damaged >= 2:
                return "Bilateral eye injury prevents acquiring a precise eye target"
            if target.permanent_injuries.eyes_damaged >= 2:
                return "No functioning eye remains to target"
        if kind == ActionKind.DISARM and target.disarmed:
            return "Opponent has no weapon to disarm"
    elif kind in {ActionKind.FEINT, ActionKind.SHOVE, ActionKind.DISENGAGE}:
        target = target_for(state, fighter, choice.target_id)
        if target is None:
            return "Choose a living hostile target"
        if len(state.fighters) > 2 and not state.can_see(fighter, target):
            return "Target is not currently visible"
    if kind in {ActionKind.THRUST, ActionKind.DISARM}:
        if fighter.disarmed or fighter.equipment.weapon is None:
            return "A weapon is required for this action"
        if fighter.equipment.weapon.damage_type == "BLUNT":
            return "This weapon cannot thrust or disarm"
    if kind in MOVEMENT_KINDS:
        direction = choice.direction
        if (not isinstance(direction, (tuple, list)) or len(direction) != 2
                or any(isinstance(v, bool) or not isinstance(v, int) or abs(v) > 1 for v in direction)
                or tuple(direction) == (0, 0)):
            return "Choose one of eight movement directions"
        if (isinstance(choice.tiles, bool) or not isinstance(choice.tiles, int)
                or not 1 <= choice.tiles <= state.config["actions"][kind.value]["max_tiles"]):
            return "Invalid movement distance"
        path = movement_path(state, fighter, choice)
        for index, pos in enumerate(path):
            if state.walkable is not None and pos not in state.walkable:
                return f"Movement blocked at {pos}: outside the arena floor"
            blocker = next((other for other in state.fighters if other.id != fighter.id
                            and state.occupies_tile(other) and other.pos == pos), None)
            if blocker and (len(state.fighters) == 2 or index < len(path) - 1):
                return f"Movement blocked at {pos}: {blocker.name} occupies the path"
        if kind == ActionKind.DISENGAGE:
            if choice.tiles != 1:
                return "Disengage moves exactly one tile"
            other = target_for(state, fighter, choice.target_id)
            before = max(abs(fighter.pos[0] - other.pos[0]), abs(fighter.pos[1] - other.pos[1]))
            destination = movement_path(state, fighter, choice)[-1]
            after = max(abs(destination[0] - other.pos[0]), abs(destination[1] - other.pos[1]))
            if after <= before:
                return "Disengage must increase distance from the opponent"
    if kind == ActionKind.SHOVE:
        other = target_for(state, fighter, choice.target_id)
        if max(abs(fighter.pos[0] - other.pos[0]), abs(fighter.pos[1] - other.pos[1])) > 1:
            return "Shove requires close contact"
    if kind == ActionKind.ADRENALINE:
        if fighter.adrenaline_used:
            return "Adrenaline already used this duel"
        if not adrenaline_trigger_reasons(fighter, state.config):
            return "Adrenaline requires meaningful danger or exertion"
    if kind == ActionKind.BANDAGE:
        if fighter.bandages <= 0:
            return "No bandage carried"
        if not any(wound.bleed_per_turn > 0 and not wound.bandaged for wound in fighter.wounds):
            return "No unbandaged bleeding wound"
    if kind == ActionKind.EAT and fighter.food <= 0:
        return "No food carried"
    if kind == ActionKind.DRINK and fighter.water <= 0:
        return "No water carried"
    if kind == ActionKind.PICKUP:
        if fighter.hand_severed:
            return "A severed hand cannot grip or pick up a weapon"
        if not choice.item_id:
            return "Select a ground weapon to pick up"
        item = next((row for row in state.ground_weapons if row.weapon.item_id == choice.item_id), None)
        if item is None:
            return "That weapon is no longer on the ground"
        if item.weapon.condition <= 0:
            return "That weapon is broken and cannot be equipped"
        distance = max(abs(fighter.pos[0]-item.pos[0]), abs(fighter.pos[1]-item.pos[1]))
        if distance > state.config["weapon_integrity"]["pickup_range"]:
            return "That weapon is out of pickup range"
    return ""


def commit_action(state, fighter_id, choice):
    error = legality(state, fighter_id, choice)
    if error:
        raise ValueError(error)
    fighter = actor_for(state, fighter_id)
    cfg = state.config
    kind = ActionKind(choice.kind)
    target = (target_for(state, fighter, choice.target_id)
              if kind in ATTACK_KINDS or kind in {ActionKind.FEINT, ActionKind.SHOVE, ActionKind.DISENGAGE}
              else None)
    table = cfg["actions"][kind.value]
    locked = effective_stats(fighter, cfg, state.tick)
    phases, path = [], []
    cursor = state.tick
    if kind in MOVEMENT_KINDS:
        tile_ticks = phase_ticks(table["per_tile"], locked.speed, cfg)
        for index, pos in enumerate(movement_path(state, fighter, choice), start=1):
            path.append((cursor + index * tile_ticks, pos))
        movement_end = cursor + len(path) * tile_ticks
        phases.append(Phase("DASH" if kind in {ActionKind.DASH, ActionKind.DASH_LIGHT} else "MOVE",
                            cursor, movement_end))
        cursor = movement_end
    windup = phase_ticks(table.get("windup", 0), locked.speed, cfg)
    if kind == ActionKind.GUARD and choice.contextual_defense:
        windup = phase_ticks(cfg["guard"]["defend_raise"], locked.speed, cfg)
    strike_tick = None
    if kind in ATTACK_KINDS:
        if fighter.riposte:
            riposte_mult = (cfg["guard"]["riposte_disarm_windup_mult"]
                            if kind == ActionKind.DISARM else cfg["guard"]["riposte_windup_mult"])
            windup = max(cfg["time"]["min_phase_ticks"], math.ceil(windup * riposte_mult))
        phases.append(Phase("WINDUP", cursor, cursor + windup))
        cursor += windup
        strike_tick = cursor
        # Contact is resolved on strike_tick; a zero active phase makes readiness
        # relative to that point while preserving the legacy scaled-phase model.
        active = phase_ticks(table.get("active", 0), locked.speed, cfg)
        phases.append(Phase("ACTIVE", cursor, cursor + active))
        cursor += active
    elif kind == ActionKind.GUARD:
        phases.append(Phase("GUARD_RAISE", cursor, cursor + windup))
        cursor += windup
        if choice.contextual_defense:
            hold = phase_ticks(cfg["guard"]["defend_hold"], locked.speed, cfg)
            phases.append(Phase("GUARD_HOLD", cursor, cursor + hold))
            cursor += hold
            lower = phase_ticks(cfg["guard"]["defend_lower"], locked.speed, cfg,
                                recovery_weight=fighter.equipment.total_weight)
            if lower:
                phases.append(Phase("RECOVERY", cursor, cursor + lower))
                cursor += lower
        else:
            phases.append(Phase("GUARD_HOLD", cursor, None))
    elif windup:
        phases.append(Phase(kind.value, cursor, cursor + windup))
        cursor += windup
    recovery = phase_ticks(table.get("recovery", 0), locked.speed, cfg,
                           recovery_weight=fighter.equipment.total_weight)
    if recovery and kind != ActionKind.GUARD:
        phases.append(Phase("RECOVERY", cursor, cursor + recovery))
        cursor += recovery
    base_cost = (cfg["guard"]["defend_setup_cost"] if kind == ActionKind.GUARD
                 and choice.contextual_defense else
                 table["stamina"] * (choice.tiles if kind == ActionKind.RUN else 1))
    cost = stamina_cost(fighter, base_cost, cfg)
    fatigue_before = fighter.fatigue
    paid = pay_stamina(state, fighter, cost)
    guard_ready = state.tick + windup if kind == ActionKind.GUARD else None
    weak_penalty = sum(s.parry_penalty for s in fighter.statuses if s.active(state.tick))
    parry_window = (cfg["guard"]["defend_parry_window"] if kind == ActionKind.GUARD
                    and choice.contextual_defense else max(cfg["guard"]["parry_min_window"], round(
        cfg["guard"]["parry_speed_coef"] * locked.speed
        + cfg["guard"]["parry_acc_coef"] * locked.accuracy) - weak_penalty))
    fighter.action = ActiveAction(kind, Zone(choice.aim) if choice.aim else None,
                                  tuple(choice.direction) if choice.direction else None,
                                  state.tick, phases, strike_tick, paid, locked,
                                   None if kind == ActionKind.GUARD and not choice.contextual_defense else cursor,
                                  path, windup, table.get("power", 0), table.get("acc_mod", 0),
                                   guard_ready, parry_window, item_id=choice.item_id,
                                     target_id=target.id if target else None,
                                     attack_bearing=(bearing_from(fighter.pos, target.pos)
                                                     if target and state.can_see(fighter, target) else None),
                                       bearing_lock_tick=(state.tick + phase_ticks(
                                           lock_delay(kind), locked.speed, cfg)
                                                          if target and lock_delay(kind) else None),
                                      contextual_defense=choice.contextual_defense)
    if kind == ActionKind.ADRENALINE:
        fighter.adrenaline_used = True
    if kind in ATTACK_KINDS:
        fighter.riposte = False
    fighter.previous_action_cat = fighter.last_action_cat
    if kind in {ActionKind.DASH, ActionKind.RUN, ActionKind.WALK, ActionKind.DISENGAGE}:
        final_pos = path[-1][1]
        if target:
            old_distance = max(abs(fighter.pos[0] - target.pos[0]), abs(fighter.pos[1] - target.pos[1]))
            new_distance = max(abs(final_pos[0] - target.pos[0]), abs(final_pos[1] - target.pos[1]))
        else:
            old_distance = new_distance = 0
        fighter.last_action_cat = (("DASH_IN" if new_distance < old_distance else "DASH_OUT")
                                   if kind == ActionKind.DASH and target else
                                   kind.value if kind == ActionKind.DISENGAGE else "MOVE")
    else:
        fighter.last_action_cat = "LIGHT" if kind == ActionKind.DASH_LIGHT else kind.value
    fighter.action_history.append(kind.value)
    state.emit(EventType.ACTION_START, fighter.id, target.id if target else None, deltas={"kind": kind.value,
                 "aim": choice.aim.value if isinstance(choice.aim, Zone) else choice.aim,
                 "target_id": target.id if target else None,
                "direction": tuple(choice.direction) if choice.direction else None, "tiles": choice.tiles,
                 "paid": paid, "cost": cost, "strike_tick": strike_tick, "end_tick": fighter.action.end_tick,
                 "contextual_defense": choice.contextual_defense,
                "speed": locked.speed, "strength": locked.strength, "accuracy": locked.accuracy,
                "endurance": locked.endurance, "fatigue_penalty": locked.fatigue_penalty,
                "fatigue_added": fighter.fatigue-fatigue_before,
                "pain_penalty": locked.pain_penalty, "blood_penalty": locked.blood_penalty,
                "weight": fighter.equipment.total_weight, "windup_ticks": windup})
    if kind == ActionKind.ADRENALINE:
        state.emit(EventType.ADRENALINE, fighter.id,
                   deltas={"outcome": "COMMITTED", "trigger_reasons": adrenaline_trigger_reasons(fighter, cfg)},
                   cause_tags=("ADRENALINE_COMMITTED",))
    if kind == ActionKind.YIELD:
        fighter.yielded = True
        fighter.action = None
        check_end_conditions(state)
    return fighter.action


def decide_batch(state, chooser):
    """Gather all free choices from independent copies of ONE pre-commit snapshot."""
    free = [fighter for fighter in state.fighters if fighter.action is None and fighter.can_act(state.tick)]
    snapshot = state.clone()
    choices = [(fighter.id, chooser(snapshot.clone(), fighter.id)) for fighter in free]
    for fighter_id, choice in choices:
        error = legality(snapshot, fighter_id, choice)
        if error:
            raise ValueError(error)
    for fighter_id, choice in choices:
        commit_action(state, fighter_id, choice)


def release_guard(state, fighter_id):
    fighter = actor_for(state, fighter_id)
    action = fighter.action
    if action is None or action.kind != ActionKind.GUARD or action.phase_at(state.tick) != "GUARD_HOLD":
        raise ValueError("Guard can only be released while held")
    ticks = phase_ticks(state.config["guard"]["lower"], action.locked_stats.speed, state.config,
                        recovery_weight=fighter.equipment.total_weight)
    action.phases = [phase for phase in action.phases if phase.name != "GUARD_HOLD"]
    action.phases.append(Phase("GUARD_HOLD", action.guard_ready_tick, state.tick))
    action.phases.append(Phase("GUARD_LOWER", state.tick, state.tick + ticks))
    action.released = True
    action.end_tick = state.tick + ticks


def apply_passive(state):
    from .mental import blood_morale, change_anger
    cfg = state.config
    turn = cfg["time"]["ticks_per_turn"]
    for fighter in state.fighters:
        if not state.combat_active(fighter):
            continue
        # Anger emits its own cause-tagged deltas through change_anger below.
        old = {key: getattr(fighter, key) for key in ("stamina", "fatigue", "blood", "pain", "nervousness")}
        action = fighter.action
        kind = action.kind if action else None
        idle = kind == ActionKind.WAIT or (action is None and state.tick - fighter.idle_since >= cfg["stamina"]["idle_delay"])
        regen = cfg["stamina"]["regen_idle_per_turn" if idle else "regen_active_per_turn"] / turn
        # Reduced circulating blood limits exertion recovery as well as force/speed.
        # Reuse the same derived impairment curve; Accuracy remains invariant.
        regen *= 1 - effective_stats(fighter, cfg, state.tick).blood_penalty
        fighter.stamina = min(max_stamina(fighter, cfg), fighter.stamina + regen)
        drain = cfg["fatigue"]["drain_idle"] if idle else cfg["fatigue"]["drain_walk"] if kind in {
            ActionKind.WALK, ActionKind.CRAWL, ActionKind.DISENGAGE} else 0
        fighter.fatigue = max(0, fighter.fatigue - drain / turn)
        if kind == ActionKind.GUARD and action.phase_at(state.tick) == "GUARD_HOLD":
            hold_cost = (cfg["guard"]["defend_hold_cost_per_tick"]
                         if action.contextual_defense else cfg["guard"]["hold_cost_per_tick"])
            pay_stamina(state, fighter, stamina_cost(fighter, hold_cost, cfg))
        fighter.pain = max(0, fighter.pain - cfg["pain"]["decay_per_turn"] / turn)
        composure = fighter.personality.composure if fighter.personality else 1
        change_anger(state, fighter, -cfg["anger"]["decay_per_turn"] * composure / turn, "ANGER_DECAY")
        if fighter.first_exchange_tick is not None:
            fighter.nervousness = max(0, fighter.nervousness - cfg["stats"]["nervousness_decay_per_turn"] / turn)
        exertion = ("walk" if kind in {ActionKind.WALK, ActionKind.CRAWL} else "dash" if kind in {ActionKind.DASH, ActionKind.RUN, ActionKind.DASH_LIGHT}
                    else "fight" if kind in (ATTACK_KINDS | {ActionKind.GUARD, ActionKind.FEINT,
                                                               ActionKind.SHOVE, ActionKind.ADRENALINE}) else "idle")
        bleed = min(cfg["blood"]["total_bleed_cap_per_turn"], sum(
            wound.bleed_per_turn * (1-cfg["blood"]["bandage_reduction"] if wound.bandaged else 1)
            for wound in fighter.wounds))
        bleed_loss = min(fighter.blood, bleed / turn * cfg["blood"]["exertion"][exertion])
        fighter.blood = max(0, fighter.blood - bleed_loss)
        blood_morale(state, fighter)
        was_exhausted = fighter.exhausted
        refresh_exhaustion(fighter, cfg)
        if was_exhausted and not fighter.exhausted:
            state.emit(EventType.RECOVER, fighter.id, cause_tags=("EXHAUSTION_CLEARED",))
        fighter.statuses = [status for status in fighter.statuses if status.active(state.tick)]
        from .stability import recover_stability
        recover_stability(state, fighter)
        deltas = {key: getattr(fighter, key) - value for key, value in old.items() if getattr(fighter, key) != value}
        if deltas:
            if bleed_loss > 0:
                deltas.update(bleed_loss=bleed_loss,
                              bleed_rate_per_turn=bleed * cfg["blood"]["exertion"][exertion],
                              bleed_exertion=exertion)
            state.emit(EventType.POOL_CHANGE, fighter.id, deltas=deltas,
                       cause_tags=("BLEEDING",) if bleed_loss > 0 else ())


def apply_pending_interrupts(state):
    for fighter_id in dict.fromkeys(state.pending_interrupts):
        fighter = actor_for(state, fighter_id)
        if fighter.action:
            action_kind = fighter.action.kind
            if action_kind == ActionKind.ADRENALINE:
                state.emit(EventType.ADRENALINE, fighter_id,
                           deltas={"outcome": "INTERRUPTED", "activated": False,
                                   "debt_applied": 0},
                           cause_tags=("ADRENALINE_INTERRUPTED",))
            state.emit(EventType.INTERRUPT, fighter_id, deltas={"kind": action_kind.value},
                       cause_tags=("RISE_INTERRUPTED",) if action_kind == ActionKind.RISE else ())
            fighter.action = None
            fighter.idle_since = state.tick
    state.pending_interrupts.clear()


def update_movement(state):
    from .mental import change_anger
    from .stability import record_successful_movement
    intents = {}
    actions = {}
    for fighter in state.fighters:
        if not state.combat_active(fighter):
            continue
        action = fighter.action
        if action is None:
            continue
        for deadline, pos in action.path:
            if deadline != state.tick:
                continue
            intents[fighter.id] = tuple(pos)
            actions[fighter.id] = action

    if not intents:
        return
    occupants = {fighter.pos: fighter for fighter in state.fighters if state.occupies_tile(fighter)}
    destination_claims = {}
    for fighter_id, destination in intents.items():
        destination_claims.setdefault(destination, []).append(fighter_id)
    blocked = {}
    for destination, claimants in destination_claims.items():
        if len(claimants) > 1:
            for fighter_id in claimants:
                blocked[fighter_id] = ("DESTINATION_CONFLICT", None, destination)

    resolved = {}
    visiting = []

    def can_move(fighter_id):
        if fighter_id in blocked:
            resolved[fighter_id] = False
            return False
        if fighter_id in resolved:
            return resolved[fighter_id]
        if fighter_id in visiting:
            # Swaps and longer movement cycles are blocked rather than passed through.
            cycle = visiting[visiting.index(fighter_id):]
            for cycle_id in cycle:
                blocked[cycle_id] = ("MOVEMENT_CYCLE", None, intents[cycle_id])
                resolved[cycle_id] = False
            return False
        visiting.append(fighter_id)
        destination = intents[fighter_id]
        occupant = occupants.get(destination)
        if occupant is None or occupant.id == fighter_id:
            result = True
        elif occupant.id not in intents:
            blocked.setdefault(fighter_id, ("OCCUPIED", occupant.id, destination))
            result = False
        else:
            result = can_move(occupant.id)
            if not result:
                blocked.setdefault(fighter_id, ("OCCUPIED", occupant.id, destination))
        visiting.remove(fighter_id)
        resolved[fighter_id] = result
        return result

    for fighter_id in sorted(intents):
        can_move(fighter_id)

    moving = [state.fighter(fighter_id) for fighter_id in sorted(intents) if resolved[fighter_id]]
    before_positions = {fighter.id: fighter.pos for fighter in moving}
    for fighter in moving:
        fighter.pos = intents[fighter.id]
    for fighter_id in sorted(intents):
        fighter = state.fighter(fighter_id)
        action, destination = actions[fighter_id], intents[fighter_id]
        if not resolved[fighter_id]:
            reason, blocker_id, position = blocked.get(
                fighter_id, ("OCCUPIED", None, destination))
            tag = "MOVEMENT_CONFLICT" if reason in {"DESTINATION_CONFLICT", "MOVEMENT_CYCLE"} else "MOVEMENT_BLOCKED"
            deltas = {"status": "movement_blocked", "position": position}
            deltas["kind"] = action.kind.value
            if len(state.fighters) > 2:
                deltas["reason"] = reason
            state.emit(EventType.STATUS, fighter.id, blocker_id, deltas=deltas, cause_tags=(tag,))
            action.path = [(at, pos) for at, pos in action.path if at <= state.tick]
            continue
        before = before_positions[fighter.id]
        state.emit(EventType.MOVE, fighter.id,
                   deltas={"from": before, "to": destination})
        record_successful_movement(state, fighter, action)
        for other in state.hostiles_for(fighter.id):
            gap = max(abs(destination[0]-other.pos[0]), abs(destination[1]-other.pos[1]))
            if (gap <= state.config["anger"]["circle_distance"]
                    and (other.last_hit_tick is None
                         or state.tick-other.last_hit_tick >= state.config["anger"]["circle_hit_window"])):
                change_anger(state, other, state.config["anger"]["circled"], "CIRCLED")


def _sign(value):
    return (value > 0) - (value < 0)


def resolve_tactical_actions(state):
    """Resolve completed Feint/Shove effects after all same-tick strikes."""
    due = {fighter.id: fighter.action for fighter in state.fighters
           if fighter.action and fighter.action.end_tick == state.tick
           and fighter.action.kind in {ActionKind.FEINT, ActionKind.SHOVE}}
    for actor_id, action in due.items():
        if action.kind != ActionKind.FEINT:
            continue
        actor = actor_for(state, actor_id)
        defender = target_for(state, actor, action.target_id)
        if defender is None:
            state.emit(EventType.FEINT, actor.id, deltas={"outcome": "NO_TARGET"},
                       cause_tags=("FEINT_NO_TARGET",))
            continue
        gap = max(abs(actor.pos[0] - defender.pos[0]), abs(actor.pos[1] - defender.pos[1]))
        guard = defender.action
        guarding = bool(guard and guard.kind == ActionKind.GUARD
                        and guard.phase_at(state.tick) in {"GUARD_RAISE", "GUARD_HOLD"})
        reach = weapon_reach(actor, state.config)
        success = gap <= reach and guarding
        drained = 0
        riposte_consumed = False
        if success:
            from .stats import pay_stamina, stamina_cost
            rule = state.config["tactics"]
            drained = pay_stamina(state, defender,
                                  stamina_cost(defender, rule["feint_guard_drain"], state.config))
            riposte_consumed = defender.riposte
            defender.riposte = False
            from .guard import stagger
            stagger(state, defender, rule["feint_disrupt_ticks"], "FEINT_DISRUPTED_GUARD")
        state.emit(EventType.FEINT, actor.id, defender.id,
                   deltas={"outcome": "GUARD_DISRUPTED" if success else "NO_EFFECT",
                            "distance": gap, "reach": reach,
                            "guard_stamina_drained": drained,
                            "riposte_consumed": riposte_consumed,
                            "opening_until_tick": (state.tick + state.config["tactics"]["feint_disrupt_ticks"]
                                                   if success else None)},
                   cause_tags=("FEINT_DISRUPTED_GUARD",) if success else ("FEINT_IGNORED",))

    plans = {}
    from .stability import balance_rating
    from .stats import effective_stats
    cfg = state.config
    shove_cfg = cfg["tactics"]
    fraction = shove_cfg["shove_balance_fraction"]
    for actor_id, action in due.items():
        if action.kind != ActionKind.SHOVE:
            continue
        actor = actor_for(state, actor_id)
        defender = target_for(state, actor, action.target_id)
        if defender is None:
            plans[actor_id] = {"actor": actor, "defender": actor, "destination": actor.pos,
                                "attacker_score": 0, "defender_score": 0, "margin": 0,
                                "braced": False, "steps": [],
                                "outcome": "NO_TARGET"}
            continue
        gap = max(abs(actor.pos[0] - defender.pos[0]), abs(actor.pos[1] - defender.pos[1]))
        direction = (_sign(defender.pos[0] - actor.pos[0]),
                     _sign(defender.pos[1] - actor.pos[1]))
        destination = (defender.pos[0] + direction[0], defender.pos[1] + direction[1])
        attacker_score = (effective_stats(actor, cfg, state.tick).strength
                          + balance_rating(actor, cfg, state.tick) * fraction
                          + max(0, cfg["stability"]["start"] - defender.stability)
                          * shove_cfg["shove_unsteady_bonus"])
        defender_score = (effective_stats(defender, cfg, state.tick).strength
                          + balance_rating(defender, cfg, state.tick) * fraction)
        guard = defender.action
        braced = bool(guard and guard.kind == ActionKind.GUARD
                      and guard.phase_at(state.tick) in {"GUARD_RAISE", "GUARD_HOLD"})
        if braced:
            defender_score += cfg["tactics"]["shove_guard_brace"]
        margin = attacker_score - defender_score
        desired_steps = (2 if margin >= shove_cfg["shove_two_step_margin"] else
                         1 if margin >= shove_cfg["shove_move_margin"] else 0)
        plans[actor_id] = {"actor": actor, "defender": defender, "destination": destination,
                           "attacker_score": attacker_score, "defender_score": defender_score,
                           "margin": margin, "braced": braced, "steps": [],
                           "outcome": ("OUT_OF_RANGE" if gap > 1 else
                                        "RESISTED" if margin <= 0 else
                                        "OFF_BALANCE" if desired_steps == 0 else "SUCCESS")}

    from .forced_movement import resolve_forced_movement
    moving_rows = [row for row in plans.values() if row["outcome"] == "SUCCESS"]
    requests = []
    for row in moving_rows:
        actor, defender = row["actor"], row["defender"]
        dx, dy = _sign(defender.pos[0] - actor.pos[0]), _sign(defender.pos[1] - actor.pos[1])
        request = {"actor": actor, "target": defender, "direction": (dx, dy),
                   "steps": 2 if row["margin"] >= shove_cfg["shove_two_step_margin"] else 1,
                   "cause_tag": "SHOVE_PUSH"}
        row["motion_request"] = request
        requests.append(request)
    resolve_forced_movement(state, requests)
    for row in moving_rows:
        request = row["motion_request"]
        row["route"], row["steps"] = request["route"], request["actual"]
        if request.get("blocker"):
            row["blocker"] = request["blocker"]
            row["outcome"] = "BLOCKED" if not row["steps"] else "PARTIAL_BLOCKED"

    for row in plans.values():
        actor, defender = row["actor"], row["defender"]
        outcome = row["outcome"]
        deltas = {"outcome": outcome, "attacker_score": round(row["attacker_score"], 3),
                  "defender_score": round(row["defender_score"], 3), "braced": row["braced"],
                  "margin": round(row["margin"], 3), "requested_steps": len(row.get("route", [])),
                  "actual_steps": len(row.get("steps", [])),
                  "destination": row["destination"]}
        if "blocker" in row:
            deltas["blocker"] = row["blocker"]
        state.emit(EventType.SHOVE, actor.id, defender.id, deltas=deltas,
                   cause_tags=("SHOVE_" + outcome,))
        if row.get("steps"):
            from .stability import interrupt_unresolved_action
            interrupt_unresolved_action(state, defender)
        if outcome not in {"NO_TARGET", "OUT_OF_RANGE"}:
            from .stability import apply_stability_loss
            margin_fraction = min(1.0, max(0.0, row["margin"])
                                  / shove_cfg["shove_margin_for_max_loss"])
            loss = (shove_cfg["shove_min_stability_loss"]
                    + (shove_cfg["shove_max_stability_loss"]
                       - shove_cfg["shove_min_stability_loss"]) * margin_fraction)
            if row["braced"]:
                loss *= shove_cfg["shove_braced_impact_mult"]
            if row.get("blocker"):
                loss *= shove_cfg["shove_blocked_impact_mult"]
            apply_stability_loss(state, defender, "SHOVE_IMPACT", loss,
                                 details={"attacker": actor.id, "contest_margin": round(row["margin"], 3),
                                          "outcome": outcome, "requested_steps": len(row.get("route", [])),
                                          "actual_steps": len(row.get("steps", [])),
                                          "braced": row["braced"]})


def finish_completed_actions(state):
    resolve_tactical_actions(state)
    apply_pending_interrupts(state)
    for fighter in state.fighters:
        action = fighter.action
        if action is None or action.end_tick is None or state.tick < action.end_tick:
            continue
        if action.kind == ActionKind.BANDAGE:
            eligible = [wound for wound in fighter.wounds if not wound.bandaged and wound.bleed_per_turn > 0]
            if eligible and fighter.bandages > 0:
                treated = max(eligible, key=lambda wound: wound.bleed_per_turn)
                rate_before = min(state.config["blood"]["total_bleed_cap_per_turn"], sum(wound.bleed_per_turn * (
                    1-state.config["blood"]["bandage_reduction"] if wound.bandaged else 1)
                    for wound in fighter.wounds))
                treated.bandaged = True
                fighter.bandages -= 1
                rate_after = min(state.config["blood"]["total_bleed_cap_per_turn"], sum(wound.bleed_per_turn * (
                    1-state.config["blood"]["bandage_reduction"] if wound.bandaged else 1)
                    for wound in fighter.wounds))
                state.emit(EventType.STATUS, fighter.id,
                           deltas={"status": "bandaged", "zone": treated.zone.value,
                                   "severity": treated.severity.value,
                                   "wound_rate": treated.bleed_per_turn,
                                   "rate_before": rate_before, "rate_after": rate_after,
                                   "bandages_remaining": fighter.bandages},
                           cause_tags=("BANDAGE_COMPLETED",))
        elif action.kind in {ActionKind.EAT, ActionKind.DRINK}:
            field = "food" if action.kind == ActionKind.EAT else "water"
            if getattr(fighter, field) > 0:
                setattr(fighter, field, getattr(fighter, field) - 1)
                fighter.fatigue = max(0, fighter.fatigue - state.config["actions"][action.kind.value]["fatigue_reduction"])
        elif action.kind == ActionKind.PICKUP:
            item_index = next((index for index, row in enumerate(state.ground_weapons)
                               if row.weapon.item_id == action.item_id), None)
            if fighter.hand_severed:
                state.emit(EventType.STATUS, fighter.id,
                           deltas={"status": "pickup_failed", "reason": "HAND_SEVERED"},
                           cause_tags=("PICKUP_FAILED", "HAND_SEVERED"))
            elif item_index is None:
                state.emit(EventType.STATUS, fighter.id,
                           deltas={"status": "pickup_failed", "reason": "ITEM_UNAVAILABLE",
                                   "item_id": action.item_id},
                           cause_tags=("PICKUP_FAILED", "ITEM_UNAVAILABLE"))
            else:
                item = state.ground_weapons[item_index]
                if max(abs(fighter.pos[0]-item.pos[0]), abs(fighter.pos[1]-item.pos[1])) > state.config["weapon_integrity"]["pickup_range"]:
                    state.emit(EventType.STATUS, fighter.id,
                               deltas={"status": "pickup_failed", "reason": "OUT_OF_RANGE",
                                       "item_id": item.weapon.item_id},
                               cause_tags=("PICKUP_FAILED", "OUT_OF_RANGE"))
                elif item.weapon.condition <= 0:
                    state.emit(EventType.STATUS, fighter.id,
                               deltas={"status": "pickup_failed", "reason": "BROKEN",
                                       "item_id": item.weapon.item_id},
                               cause_tags=("PICKUP_FAILED", "BROKEN"))
                else:
                    old = fighter.equipment.weapon
                    if old is not None:
                        drop_weapon(state, fighter, "REPLACED")
                    # The append above may shift indices, so remove by stable identity.
                    item = next(row for row in state.ground_weapons
                                if row.weapon.item_id == action.item_id)
                    state.ground_weapons.remove(item)
                    fighter.equipment.weapon = item.weapon
                    fighter.disarmed = False
                    state.emit(EventType.WEAPON_PICKUP, fighter.id,
                               deltas={"item_id": item.weapon.item_id, "name": item.weapon.name,
                                       "material": item.weapon.material,
                                       "condition": item.weapon.condition, "replaced_item_id":
                                       old.item_id if old is not None else None},
                               cause_tags=("WEAPON_PICKED_UP",))
        elif action.kind == ActionKind.ADRENALINE:
            rule = state.config["adrenaline"]
            fighter.adrenaline_until_tick = state.tick + rule["duration_ticks"]
            fighter.adrenaline_debt_pending = rule["fatigue_debt"] > 0
            fighter.adrenaline_debt_due = rule["fatigue_debt"]
            state.emit(EventType.ADRENALINE, fighter.id,
                       deltas={"outcome": "ACTIVATED", "until_tick": fighter.adrenaline_until_tick,
                               "duration_ticks": rule["duration_ticks"],
                               "pain_suppression": rule["pain_suppression"],
                               "debt_due": fighter.adrenaline_debt_due},
                       cause_tags=("ADRENALINE_ACTIVATED",))
        elif action.kind == ActionKind.RISE and fighter.grounded:
            fighter.grounded = False
            fighter.grounded_since = None
            fighter.stability = state.config["stability"]["rise_stability"]
            state.emit(EventType.RISE, fighter.id,
                       deltas={"stability": fighter.stability, "band": "STUMBLING"},
                       cause_tags=("RISE_COMPLETED",))
        state.emit(EventType.ACTION_END, fighter.id, deltas={"kind": action.kind.value})
        fighter.action = None
        fighter.idle_since = state.tick


def resolve_adrenaline_expiry(state):
    for fighter in state.fighters:
        if (not fighter.adrenaline_debt_pending or fighter.adrenaline_until_tick is None
                or state.tick < fighter.adrenaline_until_tick):
            continue
        before = fighter.fatigue
        due = fighter.adrenaline_debt_due
        fighter.fatigue = clamp(fighter.fatigue + due, 0, state.config["defaults"]["pool_max"])
        applied = fighter.fatigue - before
        fighter.adrenaline_debt_pending = False
        fighter.adrenaline_debt_due = 0
        state.emit(EventType.ADRENALINE, fighter.id,
                   deltas={"outcome": "EXPIRED", "debt_due": due, "debt_applied": applied,
                           "fatigue_before": before, "fatigue_after": fighter.fatigue},
                   cause_tags=("ADRENALINE_EXPIRED",))


def check_end_conditions(state):
    if state.end_reason:
        return
    cfg = state.config["blood"]
    reasons = {}
    for fighter in state.fighters:
        reason = None
        if fighter.blood <= 0:
            reason = "death"
        elif fighter.unconscious or fighter.blood <= cfg["collapse"] or (
                fighter.blood < cfg["critical_collapse"] and fighter.fatigue > cfg["critical_fatigue"]):
            fighter.unconscious = True
            reason = "unconscious"
        elif fighter.yielded:
            reason = "yield"
        elif fighter.fled or fighter.pos in state.exits:
            fighter.fled = True
            reason = "flee"
        if reason:
            new_terminal_outcome = fighter.terminal_reason is None
            if new_terminal_outcome:
                fighter.terminal_reason = reason
            else:
                reason = fighter.terminal_reason
            if fighter.action is not None:
                kind = fighter.action.kind.value
                if fighter.action.kind == ActionKind.ADRENALINE:
                    state.emit(EventType.ADRENALINE, fighter.id,
                               deltas={"outcome": "INTERRUPTED", "activated": False,
                                       "debt_applied": 0},
                               cause_tags=("ADRENALINE_INTERRUPTED", "TERMINAL_OUTCOME"))
                state.emit(EventType.INTERRUPT, fighter.id,
                           deltas={"kind": kind, "reason": reason},
                           cause_tags=("TERMINAL_OUTCOME", reason.upper()))
                fighter.action = None
                fighter.idle_since = state.tick
            if new_terminal_outcome:
                if len(state.fighters) > 2:
                    state.emit(EventType.TERMINAL_OUTCOME, fighter.id,
                               deltas={"reason": reason,
                                       "leader": state.leaders.get(fighter.team_id) == fighter.id},
                               cause_tags=("TERMINAL_OUTCOME", reason.upper()))
            reasons[fighter.id] = reason
    if not reasons:
        return
    if len(state.fighters) == 2:
        losers = [(fighter, reasons[fighter.id]) for fighter in state.fighters if fighter.id in reasons]
        loser, reason = losers[0]
        state.end_reason = reason if len(losers) == 1 else "mutual_" + reason
        state.winner = state.other(loser).id if len(losers) == 1 else None
        from .mental import postfight_state
        state.emit(EventType.FIGHT_END, loser.id,
                   deltas={"reason": state.end_reason, "winner": state.winner,
                           "terminal_outcomes": {fighter.id: outcome for fighter, outcome in losers},
                           "carry": {fighter.id: asdict(postfight_state(state, fighter.id))
                                     for fighter in state.fighters}})
        return
    player = state.fighters[0]
    hostiles = state.hostiles_for(player.id, active_only=False)
    player_reason = reasons.get(player.id)
    active_hostiles = [fighter for fighter in hostiles if state.combat_active(fighter)]
    if player_reason:
        state.end_reason = player_reason
        state.winner = min(active_hostiles, key=lambda fighter: fighter.id).id if active_hostiles else None
        loser_ids = [player.id]
    elif active_hostiles:
        return
    else:
        hostile_reasons = [reasons.get(fighter.id) for fighter in hostiles]
        hostile_reasons = [reason for reason in hostile_reasons if reason]
        if len(hostiles) == 1 and len(hostile_reasons) == 1:
            state.end_reason = hostile_reasons[0]
        else:
            state.end_reason = "group_defeated"
        state.winner = player.id if state.combat_active(player) else None
        loser_ids = [fighter.id for fighter in hostiles if fighter.id in reasons]
    if not loser_ids:
        return
    from .mental import postfight_state
    state.emit(EventType.FIGHT_END, loser_ids[0],
               deltas={"reason": state.end_reason, "winner": state.winner,
                       "terminal_outcomes": {fighter.id: reason for fighter in state.fighters
                                             if (reason := reasons.get(fighter.id))},
                       "carry": {fighter.id: asdict(postfight_state(state, fighter.id))
                                 for fighter in state.fighters}})


def advance_one_tick(state):
    if state.end_reason:
        return
    event_cursor = len(state.event_log)
    from .wounds import defense_snapshot, resolve_pending_injury_responses, resolve_strike
    state.tick += 1
    resolve_adrenaline_expiry(state)
    apply_passive(state)
    update_attack_bearings(state)
    snap = {fighter.id: defense_snapshot(state, fighter) for fighter in state.fighters}
    strikers = [fighter for fighter in state.fighters
                if state.combat_active(fighter) and fighter.action
                and fighter.action.strike_tick == state.tick]
    strikers.sort(key=lambda f: (f.action.windup_len, -effective_stats(f, state.config, state.tick).speed, f.id))
    strike_targets = {fighter.id: state.primary_opponent(
        fighter.id, fighter.action.target_id, active_only=False) for fighter in strikers}
    active_at_impact = {target.id: state.combat_active(target)
                        for target in strike_targets.values() if target is not None}
    for fighter in strikers:
        if fighter.action and fighter.action.strike_tick == state.tick:
            target = strike_targets[fighter.id]
            if target is not None and active_at_impact.get(target.id, False):
                resolve_strike(state, fighter, target, snap[target.id])
            elif target is not None:
                state.emit(EventType.WHIFF, fighter.id, target.id,
                           deltas={"reason": "TARGET_UNAVAILABLE"},
                           cause_tags=("TARGET_UNAVAILABLE",))
    resolve_pending_injury_responses(state)
    apply_pending_interrupts(state)
    update_movement(state)
    apply_pending_interrupts(state)
    finish_completed_actions(state)
    check_end_conditions(state)
    from .perception import refresh_contacts
    refresh_contacts(state, state.event_log[event_cursor:])


def update_attack_bearings(state):
    """Track visible targets until the committed attack's deterministic lock tick."""
    for fighter in state.fighters:
        action = fighter.action
        if (not action or action.bearing_lock_tick is None
                or state.tick > action.bearing_lock_tick):
            continue
        target = target_for(state, fighter, action.target_id)
        if target is None or not state.can_see(fighter, target):
            continue
        bearing = bearing_from(fighter.pos, target.pos)
        if bearing is None:
            continue
        action.attack_bearing = bearing
        if state.tick == action.bearing_lock_tick:
            state.emit(EventType.ATTACK_LOCK, fighter.id, target.id,
                       deltas={"bearing": bearing, "kind": action.kind.value,
                               "tick": state.tick}, cause_tags=("ATTACK_BEARING_LOCKED",))


def run_until_decision(state):
    while not state.end_reason:
        free = [fighter for fighter in state.fighters if fighter.action is None and fighter.can_act(state.tick)]
        if free:
            return free
        if all(fighter.action and fighter.action.kind == ActionKind.GUARD
               and fighter.action.phase_at(state.tick) == "GUARD_HOLD" for fighter in state.fighters):
            # A held stance is released by an explicit command, never an infinite loop.
            return []
        advance_one_tick(state)
    return []
