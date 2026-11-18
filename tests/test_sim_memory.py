import copy

import pytest

from sim.core.world import WorldState
from sim.systems.memory import load_memory_rules, validate_memory_rules


def _two_people(world):
    people = sorted(entity_id for entity_id, row in world.entities.items()
                    if row.get("kind") == "character")
    return people[0], people[1]


def test_memory_rules_are_validated_and_opinions_derive_from_repeat_offenses():
    world = WorldState.create("memory-repeat")
    holder_id, target_id = _two_people(world)
    holder = world.entities[holder_id]
    source_event = holder["last_event_id"]

    first_id = world.memories.add_memory(
        world, holder_id, target_id, "robbed", event_id=source_event,
        intensity_permille=400, valence_permille=-700,
    )
    first = holder["memory"][-1]
    second_id = world.memories.add_memory(
        world, holder_id, target_id, "robbed", event_id=source_event,
        intensity_permille=400, valence_permille=-700,
    )
    second = holder["memory"][-1]

    assert first["base_intensity_permille"] == 400
    assert second["base_intensity_permille"] == 600
    assert world.event_log.get(second_id).data["repeat_count"] == 1
    assert holder["opinions"][target_id]["grievance"] > 0
    assert holder["opinions"][target_id]["trust"] < 0
    assert world.event_log.get(first_id).caused_by == (source_event,)
    assert world.memories.current_intensity(first, 100) < first["base_intensity_permille"]

    data, _ = load_memory_rules()
    invalid = copy.deepcopy(data)
    invalid["rules"]["max_memories_per_character"] = 0
    with pytest.raises(ValueError, match="memory rules"):
        validate_memory_rules(invalid)
    world.to_snapshot()


def test_memory_decay_is_trait_shaped_and_core_memories_survive_pruning():
    world = WorldState.create("memory-traits")
    holder_id, target_id = _two_people(world)
    holder = world.entities[holder_id]
    source_event = holder["last_event_id"]
    holder["traits"].update({"pride": 100, "paranoia": 100, "sociability": 0})
    slow_decay = world.memories._trait_adjusted_decay(holder, "robbed", 8)
    holder["traits"].update({"pride": 0, "paranoia": 0, "sociability": 100})
    fast_decay = world.memories._trait_adjusted_decay(holder, "robbed", 8)
    assert slow_decay < fast_decay

    world.memories.rules_data["rules"]["max_memories_per_character"] = 1
    for _ in range(2):
        world.memories.add_memory(
            world, holder_id, target_id, "betrayed", event_id=source_event,
            intensity_permille=900,
        )
    assert len(holder["memory"]) == 2
    assert all(memory["protected"] for memory in holder["memory"])
    world.memories.rules_data["rules"]["max_memories_per_character"] = 3
    for _ in range(3):
        world.memories.add_memory(
            world, holder_id, target_id, "insulted", event_id=source_event,
            intensity_permille=100,
        )
    assert len(holder["memory"]) == 3
    assert sum(memory["protected"] for memory in holder["memory"]) == 2


def test_same_day_memory_insertions_rebuild_only_affected_opinions(monkeypatch):
    world = WorldState.create("memory-incremental-opinion")
    people = sorted(entity_id for entity_id, row in world.entities.items()
                    if row.get("kind") == "character")
    holder_id, *targets = people[:4]
    holder = world.entities[holder_id]
    source_event = holder["last_event_id"]
    original_recompute = world.memories.recompute
    full_recompute_calls = 0

    def counted_recompute(*args, **kwargs):
        nonlocal full_recompute_calls
        full_recompute_calls += 1
        return original_recompute(*args, **kwargs)

    monkeypatch.setattr(world.memories, "recompute", counted_recompute)
    world.memories.rules_data["rules"]["max_memories_per_character"] = 2
    for target_id in targets[:3]:
        world.memories.add_memory(
            world, holder_id, target_id, "insulted", event_id=source_event,
            intensity_permille=100, valence_permille=-500,
        )
    assert full_recompute_calls == 0
    assert targets[0] not in holder["opinions"]  # pruning also removes the old target

    monkeypatch.setattr(world.memories, "recompute", original_recompute)
    assert holder["opinions"] == original_recompute(world, holder_id)


def test_memory_snapshot_resume_and_encounter_hook_integration():
    world = WorldState.create("memory-encounter")
    ships = sorted(ship_id for ship_id, row in world.entities.items()
                   if row.get("kind") == "ship" and row.get("crew_id"))[:2]
    first, second = ships
    world.entities[second]["location"] = dict(world.entities[first]["location"])
    crews = [world.crew_ships.crews[world.entities[ship]["crew_id"]] for ship in ships]
    event_id = world.encounters.resolve(
        world, first, second,
        caused_by=[crew["last_event_id"] for crew in crews],
        stances={first: "attack", second: "attack"}, force_combat=True,
    )
    memories = world.event_log.by_type("MEMORY_CREATED")
    assert len(memories) == 2
    assert all(memory.caused_by for memory in memories)
    assert all(memory.data["source_event_id"] in {
        event.id for event in world.event_log.by_type("COMBAT_MEMORY_HOOK")
    } for memory in memories)
    snapshot = world.to_snapshot()
    restored = WorldState.from_snapshot(snapshot)
    assert restored.to_snapshot() == snapshot
    assert world.event_log.get(event_id).type == "ENCOUNTER_RESOLVED"


def test_revenge_action_is_grudge_scored_and_cites_memory_provenance():
    world = WorldState.create("memory-hunt-rival")
    holder_id, target_id = _two_people(world)
    source_event = world.entities[holder_id]["last_event_id"]
    memory_id = world.memories.add_memory(
        world, holder_id, target_id, "betrayed", event_id=source_event,
        intensity_permille=900, valence_permille=-1000,
    )
    holder = world.entities[holder_id]
    assert world.entities[holder_id]["dream"]["kind"] == "defeat_character"
    assert world.entities[holder_id]["dream"]["target_ref"] == target_id
    assert any(want["kind"] == "revenge" and want["target_ref"] == target_id
               for want in holder["wants"])
    assert "hunt_rival" in world.decisions.candidates(world, holder_id)
    scores = world.decisions.score_actions(world, holder_id, caused_by=memory_id)
    hunt = next(row for row in scores if row["action_id"] == "hunt_rival")
    assert hunt["components"]["grudge"] > 0

    state = world.decisions.agents.setdefault(holder_id, world.decisions._new_agent_state())
    state.update({"decision_count": 1, "last_action_id": "hunt_rival",
                  "last_decision_tick": world.clock.tick,
                  "last_executed_decision_number": 0})
    choice = {"agent_id": holder_id, "action_id": "hunt_rival", "decision_number": 1,
              "tick": world.clock.tick, "caused_by": memory_id, "score": hunt["score"],
              "top_candidates": []}
    result_id = world.decisions.execute(world, choice)
    raid = world.event_log.get(result_id)
    pursuit = world.event_log.get(raid.caused_by[0])
    action = world.event_log.get(pursuit.caused_by[0])
    assert action.type == "AGENT_ACTION"
    assert "grudge" in action.causes_tags
    assert memory_id in action.caused_by
    assert pursuit.type == "GRUDGE_PURSUED"
    assert raid.type == "FEUD_RAID_ATTEMPTED"
    assert "feud_escalation" in raid.causes_tags
    assert raid.targets[0]["entity_id"] == target_id


def test_public_insult_drives_a_causal_rival_hunt():
    world = WorldState.create("memory-insult-feud")
    instigator_id, target_id = _two_people(world)
    source_event = world.entities[instigator_id]["last_event_id"]
    world.entities[target_id]["traits"]["pride"] = 100
    insult_id = world.memories.insult(
        world, instigator_id, target_id, audience_size=25, caused_by=source_event,
    )
    target = world.entities[target_id]
    insult = world.event_log.get(insult_id)
    humiliation = next(memory for memory in target["memory"]
                       if memory["source_event_id"] == insult_id)
    assert insult.type == "INSULT"
    assert humiliation["kind"] == "humiliated"
    assert target["opinions"][instigator_id]["grievance"] >= 45
    assert target["dream"]["kind"] == "defeat_character"
    assert "hunt_rival" in world.decisions.candidates(world, target_id)

    world.decisions.data["rules"]["top_k"] = 1
    world.decisions.data["actions"]["hunt_rival"]["expected_gain"] = 5000
    choice, result_id = world.decisions.decide_and_execute(
        world, target_id, caused_by=insult_id,
    )
    assert choice["action_id"] == "hunt_rival"
    raid = world.event_log.get(result_id)
    pursuit = world.event_log.get(raid.caused_by[0])
    action = world.event_log.get(pursuit.caused_by[0])
    assert action.type == "AGENT_ACTION"
    assert "grudge" in action.causes_tags
    assert insult_id in action.caused_by
    assert pursuit.type == "GRUDGE_PURSUED"
    assert raid.type == "FEUD_RAID_ATTEMPTED"
    assert "feud_escalation" in raid.causes_tags
    assert raid.targets[0]["entity_id"] == instigator_id
    reciprocal = [memory for memory in world.entities[instigator_id]["memory"]
                  if memory["source_event_id"] == result_id]
    assert reciprocal and reciprocal[0]["kind"] == "humiliated"
    assert world.entities[instigator_id]["opinions"][target_id]["grievance"] > 0


def test_reconciliation_reduces_grievance_but_leaves_a_decaying_scar():
    world = WorldState.create("memory-reconcile")
    holder_id, target_id = _two_people(world)
    source_event = world.entities[holder_id]["last_event_id"]
    world.memories.add_memory(
        world, holder_id, target_id, "robbed", event_id=source_event,
        intensity_permille=900, valence_permille=1000,
    )
    before = world.entities[holder_id]["opinions"][target_id]["grievance"]
    reconciliation_id = world.memories.reconcile(
        world, holder_id, target_id, manner="mediation", caused_by=source_event,
    )
    after = world.entities[holder_id]["opinions"][target_id]["grievance"]
    assert after < before
    reconciled = next(memory for memory in world.entities[holder_id]["memory"]
                      if memory["source_event_id"] == reconciliation_id)
    assert reconciled["kind"] == "reconciled"
    # The old offense remains as a scar after the faster-fading repair memory.
    old_offense = next(memory for memory in world.entities[holder_id]["memory"]
                       if memory["kind"] == "robbed")
    old_effect = (700 * world.memories.current_intensity(old_offense, 120)
                  * abs(old_offense["valence_permille"]) // 1_000_000)
    repair_effect = (700 * world.memories.current_intensity(reconciled, 120)
                     * abs(reconciled["valence_permille"]) // 1_000_000)
    assert old_effect > repair_effect
    world.to_snapshot()


def test_mentor_death_passes_a_provenanced_grudge_to_student():
    world = WorldState.create("memory-inherited-grudge")
    mentor_id, rival_id = _two_people(world)
    root_event = world.event_log.by_type("WORLD_GENERATED")[0].id
    student_id = world.characters.create_character(
        world, tier="MAJOR", cause_event_id=root_event,
        heritage={"parents": [None, None], "mentor": mentor_id},
    )
    grudge_id = world.memories.add_memory(
        world, mentor_id, rival_id, "betrayed", event_id=root_event,
        intensity_permille=1000, valence_permille=-1000,
    )
    death_id = world.characters.kill_character(
        world, mentor_id, cause="test", caused_by=grudge_id,
    )
    student = world.entities[student_id]
    inherited = student["heritage"]["inherited_grudges"]
    assert len(inherited) == 1
    assert inherited[0]["target"] == rival_id
    assert inherited[0]["intensity"] >= 30
    inheritance_event = world.event_log.get(inherited[0]["source_event_id"])
    assert inheritance_event.type == "GRUDGE_INHERITED"
    assert death_id in inheritance_event.caused_by
    assert grudge_id in inheritance_event.caused_by
    assert student["opinions"][rival_id]["grievance"] >= inherited[0]["intensity"]
    assert student["dream"]["kind"] == "defeat_character"
    assert any(want["kind"] == "revenge" and want["target_ref"] == rival_id
               for want in student["wants"])
    world.to_snapshot()


def test_multi_decade_world_produces_inherited_vengeance_after_mentor_death():
    world = WorldState.create("memory-multi-decade-feud")
    root_event = world.event_log.by_type("WORLD_GENERATED")[0].id
    mentor_id = world.characters.create_character(
        world, tier="MAJOR", cause_event_id=root_event, age_years=18,
    )
    student_id = world.characters.create_character(
        world, tier="MAJOR", cause_event_id=root_event, age_years=18,
        heritage={"parents": [None, None], "mentor": mentor_id},
    )
    rival_id = world.characters.create_character(
        world, tier="MAJOR", cause_event_id=root_event, age_years=18,
    )
    ticks_per_year = (world.config["time"]["days_per_year"]
                      * world.config["time"]["hours_per_day"]
                      // world.config["time"]["hours_per_tick"])
    world.run_until(20 * ticks_per_year)

    grudge_id = world.memories.add_memory(
        world, mentor_id, rival_id, "betrayed", event_id=root_event,
        intensity_permille=1000, valence_permille=-1000,
    )
    death_id = world.characters.kill_character(
        world, mentor_id, cause="late-life feud", caused_by=grudge_id,
    )
    student = world.entities[student_id]
    inherited = next(row for row in student["heritage"]["inherited_grudges"]
                     if row["target"] == rival_id)
    inheritance = world.event_log.get(inherited["source_event_id"])
    assert world.clock.tick >= 20 * ticks_per_year
    assert inheritance.type == "GRUDGE_INHERITED"
    assert death_id in inheritance.caused_by and grudge_id in inheritance.caused_by
    assert student["dream"]["kind"] == "defeat_character"
    assert student["dream"]["target_ref"] == rival_id
    assert any(event.type == "VENGEANCE_DREAM"
               and event.actors[0]["entity_id"] == student_id
               for event in world.event_log)
    world.to_snapshot()
