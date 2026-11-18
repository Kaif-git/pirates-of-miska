import json

import pytest

from pirates.game import World
from pirates.management import crew_menu, profile_rows
from pirates.ship import Job, LABORS


def test_sustained_hardship_builds_a_deterministic_warning_then_recovers():
    world = World()
    sailor = world.ship.crew[0]
    sailor.morale = 20
    sailor.stress = 80
    sailor.grievance = 69.99
    world.ship._update_grievance(world, sailor)
    assert sailor.grievance == pytest.approx(70.04)
    assert sailor.desertion_warning_hour == world.clock.total_ticks // 360000
    assert sum("considering leaving" in row for row in world.ship.events) == 1

    sailor.grievance = 34
    sailor.morale = 50
    sailor.stress = 20
    world.ship._update_grievance(world, sailor)
    assert sailor.desertion_warning_hour is None
    assert any("put aside thoughts" in row for row in world.ship.events)


def test_retention_bonus_is_a_limited_persistent_player_response(tmp_path):
    world = World()
    sailor = world.ship.crew[0]
    sailor.grievance = 82
    sailor.desertion_warning_hour = 0
    before = world.economy.money
    world.ship.offer_retention_bonus(world, sailor.id)
    assert world.economy.money == before - 25
    assert sailor.grievance == 0
    assert sailor.desertion_warning_hour is None
    assert sailor.retention_offer_day == world.clock.day
    assert any("retention bonus" in row.lower() for row in world.ship.events)
    with pytest.raises(ValueError, match="already been offered"):
        world.ship.offer_retention_bonus(world, sailor.id)

    save_path = tmp_path / "retention.json"
    world.save(save_path)
    restored = World.load(save_path).ship.crew[0]
    assert restored.retention_offer_day == sailor.retention_offer_day


def test_crew_menu_shows_warning_and_offers_the_retention_response():
    class Scripted:
        def __init__(self):
            self.answers = iter((0, 8, None, None))
            self.rows = []

        def render_list(self, _world, _title, rows, *_args, **_kwargs):
            self.rows.append(list(rows))

        def choose(self, _count):
            return next(self.answers)

        def wait_any(self):
            pass

    world = World()
    sailor = world.ship.crew[0]
    sailor.grievance = 75
    sailor.desertion_warning_hour = world.clock.total_ticks // 360000
    assert any("leaving warning" in row for row in profile_rows(sailor))
    starting_money = world.economy.money
    renderer = Scripted()
    crew_menu(renderer, world)
    assert world.economy.money == starting_money - 25
    assert any("Offer a 25-Mark retention bonus" in row for rows in renderer.rows for row in rows)


def test_ignored_day_long_warning_causes_exactly_once_desertion_and_conserves_identity(tmp_path):
    world = World()
    sailor = world.ship.crew[0]
    sailor.grievance = 95
    sailor.morale = 20
    sailor.stress = 85
    sailor.desertion_warning_hour = 0
    world.clock.advance(24 * 360000)
    result = world.ship._update_grievance(world, sailor)
    assert result is None
    assert world.ship.sailor("mara") is None
    assert world.ship.crew_history[-1] == {
        "id": "mara", "name": "Mara", "status": "deserted", "day": 2,
        "cause": "deserted after a day-long warning; grievance 95, morale 20, stress 85",
    }
    assert any("Mara is no longer aboard (deserted)" in message for message in world.log)
    path = tmp_path / "deserted.json"
    world.save(path)
    restored = World.load(path)
    assert restored.ship.crew_history[-1] == world.ship.crew_history[-1]
    assert restored.ship.sailor("mara") is None
    # Running the expired-warning branch again cannot duplicate the same person.
    restored.clock.advance(100)
    restored.ship._update_grievance(restored, restored.ship.crew[0])
    assert sum(row["id"] == "mara" for row in restored.ship.crew_history) == 1


def test_desertion_warning_fields_reject_forged_save_state(tmp_path):
    world = World()
    path = tmp_path / "warning.json"
    world.save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["ship"]["crew"][0]["grievance"] = 90
    data["ship"]["crew"][0]["desertion_warning_hour"] = 999
    invalid = tmp_path / "future-warning.json"
    invalid.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid ship integer"):
        World.load(invalid)


def test_retirement_is_aged_warned_at_port_and_recorded_once(tmp_path):
    world = World()
    world.player.x = 40
    sailor = world.ship.crew[0]
    sailor.age_origin = 64
    sailor.age_origin_day = world.clock.day - 365
    world.ship._update_age_and_retirement(world, sailor)
    assert sailor.age == 65
    assert sailor.retirement_notice_day == world.clock.day
    assert any("asked to retire" in row for row in world.ship.events)

    with pytest.raises(ValueError, match="has not asked"):
        world.ship.honor_retirement(world, sailor.id)
    world.clock.advance(30 * 24 * 360000)
    world.ship._update_age_and_retirement(world, sailor)
    assert world.ship.sailor(sailor.id) is None
    record = world.ship.crew_history[-1]
    assert record["id"] == sailor.id
    assert record["status"] == "retired"
    assert "30-day notice" in record["cause"]

    path = tmp_path / "retirement.json"
    world.save(path)
    restored = World.load(path)
    assert restored.ship.crew_history[-1] == record
    assert sum(row["id"] == sailor.id for row in restored.ship.crew_history) == 1


def test_aged_sailor_retirement_warning_survives_save_and_invalid_future_day_is_rejected(tmp_path):
    world = World()
    world.player.x = 40
    sailor = world.ship.crew[0]
    sailor.age_origin = 65
    sailor.age_origin_day = world.clock.day
    world.ship._update_age_and_retirement(world, sailor)
    path = tmp_path / "retirement-warning.json"
    world.save(path)
    restored_world = World.load(path)
    restored = restored_world.ship.sailor(sailor.id)
    assert restored.retirement_notice_day == world.clock.day
    assert restored.age == 65

    data = json.loads(path.read_text(encoding="utf-8"))
    data["ship"]["crew"][0]["retirement_notice_day"] = 999
    invalid = tmp_path / "future-retirement.json"
    invalid.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid ship integer"):
        World.load(invalid)


def test_captain_can_honor_retirement_after_notice_from_crew_menu():
    class Scripted:
        def __init__(self):
            self.answers = iter((0, len(LABORS) + 2, 0, None))
            self.rows = []

        def render_list(self, _world, _title, rows, *_args, **_kwargs):
            self.rows.append(list(rows))

        def choose(self, _count):
            return next(self.answers)

        def wait_any(self):
            pass

    world = World()
    world.player.x = 40
    sailor = world.ship.crew[0]
    sailor.age = 65
    sailor.age_origin = 65
    sailor.age_origin_day = world.clock.day
    sailor.retirement_notice_day = world.clock.day - 30
    assert any("Retirement requested" in row for row in profile_rows(sailor))
    renderer = Scripted()
    crew_menu(renderer, world)
    assert world.ship.sailor(sailor.id) is None
    assert world.ship.crew_history[-1]["status"] == "retired"
    assert "captain honored" in world.ship.crew_history[-1]["cause"]


def test_port_betrayal_warning_is_saved_but_retention_prevents_the_theft(tmp_path):
    world = World()
    world.player.x = 40
    sailor = world.ship.crew[0]
    sailor.grievance = 90
    sailor.morale = 15
    sailor.stress = 90
    world.ship._update_betrayal(world, sailor)
    warning_tick = sailor.betrayal_warning_tick
    assert warning_tick == world.clock.total_ticks
    assert any("suspect Mara" in row for row in world.ship.events)

    path = tmp_path / "betrayal-warning.json"
    world.save(path)
    restored_world = World.load(path)
    restored = restored_world.ship.sailor(sailor.id)
    assert restored.betrayal_warning_tick == warning_tick
    restored_world.ship.offer_retention_bonus(restored_world, restored.id)
    assert restored.betrayal_warning_tick is None
    restored_world.clock.advance(13 * 360000)
    restored_world.ship._update_betrayal(restored_world, restored)
    assert restored_world.ship.sailor(restored.id) is restored
    assert not any(row["status"] == "betrayed" for row in restored_world.ship.crew_history)


def test_unresolved_port_betrayal_steals_finite_marks_and_transitions_once(tmp_path):
    world = World()
    world.player.x = 40
    sailor = world.ship.crew[0]
    sailor.grievance = 95
    sailor.morale = 15
    sailor.stress = 90
    sailor.betrayal_warning_tick = world.clock.total_ticks - 12 * 360000
    starting_money = world.economy.money
    world.ship._update_betrayal(world, sailor)
    assert world.economy.money == starting_money - 50
    assert world.ship.sailor(sailor.id) is None
    record = world.ship.crew_history[-1]
    assert record["id"] == sailor.id
    assert record["status"] == "betrayed"
    assert "stealing 50 Marks" in record["cause"]
    assert any("stole 50 Marks" in row for row in world.ship.events)

    path = tmp_path / "betrayed.json"
    world.save(path)
    loaded = World.load(path)
    assert loaded.ship.crew_history[-1] == record
    assert loaded.ship.sailor(sailor.id) is None
    assert sum(row["id"] == sailor.id for row in loaded.ship.crew_history) == 1


def test_port_betrayal_warning_requires_real_opportunity_and_validates_deadline(tmp_path):
    world = World()
    sailor = world.ship.crew[0]
    sailor.grievance = 90
    sailor.morale = 15
    sailor.stress = 90
    world.ship._update_betrayal(world, sailor)
    assert sailor.betrayal_warning_tick is None  # not ashore at port
    world.player.x = 40
    world.ship._update_betrayal(world, sailor)
    assert sailor.betrayal_warning_tick == world.clock.total_ticks

    path = tmp_path / "bad-betrayal-warning.json"
    world.save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["ship"]["crew"][0]["betrayal_warning_tick"] = world.clock.total_ticks + 1
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid ship integer"):
        World.load(path)


def test_critical_injury_has_a_saved_rescue_window_then_death_is_exactly_once(tmp_path):
    world = World()
    sailor = world.ship.crew[0]
    sailor.health = 30
    sailor.suffer_injury("burn", 2, "uncontrolled fire on deck")
    assert sailor.health == 0
    world.ship._update_critical_condition(world, sailor)
    assert sailor.critical_since_tick == world.clock.total_ticks
    assert any("MEDICAL EMERGENCY" in row for row in world.ship.events)
    assert any("within 6 game hours" in row for row in world.log)

    path = tmp_path / "critical.json"
    world.save(path)
    loaded_world = World.load(path)
    loaded = loaded_world.ship.sailor(sailor.id)
    assert loaded.critical_since_tick == sailor.critical_since_tick
    assert loaded.critical_cause == sailor.critical_cause
    chest = loaded_world.fixtures.get("medicine_chest")
    infirmary_x, infirmary_y = chest.x + 69, chest.y + 12
    loaded_world.spaces.designate(loaded_world, infirmary_x, infirmary_y, "infirmary")
    loaded.x, loaded.y, loaded.z = infirmary_x, infirmary_y, 0
    doses = loaded_world.ship.stores["medicine"]
    for _ in range(20):
        loaded_world.ship.satisfy(loaded_world, loaded, "infirmary")
        if loaded.health > 0:
            break
    assert loaded.health > 0  # real designated care, not a direct health mutation
    assert loaded_world.ship.stores["medicine"] == doses - 1
    assert loaded.medical_visit_treated
    loaded_world.ship.satisfy(loaded_world, loaded, "infirmary")
    assert loaded_world.ship.stores["medicine"] == doses - 1  # save-safe visit uses only one dose
    treated_path = tmp_path / "treated-critical.json"
    loaded_world.save(treated_path)
    loaded_world = World.load(treated_path)
    loaded = loaded_world.ship.sailor(sailor.id)
    assert loaded.medical_visit_treated
    remaining_doses = loaded_world.ship.stores["medicine"]
    loaded_world.ship.satisfy(loaded_world, loaded, "infirmary")
    assert loaded_world.ship.stores["medicine"] == remaining_doses
    loaded_world.ship._update_critical_condition(loaded_world, loaded)
    assert loaded.critical_since_tick is None
    loaded.health = 0
    loaded_world.ship._update_critical_condition(loaded_world, loaded)
    loaded_world.clock.advance(6 * 360000)
    loaded_world.ship.step(loaded_world)
    assert loaded_world.ship.sailor(loaded.id) is None
    record = loaded_world.ship.crew_history[-1]
    assert record["status"] == "dead"
    assert "six game hours" in record["cause"]
    path = tmp_path / "dead.json"
    loaded_world.save(path)
    restored = World.load(path)
    assert restored.ship.sailor(loaded.id) is None
    assert restored.ship.crew_history[-1] == record
    assert sum(row["id"] == loaded.id for row in restored.ship.crew_history) == 1


def test_critical_infirmary_waits_for_a_finite_medicine_dose():
    world = World()
    sailor = world.ship.sailor("mara")
    sailor.health = 0
    world.ship._update_critical_condition(world, sailor)
    world.ship.stores["medicine"] = 0
    chest = world.fixtures.get("medicine_chest")
    x, y = chest.x + 69, chest.y + 12
    world.spaces.designate(world, x, y, "infirmary")
    sailor.x, sailor.y, sailor.z = x, y, 0
    for _ in range(20):
        world.ship.satisfy(world, sailor, "infirmary")
        if sailor.activity == "Waiting for medical supplies":
            break
    assert sailor.health == 0
    assert sailor.activity == "Waiting for medical supplies"


def test_death_cleanup_returns_collected_supplies_before_removing_sailor():
    world = World()
    sailor = world.ship.crew[0]
    pile = world.ship.storage.piles["hold"]
    pile.contents["grain_g"] -= 3000
    sailor.cargo = {"grain_g": 3000}
    sailor.job_id = 91
    job = Job(91, "repair:test", "repair", (77, 26), state="claimed", worker=sailor.id,
              phase="work", supply_plan=[{"pile": "hold", "bundle": {"grain_g": 3000},
                                          "collected": True, "returned": False}])
    world.ship.jobs.append(job)
    world.ship._depart_crew_member(world, sailor.id, "dead", "died in a test emergency")
    assert pile.contents["grain_g"] == 18000
    assert not sailor.cargo
    assert job.state == "cancelled"
    assert job.worker is None
    assert world.ship.crew_history[-1]["status"] == "dead"
    assert world.ship.sailor(sailor.id) is None


def test_pre_lifecycle_save_migrates_new_fields_without_retroactive_aging(tmp_path):
    world = World()
    path = tmp_path / "legacy-v3.json"
    world.save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    old_age = data["ship"]["crew"][0]["age"]
    data["ticks"] = 400 * 8640000
    new_fields = ("age_origin", "age_origin_day", "retirement_notice_day", "grievance",
                  "desertion_warning_hour", "betrayal_warning_tick", "retention_offer_day",
                  "critical_since_tick", "critical_cause")
    for row in data["ship"]["crew"]:
        for key in new_fields:
            row.pop(key, None)
    path.write_text(json.dumps(data), encoding="utf-8")

    restored_world = World.load(path)
    restored = restored_world.ship.crew[0]
    assert restored.age == old_age
    assert restored.age_origin == old_age
    assert restored.age_origin_day == restored_world.clock.day
    restored_world.ship._update_age_and_retirement(restored_world, restored)
    assert restored.age == old_age
    assert restored.retirement_notice_day is None
    assert restored.grievance == 0
    assert restored.critical_since_tick is None
