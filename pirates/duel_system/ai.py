"""Deterministic Ronin decision table, delayed perception and public buckets.

Scoring and choosing are read-only. Decision recording/fear counters are separate
so candidate inspection, guide evaluation and rendering cannot teach a notebook
or advance fear. Batch commitment observes one pre-choice snapshot in engine.py.
"""
from dataclasses import dataclass
import hashlib
import math

from .engine import Choice, adrenaline_trigger_reasons, legality, movement_path
from .model import ActionKind, EffStats, EventType, Zone
from .stats import (clamp, effective_stats, legacy_fear_pressure, max_stamina,
                    phase_ticks, stamina_cost, weapon_reach)

TACTICAL_CATEGORIES = ("FEINT", "SHOVE", "DISENGAGE", "THRUST", "DISARM", "ADRENALINE",
                       "DASH_SLASH")


@dataclass(frozen=True)
class PublicKey:
    gap_band: str
    anger_band: str
    vigor_band: str
    opp_last_cat: str
    opp_state: str


@dataclass(frozen=True)
class FullKey:
    public: PublicKey
    blood_band: str
    fear_band: str
    pain_band: str


@dataclass(frozen=True)
class Observation:
    public_key: PublicKey
    full_key: FullKey
    gap: int
    anger: float
    vigor_pct: float
    fatigue: float
    fear: float
    blood: float
    pain: float
    exhausted: bool
    stats: EffStats
    opponent_strength: float
    opponent_blood: float
    opponent_bleeding: bool
    opponent_action: ActionKind | None
    opponent_remaining: int
    opponent_strike_delay: int | None
    opponent_phase: str
    opponent_hand_exposed: bool
    recent_attacks: tuple[str, ...]
    own_bleed_rate: float = 0
    opponent_unsteady: bool = False
    opponent_push_open: bool = False


@dataclass(frozen=True)
class ScoredAction:
    category: str
    choice: Choice
    score: float
    reasons: tuple[tuple[str, float], ...]


def distance(first, second):
    return max(abs(first[0] - second[0]), abs(first[1] - second[1]))


def observe(state, fighter_id):
    fighter = next(item for item in state.fighters if item.id == fighter_id)
    if len(state.fighters) <= 2:
        other = state.other(fighter)
        other_pos = other.pos
        visible = True
        group_combat = False
    else:
        contact = state.perceived_hostile_position(fighter_id)
        if contact is None:
            other, visible = None, False
            other_pos = (fighter.pos[0] + state.config["group"]["vision_range"] + 1, fighter.pos[1])
            group_combat = True
        else:
            identity, other_pos, visible = contact
            other = state.fighter(identity) if visible else None
            group_combat = True
    cfg, rule = state.config, state.config["ai"]
    gap = distance(fighter.pos, other_pos) - weapon_reach(fighter, cfg)
    limits = rule["gap_bands"]
    gap_band = "ADJ" if gap <= limits[0] else "NEAR" if gap <= limits[1] else "MID" if gap <= limits[2] else "FAR"
    anger_limits = rule["anger_bands"]
    anger_band = ("CALM" if fighter.anger < anger_limits[0] else "WARM" if fighter.anger < anger_limits[1]
                  else "ANGRY" if fighter.anger < anger_limits[2] else "RAGE")
    pct = fighter.stamina / max_stamina(fighter, cfg) * cfg["defaults"]["pool_max"]
    vigor = ("LOW" if pct < rule["vigor_low_stamina"] or fighter.fatigue > rule["vigor_low_fatigue"] or fighter.exhausted
             else "HIGH" if pct >= rule["vigor_high_stamina"] and fighter.fatigue < rule["vigor_high_fatigue"] else "MID")
    # Reaction timing is shared; profile identity must not change what evidence is
    # available to the otherwise universal combat policy.
    delay = rule["reaction_delay_default"]
    action = other.action if other is not None else None
    unseen = not visible or action is not None and state.tick - action.start_tick < delay
    last = (other.previous_action_cat if unseen else other.last_action_cat) if other is not None else "UNKNOWN"
    phase, kind, remaining, strike_delay = "IDLE", None, 0, None
    hand_exposed = False
    if unseen:
        phase, opp_state = "UNSEEN", "UNSEEN"
    else:
        phase = action.phase_at(state.tick) if action else "IDLE"
        kind = action.kind if action else None
        remaining = max(0, action.end_tick - state.tick) if action and action.end_tick is not None else 0
        strike_delay = max(0, action.strike_tick - state.tick) if action and action.strike_tick is not None and action.strike_tick >= state.tick else None
        if other is not None and state.tick < other.stagger_until:
            opp_state = phase = "STAGGERED"
            remaining = other.stagger_until - state.tick
        elif phase == "WINDUP":
            opp_state = "WINDUP_H" if kind == ActionKind.HEAVY else "WINDUP_L"
        elif phase in {"GUARD_RAISE", "GUARD_HOLD"}:
            opp_state = "GUARDING"
        elif phase == "RECOVERY":
            opp_state = "WHIFFED" if other.last_strike_whiffed else "RECOVERING"
        elif other is not None and other.exhausted:
            opp_state = "EXHAUSTED"
        else:
            opp_state = "IDLE"
        hand_exposed = phase in {"WINDUP", "ACTIVE", "RECOVERY", "STAGGERED", "GUARD_RAISE", "GUARD_HOLD"}
    public = PublicKey(gap_band, anger_band, vigor, last, opp_state)
    blood_band = "OK" if fighter.blood > rule["blood_bands"][1] else "HURT" if fighter.blood >= rule["blood_bands"][0] else "CRITICAL"
    own_fear = legacy_fear_pressure(fighter, cfg)
    fear_band = "BOLD" if own_fear < rule["fear_bands"][0] else "WARY" if own_fear <= rule["fear_bands"][1] else "SCARED"
    pain_band = "LOW" if fighter.pain < rule["pain_bands"][0] else "MID" if fighter.pain <= rule["pain_bands"][1] else "HIGH"
    full = FullKey(public, blood_band, fear_band, pain_band)
    recent = tuple(item for item in fighter.action_history
                   if item in {"LIGHT", "HEAVY", "DASH_LIGHT", "THRUST", "DISARM"})
    bleed_rate = min(cfg["blood"]["total_bleed_cap_per_turn"], sum(
        wound.bleed_per_turn * (1-cfg["blood"]["bandage_reduction"] if wound.bandaged else 1)
        for wound in fighter.wounds))
    if group_combat:
        # A group fighter may only reason from witnessed cues, not the visible
        # target's hidden simulation pools, wound list or authored attributes.
        witnessed_wounds = [event for event in state.event_log
                            if event.type == EventType.WOUND and event.target == (other.id if other else None)
                            and fighter.id in event.witnesses
                            and event.deltas.get("bleed_per_turn", 0) > 0]
        opponent_bleeding = bool(witnessed_wounds)
        opponent_blood = cfg["blood"]["start"]  # no exact public blood pool in group combat
        opponent_strength = cfg["defaults"]["strength"]
        witnessed_state = next((event for event in reversed(state.event_log)
                                if event.actor == (other.id if other else None)
                                and fighter.id in event.witnesses
                                and state.tick - event.tick <= cfg["time"]["ticks_per_turn"]
                                and event.type in {EventType.STABILITY, EventType.FALL, EventType.RISE}), None)
        opponent_unsteady = bool((other is not None and visible and other.grounded)
                                 or (witnessed_state and (
                                     witnessed_state.type == EventType.FALL
                                     or witnessed_state.type == EventType.STABILITY
                                     and witnessed_state.deltas.get("band") in {"OFF_BALANCE", "STUMBLING"})))
        opponent_pos = other.pos if other is not None else None
        push_open = False
        if other is not None and visible:
            dx = (other.pos[0] > fighter.pos[0]) - (other.pos[0] < fighter.pos[0])
            dy = (other.pos[1] > fighter.pos[1]) - (other.pos[1] < fighter.pos[1])
            destination = (other.pos[0]+dx, other.pos[1]+dy)
            occupied = {item.pos for item in state.fighters if item.id != other.id and state.combat_active(item)}
            push_open = (destination not in occupied and destination in (state.walkable or set()))
    else:
        opponent_bleeding = any(w.bleed_per_turn > 0 for w in other.wounds)
        opponent_blood = other.blood
        opponent_strength = effective_stats(other, cfg, state.tick).strength
        from .stability import stability_band
        opponent_unsteady = stability_band(other, cfg) in {"OFF_BALANCE", "STUMBLING", "GROUNDED"}
        dx = (other.pos[0] > fighter.pos[0]) - (other.pos[0] < fighter.pos[0])
        dy = (other.pos[1] > fighter.pos[1]) - (other.pos[1] < fighter.pos[1])
        destination = (other.pos[0]+dx, other.pos[1]+dy)
        occupied = {item.pos for item in state.fighters if item.id != other.id and state.combat_active(item)}
        push_open = destination not in occupied and destination in (state.walkable or set())
    return Observation(public, full, gap, fighter.anger, pct, fighter.fatigue, own_fear,
                        fighter.blood, fighter.pain, fighter.exhausted,
                        effective_stats(fighter, cfg, state.tick),
                        opponent_strength,
                        opponent_blood, opponent_bleeding,
                        kind, remaining, strike_delay, phase, hand_exposed, recent,
                        bleed_rate, opponent_unsteady, push_open)


def movement_choice(state, fighter, kind, *, toward, aim=None):
    contact = state.perceived_hostile_position(fighter.id)
    if contact is None:
        return None
    other_pos = contact[1]
    options = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == dy == 0:
                continue
            for tiles in range(1, state.config["actions"][kind.value]["max_tiles"] + 1):
                choice = Choice(kind, aim, (dx, dy), tiles)
                if legality(state, fighter.id, choice):
                    continue
                end = movement_path(state, fighter, choice)[-1]
                dist = distance(end, other_pos)
                before = distance(fighter.pos, other_pos)
                if (toward and dist < before) or (not toward and dist > before):
                    # Prefer a direct lane rather than sideways zigzags; tuple breaks ties.
                    deviation = abs(dx * (other_pos[1]-fighter.pos[1]) - dy * (other_pos[0]-fighter.pos[0]))
                    # Two fighters closing from opposite ends of a one-tile gap
                    # otherwise select the same middle tile and repeatedly block
                    # each other. Prefer distinct, deterministic flanks only when
                    # the direct endpoint is that shared center tile.
                    target_id = contact[0]
                    locations = {item.id: item.pos for item in state.fighters}
                    priority_key = lambda identity: hashlib.sha256(
                        f"{state.rng_seed}:{state.tick}:approach:{locations[identity]}".encode("utf-8")).digest()
                    approach_priority = min((fighter.id, target_id), key=priority_key)
                    # If both sides choose this same inward movement from the
                    # frozen snapshot, identical direct paths collide. Later
                    # actors take the best-progressing non-conflicting route;
                    # this is geometric and uses no hidden target statistics.
                    target_direct_path = [
                        (other_pos[0]-dx*step, other_pos[1]-dy*step)
                        for step in range(1, tiles+1)]
                    own_path = movement_path(state, fighter, choice)
                    shared_route = (toward and fighter.id != approach_priority
                                    and any(own_path[index] == target_direct_path[index]
                                            for index in range(min(len(own_path),
                                                                    len(target_direct_path)))))
                    yields_center = toward and fighter.id != approach_priority and before == 2
                    shared_center = (yields_center and dist == 1
                                     and abs(other_pos[0] - fighter.pos[0])
                                     + abs(other_pos[1] - fighter.pos[1]) == 2
                                     and end == ((fighter.pos[0] + other_pos[0]) // 2,
                                                 (fighter.pos[1] + other_pos[1]) // 2))
                    cross = dx * (other_pos[1] - fighter.pos[1]) - dy * (other_pos[0] - fighter.pos[0])
                    flank_penalty = 0 if not yields_center or cross > 0 else 1
                    options.append(((int(shared_route), dist if toward else -dist, int(shared_center),
                                     flank_penalty if before == 2 and toward else 0,
                                     deviation, tiles, dx, dy), choice))
    return min(options, key=lambda item: item[0])[1] if options else None


def attack_zone(state, fighter, view, kind):
    """Choose one target zone from shared, observable tactical cues."""
    if view.opponent_hand_exposed and view.public_key.opp_state in {"RECOVERING", "WHIFFED"}:
        return Zone.HAND
    if view.public_key.opp_state == "STAGGERED":
        return Zone.HEAD
    if view.public_key.opp_state == "GUARDING":
        return Zone.LEG
    return Zone.TORSO


def candidates(state, fighter, view):
    result = []
    if fighter.grounded:
        options = [("RISE", Choice(ActionKind.RISE)),
                   ("GUARD", Choice(ActionKind.GUARD))]
        if view.gap <= 1:
            options.append(("LIGHT", Choice(ActionKind.LIGHT,
                                             attack_zone(state, fighter, view, ActionKind.LIGHT))))
        crawl = movement_choice(state, fighter, ActionKind.CRAWL, toward=False)
        if crawl is not None:
            options.append(("CRAWL", crawl))
        return [(category, choice) for category, choice in options
                if not legality(state, fighter.id, choice)]
    categories = list(state.config["ai"]["tie_order"])
    categories.extend(kind for kind in TACTICAL_CATEGORIES if kind not in categories)
    for category in categories:
        choice = None
        if category in {"LIGHT", "HEAVY"} and view.gap <= 0:
            kind = ActionKind(category)
            choice = Choice(kind, attack_zone(state, fighter, view, kind))
        elif category == "DASH_LIGHT" and 1 <= view.gap <= state.config["ai"]["close_gap"]:
            choice = movement_choice(state, fighter, ActionKind.DASH_LIGHT, toward=True,
                                     aim=attack_zone(state, fighter, view, ActionKind.DASH_LIGHT))
        elif category in {"DASH_IN", "DASH_OUT", "WALK_IN", "WALK_OUT", "RUN_IN", "RUN_OUT"}:
            toward = category.endswith("_IN")
            if not toward or view.gap > 0:
                choice = movement_choice(state, fighter, ActionKind(category.split("_")[0]), toward=toward)
        elif category == "DASH_SLASH" and 1 <= view.gap <= state.config["ai"]["close_gap"]:
            choice = movement_choice(state, fighter, ActionKind.DASH_SLASH, toward=True,
                                     aim=attack_zone(state, fighter, view, ActionKind.DASH_SLASH))
        elif category == "FEINT" and view.public_key.opp_state == "GUARDING":
            choice = Choice(ActionKind.FEINT)
        elif category == "SHOVE" and view.gap + weapon_reach(fighter, state.config) <= 1:
            choice = Choice(ActionKind.SHOVE)
        elif category == "DISENGAGE":
            choice = movement_choice(state, fighter, ActionKind.DISENGAGE, toward=False)
        elif category == "THRUST" and view.gap <= 0:
            choice = Choice(ActionKind.THRUST, attack_zone(state, fighter, view, ActionKind.THRUST))
        elif (category == "DISARM" and fighter.riposte and view.gap <= 0
              and view.opponent_hand_exposed):
            choice = Choice(ActionKind.DISARM, Zone.HAND)
        elif category == "ADRENALINE" and adrenaline_trigger_reasons(fighter, state.config):
            choice = Choice(ActionKind.ADRENALINE)
        elif category in {"GUARD", "WAIT", "BANDAGE"}:
            choice = Choice(ActionKind(category))
        if choice is not None and not legality(state, fighter.id, choice):
            result.append((category, choice))
    return result


def strike_delay(state, view, choice):
    table = state.config["actions"][choice.kind.value]
    delay = phase_ticks(table.get("windup", 0), view.stats.speed, state.config)
    if choice.kind in {ActionKind.DASH_LIGHT, ActionKind.DASH_SLASH}:
        delay += choice.tiles * phase_ticks(table["per_tile"], view.stats.speed, state.config)
    return delay


def decision_control(state, fighter_id):
    """Derived decision control; physical effects remain governed by combat state."""
    fighter = next(item for item in state.fighters if item.id == fighter_id)
    profile = fighter.personality or state.config["ronin"]
    pool = state.config["defaults"]["pool_max"]
    composure = profile.composure if hasattr(profile, "composure") else profile["composure"]
    stress_composure = (profile.stress_composure if hasattr(profile, "stress_composure")
                        else profile.get("stress_composure", 0.5))
    weights = state.config["personality"]["pressure_weights"]
    pressure = (weights["fear"] * legacy_fear_pressure(fighter, state.config) / pool
                + weights["pain"] * fighter.pain / pool
                + weights["nervousness"] * fighter.nervousness)
    return clamp(composure * (1 - pressure * (1 - stress_composure)), 0, 1)


def _decision_index(state, fighter_id):
    return sum(event.type.value == "DECISION" and event.actor == fighter_id for event in state.event_log)


def _seeded_variation(state, fighter_id, category, choice, amplitude):
    fighter = next(item for item in state.fighters if item.id == fighter_id)
    identity = (choice.kind.value, choice.aim.value if choice.aim else None,
                choice.direction, choice.tiles, choice.target_id)
    # Key by public geometry rather than NPC/profile identity so equivalent
    # actors do not get systematically different tie preferences by name.
    key = f"{state.rng_seed}:{fighter.pos}:{_decision_index(state, fighter_id)}:{category}:{identity}".encode("utf-8")
    value = int.from_bytes(hashlib.sha256(key).digest()[:8], "big") / (2**64 - 1)
    return (2 * value - 1) * amplitude


def score_actions(state, fighter_id):
    fighter = next(item for item in state.fighters if item.id == fighter_id)
    view = observe(state, fighter_id)
    cfg = state.config
    rules = cfg["universal_ai"]
    weights = rules["weights"]
    modes = fighter.behavior_modes
    attacks = {"LIGHT", "HEAVY", "DASH_LIGHT", "THRUST", "DISARM", "DASH_SLASH"}
    outward = {"DASH_OUT", "WALK_OUT", "RUN_OUT", "DISENGAGE"}
    inward = {"DASH_IN", "WALK_IN", "RUN_IN"}
    scored = []
    for category, choice in candidates(state, fighter, view):
        terms = [("UNIVERSAL_BASE", rules["base"].get(category, 0))]

        def add(name, value):
            if value:
                terms.append((name, float(value)))

        table = cfg["actions"][choice.kind.value]
        delay = (strike_delay(state, view, choice) if category in attacks
                 else (choice.tiles * phase_ticks(table["per_tile"], view.stats.speed, cfg)
                       if choice.kind in {ActionKind.DASH, ActionKind.WALK, ActionKind.RUN,
                                          ActionKind.CRAWL, ActionKind.DISENGAGE}
                       else phase_ticks(table.get("windup", 0), view.stats.speed, cfg)))
        recovery = phase_ticks(table.get("recovery", 0), view.stats.speed, cfg,
                               recovery_weight=fighter.equipment.total_weight)
        duration = delay + recovery
        base_cost = table["stamina"] * (choice.tiles if choice.kind == ActionKind.RUN else 1)
        cost = stamina_cost(fighter, base_cost, cfg)
        add("UNIVERSAL_STAMINA_COST", -cost * weights["stamina_cost"])
        add("UNIVERSAL_COMMITMENT_TIME", -duration * weights["action_duration"])

        if category in attacks:
            zone = choice.aim or Zone.TORSO
            dispersion = cfg["accuracy"]["aim_dispersion"]
            difficulty = cfg["zones"]["difficulty"][zone.value]
            torso_difficulty = cfg["zones"]["difficulty"][Zone.TORSO.value]
            clean_chance = clamp(
                dispersion["clean_base"] + view.stats.accuracy * dispersion["per_accuracy"]
                - max(0, difficulty-torso_difficulty) * dispersion["difficulty_penalty"],
                dispersion["clean_min"], dispersion["clean_max"])
            power = table.get("power", 1)
            attack_value = clean_chance * (1 + power / 2) * (
                0.75 + clamp(view.stats.strength / max(1, view.opponent_strength), 0.5, 1.5) * 0.25)
            add("UNIVERSAL_CONTACT_VALUE", attack_value * 8)
            if view.public_key.opp_state in {"RECOVERING", "WHIFFED", "STAGGERED"}:
                if view.opponent_remaining >= delay:
                    margin = min(1, (view.opponent_remaining-delay+1) / max(1, view.opponent_remaining))
                    add("UNIVERSAL_VISIBLE_OPENING", weights["opening"] * margin)
                else:
                    add("UNIVERSAL_OPENING_TOO_SHORT", -weights["unsafe_slow_attack"])
            if (view.opponent_phase == "WINDUP" and view.opponent_strike_delay is not None):
                if delay < view.opponent_strike_delay:
                    margin = (view.opponent_strike_delay-delay) / max(1, view.opponent_strike_delay)
                    add("UNIVERSAL_COUNTER_WINDOW", weights["counter"] * min(1, margin))
                else:
                    add("UNIVERSAL_SLOW_TRADE_RISK", -weights["unsafe_slow_attack"])
        elif category == "GUARD" and view.opponent_strike_delay is not None:
            guard_ready = phase_ticks(table.get("windup", 0), view.stats.speed, cfg)
            if (view.opponent_phase in {"WINDUP", "ACTIVE"}
                    and view.opponent_strike_delay >= guard_ready):
                add("UNIVERSAL_IMMINENT_GUARD", weights["imminent_guard"])
                if view.opponent_action == ActionKind.HEAVY and (
                        view.stats.strength >= cfg["guard"]["parry_str_ratio"]["heavy"]
                        * view.opponent_strength):
                    add("UNIVERSAL_HEAVY_PARRY", weights["counter"] * 0.6)
        elif category == "SHOVE":
            if view.opponent_unsteady:
                add("UNIVERSAL_SHOVE_UNSTEADY", weights["shove_unsteady"])
            if view.opponent_push_open:
                add("UNIVERSAL_SHOVE_OPEN_SPACE", weights["shove_open_space"])
        elif category == "ADRENALINE":
            triggers = adrenaline_trigger_reasons(fighter, cfg)
            add("UNIVERSAL_ADRENALINE_TRIGGER", len(triggers) * weights["adrenaline_trigger"])

        if category in inward | outward | {"DASH_LIGHT", "DASH_SLASH", "CRAWL"}:
            destination = movement_path(state, fighter, choice)[-1]
            contact = state.perceived_hostile_position(fighter.id)
            if contact:
                before = distance(fighter.pos, contact[1])
                after = distance(destination, contact[1])
                if category in inward or category == "DASH_LIGHT":
                    add("UNIVERSAL_POSITION_GAIN", max(0, before-after) * weights["position_per_gap"])
                elif category in outward:
                    if view.opponent_strike_delay is None:
                        sign = -0.25
                    else:
                        first_step = phase_ticks(cfg["actions"][choice.kind.value]["per_tile"],
                                                 view.stats.speed, cfg)
                        # Retreat only receives evasion utility if a tile is
                        # reached before the observed strike; a long route is
                        # not a dodge when impact is already imminent.
                        sign = 1 if first_step < view.opponent_strike_delay else 0
                    if sign:
                        add("UNIVERSAL_POSITION_SAFETY", sign * max(0, after-before)
                            * weights["position_per_gap"])
        if category == "BANDAGE" and view.own_bleed_rate > 0:
            urgency = min(1, view.own_bleed_rate / cfg["blood"]["total_bleed_cap_per_turn"])
            urgency *= 1 + (1-view.blood/cfg["blood"]["start"])
            treatment_ticks = phase_ticks(table["windup"], view.stats.speed, cfg)
            safe_window = (view.public_key.opp_state in {"STAGGERED", "RECOVERING", "WHIFFED"}
                           and view.opponent_remaining >= treatment_ticks)
            immediate_threat = (view.opponent_phase in {"WINDUP", "ACTIVE"}
                                or view.public_key.opp_state == "IDLE"
                                and view.gap <= cfg["ai"]["rest_gap"])
            treatment_safety = 1 if safe_window else 0.15 if immediate_threat else 0.5
            add("UNIVERSAL_BLEED_TREATMENT",
                weights["safe_bandage"] * urgency * treatment_safety)
            if not safe_window and immediate_threat:
                add("UNIVERSAL_EXPOSED_TREATMENT", -weights["unsafe_bandage"] * (1+urgency))
        if category == "WAIT" and view.vigor_pct < cfg["ai"]["vigor_low_stamina"]:
            if view.gap >= cfg["ai"]["rest_gap"]:
                deficit = (cfg["ai"]["vigor_low_stamina"]-view.vigor_pct) / max(
                    1, cfg["ai"]["vigor_low_stamina"])
                add("UNIVERSAL_LOW_VIGOR_RECOVERY", weights["wait_low_vigor"] * deficit)
        if category == "WAIT" and fighter.stability < cfg["stability"]["stable_threshold"]:
            recovery_need = (cfg["stability"]["stable_threshold"]-fighter.stability) / max(
                1, cfg["stability"]["stable_threshold"])
            add("UNIVERSAL_STABILITY_RECOVERY", weights["injury_risk"] * recovery_need)
        if view.opponent_bleeding and category in {"WAIT", "WALK_OUT", "DASH_OUT"}:
            # Group opponents supply only the witnessed bleeding cue, never exact blood/rate.
            blood_ratio = (view.opponent_blood / cfg["blood"]["start"]
                           if len(state.fighters) <= 2 else 0.5)
            add("UNIVERSAL_BLEED_PRESSURE", weights["bleed_pressure"] * (1-blood_ratio))

        mode_bonus = rules["mode_bonus"]
        if modes["pain"] == "PROTECTIVE":
            if category == "GUARD":
                add("MODE_PROTECTIVE_GUARD", mode_bonus["protective_guard"])
            if category in outward:
                add("MODE_PROTECTIVE_SPACE", mode_bonus["protective_exit"])
        if modes["fear"] == "CAUTIOUS":
            if category == "GUARD":
                add("MODE_CAUTIOUS_GUARD", mode_bonus["cautious_guard"])
            if category in outward:
                add("MODE_CAUTIOUS_SPACE", mode_bonus["cautious_exit"])
        if modes["anger"] in {"PRESS", "HOT"}:
            bonus = mode_bonus["hot_attack"] if modes["anger"] == "HOT" else mode_bonus["press_attack"]
            if category in attacks | {"SHOVE", *inward}:
                add("MODE_ANGER_PRESSURE", bonus)
        if modes["pain"] == "BREAK" or modes["fear"] == "BREAK":
            if category == "GUARD":
                add("MODE_CRITICAL_GUARD", mode_bonus["break_guard"])
            if category in outward and "UNIVERSAL_POSITION_SAFETY" in dict(terms):
                add("MODE_CRITICAL_SPACE", mode_bonus["break_exit"])
            if (category in attacks and view.opponent_strike_delay is not None
                    and delay >= view.opponent_strike_delay):
                add("MODE_CRITICAL_AVOID_LOSING_TRADE", -mode_bonus["break_unsafe_trade"])
        scored.append(ScoredAction(category, choice, sum(value for _, value in terms), tuple(terms)))
    tie = rules["tie_break"]
    if not scored or tie["amplitude"] <= 0:
        return tuple(scored)
    best = max(item.score for item in scored)
    varied = []
    for item in scored:
        if best-item.score <= tie["margin"]:
            variation = _seeded_variation(state, fighter_id, item.category,
                                          item.choice, tie["amplitude"])
            varied.append(ScoredAction(item.category, item.choice, item.score+variation,
                                       (*item.reasons, ("UNIVERSAL_SEEDED_TIE", variation))))
        else:
            varied.append(item)
    return tuple(varied)


def choose_action(state, fighter_id):
    scored = score_actions(state, fighter_id)
    if not scored:
        raise ValueError("No free legal NPC decision")
    # Candidate enumeration follows the fixed configured tie order, never dict order.
    return max(scored, key=lambda item: item.score)
