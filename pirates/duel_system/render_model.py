"""Immutable M9 render contract. UI formatters never receive CombatState."""
from dataclasses import dataclass
from copy import copy, deepcopy
import textwrap
import math
import re

from .engine import Choice, adrenaline_trigger_reasons, commit_action, legality, movement_path
from .ai import PublicKey
from .guide import CAUSE_LINES, _public_key
from .model import ActionKind, EventType, Fighter, Severity, Zone
from .geometry import attack_cells, attack_reach
from .defense import choose_defend
from .notebook import PatternState
from .stats import (clamp, effective_stats, fear, max_stamina, pain_burden,
                    stamina_cost, phase_ticks, weapon_edge, weapon_reach)
from .stability import stability_band
from .wounds import (classify_wound, defense_snapshot, injury_number_context,
                     injury_profile)


@dataclass(frozen=True)
class Preview:
    valid: bool
    error: str
    kind: str
    aim: str
    stamina: float
    fatigue: float
    strike_tick: int | None
    enemy_strike_tick: int | None
    risk: str
    timeline: str
    end_tick: int | None = None


@dataclass(frozen=True)
class ZoneOption:
    zone: Zone
    difficulty: float
    exposed: bool
    accuracy: float
    blocked_reason: str = ""
    forecast: str = ""


@dataclass(frozen=True)
class ActionOption:
    label: str
    enabled: bool
    details: str = ""
    assessment: str = "conditional"
    stamina: float | None = None
    time_ticks: int | None = None


@dataclass(frozen=True)
class ActorSummary:
    actor_id: str
    name: str
    pos: tuple[int, int]
    glyph: str
    hostile: bool
    distance: int
    posture: str
    action: str
    grounded: bool
    target_id: str | None = None
    is_player: bool = False


@dataclass(frozen=True)
class Popup:
    pos: tuple[int, int]
    text: str
    big: bool
    tick: int
    direction: tuple[int, int] = (0, 0)


@dataclass(frozen=True)
class RenderModel:
    presentation: tuple[tuple[str, int], ...]
    columns: int
    segments: int
    tick: int
    gap: int
    enemy_name: str
    enemy_level: int
    enemy_mood: str
    enemy_stamina: float
    enemy_fatigue: float
    enemy_action: str
    enemy_progress: str
    hand_exposed: bool
    enemy_sheet: tuple[tuple[str, str], ...]
    enemy_glyph: str
    enemy_pos: tuple[int, int]
    player_pos: tuple[int, int]
    floor: frozenset[tuple[int, int]]
    exits: frozenset[tuple[int, int]]
    width: int
    height: int
    danger: frozenset[tuple[int, int]]
    player_timeline: str
    enemy_timeline: str
    enemy_predicted: bool
    pools: tuple[tuple[str, float, float], ...]
    speed_penalty: float
    wounds: tuple[tuple[str, float], ...]
    player_state: str
    player_level: int
    player_xp: float
    player_xp_next: int
    instructions: str
    preview: Preview | None
    zones: tuple[ZoneOption, ...]
    actions: tuple[ActionOption, ...]
    log: tuple[str, ...]
    full_log: tuple[str, ...]
    why: tuple[str, ...]
    popups: tuple[Popup, ...]
    notebook_rows: tuple[str, ...]
    traits: tuple[str, ...]
    advice: object
    guide_mode: str
    player_sheet: tuple[tuple[str, str], ...] = ()
    comparison: tuple[str, ...] = ()
    enemy_remaining: int | None = None
    enemy_pools: tuple[tuple[str, float, float], ...] = ()
    enemy_wounds: tuple[tuple[str, float], ...] = ()
    player_action: str = "IDLE"
    player_remaining: int | None = None
    player_aim: str = ""
    enemy_aim: str = ""
    player_penalties: tuple[tuple[str, float], ...] = ()
    own_readout: tuple[tuple[str, float], ...] = ()
    enemy_readout: tuple[tuple[str, float], ...] = ()
    enemy_observed: tuple[tuple[str, float], ...] = ()
    combat_rules: tuple[tuple[str, float], ...] = ()
    functional_alerts: tuple[str, ...] = ()
    target_effects: tuple[tuple[str, str], ...] = ()
    player_status_rows: tuple[str, ...] = ()
    enemy_status_rows: tuple[str, ...] = ()
    enemy_armor: tuple[tuple[str, float], ...] = ()
    movement_timing: tuple[tuple[str, int], ...] = ()
    player_emotion_rows: tuple[str, ...] = ()
    enemy_tell: tuple[str, ...] = ()
    enemy_confidence: str = "Unknown — no named-NPC observations in similar conditions."
    enemy_commitment: str = "No visible commitment."
    enemy_opportunity: str = "Next choice unknown."
    enemy_response_summary: str = "Response timing unknown."
    enemy_condition: str = "No visible condition read."
    player_behavior_modes: tuple[tuple[str, str], ...] = ()
    player_condition_rows: tuple[str, ...] = ()
    player_injury_rows: tuple[str, ...] = ()
    enemy_injury_rows: tuple[str, ...] = ()
    player_stability: float = 100
    player_balance: float = 50
    player_stability_state: str = "STABLE"
    player_grounded: bool = False
    enemy_stability_state: str = "STABLE"
    enemy_grounded: bool = False
    adrenaline_status: str = ""
    ground_weapons: tuple[tuple[str, str, tuple[int, int], float, str], ...] = ()
    player_weapon_status: str = ""
    enemy_weapon_status: str = ""
    player_hand_severed: bool = False
    pickup_range: int = 1
    actors: tuple[ActorSummary, ...] = ()
    target_options: tuple[ActorSummary, ...] = ()
    threats: tuple[ActorSummary, ...] = ()
    threat_overflow: int = 0
    enemy_visible: bool = True
    group_combat: bool = False
    zone_attack_kind: str = ""


def timeline_for(action, tick, horizon):
    if action is None:
        return "." * horizon
    result = []
    for at in range(tick, tick + horizon):
        phase = action.phase_at(at)
        if at == action.strike_tick:
            char = "H" if action.kind == ActionKind.HEAVY else "T" if action.kind == ActionKind.THRUST else "X" if action.kind == ActionKind.DISARM else "L"
        elif at == action.guard_ready_tick and not action.released:
            char = "G"
        elif phase == "WINDUP":
            char = "h" if action.kind == ActionKind.HEAVY else "t" if action.kind == ActionKind.THRUST else "x" if action.kind == ActionKind.DISARM else "l"
        else:
            char = {"DASH": "d", "MOVE": "m", "GUARD_RAISE": "g", "GUARD_HOLD": "g",
                    "GUARD_LOWER": "-", "RECOVERY": "-", "WAIT": "w", "BANDAGE": "b",
                    "EAT": "e", "DRINK": "e", "PICKUP": "p", "ACTIVE": "+",
                    "FEINT": "f", "SHOVE": "s", "ADRENALINE": "a"}.get(phase, ".")
        result.append(char)
    return "".join(result)


def preview_action(state, choice):
    player = state.fighters[0]
    if len(state.fighters) > 2:
        if choice.target_id:
            enemy = state.primary_opponent(player.id, choice.target_id, active_only=False)
            if enemy is not None and not state.can_see(player, enemy):
                enemy = None
        else:
            visible = state.visible_hostiles_for(player.id)
            enemy = visible[0] if visible else None
    else:
        enemy = state.primary_opponent(player.id, choice.target_id, active_only=False)
    if enemy is None and len(state.fighters) <= 2:
        enemy = state.primary_opponent(player.id, active_only=False)
    error = legality(state, player.id, choice)
    enemy_strike = enemy.action.strike_tick if enemy and enemy.action else None
    if error:
        return Preview(False, error, choice.kind.value, choice.aim.value if choice.aim else "",
                       0, 0, None, enemy_strike, error, "")
    # An immediate action preview needs independent fighters/RNG counters, not
    # thousands of historical events or another copy of the read-only config.
    # commit_action only reads config/floor/exits; it writes these private lists.
    sim = copy(state)
    sim.fighters = deepcopy(state.fighters)
    sim.event_log = []
    sim.pending_interrupts = list(state.pending_interrupts)
    action = commit_action(sim, player.id, choice)
    cost = next(event.deltas["cost"] for event in sim.event_log
                if event.type == EventType.ACTION_START and event.actor == player.id)
    acc = effective_stats(player, state.config, state.tick).accuracy
    extra = state.config["zones"]["guard_hand_extra"] if choice.aim == Zone.HAND and enemy and enemy.action and enemy.action.phase_at(state.tick) == "GUARD_HOLD" else 0
    needed = state.config["zones"]["difficulty"][choice.aim.value] + extra if choice.aim else 0
    risk = "Moving is defenseless." if choice.direction else ""
    if choice.aim:
        dispersion = state.config["accuracy"]["aim_dispersion"]
        torso_difficulty = state.config["zones"]["difficulty"][Zone.TORSO.value]
        clean_probability = clamp(
            dispersion["clean_base"] + acc * dispersion["per_accuracy"]
            - max(0, needed-torso_difficulty) * dispersion["difficulty_penalty"],
            dispersion["clean_min"], dispersion["clean_max"])
        if (choice.aim == Zone.HAND and enemy
                and not defense_snapshot(state, enemy).hand_exposed):
            clean_probability = 0
        if (choice.aim == Zone.HAND and enemy
                and len(enemy.permanent_injuries.severed_hands) >= 2):
            clean_probability = 0
        if (choice.aim == Zone.EYE and enemy
                and enemy.permanent_injuries.eyes_damaged >= 2):
            clean_probability = 0
        risk = (f"Seeded aim dispersion: {clean_probability:.0%} clean contact; drift/miss possible. "
                "Accuracy changes this chance, not legal targeting or reach.")
    if cost > player.stamina:
        risk += " Shortfall becomes fatigue/exhaustion."
    if (choice.aim and not choice.direction and enemy
            and max(abs(player.pos[0]-enemy.pos[0]), abs(player.pos[1]-enemy.pos[1]))
            > weapon_reach(player, state.config)):
        risk += " Target is out of reach now; may whiff."
    if choice.kind == ActionKind.PICKUP:
        item = next((row for row in state.ground_weapons if row.weapon.item_id == choice.item_id), None)
        if item:
            risk += f" Pick up {item.weapon.name} ({item.weapon.material}, condition {item.weapon.condition:g}%)."
    if choice.kind == ActionKind.DASH_LIGHT and enemy and action.path:
        endpoint = action.path[-1][1]
        post_dash_gap = max(abs(endpoint[0]-enemy.pos[0]), abs(endpoint[1]-enemy.pos[1]))
        reach = weapon_reach(player, state.config)
        risk += (f" After your selected route the visible target is currently {post_dash_gap} tile(s) away; "
                 f"slash reach is {reach}. "
                 + ("If that gap remains at contact, the slash will miss. "
                    if post_dash_gap > reach else
                    "The target can still move before contact. "))
    return Preview(True, "", choice.kind.value, choice.aim.value if choice.aim else "", cost,
                   sim.fighters[0].fatigue - player.fatigue, action.strike_tick, enemy_strike, risk,
                    timeline_for(action, state.tick, state.config["ui"]["timeline_ticks"]), action.end_tick)


def _conditional_injury_forecast(state, attacker, defender, kind, zone):
    """Estimate clean-contact outcomes from visible state without RNG or rollouts."""
    cfg, tick = state.config, state.tick
    kind, zone = ActionKind(kind), Zone(zone)
    weapon = attacker.equipment.weapon
    damage_type = cfg["actions"][kind.value].get(
        "damage_type", weapon.damage_type if weapon is not None else "BLUNT")
    live = effective_stats(attacker, cfg, tick)
    power = (live.strength * (cfg["gear"]["unarmed_power"]
                              if attacker.disarmed or weapon is None
                              else cfg["actions"][kind.value]["power"])
             + weapon_edge(attacker, cfg))
    armor_piece = defender.equipment.armor.get(zone)
    armor = armor_piece.value if armor_piece else 0
    net = power - armor
    severity = classify_wound(cfg, zone, net, kind, False, damage_type, site=zone.value)
    profile = injury_profile(cfg, zone, severity, kind, damage_type, net, site=zone.value,
                             weapon_hand=defender.weapon_hand)
    wound = cfg["wounds"][zone.value].get(severity.value, {})
    bleed = (wound.get("bleed", 0)
             * cfg["injury"]["damage_types"][str(damage_type).upper()]["bleed_mult"])
    clean_rule = cfg["accuracy"]["aim_dispersion"]
    difficulty = cfg["zones"]["difficulty"][zone.value]
    torso = cfg["zones"]["difficulty"][Zone.TORSO.value]
    clean_chance = clamp(
        clean_rule["clean_base"] + live.accuracy * clean_rule["per_accuracy"]
        - max(0, difficulty-torso) * clean_rule["difficulty_penalty"],
        clean_rule["clean_min"], clean_rule["clean_max"])
    numbers = injury_number_context(
        cfg, zone, kind, damage_type, net, drifted=False, site=zone.value,
        severity=severity, eye_trauma=defender.permanent_injuries.eye_trauma)
    effects = []
    for key, label in (("strength_mult", "Strength"), ("speed_mult", "Speed")):
        if key in wound:
            effects.append(f"{label} ×{wound[key]:g}")
    if wound.get("duration"):
        effects.append(f"{wound['duration']}t")
    if wound.get("no_dash"):
        effects.append("no Dash")
    if bleed:
        effects.append(f"bleed {bleed:g}/turn")
    if profile["function"] != "bleeding and pain only" and not effects:
        effects.append(profile["function"])
    effect_text = ", ".join(effects) or profile["function"]
    next_effect = (f"{numbers['next_label']} starts at {numbers['next_net_threshold']:g} wound-force points "
                   f"(+{numbers['net_to_next']:g} on one contact)"
                   if numbers.get("next_label") else "no higher configured per-hit threshold")
    wound_net = cfg["wound_net"]
    graze_band, cut_band, deep_min = (wound_net["graze"], wound_net["cut"], wound_net["deep"])
    bands_text = (f"graze {graze_band[0]}–{graze_band[1]}, cut {cut_band[0]}–{cut_band[1]}, "
                  f"deep ≥{deep_min} points")
    armor_text = (f"{armor:.1f} zone-protection points" if armor_piece else
                  f"0 zone-protection points (no {zone.value.lower()} armor item)")
    text = (f"If line/reach holds and contact is clean: wound force {net:.1f} points "
            f"= attack force {power:.1f} − {armor_text}; {severity.value.lower()} "
            f"({bands_text}); {effect_text}; {next_effect}; "
            f"clean chance {clean_chance:.0%}. Points have no fixed maximum and are not a percent.")
    if zone == Zone.EYE:
        threshold = cfg["wound_net"]["eye_injury"]
        eyes = defender.permanent_injuries.eyes_damaged
        qualifies = severity in {Severity.CUT, Severity.DEEP, Severity.SEVERED} and net >= threshold
        if qualifies and eyes < 2:
            consequence = f"this would impair eye {eyes+1}/2"
        elif eyes >= 2:
            consequence = "both eyes are already impaired; no further eye injury is possible"
        else:
            consequence = (f"requires a clean CUT-or-worse contact and at least {threshold:g} post-armor force "
                           f"to impair eye {eyes+1}/2")
        text += f" Eye injury (threshold {threshold:g} post-armor force): {consequence}."
    phase = defender.action.phase_at(tick) if defender.action else "IDLE"
    if (defender.action and defender.action.kind == ActionKind.GUARD
            and phase in {"GUARD_RAISE", "GUARD_HOLD"}):
        text += " Observed Guard succeeds: 0 impact, no wound."
    else:
        text += " Defense/next choice unknown."
    return text


def event_line(event, names):
    actor = names.get(event.actor, event.actor or "Fight")
    if event.type == EventType.ACTION_START:
        return f"{actor}: {event.deltas['kind'].lower()} committed"
    if event.type == EventType.ATTACK_LOCK:
        return f"{actor}: attack direction locked"
    if event.type == EventType.WHIFF and "OUTSIDE_LOCKED_ATTACK_GEOMETRY" in event.cause_tags:
        return f"{actor}: target moved outside the locked attack line"
    if event.type == EventType.HIT:
        structure = event.deltas.get("structure", "soft tissue").replace("_", " ").lower()
        depth = event.deltas.get("depth", event.severity.value).replace("_", " ").lower()
        return f"{actor}: {event.zone.value.lower()} {event.severity.value.lower()} ({depth} {structure})"
    if event.type == EventType.CLASH:
        target = names.get(event.target, "opponent")
        lane = event.deltas.get("lane", "MID").lower()
        return f"{actor} and {target}: blades clashed on {lane} line; neither body strike landed"
    if event.type == EventType.WOUND:
        target = names.get(event.target, "opponent")
        return f"{actor}: caused a bleeding wound on {target} ({event.zone.value.lower()})"
    if event.type == EventType.MOVE:
        destination = tuple(event.deltas.get("to", ()))
        return f"{actor}: moved to {destination}"
    if event.type == EventType.STATUS and "MOVEMENT_BLOCKED" in event.cause_tags:
        blocker = names.get(event.target, "another fighter")
        return f"{actor}: movement blocked at {tuple(event.deltas.get('position', ()))} by {blocker}"
    if event.type == EventType.STATUS and "PICKUP_FAILED" in event.cause_tags:
        reason = event.deltas.get("reason", "UNAVAILABLE").replace("_", " ").lower()
        return f"{actor}: weapon pickup failed — {reason}"
    if event.type == EventType.INTERRUPT:
        action = event.deltas.get("kind", "action").replace("_", "+").lower()
        return f"{actor}: {action} interrupted"
    if event.type == EventType.INJURY_RESPONSE:
        response = event.deltas.get("response", "reacted").replace("_", " ").lower()
        if response == "recoil":
            return f"{actor}: forced back one tile by injury reflex"
        if response == "braced":
            blocker = event.deltas.get("blocker", "an obstruction").replace("_", " ").lower()
            return f"{actor}: braced; recoil blocked by {blocker}"
        if response == "refractory":
            return f"{actor}: held position during recoil recovery"
        if response == "flinch":
            return f"{actor}: forced back one tile by injury reflex"
        return f"{actor}: injury response {response}"
    if event.type == EventType.STABILITY:
        amount = event.deltas.get("loss", event.deltas.get("gain", 0))
        verb = "lost" if "loss" in event.deltas else "recovered"
        source = event.deltas.get("source", "stability change").replace("_", " ").lower()
        return f"{actor}: {verb} {amount:g} stability ({source})"
    if event.type == EventType.FALL:
        source = event.deltas.get("source", "instability").replace("_", " ").lower()
        return f"{actor}: fell from {source}; grounded"
    if event.type == EventType.RISE:
        return f"{actor}: rose to a stumbling stance"
    if event.type == EventType.FEINT:
        return f"{actor}: feint {event.deltas.get('outcome', 'resolved').replace('_', ' ').lower()}"
    if event.type == EventType.SHOVE:
        target = names.get(event.target, "opponent")
        if event.deltas.get("pile_up"):
            return f"{actor} and {target}: fell together into a ground pile"
        outcome = event.deltas.get("outcome", "resolved").replace("_", " ").lower()
        return f"{actor}: shove {target} — {outcome}"
    if event.type == EventType.DISARM:
        target = names.get(event.target, "opponent")
        outcome = event.deltas.get("outcome", "resolved").replace("_", " ").lower()
        return f"{actor}: disarm attempt on {target} — {outcome}"
    if event.type == EventType.ADRENALINE:
        outcome = event.deltas.get("outcome", "resolved").replace("_", " ").lower()
        return f"{actor}: adrenaline {outcome}"
    if event.type == EventType.WEAPON_STRESS:
        return (f"{actor}: {event.deltas.get('name', 'weapon')} {event.deltas['contact'].lower()} — "
                f"condition {event.deltas['condition_after']:.0f}%")
    if event.type == EventType.WEAPON_DROP:
        return f"{actor}: dropped {event.deltas.get('name', 'weapon')} ({event.deltas.get('cause', 'combat').lower()})"
    if event.type == EventType.WEAPON_BREAK:
        return f"{actor}: {event.deltas.get('name', 'weapon')} broke on {event.deltas.get('contact', 'contact').lower()}"
    if event.type == EventType.WEAPON_PICKUP:
        return f"{actor}: picked up {event.deltas.get('name', 'weapon')}"
    if event.type == EventType.EYE_INJURY:
        return f"{actor}: permanent eye injury — {event.deltas.get('eyes_damaged_after', 1)} eye(s) impaired"
    if event.type == EventType.SEVER:
        target = names.get(event.target, "opponent")
        side = event.deltas.get("side")
        if event.deltas.get("ear_lost"):
            ear = f"{side.lower()} " if side else ""
            return f"{target}: {ear}ear severed — permanent structural loss; hearing mechanic not modeled"
        hand = f"{side.lower()} " if side else ""
        function = ("weapon hand lost; cannot hold a weapon" if event.deltas.get("weapon_hand_lost")
                    else "supporting hand lost; one-handed weapon remains usable")
        return f"{target}: {hand}hand severed — {function}"
    if event.type == EventType.DECAPITATION:
        return f"{actor}: clean heavy cutting head strike caused death"
    if event.type == EventType.INCAPACITATED:
        return f"{actor}: incapacitated ({event.deltas.get('cause', 'injury').replace('_', ' ').lower()})"
    if event.type == EventType.BEHAVIOR_MODE:
        mode = event.deltas["mode_after"].replace("_", " ").lower()
        meter = event.deltas["meter"]
        value = event.deltas.get("value_after", event.deltas.get("threshold", 0))
        threshold = event.deltas.get("threshold", 0)
        cause = str(event.deltas.get("cause", "state change")).replace("_", " ").lower()
        return (f"{actor}: {meter} {mode} at {value:g}/100 "
                f"(threshold {threshold:g}; {cause})")
    if event.type in {EventType.PARRY, EventType.WHIFF, EventType.BLOCK, EventType.GUARD_BREAK,
                      EventType.STAGGER, EventType.ANGER_CRASH, EventType.EXHAUST, EventType.FEAR_BREAK}:
        return f"{actor}: {event.type.value.lower().replace('_', ' ')}"
    if event.type == EventType.FIGHT_END:
        return f"Fight ended: {event.deltas['reason']}"
    if event.type == EventType.XP_GAIN:
        return f"{actor}: +{event.deltas['xp']:.0f} XP" + (f" ({event.deltas['guide_mode']} guide)" if event.deltas["guided"] else "")
    if event.type == EventType.LEVEL_UP:
        return f"{actor}: LEVEL UP -> {event.deltas['level']}"
    return None


def weapon_status(fighter):
    weapon = fighter.equipment.weapon
    if weapon is None or fighter.disarmed:
        return "Weapon: unarmed / disarmed"
    return f"Weapon: {weapon.name} · {weapon.material} · condition {weapon.condition:.0f}%"


def _enemy_tells(enemy, key, entry, config):
    rows = []
    if entry.state in {PatternState.HABIT, PatternState.TENDENCY, PatternState.TENTATIVE} and entry.top_action:
        count = dict(entry.counts).get(entry.top_action, 0)
        if entry.state == PatternState.TENTATIVE:
            rows.append(f"Early read: {entry.top_action.lower()} ({count}/{entry.total}); weak evidence.")
        else:
            rows.append(f"Often {entry.top_action.lower()} here ({count}/{entry.total}); may change.")
    if key.anger_band in {"ANGRY", "RAGE"}:
        rows.append("Looks angry; may press or rush. Next choice uncertain.")
    elif key.anger_band == "WARM":
        rows.append("Agitated; watch his commitment. Intent uncertain.")
    if enemy.wounds:
        wound = enemy.wounds[-1]
        rows.append(f"Visible wounds: {wound.zone.value.lower()} {wound.structure.replace('_', ' ').lower()}; reflex uncertain.")
    if enemy.fatigue >= config["ai"]["vigor_low_fatigue"]:
        rows.append("Looks winded; next choice unknown.")
    return tuple(rows[:2] or ("No reliable read yet; next choice unknown.",))


def _plausible_response_read(state, player, enemy, key, entry):
    """Return a cue-based watch list; this is deliberately not the NPC policy."""
    if enemy.action:
        return "Committed action is visible; no next-action prediction."
    if enemy.stagger_until > state.tick or enemy.grounded or enemy.unconscious:
        return "Control/posture limits choices; exact response remains unknown."
    learned = []
    if entry.state in {PatternState.HABIT, PatternState.TENDENCY, PatternState.TENTATIVE}:
        learned = sorted(entry.counts, key=lambda item: (-item[1], item[0]))[:3]
    if learned:
        observations = ", ".join(f"{action.replace('_', '+').title()} {count}/{entry.total}"
                                  for action, count in learned)
        evidence = f"Observed in this situation ({observations}); may change."
    else:
        evidence = "No reliable same-situation pattern."

    gap = max(abs(enemy.pos[0]-player.pos[0]), abs(enemy.pos[1]-player.pos[1]))
    reach = weapon_reach(enemy, state.config)
    angry = key.anger_band in {"ANGRY", "RAGE"}
    vigorous = (enemy.stamina > state.config["actions"]["HEAVY"]["stamina"]
                and enemy.fatigue < state.config["ai"]["vigor_low_fatigue"]
                and not enemy.exhausted)
    has_thrust = (not enemy.disarmed and enemy.equipment.weapon is not None
                  and enemy.equipment.weapon.damage_type != "BLUNT")
    if gap <= reach:
        candidates = ["Light", *( ["Thrust"] if has_thrust else []),
                      *( ["Heavy"] if vigorous else []), "Guard", "move out"]
        if angry:
            candidates = ["Light", *( ["Thrust"] if has_thrust else []),
                          *( ["Heavy"] if vigorous else []), "Dash+Light", "Guard"]
    elif gap <= state.config["ai"]["close_gap"]:
        candidates = (["Dash+Light", "Dash in", "Run in", "Guard"] if angry else
                      ["Dash+Light", "Dash/Run in", "Guard", "move out"])
    else:
        candidates = (["Run/Dash in", "Dash+Light", "Guard", "Wait"] if angry else
                      ["Run in", "Walk in", "Guard", "Wait"])
    if not vigorous:
        candidates = [item for item in candidates if item not in {"Heavy", "Dash+Light"}]
        candidates.append("recover or move")
    candidates = list(dict.fromkeys(candidates))[:5]
    cue = ("Anger favors pressure/closing" if angry else
           "No strong anger cue; defense, attack and movement remain plausible")
    if not vigorous:
        cue += "; visible low vigor/windedness may favor cheaper choices"
    return f"{cue}. Watch list (not exhaustive, not a prediction): {', '.join(candidates)}. {evidence}"


def _response_coverage_note(kind):
    if kind == ActionKind.THRUST:
        return ("Against those possibilities: Thrust is a quick straight ray; it can beat a slower approach only "
                "if the ray still contains him. A Dash+Light sidestep can leave it, and intersecting blade paths "
                "within the clash window can stop both attacks; this is not a guaranteed hit.")
    if kind in {ActionKind.LIGHT, ActionKind.DASH_LIGHT}:
        return ("Against those possibilities: prefer the quick contact when its estimated strike is earlier or "
                "can interrupt; Dash+Light also has an exposed approach and a locked final sector. A faster-looking "
                "option is not a guaranteed hit.")
    if kind == ActionKind.HEAVY:
        return ("Against those possibilities: Heavy is a force choice, not a safe answer to pressure; use a visible "
                "opening because a quicker hit, sidestep or intersecting blade can spoil it.")
    if kind == ActionKind.GUARD:
        return ("Against those possibilities: Guard can parry only with the required timing and Strength; its covered "
                "line matters, and movement/Feint or an uncovered site can bypass it.")
    if kind in {ActionKind.WALK, ActionKind.RUN, ActionKind.DASH, ActionKind.DISENGAGE,
                ActionKind.CRAWL}:
        return ("Against those possibilities: movement avoids a strike only when the first step resolves before contact "
                "and leaves its locked sector/ray; same-tick strikes resolve before movement.")
    return ("Against those possibilities: this does not guarantee a counter; compare the visible strike deadline, "
            "range, line and action cost before committing.")


def _opponent_read(state, enemy, *, visible, group_combat):
    """Summarize only current public commitment and visible physical evidence."""
    if not visible:
        return ("No visible commitment; next choice and range unknown.",
                "No observed opening; next choice remains unknown.",
                "No visible condition read.")
    if group_combat:
        posture = "grounded" if enemy.grounded else "standing"
        return (f"Visible hostile: {posture}; commitment detail may be incomplete.",
                "Do not infer an opening from an unseen choice.",
                "Group condition details withheld unless directly witnessed.")

    action = enemy.action
    # A render projection may estimate visible condition, but never inspect a
    # hidden opponent's authored tolerance trait.
    stats = effective_stats(enemy, state.config, state.tick,
                            pain_tolerance=state.config["defaults"]["pain_tolerance"])
    player = state.fighters[0]
    fastest_choice_effect = _fastest_opponent_effect(state, enemy, player, stats)
    incapacitated = (enemy.blood <= 0 or enemy.unconscious or enemy.yielded
                     or enemy.fled or enemy.decapitated)
    if incapacitated:
        commitment = "Incapacitated or out of the fight."
        opportunity = "Cannot currently make a voluntary action."
    elif action is None:
        if enemy.stagger_until > state.tick:
            commitment = f"Staggered through t{enemy.stagger_until}; no current commitment."
            opportunity = (f"Cannot voluntarily act for {enemy.stagger_until-state.tick} more ticks; "
                           f"then {fastest_choice_effect}.")
        else:
            commitment = "Ready; no committed action visible."
            opportunity = (f"Can choose now — next choice unknown; {fastest_choice_effect}. "
                           "Readiness is not an opening by itself.")
    else:
        name = action.kind.value.replace("_", " ").title()
        detail = f"Committed {name}"
        if action.aim:
            detail += f" → {action.aim.value.title()}"
            from .geometry import attack_lane
            detail += f" ({attack_lane(action.aim).lower()} line)"
        if action.attack_bearing:
            direction = {(0, -1): "north", (1, -1): "northeast", (1, 0): "east",
                         (1, 1): "southeast", (0, 1): "south", (-1, 1): "southwest",
                         (-1, 0): "west", (-1, -1): "northwest"}[action.attack_bearing]
            if action.bearing_lock_tick is not None and action.bearing_lock_tick > state.tick:
                detail += f"; tracking {direction}, locks t{action.bearing_lock_tick}"
            else:
                detail += f"; locked {direction} sector/ray"
        first_step = next((tick for tick, _ in action.path if tick >= state.tick), None)
        if first_step is not None:
            detail += f"; next step t{first_step}"
        if action.strike_tick is not None and action.strike_tick >= state.tick:
            detail += f"; contact t{action.strike_tick}"
        commitment = detail + "."
        if action.strike_tick is not None and action.strike_tick >= state.tick:
            opportunity = (f"Committed contact in {action.strike_tick-state.tick} ticks; "
                           f"earliest next choice no sooner than t{max(action.end_tick or state.tick, enemy.stagger_until)}. "
                           f"After that: {fastest_choice_effect}. Do not assume a free follow-up.")
        elif action.end_tick is not None and action.end_tick > state.tick:
            opportunity = (f"Recovering/committed until t{action.end_tick} "
                           f"({action.end_tick-state.tick} ticks); next choice is not yet available. "
                           f"After that: {fastest_choice_effect}.")
        elif action.kind == ActionKind.GUARD and action.guard_ready_tick is not None:
            release_tick = (state.tick if enemy.riposte else
                            action.guard_ready_tick + action.parry_window)
            if (not enemy.riposte and player.action and player.action.kind == ActionKind.HEAVY
                    and player.action.phase_at(state.tick) == "WINDUP"):
                release_tick += state.config["guard"]["heavy_window_bonus"]
            lower = phase_ticks(state.config["guard"]["lower"], action.locked_stats.speed,
                                state.config, recovery_weight=enemy.equipment.total_weight)
            ready_tick = max(state.tick, release_tick + lower, enemy.stagger_until)
            opportunity = (f"Held guard has no fixed recovery yet; earliest next choice no sooner than "
                           f"t{ready_tick}, then {fastest_choice_effect}.")
        else:
            opportunity = "Commitment shown; no strike deadline is currently visible."

    condition = []
    if enemy.grounded:
        condition.append("grounded")
    if enemy.stagger_until > state.tick:
        condition.append(f"staggered through t{enemy.stagger_until}")
    if enemy.disarmed:
        condition.append("disarmed")
    if enemy.wounds:
        latest = enemy.wounds[-1]
        condition.append(f"{latest.zone.value.lower()} {latest.depth.lower()} wound")
        bleed = sum(wound.bleed_per_turn for wound in enemy.wounds if not wound.bandaged)
        if bleed > 0:
            condition.append(f"visible bleeding {bleed:g}/turn")
    if enemy.fatigue >= state.config["ai"]["vigor_low_fatigue"]:
        condition.append("looks winded")
    opponent_stability = stability_band(enemy, state.config)
    if opponent_stability in {"OFF_BALANCE", "STUMBLING"}:
        condition.append("unsteady stance")
    condition_text = ("Visible condition: " + "; ".join(condition) + "."
                      if condition else "No obvious visible injury or posture impairment.")
    return commitment, opportunity, condition_text


def _opponent_response_summary(state, enemy, *, visible, group_combat):
    """Compact earliest voluntary response and fastest post-choice effect for the dashboard card."""
    if not visible:
        return "Response timing unknown."
    if group_combat:
        return "Response timing unknown; group profile hidden."
    if (enemy.blood <= 0 or enemy.unconscious or enemy.yielded
            or enemy.fled or enemy.decapitated):
        return "Cannot currently act."
    stats = effective_stats(enemy, state.config, state.tick,
                            pain_tolerance=state.config["defaults"]["pain_tolerance"])
    player = state.fighters[0]
    next_effect = _fastest_opponent_effect(state, enemy, player, stats)
    next_effect = next_effect.removesuffix(
        " after choosing, current condition-adjusted effective Speed")
    next_effect = re.sub(r"step (\d+)t \((\w+)\)", r"\2 step \1t", next_effect)
    next_effect = re.sub(r"(Light|Thrust) strike (\d+)t if line/reach holds",
                         r"\1 \2t if clear", next_effect)
    next_effect = next_effect.replace("; ", "; ")
    action = enemy.action
    if action is None:
        available = max(state.tick, enemy.stagger_until)
    elif action.end_tick is not None:
        available = max(action.end_tick, enemy.stagger_until)
    elif action.kind == ActionKind.GUARD and action.guard_ready_tick is not None:
        release = state.tick if enemy.riposte else action.guard_ready_tick + action.parry_window
        if (not enemy.riposte and player.action and player.action.kind == ActionKind.HEAVY
                and player.action.phase_at(state.tick) == "WINDUP"):
            release += state.config["guard"]["heavy_window_bonus"]
        lower = phase_ticks(state.config["guard"]["lower"], action.locked_stats.speed,
                            state.config, recovery_weight=enemy.equipment.total_weight)
        available = max(state.tick, release + lower, enemy.stagger_until)
    else:
        return "No finite response time visible."
    wait = max(0, available-state.tick)
    return (f"Choice unknown; {next_effect}" if wait == 0 else
            f"+{wait}t; then {next_effect}")


def _fastest_opponent_effect(state, enemy, player, stats):
    """Estimate fastest legal first step and in-range strike from current public combat state."""
    directions = ((0, -1), (1, -1), (1, 0), (1, 1),
                  (0, 1), (-1, 1), (-1, 0), (-1, -1))
    next_choice_tick = max(state.tick, enemy.stagger_until,
                           enemy.action.end_tick if enemy.action and enemy.action.end_tick is not None
                           else state.tick)
    if enemy.grounded:
        movement_kinds = (ActionKind.CRAWL,)
    else:
        movement_kinds = (ActionKind.WALK, ActionKind.RUN, ActionKind.DASH)
    movement_ticks = []
    for kind in movement_kinds:
        if kind == ActionKind.DASH and (enemy.exhausted or enemy.stamina <= 0):
            continue
        if kind == ActionKind.DASH and any(
                status.active(next_choice_tick) and status.no_dash for status in enemy.statuses):
            continue
        for direction in directions:
            path = movement_path(state, enemy, Choice(kind, direction=direction))
            if not path:
                continue
            destination = path[0]
            if state.walkable is not None and destination not in state.walkable:
                continue
            if any(other.id != enemy.id and state.occupies_tile(other) and other.pos == destination
                   for other in state.fighters):
                continue
            movement_ticks.append((phase_ticks(state.config["actions"][kind.value]["per_tile"],
                                               stats.speed, state.config), kind.value))
    movement = min(movement_ticks, default=None)
    movement = (movement[0], ActionKind(movement[1])) if movement else None
    gap = max(abs(enemy.pos[0]-player.pos[0]), abs(enemy.pos[1]-player.pos[1]))
    strikes = []
    if gap <= attack_reach(ActionKind.LIGHT):
        strikes.append((phase_ticks(state.config["actions"]["LIGHT"]["windup"],
                                    stats.speed, state.config), "Light"))
    if (gap <= attack_reach(ActionKind.THRUST) and not enemy.disarmed
            and enemy.equipment.weapon is not None
            and enemy.equipment.weapon.damage_type != "BLUNT"):
        strikes.append((phase_ticks(state.config["actions"]["THRUST"]["windup"],
                                    stats.speed, state.config), "Thrust"))
    parts = []
    if movement:
        parts.append(f"step {movement[0]}t ({movement[1].value.lower()})")
    if strikes:
        strike_ticks, kind = min(strikes)
        parts.append(f"{kind} strike {strike_ticks}t if line/reach holds")
    return "; ".join(parts) + " after choosing, current condition-adjusted effective Speed"


def build_render_model(state, notebook, selected=None, advice=None, *, timeline=True, guide_mode="off",
                       playback=False, zone_kind=None):
    player = state.fighters[0]
    group_combat = len(state.fighters) > 2
    visible_hostiles = sorted((fighter for fighter in state.hostiles_for(player.id)
                               if state.can_see(player, fighter)),
                              key=lambda fighter: (max(abs(player.pos[0]-fighter.pos[0]),
                                                       abs(player.pos[1]-fighter.pos[1])), fighter.id))
    enemy = None
    if selected and selected.target_id:
        candidate = state.primary_opponent(player.id, selected.target_id, active_only=False)
        if candidate and (len(state.fighters) <= 2 or state.can_see(player, candidate)):
            enemy = candidate
    enemy = enemy or (visible_hostiles[0] if visible_hostiles else None)
    cfg, horizon = state.config, state.config["ui"]["timeline_ticks"]
    if enemy is None and len(state.fighters) <= 2:
        enemy = state.primary_opponent(player.id, active_only=True) or state.primary_opponent(player.id, active_only=False)
    enemy_visible = enemy is not None and state.can_see(player, enemy)
    if enemy is None:
        # A neutral projection keeps empty-contact UI states renderable without
        # consulting any off-screen fighter's condition, action or profile.
        enemy = Fighter("unknown-hostile", "No visible hostile", cfg["defaults"]["level"],
                        **{key: cfg["defaults"][key] for key in
                           ("strength", "speed", "accuracy", "stamina_stat", "endurance")},
                        pos=player.pos, team_id="unknown-hostile")
    snap = defense_snapshot(state, enemy)
    phase = enemy.action.phase_at(state.tick) if enemy.action else "IDLE"
    glyph = "R!" if enemy.blood <= 0 or enemy.unconscious or enemy.yielded or enemy.decapitated else "R~" if enemy.stagger_until > state.tick else {
        "WINDUP": "R^" if enemy.action and enemy.action.kind == ActionKind.HEAVY else "R/",
        "GUARD_HOLD": "R]", "GUARD_RAISE": "R]", "DASH": "R>"}.get(phase, "R")
    key = (_public_key(state, enemy, observer=player.id) if enemy_visible and len(state.fighters) > 2
           else _public_key(state, enemy) if enemy_visible
           else PublicKey("FAR", "CALM", "LOW", "UNKNOWN", "UNSEEN"))
    entry = notebook.entry("", key, npc_id=enemy.id)
    enemy_tell = _enemy_tells(enemy, key, entry, cfg) if enemy_visible and not group_combat else ()
    plausible_response_read = (_plausible_response_read(state, player, enemy, key, entry)
                               if enemy_visible and not group_combat else "")
    action_count = dict(entry.counts).get(entry.top_action, 0) if entry.top_action else 0
    enemy_confidence = {
        PatternState.UNKNOWN: "Unknown — no named-NPC observations in similar conditions.",
        PatternState.TENTATIVE: f"Low — {action_count}/{entry.total} similar observations; weak evidence.",
        PatternState.TENDENCY: f"Moderate — {action_count}/{entry.total} similar observations; may change.",
        PatternState.HABIT: f"Strong tendency, not certainty — {action_count}/{entry.total} similar observations.",
        PatternState.MIXED: f"Mixed — {entry.total} similar observations show no reliable choice.",
    }[entry.state] if enemy_visible else "Unknown — no current visual contact."
    enemy_commitment, enemy_opportunity, enemy_condition = _opponent_read(
        state, enemy, visible=enemy_visible, group_combat=group_combat)
    enemy_response_summary = _opponent_response_summary(
        state, enemy, visible=enemy_visible, group_combat=group_combat)
    opponent_stability = stability_band(enemy, cfg) if enemy_visible and not group_combat else "UNKNOWN"
    ghost = not enemy.action and entry.ghost
    if ghost and enemy_visible:
        glyph = {"HEAVY": "R^", "LIGHT": "R/", "GUARD": "R]", "DASH_LIGHT": "R>",
                 "DASH_IN": "R>", "DASH_OUT": "R>"}.get(entry.top_action, "R") + "~"
    action_name = enemy.action.kind.value if enemy.action else "IDLE"
    progress = "idle"
    if enemy.action:
        phase_data = next((p for p in enemy.action.phases if p.name == phase
                           and p.start <= state.tick and (p.end is None or state.tick < p.end)), None)
        if phase_data and phase_data.end is not None:
            length = min(horizon, phase_data.end-phase_data.start)
            done = round((state.tick-phase_data.start)/(phase_data.end-phase_data.start)*length)
            progress = "[" + ">"*done + "."*(length-done) + f"] {phase_data.end-state.tick}t"
        else:
            progress = phase.lower()
    enemy_timeline = timeline_for(enemy.action, state.tick, horizon) if timeline and enemy_visible else ""
    if ghost and enemy_visible and entry.timeline_prediction and not playback:
        from .guide import make_guide_view, _responses
        view = make_guide_view(state, player.id, notebook)
        simulated = view.simulation
        response = _responses(view, entry.top_action)[0]
        if response is not None:
            action = commit_action(simulated, enemy.id, response)
            enemy_timeline = "~" + timeline_for(action, state.tick, horizon-1) + " (HABIT)" if timeline else ""
    danger = set()
    if enemy_visible and enemy.action and enemy.action.strike_tick is not None and enemy.action.strike_tick >= state.tick:
        origin = enemy.action.path[-1][1] if enemy.action.path else enemy.pos
        candidates = attack_cells(origin, enemy.action.attack_bearing, enemy.action.kind)
        for pos in candidates:
            if state.walkable is not None and pos not in state.walkable:
                continue
            line = state.line_cells(origin, pos)
            if any(state.walkable is not None and cell not in state.walkable for cell in line):
                continue
            if any(f.id not in {enemy.id, player.id} and state.occupies_tile(f)
                   and f.pos in line for f in state.fighters):
                continue
            danger.add(pos)
    if player.blood <= 0 or player.decapitated:
        status, instructions = "DEAD - DEFEAT", "Fight over. Backspace: retry | Esc: leave"
    elif player.unconscious:
        status, instructions = "UNCONSCIOUS - DEFEAT", "Alive, but cannot act. Backspace: retry | Esc: leave"
    elif state.end_reason:
        status = "VICTORY - ALIVE" if state.winner == player.id else "ALIVE - FLED" if player.fled else "DEFEAT - ALIVE"
        instructions = "Backspace: retry | V: next duel | Esc: leave"
    elif player.grounded and player.action and player.action.kind == ActionKind.RISE:
        status, instructions = "GROUNDED - RISING", "Rise is exposed and takes time; a qualifying hit interrupts it."
    elif player.grounded and player.action and player.action.kind == ActionKind.GUARD:
        status, instructions = "GROUNDED - COVERING", "Cover is committed; it does not move or attack."
    elif player.grounded:
        status, instructions = "GROUNDED - STUMBLING", "Only Cover, Crawl or Rise. Rise is exposed; Crawl is slow and collision-checked."
    elif player.action and player.action.phase_at(state.tick) == "GUARD_HOLD":
        status, instructions = "ALIVE - GUARD HELD", "Enter: hold guard | G: release guard"
    elif player.action or player.stagger_until > state.tick:
        status, instructions = "ALIVE - RESOLVING", "Committed action; time advances automatically. N: step"
    else:
        status, instructions = "ALIVE - YOUR DECISION", "Choose, preview, then Enter to confirm. Esc cancels."
    preview = preview_action(state, selected) if selected else None
    own_timeline = preview.timeline if preview and preview.valid else timeline_for(player.action, state.tick, horizon)
    acc = effective_stats(player, cfg, state.tick).accuracy
    zone_forecasts = {}
    if (zone_kind in {ActionKind.LIGHT, ActionKind.HEAVY, ActionKind.THRUST, ActionKind.DASH_SLASH}
            and enemy_visible and not group_combat):
        for zone in Zone:
            severed_hands = set(enemy.permanent_injuries.severed_hands)
            if enemy.hand_severed and not severed_hands:
                severed_hands.add(enemy.weapon_hand)
            unavailable = ((zone == Zone.EYE and enemy.permanent_injuries.eyes_damaged >= 2)
                           or (zone == Zone.HAND and
                               (not defense_snapshot(state, enemy).hand_exposed
                                or len(severed_hands) >= 2)))
            if not unavailable:
                zone_forecasts[zone] = _conditional_injury_forecast(
                    state, player, enemy, zone_kind, zone)
    zones = tuple(ZoneOption(
        zone, cfg["zones"]["difficulty"][zone.value]
        + (cfg["zones"]["guard_hand_extra"] if zone == Zone.HAND and phase == "GUARD_HOLD" else 0),
        zone != Zone.HAND or snap.hand_exposed, acc,
        ("Bilateral eye injury prevents precise eye targeting" if zone == Zone.EYE
         and player.permanent_injuries.eyes_damaged >= 2 else
         "No functioning eye remains to target" if zone == Zone.EYE and enemy
         and enemy.permanent_injuries.eyes_damaged >= 2 else ""),
        zone_forecasts.get(zone, "")) for zone in Zone)
    def action_option(label, choice):
        if (label in {"Cover", "Guard"} and player.action and player.action.kind == ActionKind.GUARD
                and player.action.phase_at(state.tick) == "GUARD_HOLD"):
            return ActionOption(label, True, "Release your held cover; the grounded fighter stays down.",
                                "conditional", 0, None)
        defense = choose_defend(state, player.id) if label == "Defend" else None
        if defense:
            choice = defense.choice
        error = legality(state, player.id, choice)
        if error or state.end_reason:
            return ActionOption(label, False, error or "Fight over.", "unavailable")
        preview = preview_action(state, choice)
        shove_effect_tick = (state.tick + phase_ticks(
            cfg["actions"]["SHOVE"]["windup"],
            effective_stats(player, cfg, state.tick).speed, cfg)
            if choice.kind == ActionKind.SHOVE else None)
        if defense and defense.arrival_tick is not None:
            relevant_tick = defense.arrival_tick
        elif shove_effect_tick is not None:
            relevant_tick = shove_effect_tick
        elif choice.kind in {ActionKind.WALK, ActionKind.RUN, ActionKind.DASH,
                           ActionKind.DISENGAGE, ActionKind.CRAWL}:
            relevant_tick = state.tick + phase_ticks(
                cfg["actions"][choice.kind.value]["per_tile"],
                effective_stats(player, cfg, state.tick).speed, cfg)
        else:
            relevant_tick = preview.strike_tick or preview.end_tick or state.tick
        duration = (f"Ready again in {preview.end_tick-state.tick} ticks."
                    if preview.end_tick is not None else "Held until you release it.")
        landing = f"Hits in {preview.strike_tick-state.tick} ticks. " if preview.strike_tick is not None else ""
        riposte_read = ("Parry riposte: this attack's windup is shortened and commits the riposte. "
                        if player.riposte and choice.kind in {ActionKind.LIGHT, ActionKind.HEAVY,
                                                             ActionKind.DISARM} else "")
        if label == "Light":
            details = (f"Quick committed cut; shorter recovery than Heavy. Forward swing sector, not a homing hit. "
                       f"{riposte_read}{landing}{duration} Costs {preview.stamina:.1f} stamina; adds {preview.fatigue:.1f} fatigue.")
        elif label == "Heavy":
            details = (f"Slower committed cut with greater contact force; exposed to faster interruption and evasion. "
                       f"{riposte_read}{landing}{duration} Costs {preview.stamina:.1f} stamina; adds {preview.fatigue:.1f} fatigue.")
        elif label == "Rise":
            details = (f"Exposed for {duration} Costs {preview.stamina:.1f} stamina; "
                       "a qualifying hit interrupts Rise and leaves you grounded.")
        elif label == "Crawl":
            details = f"One slow tile; choose a clear direction. {duration} Costs {preview.stamina:.1f} stamina; you remain grounded."
        elif label == "Cover":
            details = f"Committed guard; no movement or attack. {duration} Costs {preview.stamina:.1f} stamina."
        elif label == "Feint":
            opening = state.config["tactics"]["feint_disrupt_ticks"]
            gap = (max(abs(player.pos[0]-enemy.pos[0]), abs(player.pos[1]-enemy.pos[1]))
                   if enemy_visible and not group_combat else None)
            reach = weapon_reach(player, cfg)
            guard_phase = (enemy.action.phase_at(state.tick)
                           if enemy_visible and not group_combat and enemy.action
                           and enemy.action.kind == ActionKind.GUARD else None)
            live_read = (f" Current observed gap {gap} vs reach {reach}; "
                         f"guard phase {guard_phase or 'not currently active/visible'}. "
                         "Range and Guard phase are checked again at completion."
                         if gap is not None else " Target is unobserved; outcome is conditional.")
            details = (f"Non-damaging setup; an in-reach committed Guard is interrupted and its riposte consumed, "
                        f"creating {opening} ticks of opening. A follow-up must land before it expires; "
                        f"Light is more likely to fit than Heavy.{live_read} "
                        f"{duration} Costs {preview.stamina:.1f} stamina; ignored Feint has no effect.")
        elif label == "Shove":
            unsteady = ("Observed unsteady stance favors your contest. "
                        if enemy_visible and not group_combat
                        and opponent_stability in {"OFF_BALANCE", "STUMBLING"} else "")
            details = (f"Shove contest t{shove_effect_tick}; recover at t{preview.end_tick}. "
                       "Same-tick strikes land first. Off-balance interrupts a winding Heavy; a push interrupts "
                       f"other unresolved actions. Contact costs stability; guard braces; walls block travel. {unsteady}")
        elif label == "Defend":
            details = (f"Defend → {defense.response}. {defense.reason} "
                       f"{duration} Costs {preview.stamina:.1f} stamina; adds {preview.fatigue:.1f} fatigue.")
        elif label == "Disengage":
            details = (f"Deliberate one-tile move; destination must strictly increase distance from the chosen target "
                       f"and leave any committed threat. A clear tile that is level with or closer to the target is rejected. "
                       f"Not invulnerability; same-tick strikes resolve first. {duration} Costs {preview.stamina:.1f} stamina.")
        elif label == "Dash+Light":
            details = (f"Dash into Light reach, then commit a slash; the approach is exposed and the final sector locks. "
                       f"{landing}{duration} Costs {preview.stamina:.1f} stamina; adds {preview.fatigue:.1f} fatigue.")
        elif label == "Dash+Slash":
            details = ("Requires the hostile in current weapon reach. Slash resolves from your current tile, "
                       "then you dash exactly one legal tile away or laterally; it never closes distance. "
                       f"{landing}{duration} Costs {preview.stamina:.1f} stamina.")
        elif label == "Thrust":
            details = (f"Narrow straight reach-2 point attack; can be evaded sideways and stopped by a body or wall. "
                       f"{landing}{duration} Costs {preview.stamina:.1f} stamina. Blunt weapons cannot thrust.")
        elif label == "Disarm":
            opening = ""
            if player.riposte and enemy_visible and enemy.stagger_until > state.tick:
                fits = preview.strike_tick is not None and preview.strike_tick < enemy.stagger_until
                opening = (f"Observed parry opening until t{enemy.stagger_until}; "
                           f"this contact {'fits' if fits else 'arrives too late'}. ")
            details = (f"Requires a parry-earned riposte. {riposte_read}{opening}Clean, non-drifted hand contact must win the grip contest; "
                       f"guard may parry. {landing}{duration} Costs {preview.stamina:.1f} stamina.")
        elif label == "Adrenaline":
            rule = cfg["adrenaline"]
            reasons = adrenaline_trigger_reasons(player, cfg)
            details = (f"Once per duel; trigger: {', '.join(reasons) or 'meaningful danger/exertion'}; "
                       f"activates only on completion. Suppresses {rule['pain_suppression']:.0%} of the pain stat penalty "
                       f"for {rule['duration_ticks']} ticks; no healing. Expiry adds {rule['fatigue_debt']:g} fatigue debt. "
                       f"{duration} Costs {preview.stamina:.1f} stamina.")
        else:
            details = (f"{landing}{duration} Costs {preview.stamina:.1f} stamina; "
                       f"adds {preview.fatigue:.1f} fatigue. {preview.risk}")
        if (choice.kind in {ActionKind.LIGHT, ActionKind.HEAVY, ActionKind.THRUST,
                            ActionKind.DASH_LIGHT, ActionKind.DASH_SLASH, ActionKind.DISARM} and choice.aim):
            from .geometry import attack_lane
            lane = attack_lane(choice.aim).lower()
            details += (f" Aim commits to the {lane} blade line; an opposing intersecting "
                        "weapon path on that line clashes if contacts are within 1 tick.")
        intent = {
            ActionKind.LIGHT: "quick cut",
            ActionKind.HEAVY: "committed high-force cut",
            ActionKind.THRUST: "narrow reach-2 line attack",
            ActionKind.DASH_LIGHT: "close distance, then attack",
            ActionKind.DASH_SLASH: "slash from current reach, then retreat or move laterally",
            ActionKind.GUARD: "prepare a bounded blade response",
            ActionKind.WALK: "reposition one tile",
            ActionKind.RUN: "reposition quickly; longer route costs more",
            ActionKind.DASH: "create or close space quickly",
            ActionKind.DISENGAGE: "increase spacing if the step is timely",
            ActionKind.FEINT: "test or disrupt a committed defense",
            ActionKind.SHOVE: "contest balance and create space",
            ActionKind.DISARM: "take a weapon only from a valid opening",
            ActionKind.WAIT: "recover while accepting incoming risk",
            ActionKind.BANDAGE: "treat bleeding over time",
            ActionKind.EAT: "restore a resource after commitment",
            ActionKind.DRINK: "restore a resource after commitment",
        }.get(choice.kind, "complete the selected action")
        if defense:
            intent = f"one response: {defense.response}"
        read = ("Group response detail is withheld; hostile choices remain uncertain."
                if group_combat else
                "No current observed hostile commitment; its next choice is unknown.")
        if enemy_visible and not group_combat and enemy.action:
            if choice.kind in {ActionKind.LIGHT, ActionKind.HEAVY, ActionKind.THRUST,
                               ActionKind.DASH_LIGHT, ActionKind.DASH_SLASH, ActionKind.DISARM}:
                enemy_impact = enemy.action.strike_tick
                if (enemy_impact is not None and player.pos in danger
                        and preview.strike_tick is not None and enemy_impact <= preview.strike_tick):
                    read = f"Observed threat reaches you at t{enemy_impact} before your contact."
                elif enemy.action.end_tick is not None and preview.strike_tick is not None \
                        and enemy.action.end_tick <= preview.strike_tick:
                    read = (f"Current commitment ends at t{enemy.action.end_tick}; "
                            "the opponent's next choice remains unknown.")
                else:
                    read = "Current commitments overlap; compare the locked geometry and contact deadlines."
            elif choice.kind == ActionKind.SHOVE and enemy.action.strike_tick is not None:
                read = (f"Observed strike at t{enemy.action.strike_tick}; it resolves "
                        f"{'before' if enemy.action.strike_tick <= shove_effect_tick else 'after'} "
                        f"the shove contest at t{shove_effect_tick}. Contest outcome is not guaranteed.")
            elif choice.kind in {ActionKind.WALK, ActionKind.RUN, ActionKind.DASH,
                                 ActionKind.DISENGAGE}:
                read = "Movement is not invulnerability; the first step must beat contact and leave its locked sector."
            else:
                read = ("Current hostile commitment is visible; treatment/recovery can be interrupted if contact lands."
                        if player.pos in danger else "Current hostile commitment is visible; next opportunity remains conditional.")
        assessment = "conditional"
        assessment_reason = "Opponent is ready or the next choice is uncertain."
        if (enemy_visible and not group_combat and enemy.stagger_until > state.tick
                and preview.strike_tick is not None
                and preview.strike_tick < enemy.stagger_until):
            assessment = "favorable"
            assessment_reason = (f"Observed control loss lasts until t{enemy.stagger_until}; "
                                 f"contact at t{preview.strike_tick} fits before recovery.")
        elif (enemy_visible and not group_combat and enemy.stagger_until > state.tick
              and preview.strike_tick is not None):
            assessment_reason = (f"Observed stagger ends at t{enemy.stagger_until}, before this contact "
                                 f"at t{preview.strike_tick}; the opening may expire first.")
        elif (enemy_visible and not group_combat and enemy.action and enemy.action.end_tick is not None
              and preview.strike_tick is not None
              and enemy.action.end_tick <= preview.strike_tick):
            assessment_reason = (f"Visible commitment ends at t{enemy.action.end_tick}, before your "
                                 f"contact at t{preview.strike_tick}; they may choose again first.")
        elif (enemy_visible and not group_combat and enemy.action and enemy.action.strike_tick is not None
              and enemy.action.strike_tick <= max(
                  relevant_tick,
                  state.tick if shove_effect_tick is not None else preview.end_tick or state.tick,
                  state.tick if shove_effect_tick is not None else enemy.action.end_tick or state.tick,
                  enemy.stagger_until)):
            threatened_position = player.pos
            if choice.direction:
                for step_tick, destination in zip(
                        (state.tick + index * phase_ticks(
                            cfg["actions"][choice.kind.value]["per_tile"],
                            effective_stats(player, cfg, state.tick).speed, cfg)
                         for index in range(1, choice.tiles + 1)),
                        movement_path(state, player, choice)):
                    if step_tick < enemy.action.strike_tick:
                        threatened_position = destination
            if threatened_position in danger:
                assessment = "exposed"
                assessment_reason = (f"Visible attack can reach you at t{enemy.action.strike_tick}; "
                                     "this choice does not leave its threatened cells in time.")
        if defense and defense.safe:
            assessment = "favorable"
            assessment_reason = defense.reason
        elif defense and defense.response == "brace":
            assessment = "exposed"
            assessment_reason = defense.reason
        if defense:
            deadline = re.search(r"earliest next choice no sooner than t(\d+)", enemy_opportunity)
            if enemy_visible and not group_combat and enemy.action:
                commitment = (f"{enemy.action.kind.value.replace('_', ' ').title()} impact "
                              f"t{enemy.action.strike_tick}; " if enemy.action.strike_tick is not None else "")
                response_timing = (f"Opponent timing: {commitment}next choice no sooner than t{deadline.group(1)}."
                                   if deadline else f"Opponent timing: {enemy_opportunity}")
            else:
                response_timing = "Opponent timing: no visible committed threat; next choice unknown."
            details = (f"{assessment.upper()} — Defend → {defense.response}. {defense.reason} "
                       f"Costs {preview.stamina:.1f} STA; adds {preview.fatigue:.1f} fatigue. "
                       f"{duration} {response_timing}")
        elif choice.kind == ActionKind.SHOVE:
            if enemy_visible and not group_combat and enemy.action and enemy.action.strike_tick is not None:
                details += f" Observed strike at t{enemy.action.strike_tick}; {read.lower()}"
            else:
                details += " NPC's next choice is unknown."
        else:
            details = (f"{assessment.upper()} — {assessment_reason} Intent: {intent}. {read} "
                       f"Opponent timing: {enemy_opportunity} {details}")
        if plausible_response_read:
            details += (f" Visible-cue scenarios: {plausible_response_read} "
                        + _response_coverage_note(choice.kind))
        return ActionOption(label, True, details, assessment, preview.stamina,
                            max(0, relevant_tick - state.tick))
    if player.grounded:
        crawl_choice = next((Choice(ActionKind.CRAWL, direction=direction, tiles=1)
                             for direction in ((0, 1), (1, 0), (0, -1), (-1, 0),
                                               (1, 1), (-1, 1), (1, -1), (-1, -1))
                             if not legality(state, player.id,
                                             Choice(ActionKind.CRAWL, direction=direction, tiles=1))),
                            Choice(ActionKind.CRAWL, direction=(0, 1), tiles=1))
        grounded_actions = [("Rise", Choice(ActionKind.RISE)),
                            ("Crawl", crawl_choice), ("Cover", Choice(ActionKind.GUARD))]
        if (enemy_visible and max(abs(enemy.pos[0]-player.pos[0]),
                                  abs(enemy.pos[1]-player.pos[1])) <= 1):
            grounded_actions.append(("Ground attack", Choice(ActionKind.LIGHT, Zone.TORSO)))
        actions = () if playback else tuple(action_option(label, choice) for label, choice in grounded_actions)
    else:
        disengage_choice = next((Choice(ActionKind.DISENGAGE, direction=direction)
                                  for direction in ((0, 1), (1, 0), (0, -1), (-1, 0),
                                                    (1, 1), (-1, 1), (1, -1), (-1, -1))
                                  if not legality(state, player.id,
                                                  Choice(ActionKind.DISENGAGE, direction=direction))),
                                 Choice(ActionKind.DISENGAGE, direction=(0, 1)))
        dash_light_choice = Choice(ActionKind.DASH_LIGHT, Zone.TORSO, direction=(0, 1), tiles=1)
        dash_slash_choice = Choice(ActionKind.DASH_SLASH, Zone.TORSO, direction=(0, 1), tiles=1,
                                   target_id=enemy.id if enemy_visible else None)
        if enemy_visible:
            dx = (enemy.pos[0] > player.pos[0]) - (enemy.pos[0] < player.pos[0])
            dy = (enemy.pos[1] > player.pos[1]) - (enemy.pos[1] < player.pos[1])
            gap_to_enemy = max(abs(enemy.pos[0]-player.pos[0]), abs(enemy.pos[1]-player.pos[1]))
            max_tiles = cfg["actions"]["DASH_LIGHT"]["max_tiles"]
            preferred_tiles = min(max_tiles, max(1, gap_to_enemy-1))
            directions = ((dx, dy), (dx, 0), (0, dy), (dx, -dy), (-dy, dx),
                          (dy, dx), (-dx, dy), (-dx, -dy), (0, 1), (1, 0),
                          (0, -1), (-1, 0))
            dash_light_choice = next((choice for tiles in (preferred_tiles, *range(max_tiles, 0, -1))
                                      for direction in directions if direction != (0, 0)
                                      for choice in (Choice(ActionKind.DASH_LIGHT, Zone.TORSO,
                                                            direction=direction, tiles=tiles,
                                                            target_id=enemy.id),)
                                       if not legality(state, player.id, choice)), dash_light_choice)
            dash_slash_choice = next((choice for direction in (
                (0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1))
                for choice in (Choice(ActionKind.DASH_SLASH, Zone.TORSO, direction, 1,
                                      target_id=enemy.id),)
                if not legality(state, player.id, choice)), dash_slash_choice)
        actions = () if playback else tuple(action_option(label, choice)
                        for label, choice in (("Light", Choice(ActionKind.LIGHT, Zone.TORSO)),
                                              ("Heavy", Choice(ActionKind.HEAVY, Zone.TORSO)),
                                              ("Defend", choose_defend(state, player.id).choice),
                                              ("Dash", Choice(ActionKind.DASH, direction=(0, 1))),
                                              ("Move", Choice(ActionKind.WALK, direction=(0, 1))),
                                               ("Run", Choice(ActionKind.RUN, direction=(0, 1))),
                                               ("Wait", Choice(ActionKind.WAIT)),
                                             ("Dash+Light", dash_light_choice),
                                             ("Dash+Slash", dash_slash_choice),
                                           ("Bandage", Choice(ActionKind.BANDAGE)),
                                           ("Eat", Choice(ActionKind.EAT)),
                                           ("Drink", Choice(ActionKind.DRINK)),
                                           ("Pickup", Choice(ActionKind.PICKUP,
                                                              item_id=state.ground_weapons[0].weapon.item_id
                                                              if state.ground_weapons else None)),
                                           ("Feint", Choice(ActionKind.FEINT)),
                                           ("Shove", Choice(ActionKind.SHOVE)),
                                           ("Disengage", disengage_choice),
                                              ("Thrust", Choice(ActionKind.THRUST, Zone.TORSO)),
                                          ("Disarm", Choice(ActionKind.DISARM, Zone.HAND)),
                                          ("Adrenaline", Choice(ActionKind.ADRENALINE))))
    names = {fighter.id: ("You" if fighter.id == player.id else fighter.name
                          if len(state.fighters) <= 2 or state.can_see(player, fighter)
                          else "Unknown hostile") for fighter in state.fighters}
    render_events = state.event_log[-64:] if playback else state.event_log
    if len(state.fighters) > 2:
        render_events = [event for event in render_events
                         if player.id in event.witnesses or event.actor == player.id]
    lines = tuple(line for event in render_events if (line := event_line(event, names)))
    why_by_tick = {}
    for event in render_events:
        for tag in event.cause_tags:
            if tag in CAUSE_LINES:
                line = f"{names.get(event.actor, 'Fight')}: {CAUSE_LINES[tag]}"
                group = why_by_tick.setdefault(event.tick, [])
                if line not in group:
                    group.append(line)
    why = tuple(" | ".join(lines) for lines in why_by_tick.values())[-cfg["ui"]["why_history"]:]
    popup_types = {EventType.PARRY: "Parried!", EventType.BLOCK: "Blocked!", EventType.WHIFF: "Too far!", EventType.CLASH: "Blade clash!", EventType.GUARD_BREAK: "Guard broken!",
                      EventType.INTERRUPT: "Interrupted!", EventType.STAGGER: "Staggered!", EventType.SEVER: "Structural loss!",
                      EventType.HIT: "+pain", EventType.WOUND: "Bleeding", EventType.STATUS: "Changed", EventType.INJURY_RESPONSE: "Injury reflex",
                    EventType.STABILITY: "Off balance!", EventType.FALL: "FALLEN!", EventType.RISE: "Rising!",
                    EventType.FEINT: "Feint", EventType.SHOVE: "Shove", EventType.DISARM: "Disarm",
                     EventType.ADRENALINE: "Adrenaline", EventType.WEAPON_STRESS: "Weapon wear",
                     EventType.WEAPON_DROP: "Weapon dropped", EventType.WEAPON_BREAK: "Weapon broken",
                     EventType.WEAPON_PICKUP: "Weapon picked up", EventType.EYE_INJURY: "Eye injured",
                     EventType.DECAPITATION: "Fatal head strike", EventType.INCAPACITATED: "Incapacitated"}
    status_at = {(e.tick, e.actor): e.deltas['status'].replace('_', ' ') for e in render_events
                 if e.type == EventType.STATUS and 'status' in e.deltas}
    wounded_at = {(e.tick, e.target) for e in render_events if e.type == EventType.HIT}
    fatal_at = {(e.tick, e.target) for e in render_events if e.type == EventType.DECAPITATION}
    def popup_label(event):
        if event.type == EventType.WHIFF and "OUTSIDE_LOCKED_ATTACK_GEOMETRY" in event.cause_tags:
            return "Missed line"
        if event.type == EventType.WOUND:
            if group_combat and event.target != player.id:
                return "Bleeding wound"
            return f"Bleeding {event.zone.value.lower()}"
        if event.type == EventType.HIT:
            label = f"{event.severity.value} {event.zone.value.lower()}" if event.zone else event.severity.value
            if event.deltas.get("impact_applied", 0) > 0:
                label += " · impact"
            structure = event.deltas.get("structure")
            if structure:
                label += " · " + structure.replace("_", " ").lower()
            wound = cfg['wounds'].get(event.zone.value, {}).get(event.severity.value, {}) if event.zone else {}
            if wound.get('stamina_loss'):
                return label+f" · STA −{wound['stamina_loss']:g}"
            if (event.tick, event.target) in status_at:
                return label+" · "+status_at[(event.tick, event.target)]
            if wound.get('bleed'):
                return label+f" · bleed {wound['bleed']:g}/t"
            return label
        if event.type == EventType.INJURY_RESPONSE:
            response = event.deltas.get("response", "").replace("_", " ").lower()
            return {"recoil": "Back 1 tile", "braced": "Reflex blocked", "refractory": "Reflex recovery",
                    "flinch": "Back 1 tile", "no reflex": "No reflex"}.get(response, "Injury reflex")
        if event.type == EventType.STATUS:
            if "MOVEMENT_BLOCKED" in event.cause_tags:
                return f"Blocked by {names.get(event.target, 'fighter')}"
            return event.deltas['status'].replace('_', ' ').title()
        if event.type == EventType.STABILITY:
            return "Stability lost"
        if event.type == EventType.FALL:
            return "FALLEN!"
        if event.type == EventType.RISE:
            return "Rising"
        if event.type == EventType.FEINT:
            return "Guard disrupted" if event.deltas.get("outcome") == "GUARD_DISRUPTED" else "Feint ignored"
        if event.type == EventType.SHOVE:
            return event.deltas.get("outcome", "Shove").replace("_", " ").title()
        if event.type == EventType.DISARM:
            return event.deltas.get("outcome", "Disarm").replace("_", " ").title()
        if event.type == EventType.ADRENALINE:
            return event.deltas.get("outcome", "Adrenaline").replace("_", " ").title()
        if event.type == EventType.WEAPON_STRESS:
            return f"Weapon {event.deltas['condition_after']:.0f}%"
        if event.type == EventType.WEAPON_DROP:
            return f"Dropped {event.deltas.get('name', 'weapon')}"
        if event.type == EventType.WEAPON_BREAK:
            return "Weapon broke!"
        if event.type == EventType.WEAPON_PICKUP:
            return f"Picked up {event.deltas.get('name', 'weapon')}"
        if event.type == EventType.EYE_INJURY:
            return "Permanent eye injury"
        if event.type == EventType.SEVER and event.deltas.get("ear_lost"):
            side = event.deltas.get("side")
            return f"{side.title() + ' ' if side else ''}ear severed"
        if event.type == EventType.DECAPITATION:
            return "DEATH — decapitation"
        if event.type == EventType.INCAPACITATED:
            return "Unconscious"
        return popup_types[event.type]
    popups = tuple(Popup(state.fighter(event.actor if event.type in {EventType.WHIFF, EventType.STATUS, EventType.INJURY_RESPONSE}
                           else event.target or event.actor).pos
                         if (event.actor if event.type in {EventType.WHIFF, EventType.STATUS, EventType.INJURY_RESPONSE}
                             else event.target or event.actor) in {fighter.id for fighter in state.fighters}
                         else enemy.pos,
                         popup_label(event),
                           event.type in {EventType.PARRY, EventType.GUARD_BREAK, EventType.SEVER,
                                           EventType.WEAPON_BREAK, EventType.DECAPITATION}
                           or event.type == EventType.HIT and event.deltas.get("impact_applied", 0) > 0
                            or event.type == EventType.INJURY_RESPONSE
                            and event.deltas.get("response") in {"RECOIL", "FLINCH"},
                           event.tick,
                           (0 if event.type != EventType.HIT else
                            (1 if state.fighter(event.target).pos[0] > state.fighter(event.actor).pos[0] else
                             -1 if state.fighter(event.target).pos[0] < state.fighter(event.actor).pos[0] else 0),
                            1 if event.type == EventType.HIT and state.fighter(event.target).pos[1] > state.fighter(event.actor).pos[1] else
                            -1 if event.type == EventType.HIT and state.fighter(event.target).pos[1] < state.fighter(event.actor).pos[1] else 0))
                      for event in render_events if event.type in popup_types
                     and (event.type == EventType.DECAPITATION
                          or (event.tick, event.actor) not in fatal_at
                          and (event.tick, event.target) not in fatal_at)
                      and (event.type != EventType.STABILITY or "loss" in event.deltas)
                   and (event.type != EventType.STATUS or 'status' in event.deltas)
                   and (event.type != EventType.STATUS or (event.tick, event.actor) not in wounded_at)
                   and state.tick - event.tick < cfg["ui"]["popup_turns"] * cfg["time"]["ticks_per_turn"])
    known = (not group_combat and
             dict(notebook.exchanges).get(enemy.id, 0) >= cfg["notebook"]["reveal_exchanges"])
    sheet = tuple((name, f"{getattr(enemy, attr):g}" if known else "? (unlearned)")
                  for name, attr in (("Strength", "strength"), ("Speed", "speed"), ("Accuracy", "accuracy")))
    named_entries = dict(notebook.named).get(enemy.id, ())
    notebook_rows = tuple(line for entry in named_entries for line in (
                          f"{entry.key.anger_band.lower()} + {entry.key.gap_band.lower()} + "
                           f"you {entry.key.opp_state.lower()} -> {(entry.top_action or '?').lower()} "
                           f"x{dict(entry.counts).get(entry.top_action, 0)} [{entry.state.value}]",
                           *((entry.habit_break_hint,) if entry.habit_break_hint else ())))
    pools = tuple((label, value, maximum) for label, value, maximum in (
        ("BLD", player.blood, cfg["defaults"]["pool_max"]), ("STA", player.stamina, max_stamina(player, cfg)),
        ("FAT", player.fatigue, cfg["defaults"]["pool_max"]), ("PAIN", player.pain, cfg["defaults"]["pool_max"]),
        ("FEAR", fear(player, cfg), cfg["defaults"]["pool_max"]),
        ("ANGER", player.anger, cfg["defaults"]["pool_max"]), ("MORALE", player.morale, cfg["defaults"]["pool_max"])))
    player_emotion_rows = (
        "Modes " + " ".join(
            f"{meter}:{player.behavior_modes[meter].lower()}"
            for meter in ("pain", "fear", "anger")),
        f"Fear {fear(player, cfg):.0f}/100; anger {player.anger:.0f}/100; pain {player.pain:.0f}/100 "
        f"(effective {pain_burden(player, cfg):.0f}).",
        f"Anger tolerance {player.personality.anger_tolerance if player.personality else cfg['defaults']['anger_tolerance']:.0%}; "
        f"pain tolerance {player.personality.pain_tolerance if player.personality else cfg['defaults']['pain_tolerance']:.0%}. "
        "Fear lowers Speed/Accuracy; anger raises Speed/Strength but lowers Accuracy and footing stability.",
    )
    def injury_rows(fighter):
        rows = []
        active_statuses = {status.name for status in fighter.statuses if status.active(state.tick)}
        for wound in fighter.wounds:
            data = cfg["wounds"][wound.zone.value].get(wound.severity.value, {})
            effect = data.get("status")
            if effect not in active_statuses:
                effect = None
            if data.get("disarm") and fighter.disarmed:
                effect = "disarmed"
            if fighter.unconscious and wound.zone == Zone.HEAD:
                effect = "unconscious"
            consequence = effect.replace("_", " ") if effect else "pain"
            if wound.vital_outcome == "HEART_FATAL":
                consequence = "fatal qualified heart injury"
            elif wound.vital_outcome in {"HEART_BLEED", "LUNG_BLEED", "LIVER_BLEED"}:
                consequence = wound.vital_outcome.replace("_", " ").lower()
            bleed = wound.bleed_per_turn * (1-cfg["blood"]["bandage_reduction"] if wound.bandaged else 1)
            bleed_text = (f"continuing bleed {bleed:g}/turn "
                          f"({'partially bandaged' if wound.bandaged else 'untreated'})"
                          if bleed > 0 else "no continuing bleed")
            loss_text = (f"; one-off blood loss {wound.initial_blood_loss:g}"
                         if wound.initial_blood_loss > 0 else "")
            site = (f"{wound.side.lower()} " if wound.side else "") + wound.site.replace("_", " ").lower()
            depth = wound.depth.replace("_", " ").lower()
            structure = wound.structure.replace("_", " ").lower()
            tissue = depth if depth == structure else f"{depth} tissue ({structure})"
            numeric_function = []
            for key, label in (("strength_mult", "Strength"), ("speed_mult", "Speed"),
                               ("accuracy_mult", "Accuracy")):
                if data.get(key, 1) != 1:
                    numeric_function.append(f"{label} ×{data[key]:g}")
            if data.get("parry_penalty", 0):
                numeric_function.append(f"parry +{data['parry_penalty']:g}")
            if effect and data.get("duration"):
                active = next((status for status in fighter.statuses
                               if status.name == effect and status.active(state.tick)), None)
                remaining = (max(0, active.until_tick-state.tick)
                             if active and active.until_tick is not None else data["duration"])
                numeric_function.append(f"{remaining:g} ticks remaining")
            function_numbers = (f" ({'; '.join(numeric_function)})" if numeric_function else "")
            rows.append(f"{wound.zone.value.title()} — {site}: {wound.severity.value.lower()}, "
                        f"{tissue} {wound.damage_type.lower()}; {consequence}{function_numbers}; "
                        f"{bleed_text}{loss_text}")
        for side in fighter.permanent_injuries.severed_hands:
            if side == fighter.weapon_hand:
                rows.append(f"Permanent injury: {side.lower()} weapon hand severed; cannot hold or pick up a weapon.")
            else:
                rows.append(f"Permanent injury: {side.lower()} supporting hand severed; one-handed weapon actions remain usable.")
        for side in fighter.permanent_injuries.severed_ears:
            rows.append(f"Permanent injury: {side.lower()} ear severed; side-specific hearing is not modeled.")
        if fighter.permanent_injuries.hand_severed and not fighter.permanent_injuries.severed_hands:
            rows.append("Permanent injury: weapon hand severed (legacy side unknown); cannot hold or pick up a weapon.")
        if fighter.permanent_injuries.eyes_damaged:
            vision = ("bilateral impairment prevents acquiring precise eye targets"
                      if fighter.permanent_injuries.eyes_damaged == 2
                      else "one eye impaired; depth/long-range sight reduced")
            vision_range = (cfg["injury"]["bilateral_vision_range"]
                            if fighter.permanent_injuries.eyes_damaged == 2
                            else cfg["injury"]["unilateral_vision_range"])
            vision += (f"; sight limited to {vision_range} tile(s)"
                       if vision_range != 1 else "; sight limited to adjacent tiles")
            rows.append(f"Permanent injury: {fighter.permanent_injuries.eyes_damaged} eye(s) impaired; {vision}; Accuracy unchanged.")
        if fighter.decapitated:
            rows.append("Fatal injury: decapitation.")
        rows.append(weapon_status(fighter))
        return tuple(rows)
    player_injury_rows, enemy_injury_rows = injury_rows(player), injury_rows(enemy)
    wounds = tuple((w.zone.value.lower(), w.bleed_per_turn * (1-cfg["blood"]["bandage_reduction"] if w.bandaged else 1)) for w in player.wounds)
    eff = effective_stats(player, cfg, state.tick)
    tolerance = player.personality.pain_tolerance if player.personality else cfg["defaults"]["pain_tolerance"]
    anger_tolerance = player.personality.anger_tolerance if player.personality else cfg["defaults"]["anger_tolerance"]
    susceptibility = (player.personality.fear_gain_susceptibility if player.personality
                      else cfg["ronin"]["fear_gain_susceptibility"])
    condition_rows = [
        "MENTAL METERS (current / 100)",
        f"Pain raw {player.pain:.1f}; effective pain burden {pain_burden(player, cfg):.1f}; mode {player.behavior_modes['pain']}; enter 50/100, exit 40/90.",
        f"Fear {fear(player, cfg):.1f}; mode {player.behavior_modes['fear']}; susceptibility {susceptibility:.2f}x gains; enter 50/100, exit 40/90.",
        f"Anger {player.anger:.1f}; mode {player.behavior_modes['anger']}; tolerance {anger_tolerance:.0%}; enter 50/100, exit 40/90.",
        f"Morale {player.morale:.1f}; nervousness {player.nervousness:.1f}; yield gate morale ≤25 only at critical pain/fear with no viable exit.",
        f"Pain tolerance {tolerance:.0%}; composure {player.personality.composure if player.personality else 0.5:.2f}; stress composure {player.personality.stress_composure if player.personality else 0.5:.2f}.",
        "PHYSICAL METERS AND EFFECTIVE CAPABILITIES",
        f"Blood {player.blood:.1f}/100; stamina {player.stamina:.1f}/{max_stamina(player, cfg):.1f}; fatigue {player.fatigue:.1f}/100; stability {player.stability:.1f}/100.",
    ]
    for label, raw, current in (("Strength", player.strength, eff.strength),
                                ("Speed", player.speed, eff.speed),
                                ("Accuracy", player.accuracy, eff.accuracy)):
        condition_rows.append(f"{label} {raw:.1f} base → {current:.1f} effective ({current-raw:+.1f}).")
    condition_rows.append("BODY SITES (wound force is post-armor; unknown legacy values shown as ?)")
    for zone_name, sites in cfg["anatomy_sites"].items():
        condition_rows.append(zone_name + " —")
        for site in sites:
            site_wounds = [wound for wound in player.wounds
                           if wound.site.upper() == site.upper()
                           or wound.site.upper() == zone_name and site == zone_name]
            if not site_wounds:
                condition_rows.append(f"{site.replace('_', ' ').title()}: unaffected; wounds 0; force 0; pain +0; bleed 0/turn.")
                continue
            wound_facts = []
            for wound in site_wounds:
                bleed = wound.bleed_per_turn * (1-cfg["blood"]["bandage_reduction"] if wound.bandaged else 1)
                force = "?" if wound.net_force is None else f"{wound.net_force:.1f}"
                pain_added = "?" if wound.pain_added is None else f"{wound.pain_added:.1f}"
                function = cfg["wounds"][wound.zone.value].get(wound.severity.value, {})
                modifier = ", ".join(f"{name} ×{function[key]:g}"
                                      for key, name in (("strength_mult", "STR"),
                                                        ("speed_mult", "SPD"),
                                                        ("accuracy_mult", "ACC"))
                                      if key in function and function[key] != 1)
                wound_facts.append(f"{wound.severity.value} force {force}; pain +{pain_added}; bleed {bleed:.2f}/turn"
                                   + (f"; {modifier}" if modifier else ""))
            condition_rows.append(f"{site.replace('_', ' ').title()}: AFFECTED ({len(site_wounds)} wound(s)); "
                                  + " | ".join(wound_facts))
    condition_rows.append(f"Vision: {player.permanent_injuries.eyes_damaged}/2 eyes impaired; "
                          f"range cap {cfg['injury']['bilateral_vision_range'] if player.permanent_injuries.eyes_damaged >= 2 else cfg['injury']['unilateral_vision_range'] if player.permanent_injuries.eyes_damaged == 1 else cfg['group']['vision_range']} tiles.")
    fresh = copy(player)
    fresh.fatigue = fresh.pain = fresh.anger = fresh.nervousness = 0
    starting_fear = (fresh.personality.starting_fear if fresh.personality
                     else cfg["defaults"]["starting_fear"])
    fresh.fear_meter = starting_fear
    fresh.blood, fresh.morale = cfg["blood"]["start"], cfg["morale"]["start"]
    fresh.statuses, fresh.exhausted = [], False
    fresh.riposte = False
    healthy = effective_stats(fresh, cfg, state.tick)
    def readout(fighter, stats):
        light_ticks = phase_ticks(cfg['actions']['LIGHT']['windup'], stats.speed, cfg)
        heavy_ticks = phase_ticks(cfg['actions']['HEAVY']['windup'], stats.speed, cfg)
        if fighter.riposte:
            light_ticks = max(cfg['time']['min_phase_ticks'], math.ceil(light_ticks*cfg['guard']['riposte_windup_mult']))
            heavy_ticks = max(cfg['time']['min_phase_ticks'], math.ceil(heavy_ticks*cfg['guard']['riposte_windup_mult']))
        return {"Speed": stats.speed, "Strength": stats.strength, "Accuracy": stats.accuracy,
                "Endurance": stats.endurance,
                "light_ticks": light_ticks, "heavy_ticks": heavy_ticks,
                "thrust_ticks": phase_ticks(cfg["actions"]["THRUST"]["windup"], stats.speed, cfg),
                "guard_ticks": phase_ticks(cfg["actions"]["GUARD"]["windup"], stats.speed, cfg),
                "parry_window": max(cfg["guard"]["parry_min_window"], round(cfg["guard"]["parry_speed_coef"]*stats.speed
                                      + cfg["guard"]["parry_acc_coef"]*stats.accuracy)
                                      - sum(s.parry_penalty for s in fighter.statuses if s.active(state.tick))),
                 "light_power": stats.strength*(cfg["gear"]["unarmed_power"] if fighter.disarmed else cfg["actions"]["LIGHT"]["power"])
                                + weapon_edge(fighter, cfg)}
    own = readout(player, eff)
    own.update({"fresh_"+key: value for key, value in readout(fresh, healthy).items()})
    total_bleed_rate = min(cfg["blood"]["total_bleed_cap_per_turn"], sum(value for _, value in wounds))
    own.update({"light_cost": stamina_cost(player, cfg["actions"]["LIGHT"]["stamina"], cfg),
                "dash_cost": stamina_cost(player, cfg["actions"]["DASH"]["stamina"], cfg),
                "exhausted": float(player.exhausted), "bandages": player.bandages,
                "food": player.food, "water": player.water,
                "disarmed": float(player.disarmed),
                "unbandaged": float(any(w.bleed_per_turn > 0 and not w.bandaged for w in player.wounds)),
                "no_dash": float(any(s.no_dash for s in player.statuses if s.active(state.tick))),
                "riposte": float(player.riposte), "bleed": total_bleed_rate})
    observed = {}
    for event in reversed(state.event_log):
        if event.actor == enemy.id and event.type == EventType.ACTION_START and "speed" in event.deltas:
            observed = {name: event.deltas[key] for name, key in
                        (("Speed", "speed"), ("Strength", "strength"), ("Accuracy", "accuracy"))}
            if event.deltas.get("strike_tick") is not None:
                observed["arrival"] = event.deltas["strike_tick"]-event.tick
                observed["is_light"] = float(event.deltas["kind"] in {"LIGHT", "DASH_LIGHT"})
                observed["plain_light"] = float(event.deltas["kind"] == "LIGHT")
            observed["tick"] = event.tick
            break
    if observed:
        for event in reversed(state.event_log):
            if event.tick < observed['tick']:
                break
            if event.actor == enemy.id and event.type == EventType.HIT and 'strength' in event.deltas:
                observed['Strength'] = event.deltas['strength']
                observed['Accuracy'] = event.deltas.get('accuracy', observed['Accuracy'])
                break
    alerts = []
    from .stability import balance_rating
    player_balance = balance_rating(player, cfg, state.tick)
    player_stability_state = stability_band(player, cfg)
    enemy_stability_state = stability_band(enemy, cfg)
    if player.grounded:
        alerts.insert(0, "GROUNDED: only Cover, Crawl or Rise; rising is exposed and interruptible")
    elif player_stability_state != "STABLE":
        alerts.insert(0, f"{player_stability_state.replace('_', ' ')}: stability {player.stability:.0f}/100; heavy overreach/fast turns risk a fall")
    if own['light_ticks'] != own['fresh_light_ticks']:
        alerts.append(f"Next light {own['fresh_light_ticks']:g} → {own['light_ticks']:g}t (windup)")
    if own['fresh_light_power'] >= cfg['wound_net']['cut'][0] > own['light_power']:
        alerts.append("Light power below cut threshold before armor")
    if player.exhausted:
        alerts.insert(0, "Exhausted: no heavy/dash; speed and strength reduced")
    elif player.stamina < own['light_cost']:
        alerts.insert(0, f"Light needs {own['light_cost']:.1f} stamina; you have {player.stamina:.1f}")
    if own['bleed']:
        alerts.append(f"Bleeding {own['bleed']:g} blood per turn (10 ticks) before exertion; bandage stops part, not all")
    effects = []
    for zone in Zone:
        parts = []
        cut = cfg['wounds'][zone.value].get('CUT', {})
        for field, label in (("strength_mult", "force"), ("speed_mult", "speed")):
            if field in cut:
                parts.append(f"{label} −{(1-cut[field])*100:g}%")
        effects.append((zone.value, "Cut: " + (", ".join(parts) if parts else "bleeding") + "; only if the hit gets through armor."))
    def status_rows(fighter):
        rows = []
        for status in fighter.statuses:
            if not status.active(state.tick):
                continue
            changes = [f"{label} ×{getattr(status, field):g}" for field, label in
                       (("strength_mult", "STR"), ("speed_mult", "SPD"))
                       if getattr(status, field) != 1]
            if status.no_dash:
                changes.append("no dash")
            rows.append(status.name.replace('_', ' ') + (": " + ", ".join(changes) if changes else ""))
        return tuple(rows)
    from .progression import xp_to_next
    gap = (max(abs(player.pos[0]-enemy.pos[0]), abs(player.pos[1]-enemy.pos[1]))
           - weapon_reach(player, cfg)) if enemy_visible else -1
    rule = cfg["adrenaline"]
    active = player.adrenaline_until_tick is not None and state.tick < player.adrenaline_until_tick
    triggers = adrenaline_trigger_reasons(player, cfg)
    if active:
        adrenaline_status = (f"ACTIVE until t{player.adrenaline_until_tick}; pain penalty suppressed "
                             f"{rule['pain_suppression']:.0%}; fatigue debt {player.adrenaline_debt_due:g} at expiry")
    elif player.adrenaline_used:
        adrenaline_status = ("USED; expiry debt applied" if not player.adrenaline_debt_pending
                             else "USED; fatigue debt pending")
    elif triggers:
        adrenaline_status = "READY — trigger: " + ", ".join(triggers)
    else:
        adrenaline_status = (f"LOCKED — blood ≤{rule['trigger_blood_max']:g}, pain ≥{rule['trigger_pain_min']:g}, "
                             f"fatigue ≥{rule['trigger_fatigue_min']:g}, or stability ≤{rule['trigger_stability_max']:g}")
    visible_actors = [fighter for fighter in state.fighters
                      if fighter.id == player.id or state.can_see(player, fighter)]
    visible_ids = {fighter.id for fighter in visible_actors}
    hostile_visible = sorted((fighter for fighter in visible_actors
                              if fighter.id != player.id and fighter.team_id != player.team_id
                              and state.combat_active(fighter)),
                             key=lambda fighter: (max(abs(player.pos[0]-fighter.pos[0]),
                                                      abs(player.pos[1]-fighter.pos[1])), fighter.id))
    def posture(fighter):
        if not state.combat_active(fighter):
            return "incapacitated" if not fighter.fled else "fled"
        action = fighter.action
        delay = cfg["ai"]["reaction_delay_default"]
        if action and fighter.id != player.id and state.tick-action.start_tick < delay:
            return "unclear movement"
        phase_name = action.phase_at(state.tick) if action else "IDLE"
        return {"WINDUP": "winding up", "ACTIVE": "striking", "RECOVERY": "recovering",
                "GUARD_RAISE": "raising guard", "GUARD_HOLD": "guarding"}.get(
                    phase_name, "grounded" if fighter.grounded else "committed" if action else "ready")
    actor_summaries = tuple(ActorSummary(
        fighter.id, fighter.name, fighter.pos,
        "@" if fighter.id == player.id else str(index) if fighter.team_id != player.team_id
        else chr(ord("A") + (index % 26)),
        fighter.team_id != player.team_id,
        max(abs(player.pos[0]-fighter.pos[0]), abs(player.pos[1]-fighter.pos[1])),
        posture(fighter),
        (fighter.action.kind.value if fighter.action else "IDLE")
        if fighter.id == player.id or not fighter.action or state.tick-fighter.action.start_tick >= (
            cfg["ai"]["reaction_delay_default"])
        else "UNKNOWN",
        fighter.grounded,
        fighter.action.target_id if fighter.action and fighter.action.target_id in visible_ids
        and (fighter.id == player.id or state.tick-fighter.action.start_tick >= (
            cfg["ai"]["reaction_delay_default"]))
        else None,
        fighter.id == player.id)
        for index, fighter in enumerate(visible_actors) if state.occupies_tile(fighter))
    target_options = tuple(next(summary for summary in actor_summaries if summary.actor_id == fighter.id)
                           for fighter in hostile_visible)
    visible_threats = target_options[:cfg["group"]["threat_summary_count"]]
    enemy_render_stats = effective_stats(
        enemy, cfg, state.tick, pain_tolerance=cfg["defaults"]["pain_tolerance"])
    return RenderModel(tuple(sorted(cfg["ui"].items())), cfg["ui"]["columns"], cfg["ui"]["bar_segments"], state.tick, gap,
                         enemy.name if enemy_visible else "No visible hostile", enemy.level if enemy_visible and not group_combat else 0,
                         key.anger_band if enemy_visible and not group_combat else "UNKNOWN",
                         enemy.stamina/max_stamina(enemy, cfg) if enemy_visible and not group_combat else 0,
                         enemy.fatigue/cfg["defaults"]["pool_max"] if enemy_visible and not group_combat else 0,
                         action_name if enemy_visible and not group_combat else "UNKNOWN", progress if enemy_visible and not group_combat else "not visible",
                         snap.hand_exposed if enemy_visible and not group_combat else False,
                         sheet if enemy_visible else tuple((name, "? (not visible)") for name, _ in sheet),
                        glyph if enemy_visible else "?", enemy.pos if enemy_visible else player.pos, player.pos,
                        frozenset(state.walkable or ()), frozenset(state.exits),
                        cfg["arena"]["width"], cfg["arena"]["height"], frozenset(danger),
                         own_timeline if timeline else "", enemy_timeline if not group_combat else "",
                         ghost if not group_combat else False, pools,
                        (eff.speed / player.speed - 1) * cfg["defaults"]["pool_max"], wounds, status,
                        player.level, player.xp, xp_to_next(player.level, cfg),
                        instructions, preview, zones, actions, lines[-cfg["ui"]["log_lines"]:], lines,
                          why, popups, notebook_rows if not group_combat else (),
                          notebook.npc_traits(enemy.id) if enemy_visible and not group_combat else (), advice, guide_mode,
                          tuple((name, f"{getattr(eff, attr):.1f} (base {getattr(player, attr):g})")
                                for name, attr in (("Strength", "strength"), ("Speed", "speed"),
                                                   ("Accuracy", "accuracy"), ("Endurance", "endurance")))
                          + (("Balance", f"{player_balance:.1f}/100; {player_stability_state.lower()}"),),
                          (tuple(f"{name}: You {getattr(eff, attr):.1f} | {enemy.name} {getattr(enemy_render_stats, attr):.1f}"
                                for name, attr in (("Strength", "strength"), ("Speed", "speed"), ("Accuracy", "accuracy")))
                          if not group_combat else tuple(f"{name}: ? (group profile hidden)"
                                                         for name in ("Strength", "Speed", "Accuracy"))),
                          max(0, enemy.action.strike_tick-state.tick) if enemy_visible and not group_combat and enemy.action and enemy.action.strike_tick is not None and enemy.action.strike_tick >= state.tick
                           else max(0, enemy.action.end_tick-state.tick) if enemy_visible and not group_combat and enemy.action and enemy.action.end_tick is not None else None,
                           enemy_pools=(("BLD", enemy.blood, cfg["defaults"]["pool_max"]),
                                        ("STA", enemy.stamina, max_stamina(enemy, cfg)),
                                         ("FAT", enemy.fatigue, cfg["defaults"]["pool_max"]))
                            if enemy_visible and not group_combat else (),
                           enemy_wounds=tuple((w.zone.value.lower(), w.bleed_per_turn *
                                              (1-cfg["blood"]["bandage_reduction"] if w.bandaged else 1))
                                              for w in enemy.wounds) if enemy_visible and not group_combat else (),
                          player_action=player.action.kind.value if player.action else "IDLE",
                          player_remaining=max(0, player.action.strike_tick-state.tick)
                          if player.action and player.action.strike_tick is not None and player.action.strike_tick >= state.tick else None,
                          player_aim=player.action.aim.value if player.action and player.action.aim else "",
                            enemy_aim=enemy.action.aim.value if enemy_visible and not group_combat
                            and enemy.action and enemy.action.aim else "",
                          player_penalties=(("Fatigue", eff.fatigue_penalty), ("Pain", eff.pain_penalty), ("Blood loss", eff.blood_penalty)),
                            own_readout=tuple(own.items()), enemy_readout=tuple(readout(enemy, enemy_render_stats).items()) if known and enemy_visible else (),
                            enemy_observed=tuple(observed.items()) if enemy_visible and not group_combat else (),
                            combat_rules=(("deep", cfg['wound_net']['deep']),
                                          ("aim_clean_base", cfg['accuracy']['aim_dispersion']['clean_base']),
                                          ("aim_per_accuracy", cfg['accuracy']['aim_dispersion']['per_accuracy']),
                                          ("aim_difficulty_penalty", cfg['accuracy']['aim_dispersion']['difficulty_penalty']),
                                        ("parry_light", cfg['guard']['parry_str_ratio']['light']), ("parry_heavy", cfg['guard']['parry_str_ratio']['heavy']),
                                         ("break_heavy", cfg['guard']['break_str_ratio']),
                                         ("bandage_reduction", cfg['blood']['bandage_reduction']), ("cut", cfg['wound_net']['cut'][0])),
                           functional_alerts=tuple(alerts), target_effects=tuple(effects),
                             player_status_rows=(f"{player_stability_state.replace('_', ' ').title()}: "
                                                 f"stability {player.stability:.0f}/100; balance {player_balance:.0f}/100",
                                                 *status_rows(player),
                                                 weapon_status(player)),
                               enemy_status_rows=(f"{enemy_stability_state.replace('_', ' ').title()}"
                                                  + (" — grounded" if enemy.grounded else ""), *status_rows(enemy),
                                                  weapon_status(enemy)) if enemy_visible and not group_combat else (),
                             enemy_armor=tuple((zone.value, piece.value) for zone, piece in enemy.equipment.armor.items())
                             if enemy_visible and not group_combat else (),
                            movement_timing=tuple((kind, phase_ticks(cfg["actions"][kind]["per_tile"], eff.speed, cfg))
                                                    for kind in ("DASH", "DASH_LIGHT", "DASH_SLASH", "RUN", "WALK", "CRAWL", "DISENGAGE")),
                           player_emotion_rows=player_emotion_rows, enemy_tell=enemy_tell,
                            enemy_confidence=(enemy_confidence if not group_combat
                                              else "Unknown — group profiles and unobserved choices stay private."),
                           player_injury_rows=player_injury_rows,
                              enemy_injury_rows=enemy_injury_rows if enemy_visible and not group_combat else (),
                            player_stability=player.stability, player_balance=player_balance,
                            player_stability_state=player_stability_state, player_grounded=player.grounded,
                               enemy_stability_state=enemy_stability_state if enemy_visible and not group_combat else "UNKNOWN",
                               enemy_grounded=enemy.grounded if enemy_visible and not group_combat else False,
                              adrenaline_status=adrenaline_status,
                              ground_weapons=tuple((row.weapon.item_id, row.weapon.name, row.pos,
                                                   row.weapon.condition, row.weapon.material)
                                                  for row in state.ground_weapons),
                              player_weapon_status=weapon_status(player),
                                 enemy_weapon_status=weapon_status(enemy) if enemy_visible and not group_combat else "Weapon: unknown — group detail hidden",
                               player_hand_severed=player.hand_severed,
                               pickup_range=cfg["weapon_integrity"]["pickup_range"],
                            actors=actor_summaries, target_options=target_options,
                            threats=visible_threats,
                             threat_overflow=max(0, len(target_options)-len(visible_threats)),
                             enemy_visible=enemy_visible,
                              group_combat=group_combat,
                              enemy_commitment=enemy_commitment,
                              enemy_opportunity=enemy_opportunity,
                                enemy_response_summary=enemy_response_summary,
                                enemy_condition=enemy_condition,
                            player_behavior_modes=tuple((meter, player.behavior_modes[meter])
                                                            for meter in ("pain", "fear", "anger")),
                                player_condition_rows=tuple(condition_rows),
                                zone_attack_kind=zone_kind.value if zone_kind else "")


def bar(fraction, segments):
    filled = max(0, min(segments, round(fraction * segments)))
    return "[" + "#" * filled + "-" * (segments-filled) + "]"


def map_lines(model):
    result = []
    actors = {actor.pos: actor for actor in model.actors if actor.pos != model.player_pos}
    for y in range(model.height):
        cells = []
        for x in range(model.width):
            pos = (x, y)
            value = "#" if pos not in model.floor else ">" if pos in model.exits else "!" if pos in model.danger else "."
            if pos == model.player_pos:
                value = "@v" if model.player_grounded else "@"
            elif pos in actors:
                actor = actors[pos]
                value = actor.glyph + ("_" if actor.grounded else "")
            elif pos == model.enemy_pos and not model.actors:
                value = "R_" if model.enemy_grounded else model.enemy_glyph
            cells.append(value.ljust(3))
        result.append("".join(cells).rstrip())
    return tuple(result)


def format_ascii(model):
    """Pure 40-column ASCII layout; long text wraps rather than hiding the cause."""
    if model.group_combat:
        enemy_rows = ["VISIBLE THREATS"]
        enemy_rows.extend(f"{actor.name} · ~{actor.distance} "
                          f"{'tile' if actor.distance == 1 else 'tiles'} · {actor.posture}"
                          for actor in model.threats)
        if model.threat_overflow:
            enemy_rows.append(f"+{model.threat_overflow} additional visible hostiles")
        if not model.threats:
            enemy_rows.append("No visible hostiles; off-screen state unknown.")
    else:
        enemy_rows = ([f"{model.enemy_name} Lv{model.enemy_level} {model.enemy_mood} [?]",
                       f"Stamina {model.enemy_stamina*100:.0f}% | Fatigue {model.enemy_fatigue*100:.0f}%",
                       f"ACT {model.enemy_action} {model.enemy_progress}",
                       f"hand:{'OPEN' if model.hand_exposed else 'closed'} | gap {model.gap} | t{model.tick}"]
                      if model.enemy_visible else ["No visible hostile; next action and range unknown.", f"t{model.tick}"])
    rows = [*enemy_rows,
             f"COMMITMENT: {model.enemy_commitment}",
             f"OPPORTUNITY: {model.enemy_opportunity}",
             f"CONDITION: {model.enemy_condition}",
             *model.traits[:2], "-" * model.columns, *map_lines(model), "-" * model.columns]
    rows.append("Green: you | Red: Ronin | > exit" +
                (" | w: ground weapon; More > Pickup" if model.ground_weapons else ""))
    if model.danger:
        rows.extend(["Amber tiles: possible sword reach.", "Not an area hit: strike targets you."])
    if model.player_timeline:
        rows.extend(["Timeline: next 20 ticks (10 = 1 turn)", f"You   {model.player_timeline}", f"Ronin {model.enemy_timeline}",
                     "l/h=windup L/H=hit -=recover .=idle"])
    rows.append(model.player_state)
    rows.append(f"Stability {model.player_stability:.0f}/100 · Balance {model.player_balance:.0f}/100 · {model.player_stability_state}")
    rows.append(f"LV {model.player_level} XP {model.player_xp:.0f}/{model.player_xp_next}")
    pools = {label: (value, maximum) for label, value, maximum in model.pools}
    blood, maximum = pools["BLD"]
    rows.append(f"Blood {bar(blood/maximum, model.segments)} {blood:.0f}/{maximum:.0f}")
    rows.append(" | ".join(f"{name} {pools[label][0]:.0f}/{pools[label][1]:.0f}" for label, name in (("STA", "Stamina"), ("FAT", "Fatigue"))))
    rows.append(" ".join(f"{label}{bar(pools[label][0]/pools[label][1], model.segments//2)}" for label in ("PAIN", "ANGER", "MORALE")))
    rows.extend(model.player_emotion_rows[:1])
    rows.extend(model.enemy_tell[:1])
    if model.threats:
        rows.append("Visible threats: " + ", ".join(
            f"{actor.name} {actor.distance}t {actor.posture}" for actor in model.threats))
    if model.threat_overflow:
        rows.append(f"+{model.threat_overflow} more visible hostile(s)")
    severe = dict(model.presentation)["penalty_severe_pct"]
    rows.append(f"Speed change {model.speed_penalty:+.0f}% {'!!' if model.speed_penalty <= -severe else '!' if model.speed_penalty < 0 else ''}")
    rows.extend(model.player_injury_rows or ("No localized wounds.",))
    rows.extend(model.log or ("Choose an action. Time waits for you.",))
    if model.why:
        rows.append("WHY: " + model.why[-1].split(" | ")[0])
    if model.advice:
        advice = model.advice
        rows.append(f"GUIDE {model.guide_mode.upper()} ({advice.confidence.value.lower()})")
        if advice.action:
            rows.append(f"{advice.action.kind.value} -> {advice.zone.value if advice.zone else '-'}")
        rows.extend(advice.why if model.guide_mode == "full" else advice.why[:1])
        if model.guide_mode == "full":
            rows.extend(f"Risk: {risk}" for risk in advice.risk)
            if advice.alternatives:
                alternative = advice.alternatives[0].action
                rows.append(f"Also: {alternative.kind.value} -> {alternative.aim.value if alternative.aim else '-'}")
    if model.preview:
        p = model.preview
        rows.extend([f"> {p.kind} -> {p.aim or '-'}", f"Cost {p.stamina:.1f} STA +{p.fatigue:.1f} FAT",
                     f"Lands t{p.strike_tick if p.strike_tick is not None else '-'} / enemy t{p.enemy_strike_tick if p.enemy_strike_tick is not None else '-'}",
                     p.risk if p.valid else p.error, "Enter or repeat selection: confirm"])
    if model.player_grounded:
        rows.extend(["[Rise][Crawl][Cover]", model.instructions])
    else:
         rows.extend(["[Light][Heavy][Thrust][Defend]", "[Dash][Move][Run][Dash+Light]",
                     "[Feint][Shove][Disarm][Disengage]", "[Wait][Bandage][More][Sheet][Help]",
                     model.instructions])
    return tuple(part for row in rows for part in (textwrap.wrap(row, width=model.columns,
                 replace_whitespace=False, drop_whitespace=False) or [""]))
