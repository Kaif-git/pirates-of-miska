import json
import argparse
import builtins
from types import SimpleNamespace

import pytest

from pirates.game import MVP_SAVE_PATH, World
from pirates.economy import (CATALOG, LEGACY_MERCHANT_IDS, LEGACY_PORT_IDS,
                             MERCHANTS, PORTS)
from pirates.management import sea_map_rows
from pirates.scenario import load_scenario
from pirates.trading_ui import (market_observation_rows, market_rows, merchant_menu,
                                port_guide_rows, port_menu)
from pirates.voyage import SEA_NODES
from pirates.management import voyage_menu
import play


def test_authored_scenario_definition_is_versioned_and_keeps_world_simulation_off():
    scenario = load_scenario("lawful-trader-mvp")
    assert scenario["version"] == 1
    assert scenario["entry_port"] == "verdant"
    assert len(scenario["ports"]) >= 3
    assert set(scenario["policies"].values()) == {False}


def test_scenario_validation_checks_trade_ports_and_chart_nodes():
    ports = {"verdant": {}, "cinder": {}, "freeport": {}}
    nodes = {
        "verdant": {"port": "verdant"}, "cinder": {"port": "cinder"},
        "freeport": {"port": "freeport"},
    }
    assert load_scenario("lawful-trader-mvp", ports=ports, sea_nodes=nodes)["ports"] == [
        "verdant", "cinder", "freeport"]
    with pytest.raises(ValueError, match="unknown trade port"):
        load_scenario("lawful-trader-mvp", ports={"verdant": {}, "cinder": {}})
    with pytest.raises(ValueError, match="no chart node"):
        load_scenario("lawful-trader-mvp", ports=ports,
                      sea_nodes={"verdant": nodes["verdant"], "cinder": nodes["cinder"]})


def test_scenario_rejects_unknown_ids_and_mutable_return_does_not_change_source():
    with pytest.raises(ValueError, match="Unknown authored scenario"):
        load_scenario("../../saves/captain")
    first = load_scenario("lawful-trader-mvp")
    first["ports"].clear()
    assert load_scenario("lawful-trader-mvp")["ports"] == ["verdant", "cinder", "freeport"]


def test_scenario_policy_cannot_enable_generated_or_strategic_systems(tmp_path, monkeypatch):
    from pirates import scenario as scenario_module

    path = tmp_path / "lawful-trader-mvp.json"
    path.write_text(json.dumps({
        "id": "lawful-trader-mvp", "version": 1, "name": "Bad",
        "entry_port": "verdant", "ports": ["verdant", "cinder", "freeport"],
        "policies": {"generated_prehistory": True, "strategic_society": False,
                     "npc_destination_activity": False, "hostile_population_trade": False},
    }), encoding="utf-8")
    monkeypatch.setattr(scenario_module, "SCENARIO_DIR", tmp_path)
    with pytest.raises(ValueError, match="disconnected"):
        load_scenario("lawful-trader-mvp")


def test_authored_mvp_factory_is_explicit_static_and_uses_its_own_slot(monkeypatch):
    monkeypatch.setattr(World, "new_generated", classmethod(
        lambda cls, *args, **kwargs: (_ for _ in ()).throw(AssertionError("generated factory called"))))
    world = World.new_authored_mvp()
    assert world.scenario_id == "lawful-trader-mvp" and world.scenario_version == 1
    assert world.save_path == MVP_SAVE_PATH
    assert world.generation_manifest is None
    assert world.society.mode == "authored"
    assert world.npcs.market["enslaved_people"] == 0
    assert world.npcs.population_total == sum(ship["crew"] for ship in world.npcs.ships) == 34
    assert all(ship["captives"] == 0 for ship in world.npcs.ships)
    assert load_scenario("lawful-trader-mvp", ports=PORTS, sea_nodes=SEA_NODES)["ports"] == [
            "verdant", "cinder", "freeport"]


def test_authored_mvp_factory_never_imports_headless_simulation(monkeypatch):
    original_import = builtins.__import__

    def reject_simulation(name, *args, **kwargs):
        if name == "sim" or name.startswith("sim."):
            raise AssertionError(f"headless simulation imported: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_simulation)
    assert World.new_authored_mvp().scenario_id == "lawful-trader-mvp"


def test_legacy_and_authored_worlds_can_coexist_without_shared_mutable_scenario_state():
    legacy = World()
    authored = World.new_authored_mvp()
    authored.economy.money = 123
    authored.npcs.ships[0]["node"] = "shoals"
    assert legacy.scenario_id is None
    assert legacy.economy.money == 600
    assert legacy.npcs.ships[0]["node"] == "verdant"
    assert legacy.npcs.market["enslaved_people"] == 5
    assert authored.npcs.market["enslaved_people"] == 0


def test_authored_mvp_keeps_local_fleet_motion_but_skips_destination_and_society_activity(monkeypatch):
    world = World.new_authored_mvp()
    ship = world.npcs.ships[0]
    ship["destination"] = ship["node"]
    moving_ship = world.npcs.ships[1]
    original_node = moving_ship["node"]
    cargo_before = [dict(row["cargo"]) for row in world.npcs.ships]
    market_before = dict(world.npcs.market)
    society_before = (dict(world.society.relationships), list(world.society.news),
                      dict(world.society.decision_state))

    def forbidden_activity(*args, **kwargs):
        raise AssertionError("authored MVP destination activity must be disabled")

    monkeypatch.setattr(world.npcs, "_activity", forbidden_activity)
    monkeypatch.setattr(world.society, "advance", forbidden_activity)
    next_hour = world.npcs.hour + 1
    world.clock.total_ticks = next_hour * 360_000
    world.on_ship_second()
    assert world.npcs.hour == world.society.hour == next_hour
    assert ship["node"] != ship["destination"]
    assert moving_ship["node"] != original_node
    assert [dict(row["cargo"]) for row in world.npcs.ships] == cargo_before
    assert world.npcs.market == market_before
    assert (world.society.relationships, world.society.news,
            world.society.decision_state) == society_before


def test_authored_mvp_save_roundtrip_preserves_mode_and_rejects_cross_mode_load(tmp_path):
    authored_path = tmp_path / "lawful-trader-mvp.json"
    legacy_path = tmp_path / "captain.json"
    world = World.new_authored_mvp()
    world.save(authored_path)
    authored_bytes = authored_path.read_bytes()
    restored = World.load(authored_path, expected_scenario_id="lawful-trader-mvp")
    assert restored.scenario_id == "lawful-trader-mvp"
    assert restored.scenario_version == 1
    assert restored.save_path == authored_path
    assert restored.npcs.population_total == 34
    assert restored.npcs.market["enslaved_people"] == 0
    with pytest.raises(ValueError, match="different scenario"):
        World.load(authored_path, expected_scenario_id=None)
    assert authored_path.read_bytes() == authored_bytes

    World().save(legacy_path)
    legacy_bytes = legacy_path.read_bytes()
    with pytest.raises(ValueError, match="different scenario"):
        World.load(legacy_path, expected_scenario_id="lawful-trader-mvp")
    assert legacy_path.read_bytes() == legacy_bytes


def test_pre_scenario_v3_save_remains_a_legacy_world(tmp_path):
    path = tmp_path / "legacy-v3.json"
    World().save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = 3
    data.pop("scenario", None)
    data["economy"]["markets"] = {
        merchant: value for merchant, value in data["economy"]["markets"].items()
        if merchant in LEGACY_MERCHANT_IDS}
    data["economy"]["harbors"] = {
        port: value for port, value in data["economy"]["harbors"].items()
        if port in LEGACY_PORT_IDS}
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = World.load(path, expected_scenario_id=None)
    assert loaded.scenario_id is None
    assert loaded.npcs.market["enslaved_people"] == 5
    assert loaded.save_path == path
    assert set(loaded.economy.markets) > LEGACY_MERCHANT_IDS
    assert set(loaded.economy.harbors) > LEGACY_PORT_IDS


def test_authored_mvp_keeps_canonical_ship_time_and_market_updates():
    from tales_sim.clock import TICKS_PER_DAY

    world = World.new_authored_mvp()
    before_day = world.clock.day
    ship_seconds = world.ship.seconds
    relationships = dict(world.society.relationships)
    world.economy.markets["ada"]["stock"]["grain"] = 0
    world.clock.total_ticks = TICKS_PER_DAY - 100
    world.advance(100)
    assert world.clock.day == before_day + 1
    assert world.ship.seconds == ship_seconds + 1
    assert world.economy.last_day == world.clock.day
    assert world.economy.markets["ada"]["stock"]["grain"] > 0
    assert world.society.relationships == relationships


def test_authored_mvp_chart_does_not_reveal_unobserved_vessels_or_captive_market():
    world = World.new_authored_mvp()
    rows = sea_map_rows(world)
    chart = "\n".join(rows)
    for port in ("Verdant Cay", "Cinder Quay", "Freeport Exchange"):
        assert port in chart
    assert not any(ship["name"] in "\n".join(rows) for ship in world.npcs.ships)
    assert not any("captive market" in row.lower() for row in rows)
    ship = world.npcs.ships[0]
    world.npcs.sighted.add(ship["id"])
    world.npcs.sighting_reports[ship["id"]] = {
        "node": ship["node"], "observed_hour": world.npcs.hour,
        "crew": ship["crew"], "condition": ship["condition"],
    }
    rows = sea_map_rows(world)
    assert any(ship["name"] in row and "last seen" in row for row in rows)
    assert not any("captive market" in row.lower() for row in rows)


def test_authored_mvp_hides_contract_and_instant_passage_menu_options(monkeypatch):
    world = World.new_authored_mvp()
    observed = {}

    def choose(_renderer, _world, _title, options, *_args, **_kwargs):
        observed["options"] = list(options)
        return len(options) - 1

    monkeypatch.setattr("pirates.trading_ui.choose", choose)
    port_menu(object(), world)
    menu_text = " ".join(observed["options"]).lower()
    assert "contract" not in menu_text and "passage" not in menu_text
    with pytest.raises(ValueError, match="sea chart"):
        world.economy.travel(world, "cinder")


def test_authored_public_port_guide_lists_local_merchants_without_remote_quotes():
    world = World.new_authored_mvp()
    guide = "\n".join(port_guide_rows(world))
    for name in ("Verdant Cay", "Cinder Quay", "Freeport Exchange", "Nella Vale"):
        assert name in guide
    assert "not a live remote-price feed" in guide
    assert "No delivery contract or passage fee is required" in guide
    assert "remote stock" not in guide.lower()


def test_market_observations_are_explicit_local_bounded_snapshots_and_old_saves_migrate(tmp_path):
    world = World.new_authored_mvp()
    world.player.x, world.player.y = 31, 21
    world.refresh()
    start_ticks = world.clock.total_ticks
    assert world.economy.market_observations == {}

    # Shared render helpers and public map/guide browsing are observationally pure.
    market_rows(world, "ada")
    unknown_notes = market_observation_rows(world, "ada")
    guide = port_guide_rows(world)
    chart = sea_map_rows(world)
    assert world.economy.market_observations == {}
    assert world.clock.total_ticks == start_ticks
    assert any("unknown until inspected locally" in row for row in unknown_notes)
    assert any("quote notes: unknown" in row.lower() for row in guide)
    assert any("remote prices/stock unknown" in row for row in chart)

    observed = world.economy.observe_market(world, "ada")
    assert observed["source"] == "ada" and observed["port"] == "verdant"
    assert observed["observed_at_ticks"] == start_ticks
    assert observed["merchant_funds"] == 1500
    assert observed["goods"]["bluegrain"] == {
        "stock": world.economy.markets["ada"]["stock"]["bluegrain"],
        "ask": world.economy.price("ada", "bluegrain", True),
        "bid": world.economy.price("ada", "bluegrain", False),
    }

    # Merely remembering a quote never authorizes another island or a distant merchant.
    world.player.x, world.player.y = 76, 25
    with pytest.raises(ValueError, match="within two tiles"):
        world.economy.observe_market(world, "ada")
    assert world.economy.market_observations["ada"] == observed

    # Current remote market mutations cannot alter the remembered projection.
    world.economy.markets["ada"]["stock"]["bluegrain"] = 1
    world.economy.markets["ada"]["money"] = 2
    world.advance(100)
    notes = market_observation_rows(world, "ada")
    assert any("Day 1" in row and "ago" in row for row in notes)
    assert any("24 in stock then" in row for row in notes)
    assert any("purse then: 1500 Marks" in row for row in notes)
    chart = sea_map_rows(world)
    assert any("Verdant Cay: last inspected Ada Reed" in row for row in chart)
    assert not any("stock 1" in row or "purse 2" in row for row in chart)
    assert world.clock.total_ticks == start_ticks + 100

    path = tmp_path / "authored-market-notes.json"
    world.save(path)
    restored = World.load(path, expected_scenario_id="lawful-trader-mvp")
    assert restored.economy.market_observations == world.economy.market_observations
    assert restored.economy.market_observations["ada"]["goods"]["bluegrain"]["stock"] == 24
    assert restored.economy.markets["ada"]["stock"]["bluegrain"] == 1

    # Saves written before the optional knowledge field remain loadable as unknown.
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["economy"].pop("market_observations")
    path.write_text(json.dumps(saved), encoding="utf-8")
    migrated = World.load(path, expected_scenario_id="lawful-trader-mvp")
    assert migrated.economy.market_observations == {}
    assert "No price observation" in " ".join(market_observation_rows(migrated, "ada"))

    # A malformed provenance or future timestamp is rejected, not normalized into live knowledge.
    for mutate in (
        lambda row, ticks: row.update(source="bo"),
        lambda row, ticks: row.update(observed_at_ticks=ticks + 1),
        lambda row, ticks: row["goods"].update(imaginary={"stock": 1, "ask": 1, "bid": 1}),
    ):
        invalid = json.loads(path.read_text(encoding="utf-8"))
        invalid["economy"]["market_observations"] = json.loads(
            json.dumps(world.economy.market_observations))
        mutate(invalid["economy"]["market_observations"]["ada"], invalid["ticks"])
        path.write_text(json.dumps(invalid), encoding="utf-8")
        with pytest.raises(ValueError, match="commercial observation|economy integer"):
            World.load(path, expected_scenario_id="lawful-trader-mvp")


def test_only_a_fresh_local_inspection_replaces_a_remembered_snapshot():
    world = World.new_authored_mvp()
    world.player.x, world.player.y = 31, 21
    world.refresh()
    original = world.economy.observe_market(world, "ada")

    world.advance(100)
    world.economy.markets["ada"]["stock"]["bluegrain"] = 3
    world.economy.markets["ada"]["money"] = 25
    remembered = market_observation_rows(world, "ada")
    assert any("24 in stock then" in row for row in remembered)
    assert any("purse then: 1500 Marks" in row for row in remembered)

    refreshed = world.economy.observe_market(world, "ada")
    assert refreshed["observed_at_ticks"] == original["observed_at_ticks"] + 100
    assert refreshed["merchant_funds"] == 25
    assert refreshed["goods"]["bluegrain"]["stock"] == 3
    assert world.economy.market_observations["ada"] == refreshed


def test_authored_midpassage_reload_preserves_observation_trade_and_continuation(tmp_path):
    world = World.new_authored_mvp()
    world.player.x, world.player.y = 31, 21
    world.refresh()
    observed = world.economy.observe_market(world, "ada")
    world.economy.trade(world, "ada", "bluegrain", 2, True)
    world.voyages.confirm(world, "freeport")
    world.enter_sea()
    world.advance(6000)
    assert world.voyages.voyage["status"] == "underway"
    assert world.economy.cargo == {"bluegrain": 2}

    path = tmp_path / "authored-midpassage.json"
    world.save(path)
    restored = World.load(path, expected_scenario_id="lawful-trader-mvp")
    assert restored.economy.market_observations["ada"] == observed
    assert restored.economy.cargo == {"bluegrain": 2}
    assert restored.economy.markets["ada"]["stock"]["bluegrain"] == 22
    assert restored.voyages.voyage["status"] == "underway"
    assert restored.voyages.voyage["elapsed_seconds"] == world.voyages.voyage["elapsed_seconds"]

    world.advance(6000)
    restored.advance(6000)
    assert restored.clock.total_ticks == world.clock.total_ticks
    assert restored.economy.snapshot(restored) == world.economy.snapshot(world)
    assert restored.voyages.snapshot() == world.voyages.snapshot()
    assert restored.ship.snapshot() == world.ship.snapshot()


def test_merchant_price_list_menu_is_the_explicit_observation_action(monkeypatch):
    world = World.new_authored_mvp()
    world.player.x, world.player.y = 31, 21
    world.refresh()
    answers = iter((3, 4))  # Inspect prices, then leave the merchant.
    shown = []
    monkeypatch.setattr("pirates.trading_ui.choose", lambda *_args, **_kwargs: next(answers))
    monkeypatch.setattr("pirates.trading_ui.panel",
                        lambda _renderer, _world, title, rows, *_args, **_kwargs:
                        shown.append((title, list(rows))))

    merchant_menu(object(), world, "ada")

    assert world.economy.market_observations["ada"]["observed_at_ticks"] == world.clock.total_ticks
    assert shown and shown[0][0] == "Prices / trading terms"
    assert any("bluegrain" in row.lower() or "blue grain" in row.lower() for row in shown[0][1])


def test_authored_port_menu_opens_read_only_remembered_market_notes(monkeypatch):
    world = World.new_authored_mvp()
    world.player.x, world.player.y = 31, 21
    world.refresh()
    world.economy.observe_market(world, "ada")
    calls = iter((1, 0, 3))  # Notes, Ada Reed, then Return from the port menu.
    shown = []
    monkeypatch.setattr("pirates.trading_ui.choose", lambda *_args, **_kwargs: next(calls))
    monkeypatch.setattr("pirates.trading_ui.panel",
                        lambda _renderer, _world, title, rows, *_args, **_kwargs:
                        shown.append((title, list(rows))))
    before = world.clock.total_ticks

    port_menu(object(), world)

    assert shown and shown[0][0] == "Remembered market observation"
    assert any("not a live or executable offer" in row for row in shown[0][1])
    assert world.clock.total_ticks == before
    assert world.economy.market_observations["ada"]["merchant_funds"] == 1500


def test_legacy_price_list_keeps_its_existing_behavior_without_authored_notes(monkeypatch):
    world = World()
    world.player.x, world.player.y = 31, 21
    world.refresh()
    calls = iter((3, 4))
    monkeypatch.setattr("pirates.trading_ui.choose", lambda *_args, **_kwargs: next(calls))
    monkeypatch.setattr("pirates.trading_ui.panel", lambda *_args, **_kwargs: None)

    merchant_menu(object(), world, "ada")

    assert world.economy.market_observations == {}


def test_authored_sidebar_and_controls_fit_small_native_viewport():
    renderer = play.PiratesRenderer()
    renderer.width, renderer.height = 32, 30
    renderer.tile_width, renderer.tile_height = 20, 20
    world = World.new_authored_mvp()
    segments = renderer._sidebar_lines(world, 18, 30)
    sidebar_text = [row[0][0] for row in segments]
    assert any(line.startswith("Food 30kg | Water 60L | Planks ") for line in sidebar_text)
    controls = renderer._status_lines(world)
    assert len(controls) == 2
    assert all(len(line) <= 38 for line in controls)
    assert controls[1] == "Arrows move | Enter | F2 crew | V"
    assert "�" not in controls[0]


def test_authored_voyage_menu_selects_a_trading_port_without_a_fare(monkeypatch):
    world = World.new_authored_mvp()
    calls = []

    def choose(_renderer, _world, title, rows, *_args, **_kwargs):
        calls.append((title, list(rows)))
        if title.startswith("Voyage command"):
            return 4
        if title.startswith("Choose destination"):
            assert rows == ["Cinder Quay", "Freeport Exchange"]
            return rows.index("Freeport Exchange")
        if title.startswith("Choose a chart route"):
            return 0
        if title.startswith("Commit this voyage"):
            return 0
        return None

    monkeypatch.setattr("pirates.management.choose", choose)
    monkeypatch.setattr("pirates.management.panel", lambda *_args, **_kwargs: None)
    money = world.economy.money
    voyage_menu(object(), world)
    assert world.voyages.voyage["destination"] == "freeport"
    assert world.voyages.voyage["status"] == "underway"
    assert world.at_sea
    assert world.economy.money == money
    assert not any("shoals" in row.lower() or "deepwater" in row.lower()
                   for title, rows in calls if title.startswith("Choose destination") for row in rows)


def test_authored_voyage_menu_commits_the_selected_chart_alternative(monkeypatch):
    world = World.new_authored_mvp()
    options = world.voyages.route_options("verdant", "cinder")
    assert len(options) == 2
    observed_route_rows = []

    def choose(_renderer, _world, title, rows, *_args, **_kwargs):
        if title.startswith("Voyage command"):
            return 4
        if title.startswith("Choose destination"):
            return rows.index("Cinder Quay")
        if title.startswith("Choose a chart route"):
            observed_route_rows.extend(rows)
            return 1
        if title.startswith("Commit this voyage"):
            return 0
        return None

    monkeypatch.setattr("pirates.management.choose", choose)
    monkeypatch.setattr("pirates.management.panel", lambda *_args, **_kwargs: None)
    money = world.economy.money
    voyage_menu(object(), world)
    assert len(observed_route_rows) == 2
    assert world.voyages.voyage["route"] == options[1]["route"]
    assert world.economy.money == money


def test_authored_launch_buy_sail_sell_at_freeport_save_reload_and_sail_again(tmp_path):
    world = World.new_authored_mvp()
    starting_money = world.economy.money
    purse_total = lambda economy: (economy.money + economy.treasury
                                   + sum(row["money"] for row in economy.markets.values()))
    opening_purses = purse_total(world.economy)

    def trade_at(merchant_id, item_id, quantity, buying):
        x, y = MERCHANTS[merchant_id]["at"]
        world.player.x, world.player.y = x + 1, y
        world.refresh()
        return world.economy.trade(world, merchant_id, item_id, quantity, buying)

    def sail_to(destination):
        world.voyages.confirm(world, destination)
        world.enter_sea()
        voyage = world.voyages.voyage
        voyage["duration_seconds"] = 1
        if voyage["storm_event"]:
            voyage["storm_event"].update({"triggered": False,
                                          "trigger_roll": voyage["storm_event"]["chance_percent"]})
        for _ in range(10):
            if voyage["status"] != "underway":
                break
            world.advance(100)
        assert voyage["status"] == "arrived"
        assert world.economy.port == SEA_NODES[destination]["port"]

    assert "nella" in CATALOG["grain"]["market_buyers"]
    trade_at("ada", "grain", 5, True)
    assert world.economy.cargo["grain"] == 5
    sail_to("freeport")
    # Arrival has no report, acknowledgment or settlement gate: the captain trades first.
    trade_at("nella", "grain", 5, False)
    assert "grain" not in world.economy.cargo
    arrival_rows = world.voyages.arrival_report(world)
    assert any("No arrival settlement is required" in row for row in arrival_rows)
    assert not any("visit the harbor office to settle" in row for row in arrival_rows)
    assert world.economy.money > starting_money
    assert world.economy.markets["nella"]["stock"]["grain"] == 5
    assert purse_total(world.economy) == opening_purses

    path = tmp_path / "lawful-trader-mvp.json"
    world.save(path)
    restored = World.load(path, expected_scenario_id="lawful-trader-mvp")
    assert restored.economy.port == "freeport"
    assert restored.economy.markets["nella"]["stock"]["grain"] == 5
    assert set(restored.economy.harbors) == set(PORTS)
    assert any(any(row) for row in restored.economy.harbors["freeport"]["explored"])
    world = restored
    sail_to("cinder")
    assert world.economy.port == "cinder"


def _launch_args(**overrides):
    values = {"seed": None, "npc_people": None, "npc_organizations": None,
              "npc_vessels": None, "history_days": None, "new": False,
              "scenario": "lawful-trader-mvp", "replace_slot": False,
              "smoke_render": False}
    values.update(overrides)
    return SimpleNamespace(**values)


def test_launch_scenario_uses_separate_slot_for_new_and_resume(tmp_path, monkeypatch):
    slot = tmp_path / "lawful-trader-mvp.json"
    monkeypatch.setattr(play, "MVP_SAVE_PATH", slot)
    parser = argparse.ArgumentParser()
    created = play.resolve_launch_world(_launch_args(new=True), parser)
    assert created.scenario_id == "lawful-trader-mvp"
    assert created.save_path == slot
    created.save()
    resumed = play.resolve_launch_world(_launch_args(), parser)
    assert resumed.scenario_id == "lawful-trader-mvp"
    assert resumed.save_path == slot


def test_launch_scenario_refuses_existing_slot_without_explicit_replace(tmp_path, monkeypatch):
    slot = tmp_path / "lawful-trader-mvp.json"
    slot.write_text("protected user data", encoding="utf-8")
    monkeypatch.setattr(play, "MVP_SAVE_PATH", slot)
    parser = argparse.ArgumentParser()
    with pytest.raises(SystemExit):
        play.resolve_launch_world(_launch_args(new=True), parser)
    assert slot.read_text(encoding="utf-8") == "protected user data"
    replacement = play.resolve_launch_world(_launch_args(new=True, replace_slot=True), parser)
    assert replacement.scenario_id == "lawful-trader-mvp"
    assert slot.read_text(encoding="utf-8") == "protected user data"


def test_default_new_launch_still_uses_generated_factory(monkeypatch, tmp_path):
    monkeypatch.setattr(play, "SAVE_PATH", tmp_path / "captain.json")
    calls = []
    expected = object()
    monkeypatch.setattr(World, "new_generated", classmethod(
        lambda cls, seed=None, settings=None: calls.append((seed, settings)) or expected))
    args = _launch_args(scenario=None, new=True, seed="seed-7", history_days=0)
    assert play.resolve_launch_world(args, argparse.ArgumentParser()) is expected
    assert calls == [("seed-7", {"history_days": 0})]
