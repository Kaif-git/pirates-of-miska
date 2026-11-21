"""Matched-novice counter timing and opportunity-expiry contract fixtures."""
from pirates.duel_system.engine import Choice, advance_one_tick, commit_action
from pirates.duel_system.exchange_log import format_exchange, summarize_exchange
from pirates.duel_system.model import ActionKind, EventType, Zone
from pirates.duel_system.session import DuelSession


def duel():
    session = DuelSession.new(seed=2481, guide_mode="off")
    session.player.pos, session.enemy.pos = (4, 3), (5, 3)
    session.player.fear_meter = session.enemy.fear_meter = 0
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
    recap = "\n".join(format_exchange(
        session.state.event_log, {player.id: "You", enemy.id: "Ronin"}))
    assert "Light impact was due t3" in recap
    assert "before the opponent's Heavy at t7" in recap
    assert "effective Speed" in recap and "interrupted it" in recap
    summary = summarize_exchange(session.state.event_log,
                                {player.id: "You", enemy.id: "Ronin"})
    assert "Timing: Light impact t3 preceded Heavy t7" in summary[3]
    assert "effective Speed" in summary[3]


def test_intersecting_same_line_attacks_clash_when_contacts_are_one_tick_apart():
    session = duel()
    player, enemy = session.player, session.enemy
    player_action = commit_action(session.state, player.id,
                                  Choice(ActionKind.LIGHT, Zone.TORSO, target_id=enemy.id))
    enemy_action = commit_action(session.state, enemy.id,
                                 Choice(ActionKind.LIGHT, Zone.TORSO, target_id=player.id))
    assert player_action.strike_tick == enemy_action.strike_tick

    advance_to(session.state, player_action.strike_tick)

    clash = next(event for event in session.state.event_log if event.type == EventType.CLASH)
    assert clash.deltas["tick_gap"] == 0
    assert not any(event.type == EventType.HIT for event in session.state.event_log)
    assert player.action.clash_resolved and enemy.action.clash_resolved
    recap = "\n".join(format_exchange(
        session.state.event_log, {player.id: "You", enemy.id: "Ronin"}))
    assert "weapon paths intersected" in recap and "both body strikes were stopped" in recap

    saved = session.state.to_data()
    restored = type(session.state).from_data(saved)
    advance_to(restored, max(player.action.end_tick, enemy.action.end_tick))
    assert sum(event.type == EventType.CLASH for event in restored.event_log) == 1
    assert not any(event.type == EventType.HIT for event in restored.event_log)


def test_clash_window_includes_one_tick_lead_but_not_two_tick_lead():
    for lead, expect_clash in ((1, True), (2, False)):
        session = duel()
        player, enemy = session.player, session.enemy
        first = commit_action(session.state, player.id,
                              Choice(ActionKind.LIGHT, Zone.TORSO, target_id=enemy.id))
        second = commit_action(session.state, enemy.id,
                               Choice(ActionKind.LIGHT, Zone.TORSO, target_id=player.id))
        second.strike_tick = first.strike_tick + lead

        advance_to(session.state, first.strike_tick)

        clashes = [event for event in session.state.event_log if event.type == EventType.CLASH]
        assert bool(clashes) is expect_clash
        if expect_clash:
            advance_to(session.state, second.strike_tick)
            assert not any(event.type == EventType.HIT for event in session.state.event_log)
        else:
            assert any(event.type == EventType.HIT and event.actor == player.id
                       for event in session.state.event_log)


def test_different_aim_lanes_pass_without_a_false_blade_clash():
    session = duel()
    player, enemy = session.player, session.enemy
    first = commit_action(session.state, player.id,
                          Choice(ActionKind.LIGHT, Zone.HEAD, target_id=enemy.id))
    second = commit_action(session.state, enemy.id,
                           Choice(ActionKind.LIGHT, Zone.LEG, target_id=player.id))
    assert first.strike_tick == second.strike_tick

    advance_to(session.state, first.strike_tick)

    assert not any(event.type == EventType.CLASH for event in session.state.event_log)
    assert {event.actor for event in session.state.event_log if event.type == EventType.HIT} == {
        player.id, enemy.id}


def test_thrust_loses_to_a_timely_lateral_run_not_to_same_tick_walk():
    session = DuelSession.new(seed=2482, guide_mode="off")
    player, enemy = session.player, session.enemy
    player.fear_meter = enemy.fear_meter = 0
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
    summary = summarize_exchange(session.state.event_log,
                                 {player.id: "You", enemy.id: "Ronin"})
    assert "Target moved" in summary[3]
    assert "locked sector" in summary[3]


def test_opportunity_read_expires_at_observed_stagger_deadline():
    session = duel()
    enemy = session.enemy
    enemy.stagger_until = 4
    before = session.render_model()
    assert "Cannot voluntarily act for 4 more ticks" in before.enemy_opportunity
    session.state.tick = 4
    after = session.render_model()
    assert "Can choose now" in after.enemy_opportunity
    assert "next choice unknown" in after.enemy_opportunity
    assert "readiness is not an opening by itself" in after.enemy_opportunity.lower()
