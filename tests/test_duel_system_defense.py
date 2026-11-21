"""Contextual Defend selects only feasible responses to observed commitments."""
from copy import deepcopy

from pirates.duel_system.defense import choose_defend
from pirates.duel_system.engine import Choice, advance_one_tick, commit_action, legality
from pirates.duel_system.config import load_config
from pirates.duel_system.model import ActionKind, CombatState, EventType, Zone
from pirates.duel_system.session import DuelSession
from test_duel_system_foundation import bare_fighter


def duel():
    session = DuelSession.new(seed=921, guide_mode="off")
    session.player.pos, session.enemy.pos = (4, 3), (5, 3)
    return session


def advance_to(state, tick):
    while state.tick < tick and not state.end_reason:
        advance_one_tick(state)


def test_defend_prefers_feasible_parry_for_visible_light_and_is_bounded():
    session = duel()
    player, enemy = session.player, session.enemy
    commit_action(session.state, enemy.id,
                  Choice(ActionKind.LIGHT, Zone.TORSO, target_id=player.id))
    plan = choose_defend(session.state, player.id)
    assert plan.choice.kind == ActionKind.GUARD
    assert plan.choice.contextual_defense
    guard = commit_action(session.state, player.id, plan.choice)
    assert guard.end_tick is not None
    assert guard.end_tick - session.state.tick < 20
    advance_to(session.state, enemy.action.strike_tick)
    assert any(event.type == EventType.PARRY and event.actor == player.id
               for event in session.state.event_log)


def test_defend_uses_least_cost_timely_escape_from_heavy_line():
    session = duel()
    player, enemy = session.player, session.enemy
    commit_action(session.state, enemy.id,
                  Choice(ActionKind.HEAVY, Zone.TORSO, target_id=player.id))
    plan = choose_defend(session.state, player.id)
    assert plan.safe and plan.choice.kind in {ActionKind.WALK, ActionKind.RUN, ActionKind.DASH}
    action = commit_action(session.state, player.id, plan.choice)
    assert legality(session.state, player.id,
                    Choice(ActionKind.LIGHT, Zone.TORSO, target_id=enemy.id)) == "Already committed to an action"
    assert action.path[0][0] < enemy.action.strike_tick
    advance_to(session.state, enemy.action.strike_tick)
    assert not any(event.type == EventType.HIT and event.actor == enemy.id
                   for event in session.state.event_log)


def test_defend_turns_visible_shove_into_one_side_dash_slash_that_interrupts_it():
    session = duel()
    player, enemy = session.player, session.enemy
    player.speed = 40
    commit_action(session.state, enemy.id, Choice(ActionKind.SHOVE, target_id=player.id))

    plan = choose_defend(session.state, player.id)

    assert plan.choice.kind == ActionKind.DASH_LIGHT
    assert plan.choice.direction in {(0, -1), (0, 1)}
    player_action = commit_action(session.state, player.id, plan.choice)
    assert legality(session.state, player.id, Choice(ActionKind.LIGHT, Zone.TORSO))
    advance_to(session.state, player_action.strike_tick)
    assert any(event.type == EventType.HIT and event.actor == player.id
               and event.target == enemy.id for event in session.state.event_log)
    assert any(event.type == EventType.INTERRUPT and event.actor == enemy.id
               for event in session.state.event_log)
    assert not any(event.type == EventType.SHOVE for event in session.state.event_log)


def test_defend_side_dash_slash_can_interrupt_an_observed_dash_light():
    session = duel()
    player, enemy = session.player, session.enemy
    player.speed, enemy.speed = 40, 4
    commit_action(session.state, enemy.id,
                  Choice(ActionKind.DASH_LIGHT, Zone.TORSO, direction=(0, -1),
                         tiles=1, target_id=player.id))

    plan = choose_defend(session.state, player.id)

    assert plan.choice.kind == ActionKind.DASH_LIGHT
    assert plan.choice.direction in {(0, -1), (0, 1)}
    action = commit_action(session.state, player.id, plan.choice)
    advance_to(session.state, action.strike_tick)
    assert any(event.type == EventType.HIT and event.actor == player.id
               and event.target == enemy.id for event in session.state.event_log)
    assert any(event.type == EventType.INTERRUPT and event.actor == enemy.id
               for event in session.state.event_log)


def test_defend_parries_heavy_when_well_timed_and_strength_can_support_it():
    session = duel()
    player, enemy = session.player, session.enemy
    player.strength = 40
    enemy.strength = 1
    session.state.config["guard"]["defend_parry_window"] = 12
    session.state.config["guard"]["defend_hold"] = 20
    commit_action(session.state, enemy.id,
                  Choice(ActionKind.HEAVY, Zone.TORSO, target_id=player.id))

    plan = choose_defend(session.state, player.id)
    assert plan.choice.kind == ActionKind.GUARD
    assert "strength ratio" in plan.reason
    commit_action(session.state, player.id, plan.choice)
    advance_to(session.state, enemy.action.strike_tick)
    assert any(event.type == EventType.PARRY and event.actor == player.id
               and event.target == enemy.id for event in session.state.event_log)


def test_defend_never_selects_strength_overmatched_heavy_parry_when_escape_exists():
    session = duel()
    player, enemy = session.player, session.enemy
    player.strength, enemy.strength = 1, 40
    session.state.config["guard"]["defend_parry_window"] = 12
    session.state.config["guard"]["defend_hold"] = 20
    commit_action(session.state, enemy.id,
                  Choice(ActionKind.HEAVY, Zone.TORSO, target_id=player.id))

    plan = choose_defend(session.state, player.id)
    assert plan.choice.kind in {ActionKind.WALK, ActionKind.RUN, ActionKind.DASH}
    assert plan.safe


def test_defend_braces_when_every_escape_route_is_blocked_or_outside_floor():
    session = duel()
    player, enemy = session.player, session.enemy
    session.state.walkable = {player.pos, enemy.pos}
    commit_action(session.state, enemy.id,
                  Choice(ActionKind.HEAVY, Zone.TORSO, target_id=player.id))

    plan = choose_defend(session.state, player.id)
    assert plan.choice.kind == ActionKind.GUARD
    assert plan.choice.contextual_defense
    assert not plan.safe and plan.response == "brace"


def test_defend_braces_when_every_legal_step_arrives_after_fast_impact():
    session = duel()
    player, enemy = session.player, session.enemy
    enemy.speed = 50
    session.state.config["actions"]["HEAVY"]["windup"] = 1
    commit_action(session.state, enemy.id,
                  Choice(ActionKind.HEAVY, Zone.TORSO, target_id=player.id))

    plan = choose_defend(session.state, player.id)

    assert plan.choice.kind == ActionKind.GUARD
    assert not plan.safe and plan.response == "brace"
    assert "before impact" in plan.reason


def test_defend_escapes_all_secondary_visible_threats_not_only_first_attacker():
    config = load_config()
    config["group"]["vision_range"] = 6
    player = bare_fighter("player", pos=(4, 3), team_id="crew")
    first = bare_fighter("first", pos=(5, 3), team_id="raiders")
    second = bare_fighter("second", pos=(3, 3), team_id="raiders")
    state = CombatState([player, first, second], config=config)
    commit_action(state, first.id, Choice(ActionKind.LIGHT, Zone.TORSO,
                                          target_id=player.id))
    commit_action(state, second.id, Choice(ActionKind.HEAVY, Zone.TORSO,
                                           target_id=player.id))

    plan = choose_defend(state, player.id)
    assert plan.safe and plan.choice.kind in {ActionKind.WALK, ActionKind.RUN, ActionKind.DASH}
    move = commit_action(state, player.id, plan.choice)
    assert move.path[0][0] < min(first.action.strike_tick, second.action.strike_tick)
    advance_to(state, min(first.action.strike_tick, second.action.strike_tick))
    assert not any(event.type == EventType.HIT and event.target == player.id
                   for event in state.event_log)


def test_defend_tie_break_is_deterministic_without_mutating_rng_or_state():
    session = duel()
    player, enemy = session.player, session.enemy
    commit_action(session.state, enemy.id,
                  Choice(ActionKind.HEAVY, Zone.TORSO, target_id=player.id))
    before = session.state.to_data()

    first = choose_defend(session.state, player.id)
    second = choose_defend(session.state, player.id)

    assert first == second
    assert session.state.to_data() == before


def test_group_defend_evaluation_does_not_deepcopy_unseen_fighter_profile():
    config = load_config()
    config["group"]["vision_range"] = 2
    player = bare_fighter("player", pos=(0, 0), team_id="crew")
    enemy = bare_fighter("enemy", pos=(1, 0), team_id="raiders")
    unseen = bare_fighter("unseen", pos=(10, 10), team_id="crew")
    state = CombatState([player, enemy, unseen], config=config)
    commit_action(state, enemy.id, Choice(ActionKind.HEAVY, Zone.TORSO,
                                          target_id=player.id))

    class NoCopy:
        def __deepcopy__(self, memo):
            raise AssertionError("Defend must not clone unseen fighter profiles")

    unseen.personality = NoCopy()
    plan = choose_defend(state, player.id)

    assert plan.choice.kind in {ActionKind.WALK, ActionKind.RUN, ActionKind.DASH}


def test_defend_without_observed_threat_does_not_predict_hidden_choice():
    session = duel()
    enemy = session.enemy
    commit_action(session.state, enemy.id,
                  Choice(ActionKind.LIGHT, Zone.TORSO, target_id=session.player.id))
    enemy.pos = (12, 6)  # no longer visible; do not use its action as known information
    plan = choose_defend(session.state, session.player.id)
    assert plan.choice.kind == ActionKind.GUARD
    assert plan.choice.contextual_defense
    assert "No committed threat is visible" in plan.reason


def test_contextual_guard_parry_pays_configured_cost_and_expires():
    session = duel()
    player, enemy = session.player, session.enemy
    commit_action(session.state, enemy.id,
                  Choice(ActionKind.LIGHT, Zone.TORSO, target_id=player.id))
    guard = commit_action(session.state, player.id, choose_defend(session.state, player.id).choice)
    starting = player.stamina
    advance_to(session.state, enemy.action.strike_tick)
    parry = next(event for event in session.state.event_log if event.type == EventType.PARRY)
    assert parry.deltas["stamina_cost"] > 0
    assert player.stamina < starting
    assert guard.end_tick is not None
    advance_to(session.state, guard.end_tick)
    assert player.action is None


def test_v12_active_attack_session_migrates_with_locked_bearing_and_defend_config():
    session = duel()
    commit_action(session.state, session.player.id,
                  Choice(ActionKind.LIGHT, Zone.TORSO, target_id=session.enemy.id))
    data = deepcopy(session.to_data())
    data["version"] = data["state"]["schema_version"] = data["state"]["config"]["schema_version"] = 12
    data["notebook"]["version"] = 12
    serialized = next(row for row in data["state"]["fighters"] if row["id"] == session.player.id)
    serialized["action"].pop("attack_bearing")
    serialized["action"].pop("bearing_lock_tick")
    serialized["action"].pop("contextual_defense")
    for key in ("defend_setup_cost", "defend_parry_cost", "defend_hold_cost_per_tick",
                "defend_raise", "defend_hold", "defend_lower", "defend_parry_window"):
        data["state"]["config"]["guard"].pop(key)
    restored = DuelSession.from_data(data)
    assert restored.to_data()["version"] == 30
    assert restored.player.action.attack_bearing == (1, 0)
    assert restored.state.config["guard"]["defend_parry_window"] > 0


def test_v13_active_session_migrates_new_action_contracts_without_losing_commitment():
    session = duel()
    action = commit_action(session.state, session.player.id,
                           Choice(ActionKind.THRUST, Zone.TORSO,
                                  target_id=session.enemy.id))
    data = deepcopy(session.to_data())
    data["version"] = data["state"]["schema_version"] = data["state"]["config"]["schema_version"] = 13
    data["notebook"]["version"] = 13
    data["state"]["config"]["actions"]["THRUST"].pop("damage_type")
    data["state"]["config"]["guard"].pop("riposte_disarm_windup_mult")
    data["state"]["config"]["tactics"].pop("feint_disrupt_ticks")
    restored = DuelSession.from_data(data)
    assert restored.to_data()["version"] == 30
    assert restored.player.action.strike_tick == action.strike_tick
    assert restored.player.action.attack_bearing == action.attack_bearing
    assert restored.state.config["actions"]["THRUST"]["damage_type"] == "PIERCING"
    assert restored.state.config["guard"]["riposte_disarm_windup_mult"] == 0.3
    assert restored.state.config["tactics"]["feint_disrupt_ticks"] == 6


def test_v14_active_session_migrates_legacy_accuracy_modifiers_and_preserves_commitment():
    session = duel()
    action = commit_action(session.state, session.player.id,
                           Choice(ActionKind.HEAVY, Zone.HEAD,
                                  target_id=session.enemy.id))
    data = deepcopy(session.to_data())
    data["version"] = data["state"]["schema_version"] = data["state"]["config"]["schema_version"] = 14
    data["notebook"]["version"] = 14
    config = data["state"]["config"]
    config["stats"]["fatigue_accuracy"] = 0.7
    config["stats"]["pain_accuracy"] = 1
    config["stats"]["fear_accuracy"] = 0.2
    config["anger"]["acc_penalty"] = 0.3
    config["zones"]["heavy_acc_mod"] = 2
    config["actions"]["HEAVY"]["acc_mod"] = 2
    config["wounds"]["HEAD"]["DEEP"]["accuracy_mult"] = 0.6
    config["weapon_integrity"]["eye_accuracy_penalty_per_eye"] = 0.18
    config["weapon_integrity"]["eye_accuracy_penalty_max"] = 0.35
    restored = DuelSession.from_data(data)
    migrated = restored.state.config
    assert restored.to_data()["version"] == 30
    assert restored.player.action.strike_tick == action.strike_tick
    assert restored.player.action.aim == action.aim
    assert restored.player.accuracy == session.player.accuracy
    assert migrated["stats"]["fatigue_accuracy"] == 0
    assert migrated["stats"]["pain_accuracy"] == 0
    assert migrated["stats"]["fear_accuracy"] == 0.25
    assert migrated["anger"]["acc_penalty"] == 0.2
    assert migrated["zones"]["heavy_acc_mod"] == 0
    assert migrated["actions"]["HEAVY"]["acc_mod"] == 0
    assert "accuracy_mult" not in migrated["wounds"]["HEAD"]["DEEP"]
    assert migrated["weapon_integrity"]["eye_accuracy_penalty_per_eye"] == 0


def test_v15_active_session_migrates_anatomical_catalogue_and_legacy_wound():
    from pirates.duel_system.model import Severity, Wound

    session = duel()
    action = commit_action(session.state, session.player.id,
                           Choice(ActionKind.LIGHT, Zone.TORSO,
                                  target_id=session.enemy.id))
    session.enemy.wounds.append(Wound(Zone.HAND, Severity.CUT, 2))
    data = deepcopy(session.to_data())
    data["version"] = data["state"]["schema_version"] = data["state"]["config"]["schema_version"] = 15
    data["notebook"]["version"] = 15
    data["state"]["config"].pop("anatomy_sites")
    enemy = next(row for row in data["state"]["fighters"] if row["id"] == session.enemy.id)
    enemy["wounds"][0].pop("site")
    enemy["wounds"][0].pop("side")

    restored = DuelSession.from_data(data)
    assert restored.to_data()["version"] == 30
    assert restored.state.config["anatomy_sites"]["HEAD"]
    assert restored.player.action.strike_tick == action.strike_tick
    assert restored.enemy.wounds[0].site == "HAND"
    assert restored.enemy.wounds[0].side is None


def test_v16_session_migration_adds_wrist_sites_and_explicit_weapon_hand():
    session = duel()
    action = commit_action(session.state, session.player.id,
                           Choice(ActionKind.LIGHT, Zone.TORSO,
                                  target_id=session.enemy.id))
    data = deepcopy(session.to_data())
    data["version"] = data["state"]["schema_version"] = data["state"]["config"]["schema_version"] = 16
    data["notebook"]["version"] = 16
    data["state"]["config"]["anatomy_sites"]["HAND"] = [
        "LEFT_PALM", "RIGHT_PALM", "LEFT_FINGERS", "RIGHT_FINGERS",
        "LEFT_THUMB", "RIGHT_THUMB",
    ]
    for fighter in data["state"]["fighters"]:
        fighter.pop("weapon_hand", None)
        fighter["permanent_injuries"].pop("severed_hands", None)

    restored = DuelSession.from_data(data)
    assert restored.to_data()["version"] == 30
    assert restored.state.config["anatomy_sites"]["HAND"][-2:] == ["LEFT_WRIST", "RIGHT_WRIST"]
    assert restored.player.weapon_hand == "RIGHT"
    assert restored.player.action.strike_tick == action.strike_tick
