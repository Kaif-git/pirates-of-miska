"""Shared pure classification for commandable ship actor-budget bands."""

from __future__ import annotations


_FACTION_BANDS = {
    "actor_merchant": "merchant", "merchant": "merchant", "merchant_league": "merchant",
    "actor_pirate": "pirate", "pirate": "pirate", "raider": "pirate",
    "actor_marine": "marine", "marine": "marine", "patrol": "marine",
    "actor_privateer": "privateer", "privateer": "privateer",
}
_OWNER_BANDS = frozenset(("merchant", "pirate", "marine", "privateer"))


def classify_ship_actor_band(ship: dict, *, faction_id: str | None,
                             faction_kind: str, merchant_registered: bool) -> str | None:
    """Return the acceptance/recruitment actor band for a ship, or None.

    Known faction kinds take precedence. Any other explicit faction marks a
    faction fleet rather than silently inheriting a stale owner_type. Merchant
    records classify otherwise-unaffiliated vessels as merchant ships.
    """
    kind = str(faction_kind).lower()
    if kind in _FACTION_BANDS:
        return _FACTION_BANDS[kind]
    if faction_id:
        return None
    if merchant_registered:
        return "merchant"
    owner_type = ship.get("owner_type")
    return owner_type if owner_type in _OWNER_BANDS else None
