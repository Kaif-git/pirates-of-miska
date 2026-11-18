import pytest

from sim.core.world import WorldState
from sim.config import load_config, validate_config
from sim.tools.batch_test import _actor_band_bounds, _actor_band_counts, _year_diagnostics


def test_world_records_daily_system_diagnostic_calls_without_snapshot_state():
    world = WorldState.create(0)
    world.run_until(40 * 24)

    assert world.system_diagnostics["economy"]["call_count"] == 40
    assert world.system_diagnostics["ai"]["last_fired_tick"] == 40 * 24
    snapshot = world.to_snapshot()
    assert "system_diagnostics" not in snapshot


def test_year_diagnostics_reports_system_calls_and_zero_call_system_names():
    world = WorldState.create(1)
    world.run_until(40 * 24)

    diagnostics, _ = _year_diagnostics(world, 0, 40 * 24, 0, {})

    assert diagnostics["systems"]["economy"]["call_count"] == 40
    assert diagnostics["systems"]["director"]["last_fired_tick"] == 40 * 24
    assert "director" not in diagnostics["system_zero_call_over_30_days"]
    assert diagnostics["alive_ships_by_owner_type"]
    assert diagnostics["events_by_class"]["unclassified"] == 0
    assert sum(diagnostics["events_by_class"][kind]
               for kind in ("routine", "narrative", "structural")) == sum(
                    event.tick > 0 for event in world.event_log)
    assert diagnostics["actor_bands"]["counts"]["merchant_ships"] == 8
    assert diagnostics["actor_bands"]["bounds"]["merchant_ships"] == {
        "minimum": 32, "target": 60, "maximum": 80,
    }


def test_actor_bands_count_only_living_capable_characters_and_active_ships():
    world = WorldState.create(5)
    counts = _actor_band_counts(world)
    assert counts["merchant_ships"] == 3
    assert counts["pirate_ships"] == 0
    assert counts["marine_ships"] == 0
    assert counts["major_captains"] + counts["legends"] == 3
    assert _actor_band_bounds(world)["legends"] == {
        "minimum": 2, "target": 3, "maximum": 6,
    }


def test_actor_budget_configuration_rejects_inverted_ranges():
    config = load_config()
    config["world"]["actors"]["ship_bands"]["merchant"]["target_per_island_milli"] = 500
    with pytest.raises(ValueError, match="actor ship budget: merchant"):
        validate_config(config)


def test_events_have_data_driven_class_and_contextual_weather_class():
    world = WorldState.create(2)
    root = world.event_log.by_type("WORLD_GENERATED")[0]
    economy_event = world.event_log.by_type("ISLAND_ECONOMY_INITIALIZED")[0]
    assert root.event_class == "structural"
    assert economy_event.event_class == "routine"

    weather = world.event_log.append(
        tick=0, event_type="NATURAL_EVENT",
        data={"kind": "WEATHER_CHANGED", "region_id": "region_001"},
        causes_tags=["weather-test"],
    )
    assert weather.event_class == "routine"
    record = weather.to_record()
    assert record["class"] == "routine"
    restored = type(world.event_log).from_records(
        world.event_log.root_event_types, world.event_log.to_records(),
        world.config["event_rules"]["class_by_type"],
    )
    assert restored.get(weather.id).event_class == "routine"
    with pytest.raises(ValueError, match="Event class"):
        world.event_log.append(tick=0, event_type="WORLD_GENERATED",
                               causes_tags=["invalid-class-test"], event_class="")


def test_major_agents_receive_scheduled_decisions_without_external_event_wakes():
    world = WorldState.create(3)
    world.decisions.event_cursor = len(world.event_log)

    completed = world.decisions.process_scheduled_agents(world)

    assert completed
    actions = world.event_log.by_type("AGENT_ACTION")
    assert len(actions) == len(completed)
    assert all((world.entities.get(event.actors[0]["entity_id"])
                or world.archive.get(event.actors[0]["entity_id"]))["tier"]
               in {"MAJOR", "LEGEND"} for event in actions)
