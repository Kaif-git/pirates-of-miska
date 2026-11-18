import json

import pytest

from pirates.game import World
from pirates.management import crew_roster_rows, hardship_concern_rows, profile_rows
from pirates.ship import LABORS


def quiet_world():
    world = World()
    for sailor in world.ship.crew:
        sailor.labors = {key: 0 for key in LABORS}
        sailor.job_id = None
        sailor.intent = ""
        sailor.hunger = sailor.thirst = sailor.fatigue = sailor.loneliness = sailor.boredom = 0
        sailor.sleep_debt = 0
    return world


def two_mess_positions(world):
    mess = world.ship.stations["mess"]
    positions = []
    for dx in range(-2, 3):
        for dy in range(-2, 3):
            if abs(dx) + abs(dy) > 2:
                continue
            pos = (mess[0] + dx, mess[1] + dy)
            if world.game_map.is_walkable(*pos) and pos != (world.player.x, world.player.y):
                sailor = world.ship.crew[0]
                sailor.x, sailor.y = pos
                if world.ship.path(world, sailor, mess) == []:
                    positions.append(pos)
    assert len(positions) >= 2
    for first in positions:
        for second in positions:
            if first != second and abs(first[0] - second[0]) + abs(first[1] - second[1]) <= 2:
                return first, second
    raise AssertionError("No nearby walkable mess positions")


def test_actual_shared_meal_records_one_non_reward_evidence_event():
    world = quiet_world()
    ship = world.ship
    a, b = ship.crew[:2]
    (a.x, a.y), (b.x, b.y) = two_mess_positions(world)
    a.intent = b.intent = "eat"
    a.shared_meal_slot = b.shared_meal_slot = 91001

    assert ship.record_shared_meal_attendance(a, world)
    assert not ship.record_shared_meal_attendance(b, world)
    assert len(ship.social_events) == 1
    event = ship.social_events[0]
    assert event["kind"] == "shared_meal"
    assert event["participants"] == sorted((a.id, b.id))
    assert event["relationship_delta"] == 0
    assert "reached the mess" in event["reason"]
    assert a.relationships.get(b.id, 50) == b.relationships.get(a.id, 50) == 50


def test_completed_nearby_story_records_existing_relationship_change_once():
    world = quiet_world()
    ship = world.ship
    a, b = ship.crew[:2]
    (a.x, a.y), (b.x, b.y) = two_mess_positions(world)
    a.intent = "social"
    a.action_seconds = 15
    before_a = a.relationships.get(b.id, 0)
    before_b = b.relationships.get(a.id, 0)

    ship.satisfy(world, a, "social")

    assert a.relationships[b.id] == before_a + 1
    assert b.relationships[a.id] == before_b + 1
    assert len(ship.social_events) == 1
    event = ship.social_events[0]
    assert event["kind"] == "shared_story"
    assert event["relationship_delta"] == 1
    assert "15-second" in event["reason"]
    assert sum("RECENT SHARED-ACTIVITY EVIDENCE" in row for row in profile_rows(a, world)) == 1


def test_story_relationship_gain_is_capped_per_pair_per_day_and_resumes_after_load(tmp_path):
    world = quiet_world()
    ship = world.ship
    a, b = ship.crew[:2]
    (a.x, a.y), (b.x, b.y) = two_mess_positions(world)
    before_a = a.relationships.get(b.id, 0)
    before_b = b.relationships.get(a.id, 0)
    for _ in range(3):
        a.intent, a.action_seconds = "social", 15
        ship.satisfy(world, a, "social")
    assert a.relationships[b.id] == before_a + 2
    assert b.relationships[a.id] == before_b + 2
    assert [row["relationship_delta"] for row in ship.social_events] == [1, 1, 0]
    assert "ceiling reached" in ship.social_events[-1]["reason"]

    path = tmp_path / "story-cap.json"
    world.save(path)
    loaded = World.load(path)
    assert loaded.ship.relationship_story_gains == ship.relationship_story_gains
    la, lb = loaded.ship.sailor(a.id), loaded.ship.sailor(b.id)
    la.x, la.y = a.x, a.y
    lb.x, lb.y = b.x, b.y
    la.intent, la.action_seconds = "social", 15
    loaded.ship.satisfy(loaded, la, "social")
    assert la.relationships[lb.id] == a.relationships[b.id]
    assert loaded.ship.social_events[-1]["relationship_delta"] == 0

    loaded.clock.advance(24 * 60 * 60 * 100)
    la.intent, la.action_seconds = "social", 15
    loaded.ship.satisfy(loaded, la, "social")
    assert loaded.ship.social_events[-1]["relationship_delta"] == 1
    assert la.relationships[lb.id] == a.relationships[b.id] + 1


def test_daily_relationship_cap_clears_at_day_boundary_before_next_story_save(tmp_path):
    world = quiet_world()
    a, b = world.ship.crew[:2]
    (a.x, a.y), (b.x, b.y) = two_mess_positions(world)
    a.intent, a.action_seconds = "social", 15
    world.ship.satisfy(world, a, "social")
    assert world.ship.relationship_story_gains
    world.advance(24 * 60 * 60 * 100 - world.clock.second_of_day)
    assert world.ship.relationship_story_day == world.clock.day
    assert world.ship.relationship_story_gains == {}
    path = tmp_path / "new-day-cap.json"
    world.save(path)
    loaded = World.load(path)
    assert loaded.ship.relationship_story_day == loaded.clock.day
    assert loaded.ship.relationship_story_gains == {}


def test_social_evidence_survives_save_load_and_legacy_saves_start_empty(tmp_path):
    world = quiet_world()
    a, b = world.ship.crew[:2]
    world.ship.record_social_event("shared_story", (a, b), "A completed nearby story.", 1)
    path = tmp_path / "social-events.json"
    world.save(path)
    loaded = World.load(path)
    assert loaded.ship.social_events == world.ship.social_events
    assert loaded.ship.next_social_event_id == world.ship.next_social_event_id

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["ship"].pop("social_events")
    payload["ship"].pop("next_social_event_id")
    payload["ship"].pop("relationship_story_day")
    payload["ship"].pop("relationship_story_gains")
    payload["ship"].pop("crew_concerns")
    payload["ship"].pop("next_crew_concern_id")
    path.write_text(json.dumps(payload), encoding="utf-8")
    legacy = World.load(path)
    assert legacy.ship.social_events == []
    assert legacy.ship.next_social_event_id == 1
    assert legacy.ship.crew_concerns == []
    assert legacy.ship.next_crew_concern_id == 1


@pytest.mark.parametrize("corrupt", [
    lambda event: event.update(second=10**12),
    lambda event: event.update(kind="invented_meeting"),
    lambda event: event.update(participants=["mara", "mara"]),
    lambda event: event.update(relationship_delta=1),
])
def test_invalid_social_evidence_is_rejected_on_load(tmp_path, corrupt):
    world = quiet_world()
    a, b = world.ship.crew[:2]
    world.ship.record_social_event("shared_meal", (a, b), "Actual shared meal.")
    path = tmp_path / "invalid-social-events.json"
    world.save(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    corrupt(payload["ship"]["social_events"][0])
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="social-event"):
        World.load(path)


def test_social_evidence_is_bounded_and_does_not_mutate_when_read():
    world = quiet_world()
    ship = world.ship
    a, b = ship.crew[:2]
    for i in range(80):
        ship.record_social_event("shared_story", (a, b), f"Completed story {i}.", 1)
    before = (ship.seconds, world.clock.total_ticks, list(ship.social_events), ship.next_social_event_id)
    profile_rows(a, world)
    assert len(ship.social_events) == 64
    assert ship.social_events[0]["id"] == 17
    assert before == (ship.seconds, world.clock.total_ticks, ship.social_events, ship.next_social_event_id)


def test_repeated_companionship_requires_three_recorded_shared_occasions():
    world = quiet_world()
    ship = world.ship
    a, b = ship.crew[:2]
    a.relationships[b.id] = 1
    rows = profile_rows(a, world)
    assert not any("REPEATED COMPANIONSHIP" in row for row in rows)
    ship.record_social_event("shared_meal", (a, b), "Same meal slot, actual co-attendance.")
    ship.record_social_event("shared_story", (a, b), "Completed a nearby conversation.", 1)
    rows = profile_rows(a, world)
    assert not any("REPEATED COMPANIONSHIP" in row for row in rows)
    ship.record_social_event("shared_meal", (a, b), "Same meal slot, actual co-attendance.")
    rows = profile_rows(a, world)
    assert any("REPEATED COMPANIONSHIP" in row for row in rows)
    assert any("Ivo: 3 occasions; existing relationship 1/100" in row for row in rows)


def test_hardship_concern_reports_only_current_evidence_and_existing_recovery_rules():
    world = quiet_world()
    sailor = world.ship.crew[0]
    assert hardship_concern_rows(sailor) == []
    sailor.grievance = 68
    sailor.morale = 25
    sailor.stress = 70
    sailor.hunger = 78
    before = (sailor.grievance, sailor.morale, sailor.stress, sailor.hunger,
              world.clock.total_ticks, world.ship.seconds)
    world.ship._update_crew_concern(world, sailor, True)
    concern = world.ship.crew_concerns[0]
    assert any("Mara" in row and "CONCERN" in row for row in crew_roster_rows(world))
    rows = hardship_concern_rows(sailor, concern)
    assert any("stress 70" in row and "hunger 78" in row for row in rows)
    assert any("meet the existing grievance progression rule" in row for row in rows)
    assert any("review labor priorities" in row for row in rows)
    assert before == (sailor.grievance, sailor.morale, sailor.stress, sailor.hunger,
                      world.clock.total_ticks, world.ship.seconds)

    sailor.morale = 50
    sailor.stress = 20
    sailor.hunger = sailor.thirst = 20
    rows = hardship_concern_rows(sailor, concern)
    assert any("do not meet every progression requirement" in row for row in rows)
    assert any("It eases only under existing conditions" in row for row in rows)
    assert not any("watch" in row.lower() or "unequal burden" in row.lower() for row in rows)
    sailor.grievance = 34
    world.ship._update_crew_concern(world, sailor, False)
    assert concern["status"] == "resolved"
    assert not any("CONCERN" in row for row in crew_roster_rows(world))
    assert any("resolved at ship hour" in row for row in hardship_concern_rows(sailor, concern))


def test_grievance_threshold_opens_one_concern_and_real_recovery_resolves_it(tmp_path):
    world = quiet_world()
    sailor = world.ship.crew[0]
    sailor.grievance = 59.99
    sailor.morale = 25
    sailor.stress = 70
    world.ship._update_grievance(world, sailor)
    assert sailor.grievance >= 60
    assert len(world.ship.crew_concerns) == 1
    assert world.ship.crew_concerns[0]["signals"] == ["stress"]
    snapshot = (world.ship.seconds, world.clock.total_ticks, sailor.grievance, sailor.morale,
                list(world.ship.crew_concerns), world.economy.money)
    concern_rows = profile_rows(sailor, world)
    assert any("read-only" in row for row in concern_rows)
    assert snapshot == (world.ship.seconds, world.clock.total_ticks, sailor.grievance, sailor.morale,
                       world.ship.crew_concerns, world.economy.money)
    world.ship._update_grievance(world, sailor)
    assert len(world.ship.crew_concerns) == 1
    world.ship.set_labor(sailor.id, "clean", 5)
    assert world.ship.crew_concerns[0]["status"] == "open"

    path = tmp_path / "open-concern.json"
    world.save(path)
    resumed = World.load(path)
    assert resumed.ship.crew_concerns == world.ship.crew_concerns
    sailor.grievance = 35.01
    sailor.morale = 50
    sailor.stress = 20
    sailor.hunger = sailor.thirst = 20
    resumed_sailor = resumed.ship.sailor(sailor.id)
    resumed_sailor.grievance = sailor.grievance
    resumed_sailor.morale = sailor.morale
    resumed_sailor.stress = sailor.stress
    resumed_sailor.hunger = resumed_sailor.thirst = 20
    world.ship._update_grievance(world, sailor)
    resumed.ship._update_grievance(resumed, resumed_sailor)
    assert world.ship.crew_concerns[0]["status"] == "resolved"
    assert world.ship.snapshot() == resumed.ship.snapshot()


def test_departed_crew_concern_is_archived_and_survives_load(tmp_path):
    world = quiet_world()
    sailor = next(row for row in world.ship.crew if row.job_id is None)
    sailor.grievance = 60
    sailor.morale = 25
    sailor.stress = 70
    world.ship._update_crew_concern(world, sailor, True)
    concern = world.ship.crew_concerns[0]
    world.ship._depart_crew_member(world, sailor.id, "released", "test departure")
    assert concern["status"] == "archived"
    assert world.ship.sailor(sailor.id) is None
    assert not any("CREW CONCERN" in row for row in profile_rows(world.ship.crew[0], world))
    path = tmp_path / "archived-concern.json"
    world.save(path)
    loaded = World.load(path)
    assert loaded.ship.crew_concerns[0] == concern
