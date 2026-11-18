import json
from copy import deepcopy

import pytest

from pirates.game import World
from pirates.economy import MERCHANTS, build_port
from pirates.voyage import (SEA_NODES, VoyageSystem, NpcFleetSystem,
                            CREW_ORDERS, CAPTAIN_ACTIONS,
                            ticks_to_next_day_boundary)
from pirates.ship import LABORS


def quiet_world():
    world = World()
    for sailor in world.ship.crew:
        sailor.labors = {kind: 0 for kind in LABORS}
        sailor.hunger = sailor.thirst = sailor.fatigue = sailor.loneliness = sailor.boredom = 0
        sailor.sleep_debt = 20
    world.npcs.sighted.update(s["id"] for s in world.npcs.ships)
    return world


def active_storm_world(waypoint="shoals"):
    world = quiet_world()
    world.voyages.confirm(world, "cinder", waypoint)
    world.enter_sea()
    voyage = world.voyages.voyage
    voyage["leg"] = 1
    voyage["leg_progress"] = 0.0
    world.voyages.position = voyage["storm_event"]["node"]
    voyage["storm_event"].update({"triggered": True, "trigger_roll": 5})
    world.voyages.step_second(world)
    assert voyage["status"] == "storm"
    return world


def clear_squall_for_ordinary_scenario(voyage):
    storm = voyage.get("storm_event")
    if storm:
        storm["trigger_roll"] = storm["chance_percent"]
        storm["triggered"] = False


def test_advance_to_day_boundary_uses_exact_midnight_ticks():
    from tales_sim.clock import TICKS_PER_DAY, START_OF_DAY_TICKS

    assert ticks_to_next_day_boundary(START_OF_DAY_TICKS) == TICKS_PER_DAY - START_OF_DAY_TICKS
    assert ticks_to_next_day_boundary(TICKS_PER_DAY - 1) == 1
    assert ticks_to_next_day_boundary(TICKS_PER_DAY) == TICKS_PER_DAY
    with pytest.raises(ValueError):
        ticks_to_next_day_boundary(-1)


def test_equal_chart_routes_are_exposed_with_real_distinct_waypoints():
    world = quiet_world()
    routes = world.voyages.route_options("verdant", "cinder")
    paths = {tuple(row["route"]) for row in routes}
    assert ("verdant", "shoals", "cinder") in paths
    assert ("verdant", "navy_lane", "cinder") in paths
    assert len({round(row["distance"], 8) for row in routes}) == 1
    assert world.voyages.plan(world, "cinder", "shoals")["route"] == ["verdant", "shoals", "cinder"]
    assert world.voyages.plan(world, "cinder", "navy_lane")["route"] == ["verdant", "navy_lane", "cinder"]
    with pytest.raises(ValueError, match="Unknown chart location"):
        world.voyages.route_options("verdant", "uncharted")


def test_hail_and_repair_aid_are_persisted_and_consume_real_cargo(tmp_path):
    world = quiet_world()
    target = next(ship for ship in world.npcs.ships if ship["id"] == "government_1")
    target["condition"] = 40
    world.npcs.sighted.add(target["id"])
    world.voyages.confirm(world, "deepwater")
    world.voyages.voyage["status"] = "encounter"
    world.voyages.voyage["encounter"] = deepcopy(target)
    world.enter_sea()

    before_ticks = world.clock.total_ticks
    before_ship_seconds = world.ship.seconds
    world.npcs.hail_ship(world, target["id"])
    assert world.npcs.player_contacts[target["id"]]["hailed"] is True
    assert world.clock.total_ticks == before_ticks + 6000
    assert world.ship.seconds == before_ship_seconds + 60
    assert set(world.npcs.player_contacts[target["id"]]) == {
        "first_hour", "last_hour", "hailed", "aid_count"}
    with pytest.raises(ValueError, match="already recorded"):
        world.npcs.hail_ship(world, target["id"])

    hold_x, hold_y = world.ship.stations["hold"]
    world.player.x, world.player.y, world.player.z = hold_x - 1, hold_y, 0
    before_aid_ticks = world.clock.total_ticks
    with pytest.raises(ValueError, match="requires one uncommitted iron"):
        world.npcs.assist_ship(world, target["id"])
    assert target["condition"] == 40 and world.clock.total_ticks == before_aid_ticks
    world.economy.cargo["iron"] = 1
    before_ship_seconds = world.ship.seconds
    repaired = world.npcs.assist_ship(world, target["id"])
    assert repaired == 20 and target["condition"] == 60
    assert world.clock.total_ticks == before_aid_ticks + 6000
    assert world.ship.seconds == before_ship_seconds + 60
    assert "iron" not in world.economy.cargo
    assert world.npcs.player_contacts[target["id"]]["aid_count"] == 1
    with pytest.raises(ValueError, match="already been given"):
        world.npcs.assist_ship(world, target["id"])

    path = tmp_path / "voyage-contact.json"
    world.save(path)
    restored = World.load(path)
    assert restored.npcs.player_contacts == world.npcs.player_contacts
    restored_target = next(ship for ship in restored.npcs.ships if ship["id"] == target["id"])
    assert restored_target["condition"] == 60
    assert restored.economy.cargo.get("iron", 0) == 0
    from pirates.management import sea_map_rows
    chart = "\n".join(sea_map_rows(restored))
    assert "Signal contact recorded" in chart and "repair aid given" in chart


def test_hostile_or_changed_sighting_cannot_be_hail_target():
    world = quiet_world()
    pirate = next(ship for ship in world.npcs.ships if ship["threat"] == "pirates")
    world.npcs.sighted.add(pirate["id"])
    world.voyages.confirm(world, "deepwater")
    world.voyages.voyage["status"] = "encounter"
    world.voyages.voyage["encounter"] = deepcopy(pirate)
    world.enter_sea()
    ticks = world.clock.total_ticks

    with pytest.raises(ValueError, match="will not answer"):
        world.npcs.hail_ship(world, pirate["id"])
    with pytest.raises(ValueError, match="no longer the active"):
        world.npcs.hail_ship(world, "government_1")
    assert world.npcs.player_contacts == {}
    assert world.clock.total_ticks == ticks

    patrol = next(ship for ship in world.npcs.ships if ship["id"] == "government_1")
    world.npcs.sighted.add(patrol["id"])
    world.voyages.voyage["encounter"] = deepcopy(patrol)
    patrol["node"] = "shoals" if patrol["node"] != "shoals" else "deepwater"
    with pytest.raises(ValueError, match="moved on"):
        world.npcs.hail_ship(world, patrol["id"])
    assert world.npcs.player_contacts == {} and world.clock.total_ticks == ticks


def test_ignored_sighting_resumes_without_fabricated_world_or_economy_effects():
    world = quiet_world()
    target = next(ship for ship in world.npcs.ships if ship["id"] == "government_1")
    world.voyages.confirm(world, "deepwater")
    world.voyages.voyage["status"] = "encounter"
    world.voyages.voyage["encounter"] = deepcopy(target)
    before_npcs = world.npcs.snapshot()
    before_money = world.economy.money
    before_cargo = deepcopy(world.economy.cargo)
    before_ticks = world.clock.total_ticks
    before_ship_seconds = world.ship.seconds

    world.voyages.continue_after_sighting()

    assert world.voyages.voyage["status"] == "underway"
    assert world.voyages.voyage["encounter"] is None
    assert world.npcs.snapshot() == before_npcs
    assert world.npcs.player_contacts == {}
    assert world.economy.money == before_money and world.economy.cargo == before_cargo
    assert world.clock.total_ticks == before_ticks and world.ship.seconds == before_ship_seconds


def test_missing_live_sighting_target_fails_without_substitution_or_time_cost():
    world = quiet_world()
    target = next(ship for ship in world.npcs.ships if ship["id"] == "government_1")
    world.voyages.confirm(world, "deepwater")
    world.voyages.voyage["status"] = "encounter"
    world.voyages.voyage["encounter"] = deepcopy(target)
    world.npcs.ships.remove(target)
    before_ticks = world.clock.total_ticks

    with pytest.raises(ValueError, match="no longer in the fleet"):
        world.npcs.hail_ship(world, target["id"])
    assert world.npcs.player_contacts == {}
    assert world.clock.total_ticks == before_ticks


def test_contact_and_aid_cannot_become_free_actions_during_combat():
    world = quiet_world()
    target = next(ship for ship in world.npcs.ships if ship["id"] == "government_1")
    target["condition"] = 40
    world.voyages.confirm(world, "deepwater")
    world.voyages.voyage["status"] = "encounter"
    world.voyages.voyage["encounter"] = deepcopy(target)
    world.enter_sea()
    world.npcs.player_contacts[target["id"]] = {
        "first_hour": 8, "last_hour": 8, "hailed": True, "aid_count": 0}
    world.economy.cargo["iron"] = 1
    world.combat.active = True
    before_ticks = world.clock.total_ticks

    with pytest.raises(ValueError, match="during active combat"):
        world.npcs.hail_ship(world, target["id"])
    with pytest.raises(ValueError, match="during active combat"):
        world.npcs.assist_ship(world, target["id"])
    assert world.clock.total_ticks == before_ticks
    assert world.npcs.player_contacts[target["id"]]["aid_count"] == 0
    assert world.economy.cargo["iron"] == 1 and target["condition"] == 40


def test_non_hostile_encounter_menu_adds_contact_without_shifting_continue_action():
    from pirates.management import voyage_menu

    class Scripted:
        def __init__(self, answers):
            self.answers = iter(answers)
        def render_list(self, *_args, **_kwargs):
            pass
        def choose(self, _count):
            return next(self.answers)
        def wait_any(self):
            pass

    world = quiet_world()
    target = next(ship for ship in world.npcs.ships if ship["id"] == "government_1")
    world.npcs.sighted.add(target["id"])
    world.voyages.confirm(world, "deepwater")
    world.voyages.voyage["status"] = "encounter"
    world.voyages.voyage["encounter"] = deepcopy(target)
    world.enter_sea()
    start = world.clock.total_ticks
    voyage_menu(Scripted([6, 3, None]), world)
    assert world.npcs.player_contacts[target["id"]]["hailed"]
    assert world.clock.total_ticks == start + 6000

    world.voyages.voyage["status"] = "encounter"
    world.voyages.voyage["encounter"] = deepcopy(target)
    voyage_menu(Scripted([6, 1, None]), world)
    assert world.voyages.voyage["status"] == "underway"


def test_storm_forecast_is_deterministic_and_interrupt_state_survives_save(tmp_path):
    first, second = quiet_world(), quiet_world()
    for world in (first, second):
        world.voyages.confirm(world, "cinder", "shoals")
        world.npcs.sighted.update(ship["id"] for ship in world.npcs.ships)
        world.enter_sea()
    assert first.voyages.voyage["storm_event"] == second.voyages.voyage["storm_event"]
    from pirates.management import sea_map_rows
    assert any("Squall watch:" in row for row in sea_map_rows(first))

    voyage = first.voyages.voyage
    voyage["storm_event"].update({"triggered": True, "trigger_roll": 5})
    voyage["leg"] = 1
    voyage["leg_progress"] = 0.0
    first.voyages.position = voyage["storm_event"]["node"]
    second_voyage = second.voyages.voyage
    second_voyage["storm_event"].update({"triggered": True, "trigger_roll": 5})
    second_voyage["leg"] = 1
    second_voyage["leg_progress"] = 0.0
    second.voyages.position = second_voyage["storm_event"]["node"]
    before = first.ship.seconds
    first.advance(100 * 6000)
    second.advance(100)
    assert first.ship.seconds == before + 1
    assert voyage["status"] == "storm" and voyage["storm_event"]["stage"] == "active"
    assert first.voyages.snapshot() == second.voyages.snapshot()
    assert first.ship.snapshot() == second.ship.snapshot()
    assert first.npcs.snapshot() == second.npcs.snapshot()

    path = tmp_path / "active-squall.json"
    first.save(path)
    restored = World.load(path)
    assert restored.voyages.voyage["storm_event"] == voyage["storm_event"]
    assert restored.voyages.voyage["status"] == "storm"
    before_hull, before_rigging = restored.ship.hull, restored.ship.rigging
    restored.voyages.resolve_storm(restored, "press_on")
    assert restored.ship.hull == before_hull - 10
    assert restored.ship.rigging == before_rigging - 16
    assert restored.voyages.voyage["storm_event"]["stage"] == "resolved"


def test_same_tick_ship_sighting_is_resolved_before_due_squall():
    world = quiet_world()
    world.voyages.confirm(world, "cinder", "shoals")
    world.enter_sea()
    voyage = world.voyages.voyage
    voyage["leg"] = 1
    voyage["leg_progress"] = 0.0
    voyage["elapsed_seconds"] = 1801
    world.voyages.position = voyage["storm_event"]["node"]
    voyage["storm_event"].update({"triggered": True, "trigger_roll": 5})
    sighting = deepcopy(next(ship for ship in world.npcs.ships
                             if ship["id"] == "government_1"))

    world.voyages.step_second(world, sighting)
    assert voyage["status"] == "encounter"
    assert voyage["storm_event"]["stage"] == "forecast"
    world.voyages.continue_after_sighting()
    world.voyages.step_second(world)
    assert voyage["status"] == "storm"
    assert voyage["storm_event"]["stage"] == "active"


def test_squall_press_on_damages_existing_ship_condition_once():
    world = active_storm_world()
    voyage = world.voyages.voyage
    voyage["storm_event"]["trigger_roll"] = 8
    hull, rigging = world.ship.hull, world.ship.rigging

    world.voyages.resolve_storm(world, "press_on")
    assert world.ship.hull == hull - 5
    assert world.ship.rigging == rigging - 8
    assert voyage["status"] == "underway"
    assert voyage["storm_event"]["stage"] == "resolved"
    world.voyages.step_second(world)
    assert voyage["storm_event"]["stage"] == "resolved"
    with pytest.raises(ValueError, match="no active squall"):
        world.voyages.resolve_storm(world, "press_on")
    assert world.ship.hull == hull - 5 and world.ship.rigging == rigging - 8


def test_uncertain_squall_forecast_can_pass_without_interrupting():
    world = quiet_world()
    world.voyages.confirm(world, "cinder", "shoals")
    voyage = world.voyages.voyage
    storm = voyage["storm_event"]
    storm.update({"triggered": False, "trigger_roll": storm["chance_percent"]})
    voyage["leg"] = 1
    voyage["leg_progress"] = 0.0
    world.voyages.position = storm["node"]
    hull, rigging = world.ship.hull, world.ship.rigging

    world.voyages.step_second(world)
    assert voyage["status"] == "underway" and storm["stage"] == "clear"
    assert (world.ship.hull, world.ship.rigging) == (hull, rigging)


def test_squall_detour_adds_real_distance_without_ship_damage(tmp_path):
    world = active_storm_world()
    voyage = world.voyages.voyage
    old_distance = voyage["distance"]
    hull, rigging = world.ship.hull, world.ship.rigging

    world.voyages.resolve_storm(world, "detour", ["shoals", "navy_lane", "cinder"])
    assert voyage["route"] == ["verdant", "shoals", "navy_lane", "cinder"]
    assert voyage["distance"] > old_distance
    assert (world.ship.hull, world.ship.rigging) == (hull, rigging)
    assert voyage["status"] == "underway" and voyage["storm_event"]["stage"] == "avoided"
    path = tmp_path / "detoured-voyage.json"
    world.save(path)
    restored = World.load(path)
    assert restored.voyages.voyage["route"] == voyage["route"]
    assert restored.voyages.voyage["storm_event"] == voyage["storm_event"]


def test_squall_cannot_be_avoided_by_choosing_its_threatened_lane():
    world = active_storm_world()
    voyage = world.voyages.voyage
    before = deepcopy(voyage)
    hull, rigging = world.ship.hull, world.ship.rigging
    with pytest.raises(ValueError, match="still crosses"):
        world.voyages.resolve_storm(world, "detour", ["shoals", "cinder"])
    assert voyage == before and world.voyages.voyage["status"] == "storm"
    assert (world.ship.hull, world.ship.rigging) == (hull, rigging)


def test_legacy_voyage_snapshot_migrates_without_storm_fields():
    world = quiet_world()
    world.voyages.confirm(world, "cinder", "shoals")
    snapshot = world.voyages.snapshot()
    for field in ("storm_event", "storm_forecast", "departure_snapshot",
                  "arrival_snapshot", "outcomes", "initial_estimate_seconds"):
        snapshot["voyage"].pop(field, None)

    restored = VoyageSystem.restore(snapshot, world)
    assert restored.voyage["status"] == "underway"
    assert restored.voyage["storm_event"] is None
    assert restored.voyage["departure_snapshot"] is None
    assert restored.voyage["arrival_snapshot"] is None
    assert restored.voyage["outcomes"] == []
    assert restored.voyage["initial_estimate_seconds"] == restored.voyage["duration_seconds"]


def test_officers_consult_and_quartermaster_warns_on_low_stores():
    world = quiet_world()
    world.ship.stores["clean_water_ml"] = 1000
    world.ship.prepared_food_g = 250
    for key in ("grain_g", "beans_g", "dried_fish_g", "fruit_g", "biscuit_g"):
        world.ship.stores[key] = 0
    rows = world.voyages.role_report(world)
    assert any("water is short" in line for line in rows)
    assert any("food stores cover" in line and "0.85 kg/crew/day" in line for line in rows)
    assert any("Ration estimate: 2.2 L clean water + 0.85 kg food" in line for line in rows)
    assert any("Navigator:" in line for line in rows)
    assert any("Numbers-skilled purser" in line for line in rows)


def test_officer_roles_are_unique_and_saved():
    world = quiet_world()
    world.voyages.assign_role(world, "navigator", "tavi")
    assert world.voyages.roles["navigator"] == "tavi"
    with pytest.raises(ValueError, match="different"):
        world.voyages.assign_role(world, "purser", "tavi")
    with pytest.raises(ValueError):
        world.voyages.assign_role(world, "captain", "mara")


def test_ship_can_sail_with_no_crew_and_vacant_officer_roles():
    world = quiet_world()
    world.ship.crew = []
    world.voyages.roles = {"navigator": None, "quartermaster": None, "purser": None}
    plan = world.voyages.plan(world, "shoals")
    assert plan["navigator"] == plan["quartermaster"] == plan["purser"] == "Unassigned"
    assert plan["duration_seconds"] > 0
    assert "unassigned" in " ".join(world.voyages.role_report(world)).lower()
    world.voyages.confirm(world, "shoals")
    world.enter_sea()
    world.advance(100)
    assert world.voyages.voyage["elapsed_seconds"] == 1


def test_chart_plans_route_and_officer_skill_changes_estimate():
    world = quiet_world()
    plan = world.voyages.plan(world, "deepwater")
    assert plan["route"][0] == "verdant" and plan["route"][-1] == "deepwater"
    assert len(plan["route"]) >= 3 and plan["duration_seconds"] > 1800
    base = plan["duration_seconds"]
    world.ship.sailor(world.voyages.roles["navigator"]).skills["rig"] = 10
    assert world.voyages.plan(world, "deepwater")["duration_seconds"] < base


def test_appointed_captain_judgement_improves_estimate_and_role_is_saved(tmp_path):
    world = quiet_world()
    uncommanded = world.voyages.plan(world, "deepwater")["duration_seconds"]
    world.voyages.assign_role(world, "captain", "tavi")
    commanded = world.voyages.plan(world, "deepwater")["duration_seconds"]
    assert commanded < uncommanded
    assert any("Appointed crew captain: Tavi" in row for row in world.voyages.role_report(world))
    with pytest.raises(ValueError, match="different"):
        world.voyages.assign_role(world, "captain", "suri")  # already navigator
    path = tmp_path / "captain-role.json"
    world.save(path)
    assert World.load(path).voyages.roles["captain"] == "tavi"


def test_legacy_voyage_roles_migrate_an_available_crew_captain(tmp_path):
    world = quiet_world()
    path = tmp_path / "legacy-officers.json"
    world.save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["voyage"]["roles"].pop("captain")
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = World.load(path)
    assert loaded.voyages.roles["captain"] == "tavi"


def test_crew_leader_mental_attributes_change_team_training_and_fatigue():
    def drill(leadership, discipline):
        world = quiet_world()
        world.voyages.confirm(world, "deepwater")
        leader, trainee = world.ship.sailor("mara"), world.ship.sailor("ivo")
        leader.mental["leadership"] = leadership
        leader.mental["discipline"] = discipline
        world.economy.gear = {"mara": {"weapon": "cutlass"}, "ivo": {"weapon": "cutlass"}}
        world.voyages.set_crew_order(world, "sword_drill", leader.id, [leader.id, trainee.id])
        world.voyages.voyage["elapsed_seconds"] = 60
        before = trainee.energy
        assert world.voyages.crew_activity(world, trainee)
        return world.economy.weapon_xp.get(trainee.id, 0), before - trainee.energy

    low_command, high_command = drill(0, 0), drill(100, 100)
    assert low_command[0] == pytest.approx(0.05)
    assert high_command[0] == pytest.approx(0.15)
    assert high_command[1] < low_command[1]


def test_sea_chart_marker_interpolates_between_nodes_during_a_leg():
    world = quiet_world()
    world.voyages.confirm(world, "cinder")
    world.voyages.voyage["duration_seconds"] = 100
    world.voyages.voyage["elapsed_seconds"] = 25
    world.voyages.voyage["leg_progress"] = 0.5
    x, y = world.voyages.map_coordinate()
    assert x != SEA_NODES["verdant"]["xy"][0] or y != SEA_NODES["verdant"]["xy"][1]


def test_wind_sail_load_and_rudder_change_live_sailing_state_and_route_marker(tmp_path):
    world = quiet_world()
    world.voyages.confirm(world, "cinder")
    voyage = world.voyages.voyage
    route = voyage["route"]
    a, b = SEA_NODES[route[0]]["xy"], SEA_NODES[route[1]]["xy"]
    course = __import__("math").degrees(__import__("math").atan2(b[1] - a[1], b[0] - a[0])) % 360
    world.voyages.set_rudder(25)
    for _ in range(600):
        world.voyages.step_second(world)
    state = voyage["sailing"]
    assert state["speed_mps"] > 0
    assert state["drive_n"] > 0
    assert state["resistance_n"] >= 0
    assert state["heading_deg"] != course
    assert state["cross_track"] != 0
    position = world.voyages.map_coordinate()
    world.voyages.set_rudder(-25)
    world.advance(100 * 60)
    path = tmp_path / "sailing-save.json"
    world.save(path)
    restored = World.load(path)
    assert restored.voyages.voyage["sailing"] == world.voyages.voyage["sailing"]
    assert restored.voyages.map_coordinate() == world.voyages.map_coordinate()
    assert position != (SEA_NODES[route[0]]["xy"])


def test_invalid_persisted_sailing_state_is_rejected(tmp_path):
    world = quiet_world()
    world.voyages.confirm(world, "cinder")
    path = tmp_path / "invalid-sailing.json"
    world.save(path)
    data = json.loads(path.read_text())
    data["voyage"]["voyage"]["sailing"] = {"speed_mps": float("nan")}
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="sailing"):
        World.load(path)


def test_voyage_simulates_ship_life_and_starts_at_sea_without_ship_geometry_changes():
    world = quiet_world()
    geometry = world.ship_model.snapshot()
    seconds = world.ship.seconds
    hunger = world.ship.crew[0].hunger
    world.voyages.confirm(world, "deepwater")
    world.enter_sea()
    assert world.at_sea and world.player.x >= 69
    assert not [e for e in world.entities() if e.get("role") == "merchant"]
    assert world.ship_model.snapshot() == geometry
    world.advance(300 * 100)
    assert world.ship.seconds == seconds + 300
    assert world.ship.crew[0].hunger > hunger
    assert world.voyages.voyage["elapsed_seconds"] == 300
    assert not world.ship.check_orders()


def test_daily_chores_keep_running_during_offscreen_sailing():
    world = quiet_world()
    sailor = world.ship.sailor("mara")
    sailor.labors["repair"] = 1
    world.voyages.confirm(world, "deepwater")
    world.advance(120 * 100)
    assert any(row["crew"] == "mara" and row["event"] in ("claimed", "resumed", "supplies_collected")
               for row in world.ship.audit)
    assert world.ship.seconds == 120


def test_captain_free_time_and_team_practice_run_without_overriding_needs_or_jobs():
    world, control = quiet_world(), quiet_world()
    world.voyages.confirm(world, "deepwater")
    control.voyages.confirm(control, "deepwater")
    world.voyages.set_captain_action("rest")
    fatigue = world.voyages.captain_fatigue
    members = ["mara", "ivo"]
    world.voyages.set_crew_order(world, "brawling_drill", "mara", members)
    world.advance(600 * 100)
    assert world.voyages.captain_fatigue < fatigue
    assert world.ship.sailor("mara").energy < 100
    assert world.voyages.crew_order["members"] == members
    assert world.voyages.captain_action in CAPTAIN_ACTIONS
    control.advance(600 * 100)
    assert world.ship.sailor("mara").boredom < control.ship.sailor("mara").boredom


def test_sword_team_requires_equipment_and_leader_and_actual_unit():
    world = quiet_world()
    with pytest.raises(ValueError, match="weapon"):
        world.voyages.set_crew_order(world, "sword_drill", "mara", ["mara", "ivo"])
    world.economy.gear = {sid: {"weapon": "cutlass"} for sid in ("mara", "ivo")}
    world.voyages.set_crew_order(world, "sword_drill", "mara", ["mara", "ivo"])
    with pytest.raises(ValueError, match="leader"):
        world.voyages.set_crew_order(world, "brawling_drill", "mara", ["ivo", "nell"])
    with pytest.raises(ValueError, match="two sailors"):
        world.voyages.set_crew_order(world, "brawling_drill", "mara", ["mara"])


def test_sea_team_practice_yields_to_urgent_needs_and_active_jobs():
    world = quiet_world()
    world.voyages.confirm(world, "deepwater")
    world.voyages.set_crew_order(world, "brawling_drill", "mara", ["mara", "ivo"])
    mara = world.ship.sailor("mara")
    mara.thirst = 80
    assert not world.voyages.crew_activity(world, mara)
    mara.thirst = 0
    mara.job_id = 777
    assert not world.voyages.crew_activity(world, mara)


def test_encounter_interrupts_long_wait_and_returns_control_to_ship_scene():
    world = quiet_world()
    world.voyages.confirm(world, "deepwater")
    world.enter_sea()
    sighting = {"id": "marine_test", "name": "Test patrol", "faction": "World Government"}
    world.npcs.advance_second = lambda *_args, **_kwargs: sighting
    before = world.ship.seconds
    world.advance(100 * 6000)
    assert world.voyages.voyage["status"] == "encounter"
    assert world.voyages.voyage["encounter"]["name"] == "Test patrol"
    assert world.ship.seconds == before + 1801
    assert world.at_sea
    assert world.log[-1].startswith("LOOKOUTS:")
    world.advance(100)
    assert world.ship.seconds == before + 1802
    assert world.voyages.voyage["status"] == "encounter"


def test_npc_sighting_uses_nearby_chart_distance_not_only_identical_nodes():
    world = quiet_world()
    fleets = world.npcs
    fleets.sighted.clear()
    fleets.hour = world.clock.total_ticks // 360000 - 1
    fleets.ships[0]["node"] = fleets.ships[0]["destination"] = "deepwater"
    fleets.ships[1]["node"] = fleets.ships[1]["destination"] = "shoals"
    fleets.ships[2]["node"] = fleets.ships[2]["destination"] = "freeport"
    sighting = fleets.advance_second(world, (3.0, 2.6), sighting_enabled=True)
    assert sighting and sighting["id"] == "government_1"


def test_arrival_interrupts_watch_interval_and_returns_to_real_port():
    world = quiet_world()
    plan = world.voyages.confirm(world, "cinder")
    clear_squall_for_ordinary_scenario(world.voyages.voyage)
    world.voyages.voyage["duration_seconds"] = 25
    world.enter_sea()
    before = world.ship.seconds
    world.advance(100 * 600)
    assert world.ship.seconds == before + 25
    assert world.voyages.voyage["status"] == "arrived"
    assert world.voyages.position == "cinder" and world.economy.port == "cinder"
    assert not world.at_sea and world.game_map.is_walkable(world.player.x, world.player.y)
    assert world.voyages.voyage["departure_snapshot"]["ticks"] < world.voyages.voyage["arrival_snapshot"]["ticks"]
    first_report = world.voyages.arrival_report(world)
    first_snapshot = world.voyages.snapshot()
    clock = world.clock.total_ticks
    second_report = world.voyages.arrival_report(world)
    assert first_report == second_report
    assert world.voyages.snapshot() == first_snapshot
    assert world.clock.total_ticks == clock
    assert any("Actual elapsed:" in line for line in first_report)
    assert any("Cargo changes:" in line for line in first_report)


def test_arrival_report_survives_save_reload_and_reports_outcomes(tmp_path):
    world = quiet_world()
    world.voyages.confirm(world, "cinder")
    clear_squall_for_ordinary_scenario(world.voyages.voyage)
    world.voyages.voyage["duration_seconds"] = 25
    world.voyages._record("Test recorded outcome.")
    world.enter_sea()
    world.advance(100 * 600)
    assert world.voyages.voyage["status"] == "arrived"
    path = tmp_path / "arrival-report.json"
    world.save(path)
    restored = World.load(path)
    assert restored.voyages.arrival_report(restored) == world.voyages.arrival_report(world)
    assert any("Test recorded outcome." in line
               for line in restored.voyages.arrival_report(restored))


def test_arrival_report_distinguishes_delivery_arrival_from_manual_settlement():
    world = quiet_world()
    world.player.x, world.player.y, world.player.z = 42, 23, 0
    world.refresh()
    offer = next(row for row in world.economy.contract_offers("verdant", world.clock.day)
                 if row["item"] == "bluegrain")
    contract = world.economy.accept_contract(world, offer["offer_id"])
    merchant = MERCHANTS[offer["issuer"]]
    world.player.x, world.player.y = merchant["at"][0] + 1, merchant["at"][1]
    world.refresh()
    world.economy.trade(world, offer["issuer"], offer["item"], offer["quantity"], True)
    world.voyages.confirm(world, "cinder")
    clear_squall_for_ordinary_scenario(world.voyages.voyage)
    world.voyages.voyage["duration_seconds"] = 25
    world.enter_sea()
    world.advance(100 * 600)
    assert world.voyages.voyage["status"] == "arrived"
    report = world.voyages.arrival_report(world)
    objective_row = next(line for line in report if contract["offer_id"] in line)
    assert "visit the harbor office to settle" in objective_row
    assert contract["status"] == "active"

    world.player.x, world.player.y, world.player.z = 42, 23, 0
    world.refresh()
    build_port(world)
    world.economy.deliver_contract(world, contract["offer_id"])
    settled_report = world.voyages.arrival_report(world)
    objective_row = next(line for line in settled_report if contract["offer_id"] in line)
    assert "current ledger: completed" in objective_row


def test_voyage_persists_midroute_and_reloads_as_an_ocean_ship(tmp_path):
    world = quiet_world()
    world.voyages.confirm(world, "freeport")
    harbor = world.economy.remember_harbor(world)
    world.enter_sea()
    world.advance(900 * 100)
    path = tmp_path / "voyage.json"
    world.save(path)
    restored = World.load(path)
    assert restored.at_sea
    assert restored.voyages.snapshot() == world.voyages.snapshot()
    assert restored.npcs.snapshot() == world.npcs.snapshot()
    assert restored.economy.harbors["verdant"] == harbor
    assert world.economy.harbors["verdant"] == harbor
    assert restored.ship.seconds == world.ship.seconds
    restored.advance(100 * 10)
    assert restored.voyages.voyage["elapsed_seconds"] == 910


def test_voyage_chunking_and_midroute_save_resume_preserve_simulation_state(tmp_path):
    uninterrupted, resumed = quiet_world(), quiet_world()
    for world in (uninterrupted, resumed):
        world.npcs.sighted.update(ship["id"] for ship in world.npcs.ships)
        world.voyages.confirm(world, "freeport")
        world.enter_sea()

    # Identical one-hour elapsed simulation: one large advance versus two
    # chunks with a real save/reload at the midpoint.
    uninterrupted.advance(3600 * 100)
    resumed.advance(1800 * 100)
    path = tmp_path / "mid-voyage-continuation.json"
    resumed.save(path)
    resumed = World.load(path)
    resumed.advance(1800 * 100)

    assert resumed.clock.total_ticks == uninterrupted.clock.total_ticks
    assert resumed.ship.snapshot() == uninterrupted.ship.snapshot()
    assert resumed.voyages.snapshot() == uninterrupted.voyages.snapshot()
    assert resumed.npcs.snapshot() == uninterrupted.npcs.snapshot()
    assert resumed.economy.snapshot(resumed) == uninterrupted.economy.snapshot(uninterrupted)
    assert resumed.ship_model.snapshot() == uninterrupted.ship_model.snapshot()
    assert (resumed.at_sea, resumed.player.x, resumed.player.y, resumed.player.z) == (
        uninterrupted.at_sea, uninterrupted.player.x, uninterrupted.player.y, uninterrupted.player.z)


def test_npc_trade_and_hunt_are_persistent_autonomous_actions():
    fleets = NpcFleetSystem()
    world = quiet_world()
    trader = fleets.ships[0]
    trader["node"] = trader["destination"] = "cinder"
    trader["cargo"] = {"grain": 3}
    silver = trader["silver"]
    sell_quote = world.economy.quote("dara", "grain", 3, False)
    iron_stock = world.economy.markets["brann"]["stock"]["iron"]
    buy_cost = sum(world.economy.price("brann", "iron", True, iron_stock - i) for i in range(2))
    dara_stock = world.economy.markets["dara"]["stock"].get("grain", 0)
    fleets._activity(world, trader)
    assert trader["cargo"]["grain"] == 0 and trader["cargo"]["iron"] == 2
    assert trader["silver"] == silver + sell_quote - buy_cost
    assert world.economy.markets["dara"]["stock"]["grain"] == dara_stock + 3
    assert world.economy.markets["brann"]["stock"]["iron"] == iron_stock - 2
    assert any("sold" in event for event in fleets.events)
    assert any("bought" in event for event in fleets.events)
    saved = NpcFleetSystem.restore(fleets.snapshot())
    assert saved.snapshot() == fleets.snapshot()


def test_npc_slave_sale_and_purchase_transfer_headcount_without_spawning():
    fleets = NpcFleetSystem()
    world = quiet_world()
    seller = fleets.ships[2]
    buyer = fleets.ships[0]
    seller["node"] = "freeport"
    seller["captives"] = 2
    buyer["node"] = "cinder"
    before = (fleets.market["enslaved_people"] + seller["captives"] + buyer["captives"]
              + sum(s["crew"] for s in fleets.ships))
    fleets._activity(world, seller)
    after_sale = (fleets.market["enslaved_people"] + seller["captives"] + buyer["captives"]
                  + sum(s["crew"] for s in fleets.ships))
    assert after_sale == before
    assert seller["captives"] == 0
    assert any("sold 2 captive" in event for event in fleets.events)
    # A second NPC buys one person from the same finite market pool.
    buyer["node"] = "freeport"
    buyer["mission"] = "trade"
    seller["node"] = "deepwater"
    market_before = fleets.market["enslaved_people"]
    people_before = buyer["captives"]
    fleets._activity(world, buyer)
    assert fleets.market["enslaved_people"] == market_before - 1
    assert buyer["captives"] == people_before + 1
    assert any("purchased 1 enslaved" in event for event in fleets.events)


def test_pirate_hunt_takes_a_survivor_from_the_target_crew():
    fleets = NpcFleetSystem()
    world = quiet_world()
    pirate = fleets.ships[2]
    marine = fleets.ships[1]
    pirate["node"] = pirate["destination"] = "navy_lane"
    marine["node"] = "navy_lane"
    headcount = sum(s["crew"] + s["captives"] for s in fleets.ships) + fleets.market["enslaved_people"]
    fleets._activity(world, pirate)
    after = sum(s["crew"] + s["captives"] for s in fleets.ships) + fleets.market["enslaved_people"]
    assert marine["crew"] == 9 and pirate["captives"] == 2
    assert after == headcount
    assert any("surviving crew member" in event for event in fleets.events)


def test_npc_system_advances_during_port_time_without_a_player_voyage():
    world = quiet_world()
    before = world.npcs.hour
    world.advance(3600 * 100)
    assert world.npcs.hour > before
    assert world.npcs.ships != NpcFleetSystem().ships


def test_voyage_save_rejects_invalid_route_or_sword_roster(tmp_path):
    world = quiet_world()
    world.voyages.confirm(world, "cinder")
    world.enter_sea()
    path = tmp_path / "invalid-voyage.json"
    world.save(path)
    data = json.loads(path.read_text())
    data["voyage"]["voyage"]["route"] = ["verdant", "freeport", "cinder"]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        World.load(path)


def test_npc_headcount_and_trade_history_roundtrip(tmp_path):
    world = quiet_world()
    world.npcs.events.append("Synthetic ledger entry")
    path = tmp_path / "npc.json"
    world.save(path)
    loaded = World.load(path)
    assert loaded.npcs.snapshot() == world.npcs.snapshot()
    assert loaded.npcs.market["enslaved_people"] + sum(s["crew"] + s["captives"] for s in loaded.npcs.ships) == 40


def test_npc_save_rejects_created_or_lost_people(tmp_path):
    world = quiet_world()
    path = tmp_path / "bad-npc.json"
    world.save(path)
    data = json.loads(path.read_text())
    data["npcs"]["market"]["enslaved_people"] += 1
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="headcount"):
        World.load(path)


def test_voyage_ui_confirms_charted_route_and_returns_to_sailing_scene():
    from pirates.management import voyage_menu
    class Scripted:
        def __init__(self, answers):
            self.answers = iter(answers)
            self.titles = []
        def render_list(self, _world, title, *_args, **_kwargs):
            self.titles.append(title)
        def choose(self, _count):
            return next(self.answers)
        def wait_any(self):
            pass
    world = quiet_world()
    ui = Scripted([4, 0, 0])
    voyage_menu(ui, world)
    assert world.at_sea and world.voyages.voyage["status"] == "underway"
    assert "Review voyage order" in ui.titles


def test_departure_review_binds_active_delivery_to_selected_chart_route():
    from pirates.management import voyage_menu
    from pirates.economy import CATALOG

    class Scripted:
        def __init__(self):
            self.panels = []

        def render_list(self, _world, title, rows, *_args, **_kwargs):
            self.panels.append((title, list(rows)))

        def choose(self, count):
            title = self.panels[-1][0]
            if title.startswith("Voyage command"):
                return 4
            if title == "Choose destination; officers will review the plan":
                return 0  # Cinder Quay is the first destination from Verdant Reach.
            if title == "Choose a chart route":
                return 1
            if title == "Commit this voyage order?":
                return 0
            return None

        def wait_any(self):
            pass

    world = quiet_world()
    world.player.x, world.player.y = 42, 23
    world.refresh()
    offer = next(row for row in world.economy.contract_offers("verdant", world.clock.day)
                 if row["destination"] == "cinder" and row["item"] == "bluegrain")
    world.economy.accept_contract(world, offer["offer_id"])
    merchant = MERCHANTS[offer["issuer"]]
    world.player.x, world.player.y = merchant["at"][0] + 1, merchant["at"][1]
    world.refresh()
    world.economy.trade(world, offer["issuer"], offer["item"], offer["quantity"], True)

    options = world.voyages.route_options("verdant", "cinder")
    ui = Scripted()
    voyage_menu(ui, world)

    assert world.at_sea and world.voyages.voyage["route"] == options[1]["route"]
    review = next(rows for title, rows in ui.panels if title == "Review voyage order")
    text = "\n".join(review)
    assert "DELIVERY OBJECTIVE" in text
    assert CATALOG[offer["item"]]["name"] in text
    assert str(offer["quantity"]) in text and str(offer["deadline_day"]) in text
    assert str(offer["reward"]) in text and "settled later at the destination harbor office" in text


def test_voyage_ui_resolves_nearby_ship_in_normal_game_scene():
    from pirates.management import voyage_menu
    class Scripted:
        def __init__(self, answers):
            self.answers = iter(answers)
        def render_list(self, *_args, **_kwargs):
            pass
        def choose(self, _count):
            return next(self.answers)
        def wait_any(self):
            pass
    world = quiet_world()
    world.voyages.confirm(world, "deepwater")
    world.enter_sea()
    world.voyages.voyage["status"] = "encounter"
    world.voyages.voyage["encounter"] = {"name": "Marine cutter", "faction": "World Government"}
    voyage_menu(Scripted([6, 1, None]), world)
    assert world.voyages.voyage["status"] == "underway"
    assert world.at_sea


def test_pirate_sighting_breaches_real_hull_and_starts_shipboard_combat():
    from pirates.management import voyage_menu

    class Scripted:
        def __init__(self, answers):
            self.answers = iter(answers)
        def render_list(self, *_args, **_kwargs):
            pass
        def choose(self, _count):
            return next(self.answers)
        def wait_any(self):
            pass

    world = quiet_world()
    world.voyages.confirm(world, "deepwater")
    world.enter_sea()
    world.voyages.voyage["status"] = "encounter"
    world.voyages.voyage["encounter"] = {
        "id": "boarding_test", "name": "Rogue pirate brig", "faction": "Free Syndicate",
        "threat": "pirates", "crew": 6,
    }
    world.set_lockdown(True)
    voyage_menu(Scripted([6, 1]), world)
    assert world.combat.active and len(world.combat.enemies) == 2
    assert world.hazards.breaches
    assert world.voyages.voyage["status"] == "encounter"
    for enemy in world.combat.enemies:
        enemy.hp = 0
    world.combat.exit(world)
    assert not world.combat.active
    assert world.voyages.voyage["status"] == "underway"
    assert world.hazards.breaches


def test_turnback_at_a_port_node_resolves_sighting_and_save_validates(tmp_path):
    world = quiet_world()
    world.voyages.confirm(world, "deepwater")
    world.voyages.position = "verdant"
    world.voyages.voyage["status"] = "encounter"
    world.voyages.voyage["encounter"] = {"name": "Patrol", "faction": "World Government"}
    world.enter_sea()
    world.voyages.turn_back(world)
    assert world.voyages.voyage["status"] == "arrived"
    assert not world.at_sea and world.economy.port == "verdant"
    path = tmp_path / "returned.json"
    world.save(path)
    assert World.load(path).voyages.voyage["status"] == "arrived"


def test_v_key_routes_to_voyage_command_without_touching_engine_snapshot():
    import tcod.event
    import sys
    sys.path.insert(0, "engine")
    import play
    assert play._pirates_world_action_for_key(tcod.event.KeySym.V) == ("voyage",)


def test_arrival_restores_destination_harbor_memory_and_ground_items():
    world = quiet_world()
    world.economy.harbors["cinder"]["explored"][12][30] = True
    world.economy.harbors["cinder"]["ground"]["30,12"] = [{
        "id": "beans", "name": "Dried beans", "weight_g": 2000, "bulk_ml": 2600, "quantity": 1}]
    world.voyages.confirm(world, "cinder")
    clear_squall_for_ordinary_scenario(world.voyages.voyage)
    world.voyages.voyage["duration_seconds"] = 5
    world.enter_sea()
    world.advance(100 * 5)
    assert not world.at_sea and world.economy.port == "cinder"
    assert world.game_map.explored[12, 30]
    assert world.ground_items[(30, 12)][0]["id"] == "beans"
    assert world.game_map.is_walkable(50, 23)  # gangplank connects the harbor to the ship


def cannon_world():
    world = quiet_world()
    world.voyages.confirm(world, "deepwater")
    target = world.npcs.ships[1]
    world.voyages.voyage["status"] = "encounter"
    world.voyages.voyage["encounter"] = deepcopy(target)
    world.voyages.assign_role(world, "captain", "tavi")
    world.ship.ammo["port_cannon"] = 1
    world.ship.ammo["starboard_cannon"] = 1
    world.voyages.begin_naval_combat(world)
    return world


def test_ship_cannon_exchange_is_deterministic_and_conserves_both_sides_shot():
    first, second = cannon_world(), cannon_world()
    for world in (first, second):
        target = next(ship for ship in world.npcs.ships if ship["id"] == "government_1")
        before_shot = target["cargo"]["shot"]
        world.voyages.cannon_exchange(world, "port_cannon")
        target = next(ship for ship in world.npcs.ships if ship["id"] == "government_1")
        assert world.ship.ammo["port_cannon"] == 0
        assert target["cargo"]["shot"] == before_shot - 1
    a, b = first.voyages.voyage["encounter"]["combat"], second.voyages.voyage["encounter"]["combat"]
    assert a == b
    assert first.ship.hull == second.ship.hull
    assert first.npcs.ships[1]["condition"] == second.npcs.ships[1]["condition"]


def test_voyage_sighting_ui_opens_a_commandable_cannon_exchange():
    from pirates.management import voyage_menu

    class Scripted:
        def __init__(self, answers):
            self.answers = iter(answers)
        def render_list(self, *_args, **_kwargs):
            pass
        def choose(self, _count):
            return next(self.answers)
        def wait_any(self):
            pass

    world = cannon_world()
    world.voyages.voyage["encounter"].pop("combat")
    # Sightings open the saved encounter, then select cannons by their real loaded state.
    voyage_menu(Scripted([6, 0, 0, None]), world)
    assert world.voyages.voyage["status"] == "encounter"
    assert world.voyages.voyage["encounter"]["combat"]["exchange"] == 1
    assert world.ship.ammo["port_cannon"] == 0


def test_cannon_exchange_saves_and_resumes_the_same_deterministic_battle(tmp_path):
    world, uninterrupted = cannon_world(), cannon_world()
    world.voyages.cannon_exchange(world, "port_cannon")
    uninterrupted.voyages.cannon_exchange(uninterrupted, "port_cannon")
    path = tmp_path / "mid-cannon-fight.json"
    world.save(path)
    restored = World.load(path)
    assert restored.voyages.voyage["encounter"]["combat"] == world.voyages.voyage["encounter"]["combat"]
    restored.voyages.cannon_exchange(restored, "starboard_cannon")
    uninterrupted.voyages.cannon_exchange(uninterrupted, "starboard_cannon")
    assert restored.voyages.voyage["encounter"]["combat"] == uninterrupted.voyages.voyage["encounter"]["combat"]
    assert restored.ship.hull == uninterrupted.ship.hull
    assert restored.npcs.ships[1] == uninterrupted.npcs.ships[1]


def test_cannon_requires_loaded_installed_gun_and_enemy_can_be_forced_to_withdraw():
    world = cannon_world()
    world.ship.ammo["port_cannon"] = 0
    with pytest.raises(ValueError, match="loaded charge"):
        world.voyages.cannon_exchange(world, "port_cannon")
    world.ship.ammo["port_cannon"] = 1
    target = world.npcs.ships[1]
    target["condition"] = 21
    world.voyages.voyage["encounter"]["condition"] = 21
    combat = world.voyages.voyage["encounter"]["combat"]
    captain = world.ship.sailor(world.voyages.roles["captain"])
    chance = min(0.85, 0.3 + captain.mental["tactical_awareness"] * 0.0025
                 + captain.mental["leadership"] * 0.001)
    combat["seed"] = next(seed for seed in range(10000)
                          if world.voyages._combat_roll({**combat, "seed": seed}, 0, "player", "port_cannon") < chance)
    state = world.voyages.cannon_exchange(world, "port_cannon")
    assert state["status"] == "enemy_withdrew"
    assert target["condition"] <= 20
    assert target["destination"] in ("verdant", "cinder")
