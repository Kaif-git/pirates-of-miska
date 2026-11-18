import pytest

from pirates.game import World
from pirates.ship_map import ORIGIN
from pirates.ship_physics import calculate, load_by_position


def test_hydrostatics_count_actual_mass_categories_and_have_positive_reserve():
    world = World()
    report = calculate(world)
    assert report["mass_kg"]["structure"] == world.ship_model.mass_summary()["structural_mass_kg"]
    assert report["mass_kg"]["fixtures"] > 0
    assert report["mass_kg"]["supplies"] > 0
    assert report["mass_kg"]["actors"] >= 7 * 80
    assert sum(report["mass_kg"].values()) == pytest.approx(report["total_mass_kg"])
    assert report["waterplane_area_m2"] > 0
    assert report["displaced_volume_m3"] > 0
    assert 0 < report["draft_m"] < 2
    assert report["freeboard_m"] > 0
    assert report["loading_margin_kg"] > 0


def test_more_cargo_increases_draft_and_shifts_trim_and_list():
    world = World()
    before = calculate(world)
    world.economy.cargo["planks"] = 10
    after = calculate(world)
    assert after["total_mass_kg"] > before["total_mass_kg"]
    assert after["draft_m"] > before["draft_m"]
    assert after["freeboard_m"] < before["freeboard_m"]
    assert after["trim_m"] != before["trim_m"]
    assert after["list_deg"] != before["list_deg"]
    assert after["mass_kg"]["trade_cargo"] > 0


def test_upper_deck_stock_load_raises_center_of_mass_and_reduces_gm():
    world = World()
    x, y = 5, 5
    world.ship_model.place(x, y, 1, "floor")
    pile = world.ship.storage.designate(world.ship_model, world.ship_fixtures,
                                        ORIGIN[0] + x, ORIGIN[1] + y, z=1)
    pile.contents["planks"] = 30
    report = calculate(world)
    assert report["mass_kg"]["supplies"] >= 600
    assert report["center_of_mass"]["height_m"] > 0.5
    assert report["estimated_gm_m"] < calculate(World())["estimated_gm_m"]


def test_load_positions_include_cargo_and_ship_ground_items_but_not_harbor_items():
    world = World()
    world.economy.cargo["planks"] = 2
    world.ground_items[(88, 40, 0)] = [{
        "id": "test", "name": "test load", "quantity": 2,
        "weight_g": 3000, "bulk_ml": 1}]
    categories, loads = load_by_position(world)
    assert categories["trade_cargo"] > 0
    assert categories["ground_items"] == 6
    assert (19, 28, 0) in loads  # physical hold location
    assert categories["personal_inventory"] == pytest.approx(
        sum(item["weight_g"] * item["quantity"] for item in world.inventory.items) / 1000)


def test_load_and_hydrostatics_report_roundtrip_in_save(tmp_path):
    world = World()
    world.economy.cargo["planks"] = 3
    before = calculate(world)
    path = tmp_path / "physics-load.json"
    world.save(path)
    restored = World.load(path)
    assert calculate(restored) == before


def test_ship_ledger_exposes_mass_hydrostatics_and_balance():
    from pirates.management import ship_rows
    joined = "\n".join(ship_rows(World()))
    assert "Displacement:" in joined
    assert "draft" in joined and "freeboard" in joined
    assert "trim" in joined and "list" in joined and "estimated GM" in joined
    assert "Hull estimate" in joined


def test_sailing_force_estimate_responds_to_rigging_wind_and_loaded_hull():
    from pirates.ship_physics import sailing_forces
    world = World()
    trade = {"dx": 1, "dy": 0, "name": "trade"}
    light = {"dx": 0, "dy": 0, "name": "light"}
    downwind = sailing_forces(world, trade, 0)
    light_air = sailing_forces(world, light, 0)
    assert downwind["sail_force_n"] > light_air["sail_force_n"]
    world.ship.rigging = 0
    disabled = sailing_forces(world, trade, 0)
    assert disabled["sail_force_n"] == 0
    assert disabled["target_speed_mps"] == 0
    world.ship.rigging = 100
    loaded = World()
    loaded.economy.cargo["planks"] = 150
    assert sailing_forces(loaded, trade, 0)["target_speed_mps"] < downwind["target_speed_mps"]
