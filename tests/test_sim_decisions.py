import copy

import pytest

from sim.core.world import WorldState
from sim.systems.decisions import load_action_data, validate_action_data
from sim.tools.batch_test import _terminal_actor_violations
from sim.tools.inspect import main as inspect_main


def _unassigned_character(world):
    return next((key, row) for key, row in world.entities.items()
                if row.get("kind") == "character" and row.get("crew_id") is None)


def test_action_catalog_is_structured_and_rejects_invalid_costs():
    data, digest = load_action_data()
    validate_action_data(data)
    assert len(digest) == 64
    assert {row["handler"] for row in data["actions"].values()} == {
        "advance_dream", "rest_crew", "dispatch_trade", "pursue_grudge"}
    invalid = copy.deepcopy(data)
    invalid["actions"]["trade_run"]["risk_permille"] = 1001
    with pytest.raises(ValueError, match="action definition"):
        validate_action_data(invalid)


def test_decision_candidates_have_hard_preconditions_and_explainable_scores():
    world = WorldState.create("decision-score")
    character_id, character = _unassigned_character(world)
    cause = character["last_event_id"]
    assert world.decisions.candidates(world, character_id) == ["pursue_dream_step"]
    scores = world.decisions.score_actions(world, character_id, caused_by=cause)
    assert len(scores) == 1
    row = scores[0]
    assert row["action_id"] == "pursue_dream_step"
    assert set(row["components"]) == {
        "wants", "dream", "plan", "expected_gain", "expected_loss", "personality",
        "grudge", "costs", "recency", "repeated_sequence", "noise",
    }
    assert row["cost"]["time_cost_ticks"] > 0
    assert row["outcome"]["expected_gain"] > 0
    assert row["noise_bound"] >= 0
    assert world.decisions.agents == {}


def test_top_k_decision_executes_a_causal_dream_step():
    world = WorldState.create("decision-execute")
    character_id, character = _unassigned_character(world)
    before = character["dream"]["progress"]
    choice = world.decisions.decide(
        world, character_id, caused_by=character["last_event_id"])
    assert choice["action_id"] == "pursue_dream_step"
    assert len(choice["top_candidates"]) == 1
    result_id = world.decisions.execute(world, choice)
    assert world.entities[character_id]["dream"]["progress"] == before + 1
    action = world.event_log.by_type("AGENT_ACTION")[-1]
    result = world.event_log.get(result_id)
    assert result.type == "DREAM_PROGRESS"
    assert result.caused_by == (action.id,)
    assert action.caused_by == (choice["caused_by"],)
    world.event_log.validate_causality()
    with pytest.raises(ValueError, match="stale or has already executed"):
        world.decisions.execute(world, choice)


def test_terminal_agent_pending_wakeup_is_dropped_and_diagnosed():
    world = WorldState.create("terminal-wakeup")
    character_id, character = next(
        (entity_id, row) for entity_id, row in world.entities.items()
        if row.get("kind") == "character" and row.get("tier") in {"MAJOR", "LEGEND"})
    state = world.decisions.agents.setdefault(
        character_id, world.decisions._new_agent_state())
    state["next_decision_tick"] = 100
    root = world.event_log.by_type("WORLD_GENERATED")[0]
    wake_event = world.event_log.append(
        tick=0, event_type="ROUTE_HAZARD", caused_by=[root.id],
        causes_tags=["test_wakeup"], data={"route_id": "route_001"})
    world.decisions.event_cursor = len(world.event_log)
    world.decisions.pending_wakes = [{
        "agent_id": character_id, "event_id": wake_event.id, "reason": "ROUTE_HAZARD",
    }]

    death_event_id = world.characters.kill_character(
        world, character_id, cause="test", caused_by=character["last_event_id"])
    before_actions = len(world.event_log.by_type("AGENT_ACTION"))
    world.decisions.process_event_wakes(world)

    assert world.decisions.pending_wakes == []
    assert len(world.event_log.by_type("AGENT_ACTION")) == before_actions
    assert world.lifecycle_diagnostics == [{
        "code": "STALE_WAKEUP", "tick": world.clock.tick, "entity_id": character_id,
        "event_id": wake_event.id, "reason": "terminal_state:dead",
    }]
    assert world.event_log.get(death_event_id).type == "CHARACTER_DIED"
    world.characters.validate_world(world)


def test_pending_wakes_preserve_event_order_for_busy_agent():
    world = WorldState.create("wake-coalescing")
    character_id, _character = _unassigned_character(world)
    state = world.decisions.agents.setdefault(
        character_id, world.decisions._new_agent_state())
    state["next_decision_tick"] = 100
    world.decisions.event_cursor = len(world.event_log)
    root = world.event_log.by_type("WORLD_GENERATED")[0]
    first = world.event_log.append(
        tick=0, event_type="ROUTE_HAZARD", targets=[{
            "role": "affected_character", "entity_id": character_id,
        }], caused_by=[root.id], causes_tags=["wake_test", "first"])
    second = world.event_log.append(
        tick=0, event_type="SHIP_DAMAGED", targets=[{
            "role": "affected_character", "entity_id": character_id,
        }], caused_by=[first.id], causes_tags=["wake_test", "latest"])

    world.decisions.process_event_wakes(world)

    assert world.decisions.pending_wakes == [
        {"agent_id": character_id, "event_id": first.id, "reason": "ROUTE_HAZARD"},
        {"agent_id": character_id, "event_id": second.id, "reason": "SHIP_DAMAGED"},
    ]


def test_pending_wake_heap_rebuilds_from_compatible_snapshot_shape():
    world = WorldState.create("pending-wake-heap-snapshot")
    character_id, _character = _unassigned_character(world)
    state = world.decisions.agents.setdefault(
        character_id, world.decisions._new_agent_state())
    state["next_decision_tick"] = 100
    root = world.event_log.by_type("WORLD_GENERATED")[0]
    wake = world.event_log.append(
        tick=0, event_type="ROUTE_HAZARD", targets=[{
            "role": "affected_character", "entity_id": character_id,
        }], caused_by=[root.id], causes_tags=["wake_snapshot_test"])
    world.decisions.event_cursor = len(world.event_log)
    world.decisions.pending_wakes = [{
        "agent_id": character_id, "event_id": wake.id, "reason": "ROUTE_HAZARD",
    }]

    restored = WorldState.from_snapshot(world.to_snapshot())
    assert restored.decisions.pending_wakes == world.decisions.pending_wakes
    assert restored.decisions._pending_wake_heap == [(100, character_id)]
    assert restored.decisions.process_event_wakes(restored) == []
    assert restored.decisions.pending_wakes == world.decisions.pending_wakes


def test_terminal_actor_audit_detects_only_post_terminal_activity():
    world = WorldState.create("terminal-actor-audit")
    character_id, _character = _unassigned_character(world)
    root = world.event_log.by_type("WORLD_GENERATED")[0]
    death = world.event_log.append(
        tick=0, event_type="CHARACTER_DIED", targets=[{
            "role": "deceased", "entity_id": character_id,
        }], caused_by=[root.id], causes_tags=["terminal_test"])
    world.event_log.append(
        tick=0, event_type="AGENT_ACTION", actors=[{
            "role": "agent", "entity_id": character_id,
        }], data={"action_id": "test"}, caused_by=[death.id],
        causes_tags=["terminal_test"])

    violations = _terminal_actor_violations(world.event_log)

    assert len(violations) == 1
    assert violations[0]["entity_id"] == character_id
    assert violations[0]["event_type"] == "AGENT_ACTION"


def test_seed_27_combat_death_and_later_succession_do_not_reuse_dead_actor():
    world = WorldState.create(27)
    world.run_until(720)

    assert world.event_log.by_type("CHARACTER_DIED")
    assert _terminal_actor_violations(world.event_log) == []


def test_top_k_weighted_choice_is_per_agent_deterministic():
    first = WorldState.create("decision-top-k")
    second = WorldState.create("decision-top-k")
    crew = next(iter(first.crew_ships.crews.values()))
    other_crew = second.crew_ships.crews[crew["id"]]
    agent_id = crew["captain_id"]
    cause = crew["last_event_id"]
    for world, matching_crew in ((first, crew), (second, other_crew)):
        matching_crew["morale"] = 40
        world.entities[agent_id]["wants"] = [{
            "kind": "find_safe_harbor", "urgency_permille": 1000,
            "created_tick": 0, "source_event": cause,
        }]
    choice_a = first.decisions.decide(first, agent_id, caused_by=cause)
    choice_b = second.decisions.decide(second, agent_id, caused_by=cause)
    assert choice_a == choice_b
    assert choice_a["action_id"] in {"pursue_dream_step", "rest_with_crew", "trade_run"}
    assert 1 <= len(choice_a["top_candidates"]) <= first.decisions.data["rules"]["top_k"]
    assert sum(row["selection_weight"] for row in choice_a["top_candidates"]) > 0
    assert first.decisions.debug_history[-1]["score_components"]


def test_rest_action_changes_crew_morale_and_emits_a_child_event():
    world = WorldState.create("decision-rest")
    crew = next(iter(world.crew_ships.crews.values()))
    agent_id = crew["captain_id"]
    cause = crew["last_event_id"]
    crew["morale"] = 20
    world.entities[agent_id]["wants"] = [{
        "kind": "find_safe_harbor", "urgency_permille": 1000,
        "created_tick": 0, "source_event": cause,
    }]
    world.decisions.data["rules"]["top_k"] = 1
    world.decisions.data["actions"]["rest_with_crew"]["expected_gain"] = 1000
    choice = world.decisions.decide(world, agent_id, caused_by=cause)
    assert choice["action_id"] == "rest_with_crew"
    outcome_id = world.decisions.execute(world, choice)
    assert crew["morale"] == 28
    outcome = world.event_log.get(outcome_id)
    assert outcome.type == "CREW_RESTED"
    assert outcome.caused_by == (world.event_log.by_type("AGENT_ACTION")[-1].id,)


def test_dream_templates_queue_steps_and_complete_with_causal_events():
    world = WorldState.create("decision-plan")
    agent_id, agent = _unassigned_character(world)
    cause = agent["last_event_id"]
    plan = world.decisions.ensure_dream_plan(world, agent_id, caused_by=cause)
    assert plan["dream_kind"] == agent["dream"]["kind"]
    assert plan["steps"] == world.decisions.ai_data["plan_templates"][agent["dream"]["kind"]]
    assert plan["status"] == "active"
    scores = world.decisions.score_actions(
        world, agent_id, caused_by=plan["source_event_id"])
    assert scores[0]["components"]["plan"] == 18
    event_ids = []
    for _ in plan["steps"]:
        event_ids.append(world.decisions.advance_dream_plan_step(
            world, agent_id, caused_by=world.entities[agent_id]["last_event_id"]))
    assert world.decisions.agents[agent_id]["plan"]["status"] == "completed"
    completed = world.event_log.get(event_ids[-1])
    assert completed.type == "DREAM_PLAN_COMPLETED"
    world.event_log.validate_causality()


def test_voyage_plan_queue_progresses_matches_targets_and_persists(tmp_path):
    world = WorldState.create("decision-voyage-plan")
    crew = next(iter(world.crew_ships.crews.values()))
    captain_id = crew["captain_id"]
    cause = crew["last_event_id"]
    island_ids = sorted(world.geography.islands)
    plan_event = world.decisions.set_voyage_plan(world, captain_id, [
        {"action": "travel", "target_island_id": island_ids[1]},
        {"action": "trade", "target_island_id": island_ids[1]},
        {"action": "travel", "target_island_id": island_ids[2]},
        {"action": "repair", "target_island_id": island_ids[2]},
    ], caused_by=cause)
    assert world.decisions.agents[captain_id]["voyage_plan"]["current_step"] == 0
    with pytest.raises(ValueError, match="does not match"):
        world.decisions.advance_voyage_plan(
            world, captain_id, caused_by=plan_event,
            completed_action="travel", island_id=island_ids[0])
    parent = plan_event
    for action in ("travel", "trade", "travel", "repair"):
        result = world.decisions.advance_voyage_plan(
            world, captain_id, caused_by=parent, completed_action=action,
            island_id=island_ids[1] if action in {"travel", "trade"}
            and world.decisions.agents[captain_id]["voyage_plan"]["current_step"] < 2
            else island_ids[2])
        parent = result
    voyage_plan = world.decisions.agents[captain_id]["voyage_plan"]
    assert voyage_plan["status"] == "completed"
    assert voyage_plan["current_step"] == 4
    restored = WorldState.load(world.save(tmp_path / "voyage-plan.json"))
    assert restored.to_snapshot() == world.to_snapshot()


def test_wrong_strength_belief_emits_traceable_belief_error():
    world = WorldState.create("decision-belief-error")
    crew = next(iter(world.crew_ships.crews.values()))
    observer_id = crew["captain_id"]
    ship_id = crew["ship_ids"][0]
    evidence = crew["last_event_id"]
    trigger = world.crew_ships.damage_ship(
        world, ship_id, "hull", 1, caused_by=evidence)
    world.decisions.set_belief(
        world, observer_id, ship_id, "estimated_strength", 1,
        source_event_id=evidence, confidence_permille=800)
    error_id = world.decisions.audit_belief(
        world, observer_id, ship_id, "estimated_strength", triggered_by=trigger)
    assert error_id is not None
    error = world.event_log.get(error_id)
    assert error.type == "BELIEF_ERROR"
    assert "belief_error" in error.causes_tags
    assert set(error.caused_by) == {evidence, trigger}
    assert error.data["believed_value"] == 1
    assert error.data["actual_value"] > 1
    world.event_log.validate_causality()


def test_inspect_decisions_reports_private_scores_beliefs_and_pending_wakes(
        tmp_path, capsys):
    world = WorldState.create("decision-inspect")
    agent_id, agent = _unassigned_character(world)
    world.decisions.decide(world, agent_id, caused_by=agent["last_event_id"])
    path = tmp_path / "inspect-decisions.json"
    world.save(path)
    assert inspect_main([str(path), "--decisions", "--decision-agent", agent_id]) == 0
    import json
    report = json.loads(capsys.readouterr().out)
    assert list(report["agents"]) == [agent_id]
    assert report["agents"][agent_id]["last_action_id"] == "pursue_dream_step"
    assert report["debug_traces"][0]["score_components"]
    assert report["event_cursor"] <= len(world.event_log)


def test_action_choices_show_diversity_across_seeds_and_penalize_ritual_loops():
    choices = []
    for seed in range(32):
        world = WorldState.create(seed)
        crew = next(iter(world.crew_ships.crews.values()))
        crew["morale"] = 30
        agent_id = crew["captain_id"]
        captain = world.entities[agent_id]
        cause = crew["last_event_id"]
        captain["wants"] = [{"kind": "find_safe_harbor", "urgency_permille": 1000,
                             "created_tick": 0, "source_event": cause}]
        choices.append(world.decisions.decide(world, agent_id, caused_by=cause)["action_id"])
    assert len(set(choices)) >= 2

    world = WorldState.create("decision-loop")
    crew = next(iter(world.crew_ships.crews.values()))
    agent_id = crew["captain_id"]
    captain = world.entities[agent_id]
    cause = crew["last_event_id"]
    captain["wants"] = [{"kind": "find_safe_harbor", "urgency_permille": 1000,
                         "created_tick": 0, "source_event": cause}]
    state = world.decisions.agents.setdefault(agent_id, world.decisions._new_agent_state())
    state["history"] = [
        {"action_id": "rest_with_crew", "tick": index * 24}
        for index in range(4)
    ]
    scores = world.decisions.score_actions(world, agent_id, caused_by=cause)
    rest = next(row for row in scores if row["action_id"] == "rest_with_crew")
    assert rest["loop_repetitions"] == 4
    assert rest["components"]["repeated_sequence"] == -1000


def test_noncombat_simulation_activity_has_no_three_day_liveness_gap():
    world = WorldState.create("decision-liveness")
    world.run_until(24 * 30)
    noncombat = [event.tick for event in world.event_log
                 if event.type in {"ISLAND_ECONOMY_UPDATED", "WAGES_PAID",
                                   "SHIP_SUPPLIES_CONSUMED", "AGENT_ACTION",
                                   "DREAM_PROGRESS", "CREW_RESTED", "MARKET_TRADE"}]
    assert noncombat
    assert max(noncombat) >= 24 * 30
    assert max((right - left for left, right in zip(noncombat, noncombat[1:])), default=0) <= 72


def test_beliefs_decay_spread_with_attribution_and_survive_snapshot(tmp_path):
    world = WorldState.create("decision-beliefs")
    sender_id, sender = _unassigned_character(world)
    recipient_id = next(key for key, row in world.entities.items()
                        if row.get("kind") == "character" and row.get("crew_id") is None
                        and key != sender_id)
    recipient = world.entities[recipient_id]
    recipient["location"] = dict(sender["location"])
    source_event = sender["last_event_id"]
    world.decisions.set_belief(
        world, sender_id, "ship_000001", "estimated_strength", 100,
        source_event_id=source_event)
    original = world.decisions.ai_data["belief_rules"].copy()
    world.decisions.ai_data["belief_rules"].update({
        "share_base_basis_points": 10000,
        "distortion_basis_points": 10000,
        "max_distortion_permille": 1000,
    })
    world.run_until(24)
    world.decisions.ai_data["belief_rules"].update(original)
    received = world.decisions.belief(recipient_id, "ship_000001", "estimated_strength")
    assert received is not None
    assert received["confidence_permille"] < 1000
    assert received["source"]["kind"] == "rumor"
    rumor = world.event_log.get(received["source"]["event_id"])
    assert rumor.type == "RUMOR_SHARED"
    assert rumor.data["original_source_event"] == source_event
    assert rumor.caused_by[0] == source_event
    assert all(event.actors[0]["entity_id"] == sender_id
               for event in world.event_log.by_type("RUMOR_SHARED"))
    restored = WorldState.load(world.save(tmp_path / "beliefs.json"))
    assert restored.to_snapshot() == world.to_snapshot()


def test_significant_events_wake_affected_agents_and_cause_actions():
    world = WorldState.create("decision-wakeup")
    crew = next(iter(world.crew_ships.crews.values()))
    captain_id = crew["captain_id"]
    damage_event = world.crew_ships.damage_ship(
        world, crew["ship_ids"][0], "rigging", 1, caused_by=crew["last_event_id"])
    world.run_until(24)
    actions = world.event_log.by_type("AGENT_ACTION")
    assert any(captain_id in {actor["entity_id"] for actor in event.actors}
               and damage_event in event.caused_by for event in actions)
    assert any(row["agent_id"] == captain_id for row in world.decisions.debug_history)


def test_recency_penalty_and_agent_rng_state_survive_resume(tmp_path):
    direct = WorldState.create("decision-resume")
    split = WorldState.create("decision-resume")
    agent_id, agent = _unassigned_character(direct)
    other_agent = split.entities[agent_id]
    agent["tier"] = other_agent["tier"] = "MINOR"  # isolate manual review from scheduled major reviews
    cause = agent["last_event_id"]
    first = direct.decisions.decide(direct, agent_id, caused_by=cause)
    first_split = split.decisions.decide(split, agent_id, caused_by=other_agent["last_event_id"])
    assert first == first_split
    direct.run_until(24)
    split.run_until(24)
    restored = WorldState.load(split.save(tmp_path / "decision.json"))
    direct_scores = direct.decisions.score_actions(
        direct, agent_id, caused_by=cause)
    restored_scores = restored.decisions.score_actions(
        restored, agent_id, caused_by=cause)
    assert restored_scores == direct_scores
    assert restored_scores[0]["recent_repeats"] == 1
    assert restored_scores[0]["components"]["recency"] < 0
    next_direct = direct.decisions.decide(direct, agent_id, caused_by=cause)
    next_restored = restored.decisions.decide(restored, agent_id, caused_by=cause)
    assert next_restored == next_direct
    assert restored.to_snapshot() == direct.to_snapshot()


def test_cooldown_filters_decisions_until_action_time_cost_has_elapsed():
    world = WorldState.create("decision-cooldown")
    agent_id, agent = _unassigned_character(world)
    agent["tier"] = "MINOR"  # scheduled daily reviews target MAJOR+ characters
    first = world.decisions.decide(world, agent_id, caused_by=agent["last_event_id"])
    assert first is not None
    assert world.decisions.decide(world, agent_id, caused_by=agent["last_event_id"]) is None
    world.run_until(24)
    assert world.decisions.decide(world, agent_id, caused_by=agent["last_event_id"]) is not None
