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
    assert len(characters) == 112
    assert len({row["display_name"] for row in characters.values()}) == len(characters)
    assert len({row["archetype_id"] for row in characters.values()}) > 1
    assert first.to_snapshot() == second.to_snapshot()
    first.event_log.validate_causality()


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
    world.run_until(8760)
    assert all(key in world.archive for key in majors[:5])
    assert all(world.archive[key]["death_tick"] == world.clock.tick for key in majors[:5])
    assert all(world.archive[key]["health"]["max_hp_from_age"] < 100 for key in majors[:5])
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
