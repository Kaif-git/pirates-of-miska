import json

import pytest

from pirates.game import World
from pirates.ship_storage import (HAUL_CAPACITY_KG, RESOURCES, STOCK_FILTER_PROFILES,
                                  bundle_mass, queue_transfer)
from pirates.management import storage_menu


def storage_world():
    world = World()
    world.ship.jobs = []
    world.ship.hull = world.ship.rigging = 100
    world.ship.bilge = 0
    world.ship.meals = 20
    world.ship.ammo = {k: 1 for k in world.ship.ammo}
    world.ship.dirt = {k: 0 for k in world.ship.dirt}
    for c in world.ship.crew:
        c.labors = {k: 0 for k in c.labors}
        for key in ("hunger", "thirst", "fatigue", "sleep_debt", "stress", "loneliness", "boredom"):
            setattr(c, key, 0)
        c.energy = 100
        c.intent = ""
    world.ship.crew[0].labors["repair"] = 1
    pile = world.ship.storage.designate(world.ship_model, world.ship_fixtures, 78, 27)
    return world, world.ship.crew[0], pile


def until(world, predicate, limit=600):
    for _ in range(limit):
        if predicate():
            return
        world.advance(100)
    assert predicate(), [(j.state, j.reason, j.phase) for j in world.ship.jobs]


def test_stockpile_transfer_physically_collects_and_delivers():
    world, sailor, destination = storage_world()
    source = world.ship.storage.piles["hold"]
    before = source.contents["planks"]
    job = queue_transfer(world, "hold", destination.id, {"planks": 1})
    assert source.contents["planks"] == before
    until(world, lambda: bool(sailor.cargo))
    assert sailor.cargo == {"planks": 1}
    assert source.contents["planks"] == before - 1
    assert destination.contents == {}
    until(world, lambda: job.state == "done")
    assert destination.contents == {"planks": 1}
    assert world.ship.stores["planks"] == before
    assert not sailor.cargo
    assert world.ship.check_orders() == []


def test_split_job_supply_sources_are_reserved_and_walked():
    world, sailor, destination = storage_world()
    storage = world.ship.storage
    storage.piles["hold"].contents["grain_g"] = 1000
    source2 = storage.designate(world.ship_model, world.ship_fixtures, 79, 27, 500)
    source2.contents["grain_g"] = 1000
    job = world.ship.add_job("cook:grain_porridge", "cook", world.ship.stations["galley"], work=1)
    job.reserved = world.ship.job_cost(job)
    sailor.labors["cook"] = 1
    allocations = storage.plan(world, sailor, job.reserved)
    assert allocations and len(allocations) == 3
    storage.reserve(allocations)
    job.supply_plan = allocations
    job.state, job.worker, sailor.job_id = "claimed", sailor.id, job.id
    job.phase = "collect"
    for _ in range(200):
        world.advance(100)
        if job.phase == "work":
            break
    assert job.phase == "work"
    assert sailor.cargo == {"grain_g": 2000, "clean_water_ml": 1000}
    assert sum(p.contents.get("grain_g", 0) for p in storage.piles.values()) == 0
    assert world.ship.check_orders() == []


def test_transfer_midhaul_save_resume_and_cancellation_returns_source(tmp_path):
    world, sailor, destination = storage_world()
    source = world.ship.storage.piles["hold"]
    original = source.contents.copy()
    job = queue_transfer(world, "hold", destination.id, {"shot": 3})
    until(world, lambda: bool(sailor.cargo))
    path = tmp_path / "ship.json"
    world.save(path)
    resumed = World.load(path)
    until(resumed, lambda: resumed.ship.job(job.id).state == "done")
    assert resumed.ship.storage.piles[destination.id].contents == {"shot": 3}
    cancelled = World.load(path)
    cancelled.ship.cancel(job.id)
    until(cancelled, lambda: cancelled.ship.job(job.id).state == "cancelled")
    assert cancelled.ship.storage.piles["hold"].contents == original
    assert cancelled.ship.storage.piles[destination.id].contents == {}
    assert cancelled.ship.check_orders() == []


def test_upper_deck_stockpile_transfer_routes_by_z_and_survives_save(tmp_path):
    world, sailor, _ = storage_world()
    x, y = 5, 5
    world.ship_model.place(x, y, 1, "floor")
    world.ship_model.place(x, y, 0, "stair")
    world.ship_model.place(x + 1, y, 1, "floor")
    pile = world.ship.storage.designate(world.ship_model, world.ship_fixtures,
                                        x + 1 + 69, y + 12, z=1)
    assert pile.id == "pile:6,5,1"
    assert world.ship.storage.position(pile.id, world.fixtures) == (x + 1 + 69, y + 12, 1)
    job = queue_transfer(world, "hold", pile.id, {"planks": 1})
    until(world, lambda: bool(sailor.cargo))
    path = tmp_path / "upper-pile.json"
    world.save(path)
    restored = World.load(path)
    until(restored, lambda: restored.ship.job(job.id).state == "done")
    upper = restored.ship.storage.piles[pile.id]
    assert upper.contents == {"planks": 1}
    assert upper.z == 1
    assert restored.ship.storage.piles["hold"].contents["planks"] == 15
    assert restored.ship.check_orders() == []


def test_spatial_job_reservations_prevent_duplicate_claims():
    world, _, destination = storage_world()
    a = world.ship.add_job("test:a", "repair", world.ship.stations["repair"])
    b = world.ship.add_job("test:b", "repair", world.ship.stations["repair"])
    sailor = world.ship.crew[0]
    aa = world.ship.storage.plan(world, sailor, {"planks": 1})
    world.ship.storage.reserve(aa)
    a.reserved, a.supply_plan = {"planks": 1}, aa
    a.state, a.worker, sailor.job_id = "claimed", sailor.id, a.id
    bb = world.ship.storage.plan(world, world.ship.crew[1], {"planks": 16})
    assert bb is None
    aa = world.ship.storage.plan(world, sailor, {"grain_g": 18000})
    assert aa is not None
    world.ship.storage.reserve(aa)
    a.supply_plan = aa
    a.reserved = {"planks": 1, "grain_g": 18000}
    with pytest.raises(ValueError, match="unreserved"):
        queue_transfer(world, "hold", destination.id, {"grain_g": 18000})


def test_transfer_capacity_and_weight_limits():
    world, _, destination = storage_world()
    destination.capacity_kg = 10
    with pytest.raises(ValueError, match="capacity"):
        queue_transfer(world, "hold", destination.id, {"shot": 3})
    destination.capacity_kg = 100
    with pytest.raises(ValueError, match="limited"):
        queue_transfer(world, "hold", destination.id, {"planks": 3})
    assert HAUL_CAPACITY_KG == 50
    assert bundle_mass({"shot": 2}) == 10


def test_transfer_source_reservations_reduce_the_quantity_available_for_new_orders():
    world, _, destination = storage_world()
    storage = world.ship.storage
    source = storage.piles["hold"]
    source.contents["shot"] = 10
    available_before = storage.available_quantity("hold", "shot", world.ship.jobs)
    first = queue_transfer(world, "hold", destination.id, {"shot": 1})
    assert storage.available_quantity("hold", "shot", world.ship.jobs) == available_before - 1
    with pytest.raises(ValueError, match="unreserved"):
        queue_transfer(world, "hold", destination.id, {"shot": available_before})
    assert first.state == "pending"
    assert source.contents["shot"] == available_before


def test_transfer_reserves_room_for_a_cancelled_job_to_return_its_carried_stock():
    world, sailor, source = storage_world()
    job = world.ship.add_job("test:return", "repair", world.ship.stations["repair"], work=100)
    until(world, lambda: bool(sailor.cargo))
    assert sailor.cargo == {"planks": 1}
    world.ship.cancel(job.id)
    assert job.state == "returning"

    hold = world.ship.storage.piles["hold"]
    hold.capacity_kg = bundle_mass(hold.contents) + 20
    source.contents["shot"] = 1
    with pytest.raises(ValueError, match="capacity"):
        queue_transfer(world, source.id, "hold", {"shot": 1})
    assert not any(j.transfer and j.transfer["destination"] == "hold"
                   for j in world.ship.jobs)
    assert world.ship.check_orders() == []


def test_legacy_overbooked_return_waits_without_losing_carried_stock():
    world, sailor, _ = storage_world()
    original = world.ship.storage.piles["hold"].contents.copy()
    job = world.ship.add_job("test:return", "repair", world.ship.stations["repair"], work=100)
    until(world, lambda: bool(sailor.cargo))
    world.ship.cancel(job.id)
    hold = world.ship.storage.piles["hold"]
    hold.capacity_kg = bundle_mass(hold.contents) + 19
    world.advance(100)
    assert job.state == "returning"
    assert sailor.cargo == {"planks": 1}
    available = original.copy()
    available["planks"] -= 1
    assert hold.contents == available
    assert "return capacity" in job.reason

    hold.capacity_kg += 1
    until(world, lambda: job.state == "cancelled")
    assert hold.contents == original
    assert not sailor.cargo


def test_filters_priorities_and_policy_survive_save_and_load(tmp_path):
    world, sailor, pile = storage_world()
    storage = world.ship.storage
    storage.configure(pile.id, filters=STOCK_FILTER_PROFILES["Construction materials"],
                      priority=1, jobs=world.ship.jobs)
    path = tmp_path / "filtered-stock.json"
    world.save(path)
    restored = World.load(path)
    restored_pile = restored.ship.storage.piles[pile.id]
    assert restored_pile.filters == ["planks"]
    assert restored_pile.priority == 1
    with pytest.raises(ValueError, match="filter rejects"):
        queue_transfer(restored, "hold", pile.id, {"shot": 1})
    job = queue_transfer(restored, "hold", pile.id, {"planks": 1})
    path = tmp_path / "filtered-transfer.json"
    restored.save(path)
    restored = World.load(path)
    job = restored.ship.job(job.id)
    with pytest.raises(ValueError, match="active transfer"):
        restored.ship.storage.configure(pile.id, filters=["shot"], jobs=restored.ship.jobs)
    assert job.state == "pending"
    until(restored, lambda: job.state == "done")
    assert restored.ship.storage.piles[pile.id].contents == {"planks": 1}


def test_high_priority_reachable_pile_is_used_before_a_nearer_lower_priority_pile():
    world, sailor, pile = storage_world()
    pile.contents["planks"] = 1
    hold = world.ship.storage.piles["hold"]
    hold.contents["planks"] = 1
    world.ship.storage.configure(pile.id, priority=5, jobs=world.ship.jobs)
    world.ship.storage.configure("hold", priority=1, jobs=world.ship.jobs)
    floor_route = world.ship.path(world, sailor, world.ship.storage.position(pile.id, world.fixtures))
    hold_route = world.ship.path(world, sailor, world.ship.storage.position("hold", world.fixtures))
    assert floor_route is not None and hold_route is not None
    assert len(hold_route) > len(floor_route)
    plan = world.ship.storage.plan(world, sailor, {"planks": 1})
    assert plan == [{"pile": "hold", "bundle": {"planks": 1},
                     "collected": False, "returned": False}]
    job = world.ship.add_job("test:priority", "repair", world.ship.stations["repair"], work=100)
    world.ship.claim(world, sailor)
    assert job.supply_plan[0]["pile"] == "hold"
    until(world, lambda: job.phase == "work")
    assert sailor.cargo == {"planks": 1}
    assert world.ship.storage.piles[pile.id].contents["planks"] == 1


def test_pre_filter_stockpile_save_migrates_to_all_resources_and_normal_priority(tmp_path):
    world, _, pile = storage_world()
    path = tmp_path / "old-pile-schema.json"
    world.save(path)
    data = json.loads(path.read_text())
    for row in data["ship"]["stockpiles"]:
        row.pop("filters", None)
        row.pop("priority", None)
    path.write_text(json.dumps(data))
    restored = World.load(path)
    migrated = restored.ship.storage.piles[pile.id]
    assert migrated.filters == list(RESOURCES)
    assert migrated.priority == 3


def test_designation_and_removal_validate_contents_and_reservations():
    world, _, pile = storage_world()
    world.ship.storage.und_designate(pile.id, world.ship.jobs)
    with pytest.raises(ValueError):
        world.ship.storage.und_designate(pile.id)
    world, _, pile = storage_world()
    pile.contents["shot"] = 1
    with pytest.raises(ValueError):
        world.ship.storage.und_designate(pile.id)
    pile.contents.clear()
    world.ship.storage.und_designate(pile.id)
    with pytest.raises(ValueError):
        world.ship.storage.designate(world.ship_model, world.ship_fixtures, 72, 36)
    with pytest.raises(ValueError):
        world.ship.storage.designate(world.ship_model, world.ship_fixtures, 78, 20)


def test_spatial_storage_survives_save_and_legacy_v3_migrates(tmp_path):
    world, _, pile = storage_world()
    pile.contents["shot"] = 4
    path = tmp_path / "ship.json"
    world.save(path)
    loaded = World.load(path)
    assert loaded.ship.storage.snapshot() == world.ship.storage.snapshot()
    assert dict(loaded.ship.stores) == dict(world.ship.stores)
    data = json.loads(path.read_text())
    del data["ship"]["stockpiles"]
    data["ship"].pop("fresh_food_lots", None)
    data["ship"].pop("next_fresh_food_lot_id", None)
    data["ship"]["stores"] = {"raw_food": 30, "water": 60, "planks": 16, "shot": 16}
    data["ship"]["jobs"] = []
    data["ship"]["next_id"] = 1
    data["ship"]["crew"] = [dict(c, job_id=None, cargo={}) for c in data["ship"]["crew"]]
    path.write_text(json.dumps(data))
    legacy = World.load(path)
    assert dict(legacy.ship.stores) == {"grain_g": 30000, "beans_g": 0, "rice_g": 0, "oats_g": 0,
                                        "potatoes_g": 0, "onions_g": 0, "salted_pork_g": 0,
                                        "hard_cheese_g": 0, "dried_fish_g": 0, "fruit_g": 0,
                                        "salted_fruit_g": 0, "salt_g": 0, "food_waste_g": 0,
                                        "biscuit_g": 0, "clean_water_ml": 60000,
                                        "questionable_water_ml": 0, "firewood_g": 0,
                                        "planks": 16, "shot": 16, "medicine": 0,
                                        "bandages": 0, "soap": 0, "lanterns": 0,
                                        "lamp_oil_ml": 0, "candles": 0}


def test_legacy_aggregate_save_places_piles_at_migrated_fixture_locations(tmp_path):
    from pirates.ship_fixtures import FixtureLayout
    world, _, _ = storage_world()
    world.fixtures = world.fixtures.relocated("hold", 78, 27)
    world.ship.layout = world.fixtures
    path = tmp_path / "legacy.json"
    world.save(path)
    data = json.loads(path.read_text())
    data["ship"].pop("stockpiles")
    path.write_text(json.dumps(data))
    restored = World.load(path)
    assert restored.ship.storage.position("hold", restored.fixtures) == (78, 27)
    assert restored.ship.stores["planks"] == 16


def test_storage_menu_designates_and_transfer_ui_is_paused():
    world, _, _ = storage_world()
    world.ship.storage.und_designate("pile:9,15")
    # Direct UI test starts without this test fixture's pile.
    world, _, _ = storage_world()
    world.ship.storage.und_designate("pile:9,15")
    world.player.x, world.player.y = 77, 27

    class Renderer:
        choices = iter([1, 0, 3, None])

        def render_list(self, *args, **kwargs):
            pass

        def choose(self, count):
            return next(self.choices)

        def wait_any(self):
            pass

    ticks = world.clock.total_ticks
    storage_menu(Renderer(), world)
    assert "pile:8,14" in world.ship.storage.piles
    assert world.ship.storage.piles["pile:8,14"].capacity_kg == 1000
    assert world.clock.total_ticks == ticks


def test_storage_menu_configures_filter_without_advancing_time():
    world, _, pile = storage_world()

    class Renderer:
        choices = iter([9, 2, 0, 2, None])

        def render_list(self, *args, **kwargs):
            pass

        def choose(self, count):
            return next(self.choices)

        def wait_any(self):
            pass

    ticks = world.clock.total_ticks
    storage_menu(Renderer(), world)
    assert pile.filters == ["planks"]
    assert world.clock.total_ticks == ticks


def test_storage_menu_configures_capacity_and_source_priority_without_ticking():
    world, _, pile = storage_world()

    class Renderer:
        def __init__(self, choices):
            self.choices = iter(choices)

        def render_list(self, *args, **kwargs):
            pass

        def choose(self, count):
            return next(self.choices)

        def wait_any(self):
            pass

    ticks = world.clock.total_ticks
    storage_menu(Renderer([9, 2, 2, 0, None]), world)
    assert pile.capacity_kg == 100
    storage_menu(Renderer([9, 2, 1, 0, None]), world)
    assert pile.priority == 1
    assert world.clock.total_ticks == ticks


@pytest.mark.parametrize("commit", [False, True])
def test_transfer_menu_reviews_source_destination_capacity_and_allows_free_cancel(commit):
    world, _, pile = storage_world()

    class Renderer:
        def __init__(self):
            self.last = None
            self.frames = []
            self.main_opened = False

        def render_list(self, world, title, rows, footer="", **kwargs):
            self.last = title, list(rows)
            self.frames.append(self.last)

        def choose(self, count):
            title, rows = self.last
            if title == "Spatial storage — location and contents matter":
                if self.main_opened:
                    return None
                self.main_opened = True
                return 3
            if title == "Choose supply source":
                return 0  # starter hold
            if title == "Choose destination":
                return 2  # designated floor pile
            if title == "Choose resource":
                return next(i for i, row in enumerate(rows) if row.startswith("planks:"))
            if title == "Choose quantity":
                return 0
            if title == "Commit physical transfer?":
                return 0 if commit else 1
            raise AssertionError(f"Unexpected transfer menu: {title}")

        def wait_any(self):
            pass

    renderer = Renderer()
    ticks = world.clock.total_ticks
    contents = {pid: dict(row.contents) for pid, row in world.ship.storage.piles.items()}
    storage_menu(renderer, world)

    review = next(rows for title, rows in renderer.frames if title == "Review physical transfer")
    assert any(row.startswith("Source: hold | ") and " bundles unreserved" in row for row in review)
    assert any(row.startswith(f"Destination: {pile.id} | accepts All resources") for row in review)
    assert any("Capacity:" in row and "/ 1000 kg" in row for row in review)
    assert any("Nothing moves until confirmed" in row for row in review)
    assert world.clock.total_ticks == ticks

    transfers = [job for job in world.ship.jobs if job.transfer and job.state not in ("done", "cancelled")]
    if commit:
        assert len(transfers) == 1
        assert transfers[0].transfer["bundle"] == {"planks": 1}
    else:
        assert not transfers
        assert {pid: dict(row.contents) for pid, row in world.ship.storage.piles.items()} == contents
