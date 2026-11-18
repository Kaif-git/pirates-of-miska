import copy
import random

from pirates.npc_decisions import choose_action, score_action
from pirates.npc_generation import generate_person, make_manifest
from pirates.game import World
from pirates.voyage import NpcFleetSystem, SEA_NODES
from pirates.management import generated_person_profile_rows, generated_society_menu, sea_map_rows


def generated_person(person_id="decision-person"):
    return generate_person(make_manifest("decision-tests"), person_id, role="captain",
                           organization_kind="pirate_fleet")


def test_personality_traits_and_values_change_strategic_preferences():
    bold = generated_person("bold-one")
    bold["personality"].update(boldness=92, pride=88, discipline=45)
    bold["values"].update(fame=90, wealth=30)
    bold["traits"] = ["bold", "vindictive"]
    cautious = copy.deepcopy(bold)
    cautious["personality"].update(boldness=18, patience=92, empathy=88)
    cautious["values"].update(fame=25, wealth=85)
    cautious["traits"] = ["cautious", "merciful"]

    candidates = [
        {"action": {"kind": "hunt", "base": 35, "risk": 40, "cost": 8,
                    "goal_progress": 25, "goal_match": "pursue_ambition"},
         "goal": {"type": "pursue_ambition"}, "relationship_bonus": 4},
        {"action": {"kind": "trade", "base": 34, "risk": 5, "cost": 12,
                    "goal_progress": 10}, "goal": {"type": "pursue_ambition"}},
    ]
    bold_decision = choose_action(bold, candidates, make_manifest("decision-tests"), "bold-one", 12)
    cautious_decision = choose_action(cautious, candidates, make_manifest("decision-tests"), "cautious-one", 12)
    assert bold_decision["kind"] == "hunt"
    assert cautious_decision["kind"] == "trade"
    assert bold_decision["risk_tolerance"] > cautious_decision["risk_tolerance"]


def test_score_breakdown_explains_goal_relationship_risk_and_cost():
    person = generated_person()
    action = {"kind": "negotiate", "base": 20, "goal_progress": 40, "risk": 8,
              "cost": 6, "goal_match": "pursue_ambition"}
    goal = {"type": "pursue_ambition"}
    supported = score_action(person, action, goal, relationship_bonus=20)
    unsupported = score_action(person, action, goal, relationship_bonus=-20)
    assert supported["goal_bonus"] >= 26
    assert supported["relationship_bonus"] > unsupported["relationship_bonus"]
    assert supported["risk_penalty"] >= 0
    assert supported["cost_penalty"] >= 0
    assert supported["score"] > unsupported["score"]


def test_decisions_are_event_keyed_repeatable_and_do_not_consume_global_rng():
    person = generated_person()
    manifest = make_manifest("tie-seed")
    candidates = [
        {"action": {"kind": "trade", "base": 50}},
        {"action": {"kind": "patrol", "base": 50}},
    ]
    state = random.getstate()
    first = choose_action(person, candidates, manifest, "captain-id", 99)
    repeated = choose_action(person, candidates, manifest, "captain-id", 99)
    next_event = choose_action(person, candidates, manifest, "captain-id", 100)
    assert first == repeated
    assert first["kind"] in ("trade", "patrol")
    assert next_event["kind"] in ("trade", "patrol")
    assert random.getstate() == state


def test_commitment_hysteresis_prevents_small_tick_to_tick_action_flips():
    person = generated_person()
    current = {"action": {"kind": "trade", "base": 45}}
    slightly_better = {"action": {"kind": "patrol", "base": 46}}
    choice = choose_action(person, [slightly_better, current], make_manifest(5), "captain", 3,
                           current_kind="trade", commitment=80)
    assert choice["kind"] == "trade"


def test_generated_tick_records_limited_knowledge_and_a_persisted_explanation():
    world = World.new_generated("knowledge-seed", {"history_days": 0})
    ships = world.npcs.ships
    # Place one ship from each of two organizations together. A third
    # organization's unrelated vessel remains unseen unless co-located.
    first = ships[0]
    first_org = first["organization_id"]
    second = next(ship for ship in ships[1:] if ship["organization_id"] != first_org)
    second_org = second["organization_id"]
    third = next(ship for ship in ships[1:] if ship["organization_id"] not in (first_org, second_org))
    second["node"] = first["node"]
    second["destination"] = first["destination"]
    third["node"] = next(node for node in SEA_NODES if node != first["node"])
    world.clock.total_ticks = (world.society.hour + 1) * 360000
    world.society.advance(world, [])
    contacts = world.society.knowledge[first_org]
    assert first["id"] in contacts and contacts[first["id"]]["source"] == "own fleet"
    assert second["id"] in contacts and contacts[second["id"]]["source"] == first["id"]
    assert third["id"] not in contacts
    assert world.society.decision_state[first["id"]]["explanation"]
    assert world.society.decision_state[first["id"]]["target_node"] in SEA_NODES

    state = world.society.snapshot()
    restored = type(world.society).restore(state, world.npcs, current_hour=world.society.hour,
                                            generation_manifest=world.generation_manifest)
    assert restored.knowledge == world.society.knowledge
    assert restored.decision_state == world.society.decision_state


def test_goal_progress_requires_a_matching_consequential_event_not_arrival():
    world = World.new_generated("goal-event-seed", {"history_days": 0})
    first_ship = world.npcs.ships[0]
    second_ship = next(ship for ship in world.npcs.ships
                       if ship["organization_id"] != first_ship["organization_id"])
    first_org = world.society.organizations[first_ship["organization_id"]]
    captain = world.society.people[first_ship["captain_id"]]
    goal = captain["active_goal"]
    goal.update(type="hunt", target_id=second_ship["organization_id"],
                progress=0, status="active", completed_hour=None)

    world.society._append_news(world.society.hour + 1, "port_arrival",
                               [first_ship["id"], captain["person_id"], first_org["organization_id"]],
                               first_ship["node"], "arrival", "arrived", "A ship arrives", "Routine arrival")
    assert goal["progress"] == 0
    assert goal["status"] == "active"

    participants = [first_ship["id"], second_ship["id"], first_ship["organization_id"],
                    second_ship["organization_id"], first_ship["captain_id"], second_ship["captain_id"]]
    for index in (1, 2):
        world.society._append_news(world.society.hour + index, "npc_clash", participants,
                                   first_ship["node"], "feud with target", "clash resolved",
                                   "Ships clash", "An actual clash took place")
    assert goal["progress"] == 100
    assert goal["status"] == "completed"
    assert goal["completed_hour"] == world.society.hour + 2


def test_pirate_target_candidates_include_known_marine_and_comparable_rival():
    world = World.new_generated("predatory-targets", {"history_days": 0})
    society = world.society
    pirate_org = next(org for org in society.organizations.values() if org["kind"] == "pirate_fleet")
    marine_org = next(org for org in society.organizations.values() if org["kind"] == "naval_command")
    rival_org = next(org for org in society.organizations.values()
                     if org["kind"] == "independent_crew")
    pirate_ship = next(ship for ship in world.npcs.ships
                       if ship["organization_id"] == pirate_org["organization_id"])
    captain = society.people[pirate_ship["captain_id"]]
    marine_ship = next(ship for ship in world.npcs.ships
                       if ship["organization_id"] == marine_org["organization_id"])
    rival_ship = next(ship for ship in world.npcs.ships
                      if ship["organization_id"] == rival_org["organization_id"])
    society._set_relationship(pirate_org["organization_id"], rival_org["organization_id"], -70)
    society.knowledge[pirate_org["organization_id"]].update({
        marine_ship["id"]: {"node": "navy_lane", "observed_hour": society.hour,
                            "source": pirate_ship["id"], "estimated_strength": 75},
        rival_ship["id"]: {"node": "shoals", "observed_hour": society.hour,
                           "source": pirate_ship["id"], "estimated_strength": 50},
    })
    # The target estimate remains the value recorded at sighting, not an
    # omniscient read of the rival's current crew after the report was made.
    rival_ship["crew"] = 1
    goal = captain["active_goal"]
    goal.update(type="hunt", target_id=marine_org["organization_id"], reason="win fame by hunting rivals")

    _, candidates = society._generated_candidates(world, pirate_ship, captain, pirate_org,
                                                   society.hour + 1)
    hunts = {candidate["target_ship_id"]: candidate for candidate in candidates
             if candidate["action"]["kind"] == "hunt"}
    assert marine_ship["id"] in hunts
    assert rival_ship["id"] in hunts
    assert hunts[marine_ship["id"]]["target_node"] == "navy_lane"
    assert hunts[rival_ship["id"]]["target_node"] == "shoals"
    assert hunts[rival_ship["id"]]["action"]["risk"] < 70


def test_generated_sea_chart_hides_unconfirmed_rival_positions():
    world = World.new_generated("chart-knowledge", {"history_days": 0})
    rows = sea_map_rows(world)
    assert "No rival vessel positions are currently confirmed." in rows
    assert all(ship["name"] not in "\n".join(rows) for ship in world.npcs.ships)
    ship = world.npcs.ships[0]
    world.npcs.sighted.add(ship["id"])
    known_node = ship["node"]
    world.npcs.sighting_reports[ship["id"]] = {
        "node": known_node, "observed_hour": world.npcs.hour,
        "crew": ship["crew"], "condition": ship["condition"],
    }
    ship["node"] = "deepwater" if known_node != "deepwater" else "shoals"
    rows = sea_map_rows(world)
    assert world.npcs.ships[0]["name"] in "\n".join(rows)
    assert f"last seen at {SEA_NODES[known_node]['name']}" in "\n".join(rows)
    assert f"last seen at {SEA_NODES[ship['node']]['name']}" not in "\n".join(rows)
    assert all(ship["name"] not in "\n".join(rows) for ship in world.npcs.ships[1:])
    restored = NpcFleetSystem.restore(world.npcs.snapshot(), world.generation_manifest)
    assert restored.sighting_reports == world.npcs.sighting_reports
    legacy_snapshot = world.npcs.snapshot()
    legacy_snapshot.pop("sighting_reports")
    migrated = NpcFleetSystem.restore(legacy_snapshot, world.generation_manifest)
    assert migrated.sighted == restored.sighted
    assert migrated.sighting_reports == {}


def test_generated_public_profile_excludes_private_personality_and_goal_data():
    world = World.new_generated("public-profile", {"history_days": 0})
    person = next(iter(world.society.people.values()))
    rows = generated_person_profile_rows(world, person)
    rendered = "\n".join(rows)
    assert person["name"] in rendered
    assert "no recent player sighting" in rendered
    assert "Private traits, values, goals" in rendered
    assert "boldness" not in rendered and "personality" not in rendered
    assert person["active_goal"]["reason"] not in rendered


def test_public_profile_uses_only_a_sighted_historical_strength_estimate():
    world = World.new_generated("public-strength-profile", {"history_days": 0})
    ship = world.npcs.ships[0]
    target = next(row for row in world.npcs.ships
                  if row["organization_id"] != ship["organization_id"])
    ship["node"] = target["node"] = "shoals"
    ship["condition"] = target["condition"] = 80
    world.npcs.sighted.add(ship["id"])
    world.npcs.sighting_reports[ship["id"]] = {
        "node": ship["node"], "observed_hour": world.npcs.hour,
        "crew": ship["crew"], "condition": ship["condition"],
    }
    world.society._set_relationship(ship["organization_id"], target["organization_id"], -90)
    world.society._resolve_clash(world, ship, target, world.society.hour + 1)
    captain = world.society.people[ship["captain_id"]]
    world.npcs.sighting_reports[ship["id"]]["node"] = "navy_lane"
    ship["node"] = "cinder"

    rendered = "\n".join(generated_person_profile_rows(world, captain))

    assert "Last confirmed vessel:" in rendered and "Government Patrol Lane" in rendered
    assert "Last confirmed vessel:" in rendered and "Cinder Quay" not in rendered
    assert "Last public encounter estimate: strategic strength" in rendered
    assert "not a current reading" in rendered
    assert captain["active_goal"]["reason"] not in rendered


def test_public_organization_profile_shows_only_reported_assets_relationships_and_strength(monkeypatch):
    world = World.new_generated("public-organization-profile", {"history_days": 0})
    ship = world.npcs.ships[0]
    target = next(row for row in world.npcs.ships
                  if row["organization_id"] != ship["organization_id"])
    ship["node"] = target["node"] = "shoals"
    ship["condition"] = target["condition"] = 80
    world.npcs.sighting_reports[ship["id"]] = {
        "node": "shoals", "observed_hour": world.npcs.hour,
        "crew": ship["crew"], "condition": ship["condition"],
    }
    world.npcs.sighted.add(ship["id"])
    world.society._set_relationship(ship["organization_id"], target["organization_id"], -90)
    world.society._resolve_clash(world, ship, target, world.society.hour + 1)
    first_org = world.society.organizations[ship["organization_id"]]
    second_org = world.society.organizations[target["organization_id"]]
    world.society._append_news(
        world.society.hour + 2, "petty_feud",
        [first_org["organization_id"], second_org["organization_id"],
         first_org["leader_id"], second_org["leader_id"]], "shoals",
        "A captain took a silly insult personally", "Public relations soured",
        "Two crews quarrel over a ridiculous insult", "A named argument became port gossip.",
        {"incident_id": "lucky_hat"})
    ordered_orgs = sorted(world.society.organizations.values(), key=lambda row: row["name"])
    select_index = len(world.society.people) + ordered_orgs.index(first_org)
    captures = []
    choices = iter((select_index, len(world.society.people) + len(ordered_orgs)))
    monkeypatch.setattr("pirates.management.choose", lambda *args, **kwargs: next(choices))
    monkeypatch.setattr("pirates.management.panel",
                        lambda _renderer, _world, title, rows: captures.append((title, rows)))

    generated_society_menu(None, world)

    title, rows = captures[0]
    rendered = "\n".join(rows)
    assert first_org["name"] in title
    assert "Vessels:" in rendered
    assert "Known relationship reports:" in rendered
    assert "Reported vessel strength:" in rendered
    assert "not current" in rendered
    assert "private" not in rendered.lower()
    assert world.society.people[first_org["leader_id"]]["active_goal"]["reason"] not in rendered


def test_signed_agreement_shares_reports_and_records_event_linked_memories():
    world = World.new_generated("agreement-seed", {"history_days": 0})
    org_ids = list(world.society.organizations)
    first_id, second_id = org_ids[:2]
    first, second = world.society.organizations[first_id], world.society.organizations[second_id]
    agreement = {
        "agreement_id": "agreement-test",
        "kind": "mutual_aid",
        "participants": [first_id, second_id],
        "signatories": [first["leader_id"], second["leader_id"]],
        "purpose": "share reports",
        "terms": ["share reports"],
        "start_hour": world.society.hour,
        "expires_hour": world.society.hour + 20,
        "status": "active",
        "basis": "test",
        "fulfillments": [],
        "shared_reports": [],
    }
    world.society.agreements = [agreement]
    world.society._update_generated_knowledge(world, world.society.hour)
    owned_ship = first["vessel_ids"][0]
    assert world.society.knowledge[second_id][owned_ship]["source"] == "agreement:agreement-test"
    assert agreement["fulfillments"]
    event_id = agreement["fulfillments"][0]
    assert any(memory["event_id"] == event_id for memory in world.society.memories[first_id])
    assert any(memory["event_id"] == event_id for memory in world.society.memories[second_id])

    restored = type(world.society).restore(
        world.society.snapshot(), world.npcs, current_hour=world.society.hour,
        generation_manifest=world.generation_manifest)
    assert restored.agreements == world.society.agreements
    assert restored.memories == world.society.memories


def test_agreement_expiry_is_a_causal_persisted_news_event():
    world = World.new_generated("expiry-seed", {"history_days": 0})
    org_ids = list(world.society.organizations)
    first_id, second_id = org_ids[:2]
    first, second = world.society.organizations[first_id], world.society.organizations[second_id]
    expiry_hour = world.society.hour + 1
    agreement = {
        "agreement_id": "agreement-expiring",
        "kind": "mutual_aid",
        "participants": [first_id, second_id],
        "signatories": [first["leader_id"], second["leader_id"]],
        "purpose": "temporary support",
        "terms": ["provide aid"],
        "start_hour": world.society.hour,
        "expires_hour": expiry_hour,
        "status": "active",
        "basis": "test",
        "fulfillments": [],
        "shared_reports": [],
    }
    world.society.agreements = [agreement]
    world.clock.total_ticks = expiry_hour * 360000
    world.society.advance(world, [])
    assert agreement["status"] == "expired"
    assert agreement["expiry_event_id"]
    assert any(row["id"] == agreement["expiry_event_id"] and row["kind"] == "agreement_expiry"
               for row in world.society.news)
    restored = type(world.society).restore(
        world.society.snapshot(), world.npcs, current_hour=expiry_hour,
        generation_manifest=world.generation_manifest)
    assert restored.agreements[0]["status"] == "expired"


def test_hostile_clash_breaches_a_signed_agreement_with_event_memories():
    world = World.new_generated("breach-seed", {"history_days": 0})
    first_ship = world.npcs.ships[0]
    second_ship = next(ship for ship in world.npcs.ships
                       if ship["organization_id"] != first_ship["organization_id"])
    first_id, second_id = first_ship["organization_id"], second_ship["organization_id"]
    first_org, second_org = world.society.organizations[first_id], world.society.organizations[second_id]
    agreement = {
        "agreement_id": "agreement-breach",
        "kind": "mutual_aid",
        "participants": [first_id, second_id],
        "signatories": [first_org["leader_id"], second_org["leader_id"]],
        "purpose": "mutual aid",
        "terms": ["do not attack signatories"],
        "start_hour": world.society.hour,
        "expires_hour": world.society.hour + 20,
        "status": "active",
        "basis": "test",
        "fulfillments": [],
        "shared_reports": [],
    }
    world.society.agreements = [agreement]
    world.society._set_relationship(first_id, second_id, -90)
    second_ship["node"] = first_ship["node"]
    breach_hour = world.society.hour + 1
    world.society._resolve_clash(world, first_ship, second_ship, breach_hour)
    world.society.hour = breach_hour
    assert agreement["status"] == "breached"
    breach_id = agreement["breach_event_id"]
    assert any(memory["event_id"] == breach_id for owner in (first_id, second_id)
               for memory in world.society.memories[owner])
    restored = type(world.society).restore(
        world.society.snapshot(), world.npcs, current_hour=breach_hour,
        generation_manifest=world.generation_manifest)
    assert restored.agreements[0]["status"] == "breached"


def test_generated_save_load_continuation_and_hour_chunking_are_deterministic(tmp_path):
    one_shot = World.new_generated("continuation-seed", {"history_days": 0})
    stepped = World.new_generated("continuation-seed", {"history_days": 0})
    save_path = tmp_path / "generated-continuation.json"
    stepped.save(save_path)
    stepped = World.load(save_path)
    starting_ticks = one_shot.clock.total_ticks

    one_shot.clock.total_ticks = starting_ticks + 2 * 360000
    one_shot.society.advance(one_shot, [])
    for hour in (1, 2):
        stepped.clock.total_ticks = starting_ticks + hour * 360000
        stepped.society.advance(stepped, [])

    assert one_shot.npcs.snapshot() == stepped.npcs.snapshot()
    assert one_shot.society.snapshot() == stepped.society.snapshot()
    assert one_shot.generation_manifest == stepped.generation_manifest
