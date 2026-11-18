import copy

import pytest

from sim.chronicler import Chronicler
from sim.core.world import WorldState
from sim.systems.battles import BattleSystem, _validate_data


def _hostile_encounter(world):
    ships = sorted(entity_id for entity_id, row in world.entities.items()
                   if row.get("kind") == "ship")
    first, second = ships[:2]
    crew_id = world.entities[first]["crew_id"]
    cause = world.crew_ships.crews[crew_id]["last_event_id"]
    location = world.location_of(first)["id"]
    return world.event_log.append(
        tick=world.clock.tick, event_type="ENCOUNTER_RESOLVED", location=location,
        actors=[{"role": "combatant", "entity_id": first},
                {"role": "combatant", "entity_id": second}], targets=[],
        data={"outcome": "combat_resolved", "ships": [first, second]},
        caused_by=[cause], causes_tags=["test_combat"], magnitude=0.8,
    )


def test_battle_catalog_is_data_driven_and_strictly_validated():
    data = BattleSystem.create().data
    _validate_data(data)
    invalid = copy.deepcopy(data)
    invalid["rules"]["rounds"] = 0
    with pytest.raises(ValueError, match="battle rules"):
        _validate_data(invalid)


def test_world_seeds_named_legend_commanders_and_persistent_fleet_doctrine():
    world = WorldState.create("battle-founders")

    legends = [row for row in world.entities.values()
               if row.get("kind") == "character" and row.get("tier") == "LEGEND"]

    assert len(legends) == min(len(world.crew_ships.crews),
                               world.battles.data["rules"]["starting_legend_captain_count"])
    assert all(len(row["legend_traits"]) == 1 for row in legends)
    assert len(world.battles.fleet_commands) == len(world.politics.factions)
    assert all(command["doctrine"] in world.battles.data["doctrines"]
               for command in world.battles.fleet_commands.values())
    assert not world.event_log.by_type("LEGEND_RECOGNIZED")
    assert world.to_snapshot()["snapshot_schema_version"] == 17


def test_hostile_legend_encounter_resolves_a_causal_multiround_battle_and_duel():
    world = WorldState.create("battle-theater")
    trigger = _hostile_encounter(world)

    battle_id = world.battles._maybe_resolve_set_piece(world, trigger, day=1)

    assert battle_id is not None
    battle = world.event_log.get(battle_id)
    rounds = [event for event in world.event_log.by_type("BATTLE_ROUND")
              if event.data["battle_event_id"] == battle_id]
    duels = [event for event in world.event_log.by_type("LEGEND_DUEL")
             if event.data["battle_event_id"] == battle_id]
    result = next(event for event in world.event_log.by_type("BATTLE_CONCLUDED")
                  if event.data["battle_event_id"] == battle_id)

    assert battle.caused_by[0] == trigger.id
    assert len(battle.caused_by) == 3
    assert len(world.event_log.by_type("LEGEND_RECOGNIZED")) == 2
    assert battle.data["legend_count"] == 2
    assert len(rounds) == world.battles.data["rules"]["rounds"]
    assert all(len(event.data["legend_traits"]) == 2 for event in rounds)
    assert all(row["trait_id"] and row["round_power_permille"] >= 1000
               for event in rounds for row in event.data["legend_traits"])
    assert len(duels) == 1
    assert duels[0].caused_by[0] == battle_id
    assert result.caused_by[0] == battle_id
    assert world.event_log.consequences(battle_id)
    world.event_log.validate_causality()


def test_data_defined_doctrine_and_legend_trait_change_fleet_power():
    world = WorldState.create("battle-doctrine-effects")
    crew_id, crew = next(iter(sorted(world.crew_ships.crews.items())))
    ship_id = crew["ship_ids"][0]
    captain_id = crew["captain_id"]
    balanced = world.battles._fleet_power(world, crew_id, captain_id, [ship_id], "balanced")
    aggressive = world.battles._fleet_power(world, crew_id, captain_id, [ship_id], "aggressive")
    defensive = world.battles._fleet_power(world, crew_id, captain_id, [ship_id], "defensive")

    assert min(balanced, aggressive, defensive) > 0
    assert len({balanced, aggressive, defensive}) == 3


def test_battle_aftermath_persists_morale_damage_casualties_and_bounty_spike():
    world = WorldState.create("battle-aftermath")
    world.battles.data["rules"]["defeat_casualty_chance_basis_points"] = 10000
    world.battles.data["rules"]["power_vacuum_morale_threshold"] = 100
    trigger = _hostile_encounter(world)
    ship_ids = trigger.data["ships"]
    crews = [world.crew_ships.crews[world.entities[ship_id]["crew_id"]]
             for ship_id in ship_ids]
    old_hulls = [world.entities[ship_id]["hull"]["current"] for ship_id in ship_ids]
    world.battles._maybe_resolve_set_piece(world, trigger, day=1)

    assert any(world.entities[ship_id]["hull"]["current"] < old_hulls[index]
               for index, ship_id in enumerate(ship_ids))
    assert any(event.type == "BATTLE_MORALE_CHANGED"
               for event in world.event_log if event.data.get("battle_event_id"))
    assert world.event_log.by_type("BATTLE_CASUALTY")
    assert world.event_log.by_type("BOUNTY_SET") or world.event_log.by_type("BOUNTY_RAISED")
    assert world.event_log.by_type("FACTION_POWER_VACUUM")
    assert any(crew["morale"] < 100 for crew in crews)
    world.event_log.validate_causality()


def test_battle_defeat_can_create_a_causal_faction_power_vacuum():
    world = WorldState.create("battle-power-vacuum")
    faction_id, faction = sorted(world.politics.factions.items())[0]
    old_leader = faction["leader_id"]
    cause = faction["last_event_id"]

    event_id = world.politics.resolve_battle_power_vacuum(
        world, faction_id, defeated_captain=old_leader, caused_by=cause)

    event = world.event_log.get(event_id)
    assert event.type == "FACTION_POWER_VACUUM"
    assert faction["leader_id"] == event.data["new_leader_id"]
    assert faction["leader_id"] != old_leader
    assert event.caused_by == (cause,)


def test_battle_stalemate_still_has_causal_political_aftermath():
    world = WorldState.create("battle-stalemate")
    factions = sorted(world.crew_ships.crews)[:2]
    captains = [world.crew_ships.crews[faction_id]["captain_id"] for faction_id in factions]
    cause = world.crew_ships.crews[factions[0]]["last_event_id"]
    result = world.event_log.append(
        tick=world.clock.tick, event_type="BATTLE_CONCLUDED",
        actors=[{"role": "commander", "entity_id": captain} for captain in captains],
        targets=[], data={"winner_side": None, "battle_event_id": cause,
                          "legend_duel_event_id": None},
        caused_by=[cause], causes_tags=["battle_outcome", "stalemate"], magnitude=0.9,
    )

    world.battles._political_aftermath(
        world, result, factions, captains, [50, 50], defeated_side=0)

    heat = [event for event in world.event_log.by_type("HEAT_CHANGED")
            if event.data["reason"] == "set_piece_battle_stalemate"]
    assert len(heat) == 2
    assert all(event.caused_by == (result.id,) for event in heat)


def test_routed_faction_collapses_but_the_crew_and_archive_remain_valid():
    world = WorldState.create("battle-faction-collapse")
    faction_id, faction = sorted(world.politics.factions.items())[0]
    captain_id = faction["leader_id"]
    crew = world.crew_ships.crews[faction_id]
    cause = faction["last_event_id"]
    result = world.event_log.append(
        tick=world.clock.tick, event_type="BATTLE_CONCLUDED",
        actors=[{"role": "defeated_commander", "entity_id": captain_id}], targets=[],
        data={"winner_side": 1, "battle_event_id": cause,
              "legend_duel_event_id": None},
        caused_by=[cause], causes_tags=["battle_outcome", "defeat"], magnitude=0.9,
    )
    world.politics.data["rules"]["heat_per_hostile_combat"] = 1

    world.battles._political_aftermath(
        world, result, [faction_id, "crew_000002"],
        [captain_id, world.crew_ships.crews["crew_000002"]["captain_id"]],
        [0, 100], defeated_side=0,
    )

    assert faction_id in world.politics.archived_factions
    assert faction_id not in world.politics.factions
    assert crew["faction_id"] == faction_id
    assert world.politics.archived_factions[faction_id]["dissolved_event_id"]
    restored = WorldState.from_snapshot(world.to_snapshot())
    assert faction_id in restored.politics.archived_factions
    assert restored.crew_ships.crews[faction_id]["ship_ids"] == crew["ship_ids"]


def test_battle_chronicler_narrates_legends_and_persistent_morale_aftermath():
    world = WorldState.create("battle-chronicler")
    trigger = _hostile_encounter(world)
    battle_id = world.battles._maybe_resolve_set_piece(world, trigger, day=1)
    chronicler = Chronicler()
    recognition = world.event_log.by_type("LEGEND_RECOGNIZED")[0]
    battle = world.event_log.get(battle_id)
    morale = next(event for event in world.event_log.by_type("BATTLE_MORALE_CHANGED")
                  if event.data["battle_event_id"] == battle_id)

    assert "recognized as a legend for commanding" in chronicler.narrate(world, recognition)
    assert "legendary commanders fought through 3 rounds" in chronicler.narrate(world, battle)
    assert "morale" in chronicler.narrate(world, morale)


def test_battle_cooldown_blocks_duplicate_pair_and_snapshot_roundtrips():
    world = WorldState.create("battle-cooldown")
    trigger = _hostile_encounter(world)
    first = world.battles._maybe_resolve_set_piece(world, trigger, day=1)
    assert first
    second = world.battles._maybe_resolve_set_piece(world, trigger, day=2)
    assert second is None

    snapshot = world.to_snapshot()
    restored = WorldState.from_snapshot(snapshot)

    assert restored.battles.snapshot() == world.battles.snapshot()
    assert restored.to_snapshot() == snapshot
