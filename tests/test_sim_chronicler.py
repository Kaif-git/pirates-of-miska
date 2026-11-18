import copy

import pytest

from sim.chronicler import Chronicler, NameRegistry, load_templates
from sim.chronicler.core import _year_for_tick, validate_templates
from sim.core.world import WorldState


def _event(world, event_type, actor_id, target_id=None, *, data=None, magnitude=0.8):
    root_id = world.event_log.by_type("WORLD_GENERATED")[0].id
    return world.event_log.append(
        tick=world.clock.tick, event_type=event_type,
        location=world.entities[actor_id]["location"]["id"],
        actors=[{"role": "actor", "entity_id": actor_id}],
        targets=([{"role": "target", "entity_id": target_id}] if target_id else []),
        data=data or {}, caused_by=[root_id], causes_tags=["test_event"], magnitude=magnitude,
    )


def test_chronicler_template_catalog_is_valid_and_rejects_ids_unknown_slots_and_self_reference():
    catalog = load_templates()
    assert "ENCOUNTER_RESOLVED" in catalog["templates"]

    invalid = copy.deepcopy(catalog)
    invalid["templates"]["BROKEN"] = {
        "required_slots": ["actor"],
        "variants": [{"tone": "plain", "context_tags": [],
                      "text": "{actor} remembers event_000001."}],
    }
    with pytest.raises(ValueError, match="Unsafe"):
        validate_templates(invalid)

    invalid = copy.deepcopy(catalog)
    invalid["templates"]["BROKEN"] = {
        "required_slots": ["actor"],
        "variants": [{"tone": "plain", "context_tags": [], "text": "I am {actor}."}],
    }
    with pytest.raises(ValueError, match="Unsafe"):
        validate_templates(invalid)


def test_chronicler_narration_uses_names_place_and_causal_clause_not_internal_ids():
    world = WorldState.create("chronicle-narration", director_enabled=False)
    people = sorted(entity_id for entity_id, row in world.entities.items()
                    if row.get("kind") == "character")
    event = _event(world, "ENCOUNTER_RESOLVED", people[0], people[1],
                   data={"outcome": "mutual_withdrawal"})
    chronicler = Chronicler()

    text = chronicler.narrate(world, event)

    assert world.entities[people[0]]["display_name"] in text
    assert world.entities[people[1]]["display_name"] in text
    assert world.geography.islands[world.entities[people[0]]["location"]["id"]].name in text
    assert "mutual withdrawal" in text
    assert "event_" not in text and people[0] not in text and people[1] not in text


def test_epithets_are_deed_earned_and_provenanced_without_mutating_world():
    world = WorldState.create("chronicle-epithets", director_enabled=False)
    people = sorted(entity_id for entity_id, row in world.entities.items()
                    if row.get("kind") == "character")
    captain = people[0]
    before = copy.deepcopy(world.to_snapshot())
    breach = _event(world, "TREATY_BREACHED", captain, people[1], data={"reason": "betrayal"})
    chronicler = Chronicler()

    awards = chronicler.epithet_awards(world)
    award = next(row for row in awards if row.entity_id == captain)

    assert award.epithet == "Oathbreaker"
    assert award.deed_event_id == breach.id
    assert world.to_snapshot() == {**before, "events": world.event_log.to_records()}
    assert world.entities[captain]["epithet"] is None


def test_hull_breaker_credit_goes_to_the_attacking_captain_not_the_sunk_crew():
    world = WorldState.create("chronicle-hull-breaker", director_enabled=False)
    ships = sorted(entity_id for entity_id, row in world.entities.items()
                   if row.get("kind") == "ship")
    attacker, victim = ships[:2]
    attacker_captain = world.crew_ships.crews[world.entities[attacker]["crew_id"]]["captain_id"]
    victim_captain = world.crew_ships.crews[world.entities[victim]["crew_id"]]["captain_id"]
    parent = world.event_log.append(
        tick=0, event_type="ENCOUNTER_RESOLVED", location=world.entities[attacker]["location"]["id"],
        actors=[{"role": "combatant", "entity_id": attacker},
                {"role": "combatant", "entity_id": victim}], targets=[],
        data={"ships": [attacker, victim]}, caused_by=[world.event_log.by_type(
            "WORLD_GENERATED")[0].id], causes_tags=["test_combat"], magnitude=0.8,
    )
    for _ in range(5):
        world.event_log.append(
            tick=0, event_type="SHIP_SUNK", location=world.entities[victim]["location"]["id"],
            actors=[], targets=[{"role": "sunken_ship", "entity_id": victim}],
            data={"crew_id": world.entities[victim]["crew_id"]}, caused_by=[parent.id],
            causes_tags=["ship_sinking"], magnitude=1.0,
        )

    awards = {award.entity_id: award for award in Chronicler().epithet_awards(world)}

    assert awards[attacker_captain].epithet == "the Hull-Breaker"
    assert victim_captain not in awards


def test_chronicler_artifact_bundle_has_expected_forms_and_is_pure():
    world = WorldState.create("chronicle-artifacts", director_enabled=False)
    people = sorted(entity_id for entity_id, row in world.entities.items()
                    if row.get("kind") == "character")
    _event(world, "TREATY_BREACHED", people[0], people[1], data={"reason": "betrayal"})
    snapshot = copy.deepcopy(world.to_snapshot())
    chronicler = Chronicler()

    artifacts = chronicler.generate_all(world)

    assert {"chronicle", "rumors", "bounty_posters", "biographies", "faction_histories",
            "epithet_awards", "legends", "almanac", "obituaries", "weekly_digest"} \
        <= artifacts.keys()
    assert artifacts["epithet_awards"]
    assert artifacts["biographies"]
    biography_names = {row["name"] for row in artifacts["biographies"]}
    assert any(name.startswith(NameRegistry(world).entity(people[0])) for name in biography_names)
    assert world.to_snapshot() == snapshot
    for entry in artifacts["chronicle"]:
        assert "event_" not in entry["text"]
        assert "{" not in entry["text"] and "}" not in entry["text"]


def test_rumor_generation_is_repeatable_and_marked_as_distorted():
    world = WorldState.create("chronicle-rumor", director_enabled=False)
    people = sorted(entity_id for entity_id, row in world.entities.items()
                    if row.get("kind") == "character")
    event = _event(world, "TREATY_BREACHED", people[0], people[1], data={"reason": "betrayal"})
    chronicler = Chronicler()

    first = chronicler.rumor(world, event, rumor_seed=9)
    second = chronicler.rumor(world, event, rumor_seed=9)

    assert first == second
    assert first["distorted"] is True
    assert "event_" not in first["text"]


def test_name_registry_uses_lineage_suffix_only_when_explicitly_recorded():
    world = WorldState.create("chronicle-lineage", director_enabled=False)
    people = sorted(entity_id for entity_id, row in world.entities.items()
                    if row.get("kind") == "character")
    inherited_id = world.characters.create_character(
        world, tier="MINOR", role="captain", cause_event_id=world.event_log.by_type(
            "WORLD_GENERATED")[0].id, name="Alden Lineage",
        heritage={"parents": [people[0], None], "titles": ["Jr."]},
    )
    unrelated_id = world.characters.create_character(
        world, tier="MINOR", role="captain", cause_event_id=world.event_log.by_type(
            "WORLD_GENERATED")[0].id, name="Bryn Lineage",
    )

    registry = NameRegistry(world)

    assert registry.entity(inherited_id) == "Alden Lineage Jr."
    assert registry.entity(unrelated_id) == "Bryn Lineage"


def test_year_boundary_tick_remains_in_the_year_that_just_finished():
    world = WorldState.create("chronicle-calendar", director_enabled=False)
    year_ticks = (world.config["time"]["days_per_year"]
                  * world.config["time"]["hours_per_day"]
                  // world.config["time"]["hours_per_tick"])

    assert _year_for_tick(world, 0) == 1
    assert _year_for_tick(world, year_ticks) == 1
    assert _year_for_tick(world, year_ticks + 1) == 2


def test_name_registry_enforces_living_uniqueness_but_allows_archived_ship_reuse():
    world = WorldState.create("chronicle-ship-replacement", director_enabled=False)
    ships = sorted(entity_id for entity_id, row in world.entities.items()
                   if row.get("kind") == "ship")
    first, second = ships[:2]
    prior = copy.deepcopy(world.entities[first])
    prior["id"] = "ship_archived_test"
    world.archive[prior["id"]] = prior
    world.archive[prior["id"]]["name"] = world.entities[second]["name"]

    assert NameRegistry(world).entity(prior["id"]) == world.entities[second]["name"]
    world.entities[second]["name"] = world.entities[first]["name"]
    with pytest.raises(ValueError, match="duplicate living"):
        NameRegistry(world)
