"""P7 deterministic multi-actor targeting, occupancy, and timing contracts."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from pirates.duel_system.ai import movement_choice, observe, score_actions
from pirates.duel_system.config import load_config
from pirates.duel_system.engine import Choice, advance_one_tick, check_end_conditions, commit_action, legality
from pirates.duel_system.model import ActionKind, CombatState, EventType, Personality, Severity, Wound, Zone
from pirates.duel_system.perception import apply_witnessed_morale, refresh_contacts
from pirates.duel_coach import coach, strategy_rows
from pirates.duel_system.session import DuelSession
from pirates.duel_ui import DuelUI
from pirates.duel_system.render_model import build_render_model, format_ascii
from pirates.duel_system.exchange_log import format_exchange
from test_duel_system_foundation import bare_fighter


def encounter(*fighters, seed=19):
    return CombatState(list(fighters), rng_seed=seed)


def test_multi_actor_state_requires_explicit_teams_and_roundtrips_membership():
    fighters = [bare_fighter("player", pos=(1, 1), team_id="player"),
                bare_fighter("raider_a", pos=(5, 1), team_id="raiders"),
                bare_fighter("raider_b", pos=(5, 3), team_id="raiders")]
    state = encounter(*fighters)
    assert len(state.fighters) == 3
    restored = CombatState.from_data(state.to_data())
    assert [fighter.team_id for fighter in restored.fighters] == ["player", "raiders", "raiders"]

    with pytest.raises(ValueError, match="team"):
        encounter(bare_fighter("player"), bare_fighter("raider"), bare_fighter("second"))


def test_explicit_attack_target_is_locked_and_allies_are_not_legal_targets():
    player = bare_fighter("player", pos=(1, 1), team_id="player", strength=100, accuracy=100)
    ally = bare_fighter("ally", pos=(1, 2), team_id="player")
    near = bare_fighter("near", pos=(2, 2), team_id="raiders")
    far = bare_fighter("far", pos=(2, 0), team_id="raiders")
    state = encounter(player, ally, near, far)

    choice = Choice(ActionKind.HEAVY, Zone.TORSO, target_id="far")
    assert "hostile" in legality(state, player.id,
                                 Choice(ActionKind.LIGHT, Zone.TORSO, target_id="ally")).lower()
    action = commit_action(state, player.id, choice)
    assert action.target_id == "far"
    restored = CombatState.from_data(state.to_data())
    assert restored.fighters[0].action.target_id == "far"

    deadline = action.strike_tick
    while state.tick < deadline:
        advance_one_tick(state)
    assert far.wounds
    assert not near.wounds
    hit = next(event for event in state.event_log if event.type == EventType.HIT)
    assert hit.target == "far"


def test_hidden_target_eye_state_does_not_leak_through_target_legality():
    outcomes = []
    for eyes in (0, 2):
        config = load_config()
        config["group"]["vision_range"] = 1
        player = bare_fighter("player", pos=(1, 1), team_id="player")
        ally = bare_fighter("ally", pos=(1, 2), team_id="player")
        target = bare_fighter("far", pos=(7, 1), team_id="raiders")
        target.permanent_injuries.eyes_damaged = eyes
        state = CombatState([player, ally, target], config=config)
        outcomes.append(legality(state, player.id,
                                 Choice(ActionKind.LIGHT, Zone.EYE, target_id=target.id)))
    assert outcomes == ["Target is not currently visible"] * 2


def test_simultaneous_contested_move_blocks_all_claimants_without_id_priority():
    player = bare_fighter("player", pos=(6, 4), team_id="player")
    left = bare_fighter("a_raider", pos=(1, 1), team_id="raiders")
    right = bare_fighter("z_raider", pos=(3, 1), team_id="raiders")
    state = encounter(player, left, right)
    commit_action(state, left.id, Choice(ActionKind.WALK, direction=(1, 0)))
    commit_action(state, right.id, Choice(ActionKind.WALK, direction=(-1, 0)))
    deadline = max(left.action.end_tick, right.action.end_tick)
    while state.tick < deadline:
        advance_one_tick(state)

    assert left.pos == (1, 1) and right.pos == (3, 1)
    blocked = [event for event in state.event_log
               if event.type == EventType.STATUS and "MOVEMENT_CONFLICT" in event.cause_tags]
    assert {event.actor for event in blocked} == {left.id, right.id}
    assert {tuple(event.deltas["position"]) for event in blocked} == {(2, 1)}


def test_simultaneous_chain_can_enter_vacated_tiles_but_cycles_and_bodies_block():
    player = bare_fighter("player", pos=(6, 4), team_id="player")
    first = bare_fighter("first", pos=(1, 1), team_id="raiders")
    second = bare_fighter("second", pos=(2, 1), team_id="raiders")
    state = encounter(player, first, second)
    commit_action(state, first.id, Choice(ActionKind.WALK, direction=(1, 0)))
    commit_action(state, second.id, Choice(ActionKind.WALK, direction=(1, 0)))
    deadline = first.action.end_tick
    while state.tick < deadline:
        advance_one_tick(state)
    assert first.pos == (2, 1) and second.pos == (3, 1)

    state = encounter(bare_fighter("player", pos=(6, 4), team_id="player"),
                      bare_fighter("first", pos=(1, 1), team_id="raiders"),
                      bare_fighter("second", pos=(2, 1), team_id="raiders"))
    first, second = state.fighters[1:]
    commit_action(state, first.id, Choice(ActionKind.WALK, direction=(1, 0)))
    commit_action(state, second.id, Choice(ActionKind.WALK, direction=(-1, 0)))
    deadline = first.action.end_tick
    while state.tick < deadline:
        advance_one_tick(state)
    assert first.pos == (1, 1) and second.pos == (2, 1)
    assert len([event for event in state.event_log if "MOVEMENT_CONFLICT" in event.cause_tags]) == 2

    state = encounter(bare_fighter("player", pos=(6, 4), team_id="player"),
                      bare_fighter("mover", pos=(1, 1), team_id="raiders"),
                      bare_fighter("corpse", pos=(2, 1), team_id="player", blood=0))
    mover = state.fighters[1]
    commit_action(state, mover.id, Choice(ActionKind.WALK, direction=(1, 0)))
    deadline = mover.action.end_tick
    while state.tick < deadline:
        advance_one_tick(state)
    assert mover.pos == (1, 1)
    assert any("MOVEMENT_BLOCKED" in event.cause_tags and event.target == "corpse"
               for event in state.event_log)


def test_melee_strike_cannot_pass_through_an_intervening_ally():
    player = bare_fighter("player", pos=(1, 1), team_id="player", strength=100, accuracy=100)
    ally = bare_fighter("ally", pos=(2, 2), team_id="player")
    enemy = bare_fighter("enemy", pos=(3, 1), team_id="raiders")
    player.equipment.weapon.damage_type = "PIERCING"
    state = encounter(player, ally, enemy)
    action = commit_action(state, player.id,
                           Choice(ActionKind.THRUST, Zone.TORSO, target_id=enemy.id))
    ally.pos = (2, 1)
    while state.tick < action.strike_tick:
        advance_one_tick(state)

    assert enemy.blood == 100
    whiff = next(event for event in state.event_log if event.type == EventType.WHIFF)
    assert whiff.target == enemy.id
    assert "ALLY_BLOCKED_LINE" in whiff.cause_tags


def test_v6_snapshot_migration_adds_team_and_target_defaults_without_changing_deadlines():
    player = bare_fighter("player", pos=(1, 1), team_id="player")
    enemy = bare_fighter("ronin", pos=(2, 1), team_id="enemy")
    state = encounter(player, enemy)
    action = commit_action(state, player.id, Choice(ActionKind.RUN, direction=(0, 1), tiles=2))
    deadline = action.end_tick
    old = deepcopy(state.to_data())
    old["schema_version"] = old["config"]["schema_version"] = 6
    old.pop("leaders", None)
    for fighter in old["fighters"]:
        fighter.pop("team_id", None)
        if fighter["action"]:
            fighter["action"].pop("target_id", None)
    old["config"].pop("group", None)

    restored = CombatState.from_data(old)
    assert restored.config["schema_version"] == 18
    assert restored.fighters[0].action.end_tick == deadline
    assert restored.fighters[0].team_id != restored.fighters[1].team_id
    assert restored.fighters[0].action.target_id is None
    replay = CombatState.from_data(old)
    while restored.tick < deadline:
        advance_one_tick(restored)
        advance_one_tick(replay)
    assert restored.to_data() == replay.to_data()


def test_v6_session_migration_preserves_active_path_deadlines_and_replay():
    session = DuelSession.new(seed=40, guide_mode="off")
    session.selected = Choice(ActionKind.RUN, direction=(0, 1), tiles=2)
    assert session.confirm()
    old = deepcopy(session.to_data())
    old["version"] = old["notebook"]["version"] = 6
    old["exchange"] = None
    old.pop("ai_outcome_cursor", None)
    state = old["state"]
    state["schema_version"] = state["config"]["schema_version"] = 6
    state.pop("leaders", None)
    state["config"].pop("group", None)
    for fighter in state["fighters"]:
        fighter.pop("team_id", None)
        if fighter["action"]:
            fighter["action"].pop("target_id", None)
    deadline = session.player.action.end_tick
    expected_path = deepcopy(session.player.action.path)

    restored, replay = DuelSession.from_data(old), DuelSession.from_data(old)
    assert restored.state.config["schema_version"] == 18
    assert restored.player.action.end_tick == deadline
    assert restored.player.action.path == expected_path
    assert restored.exchange is not None and set(restored.exchange["participants"]) == {
        fighter.id for fighter in restored.state.fighters}
    for _ in range(8):
        restored.step()
        replay.step()
    assert restored.to_data() == replay.to_data()


def test_group_encounter_continues_until_all_hostiles_are_inactive_and_records_distinct_outcomes():
    player = bare_fighter("player", pos=(1, 1), team_id="player")
    first = bare_fighter("first", pos=(4, 1), team_id="raiders", unconscious=True)
    second = bare_fighter("second", pos=(5, 1), team_id="raiders")
    state = encounter(player, first, second)
    check_end_conditions(state)
    assert state.end_reason is None

    second.fled = True
    check_end_conditions(state)
    assert state.end_reason == "group_defeated"
    assert state.winner == player.id
    ended = next(event for event in state.event_log if event.type == EventType.FIGHT_END)
    assert ended.deltas["terminal_outcomes"] == {"first": "unconscious", "second": "flee"}


def test_group_session_commits_once_from_one_snapshot_and_faster_actors_wait(monkeypatch):
    player = bare_fighter("player", pos=(1, 1), team_id="player", speed=30)
    fast = bare_fighter("fast", pos=(4, 1), team_id="raiders", speed=30)
    slow = bare_fighter("slow", pos=(3, 3), team_id="raiders", speed=5)
    session = DuelSession.new_group([player, fast, slow], guide_mode="off", seed=31)
    observed_snapshots = []

    def choose(snapshot, fighter_id):
        observed_snapshots.append((snapshot.tick, tuple((actor.id, actor.action) for actor in snapshot.fighters)))
        if fighter_id == "slow":
            return SimpleNamespace(category="HEAVY", choice=Choice(
                ActionKind.HEAVY, Zone.TORSO, target_id="player"))
        return SimpleNamespace(category="WAIT", choice=Choice(ActionKind.WAIT))

    monkeypatch.setattr("pirates.duel_system.session.choose_action", choose)
    session.selected = Choice(ActionKind.WAIT)
    assert session.confirm()
    assert len(observed_snapshots) == 2
    assert observed_snapshots[0] == observed_snapshots[1]
    starts = [event for event in session.state.event_log if event.type == EventType.ACTION_START]
    assert {event.actor for event in starts} == {"player", "fast", "slow"}
    slow_end = session.state.fighter("slow").action.end_tick
    assert len(starts) == 3

    while session.state.tick < slow_end:
        if session.exchange is None:
            pytest.fail("exchange closed before its slowest committed actor")
        session.step()
    starts = [event for event in session.state.event_log if event.type == EventType.ACTION_START]
    assert {actor.id: sum(event.actor == actor.id for event in starts)
            for actor in session.state.fighters} == {"player": 1, "fast": 1, "slow": 1}
    assert session.exchange is None


def test_incapacitated_group_actor_cannot_finish_committed_movement_or_strike():
    player = bare_fighter("player", pos=(1, 1), team_id="players")
    mover = bare_fighter("mover", pos=(3, 1), team_id="raiders")
    reserve = bare_fighter("reserve", pos=(5, 3), team_id="raiders")
    state = encounter(player, mover, reserve)
    movement = commit_action(state, mover.id,
                             Choice(ActionKind.DASH, direction=(1, 0), tiles=1))
    movement_tick, old_position = movement.path[0][0], mover.pos
    state.tick = movement_tick-1
    mover.unconscious = True
    advance_one_tick(state)
    assert mover.pos == old_position
    assert mover.action is None
    interrupted = next(event for event in state.event_log
                       if event.type == EventType.INTERRUPT and event.actor == mover.id)
    assert interrupted.deltas == {"kind": "DASH", "reason": "unconscious"}

    player = bare_fighter("player", pos=(1, 1), team_id="players")
    attacker = bare_fighter("attacker", pos=(2, 1), team_id="raiders")
    reserve = bare_fighter("reserve", pos=(5, 3), team_id="raiders")
    state = encounter(player, attacker, reserve)
    attack = commit_action(state, attacker.id,
                           Choice(ActionKind.LIGHT, Zone.TORSO, target_id=player.id))
    state.tick = attack.strike_tick-1
    attacker.unconscious = True
    blood = player.blood
    advance_one_tick(state)
    assert player.blood == blood
    assert attacker.action is None
    assert not any(event.type == EventType.HIT and event.actor == attacker.id
                   for event in state.event_log)


def test_terminal_actor_stale_commitment_does_not_block_group_exchange(monkeypatch):
    player = bare_fighter("player", pos=(1, 1), team_id="players")
    incapacitated = bare_fighter("incapacitated", pos=(2, 1), team_id="raiders")
    active = bare_fighter("active", pos=(5, 3), team_id="raiders")
    state = encounter(player, incapacitated, active)
    commit_action(state, incapacitated.id,
                  Choice(ActionKind.LIGHT, Zone.TORSO, target_id=player.id))
    incapacitated.unconscious, incapacitated.terminal_reason = True, "unconscious"
    session = DuelSession.from_state(state, guide_mode="off")
    monkeypatch.setattr("pirates.duel_system.session.choose_action",
                        lambda snapshot, fighter_id: SimpleNamespace(
                            category="WAIT", choice=Choice(ActionKind.WAIT)))

    assert session.resolve_round(Choice(ActionKind.WAIT))
    assert not state.end_reason
    assert incapacitated.action is None
    assert player.blood == 100


def test_group_session_save_load_preserves_exchange_commitments_and_replay(monkeypatch):
    player = bare_fighter("player", pos=(1, 1), team_id="player")
    first = bare_fighter("first", pos=(4, 1), team_id="raiders")
    second = bare_fighter("second", pos=(5, 2), team_id="raiders")
    session = DuelSession.new_group([player, first, second], guide_mode="off", seed=37)
    monkeypatch.setattr("pirates.duel_system.session.choose_action",
                        lambda state, fighter_id: SimpleNamespace(
                            category="WAIT", choice=Choice(ActionKind.WAIT)))
    session.selected = Choice(ActionKind.WAIT)
    assert session.confirm()
    session.step()
    restored = DuelSession.from_data(session.to_data())
    assert restored.exchange == session.exchange
    assert restored.to_data() == session.to_data()
    for _ in range(5):
        session.step()
        restored.step()
    assert restored.to_data() == session.to_data()


def test_group_guards_release_at_configured_exchange_deadline_without_recommit(monkeypatch):
    actors = [bare_fighter("player", pos=(1, 1), team_id="players"),
              bare_fighter("first", pos=(4, 1), team_id="raiders"),
              bare_fighter("second", pos=(5, 2), team_id="raiders")]
    session = DuelSession.new_group(actors, guide_mode="off", seed=38)
    monkeypatch.setattr("pirates.duel_system.session.choose_action",
                        lambda state, fighter_id: SimpleNamespace(
                            category="GUARD", choice=Choice(ActionKind.GUARD)))
    session.selected = Choice(ActionKind.GUARD)
    assert session.confirm()
    deadline = session.exchange["guard_deadline"]
    assert deadline == session.exchange["start_tick"] + session.state.config["group"]["guard_exchange_ticks"]

    for _ in range(deadline+30):
        if session.exchange is None:
            break
        session.step()
    assert session.exchange is None and session.state.tick > deadline
    assert all(fighter.action is None for fighter in session.state.fighters)
    starts = [event for event in session.state.event_log if event.type == EventType.ACTION_START]
    assert {fighter.id: sum(event.actor == fighter.id for event in starts)
            for fighter in session.state.fighters} == {"player": 1, "first": 1, "second": 1}
    assert sum(event.type == EventType.ACTION_END and event.actor == "player"
               for event in session.state.event_log) == 1


def test_group_fear_break_counts_each_decision_and_yields_after_three(monkeypatch):
    actors = [bare_fighter("player", pos=(1, 1), team_id="players"),
              bare_fighter("first", pos=(4, 1), team_id="raiders"),
              bare_fighter("second", pos=(5, 2), team_id="raiders")]
    session = DuelSession.new_group(actors, guide_mode="off", seed=39)
    session.state.fighter("first").morale = session.state.fighter("second").morale = 0
    monkeypatch.setattr("pirates.duel_system.session.choose_action",
                        lambda state, fighter_id: SimpleNamespace(
                            category="WAIT", choice=Choice(ActionKind.WAIT)))

    assert session.resolve_round(Choice(ActionKind.WAIT))
    assert [fighter.fear_decisions for fighter in session.state.fighters[1:]] == [1, 1]
    assert not session.state.end_reason
    assert session.resolve_round(Choice(ActionKind.WAIT))
    assert [fighter.fear_decisions for fighter in session.state.fighters[1:]] == [2, 2]
    assert not session.state.end_reason
    assert not session.resolve_round(Choice(ActionKind.WAIT))

    assert session.state.end_reason == "group_defeated"
    assert session.state.winner == "player"
    assert all(fighter.yielded and fighter.fear_decisions == 3
               for fighter in session.state.fighters[1:])
    decisions = [event for event in session.state.event_log if event.type == EventType.DECISION]
    assert {fighter.id: sum(event.actor == fighter.id for event in decisions)
            for fighter in session.state.fighters[1:]} == {"first": 3, "second": 3}
    end_index = next(index for index, event in enumerate(session.state.event_log)
                     if event.type == EventType.FIGHT_END)
    yield_indices = [index for index, event in enumerate(session.state.event_log)
                     if event.type == EventType.DECISION and event.deltas["category"] == "YIELD"]
    assert yield_indices and max(yield_indices) < end_index


def test_group_render_limits_threat_summaries_and_target_selection_is_explicit():
    actors = [bare_fighter("player", pos=(6, 3), team_id="player")]
    actors.extend(bare_fighter(f"raider_{index}", pos=(1, y), team_id="raiders")
                  for index, y in enumerate((1, 2, 3, 4), 1))
    actors.append(bare_fighter("distant", pos=(12, 1), team_id="raiders"))
    state = encounter(*actors)
    state.config["group"]["vision_range"] = 5
    state.emit(EventType.ACTION_START, "distant", "distant",
               deltas={"kind": "HEAVY"})
    session = DuelSession.from_state(state, guide_mode="off")
    model = build_render_model(state, session.notebook.public_view(), guide_mode="off")
    assert {actor.actor_id for actor in model.actors} == {
        "player", "raider_1", "raider_2", "raider_3", "raider_4"}
    assert len(model.target_options) == 4
    assert len(model.threats) == 3 and model.threat_overflow == 1
    assert all("distant" not in line for line in model.full_log)
    assert "Visible threats:" in "\n".join(format_ascii(model))

    ui = DuelUI(session, rounds=False)
    ui.activate("Heavy")
    assert ui.panel == "targets"
    assert "Choose a visible hostile:" in "\n".join(ui.rows(ui.model()))
    chosen = model.target_options[1].actor_id
    ui.activate("Target 2")
    assert ui.panel == "zones" and ui.target_id == chosen
    ui.activate("Torso")
    assert ui.pending is not None and ui.pending.target_id == chosen


def test_group_event_witnesses_and_rendering_do_not_reveal_occluded_choice_or_state():
    player = bare_fighter("player", pos=(1, 1), team_id="player")
    blocker = bare_fighter("blocker", pos=(2, 1), team_id="raiders")
    hidden = bare_fighter("hidden", pos=(4, 1), team_id="raiders", strength=99)
    state = encounter(player, blocker, hidden)
    event = state.emit(EventType.ACTION_START, hidden.id, player.id,
                       deltas={"kind": "HEAVY", "strength": 99})
    assert player.id not in event.witnesses
    hidden_wound = state.emit(EventType.WOUND, hidden.id, blocker.id, Zone.TORSO,
                              Severity.DEEP, deltas={"bleed_per_turn": 6})
    hidden_bleed = state.emit(EventType.POOL_CHANGE, hidden.id,
                              deltas={"blood": -0.42, "bleed_loss": 0.42,
                                      "bleed_rate_per_turn": 6},
                              cause_tags=("BLEEDING",))
    assert player.id not in hidden_wound.witnesses + hidden_bleed.witnesses
    blocker.strength, blocker.stamina, blocker.fatigue, blocker.blood = 99, 1, 90, 12
    session = DuelSession.from_state(state, guide_mode="off")
    ui = DuelUI(session, rounds=True)
    assert hidden_wound not in ui._public_exchange_events([hidden_wound, hidden_bleed])
    assert hidden_bleed not in ui._public_exchange_events([hidden_wound, hidden_bleed])
    private_recap = format_exchange([hidden_bleed], {hidden.id: "Unknown hostile"},
                                    private_actor_ids={hidden.id})
    assert any("appears to be bleeding" in line and "exact blood loss and rate are unknown" in line
               for line in private_recap)
    assert all("0.42" not in line and "6/turn" not in line for line in private_recap)
    before = state.to_data()
    model = build_render_model(state, session.notebook.public_view(), guide_mode="off")
    assert model.enemy_name == "Blocker"
    assert model.enemy_level == 0 and model.enemy_mood == "UNKNOWN"
    assert not model.enemy_pools and not model.enemy_readout and not model.enemy_observed
    assert not model.enemy_wounds and not model.enemy_armor and not model.enemy_injury_rows
    assert all("99" not in value for _, value in model.enemy_sheet)
    assert all("99" not in value for value in model.comparison)
    assert all("hidden" not in line and "99" not in line and "heavy" not in line.lower()
               for line in model.full_log)
    assert all("bleed_loss" not in line and "bleeding wound" not in line.lower()
               for line in model.full_log)
    assert all(actor.actor_id != "hidden" for actor in model.actors)
    assert state.to_data() == before

    hidden.strength, hidden.pain, hidden.morale = 1, 100, 0
    hidden.action = commit_action(state, hidden.id, Choice(ActionKind.WAIT))
    hidden.personality = object()
    public_after = build_render_model(state, session.notebook.public_view(), guide_mode="off")
    assert public_after == model


def test_witnessed_group_wound_gets_coarse_bleeding_cue_without_private_rate():
    player = bare_fighter("player", pos=(1, 1), team_id="players")
    enemy = bare_fighter("enemy", pos=(2, 1), team_id="raiders")
    ally = bare_fighter("ally", pos=(1, 2), team_id="players")
    state = encounter(player, enemy, ally)
    state.emit(EventType.WOUND, player.id, enemy.id, Zone.TORSO, Severity.DEEP,
               deltas={"bleed_per_turn": 6})
    session = DuelSession.from_state(state, guide_mode="off")
    model = build_render_model(state, session.notebook.public_view(), guide_mode="off")
    assert any("bleeding wound" in line.lower() for line in model.full_log)
    assert any(popup.text == "Bleeding wound" for popup in model.popups)
    assert not model.enemy_wounds and not model.enemy_pools
    assert all("6/turn" not in line and "bleed_per_turn" not in line for line in model.full_log)


def test_group_ai_observation_and_scores_ignore_unobserved_exact_enemy_pools_wounds_and_strength():
    player = bare_fighter("player", pos=(1, 1), team_id="players")
    enemy = bare_fighter("enemy", pos=(2, 1), team_id="raiders")
    ally = bare_fighter("ally", pos=(1, 2), team_id="players")
    state = encounter(player, enemy, ally)
    event = state.emit(EventType.WOUND, player.id, enemy.id, Zone.TORSO, Severity.CUT,
                       deltas={"bleed_per_turn": 3})
    assert player.id in event.witnesses
    baseline_view = observe(state, player.id)
    baseline_scores = score_actions(state, player.id)
    altered = state.clone()
    changed = altered.fighter(enemy.id)
    changed.blood, changed.strength = 1, 99
    changed.wounds = []
    changed.wounds.append(Wound(Zone.HEAD, Severity.DEEP, 20))
    changed.stability = 0
    assert observe(altered, player.id) == baseline_view
    assert score_actions(altered, player.id) == baseline_scores
    assert baseline_view.opponent_bleeding
    assert baseline_view.opponent_blood == state.config["blood"]["start"]
    assert baseline_view.opponent_strength == state.config["defaults"]["strength"]


def test_group_ai_unsteady_opening_uses_fresh_witnessed_state_only():
    player = bare_fighter("player", pos=(1, 1), team_id="players")
    enemy = bare_fighter("enemy", pos=(2, 1), team_id="raiders")
    ally = bare_fighter("ally", pos=(1, 2), team_id="players")
    state = encounter(player, enemy, ally)
    state.emit(EventType.STABILITY, enemy.id,
               deltas={"source": "HIT_IMPACT", "loss": 12, "band": "OFF_BALANCE"})
    assert observe(state, player.id).opponent_unsteady
    state.tick = state.config["time"]["ticks_per_turn"] + 1
    assert not observe(state, player.id).opponent_unsteady


def test_hearing_only_contact_is_coarse_and_expires_without_revealing_location():
    player = bare_fighter("player", pos=(1, 1), team_id="player")
    blocker = bare_fighter("blocker", pos=(2, 1), team_id="player")
    hostile = bare_fighter("hostile", pos=(4, 1), team_id="raiders")
    state = encounter(player, blocker, hostile)
    state.config["group"]["vision_range"] = 2
    state.config["group"]["hearing_range"] = 6
    assert not state.can_see(player, hostile)

    event = state.emit(EventType.MOVE, hostile.id,
                       deltas={"from": (3, 1), "to": hostile.pos})
    assert player.id in event.hearers and player.id not in event.witnesses
    refresh_contacts(state, [event])
    memory = player.contact_memory[hostile.id]
    assert memory["position"] is None
    assert memory["bearing"] == (1, 0)
    identity, estimate, visible = state.perceived_hostile_position(player.id)
    assert identity == hostile.id and estimate != hostile.pos and not visible
    observation = observe(state, player.id)
    assert observation.opponent_phase == "UNSEEN"

    state.tick += state.config["group"]["contact_memory_exchanges"] * state.config["time"]["ticks_per_turn"] + 1
    refresh_contacts(state)
    assert state.perceived_hostile_position(player.id) is None
    restored = CombatState.from_data(state.to_data())
    assert restored.fighter(player.id).contact_memory == {}

    no_contact = encounter(
        bare_fighter("player", pos=(1, 1), team_id="players"),
        bare_fighter("ally", pos=(2, 1), team_id="players"),
        bare_fighter("hidden", pos=(4, 1), team_id="raiders"))
    no_contact.config["group"]["vision_range"] = 2
    preview = build_render_model(no_contact, DuelSession.from_state(no_contact).notebook.public_view(),
                                 guide_mode="off")
    assert preview.enemy_name == "No visible hostile" and not preview.enemy_visible
    attack_preview = DuelSession.from_state(no_contact).select(
        Choice(ActionKind.LIGHT, Zone.TORSO))
    assert not attack_preview.valid and attack_preview.enemy_strike_tick is None


def test_witnessed_morale_is_local_bounded_and_leader_loss_dominates_once_per_exchange():
    player = bare_fighter("player", pos=(1, 1), team_id="players")
    leader = bare_fighter("leader", pos=(4, 1), team_id="raiders")
    nearby = bare_fighter("nearby", pos=(1, 2), team_id="raiders")
    remote = bare_fighter("remote", pos=(12, 5), team_id="raiders")
    state = encounter(player, leader, nearby, remote)
    state.config["group"]["vision_range"] = 1
    state.config["group"]["hearing_range"] = 4
    state.leaders["raiders"] = leader.id
    for fighter in (leader, nearby, remote):
        fighter.morale = 70

    wound = state.emit(EventType.WOUND, player.id, leader.id,
                       deltas={"severity": "CUT"})
    loss = state.emit(EventType.TERMINAL_OUTCOME, leader.id,
                      deltas={"reason": "unconscious", "leader": True})
    assert nearby.id in wound.witnesses + wound.hearers
    assert nearby.id in loss.hearers and nearby.id not in loss.witnesses
    assert remote.id not in loss.witnesses + loss.hearers

    apply_witnessed_morale(state, [wound, loss], exchange_id=3)
    assert nearby.morale == 50
    assert nearby.morale_reacted_exchange == 3
    assert remote.morale == 70 and remote.morale_reacted_exchange is None
    changes = [event for event in state.event_log
               if "WITNESSED_GROUP_MORALE" in event.cause_tags]
    assert len(changes) == 1
    assert changes[0].deltas["group_response"] == "LEADER_LOST"
    apply_witnessed_morale(state, [wound, loss], exchange_id=3)
    assert nearby.morale == 50
    assert len([event for event in state.event_log
                if "WITNESSED_GROUP_MORALE" in event.cause_tags]) == 1


def test_group_session_applies_observed_leader_loss_at_exchange_boundary(monkeypatch):
    player = bare_fighter("player", pos=(1, 1), team_id="players")
    leader = bare_fighter("leader", pos=(4, 1), team_id="raiders", blood=0)
    nearby = bare_fighter("nearby", pos=(1, 2), team_id="raiders")
    remote = bare_fighter("remote", pos=(12, 5), team_id="raiders")
    state = encounter(player, leader, nearby, remote)
    state.config["group"]["vision_range"] = 1
    state.config["group"]["hearing_range"] = 4
    state.leaders["raiders"] = leader.id
    session = DuelSession.from_state(state, guide_mode="off")
    monkeypatch.setattr("pirates.duel_system.session.choose_action",
                        lambda snapshot, fighter_id: SimpleNamespace(
                            category="WAIT", choice=Choice(ActionKind.WAIT)))

    session.step()
    while session.exchange is not None:
        session.step()
    assert nearby.morale == 50
    assert remote.morale == 70
    assert nearby.morale_reacted_exchange == 0
    terminal = next(event for event in state.event_log
                    if event.type == EventType.TERMINAL_OUTCOME and event.actor == leader.id)
    assert nearby.id in terminal.hearers and nearby.id not in terminal.witnesses
    assert any(event.actor == nearby.id and "WITNESSED_GROUP_MORALE" in event.cause_tags
               for event in state.event_log)


def test_disciplined_group_movement_preserves_approach_spacing_while_recruits_crowd(monkeypatch):
    player = bare_fighter("player", pos=(4, 1), team_id="players")
    recruit = bare_fighter("recruit", pos=(2, 2), team_id="raiders")
    ally = bare_fighter("ally", pos=(3, 1), team_id="raiders")
    blocker = bare_fighter("blocker", pos=(10, 6), team_id="players")
    cfg = load_config()
    profile = deepcopy(cfg["ronin"])
    profile.update(deepcopy(cfg["ai_profiles"]["recruit"]))
    profile["archetype"] = "recruit"
    recruit.personality = Personality(**profile)
    state = encounter(player, recruit, ally, blocker)
    # The recruit saw this hostile earlier; its current direct lane is obstructed.
    monkeypatch.setattr(state, "perceived_hostile_position",
                        lambda fighter_id: (player.id, player.pos, False))
    crowded = movement_choice(state, recruit, ActionKind.WALK, toward=True)
    assert crowded is not None and crowded.direction == (1, -1)
    assert (recruit.pos[0] + crowded.direction[0], recruit.pos[1] + crowded.direction[1]) == ally.pos

    recruit.personality.traits["discipline"] = 1
    spaced = movement_choice(state, recruit, ActionKind.WALK, toward=True)
    assert spaced is not None and spaced.direction == (1, 0)
    assert (recruit.pos[0] + spaced.direction[0], recruit.pos[1] + spaced.direction[1]) != ally.pos


def test_group_coach_uses_only_visible_threats_without_single_opponent_rollout(monkeypatch):
    actors = [bare_fighter("player", pos=(6, 3), team_id="players")]
    actors.extend(bare_fighter(f"raider_{index}", pos=(1, y), team_id="raiders")
                  for index, y in enumerate((1, 2, 3, 4), 1))
    session = DuelSession.new_group(actors, guide_mode="hints")
    monkeypatch.setattr("pirates.duel_system.session.guide",
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("group rollout")))
    model = session.render_model(include_advice=True)
    advice = coach(model)
    rows = strategy_rows(model, advice)
    assert model.group_combat and advice.action == "Choose target"
    assert "no single-opponent rollout" in advice.headline
    assert any("Visible hostiles" in row for row in advice.advantages)
    assert rows[0] == "GROUP STRATEGY COACH"
    assert any("Off-screen" in row for row in advice.cautions)
