"""Deterministic seed batch with economy acceptance metrics."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any
import uuid

from sim.core.world import WorldState
from sim.core.ownership import ship_is_commandable
from sim.core.actor_bands import classify_ship_actor_band


def _screen_stat(values: list[int]) -> dict[str, Any]:
    """Descriptive fixed-seed summary plus approximate 95% mean and Wilson bounds."""
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError("Screen metrics must be nonnegative integer counts")
    count = len(values)
    if not count:
        return {"n": 0, "values": [], "mean": None, "median": None,
                "minimum": None, "maximum": None,
                "normal_approximation_95_ci_mean": None,
                "wilson_95_ci_nonzero_seed_fraction": None}
    mean = sum(values) / count
    median_values = sorted(values)
    median = (median_values[count // 2] if count % 2 else
              (median_values[count // 2 - 1] + median_values[count // 2]) / 2)
    if count > 1:
        variance = sum((value - mean) ** 2 for value in values) / (count - 1)
        margin = 1.96 * math.sqrt(variance / count)
        mean_ci = [max(0.0, mean - margin), mean + margin]
    else:
        mean_ci = None
    successes = sum(value > 0 for value in values)
    z = 1.96
    denominator = 1 + z * z / count
    center = (successes / count + z * z / (2 * count)) / denominator
    half = z * math.sqrt(successes * (count - successes) / count**3
                          + z * z / (4 * count**2)) / denominator
    return {"n": count, "values": values, "mean": mean, "median": median,
            "minimum": min(values), "maximum": max(values),
            "normal_approximation_95_ci_mean": mean_ci,
            "wilson_95_ci_nonzero_seed_fraction": [max(0.0, center - half),
                                                   min(1.0, center + half)]}


def _acceptance_gate_report(acceptance: dict[str, bool | None], *, years: int,
                            director: str) -> dict[str, dict[str, Any]]:
    """Attach an explicit status, required horizon/configuration and reason to every gate."""
    requirements = {
        "ledger_conserved": ("every run", "run-level ledger conservation"),
        "no_post_terminal_actor_actions": ("every run", "run-level terminal-action audit"),
        "actor_bands_within_budget": ("1 year", "one-year actor-band acceptance screen"),
        "turnover_acceptance": ("10 years", "turnover acceptance requires the 10-year horizon"),
        "pirate_merchant_loss_share_acceptance": ("10 years", "loss-share acceptance requires the 10-year horizon"),
        "trade_volume_stable": ("3 years", "trade stability requires at least three annual samples"),
        "founded_and_dissolved_factions": ("10 years", "political lifecycle acceptance requires the 10-year horizon"),
        "alliances_betrayals_and_wars_causal": ("10 years", "alliance/betrayal/war acceptance requires the 10-year horizon"),
        "no_duplicate_alliance_events": ("10 years", "alliance-transition acceptance requires the 10-year horizon"),
        "heat_response_causal": ("10 years", "heat response acceptance requires the 10-year horizon"),
        "set_piece_battles_most_runs": ("10 years", "set-piece frequency acceptance requires the 10-year horizon"),
        "battle_aftermath_causal": ("10 years", "battle aftermath acceptance requires the 10-year horizon"),
        "lod_scale_target": ("10 years", "LOD scale acceptance requires the 10-year horizon"),
        "lod_performance_target": ("10 years", "LOD performance acceptance requires the 10-year horizon"),
        "director_off_liveness_density": ("1 year, director off", "director-off screen is mode-specific"),
        "director_on_liveness_density": ("1 year, director on", "director-on screen is mode-specific"),
        "narrative_liveness_95_percent_within_7_days": ("1 year", "one-year narrative liveness screen"),
        "narrative_liveness_all_within_14_days": ("1 year", "one-year narrative liveness screen"),
        "narrative_density_25_percent_floor_and_median_30": ("1 year", "one-year narrative density screen"),
        "routine_event_log_reduction_80_percent": ("10 years", "formal event-byte reduction requires the 10-year horizon"),
        "diagnostic_systems_called": ("every run", "run-level diagnostic system call audit"),
    }
    if set(acceptance) != set(requirements):
        raise ValueError("Acceptance gate requirements do not cover the reported gate set")
    gates = {}
    for name, value in acceptance.items():
        horizon, description = requirements[name]
        applicable = True
        not_applicable_kind = None
        if name.startswith("director_off_"):
            applicable = director == "off" and years >= 1
            if director != "off":
                not_applicable_kind = "configuration"
            elif years < 1:
                not_applicable_kind = "horizon"
        elif name.startswith("director_on_"):
            applicable = director == "on" and years >= 1
            if director != "on":
                not_applicable_kind = "configuration"
            elif years < 1:
                not_applicable_kind = "horizon"
        elif name in {"actor_bands_within_budget", "narrative_liveness_95_percent_within_7_days",
                      "narrative_liveness_all_within_14_days",
                      "narrative_density_25_percent_floor_and_median_30"}:
            applicable = years >= 1
            if not applicable:
                not_applicable_kind = "horizon"
        elif name == "trade_volume_stable":
            applicable = years >= 3
            if not applicable:
                not_applicable_kind = "horizon"
        elif horizon == "10 years":
            applicable = years >= 10
            if not applicable:
                not_applicable_kind = "horizon"
        if value is None or not applicable:
            not_applicable_kind = not_applicable_kind or "horizon"
            reason = (f"N/A: {description}; current run is {years} year(s), "
                      f"director={director}.")
            status = "N/A"
        else:
            status = "pass" if value else "fail"
            reason = (f"{description}; evaluated over {years} year(s), "
                      f"director={director}; "
                      + ("predicate satisfied." if value else "predicate not satisfied."))
        gates[name] = {"status": status, "value": value,
                       "required_horizon": horizon,
                       "not_applicable_kind": not_applicable_kind,
                       "reason": reason}
    return gates


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


def _dream_story_funnel(events) -> dict[str, Any]:
    """Report selected dream plans, attempted steps, blockers, failures and resolutions."""
    event_types = {
        "DREAM_PLAN_CREATED", "DREAM_PLAN_COMPLETED", "DREAM_PLAN_FAILED",
        "DREAM_PLAN_ABANDONED", "PLAN_STEP_STARTED", "PLAN_STEP_BLOCKED",
        "PLAN_STEP_FAILED", "PLAN_STEP_COMPLETED",
    }
    grouped: dict[str, dict[str, dict[str, int]]] = {}
    actor_action: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    by_reason: dict[str, int] = {}
    for event in events:
        if event.type not in event_types:
            continue
        dream_kind = str(event.data.get("dream_kind", "unknown"))
        actor_id = next((row.get("entity_id") for row in event.actors
                         if row.get("role") in {"dreamer", "planner"}), None)
        step_id = event.data.get("step_id")
        reason = event.data.get("reason")
        step_key = str(step_id or "none")
        reason_key = str(reason or "none")
        group_key = "|".join((step_key, reason_key))
        step_groups = grouped.setdefault(dream_kind, {}).setdefault(event.type, {})
        step_groups[group_key] = step_groups.get(group_key, 0) + 1
        detail_key = (dream_kind, event.type, str(actor_id or "unknown"),
                      step_key, reason_key)
        detail = actor_action.setdefault(detail_key, {
            "dream_kind": dream_kind, "event_type": event.type,
            "actor_id": actor_id, "step_id": step_id, "reason": reason,
            "count": 0, "sample_event_ids": [], "sample_caused_by": [],
        })
        detail["count"] += 1
        if len(detail["sample_event_ids"]) < 3:
            detail["sample_event_ids"].append(event.id)
            detail["sample_caused_by"].append(list(event.caused_by))
        if event.type in {"PLAN_STEP_BLOCKED", "PLAN_STEP_FAILED",
                          "DREAM_PLAN_FAILED", "DREAM_PLAN_ABANDONED"}:
            key = "|".join((event.type, dream_kind, str(step_id or "none"),
                            str(reason or "unspecified")))
            by_reason[key] = by_reason.get(key, 0) + 1
    totals = {event_type: sum(sum(by_type.get(event_type, {}).values())
                              for by_type in grouped.values())
              for event_type in sorted(event_types)}
    return {"counts_by_event_type": totals,
            "by_dream_kind": {kind: {event_type: dict(sorted(counts.items()))
                                      for event_type, counts in sorted(by_type.items())}
                              for kind, by_type in sorted(grouped.items())},
            "by_actor_action_reason": [actor_action[key] for key in sorted(actor_action)],
            "blocked_failed_abandoned_by_reason": dict(sorted(by_reason.items()))}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _source_metadata() -> dict[str, Any]:
    """Identify the checked-out simulation sources without requiring a clean tree."""
    root = Path(__file__).resolve().parents[2]
    files = sorted(path for path in (root / "sim").rglob("*")
                   if path.is_file() and path.suffix.lower() in {".py", ".json"})
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True,
            capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        revision = None
    return {"git_revision": revision, "sim_source_tree_sha256": digest.hexdigest(),
            "sim_source_file_count": len(files)}


def _actor_band_status(counts: dict[str, int], bounds: dict[str, dict[str, int]]) -> dict:
    bands = {
        band: {"count": count, **bounds[band],
               "pass": bounds[band]["minimum"] <= count <= bounds[band]["maximum"]}
        for band, count in counts.items()
    }
    return {"counts": counts, "bounds": bounds, "bands": bands,
            "within_bounds": bool(bands) and all(row["pass"] for row in bands.values())}


def _pending_prize_crew_audit(world) -> list[dict[str, Any]]:
    """Explain current pending-prize state and actual same-crew arrival opportunities."""
    events = list(world.event_log)
    capture_by_ship = {}
    arrivals_by_location: dict[str, list[tuple[int, Any]]] = {}
    for index, event in enumerate(events):
        if event.type == "SHIP_CAPTURED":
            for target in event.targets:
                ship_id = target.get("entity_id")
                if ship_id:
                    capture_by_ship[ship_id] = (index, event)
        elif event.type == "VOYAGE_ARRIVED" and event.location is not None:
            arrivals_by_location.setdefault(event.location, []).append((index, event))
    audits = []
    for ship_id, ship in sorted(world.entities.items()):
        if (ship.get("kind") != "ship" or ship.get("sunk")
                or ship.get("state") == "sunk"
                or ship.get("captured_by_crew_id") is None):
            continue
        controller_id = ship.get("controller_crew_id")
        controller = (world.crew_ships.crews.get(controller_id)
                      if isinstance(controller_id, str) else None)
        onboard_id = ship.get("crew_id")
        onboard = (world.crew_ships.crews.get(onboard_id)
                   if isinstance(onboard_id, str) else None)
        former_captain_id = onboard.get("captain_id") if onboard else None
        former_captain = world.entities.get(former_captain_id, {})
        in_transit = ship_id in world.travel.transits
        location_error = None
        try:
            location = world.location_of(ship_id)
        except (KeyError, TypeError, ValueError) as exc:
            location = {"kind": "unknown", "id": None}
            location_error = type(exc).__name__
        capture_position, capture_event = capture_by_ship.get(ship_id, (-1, None))

        recorded_arrivals = []
        if location.get("kind") == "island":
            for position, event in arrivals_by_location.get(location.get("id"), ()):
                if position <= capture_position:
                    continue
                for actor in event.actors:
                    arriving_id = actor.get("entity_id")
                    arriving_ship = world.entities.get(arriving_id, {})
                    if (actor.get("role") == "vessel"
                            and arriving_ship.get("kind") == "ship"
                            and arriving_ship.get("controller_crew_id") == controller_id):
                        recorded_arrivals.append({
                            "event_id": event.id, "ship_id": arriving_id,
                            "currently_commandable": ship_is_commandable(world, arriving_id),
                        })

        captured_candidate = (world.encounters._prize_captain_candidate(world, onboard)
                              if onboard is not None else None)
        controller_candidate = (world.encounters._prize_captain_candidate(world, controller)
                                if controller is not None
                                and len(controller.get("roster", ())) > 1 else None)
        controller_arrival_candidates = []
        if controller is not None and location.get("kind") == "island":
            primary_controller_ship = next(iter(controller.get("ship_ids", ())), None)
            for arriving_id in controller.get("ship_ids", ()):
                arriving = world.entities.get(arriving_id, {})
                try:
                    arriving_location = world.location_of(arriving_id)
                except (KeyError, TypeError, ValueError):
                    arriving_location = None
                if (arriving_id != ship_id and ship_is_commandable(world, arriving_id)
                        and arriving_location == location
                        and primary_controller_ship == arriving_id):
                    candidate = world.encounters._prize_captain_candidate(world, controller)
                    if candidate is not None:
                        controller_arrival_candidates.append({
                            "arriving_ship_id": arriving_id,
                            "candidate_character_id": candidate,
                        })
        local_candidate = (world.encounters._local_prize_captain_candidate(world, ship_id)
                           if location.get("kind") == "island" and not in_transit else None)

        owner = ship.get("legal_owner")
        owner_id = owner.get("id") if isinstance(owner, dict) else None
        owner_valid = (isinstance(owner, dict) and isinstance(owner_id, str)
                       and ((owner.get("kind") == "crew"
                             and owner_id in world.crew_ships.crews)
                            or (owner.get("kind") == "merchant"
                                and owner_id in world.economy.merchants)))
        linkage_valid = (onboard is not None and ship_id in onboard.get("ship_ids", ()))
        captain_link_valid = (
            former_captain_id in world.entities
            and former_captain.get("kind") == "character"
            and former_captain.get("death_tick") is None
            and former_captain_id in onboard.get("roster", ())
            and former_captain.get("crew_id") == onboard_id
            and former_captain.get("ship_id") == ship_id)
        controller_valid = (controller is not None and controller_id ==
                            ship.get("captured_by_crew_id"))
        candidate_sources = {
            "captured_crew_candidate_id": captured_candidate,
            "controller_crew_candidate_id": controller_candidate,
            "controller_crew_candidates": controller_arrival_candidates,
            "local_candidate_id": local_candidate,
        }
        blockers = []
        if location_error is not None:
            blockers.append("missing_or_invalid_captured_hull_location")
        if in_transit:
            blockers.append("captured_hull_in_transit")
        if location.get("kind") != "island":
            blockers.append("captured_hull_not_at_island")
        if not controller_valid:
            blockers.append("stale_or_missing_controller_linkage")
        if not owner_valid:
            blockers.append("invalid_or_missing_legal_owner")
        if not capture_event:
            blockers.append("missing_capture_event_evidence")
        if not linkage_valid:
            blockers.append("stale_or_missing_onboard_crew_linkage")
        if not captain_link_valid:
            blockers.append("missing_or_inactive_former_captain_linkage")
        eligible_arrivals = [row for row in recorded_arrivals
                             if row["currently_commandable"]]
        if not recorded_arrivals:
            blockers.append("no_same_location_arrival_observed_since_capture")
        elif not eligible_arrivals:
            blockers.append("same_location_arrivals_not_currently_commandable")
        if not (captured_candidate or controller_arrival_candidates or local_candidate):
            blockers.append("no_lawful_prize_captain_candidate_at_checkpoint")
        if not blockers:
            blockers.append("pending_after_eligible_arrival_and_candidate_observed")
        audits.append({
            "ship_id": ship_id,
            "capture_event_id": capture_event.id if capture_event else None,
            "location": location,
            "location_error": location_error,
            "in_transit": in_transit,
            "transit": dict(world.travel.transits[ship_id]) if in_transit else None,
            "captured_by_crew_id": ship.get("captured_by_crew_id"),
            "controller_crew_id": controller_id,
            "onboard_crew_id": onboard_id,
            "former_captain_id": former_captain_id,
            "legal_owner": dict(owner) if isinstance(owner, dict) else None,
            "controller_link_valid": controller_valid,
            "legal_owner_valid": owner_valid,
            "onboard_crew_link_valid": linkage_valid,
            "former_captain_link_valid": captain_link_valid,
            "candidate_sources": candidate_sources,
            "same_location_arrivals_since_capture": recorded_arrivals,
            "eligible_commanded_arrivals_since_capture": eligible_arrivals,
            "last_real_arrival_opportunity": eligible_arrivals[-1] if eligible_arrivals else None,
            "resolution_blockers": blockers,
            "resolution_blocker": blockers[0],
        })
    return audits


def _ship_control_audit(world, ship: dict[str, Any]) -> dict[str, Any]:
    """Explain whether an uncommandable hull has valid onboard authority or stale control."""
    ship_id = ship.get("id")
    onboard_id = ship.get("crew_id")
    onboard = world.crew_ships.crews.get(onboard_id)
    captain_id = onboard.get("captain_id") if onboard else None
    captain = world.entities.get(captain_id, {})
    onboard_authority_valid = (
        onboard is not None and ship_id in onboard.get("ship_ids", ())
        and captain_id in onboard.get("roster", ())
        and captain.get("kind") == "character" and captain.get("death_tick") is None
        and captain.get("crew_id") == onboard_id and captain.get("ship_id") == ship_id)
    controller_id = ship.get("controller_crew_id")
    if ship.get("captured_by_crew_id") is not None:
        classification = "pending_prize_crew_assignment"
    elif controller_id is None and onboard_authority_valid:
        classification = "unassigned_controller_despite_valid_onboard_authority"
    elif controller_id is None:
        classification = "unassigned_controller_without_valid_onboard_authority"
    elif controller_id not in world.crew_ships.crews:
        classification = "stale_controller_crew_reference"
    else:
        classification = "controller_differs_from_onboard_crew"
    ownership_event_id = ship.get("ownership_event_id")
    ownership_event = None
    if ownership_event_id:
        try:
            event = world.event_log.get(ownership_event_id)
            ownership_event = {
                "event_id": event.id, "event_type": event.type,
                "caused_by": list(event.caused_by),
                "method": event.data.get("method"),
                "previous_controller_crew_id": event.data.get(
                    "previous_controller_crew_id"),
                "controller_crew_id": event.data.get("controller_crew_id"),
                "new_controller_crew_id": event.data.get("new_controller_crew_id"),
                "legal_owner_kind": event.data.get("legal_owner_kind"),
                "legal_owner_id": event.data.get("legal_owner_id"),
            }
        except KeyError:
            ownership_event = {"event_id": ownership_event_id,
                               "missing_from_event_log": True}
    return {
        "classification": classification,
        "onboard_authority_valid": onboard_authority_valid,
        "onboard_crew_id": onboard_id,
        "controller_crew_id": controller_id,
        "legal_owner": dict(ship["legal_owner"])
        if isinstance(ship.get("legal_owner"), dict) else None,
        "ownership_event": ownership_event,
    }


def _actor_population_census(world, *, label: str) -> dict[str, Any]:
    """Separate physical hulls, working crews/captains, title and command by band."""
    bands: dict[str, dict[str, Any]] = {}
    uncommandable_hulls = []
    held_prisoners = {character_id: row for character_id, row in world.encounters.prisoners.items()
                      if row.get("status") == "held" and character_id in world.entities}
    pending_prize_audit = _pending_prize_crew_audit(world)
    prize_audit_by_ship = {row["ship_id"]: row for row in pending_prize_audit}
    prize_blocker_counts: dict[str, int] = {}
    for audit in pending_prize_audit:
        for reason in audit["resolution_blockers"]:
            prize_blocker_counts[reason] = prize_blocker_counts.get(reason, 0) + 1
    for ship in world.entities.values():
        if ship.get("kind") != "ship":
            continue
        crew_id = ship.get("crew_id")
        crew = world.crew_ships.crews.get(crew_id, {})
        faction_id = crew.get("faction_id")
        faction = world.politics.factions.get(faction_id, {}) if faction_id else {}
        ship_id = ship.get("id")
        band = classify_ship_actor_band(
            ship, faction_id=faction_id, faction_kind=faction.get("kind", ""),
            merchant_registered=any(row.get("ship_id") == ship_id
                                    for row in world.economy.merchants.values())) or "unclassified"
        row = bands.setdefault(band, {
            "physical_hulls": 0, "commandable_hulls": 0,
            "hulls_with_active_crew": 0, "hulls_with_living_captain": 0,
            "legal_owner_kinds": {}, "controllers_present": 0,
            "controller_ids": {}, "faction_kinds": {}, "uncommandable_reasons": {},
        })
        sunk = bool(ship.get("sunk") or ship.get("state") == "sunk")
        if sunk:
            continue
        row["physical_hulls"] += 1
        commandable = ship_is_commandable(world, ship_id)
        row["commandable_hulls"] += int(commandable)
        if not commandable:
            if not crew_id:
                reason = "no_onboard_crew"
            elif crew_id not in world.crew_ships.crews:
                reason = "onboard_crew_missing"
            elif ship.get("captured_by_crew_id") is not None:
                reason = "captured_pending_prize_crew"
            elif ship.get("controller_crew_id") != crew_id:
                reason = "onboard_crew_is_not_controller"
            else:
                reason = "other_commandability_constraint"
            reasons = row["uncommandable_reasons"]
            reasons[reason] = reasons.get(reason, 0) + 1
            uncommandable_hulls.append({
                "ship_id": ship_id, "band": band, "reason": reason,
                "onboard_crew_id": crew_id,
                "controller_crew_id": ship.get("controller_crew_id"),
                "captured_by_crew_id": ship.get("captured_by_crew_id"),
                "legal_owner": ship.get("legal_owner"),
                "ownership_event_id": ship.get("ownership_event_id"),
                "last_event_id": ship.get("last_event_id"),
                "control_audit": _ship_control_audit(world, ship),
                "prize_crew_resolution": prize_audit_by_ship.get(ship_id),
                "crew_member_dispositions": [
                    {"character_id": member_id,
                     "disposition": (
                         "missing_at_sea" if world.entities.get(member_id, {}).get(
                             "missing_at_sea") is not None else
                         "held_prisoner" if member_id in held_prisoners else
                         "deceased" if world.entities.get(member_id, {}).get(
                             "death_tick") is not None else
                         "aboard_hull" if world.entities.get(member_id, {}).get(
                             "ship_id") == ship_id else "crew_member_not_aboard"),
                     "last_event_id": world.entities.get(member_id, {}).get("last_event_id"),
                     "missing_record": (dict(world.entities.get(member_id, {}).get("missing_at_sea") or {})
                                        or None)}
                    for member_id in sorted(crew.get("roster", ()))
                ],
            })
        active_crew = bool(crew and ship_id in crew.get("ship_ids", ()))
        row["hulls_with_active_crew"] += int(active_crew)
        captain_id = crew.get("captain_id") if active_crew else None
        captain = world.entities.get(captain_id, {}) if captain_id else {}
        row["hulls_with_living_captain"] += int(
            captain.get("kind") == "character" and captain.get("death_tick") is None)
        legal_owner = ship.get("legal_owner") or {}
        owner_kind = str(legal_owner.get("kind", "unknown"))
        row["legal_owner_kinds"][owner_kind] = row["legal_owner_kinds"].get(owner_kind, 0) + 1
        controller_id = ship.get("controller_crew_id")
        if controller_id in world.crew_ships.crews:
            row["controllers_present"] += 1
        controller_key = str(controller_id or "none")
        row["controller_ids"][controller_key] = row["controller_ids"].get(controller_key, 0) + 1
        faction_kind = str(faction.get("kind", "none"))
        row["faction_kinds"][faction_kind] = row["faction_kinds"].get(faction_kind, 0) + 1
    recruitment = world.crew_ships.actor_recruitment_diagnostics
    recruitment_summary = {}
    for band in ("merchant", "pirate", "marine", "privateer"):
        row = recruitment.get(band, {"opportunities": 0, "recruited": 0, "blocked": {}})
        recruitment_summary[band] = {
            "opportunities": row["opportunities"], "recruited": row["recruited"],
            "blocked": dict(sorted(row["blocked"].items())),
        }
    living_dispositions: dict[str, int] = {}
    for character_id, person in world.entities.items():
        if person.get("kind") != "character" or person.get("death_tick") is not None:
            continue
        if person.get("missing_at_sea") is not None:
            disposition = "missing_at_sea"
        elif character_id in held_prisoners:
            disposition = "held_prisoner"
        elif person.get("crew_id") in world.crew_ships.crews:
            crew = world.crew_ships.crews[person["crew_id"]]
            if not crew.get("ship_ids"):
                disposition = "stranded_crew_member"
            elif person.get("ship_id") in crew["ship_ids"]:
                ship_id = person["ship_id"]
                ship = world.entities.get(ship_id, {})
                if ship_is_commandable(world, ship_id):
                    disposition = "aboard_commandable_hull"
                elif ship.get("captured_by_crew_id") is not None:
                    disposition = "aboard_captured_hull_pending_prize_crew"
                else:
                    disposition = "aboard_other_uncommandable_hull"
            else:
                disposition = "crew_member_not_assigned_to_hull"
        elif person.get("ship_id"):
            disposition = "aboard_without_active_crew_membership"
        else:
            disposition = "free_on_land"
        living_dispositions[disposition] = living_dispositions.get(disposition, 0) + 1
    stranded_crews = []
    for crew_id, crew in sorted(world.crew_ships.crews.items()):
        if crew.get("ship_ids"):
            continue
        member_status = {}
        member_rows = []
        for person_id in crew.get("roster", ()):
            person = world.entities.get(person_id, {})
            if person.get("missing_at_sea") is not None:
                status = "missing"
            elif person_id in held_prisoners:
                status = "held_prisoner"
            elif person and person.get("death_tick") is None:
                status = "alive_stranded"
            else:
                status = "inactive_or_missing_entity"
            member_status[status] = member_status.get(status, 0) + 1
            member_rows.append({
                "character_id": person_id, "disposition": status,
                "last_event_id": person.get("last_event_id"),
                "missing_record": dict(person.get("missing_at_sea") or {}) or None,
                "prisoner_status": held_prisoners.get(person_id, {}).get("status"),
            })
        stranded_crews.append({
            "crew_id": crew_id, "roster_count": len(crew.get("roster", ())),
            "member_dispositions": dict(sorted(member_status.items())),
            "members": member_rows,
            "stranded_location": crew.get("stranded_location"),
            "distress_days": crew.get("distress_days", 0),
            "last_event_id": crew.get("last_event_id"),
        })
    return {"label": label, "tick": world.clock.tick,
            "bands": {band: bands[band] for band in sorted(bands)},
            "uncommandable_hulls": sorted(uncommandable_hulls,
                                           key=lambda row: row["ship_id"]),
            "total_physical_hulls": sum(row["physical_hulls"] for row in bands.values()),
            "total_commandable_hulls": sum(row["commandable_hulls"] for row in bands.values()),
            "recruitment_opportunities_cumulative": recruitment_summary,
            "living_character_dispositions": dict(sorted(living_dispositions.items())),
            "living_character_count": sum(living_dispositions.values()),
            "stranded_crews": stranded_crews,
            "pending_prize_crew_hulls": pending_prize_audit,
            "pending_prize_crew_blocker_counts": dict(sorted(prize_blocker_counts.items()))}


def _actor_population_transitions(world, start_event: int) -> dict[str, Any]:
    """Summarize non-overlapping ship/captain transition event families."""
    recruited = {band: [] for band in ("merchant", "pirate", "marine", "privateer")}
    sunk = {band: [] for band in recruited}
    captures = []
    pending_prize_crews = []
    prize_crew_installations = []
    other_title_transfers = []
    prize_crew_control_recoveries = []
    ship_creations = []
    ship_sink_ids = []
    captain_deaths = []
    captain_retirements = []
    ship_loss_dispositions = []
    employment_searches = []
    employment_acceptances = []
    character_rescues = []
    missing_person_resolutions = []
    prisoner_captures = []
    prisoner_fates = []
    prisoner_resolutions = []
    for event in world.event_log.since(start_event):
        if event.type == "SHIP_CREATED":
            ship_creations.append(event.id)
        elif event.type == "SHIP_ACQUIRED":
            if event.data.get("method") == "pending_prize_crew_installed":
                prize_crew_control_recoveries.append(event.id)
            band = event.data.get("owner_type")
            budget_recruitment = "actor_budget_recruitment" in event.causes_tags
            if budget_recruitment and band in recruited:
                recruited[band].append(event.id)
            elif event.data.get("method") == "capture" or "capture" in event.causes_tags:
                # The paired SHIP_ACQUIRED record is represented by its SHIP_CAPTURED cause.
                continue
            elif band in recruited:
                other_title_transfers.append(event.id)
        elif event.type == "SHIP_CAPTURED":
            captures.append(event.id)
            if event.data.get("prize_crew_pending") is True:
                pending_prize_crews.append(event.id)
        elif event.type == "PRIZE_CREW_INSTALLED":
            prize_crew_installations.append(event.id)
        elif event.type == "SHIP_SUNK":
            ship_sink_ids.append(event.id)
            ship_id = next((row.get("entity_id") for row in event.targets
                            if row.get("role") == "sunken_ship"), None)
            band = _ship_actor_band(world, ship_id) if ship_id else None
            if band in sunk:
                sunk[band].append(event.id)
        elif event.type == "CHARACTER_DIED":
            person_id = next((row.get("entity_id") for row in event.targets
                              if row.get("role") == "deceased"), None)
            person = world.archive.get(person_id) or world.entities.get(person_id) or {}
            if person.get("role") == "captain":
                captain_deaths.append(event.id)
        elif event.type == "CHARACTER_RETIRED":
            person_id = next((row.get("entity_id") for row in event.targets), None)
            person = world.archive.get(person_id) or world.entities.get(person_id) or {}
            if person.get("role") == "captain":
                captain_retirements.append(event.id)
        elif event.type == "SHIP_LOSS_DISPOSITION":
            survivors = list(event.data.get("survivors", ()))
            missing = list(event.data.get("missing", ()))
            deceased = list(event.data.get("deceased", ()))
            roster_before = list(event.data.get("crew_roster_before", ()))
            partition = survivors + missing + deceased
            ship_loss_dispositions.append({
                "event_id": event.id, "ship_id": event.data.get("ship_id"),
                "crew_id": event.data.get("crew_id"),
                "caused_by": list(event.caused_by),
                "survivors": survivors,
                "missing": missing,
                "deceased": deceased,
                "prisoners_released": list(event.data.get("prisoners_released", ())),
                "salvage_location": dict(event.data.get("salvage_location") or {}),
                "member_partition_reconciles": (
                    len(partition) == len(set(partition))
                    and sorted(partition) == sorted(roster_before)),
            })
        elif event.type == "CREW_EMPLOYMENT_SOUGHT":
            employment_searches.append({
                "event_id": event.id, "crew_id": event.data.get("crew_id"),
                "eligible_employer_count": len(event.data.get("eligible_employer_crew_ids", ())),
                "blocked_candidate_reasons": dict(event.data.get("blocked_candidate_reasons", {})),
            })
        elif event.type == "CREW_EMPLOYMENT_ACCEPTED":
            employment_acceptances.append(event.id)
        elif event.type == "CHARACTER_RESCUED":
            character_rescues.append(event.id)
        elif event.type == "MISSING_PERSON_RESOLVED":
            missing_person_resolutions.append(event.id)
        elif event.type == "PRISONER_CAPTURED":
            prisoner_captures.append(event.id)
        elif event.type == "PRISONER_FATE":
            prisoner_fates.append({"event_id": event.id,
                                    "fate": event.data.get("fate"),
                                    "caused_by": list(event.caused_by)})
        elif event.type == "PRISONER_RELEASED":
            prisoner_resolutions.append({"event_id": event.id, "event_type": event.type})
    return {
        "resource_backed_recruitments_by_band": {
            band: {"count": len(ids), "event_ids": ids} for band, ids in recruited.items()},
        "ship_sinks_by_band": {band: {"count": len(ids), "event_ids": ids}
                               for band, ids in sunk.items()},
        "ship_creations": {"count": len(ship_creations), "event_ids": ship_creations},
        "ship_sinks": {"count": len(ship_sink_ids), "event_ids": ship_sink_ids},
        "ship_captures": {"count": len(captures), "event_ids": captures},
        "captures_pending_prize_crew": {"count": len(pending_prize_crews),
                                         "event_ids": pending_prize_crews},
        "prize_crew_installations": {"count": len(prize_crew_installations),
                                     "event_ids": prize_crew_installations},
        "other_title_transfers": {"count": len(other_title_transfers),
                                  "event_ids": other_title_transfers},
        "prize_crew_control_recoveries": {
            "count": len(prize_crew_control_recoveries),
            "event_ids": prize_crew_control_recoveries},
        "captain_deaths": {"count": len(captain_deaths), "event_ids": captain_deaths},
        "captain_retirements": {"count": len(captain_retirements),
                                "event_ids": captain_retirements},
        "ship_loss_dispositions": {
            "count": len(ship_loss_dispositions), "events": ship_loss_dispositions},
        "stranded_employment_searches": {
            "count": len(employment_searches), "events": employment_searches,
            "accepted_count": len(employment_acceptances),
            "accepted_event_ids": employment_acceptances},
        "missing_person_rescues": {"count": len(character_rescues),
                                   "event_ids": character_rescues},
        "missing_person_resolutions": {"count": len(missing_person_resolutions),
                                       "event_ids": missing_person_resolutions},
        "prisoner_captures": {"count": len(prisoner_captures),
                              "event_ids": prisoner_captures},
        "prisoner_fates": {"count": len(prisoner_fates), "events": prisoner_fates},
        "prisoner_resolutions": {"count": len(prisoner_resolutions),
                                 "events": prisoner_resolutions},
    }


def _terminal_actor_violations(event_log) -> list[dict[str, Any]]:
    """Find direct entity and attributable faction commands after terminal state."""
    terminal_positions: dict[str, tuple[int, int]] = {}
    terminal_org_positions: dict[str, tuple[int, int]] = {}
    violations = []
    terminal_types = {"CHARACTER_DIED", "SHIP_SUNK"}
    terminal_bookkeeping_types = {"DREAM_PLAN_FAILED"}
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
            if entity_id in terminal_positions and event.type not in terminal_bookkeeping_types:
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
        crew = world.crew_ships.crews.get(ship.get("crew_id"), {})
        faction_id = crew.get("faction_id")
        faction = world.politics.factions.get(faction_id, {}) if faction_id else {}
        band = classify_ship_actor_band(
            ship, faction_id=faction_id, faction_kind=faction.get("kind", ""),
            merchant_registered=any(row.get("ship_id") == ship.get("id")
                                    for row in world.economy.merchants.values()))
        if band:
            counts[f"{band}_ships"] += 1
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
    crew = world.crew_ships.crews.get(ship.get("crew_id"), {})
    faction_id = crew.get("faction_id")
    faction = world.politics.factions.get(faction_id, {}) if faction_id else {}
    return classify_ship_actor_band(
        ship, faction_id=faction_id, faction_kind=faction.get("kind", ""),
        merchant_registered=any(merchant.get("ship_id") == ship_id
                                for merchant in world.economy.merchants.values()))


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
    plan_templates = world.decisions.ai_data.get("plan_templates", {})
    dream_opportunity_snapshot = {
        "scope": "living-character endpoint snapshot, not cumulative decision opportunities",
        "living_with_dream": 0,
        "registered_dream_template": 0,
        "registered_template_with_decision_agent": 0,
        "registered_template_without_active_plan": 0,
        "active_plan": 0,
        "held_prisoner": 0,
        "unregistered_dream_kind": {},
    }
    for person in living:
        dream = person.get("dream") or {}
        dream_kind = dream.get("kind")
        if not dream_kind:
            continue
        dream_opportunity_snapshot["living_with_dream"] += 1
        if dream_kind not in plan_templates:
            missing = dream_opportunity_snapshot["unregistered_dream_kind"]
            missing[dream_kind] = missing.get(dream_kind, 0) + 1
            continue
        dream_opportunity_snapshot["registered_dream_template"] += 1
        if person["id"] in world.encounters.prisoners and world.encounters.prisoners[
                person["id"]].get("status") == "held":
            dream_opportunity_snapshot["held_prisoner"] += 1
        state = world.decisions.agents.get(person["id"])
        if state is None:
            continue
        dream_opportunity_snapshot["registered_template_with_decision_agent"] += 1
        plan = state.get("plan") or {}
        if plan.get("status") == "active":
            dream_opportunity_snapshot["active_plan"] += 1
        else:
            dream_opportunity_snapshot["registered_template_without_active_plan"] += 1
    dream_opportunity_snapshot["unregistered_dream_kind"] = dict(sorted(
        dream_opportunity_snapshot["unregistered_dream_kind"].items()))
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
    event_counts_by_type = {}
    for event in yearly:
        event_counts_by_type[event.type] = event_counts_by_type.get(event.type, 0) + 1
    encounter_outcomes = {}
    for event in yearly:
        if event.type == "ENCOUNTER_RESOLVED":
            outcome = str(event.data.get("outcome", "unknown"))
            encounter_outcomes[outcome] = encounter_outcomes.get(outcome, 0) + 1
    plan_outcomes = {event_type: event_counts_by_type.get(event_type, 0)
                     for event_type in ("DREAM_PLAN_COMPLETED", "DREAM_PLAN_FAILED",
                                        "DREAM_PLAN_ABANDONED", "PLAN_STEP_STARTED",
                                        "PLAN_STEP_BLOCKED", "PLAN_STEP_FAILED",
                                        "PLAN_STEP_COMPLETED")}
    dream_funnel = _dream_story_funnel(yearly)
    conflict_types = ("ROUTE_BLOCKADED", "SHORTAGE_RELIEF_NEGOTIATED",
                      "PROFITABLE_TRADE_RIVALRY", "CONTRABAND_SEIZED",
                      "TREATY_BREACHED", "TREATY_OBLIGATION_FULFILLED")
    conflict_counts = {event_type: event_counts_by_type.get(event_type, 0)
                       for event_type in conflict_types}
    public_deeds_by_kind = {}
    for event in yearly:
        if event.type == "PUBLIC_RENOWN_EARNED":
            deed_kind = event.data.get("deed_kind", "unknown")
            public_deeds_by_kind[deed_kind] = public_deeds_by_kind.get(deed_kind, 0) + 1
    duel_outcomes = {}
    for event in yearly:
        if event.type == "LEGEND_DUEL":
            outcome = str(event.data.get("outcome", "unknown"))
            duel_outcomes[outcome] = duel_outcomes.get(outcome, 0) + 1
    exchange_bands = {}
    for event in yearly:
        if event.type != "PERSONAL_EXCHANGE":
            continue
        morale = event.data.get("attacker_morale")
        morale_band = ("unknown" if type(morale) is not int else
                       "low" if morale < 34 else "mid" if morale < 67 else "high")
        label = "|".join((str(event.data.get("weapon_id", "unknown")),
                          str(event.data.get("armor_id", "unknown")), morale_band))
        exchange_bands[label] = exchange_bands.get(label, 0) + 1
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
    actor_budget = _actor_band_status(actor_counts, actor_bounds)
    battle_observations = [row for row in world.battles.opportunity_observations
                           if start_tick < row["tick"] <= end_tick]
    battle_opportunity_reasons: dict[str, dict[str, Any]] = {}
    for row in battle_observations:
        key = row["reason"]
        bucket = battle_opportunity_reasons.setdefault(key, {
            "count": 0, "sample_encounter_event_ids": [], "sample_details": [],
        })
        bucket["count"] += 1
        if len(bucket["sample_encounter_event_ids"]) < 3:
            bucket["sample_encounter_event_ids"].append(row["encounter_event_id"])
            bucket["sample_details"].append(row)
    battle_opportunity_report = {
        "observed_encounter_candidates": len(battle_observations),
        "by_reason": {reason: battle_opportunity_reasons[reason]
                      for reason in sorted(battle_opportunity_reasons)},
        "scope": "ENCOUNTER_RESOLVED events evaluated by BattleSystem for set-piece conversion",
    }
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
        "event_counts_by_type": dict(sorted(event_counts_by_type.items())),
        "story_outcomes": {
            "dream_plans": plan_outcomes,
            "dream_funnel": dream_funnel,
            "dream_opportunity_snapshot": dream_opportunity_snapshot,
            "set_piece_battle_opportunities": battle_opportunity_report,
            "treasure_clues_learned": (event_counts_by_type.get("TREASURE_CLUE_LEARNED", 0)
                                       + event_counts_by_type.get("TREASURE_CLUE_SPREAD", 0)),
            "treasure_discoveries": event_counts_by_type.get("TREASURE_FOUND", 0),
            "trade_conflicts": conflict_counts,
            "encounter_outcomes": dict(sorted(encounter_outcomes.items())),
            "legend_duel_outcomes": dict(sorted(duel_outcomes.items())),
            "personal_exchange_bands": dict(sorted(exchange_bands.items())),
            "public_deeds_by_kind": dict(sorted(public_deeds_by_kind.items())),
            "legend_recognitions": event_counts_by_type.get("LEGEND_RECOGNIZED", 0),
            "rookie_promotions": event_counts_by_type.get("ROOKIE_PROMOTED", 0),
            "rivalry_attempts": event_counts_by_type.get("FEUD_RAID_ATTEMPTED", 0),
            "stranded_crew_employments": event_counts_by_type.get("CREW_EMPLOYMENT_ACCEPTED", 0),
        },
        "vendettas_created": vendettas,
        "vendettas_converted_to_action": vendetta_actions if vendettas else 0,
        "lifecycle_diagnostics": lifecycle_diagnostics,
        "stale_wakeups": len(stale_wakeups),
        "actor_bands": {
            "counts": actor_counts,
            "bounds": actor_bounds,
            "bands": actor_budget["bands"],
            "within_bounds": actor_budget["within_bounds"],
        },
    }, next_previous)


def _write_diagnostics(seed: int, years: list[dict], *, run_id: str,
                       metadata: dict[str, Any], output_dir: Path,
                       total_event_count: int, initial_event_count: int,
                       actor_population_census: list[dict[str, Any]]) -> dict[str, str]:
    yearly_event_count = sum(sum(row["event_counts_by_type"].values()) for row in years)
    report = {"run_id": run_id, "seed": seed, "metadata": metadata,
              "event_count_scope": {
                  "yearly_tick_range_exclusive_inclusive": "(start_tick, end_tick]",
                  "yearly_event_count": yearly_event_count,
                  "initial_tick_zero_event_count": initial_event_count,
                  "total_event_count": total_event_count,
                  "yearly_plus_initial_reconciles": (
                      yearly_event_count + initial_event_count == total_event_count),
              },
              "tick_ranges": [row["tick_range"] for row in years],
              "actor_population_census": actor_population_census,
              "years": years,
              "zero_call_systems": sorted({name for row in years
                                            for name in row["system_zero_call_over_30_days"]})}
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"diag_{seed}.json"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                         encoding="utf-8", newline="\n")
    lines = [f"# Simulation diagnostics — run {run_id}, seed {seed}", "",
             f"- Total events: {total_event_count}; tick-zero initialization: {initial_event_count}; "
             f"yearly-scope events: {yearly_event_count}; scope: `(start_tick, end_tick]`",
             f"- Event-count reconciliation: {report['event_count_scope']['yearly_plus_initial_reconciles']}", ""]
    for row in years:
        lines.extend([f"## Year {row['year']}", "",
                       f"- Events by class: `{json.dumps(row['events_by_class'], sort_keys=True)}`",
                      f"- Story outcomes: `{json.dumps(row.get('story_outcomes', {}), sort_keys=True)}`",
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
    markdown_path = output_dir / f"diag_{seed}.md"
    markdown_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return {"json": str(json_path), "markdown": str(markdown_path)}


def _verify_diagnostic_report(paths: dict[str, str], *, run_id: str, seed: int,
                              yearly_event_count: int, initial_event_count: int,
                              total_event_count: int, expected_tick_ranges: list[list[int]],
                              endpoint_actor_bands: dict) -> dict[str, Any]:
    report = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    scope = report.get("event_count_scope", {})
    years = report.get("years", [])
    reported_type_count = sum(sum(row.get("event_counts_by_type", {}).values())
                              for row in years)
    reported_class_count = sum(sum(row.get("events_by_class", {}).values())
                               for row in years)
    dream_funnel_counts_match = all(
        count == row.get("event_counts_by_type", {}).get(event_type, 0)
        for row in years
        for event_type, count in row.get("story_outcomes", {}).get(
            "dream_funnel", {}).get("counts_by_event_type", {}).items())
    checks = {
        "diagnostic_run_id_matches": report.get("run_id") == run_id,
        "diagnostic_seed_matches": report.get("seed") == seed,
        "diagnostic_tick_ranges_match": report.get("tick_ranges") == expected_tick_ranges,
        "diagnostic_yearly_count_matches": scope.get("yearly_event_count")
        == yearly_event_count == reported_type_count,
        "diagnostic_event_class_count_matches": reported_class_count == yearly_event_count,
        "diagnostic_actor_bands_match_endpoint": (
            not years or years[-1].get("actor_bands") == endpoint_actor_bands),
        "diagnostic_dream_funnel_counts_match_events": dream_funnel_counts_match,
        "diagnostic_initial_count_matches": scope.get("initial_tick_zero_event_count")
        == initial_event_count,
        "diagnostic_total_count_matches": scope.get("total_event_count")
        == total_event_count,
        "event_scope_reconciles": (yearly_event_count + initial_event_count
                                    == total_event_count
                                    and scope.get("yearly_plus_initial_reconciles") is True),
    }
    if not all(checks.values()):
        raise RuntimeError(f"diagnostic report integrity failed: {checks}")
    return {**checks, "status": "pass", "yearly_event_count": yearly_event_count,
            "initial_tick_zero_event_count": initial_event_count,
            "total_event_count": total_event_count}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a deterministic seed batch")
    parser.add_argument("--seeds", type=int, default=1, help="number of sequential seeds")
    parser.add_argument("--years", type=int, default=0, help="years per seed")
    parser.add_argument("--director", choices=("on", "off", "config"), default="config",
                        help="story director setting for every seed (default: config)")
    parser.add_argument("--progress", action="store_true",
                        help="write one JSON progress record per completed year to stderr")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="parent for this run's unique reports directory (default: system temp)")
    args = parser.parse_args(argv)
    if args.seeds <= 0 or args.years < 0:
        parser.error("--seeds must be positive and --years nonnegative")
    run_id = uuid.uuid4().hex
    run_output_dir = (args.output_dir or Path(tempfile.gettempdir()) / "pirates-of-miska-batches") / run_id
    source = _source_metadata()
    results = []
    for seed in range(args.seeds):
        run_started = time.perf_counter()
        enabled = None if args.director == "config" else args.director == "on"
        world = WorldState.create(seed, director_enabled=enabled)
        initial_event_count = len(world.event_log)
        config_hash = _sha256_bytes(json.dumps(
            world.config, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        metadata = {"source": source, "effective_config_sha256": config_hash,
                    "director_argument": args.director}
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
        actor_population_census = [_actor_population_census(world, label="opening")]
        actor_population_census[0]["transitions_since_previous_sample"] = None
        transition_event_cursor = initial_event_count
        for year in range(1, args.years + 1):
            year_started = time.perf_counter()
            quarter_ticks = [((year - 1) * ticks_per_year
                              + ticks_per_year * quarter // 4)
                             for quarter in range(1, 5)]
            for quarter, target_tick in enumerate(quarter_ticks, start=1):
                world.run_until(target_tick)
                label = (f"year_{year}_end" if quarter == 4
                         else f"year_{year}_quarter_{quarter}")
                sample = _actor_population_census(world, label=label)
                transitions = _actor_population_transitions(world, transition_event_cursor)
                created_count = transitions["ship_creations"]["count"]
                sunk_count = transitions["ship_sinks"]["count"]
                hull_delta = (sample["total_physical_hulls"]
                              - actor_population_census[-1]["total_physical_hulls"])
                transitions["physical_hull_delta"] = hull_delta
                transitions["physical_hull_delta_reconciles"] = (
                    hull_delta == created_count - sunk_count)
                sample["transitions_since_previous_sample"] = transitions
                transition_event_cursor = len(world.event_log)
                actor_population_census.append(sample)
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
        total_event_count = len(world.event_log)
        endpoint_actor_bands = _actor_band_status(
            _actor_band_counts(world), _actor_band_bounds(world))
        yearly_event_count = sum(
            sum(row["event_counts_by_type"].values()) for row in diagnostic_years)
        event_scope_reconciles = yearly_event_count + initial_event_count == total_event_count
        if not event_scope_reconciles:
            raise RuntimeError(
                f"run {run_id} seed {seed}: event scope does not reconcile "
                f"({yearly_event_count}+{initial_event_count}!={total_event_count})")
        diagnostic_paths = _write_diagnostics(
            seed, diagnostic_years, run_id=run_id, metadata=metadata,
            output_dir=run_output_dir, total_event_count=total_event_count,
            initial_event_count=initial_event_count,
            actor_population_census=actor_population_census)
        diagnostic_integrity = _verify_diagnostic_report(
            diagnostic_paths, run_id=run_id, seed=seed,
            yearly_event_count=yearly_event_count,
            initial_event_count=initial_event_count,
            total_event_count=total_event_count,
            expected_tick_ranges=[row["tick_range"] for row in diagnostic_years],
            endpoint_actor_bands={
                "counts": endpoint_actor_bands["counts"],
                "bounds": endpoint_actor_bands["bounds"],
                "bands": endpoint_actor_bands["bands"],
                "within_bounds": endpoint_actor_bands["within_bounds"],
            })
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
        event_counts_by_type = {}
        for year_row in diagnostic_years:
            for event_type, count in year_row["event_counts_by_type"].items():
                event_counts_by_type[event_type] = event_counts_by_type.get(event_type, 0) + count
        dream_reason_totals = {}
        for year_row in diagnostic_years:
            for reason, count in year_row["story_outcomes"]["dream_funnel"][
                    "blocked_failed_abandoned_by_reason"].items():
                dream_reason_totals[reason] = dream_reason_totals.get(reason, 0) + count
        story_outcomes = {
            "dream_plans_completed": sum(row["story_outcomes"]["dream_plans"][
                "DREAM_PLAN_COMPLETED"] for row in diagnostic_years),
            "dream_plans_failed": sum(row["story_outcomes"]["dream_plans"][
                "DREAM_PLAN_FAILED"] for row in diagnostic_years),
            "dream_plan_steps_blocked": sum(row["story_outcomes"]["dream_plans"][
                "PLAN_STEP_BLOCKED"] for row in diagnostic_years),
            "dream_funnel_event_totals": {
                event_type: event_counts_by_type.get(event_type, 0)
                for event_type in sorted({event_type for year_row in diagnostic_years
                                          for event_type in year_row["story_outcomes"][
                                              "dream_funnel"]["counts_by_event_type"]})},
            "dream_blocked_failed_abandoned_by_reason": dict(sorted(dream_reason_totals.items())),
            "dream_opportunity_snapshots": [
                {"year": row["year"], **row["story_outcomes"][
                    "dream_opportunity_snapshot"]} for row in diagnostic_years],
            "treasure_clues_learned": sum(row["story_outcomes"]["treasure_clues_learned"]
                                          for row in diagnostic_years),
            "treasure_discoveries": sum(row["story_outcomes"]["treasure_discoveries"]
                                        for row in diagnostic_years),
            "trade_conflict_events": sum(sum(row["story_outcomes"]["trade_conflicts"].values())
                                          for row in diagnostic_years),
            "encounters": sum(sum(row["story_outcomes"]["encounter_outcomes"].values())
                              for row in diagnostic_years),
            "legend_duels": sum(sum(row["story_outcomes"]["legend_duel_outcomes"].values())
                                for row in diagnostic_years),
            "personal_exchanges": sum(sum(row["story_outcomes"]["personal_exchange_bands"].values())
                                       for row in diagnostic_years),
            "public_renown_awards": sum(sum(row["story_outcomes"]["public_deeds_by_kind"].values())
                                         for row in diagnostic_years),
            "legend_recognitions": sum(row["story_outcomes"]["legend_recognitions"]
                                        for row in diagnostic_years),
            "rookie_promotions": sum(row["story_outcomes"]["rookie_promotions"]
                                     for row in diagnostic_years),
            "rivalry_attempts": sum(row["story_outcomes"]["rivalry_attempts"]
                                    for row in diagnostic_years),
            "stranded_crew_employments": sum(row["story_outcomes"]["stranded_crew_employments"]
                                             for row in diagnostic_years),
            "open_treaty_obligations_at_end": sum(
                obligation["status"] == "open"
                for treaty in world.politics.treaties.values()
                for obligation in treaty["obligations"]),
            "currently_stranded_crews_at_end": sum(
                not crew["ship_ids"] and bool(crew.get("stranded_location"))
                for crew in world.crew_ships.crews.values()),
        }
        results.append({"run_id": run_id, "seed": seed, "metadata": metadata,
                        "tick": world.clock.tick,
                        "run_seconds": round(time.perf_counter() - run_started, 3),
                        "named_characters": named_characters,
                        "island_count": len(world.geography.islands),
                        "regional_coarse_summaries": sum(
                            len(rows) for rows in world.lod.regional_history.values()),
                         "event_count": total_event_count,
                         "event_count_scope": "total log including tick-zero initialization; diagnostics count (start_tick, end_tick]",
                         "diagnostic_tick_ranges": [row["tick_range"] for row in diagnostic_years],
                             "event_compaction": event_compaction,
                         "event_counts_by_type": dict(sorted(event_counts_by_type.items())),
                         "story_outcomes": story_outcomes,
                        "annual_trade": annual_trade,
                        "trade_stable": trade_stable,
                        "trade_final_to_first_permille": trade_ratio_permille,
                            "ledger_balanced": (balances["silver_expected"] == balances["silver_actual"]
                                              and balances["goods_expected"] == balances["goods_actual"]),
                             "terminal_actor_violations": _terminal_actor_violations(world.event_log),
                         "actor_bands": endpoint_actor_bands,
                         "actor_population_census": actor_population_census,
                             "actor_bands_within_budget": endpoint_actor_bands["within_bounds"],
                             "diagnostic_integrity": diagnostic_integrity,
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
    screen_fields = ("dream_plans_completed", "dream_plans_failed", "dream_plan_steps_blocked",
                     "treasure_clues_learned", "treasure_discoveries", "trade_conflict_events",
                     "encounters", "legend_duels", "personal_exchanges", "public_renown_awards",
                     "legend_recognitions", "rookie_promotions", "rivalry_attempts",
                     "stranded_crew_employments", "open_treaty_obligations_at_end",
                     "currently_stranded_crews_at_end")
    statistical_screen = {
        "seed_ids": [run["seed"] for run in results],
        "method": "Per-seed natural-run counts, normal-approximation 95% confidence intervals for means, and Wilson 95% intervals for the fraction of seeds with nonzero counts. Fixed screen seeds are descriptive, not a random population sample; rare event estimates need larger samples.",
        "reachability_is_separate": True,
        "reachability_fixture_suites": [
            "tests/test_sim_decisions.py (treasure clue/discovery/return and blocked/terminal paths)",
            "tests/test_sim_economy_data.py (blockade, shortage relief, profitable-trade rivalry, contraband, rerouting)",
            "tests/test_sim_encounters.py and tests/test_sim_battles.py (encounter/boarding/duel outcome reachability)",
        ],
        "metrics": {field: _screen_stat([run["story_outcomes"][field] for run in results])
                    for field in screen_fields},
        "event_counts_by_type": {
            event_type: _screen_stat([run["event_counts_by_type"].get(event_type, 0)
                                      for run in results])
            for event_type in sorted({event_type for run in results
                                      for event_type in run["event_counts_by_type"]})},
    }
    acceptance_gates = _acceptance_gate_report(
        acceptance, years=args.years, director=args.director)
    print(json.dumps({"run_id": run_id, "output_dir": str(run_output_dir),
                      "metadata": {"source": source},
                      "runs": results, "acceptance": acceptance,
                      "acceptance_gates": acceptance_gates,
                      "statistical_screen": statistical_screen}, sort_keys=True))
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
