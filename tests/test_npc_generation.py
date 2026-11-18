import copy
import statistics

import pytest

from pirates.npc_generation import (
    BACKGROUNDS,
    DEFAULT_SETTINGS,
    GENERATOR_VERSION,
    derive_command_power,
    generate_people,
    generate_person,
    generate_society,
    make_manifest,
    random_stream,
    stable_id,
    validate_manifest,
    validate_person,
)
from pirates.game import World
from pirates.voyage import SEA_NODES


def test_manifest_is_stable_json_safe_and_settings_are_validated():
    first = make_manifest(17, {"notable_people": 30})
    second = make_manifest(17, {"notable_people": 30})
    assert first == second
    assert first["generator_version"] == GENERATOR_VERSION
    assert validate_manifest(first) == first
    assert first["settings"] == DEFAULT_SETTINGS | {"notable_people": 30}
    assert make_manifest("harbor-seed") != make_manifest("another-harbor-seed")
    with pytest.raises(ValueError, match="Unknown NPC generation setting"):
        make_manifest(17, {"dice": 6})
    with pytest.raises(ValueError, match="at least one vessel"):
        make_manifest(17, {"organization_count": 5, "vessel_count": 4})


def test_streams_are_stable_independent_and_do_not_use_process_random_hashing():
    manifest = make_manifest("stable-world")
    first = random_stream(manifest, "names", "person-1")
    expected = [first.randrange(1_000_000) for _ in range(5)]
    repeated = random_stream(manifest, "names", "person-1")
    assert [repeated.randrange(1_000_000) for _ in range(5)] == expected
    independent = random_stream(manifest, "skills", "person-1")
    assert [independent.randrange(1_000_000) for _ in range(5)] != expected
    assert stable_id(manifest, "person", 0) == stable_id(manifest, "person", 0)
    assert stable_id(manifest, "person", 0) != stable_id(manifest, "person", 1)
    assert stable_id(manifest, "person", 0) != stable_id(make_manifest("other"), "person", 0)


def test_same_seed_reproduces_full_people_and_different_seed_changes_them():
    first_manifest = make_manifest(12345, {"notable_people": 30})
    repeated_manifest = make_manifest(12345, {"notable_people": 30})
    first = generate_people(first_manifest)
    repeated = generate_people(repeated_manifest)
    different = generate_people(make_manifest(12346, {"notable_people": 30}))
    assert first == repeated
    assert first != different
    assert set(first) == set(repeated)
    assert len({person["name"] for person in first.values()}) == 30
    assert all(validate_person(person) for person in first.values())


def test_generated_people_have_correlated_but_not_deterministic_backgrounds():
    manifest = make_manifest("correlation-check", {"notable_people": 200})
    independent = generate_people(manifest, organization_kind="independent_crew")
    assert {person["background"] for person in independent.values()} <= set(BACKGROUNDS)
    assert "deckhand" in {person["background"] for person in independent.values()}
    assert "merchant" in {person["background"] for person in independent.values()}
    assert len({person["personality"]["boldness"] for person in independent.values()}) > 30

    merchant_trade = [p["skills"]["trade"] for p in independent.values() if p["background"] == "merchant"]
    deckhand_trade = [p["skills"]["trade"] for p in independent.values() if p["background"] == "deckhand"]
    assert merchant_trade and deckhand_trade
    assert statistics.mean(merchant_trade) > statistics.mean(deckhand_trade)
    # The background is only a bias; its members still have varied outcomes.
    assert len(set(merchant_trade)) > 1


def test_personality_and_background_generate_effective_traits_and_xp_levels():
    manifest = make_manifest("traits-seed")
    person = generate_person(manifest, "test-captain", role="captain", organization_kind="pirate_fleet")
    assert person["role"] == "captain"
    assert person["organization_kind"] == "pirate_fleet"
    assert person["skills"]["leadership"] >= 0
    assert person["career_level"] >= 1
    assert 0 <= person["personal_capability"] <= 100
    assert 0 <= person["command_power"] <= 100
    assert all(person["skills"][skill] == int(person["skill_xp"][skill] ** 0.5)
               for skill in person["skills"])
    assert all(trait in {"cautious", "bold", "greedy", "merciful", "vindictive",
                         "disciplined", "charismatic", "steadfast"} for trait in person["traits"])


def test_personal_capability_and_command_power_are_separate_role_sensitive_scores():
    manifest = make_manifest("power-separation")
    crew = generate_person(manifest, "same-person", role="crew")
    captain = generate_person(manifest, "same-person", role="captain")
    commander = generate_person(manifest, "same-person", role="commander")
    assert crew["personal_capability"] != crew["command_power"]
    assert crew["command_power"] <= captain["command_power"] <= commander["command_power"]


def test_validation_rejects_skill_drift_and_duplicate_traits():
    manifest = make_manifest(22)
    person = generate_person(manifest, "test-person")
    broken = copy.deepcopy(person)
    broken["skills"]["navigation"] = min(100, broken["skills"]["navigation"] + 1)
    with pytest.raises(ValueError, match="disagree with experience"):
        validate_person(broken)
    broken = copy.deepcopy(person)
    if broken["traits"]:
        broken["traits"].append(broken["traits"][0])
    else:
        broken["traits"] = ["cautious", "cautious"]
    with pytest.raises(ValueError, match="traits"):
        validate_person(broken)


def test_generated_society_has_coherent_unique_hierarchies_and_conserved_crew():
    manifest = make_manifest("society-seed", {"notable_people": 24, "organization_count": 4, "vessel_count": 8})
    world = generate_society(manifest)
    assert len(world["people"]) == 24
    assert len(world["organizations"]) == 4
    assert len(world["vessels"]) == 8
    assert len({person["name"] for person in world["people"].values()}) == 24
    assert len({organization["name"] for organization in world["organizations"].values()}) == 4
    assert len({vessel["name"] for vessel in world["vessels"].values()}) == 8
    assert world["population_ledger"]["total"] == sum(vessel["crew"] + vessel["captives"]
                                                        for vessel in world["vessels"].values())
    assert world["population_ledger"]["total"] == world["population_ledger"]["aboard"]
    for org_id, organization in world["organizations"].items():
        assert organization["leader_id"] in organization["member_ids"]
        assert organization["vessel_ids"]
        target = organization["active_goal"]["target_id"]
        if organization["active_goal"]["type"] == "trade":
            assert target in SEA_NODES
        else:
            assert target in set(world["organizations"]) - {org_id}
        for vessel_id in organization["vessel_ids"]:
            vessel = world["vessels"][vessel_id]
            assert vessel["organization_id"] == org_id
            assert vessel["captain_id"] in organization["member_ids"]
            assert vessel["captain_id"] in vessel["member_ids"]
            assert vessel["crew"] >= len(vessel["member_ids"])
        for person_id in organization["member_ids"]:
            person = world["people"][person_id]
            assert person["organization_id"] == org_id
            assert person["vessel_id"] in organization["vessel_ids"]
            assert person["person_id"] == person_id
            target = person["active_goal"]["target_id"]
            if person["active_goal"]["type"] == "trade":
                assert target in SEA_NODES
            else:
                assert target in set(world["organizations"]) - {org_id}

    known_people = set(world["people"])
    known_orgs = set(world["organizations"])
    person_edges = [row for row in world["relationships"] if row["entity_type"] == "person"]
    org_edges = [row for row in world["relationships"] if row["entity_type"] == "organization"]
    assert person_edges and org_edges
    assert len(person_edges) < len(known_people) * (len(known_people) - 1)
    assert all(row["source_id"] in known_people and row["target_id"] in known_people
               for row in person_edges)
    assert all(row["source_id"] in known_orgs and row["target_id"] in known_orgs
               for row in org_edges)
    assert all(set(row["dimensions"]) == {"trust", "affection", "respect", "fear", "grievance", "obligation"}
               for row in world["relationships"])
    for agreement in world["agreements"]:
        assert len(agreement["participants"]) == len(agreement["signatories"]) == 2
        assert agreement["status"] == "active"
        assert all(organization_id in known_orgs for organization_id in agreement["participants"])


def test_generated_society_is_seed_reproducible_and_changes_meaningfully_between_seeds():
    first = generate_society(make_manifest(808, {"notable_people": 32, "organization_count": 4, "vessel_count": 10}))
    repeated = generate_society(make_manifest(808, {"notable_people": 32, "organization_count": 4, "vessel_count": 10}))
    different = generate_society(make_manifest(809, {"notable_people": 32, "organization_count": 4, "vessel_count": 10}))
    assert first == repeated
    assert first != different
    paired_background_changes = sum(
        left["background"] != right["background"]
        for left, right in zip(first["people"].values(), different["people"].values()))
    assert paired_background_changes > 16


def test_world_can_start_generated_and_roundtrip_seeded_roster_without_reroll(tmp_path):
    settings = {"notable_people": 24, "organization_count": 4, "vessel_count": 8, "history_days": 2}
    world = World.new_generated("save-seed", settings)
    assert world.generation_manifest["seed"] == "s:save-seed"
    assert world.npcs.roster_mode == "generated"
    assert len(world.npcs.ships) == 8
    assert len(world.society.people) == 24
    opening = world.society.snapshot()

    path = tmp_path / "generated-captain.json"
    world.save(path)
    loaded = World.load(path)
    assert loaded.generation_manifest == world.generation_manifest
    assert loaded.npcs.snapshot() == world.npcs.snapshot()
    assert loaded.society.snapshot() == opening
    assert loaded.society.snapshot() == world.society.snapshot()


def test_generated_world_rejects_mismatched_manifest_and_fleet_state(tmp_path):
    import json

    world = World.new_generated(901, {"history_days": 0})
    path = tmp_path / "generated-captain.json"
    world.save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["generation_manifest"]["seed"] = make_manifest(902)["seed"]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="roster and generation manifest disagree"):
        World.load(path)

    data["generation_manifest"] = world.generation_manifest
    data["npcs"]["ships"][0]["crew"] += 1
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="headcount was created or lost"):
        World.load(path)


def test_generated_save_backfills_command_power_without_rerolling_person(tmp_path):
    import json

    world = World.new_generated("command-power-migration", {"history_days": 0})
    path = tmp_path / "generated-command-power.json"
    world.save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    person_id, saved_person = next(iter(data["society"]["people"].items()))
    saved_person.pop("command_power")
    path.write_text(json.dumps(data), encoding="utf-8")

    loaded = World.load(path)
    migrated = loaded.society.people[person_id]
    assert migrated["command_power"] == derive_command_power(migrated)
    for key in ("name", "age", "background", "skills", "skill_xp", "personal_capability", "reputation"):
        assert migrated[key] == world.society.people[person_id][key]


def test_generated_early_save_migrates_goal_and_runtime_ledgers_without_regeneration(tmp_path):
    import json

    world = World.new_generated("early-generated-save", {"history_days": 0})
    path = tmp_path / "early-generated-save.json"
    world.save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    society = data["society"]
    person_id = next(iter(society["people"]))
    person = society["people"][person_id]
    goal = person["active_goal"]
    goal["type"] = "pursue_ambition"
    goal["target_id"] = next(org_id for org_id in society["organizations"]
                              if org_id != person["organization_id"])
    goal.pop("status")
    goal.pop("completed_hour")
    for saved_person in society["people"].values():
        saved_person.pop("command_power", None)
        saved_person.get("active_goal", {}).pop("status", None)
        saved_person.get("active_goal", {}).pop("completed_hour", None)
    for organization in society["organizations"].values():
        organization.get("active_goal", {}).pop("status", None)
        organization.get("active_goal", {}).pop("completed_hour", None)
    society.pop("memories")
    society.pop("knowledge")
    society.pop("decision_state")
    for agreement in society["agreements"]:
        agreement.pop("fulfillments", None)
        agreement.pop("shared_reports", None)
    path.write_text(json.dumps(data), encoding="utf-8")

    loaded = World.load(path)
    assert loaded.generation_manifest == world.generation_manifest
    assert loaded.society.people[person_id]["active_goal"]["type"] == "hunt"
    assert loaded.society.people[person_id]["active_goal"]["target_id"] in loaded.society.organizations
    assert all("command_power" in row for row in loaded.society.people.values())
    assert set(loaded.society.knowledge) == set(loaded.society.organizations)
    assert set(loaded.society.memories) == set(loaded.society.people) | set(loaded.society.organizations)
    assert loaded.society.decision_state == {}


def test_bounded_prehistory_advances_only_generated_world_clocks_deterministically():
    settings = {"history_days": 3}
    first = World.new_generated("prehistory-seed", settings)
    second = World.new_generated("prehistory-seed", settings)
    assert first.clock.total_ticks == 8 * 360000 + 3 * 24 * 360000
    assert first.npcs.hour == first.society.hour == first.clock.total_ticks // 360000
    assert first.npcs.snapshot() == second.npcs.snapshot()
    assert first.society.snapshot() == second.society.snapshot()
    assert first.society.news
    assert any(memory for memory in first.society.memories.values())
    assert first.npcs.population_total == (first.npcs.market["enslaved_people"]
        + sum(ship["crew"] + ship["captives"] for ship in first.npcs.ships))
    assert first.clock.total_ticks == second.clock.total_ticks
