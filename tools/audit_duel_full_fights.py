"""Compare authored duel profiles over complete, fixed-policy seeded fights.

This is a descriptive variety diagnostic, not player-feel or balance acceptance.
Run from the project root: python -B tools/audit_duel_full_fights.py --fights 30
"""
from collections import Counter
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pirates.duel_system.ai import candidates, observe
from pirates.duel_system.config import load_config
from pirates.duel_system.engine import Choice
from pirates.duel_system.model import ActionKind, EventType, Zone
from pirates.duel_system.session import DuelSession


def player_policy(session):
    """Use a stable, visible-info-only approach/Light baseline for comparison."""
    state, player = session.state, session.player
    options = dict(candidates(state, player, observe(state, player.id)))
    gap = max(abs(player.pos[0]-session.enemy.pos[0]),
              abs(player.pos[1]-session.enemy.pos[1]))
    preference = (("LIGHT", "THRUST", "HEAVY", "SHOVE", "GUARD", "WAIT")
                  if gap <= 1 else
                  ("DASH_IN", "WALK_IN", "RUN_IN", "DASH_LIGHT", "WAIT", "GUARD"))
    for category in preference:
        if category in options:
            choice = options[category]
            if choice.kind in {ActionKind.LIGHT, ActionKind.HEAVY, ActionKind.THRUST}:
                return Choice(choice.kind, Zone.TORSO, target_id=session.enemy.id)
            return choice
    return Choice(ActionKind.WAIT)


def audit_profile(profile, fights, seed):
    actions, outcomes, openings = Counter(), Counter(), Counter()
    completed = 0
    for index in range(fights):
        session = DuelSession.new(seed=seed+index, archetype=profile, guide_mode="off")
        prior = len(session.state.event_log)
        for _ in range(80):
            if session.state.end_reason:
                break
            session.resolve_round(player_policy(session))
        events = session.state.event_log[prior:]
        npc_actions = [event.deltas.get("kind") for event in events
                       if event.type == EventType.ACTION_START and event.actor == session.enemy.id]
        actions.update(npc_actions)
        if npc_actions:
            openings[npc_actions[0]] += 1
        outcomes.update(f"{event.type.value}:{event.deltas.get('outcome', 'UNKNOWN')}"
                        for event in events if event.type in {
                            EventType.SHOVE, EventType.FEINT, EventType.DISARM})
        completed += session.state.end_reason is not None
    total = sum(actions.values())
    return {
        "profile": profile,
        "fights": fights,
        "completed_fights": completed,
        "npc_actions": sum(actions.values()),
        "distinct_npc_actions": len(actions),
        "action_counts": dict(actions),
        "opening_counts": dict(openings),
        "tactical_outcomes": dict(outcomes),
        "top_action_share": round(max(actions.values(), default=0)/total, 3) if total else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fights", type=int, default=30)
    parser.add_argument("--seed", type=int, default=9400)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.fights <= 0:
        parser.error("--fights must be positive")
    profiles = load_config()["personality"]["roster"]
    report = {
        "scope": "descriptive full-fight distribution under fixed visible-info player policy; not acceptance",
        "seed": args.seed,
        "fights_per_profile": args.fights,
        "profiles": [audit_profile(profile, args.fights, args.seed) for profile in profiles],
    }
    payload = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
