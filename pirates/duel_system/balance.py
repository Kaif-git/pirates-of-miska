"""Real seeded Section21.3 fights. No injected outcomes or matchup win weights.

All policies read the pre-commit state; the punisher uses visible commitments,
own stats and geometry, never Ronin's private weights or next same-tick choice.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import random
import statistics

from .ai import choose_action, distance, observe, record_ai_decision, record_ai_outcomes
from .config import load_config
from .engine import Choice, advance_one_tick, commit_action, legality, movement_path, release_guard
from .guide import legal_choices
from .mental import fear_decision, initialize_fight
from .model import ActionKind, Equipment, EventType, Fighter, Personality, Weapon, Zone
from .stats import effective_stats, max_stamina, phase_ticks, weapon_reach


MATCHUPS = {
    "punisher": ("ScriptedPunisherBot", "Ronin"),
    "spam_light": ("SpamLightBot", "Ronin"),
    "spam_heavy": ("SpamHeavyBot", "Ronin"),
    "naive_guard": ("NaiveGuardBot", "Ronin"),
    "random": ("RandomBot", "Ronin"),
    "kite": ("KiteBot", "Ronin"),
    "mirror": ("ScriptedPunisherBot", "ScriptedPunisherBot"),
    "level_gap": ("ScriptedPunisherBot", "Ronin"),
}


def fingerprint(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def move(state, fighter, kind, toward, *, aim=None):
    other = state.other(fighter)
    choices = []
    before = distance(fighter.pos, other.pos)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == dy == 0:
                continue
            for tiles in range(1, state.config["actions"][kind.value]["max_tiles"] + 1):
                choice = Choice(kind, aim, (dx, dy), tiles)
                if legality(state, fighter.id, choice):
                    continue
                path = movement_path(state, fighter, choice)
                # Kiting/positioning is not a deliberate map-exit escape shortcut.
                if any(pos in state.exits for pos in path):
                    continue
                after = distance(path[-1], other.pos)
                if toward and after >= before or not toward and after <= before:
                    continue
                deviation = abs(dx * (other.pos[1]-fighter.pos[1]) - dy * (other.pos[0]-fighter.pos[0]))
                choices.append(((after if toward else -after, deviation, tiles, dx, dy), choice))
    return min(choices, key=lambda item: item[0])[1] if choices else None


def _fallback(state, fighter, preferred):
    if preferred is not None and not legality(state, fighter.id, preferred):
        return preferred
    return Choice(ActionKind.WAIT)


def choose_bot(state, fighter_id, bot, rng):
    fighter = next(f for f in state.fighters if f.id == fighter_id)
    other = state.other(fighter)
    gap = distance(fighter.pos, other.pos) - weapon_reach(fighter, state.config)
    cfg = state.config
    if bot == "Ronin":
        return choose_action(state, fighter.id).choice
    if bot == "RandomBot":
        return rng.choice(legal_choices(state, fighter.id, affordable=False))
    if bot in {"SpamLightBot", "SpamHeavyBot"}:
        if gap > 0:
            return _fallback(state, fighter, move(state, fighter, ActionKind.WALK, True))
        return _fallback(state, fighter, Choice(ActionKind.LIGHT if bot == "SpamLightBot" else ActionKind.HEAVY, Zone.TORSO))
    if bot == "NaiveGuardBot":
        return _fallback(state, fighter, Choice(ActionKind.GUARD))
    if bot == "KiteBot":
        if other.wounds:
            away = move(state, fighter, ActionKind.DASH, False) or move(state, fighter, ActionKind.WALK, False)
            if away:
                return away
            if gap > 0:
                return Choice(ActionKind.WAIT)
        if gap > 0:
            return _fallback(state, fighter, move(state, fighter, ActionKind.WALK, True))
        return Choice(ActionKind.LIGHT, Zone.TORSO)
    if bot != "ScriptedPunisherBot":
        raise ValueError(f"Unknown bot {bot}")
    own = effective_stats(fighter, cfg, state.tick)
    table = cfg["actions"]
    light = phase_ticks(table["LIGHT"]["windup"], own.speed, cfg)
    heavy = phase_ticks(table["HEAVY"]["windup"], own.speed, cfg)
    action = other.action
    reaction_delay = cfg["ai"]["reaction_delay_default"]
    visible_action = action if action and state.tick - action.start_tick >= reaction_delay else None
    phase = visible_action.phase_at(state.tick) if visible_action else "UNSEEN" if action else "IDLE"
    exposed = phase in {"WINDUP", "RECOVERY", "ACTIVE", "GUARD_RAISE", "GUARD_HOLD"} or other.stagger_until > state.tick
    # Against an idle target, the lower leg is a safe, useful slowdown target;
    # against a committed/exposed target, punish the open hand when accuracy allows.
    aim = (Zone.HAND if own.accuracy >= cfg["zones"]["difficulty"]["HAND"] else Zone.TORSO) if exposed else Zone.LEG
    if gap >= cfg["ai"]["rest_gap"]:
        if fighter.wounds and fighter.bandages > 0 and not legality(state, fighter.id, Choice(ActionKind.BANDAGE)):
            return Choice(ActionKind.BANDAGE)
        if fighter.fatigue > cfg["balance"]["rest_fatigue"] or fighter.stamina < max_stamina(fighter, cfg)*cfg["balance"]["rest_stamina_pct"]:
            return Choice(ActionKind.WAIT)
    if visible_action and visible_action.strike_tick is not None and visible_action.strike_tick >= state.tick:
        remaining = visible_action.strike_tick - state.tick
        if gap <= 0 or phase in {"DASH", "WINDUP", "ACTIVE"}:
            if visible_action.kind == ActionKind.HEAVY and light < remaining:
                return Choice(ActionKind.LIGHT, aim)
            raise_ticks = phase_ticks(cfg["guard"]["raise"], own.speed, cfg)
            estimated_str = cfg["fixtures"]["ronin"]["strength"] * (1+cfg["level"]["stat_per_level"]*(other.level-1))
            can_parry = own.strength >= cfg["guard"]["parry_str_ratio"]["heavy" if visible_action.kind == ActionKind.HEAVY else "light"] * estimated_str
            if visible_action.kind == ActionKind.LIGHT and raise_ticks <= remaining:
                return _fallback(state, fighter, Choice(ActionKind.GUARD))
            if can_parry and raise_ticks <= remaining:
                return _fallback(state, fighter, Choice(ActionKind.GUARD))
            dash_ticks = phase_ticks(table["DASH"]["per_tile"], own.speed, cfg)
            if dash_ticks < remaining:
                dodge = move(state, fighter, ActionKind.DASH, False)
                if dodge:
                    return dodge
    if gap <= 0:
        if phase in {"GUARD_RAISE", "GUARD_HOLD"}:
            return _fallback(state, fighter, Choice(ActionKind.HEAVY, Zone.LEG))
        remaining = visible_action.end_tick-state.tick if visible_action and visible_action.end_tick is not None else other.stagger_until-state.tick
        if remaining >= heavy:
            heavy_aim = (Zone.HAND if exposed and own.accuracy >=
                         cfg["zones"]["difficulty"]["HAND"] + cfg["zones"]["heavy_acc_mod"] else Zone.LEG)
            return _fallback(state, fighter, Choice(ActionKind.HEAVY, heavy_aim))
        return Choice(ActionKind.LIGHT, aim)
    if visible_action and visible_action.kind in {ActionKind.HEAVY, ActionKind.DASH_LIGHT, ActionKind.DASH}:
        dash = move(state, fighter, ActionKind.DASH_LIGHT, True, aim=Zone.HAND if own.accuracy >= cfg["zones"]["difficulty"]["HAND"] else Zone.TORSO)
        if dash:
            return dash
    if gap <= cfg["ai"]["close_gap"]:
        return _fallback(state, fighter, move(state, fighter, ActionKind.DASH_LIGHT, True, aim=Zone.TORSO))
    return _fallback(state, fighter, move(state, fighter, ActionKind.WALK, True))


def _guard_release(state, fighter, bot):
    action = fighter.action
    if not action or action.kind != ActionKind.GUARD or action.phase_at(state.tick) != "GUARD_HOLD":
        return
    if bot == "NaiveGuardBot":
        return
    other = state.other(fighter)
    visible_heavy = observe(state, fighter.id).public_key.opp_state == "WINDUP_H"
    bonus = state.config["guard"]["heavy_window_bonus"] if visible_heavy else 0
    if fighter.riposte or state.tick >= action.guard_ready_tick + action.parry_window + bonus:
        release_guard(state, fighter.id)


def make_fight(first_bot, second_bot, seed, config, *, level_gap=0, mirror=False, swap=False):
    layout = config["arena"]
    player_level = config["balance"]["test_level"] + level_gap
    enemy_level = config["balance"]["test_level"]
    fighters = []
    for index, (bot, level) in enumerate(((first_bot, player_level), (second_bot, enemy_level))):
        identity = ("ronin" if swap else "player") if index == 0 else ("player" if swap else "ronin")
        fixture = "dorime" if index == 0 or mirror else "ronin"
        pos = layout["player_pos"] if index == 0 else layout["enemy_pos"]
        fighter = Fighter(identity, bot, level, **config["fixtures"][fixture], pos=tuple(pos),
                          equipment=Equipment(Weapon(**config["gear"]["weapon"])),
                          bandages=layout["bandages"], food=layout["food"], water=layout["water"])
        if bot == "Ronin":
            fighter.personality = Personality(**deepcopy(config["ronin"]))
        fighters.append(fighter)
    floor = {(x, y) for x in range(1, layout["width"]-1) for y in range(1, layout["height"]-1)}
    return initialize_fight(fighters, config=config, rng_seed=seed, walkable=floor,
                             exits={tuple(pos) for pos in layout["exits"]})


def run_fight(first_bot, second_bot, *, seed, config=None, level_gap=0, mirror=False, swap=False, trace=False):
    cfg = deepcopy(config) if config is not None else load_config()
    state = make_fight(first_bot, second_bot, seed, cfg, level_gap=level_gap, mirror=mirror, swap=swap)
    bots = {state.fighters[0].id: first_bot, state.fighters[1].id: second_bot}
    rngs = {fighter.id: random.Random(f"{seed}:{index}") for index, fighter in enumerate(state.fighters)}
    counts = Counter()
    action_counts = {fighter.id: Counter() for fighter in state.fighters}
    outcome_cursor = len(state.event_log)
    while not state.end_reason and state.tick < cfg["balance"]["max_ticks"] and sum(counts.values()) < cfg["balance"]["max_decisions"]:
        for fighter in state.fighters:
            _guard_release(state, fighter, bots[fighter.id])
        choices = []
        for fighter in state.fighters:
            if fighter.action is None and fighter.can_act(state.tick):
                # Both sides are policy-driven bots in this harness, so every
                # actor follows the engine's normal three-decision fear break.
                if fear_decision(state, fighter):
                    break
                category = None
                if bots[fighter.id] == "Ronin":
                    decision = choose_action(state, fighter.id)
                    choice, category = decision.choice, decision.category
                else:
                    choice = choose_bot(state, fighter.id, bots[fighter.id], rngs[fighter.id])
                choices.append((fighter.id, choice, category))
        if state.end_reason:
            break
        # All choices above read precisely the same pre-commit state.
        for identity, choice, category in choices:
            if category is not None:
                record_ai_decision(next(f for f in state.fighters if f.id == identity), category)
                state.emit(EventType.DECISION, identity, deltas={"category": category})
            commit_action(state, identity, choice)
            counts[identity] += 1
            action_counts[identity][choice.kind.value] += 1
        advance_one_tick(state)
        record_ai_outcomes(state, state.event_log[outcome_cursor:])
        outcome_cursor = len(state.event_log)
    first, second = state.fighters
    winner = "first" if state.winner == first.id else "second" if state.winner == second.id else None
    loser = second if winner == "first" else first if winner == "second" else None
    encoded = json.dumps(state.to_data()["event_log"], sort_keys=True).encode()
    report = {"seed": seed, "winner": winner, "reason": state.end_reason or "timeout", "ticks": state.tick,
            "decisions": sum(counts.values()), "player_decisions": counts[first.id], "events": len(state.event_log),
            "first_actions": dict(action_counts[first.id]), "second_actions": dict(action_counts[second.id]),
             "event_sha256": hashlib.sha256(encoded).hexdigest(),
             "loser_lost_limb": bool(loser and loser.hand_severed),
             "loser_crippled_leg": bool(loser and any(status.no_dash for status in loser.statuses)),
             "first_blood": first.blood, "second_blood": second.blood,
             "first_stamina": first.stamina, "second_stamina": second.stamina,
             "first_fatigue": first.fatigue, "second_fatigue": second.fatigue,
             "adrenaline_debt_pending": sum(fighter.adrenaline_debt_due
                                            for fighter in state.fighters
                                            if fighter.adrenaline_debt_pending),
             "seat": "second" if swap else "first"}
    if trace:
        relevant = {"ACTION_START", "HIT", "BLOCK", "PARRY", "GUARD_BREAK", "WHIFF", "FEAR_BREAK",
                    "STABILITY", "FALL", "RISE", "FIGHT_END", "FEINT", "SHOVE", "DISARM", "ADRENALINE",
                    "SEVER", "WEAPON_STRESS", "WEAPON_DROP", "WEAPON_BREAK", "WEAPON_PICKUP",
                    "EYE_INJURY", "DECAPITATION", "INCAPACITATED"}
        report["trace"] = [{"tick": event.tick, "type": event.type.value,
                            "actor": event.actor, "target": event.target,
                            "zone": event.zone.value if event.zone else None,
                            "severity": event.severity.value if event.severity else None,
                            "deltas": event.deltas, "causes": event.cause_tags}
                           for event in state.event_log if event.type.value in relevant]
    return report


def run_matchup(first_bot, second_bot, *, fights, seed=0, config=None, level_gap=0, mirror=False):
    cfg = deepcopy(config) if config is not None else load_config()
    if fights <= 0:
        raise ValueError("Fights must be positive")
    results = [run_fight(first_bot, second_bot, seed=seed+index, config=cfg, level_gap=level_gap,
                         mirror=mirror, swap=mirror and index % 2 == 1) for index in range(fights)]
    wins = sum(item["winner"] == "first" for item in results)
    losses = sum(item["winner"] == "second" for item in results)
    limbs = [item for item in results if item["loser_lost_limb"]]
    reasons = Counter(item["reason"] for item in results)
    reason_labels = {"death": "blood_loss", "unconscious": "collapse"}
    end_reasons = {reason_labels.get(reason, reason): count for reason, count in reasons.items()}
    return {"first_bot": first_bot, "second_bot": second_bot, "fights": fights, "wins": wins,
            "losses": losses, "draws": fights-wins-losses, "win_rate": wins/fights,
            "end_reasons": end_reasons,
            "median_player_decisions": statistics.median(item["player_decisions"] for item in results),
            "median_total_decisions": statistics.median(item["decisions"] for item in results),
            "first_actions": dict(sum((Counter(item["first_actions"]) for item in results), Counter())),
            "second_actions": dict(sum((Counter(item["second_actions"]) for item in results), Counter())),
            "loser_limb_fights": len(limbs), "loser_limb_yields": sum(item["reason"] == "yield" for item in limbs),
            "loser_crippled_leg_fights": sum(item["loser_crippled_leg"] for item in results),
            "seat_counts": dict(Counter(item["seat"] for item in results)),
            "config_sha256": fingerprint(cfg), "results": results}


def assess(reports, config):
    rule = config["balance"]
    failed = []
    if set(MATCHUPS) - set(reports):
        failed.append("missing_matchups")
    if any(report["fights"] < rule["fights_per_matchup"] for report in reports.values()):
        failed.append("sample_size")
    if any(report["end_reasons"].get("timeout", 0) for report in reports.values()):
        failed.append("timeouts")
    if "punisher" in reports and not rule["punisher_win"][0] <= reports["punisher"]["win_rate"] <= rule["punisher_win"][1]:
        failed.append("punisher_win")
    for name in ("spam_light", "spam_heavy"):
        if name in reports and reports[name]["win_rate"] >= rule["spam_win_max"]:
            failed.append(name)
    if "kite" in reports and reports["kite"]["win_rate"] >= rule["kite_win_max"]:
        failed.append("kite_win")
    if "mirror" in reports and not rule["mirror_win"][0] <= reports["mirror"]["win_rate"] <= rule["mirror_win"][1]:
        failed.append("mirror_win")
    gain = reports["level_gap"]["win_rate"] - reports["punisher"]["win_rate"] if "level_gap" in reports and "punisher" in reports else None
    if gain is not None and (not rule["level_win_gain"][0] <= gain <= rule["level_win_gain"][1] or reports["level_gap"]["win_rate"] >= 1):
        failed.append("level_gain")
    decisions = [item["decisions"] for report in reports.values() for item in report["results"]]
    median = statistics.median(decisions) if decisions else None
    if median is not None and not rule["median_decisions"][0] <= median <= rule["median_decisions"][1]:
        failed.append("fight_length")
    limb_total = sum(report["loser_limb_fights"] for report in reports.values())
    limb_yields = sum(report["loser_limb_yields"] for report in reports.values())
    yield_rate = limb_yields/limb_total if limb_total else None
    if yield_rate is None or yield_rate < rule["limb_loss_yield_min"]:
        failed.append("limb_loss_yield")
    return {"passed": not failed, "failed": failed, "median_decisions": median,
            "level_win_gain": gain, "limb_loss_yield_rate": yield_rate}
