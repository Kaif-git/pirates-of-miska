"""Headless seeded combat, counter bot, replay verifier and JSON metrics.

Run from the project root: python tools/combat_headless.py --archetype duelist
Replay an arena F10 dump: python tools/combat_headless.py --replay PATH
"""
from pathlib import Path
import argparse
import json
import sys
from copy import deepcopy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "engine")]
from pirates.combat_core import (Actor, Combat, ARCHETYPES, COMBAT_RULESET_VERSION,
                                 PATTERNS, WEAPONS, distance, telegraph_audit)


def counter_bot(core, limit=500):
    """Only reads public state/telegraphs. No HP, damage or hit-roll cheats."""
    player = core.actors["captain"]
    for _ in range(limit):
        enemies = [a for a in core.actors.values() if a.faction != player.faction and a.active]
        if not player.active or not enemies:
            break
        enemy = min(enemies, key=lambda a: (distance(player.pos, a.pos), a.id))
        threats = [a for a in enemies if a.intent and player.pos in a.intent.target_tiles]
        if threats:
            attacker = min(threats, key=lambda a: a.intent.resolves_at_tick)
            if attacker.intent.resolves_at_tick - core.tick > 50:
                core.command("wait")
            elif PATTERNS[attacker.intent.kind]["parryable"]:
                core.command("brace", attacker.pos)
            else:
                safe = [(player.x + dx, player.y + dy) for dx, dy in ((0,-1),(1,0),(0,1),(-1,0))]
                safe = [p for p in safe if p in core.walkable and not core.at(p)
                        and not any(a.intent and p in a.intent.target_tiles for a in enemies)]
                if safe:
                    core.command("move", min(safe, key=lambda p: distance(p, enemy.pos)))
                else:
                    core.command("wait")
        elif player.statuses.get("bleeding", 0) > core.tick:
            core.command("bandage")
        elif distance(player.pos, enemy.pos) <= 1 and enemy.shield and core.tick >= enemy.shield_broken_until:
            core.command("shove", enemy.pos)
        elif (enemy.next_ready_tick - core.tick >= (100 if WEAPONS[enemy.weapon]["reload"] else 150)
              and distance(player.pos, enemy.pos) <= 2
              and (enemy.pos in core.tiles(player, "thrust", enemy.pos)
                   or (enemy.next_ready_tick - core.tick >= 150
                       and enemy.pos in core.tiles(player, "sweep", enemy.pos)))):
            pattern = "sweep" if enemy.next_ready_tick - core.tick >= 150 and enemy.pos in core.tiles(player, "sweep", enemy.pos) else "thrust"
            core.command(pattern, enemy.pos)
        elif distance(player.pos, enemy.pos) > 1:
            step = core.step_toward(player, enemy.pos)
            safe = step and not any(a.intent and step in a.intent.target_tiles for a in enemies)
            core.command("move", step) if safe else core.command("wait")
        elif all(player.pos not in core.tiles(enemy, p, player.pos) for p in WEAPONS[enemy.weapon]["patterns"]):
            core.command("shove", enemy.pos)
        else:
            core.command("wait")
    return core.report()


def bot_action(core, bot):
    """Return one ordinary player command for the baseline behavior bots."""
    player = core.actors["captain"]
    foes = [a for a in core.actors.values()
            if a.faction != player.faction and a.active]
    if not foes:
        return None
    enemy = min(foes, key=lambda a: (distance(player.pos, a.pos), a.id))
    if bot == "runner":
        options = []
        mode, stride = ("run", 2) if player.stamina >= 16 else ("move", 1)
        for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
            pos = player.x + dx * stride, player.y + dy * stride
            if core.blocker(player, mode, pos):
                continue
            options.append(pos)
        if not options and mode == "run":
            mode, stride = "move", 1
            for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
                pos = player.x + dx, player.y + dy
                if not core.blocker(player, mode, pos):
                    options.append(pos)
        if not options:
            return "wait", None
        destination = max(options,
                          key=lambda pos: (min(distance(pos, foe.pos) for foe in foes),
                                            distance(pos, enemy.pos), -pos[1], -pos[0]))
        return mode, destination

    threats = [foe for foe in foes if foe.intent and player.pos in foe.intent.target_tiles]
    if threats:
        options = [(player.x + dx, player.y + dy)
                   for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0))]
        options = [pos for pos in options if pos in core.walkable and not core.at(pos)
                   and core.doors.get(pos, True)]
        safe = [pos for pos in options
                if not any(foe.intent and pos in foe.intent.target_tiles for foe in foes)]
        if safe:
            destination = max(safe, key=lambda pos: (distance(pos, enemy.pos), -pos[1], -pos[0]))
            return "move", destination
        return "wait", None

    # Deliberately exploit the old recovery window: hit when an enemy is
    # committed/recovering, then increase distance instead of trading blows.
    if enemy.state in {"recovery", "staggered", "reloading"}:
        available = [pattern for pattern in WEAPONS[player.weapon]["patterns"]
                     if not PATTERNS[pattern].get("feint")
                     and not core.blocker(player, pattern, enemy.pos)]
        if available:
            pattern = max(available, key=lambda value: (PATTERNS[value]["damage"], value))
            return pattern, enemy.pos
    options = [(player.x + dx, player.y + dy)
               for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0))]
    options = [pos for pos in options if pos in core.walkable and not core.at(pos)
               and core.doors.get(pos, True)]
    if distance(player.pos, enemy.pos) <= 2 and options:
        destination = max(options, key=lambda pos: (distance(pos, enemy.pos), -pos[1], -pos[0]))
        return "move", destination
    step = core.step_toward(player, enemy.pos)
    return ("move", step) if step else ("wait", None)


def bot_match(archetype="duelist", seed=1, bot="kiter", limit=500):
    """Run a public-state-only kiter/runner against the production NPC AI."""
    core = match(archetype, seed)
    player = core.actors["captain"]
    commands = 0
    while commands < limit:
        if not player.active or not any(a.active for a in core.actors.values()
                                     if a.faction != player.faction):
            break
        action, target = bot_action(core, bot)
        if action is None:
            break
        core.command(action, target)
        commands += 1
    foes = [a for a in core.actors.values() if a.faction != player.faction]
    winner = ("captain" if player.active and all(not a.active for a in foes)
              else "enemy" if not player.active else None)
    return {**core.report(), "bot": bot, "archetype": archetype, "winner": winner,
            "commands": commands, "captain_hp": player.hp,
            "enemy_hp": {a.id: a.hp for a in foes}}


def bot_sweep(bot, matches=10, seed=2026, archetypes=None, limit=500):
    archetypes = list(archetypes or ARCHETYPES)
    rows = {name: [bot_match(name, seed + index, bot, limit)
                   for index in range(matches)] for name in archetypes}
    return {"bot": bot, "matches_per_archetype": matches, "seed": seed,
            "archetypes": {name: {
                "matches": len(reports),
                "wins": sum(row["winner"] == "captain" for row in reports),
                "losses": sum(row["winner"] == "enemy" for row in reports),
                "timeouts": sum(row["winner"] is None for row in reports),
                "mean_damage_taken": round(sum(row["damage_taken_by_actor"]["captain"]
                                                for row in reports) / len(reports), 2),
                "mean_ticks": round(sum(row["ticks"] for row in reports) / len(reports), 1),
                "samples": [{"seed": row["seed"], "winner": row["winner"],
                             "ticks": row["ticks"], "damage_taken": row["damage_taken_by_actor"]["captain"],
                             "damage_dealt": row["damage_by_actor"]["captain"],
                             "commands": row["commands"]} for row in reports],
            } for name, reports in rows.items()}}


def match(archetype="duelist", seed=1, speed=None):
    core = Combat({(x,y) for x in range(1,17) for y in range(1,15)}, seed)
    core.add(Actor("captain", "You", 5, 7, faction="player", facing=(1,0)))
    data = ARCHETYPES[archetype]
    core.add(Actor("enemy", data["name"], 9, 7, weapon=data["weapon"], personality=data["personality"],
                   secondary_weapon=data.get("secondary_weapon"),
                   tactics=data.get("tactics", "standard"),
                   speed=speed or data["speed"], shield=data.get("shield",False)))
    return core


def flawless_search(core, depth=35, width=24):
    """Bounded deterministic counter-existence search, not the NPC's AI.

    It emits an ordinary replayable input trace; it never edits combat state.
    Search is deliberately separate from the limited-perception production AI.
    """
    frontier = [core]
    seen = set()
    for _ in range(depth):
        candidates = []
        for state in frontier:
            p = state.actors["captain"]
            enemies = [a for a in state.actors.values() if a.faction != p.faction and a.active]
            if not enemies:
                return state
            e = min(enemies, key=lambda a: distance(a.pos, p.pos))
            actions = [("wait", None), ("brace", e.pos)]
            actions += [(pat, e.pos) for pat in ("thrust", "sweep", "shove")]
            actions += [("move", (p.x+dx, p.y+dy)) for dx,dy in ((0,-1),(1,0),(0,1),(-1,0))]
            for action, target in actions:
                if state.blocker(p, action, target):
                    continue
                nxt = deepcopy(state)
                if not nxt.command(action, target) or nxt.metrics["player_damage"]:
                    continue
                actors = list(nxt.actors.values())
                key = tuple((a.id,a.pos,a.hp,a.state,a.next_ready_tick-nxt.tick,a.braced,
                             a.intent.kind if a.intent else None,
                             a.intent.resolves_at_tick-nxt.tick if a.intent else None,
                             a.shield_broken_until > nxt.tick) for a in actors)
                if key in seen:
                    continue
                seen.add(key)
                remaining = sum(a.hp for a in actors if a.faction == "enemy")
                nearest = min((distance(nxt.actors["captain"].pos, a.pos) for a in actors if a.active and a.faction == "enemy"), default=0)
                score = remaining * 100 + nearest * 4 + nxt.tick * .001
                candidates.append((score, nxt))
        if not candidates:
            break
        frontier = [s for _,s in sorted(candidates, key=lambda pair: pair[0])[:width]]
    return None


def replay_dump(data):
    version = data.get("combat_ruleset_version")
    if version != COMBAT_RULESET_VERSION:
        raise ValueError(f"Replay ruleset {version!r} is incompatible; expected {COMBAT_RULESET_VERSION}")
    environment = data.get("environment", {})
    core = Combat({tuple(p) for p in data["walkable"]}, data["report"]["seed"],
                  doors={tuple(p): opened for p, opened in environment.get("doors", [])},
                  hazards={tuple(p): kind for p, kind in environment.get("hazards", [])})
    for fields in data["initial"]:
        fields = dict(fields)
        fields["intent"] = None
        fields["facing"] = tuple(fields["facing"])
        fields["last_seen"] = tuple(fields["last_seen"]) if fields["last_seen"] else None
        fields["aim_tiles"] = tuple(tuple(p) for p in fields.get("aim_tiles", ()))
        fields["home_pos"] = tuple(fields["home_pos"]) if fields.get("home_pos") else None
        core.add(Actor(**fields))
    for command in data["report"]["inputs"]:
        target = command["target"]
        if isinstance(target, list):
            target = tuple(target)
        if core.tick != command["tick"] or not core.command(command["action"], target):
            raise ValueError("Replay diverged (debug mutations/stepping are not normal inputs)")
    return core


def ai_match(first, second, seed=1, max_events=500):
    core = Combat({(x,y) for x in range(1,17) for y in range(1,15)}, seed)
    for aid, archetype, pos, faction in (("a", first, (5,7), "a"), ("b", second, (9,7), "b")):
        data = ARCHETYPES[archetype]
        core.add(Actor(aid, data["name"], *pos, weapon=data["weapon"],
                       secondary_weapon=data.get("secondary_weapon"), faction=faction,
                       personality=data["personality"], tactics=data.get("tactics", "standard"),
                       speed=data["speed"], shield=data.get("shield",False)))
    for _ in range(max_events):
        if sum(a.active for a in core.actors.values()) < 2:
            break
        core.run_until_player(single_event=True)
    survivors = [a for a in core.actors.values() if a.active]
    surviving_factions = sorted({a.faction for a in survivors})
    winner = surviving_factions[0] if len(surviving_factions) == 1 else None
    return {"tick": core.tick, "time_to_kill_ticks": core.tick if winner else None,
            "survivors": [a.id for a in survivors], "winner": winner,
            "fled": [a.id for a in core.actors.values() if a.fled],
            "actor_metrics": {k:dict(v) for k,v in core.actor_metrics.items()}, **core.metrics}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archetype", choices=ARCHETYPES, default="duelist")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--matches", type=int, default=1)
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--ai-vs-ai", action="store_true")
    parser.add_argument("--opponent", choices=ARCHETYPES)
    parser.add_argument("--same-matchup", action="store_true", help="Repeat --archetype vs --opponent instead of sweeping varied matchups")
    parser.add_argument("--prove-counter", action="store_true")
    parser.add_argument("--fairness-audit", action="store_true")
    parser.add_argument("--bot", choices=("kiter", "runner", "all"),
                        help="Run a disengagement-exploit or flee-only bot")
    parser.add_argument("--all-archetypes", action="store_true",
                        help="Sweep the selected bot against every archetype")
    parser.add_argument("--output", type=Path,
                        help="Write the complete JSON result to this path as well as stdout")
    parser.add_argument("--baseline", action="store_true",
                        help="Run both bots against all archetypes and save docs/combat_baseline.json")
    args = parser.parse_args()
    if args.baseline:
        result = {"ruleset": "pre-combat-rework", "seed_start": args.seed,
                  "matches_per_archetype": args.matches,
                  "bots": {bot: bot_sweep(bot, args.matches, args.seed)
                           for bot in ("kiter", "runner")}}
        output = args.output or ROOT / "docs" / "combat_baseline.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"baseline_saved": str(output), "matches_per_bot":
                          args.matches * len(ARCHETYPES), "bots": {
                              bot: {name: {key: value for key, value in row.items()
                                           if key != "samples"}
                                    for name, row in report["archetypes"].items()}
                              for bot, report in result["bots"].items()}}, indent=2))
    elif args.bot:
        bots = ("kiter", "runner") if args.bot == "all" else (args.bot,)
        if args.all_archetypes or args.bot == "all":
            result = {bot: bot_sweep(bot, args.matches, args.seed) for bot in bots}
        else:
            result = {bot: {"bot": bot, "archetype": args.archetype,
                            "matches": [bot_match(args.archetype, args.seed + n, bot)
                                        for n in range(args.matches)]} for bot in bots}
        if len(result) == 1:
            result = next(iter(result.values()))
        rendered = json.dumps(result, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
    elif args.fairness_audit:
        audit = telegraph_audit()
        print(json.dumps(audit, indent=2))
        if not audit["passed"]:
            raise SystemExit(1)
    elif args.replay:
        data = json.loads(args.replay.read_text(encoding="utf-8"))
        core = replay_dump(data)
        # Normalize tuples as JSON arrays for portable comparisons.
        assert json.loads(json.dumps(core.snapshot())) == data["state"], "Replay final state diverged"
        print("Replay verified: identical final state, intents, metrics and log.")
    elif args.ai_vs_ai:
        fixed = args.same_matchup or args.opponent is not None
        pairs = [(args.archetype, args.opponent or "brawler")] if fixed else [
            (first, second) for first in ARCHETYPES for second in ARCHETYPES if first != second]
        reports = [ai_match(*pairs[n % len(pairs)], args.seed + n // len(pairs))
                   for n in range(args.matches)]
        wins = {archetype: {"a": 0, "b": 0} for archetype in ARCHETYPES}
        matchup_rows = {}
        for n, report in enumerate(reports):
            pair = pairs[n % len(pairs)]
            key = " vs ".join(pair)
            row = matchup_rows.setdefault(key, {"matches":0,"a_wins":0,"b_wins":0,"draws":0,"ticks":0})
            row["matches"] += 1
            row["a_wins"] += report["winner"] == "a"
            row["b_wins"] += report["winner"] == "b"
            row["draws"] += report["winner"] is None
            row["ticks"] += report["tick"]
            if report["winner"]:
                wins[pair[0 if report["winner"] == "a" else 1]][report["winner"]] += 1
        for row in matchup_rows.values():
            row["mean_ticks"] = round(row.pop("ticks") / row["matches"], 1)
        resolved = [report for report in reports if report["winner"] is not None]
        result = {"combat_ruleset_version": COMBAT_RULESET_VERSION,
                  "matches": len(reports), "mode": "fixed" if fixed else "varied_ordered_matchups",
                  "resolved": len(resolved), "timeouts": len(reports) - len(resolved),
                  "mean_landed_hits_per_match": round(sum(r["hits"] for r in reports) / len(reports), 3),
                  "mean_landed_hits_per_resolved_match": round(
                      sum(r["hits"] for r in resolved) / max(1, len(resolved)), 3),
                  "wins_by_faction_and_archetype": wins, "matchups": matchup_rows,
                  "last": reports[-1]}
        rendered = json.dumps(result, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
    elif args.prove_counter:
        core = flawless_search(match(args.archetype, args.seed))
        print(json.dumps({**(core.report(flawless_possible=True) if core else {}),
                          "flawless_possible":core is not None, "proof_found":core is not None}, indent=2))
        if core is None:
            raise SystemExit(1)
    else:
        reports = [counter_bot(match(args.archetype, args.seed+n)) for n in range(args.matches)]
        print(json.dumps({"archetype":args.archetype, "matches":len(reports),
                          "flawless_wins":sum(r["flawless"] for r in reports),
                          "last":reports[-1]}, indent=2))


if __name__ == "__main__":
    main()
