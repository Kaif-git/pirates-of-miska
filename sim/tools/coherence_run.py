"""Run a bounded coherence baseline with durable event and state-census output."""

from __future__ import annotations

import argparse
import gzip
import hashlib
from itertools import islice
import json
from pathlib import Path
import platform
import subprocess
import sys
import time
from typing import Any

from sim.core.world import WorldState


def _census(world, sample_day: int) -> dict[str, Any]:
    ships = {entity_id: row for entity_id, row in world.entities.items()
             if row.get("kind") == "ship"}
    merchant_ship_ids = {merchant.get("ship_id")
                         for merchant in world.economy.merchants.values()}
    crew_memberships: dict[str, list[str]] = {}
    roster_references = []
    for crew_id, crew in world.crew_ships.crews.items():
        roster = list(crew.get("roster", ()))
        crew_memberships[crew_id] = roster
        for person_id in roster:
            roster_references.append((crew_id, person_id))
    memberships_per_person: dict[str, int] = {}
    for _, person_id in roster_references:
        memberships_per_person[person_id] = memberships_per_person.get(person_id, 0) + 1
    missing_roster_people = sorted({person_id for _, person_id in roster_references
                                    if person_id not in world.entities
                                    and person_id not in world.archive})
    living_roster_people = {
        entity_id for entity_id, row in world.entities.items()
        if row.get("kind") == "character" and row.get("death_tick") is None
        and row.get("departure_tick") is None
    }
    living_roster_people.update(
        entity_id for entity_id, row in world.archive.items()
        if row.get("kind") == "character" and row.get("death_tick") is None
        and row.get("departure_tick") is None)
    nonliving_crew_roster_members = sorted(
        {person_id for _, person_id in roster_references
         if person_id not in living_roster_people})
    hull_less_crews = sorted(
        crew_id for crew_id, crew in world.crew_ships.crews.items()
        if not crew.get("ship_ids"))
    stranded_roster_people = sorted(
        person_id for crew_id in hull_less_crews
        for person_id in world.crew_ships.crews[crew_id].get("roster", ())
        if (person := world.entities.get(person_id)) is not None
        and person.get("crew_id") == crew_id and person.get("ship_id") is None)
    ships_without_crew = sorted(ship_id for ship_id, ship in ships.items()
                                if ship.get("crew_id") is not None
                                and ship.get("crew_id") not in world.crew_ships.crews)
    explicitly_uncrewed_ships = sorted(
        ship_id for ship_id, ship in ships.items()
        if ship.get("crew_id") is None and ship.get("controller_crew_id") is None)
    invalid_legal_owners = []
    invalid_controllers = []
    invalid_ownership_events = []
    for ship_id, ship in sorted(ships.items()):
        owner = ship.get("legal_owner")
        owner_error = None
        if owner is not None:
            if (not isinstance(owner, dict) or set(owner) != {"kind", "id"}
                    or not isinstance(owner.get("id"), str) or not owner.get("id")):
                owner_error = "malformed legal owner"
            elif owner["kind"] == "crew" and owner["id"] not in world.crew_ships.crews:
                owner_error = "owner crew is not active"
            elif owner["kind"] == "merchant":
                merchant = world.economy.merchants.get(owner["id"])
                if merchant is None:
                    owner_error = "owner merchant is not active"
                elif merchant.get("ship_id") != ship_id:
                    owner_error = "merchant registry does not reference titled ship"
            elif owner["kind"] != "crew":
                owner_error = "unsupported legal owner kind"
        if owner_error:
            invalid_legal_owners.append({"ship_id": ship_id, "owner": owner,
                                        "error": owner_error})
        controller = ship.get("controller_crew_id")
        if controller is not None and controller not in world.crew_ships.crews:
            invalid_controllers.append({"ship_id": ship_id,
                                        "controller_crew_id": controller,
                                        "error": "controller crew is not active"})
        ownership_event_id = ship.get("ownership_event_id")
        try:
            ownership_event = world.event_log.get(ownership_event_id)
            if ownership_event.type not in {
                    "SHIP_CREATED", "SHIP_ACQUIRED", "SHIP_CAPTURED", "CREW_FOUNDED"}:
                raise ValueError("unsupported ownership event")
        except (KeyError, TypeError, ValueError):
            invalid_ownership_events.append({"ship_id": ship_id,
                                             "ownership_event_id": ownership_event_id,
                                             "error": "missing or invalid ownership event"})
    ownership_disagreements = []
    for ship_id, ship in ships.items():
        intended_controller = ship.get("captured_by_crew_id")
        current_crew = ship.get("crew_id")
        if intended_controller and intended_controller != current_crew:
            ownership_disagreements.append({
                "ship_id": ship_id,
                "crew_id": current_crew,
                "captured_by_crew_id": intended_controller,
            })

    merchants = []
    for merchant_id, merchant in sorted(world.economy.merchants.items()):
        ship_id = merchant.get("ship_id")
        ship = world.entities.get(ship_id) or world.archive.get(ship_id) or {}
        location = ship.get("location")
        merchants.append({
            "merchant_id": merchant_id,
            "status": merchant.get("status", "active"),
            "closed_event_id": merchant.get("closed_event_id"),
            "closure_reason": merchant.get("closure_reason"),
            "ship_id": ship_id,
            "ship_exists": bool(ship),
            "ship_state": ship.get("state", "active" if ship else "missing"),
            "crew_id": ship.get("crew_id"),
            "captured_by_crew_id": ship.get("captured_by_crew_id"),
            "in_transit": ship_id in world.travel.transits,
            "location_kind": location.get("kind") if isinstance(location, dict) else None,
            "location_id": location.get("id") if isinstance(location, dict) else None,
            "blocker_reason": merchant.get("activity_blocker_reason"),
            "blocker_since_day": merchant.get("activity_blocker_since_day"),
        })

    living_named = [row for row in world.entities.values()
                    if row.get("kind") == "character" and row.get("death_tick") is None
                    and row.get("departure_tick") is None]
    archived_named = [row for row in world.archive.values()
                      if row.get("kind") == "character" and row.get("death_tick") is None
                      and row.get("departure_tick") is None]
    living_ids = {row["id"] for row in living_named + archived_named}
    crew_membership_link_errors = []
    for person_id in sorted(living_ids):
        person = world.entities.get(person_id) or world.archive.get(person_id)
        roster_crews = sorted(
            crew_id for crew_id, crew in world.crew_ships.crews.items()
            for roster_person_id in crew.get("roster", ())
            if roster_person_id == person_id)
        linked_crew_id = person.get("crew_id")
        if linked_crew_id is None and roster_crews:
            crew_membership_link_errors.append({
                "character_id": person_id,
                "character_crew_id": None,
                "roster_crew_ids": roster_crews,
                "error": "crew roster contains a character with no crew link",
            })
        elif linked_crew_id is not None and roster_crews != [linked_crew_id]:
            crew_membership_link_errors.append({
                "character_id": person_id,
                "character_crew_id": linked_crew_id,
                "roster_crew_ids": roster_crews,
                "error": "character crew link disagrees with active roster membership",
            })
    uncrewed_living_named = sorted(living_ids - set(memberships_per_person))
    lod_population = sum(int(row["population"]) for row in world.lod.cohorts.values())
    lod_population_classes: dict[str, int] = {}
    for cohort in world.lod.cohorts.values():
        for population_class, count in cohort["population_classes"].items():
            lod_population_classes[population_class] = (
                lod_population_classes.get(population_class, 0) + int(count))
    lod_named_ids = set(world.lod.named_records)
    lod_named_ids.update(entity_id for entity_id, row in world.entities.items()
                         if row.get("lod_resident_origin") is True
                         and row.get("death_tick") is None
                         and row.get("departure_tick") is None)
    lod_named_ids.update(entity_id for entity_id, row in world.archive.items()
                         if row.get("lod_resident_origin") is True
                         and row.get("death_tick") is None
                         and row.get("departure_tick") is None)
    departed_lod_ids = {
        target.get("entity_id") for event in world.event_log.by_type("CHARACTER_DEPARTED")
        for target in event.targets if target.get("role") in {"departed", "emigrant"}
        and target.get("entity_id") in lod_named_ids
    }
    lod_initial = world.lod.census_population_initial
    lod_balance = lod_population + len(lod_named_ids - departed_lod_ids)
    generated_at_start = {
        actor.get("entity_id") for event in world.event_log.by_type("CHARACTER_GENERATED")
        if event.tick == 0
        for actor in event.actors if actor.get("role") == "character"
    }
    generated_after_start = {
        actor.get("entity_id") for event in world.event_log.by_type("CHARACTER_GENERATED")
        if event.tick > 0
        for actor in event.actors if actor.get("role") == "character"
    }
    background_samples = {
        actor.get("entity_id") for event in world.event_log.by_type("LOD_BACKGROUND_RECORDED")
        for actor in event.actors if actor.get("role") == "named_resident"
    }
    dead_after_start = {
        target.get("entity_id") for event in world.event_log.by_type("CHARACTER_DIED")
        if event.tick > 0
        for target in event.targets if target.get("role") == "deceased"
    }
    departed_after_start = {
        target.get("entity_id") for event in world.event_log.by_type("CHARACTER_DEPARTED")
        if event.tick > 0
        for target in event.targets if target.get("role") in {"departed", "emigrant"}
    }
    background_samples_at_start = {
        actor.get("entity_id") for event in world.event_log.by_type("LOD_BACKGROUND_RECORDED")
        if event.tick == 0
        for actor in event.actors if actor.get("role") == "named_resident"
    }
    living_population = (lod_population + len(lod_named_ids - departed_lod_ids)
                         + len(living_ids - lod_named_ids))
    initial_population = (world.lod.census_population_initial
                         + len(generated_at_start - background_samples_at_start))
    expected_living_population = (
        initial_population
        + len(generated_after_start - background_samples)
        - len(dead_after_start)
        - len(departed_after_start)
    )
    transition_deltas = []
    transition_population = initial_population
    transition_errors = []
    for event in world.event_log:
        if event.tick <= 0 or event.tick > world.clock.tick:
            continue
        transition_kind = {
            "CHARACTER_GENERATED": (1, "new_identity"),
            "LOD_BACKGROUND_RECORDED": (-1, "statistical_to_named_identity_transfer"),
            "LOD_REGION_AGGREGATED": (0, "regional_summary_not_population_change"),
            "CHARACTER_DIED": (-1, "death"),
            "CHARACTER_AGED": (0, "identity_age_change"),
            "CHARACTER_DEPARTED": (-1, "departure"),
            "CHARACTER_ARRIVED": (0, "crew_arrival_same_identity"),
            "ROOKIE_PROMOTED": (0, "character_tier_change"),
            "ROOKIE_SPAWNED": (0, "new_identity_generation_marker"),
            "LOD_PROMOTED": (0, "named_representation_promotion"),
            "LOD_DEMOTED": (0, "named_representation_demotion"),
            "SHIP_LOSS_DISPOSITION": (0, "survivor_missing_death_partition"),
            "SHIP_SUNK": (0, "asset_loss_population_disposition_follows"),
            "SHIP_CAPTURED": (0, "ship_title_transfer"),
            "SHIP_ACQUIRED": (0, "ship_title_transfer"),
            "PRIZE_CREW_INSTALLED": (0, "command_assignment_not_population_change"),
            "CHARACTER_MISSING": (0, "missing_person_state"),
            "CHARACTER_RESCUED": (0, "same_identity_rescue"),
            "PRISONER_CAPTURED": (0, "custody_transfer"),
            "PRISONER_RELEASED": (0, "custody_release"),
            "PRISONER_CUSTODY_TRANSFERRED": (0, "custody_transfer"),
            "PRISONER_FATE": (0, "custody_fate_resolution"),
            "RANSOM_PAID": (0, "silver_transfer_not_population_change"),
            "MISSING_PERSON_RESOLVED": (0, "missing_fate_resolution"),
            "CREW_MEMBER_TRANSFERRED": (0, "crew_membership_transfer"),
            "CREW_MEMBER_DEFECTED": (0, "crew_membership_transfer"),
            "CREW_FOUNDED": (0, "organization_formation"),
            "CREW_FUNDED": (0, "organization_account_funding"),
            "CREW_RESTED": (0, "crew_state_change"),
            "CREW_REPUTATION_CHANGED": (0, "crew_reputation_change"),
            "CREW_SUPPLIES_EXHAUSTED": (0, "crew_condition_change"),
            "CREW_EMPLOYMENT_SOUGHT": (0, "employment_opportunity_request"),
            "CREW_EMPLOYMENT_ACCEPTED": (0, "crew_consolidation"),
            "CREW_WALLET_TRANSFERRED": (0, "personal_wallet_account_transfer"),
            "CREW_ASSETS_TRANSFERRED": (0, "crew_treasury_and_debt_transfer"),
            "CREW_DISSOLVED": (0, "organization_change_not_population_change"),
            "CAPTAIN_CHANGED": (0, "crew_leadership_transfer"),
            "CAPTAIN_APPOINTED": (0, "crew_leadership_transfer"),
            "DESERTION": (0, "crew_departure_not_population_departure"),
            "RECRUITED": (0, "crew_arrival_same_identity"),
            "FACTION_FOUNDED": (0, "political_organization_only"),
            "FACTION_MONTHLY_DECISION": (0, "political_command_only"),
            "FACTION_POWER_VACUUM": (0, "political_organization_only"),
            "FACTION_SUCCESSION": (0, "political_leadership_transfer"),
            "FACTION_DISSOLVED": (0, "political_organization_only"),
        }.get(event.type)
        if transition_kind is None:
            continue
        delta, reason = transition_kind
        transition_population += delta
        transition = {
            "event_id": event.id,
            "tick": event.tick,
            "event_type": event.type,
            "reason": reason,
            "living_population_delta": delta,
            "ledger_balance_after": transition_population,
        }
        transition_deltas.append(transition)
        if transition_population < 0:
            transition_errors.append({"event_id": event.id,
                                      "error": "living population ledger underflow"})
    transition_residual = living_population - transition_population
    if transition_residual != 0:
        transition_errors.append({
            "error": "transition-local population ledger disagrees with observed living population",
            "observed_living_population": living_population,
            "ledger_balance": transition_population,
            "residual": transition_residual,
        })
    dissolved_ids = set(world.politics.archived_factions)
    active_factions = set(world.politics.factions)
    crews_with_dissolved_faction_ids = sorted(
        crew_id for crew_id, crew in world.crew_ships.crews.items()
        if crew.get("faction_id") in dissolved_ids and crew.get("faction_id") not in active_factions)

    return {
        "sample_day": sample_day,
        "tick": world.clock.tick,
        "event_count": len(world.event_log),
        "ships": {
            "active_total": len(ships),
            "by_owner_type": {
                owner_type: sum(
                    ("merchant" if ship_id in merchant_ship_ids else
                     str(ship.get("owner_type") or "unknown")) == owner_type
                    for ship_id, ship in ships.items())
                for owner_type in sorted({
                    "merchant" if ship_id in merchant_ship_ids else
                    str(ship.get("owner_type") or "unknown")
                    for ship_id, ship in ships.items()})
            },
            "captured_markers_with_crew_id_disagreement": ownership_disagreements,
            "ships_without_valid_crew": ships_without_crew,
            "explicitly_uncrewed_hulls": explicitly_uncrewed_ships,
            "ships_with_invalid_legal_owner": invalid_legal_owners,
            "ships_with_invalid_controller": invalid_controllers,
            "ships_with_invalid_ownership_event": invalid_ownership_events,
        },
        "organizations": {
            "active_crews": len(world.crew_ships.crews),
            "hull_less_crews": hull_less_crews,
            "hull_less_crew_count": len(hull_less_crews),
            "active_factions": len(world.politics.factions),
            "archived_factions": len(world.politics.archived_factions),
            "crews_with_dissolved_faction_id": crews_with_dissolved_faction_ids,
            "crew_roster_entries": len(roster_references),
            "distinct_people_in_rosters": len(memberships_per_person),
            "duplicate_crew_memberships": {
                person_id: count for person_id, count in sorted(memberships_per_person.items())
                if count > 1
            },
            "living_character_crew_link_errors": crew_membership_link_errors,
            "living_character_crew_links_balanced": not crew_membership_link_errors,
            "roster_references_without_character_entity": missing_roster_people,
            "roster_references_to_nonliving_characters": nonliving_crew_roster_members,
        },
        "people": {
            "living_active_character_entities": len(living_named),
            "living_archived_character_entities": len(archived_named),
        "lod_named_records": len(world.lod.named_records) - len(
            set(world.lod.named_records) & departed_lod_ids),
            "living_named_people_not_in_any_crew_roster": uncrewed_living_named,
            "stranded_named_crew_members": stranded_roster_people,
            "lod_population_census": {
                "aggregate_cohort_population": lod_population,
                "aggregate_population_classes": dict(sorted(lod_population_classes.items())),
                "distinct_named_lod_residents_active_archived_or_compact": len(lod_named_ids),
                "initial_population": lod_initial,
                "accounted_population": lod_balance,
                "residual": lod_balance - lod_initial if lod_initial is not None else None,
                "verified_by_world_validator": lod_balance == lod_initial,
            },
            "world_population_conservation": {
                "opening_living_population": initial_population,
                "runtime_new_characters": len(generated_after_start - background_samples),
                "runtime_deaths": len(dead_after_start),
                "runtime_departures": len(departed_after_start),
                "expected_living_population": expected_living_population,
                "observed_living_population": living_population,
                "residual": living_population - expected_living_population,
                "transition_local_ledger": {
                    "transition_count": len(transition_deltas),
                    "expected_living_population": transition_population,
                    "observed_living_population": living_population,
                    "residual": transition_residual,
                    "errors": transition_errors,
                    "transitions": transition_deltas,
                },
                "validated_event_types": ["CHARACTER_GENERATED", "LOD_BACKGROUND_RECORDED",
                                          "LOD_REGION_AGGREGATED", "CHARACTER_DIED",
                                          "CHARACTER_DEPARTED", "CHARACTER_ARRIVED",
                                          "CHARACTER_AGED", "ROOKIE_PROMOTED", "ROOKIE_SPAWNED",
                                          "LOD_PROMOTED", "LOD_DEMOTED", "SHIP_LOSS_DISPOSITION",
                                          "SHIP_SUNK", "SHIP_CAPTURED", "SHIP_ACQUIRED",
                                          "PRIZE_CREW_INSTALLED", "CHARACTER_MISSING",
                                          "CHARACTER_RESCUED", "PRISONER_CAPTURED",
                                          "PRISONER_RELEASED", "PRISONER_CUSTODY_TRANSFERRED",
                                          "PRISONER_FATE", "RANSOM_PAID",
                                          "MISSING_PERSON_RESOLVED", "ROOKIE_SPAWNED",
                                          "CREW_MEMBER_TRANSFERRED", "CREW_MEMBER_DEFECTED",
                                          "CREW_EMPLOYMENT_SOUGHT", "CREW_EMPLOYMENT_ACCEPTED",
                                          "CREW_FOUNDED", "CREW_FUNDED", "CREW_RESTED",
                                          "CREW_REPUTATION_CHANGED", "CREW_SUPPLIES_EXHAUSTED",
                                          "CREW_WALLET_TRANSFERRED", "CREW_ASSETS_TRANSFERRED",
                                          "CREW_DISSOLVED",
                                          "CAPTAIN_CHANGED", "CAPTAIN_APPOINTED", "DESERTION",
                                          "RECRUITED", "FACTION_FOUNDED",
                                          "FACTION_MONTHLY_DECISION", "FACTION_POWER_VACUUM",
                                          "FACTION_SUCCESSION", "FACTION_DISSOLVED"],
                "limitations": [
                    "Event IDs track named people; background cohort headcount is represented by LOD population counters.",
                    "Explicit CHARACTER_DEPARTED transitions count as world-population exits; this API currently accepts named, unaffiliated island residents only.",
                    "This aggregate census does not replace transition-local roster and custody audits.",
                ],
            },
            "squad_headcount_conservation": {
                "named_crew_roster_members": len(memberships_per_person),
                "living_named_people_outside_crew_rosters": len(uncrewed_living_named),
                "living_named_population": len(living_ids),
                "living_named_population_in_rosters": len(
                    living_ids & set(memberships_per_person)),
                "living_named_population_outside_rosters": len(uncrewed_living_named),
                "living_character_crew_link_errors": crew_membership_link_errors,
                "living_named_roster_partition_sum": (
                    len(living_ids & set(memberships_per_person))
                    + len(uncrewed_living_named)),
                "living_named_roster_partition_residual": (
                    len(living_ids)
                    - len(living_ids & set(memberships_per_person))
                    - len(uncrewed_living_named)),
                "lod_statistical_population": lod_population,
                "living_named_lod_residents": len(lod_named_ids - departed_lod_ids),
                "observed_living_population": living_population,
                "population_representation_sum": (
                    lod_population + len(lod_named_ids - departed_lod_ids)
                    + len(living_ids - lod_named_ids)),
                "population_representation_balanced": (
                    living_population == lod_population
                    + len(lod_named_ids - departed_lod_ids)
                    + len(living_ids - lod_named_ids)
                    and not crew_membership_link_errors
                    and not nonliving_crew_roster_members
                    and not missing_roster_people
                    and not any(count > 1 for count in memberships_per_person.values())),
                "crew_roster_nonliving_members": nonliving_crew_roster_members,
                "limitation": (
                    "Crew rosters account for named members only; LOD cohorts are not "
                    "crew squads. Membership replay and population partition are separate ledgers."),
            },
        },
        "merchants": {
            "registered": len(merchants),
            "active": sum(row["status"] == "active" for row in merchants),
            "closed": sum(row["status"] == "closed" for row in merchants),
            "valid_ship_links": sum(row["ship_exists"] for row in merchants),
            "underway": sum(row["in_transit"] for row in merchants),
            "at_island": sum(row["location_kind"] == "island" for row in merchants),
            "captured_marker": sum(row["captured_by_crew_id"] is not None for row in merchants),
            "missing_or_archived_ship": [row for row in merchants if not row["ship_exists"]],
            "ship_links": merchants,
        },
        "event_activity": {
            "market_trades": len(world.event_log.by_type("MARKET_TRADE")),
            "voyages_started": len(world.event_log.by_type("VOYAGE_STARTED")),
            "voyages_arrived": len(world.event_log.by_type("VOYAGE_ARRIVED")),
            "ship_captures": len(world.event_log.by_type("SHIP_CAPTURED")),
            "ship_sinkings": len(world.event_log.by_type("SHIP_SUNK")),
            "character_deaths": len(world.event_log.by_type("CHARACTER_DIED")),
        },
    }


def _sampled_merchant_utilization(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    """Estimate operating states from interval-start snapshots, not event-exact time."""
    weights = {"underway": 0, "blocked": 0, "at_island_unblocked": 0,
               "closed": 0, "other_or_unknown": 0}
    blocker_days: dict[str, int] = {}
    observed_merchant_days = 0
    for earlier, later in zip(snapshots, snapshots[1:]):
        interval_days = later["sample_day"] - earlier["sample_day"]
        if interval_days <= 0:
            continue
        for merchant in earlier["merchants"]["ship_links"]:
            observed_merchant_days += interval_days
            if merchant.get("status", "active") == "closed":
                weights["closed"] += interval_days
                continue
            if merchant["in_transit"]:
                weights["underway"] += interval_days
            elif merchant.get("blocker_reason"):
                weights["blocked"] += interval_days
                blocker = merchant["blocker_reason"]
                blocker_days[blocker] = blocker_days.get(blocker, 0) + interval_days
            elif merchant["location_kind"] == "island":
                weights["at_island_unblocked"] += interval_days
            else:
                weights["other_or_unknown"] += interval_days
    return {
        "method": "30-day census interval-start state estimate plus unique merchant/calendar-day activity; not event-exact time",
        "observed_merchant_days": observed_merchant_days,
        "merchant_days_by_state": weights,
        "merchant_days_by_blocker_reason": dict(sorted(blocker_days.items())),
        "share_by_state": {
            state: round(days / observed_merchant_days, 4) if observed_merchant_days else None
            for state, days in weights.items()
        },
        "merchant_activity_event_counts": {
            key: sum(snapshot.get("merchant_activity_events_since_previous_sample", {}).get(key, 0)
                     for snapshot in snapshots[1:])
            for key in ("trade", "travel", "provisioning", "repair", "weather_loss",
                        "blocked_review", "route_review")
        },
        "merchant_activity_days": {
            key: sum(snapshot.get("merchant_activity_days_since_previous_sample", {}).get(key, 0)
                     for snapshot in snapshots[1:])
            for key in ("trade", "travel", "provisioning", "repair", "weather_loss",
                        "blocked_review", "route_review")
        },
        "limitations": [
            "30-day census interval-start states estimate merchant-days; event transitions are not time-exact",
            "activity days count unique merchant/calendar days with a matching event, not duration within a day",
            "repair activity is reported only if a repair event exists; no merchant repair event is currently defined",
            "at-island unblocked means no recorded route blocker, not proof of trading or provisioning",
        ],
    }


def _merchant_activity_for_window(events, merchants: dict[str, dict[str, Any]],
                                 ticks_per_day: int) -> tuple[dict[str, int], dict[str, int]]:
    """Attribute event counts/days to the firm that owned the vessel at that event."""
    categories = ("trade", "travel", "provisioning", "repair", "weather_loss",
                  "blocked_review", "route_review")
    counts = {key: 0 for key in categories}
    activity_days: dict[str, set[tuple[str, int]]] = {key: set() for key in categories}
    merchant_by_ship = {row["ship_id"]: (merchant_id, row)
                        for merchant_id, row in merchants.items()}
    event_groups = {
        "MARKET_TRADE": "trade",
        "VOYAGE_STARTED": "travel", "VOYAGE_ARRIVED": "travel",
        "SHIP_SUPPLIED": "provisioning", "SHIP_SUPPLIES_CONSUMED": "provisioning",
        "PRISONER_PROVISIONS_CONSUMED": "provisioning",
        "SHIP_REPAIRED": "repair",
        "SHIP_SUPPLIES_LOST": "weather_loss",
        "MERCHANT_ACTIVITY_BLOCKED": "blocked_review",
        "MERCHANT_ROUTE_DECISION": "route_review",
    }
    for event in events:
        group = event_groups.get(event.type)
        if group is None:
            continue
        relevant_ids = {row.get("entity_id") for row in (*event.actors, *event.targets)}
        event_merchants = relevant_ids.intersection(merchants)
        event_merchants.update(
            merchant_id for ship_id, (merchant_id, _) in merchant_by_ship.items()
            if ship_id in relevant_ids)
        # A firm remains attributable for activity before its terminal event,
        # but post-closure use of its former hull belongs to the new controller.
        event_merchants = {
            merchant_id for merchant_id in event_merchants
            if (merchants[merchant_id].get("status", "active") != "closed"
                or event.id < merchants[merchant_id].get("closed_event_id", ""))
        }
        if not event_merchants:
            continue
        counts[group] += 1
        event_day = event.tick // ticks_per_day
        activity_days[group].update((merchant_id, event_day) for merchant_id in event_merchants)
    return counts, {key: len(days) for key, days in activity_days.items()}


def run_coherence_baseline(seed: int, years: int, director_enabled: bool,
                           output: str | Path) -> dict[str, Any]:
    if type(seed) is not int or seed < 0 or type(years) is not int or years < 1:
        raise ValueError("seed must be nonnegative and years must be positive")
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    events_path = out / "events.jsonl.gz"
    census_path = out / "state-census.json"
    manifest_path = out / "run-manifest.json"

    world = WorldState.create(seed, director_enabled=director_enabled)
    time_config = world.config["time"]
    ticks_per_day = time_config["hours_per_day"] // time_config["hours_per_tick"]
    sample_days = sorted({min(years * time_config["days_per_year"], day)
                          for day in range(30, years * time_config["days_per_year"] + 1, 30)}
                         | {year * time_config["days_per_year"] for year in range(1, years + 1)})
    snapshots = [_census(world, 0)]
    digest = hashlib.sha256()
    cursor = 0
    started = time.perf_counter()
    # Persist each completed window so a later interruption leaves a valid prefix log.
    with gzip.open(events_path, "wt", encoding="utf-8", newline="\n") as event_stream:
        for day in sample_days:
            world.run_until(day * ticks_per_day)
            window_events = list(islice(iter(world.event_log), cursor, None))
            known_merchants = world.economy.merchants
            activity_counts, activity_days_by_category = _merchant_activity_for_window(
                window_events, known_merchants, ticks_per_day)
            for event in window_events:
                encoded = json.dumps(event.to_record(), sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False)
                event_stream.write(encoded + "\n")
                digest.update(encoded.encode("utf-8"))
                digest.update(b"\n")
            cursor = len(world.event_log)
            event_stream.flush()
            census = _census(world, day)
            census["merchant_activity_events_since_previous_sample"] = activity_counts
            census["merchant_activity_days_since_previous_sample"] = activity_days_by_category
            snapshots.append(census)
            census_path.write_text(
                json.dumps({"seed": seed, "director_enabled": director_enabled,
                            "requested_years": years, "samples": snapshots},
                           ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8", newline="\n")

    elapsed = time.perf_counter() - started
    try:
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[2],
                                  check=True, capture_output=True, text=True).stdout.strip()
        diff = subprocess.run(["git", "diff", "--binary", "HEAD", "--", "sim"],
                              cwd=Path(__file__).resolve().parents[2], check=True,
                              capture_output=True).stdout
        working_tree_status = subprocess.run(
            ["git", "status", "--short", "--", "sim"],
            cwd=Path(__file__).resolve().parents[2], check=True,
            capture_output=True, text=True).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        revision, diff, working_tree_status = None, b"", ["unavailable"]
    config_bytes = json.dumps(world.config, sort_keys=True, ensure_ascii=False,
                              separators=(",", ":"), allow_nan=False).encode("utf-8")
    manifest = {
        "seed": seed,
        "director_enabled": director_enabled,
        "requested_years": years,
        "completed_tick": world.clock.tick,
        "completed_days": world.clock.tick // ticks_per_day,
        "elapsed_seconds": round(elapsed, 3),
        "process_cpu_seconds": round(time.process_time(), 3),
        "python_version": sys.version,
        "platform": platform.platform(),
        "code_revision": revision,
        "sim_worktree_status": working_tree_status,
        "sim_diff_sha256": hashlib.sha256(diff).hexdigest(),
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "command": f"python -m sim.tools.coherence_run --seed {seed} --years {years} "
                   f"--director {'on' if director_enabled else 'off'} --output {out}",
        "event_count": len(world.event_log),
        "event_log_sha256": digest.hexdigest(),
        "durable_event_log": str(events_path),
        "state_census": str(census_path),
        "sampled_merchant_utilization": _sampled_merchant_utilization(snapshots),
        "chunk_boundaries_days": sample_days,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8", newline="\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--years", type=int, default=1)
    parser.add_argument("--director", choices=("on", "off"), default="off")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = run_coherence_baseline(args.seed, args.years, args.director == "on", args.output)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
