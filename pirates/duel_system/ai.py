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
from .stats import clamp, effective_stats, fear, max_stamina, phase_ticks, weapon_reach


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
    delay = fighter.personality.reaction_delay if fighter.personality else rule["reaction_delay_default"]
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
    own_fear = fear(fighter, cfg)
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
                profile = fighter.personality
                discipline = (profile.traits["discipline"] if profile else 0)
                if (toward and discipline >= state.config["group"]["coordination_discipline_threshold"]
                        and any(other.id != fighter.id and other.pos == end and state.occupies_tile(other)
                                for other in state.fighters)):
                    continue
                dist = distance(end, other_pos)
                before = distance(fighter.pos, other_pos)
                if (toward and dist < before) or (not toward and dist > before):
                    # Prefer a direct lane rather than sideways zigzags; tuple breaks ties.
                    deviation = abs(dx * (other_pos[1]-fighter.pos[1]) - dy * (other_pos[0]-fighter.pos[0]))
                    options.append(((dist if toward else -dist, deviation, tiles, dx, dy), choice))
    return min(options, key=lambda item: item[0])[1] if options else None


def attack_zone(state, fighter, view, kind):
    cfg = state.config
    preferences = fighter.personality.zone_pref if fighter.personality else ["TORSO"]
    for preference in preferences:
        if "_IF_" in preference:
            name, condition = preference.split("_IF_", 1)
            if condition == "RECOVERING" and view.public_key.opp_state not in {"RECOVERING", "WHIFFED"}:
                continue
            if condition == "STAGGERED" and view.public_key.opp_state != "STAGGERED":
                continue
            if condition == "GUARDING" and view.public_key.opp_state != "GUARDING":
                continue
        else:
            name = preference
        zone = Zone(name)
        if zone == Zone.HAND and not view.opponent_hand_exposed:
            continue
        difficulty = cfg["zones"]["difficulty"][name]
        if zone == Zone.HAND and view.opponent_phase == "GUARD_HOLD":
            difficulty += cfg["zones"]["guard_hand_extra"]
        if zone == Zone.TORSO or view.stats.accuracy >= difficulty:
            return zone
    return Zone.TORSO


def candidates(state, fighter, view):
    result = []
    if fighter.grounded:
        options = [("RISE", Choice(ActionKind.RISE)),
                   ("GUARD", Choice(ActionKind.GUARD))]
        crawl = movement_choice(state, fighter, ActionKind.CRAWL, toward=False)
        if crawl is not None:
            options.append(("CRAWL", crawl))
        return [(category, choice) for category, choice in options
                if not legality(state, fighter.id, choice)]
    categories = list(state.config["ai"]["tie_order"])
    categories.extend(kind for kind in ("FEINT", "SHOVE", "DISENGAGE", "THRUST", "DISARM", "ADRENALINE")
                      if kind not in categories)
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
    if choice.kind == ActionKind.DASH_LIGHT:
        delay += choice.tiles * phase_ticks(table["per_tile"], view.stats.speed, state.config)
    return delay


def decision_control(state, fighter_id):
    """Derived decision control; physical effects remain governed by combat state."""
    fighter = next(item for item in state.fighters if item.id == fighter_id)
    profile = fighter.personality or state.config["ronin"]
    traits = profile.traits if hasattr(profile, "traits") else profile["traits"]
    weights = state.config["personality"]["pressure_weights"]
    pool = state.config["defaults"]["pool_max"]
    pressure = (weights["fear"] * fear(fighter, state.config) / pool
                + weights["pain"] * fighter.pain / pool
                + weights["nervousness"] * fighter.nervousness)
    composure = profile.composure if hasattr(profile, "composure") else profile["composure"]
    return clamp(composure * (1 - pressure * (1 - traits["discipline"])), 0, 1)


def adaptation_penalty(fighter, category, config):
    """Bounded soft penalty from recent resolved counters; never removes legality."""
    rule = config["personality"]["adaptation"]
    adaptability = fighter.personality.traits["adaptability"] if fighter.personality else 0
    outcomes = [row["outcome"] for row in fighter.ai_experience if row["category"] == category]
    counters = outcomes.count("COUNTERED")
    if counters < rule["minimum_counters"] or adaptability <= 0:
        return 0
    successes = outcomes.count("SUCCESS")
    raw = max(0, (counters - rule["minimum_counters"] + 1) * rule["counter_penalty"]
              - successes * rule["success_decay"])
    return min(rule["max_penalty"], raw) * adaptability


def record_ai_decision(fighter, category):
    """Record a committed decision after all policies have read the same snapshot."""
    fighter.ai_current_category = category
    if category in {"LIGHT", "HEAVY", "DASH_LIGHT", "THRUST", "DISARM", "SHOVE",
                    "DASH_IN", "WALK_IN", "RUN_IN"}:
        fighter.ai_stagnant_decisions = 0
    else:
        fighter.ai_stagnant_decisions += 1


def record_ai_outcomes(state, events):
    """Learn only from public resolution events, never from a queued choice."""
    history = state.config["personality"]["adaptation"]["history"]
    for event in events:
        for fighter in state.fighters:
            category = fighter.ai_current_category
            if not category or not fighter.personality:
                continue
            outcome = None
            if event.type == EventType.HIT and event.actor == fighter.id:
                outcome = "SUCCESS"
            elif event.type == EventType.WHIFF and event.actor == fighter.id:
                outcome = "COUNTERED"
            elif event.type in {EventType.PARRY, EventType.BLOCK}:
                if event.target == fighter.id:
                    outcome = "COUNTERED"
                elif event.actor == fighter.id:
                    outcome = "SUCCESS"
            elif event.type == EventType.INTERRUPT and event.actor == fighter.id:
                outcome = "COUNTERED"
            elif event.type == EventType.FEINT and event.actor == fighter.id:
                outcome = "SUCCESS" if event.deltas.get("outcome") == "GUARD_DISRUPTED" else "COUNTERED"
            elif event.type == EventType.SHOVE and event.actor == fighter.id:
                outcome = "SUCCESS" if event.deltas.get("outcome") == "SUCCESS" else "COUNTERED"
            elif event.type == EventType.DISARM and event.actor == fighter.id:
                outcome = "SUCCESS" if event.deltas.get("outcome") == "DISARMED" else "COUNTERED"
            elif event.type == EventType.ADRENALINE and event.actor == fighter.id:
                if event.deltas.get("outcome") == "ACTIVATED":
                    outcome = "SUCCESS"
                elif event.deltas.get("outcome") == "INTERRUPTED":
                    outcome = "COUNTERED"
            if outcome:
                fighter.ai_experience.append({"category": category, "outcome": outcome})
                fighter.ai_experience = fighter.ai_experience[-history:]


def _decision_index(state, fighter_id):
    return sum(event.type.value == "DECISION" and event.actor == fighter_id for event in state.event_log)


def _seeded_variation(state, fighter_id, category, amplitude):
    key = f"{state.rng_seed}:{fighter_id}:{_decision_index(state, fighter_id)}:{category}".encode("utf-8")
    value = int.from_bytes(hashlib.sha256(key).digest()[:8], "big") / (2**64 - 1)
    return (2 * value - 1) * amplitude


def score_actions(state, fighter_id):
    fighter = next(item for item in state.fighters if item.id == fighter_id)
    view = observe(state, fighter_id)
    personality = fighter.personality
    bases = personality.base if personality else state.config["ronin"]["base"]
    weights = personality.weights if personality else state.config["ronin"]["weights"]
    profile_bias = personality.score_bias if personality else state.config["ronin"].get("score_bias", {})
    traits = personality.traits if personality else state.config["ronin"]["traits"]
    rule = state.config["ai"]
    p2 = state.config["personality"]
    control = decision_control(state, fighter_id)
    pressure_weights = p2["pressure_weights"]
    pressure = (pressure_weights["fear"] * view.fear / state.config["defaults"]["pool_max"]
                + pressure_weights["pain"] * view.pain / state.config["defaults"]["pool_max"]
                + pressure_weights["nervousness"] * fighter.nervousness)
    attacks = {"LIGHT", "HEAVY", "DASH_LIGHT", "THRUST", "DISARM"}
    scored = []
    for category, choice in candidates(state, fighter, view):
        terms = [("base", bases.get(category, 0))]
        def add(name, value, weight=None):
            terms.append((name, value * (weights.get(weight, 1) if weight else 1)))
        if profile_bias.get(category, 0):
            add("P2_PROFILE_BIAS", profile_bias[category])
        tactical_score = state.config["tactics"]["ai_scores"]
        if category == "ADRENALINE":
            add("P5_ADRENALINE_TRIGGER", tactical_score[category])
        elif category == "FEINT" or category == "DISARM":
            add("P5_TACTIC_SETUP", tactical_score[category])
        elif category == "SHOVE" and view.public_key.opp_state in {"RECOVERING", "WHIFFED", "STAGGERED"}:
            add("P5_SHOVE_OPENING", tactical_score[category])
        elif category == "THRUST" and view.public_key.opp_state in {"RECOVERING", "WHIFFED"}:
            add("P5_THRUST_OPENING", tactical_score[category])
        elif (category == "DISENGAGE" and view.opponent_strike_delay is not None
              and view.gap <= 1
              and view.opponent_strike_delay > phase_ticks(
                  state.config["actions"]["DISENGAGE"]["per_tile"], view.stats.speed, state.config)):
            add("P5_DISENGAGE_WINDOW", tactical_score[category])
        if category == "SHOVE" and view.opponent_unsteady:
            add("I5_SHOVE_UNSTEADY", tactical_score["SHOVE_UNSTEADY"])
        if category == "SHOVE" and view.opponent_push_open:
            add("I5_SHOVE_SPACE", tactical_score["SHOVE_OPEN_SPACE"])
        if category == "BANDAGE" and view.own_bleed_rate > 0:
            urgency = min(1, view.own_bleed_rate / state.config["blood"]["total_bleed_cap_per_turn"])
            urgency *= 1 + (1-view.blood/state.config["blood"]["start"])
            add("I5_BANDAGE_BLEED", tactical_score["BANDAGE_BLEEDING"] * urgency)
            treatment_ticks = phase_ticks(state.config["actions"]["BANDAGE"]["windup"],
                                          view.stats.speed, state.config)
            safe_action_window = (view.public_key.opp_state in {"STAGGERED", "RECOVERING", "WHIFFED"}
                                  and view.opponent_remaining >= treatment_ticks)
            if not safe_action_window:
                add("I5_BANDAGE_EXPOSED",
                    -tactical_score["BANDAGE_UNSAFE_PENALTY"] * (1+urgency))
        if (category == "WAIT" and not fighter.grounded
                and fighter.stability < state.config["stability"]["stable_threshold"]
                and view.gap > 0):
            recovery_need = (state.config["stability"]["stable_threshold"]-fighter.stability) / max(
                1, state.config["stability"]["stable_threshold"])
            add("I5_WAIT_STABILITY", tactical_score["WAIT_STABILITY"] * recovery_need)
        if view.opponent_bleeding and category in {"WAIT", "WALK_OUT", "DASH_OUT"}:
            # In group combat this is only a witnessed wound cue, not an exact
            # pool/rate estimate. Solo legacy scoring retains its blood band.
            if len(state.fighters) > 2:
                patience = traits["patience"]
                add("I5_WITNESSED_BLEED_PATIENCE",
                    tactical_score["BLEED_PATIENCE"] * patience)
        aggressive_categories = attacks | {"SHOVE", "THRUST", "DISARM", "DASH_IN", "WALK_IN", "RUN_IN"}
        aggression_factor = 1 if category in aggressive_categories else -0.35 if category in {
            "GUARD", "DASH_OUT", "WALK_OUT", "RUN_OUT", "WAIT",
        } else 0
        if aggression_factor:
            add("P2_TRAIT_AGGRESSION", (traits["aggression"] - 0.5)
                * p2["trait_score"] * aggression_factor)
        confidence_factor = 1 if category == "HEAVY" else 0.45 if category in {"LIGHT", "DASH_LIGHT"} else (
            -0.5 if category in {"GUARD", "DASH_OUT", "WALK_OUT", "RUN_OUT"} else 0)
        if confidence_factor:
            add("P2_TRAIT_CONFIDENCE", (traits["confidence"] - 0.5)
                * p2["confidence_score"] * confidence_factor)
        if view.opponent_bleeding and category in {"WAIT", "WALK_OUT", "DASH_OUT"}:
            add("P2_TRAIT_PATIENCE", (traits["patience"] - 0.5) * p2["patience_score"])
        if category in attacks and view.anger > 0 and control < 1:
            add("P2_ANGER_IMPULSE", (1 - control) * view.anger
                / state.config["defaults"]["pool_max"] * p2["impulse_score"] * traits["aggression"])
        if fighter.grounded and category in state.config["stability"]["grounded_ai_scores"]:
            add("P4_GROUNDED_CHOICE", state.config["stability"]["grounded_ai_scores"][category])
            if category == "GUARD" and view.opponent_phase in {"WINDUP", "ACTIVE"}:
                add("P4_COVER_IMMINENT_ATTACK", state.config["stability"]["grounded_cover_bonus"])
            if category == "CRAWL" and view.gap <= state.config["stability"]["grounded_crawl_gap"]:
                add("P4_CRAWL_FROM_PRESSURE", state.config["stability"]["grounded_crawl_bonus"])
        if category in {"DASH_OUT", "WALK_OUT", "RUN_OUT"} and pressure > 0 and control < 1:
            add("P2_PANIC", (1 - control) * pressure * p2["panic_score"])
        if (fighter.ai_stagnant_decisions >= p2["anti_deadlock"]["after_decisions"]
                and category in aggressive_categories):
            add("P2_ANTI_DEADLOCK", p2["anti_deadlock"]["bias"])
        penalty = adaptation_penalty(fighter, category, state.config)
        if penalty:
            add("P2_ADAPTATION", -penalty)
        if category in {"HEAVY", "LIGHT", "GUARD"}:
            add("R1_ANGER", rule[{"HEAVY": "rage_heavy", "LIGHT": "rage_light", "GUARD": "rage_guard"}[category]] * view.anger, "rage")
        if view.public_key.opp_state in {"RECOVERING", "WHIFFED", "STAGGERED"} and category in {"LIGHT", "HEAVY"}:
            if view.opponent_remaining >= strike_delay(state, view, choice):
                add("R2_PUNISH", rule["punish"], "opportunism")
        if view.public_key.opp_state == "WINDUP_H":
            light_delay = phase_ticks(state.config["actions"]["LIGHT"]["windup"], view.stats.speed, state.config)
            if view.opponent_strike_delay is not None and light_delay < view.opponent_strike_delay:
                if category == "LIGHT":
                    add("R3_INTERRUPT_HEAVY", rule["counter_heavy_light"], "reading")
            elif view.stats.strength >= state.config["guard"]["parry_str_ratio"]["heavy"] * view.opponent_strength:
                if category == "GUARD":
                    add("R3_PARRY_HEAVY", rule["counter_heavy_guard"], "reading")
            elif category == "DASH_OUT":
                add("R3_DODGE_HEAVY", rule["counter_heavy_dash"], "reading")
        if view.public_key.opp_state == "WINDUP_L":
            if category == "GUARD":
                add("R4_COUNTER_LIGHT", rule["counter_light_guard"], "reading")
            if category == "HEAVY" and view.opponent_strike_delay is not None and strike_delay(state, view, choice) > view.opponent_strike_delay:
                add("R4_HEAVY_TOO_SLOW", rule["counter_light_heavy"], "reading")
        if view.public_key.opp_state == "GUARDING" and category in {"HEAVY", "LIGHT"}:
            add("R5_GUARD_PRESSURE", rule["pressure_heavy" if category == "HEAVY" else "pressure_light"], "pressure")
        if view.public_key.vigor_band == "LOW":
            if category == "HEAVY":
                add("R6_LOW_VIGOR", -max(0, rule["low_vigor_threshold"] - view.vigor_pct) * rule["low_vigor_scale"])
                if view.exhausted:
                    add("R6_EXHAUSTED", rule["exhaust_heavy"])
            if view.gap >= rule["rest_gap"] and category == "WAIT":
                add("R6_REST", rule["low_vigor_rest"])
        if category in {"WALK_OUT", "RUN_OUT", "DASH_OUT"}:
            add("R7_FEAR_ESCAPE", rule["fear_escape"] * view.fear, "caution")
        elif category in attacks:
            add("R7_FEAR_ATTACK", rule["fear_attack"] * view.fear, "caution")
        if view.full_key.blood_band == "CRITICAL":
            if category == "GUARD":
                add("R8_LOW_BLOOD", rule["blood_guard"])
            elif category == "DASH_OUT":
                add("R8_LOW_BLOOD", rule["blood_dash"] * (1 - view.anger / state.config["defaults"]["pool_max"]))
        if view.full_key.pain_band == "HIGH" and view.anger < rule["pain_anger_max"]:
            if category == "GUARD":
                add("R9_PAIN", rule["pain_guard"])
            elif category == "DASH_OUT":
                add("R9_PAIN", rule["pain_dash"])
        repeats = rule["repeat_count"]
        if category in attacks and len(view.recent_attacks) >= repeats and all(item == category for item in view.recent_attacks[-repeats:]):
            add("R10_VARIETY", -rule["repeat_penalty"] * repeats, "discipline")
        if (len(state.fighters) <= 2 and view.opponent_bleeding
                and view.opponent_blood < rule["patience_blood"]
                and category in {"DASH_OUT", "WALK_OUT"}):
            add("R11_PATIENCE", rule["patience_escape"], "patience")
        if 1 <= view.gap <= rule["close_gap"]:
            if category == "DASH_LIGHT":
                add("R12_CLOSE", rule["close_dash_light"] + rule["close_anger"] * view.anger)
            elif category == "WALK_IN":
                add("R12_CLOSE", rule["close_walk"])
                if view.public_key.opp_state == "WINDUP_H":
                    add("R12_UNSAFE_WALK", rule["close_seen_heavy"])
        elif view.gap > rule["close_gap"]:
            if category == "WALK_IN":
                add("R12_CLOSE_FAR", rule["far_walk"])
            elif category == "RUN_IN":
                add("R12_CLOSE_FAR", rule["far_run"])
        if view.gap >= rule["rest_gap"] and view.fatigue > rule["rest_fatigue"] and category == "WAIT":
            add("R13_GAP_REST", rule["gap_rest"])
        scored.append(ScoredAction(category, choice, sum(value for _, value in terms), tuple(terms)))
    if not scored:
        return ()
    variation_rule = p2["variation"]
    max_score = max(item.score for item in scored)
    amplitude = variation_rule["amplitude"] * max(
        0, 1 - traits["discipline"] * variation_rule["discipline_reduction"])
    varied = []
    for item in scored:
        if max_score - item.score <= variation_rule["margin"] and amplitude:
            variation = _seeded_variation(state, fighter_id, item.category, amplitude)
            varied.append(ScoredAction(item.category, item.choice, item.score + variation,
                                       (*item.reasons, ("P2_SEEDED_VARIATION", variation))))
        else:
            varied.append(item)
    return tuple(varied)


def choose_action(state, fighter_id):
    scored = score_actions(state, fighter_id)
    if not scored:
        raise ValueError("No free legal NPC decision")
    # Candidate enumeration follows the fixed configured tie order, never dict order.
    return max(scored, key=lambda item: item.score)
