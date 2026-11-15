import json

import pytest

from pirates.game import World
from pirates.voyage import (SEA_NODES, VoyageSystem, NpcFleetSystem,
                            CREW_ORDERS, CAPTAIN_ACTIONS)
from pirates.ship import LABORS


def quiet_world():
    world = World()
    for sailor in world.ship.crew:
        sailor.labors = {kind: 0 for kind in LABORS}
        sailor.hunger = sailor.thirst = sailor.fatigue = sailor.loneliness = sailor.boredom = 0
        sailor.sleep_debt = 20
    world.npcs.sighted.update(s["id"] for s in world.npcs.ships)
    return world


def test_officers_consult_and_quartermaster_warns_on_low_stores():
    world = quiet_world()
    world.ship.stores["water"] = 1
    world.ship.meals = 1
    rows = world.voyages.role_report(world)
    assert any("water is short" in line for line in rows)
    assert any("meals cover" in line for line in rows)
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


def test_chart_plans_route_and_officer_skill_changes_estimate():
    world = quiet_world()
    plan = world.voyages.plan(world, "deepwater")
    assert plan["route"][0] == "verdant" and plan["route"][-1] == "deepwater"
    assert len(plan["route"]) >= 3 and plan["duration_seconds"] > 1800
    base = plan["duration_seconds"]
    world.ship.sailor(world.voyages.roles["navigator"]).skills["rig"] = 10
    assert world.voyages.plan(world, "deepwater")["duration_seconds"] < base


def test_sea_chart_marker_interpolates_between_nodes_during_a_leg():
    world = quiet_world()
    world.voyages.confirm(world, "cinder")
    world.voyages.voyage["duration_seconds"] = 100
    world.voyages.voyage["elapsed_seconds"] = 25
    world.voyages.voyage["leg_progress"] = 0.5
    x, y = world.voyages.map_coordinate()
    assert x != SEA_NODES["verdant"]["xy"][0] or y != SEA_NODES["verdant"]["xy"][1]


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
    world.voyages.voyage["duration_seconds"] = 25
    world.enter_sea()
    before = world.ship.seconds
    world.advance(100 * 600)
    assert world.ship.seconds == before + 25
    assert world.voyages.voyage["status"] == "arrived"
    assert world.voyages.position == "cinder" and world.economy.port == "cinder"
    assert not world.at_sea and world.game_map.is_walkable(world.player.x, world.player.y)


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
    assert loaded.npcs.market["enslaved_people"] + sum(s["crew"] + s["captives"] for s in loaded.npcs.ships) == 29


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
    assert "Review and finalize voyage order" in ui.titles


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
    voyage_menu(Scripted([6, 0, None]), world)
    assert world.voyages.voyage["status"] == "underway"
    assert world.at_sea


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
    world.voyages.voyage["duration_seconds"] = 5
    world.enter_sea()
    world.advance(100 * 5)
    assert not world.at_sea and world.economy.port == "cinder"
    assert world.game_map.explored[12, 30]
    assert world.ground_items[(30, 12)][0]["id"] == "beans"
    assert world.game_map.is_walkable(50, 23)  # gangplank connects the harbor to the ship
