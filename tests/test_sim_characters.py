import copy

import pytest

from sim.core.world import WorldState
from sim.systems.characters import (
    CharacterSystem,
    load_character_data,
    validate_character_data,
)


def _characters(world):
    return {key: row for key, row in world.entities.items() if row["kind"] == "character"}


def test_character_catalog_has_valid_roles_archetypes_and_name_pools():
    data, digest = load_character_data()
    validate_character_data(data)
    assert len(digest) == 64
    assert len(data["archetypes"]) >= 15
    assert len(data["name_pools"]["given"]) >= 50
    assert len(data["name_pools"]["family"]) >= 50
    invalid = copy.deepcopy(data)
    invalid["role_profiles"]["captain"]["offsets"]["bogus"] = 20
    with pytest.raises(ValueError, match="role profile"):
        validate_character_data(invalid)


def test_seeded_character_population_is_deterministic_and_uniquely_named():
    first = WorldState.create("character-catalog-seed")
    second = WorldState.create("character-catalog-seed")
    characters = _characters(first)
    assert len(characters) == 115
    assert len({row["display_name"] for row in characters.values()}) == len(characters)
    assert len({row["archetype_id"] for row in characters.values()}) > 1
    assert first.to_snapshot() == second.to_snapshot()
    first.event_log.validate_causality()


def test_physical_attributes_are_bounded_role_aware_and_injury_adjusted():
    world = WorldState.create("physical-attributes")
    characters = _characters(world)
    for character in characters.values():
        assert all(type(character["stats"][stat]) is int
                   and 0 <= character["stats"][stat] <= 100
                   for stat in ("strength", "speed", "endurance", "toughness"))
    sailors = [row["stats"]["endurance"] for row in characters.values()
               if row["role"] == "sailor"]
    merchants = [row["stats"]["endurance"] for row in characters.values()
                 if row["role"] == "merchant"]
    assert sailors and merchants
    assert sum(sailors) / len(sailors) > sum(merchants) / len(merchants)

    character_id = next(key for key, row in characters.items() if row["role"] == "gunner")
    character = world.entities[character_id]
    base_strength = character["stats"]["strength"]
    event_id = world.characters.inflict_injury(
        world, character_id, "broken_arm", caused_by=character["last_event_id"])
    profile = world.characters.personal_combat_profile(character)
    assert profile["stats"]["strength"] == {
        "base": base_strength, "injury_delta": -8,
        "effective": max(0, base_strength - 8),
    }
    assert world.event_log.get(event_id).type == "INJURY"


def test_character_skill_injury_and_snapshot_resume_preserve_state(tmp_path):
    world = WorldState.create("character-injury")
    character_id = next(key for key, row in _characters(world).items()
                        if row["role"] == "gunner")
    character = world.entities[character_id]
    cause = character["last_event_id"]
    before = character["stats"]["gunnery"]
    skill_event = world.characters.improve_skill(
        world, character_id, "gunnery", 3, caused_by=cause)
    assert world.entities[character_id]["stats"]["gunnery"] == min(100, before + 3)
    injury_event = world.characters.inflict_injury(
        world, character_id, "lost_eye", caused_by=skill_event)
    injured = world.entities[character_id]
    assert injured["health"]["hp"] == 65
    assert world.characters.effective_stat(injured, "gunnery") == max(
        0, injured["stats"]["gunnery"] - 18)
    path = tmp_path / "characters.json"
    world.save(path)
    restored = WorldState.load(path)
    assert restored.to_snapshot() == world.to_snapshot()
    assert restored.entities[character_id]["last_event_id"] == injury_event


def test_temporary_injury_heals_with_causal_followups_and_restores_capability():
    world = WorldState.create("injury-natural-recovery")
    character_id = next(key for key, row in _characters(world).items()
                        if row["role"] == "gunner")
    character = world.entities[character_id]
    injury_event_id = world.characters.inflict_injury(
        world, character_id, "broken_arm", caused_by=character["last_event_id"])
    assert world.characters.effective_stat(character, "gunnery") == \
        max(0, character["stats"]["gunnery"] - 10)

    world.run_until(30 * 24)

    recovered = world.entities[character_id]
    injury = next(row for row in recovered["health"]["injuries"]
                  if row["injury_id"] == "broken_arm")
    assert injury["severity"] == 0
    assert injury["recovery_days_elapsed"] == 30
    assert world.characters.effective_stat(recovered, "gunnery") == recovered["stats"]["gunnery"]
    assert recovered["health"]["hp"] > 75
    followups = [event for event in world.event_log.by_type("INJURY_RECOVERY")
                 if event.data.get("source_injury_event_id") == injury_event_id]
    assert len(followups) == 30
    assert all(list(event.caused_by) == [injury_event_id] for event in followups)
    assert followups[-1].data["recovered"] is True
    assert followups[-1].event_class == "narrative"
    scar = next(row for row in recovered["health"]["injuries"]
                if row["injury_id"] == "scar")
    scar_event = world.event_log.get(scar["event_id"])
    assert scar_event.caused_by == (followups[-1].id, injury_event_id)
    assert world.characters.effective_stat(recovered, "gunnery") == recovered["stats"]["gunnery"]
    snapshot = world.to_snapshot()
    assert WorldState.from_snapshot(snapshot).to_snapshot() == snapshot
    first = WorldState.from_snapshot(snapshot)
    second = WorldState.from_snapshot(snapshot)
    first.run_until(first.clock.tick + 24)
    second.run_until(second.clock.tick + 24)
    assert first.event_log.to_jsonl() == second.event_log.to_jsonl()
    assert first.to_snapshot() == second.to_snapshot()


def test_configured_fatal_injury_emits_causal_death_without_population_creation():
    world = WorldState.create("fatal-injury-outcome", director_enabled=False)
    generated = world.event_log.by_type("WORLD_GENERATED")[0].id
    character_id = world.characters.create_character(
        world, tier="BACKGROUND", cause_event_id=generated,
        role="civilian", island_id="isle_001")
    world.characters.data["injuries"]["chronic_fever"]["fatal_basis_points"] = 10_000

    injury_id = world.characters.inflict_injury(
        world, character_id, "chronic_fever", caused_by=generated)

    death = next(event for event in world.event_log.by_type("CHARACTER_DIED")
                 if event.data["cause"] == "injury_complication")
    assert death.caused_by == (injury_id,)
    assert character_id not in world.entities
    assert world.archive[character_id]["cause_of_death_event"] == injury_id


def test_permanent_injury_does_not_gain_a_synthetic_recovery_timer():
    world = WorldState.create("injury-permanent")
    character_id = next(key for key, row in _characters(world).items()
                        if row["role"] == "gunner")
    cause = world.entities[character_id]["last_event_id"]
    injury_event_id = world.characters.inflict_injury(
        world, character_id, "lost_eye", caused_by=cause)

    world.run_until(35 * 24)

    injury = world.entities[character_id]["health"]["injuries"][-1]
    assert injury["severity"] == 80
    assert injury["recovery_days_total"] == 0
    assert not any(event.data.get("source_injury_event_id") == injury_event_id
                   for event in world.event_log.by_type("INJURY_RECOVERY"))


def test_character_generation_can_inherit_parent_traits_and_history():
    world = WorldState.create("character-heritage")
    parent_id = next(iter(_characters(world)))
    parent = world.entities[parent_id]
    heritage = {
        "parents": [parent_id, None],
        "mentor": parent_id,
        "house": "house_ashford",
        "inherited_grudges": [{"target": "faction_001", "intensity": 40}],
        "inherited_debts": [{"creditor": "merchant_001", "amount": 25}],
        "titles": ["Harbor Warden"],
    }
    character_id = world.characters.create_character(
        world, tier="MINOR", cause_event_id=parent["last_event_id"],
        role="sailor", heritage=heritage,
    )
    child = world.entities[character_id]
    assert child["heritage"] == heritage
    assert child["stats"]["leadership"] >= 0
    assert all(0 <= value <= 100 for value in child["traits"].values())
    with pytest.raises(ValueError, match="heritage reference"):
        world.characters.create_character(
            world, tier="MINOR", cause_event_id=parent["last_event_id"],
            heritage={"parents": ["character_missing", None]},
        )


def test_character_aging_death_archive_and_rookie_replacement():
    world = WorldState.create("character-turnover")
    # Keep this lifecycle-only scenario isolated from the newly integrated M6 encounter system.
    world.encounters.data["rules"]["base_detection_basis_points"] = 0
    world.encounters.data["rules"]["lookout_basis_points_per_point"] = 0
    world.encounters.data["rules"]["island_detection_bonus_basis_points"] = 0
    # Keep this lifecycle-only regression independent of P2's actor-budget recruiter.
    world.config.pop("world", None)
    system = world.characters
    system.data["rules"]["death_max_basis_points"] = 10000
    majors = [key for key, row in _characters(world).items() if row["tier"] == "MAJOR"]
    for character_id in majors[:5]:
        character = world.entities[character_id]
        character["age_years"] = 100
        character["birth_tick"] = world.clock.tick - 100 * system._ticks_per_year(world)
    physical_before = {
        character_id: {stat: world.entities[character_id]["stats"][stat]
                       for stat in ("speed", "endurance")}
        for character_id in majors[:5]
    }
    world.run_until(8760)
    assert all(key in world.archive for key in majors[:5])
    assert all(world.archive[key]["death_tick"] == world.clock.tick for key in majors[:5])
    assert all(world.archive[key]["health"]["max_hp_from_age"] < 100 for key in majors[:5])
    assert all(world.archive[key]["stats"][stat] < physical_before[key][stat]
               for key in majors[:5] for stat in ("speed", "endurance"))
    major_plus = [row for row in _characters(world).values()
                  if row["tier"] in {"LEGEND", "MAJOR"}]
    assert len(major_plus) == 12
    rookies = [row for row in major_plus if row["age_years"] == 18]
    assert len(rookies) >= 5
    assert world.event_log.by_type("CHARACTER_AGED")
    assert world.event_log.by_type("CHARACTER_DIED")
    assert world.event_log.by_type("ROOKIE_SPAWNED")
    world.characters.validate_world(world)


def test_character_lifecycle_day_must_be_consecutive():
    world = WorldState.create("character-day-order")
    with pytest.raises(ValueError, match="consecutive"):
        world.characters.update_day(2, world)
