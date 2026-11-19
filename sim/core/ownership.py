"""Canonical ship title/control helpers shared by simulation systems."""

from __future__ import annotations

from typing import Any


OWNER_KINDS = frozenset({"crew", "merchant"})


def crew_can_command(world, crew_id: str | None) -> bool:
    """Crew operational authority is independent of its faction allegiance."""
    return crew_id is not None and crew_id in world.crew_ships.crews


def assign_ship_title(ship: dict[str, Any], *, owner_kind: str, owner_id: str,
                      controller_crew_id: str | None, event_id: str) -> None:
    """Commit the current legal title and designated controller to a ship row.

    ``crew_id`` remains the onboard crew for legacy roster/supply bookkeeping.
    The controller is the crew authorized to command the hull. A captured hull
    may have a controller while awaiting a physically assigned prize crew.
    """
    if owner_kind not in OWNER_KINDS or not isinstance(owner_id, str) or not owner_id:
        raise ValueError("Ship title requires a supported owner kind and stable ID")
    if controller_crew_id is not None and (
            not isinstance(controller_crew_id, str) or not controller_crew_id):
        raise ValueError("Ship controller must be a crew ID or None")
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("Ship title changes require a causal event ID")
    ship["legal_owner"] = {"kind": owner_kind, "id": owner_id}
    ship["controller_crew_id"] = controller_crew_id
    ship["ownership_event_id"] = event_id
    # Temporary schema-17 compatibility mirror. New command checks use the
    # canonical controller field; the validator requires the mirror to agree.
    ship["captured_by_crew_id"] = (
        controller_crew_id
        if controller_crew_id is not None
        and controller_crew_id != ship.get("crew_id") else None)


def ship_is_commandable(world, ship_id: str) -> bool:
    """True only when the active onboard crew is also the designated controller."""
    ship = world.entities.get(ship_id)
    if not ship or ship.get("kind") != "ship":
        return False
    crew_id = ship.get("crew_id")
    return (crew_id is not None
            and crew_id == ship.get("controller_crew_id")
            and crew_can_command(world, crew_id)
            and ship.get("captured_by_crew_id") is None)
