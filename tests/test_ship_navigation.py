import pytest

from pirates.ship_model import ShipModel
from pirates.ship_navigation import route, route_to_worksite, traversed_doors


def two_deck_model():
    model = ShipModel()
    for z in (0, 1):
        for x in range(3):
            model.place(x, 1, z, "floor")
    return model


def test_decks_are_disconnected_until_a_stair_is_built():
    model = two_deck_model()
    assert route(model, (0, 1, 0), (2, 1, 1)) is None
    stair = model.place(1, 1, 0, "stair")
    path = route(model, (0, 1, 0), (2, 1, 1))
    assert path == [(0, 1, 0), (1, 1, 0), (1, 1, 1), (2, 1, 1)]
    assert stair.kind == "stair"
    assert route(model, (2, 1, 1), (0, 1, 0)) == list(reversed(path))


def test_route_obeys_walls_and_reports_doors_on_path():
    model = ShipModel()
    for x in range(3):
        model.place(x, 0, 0, "floor")
    door = model.place(1, 0, 0, "door")
    assert traversed_doors(model, route(model, (0, 0, 0), (2, 0, 0))) == ((1, 0, 0),)
    model.remove(door.id)
    model.place(1, 0, 0, "wall")
    assert route(model, (0, 0, 0), (2, 0, 0)) is None


def test_stairs_require_floors_at_both_ends_and_roundtrip():
    model = ShipModel()
    model.place(4, 6, 0, "floor")
    with pytest.raises(ValueError, match="upper-deck"):
        model.place(4, 6, 0, "stair")
    model.place(4, 6, 1, "floor")
    model.place(4, 6, 0, "stair")
    restored = ShipModel.restore(model.snapshot())
    assert restored.snapshot() == model.snapshot()
    malformed = model.snapshot()
    malformed["components"] = [c for c in malformed["components"]
                               if not (c["kind"] == "floor" and c["z"] == 1)]
    with pytest.raises(ValueError, match="both deck ends"):
        ShipModel.restore(malformed)


def test_navigation_coordinates_must_be_integer_deck_triples():
    model = two_deck_model()
    with pytest.raises(ValueError, match="triples"):
        route(model, (0, 1), (2, 1, 1))
    with pytest.raises(ValueError, match="triples"):
        route(model, (0, 1, 0.5), (2, 1, 1))


def test_cross_deck_worksite_requires_a_real_stair_route():
    model = two_deck_model()
    target = (2, 1, 1)
    assert route_to_worksite(model, (0, 1, 0), target) is None
    model.place(1, 1, 0, "stair")
    path = route_to_worksite(model, (0, 1, 0), target)
    assert path == [(0, 1, 0), (1, 1, 0), (1, 1, 1)]


def test_worksite_position_has_strict_three_dimensional_coordinates():
    model = two_deck_model()
    with pytest.raises(ValueError, match="triples"):
        route_to_worksite(model, (0, 1, 0), (2, 1))
