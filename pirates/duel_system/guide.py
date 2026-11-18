"""M8: observation-only tutor, using bounded cloned simulations, never live AI.

The projection is a whitelist. It does not clone a hidden enemy Fighter, RNG,
personality or event log and then hope downstream consumers ignore the secrets.
"""
from copy import deepcopy
from dataclasses import dataclass, replace
from enum import Enum

from .ai import PublicKey, distance, movement_choice
from .config import load_config
from .engine import Choice, advance_one_tick, commit_action, legality
from .model import ActionKind, CombatState, Fighter, Zone
from .notebook import NotebookView, PatternState
from .stats import effective_stats, max_stamina, stamina_cost, weapon_reach


class Confidence(str, Enum):
    CERTAIN = "CERTAIN"
    LIKELY = "LIKELY"
    GUESSING = "GUESSING"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class GuideView:
    simulation: CombatState
    player_id: str
    enemy_id: str
    public_key: PublicKey
    exact_stats: bool
    jitter: bool


@dataclass(frozen=True)
class Outcome:
    response: Choice | None
    score: float
    cause_tags: tuple[str, ...]


@dataclass(frozen=True)
class Candidate:
    action: Choice
    score: float
    outcomes: tuple[Outcome, ...]


@dataclass(frozen=True)
class GuideAdvice:
    action: Choice | None
    zone: Zone | None
    why: tuple[str, ...]
    risk: tuple[str, ...]
    confidence: Confidence
    alternatives: tuple[Candidate, ...]
    predicted: str | None
    score: float
    outcomes: tuple[Outcome, ...]
    simulations: int


def _public_key(state, enemy, observer=None):
    """Public enemy-POV buckets; no FullKey construction or AI perception calls."""
    cfg, rule = state.config, state.config["ai"]
    player = state.fighter(observer) if observer is not None else state.other(enemy)
    if player is None:
        return PublicKey("FAR", "CALM", "LOW", "UNKNOWN", "UNSEEN")
    gap = distance(enemy.pos, player.pos) - weapon_reach(enemy, cfg)
    limits = rule["gap_bands"]
    gap_band = "ADJ" if gap <= limits[0] else "NEAR" if gap <= limits[1] else "MID" if gap <= limits[2] else "FAR"
    limits = rule["anger_bands"]
    anger = "CALM" if enemy.anger < limits[0] else "WARM" if enemy.anger < limits[1] else "ANGRY" if enemy.anger < limits[2] else "RAGE"
    pct = enemy.stamina / max_stamina(enemy, cfg) * cfg["defaults"]["pool_max"]
    vigor = ("LOW" if pct < rule["vigor_low_stamina"] or enemy.fatigue > rule["vigor_low_fatigue"] or enemy.exhausted else
             "HIGH" if pct >= rule["vigor_high_stamina"] and enemy.fatigue < rule["vigor_high_fatigue"] else "MID")
    phase = player.action.phase_at(state.tick) if player.action else "IDLE"
    last = player.last_action_cat
    if player.action and state.tick - player.action.start_tick < rule["reaction_delay_default"]:
        opponent = "UNSEEN"
        last = player.previous_action_cat
    elif state.tick < player.stagger_until:
        opponent = "STAGGERED"
    elif phase == "WINDUP":
        opponent = "WINDUP_H" if player.action.kind == ActionKind.HEAVY else "WINDUP_L"
    elif phase in {"GUARD_RAISE", "GUARD_HOLD"}:
        opponent = "GUARDING"
    elif phase == "RECOVERY":
        opponent = "WHIFFED" if player.last_strike_whiffed else "RECOVERING"
    else:
        opponent = "EXHAUSTED" if player.exhausted else "IDLE"
    return PublicKey(gap_band, anger, vigor, last, opponent)


def make_guide_view(state, player_id, notebook):
    if not isinstance(notebook, NotebookView):
        raise TypeError("Guide projection requires an immutable public NotebookView")
    player = next(f for f in state.fighters if f.id == player_id)
    enemy = state.other(player)
    # Mechanical rules are public. AI coefficients/personality and RNG are not copied.
    cfg = load_config()
    for group in ("time", "defaults", "stamina", "fatigue", "stats", "blood", "anger", "morale",
                  "fear", "level", "zones", "wound_net", "wounds", "injury", "stability", "pain",
                  "guard", "gear", "actions", "guide"):
        cfg[group] = deepcopy(state.config[group])
    cfg["accuracy"]["jitter"] = 0
    for group in ("base", "weights"):
        cfg["ronin"][group] = dict.fromkeys(cfg["ronin"][group], 0)
    own = deepcopy(player)
    own.personality, own.notebook = None, None
    known = dict(notebook.exchanges).get(enemy.id, 0) >= state.config["notebook"]["reveal_exchanges"]
    public = _public_key(state, enemy)
    anger_estimate = {"CALM": 12.5, "WARM": 42.5, "ANGRY": 72.5, "RAGE": 92.5}[public.anger_band]
    default = cfg["defaults"]
    stats = {key: getattr(enemy, key) if known else default[key]
             for key in ("strength", "speed", "accuracy", "stamina_stat", "endurance")}
    # Enemy STA/FAT, mood, equipment, wounds and committed phases are visible.
    estimated = Fighter(enemy.id, enemy.name, enemy.level, **stats,
                        equipment=deepcopy(enemy.equipment), stamina=0,
                        fatigue=enemy.fatigue, anger=anger_estimate, pos=enemy.pos,
                        blood=enemy.blood, pain=default["pain"], morale=default["morale"])
    # Preserve the visible stamina percentage without discovering the hidden stat.
    visible_fraction = enemy.stamina / max_stamina(enemy, state.config)
    estimated.stamina = visible_fraction * max_stamina(estimated, cfg)
    for name in ("wounds", "statuses", "last_action_cat", "previous_action_cat", "exhausted",
                 "last_strike_whiffed", "unconscious", "yielded", "fled", "disarmed", "hand_severed",
                 "stagger_until", "riposte"):
        setattr(estimated, name, deepcopy(getattr(enemy, name)))
    if enemy.action:
        # Only committed telegraph scheduling is exact; locked combat stats are estimated.
        action = enemy.action
        locked = effective_stats(estimated, cfg, state.tick)
        estimated.action = replace(action, phases=deepcopy(action.phases), path=deepcopy(action.path),
                                   locked_stats=locked, cost_paid=0,
                                   parry_window=max(cfg["guard"]["parry_min_window"], round(
                                       cfg["guard"]["parry_speed_coef"] * locked.speed
                                       + cfg["guard"]["parry_acc_coef"] * locked.accuracy)))
    sample = CombatState([own, estimated], rng_seed=cfg["guide"]["simulation_seed"], config=cfg,
                         tick=state.tick, walkable=deepcopy(state.walkable), exits=deepcopy(state.exits),
                         end_reason=state.end_reason, winner=state.winner)
    return GuideView(sample, own.id, estimated.id, _public_key(sample, estimated),
                     known, state.config["accuracy"]["jitter"] > 0)


def legal_choices(state, fighter_id, *, affordable=True):
    fighter = next(f for f in state.fighters if f.id == fighter_id)
    choices = []
    for kind in (ActionKind.GUARD, ActionKind.LIGHT, ActionKind.HEAVY, ActionKind.DASH_LIGHT,
                 ActionKind.THRUST, ActionKind.DISARM, ActionKind.FEINT, ActionKind.SHOVE,
                 ActionKind.DASH, ActionKind.WALK, ActionKind.RUN, ActionKind.CRAWL, ActionKind.DISENGAGE,
                 ActionKind.RISE, ActionKind.ADRENALINE, ActionKind.WAIT,
                 ActionKind.BANDAGE, ActionKind.EAT, ActionKind.DRINK, ActionKind.PICKUP):
        if kind in {ActionKind.LIGHT, ActionKind.HEAVY, ActionKind.THRUST}:
            options = [Choice(kind, zone) for zone in Zone]
        elif kind == ActionKind.DISARM:
            options = [Choice(kind, Zone.HAND)]
        elif kind == ActionKind.PICKUP:
            pickup_range = state.config["weapon_integrity"]["pickup_range"]
            options = ([Choice(kind, item_id=item.weapon.item_id) for item in state.ground_weapons
                        if item.weapon.condition > 0
                        and max(abs(fighter.pos[0]-item.pos[0]), abs(fighter.pos[1]-item.pos[1])) <= pickup_range]
                       if not fighter.hand_severed else [])
        elif kind in {ActionKind.WALK, ActionKind.RUN, ActionKind.DASH, ActionKind.DASH_LIGHT,
                      ActionKind.CRAWL, ActionKind.DISENGAGE}:
            aims = list(Zone) if kind == ActionKind.DASH_LIGHT else [None]
            options = [Choice(kind, zone, (dx, dy), tiles) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                       if dx or dy for tiles in range(1, state.config["actions"][kind.value]["max_tiles"] + 1) for zone in aims]
        else:
            options = [Choice(kind)]
        for choice in options:
            if legality(state, fighter_id, choice):
                continue
            base = state.config["actions"][kind.value]["stamina"] * (choice.tiles if kind == ActionKind.RUN else 1)
            if not affordable or stamina_cost(fighter, base, state.config) <= fighter.stamina:
                choices.append(choice)
    return tuple(choices)


def _responses(view, predicted):
    state = view.simulation
    enemy = next(f for f in state.fighters if f.id == view.enemy_id)
    if enemy.action or not enemy.can_act(state.tick):
        return (None,)
    if predicted:
        if predicted == "DISENGAGE":
            choice = movement_choice(state, enemy, ActionKind.DISENGAGE, toward=False)
            if choice:
                return (choice,)
        if predicted in {"DASH_OUT", "WALK_OUT", "RUN_OUT", "DASH_IN", "WALK_IN", "RUN_IN"}:
            choice = movement_choice(state, enemy, ActionKind(predicted.split("_")[0]), toward=predicted.endswith("_IN"))
            if choice:
                return (choice,)
        else:
            kind = ActionKind(predicted)
            if kind == ActionKind.DASH_LIGHT:
                choice = movement_choice(state, enemy, kind, toward=True, aim=Zone.TORSO)
            else:
                aim = (Zone.HAND if kind == ActionKind.DISARM else Zone.TORSO
                       if kind in {ActionKind.LIGHT, ActionKind.HEAVY, ActionKind.THRUST} else None)
                choice = Choice(kind, aim)
            if choice and not legality(state, enemy.id, choice):
                return (choice,)
    # Plausible responses are all attack zones and stances plus direct in/out movement.
    result = [choice for choice in legal_choices(state, enemy.id, affordable=False)
              if choice.kind not in {ActionKind.WALK, ActionKind.RUN, ActionKind.DASH,
                                     ActionKind.DASH_LIGHT, ActionKind.DISENGAGE}]
    for kind in (ActionKind.WALK, ActionKind.RUN, ActionKind.DASH, ActionKind.DASH_LIGHT,
                 ActionKind.DISENGAGE):
        for toward in (True, False):
            aims = list(Zone) if kind == ActionKind.DASH_LIGHT else [None]
            for aim in aims:
                choice = movement_choice(state, enemy, kind, toward=toward, aim=aim)
                if choice is not None:
                    result.append(choice)
    return tuple(dict.fromkeys(result)) or (None,)


def _prediction(view, notebook):
    enemy = next(f for f in view.simulation.fighters if f.id == view.enemy_id)
    if enemy.action:
        return enemy.action.kind.value, Confidence.CERTAIN
    entry = notebook.entry("", view.public_key, npc_id=view.enemy_id)
    if entry.state in {PatternState.HABIT, PatternState.TENDENCY}:
        return entry.top_action, Confidence.LIKELY
    if entry.state == PatternState.TENTATIVE:
        return entry.top_action, Confidence.GUESSING
    return None, Confidence.UNKNOWN


def evaluate(before, after, player_id):
    own_before = next(f for f in before.fighters if f.id == player_id)
    own_after = next(f for f in after.fighters if f.id == player_id)
    enemy_before, enemy_after = before.other(own_before), after.other(own_after)
    weight = before.config["guide"]["weights"]
    maimed_before = enemy_before.hand_severed or any(s.no_dash for s in enemy_before.statuses)
    maimed_after = enemy_after.hand_severed or any(s.no_dash for s in enemy_after.statuses)
    values = {"enemy_blood": enemy_before.blood - enemy_after.blood,
              "enemy_pain": enemy_after.pain - enemy_before.pain,
              "enemy_morale": enemy_before.morale - enemy_after.morale,
              "enemy_fatigue": enemy_after.fatigue - enemy_before.fatigue,
              "enemy_maimed": int(maimed_after and not maimed_before),
              "enemy_out": int((enemy_after.unconscious or enemy_after.blood <= 0) and
                               not (enemy_before.unconscious or enemy_before.blood <= 0)),
              "own_blood": own_before.blood - own_after.blood,
              "own_fatigue": own_after.fatigue - own_before.fatigue,
              "own_stamina": max(0, own_before.stamina - own_after.stamina),
              "own_stagger": int(own_after.stagger_until > after.tick)}
    return sum(weight[key] * value for key, value in values.items())


def _check(view, notebook):
    if not isinstance(view, GuideView) or not isinstance(notebook, NotebookView):
        raise TypeError("Guide accepts only GuideView and public NotebookView")


def rollout_scores(view, notebook):
    _check(view, notebook)
    before = view.simulation
    predicted, confidence = _prediction(view, notebook)
    responses = _responses(view, predicted)
    results = []
    for action in legal_choices(before, view.player_id):
        outcomes = []
        for response in responses:
            sim = before.clone()
            commit_action(sim, view.player_id, action)
            if response is not None:
                commit_action(sim, view.enemy_id, response)
            deadline = before.tick + before.config["guide"]["horizon"]
            while sim.tick < deadline and not sim.end_reason:
                advance_one_tick(sim)
                if all(f.action is None and f.can_act(sim.tick) for f in sim.fighters):
                    break
            tags = tuple(dict.fromkeys(tag for event in sim.event_log for tag in event.cause_tags))
            outcomes.append(Outcome(response, evaluate(before, sim, view.player_id), tags))
        results.append(Candidate(action, min(outcome.score for outcome in outcomes), tuple(outcomes)))
    return tuple(results)


CAUSE_LINES = {
    "LIGHT_INTERRUPTS_HEAVY": "Light lands during his heavy windup and interrupts it.",
    "HEAVY_BEATS_GUARD": "Heavy pressures his guard or breaks it.",
    "PARRY_WINDOW": "A guarded strike reaches the parry window.",
    "LIGHT_CATCHES_RECOVERY": "Light can punish exposed recovery.",
    "GUARD_BYPASSED": "Hand and leg strikes bypass the guard line.",
    "WALK_DEFENSELESS": "Walking in reach leaves you defenseless.",
    "ZONE_DRIFT": "Accuracy or exposure forces the strike to a safer fallback zone.",
    "OUT_OF_REACH": "An attack misses because its target is out of reach.",
    "OVEREXTENDED_HEAVY_WHIFF": "An out-of-reach heavy overextends and costs stability.",
    "SHARP_FAST_TURN": "A sharp turn during successful fast movement costs stability.",
    "STABILITY_ZERO": "Stability reached zero; the fighter fell and is grounded.",
    "FEINT_DISRUPTED_GUARD": "Feint drains a committed guard and consumes its stored riposte, but only if still in reach.",
    "FEINT_IGNORED": "Feint found no active guard in reach; it dealt no damage.",
    "SHOVE_SUCCESS": "The strength-and-balance contest succeeded; the target moved one clear tile and lost stability.",
    "SHOVE_RESISTED": "The defender's strength, balance, or guard brace won the shove contest.",
    "SHOVE_BLOCKED": "The shove had no effect because its forced destination was blocked.",
    "SHOVE_OUT_OF_RANGE": "The target moved out of close contact before the committed shove resolved.",
    "RIPOSTE_DISARM": "A clean hand contact won the disarm grip contest after a parry setup.",
    "DISARM_RESISTED": "The hand contact drifted or the defender won the grip contest; no disarm occurred.",
    "ADRENALINE_ACTIVATED": "A once-per-duel adrenaline surge suppresses only part of the pain stat penalty; fatigue debt is due at expiry.",
    "ADRENALINE_EXPIRED": "The temporary pain suppression ended and its one-time fatigue debt was applied.",
    "ADRENALINE_INTERRUPTED": "The committed surge was interrupted; its once-per-duel use is spent, but it never activated.",
}


def guide(view, notebook, mode="full"):
    _check(view, notebook)
    if mode not in {"off", "hints", "full"}:
        raise ValueError("Guide mode must be off, hints or full")
    if mode == "off":
        return None
    predicted, confidence = _prediction(view, notebook)
    candidates = rollout_scores(view, notebook)
    if not candidates:
        return GuideAdvice(None, None, ("An action is already resolving, or the fight is over.",), (),
                           confidence, (), predicted, 0, (), 0)
    best = max(candidates, key=lambda item: item.score)
    tolerance = abs(best.score) * view.simulation.config["guide"]["alternative_fraction"]
    alternatives = tuple(item for item in candidates if item is not best and item.score >= best.score - tolerance)
    worst = min(best.outcomes, key=lambda item: item.score)
    why = []
    if confidence == Confidence.UNKNOWN:
        why.append("Unknown. Keep distance and watch.")
    elif confidence == Confidence.CERTAIN:
        why.append(f"His {predicted.lower()} is a visible commitment, not a prediction.")
    else:
        why.append(f"Notebook predicts {predicted.lower()} ({confidence.value.lower()}).")
    why.extend(CAUSE_LINES[tag] for tag in worst.cause_tags if tag in CAUSE_LINES)
    if len(why) == 1:
        why.append("Compared the resource and injury outcomes of legal responses.")
    risk = []
    if not view.exact_stats:
        risk.append("Opponent stats are estimates until three observed exchanges.")
    if view.jitter:
        risk.append("Strike accuracy has seeded jitter; forecasts are not guarantees.")
    player = next(f for f in view.simulation.fighters if f.id == view.player_id)
    acc = effective_stats(player, view.simulation.config, view.simulation.tick).accuracy
    difficulty = view.simulation.config["zones"]["difficulty"]["HAND"]
    if acc < difficulty:
        risk.append(f"Hand needs {difficulty:g} ACC; you have {acc:g}: may drift to arm. Torso is safer.")
    return GuideAdvice(best.action if mode == "full" else None,
                       best.action.aim if mode == "full" else None,
                       tuple(why), tuple(risk), confidence, alternatives if mode == "full" else (),
                       predicted, best.score, best.outcomes, sum(len(item.outcomes) for item in candidates))
