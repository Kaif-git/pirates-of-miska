import copy

import pytest

from sim.chronicler import Chronicler
from sim.core.world import SNAPSHOT_SCHEMA_VERSION, WorldState
from sim.systems.battles import BattleSystem, _validate_data


def _hostile_encounter(world):
    merchant_faction = next(faction for faction in world.politics.factions.values()
                            if faction["kind"] != "marine")
    merchant_crew_id = merchant_faction["member_crew_ids"][0]
    merchant_ship_id = world.crew_ships.crews[merchant_crew_id]["ship_ids"][0]
    island_id = next(island_id for island_id in sorted(world.geography.islands)
                     if island_id not in world.politics.island_rulers)
    pirate_crew_ids = []
    for suffix in ("A", "B"):
        ship_id, created = world.create_vessel(f"Battle Fixture Raider {suffix}", island_id)
        pirate_crew_ids.append(world.crew_ships.provision_recruited_vessel(
            world, ship_id, "pirate", caused_by=created))
    founder_crew_id = pirate_crew_ids[0]
    founder_id = world.crew_ships.crews[founder_crew_id]["captain_id"]
    founder = world.entities[founder_id]
    founder["traits"]["ambition"] = max(
        founder["traits"]["ambition"],
        world.politics.data["rules"]["faction_founder_min_ambition"])
    founder["stats"]["leadership"] = max(
        founder["stats"]["leadership"],
        world.politics.data["rules"]["faction_founder_min_leadership"])
    world.politics.found_faction(
        world, member_crew_ids=pirate_crew_ids, founder_id=founder_id,
        anchorage_island_id=island_id, purpose="test_interception_defense",
        name=f"{founder['display_name']}'s Battle Privateers",
        kind="pirate_coalition",
        caused_by=[world.crew_ships.crews[crew_id]["last_event_id"]
                   for crew_id in pirate_crew_ids])
    used_traits = {trait_id for row in world.entities.values()
                   if row.get("kind") == "character" and row.get("tier") == "LEGEND"
                   for trait_id in row.get("legend_traits", ())}
    trait = next(row for row in world.battles.data["legend_traits"]
                 if row["id"] not in used_traits)
    founder.update({"tier": "LEGEND", "legend_traits": [trait["id"]],
                    "legend_since_event_id": None})
    world.entities[merchant_ship_id]["location"] = {"kind": "island", "id": island_id}
    world.battles.refresh_fleet_commands(world)
    first, second = merchant_ship_id, world.crew_ships.crews[founder_crew_id]["ship_ids"][0]
    cause = world.crew_ships.crews[merchant_crew_id]["last_event_id"]
    location = island_id
    return world.event_log.append(
        tick=world.clock.tick, event_type="ENCOUNTER_RESOLVED", location=location,
        actors=[{"role": "combatant", "entity_id": first},
                {"role": "combatant", "entity_id": second}], targets=[],
        data={"outcome": "combat_resolved", "ships": [first, second]},
        caused_by=[cause], causes_tags=["test_combat"], magnitude=0.8,
    )


def test_set_piece_candidate_blockers_are_reported_without_changing_snapshot_state():
    world = WorldState.create("set-piece-opportunity-report")
    cause = world.event_log.by_type("WORLD_GENERATED")[0]
    encounter = world.event_log.append(
        tick=world.clock.tick, event_type="ENCOUNTER_RESOLVED",
        location=next(iter(world.geography.islands)),
        data={"outcome": "combat_resolved", "ships": ["missing-a", "missing-b"]},
        caused_by=[cause.id], causes_tags=["set_piece_blocker_fixture"], magnitude=0.1)
    before = copy.deepcopy(world.battles.snapshot())

    assert world.battles._maybe_resolve_set_piece(world, encounter, day=1) is None

    assert world.battles.opportunity_observations == [{
        "tick": encounter.tick, "day": 1, "encounter_event_id": encounter.id,
        "reason": "encounter_missing_two_active_ships",
        "ships": ["missing-a", "missing-b"],
    }]
    assert world.battles.snapshot() == before


def test_battle_catalog_is_data_driven_and_strictly_validated():
    data = BattleSystem.create().data
    _validate_data(data)
    invalid = copy.deepcopy(data)
    invalid["rules"]["rounds"] = 0
    with pytest.raises(ValueError, match="battle rules"):
        _validate_data(invalid)
    invalid_doctrine = copy.deepcopy(data)
    invalid_doctrine["doctrines"]["balanced"]["power_permille"] = 10000
    with pytest.raises(ValueError, match="fleet doctrine"):
        _validate_data(invalid_doctrine)
    invalid_trait = copy.deepcopy(data)
    invalid_trait["legend_traits"][0]["duel_bonus"] = 21
    with pytest.raises(ValueError, match="legend trait"):
        _validate_data(invalid_trait)


def test_world_seeds_named_legend_commanders_and_persistent_fleet_doctrine():
    world = WorldState.create("battle-founders")

    legends = [row for row in world.entities.values()
               if row.get("kind") == "character" and row.get("tier") == "LEGEND"]

    opening_major_captains = sum(
        crew["founded_tick"] == 0 and crew["ideology_tags"] == ["merchant", "trade"]
        for crew in world.crew_ships.crews.values())
    assert len(legends) == min(opening_major_captains,
                               world.battles.data["rules"]["starting_legend_captain_count"])
    assert all(len(row["legend_traits"]) == 1 for row in legends)
    assert len(world.battles.fleet_commands) == len(world.politics.factions)
    assert all(command["doctrine"] in world.battles.data["doctrines"]
               for command in world.battles.fleet_commands.values())
    assert not world.event_log.by_type("LEGEND_RECOGNIZED")
    assert world.to_snapshot()["snapshot_schema_version"] == SNAPSHOT_SCHEMA_VERSION


def test_hostile_legend_encounter_resolves_a_causal_multiround_battle_and_duel():
    world = WorldState.create("battle-theater")
    trigger = _hostile_encounter(world)

    battle_id = world.battles._maybe_resolve_set_piece(world, trigger, day=1)

    assert battle_id is not None
    assert world.battles.opportunity_observations[-1]["reason"] == \
        "eligible_set_piece_converted"
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


def test_personal_legend_combat_is_seeded_causal_and_snapshot_stable():
    first = WorldState.create("personal-combat-event-chain")
    second = WorldState.create("personal-combat-event-chain")
    trigger_a = _hostile_encounter(first)
    trigger_b = _hostile_encounter(second)
    captains_a = [first.crew_ships.crews[first.entities[ship_id]["crew_id"]]["captain_id"]
                  for ship_id in trigger_a.data["ships"]]
    captains_b = [second.crew_ships.crews[second.entities[ship_id]["crew_id"]]["captain_id"]
                  for ship_id in trigger_b.data["ships"]]
    economy_before = copy.deepcopy(first.economy.snapshot())
    ships_before = {ship_id: copy.deepcopy(first.entities[ship_id])
                    for ship_id in trigger_a.data["ships"]}

    result_a = first.battles.resolve_personal_combat(
        first, captains_a, caused_by=[trigger_a.id], location=trigger_a.location,
        context="legend_duel", context_id="seeded-test-duel")
    result_b = second.battles.resolve_personal_combat(
        second, captains_b, caused_by=[trigger_b.id], location=trigger_b.location,
        context="legend_duel", context_id="seeded-test-duel")

    assert result_a == result_b
    assert first.event_log.to_jsonl() == second.event_log.to_jsonl()
    assert first.economy.snapshot() == economy_before
    assert {ship_id: first.entities[ship_id] for ship_id in ships_before} == ships_before
    start = first.event_log.get(result_a["started_event_id"])
    exchanges = [first.event_log.get(event_id) for event_id in result_a["exchange_event_ids"]
                 if first.event_log.get(event_id).type == "PERSONAL_EXCHANGE"]
    assert start.caused_by == (trigger_a.id,)
    assert exchanges
    wear_events = [event for event in first.event_log.by_type("COMBAT_EQUIPMENT_WORN")
                   if event.data["character_id"] in captains_a]
    assert wear_events
    wear_by_item = {}
    for event in wear_events:
        key = (event.data["character_id"], event.data["equipment_kind"])
        previous = wear_by_item.get(key)
        if previous:
            assert event.data["condition_before_permille"] == \
                previous.data["condition_after_permille"]
        wear_by_item[key] = event
    for (character_id, equipment_kind), event in wear_by_item.items():
        gear = first.entities[character_id]["combat_equipment"]
        assert gear[f"{equipment_kind}_condition_permille"] == event.data["condition_after_permille"]
    assert all(len(event.caused_by) == len(set(event.caused_by)) for event in exchanges)
    assert all(first.event_log.get(parent).tick <= event.tick
               for event in exchanges for parent in event.caused_by)
    first.event_log.validate_causality()
    restored = WorldState.from_snapshot(first.to_snapshot())
    assert restored.to_snapshot() == first.to_snapshot()


def test_personal_combat_speed_sets_first_action_without_ship_power_input():
    world = WorldState.create("personal-combat-initiative")
    trigger = _hostile_encounter(world)
    captains = [world.crew_ships.crews[world.entities[ship_id]["crew_id"]]["captain_id"]
                for ship_id in trigger.data["ships"]]
    world.entities[captains[0]]["stats"]["speed"] = 100
    world.entities[captains[1]]["stats"]["speed"] = 0

    result = world.battles.resolve_personal_combat(
        world, captains, caused_by=[trigger.id], location=trigger.location,
        context="legend_duel", context_id="speed-initiative")

    initiative = world.event_log.get(result["initiative_event_id"])
    assert initiative.data["scores"][0] - initiative.data["scores"][1] >= 80
    assert initiative.data["first_actor_index"] == 0
    first_exchange = world.event_log.get(result["exchange_event_ids"][0])
    assert first_exchange.actors[0]["entity_id"] == captains[0]


def test_personal_physical_strength_does_not_change_ship_combat_power():
    world = WorldState.create("personal-strength-not-cannon-power")
    crew = next(iter(world.crew_ships.crews.values()))
    ship_id = crew["ship_ids"][0]
    captain = world.entities[crew["captain_id"]]
    before = world.crew_ships.combat_power(world, ship_id)

    captain["stats"]["strength"] = 0
    weak = world.crew_ships.combat_power(world, ship_id)
    captain["stats"]["strength"] = 100
    strong = world.crew_ships.combat_power(world, ship_id)

    assert before == weak == strong


def test_personal_exchange_can_causally_injure_but_not_guarantee_casualties():
    world = WorldState.create("personal-combat-causal-injury")
    trigger = _hostile_encounter(world)
    captains = [world.crew_ships.crews[world.entities[ship_id]["crew_id"]]["captain_id"]
                for ship_id in trigger.data["ships"]]
    for name in ("combat", "speed", "strength", "endurance", "toughness", "willpower"):
        world.entities[captains[0]]["stats"][name] = 100
        world.entities[captains[1]]["stats"][name] = 0
    rules = world.characters.data["combat_equipment"]["rules"]
    rules.update({"max_exchanges": 1, "injury_impact_threshold": 0,
                  "injury_basis_points_per_impact": 10000,
                  "max_injury_basis_points": 10000})

    result = world.battles.resolve_personal_combat(
        world, captains, caused_by=[trigger.id], location=trigger.location,
        context="legend_duel", context_id="causal-injury")

    exchange = world.event_log.get(result["exchange_event_ids"][0])
    injury = next(event for event in world.event_log.by_type("INJURY")
                  if event.caused_by == (exchange.id,))
    assert exchange.data["hit"] and exchange.data["impact"] > 0
    assert injury.targets[0]["entity_id"] == captains[1]
    assert world.entities[captains[1]]["health"]["injuries"][-1]["source_event"] == exchange.id
    assert world.entities[captains[1]]["health"]["hp"] < 100
    assert not world.event_log.by_type("CHARACTER_DIED")
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
    factions = sorted(world.politics.factions)[:2]
    captains = [world.politics.factions[faction_id]["leader_id"] for faction_id in factions]
    cause = world.politics.factions[factions[0]]["last_event_id"]
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
    # The Marine government faction has no crew roster to charge heat to.
    assert len(heat) == 1
    assert all(event.caused_by == (result.id,) for event in heat)


def test_routed_faction_collapses_but_the_crew_and_archive_remain_valid():
    world = WorldState.create("battle-faction-collapse")
    faction_id, faction = next((fid, faction) for fid, faction
                               in sorted(world.politics.factions.items())
                               if faction["kind"] != "marine")
    captain_id = faction["leader_id"]
    crew_id = faction["member_crew_ids"][0]
    crew = world.crew_ships.crews[crew_id]
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
        world, result, [faction_id, world.politics.marine_command["faction_id"]],
        [captain_id, world.politics.marine_command["commander_id"]],
        [0, 100], defeated_side=0,
    )

    assert faction_id in world.politics.archived_factions
    assert faction_id not in world.politics.factions
    assert crew["faction_id"] is None
    assert world.politics.archived_factions[faction_id]["dissolved_event_id"]
    restored = WorldState.from_snapshot(world.to_snapshot())
    assert faction_id in restored.politics.archived_factions
    assert restored.crew_ships.crews[crew_id]["ship_ids"] == crew["ship_ids"]


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
    assert world.battles.opportunity_observations[-1]["reason"] == "pair_cooldown_active"

    snapshot = world.to_snapshot()
    restored = WorldState.from_snapshot(snapshot)

    assert restored.battles.snapshot() == world.battles.snapshot()
    assert restored.to_snapshot() == snapshot
