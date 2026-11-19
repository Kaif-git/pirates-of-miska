"""Attack bearing, reach, tracking lock and shared threat geometry regressions."""
import pytest

from pirates.duel_system.engine import Choice, advance_one_tick, commit_action
from pirates.duel_system.geometry import attack_cells, attack_contains
from pirates.duel_system.model import ActionKind, CombatState, EventType, Zone
from pirates.duel_system.render_model import build_render_model
from pirates.duel_system.session import DuelSession
from test_duel_system_foundation import bare_fighter


def advance_to(state, tick):
    while state.tick < tick and not state.end_reason:
        advance_one_tick(state)


def adjacent_session():
    session = DuelSession.new(seed=1801, guide_mode="off")
    player, enemy = session.player, session.enemy
    player.pos, enemy.pos = (4, 3), (5, 3)
    return session, player, enemy


def test_attack_cells_are_a_forward_sector_or_narrow_reach_two_ray():
    origin = (4, 3)
    assert attack_cells(origin, (1, 0), ActionKind.LIGHT) == {
        (5, 2), (5, 3), (5, 4)}
    assert attack_contains(origin, (5, 4), (1, 0), ActionKind.HEAVY)
    assert not attack_contains(origin, (4, 2), (1, 0), ActionKind.HEAVY)
    assert attack_cells(origin, (1, 0), ActionKind.THRUST) == {
        (5, 3), (6, 3)}
    assert not attack_contains(origin, (5, 4), (1, 0), ActionKind.THRUST)


def test_light_tracks_until_lock_then_misses_target_outside_locked_sector():
    session, player, enemy = adjacent_session()
    action = commit_action(session.state, player.id,
                           Choice(ActionKind.LIGHT, Zone.TORSO, target_id=enemy.id))
    assert action.attack_bearing == (1, 0)
    assert action.bearing_lock_tick is not None

    # The target moves during preparation; lock reads its position before that tick's movement.
    enemy.pos = (4, 2)
    advance_to(session.state, action.bearing_lock_tick)
    assert action.attack_bearing == (0, -1)
    lock = next(event for event in session.state.event_log if event.type == EventType.ATTACK_LOCK)
    assert lock.deltas["bearing"] == (0, -1)

    # Movement after lock cannot make the attack home back onto its original eastward line.
    enemy.pos = (5, 3)
    advance_to(session.state, action.strike_tick)
    assert not any(event.type == EventType.HIT for event in session.state.event_log)
    whiff = next(event for event in session.state.event_log if event.type == EventType.WHIFF)
    assert "OUTSIDE_LOCKED_ATTACK_GEOMETRY" in whiff.cause_tags


def test_thrust_reaches_two_tiles_only_on_locked_line_and_hits_single_target():
    session = DuelSession.new(seed=1802, guide_mode="off")
    player, enemy = session.player, session.enemy
    player.pos, enemy.pos = (2, 3), (4, 3)
    action = commit_action(session.state, player.id,
                           Choice(ActionKind.THRUST, Zone.TORSO, target_id=enemy.id))
    advance_to(session.state, action.strike_tick)
    assert any(event.type == EventType.HIT and event.target == enemy.id
               for event in session.state.event_log)


def test_thrust_uses_piercing_contact_profile_while_light_remains_weapon_cutting():
    session, player, enemy = adjacent_session()
    thrust = commit_action(session.state, player.id,
                           Choice(ActionKind.THRUST, Zone.TORSO, target_id=enemy.id))
    advance_to(session.state, thrust.strike_tick)
    thrust_hit = next(event for event in session.state.event_log
                      if event.type == EventType.HIT and event.actor == player.id)
    assert thrust_hit.deltas["damage_type"] == "PIERCING"
    assert thrust_hit.deltas["structure"] == "TORSO_WALL"

    session, player, enemy = adjacent_session()
    light = commit_action(session.state, player.id,
                          Choice(ActionKind.LIGHT, Zone.TORSO, target_id=enemy.id))
    advance_to(session.state, light.strike_tick)
    light_hit = next(event for event in session.state.event_log
                     if event.type == EventType.HIT and event.actor == player.id)
    assert light_hit.deltas["damage_type"] == "CUTTING"


def test_thrust_ray_stops_at_an_intervening_body():
    attacker = bare_fighter("player", pos=(2, 3), team_id="players")
    blocker = bare_fighter("ally", pos=(3, 3), team_id="players")
    target = bare_fighter("enemy", pos=(4, 3), team_id="raiders")
    state = CombatState([attacker, target], rng_seed=34)
    action = commit_action(state, attacker.id,
                           Choice(ActionKind.THRUST, Zone.TORSO, target_id=target.id))
    state.fighters.insert(1, blocker)
    advance_to(state, action.strike_tick)
    whiff = next(event for event in state.event_log if event.type == EventType.WHIFF)
    assert whiff.deltas["blocker"] == blocker.id
    assert "ALLY_BLOCKED_LINE" in whiff.cause_tags
    assert not any(event.type == EventType.HIT and event.actor == attacker.id
                   for event in state.event_log)


def test_dash_light_approach_uses_actual_endpoint_and_cut_contact_contract():
    session = DuelSession.new(seed=1803, guide_mode="off")
    player, enemy = session.player, session.enemy
    player.pos, enemy.pos = (2, 3), (4, 3)
    action = commit_action(session.state, player.id,
                           Choice(ActionKind.DASH_LIGHT, Zone.TORSO, direction=(1, 0),
                                  tiles=1, target_id=enemy.id))
    assert action.path == [(2, (3, 3))]
    assert action.strike_tick == 5 and action.end_tick == 10
    assert action.cost_paid == pytest.approx(13, abs=0.01)
    advance_to(session.state, action.strike_tick)
    hit = next(event for event in session.state.event_log
               if event.type == EventType.HIT and event.actor == player.id)
    assert hit.deltas["damage_type"] == "CUTTING"


def test_early_diagonal_step_leaves_locked_heavy_sector_but_late_step_does_not():
    session, player, enemy = adjacent_session()
    player_choice = Choice(ActionKind.WALK, direction=(1, -1), tiles=1)
    commit_action(session.state, player.id, player_choice)
    heavy = commit_action(session.state, enemy.id,
                          Choice(ActionKind.HEAVY, Zone.TORSO, target_id=player.id))
    assert heavy.bearing_lock_tick < player.action.path[0][0] < heavy.strike_tick
    advance_to(session.state, heavy.strike_tick)
    assert player.pos == (5, 2)
    assert not any(event.type == EventType.HIT and event.actor == enemy.id
                   for event in session.state.event_log)
    whiff = next(event for event in session.state.event_log if event.type == EventType.WHIFF)
    assert "OUTSIDE_LOCKED_ATTACK_GEOMETRY" in whiff.cause_tags


def test_attack_bearing_survives_active_action_snapshot_round_trip():
    session, player, enemy = adjacent_session()
    action = commit_action(session.state, player.id,
                           Choice(ActionKind.HEAVY, Zone.TORSO, target_id=enemy.id))
    enemy.pos = (4, 2)
    advance_to(session.state, action.bearing_lock_tick)
    restored = DuelSession.from_data(session.to_data())
    action2 = restored.player.action
    assert action2.attack_bearing == (0, -1)
    assert action2.bearing_lock_tick == action.bearing_lock_tick


def test_legacy_active_attack_snapshot_locks_current_bearing_on_restore():
    session, player, enemy = adjacent_session()
    action = commit_action(session.state, player.id,
                           Choice(ActionKind.LIGHT, Zone.TORSO, target_id=enemy.id))
    data = session.to_data()
    serialized = next(row for row in data["state"]["fighters"] if row["id"] == player.id)
    serialized["action"].pop("attack_bearing")
    serialized["action"].pop("bearing_lock_tick")
    restored = DuelSession.from_data(data)
    assert restored.player.action.attack_bearing == (1, 0)
    assert restored.player.action.bearing_lock_tick == restored.state.tick


def test_visible_threat_preview_uses_the_same_forward_sector_as_contact():
    session, player, enemy = adjacent_session()
    commit_action(session.state, enemy.id,
                  Choice(ActionKind.LIGHT, Zone.TORSO, target_id=player.id))
    model = build_render_model(session.state, session.notebook.public_view())
    assert model.danger == frozenset({(4, 2), (4, 3), (4, 4)})
