"""P4 balance, stability, deterministic falls and grounded-play gates."""
from copy import deepcopy

import pytest

from pirates.duel_system.ai import candidates, observe
from pirates.duel_system.engine import Choice, advance_one_tick, commit_action, legality
from pirates.duel_system.model import ActionKind, CombatState, EventType, Severity, Status, Wound, Zone
from pirates.duel_system.stability import balance_rating, stability_band
from pirates.duel_system.wounds import defense_snapshot
from test_duel_system_scheduler import advance_to, arena


def grounded(fighter, tick=0):
    fighter.grounded = True
    fighter.grounded_since = tick
    fighter.stability = 0


def test_safe_walk_and_straight_fast_movement_never_randomly_reduce_stability():
    state = arena()
    player, enemy = state.fighters
    enemy.pos = (8, 6)
    original = player.stability
    commit_action(state, player.id, Choice(ActionKind.WALK, direction=(0, 1)))
    advance_to(state, player.action.end_tick)
    assert player.pos == (0, 1)
    commit_action(state, player.id, Choice(ActionKind.RUN, direction=(0, 1), tiles=2))
    advance_to(state, player.action.end_tick)
    assert player.pos == (0, 3)
    assert player.stability == original
    assert not player.grounded
    assert not any(event.type == EventType.FALL for event in state.event_log)


def test_only_overextended_heavy_miss_causes_configured_stability_loss():
    state = arena()
    attacker, enemy = state.fighters
    enemy.pos = (4, 0)
    before = attacker.stability
    commit_action(state, attacker.id, Choice(ActionKind.HEAVY, Zone.TORSO))
    advance_to(state, attacker.action.strike_tick)
    assert any(event.type == EventType.WHIFF and event.actor == attacker.id for event in state.event_log)
    loss = next(event for event in state.event_log if event.type == EventType.STABILITY)
    assert loss.cause_tags == ("OVEREXTENDED_HEAVY_WHIFF",)
    assert loss.deltas["before"] == before
    assert loss.deltas["after"] == attacker.stability < before
    assert not attacker.grounded

    light = arena()
    light.fighters[1].pos = (4, 0)
    original = light.fighters[0].stability
    commit_action(light, light.fighters[0].id, Choice(ActionKind.LIGHT, Zone.TORSO))
    advance_to(light, light.fighters[0].action.strike_tick)
    assert light.fighters[0].stability == original
    assert not any(event.type == EventType.STABILITY for event in light.event_log)


def test_overextended_heavy_fall_does_not_undo_its_strike_or_grant_an_extra_action():
    state = arena()
    attacker, enemy = state.fighters
    attacker.stability = 1
    enemy.pos = (4, 0)
    commit_action(state, attacker.id, Choice(ActionKind.HEAVY, Zone.TORSO))
    strike_tick = attacker.action.strike_tick
    advance_to(state, strike_tick)
    assert attacker.grounded and attacker.action is not None
    assert attacker.action.kind == ActionKind.HEAVY
    assert sum(event.type == EventType.ACTION_START and event.actor == attacker.id
               for event in state.event_log) == 1
    advance_to(state, attacker.action.end_tick)
    assert attacker.grounded and attacker.action is None
    assert sum(event.type == EventType.ACTION_START and event.actor == attacker.id
               for event in state.event_log) == 1


def test_stability_zero_emits_one_causal_fall_and_cancels_unresolved_movement():
    from pirates.duel_system.engine import apply_pending_interrupts
    from pirates.duel_system.stability import apply_stability_loss

    state = arena()
    fighter = state.fighters[0]
    fighter.stability = 10
    commit_action(state, fighter.id, Choice(ActionKind.DASH, direction=(0, 1), tiles=2))
    action = fighter.action
    before_position = fighter.pos
    apply_stability_loss(state, fighter, "SHARP_FAST_TURN", 1000)
    assert fighter.stability == 0 and fighter.grounded and fighter.grounded_since == state.tick
    assert state.pending_interrupts == [fighter.id]
    apply_pending_interrupts(state)
    assert fighter.action is None and fighter.pos == before_position
    falls = [event for event in state.event_log if event.type == EventType.FALL]
    assert len(falls) == 1 and falls[0].cause_tags == ("STABILITY_ZERO", "SHARP_FAST_TURN")
    assert falls[0].deltas["source"] == "SHARP_FAST_TURN"
    assert action is not None
    assert apply_stability_loss(state, fighter, "SHARP_FAST_TURN", 1000) == 0
    assert len([event for event in state.event_log if event.type == EventType.FALL]) == 1


def test_sharp_fast_turn_is_deterministic_and_requires_successful_movement():
    state = arena()
    player, enemy = state.fighters
    enemy.pos = (8, 6)
    commit_action(state, player.id, Choice(ActionKind.RUN, direction=(0, 1), tiles=2))
    advance_to(state, player.action.end_tick)
    before = player.stability
    commit_action(state, player.id, Choice(ActionKind.RUN, direction=(1, 0), tiles=1))
    advance_to(state, player.action.path[0][0])
    assert player.stability < before
    turn = next(event for event in state.event_log if event.type == EventType.STABILITY)
    assert turn.cause_tags == ("SHARP_FAST_TURN",)
    assert turn.deltas["turn_degrees"] == 90

    blocked = arena(walkable={(0, 0), (0, 1), (1, 1)})
    mover, blocker = blocked.fighters
    blocker.pos = (8, 6)
    commit_action(blocked, mover.id, Choice(ActionKind.RUN, direction=(0, 1)))
    advance_to(blocked, mover.action.end_tick)
    before_blocked_turn = mover.stability
    commit_action(blocked, mover.id, Choice(ActionKind.RUN, direction=(1, 0)))
    blocker.pos = (1, 1)  # obstruct after commitment; failed movement is not a turn.
    advance_to(blocked, mover.action.path[0][0])
    assert mover.stability == before_blocked_turn
    assert not any(event.cause_tags == ("SHARP_FAST_TURN",) for event in blocked.event_log)


def test_balance_rating_applies_load_fatigue_pain_and_leg_wounds_once():
    state = arena()
    fighter = state.fighters[0]
    cfg = state.config["stability"]
    baseline = balance_rating(fighter, state.config, state.tick)
    fighter.fatigue = 20
    fatigue_rating = balance_rating(fighter, state.config, state.tick)
    assert baseline - fatigue_rating == pytest.approx(20 * cfg["balance_fatigue_penalty"])
    fighter.fatigue = 0
    fighter.equipment.weapon.weight += 2
    loaded_rating = balance_rating(fighter, state.config, state.tick)
    assert baseline - loaded_rating == pytest.approx(2 * cfg["balance_weight_penalty"])
    fighter.equipment.weapon.weight -= 2
    fighter.wounds.append(Wound(Zone.LEG, Severity.CUT, 0))
    injured_rating = balance_rating(fighter, state.config, state.tick)
    assert baseline - injured_rating == pytest.approx(cfg["leg_wound_penalty"]["CUT"])
    fighter.statuses.append(Status("leg_slow", speed_mult=.5))
    assert balance_rating(fighter, state.config, state.tick) == injured_rating


def test_grounded_legality_allows_ground_attack_cover_crawl_and_rise():
    state = arena()
    fighter = state.fighters[0]
    grounded(fighter)
    assert legality(state, fighter.id, Choice(ActionKind.LIGHT, Zone.TORSO)) == ""
    assert legality(state, fighter.id, Choice(ActionKind.GUARD)) == ""
    assert legality(state, fighter.id, Choice(ActionKind.CRAWL, direction=(0, 1), tiles=1)) == ""
    assert legality(state, fighter.id, Choice(ActionKind.RISE)) == ""
    standing = arena().fighters[0]
    state.fighters[0].grounded = False
    assert legality(state, standing.id, Choice(ActionKind.RISE)) == "Only a grounded fighter can rise"
    assert legality(state, standing.id, Choice(ActionKind.CRAWL, direction=(0, 1))) == "Only a grounded fighter can crawl"


def test_grounded_fighter_must_crawl_clear_of_standing_pile_partner_before_rising():
    state = arena()
    fighter, standing = state.fighters
    fighter.pos = standing.pos
    grounded(fighter)
    assert "Crawl clear" in legality(state, fighter.id, Choice(ActionKind.RISE))


def test_disarmed_grounded_fighter_can_still_use_cover():
    state = arena()
    fighter = state.fighters[0]
    grounded(fighter)
    fighter.disarmed = True
    assert legality(state, fighter.id, Choice(ActionKind.GUARD)) == ""
    commit_action(state, fighter.id, Choice(ActionKind.GUARD))
    assert fighter.action.kind == ActionKind.GUARD


def test_crawl_is_one_tile_collision_checked_and_leaves_fighter_grounded():
    state = arena(walkable={(0, 0), (0, 1), (1, 0)})
    player, enemy = state.fighters
    enemy.pos = (1, 0)
    grounded(player)
    assert "outside the arena floor" in legality(state, player.id,
                                                    Choice(ActionKind.CRAWL, direction=(-1, 0)))
    assert "occupies the path" in legality(state, player.id,
                                               Choice(ActionKind.CRAWL, direction=(1, 0)))
    commit_action(state, player.id, Choice(ActionKind.CRAWL, direction=(0, 1)))
    advance_to(state, player.action.end_tick)
    assert player.pos == (0, 1)
    assert player.grounded
    assert not any(event.type == EventType.RISE for event in state.event_log)


def test_rise_is_timed_exposed_and_restores_only_partial_stability_on_completion():
    state = arena()
    player = state.fighters[0]
    grounded(player)
    assert defense_snapshot(state, player).defense == "OPEN"
    commit_action(state, player.id, Choice(ActionKind.RISE))
    end_tick = player.action.end_tick
    advance_to(state, end_tick - 1)
    assert player.grounded and player.action is not None
    advance_to(state, end_tick)
    assert not player.grounded
    assert player.stability == state.config["stability"]["rise_stability"]
    assert stability_band(player, state.config) == "STUMBLING"
    assert player.action is None
    assert any(event.type == EventType.RISE for event in state.event_log)
    assert not any(event.type == EventType.HIT and event.actor == player.id for event in state.event_log)
    advance_one_tick(state)
    assert not player.grounded and player.stability == state.config["stability"]["rise_stability"]


def test_stability_recovers_only_during_committed_wait_and_never_while_grounded():
    state = arena()
    player = state.fighters[0]
    player.stability = 40
    commit_action(state, player.id, Choice(ActionKind.WAIT))
    advance_to(state, player.action.end_tick)
    assert player.stability > 40
    grounded(player, state.tick)
    before = player.stability
    advance_one_tick(state)
    assert player.stability == before
    assert "Grounded" in legality(state, player.id, Choice(ActionKind.WAIT))


def test_qualifying_hit_interrupts_rise_without_lifting_or_refunding():
    state = arena()
    attacker, defender = state.fighters
    attacker.strength = 20
    grounded(defender)
    commit_action(state, defender.id, Choice(ActionKind.RISE))
    paid_stamina = defender.stamina
    commit_action(state, attacker.id, Choice(ActionKind.LIGHT, Zone.TORSO))
    advance_to(state, attacker.action.strike_tick)
    assert defender.grounded
    assert defender.action is None
    assert defender.stamina < paid_stamina
    assert any(event.type == EventType.INTERRUPT and event.actor == defender.id for event in state.event_log)


def test_grounded_ai_candidates_are_legal_cover_crawl_or_rise_only():
    state = arena()
    fighter = state.fighters[1]
    grounded(fighter)
    view = observe(state, fighter.id)
    options = candidates(state, fighter, view)
    assert options
    assert {choice.kind for _, choice in options} <= {
        ActionKind.GUARD, ActionKind.CRAWL, ActionKind.RISE, ActionKind.LIGHT}
    assert all(not legality(state, fighter.id, choice) for _, choice in options)


def test_v3_snapshot_migration_preserves_actions_and_defaults_stable_state():
    state = arena(rng_seed=812)
    state.fighters[0].wounds = [Wound(Zone.LEG, Severity.CUT, 2)]
    commit_action(state, state.fighters[0].id,
                  Choice(ActionKind.DASH, direction=(0, 1), tiles=2))
    data = state.to_data()
    old_path = deepcopy(data["fighters"][0]["action"]["path"])
    old_end = data["fighters"][0]["action"]["end_tick"]
    data["schema_version"] = data["config"]["schema_version"] = 3
    data["config"].pop("stability")
    for fighter in data["fighters"]:
        for key in ("stability", "grounded", "grounded_since", "last_fast_direction", "last_fast_move_tick"):
            fighter.pop(key, None)
    restored = CombatState.from_data(data)
    assert restored.config["schema_version"] == 30
    assert restored.fighters[0].stability == 100
    assert not restored.fighters[0].grounded
    assert restored.fighters[0].action.path == [(tick, tuple(pos)) for tick, pos in old_path]
    assert restored.fighters[0].action.end_tick == old_end
    replay = CombatState.from_data(restored.to_data())
    while restored.tick < old_path[0][0]:
        advance_one_tick(restored)
        advance_one_tick(replay)
    assert replay.to_data() == restored.to_data()


def test_v3_session_migration_keeps_mid_action_and_deterministic_continuation():
    from pirates.duel_system.session import DuelSession

    session = DuelSession.new(seed=32, guide_mode="off")
    session.select(Choice(ActionKind.DASH, direction=(0, 1), tiles=2))
    assert session.confirm()
    data = session.to_data()
    old_path = deepcopy(data["state"]["fighters"][0]["action"]["path"])
    old_deadline = data["state"]["fighters"][0]["action"]["end_tick"]
    data["version"] = data["state"]["schema_version"] = data["state"]["config"]["schema_version"] = 3
    data["state"]["config"].pop("stability")
    data["notebook"]["version"] = 3
    for fighter in data["state"]["fighters"]:
        for key in ("stability", "grounded", "grounded_since", "last_fast_direction", "last_fast_move_tick"):
            fighter.pop(key, None)
    restored = DuelSession.from_data(data)
    replay = DuelSession.from_data(restored.to_data())
    assert restored.to_data()["version"] == 30
    assert restored.notebook.to_data()["version"] == 30
    assert restored.player.action.path == [(tick, tuple(pos)) for tick, pos in old_path]
    assert restored.player.action.end_tick == old_deadline
    restored.run_to_player()
    replay.run_to_player()
    assert replay.state.to_data() == restored.state.to_data()


def test_v3_world_journal_migrates_and_resumes_with_the_same_action_schedule(tmp_path):
    from pirates.duel_system.session import DuelSession
    from pirates.game import World

    session = DuelSession.new(seed=33, guide_mode="off")
    session.select(Choice(ActionKind.RUN, direction=(0, 1), tiles=2))
    assert session.confirm()
    data = session.to_data()
    old_path = deepcopy(data["state"]["fighters"][0]["action"]["path"])
    data["version"] = data["state"]["schema_version"] = data["state"]["config"]["schema_version"] = 3
    data["state"]["config"].pop("stability")
    data["notebook"]["version"] = 3
    for fighter in data["state"]["fighters"]:
        for key in ("stability", "grounded", "grounded_since", "last_fast_direction", "last_fast_move_tick"):
            fighter.pop(key, None)

    world = World()
    world.duel_journal = data
    save_path = tmp_path / "v3-duel-world.json"
    world.save(save_path)
    loaded = World.load(save_path)
    restored = DuelSession.from_data(loaded.duel_journal)
    replay = DuelSession.from_data(data)
    assert loaded.duel_journal["version"] == 30
    assert restored.player.action.path == [(tick, tuple(pos)) for tick, pos in old_path]
    restored.run_to_player()
    replay.run_to_player()
    assert restored.state.to_data() == replay.state.to_data()
