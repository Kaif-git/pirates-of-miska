"""Contracts for the replacement tick/intent system, not legacy hit percentages."""
import json
from dataclasses import asdict, replace
import heapq
import math

import pytest

from pirates.combat_core import (AI_RULES, Actor, BALANCE_RULES, Combat, Intent, MOVEMENT, PATTERNS, WEAPONS, WOUNDS, ARCHETYPES,
                                contact_quality, distance, movement_speed, severity,
                                duel_stats_for_actor, duel_weapon_for_actor,
                                step_cost, telegraph_audit)
from pirates.game import World
from pirates.combat import format_combat_time, validate_npc_profile
from pirates.combat_duel import ActionKind, DuelAction, apply_action, commit_action, legal_actions


def duel(weapon="sword", enemy_weapon="sword", enemy_speed=100):
    core = Combat({(x, y) for x in range(1, 13) for y in range(1, 13)}, 17)
    core.add(Actor("captain", "You", 5, 5, weapon=weapon, faction="player", facing=(1, 0)))
    core.add(Actor("enemy", "Enemy", 6, 5, weapon=enemy_weapon, speed=enemy_speed))
    return core


def finish_movement(core, actor):
    """Drive only this actor's queued tile events for direct AI unit tests."""
    while actor.movement_path:
        event = min(row for row in core.queue
                    if row[1] == 2 and row[2] == actor.id and row[3] == actor.token)
        core.queue.remove(event)
        heapq.heapify(core.queue)
        core.advance_time(event[0])
        core.movement_step(actor)


def test_default_closed_duel_is_save_safe_and_restores_exact_map(tmp_path):
    world = World()
    original = world.game_map
    pos = world.player.x, world.player.y, world.player.z
    ticks = world.clock.total_ticks
    assert world.combat.start(world)
    assert world.game_map is not original
    assert len(world.combat.enemies) == 1
    assert {a.id for a in world.entities()} == {"captain", "enemy_001"}
    assert not world.game_map.is_walkable(72, 26)
    with pytest.raises(ValueError):
        world.save(tmp_path / "unsafe.json")
    world.combat.act(world, "wait")
    assert world.clock.total_ticks == ticks
    world.combat.exit(world)
    assert world.game_map is original
    assert (world.player.x, world.player.y, world.player.z) == pos
    world.save(tmp_path / "safe.json")
    assert World.load(tmp_path / "safe.json").combat.progression == world.combat.progression


def test_boarding_combat_uses_constructed_ship_deck_and_restores_live_world():
    world = World()
    original_map = world.game_map
    original_pos = (world.player.x, world.player.y, world.player.z)
    wall = next(c for c in world.ship_model.components if c.kind == "wall"
                and world.hazards._outer_wall(world, c.x, c.y, c.z))
    world.hazards.breach(world, wall.x, wall.y, wall.z)
    entry = (wall.x + 69, wall.y + 12)
    assert world.combat.start_boarding(world, entry, count=2, seed=23)
    assert world.game_map is not original_map
    assert len(world.combat.enemies) == 2
    assert all(actor.pos in world.combat.engine.walkable for actor in world.combat.fighters.values())
    world.combat.exit(world)
    assert world.game_map is original_map
    assert (world.player.x, world.player.y, world.player.z) == original_pos
    assert wall.id not in {c.id for c in world.ship_model.components}


def test_boarding_combat_preserves_selected_upper_deck():
    world = World()
    x, y = 3, 2
    world.ship_model.place(x, y, 1, "floor")
    world.ship_model.place(x + 1, y, 1, "floor")
    door = world.ship_model.place(x + 1, y, 1, "door")
    world.ship_model.place(x, y, 0, "stair")
    world.player.x, world.player.y, world.player.z = x + 69, y + 12, 1
    world.set_active_deck(1)
    world.game_map.doors[y + 12, x + 1 + 69] = True
    original_map = world.game_map
    original = world.active_deck, world.player.x, world.player.y, world.player.z
    assert world.combat.start_boarding(world, (door.x + 69, door.y + 12, 1), count=1)
    assert world.active_deck == 1
    world.combat.exit(world)
    assert world.game_map is original_map
    assert (world.active_deck, world.player.x, world.player.y, world.player.z) == original


def test_boarding_victory_interns_raiders_only_in_a_secured_brig():
    world = World()
    cage = world.fixtures.get("brig_cage")
    at = cage.x + 69, cage.y + 12
    world.spaces.designate(world, *at, "brig")
    world.set_lockdown(True)
    wall = next(c for c in world.ship_model.components if c.kind == "wall"
                and world.hazards._outer_wall(world, c.x, c.y, c.z))
    world.hazards.breach(world, wall.x, wall.y, wall.z)
    assert world.combat.start_boarding(world, (wall.x + 69, wall.y + 12), count=2, seed=31)
    world.combat.enemies[0].hp = 0
    world.combat.enemies[1].surrendered = True
    world.combat.enemies[1].state = "surrendered"
    world.combat.exit(world)
    assert len(world.ship.captives) == 2
    assert world.ship.brig_ready(world)


def test_telegraphs_have_positive_reaction_time_and_valid_data():
    for pattern in PATTERNS.values():
        if pattern["damage"]:
            assert pattern["windup"] >= 101
        else:
            assert pattern.get("feint")
        assert pattern["recovery"] > 0
        assert pattern["damage"] > 0 or pattern.get("feint")
    for weapon in WEAPONS.values():
        assert all(p in PATTERNS for p in weapon["patterns"])
        if weapon["reload"] == 0:
            assert {"reach_tiles", "handling", "mass"} <= set(weapon["duel"])
        else:
            assert "duel" not in weapon
    for archetype in ARCHETYPES.values():
        assert archetype["weapon"] in WEAPONS
        assert archetype["hp"] == 20
        assert archetype["counter"] and archetype["tell"]
    audit = telegraph_audit()
    assert audit["passed"]
    assert all(row["passes"] for row in audit["patterns"].values())
    assert all(audit["weapon_references_valid"].values())


def test_live_actor_duel_projection_uses_current_body_resources_and_weapon_metadata():
    actor = Actor("fighter", "Fighter", 4, 5, weapon="sword", speed=125,
                  stamina=60, max_stamina=100, fatigue=30, balance=75,
                  strength=4, severed_parts=["right_arm"])
    actor.body_parts["torso"]["structure"] = 87
    actor.body_parts["left_arm"]["structure"] = 61
    stats = duel_stats_for_actor(actor)
    assert (stats.speed, stats.strength, stats.stamina, stats.max_stamina) == (125, 4, 60, 100)
    assert stats.fatigue == 30 and stats.balance == 75
    assert (stats.torso_structure, stats.left_arm_structure,
            stats.right_arm_structure, stats.severed_arms) == (87, 61, 100, ("right",))
    assert duel_weapon_for_actor(actor).reach_tiles == 1
    assert duel_weapon_for_actor(Actor("spear", "Spear", 1, 1, weapon="spear")) == \
        duel_weapon_for_actor(Actor("spear2", "Spear", 1, 1, weapon="spear"))
    with pytest.raises(ValueError, match="no close-duel weapon profile"):
        duel_weapon_for_actor(Actor("gun", "Gun", 1, 1, weapon="gun"))


def test_guided_duel_action_routes_through_deterministic_kernel_at_contact():
    world = World()
    arena = world.combat
    assert arena.start(world, seed=414)
    player, enemy = arena.fighters["captain"], arena.enemies[0]
    enemy.x, enemy.y = player.x + 1, player.y
    enemy.next_ready_tick = arena.engine.tick
    enemy.facing = (-1, 0)
    arena.selected_target = enemy.id
    assert not hasattr(arena.engine, "rng")
    assert arena.act(world, "duel_light")
    assert arena._duel_state is not None
    assert not hasattr(arena.engine, "rng")
    assert arena.engine.tick > 0
    assert arena.engine.replay[-1]["ruleset"] == "guided-duel-v1"
    assert arena.engine.replay[-1]["action"] == "light"
    assert player.stamina < player.max_stamina or enemy.stamina < enemy.max_stamina
    assert arena.engine.snapshot()["guided_duel"] == arena.engine.replay[-1]["duel_state"]
    json.dumps(arena.engine.snapshot(), sort_keys=True)
    labels = arena.floating_labels()
    assert any(label in {"Observing", "Waiting", "Recovering", "Preparing Light",
                         "Preparing Heavy", "Preparing Dash-strike", "Parrying",
                         "Dashing", "Down"}
               for aid, _, label, _ in labels if aid == enemy.id)
    player_guides = [label for aid, _, label, _ in labels if aid == player.id
                     and ("[Space]" in label or "Parry [G]" in label)]
    if max(abs(player.x - enemy.x), abs(player.y - enemy.y)) <= 1:
        assert player_guides
        incoming = arena._duel_state.right.intent
        if (incoming and incoming.action.kind in
                {ActionKind.LIGHT, ActionKind.HEAVY, ActionKind.DASH_STRIKE}
                and incoming.target_pos == player.pos):
            assert "Parry [G]" in player_guides[0]
        else:
            assert any("Light [Space]" in label or "Observing" in label
                       or "recovering" in label for label in player_guides)


def test_guided_duel_ordered_input_replays_identically_without_rng():
    def run_once():
        world = World()
        arena = world.combat
        assert arena.start(world, seed=416)
        player, enemy = arena.fighters["captain"], arena.enemies[0]
        enemy.x, enemy.y = player.x + 1, player.y
        enemy.next_ready_tick = arena.engine.tick
        enemy.facing = (-1, 0)
        arena.selected_target = enemy.id
        assert not hasattr(arena.engine, "rng")
        assert arena.act(world, "duel_light")
        assert not hasattr(arena.engine, "rng")
        return arena.engine.snapshot(), arena.engine.replay

    assert run_once() == run_once()


def test_each_guided_core_action_succeeds_with_rng_access_forbidden():
    scenarios = ("duel_light", "duel_heavy", "duel_parry", "duel_dash",
                 "duel_dash_strike")
    for index, command in enumerate(scenarios):
        world = World()
        arena = world.combat
        assert arena.start(world, seed=430 + index)
        player, enemy = arena.fighters["captain"], arena.enemies[0]
        arena.selected_target = enemy.id
        if command == "duel_dash_strike":
            approach = next(((dx, dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                             if all((player.x + dx * step, player.y + dy * step)
                                    in arena.engine.walkable
                                    for step in (1, 2, 3))), None)
            assert approach
            dx, dy = approach
            enemy.x, enemy.y = player.x + dx * 3, player.y + dy * 3
            target = None
        else:
            adjacent = next(((player.x + dx, player.y + dy, dx, dy)
                             for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                             if (player.x + dx, player.y + dy) in arena.engine.walkable
                             and not arena.engine.at((player.x + dx, player.y + dy))), None)
            assert adjacent
            enemy.x, enemy.y, _, _ = adjacent
            target = None
            if command == "duel_dash":
                dash = next(((dx, dy, 1) for dx, dy in ((0, -1), (1, 0),
                                                          (0, 1), (-1, 0))
                             if (player.x + dx, player.y + dy) in arena.engine.walkable
                             and not arena.engine.at((player.x + dx, player.y + dy))), None)
                assert dash
                target = dash
        enemy.next_ready_tick = arena.engine.tick
        assert not hasattr(arena.engine, "rng")
        assert arena.act(world, command, target), command


def test_guided_duel_dump_restores_validated_kernel_and_replay_state(tmp_path):
    def setup(seed):
        world = World()
        arena = world.combat
        assert arena.start(world, seed=seed)
        player, enemy = arena.fighters["captain"], arena.enemies[0]
        enemy.x, enemy.y = player.x + 1, player.y
        enemy.next_ready_tick = arena.engine.tick
        enemy.facing = (-1, 0)
        arena.selected_target = enemy.id
        return world, arena

    source_world, source = setup(444)
    assert source.act(source_world, "duel_light")
    path = tmp_path / "guided-combat.json"
    source.dump(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["guided_duel_state_version"] == 1
    assert payload["guided_duel_actors"] == list(source._duel_actor_ids)

    restored_world, restored = setup(444)
    assert restored.restore_guided_duel_snapshot(payload) == source._duel_state
    normalized = lambda replay: json.loads(json.dumps(replay))
    assert normalized(restored.engine.replay) == normalized(source.engine.replay)
    next_action = next(action for action in legal_actions(source._duel_state)
                       if action.kind != ActionKind.WAIT)
    assert apply_action(source._duel_state, next_action) == \
        apply_action(restored._duel_state, next_action)

    bad_version = dict(payload, guided_duel_state_version=999)
    with pytest.raises(ValueError, match="unsupported"):
        restored.restore_guided_duel_snapshot(bad_version)
    bad_tail = json.loads(json.dumps(payload))
    bad_tail["replay"][-1]["duel_state"]["tick"] += 1
    with pytest.raises(ValueError, match="replay tail"):
        restored.restore_guided_duel_snapshot(bad_tail)


def test_guided_duel_rejects_invalid_dash_without_time_or_rng():
    world = World()
    arena = world.combat
    assert arena.start(world, seed=415)
    player, enemy = arena.fighters["captain"], arena.enemies[0]
    enemy.x, enemy.y = player.x + 1, player.y
    enemy.next_ready_tick = arena.engine.tick
    arena.selected_target = enemy.id
    assert not hasattr(arena.engine, "rng")
    tick = arena.engine.tick
    assert not arena.act(world, "duel_dash", (0, 0, 2))
    assert arena.engine.tick == tick
    assert not hasattr(arena.engine, "rng")
    assert not arena.engine.replay


def test_contact_guide_uses_committed_timing_and_does_not_claim_parry_force():
    world = World()
    arena = world.combat
    assert arena.start(world, seed=417)
    player, enemy = arena.fighters["captain"], arena.enemies[0]
    enemy.x, enemy.y = player.x + 1, player.y
    enemy.next_ready_tick = arena.engine.tick
    arena.selected_target = enemy.id
    state = arena._guided_duel_state(enemy)
    state = commit_action(replace(state, to_act=1), DuelAction(ActionKind.LIGHT))
    arena._duel_state = state
    labels = arena.floating_labels()
    enemy_texts = [label for aid, _, label, _ in labels if aid == enemy.id]
    player_texts = [label for aid, _, label, _ in labels if aid == player.id]
    assert any(text.startswith("Preparing Light ·") for text in enemy_texts)
    assert any("Parry [G] fits window" in text and "force uncertain" in text
               for text in player_texts)
    assert any("Dash [D+dir] clears before contact" in text for text in player_texts)

    committed = state.right.intent
    assert committed is not None
    late_intent = replace(committed, impact_tick=state.tick + 5)
    arena._duel_state = replace(state, right=replace(state.right, intent=late_intent))
    late_text = " ".join(label for aid, _, label, _ in arena.floating_labels()
                          if aid == player.id)
    assert "Parry [G] starts too late" in late_text
    assert "Dash cannot clear before contact" in late_text


def test_guided_duel_does_not_execute_player_input_on_npc_turn():
    world = World()
    arena = world.combat
    assert arena.start(world, seed=418)
    player, enemy = arena.fighters["captain"], arena.enemies[0]
    enemy.x, enemy.y = player.x + 1, player.y
    enemy.next_ready_tick = arena.engine.tick
    arena.selected_target = enemy.id
    state = arena._guided_duel_state(enemy)
    arena._duel_state = replace(state, to_act=1)
    tick_before = arena.engine.tick
    assert not arena.act(world, "duel_light")
    assert not hasattr(arena.engine, "rng")
    assert arena.engine.tick == tick_before
    assert not arena.engine.replay


def test_live_parry_feedback_explains_capacity_counter_window_and_resource_cost():
    world = World()
    arena = world.combat
    assert arena.start(world, seed=419)
    player, enemy = arena.fighters["captain"], arena.enemies[0]
    enemy.x, enemy.y = player.x + 1, player.y
    enemy.next_ready_tick = arena.engine.tick
    arena.selected_target = enemy.id
    state = arena._guided_duel_state(enemy)
    state = commit_action(replace(state, to_act=1), DuelAction(ActionKind.LIGHT))
    arena._duel_state = state
    assert not hasattr(arena.engine, "rng")
    assert arena.act(world, "duel_parry")
    assert not hasattr(arena.engine, "rng")
    parried = next(item for item in arena.engine.feedback if item["outcome"] == "parried")
    assert "within parry capacity" in parried["text"]
    assert parried["consequences"]["force"] <= parried["consequences"]["parry_capacity"]
    cost = next(item for item in arena.engine.feedback if item["outcome"] == "action_cost")
    assert cost["consequences"]["stamina_cost"] == 20
    assert cost["consequences"]["fatigue_added"] == 2
    assert "attack control" in cost["text"]
    assert cost["consequences"]["fatigue_control_penalty"] == 0
    assert any(item["outcome"] == "counter_window" for item in arena.engine.feedback)
    assert "Fatigue" in " ".join(arena.hud_lines(world)[1:2])


def test_live_dash_strike_feedback_records_cost_contact_and_recovery_risk():
    world = World()
    arena = world.combat
    assert arena.start(world, seed=420)
    player, enemy = arena.fighters["captain"], arena.enemies[0]
    enemy.x, enemy.y = player.x + 3, player.y
    enemy.balance = 0
    enemy.body_parts["left_arm"]["structure"] = 0
    enemy.body_parts["right_arm"]["structure"] = 0
    enemy.next_ready_tick = arena.engine.tick
    arena.selected_target = enemy.id
    assert arena.guided_duel_available
    assert arena.act(world, "duel_dash_strike")
    assert arena.engine.replay[-1]["action"] == "dash_strike"
    cost = next(item for item in arena.engine.feedback if item["outcome"] == "action_cost")
    assert cost["consequences"]["stamina_cost"] == 60
    assert cost["consequences"]["fatigue_added"] == 12
    assert cost["consequences"]["fatigue_control_penalty"] == 2
    assert "attack control" in cost["text"]
    outcomes = [item for item in arena.engine.feedback
                if item["type"] in {"hit", "parried", "overpowered", "precision-miss",
                                    "committed-lane-miss"}]
    assert outcomes
    assert outcomes[0]["outcome"] == "committed-lane-miss"
    assert "exposed recovery" in outcomes[0]["text"]
    assert outcomes[0]["consequences"]["recovery_ticks"] > 0


def test_live_feedback_explains_first_contact_before_enemy_commitment():
    world = World()
    arena = world.combat
    assert arena.start(world, seed=421)
    player, enemy = arena.fighters["captain"], arena.enemies[0]
    enemy.x, enemy.y = player.x + 1, player.y
    enemy.next_ready_tick = arena.engine.tick
    arena.selected_target = enemy.id
    state = arena._guided_duel_state(enemy)
    state = commit_action(replace(state, to_act=1), DuelAction(ActionKind.HEAVY))
    arena._duel_state = replace(state, to_act=0)
    assert arena.act(world, "duel_light")
    landed = next(item for item in arena.engine.feedback
                  if item["source_id"] == player.id and item["outcome"] == "hit")
    assert "first contact landed before opposing heavy" in landed["text"].lower()
    assert "due in" in landed["text"].lower()


def test_combat_action_bar_matches_guided_group_and_ranged_contexts():
    import play

    world = World()
    arena = world.combat
    assert arena.start(world, seed=422)
    player, enemy = arena.fighters["captain"], arena.target()
    enemy.x, enemy.y = player.x + 1, player.y
    enemy.next_ready_tick = arena.engine.tick
    assert arena.guided_duel_available
    title, actions = play.combat_action_bar(arena)
    assert title.startswith("GUIDED DUEL")
    assert tuple(label for label, _, _ in actions) == (
        "Light", "Heavy", "Parry", "Dash", "Dash-strike")

    arena.exit(world)
    arena.count = 2
    assert arena.start(world, seed=423)
    title, actions = play.combat_action_bar(arena)
    assert title.startswith("GROUP / MELEE")
    assert tuple(label for label, _, _ in actions) == (
        "Attack", "Heavy", "Guard", "Dash", "More")
    assert play.legacy_action_for_duel_key("combat_duel_light") == "attack"
    assert play.legacy_action_for_duel_key("combat_duel_heavy") == "heavy"
    assert play.legacy_action_for_duel_key("combat_duel_parry") == "guard"

    player = arena.fighters["captain"]
    player.weapon = "gun"
    player.ammo = 1
    player.spare_shots = 2
    title, actions = play.combat_action_bar(arena)
    assert title.startswith("RANGED")
    assert tuple(label for label, _, _ in actions) == (
        "Aim", "Fire", "Reload", "Guard", "More")
    old_mode = play.COMBAT_INPUT_MODE
    play.COMBAT_INPUT_MODE = True
    try:
        assert play._pirates_world_action_for_key(play.tcod.event.KeySym.R) == ("combat_aim",)
        assert play._pirates_world_action_for_key(play.tcod.event.KeySym.T) == ("combat_reload",)
    finally:
        play.COMBAT_INPUT_MODE = old_mode
    arena.exit(world)


def test_intent_resolution_uses_original_tiles_not_original_target():
    core = duel()
    actor, player = core.actors["enemy"], core.actors["captain"]
    actor.next_ready_tick = 0
    assert not core.act("enemy", "sweep", player.pos)
    original_tiles = actor.intent.target_tiles
    assert player.pos in original_tiles
    player.x, player.y = 3, 3
    core.advance_time(150)
    core.resolve(actor)
    assert player.hp == 20
    assert core.metrics["whiffs"] == 1
    assert tuple(core.audit[0]["target_tiles"]) == original_tiles


def test_telegraphed_severe_hits_apply_deterministic_wounds():
    for seed in range(10):
        core = duel()
        e = core.actors["enemy"]
        e.next_ready_tick = 0
        core.act("enemy", "sweep", (5, 5))
        core.advance_time(150)
        core.resolve(e)
        assert not hasattr(core, "rng")
        assert core.actors["captain"].blood < core.actors["captain"].max_hp
        assert core.actors["captain"].shock > 0
        assert core.audit and core.audit[0]["kind"] == "sweep"


def test_fight_metrics_track_damage_punishes_kill_credit_and_time_to_kill():
    core = duel()
    player, enemy = core.actors["captain"], core.actors["enemy"]
    enemy.hp = 5
    assert core.command("target_part", "head")
    assert core.act("captain", "thrust", enemy.pos) == ""
    core.advance_time(player.intent.resolves_at_tick)
    core.resolve(player)
    report = core.report(flawless_possible=True)
    assert report["damage_by_actor"]["captain"] == 5
    assert report["damage_taken_by_actor"]["enemy"] == 5
    assert report["actor_metrics"]["captain"]["kills"] == 1
    assert report["time_to_kill_ticks"] == 101
    assert report["flawless"] and report["flawless_possible"] is True
    assert report["kill_events"][0]["killer_id"] == "captain"


def test_varied_ai_match_metrics_are_seed_reproducible_and_attribute_damage():
    from tools.combat_headless import ai_match
    first = ai_match("duelist", "brawler", seed=91)
    second = ai_match("duelist", "brawler", seed=91)
    assert first == second
    assert set(first["actor_metrics"]) == {"a", "b"}
    assert all("damage_dealt" in row and "punishes" in row for row in first["actor_metrics"].values())
    assert first["time_to_kill_ticks"] is not None or first["winner"] is None


def test_parry_requires_facing_and_staggers_attacker():
    core = duel()
    enemy, player = core.actors["enemy"], core.actors["captain"]
    enemy.next_ready_tick = 0
    core.act("enemy", "sweep", player.pos)
    core.act("captain", "brace", enemy.pos)
    core.advance_time(150)
    core.resolve(enemy)
    assert player.hp == 20
    assert enemy.state == "staggered"
    assert core.metrics["parries"] == 1


def test_parry_wrong_facing_does_not_negate_hit():
    core = duel()
    enemy, player = core.actors["enemy"], core.actors["captain"]
    enemy.next_ready_tick = 0
    core.act("enemy", "sweep", player.pos)
    core.act("captain", "brace", (4, 5))
    core.advance_time(150)
    core.resolve(enemy)
    assert player.hp < player.max_hp
    assert any("back" in row for row in core.log)


def test_shove_cancels_windup_pushes_and_breaks_shield():
    core = duel()
    enemy = core.actors["enemy"]
    enemy.shield = True
    enemy.next_ready_tick = 0
    core.act("enemy", "sweep", (5, 5))
    core.act("captain", "shove", enemy.pos)
    core.advance_time(core.actors["captain"].intent.resolves_at_tick)
    core.resolve(core.actors["captain"])
    assert enemy.intent is None
    assert enemy.pos == (7, 5)
    assert enemy.shield_broken_until > core.tick


def test_speed_changes_action_deadlines_not_windup():
    core = duel()
    player = core.actors["captain"]
    player.speed = 200
    core.act("captain", "sweep", (6, 5))
    assert player.intent.resolves_at_tick == 150
    assert player.next_ready_tick == 350  # 150 windup + 150 recovery + 50 energy time


def test_player_and_npc_share_same_action_definition():
    core = duel()
    core.actors["enemy"].next_ready_tick = 0
    core.act("captain", "thrust", (6, 5))
    core.act("enemy", "thrust", (5, 5))
    a, b = core.actors.values()
    assert a.intent.kind == b.intent.kind == "thrust"
    assert a.intent.target_part == b.intent.target_part == "torso"
    assert b.intent.resolves_at_tick > a.intent.resolves_at_tick
    assert b.next_ready_tick > a.next_ready_tick  # NPC grants the player time to finish a real escape step


def test_invalid_actions_do_not_spend_time_or_change_replay():
    core = duel()
    core.actors["enemy"].x = 11
    assert not core.command("thrust", (11, 5))
    assert core.tick == 0 and core.replay == []
    assert not core.command("move", (5, 5))
    assert core.tick == 0


def test_directional_patterns_rotate_and_spear_has_close_weakness():
    core = duel(weapon="spear")
    player = core.actors["captain"]
    assert core.tiles(player, "spear_thrust", (7, 5)) == ((7, 5), (8, 5), (9, 5))
    assert core.tiles(player, "spear_thrust", (5, 7)) == ((5, 7), (5, 8), (5, 9))
    assert core.blocker(player, "spear_thrust", (6, 5))


def test_diagonal_directional_attacks_target_and_telegraph_the_same_diagonal_tiles():
    core = duel()
    player, enemy = core.actors["captain"], core.actors["enemy"]
    player.x, player.y = 5, 5
    enemy.x, enemy.y = 6, 6

    tiles = core.tiles(player, "thrust", enemy.pos)
    assert enemy.pos in tiles
    assert core.blocker(player, "thrust", enemy.pos) == ""
    assert core.act("captain", "thrust", enemy.pos) == ""
    assert player.intent.target_tiles == tiles
    assert player.facing == (1, 1)

    core.advance_time(player.intent.resolves_at_tick)
    core.resolve(player)

    assert enemy.hp < enemy.max_hp
    assert "back hit" in core.log[-1]
    assert not enemy.unconscious


def test_diagonal_sweep_keeps_committed_body_part_for_each_covered_target():
    core = Combat({(x, y) for x in range(1, 13) for y in range(1, 13)})
    attacker = Actor("captain", "You", 5, 5, faction="player", weapon="sword")
    first = Actor("first", "First", 6, 6)
    second = Actor("second", "Second", 7, 5)
    core.add(attacker)
    core.add(first)
    core.add(second)
    assert core.command("target_part", "head")

    tiles = core.tiles(attacker, "sweep", first.pos)
    assert first.pos in tiles and second.pos in tiles
    assert core.act("captain", "sweep", first.pos) == ""
    intent = attacker.intent
    attacker.target_part = "torso"
    core.advance_time(intent.resolves_at_tick)
    core.resolve(attacker)

    assert first.hp < first.max_hp and second.hp < second.max_hp
    assert first.injuries.get("head") is not None
    assert second.injuries.get("head") is not None


def test_diagonal_attack_respects_intervening_cover_and_eight_way_facing():
    core = duel()
    player, enemy = core.actors["captain"], core.actors["enemy"]
    player.x, player.y = 5, 5
    enemy.x, enemy.y = 7, 7
    tiles = core.tiles(player, "thrust", enemy.pos)
    assert (6, 6) in tiles and enemy.pos in tiles
    assert core.blocker(player, "thrust", enemy.pos) == ""
    core.walkable.remove((6, 6))
    assert core.blocker(player, "thrust", enemy.pos) == "Out of range/cover"

    core.walkable.add((6, 6))
    player.facing = (-1, -1)
    assert core.act("captain", "thrust", enemy.pos) == ""
    assert player.intent.target_tiles == tiles
    assert player.facing == (1, 1)


def test_projectile_line_stops_at_pillar_and_does_not_track():
    core = duel(weapon="gun")
    player = core.actors["captain"]
    tiles = core.tiles(player, "pistol_shot", (9, 5))
    assert (9, 5) in tiles
    core.walkable.remove((7, 5))
    assert (9, 5) not in core.tiles(player, "pistol_shot", (9, 5))


def test_reload_takes_time_and_consumes_finite_ammo():
    core = duel(weapon="gun")
    player = core.actors["captain"]
    player.ammo = 0
    core.command("reload")
    assert core.tick == 150
    assert player.ammo == 1 and player.spare_shots == 5
    player.ammo = 0
    player.spare_shots = 0
    assert not core.command("reload")
    assert core.tick == 150


def test_same_seed_and_inputs_produce_identical_replay_state():
    first, second = duel(), duel()
    inputs = [("wait", None), ("move", (5, 4)), ("wait", None), ("brace", (6, 5))]
    for action, target in inputs:
        first.command(action, target)
        second.command(action, target)
    assert first.snapshot() == second.snapshot()
    assert first.replay == second.replay


def test_movement_preview_is_pure_and_reports_arrival_cost_and_risk():
    core = duel()
    player = core.actors["captain"]
    before = core.snapshot()
    replay = list(core.replay)
    assert not hasattr(core, "rng")

    preview = core.preview("move", (5, 4))

    assert preview["valid"]
    assert preview["path"] == ((5, 4),)
    assert preview["destination"] == (5, 4)
    assert preview["ticks"] == preview["step_ticks"][0]
    assert preview["stamina_cost"] == 0
    assert preview["enemy_acts_first"]
    assert core.snapshot() == before
    assert core.replay == replay
    assert not hasattr(core, "rng")
    assert player.pos == (5, 5)


def test_step_cost_distinguishes_forward_sidestep_backstep_and_flee():
    actor = Actor("runner", "Runner", 5, 5, facing=(1, 0))
    forward = step_cost(actor, (1, 0))[0]
    side = step_cost(actor, (0, 1))[0]
    back = step_cost(actor, (-1, 0), target=(7, 5))[0]
    flee = step_cost(actor, (-1, 0))[0]
    assert (forward, side, back, flee) == (90, 110, 150, 100)


def test_spear_lunge_stops_at_the_first_occupied_line_tile():
    core = Combat({(x, y) for x in range(1, 13) for y in range(1, 13)})
    attacker = Actor("captain", "You", 2, 5, weapon="spear", faction="player")
    first = Actor("first", "First", 4, 5)
    second = Actor("second", "Second", 6, 5)
    core.add(attacker)
    core.add(first)
    core.add(second)
    tiles = core.tiles(attacker, "spear_thrust", first.pos)
    assert first.pos in tiles
    assert second.pos not in tiles


def test_status_bleeding_and_bandage_are_symmetric():
    core = duel()
    core.actors["enemy"].x, core.actors["enemy"].y = 12, 12
    a = core.actors["captain"]
    a.statuses["bleeding"] = 400
    core.command("wait")
    core.command("wait")
    assert a.hp == 19
    core.command("bandage")
    assert "bleeding" not in a.statuses


def test_wound_foundation_initializes_blood_shock_parts_and_legacy_hp_sync():
    core = duel()
    actor = core.actors["captain"]
    assert actor.blood == actor.hp == actor.max_hp == 20
    assert actor.shock == 0
    assert set(actor.body_parts) == {
        "head", "torso", "left_arm", "right_arm", "left_leg", "right_leg"}
    assert all(part["structure"] == WOUNDS["part_structure"]
               for part in actor.body_parts.values())
    assert core.apply_damage(None, actor, 3) == 3
    assert actor.blood == actor.hp == 17
    actor.hp = 5  # legacy save/consumer write is reconciled on the next wound update
    assert core.apply_damage(None, actor, 1) == 1
    assert actor.blood == actor.hp == 4


@pytest.mark.parametrize("damage_type,quality,part,armor,heavy,expected", [
    ("edge", "clean", "head", 0, False, 3),
    ("edge", "clean", "torso", 0, False, 2),
    ("point", "graze", "torso", 0, False, 0),
    ("blunt", "clean", "left_arm", 0, False, 1),
    ("edge", "clean", "head", 1, False, 2),
    ("edge", "clean", "torso", 2, True, 1),
])
def test_data_driven_wound_severity(damage_type, quality, part, armor, heavy, expected):
    assert severity(damage_type, quality, part, armor, heavy) == expected


def test_contact_quality_uses_back_facing_edge_and_brace_state():
    attacker = Actor("a", "Attacker", 5, 5, facing=(1, 0))
    defender = Actor("d", "Defender", 6, 5, facing=(-1, 0))
    intent = Intent("a", "thrust", ((6, 5), (7, 5)), 0, 101,
                    attacker_origin=attacker.pos, target_origin=defender.pos)
    assert contact_quality(intent, attacker, defender) == "clean"
    defender.x = 7
    assert contact_quality(intent, attacker, defender) == "graze"
    defender.x = 6
    defender.facing = (1, 0)
    assert contact_quality(intent, attacker, defender) == "back"
    defender.facing = (-1, 0)
    defender.braced = True
    assert contact_quality(intent, attacker, defender) == "glancing"


def test_clean_heavy_decisive_wounds_use_strength_balance_thresholds():
    results = []
    for strength in (7, 8):
        core = duel()
        player, enemy = core.actors["captain"], core.actors["enemy"]
        player.strength = strength
        enemy.max_hp = enemy.hp = 100
        enemy.blood = 100.0
        enemy.facing = (-1, 0)
        assert core.act("captain", "sweep", enemy.pos) == ""
        core.advance_time(player.intent.resolves_at_tick)
        core.resolve(player)
        results.append((enemy.hp, enemy.unconscious, enemy.blood))
        assert not hasattr(core, "rng")
    assert results[0][0] > 0 and not results[0][1]
    assert results[1][0] > 0 and results[1][1]


def test_fractional_bleeding_and_stat_thresholded_aim_drift_are_deterministic():
    core = duel()
    victim = core.actors["captain"]
    victim.bleed_rate = 0.6
    victim.statuses["bleeding"] = 500
    core.advance_time(100)
    assert victim.blood == pytest.approx(19.4)
    core.advance_time(200)
    assert victim.blood == pytest.approx(18.8)
    assert victim.hp == 19

    outcomes = []
    for _ in range(2):
        match = duel()
        attacker, target = match.actors["captain"], match.actors["enemy"]
        attacker.balance = 0
        attacker.target_part = "head"
        assert match.act("captain", "thrust", target.pos) == ""
        match.advance_time(attacker.intent.resolves_at_tick)
        match.resolve(attacker)
        outcomes.append((target.injuries, target.body_parts, target.blood, target.shock))
    assert outcomes[0] == outcomes[1]
    assert (outcomes[0][1]["left_arm"]["damage"] > 0
            or outcomes[0][1]["right_arm"]["damage"] > 0)


@pytest.mark.parametrize("weapon", ["sword", "brawl", "ram"])
def test_ai_landed_hits_use_contact_wounds_not_legacy_hp_damage(weapon):
    core = Combat({(x, y) for x in range(1, 13) for y in range(1, 13)}, seed=23)
    player = Actor("captain", "You", 5, 5, faction="player", facing=(1, 0))
    enemy = Actor("enemy", "Raider", 6, 5, weapon=weapon, personality="berserker")
    core.add(player)
    core.add(enemy)
    enemy.next_ready_tick = core.tick
    core.ai(enemy)
    assert enemy.intent
    core.advance_time(enemy.intent.resolves_at_tick)
    core.resolve(enemy)
    assert player.blood < player.max_hp
    assert player.shock > 0
    assert player.injuries


def test_ai_uses_deterministic_timed_dash_to_escape_locked_attack_before_impact():
    core = Combat({(x, y) for x in range(1, 13) for y in range(1, 13)}, seed=5)
    player = Actor("captain", "You", 5, 5, faction="player")
    raider = Actor("enemy", "Raider", 6, 5, personality="berserker")
    core.add(player)
    core.add(raider)
    impact = 90
    player.intent = Intent("captain", "thrust", ((6, 5), (7, 5)), 0, impact,
                           attacker_origin=player.pos, target_origin=raider.pos)
    player.state = "windup"
    player.next_ready_tick = 300
    heapq.heappush(core.queue, (impact, 0, player.id, player.token))
    raider.next_ready_tick = core.tick
    raider.state = "ready"

    core.ai(raider)

    assert raider.movement_mode == "dash"
    assert raider.movement_path[0] not in player.intent.target_tiles
    assert raider.movement_step_ticks < impact
    core.run_until_player()
    assert raider.pos != (6, 5)
    assert raider.hp == raider.max_hp


def test_winded_ai_does_not_choose_unsustainable_run_or_dash_chase():
    core = Combat({(x, y) for x in range(1, 13) for y in range(1, 13)}, seed=7)
    core.add(Actor("captain", "You", 2, 2, faction="player"))
    enemy = Actor("enemy", "Winded Raider", 10, 10, personality="berserker",
                  stamina=0, fatigue=90)
    core.add(enemy)
    enemy.next_ready_tick = core.tick
    enemy.state = "ready"
    core.ai(enemy)
    assert enemy.movement_mode not in {"run", "dash"}


def test_ai_tracks_repeated_player_retreats_and_bracing_as_habits():
    # Repeated cardinal movement adds a run cutoff option even inside ordinary
    # chase range; repeated braces make a legal feint candidate memorable.
    core = Combat({(x, y) for x in range(1, 13) for y in range(1, 13)}, seed=17)
    player = Actor("captain", "You", 5, 5, faction="player")
    enemy = Actor("enemy", "Patient", 8, 5, personality="patient")
    core.add(player)
    core.add(enemy)
    enemy.next_ready_tick = core.tick
    enemy.state = "ready"
    core.ai(enemy)
    assert not any(key.startswith("run:") for key in enemy.utilities)

    enemy.intent = None
    enemy.state = "ready"
    enemy.next_ready_tick = core.tick
    core.replay.extend({"tick": i, "action": "move", "target": (5, 5 + i),
                        "origin": (5, 4 + i)} for i in range(3))
    core.ai(enemy)
    assert any(key.startswith("run:") for key in enemy.utilities)

    # Keep the target at the spear/sword boundary and record repeated guards.
    enemy.intent = None
    enemy.state = "ready"
    enemy.next_ready_tick = core.tick
    player.x, player.y = 6, 5
    player.braced = False
    core.replay.extend({"tick": 20 + i, "action": "brace", "target": enemy.pos,
                        "origin": player.pos} for i in range(2))
    core.ai(enemy)
    assert any(key.startswith("feint:") for key in enemy.utilities)


def test_legacy_ai_tie_breaking_is_seed_independent_and_replay_deterministic():
    def choice(seed):
        core = Combat({(x, y) for x in range(1, 13) for y in range(1, 13)}, seed=seed)
        core.add(Actor("captain", "You", 5, 5, faction="player"))
        enemy = Actor("enemy", "Raider", 8, 7, personality="patient")
        core.add(enemy)
        enemy.next_ready_tick = core.tick
        enemy.state = "ready"
        core.ai(enemy)
        return enemy.movement_path, enemy.intent.kind if enemy.intent else None

    assert choice(0) == choice(0)
    assert len({choice(seed) for seed in range(10)}) == 1


def test_armor_reduces_severity_instead_of_scaling_hp():
    core = duel()
    enemy, player = core.actors["enemy"], core.actors["captain"]
    player.armor = 2
    enemy.next_ready_tick = 0
    core.act("enemy", "sweep", player.pos)
    core.advance_time(150)
    core.resolve(enemy)
    assert player.hp == 17


def test_hud_reads_real_intent_and_loadout_and_look_is_free():
    world = World()
    arena = world.combat
    arena.start(world)
    arena.act(world, "wait")
    before = arena.engine.tick
    text = "\n".join(arena.hud_lines(world))
    assert "Cutlass" in text and "spare shots 6" in text
    assert "Next:" in text and "ready in" in text
    arena.look(arena.enemies[0].id)
    assert arena.engine.tick == before
    arena.exit(world)


def test_same_seed_reset_restores_all_transient_state():
    world = World()
    arena = world.combat
    arena.start(world, seed=44)
    initial = arena.engine.snapshot()
    arena.act(world, "wait")
    arena.reset(world)
    assert arena.engine.snapshot() == initial


def test_dump_contains_replay_initial_state_and_metrics(tmp_path):
    world = World()
    world.combat.start(world)
    world.combat.act(world, "wait")
    path = tmp_path / "replay.json"
    world.combat.dump(path)
    data = json.loads(path.read_text())
    assert data["combat_ruleset_version"] == 3
    assert data["report"]["inputs"]
    assert data["initial"] and data["walkable"]
    assert {"damage_by_actor", "damage_taken_by_actor", "time_to_kill_ticks",
            "actor_metrics", "kill_events", "flawless_possible"} <= set(data["report"])
    assert data["environment"]["doors"] and data["environment"]["hazards"]


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_every_archetype_can_spawn_and_telegraphs_before_resolution(archetype):
    world = World()
    arena = world.combat
    arena.archetype = archetype
    arena.start(world)
    for _ in range(12):
        if arena.finished:
            break
        arena.act(world, "wait")
    for intent in arena.engine.audit:
        pattern = PATTERNS[intent["kind"]]
        assert intent["resolves_at_tick"] - intent["start_tick"] >= (101 if pattern["damage"] else 100)
        assert intent["target_tiles"]
    assert arena.fighters["captain"].max_hp == 20


def test_player_status_distinguishes_alive_unconscious_dead_and_victory():
    world = World()
    arena = world.combat
    arena.start(world, seed=422)
    player = arena.fighters["captain"]
    assert "ALIVE" in arena.player_status()[0]
    player.unconscious = True
    assert "UNCONSCIOUS" in arena.player_status()[0]
    assert "Backspace" in arena.player_status()[1]
    assert not arena.act(world, "move", (1, 0))
    assert "UNCONSCIOUS" in arena.log[-1]
    player.hp = 0
    assert "DEAD" in arena.player_status()[0]
    player.hp = player.max_hp
    player.unconscious = False
    for enemy in arena.enemies:
        enemy.hp = 0
    assert "VICTORY" in arena.player_status()[0]
    arena.exit(world)


def test_player_status_explains_guided_walking_lock_and_debug_pause():
    world = World()
    arena = world.combat
    arena.start(world, seed=422)
    player, enemy = arena.fighters["captain"], arena.target()
    enemy.x, enemy.y = player.x + 1, player.y
    enemy.next_ready_tick = arena.engine.tick
    state = arena._guided_duel_state(enemy)
    assert state is not None
    assert arena.guided_duel_engaged
    assert "Walking is locked" in arena.player_status()[1]
    assert not arena.act(world, "move", (1, 0))
    assert "D then a direction" in arena.log[-1]
    arena.debug_step = True
    player.next_ready_tick = arena.engine.tick + 100
    assert "RECOVERING" in arena.player_status()[0]
    assert "N advances time" in arena.player_status()[1]
    arena.exit(world)


def test_contextual_keys_preserve_normal_game_controls():
    import play
    from tcod.event import KeySym
    play.COMBAT_INPUT_MODE = False
    assert play._pirates_world_action_for_key(KeySym.G) == ("get",)
    play.COMBAT_INPUT_MODE = True
    assert play._pirates_world_action_for_key(KeySym.G) == ("combat_duel_parry",)
    assert play._pirates_world_action_for_key(KeySym.SPACE) == ("combat_duel_light",)
    assert play._pirates_world_action_for_key(KeySym.F) == ("combat_duel_heavy",)
    assert play._pirates_world_action_for_key(KeySym.J) == ("combat_duel_dash_strike",)
    assert len(play.COMBAT_ACTION_BUTTONS) == 5
    assert {item[2] for item in play.COMBAT_ACTION_BUTTONS} == {
        "combat_duel_light", "combat_duel_heavy", "combat_duel_parry",
        "combat_dash_arm", "combat_duel_dash_strike",
    }
    assert play._pirates_world_action_for_key(KeySym.Z) == ("combat_body",)
    assert play._pirates_world_action_for_key(KeySym.O) != ("combat_door",)
    assert play._pirates_world_action_for_key(KeySym.M) != ("combat_smoke",)
    assert play._pirates_world_action_for_key(KeySym.R) == ("combat_aim",)
    assert play._pirates_world_action_for_key(KeySym.T) == ("combat_reload",)
    assert play._pirates_world_action_for_key(KeySym.N) == ("combat_tick_step",)
    assert play._pirates_world_action_for_key(KeySym.N, shift=True) == ("combat_fast_step",)
    assert play._pirates_world_action_for_key(KeySym.GRAVE) == ("combat_run_toggle",)
    assert play._pirates_world_action_for_key(KeySym.CAPSLOCK) == ("combat_run_toggle",)
    assert play._pirates_world_action_for_key(KeySym.PERIOD) == ("combat_wait",)
    assert play._pirates_world_action_for_key(KeySym.I) == ("combat_help",)
    assert play._pirates_world_action_for_key(KeySym.H) == ("combat_history",)
    assert play._pirates_world_action_for_key(KeySym.M) == ("combat_motion_toggle",)
    assert play._pirates_world_action_for_key(KeySym.RETURN) == ("combat_wait",)
    assert play._pirates_world_action_for_key(KeySym.C) == ("combat_context",)
    assert play._pirates_world_action_for_key(KeySym.N1) == ("combat_part", "head")
    assert play._pirates_world_action_for_key(KeySym.N4) == ("combat_part", "leg")
    assert play._pirates_world_action_for_key(KeySym.RIGHT, control=True) == ("combat_dash", 1, 0)
    assert play._pirates_world_action_for_key(KeySym.KP_7) == ("move", -1, -1)
    assert play._pirates_world_action_for_key(KeySym.KP_9, control=True) == ("combat_dash", 1, -1)
    assert play._pirates_world_action_for_key(KeySym.LSHIFT) == ("combat_run_toggle",)
    assert play._pirates_world_action_for_key(KeySym.D) == ("combat_dash_arm",)
    assert play.COMBAT_DASH_ARMED
    assert play._pirates_world_action_for_key(KeySym.RIGHT) == ("combat_dash", 1, 0)
    assert not play.COMBAT_DASH_ARMED
    assert play._pirates_world_action_for_key(KeySym.D) == ("combat_dash_arm",)
    assert play._pirates_world_action_for_key(KeySym.ESCAPE) == ("combat_dash_cancel",)
    assert play._pirates_world_action_for_key(KeySym.D) == ("combat_dash_arm",)
    assert play._pirates_world_action_for_key(KeySym.KP_7) == ("combat_dash", -1, -1)
    assert not play.COMBAT_DASH_ARMED
    assert "DASH ARMED" in play.combat_control_hint(True)
    assert "Esc cancels" in play.combat_control_hint(True)
    assert "Shift sprint" in play.combat_control_hint(False)
    play.COMBAT_INPUT_MODE = False
    play.COMBAT_DASH_ARMED = False


def test_combat_arena_preview_adapter_is_read_only():
    world = World()
    arena = world.combat
    arena.start(world, seed=31)
    before = arena.engine.snapshot()
    replay = list(arena.engine.replay)
    preview = arena.preview("move", (0, 1))
    assert preview["valid"]
    assert preview["path"]
    assert "safe_disengage" in preview
    assert arena.engine.snapshot() == before
    assert arena.engine.replay == replay
    arena.exit(world)


def test_combat_actions_execute_without_confirmation():
    world = World()
    arena = world.combat
    assert arena.start(world, seed=33)
    assert arena.request(world, "move", (0, 1))
    assert arena.engine.replay[-1]["action"] == "move"
    arena.exit(world)


def test_mouse_dash_preview_uses_the_selected_landing_distance():
    world = World()
    arena = world.combat
    assert arena.start(world, seed=34)
    player = arena.fighters["captain"]
    direction = next((d for d in ((0, -1), (1, 0), (0, 1), (-1, 0))
                      if all((player.x + d[0] * n, player.y + d[1] * n) in arena.engine.walkable
                             and not arena.engine.at((player.x + d[0] * n, player.y + d[1] * n))
                             for n in (1, 2))), None)
    assert direction
    preview = arena.preview("dash", (*direction, 2))
    assert preview["valid"] and len(preview["path"]) == 2
    assert preview["destination"] == (player.x + direction[0] * 2,
                                      player.y + direction[1] * 2)
    arena.exit(world)


def test_dash_supports_diagonal_three_tile_paths():
    core = Combat({(x, y) for x in range(1, 12) for y in range(1, 12)}, seed=35)
    actor = Actor("captain", "You", 3, 3, faction="player", stamina=100)
    core.add(actor)
    assert core.act(actor.id, "dash", (6, 6)) == ""
    assert actor.movement_path == ((4, 4), (5, 5), (6, 6))


def test_clean_edge_hit_to_arm_can_sever_and_reports_both_sides():
    core = Combat({(x, y) for x in range(1, 12) for y in range(1, 12)}, seed=1)
    attacker = Actor("captain", "You", 4, 5, faction="player", facing=(1, 0))
    defender = Actor("enemy", "Enemy", 5, 5, facing=(-1, 0))
    core.add(attacker)
    core.add(defender)
    attacker.next_ready_tick = 0
    assert core.act(attacker.id, "thrust", defender.pos) == ""
    intent = attacker.intent
    attacker.intent = Intent(intent.actor_id, intent.kind, intent.target_tiles, intent.start_tick,
                             intent.resolves_at_tick, intent.interruptible, "hand",
                             intent.attacker_origin, intent.target_origin)
    core.tick = intent.resolves_at_tick
    core.resolve(attacker)
    assert defender.severed_parts
    assert any("SEVERS" in row for row in core.log)
    assert {entry["actor_id"] for entry in core.feedback} == {"captain", "enemy"}
    assert core.feedback[-1]["source_id"] == "captain"
    assert core.feedback[-1]["recipient_id"] == "enemy"
    assert core.feedback[-1]["body_part"] in {"left_arm", "right_arm"}
    assert core.feedback[-1]["outcome"] == "severed"
    assert core.feedback[-1]["consequences"]["severed"] is True


def test_configured_npc_profile_applies_combat_stats_equipment_and_condition():
    world = World()
    arena = world.combat
    arena.npc_overrides = {
        "weapon": "gun", "secondary_weapon": "sword", "personality": "coward",
        "tactics": "flanker", "speed": 150, "movement_skill": 40,
        "strength": 4, "armor": 2, "shield": True, "stamina": 60,
        "max_stamina": 125, "balance": 55, "fatigue": 30, "morale": 20,
        "perception": 15, "carried_weight_kg": 10, "carry_capacity_kg": 30,
        "target_part": "hand", "ammo": 0, "spare_shots": 4,
        "smoke_charges": 2, "glyph": "x", "condition_left_arm": "severed",
    }
    assert arena.start(world, seed=57)
    enemy = arena.enemies[0]
    assert (enemy.weapon, enemy.secondary_weapon, enemy.personality, enemy.tactics) == (
        "gun", "sword", "coward", "flanker")
    assert (enemy.speed, enemy.movement_skill, enemy.strength, enemy.armor) == (150, 40, 4, 2)
    assert enemy.shield and enemy.stamina == 60 and enemy.max_stamina == 125
    assert (enemy.balance, enemy.fatigue, enemy.morale, enemy.perception) == (55, 30, 20, 15)
    assert (enemy.carried_weight_kg, enemy.carry_capacity_kg) == (10, 30)
    assert (enemy.target_part, enemy.ammo, enemy.spare_shots, enemy.smoke_charges, enemy.glyph) == (
        "hand", 0, 4, 2, "x")
    assert "left_arm" in enemy.severed_parts
    assert arena.engine.snapshot()["actors"][1]["severed_parts"] == ["left_arm"]
    arena.exit(world)


def test_npc_profile_validation_preserves_inheritance_false_and_zero_values():
    import play

    profile = {"speed": 50, "strength": 0, "armor": 0, "shield": False,
               "stamina": 0, "max_stamina": 50, "condition_left_arm": "severed",
               "weapon": None}
    assert validate_npc_profile(profile)
    with pytest.raises(ValueError, match="cannot exceed"):
        validate_npc_profile({"stamina": 100, "max_stamina": 50})
    with pytest.raises(ValueError, match="cannot exceed"):
        validate_npc_profile({"stamina": 125})
    with pytest.raises(ValueError, match="speed"):
        validate_npc_profile({"speed": 201})
    with pytest.raises(ValueError, match="shield"):
        validate_npc_profile({"shield": 0})
    with pytest.raises(ValueError, match="Unknown profile"):
        validate_npc_profile({"hp": 5})
    with pytest.raises(ValueError, match="cannot be severed"):
        validate_npc_profile({"condition_head": "severed"})
    with pytest.raises(ValueError, match="supported weapon"):
        validate_npc_profile({"weapon": []})
    assert play.parse_profile_number("speed", "137") == 137
    assert play.parse_profile_number("balance", "42.5") == 42.5
    with pytest.raises(ValueError, match="whole number"):
        validate_npc_profile({"speed": 137.5})
    with pytest.raises(ValueError, match="between"):
        play.parse_profile_number("perception", "99")
    with pytest.raises(ValueError, match="valid number"):
        play.parse_profile_number("balance", "abc")
    with pytest.raises(ValueError, match="between"):
        play.parse_profile_number("balance", "NaN")


def test_enemy_profile_editor_discard_keeps_shared_profile_and_world_untouched():
    import play

    class ScriptedRenderer:
        def __init__(self, choices):
            self.choices = iter(choices)

        def render_list(self, *args, **kwargs):
            pass

        def choose(self, _count):
            return next(self.choices)

        def wait_any(self):
            pass

    world = World()
    arena = world.combat
    arena.npc_overrides = {"strength": 4, "shield": False}
    original_map = world.game_map
    original_profile = dict(arena.npc_overrides)
    play.combat_setup(ScriptedRenderer([10, 7, None]), world)
    assert arena.npc_overrides == original_profile
    assert world.game_map is original_map
    assert not arena.active


def test_enemy_profile_editor_applies_reviewed_false_override_to_spawned_foes():
    import play

    class ScriptedRenderer:
        def __init__(self, choices):
            self.choices = iter(choices)

        def render_list(self, *args, **kwargs):
            pass

        def choose(self, _count):
            return next(self.choices)

        def wait_any(self):
            pass

    world = World()
    arena = world.combat
    play.combat_setup(ScriptedRenderer([10, 2, 2, 2, 9, 6, 0, 11]), world)
    assert arena.active
    assert arena.npc_overrides["shield"] is False
    assert arena.enemies and all(enemy.shield is False for enemy in arena.enemies)
    arena.exit(world)


def test_enemy_profile_editor_preset_and_reset_remain_drafts_until_reviewed_apply():
    import play

    class ScriptedRenderer:
        def __init__(self, choices):
            self.choices = iter(choices)

        def render_list(self, *args, **kwargs):
            pass

        def choose(self, _count):
            return next(self.choices)

        def wait_any(self):
            pass

    world = World()
    arena = world.combat
    arena.npc_overrides = {"shield": False, "strength": 8}
    # Select the agile preset, review, continue, reset draft, then review/apply.
    choices = [10, 4, 0, 6, 1, 5, 6, 0, 11]
    play.combat_setup(ScriptedRenderer(choices), world)
    assert arena.active
    assert arena.npc_overrides == {}
    assert all(enemy.speed == 100 and enemy.tactics == "standard" for enemy in arena.enemies)
    arena.exit(world)


def test_enemy_profile_preset_applies_only_after_review_confirmation():
    import play

    class ScriptedRenderer:
        def __init__(self, choices):
            self.choices = iter(choices)

        def render_list(self, *args, **kwargs):
            pass

        def choose(self, _count):
            return next(self.choices)

        def wait_any(self):
            pass

    world = World()
    arena = world.combat
    arena.npc_overrides = {"strength": 8}
    play.combat_setup(ScriptedRenderer([10, 4, 0, 6, 0, 11]), world)
    assert arena.npc_overrides == {
        "weapon": "sword", "tactics": "flanker", "speed": 125,
        "movement_skill": 50, "shield": False,
    }
    assert all(enemy.speed == 125 and enemy.tactics == "flanker" for enemy in arena.enemies)
    arena.exit(world)


def test_combat_time_and_feedback_animation_use_player_facing_seconds_and_wall_time():
    import play
    assert format_combat_time(0) == "0.0s"
    assert format_combat_time(4) == "0.1s"
    assert format_combat_time(25) == "0.3s"
    assert format_combat_time(150) == "1.5s"
    assert play.combat_feedback_rise(0.0) == 0
    assert play.combat_feedback_rise(0.9) == 14
    assert play.combat_feedback_rise(1.8) is None
    assert play.combat_feedback_offset(0.9, reduced_motion=True) == 0

    world = World()
    arena = world.combat
    assert arena.start(world, seed=58)
    enemy = arena.enemies[0]
    arena.engine.show_feedback(enemy, "BLOCK", "block")
    arena.engine.show_feedback(enemy, "HIT · TORSO", "wound")
    arena.engine.show_feedback(arena.fighters["captain"], "PARRY", "parry")
    event_tick = arena.engine.feedback[-1]["tick"]
    assert [event["event_id"] for event in arena.engine.feedback] == [1, 2, 3]
    assert len({event["event_id"] for event in arena.engine.feedback}) == 3
    replay_before = list(arena.engine.replay)
    labels = arena.feedback_labels()
    arena.engine.tick += 10_000
    assert arena.feedback_labels() == labels
    assert arena.engine.feedback[-1]["tick"] == event_tick
    assert arena.engine.feedback[-1]["event_id"] == 3
    assert arena.engine.replay == replay_before
    assert len(labels) == 3
    arena.exit(world)


def test_combat_ui_preferences_are_separate_validated_atomic_json(tmp_path):
    import json
    import play

    path = tmp_path / "preferences.json"
    assert play.load_ui_preferences(path) == {
        "combat_reduced_motion": False, "combat_tips_dismissed": False}
    play.save_ui_preferences({"combat_reduced_motion": True,
                              "combat_tips_dismissed": True, "unexpected": "ignored"}, path)
    assert play.load_ui_preferences(path) == {
        "combat_reduced_motion": True, "combat_tips_dismissed": True}
    assert json.loads(path.read_text(encoding="utf-8"))["combat_reduced_motion"] is True
    path.write_text("not json", encoding="utf-8")
    assert play.load_ui_preferences(path)["combat_reduced_motion"] is False


def test_combat_history_browses_structured_events_without_advancing_or_mutating(tmp_path):
    import play

    class ScriptedRenderer:
        def __init__(self, choices):
            self.choices = iter(choices)
            self.rows = []

        def render_list(self, _world, title, rows, *_args, **_kwargs):
            self.rows.append((title, list(rows)))

        def choose(self, _count):
            return next(self.choices)

        def wait_any(self):
            pass

    world = World()
    arena = world.combat
    assert arena.start(world, seed=82)
    enemy = arena.enemies[0]
    arena.engine.show_feedback(enemy, "HIT · TORSO", "wound", source=arena.fighters["captain"],
                               recipient=enemy, body_part="torso", outcome="wounded",
                               consequences={"shock": 12, "blood_loss": 3})
    before = arena.engine.snapshot()
    renderer = ScriptedRenderer([0, None])
    play.combat_history_menu(renderer, world)
    assert renderer.rows[0][0] == "Combat history · newest first"
    assert "#1" in renderer.rows[0][1][0]
    assert renderer.rows[1][0] == "HIT · TORSO"
    assert any("shock: 12" in row for row in renderer.rows[1][1])
    assert arena.engine.snapshot() == before
    arena.exit(world)


def test_first_arena_tips_can_be_dismissed_without_advancing_time(tmp_path, monkeypatch):
    import json
    import play

    class ScriptedRenderer:
        def __init__(self, choice):
            self.choice = choice
            self.rows = []

        def render_list(self, _world, title, rows, *_args, **_kwargs):
            self.rows.append((title, list(rows)))

        def choose(self, _count):
            return self.choice

        def wait_any(self):
            pass

    path = tmp_path / "ui-preferences.json"
    monkeypatch.setattr(play, "UI_PREFERENCES_PATH", path)
    prefs = play.COMBAT_UI_PREFERENCES
    old = dict(prefs)
    prefs.update(combat_tips_dismissed=False)
    world = World()
    assert world.combat.start(world, seed=83)
    tick = world.combat.engine.tick
    renderer = ScriptedRenderer(2)
    play.combat_onboarding(renderer, world)
    assert world.combat.engine.tick == tick
    assert prefs["combat_tips_dismissed"] is True
    assert json.loads(path.read_text(encoding="utf-8"))["combat_tips_dismissed"] is True
    assert renderer.rows[0][0].startswith("First arena")
    prefs.update(old)
    world.combat.exit(world)


def test_compact_combat_hud_timeline_and_wounds_are_read_only_and_mono_safe():
    world = World()
    arena = world.combat
    assert arena.start(world, seed=39)
    enemy = arena.target()
    player = arena.fighters["captain"]
    enemy.x, enemy.y = player.x + 1, player.y
    enemy.next_ready_tick = arena.engine.tick
    assert arena.engine.act(enemy.id, "thrust", player.pos) == ""
    before = arena.engine.snapshot()
    rows = arena.hud_lines(world)
    timeline = arena.timeline_events()
    assert any("Shock" in row and "Balance" in row for row in rows)
    assert any("[TORSO:" in row for row in rows)
    assert any("THREAT" in row for row in timeline)
    assert len(rows) <= 12
    assert arena.engine.snapshot() == before
    arena.monochrome = True
    assert arena.overlay(arena.fighters["captain"].pos)[0] == "!"
    arena.exit(world)


def test_target_selection_is_persistent_and_repeated_left_click_never_attacks():
    world = World()
    arena = world.combat
    assert arena.start(world, seed=94)
    enemy = arena.target()
    before = arena.engine.snapshot()
    replay_before = list(arena.engine.replay)
    metrics_before = dict(arena.engine.metrics)
    assert arena.select_at(world, enemy.x, enemy.y)
    after_first = arena.engine.snapshot()
    assert arena.select_at(world, enemy.x, enemy.y)
    after_second = arena.engine.snapshot()
    assert after_first["tick"] == before["tick"]
    assert after_second["tick"] == before["tick"]
    assert arena.engine.replay == replay_before
    assert arena.engine.metrics == metrics_before
    assert arena.selected_target == enemy.id
    assert "TARGET" in arena.floating_labels()[0][2]
    arena.exit(world)


def test_keyboard_sprint_uses_two_tile_run_burst_and_renderer_only_animates_resolved_path():
    import play

    class CaptureRenderer:
        def __init__(self, arena):
            self.arena = arena
            self.captured = None

        def animate_combat_path(self, world, path, mode):
            self.captured = (tuple(path), mode, self.arena.engine.snapshot(),
                             world.clock.total_ticks)

    world = World()
    arena = world.combat
    assert arena.start(world, seed=95)
    player = arena.fighters["captain"]
    arena.act(world, "toggle_run")
    start = player.pos
    renderer = CaptureRenderer(arena)
    clock = world.clock.total_ticks
    play.execute_combat_movement(renderer, world, "move", (1, 0))
    end = player.pos
    assert end == (start[0] + 2, start[1])
    assert len(renderer.captured[0]) == 3
    assert renderer.captured[1] == "run"
    assert renderer.captured[2] == arena.engine.snapshot()
    assert renderer.captured[3] == clock
    assert player.move_mode == "run"
    assert player.state == "ready"
    arena.exit(world)


def test_dash_presents_only_committed_actual_route_without_changing_simulation():
    import play

    class CaptureRenderer:
        def __init__(self, arena):
            self.arena = arena
            self.captured = None

        def animate_combat_path(self, world, path, mode):
            self.captured = (tuple(path), mode, self.arena.engine.snapshot())

    world = World()
    arena = world.combat
    assert arena.start(world, seed=96)
    player = arena.fighters["captain"]
    start = player.pos
    renderer = CaptureRenderer(arena)
    play.execute_combat_movement(renderer, world, "dash", (1, 0))
    expected = tuple((start[0] + n, start[1]) for n in range(4))
    assert renderer.captured[0] == expected
    assert renderer.captured[1] == "dash"
    assert renderer.captured[2] == arena.engine.snapshot()
    assert player.pos == expected[-1]
    assert arena.engine.tick > 0
    arena.exit(world)


def test_unconscious_actor_ends_the_arena_and_counts_as_a_resolved_foe():
    world = World()
    arena = world.combat
    assert arena.start(world, seed=9)
    foe = arena.enemies[0]
    assert arena.engine.mark_unconscious(foe, arena.fighters["captain"], "test")
    assert arena.finished
    assert arena.engine.report()["winner"] == "captain"
    arena.exit(world)

    assert arena.start(world, seed=10)
    captain = arena.fighters["captain"]
    assert arena.engine.mark_unconscious(captain, arena.enemies[0], "test")
    assert arena.finished
    arena.exit(world)


def test_enemy_morale_breaks_into_personality_driven_flee_or_surrender():
    core = Combat({(x, y) for x in range(1, 13) for y in range(1, 13)}, seed=6)
    core.add(Actor("captain", "You", 2, 2, faction="player"))
    coward = Actor("coward", "Coward", 6, 5, personality="coward")
    patient = Actor("patient", "Patient", 8, 5, personality="patient")
    berserker = Actor("berserker", "Berserker", 10, 5, personality="berserker")
    core.add(coward)
    core.add(patient)
    core.add(berserker)
    assert core.change_morale(coward, 40, "test pressure")
    assert coward.fled and not coward.active
    assert core.change_morale(patient, 80, "test pressure")
    assert patient.surrendered and not patient.active
    assert not core.change_morale(berserker, 100, "test pressure")
    assert berserker.active


def test_unconscious_captain_is_robbed_on_arena_exit():
    world = World()
    arena = world.combat
    item = {"id": "test_loot", "name": "Test Locket", "weight_g": 1,
            "bulk_ml": 1, "quantity": 1}
    world.inventory.items.append(item.copy())
    original_ground = (world.player.x, world.player.y)
    assert arena.start(world, seed=11)
    arena.engine.mark_unconscious(arena.fighters["captain"], arena.enemies[0], "test")
    arena.exit(world)
    assert not any(row.get("id") == "test_loot" for row in world.inventory.items)
    assert any(row.get("id") == "test_loot" for row in world.ground_items[original_ground])


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_each_archetype_has_a_replayable_zero_damage_win(archetype):
    from tools.combat_headless import match, counter_bot, flawless_search
    core = match(archetype)
    result = counter_bot(core)
    if not result["flawless"]:
        core = flawless_search(match(archetype))
    assert core is not None, f"No zero-damage counter found: {archetype}"
    assert core.report()["flawless"]
    replay = match(archetype)
    for command in core.replay:
        assert replay.command(command["action"], command["target"])
    assert replay.snapshot() == core.snapshot()


def test_same_tick_npc_telegraph_is_visible_before_next_player_decision():
    core = duel()
    core.actors["enemy"].personality = "berserker"
    core.command("move", (4, 5))
    # NPC's deadline ties the player's readiness. It must be processed now,
    # never deferred until after the player has already committed another move.
    assert core.actors["enemy"].intent is not None


def test_dump_replays_without_rendering_side_effects(tmp_path):
    from tools.combat_headless import replay_dump
    world = World()
    world.combat.start(world)
    for _ in range(5):
        world.combat.act(world, "wait")
        world.combat.visible_entities()
    path = tmp_path / "replay.json"
    world.combat.dump(path)
    data = json.loads(path.read_text())
    replay = replay_dump(data)
    assert json.loads(json.dumps(replay.snapshot())) == data["state"]


@pytest.mark.parametrize("version", [None, 1, 2, 999])
def test_replay_dump_rejects_missing_or_incompatible_ruleset(version):
    from tools.combat_headless import replay_dump
    data = {"combat_ruleset_version": version, "walkable": [], "report": {"seed": 1}}
    with pytest.raises(ValueError, match="incompatible"):
        replay_dump(data)


def test_group_spawns_have_independent_visible_intents_and_no_hidden_attack_cap():
    world = World()
    arena = world.combat
    arena.count = 5
    arena.start(world)
    assert len(arena.enemies) == 5
    assert len({a.pos for a in arena.enemies}) == 5
    for _ in range(12):
        if arena.finished:
            break
        arena.act(world, "wait")
    for event in arena.engine.audit:
        assert event["resolves_at_tick"] > event["start_tick"]


def test_paralysis_requires_punishing_recovery_and_expires_without_deadlock():
    core = duel(enemy_weapon="brawl")
    enemy, player = core.actors["enemy"], core.actors["captain"]
    enemy.next_ready_tick = 0
    player.armor = 2
    player.state = "recovery"
    core.act("enemy", "slam", player.pos)
    core.advance_time(180)
    core.resolve(enemy)
    assert player.statuses["paralyzed"] == 380
    assert core.blocker(player, "wait")
    core.run_until_player()
    assert core.tick == 380
    assert not core.blocker(player, "wait")


def test_fast_enemy_cannot_shorten_visible_windup():
    core = duel(enemy_speed=200)
    core.command("wait")
    enemy = core.actors["enemy"]
    assert enemy.intent
    assert enemy.intent.resolves_at_tick - core.tick >= 100


def test_npc_telegraph_uses_declared_counter_not_free_escape_extension():
    core = duel()
    player, enemy = core.actors["captain"], core.actors["enemy"]
    core.advance_time(594)
    player.next_ready_tick = 600
    player.fatigue = 40
    player.carried_weight_kg = 12
    enemy.next_ready_tick = core.tick
    enemy.state = "ready"

    assert core.act("enemy", "thrust", player.pos) == ""

    # Thrust has a quick parry/block counter. Readiness and encumbrance do not
    # stretch the tell until an ordinary movement step becomes an escape.
    assert enemy.intent.resolves_at_tick - core.tick == PATTERNS["thrust"]["windup"]
    core.advance_time(player.next_ready_tick)
    assert core.act("captain", "brace", enemy.pos) == ""
    core.advance_time(enemy.intent.resolves_at_tick)
    core.resolve(enemy)
    assert core.actors["captain"].hp == 20


def test_wounded_leg_cannot_turn_quick_thrust_into_a_free_escape():
    core = duel()
    player, enemy = core.actors["captain"], core.actors["enemy"]
    player.statuses["crippled_leg"] = 500
    enemy.next_ready_tick = core.tick
    enemy.state = "ready"

    assert core.act("enemy", "thrust", player.pos) == ""
    intent = enemy.intent
    assert intent.resolves_at_tick - core.tick == PATTERNS["thrust"]["windup"]
    assert core.act("captain", "move", (5, 4)) == ""

    core.run_until_player()

    assert player.pos == (5, 5)
    assert player.hp < player.max_hp
    assert core.metrics["hits"] >= 1


def test_debug_scheduler_steps_one_or_ten_events_and_stops_at_player_ready():
    core = duel()
    player = core.actors["captain"]
    player.next_ready_tick = 1000
    one = core.step_events(1)
    assert one == 1 and core.tick == 100
    ten = core.step_events(10)
    assert 1 <= ten <= 10
    assert core.tick > 100


def test_debug_tick_steps_are_exact_and_fast_forward_stops_at_player_decision():
    core = duel()
    player = core.actors["captain"]
    player.next_ready_tick = 1000
    assert core.advance_debug_ticks(1) == 0
    assert core.tick == 1
    assert core.advance_debug_ticks(9) == 0
    assert core.tick == 10

    core = duel()
    player = core.actors["captain"]
    core.schedule(player, 100)
    before = core.tick
    core.advance_debug_ticks(500)
    assert core.tick == 100
    assert player.next_ready_tick == core.tick


def test_body_target_is_locked_into_intent_and_head_is_vital():
    core = duel()
    player, enemy = core.actors["captain"], core.actors["enemy"]
    assert core.command("target_part", "head")
    assert core.tick == 0
    assert core.act("captain", "thrust", enemy.pos) == ""
    intent = player.intent
    assert intent.target_part == "head"
    player.target_part = "torso"
    core.advance_time(intent.resolves_at_tick)
    core.resolve(player)
    assert enemy.hp == 13
    assert enemy.body_parts["head"]["damage"] > 0
    assert "head" in enemy.injuries


def test_arm_and_leg_hits_apply_limb_specific_restrictions():
    core = duel(weapon="rifle")
    player, enemy = core.actors["captain"], core.actors["enemy"]
    enemy.next_ready_tick = 0
    assert core.command("target_part", "arm")
    assert core.act("captain", "aim", enemy.pos) == ""
    player.next_ready_tick = core.tick
    assert core.act("captain", "fire") == ""
    intent = player.intent
    core.advance_time(intent.resolves_at_tick)
    core.resolve(player)
    assert enemy.statuses["crippled_arm"] > core.tick
    assert "arm" in enemy.injuries
    enemy.weapon = "rifle"
    enemy.ammo = 0
    enemy.next_ready_tick = core.tick
    enemy.state = "ready"
    assert "two-handed" in core.blocker(enemy, "rifle_shot", player.pos)
    assert "cannot reload" in core.blocker(enemy, "reload")

    enemy.statuses.clear()
    enemy.weapon = "sword"
    enemy.next_ready_tick = core.tick
    player.next_ready_tick = core.tick
    player.weapon = "sword"
    player.state = "ready"
    player.x, player.y = 5, 5
    player.target_part = "leg"
    assert core.act("captain", "sweep", enemy.pos) == ""
    intent = player.intent
    core.advance_time(intent.resolves_at_tick)
    core.resolve(player)
    assert enemy.statuses["crippled_leg"] > core.tick
    assert "leg" in enemy.injuries
    enemy.next_ready_tick = core.tick
    start = core.tick
    assert core.act("enemy", "move", (7, 5)) == ""
    assert enemy.movement_step_base == MOVEMENT["backstep"]["time"]
    assert enemy.next_ready_tick - start == enemy.movement_step_ticks


def test_ranged_actions_require_explicit_aim_then_fire_on_the_locked_line():
    core = duel(weapon="gun")
    player, enemy = core.actors["captain"], core.actors["enemy"]
    enemy.x = 10
    enemy.next_ready_tick = 10_000
    assert "Aim first" in core.blocker(player, "pistol_shot", enemy.pos)
    assert "Aim first" in core.blocker(player, "fire")
    assert core.act("captain", "aim", enemy.pos) == ""
    locked = player.aim_tiles
    assert enemy.pos in locked
    assert player.ammo == 1
    player.next_ready_tick = core.tick
    assert core.act("captain", "fire") == ""
    assert player.intent and player.intent.kind == "pistol_shot"
    assert player.intent.target_tiles == locked
    assert player.ammo == 0
    assert enemy.hp == 20
    enemy.y = 8
    core.advance_time(player.intent.resolves_at_tick)
    core.resolve(player)
    assert core.metrics["whiffs"] == 1


def test_ranged_aim_is_canceled_by_movement_and_arm_injury_blocks_aim():
    core = duel(weapon="gun")
    player, enemy = core.actors["captain"], core.actors["enemy"]
    enemy.x = 10
    enemy.next_ready_tick = 10_000
    assert core.command("aim", enemy.pos)
    assert player.aim_tiles
    assert core.command("move", (5, 4))
    assert not player.aim_tiles
    player.next_ready_tick = core.tick
    player.statuses["crippled_arm"] = core.tick + 100
    assert "cannot aim" in core.blocker(player, "aim", enemy.pos)


def test_ranged_ai_uses_aim_then_fire_not_a_single_hidden_shot():
    core = duel(enemy_weapon="gun")
    player, enemy = core.actors["captain"], core.actors["enemy"]
    player.x, player.y = 9, 5
    enemy.x, enemy.y = 5, 5
    enemy.next_ready_tick = 0
    core.ai(enemy)
    assert enemy.state == "aiming" and enemy.aim_tiles
    assert enemy.intent is None and enemy.ammo == 1
    enemy.next_ready_tick = core.tick
    core.ai(enemy)
    assert enemy.intent and enemy.intent.kind == "pistol_shot"
    assert enemy.ammo == 0


def test_doors_block_los_and_movement_until_opened():
    core = duel()
    player, enemy = core.actors["captain"], core.actors["enemy"]
    enemy.x = 9
    core.doors[(6, 5)] = False
    assert not core.los(player.pos, enemy.pos)
    assert core.blocker(player, "move", (6, 5)) == "Door is closed: open it first"
    assert core.act("captain", "door", (7, 5)) == "No adjacent door"
    assert core.act("captain", "door", (6, 5)) == ""
    assert core.doors[(6, 5)] is True
    assert core.los(player.pos, enemy.pos)


def test_ai_opens_a_closed_door_on_its_path():
    core = duel()
    player, enemy = core.actors["captain"], core.actors["enemy"]
    player.x, player.y = 10, 5
    enemy.x, enemy.y = 5, 5
    core.doors[(6, 5)] = False
    enemy.last_seen = player.pos
    enemy.last_seen_tick = core.tick
    enemy.next_ready_tick = core.tick
    core.ai(enemy)
    assert core.doors[(6, 5)] is True
    assert "opens the door" in core.log[-1]


def test_smoke_blocks_perception_and_los_until_expiry():
    core = duel()
    player, enemy = core.actors["captain"], core.actors["enemy"]
    enemy.x = 9
    core.smoke[(7, 5)] = 300
    assert not core.los(player.pos, enemy.pos)
    assert core.act("captain", "smoke", (7, 5)) == "No smoke charges"
    player.smoke_charges = 1
    player.next_ready_tick = core.tick
    assert core.act("captain", "smoke", (7, 5)) == ""
    assert player.smoke_charges == 0
    assert core.smoke[(7, 5)] == 500
    core.advance_time(500)
    assert core.los(player.pos, enemy.pos)
    assert not core.smoke


def test_hazards_damage_on_entry_and_on_shove():
    core = duel()
    player, enemy = core.actors["captain"], core.actors["enemy"]
    core.hazards[(5, 6)] = "spikes"
    assert core.command("move", (5, 6))
    assert player.hp == 12
    assert player.statuses["crippled_leg"] > core.tick

    core = duel()
    player, enemy = core.actors["captain"], core.actors["enemy"]
    core.hazards[(7, 5)] = "spikes"
    assert core.act("captain", "shove", enemy.pos) == ""
    core.advance_time(player.intent.resolves_at_tick)
    core.resolve(player)
    assert enemy.pos == (7, 5)
    assert enemy.hp == 5  # deep shove wound plus spike damage
    assert enemy.statuses["crippled_leg"] > core.tick


def test_utility_ai_scores_recovery_opportunities_and_telegraph_risk():
    core = duel()
    player, enemy = core.actors["captain"], core.actors["enemy"]
    enemy.next_ready_tick = core.tick
    player.state = "ready"
    player.next_ready_tick = core.tick
    core.ai(enemy)
    baseline = enemy.utilities["thrust:(5, 5)"]

    enemy.intent = None
    enemy.state = "ready"
    enemy.next_ready_tick = core.tick
    player.state = "recovery"
    player.next_ready_tick = core.tick + 500
    core.ai(enemy)
    assert enemy.utilities["thrust:(5, 5)"] > baseline

    enemy.intent = None
    enemy.state = "ready"
    enemy.next_ready_tick = core.tick
    player.state = "ready"
    player.next_ready_tick = core.tick
    core.ai(enemy)
    evasive_risk = enemy.utilities["thrust:(5, 5)"]
    enemy.intent = None
    enemy.state = "ready"
    enemy.next_ready_tick = core.tick
    player.next_ready_tick = core.tick + 500
    core.ai(enemy)
    safe_window = enemy.utilities["thrust:(5, 5)"]
    assert safe_window > evasive_risk


def test_coward_flees_at_low_hp_while_berserker_stays_aggressive():
    coward = duel()
    actor = coward.actors["enemy"]
    actor.personality = "coward"
    actor.hp = 5
    actor.next_ready_tick = coward.tick
    coward.ai(actor)
    assert actor.behavior == "fleeing"
    finish_movement(coward, actor)
    assert actor.x > 6

    berserker = duel()
    actor = berserker.actors["enemy"]
    actor.personality = "berserker"
    actor.hp = 5
    actor.next_ready_tick = berserker.tick
    berserker.ai(actor)
    assert actor.behavior == "engaging"
    assert actor.intent is not None


def test_low_health_cautious_ai_escapes_at_arena_edge_as_a_resolved_outcome():
    core = duel()
    player, enemy = core.actors["captain"], core.actors["enemy"]
    enemy.personality = "coward"
    enemy.x, enemy.y = min(x for x, _ in core.walkable), 5
    enemy.hp = 5
    enemy.next_ready_tick = core.tick
    core.ai(enemy)
    assert enemy.fled and enemy.fled_tick == core.tick
    assert core.at(enemy.pos) is None
    report = core.report()
    assert report["time_to_kill_ticks"] == core.tick
    assert report["flawless"]


def test_low_health_ai_withdraws_after_sustained_retreat_even_without_a_map_edge():
    core = duel()
    enemy = core.actors["enemy"]
    enemy.personality = "coward"
    enemy.hp = 5
    enemy.retreat_steps = 7
    enemy.next_ready_tick = core.tick
    core.ai(enemy)
    assert enemy.fled
    assert "withdraws from the fight" in core.log[-1]


def test_arena_hides_fled_enemy_and_finishes_without_mutating_world_state():
    world = World()
    original_map = world.game_map
    arena = world.combat
    assert arena.start(world, seed=44)
    enemy = arena.enemies[0]
    enemy.personality = "coward"
    enemy.hp = 5
    enemy.x = min(x for x, _ in arena.engine.walkable)
    enemy.next_ready_tick = arena.engine.tick
    arena.engine.ai(enemy)
    assert enemy.fled
    assert arena.finished
    assert not arena.visible_entities()
    arena.exit(world)
    assert world.game_map is original_map


def test_actor_local_labels_show_target_action_and_aim_without_countdown_dump():
    world = World()
    arena = world.combat
    assert arena.start(world, seed=44)
    enemy = arena.enemies[0]
    enemy.intent = Intent(enemy.id, "sweep", (), arena.engine.tick, arena.engine.tick + 150)
    labels = arena.floating_labels()
    assert labels[0] == (enemy.id, enemy.pos, f"TARGET · {enemy.name}", (255, 220, 105))
    assert labels[1] == (enemy.id, enemy.pos, "PREPARING CUTLASS SWEEP", (255, 165, 125))
    assert all("1.5s" not in label[2] for label in labels)

    enemy.intent = None
    enemy.weapon = "gun"
    enemy.aim_tiles = ((80, 26), (81, 26))
    labels = arena.floating_labels()
    assert labels[1] == (enemy.id, enemy.pos, "AIMING FLINTLOCK PISTOL", (255, 225, 125))
    arena.exit(world)


def test_combat_overlay_reports_stamina_load_and_both_ascii_body_states():
    world = World()
    arena = world.combat
    assert arena.start(world, seed=44)
    player = arena.fighters["captain"]
    enemy = arena.enemies[0]
    player.stamina = 45
    player.fatigue = 60
    player.injuries["torso"] = 1
    player.statuses["crippled_leg"] = arena.engine.tick + 100
    enemy.injuries["head"] = 0
    enemy.statuses["bleeding"] = arena.engine.tick + 100
    enemy.personality = "berserker"

    before = arena.engine.snapshot()
    lines = arena.combat_overlay_lines()
    labels = arena.floating_labels()

    assert arena.engine.snapshot() == before
    assert labels
    assert any("Stamina 45/100" in row and "Balance" in row for row in lines)
    assert "Enemy body" in "\n".join(lines) and "HEAD:" in "\n".join(lines) and "TORSO:" in "\n".join(lines)
    assert any("Speed: steady" in row and "Technique:" in row for row in lines)
    assert any("Next:" in row for row in lines)
    assert len(lines) <= 12
    arena.exit(world)


def test_enemy_state_icon_is_shape_distinct_without_overhead_caption():
    world = World()
    arena = world.combat
    assert arena.start(world, seed=44)
    enemy = arena.enemies[0]
    enemy.intent = None
    enemy.aim_tiles = ()
    enemy.personality = "berserker"
    enemy.movement_mode = "dash"

    assert arena.visible_entities()[0].glyph == enemy.glyph.lower()
    enemy.intent = Intent(enemy.id, "thrust", (arena.fighters["captain"].pos,),
                          arena.engine.tick, arena.engine.tick + 101)
    assert arena.visible_entities()[0].glyph == "!"
    arena.exit(world)


def test_compact_enemy_summary_stays_bounded_with_multiple_long_names():
    world = World()
    arena = world.combat
    assert arena.start(world, seed=45)
    assert arena.spawn("duelist")
    first, second = arena.enemies[0], arena.enemies[1]
    first.name = "VeryLongFighterNameThatMustNotWidenThePanel"
    second.personality = "berserker"
    second.movement_mode = "dash"
    arena.selected_target = first.id

    before = arena.engine.snapshot()
    rows = arena.combat_overlay_lines()
    assert arena.engine.snapshot() == before
    assert any("FOE VeryLong" in row for row in rows)
    assert len(rows) <= 12
    assert max(map(len, rows)) < 100
    arena.exit(world)


def test_ai_uses_limited_perception_and_expires_stale_last_seen_memory():
    core = duel()
    player, enemy = core.actors["captain"], core.actors["enemy"]
    player.x, player.y = 5, 5
    enemy.x, enemy.y = 10, 10
    enemy.perception = 2
    enemy.last_seen = None
    enemy.next_ready_tick = core.tick
    core.ai(enemy)
    assert enemy.last_seen is None
    assert enemy.state == "recovery"  # spends only the fallback wait
    enemy.last_seen = player.pos
    enemy.last_seen_tick = core.tick - AI_RULES["chase_memory_ticks"] - 1
    enemy.state = "ready"
    enemy.next_ready_tick = core.tick
    core.ai(enemy)
    assert enemy.last_seen is None


def test_pack_hunter_moves_to_unoccupied_rear_flank_and_bypasses_shield():
    walkable = {(x, y) for x in range(1, 13) for y in range(1, 13)}
    core = Combat(walkable)
    core.add(Actor("captain", "You", 8, 5, faction="player", facing=(1, 0), shield=True))
    core.add(Actor("pack_a", "Pack A", 6, 5, personality="pack_hunter", tactics="flanker"))
    core.add(Actor("pack_b", "Pack B", 5, 5, personality="pack_hunter", tactics="flanker"))
    flanker = core.actors["pack_a"]
    flanker.next_ready_tick = core.tick
    core.ai(flanker)
    finish_movement(core, flanker)
    assert flanker.pos == (7, 5)
    assert not core.actors["captain"].shield_broken_until
    flanker.next_ready_tick = core.tick
    flanker.state = "ready"
    core.ai(flanker)
    assert flanker.intent and flanker.intent.kind in {"thrust", "sweep"}
    core.advance_time(flanker.intent.resolves_at_tick)
    core.resolve(flanker)
    assert core.actors["captain"].hp < 20


def test_mixed_squad_spawns_distinct_roles_and_mixed_weapon_user():
    world = World()
    arena = world.combat
    arena.count = 5
    arena.squad_mode = "mixed"
    arena.start(world, seed=71)
    assert {a.weapon for a in arena.enemies} >= {"sword", "gun", "spear"}
    assert any(a.secondary_weapon == "gun" for a in arena.enemies)
    assert any(a.shield for a in arena.enemies)
    assert any(a.tactics == "flanker" for a in arena.enemies)
    arena.exit(world)


def test_mixed_weapon_ai_switches_ranged_at_distance_and_melee_when_close():
    core = duel()
    player, enemy = core.actors["captain"], core.actors["enemy"]
    enemy.weapon, enemy.secondary_weapon = "sword", "gun"
    player.x, player.y = 10, 5
    enemy.x, enemy.y = 5, 5
    enemy.last_seen, enemy.last_seen_tick = player.pos, core.tick
    enemy.next_ready_tick = core.tick
    core.ai(enemy)
    assert enemy.weapon == "gun"
    enemy.intent = None
    enemy.state = "ready"
    enemy.next_ready_tick = core.tick
    player.x, player.y = 6, 5
    player.next_ready_tick = core.tick
    enemy.last_seen, enemy.last_seen_tick = player.pos, core.tick
    core.ai(enemy)
    assert enemy.weapon == "sword"
    player.x, player.y = 10, 5
    enemy.next_ready_tick = core.tick
    enemy.state = "ready"
    core.ai(enemy)
    assert enemy.weapon == "gun"
    enemy.next_ready_tick = core.tick
    enemy.state = "ready"
    core.ai(enemy)
    assert enemy.state == "aiming" and enemy.aim_tiles
    enemy.next_ready_tick = core.tick
    core.ai(enemy)
    assert enemy.intent and enemy.intent.kind == "pistol_shot"


def test_duelist_feint_is_distinguishable_safe_and_punishable():
    core = duel()
    player, enemy = core.actors["captain"], core.actors["enemy"]
    enemy.personality = "patient"
    enemy.next_ready_tick = 0
    assert not core.act("enemy", "feint", player.pos)
    intent = enemy.intent
    assert intent and intent.kind == "feint"
    assert player.pos not in intent.target_tiles  # fake line never masquerades as a lethal threat
    core.command("wait")
    core.command("wait")
    assert player.hp == 20
    assert core.metrics["feints"] == 1
    assert "reveals a feint" in core.log[-1]
    assert enemy.next_ready_tick > core.tick


def test_movement_speed_responds_to_skill_fatigue_and_carried_load():
    actor = Actor("runner", "Runner", 2, 2, speed=100)
    rested = movement_speed(actor)
    actor.movement_skill = 100
    skilled = movement_speed(actor)
    actor.fatigue = 80
    tired = movement_speed(actor)
    actor.fatigue = 0
    actor.carried_weight_kg = 24
    actor.carry_capacity_kg = 24
    loaded = movement_speed(actor)

    assert rested == 100
    assert skilled > rested
    assert tired < skilled
    assert loaded < skilled
    assert 25 <= min(tired, loaded) <= max(tired, loaded) <= 200


def test_kiter_baseline_does_not_dominate_archetypes_after_rework():
    from tools.combat_headless import bot_sweep
    report = bot_sweep("kiter", matches=1, seed=2026)
    wins = sum(row["wins"] for row in report["archetypes"].values())
    total = sum(row["matches"] for row in report["archetypes"].values())
    assert wins / total <= 0.4


def test_runner_baseline_is_caught_or_exhausted_not_left_in_timeout():
    from tools.combat_headless import bot_sweep
    report = bot_sweep("runner", matches=1, seed=2026)
    timeouts = sum(row["timeouts"] for row in report["archetypes"].values())
    wins = sum(row["wins"] for row in report["archetypes"].values())
    total = sum(row["matches"] for row in report["archetypes"].values())
    assert wins == 0
    assert timeouts / total <= 0.25  # bounded residual standoff cases are disclosed in the matrix


def test_run_and_dash_cover_more_distance_per_tick_and_modifiers_change_action_time():
    def action_result(action, speed=100, skill=0, fatigue=0, load=0):
        core = Combat({(x, y) for x in range(1, 13) for y in range(1, 8)})
        actor = Actor("captain", "You", 2, 4, faction="player", speed=speed,
                      movement_skill=skill, fatigue=fatigue, carried_weight_kg=load)
        core.add(actor)
        stride = {"move": 1, "run": 2, "dash": 3}[action]
        assert core.act("captain", action, (2 + stride, 4)) == ""
        core.run_until_player()
        return distance((2, 4), actor.pos), actor.next_ready_tick

    walk_distance, walk_ticks = action_result("move")
    run_distance, run_ticks = action_result("run")
    dash_distance, dash_ticks = action_result("dash")
    skilled_distance, skilled_ticks = action_result("run", skill=100)
    tired_distance, tired_ticks = action_result("run", fatigue=80)
    loaded_distance, loaded_ticks = action_result("run", load=24)

    assert (walk_distance, walk_ticks) == (1, 100)
    assert run_distance / run_ticks > walk_distance / walk_ticks
    assert dash_distance / dash_ticks > walk_distance / walk_ticks
    assert skilled_distance == run_distance and skilled_ticks < run_ticks
    assert tired_distance == run_distance and tired_ticks > run_ticks
    assert loaded_distance == run_distance and loaded_ticks > run_ticks


def test_run_covers_more_distance_than_walk_and_costs_stamina():
    core = Combat({(x, y) for x in range(1, 13) for y in range(1, 13)})
    runner = Actor("captain", "You", 3, 5, faction="player")
    core.add(runner)
    stamina = runner.stamina

    assert not core.act("captain", "run", (5, 5))
    core.run_until_player()
    assert runner.pos == (5, 5)
    assert runner.stamina < stamina
    assert runner.fatigue > 0


def test_run_toggle_stops_with_recovery_and_balance_penalty():
    core = Combat({(x, y) for x in range(1, 13) for y in range(1, 13)})
    runner = Actor("captain", "You", 3, 5, faction="player")
    core.add(runner)
    assert core.act("captain", "toggle_run") == ""
    assert runner.move_mode == "run"
    assert core.act("captain", "move", (4, 5)) == ""
    core.run_until_player()
    assert runner.pos == (4, 5)
    assert runner.stamina == 92
    balance = runner.balance
    assert core.act("captain", "toggle_run") == ""
    assert runner.move_mode == "walk"
    assert runner.state == "recovery"
    assert runner.balance == balance - BALANCE_RULES["run_stop_loss"]
    assert runner.next_ready_tick - core.tick == MOVEMENT["run"]["stop_recovery"]


def test_low_stamina_slows_movement_and_zero_stamina_blocks_dash_and_heavy():
    core = duel()
    player, enemy = core.actors["captain"], core.actors["enemy"]
    player.stamina = 29
    assert step_cost(player, (1, 0), "walk")[0] == 113
    player.stamina = 0
    assert "exhausted" in core.blocker(player, "dash", (8, 5))
    assert "exhausted" in core.blocker(player, "sweep", enemy.pos)


def test_arena_movement_uses_temporary_captain_skill_and_real_inventory_load():
    world = World()
    world.combat.progression["skills"].update(sword=41.0, brawl=32.0, gun=27.0)
    world.inventory.equipment.append({"id": "test_weight", "weight_g": 8000, "bulk_ml": 0})
    assert world.combat.start(world, seed=91)
    player = world.combat.fighters["captain"]
    assert player.movement_skill == pytest.approx(100 / 3)
    assert player.carried_weight_kg == 8
    assert player.carry_capacity_kg == 24
    start_fatigue = player.fatigue
    assert world.combat.act(world, "run", (1, 0))
    assert player.fatigue > start_fatigue
    world.combat.exit(world)
    assert not hasattr(world.player, "fatigue")  # arena exertion is temporary


def test_dash_checks_each_tile_and_stops_at_blocked_tile_without_teleporting():
    core = Combat({(x, y) for x in range(1, 13) for y in range(1, 13)})
    runner = Actor("captain", "You", 3, 5, faction="player")
    core.add(runner)
    core.add(Actor("blocker", "Blocker", 5, 5))

    assert not core.act("captain", "dash", (6, 5))
    core.run_until_player()
    assert runner.pos == (4, 5)
    assert runner.pos != (6, 5)


def test_dash_resolves_one_tile_at_a_time_and_can_escape_a_locked_telegraph():
    core = Combat({(x, y) for x in range(1, 13) for y in range(1, 13)})
    runner = Actor("captain", "You", 5, 5, faction="player")
    attacker = Actor("enemy", "Attacker", 4, 5)
    core.add(runner)
    core.add(attacker)
    attacker.next_ready_tick = core.tick
    assert core.act("enemy", "thrust", runner.pos) == ""
    assert core.act("captain", "dash", (8, 5)) == ""
    assert runner.pos == (5, 5)  # first cell is not crossed before its movement tick

    core.run_until_player()

    assert runner.pos == (8, 5)
    assert runner.hp == 20
    assert core.metrics["whiffs"] == 1


def test_interrupting_hit_cancels_remaining_run_or_dash_path():
    core = Combat({(x, y) for x in range(1, 13) for y in range(1, 13)})
    runner = Actor("captain", "You", 5, 5, faction="player")
    attacker = Actor("enemy", "Shoveman", 9, 5)
    core.add(runner)
    core.add(attacker)
    attacker.next_ready_tick = core.tick
    assert core.act("enemy", "shove", (8, 5)) == ""
    # Model a previously locked quick attack that resolves between dash tiles.
    attacker.intent = Intent("enemy", "shove", ((6, 5),), core.tick, 50)
    core.queue = [event for event in core.queue if not (event[1] == 0 and event[2] == "enemy")]
    heapq.heappush(core.queue, (50, 0, "enemy", attacker.token))
    assert core.act("captain", "dash", (8, 5)) == ""

    core.run_until_player()

    assert runner.pos == (5, 5)  # shove displacement is separate from the cancelled dash
    assert runner.pos != (8, 5)
    assert runner.movement_path == ()


def test_intermediate_dash_hazard_triggers_and_slows_later_stride_ticks():
    core = Combat({(x, y) for x in range(1, 13) for y in range(1, 8)})
    runner = Actor("captain", "You", 2, 4, faction="player")
    core.add(runner)
    core.hazards[(3, 4)] = "spikes"
    assert core.act("captain", "dash", (5, 4)) == ""
    initial_finish_tick = runner.next_ready_tick

    core.run_until_player()

    assert runner.pos == (5, 4)
    assert runner.hp == 12
    assert runner.statuses["crippled_leg"] > core.tick - 1
    assert core.tick > initial_finish_tick  # leg wound slows the remaining dash steps


def test_hotheaded_ai_chooses_a_readable_burst_to_close_distance():
    core = Combat({(x, y) for x in range(1, 15) for y in range(1, 10)})
    core.add(Actor("captain", "You", 12, 5, faction="player"))
    raider = Actor("raider", "Hothead", 3, 5, personality="berserker")
    core.add(raider)
    raider.next_ready_tick = core.tick

    core.ai(raider)
    finish_movement(core, raider)

    assert raider.pos[0] >= 6
    assert raider.movement_mode in {"run", "dash"}


def test_hotheaded_pursuer_closes_gap_against_repeated_vertical_kiting():
    core = Combat({(x, y) for x in range(1, 16) for y in range(1, 12)})
    player = Actor("captain", "You", 13, 5, faction="player")
    raider = Actor("raider", "Hothead", 2, 5, personality="berserker")
    core.add(player)
    core.add(raider)
    start_distance = distance(player.pos, raider.pos)

    for y in (6, 5, 6):
        player.y = y  # repeat the user's one-tile up/down escape pattern
        raider.next_ready_tick = core.tick
        raider.state = "ready"
        raider.last_seen = player.pos
        raider.last_seen_tick = core.tick
        core.ai(raider)
        finish_movement(core, raider)
        raider.intent = None
        raider.next_ready_tick = core.tick
        raider.state = "ready"

    assert distance(player.pos, raider.pos) < start_distance


def test_ai_approach_avoids_known_spikes_even_when_shortest_path_crosses_them():
    core = Combat({(x, y) for x in range(1, 13) for y in range(1, 10)})
    core.add(Actor("captain", "You", 10, 5, faction="player"))
    pursuer = Actor("enemy", "Pursuer", 3, 5, personality="berserker")
    core.add(pursuer)
    core.hazards[(4, 5)] = "spikes"
    pursuer.last_seen = (10, 5)
    pursuer.last_seen_tick = core.tick
    pursuer.next_ready_tick = core.tick
    assert core.step_toward(pursuer, (10, 5)) == (4, 5)

    core.ai(pursuer)

    assert pursuer.movement_path
    assert (4, 5) not in pursuer.movement_path
    assert pursuer.pos != (4, 5)


def test_fatigued_npc_prefers_breath_recovery_when_not_under_threat():
    core = Combat({(x, y) for x in range(1, 13) for y in range(1, 10)})
    core.add(Actor("captain", "You", 10, 5, faction="player"))
    pursuer = Actor("enemy", "Pursuer", 3, 5, personality="patient", fatigue=85)
    core.add(pursuer)
    pursuer.last_seen = (10, 5)
    pursuer.last_seen_tick = core.tick
    pursuer.next_ready_tick = core.tick

    core.ai(pursuer)

    assert pursuer.stamina > 80
    assert pursuer.fatigue < 85
    assert "catches breath" in core.log[-1]


def test_wait_recovers_stamina_and_reduces_fatigue():
    core = Combat({(x, y) for x in range(1, 6) for y in range(1, 6)})
    actor = Actor("captain", "You", 2, 2, faction="player", stamina=30, fatigue=45)
    core.add(actor)
    assert core.act("captain", "wait") == ""
    assert actor.stamina == 48
    assert actor.fatigue == 37
    assert actor.movement_mode == "rest"


def test_duelist_uses_feint_against_a_braced_target():
    core = duel()
    player, enemy = core.actors["captain"], core.actors["enemy"]
    player.braced = True
    enemy.next_ready_tick = 0
    core.ai(enemy)
    assert enemy.intent and enemy.intent.kind == "feint"


def test_feint_is_not_scored_as_a_generic_attack_against_unbraced_targets():
    core = duel()
    player, enemy = core.actors["captain"], core.actors["enemy"]
    player.braced = False
    enemy.next_ready_tick = core.tick
    core.ai(enemy)
    assert not any(key.startswith("feint:") for key in enemy.utilities)
    assert enemy.intent and enemy.intent.kind != "feint"
