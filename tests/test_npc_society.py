import json

import pytest

from pirates.game import World
from pirates.society import NpcSocietySystem, relation_key, relation_status
from pirates.voyage import NpcFleetSystem
from pirates.npc_generation import random_stream


def quiet_world():
    world = World()
    for sailor in world.ship.crew:
        sailor.labors = {kind: 0 for kind in sailor.labors}
        sailor.hunger = sailor.thirst = sailor.fatigue = sailor.loneliness = sailor.boredom = 0
        sailor.sleep_debt = 20
    world.npcs.sighted.update(ship["id"] for ship in world.npcs.ships)
    return world


def people_total(world):
    return (world.npcs.market["enslaved_people"]
            + sum(ship["crew"] + ship["captives"] for ship in world.npcs.ships))


def test_authored_cast_has_pirate_hierarchy_independent_crews_and_marines():
    world = quiet_world()
    society = world.society
    blackwake = society.organizations["blackwake_fleet"]
    assert blackwake["leader"] == "mara_vey"
    assert blackwake["ships"] == ["raider_1", "blackwake_escort"]
    assert society.people["mara_vey"]["name"] == "Mara Vey"
    assert society.people["tamsin_rill"]["organization"] == "blackwake_fleet"
    assert society.organizations["salt_jackals"]["kind"] == "independent_crew"
    assert society.organizations["world_government"]["kind"] == "naval_command"
    assert all(person["ship"] in {ship["id"] for ship in world.npcs.ships}
               for person in society.people.values())


def test_relationships_are_signed_and_map_to_legible_social_states():
    assert relation_status(80) == "allied"
    assert relation_status(40) == "friendly"
    assert relation_status(0) == "wary"
    assert relation_status(-45) == "rivals"
    assert relation_status(-90) == "hostile"


def test_hostile_npc_meeting_resolves_abstract_clash_and_creates_causal_news():
    world = quiet_world()
    society = world.society
    pirate = next(ship for ship in world.npcs.ships if ship["id"] == "raider_1")
    marine = next(ship for ship in world.npcs.ships if ship["id"] == "government_1")
    pirate["node"] = marine["node"] = "navy_lane"
    people_before = people_total(world)
    pirate_condition, marine_condition = pirate["condition"], marine["condition"]
    society._resolve_meetings(world, society.hour + 1)
    event = society.news[-1]
    assert event["kind"] == "npc_clash"
    assert "Mara Vey" in event["body"] and "Ilyan Rusk" in event["body"]
    assert event["cause"] and event["outcome"]
    assert "estimated strategic strength" in event["cause"]
    assert pirate["condition"] < pirate_condition or marine["condition"] < marine_condition
    assert pirate["destination"] != "navy_lane" or marine["destination"] != "navy_lane"
    assert people_total(world) == people_before
    assert society.relationship("blackwake_fleet", "world_government") < -82


def test_organization_pair_cooldown_prevents_repeating_a_clash_each_hour():
    world = quiet_world()
    society = world.society
    first, second = world.npcs.ships[2], world.npcs.ships[1]
    first["node"] = second["node"] = "navy_lane"
    society._set_relationship("blackwake_fleet", "world_government", -90)

    society._resolve_meetings(world, society.hour + 1)
    initial_clashes = sum(row["kind"] == "npc_clash" for row in society.news)
    assert initial_clashes == 1
    society._resolve_meetings(world, society.hour + 2)
    assert sum(row["kind"] == "npc_clash" for row in society.news) == initial_clashes


def test_generated_close_strength_clash_can_end_in_reproducible_stalemate():
    def prepared_world():
        world = World.new_generated("costly-standstill", {"history_days": 0})
        society = world.society
        first = world.npcs.ships[0]
        second = next(ship for ship in world.npcs.ships
                      if ship["organization_id"] != first["organization_id"])
        first["node"] = second["node"] = "shoals"
        first["crew"] = second["crew"] = 5
        first["condition"] = second["condition"] = 80
        for ship in (first, second):
            captain = society.people[ship["captain_id"]]
            captain["command_power"] = 50
            captain["reputation"] = 50
            captain["skills"]["tactics"] = 50
            captain["skills"]["seamanship"] = 50
            org = society.organizations[ship["organization_id"]]
            org["cohesion"] = 50
        society._set_relationship(first["organization_id"], second["organization_id"], -90)
        pair = "|".join(sorted((first["id"], second["id"])))
        hour = next(hour for hour in range(society.hour + 1, society.hour + 100)
                    if random_stream(world.generation_manifest, "clash_outcome",
                                     f"{hour}:{pair}").random() < 0.28)
        return world, first, second, hour

    first_world, first_a, first_b, hour = prepared_world()
    second_world, second_a, second_b, second_hour = prepared_world()
    assert hour == second_hour
    before = (first_a["condition"], first_b["condition"])

    first_world.society._resolve_clash(first_world, first_a, first_b, hour)
    second_world.society._resolve_clash(second_world, second_a, second_b, hour)

    event = first_world.society.news[-1]
    assert "trade damage" in event["headline"]
    assert first_a["condition"] == before[0] - 12
    assert first_b["condition"] == before[1] - 12
    assert first_world.society.snapshot() == second_world.society.snapshot()
    assert first_world.npcs.snapshot() == second_world.npcs.snapshot()


def test_condition_damage_never_repairs_a_battered_ship_and_low_readiness_avoids_clash():
    world = World.new_generated("low-readiness-retreat", {"history_days": 0})
    society = world.society
    battered = world.npcs.ships[0]
    opponent = next(ship for ship in world.npcs.ships
                    if ship["organization_id"] != battered["organization_id"])
    battered["condition"] = 8
    before = battered["condition"]

    actual_loss = society._apply_condition_damage(battered, 24)

    assert actual_loss == 7
    assert battered["condition"] == 1
    assert battered["condition"] < before
    assert not society._may_engage(battered, opponent, -100)


def test_named_captains_gain_bounded_experience_from_actual_clash_outcomes():
    from pirates.npc_generation import derive_command_power, random_stream

    world = World.new_generated("captain-clash-experience", {"history_days": 0})
    society = world.society
    first, second = world.npcs.ships[0], next(
        ship for ship in world.npcs.ships
        if ship["organization_id"] != world.npcs.ships[0]["organization_id"])
    first["node"] = second["node"] = "shoals"
    first["condition"] = second["condition"] = 80
    society._set_relationship(first["organization_id"], second["organization_id"], -90)
    society.organizations[first["organization_id"]]["cohesion"] = 50
    society.organizations[second["organization_id"]]["cohesion"] = 50
    captains = [society.people[first["captain_id"]], society.people[second["captain_id"]]]
    for captain in captains:
        for skill in ("tactics", "seamanship"):
            captain["skill_xp"][skill] = 1600
            captain["skills"][skill] = 40
        captain["command_power"] = derive_command_power(captain)
    before = {captain["person_id"]: dict(captain["skill_xp"]) for captain in captains}
    pair = "|".join(sorted((first["id"], second["id"])))
    hour = next(value for value in range(society.hour + 1, society.hour + 100)
                if random_stream(world.generation_manifest, "clash_outcome",
                                 f"{value}:{pair}").random() >= 0.28)

    society._resolve_clash(world, first, second, hour)

    event = next(row for row in reversed(society.news) if row["kind"] == "npc_clash")
    progression = event["details"]["captain_progression"]
    cohesion_change = event["details"]["cohesion_change"]
    assert sum(cohesion_change.values()) < 0
    assert all(value in (-5, 3) for value in cohesion_change.values())
    assert set(progression) == {captain["person_id"] for captain in captains}
    for captain in captains:
        result = progression[captain["person_id"]]
        assert sum(result["xp_gained"].values()) > 0
        assert captain["skill_xp"]["tactics"] > before[captain["person_id"]]["tactics"]
        assert captain["skill_xp"]["seamanship"] > before[captain["person_id"]]["seamanship"]
        assert captain["skills"]["tactics"] == int(captain["skill_xp"]["tactics"] ** 0.5)
        assert captain["command_power"] == result["command_power_after"]
    society.hour = hour
    restored = NpcSocietySystem.restore(
        society.snapshot(), world.npcs, current_hour=hour,
        generation_manifest=world.generation_manifest)
    assert next(row for row in restored.news if row["id"] == event["id"])["details"] == event["details"]


def test_generated_victory_transfers_bounded_silver_and_cargo_without_minting():
    from pirates.npc_generation import derive_command_power, random_stream

    world = World.new_generated("finite-clash-plunder", {"history_days": 0})
    society = world.society
    strong = world.npcs.ships[0]
    weak = next(ship for ship in world.npcs.ships
                if ship["organization_id"] != strong["organization_id"])
    strong["node"] = weak["node"] = "shoals"
    strong["crew"], weak["crew"] = 10, 4
    strong["condition"], weak["condition"] = 100, 50
    strong["silver"], weak["silver"] = 10, 100
    strong["cargo"], weak["cargo"] = {}, {"beans": 3}
    society._set_relationship(strong["organization_id"], weak["organization_id"], -90)
    for ship, level, cohesion in ((strong, 100, 100), (weak, 0, 0)):
        captain = society.people[ship["captain_id"]]
        captain["command_power"] = level
        captain["skills"]["tactics"] = level
        captain["skills"]["seamanship"] = level
        captain["skill_xp"]["tactics"] = level * level
        captain["skill_xp"]["seamanship"] = level * level
        captain["command_power"] = derive_command_power(captain)
        society.organizations[ship["organization_id"]]["cohesion"] = cohesion
    pair = "|".join(sorted((strong["id"], weak["id"])))
    hour = next(value for value in range(society.hour + 1, society.hour + 100)
                if random_stream(world.generation_manifest, "clash_outcome",
                                 f"{value}:{pair}").random() < 0.82)
    silver_before = strong["silver"] + weak["silver"]
    beans_before = strong["cargo"].get("beans", 0) + weak["cargo"].get("beans", 0)
    people_before = people_total(world)

    society._resolve_clash(world, strong, weak, hour)

    assert strong["condition"] < 100
    assert strong["silver"] + weak["silver"] == silver_before
    assert strong["silver"] > 10
    assert sum(ship["cargo"].get("beans", 0) for ship in (strong, weak)) == beans_before
    assert strong["cargo"].get("beans", 0) + weak["cargo"].get("beans", 0) == 3
    event = next(row for row in reversed(society.news) if row["kind"] == "npc_clash")
    assert event["details"]["outcome_type"] == "victory"
    assert event["details"]["transfers"]["silver"] > 0
    assert event["details"]["transfers"]["cargo"].get("beans", 0) > 0
    assert people_total(world) == people_before
    society.hour = hour
    restored = NpcSocietySystem.restore(
        society.snapshot(), world.npcs, current_hour=hour,
        generation_manifest=world.generation_manifest)
    restored_event = next(row for row in restored.news if row["id"] == event["id"])
    assert restored_event["details"] == event["details"]


def test_petty_pride_incident_creates_a_real_feud_and_future_hunt_opportunity():
    world = World.new_generated("ridiculous-pirate-feud", {"history_days": 0})
    society = world.society
    pirate_org = next(org for org in society.organizations.values() if org["kind"] == "pirate_fleet")
    rival_org = next(org for org in society.organizations.values()
                     if org["kind"] == "independent_crew")
    first_ship = next(ship for ship in world.npcs.ships
                      if ship["organization_id"] == pirate_org["organization_id"])
    second_ship = next(ship for ship in world.npcs.ships
                       if ship["organization_id"] == rival_org["organization_id"])
    first_ship["node"] = second_ship["node"] = "shoals"
    first = society.people[pirate_org["leader_id"]]
    second = society.people[rival_org["leader_id"]]
    for person in (first, second):
        person["personality"]["pride"] = 100
        person["personality"]["empathy"] = 0
        person["traits"] = sorted(set(person["traits"] + ["vindictive"]))
    society.agreements = []
    first_id, second_id = pirate_org["organization_id"], rival_org["organization_id"]
    society._set_relationship(first_id, second_id, 20)
    pair = relation_key(first_id, second_id)
    hour = next(value for value in range(society.hour + 1, society.hour + 100)
                if random_stream(world.generation_manifest, "petty_slight",
                                 f"{value}:{pair}").random() < 0.08)

    assert society._maybe_create_petty_feud(first_id, second_id, first_ship, second_ship, hour)
    event = society.news[-1]
    assert event["kind"] == "petty_feud"
    assert event["details"]["incident_id"] in {
        "lucky_hat", "borrowed_shanty", "parrot_laugh", "reef_naming", "stew_insult", "flag_mixup"}
    assert first["name"] in event["cause"] or second["name"] in event["cause"]
    assert society.relationship(first_id, second_id) <= -35
    assert any(memory["event_id"] == event["id"] and memory["valence"] == "adverse"
               for person_id in (first["person_id"], second["person_id"])
               for memory in society.memories[person_id])
    captain_memory = next(memory for memory in society.memories[first["person_id"]]
                          if memory["event_id"] == event["id"])
    assert captain_memory["cause"] == event["cause"]
    assert captain_memory["outcome"] == event["outcome"]
    assert {first_id, second_id}.issubset(captain_memory["participants"])

    society.knowledge[first_id][second_ship["id"]] = {
        "node": "shoals", "observed_hour": hour, "source": first_ship["id"],
        "estimated_strength": society._strength_for_ship(second_ship)["score"]}
    first["active_goal"].update(type="hunt", target_id=second_id)
    _, candidates = society._generated_candidates(world, first_ship, first, pirate_org, hour + 1)
    hunt = next(row for row in candidates if row["action"]["kind"] == "hunt"
                and row["target_org_id"] == second_id)
    with_memory_bonus = hunt["relationship_bonus"]
    society.memories[first["person_id"]] = [
        memory for memory in society.memories[first["person_id"]] if memory["event_id"] != event["id"]]
    _, without_memory = society._generated_candidates(world, first_ship, first, pirate_org, hour + 1)
    without_memory_hunt = next(row for row in without_memory if row["action"]["kind"] == "hunt"
                               and row["target_org_id"] == second_id)
    assert with_memory_bonus == without_memory_hunt["relationship_bonus"] + 15


def test_routine_news_cannot_evict_a_consequential_story_from_bounded_history():
    world = World.new_generated("priority-news-retention", {"history_days": 0})
    society = world.society
    first_org, second_org = list(society.organizations.values())[:2]
    first_ship = next(ship for ship in world.npcs.ships
                      if ship["organization_id"] == first_org["organization_id"])
    second_ship = next(ship for ship in world.npcs.ships
                       if ship["organization_id"] == second_org["organization_id"])
    story = society._append_news(
        society.hour + 1, "petty_feud",
        [first_org["organization_id"], second_org["organization_id"],
         first_org["leader_id"], second_org["leader_id"]],
        "shoals", "a ridiculous insult", "relations soured",
        "A hat insult begins a feud", "The silly incident had consequences.",
        {"incident_id": "lucky_hat"})
    followup = society._append_news(
        society.hour + 30, "npc_clash",
        [first_org["organization_id"], second_org["organization_id"],
         first_ship["id"], second_ship["id"]],
        "shoals", "rivalry after the hat insult", "silver changed hands",
        "The rival crews clash again", "The feud cost both crews hull and silver.",
        {"public_report": True, "strength": {first_ship["id"]: 50}})
    for index in range(200):
        society._append_news(society.hour + 2 + index, "npc_market_event", [], "verdant",
                             "routine trade", "goods exchanged", "A merchant trades", "Routine report")

    assert len(society.news) == 120
    assert any(row["id"] == story["id"] for row in society.news)
    reported_ids = {row["id"] for row in society.chronicle_rows(20, public_only=True)}
    assert story["id"] in reported_ids and followup["id"] in reported_ids
    assert sum(row["kind"] == "npc_market_event" for row in society.news) == 118


def test_generated_clash_reports_require_a_player_sighting():
    world = World.new_generated("public-clash-knowledge", {"history_days": 0})
    society = world.society
    first = world.npcs.ships[0]
    second = next(ship for ship in world.npcs.ships
                  if ship["organization_id"] != first["organization_id"])
    first["node"] = second["node"] = "shoals"
    first["condition"] = second["condition"] = 80
    society._set_relationship(first["organization_id"], second["organization_id"], -90)
    world.npcs.sighted.clear()
    world.npcs.sighting_reports.clear()

    society._resolve_clash(world, first, second, society.hour + 1)
    hidden = next(row for row in reversed(society.news) if row["kind"] == "npc_clash")
    assert hidden["details"]["public_report"] is False
    assert hidden not in society.chronicle_rows(20, public_only=True)
    hidden_relation = society._append_news(
        society.hour + 2, "rivalry",
        [first["organization_id"], second["organization_id"]], "shoals",
        f"the clash between {first['name']} and {second['name']}",
        "relationship worsened", "Tensions rise between two fleets",
        "The crews' relationship worsened after an unseen battle.", {"public_report": False})
    assert hidden_relation not in society.chronicle_rows(20, public_only=True)
    hidden_diplomacy = society._append_news(
        society.hour + 3, "diplomacy",
        [first["organization_id"], second["organization_id"]], "shoals",
        f"the clash between {first['name']} and {second['name']}",
        "relationship softened", "Relations shift between two fleets",
        "A private after-battle relationship change.", {"public_report": False})
    assert hidden_diplomacy not in society.chronicle_rows(20, public_only=True)

    world.npcs.sighted.add(first["id"])
    world.npcs.sighting_reports[first["id"]] = {
        "node": "shoals", "observed_hour": society.hour + 100,
        "crew": first["crew"], "condition": 80,
    }
    first["condition"] = second["condition"] = 80
    society._resolve_clash(world, first, second, society.hour + 100)
    witnessed = next(row for row in reversed(society.news) if row["kind"] == "npc_clash")
    assert witnessed["details"]["public_report"] is True
    assert witnessed in society.chronicle_rows(20, public_only=True)


def test_petty_slight_severity_depends_on_pride_vindictiveness_and_empathy():
    from pirates.npc_generation import random_stream

    def slight_result(seed, pride, vindictive, empathy):
        world = World.new_generated(seed, {"history_days": 0})
        society = world.society
        first_org = next(org for org in society.organizations.values() if org["kind"] == "pirate_fleet")
        second_org = next(org for org in society.organizations.values()
                          if org["kind"] == "independent_crew")
        first_ship = next(ship for ship in world.npcs.ships
                          if ship["organization_id"] == first_org["organization_id"])
        second_ship = next(ship for ship in world.npcs.ships
                           if ship["organization_id"] == second_org["organization_id"])
        first_ship["node"] = second_ship["node"] = "shoals"
        first = society.people[first_org["leader_id"]]
        second = society.people[second_org["leader_id"]]
        first["personality"].update(pride=pride, empathy=empathy)
        second["personality"].update(pride=pride, empathy=empathy)
        first["traits"] = [trait for trait in first["traits"] if trait != "vindictive"]
        second["traits"] = [trait for trait in second["traits"] if trait != "vindictive"]
        if vindictive:
            first["traits"].append("vindictive")
        society.agreements = []
        org_a, org_b = first_org["organization_id"], second_org["organization_id"]
        society._set_relationship(org_a, org_b, 20)
        pair = relation_key(org_a, org_b)
        chance = max(0.01, min(0.08, 0.018 + pride * 0.00035 + int(vindictive) * 0.012 - empathy * 0.0001))
        hour = next(value for value in range(1, 1001)
                    if random_stream(world.generation_manifest, "petty_slight",
                                     f"{value}:{pair}").random() < chance)
        assert society._maybe_create_petty_feud(org_a, org_b, first_ship, second_ship, hour)
        return society.relationship(org_a, org_b)

    mild = slight_result("mild-slight", pride=10, vindictive=False, empathy=100)
    severe = slight_result("severe-slight", pride=100, vindictive=True, empathy=0)
    assert mild == -15
    assert severe <= -35


def test_world_hourly_hook_publishes_a_real_offscreen_ship_clash():
    world = quiet_world()
    pirate = next(ship for ship in world.npcs.ships if ship["id"] == "raider_1")
    marine = next(ship for ship in world.npcs.ships if ship["id"] == "government_1")
    pirate["node"] = pirate["destination"] = "navy_lane"
    marine["node"] = marine["destination"] = "navy_lane"
    before = people_total(world)
    world.advance(3600 * 100)
    assert any(item["kind"] == "npc_clash" for item in world.society.news)
    assert world.society.hour == world.npcs.hour == world.clock.total_ticks // 360000
    assert people_total(world) == before


def test_repeated_peaceful_contact_can_form_a_newsworthy_alliance():
    world = quiet_world()
    society = world.society
    first = next(ship for ship in world.npcs.ships if ship["id"] == "syndicate_1")
    second = next(ship for ship in world.npcs.ships if ship["id"] == "jackal_sloop")
    first["node"] = second["node"] = "shoals"
    world.npcs.ships[1]["node"] = "navy_lane"
    world.npcs.ships[2]["node"] = "freeport"
    world.npcs.ships[3]["node"] = "cinder"
    society._set_relationship("cinder_free_company", "salt_jackals", 52)
    society._resolve_meetings(world, society.hour + 1)
    assert society.relationship("cinder_free_company", "salt_jackals") == 60
    # One more permitted encounter takes the relationship over the alliance mark.
    society._resolve_meetings(world, society.hour + 26)
    assert society.relationship("cinder_free_company", "salt_jackals") == 68
    assert society.news[-1]["kind"] == "alliance"
    assert "alliance" in society.news[-1]["headline"]


def test_existing_fleet_activity_generates_news_only_from_real_events():
    world = quiet_world()
    trader = next(ship for ship in world.npcs.ships if ship["id"] == "syndicate_1")
    trader["node"] = trader["destination"] = "cinder"
    trader["cargo"] = {"grain": 3}
    previous = list(world.npcs.events)
    # The old market loop is still the source of inventory/purse mutations.
    world.npcs._activity(world, trader)
    world.society._news_from_legacy(world, previous, world.society.hour + 1)
    assert world.society.news
    assert world.society.news[-1]["kind"] == "npc_market_event"
    assert "Free Syndicate cutter" in world.society.news[-1]["body"]
    assert not any("invented" in item["body"] for item in world.society.news)


def test_society_news_and_relations_roundtrip_and_old_v3_fleet_state_migrates(tmp_path):
    world = quiet_world()
    first, second = world.npcs.ships[0], world.npcs.ships[1]
    first["node"] = second["node"] = "navy_lane"
    first["motion"] = second["motion"] = None
    world.society._resolve_meetings(world, world.society.hour)
    path = tmp_path / "society.json"
    world.save(path)
    restored = World.load(path)
    assert restored.society.snapshot() == world.society.snapshot()
    assert restored.npcs.snapshot() == world.npcs.snapshot()

    data = json.loads(path.read_text(encoding="utf-8"))
    old_fleets = NpcFleetSystem()
    old_ships = []
    for ship in old_fleets.ships[:3]:
        ship = dict(ship)
        ship.pop("condition")
        old_ships.append(ship)
    data["npcs"] = {"hour": world.society.hour, "ships": old_ships,
                     "market": {"enslaved_people": 5, "silver": 500},
                     "events": [], "sighted": []}
    data.pop("society")
    path.write_text(json.dumps(data), encoding="utf-8")
    migrated = World.load(path)
    assert len(migrated.npcs.ships) == 5
    assert migrated.society.people["mara_vey"]["name"] == "Mara Vey"
    assert people_total(migrated) == 40


def test_invalid_society_relationship_or_news_reference_is_rejected(tmp_path):
    world = quiet_world()
    world.society._append_news(world.society.hour, "diplomacy", ["blackwake_fleet", "world_government"],
                               "shoals", "test cause", "test outcome", "Test headline", "Test report")
    path = tmp_path / "bad_society.json"
    world.save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["society"]["relationships"]["blackwake_fleet|world_government"] = 101
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="relationship score"):
        World.load(path)

    data["society"]["relationships"]["blackwake_fleet|world_government"] = -82
    data["society"]["news"][0]["participants"] = ["missing_person"]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="news history"):
        World.load(path)


def test_society_simulation_is_deterministic_across_hour_chunks():
    first, second = quiet_world(), quiet_world()
    first.advance(2 * 3600 * 100)
    second.advance(3600 * 100)
    second.advance(3600 * 100)
    assert first.npcs.snapshot() == second.npcs.snapshot()
    assert first.society.snapshot() == second.society.snapshot()


def test_sea_chart_and_news_menu_expose_named_captains_and_recent_headlines():
    from pirates.management import sea_map_rows
    world = quiet_world()
    world.society._append_news(world.society.hour, "rivalry", ["blackwake_fleet", "world_government"],
                               "navy_lane", "a patrol intercepted a raider", "both withdrew",
                               "Blackwake clashes with the Marines", "Mara Vey retreated from Ilyan Rusk.")
    rows = "\n".join(sea_map_rows(world))
    assert "Mara Vey" in rows and "Ilyan Rusk" in rows
    assert "Blackwake clashes with the Marines" in rows
