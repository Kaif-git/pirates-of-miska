"""Matched-novice counter timing and opportunity-expiry contract fixtures."""
from pirates.duel_system.engine import Choice, advance_one_tick, commit_action
from pirates.duel_system.model import ActionKind, EventType, Zone
from pirates.duel_system.session import DuelSession


def duel():
    session = DuelSession.new(seed=2481, guide_mode="off")
    session.player.pos, session.enemy.pos = (4, 3), (5, 3)
    return session


def advance_to(state, tick):
    while state.tick < tick and not state.end_reason:
        advance_one_tick(state)


def test_light_is_a_short_counter_to_exposed_heavy_windup():
    session = duel()
    player, enemy = session.player, session.enemy
    heavy = commit_action(session.state, enemy.id,
                          Choice(ActionKind.HEAVY, Zone.TORSO, target_id=player.id))
    light = commit_action(session.state, player.id,
                          Choice(ActionKind.LIGHT, Zone.TORSO, target_id=enemy.id))
    assert light.strike_tick == 3 < heavy.strike_tick == 7
    advance_to(session.state, light.strike_tick)
    assert any(event.type == EventType.HIT and event.actor == player.id
               and "LIGHT_INTERRUPTS_HEAVY" in event.cause_tags
               for event in session.state.event_log)
    assert enemy.action is None
    assert any(event.type == EventType.INTERRUPT and event.actor == enemy.id
               for event in session.state.event_log)


def test_thrust_loses_to_a_timely_lateral_run_not_to_same_tick_walk():
    session = DuelSession.new(seed=2482, guide_mode="off")
    player, enemy = session.player, session.enemy
    player.pos, enemy.pos = (2, 3), (4, 3)
    thrust = commit_action(session.state, player.id,
                           Choice(ActionKind.THRUST, Zone.TORSO, target_id=enemy.id))
    run = commit_action(session.state, enemy.id,
                        Choice(ActionKind.RUN, direction=(0, 1), tiles=1))
    assert run.path[0][0] == 3 < thrust.strike_tick == 4
    advance_to(session.state, thrust.strike_tick)
    assert enemy.pos == (4, 4)
    assert not any(event.type == EventType.HIT and event.actor == player.id
                   for event in session.state.event_log)
    miss = next(event for event in session.state.event_log
                if event.type == EventType.WHIFF and event.actor == player.id)
    assert "OUTSIDE_LOCKED_ATTACK_GEOMETRY" in miss.cause_tags


def test_opportunity_read_expires_at_observed_stagger_deadline():
    session = duel()
    enemy = session.enemy
    enemy.stagger_until = 4
    before = session.render_model()
    assert "Cannot voluntarily act for 4 more ticks" in before.enemy_opportunity
    session.state.tick = 4
    after = session.render_model()
    assert after.enemy_opportunity == "Next choice unknown; readiness is not an opening by itself."
