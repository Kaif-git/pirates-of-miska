"""Controlled choke-point isolation and open-encirclement P7 scenarios.

The isolation sample intentionally uses a strong player, an injured leader,
low starting hostile morale, a one-tile corridor, and a fixed passive-unseen
NPC policy. It demonstrates the mechanics' limits, not expected player win rate.
The open-surround sample uses normal group AI against a depleted player.
"""
from pathlib import Path
import argparse
import hashlib
import json
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from duel_group_fixture import make_group_session
from pirates.duel_system.engine import Choice, check_end_conditions, legality
from pirates.duel_system.model import ActionKind, EventType, Zone
from pirates.duel_system.stats import weapon_reach


def _attack_counts(session):
    hits = [event for event in session.state.event_log
            if event.type == EventType.HIT and event.target == session.player.id]
    by_tick = {}
    for event in hits:
        by_tick.setdefault(event.tick, set()).add(event.actor)
    return len(hits), max((len(actors) for actors in by_tick.values()), default=0)


def _assert_one_commitment_per_actor_tick(session):
    starts = [event for event in session.state.event_log if event.type == EventType.ACTION_START]
    counts = {}
    for event in starts:
        counts.setdefault(event.tick, {}).setdefault(event.actor, 0)
        counts[event.tick][event.actor] += 1
    assert all(count <= 1 for row in counts.values() for count in row.values()), counts
    return starts


def _run_isolation(seed):
    from pirates.duel_system import session as session_module

    session = make_group_session(5, seed, guide_mode="off")
    state, player = session.state, session.player
    state.walkable = {(x, 3) for x in range(1, 12)}
    player.pos = (4, 3)
    player.strength, player.accuracy, player.speed = 45, 45, 30
    player.blood, player.fatigue, player.stamina = 100, 0, 100
    for index, fighter in enumerate(state.fighters[1:]):
        fighter.pos = (5+index, 3)
        fighter.strength, fighter.accuracy, fighter.speed = 12, 12, 12
        fighter.morale, fighter.anger = 20, 0
        fighter.fatigue, fighter.stamina = 0, 100
    leader = state.fighter("raider_1")
    leader.blood = 30
    check_end_conditions(state)

    def passive_unseen_policy(snapshot, fighter_id):
        actor = snapshot.fighter(fighter_id)
        for target in snapshot.visible_hostiles_for(fighter_id):
            gap = max(abs(actor.pos[0]-target.pos[0]), abs(actor.pos[1]-target.pos[1]))
            if gap <= weapon_reach(actor, snapshot.config):
                choice = Choice(ActionKind.LIGHT, Zone.TORSO, target_id=target.id)
                if not legality(snapshot, fighter_id, choice):
                    return SimpleNamespace(category="LIGHT", choice=choice)
        return SimpleNamespace(category="WAIT", choice=Choice(ActionKind.WAIT))

    original = session_module.choose_action
    session_module.choose_action = passive_unseen_policy
    rounds = 0
    try:
        while rounds < 20 and not state.end_reason:
            if not session.ready:
                session.run_to_player()
                if state.end_reason:
                    break
            visible = state.visible_hostiles_for(player.id)
            target = leader if leader in visible and not leader.unconscious else None
            choice = (Choice(ActionKind.HEAVY, Zone.TORSO, target_id=leader.id)
                      if target else Choice(ActionKind.WAIT))
            if not session.resolve_round(choice):
                assert state.end_reason, "Isolation scenario stopped without a terminal outcome"
            rounds += 1
    finally:
        session_module.choose_action = original

    starts = _assert_one_commitment_per_actor_tick(session)
    total_hits, max_attackers = _attack_counts(session)
    outcomes = {fighter.id: fighter.terminal_reason for fighter in state.fighters
                if fighter.terminal_reason}
    leader_responses = [event for event in state.event_log
                        if event.type == EventType.POOL_CHANGE
                        and event.deltas.get("group_response") == "LEADER_LOST"]
    assert state.winner == player.id and leader.terminal_reason == "unconscious", (seed, state.end_reason, outcomes)
    assert all(outcomes.get(f"raider_{index}") == "yield" for index in range(2, 5)), outcomes
    assert max_attackers <= 1, (seed, max_attackers)
    assert len(leader_responses) == 3, (seed, len(leader_responses))
    return {"seed": seed, "end_reason": state.end_reason, "winner": state.winner,
            "rounds": rounds, "ticks": state.tick, "player_blood_remaining": round(player.blood, 2),
            "player_hits_taken": total_hits, "max_distinct_attackers_same_tick": max_attackers,
            "terminal_outcomes": outcomes, "leader_loss_morale_responses": len(leader_responses),
            "action_starts": len(starts), "player_commits": len(session.inputs)}


def _run_encirclement(seed):
    session = make_group_session(5, seed, guide_mode="off")
    state, player = session.state, session.player
    player.pos = (6, 3)
    player.blood, player.fatigue, player.stamina = 35, 65, 20
    player.strength, player.accuracy = 8, 12
    positions = ((5, 3), (7, 3), (6, 2), (6, 4))
    for fighter, position in zip(state.fighters[1:], positions):
        fighter.pos = position
        fighter.strength, fighter.accuracy, fighter.speed = 25, 25, 22
        fighter.morale = 100
    check_end_conditions(state)

    rounds = 0
    while rounds < 12 and not state.end_reason:
        if not session.ready:
            session.run_to_player()
            if state.end_reason:
                break
        assert session.resolve_round(Choice(ActionKind.WAIT)), (seed, state.tick, state.end_reason)
        rounds += 1

    starts = _assert_one_commitment_per_actor_tick(session)
    total_hits, max_attackers = _attack_counts(session)
    outcomes = {fighter.id: fighter.terminal_reason for fighter in state.fighters
                if fighter.terminal_reason}
    assert state.end_reason == "unconscious" and player.terminal_reason == "unconscious", (seed, outcomes)
    assert max_attackers == 4, (seed, max_attackers)
    assert state.winner in {fighter.id for fighter in state.fighters[1:]}, seed
    return {"seed": seed, "end_reason": state.end_reason, "winner": state.winner,
            "rounds": rounds, "ticks": state.tick, "player_blood_remaining": round(player.blood, 2),
            "player_hits_taken": total_hits, "max_distinct_attackers_same_tick": max_attackers,
            "terminal_outcomes": outcomes, "action_starts": len(starts),
            "player_commits": len(session.inputs)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be positive")
    isolation = [_run_isolation(700+index) for index in range(1, args.samples+1)]
    encirclement = [_run_encirclement(8000+index) for index in range(1, args.samples+1)]
    config_hash = hashlib.sha256((ROOT / "content" / "duel_system.json").read_bytes()).hexdigest()
    result = {"scope": "controlled demonstration only; not a matchup or universal balance estimate",
              "config_sha256": config_hash,
              "isolation": {"setup": "5 actors; one-tile corridor; strong player; low-morale group; injured leader; "
                                      "fixed NPC policy attacks only visible in-reach targets and otherwise waits",
                           "samples": isolation,
                           "successes": sum(row["winner"] == "player" for row in isolation)},
              "encirclement": {"setup": "5 actors; open four-sided surround; depleted player; normal group AI",
                               "samples": encirclement,
                               "player_incapacitations": sum(row["end_reason"] == "unconscious"
                                                             for row in encirclement)}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Isolation: {result['isolation']['successes']}/{args.samples} controlled wins; "
          f"encirclement: {result['encirclement']['player_incapacitations']}/{args.samples} incapacitations.")
    print(f"Config SHA-256: {config_hash}")


if __name__ == "__main__":
    main()
