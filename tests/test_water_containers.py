import pytest

from pirates.game import World
from pirates.ship_physics import load_by_position


def test_container_fill_decant_mass_and_save_roundtrip(tmp_path):
    world = World()
    ship = world.ship
    before_mass = ship.stores["clean_water_ml"] + sum(
        row["tare_g"] + row["contents_ml"] for row in ship.containers)
    vessel = ship.add_container("waterskin", 1000, 250)
    before_water = ship.clean_water_total_ml()
    ship.fill_container(vessel["id"], 750)
    assert vessel["contents_ml"] == 750 and vessel["quality"] == "clean"
    assert ship.clean_water_total_ml() == before_water
    assert ship.decant_container(vessel["id"], 250)["contents_ml"] == 500
    assert ship.clean_water_total_ml() == before_water
    categories, _ = load_by_position(world)
    assert categories["containers"] == pytest.approx(0.75)
    after_mass = ship.stores["clean_water_ml"] + sum(
        row["tare_g"] + row["contents_ml"] for row in ship.containers)
    assert after_mass == before_mass + 250  # New empty waterskin tare.

    path = tmp_path / "water-containers.json"
    world.save(path)
    resumed = World.load(path)
    assert resumed.ship.containers == ship.containers
    assert resumed.ship.clean_water_total_ml() == ship.clean_water_total_ml()


def test_container_capacity_and_failed_decant_are_atomic():
    world = World()
    ship = world.ship
    vessel = ship.add_container("waterskin", 1000, 250)
    with pytest.raises(ValueError):
        ship.fill_container(vessel["id"], 1001)
    ship.fill_container(vessel["id"], 1000)
    # Fill the water stockpile to its physical limit so a return decant cannot fit.
    ship.stores["clean_water_ml"] = 250000
    contents_before = vessel["contents_ml"]
    with pytest.raises(ValueError):
        ship.decant_container(vessel["id"], 250)
    assert vessel["contents_ml"] == contents_before
    assert vessel["quality"] == "clean"


def test_personal_container_water_is_owner_limited():
    world = World()
    ship = world.ship
    owner, other = ship.crew[:2]
    vessel = ship.add_container("waterskin", 1000, 250, owner=owner.id)
    vessel["contents_ml"] = 500
    vessel["quality"] = "clean"
    ship.stores["clean_water_ml"] = 0
    assert not ship.consume_clean_water(other, 250)
    assert vessel["contents_ml"] == 500
    assert ship.consume_clean_water(owner, 250)
    assert vessel["contents_ml"] == 250


def test_rain_collection_is_weather_gated_capped_and_chunk_deterministic():
    first = World()
    second = World()
    for world in (first, second):
        for sailor in world.ship.crew:
            sailor.hunger = sailor.thirst = sailor.fatigue = sailor.loneliness = sailor.boredom = 0
            sailor.sleep_debt = 0
        ship = world.ship
        ship.seconds = 59
        day = world.clock.day
        rain_hour = next(hour for hour in range(24) if ship.is_raining(day, hour))
        world.clock.total_ticks = rain_hour * 60 * 60 * 100
    first.advance(100 * 60 * 10)
    for _ in range(10):
        second.advance(100 * 60)
    assert first.ship.stores["questionable_water_ml"] == second.ship.stores["questionable_water_ml"]
    assert 0 < first.ship.rain_collected_today_ml <= 20
    assert first.ship.stores["clean_water_ml"] == 60000


def test_rainwater_boiling_uses_finite_fuel_and_records_evaporation():
    world = World()
    ship = world.ship
    sailor = ship.crew[0]
    ship.stores["questionable_water_ml"] = 1000
    ship.stores["firewood_g"] = 250
    job = ship.queue_water_treatment()
    assert ship.job_cost(job) == {"questionable_water_ml": 1000, "firewood_g": 250}
    ship.stores["questionable_water_ml"] -= 1000
    ship.stores["firewood_g"] -= 250
    clean_before = ship.stores["clean_water_ml"]
    ship.finish(job, sailor, world)
    assert ship.stores["clean_water_ml"] == clean_before + 900
    assert ship.stores["questionable_water_ml"] == 0
    assert ship.stores["firewood_g"] == 0
    assert any(row["event"] == "water_boiled" for row in ship.audit)


def test_treatment_rejects_seawater_and_ordinary_drinking_ignores_questionable_water():
    world = World()
    ship = world.ship
    ship.stores["questionable_water_ml"] = 2000
    ship.stores["clean_water_ml"] = 0
    sailor = ship.crew[0]
    sailor.thirst = 95
    assert not ship.consume_clean_water(sailor, 250)
    assert ship.stores["questionable_water_ml"] == 2000
    assert ship.stores["clean_water_ml"] == 0


def test_new_water_quality_and_fuel_state_persist_without_counting_fuel_as_food(tmp_path):
    world = World()
    world.ship.stores["questionable_water_ml"] = 1234
    world.ship.stores["firewood_g"] = 3000
    assert world.ship.food_stock_g() == 30000
    path = tmp_path / "water-quality.json"
    world.save(path)
    resumed = World.load(path)
    assert resumed.ship.stores["questionable_water_ml"] == 1234
    assert resumed.ship.stores["firewood_g"] == 3000


def test_water_treatment_job_completes_normally_and_records_its_real_cost():
    world = World()
    ship = world.ship
    sailor = ship.crew[0]
    ship.stores["questionable_water_ml"] = 1000
    ship.stores["firewood_g"] = 250
    job = ship.queue_water_treatment()
    sailor.cargo = ship.job_cost(job)
    ship.stores["questionable_water_ml"] -= 1000
    ship.stores["firewood_g"] -= 250
    before_completed = sailor.completed
    ship.finish(job, sailor, world)
    assert sailor.cargo == {}
    assert sailor.completed == before_completed + 1
    assert any(event["event"] == "food_cooked" for event in ship.audit) is False
    assert any(event["event"] == "water_boiled" for event in ship.audit)
    assert any("Boiled rainwater" in event for event in ship.events)
