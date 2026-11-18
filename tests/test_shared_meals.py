import json
import pytest

from pirates.game import World
from pirates.ship import LABORS


def quiet_world():
    world = World()
    ship = world.ship
    for sailor in ship.crew:
        sailor.labors = {key: 0 for key in LABORS}
        sailor.job_id = None
        sailor.intent = ""
        sailor.hunger = sailor.thirst = sailor.fatigue = sailor.loneliness = sailor.boredom = 0
        sailor.sleep_debt = 0
        sailor.energy = 100
    return world


def first_meal_window(world):
    world.clock.total_ticks += 3600 * 100
    world.society.hour = world.clock.hour_minute[0]


def test_staggered_meal_windows_call_only_two_off_duty_sailors_at_a_time():
    world = quiet_world()
    first_meal_window(world)
    world.advance(100)
    sailors = world.ship.crew
    assert [s.shared_meal_slot >= 0 for s in sailors] == [True, True, False, False, False, False]
    assert len({s.shared_meal_slot for s in sailors[:2]}) == 1
    assert [s.intent for s in sailors[:2]] == ["eat", "eat"]


def test_scheduled_meal_never_interrupts_an_assigned_duty():
    world = quiet_world()
    first_meal_window(world)
    sailor = world.ship.crew[0]
    job = next(job for job in world.ship.jobs if job.kind == "repair")
    job.state, job.worker = "assigned", sailor.id
    sailor.job_id = job.id
    world.advance(100)
    assert sailor.job_id == job.id
    assert sailor.shared_meal_slot == -1
    assert sailor.intent == ""
    assert world.ship.crew[1].shared_meal_slot >= 0
    assert world.ship.crew[1].intent == "eat"


def test_critical_thirst_interrupts_a_scheduled_meal_without_spending_food():
    world = quiet_world()
    first_meal_window(world)
    sailor = world.ship.crew[0]
    sailor.thirst = 85
    food_before = world.ship.edible_food_g()
    food_counter_before = sailor.food_consumed_today_g
    world.advance(100)
    assert world.ship.edible_food_g() == food_before
    assert sailor.last_shared_meal_slot > 0
    assert sailor.shared_meal_slot == -1
    assert sailor.intent == "drink"
    assert sailor.food_consumed_today_g == food_counter_before


def test_meal_schedule_and_in_progress_attendance_survive_save_and_chunking(tmp_path):
    whole, chunked = quiet_world(), quiet_world()
    first_meal_window(whole)
    first_meal_window(chunked)
    whole.advance(100)
    chunked.advance(100)
    path = tmp_path / "shared-meal.json"
    whole.save(path)
    resumed = World.load(path)
    assert resumed.ship.meal_window_config == whole.ship.meal_window_config
    assert [s.shared_meal_slot for s in resumed.ship.crew] == [s.shared_meal_slot for s in whole.ship.crew]
    whole.advance(10000)
    for _ in range(100):
        chunked.advance(100)
    assert whole.ship.snapshot() == chunked.ship.snapshot()
    assert resumed.ship.shared_meal_social_slots == []


def test_shared_attendance_effect_requires_real_co_presence_and_meals_stay_finite():
    world = quiet_world()
    ship = world.ship
    a, b = ship.crew[:2]
    mess = ship.stations["mess"]
    spots = []
    for dx in range(-2, 3):
        for dy in range(-2, 3):
            at = (mess[0] + dx, mess[1] + dy)
            if abs(dx) + abs(dy) > 2 or not world.game_map.is_walkable(*at):
                continue
            a.x, a.y = at
            if ship.path(world, a, mess) == []:
                spots.append(at)
    assert len(spots) >= 2
    (a.x, a.y), (b.x, b.y) = spots[:2]
    a.intent = b.intent = "eat"
    a.shared_meal_slot = b.shared_meal_slot = 81000
    a.action_seconds = b.action_seconds = 5
    before_food = ship.edible_food_g()
    ship.satisfy(world, a, "eat")
    assert ship.edible_food_g() == before_food - 250
    ship.consume_food_details(250)
    assert ship.edible_food_g() == before_food - 500
    assert a.morale > 65 and b.morale > 65
    assert sum(row["event"] == "shared_meal" for row in ship.audit) == 1


def test_shared_social_memory_requires_partner_co_presence():
    world = quiet_world()
    sailor = world.ship.crew[0]
    sailor.shared_meal_slot = 12345
    before = sailor.morale
    assert not world.ship.record_shared_meal_attendance(sailor, world)
    assert sailor.morale == before
    assert world.ship.shared_meal_social_slots == []


def test_unreachable_mess_skips_scheduled_meal_without_claiming_attendance(monkeypatch):
    world = quiet_world()
    first_meal_window(world)
    monkeypatch.setattr(world.ship.layout, "available", lambda key: key != "mess")
    sailor = world.ship.crew[0]
    world.advance(100)
    assert sailor.last_shared_meal_slot == -1
    assert sailor.shared_meal_slot == -1
    assert sailor.intent == ""


def test_shortage_at_scheduled_meal_neither_creates_food_nor_social_bonus():
    world = quiet_world()
    ship = world.ship
    sailor = ship.crew[0]
    ship.cooked_food_batches = []
    ship.stores.update({key: 0 for key in ship.stores if key in
                        ("biscuit_g", "fruit_g", "salted_fruit_g", "hard_cheese_g", "dried_fish_g")})
    mess = ship.stations["mess"]
    for dx in range(-2, 3):
        for dy in range(-2, 3):
            at = (mess[0] + dx, mess[1] + dy)
            if abs(dx) + abs(dy) <= 2 and world.game_map.is_walkable(*at):
                sailor.x, sailor.y = at
                if ship.path(world, sailor, mess) == []:
                    break
        else:
            continue
        break
    sailor.intent = "eat"
    sailor.shared_meal_slot = 98765
    sailor.action_seconds = 5
    before = ship.edible_food_g()
    ship.satisfy(world, sailor, "eat")
    assert ship.edible_food_g() == before == 0
    assert ship.shared_meal_social_slots == []
    assert any("No ready-to-eat food" in thought for thought in sailor.thoughts)


def test_shared_meal_window_migration_and_validation(tmp_path):
    world = quiet_world()
    path = tmp_path / "meal-config.json"
    world.save(path)
    resumed = World.load(path)
    assert resumed.ship.meal_window_config["hours"] == [9, 15, 21]
    legacy = json.loads(path.read_text())
    legacy["ship"].pop("meal_windows")
    for sailor in legacy["ship"]["crew"]:
        sailor.pop("last_shared_meal_slot")
        sailor.pop("shared_meal_slot")
    path.write_text(json.dumps(legacy))
    migrated = World.load(path)
    assert migrated.ship.crew[0].last_shared_meal_slot == -1
    assert migrated.ship.meal_window_config["hours"] == [9, 15, 21]
    with pytest.raises(ValueError, match="shared-meal"):
        world.ship.meal_window_config["group_size"] = 1
        world.save(path)
        World.load(path)
