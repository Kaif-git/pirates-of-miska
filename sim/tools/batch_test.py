"""Deterministic seed batch with economy acceptance metrics."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any

from sim.core.world import WorldState
from sim.core.ownership import ship_is_commandable


def _trade_volume_stable(annual_trade: list[dict], *, minimum_ratio_permille: int = 100,
                         maximum_ratio_permille: int = 10000) -> tuple[bool | None, int | None]:
    """M3's first-run band: active every year; final volume stays within 0.1x–10x year one."""
    if len(annual_trade) < 3:
        return None, None
    quantities = [row["quantity_milli"] for row in annual_trade]
    if any(type(quantity) is not int or quantity <= 0 for quantity in quantities):
        return False, None
    ratio_permille = quantities[-1] * 1000 // quantities[0]
    return minimum_ratio_permille <= ratio_permille <= maximum_ratio_permille, ratio_permille


def _event_log_sha256(event_log) -> str:
    """Hash canonical JSONL incrementally without duplicating a long event log in memory."""
    digest = hashlib.sha256()
    for event in event_log:
        line = json.dumps(event.to_record(), sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False)
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _terminal_actor_violations(event_log) -> list[dict[str, Any]]:
    """Find direct entity and attributable faction commands after terminal state."""
    terminal_positions: dict[str, tuple[int, int]] = {}
    terminal_org_positions: dict[str, tuple[int, int]] = {}
    violations = []
    terminal_types = {"CHARACTER_DIED", "SHIP_SUNK"}
    organization_actor_roles = {
        "faction", "government", "organization", "faction_command",
        "attacking_faction", "trading_faction", "proposer", "requester",
        "obligated_faction", "obligated_ally",
    }
    faction_command_types = {
        "FACTION_POWER_VACUUM", "TERRITORY_CLAIMED", "ISLAND_FORTIFIED",
        "TREATY_BREACHED", "DIPLOMATIC_EXCHANGE", "FACTION_TRADE_RECORDED",
        "TREATY_TRANSITION", "TREATY_AID_REQUESTED", "TREATY_OBLIGATION_RESOLVED",
        "WAR_DECLARED",
    }
    for position, event in enumerate(event_log):
        for participant in event.actors:
            entity_id = participant["entity_id"]
            if entity_id in terminal_positions:
                terminal_position, terminal_tick = terminal_positions[entity_id]
                if position <= terminal_position:
                    continue
                violations.append({
                    "entity_id": entity_id, "terminal_tick": terminal_tick,
                    "event_id": event.id, "event_type": event.type, "tick": event.tick,
                })
            terminal_org = terminal_org_positions.get(entity_id)
            if (terminal_org and position > terminal_org[0]
                    and participant.get("role") in organization_actor_roles):
                violations.append({
                    "entity_id": entity_id, "terminal_tick": terminal_org[1],
                    "event_id": event.id, "event_type": event.type, "tick": event.tick,
                    "terminal_kind": "organization", "actor_role": participant.get("role"),
                })
        if event.type in faction_command_types:
            command_org_ids = set()
            explicit_actor_ids = {
                actor["entity_id"] for actor in event.actors
                if actor.get("role") in organization_actor_roles}
            faction_id = event.data.get("faction_id")
            if isinstance(faction_id, str):
                command_org_ids.add(faction_id)
            faction_ids = event.data.get("factions", ())
            if isinstance(faction_ids, (list, tuple)):
                command_org_ids.update(item for item in faction_ids if isinstance(item, str))
            for organization_id in command_org_ids - explicit_actor_ids:
                terminal_org = terminal_org_positions.get(organization_id)
                if terminal_org and position > terminal_org[0]:
                    violations.append({
                        "entity_id": organization_id, "terminal_tick": terminal_org[1],
                        "event_id": event.id, "event_type": event.type, "tick": event.tick,
                        "terminal_kind": "organization", "actor_role": "indirect_command",
                    })
        if event.type in terminal_types:
            for participant in event.targets:
                terminal_positions[participant["entity_id"]] = (position, event.tick)
        if event.type == "FACTION_DISSOLVED":
            organization_id = event.data.get("faction_id")
            if not organization_id:
                organization_id = next((participant["entity_id"]
                                        for participant in event.targets
                                        if participant.get("role") == "former_faction"), None)
            if organization_id:
                terminal_org_positions[organization_id] = (position, event.tick)
    return violations


def _actor_band_counts(world) -> dict[str, int]:
    """Count living ship bands and captain-capable named characters."""
    counts = {name: 0 for name in (
        "merchant_ships", "pirate_ships", "marine_ships", "privateer_ships",
        "major_captains", "legends")}
    for ship in world.entities.values():
        if (ship.get("kind") != "ship" or ship.get("sunk")
                or ship.get("state") == "sunk"
                or not ship_is_commandable(world, ship["id"])):
            continue
        owner_type = ship.get("owner_type")
        crew = world.crew_ships.crews.get(ship.get("crew_id"), {})
        faction_id = crew.get("faction_id")
        faction = world.politics.factions.get(faction_id, {}) if faction_id else {}
        faction_kind = str(faction.get("kind", "")).lower()
        if faction_kind in {"marine", "patrol", "actor_marine"}:
            owner_type = "marine"
        elif faction_kind in {"merchant", "merchant_league", "actor_merchant"}:
            owner_type = "merchant"
        elif faction_kind in {"pirate", "raider", "actor_pirate"}:
            owner_type = "pirate"
        elif faction_kind in {"privateer", "actor_privateer"}:
            owner_type = "privateer"
        elif faction_id:
            owner_type = "faction_fleet"
        elif any(row.get("ship_id") == ship.get("id")
                 for row in world.economy.merchants.values()):
            owner_type = "merchant"
        if owner_type in {"merchant", "pirate", "marine", "privateer"}:
            counts[f"{owner_type}_ships"] += 1
    for character in world.entities.values():
        if (character.get("kind") != "character" or character.get("death_tick") is not None
                or character.get("role") != "captain"):
            continue
        if character.get("tier") == "LEGEND":
            counts["legends"] += 1
        elif character.get("tier") == "MAJOR":
            counts["major_captains"] += 1
    return counts


def _actor_band_bounds(world) -> dict[str, dict[str, int]]:
    """Scale ship-band per-island rates and preserve fixed captain ranges."""
    actors = world.config.get("world", {}).get("actors")
    if not actors:
        return {}
    island_count = len(world.geography.islands)
    bounds = {}
    for band, values in actors["ship_bands"].items():
        bounds[f"{band}_ships"] = {
            "minimum": (values["minimum_per_island_milli"] * island_count + 999) // 1000,
            "target": (values["target_per_island_milli"] * island_count + 999) // 1000,
            "maximum": (values["maximum_per_island_milli"] * island_count + 999) // 1000,
        }
    for band, values in actors["captain_bands"].items():
        bounds["major_captains" if band == "major" else "legends"] = dict(values)
    return bounds


def _ship_actor_band(world, ship_id: str) -> str | None:
    ship = world.entities.get(ship_id) or world.archive.get(ship_id) or {}
    if ship_id in world.entities and not ship_is_commandable(world, ship_id):
        return None
    owner_type = ship.get("owner_type")
    crew = world.crew_ships.crews.get(ship.get("crew_id"), {})
    faction = world.politics.factions.get(crew.get("faction_id"), {})
    kind = str(faction.get("kind", "")).lower()
    if kind in {"actor_merchant", "merchant", "merchant_league"}:
        return "merchant"
    if kind in {"actor_pirate", "pirate", "raider"}:
        return "pirate"
    if kind in {"actor_marine", "marine", "patrol"}:
        return "marine"
    if kind in {"actor_privateer", "privateer"}:
        return "privateer"
    if any(merchant.get("ship_id") == ship_id
           for merchant in world.economy.merchants.values()):
        return "merchant"
    return owner_type


def _merchant_raid_loss_metrics(world) -> dict[str, Any]:
    """Deduplicate merchant loss incidents and identify pirate-caused incidents."""
    merchant_ships = {merchant["ship_id"] for merchant in world.economy.merchants.values()}
    merchant_ships.update(
        event.data.get("old_ship_id") for event in world.event_log.by_type(
            "MERCHANT_VESSEL_REPLACED") if event.data.get("old_ship_id"))
    losses: dict[tuple[str, str], bool] = {}
    for event_type in ("SHIP_SUNK", "SHIP_CAPTURED", "COMBAT_LOOT_TRANSFERRED"):
        for event in world.event_log.by_type(event_type):
            if event_type == "SHIP_SUNK":
                ship_id = next((row["entity_id"] for row in event.targets
                                if row.get("role") == "sunken_ship"), None)
            elif event_type == "SHIP_CAPTURED":
                ship_id = next((row["entity_id"] for row in event.targets
                                if row.get("role") == "captured_ship"), None)
            else:
                ship_id = event.data.get("loser_ship_id")
                if not any(event.data.get("goods_milli", {}).values()):
                    continue
            if ship_id not in merchant_ships:
                continue
            cause = world.event_log.get(event.caused_by[0]) if event.caused_by else None
            if cause and cause.type in {"SHIP_SUNK", "SHIP_CAPTURED"} and cause.caused_by:
                cause = world.event_log.get(cause.caused_by[0])
            if cause and cause.type != "ENCOUNTER_RESOLVED":
                cause = next((world.event_log.get(parent) for parent in cause.caused_by
                              if world.event_log.get(parent).type == "ENCOUNTER_RESOLVED"), cause)
            pirate_involved = bool(
                cause and cause.type == "ENCOUNTER_RESOLVED"
                and any(_ship_actor_band(world, participant) == "pirate"
                        for participant in cause.data.get("ships", [])))
            losses[(cause.id if cause else event.id, ship_id)] = (
                losses.get((cause.id if cause else event.id, ship_id), False) or pirate_involved)
    pirate_count = sum(losses.values())
    total = len(losses)
    return {"merchant_loss_incidents": total,
            "pirate_caused_merchant_loss_incidents": pirate_count,
            "raider_target_decisions": len(world.event_log.by_type("RAIDER_TARGET_SELECTED")),
            "pirate_loss_share_permille": pirate_count * 1000 // total if total else None}


def _has_event_ancestor(event_log, event, ancestor_type: str) -> bool:
    pending = list(event.caused_by)
    visited = set()
    while pending:
        event_id = pending.pop()
        if event_id in visited:
            continue
        visited.add(event_id)
        parent = event_log.get(event_id)
        if parent.type == ancestor_type:
            return True
        pending.extend(parent.caused_by)
    return False


def _politics_metrics(world) -> dict:
    """Summarize M8 outcomes and audit transition continuity from recorded events."""
    transition_state = {}
    duplicate_alliance = False
    transition_chain_valid = True
    alliance_count = war_count = 0
    breach_causality_valid = True
    for event in world.event_log.by_type("TREATY_TRANSITION"):
        pair = tuple(event.data["factions"])
        previous = transition_state.get(pair, "none")
        source, target = event.data["from_state"], event.data["to_state"]
        if source != previous or source == target or not event.caused_by:
            transition_chain_valid = False
        if target == "alliance":
            alliance_count += 1
            if previous == "alliance":
                duplicate_alliance = True
        if target == "war":
            war_count += 1
        transition_state[pair] = target
    hostile_betrayals = 0
    for event in world.event_log.by_type("TREATY_BREACHED"):
        if event.data.get("reason") != "hostile_combat":
            continue
        hostile_betrayals += 1
        if not _has_event_ancestor(world.event_log, event, "ENCOUNTER_RESOLVED"):
            breach_causality_valid = False
        if not any(
                transition.data.get("to_state") == "war"
                and tuple(transition.data.get("factions", ()))
                == tuple(event.data.get("factions", ()))
                and event.id in transition.caused_by
                for transition in world.event_log.by_type("TREATY_TRANSITION")):
            breach_causality_valid = False
    heat_crossings = world.event_log.by_type("HEAT_THRESHOLD_CROSSED")
    heat_responses = world.event_log.by_type("GOVERNMENT_RESPONSE_FUNDED")
    heat_response_causality_valid = (
        not heat_crossings and not heat_responses
        or bool(heat_crossings) and bool(heat_responses)
        and all(
            _has_event_ancestor(world.event_log, response, "HEAT_THRESHOLD_CROSSED")
            or _has_event_ancestor(world.event_log, response, "HEAT_CHANGED")
            for response in heat_responses)
    )
    return {
        "factions_founded": len(world.event_log.by_type("FACTION_FOUNDED")),
        "factions_dissolved": len(world.event_log.by_type("FACTION_DISSOLVED")),
        "active_factions": len(world.politics.factions),
        "alliances_formed": alliance_count,
        "wars_declared": war_count,
        "hostile_treaty_betrayals": hostile_betrayals,
        "duplicate_alliance_events": duplicate_alliance,
        "treaty_transition_chain_valid": transition_chain_valid,
        "hostile_betrayals_causal": breach_causality_valid,
        "heat_events": len(world.event_log.by_type("HEAT_CHANGED")),
        "heat_threshold_crossings": len(heat_crossings),
        "bounties_set_or_raised": (len(world.event_log.by_type("BOUNTY_SET"))
                                    + len(world.event_log.by_type("BOUNTY_RAISED"))),
        "bounties_paid": len(world.event_log.by_type("BOUNTY_PAID")),
        "government_response_events": len(
            world.event_log.by_type("GOVERNMENT_RESPONSE_FUNDED")),
        "heat_responses_have_threshold_ancestors": heat_response_causality_valid,
        "government_silver_remaining": world.economy.government_silver,
    }


def _director_metrics(world, years: int) -> dict:
    """Measure liveness/density on narrative and structural events only."""
    ticks_per_day = (world.config["time"]["hours_per_day"]
                     // world.config["time"]["hours_per_tick"])
    narrative = [event for event in world.event_log
                 if event.event_class in {"narrative", "structural"}]
    total_days = years * world.config["time"]["days_per_year"]
    warmup_day = min(60, total_days)
    event_days = sorted({event.tick // ticks_per_day for event in narrative
                         if event.tick // ticks_per_day > warmup_day})
    boundaries = [warmup_day, *event_days, total_days]
    gaps = [right - left for left, right in zip(boundaries, boundaries[1:])]
    maximum_gap = max(gaps, default=max(0, total_days - warmup_day))
    days_per_year = world.config["time"]["days_per_year"]
    months_per_year = 12
    month_count = years * months_per_year
    monthly_counts = [0] * month_count
    for event in narrative:
        day_index = max(0, event.tick // ticks_per_day - 1)
        year_index, day_of_year = divmod(day_index, days_per_year)
        month_in_year = min(months_per_year - 1,
                            day_of_year * months_per_year // days_per_year)
        month = year_index * months_per_year + month_in_year
        if month < month_count:
            monthly_counts[month] += 1
    typical = sorted(monthly_counts)
    median_density = (typical[len(typical) // 2] if len(typical) % 2 else
                      (typical[len(typical) // 2 - 1] + typical[len(typical) // 2]) / 2) \
        if typical else 0
    density_floor = median_density * 0.25
    failing_months = [index + 1 for index, count in enumerate(monthly_counts)
                      if count < density_floor]
    return {
        "notable_event_count": len(narrative),
        "notable_event_days": len(event_days),
        "max_gap_days": maximum_gap,
        "monthly_notable_events": monthly_counts,
        "median_monthly_notable_events": median_density,
        "density_floor_events": density_floor,
        "density_failing_months": failing_months,
        "liveness_pass": maximum_gap <= 14,
        "liveness_7_days_pass": maximum_gap <= 7,
        "density_pass": not failing_months and median_density >= 30,
        "density_minimum_pass": median_density >= 30,
        "director_injections": len(world.event_log.by_type("DIRECTOR_INJECTION")),
        "director_enabled": world.director.settings["enabled"],
    }


def _battle_metrics(world) -> dict:
    battles = world.event_log.by_type("BATTLE")
    qualifying = [event for event in battles
                  if event.data.get("legend_count", 0) >= 2
                  or sum(len(row) for row in event.data.get("ships_by_side", [])) >= 10]
    causal = True
    political_aftermath = True
    for battle in battles:
        descendants = []
        pending = [battle.id]
        visited = set()
        while pending:
            event_id = pending.pop()
            if event_id in visited:
                continue
            visited.add(event_id)
            for child in world.event_log.consequences(event_id):
                descendants.append(child)
                pending.append(child.id)
        if (not battle.caused_by
                or not any(event.type == "BATTLE_ROUND" for event in descendants)
                or not any(event.type == "BATTLE_CONCLUDED" for event in descendants)):
            causal = False
        if not any(event.type == "HEAT_CHANGED"
                   and event.data.get("reason") in {
                       "set_piece_battle_defeat", "set_piece_battle_stalemate"}
                   for event in descendants):
            political_aftermath = False
    return {
        "set_piece_battles": len(battles),
        "qualifying_set_piece_battles": len(qualifying),
        "legend_duels": len(world.event_log.by_type("LEGEND_DUEL")),
        "battle_causal_chains_valid": causal,
        "battle_political_aftermath_causal": political_aftermath,
        "battle_casualties": len(world.event_log.by_type("BATTLE_CASUALTY")),
        "faction_power_vacuums": len(world.event_log.by_type("FACTION_POWER_VACUUM")),
    }


def _year_diagnostics(world, start_tick: int, end_tick: int, start_event: int,
                     previous_system_calls: dict[str, int]) -> tuple[dict, dict[str, int]]:
    """Build a compact year sample without retaining another copy of event records."""
    events = world.event_log.since(start_event)
    yearly = [event for event in events if start_tick < event.tick <= end_tick]
    actions = {}
    for event in yearly:
        if event.type != "AGENT_ACTION":
            continue
        action_id = event.data["action_id"]
        actor_id = event.actors[0]["entity_id"] if event.actors else "unknown"
        actor = world.entities.get(actor_id) or world.archive.get(actor_id) or {}
        tier = actor.get("tier", "unknown")
        row = actions.setdefault(action_id, {})
        row[tier] = row.get(tier, 0) + 1

    ships = {kind: 0 for kind in ("merchant", "pirate", "marine", "privateer", "faction_fleet", "unknown")}
    for ship in world.entities.values():
        if ship.get("kind") != "ship" or ship.get("sunk") or ship.get("state") == "sunk":
            continue
        owner_type = ship.get("owner_type")
        crew = world.crew_ships.crews.get(ship.get("crew_id"), {})
        faction_id = crew.get("faction_id")
        faction = world.politics.factions.get(faction_id, {}) if faction_id else {}
        faction_kind = str(faction.get("kind", "")).lower()
        if faction_kind == "marine":
            owner_type = "marine"
        elif faction_kind in {"merchant", "merchant_league"}:
            owner_type = "merchant"
        elif faction_kind in {"pirate", "raider"}:
            owner_type = "pirate"
        elif faction_kind == "privateer":
            owner_type = "privateer"
        elif faction_id:
            owner_type = "faction_fleet"
        elif any(row.get("ship_id") == ship.get("id")
                 for row in world.economy.merchants.values()):
            owner_type = "merchant"
        elif owner_type not in ships:
            owner_type = "unknown"
        ships[owner_type] += 1

    characters = {}
    living = [row for row in world.entities.values()
              if row.get("kind") == "character" and row.get("death_tick") is None]
    for character in living:
        key = f"{character.get('tier', 'unknown')}:{character.get('role', 'unknown')}"
        characters[key] = characters.get(key, 0) + 1
    major_plus = [row for row in living if row.get("tier") in {"MAJOR", "LEGEND"}]
    decision_ready = [row for row in major_plus if row.get("id") in world.decisions.agents]
    valid_wakeup = [row for row in decision_ready
                    if type(world.decisions.agents[row["id"]].get("next_decision_tick")) is int]
    non_idle = [row for row in major_plus
                if (world.decisions.agents.get(row.get("id"), {}).get("plan") or {}).get("status")
                == "active"]
    call_counts = {}
    zero_call_windows = []
    days_per_year = world.config["time"]["days_per_year"]
    ticks_per_day = (world.config["time"]["hours_per_day"]
                     // world.config["time"]["hours_per_tick"])
    for name in ("economy", "ai", "encounters", "factions", "director", "heat", "memory", "lod"):
        stats = world.system_diagnostics.get(name, {})
        ticks = [tick for tick in stats.get("call_ticks", ()) if start_tick < tick <= end_tick]
        call_counts[name] = {"call_count": len(ticks), "last_fired_tick": ticks[-1] if ticks else None}
        if ticks:
            boundaries = [start_tick, *ticks, end_tick]
            max_gap = max((right - left for left, right in zip(boundaries, boundaries[1:])),
                          default=0)
        else:
            max_gap = end_tick - start_tick
        if max_gap >= 30 * ticks_per_day:
            zero_call_windows.append(name)

    def count_types(*types: str) -> int:
        return sum(event.type in types for event in yearly)

    event_classes = {name: sum(getattr(event, "event_class", "unclassified") == name
                               for event in yearly)
                     for name in ("routine", "narrative", "structural", "unclassified")}
    deaths_by_cause = {}
    for event in yearly:
        if event.type == "CHARACTER_DIED":
            cause = str(event.data.get("cause", "unknown"))
            deaths_by_cause[cause] = deaths_by_cause.get(cause, 0) + 1
    vendettas = count_types("VENDETTA_DECLARED")
    vendetta_action_events = [event for event in yearly
                              if event.type in {"GRUDGE_PURSUED", "FEUD_RAID_ATTEMPTED",
                                                "ENCOUNTER_RESOLVED", "BATTLE_CONCLUDED", "BATTLE"}]
    vendetta_actions = (sum(
        _has_event_ancestor(world.event_log, event, "VENDETTA_DECLARED")
        or event.data.get("vendetta_id") in {
            row.id for row in yearly if row.type == "VENDETTA_DECLARED"}
        for event in vendetta_action_events) if vendettas else 0)
    lifecycle_diagnostics = [row for row in world.lifecycle_diagnostics
                             if start_tick < row["tick"] <= end_tick]
    stale_wakeups = [row for row in lifecycle_diagnostics
                     if row["code"] == "STALE_WAKEUP"]
    actor_counts = _actor_band_counts(world)
    actor_bounds = _actor_band_bounds(world)
    actor_budget_pass = bool(actor_bounds) and all(
        actor_bounds[band]["minimum"] <= count <= actor_bounds[band]["maximum"]
        for band, count in actor_counts.items())
    next_previous = dict(previous_system_calls)
    for name, stats in world.system_diagnostics.items():
        next_previous[name] = int(stats["call_count"])
    return ({
        "year": max(1, end_tick // (days_per_year * ticks_per_day)),
        "tick_range": [start_tick + 1, end_tick],
        "decision_scoring": {
            "executed_by_action_and_tier": actions,
            "scored_decisions": getattr(world, "decision_score_diagnostics", {}).get(
                "scored_decisions", 0),
            "scored_by_tier": getattr(world, "decision_score_diagnostics", {}).get("by_tier", {}),
        },
        "alive_ships_by_owner_type": ships,
        "alive_characters_by_tier_and_role": characters,
        "births_or_recruits": count_types("CHARACTER_GENERATED", "ROOKIE_SPAWNED", "RECRUITED"),
        "deaths_by_cause": deaths_by_cause,
        "major_plus_with_valid_next_decision_tick": len(valid_wakeup) / len(major_plus)
        if major_plus else None,
        "major_plus_with_non_idle_plan": len(non_idle) / len(major_plus) if major_plus else None,
        "systems": call_counts,
        "system_zero_call_over_30_days": zero_call_windows,
        "events_by_class": event_classes,
        "vendettas_created": vendettas,
        "vendettas_converted_to_action": vendetta_actions if vendettas else 0,
        "lifecycle_diagnostics": lifecycle_diagnostics,
        "stale_wakeups": len(stale_wakeups),
        "actor_bands": {
            "counts": actor_counts,
            "bounds": actor_bounds,
            "within_bounds": actor_budget_pass,
        },
    }, next_previous)


def _write_diagnostics(seed: int, years: list[dict]) -> dict[str, str]:
    report = {"seed": seed, "years": years,
              "zero_call_systems": sorted({name for row in years
                                            for name in row["system_zero_call_over_30_days"]})}
    output = Path("reports")
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / f"diag_{seed}.json"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                         encoding="utf-8", newline="\n")
    lines = [f"# Simulation diagnostics — seed {seed}", ""]
    for row in years:
        lines.extend([f"## Year {row['year']}", "",
                      f"- Events by class: `{json.dumps(row['events_by_class'], sort_keys=True)}`",
                      f"- Alive ships by owner type: `{json.dumps(row['alive_ships_by_owner_type'], sort_keys=True)}`",
                      f"- Alive characters (tier:role): `{json.dumps(row['alive_characters_by_tier_and_role'], sort_keys=True)}`",
                      f"- Births/recruits: {row['births_or_recruits']}; deaths by cause: `{json.dumps(row['deaths_by_cause'], sort_keys=True)}`",
                      f"- Actor-band budgets pass: {row['actor_bands']['within_bounds']}; counts/bounds: `{json.dumps(row['actor_bands'], sort_keys=True)}`",
                      f"- Major+ valid wake fraction: {row['major_plus_with_valid_next_decision_tick']}; active-plan fraction: {row['major_plus_with_non_idle_plan']}",
                      f"- Decisions scored: {row['decision_scoring']['scored_decisions']}; executed by action/tier: `{json.dumps(row['decision_scoring']['executed_by_action_and_tier'], sort_keys=True)}`",
                      f"- Candidate scores by action: `{json.dumps(row['decision_scoring'].get('candidates_scored_by_action', {}), sort_keys=True)}`; scored by tier: `{json.dumps(row['decision_scoring']['scored_by_tier'], sort_keys=True)}`",
                      f"- System zero-call windows (>30 days): {', '.join(row['system_zero_call_over_30_days']) or 'none'}",
                      f"- Vendettas created/converted: {row['vendettas_created']}/{row['vendettas_converted_to_action']}", ""])
        if row.get("lifecycle_diagnostics"):
            lines.extend([f"- Lifecycle diagnostics (including STALE_WAKEUP): `{json.dumps(row['lifecycle_diagnostics'], sort_keys=True)}`", ""])
    if report["zero_call_systems"]:
        lines.extend(["## Failed systems", "", ", ".join(report["zero_call_systems"]), ""])
    markdown_path = output / f"diag_{seed}.md"
    markdown_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return {"json": str(json_path), "markdown": str(markdown_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a deterministic seed batch")
    parser.add_argument("--seeds", type=int, default=1, help="number of sequential seeds")
    parser.add_argument("--years", type=int, default=0, help="years per seed")
    parser.add_argument("--director", choices=("on", "off", "config"), default="config",
                        help="story director setting for every seed (default: config)")
    parser.add_argument("--progress", action="store_true",
                        help="write one JSON progress record per completed year to stderr")
    args = parser.parse_args(argv)
    if args.seeds <= 0 or args.years < 0:
        parser.error("--seeds must be positive and --years nonnegative")
    results = []
    for seed in range(args.seeds):
        run_started = time.perf_counter()
        enabled = None if args.director == "config" else args.director == "on"
        world = WorldState.create(seed, director_enabled=enabled)
        hours = world.config["time"]["hours_per_day"] * world.config["time"]["days_per_year"]
        ticks_per_year = hours // world.config["time"]["hours_per_tick"]
        annual_trade = []
        diagnostic_years = []
        previous_trade_count = 0
        previous_trade_quantity = 0
        previous_event_cursor = 0
        previous_scored = 0
        previous_scored_by_tier = {}
        previous_scored_by_action = {}
        previous_system_calls = {}
        for year in range(1, args.years + 1):
            year_started = time.perf_counter()
            world.run_until(year * ticks_per_year)
            world.economy.ledger.assert_balanced(world.economy)
            diag, previous_system_calls = _year_diagnostics(
                world, (year - 1) * ticks_per_year, year * ticks_per_year,
                previous_event_cursor, previous_system_calls)
            scoring = getattr(world, "decision_score_diagnostics", {})
            current_scored = scoring.get("scored_decisions", 0)
            current_by_tier = scoring.get("by_tier", {})
            diag["decision_scoring"]["scored_decisions"] = current_scored - previous_scored
            diag["decision_scoring"]["scored_by_tier"] = {
                tier: count - previous_scored_by_tier.get(tier, 0)
                for tier, count in current_by_tier.items()
                if count - previous_scored_by_tier.get(tier, 0)
            }
            current_by_action = scoring.get("by_action", {})
            diag["decision_scoring"]["candidates_scored_by_action"] = {
                action: values.get("candidates_scored", 0)
                - previous_scored_by_action.get(action, 0)
                for action, values in current_by_action.items()
                if values.get("candidates_scored", 0)
                - previous_scored_by_action.get(action, 0)
            }
            previous_scored = current_scored
            previous_scored_by_tier = dict(current_by_tier)
            previous_scored_by_action = {
                action: values.get("candidates_scored", 0)
                for action, values in current_by_action.items()
            }
            previous_event_cursor = len(world.event_log)
            diagnostic_years.append(diag)
            trades = world.event_log.by_type("MARKET_TRADE")
            trade_count = len(trades)
            trade_quantity = sum(event.data["quantity_milli"] for event in trades)
            annual_trade.append({
                "year": year,
                "trade_count": trade_count - previous_trade_count,
                "quantity_milli": trade_quantity - previous_trade_quantity,
            })
            previous_trade_count = trade_count
            previous_trade_quantity = trade_quantity
            if args.progress:
                print(json.dumps({
                    "seed": seed,
                    "year": year,
                    "year_seconds": round(time.perf_counter() - year_started, 3),
                    "run_seconds": round(time.perf_counter() - run_started, 3),
                    "tick": world.clock.tick,
                    "event_count": len(world.event_log),
                    "named_characters": (
                        sum(row.get("kind") == "character" for row in world.entities.values())
                        + sum(row.get("kind") == "character" for row in world.archive.values())
                        + len(world.lod.named_records)
                    ),
                    "scored_decisions": diag["decision_scoring"]["scored_decisions"],
                }, sort_keys=True), file=sys.stderr, flush=True)
        diagnostic_paths = _write_diagnostics(seed, diagnostic_years)
        world.event_log.validate_causality()
        world.economy.validate_world(world)
        world.crew_ships.validate_world(world)
        world.politics.validate_world(world)
        balances = {
            "silver_expected": (world.economy.ledger.opening_silver
                                + world.economy.ledger.silver_sources
                                - world.economy.ledger.silver_sinks),
            "silver_actual": world.economy._total_silver(),
            "goods_expected": {
                goods_id: world.economy.ledger.opening_goods[goods_id]
                + world.economy.ledger.goods_sources[goods_id]
                - world.economy.ledger.goods_sinks[goods_id]
                for goods_id in sorted(world.economy.goods)
            },
            "goods_actual": world.economy._total_goods(),
        }
        trade_stable, trade_ratio_permille = _trade_volume_stable(annual_trade)
        event_compaction = world.event_log.compaction_stats()
        named_characters = (
            sum(entity.get("kind") == "character" for entity in world.entities.values())
            + sum(entity.get("kind") == "character" for entity in world.archive.values())
            + len(world.lod.named_records)
        )
        results.append({"seed": seed, "tick": world.clock.tick,
                        "run_seconds": round(time.perf_counter() - run_started, 3),
                        "named_characters": named_characters,
                        "island_count": len(world.geography.islands),
                        "regional_coarse_summaries": sum(
                            len(rows) for rows in world.lod.regional_history.values()),
                            "event_count": len(world.event_log),
                            "event_compaction": event_compaction,
                        "annual_trade": annual_trade,
                        "trade_stable": trade_stable,
                        "trade_final_to_first_permille": trade_ratio_permille,
                            "ledger_balanced": (balances["silver_expected"] == balances["silver_actual"]
                                              and balances["goods_expected"] == balances["goods_actual"]),
                             "terminal_actor_violations": _terminal_actor_violations(world.event_log),
                              "actor_bands_within_budget": all(
                                  row["actor_bands"]["within_bounds"]
                                  for row in diagnostic_years) if diagnostic_years else None,
                             "merchant_raid_losses": _merchant_raid_loss_metrics(world),
                             "turnover": {
                                 "major_plus_deaths_or_retirements": sum(
                                     event.type in {"CHARACTER_DIED", "CHARACTER_RETIRED"}
                                     and any((world.archive.get(target["entity_id"])
                                              or world.entities.get(target["entity_id"])
                                              or {}).get("tier") in {"MAJOR", "LEGEND"}
                                             for target in event.targets)
                                     for event in world.event_log),
                                 "major_plus_risers": len(world.event_log.by_type("ROOKIE_PROMOTED")),
                                 "rookies_promoted_via_logged_deeds": sum(
                                     len(event.data.get("deed_event_ids", [])) >= 2
                                     for event in world.event_log.by_type("ROOKIE_PROMOTED")),
                             },
                            "ledger_balances": balances,
                            "politics": _politics_metrics(world),
                            "battles": _battle_metrics(world),
                            "diagnostic_reports": diagnostic_paths,
                            "diagnostic_zero_call_systems": sorted({name for row in diagnostic_years
                                for name in row["system_zero_call_over_30_days"]}),
                            "director": _director_metrics(world, args.years),
                           "event_log_sha256": _event_log_sha256(world.event_log)})
    stability_results = [run["trade_stable"] for run in results]
    acceptance = {
        "ledger_conserved": all(run["ledger_balanced"] for run in results),
        "no_post_terminal_actor_actions": all(
            not run["terminal_actor_violations"] for run in results),
        "actor_bands_within_budget": (
            all(run["actor_bands_within_budget"] for run in results)
            if args.years > 0 else None),
        "turnover_acceptance": (
            all(run["turnover"]["major_plus_deaths_or_retirements"] >= 6
                and run["turnover"]["major_plus_risers"] >= 6
                and run["turnover"]["rookies_promoted_via_logged_deeds"] >= 2
                for run in results) if args.years >= 10 else None),
        "pirate_merchant_loss_share_acceptance": (
            all(run["merchant_raid_losses"]["pirate_loss_share_permille"] is not None
                and run["merchant_raid_losses"]["pirate_loss_share_permille"] >= 300
                for run in results) if args.years >= 10 else None),
        "trade_volume_stable": (all(stability_results) if args.years >= 3 else None),
        "founded_and_dissolved_factions": (
            all(run["politics"]["factions_founded"] > 0 for run in results)
            and any(run["politics"]["factions_dissolved"] > 0 for run in results)
            if args.years >= 10 else None),
        "alliances_betrayals_and_wars_causal": (
            all(run["politics"]["treaty_transition_chain_valid"] for run in results)
            and all(run["politics"]["hostile_betrayals_causal"] for run in results)
            and any(run["politics"]["alliances_formed"] > 0 for run in results)
            and any(run["politics"]["hostile_treaty_betrayals"] > 0 for run in results)
            and any(run["politics"]["wars_declared"] > 0 for run in results)
            if args.years >= 10 else None),
        "no_duplicate_alliance_events": (
            all(not run["politics"]["duplicate_alliance_events"]
                and run["politics"]["treaty_transition_chain_valid"] for run in results)
            if args.years >= 10 else None),
        "heat_response_causal": (
            all(run["politics"]["heat_responses_have_threshold_ancestors"] for run in results)
            if args.years >= 10 else None),
        "set_piece_battles_most_runs": (
            sum(run["battles"]["qualifying_set_piece_battles"] > 0 for run in results)
            * 100 >= len(results) * 80
            and all(run["battles"]["battle_causal_chains_valid"] for run in results)
            if args.years >= 10 else None),
        "battle_aftermath_causal": (
            all(run["battles"]["battle_political_aftermath_causal"]
                for run in results if run["battles"]["set_piece_battles"] > 0)
            and any(run["battles"]["set_piece_battles"] > 0 for run in results)
            if args.years >= 10 else None),
        "lod_scale_target": (
            all(run["named_characters"] >= 300 and run["island_count"] >= 40
                for run in results)
            if args.years >= 10 else None),
        "lod_performance_target": (
            all(run["run_seconds"] < 300 for run in results)
            if args.years >= 10 else None),
        "director_off_liveness_density": (
            (sum(run["director"]["liveness_7_days_pass"] for run in results) * 100
             >= len(results) * 95
             and all(run["director"]["liveness_pass"] and run["director"]["density_pass"]
                     for run in results))
            if args.years >= 1 and args.director == "off" else None),
        "director_on_liveness_density": (
            (sum(run["director"]["liveness_7_days_pass"] for run in results) * 100
             >= len(results) * 95
             and all(run["director"]["liveness_pass"] and run["director"]["density_pass"]
                     for run in results))
            if args.years >= 1 and args.director == "on" else None),
        "narrative_liveness_95_percent_within_7_days": (
            sum(run["director"]["liveness_7_days_pass"] for run in results) * 100
            >= len(results) * 95 if args.years >= 1 else None),
        "narrative_liveness_all_within_14_days": (
            all(run["director"]["liveness_pass"] for run in results)
            if args.years >= 1 else None),
        "narrative_density_25_percent_floor_and_median_30": (
            all(run["director"]["density_pass"] for run in results)
            if args.years >= 1 else None),
        "routine_event_log_reduction_80_percent": (
            all(run["event_compaction"]["bytes_reduced_percent"] >= 80
                for run in results)
            if args.years >= 10 else None),
        "diagnostic_systems_called": all(not run["diagnostic_zero_call_systems"]
                                          for run in results),
    }
    print(json.dumps({"runs": results, "acceptance": acceptance}, sort_keys=True))
    if args.years >= 3 and not acceptance["trade_volume_stable"]:
        return 1
    if args.years >= 10 and not all(value for key, value in acceptance.items()
                                    if key not in {"ledger_conserved", "trade_volume_stable"}
                                    and value is not None):
        return 1
    if args.years >= 1 and args.director == "off" \
            and not acceptance["director_off_liveness_density"]:
        return 1
    if args.years >= 1 and args.director == "on" \
            and not acceptance["director_on_liveness_density"]:
        return 1
    if args.years >= 1 and not (
            acceptance["narrative_liveness_95_percent_within_7_days"]
            and acceptance["narrative_liveness_all_within_14_days"]
            and acceptance["narrative_density_25_percent_floor_and_median_30"]):
        return 1
    if args.years >= 10 and not acceptance["routine_event_log_reduction_80_percent"]:
        return 1
    if not acceptance["diagnostic_systems_called"]:
        for run in results:
            if run["diagnostic_zero_call_systems"]:
                print(f"seed {run['seed']} had systems with no calls for a 30-day window: "
                      f"{', '.join(run['diagnostic_zero_call_systems'])}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
