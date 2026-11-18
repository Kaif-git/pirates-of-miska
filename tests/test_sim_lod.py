from __future__ import annotations

import time

from sim.core.world import WorldState
from sim.systems.lod import LODSystem, load_lod_data


def _all_named_character_ids(world):
    return ({entity_id for entity_id, entity in world.entities.items()
             if entity.get("kind") == "character"}
            | {entity_id for entity_id, entity in world.archive.items()
               if entity.get("kind") == "character"}
            | set(world.lod.named_records))


def test_lod_catalog_and_world_scale_are_data_driven():
    data, digest = load_lod_data()
    world = WorldState.create("lod-catalog")

    assert digest and data["rules"]["coarse_step_days"] == 7
    assert len(world.geography.islands) >= 40
    assert set(world.lod.cohorts) == set(world.geography.islands)
    assert set(world.lod.regional_history) == set(world.geography.regions)


def test_daily_and_coarse_aggregate_metrics_match_exactly():
    daily = [
        {"trade_quantity_milli": 100 * day,
         "patrol_coverage_permille_days": (day * 137) % 1001,
         "incident_count": day % 3}
        for day in range(1, 8)
    ]
    fine = LODSystem.aggregate_daily(daily)
    coarse_parts = [LODSystem.aggregate_daily(daily[:3]),
                    LODSystem.aggregate_daily(daily[3:])]
    coarse = LODSystem.merge_aggregates(coarse_parts)

    assert {key: fine[key] for key in ("days", "trade_quantity_milli",
                                      "patrol_coverage_permille_days", "incident_count")} == {
        key: coarse[key] for key in ("days", "trade_quantity_milli",
                                    "patrol_coverage_permille_days", "incident_count")}


def test_same_world_region_metrics_agree_under_daily_and_weekly_lod_clocks():
    daily = WorldState.create("lod-fine-coarse-equivalence")
    weekly = WorldState.create("lod-fine-coarse-equivalence")
    daily.lod.data["rules"]["coarse_step_days"] = 1
    daily.run_until(28 * 24)
    weekly.run_until(28 * 24)

    fields = ("days", "trade_quantity_milli", "patrol_coverage_permille",
              "incident_count")
    for region_id in daily.geography.regions:
        def totals(world):
            rows = world.lod.regional_history[region_id]
            return {
                "days": sum(row["days"] for row in rows),
                "trade_quantity_milli": sum(row["trade_quantity_milli"] for row in rows),
                "patrol_coverage_permille": sum(
                    row.get("patrol_coverage_permille_days",
                            row["patrol_coverage_permille"] * row["days"])
                    for row in rows),
                "incident_count": sum(row["incident_count"] for row in rows),
            }
        assert {field: totals(daily)[field] for field in fields} == \
            {field: totals(weekly)[field] for field in fields}


def test_named_background_residents_transfer_from_census_and_snapshot():
    world = WorldState.create("lod-background-cohort")
    original_population = sum(row["population"] for row in world.economy.islands.values())
    world.run_until(365 * 24)

    records = world.lod.named_records
    assert len(records) == world.lod.data["rules"]["named_background_per_year"]
    assert sum(row["population"] for row in world.economy.islands.values()) == original_population - len(records)
    assert len(_all_named_character_ids(world)) >= 112 + len(records)
    assert all(record["history_event_ids"] for record in records.values())
    assert all("health" not in record and "wealth" not in record and "wants" not in record
               for record in records.values())
    assert world.event_log.by_type("LOD_BACKGROUND_RECORDED")
    for event in world.event_log.by_type("LOD_BACKGROUND_RECORDED"):
        assert world.event_log.get(event.caused_by[0]).type == "CHARACTER_GENERATED"

    restored = WorldState.from_snapshot(world.to_snapshot())
    assert restored.lod.snapshot() == world.lod.snapshot()
    assert restored.economy.snapshot() == world.economy.snapshot()


def test_promote_demote_retains_name_stats_traits_and_event_history():
    world = WorldState.create("lod-promotion-coherence")
    world.run_until(365 * 24)
    character_id, record = sorted(world.lod.named_records.items())[0]
    original = {key: record[key] for key in ("display_name", "stats", "traits", "heritage")}
    history = list(record["history_event_ids"])
    trigger = world.event_log.append(
        tick=world.clock.tick, event_type="LOD_TEST_NOTABLE", location=record["location"]["id"],
        data={"test": True}, caused_by=[record["last_event_id"]],
        causes_tags=["lod_test"], magnitude=0.8,
    )
    promotion = world.lod._promote(character_id, trigger.id, world)
    active = world.entities[character_id]

    assert active["tier"] == "MINOR"
    assert all(active[key] == value for key, value in original.items())
    assert active["lod_history_event_ids"] == [*history, promotion]
    demotion = world.lod.demote_character(world, character_id, cause_event_id=promotion)
    compacted = world.lod.named_records[character_id]
    assert character_id not in world.entities
    assert compacted["display_name"] == original["display_name"]
    assert compacted["stats"] == original["stats"]
    assert compacted["traits"] == original["traits"]
    assert compacted["history_event_ids"] == [*history, promotion, demotion]
    world.event_log.validate_causality()
    restored = WorldState.from_snapshot(world.to_snapshot())
    restored.lod._promote(character_id, demotion, restored)
    assert restored.entities[character_id]["display_name"] == original["display_name"]
    assert restored.entities[character_id]["stats"] == original["stats"]
    assert restored.entities[character_id]["lod_history_event_ids"][-1] == \
        restored.entities[character_id]["last_event_id"]


def test_ten_year_default_run_meets_named_character_scale_target():
    world = WorldState.create("lod-ten-year-scale")
    started = time.perf_counter()
    world.run_until(10 * 365 * 24)
    elapsed = time.perf_counter() - started

    assert len(world.geography.islands) >= 40
    assert len(_all_named_character_ids(world)) >= 300
    assert len(world.lod.regional_history["tempest_belt"]) == \
        world.lod.data["rules"]["regional_history_weeks"]
    assert elapsed < 300
    world.to_snapshot()
