from sim.core.world import WorldState
from sim.tools.batch_test import _director_metrics, _politics_metrics


def test_director_injects_logged_rookie_vendetta_when_major_population_is_low():
    world = WorldState.create("director-low-majors", director_enabled=True)
    world.director.settings["check_interval_days"] = 1
    world.director.settings["minimum_major_characters"] = 100
    world.director.settings["injection_cooldown_days"] = 60
    world.director.settings["max_injections_per_year"] = 1
    world.run_until(24)

    injections = world.event_log.by_type("DIRECTOR_INJECTION")
    assert len(injections) == 1
    injection = injections[0]
    assert injection.causes_tags == ("director:low_major_population",)
    assert injection.data["condition"] == "rising_rookie_vendetta"
    rookie_id = next(
        entity_id for entity_id, person in world.entities.items()
        if person.get("kind") == "character" and person.get("tier") == "MAJOR"
        and person.get("dream", {}).get("kind") == "defeat_character"
        and person["dream"].get("stated_tick") == world.clock.tick
    )
    rookie = world.entities[rookie_id]
    assert rookie["dream"]["target_ref"] == injection.targets[0]["entity_id"]
    assert world.memories.grudge_evidence(
        world, rookie_id, injection.targets[0]["entity_id"])
    assert world.director.injections_by_year == {"1": 1}
    snapshot = world.to_snapshot()
    assert WorldState.from_snapshot(snapshot).to_snapshot() == snapshot


def test_director_off_records_health_without_injecting_conditions():
    world = WorldState.create("director-disabled", director_enabled=False)
    world.director.settings["check_interval_days"] = 1
    world.director.settings["minimum_major_characters"] = 100
    world.run_until(24)

    assert world.event_log.by_type("DIRECTOR_INJECTION") == ()
    assert world.director.metrics["day"] == 1
    assert world.director.metrics["major_plus_characters"] > 0
    assert WorldState.from_snapshot(world.to_snapshot()).director.snapshot() == \
        world.director.snapshot()


def test_default_configuration_keeps_director_off_for_baseline_runs():
    world = WorldState.create("director-default")

    assert world.config["director"]["enabled"] is False
    assert world.director.settings["enabled"] is False


def test_director_on_and_off_batch_metrics_enforce_liveness_and_density():
    for enabled in (False, True):
        world = WorldState.create(f"director-batch-{enabled}", director_enabled=enabled)
        world.run_until(365 * 24)
        metrics = _director_metrics(world, 1)
        assert metrics["liveness_pass"]
        assert metrics["density_pass"]
        assert metrics["director_enabled"] is enabled


def test_batch_heat_response_causality_handles_no_crossings_and_checks_ancestors():
    world = WorldState.create("batch-heat-causality")
    assert _politics_metrics(world)["heat_responses_have_threshold_ancestors"]

    root = world.event_log.by_type("WORLD_GENERATED")[0]
    orphan = WorldState.create("batch-heat-orphan-response")
    orphan_root = orphan.event_log.by_type("WORLD_GENERATED")[0]
    orphan.event_log.append(
        tick=orphan.clock.tick, event_type="GOVERNMENT_RESPONSE_FUNDED",
        location="isle_001", data={"test": True}, caused_by=[orphan_root.id],
        causes_tags=["test_orphan_response"], magnitude=0.3,
    )
    assert not _politics_metrics(orphan)["heat_responses_have_threshold_ancestors"]

    heat = world.event_log.append(
        tick=world.clock.tick, event_type="HEAT_CHANGED", location="isle_001",
        data={"test": True}, caused_by=[root.id],
        causes_tags=["test_heat"], magnitude=0.3,
    )
    crossing = world.event_log.append(
        tick=world.clock.tick, event_type="HEAT_THRESHOLD_CROSSED",
        location="isle_001", data={"test": True}, caused_by=[heat.id],
        causes_tags=["test_threshold"], magnitude=0.3,
    )
    assert not _politics_metrics(world)["heat_responses_have_threshold_ancestors"]

    response = world.event_log.append(
        tick=world.clock.tick, event_type="GOVERNMENT_RESPONSE_FUNDED",
        location="isle_001", data={"test": True}, caused_by=[crossing.id],
        causes_tags=["test_response"], magnitude=0.3,
    )
    assert response.id
    assert _politics_metrics(world)["heat_responses_have_threshold_ancestors"]
