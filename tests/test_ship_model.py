import pytest

from pirates.ship_model import ShipModel


def test_layers_slots_and_rebuilding():
    ship = ShipModel()
    floor = ship.place(-2, 3, -1, "floor")
    wall = ship.place(-2, 3, -1, "wall")
    ship.place(-2, 3, 0, "floor")
    with pytest.raises(ValueError):
        ship.place(-2, 3, -1, "door")
    assert len(ship.at(-2, 3, -1)) == 2
    assert ship.remove(wall.id) == wall
    door = ship.place(-2, 3, -1, "door")
    assert door.id > wall.id > floor.id


def test_mass_and_roundtrip():
    ship = ShipModel()
    ship.place(0, 0, 0, "floor", "pine")
    ship.place(4, 0, 2, "wall", "oak")
    assert ship.mass_summary() == {
        "structural_mass_kg": 100, "center_of_mass": (2.4, 0, 1.2)}
    restored = ShipModel.restore(ship.snapshot())
    assert restored.snapshot() == ship.snapshot()
    assert restored.mass_summary() == ship.mass_summary()
    restored.remove(1)
    assert ship.mass_summary()["structural_mass_kg"] == 100


@pytest.mark.parametrize("field,value", [
    ("x", True), ("z", 1.5), ("material", "magic"),
    ("kind", "unknown"), ("condition", -1), ("id", 0),
])
def test_reject_invalid_records(field, value):
    ship = ShipModel()
    ship.place(0, 0, 0, "floor")
    data = ship.snapshot()
    data["components"][0][field] = value
    with pytest.raises(ValueError):
        ShipModel.restore(data)


def test_duplicate_and_identity_validation():
    ship = ShipModel()
    ship.place(0, 0, 0, "floor")
    data = ship.snapshot()
    data["components"].append(dict(data["components"][0]))
    with pytest.raises(ValueError):
        ShipModel.restore(data)
    data = ship.snapshot()
    data["next_id"] = 1
    with pytest.raises(ValueError):
        ShipModel.restore(data)


def test_empty_ship():
    ship = ShipModel()
    assert ship.mass_summary() == {"structural_mass_kg": 0, "center_of_mass": None}
    assert ship.deck_geometry() == {"deck": 0, "tiles": 0, "bounds": None,
                                   "perimeter_edges": (), "perimeter": 0,
                                   "components": 0, "holes": 0}
    assert ShipModel.restore(ship.snapshot()).snapshot() == ship.snapshot()


def test_deck_geometry_measures_irregular_contours_holes_and_components():
    ship = ShipModel()
    # An 8-tile ring has one enclosed void and 16 exposed cardinal edges.
    for x in range(3):
        for y in range(3):
            if (x, y) != (1, 1):
                ship.place(x, y, 0, "floor")
    geometry = ship.deck_geometry(0)
    assert geometry["tiles"] == 8
    assert geometry["bounds"] == (0, 0, 2, 2)
    assert geometry["perimeter"] == 16
    assert geometry["holes"] == 1
    assert geometry["components"] == 1
    assert len(geometry["perimeter_edges"]) == 16
    # A separate non-rectangular deck is reported as its own connected component.
    ship.place(8, 4, 0, "floor")
    geometry = ship.deck_geometry(0)
    assert geometry["components"] == 2
    assert geometry["perimeter"] == 20
    assert ship.deck_geometry(1)["tiles"] == 0
