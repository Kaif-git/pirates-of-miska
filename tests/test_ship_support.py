from pirates.ship_model import ShipModel
from pirates.game import World
from pirates.ship_map import ORIGIN
from pirates.ship_support import advance, assess, world_loads


def test_upper_deck_floor_needs_a_real_load_path():
    model = ShipModel()
    model.place(2, 2, 0, "floor")
    model.place(2, 2, 1, "floor")
    model.place(6, 2, 1, "floor")

    report = assess(model)
    assert (2, 2, 0) in report["supported"]
    assert (2, 2, 1) in report["supported"]
    assert report["unsupported"] == ((6, 2, 1),)


def test_oak_beam_span_bridges_to_a_supported_lower_bearing():
    model = ShipModel()
    model.place(1, 4, 0, "floor")
    model.place(3, 4, 1, "floor")
    model.place(3, 4, 0, "beam", "oak")
    model.place(2, 4, 0, "beam", "oak")

    report = assess(model)
    assert report["unsupported"] == ()
    assert report["beam_spans"][0]["anchor"] == (1, 4, 0)
    assert report["beam_spans"][0]["span_tiles"] == 2


def test_weak_beam_span_and_overload_are_reported():
    model = ShipModel()
    model.place(0, 4, 0, "floor")
    model.place(3, 4, 1, "floor")
    model.place(3, 4, 0, "beam", "pine")
    model.place(2, 4, 0, "beam", "pine")
    model.place(1, 4, 0, "beam", "pine")
    assert assess(model)["unsupported"] == ((3, 4, 1),)

    model = ShipModel()
    model.place(1, 4, 0, "floor")
    model.place(2, 4, 1, "floor")
    model.place(2, 4, 0, "beam", "oak")
    report = assess(model, {(2, 4, 1): 1200})
    assert report["overloaded"]
    assert report["overloaded"][0]["load_kg"] > report["overloaded"][0]["capacity_kg"]


def test_pillar_increases_vertical_bearing_capacity():
    model = ShipModel()
    model.place(2, 4, 0, "floor")
    model.place(2, 4, 1, "floor")
    weight = {(2, 4, 1): 1200}
    assert assess(model, weight)["overloaded"]
    model.place(2, 4, 0, "pillar")
    assert assess(model, weight)["overloaded"] == ()


def test_live_load_estimate_counts_fixture_stores_and_actors():
    world = World()
    loads = world_loads(world)
    galley = world.fixtures.get("galley")
    assert loads[(galley.x, galley.y, galley.z)] >= galley.mass_kg
    hold = world.ship.storage.piles["hold"]
    assert loads[(hold.x, hold.y, hold.z)] >= 80


def test_unsupported_floor_fatigues_then_detaches_and_updates_live_model():
    world = World()
    floor = world.ship_model.place(1, 1, 1, "floor")
    adjacent = world.ship_model.place(2, 1, 1, "floor")
    for _ in range(99):
        advance(world)
    assert next(c for c in world.ship_model.components if c.id == floor.id).condition == 1
    advance(world)
    assert all(c.id not in (floor.id, adjacent.id) for c in world.ship_model.components)
    assert any("unsupported 2-tile deck section detached" in message for message in world.log)
    assert world.ship_model.deck_geometry(1)["tiles"] == 0


def test_overloaded_beam_fails_and_leaves_its_upper_floor_unsupported():
    world = World()
    world.ship_model.place(0, 2, 1, "floor")
    failed_beam = world.ship_model.place(0, 2, 0, "beam", "oak")
    world.ship_model.place(1, 2, 0, "beam", "oak")
    world.ship_model.set_condition(failed_beam.id, 1)
    pile = world.ship.storage.designate(world.ship_model, world.ship_fixtures,
                                        ORIGIN[0], ORIGIN[1] + 2, z=1)
    pile.contents["planks"] = 50

    assert assess(world.ship_model, world_loads(world))["overloaded"]
    advance(world)
    assert all(c.id != failed_beam.id for c in world.ship_model.components)
    assert (0, 2, 1) in assess(world.ship_model, world_loads(world))["unsupported"]
