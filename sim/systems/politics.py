"""Derived faction relations and validated, event-attributed treaty transitions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


POLITICS_PATH = Path(__file__).resolve().parents[1] / "data" / "politics.json"
_DIMENSIONS = {"trust", "fear", "grievance", "kinship", "trade_dependence"}


def load_politics_data(path: str | Path = POLITICS_PATH) -> tuple[dict[str, Any], str]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load politics rules {path}: {exc}") from exc
    validate_politics_data(data)
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return data, digest


def validate_politics_data(data: Any) -> None:
    if (not isinstance(data, dict) or set(data) != {
            "schema_version", "rules", "relation_weights", "treaty_states", "transitions", "terms"}
            or type(data["schema_version"]) is not int or data["schema_version"] != 1):
        raise ValueError("Invalid politics catalog schema")
    rules = data["rules"]
    if (not isinstance(rules, dict) or set(rules) != {
            "relation_min", "relation_max", "trade_dependence_per_volume", "trade_dependence_cap",
             "max_trade_volume", "faction_month_days", "faction_founder_min_ambition",
             "faction_founder_min_leadership", "faction_min_member_crews",
             "expansion_base_basis_points",
            "expansion_ambition_basis_points", "max_faction_territories",
            "coup_base_basis_points", "coup_ambition_basis_points",
            "coup_success_base_basis_points", "coup_success_leadership_basis_points",
            "marine_officer_slots", "marine_challenge_base_basis_points",
            "marine_challenge_ambition_basis_points",
            "marine_challenge_success_base_basis_points",
            "government_starting_budget_silver", "heat_bounty_threshold",
            "heat_hunter_threshold", "heat_fleet_threshold",
            "heat_per_hostile_combat", "heat_per_ship_sunk", "heat_daily_decay",
            "bounty_base_silver", "bounty_per_heat_silver",
            "monthly_patrol_cost_silver", "monthly_hunter_cost_silver",
            "monthly_fleet_cost_silver", "monthly_diplomacy_basis_points"}
            or any(type(value) is not int or value < 0 for value in rules.values())
            or rules["relation_min"] != 0 or rules["relation_max"] != 100
            or rules["trade_dependence_cap"] > 100 or rules["faction_month_days"] < 1
            or rules["faction_founder_min_ambition"] > 100
            or rules["faction_founder_min_leadership"] > 100
            or rules["faction_min_member_crews"] < 2
            or rules["max_faction_territories"] < 1
            or rules["expansion_base_basis_points"] > 10000
            or rules["coup_base_basis_points"] > 10000
            or rules["coup_success_base_basis_points"] > 10000
            or rules["marine_officer_slots"] < 1
            or rules["marine_challenge_base_basis_points"] > 10000
            or rules["marine_challenge_success_base_basis_points"] > 10000
            or not rules["heat_bounty_threshold"] <= rules["heat_hunter_threshold"]
            <= rules["heat_fleet_threshold"]
            or rules["monthly_diplomacy_basis_points"] > 10000):
        raise ValueError("Invalid politics rules")
    weights = data["relation_weights"]
    expected_weights = {
        "trust": {"opinion_trust", "institutional"},
        "fear": {"opinion_fear", "institutional"},
        "grievance": {"opinion_grievance", "institutional"},
        "kinship": {"shared_house"},
        "trade_dependence": {"trade_volume"},
    }
    if (not isinstance(weights, dict) or set(weights) != _DIMENSIONS
            or any(not isinstance(weights.get(dim), dict)
                   or set(weights[dim]) != expected_weights[dim]
                   or any(type(weight) is not int or weight < 0
                          for weight in weights[dim].values())
                   for dim in _DIMENSIONS)):
        raise ValueError("Invalid relation weights")
    states = data["treaty_states"]
    if (not isinstance(states, list) or states[0] != "none" or len(set(states)) != len(states)
            or any(not isinstance(state, str) or not state for state in states)):
        raise ValueError("Invalid treaty states")
    transitions = data["transitions"]
    if not isinstance(transitions, dict) or set(transitions) != set(states):
        raise ValueError("Invalid treaty transition table")
    for source, rows in transitions.items():
        if not isinstance(rows, dict) or source in rows or set(rows) - set(states):
            raise ValueError("Invalid or duplicate treaty transition")
        for target, rule in rows.items():
            if (not isinstance(rule, dict) or set(rule) != {"min_trust", "max_grievance"}
                    or any(type(value) is not int or not 0 <= value <= 100
                           for value in rule.values())):
                raise ValueError(f"Invalid treaty transition: {source}->{target}")
    terms = data["terms"]
    if (not isinstance(terms, dict) or set(terms) != set(states)
            or any(not isinstance(rows, list) or len(set(rows)) != len(rows)
                   or any(not isinstance(term, str) or not term for term in rows)
                   for rows in terms.values())):
        raise ValueError("Invalid treaty terms")


class PoliticsSystem:
    def __init__(self, *, digest: str, treaties: dict[str, dict[str, Any]] | None = None,
                 institutional: dict[str, dict[str, int]] | None = None,
                 trade_volume: dict[str, int] | None = None,
                 factions: dict[str, dict[str, Any]] | None = None,
                 archived_factions: dict[str, dict[str, Any]] | None = None,
                 island_rulers: dict[str, str] | None = None,
                 fortifications: dict[str, int] | None = None,
                 marine_command: dict[str, Any] | None = None,
                 character_heat: dict[str, int] | None = None,
                 crew_heat: dict[str, int] | None = None,
                 crew_bounties: dict[str, dict[str, Any]] | None = None,
                 response_assignments: dict[str, dict[str, Any]] | None = None,
                 global_tension: int = 0,
                 last_updated_day: int = 0) -> None:
        self.data, actual_digest = load_politics_data()
        if digest != actual_digest:
            raise ValueError("Politics rules changed since snapshot creation")
        self.digest = digest
        self.treaties = json.loads(json.dumps(treaties or {}, sort_keys=True))
        self.institutional = json.loads(json.dumps(institutional or {}, sort_keys=True))
        self.trade_volume = dict(trade_volume or {})
        self.factions = json.loads(json.dumps(factions or {}, sort_keys=True))
        self.archived_factions = json.loads(json.dumps(archived_factions or {}, sort_keys=True))
        self.island_rulers = dict(island_rulers or {})
        self.fortifications = dict(fortifications or {})
        self.marine_command = json.loads(json.dumps(marine_command or {}, sort_keys=True))
        self.character_heat = dict(character_heat or {})
        self.crew_heat = dict(crew_heat or {})
        self.crew_bounties = json.loads(json.dumps(crew_bounties or {}, sort_keys=True))
        self.response_assignments = json.loads(json.dumps(response_assignments or {}, sort_keys=True))
        self.global_tension = global_tension
        if type(last_updated_day) is not int or last_updated_day < 0:
            raise ValueError("Invalid politics update day")
        self.last_updated_day = last_updated_day
        self.validate_state()

    @classmethod
    def create(cls) -> "PoliticsSystem":
        _, digest = load_politics_data()
        return cls(digest=digest)

    @classmethod
    def restore(cls, snapshot: dict[str, Any]) -> "PoliticsSystem":
        if not isinstance(snapshot, dict) or set(snapshot) != {
                "digest", "treaties", "institutional", "trade_volume", "factions",
                "archived_factions", "island_rulers", "fortifications", "marine_command",
                "character_heat", "crew_heat", "crew_bounties", "response_assignments",
                "global_tension",
                "last_updated_day"}:
            raise ValueError("Invalid politics snapshot")
        return cls(digest=snapshot["digest"], treaties=snapshot["treaties"],
                   institutional=snapshot["institutional"], trade_volume=snapshot["trade_volume"],
                   factions=snapshot["factions"], archived_factions=snapshot["archived_factions"],
                   island_rulers=snapshot["island_rulers"],
                   fortifications=snapshot["fortifications"],
                   marine_command=snapshot["marine_command"],
                   character_heat=snapshot["character_heat"], crew_heat=snapshot["crew_heat"],
                   crew_bounties=snapshot["crew_bounties"],
                   response_assignments=snapshot["response_assignments"],
                   global_tension=snapshot["global_tension"],
                   last_updated_day=snapshot["last_updated_day"])

    def snapshot(self) -> dict[str, Any]:
        return {"digest": self.digest, "treaties": self.treaties,
                "institutional": self.institutional, "trade_volume": self.trade_volume,
                "factions": self.factions, "archived_factions": self.archived_factions,
                "island_rulers": self.island_rulers, "fortifications": self.fortifications,
                "marine_command": self.marine_command,
                "character_heat": self.character_heat, "crew_heat": self.crew_heat,
                "crew_bounties": self.crew_bounties,
                "response_assignments": self.response_assignments,
                "global_tension": self.global_tension,
                "last_updated_day": self.last_updated_day}

    def _pair_key(self, first: str, second: str) -> str:
        if first == second:
            raise ValueError("Faction relations require distinct factions")
        return "|".join(sorted((first, second)))

    def _crew(self, world, faction_id: str) -> dict[str, Any]:
        crew = world.crew_ships.crews.get(faction_id)
        if crew is not None:
            return crew
        faction = self.factions.get(faction_id)
        if faction:
            member_ids = faction.get("member_crew_ids", ())
            for crew_id in member_ids:
                crew = world.crew_ships.crews.get(crew_id)
                if crew is not None:
                    return crew
        raise ValueError(f"Faction has no active member crew: {faction_id}")

    def _character(self, world, character_id: str) -> dict[str, Any]:
        character = world.entities.get(character_id)
        if not character or character.get("kind") != "character":
            raise ValueError(f"Unknown active faction member: {character_id}")
        return character

    def seed_world(self, world) -> None:
        if self.factions or self.island_rulers:
            raise ValueError("Starting factions can only be seeded once")
        merchant_crews = []
        for crew_id, crew in sorted(world.crew_ships.crews.items()):
            ship_id = next((candidate for candidate in crew["ship_ids"]
                            if candidate in world.entities), None)
            captain = world.entities.get(crew["captain_id"])
            if (ship_id is None or captain is None
                    or world.entities[ship_id].get("legal_owner", {}).get("kind")
                    != "merchant"):
                continue
            location = world.location_of(ship_id)
            if location.get("kind") != "island":
                continue
            if self._founder_is_qualified(captain):
                merchant_crews.append(crew_id)
        if len(merchant_crews) >= self.data["rules"]["faction_min_member_crews"]:
            founder_crew_id = min(merchant_crews, key=lambda crew_id: (
                -(world.entities[world.crew_ships.crews[crew_id]["captain_id"]]
                  ["stats"]["leadership"] * 2
                  + world.entities[world.crew_ships.crews[crew_id]["captain_id"]]
                  ["traits"]["ambition"]),
                world.crew_ships.crews[crew_id]["captain_id"], crew_id))
            founder_crew = world.crew_ships.crews[founder_crew_id]
            founder_ship = next(ship_id for ship_id in founder_crew["ship_ids"]
                                if ship_id in world.entities)
            anchorage = world.location_of(founder_ship)["id"]
            purpose = "mutual_trade_protection"
            name = (f"{world.entities[founder_crew['captain_id']]['display_name']}"
                    "'s Trade Compact")
            causes = sorted({world.crew_ships.crews[crew_id]["last_event_id"]
                             for crew_id in merchant_crews})
            self.found_faction(
                world, member_crew_ids=merchant_crews, founder_id=founder_crew["captain_id"],
                anchorage_island_id=anchorage, purpose=purpose, name=name,
                kind="merchant_league", caused_by=causes)

        officer_slots = self.data["rules"]["marine_officer_slots"]
        eligible = [row for row in world.entities.values()
                    if row.get("kind") == "character" and row.get("crew_id") is None]
        eligible.sort(key=lambda row: (
            row.get("role") != "official",
            -(row["stats"]["leadership"] * 2 + row["traits"]["loyalty"]
              - row["traits"]["ambition"] // 2), row["id"]))
        if len(eligible) < officer_slots:
            raise ValueError("Not enough free characters to staff Marine command")
        officers = eligible[:officer_slots]
        commander_id = officers[0]["id"]
        marine_id = world.ids.next("faction")
        founder = world.event_log.append(
            tick=world.clock.tick, event_type="FACTION_FOUNDED",
            location=world.location_of(commander_id)["id"],
            actors=[{"role": "marine_commander", "entity_id": commander_id}],
            targets=[{"role": "faction", "entity_id": marine_id}],
            data={"faction_id": marine_id, "name": "Royal Marines",
                  "kind": "marine", "home_island": None,
                  "succession_rule": "council_vote", "member_crew_ids": [],
                  "founder_id": commander_id, "anchorage_island_id": None,
                  "purpose": "government_naval_service"},
            caused_by=[world.entities[commander_id]["last_event_id"]],
            causes_tags=["faction_founding", "marine_command"], magnitude=0.5,
        )
        for officer in officers:
            officer["home_faction"] = marine_id
        self.factions[marine_id] = {
            "id": marine_id, "name": "Royal Marines", "kind": "marine",
            "leader_id": commander_id, "council": sorted(row["id"] for row in officers),
            "succession_rule": "council_vote", "doctrine": {
                "order": 90, "freedom": 10, "profit": 20, "glory": 50, "faith": 30},
            "territory": [], "founded_event_id": founder.id,
            "last_event_id": founder.id, "monthly_decisions": 0,
            "member_crew_ids": [], "founder_id": commander_id,
            "anchorage_island_id": None, "purpose": "government_naval_service",
        }
        self.marine_command = {
            "faction_id": marine_id, "commander_id": commander_id,
            "deputy_ids": sorted(row["id"] for row in officers[1:]),
            "appointment_event_id": founder.id,
        }
        budget = self.data["rules"]["government_starting_budget_silver"]
        funding_island = max(world.economy.islands, key=lambda island_id: (
            world.economy.islands[island_id]["treasury_silver"], island_id))
        funding_event = world.economy.fund_government(
            world, budget, island_id=funding_island,
            caused_by="event_000001")
        self.marine_command["budget_funding_event_id"] = funding_event

    def _founder_is_qualified(self, character: dict[str, Any]) -> bool:
        rules = self.data["rules"]
        return (character.get("kind") == "character"
                and character.get("death_tick") is None
                and character.get("departure_tick") is None
                and character.get("traits", {}).get("ambition", -1)
                >= rules["faction_founder_min_ambition"]
                and character.get("stats", {}).get("leadership", -1)
                >= rules["faction_founder_min_leadership"])

    def found_faction(self, world, *, member_crew_ids: list[str], founder_id: str,
                      anchorage_island_id: str, purpose: str, name: str,
                      kind: str, caused_by: list[str] | tuple[str, ...]) -> str:
        """Found a coalition without conflating its identity with any member crew."""
        minimum = self.data["rules"]["faction_min_member_crews"]
        if (not isinstance(member_crew_ids, list) or len(member_crew_ids) < minimum
                or any(not isinstance(crew_id, str) for crew_id in member_crew_ids)
                or len(set(member_crew_ids)) != len(member_crew_ids)):
            raise ValueError(f"Faction founding requires at least {minimum} distinct crews")
        if (not isinstance(name, str) or not name.strip()
                or not isinstance(purpose, str) or not purpose.strip()
                or kind == "marine" or not isinstance(kind, str) or not kind):
            raise ValueError("Coalition founding requires a name, purpose and non-Marine kind")
        if anchorage_island_id not in world.geography.islands:
            raise ValueError("Faction anchorage must be a known island")
        founder = world.entities.get(founder_id)
        if not founder or not self._founder_is_qualified(founder):
            raise ValueError("Faction founder must meet ambition and leadership thresholds")
        founder_name = founder.get("display_name")
        if (not isinstance(founder_name, str) or not founder_name.strip()
                or founder_name.casefold() not in name.casefold()):
            raise ValueError("Coalition name must be derived from its founder's display name")
        crews = [world.crew_ships.crews.get(crew_id) for crew_id in member_crew_ids]
        if any(crew is None or crew.get("faction_id") is not None for crew in crews):
            raise ValueError("Coalition members must be active and unaffiliated crews")
        captains = [world.entities.get(crew["captain_id"]) for crew in crews]
        if founder_id not in {captain.get("id") for captain in captains if captain}:
            raise ValueError("Faction founder must captain one of the member crews")
        alignments = [set(crew.get("ideology_tags", ())) for crew in crews]
        shared_alignment = set.intersection(*alignments) if alignments else set()
        if not shared_alignment:
            raise ValueError("Faction members must share at least one alignment")
        founder_crew = crews[[crew["captain_id"] for crew in crews].index(founder_id)]
        founder_ships = [world.entities[ship_id] for ship_id in founder_crew["ship_ids"]
                         if ship_id in world.entities]
        if not any(world.ship_is_commandable(ship["id"])
                   and world.entities[ship["id"]].get("controller_crew_id")
                   == founder_crew["id"]
                   and world.location_of(ship["id"]).get("kind") == "island"
                   and world.location_of(ship["id"]).get("id") == anchorage_island_id
                   for ship in founder_ships):
            raise ValueError("Faction founder must establish the anchorage at a commanded island")
        if not caused_by:
            raise ValueError("Faction founding requires causal crew events")
        for member_id in caused_by:
            world.event_log.get(member_id)
        if any(island_id in self.island_rulers for island_id in [anchorage_island_id]):
            raise ValueError("Faction anchorage is already governed")

        faction_id = world.ids.next("faction")
        member_crew_ids = sorted(member_crew_ids)
        founder_crew_id = founder_crew["id"]
        event = world.event_log.append(
            tick=world.clock.tick, event_type="FACTION_FOUNDED",
            location=anchorage_island_id,
            actors=[{"role": "faction_founder", "entity_id": founder_id},
                    *[{"role": "coalition_captain", "entity_id": crew["captain_id"]}
                      for crew in crews if crew["captain_id"] != founder_id]],
            targets=[{"role": "faction", "entity_id": faction_id},
                     *[{"role": "member_crew", "entity_id": crew_id}
                       for crew_id in member_crew_ids]],
            data={"faction_id": faction_id, "name": " ".join(name.split()),
                  "kind": kind, "home_island": anchorage_island_id,
                  "anchorage_island_id": anchorage_island_id,
                  "purpose": purpose.strip(), "founder_id": founder_id,
                  "member_crew_ids": member_crew_ids,
                  "member_captain_ids": sorted(crew["captain_id"] for crew in crews),
                  "founder_display_name": founder_name,
                  "founder_ambition": founder["traits"]["ambition"],
                  "founder_leadership": founder["stats"]["leadership"],
                  "alignment": sorted(shared_alignment),
                  "succession_rule": "council_vote"},
            caused_by=sorted(set(caused_by)),
            causes_tags=["faction_founding", "coalition", *sorted(shared_alignment)],
            magnitude=0.5)
        doctrine = {"order": 30, "freedom": 30, "profit": 70,
                    "glory": 35, "faith": 20}
        council = sorted({member for crew in crews for member in crew["roster"]})
        self.factions[faction_id] = {
            "id": faction_id, "name": " ".join(name.split()), "kind": kind,
            "leader_id": founder_id, "council": council,
            "succession_rule": "council_vote", "doctrine": doctrine,
            "territory": [anchorage_island_id], "founded_event_id": event.id,
            "last_event_id": event.id, "monthly_decisions": 0,
            "member_crew_ids": member_crew_ids, "founder_id": founder_id,
            "anchorage_island_id": anchorage_island_id, "purpose": purpose.strip(),
        }
        for crew in crews:
            crew["faction_id"] = faction_id
            crew["last_event_id"] = event.id
        self.island_rulers[anchorage_island_id] = faction_id
        return event.id

    def register_recruited_crew(self, world, crew_id: str, *,
                                owner_type: str, caused_by: str) -> str:
        """Reject the old invalid behavior of founding a faction for one new crew."""
        if owner_type not in {"merchant", "pirate", "marine", "privateer"}:
            raise ValueError("Unknown actor-budget crew type")
        if crew_id not in world.crew_ships.crews:
            raise ValueError("Unknown recruited crew")
        world.event_log.get(caused_by)
        raise ValueError("A recruited crew needs an explicit coalition before faction founding")

    def _faction(self, world, faction_id: str) -> dict[str, Any]:
        faction = self.factions.get(faction_id)
        if faction is None:
            raise ValueError(f"Unknown faction: {faction_id}")
        return faction

    def on_crew_dissolved(self, world, crew_id: str, *, caused_by: str,
                          reason: str) -> str | None:
        crew = world.crew_ships.crews.get(crew_id)
        faction_id = crew.get("faction_id") if crew else None
        faction = self.factions.get(faction_id)
        if faction is None or faction["kind"] == "marine":
            return None
        remaining_ids = [member_id for member_id in faction["member_crew_ids"]
                         if member_id != crew_id and member_id in world.crew_ships.crews]
        departure = world.event_log.append(
            tick=world.clock.tick, event_type="FACTION_MEMBER_DEPARTED",
            location=world.location_of(crew_id)["id"] if crew_id in world.entities else None,
            actors=[{"role": "departing_crew", "entity_id": crew_id}],
            targets=[{"role": "faction", "entity_id": faction_id}],
            data={"faction_id": faction_id, "crew_id": crew_id,
                  "remaining_member_crew_ids": sorted(remaining_ids), "reason": reason},
            caused_by=[caused_by], causes_tags=["faction_membership", "crew_departure", reason],
            magnitude=0.35)
        faction["member_crew_ids"] = sorted(remaining_ids)
        faction["council"] = sorted({member_id for member_crew_id in remaining_ids
                                      for member_id in world.crew_ships.crews[member_crew_id]["roster"]
                                      if member_id in world.entities})
        faction["last_event_id"] = departure.id
        if crew is not None:
            crew["faction_id"] = None
        # Crew-scoped policing cannot continue after the crew identity ends.
        # Close those registries causally rather than leaving snapshot-invalid
        # heat, bounty, or patrol assignments attached to a deleted crew.
        former_heat = self.crew_heat.pop(crew_id, 0)
        bounty = self.crew_bounties.pop(crew_id, None)
        response = self.response_assignments.pop(crew_id, None)
        if bounty is not None or former_heat or response is not None:
            closed = world.event_log.append(
                tick=world.clock.tick, event_type="GOVERNMENT_TARGET_CLOSED",
                actors=[{"role": "government", "entity_id": self.marine_command["faction_id"]}],
                targets=[{"role": "former_crew_target", "entity_id": crew_id}],
                data={"crew_id": crew_id, "faction_id": faction_id,
                      "heat": former_heat,
                      "bounty_amount_silver": bounty["amount"] if bounty else 0,
                      "bounty_event_id": bounty["event_id"] if bounty else None,
                      "response_event_id": response["event_id"] if response else None,
                      "reason": reason},
                caused_by=[departure.id],
                causes_tags=["government_response", "crew_identity_closed", reason],
                magnitude=0.2)
            faction["last_event_id"] = closed.id
        self.global_tension = min(10000, sum(self.crew_heat.values()))
        if len(remaining_ids) < self.data["rules"]["faction_min_member_crews"]:
            return self.dissolve_faction(
                world, faction_id, caused_by=faction["last_event_id"],
                reason="coalition_below_minimum_membership")
        if faction["leader_id"] not in faction["council"]:
            successor = self._successor(world, faction)
            succession = self._emit_faction_event(
                world, faction_id, "FACTION_SUCCESSION", cause_id=departure.id,
                data={"former_leader_id": faction["leader_id"],
                      "new_leader_id": successor, "rule": "coalition_council"},
                target_id=successor, tags=("succession", "crew_departure"),
                actor_id=successor)
            faction["leader_id"] = successor
            return succession.id
        return departure.id

    def dissolve_faction(self, world, faction_id: str, *, caused_by: str,
                         reason: str) -> str | None:
        faction = self.factions.get(faction_id)
        if faction is None or faction["kind"] == "marine":
            return None
        world.event_log.get(caused_by)
        member_ids = list(faction["member_crew_ids"])
        assets = sorted(ship_id for crew_id in member_ids
                        if crew_id in world.crew_ships.crews
                        for ship_id in world.crew_ships.crews[crew_id]["ship_ids"]
                        if ship_id in world.entities)
        member_people = sorted({person_id for crew_id in member_ids
                                if crew_id in world.crew_ships.crews
                                for person_id in world.crew_ships.crews[crew_id]["roster"]})
        silver = sum(world.economy.crew_accounts.get(crew_id, {}).get("treasury_silver", 0)
                     for crew_id in member_ids)
        arrears = sum(world.crew_ships.crews.get(crew_id, {}).get("arrears_silver", 0)
                      for crew_id in member_ids)
        event = world.event_log.append(
            tick=world.clock.tick, event_type="FACTION_DISSOLVED",
            actors=[{"role": "faction_leader", "entity_id": faction["leader_id"]}],
            targets=[{"role": "former_faction", "entity_id": faction_id}],
            data={"faction_id": faction_id, "name": faction["name"], "reason": reason,
                  "territory": list(faction["territory"]),
                  "member_crew_ids": member_ids, "member_character_ids": member_people,
                  "ship_ids": assets, "crew_treasury_silver": silver,
                  "crew_arrears_silver": arrears,
                  "successor_faction_id": None,
                  "successor_crew_ids": sorted(member_ids)},
            caused_by=[caused_by], causes_tags=["faction_dissolution", reason], magnitude=0.8,
        )
        archived = json.loads(json.dumps(faction, sort_keys=True))
        archived["dissolved_event_id"] = event.id
        self.archived_factions[faction_id] = archived
        del self.factions[faction_id]
        for crew_id in member_ids:
            crew = world.crew_ships.crews.get(crew_id)
            if crew:
                crew["faction_id"] = None
                crew["last_event_id"] = event.id
        for island_id in faction["territory"]:
            self.island_rulers.pop(island_id, None)
            self.fortifications.pop(island_id, None)
        for crew_id in member_ids:
            self.response_assignments.pop(crew_id, None)
        self.battles_cleanup_command(world, faction_id)
        return event.id

    @staticmethod
    def battles_cleanup_command(world, faction_id: str) -> None:
        world.battles.fleet_commands.pop(faction_id, None)

    def resolve_battle_power_vacuum(self, world, faction_id: str, *,
                                    defeated_captain: str, caused_by: str) -> str | None:
        """Record a commander legitimacy crisis and install the strongest living council successor."""
        faction = self.factions.get(faction_id)
        if faction is None or faction.get("leader_id") != defeated_captain:
            return None
        candidates = [world.entities[member_id] for member_id in faction["council"]
                      if member_id in world.entities and member_id != defeated_captain]
        if not candidates:
            return None
        successor = min(candidates, key=lambda row: (
            -(row["stats"]["leadership"] * 2 + row["traits"]["loyalty"]
              - row["traits"]["ambition"] // 2), row["id"]))
        event = world.event_log.append(
            tick=world.clock.tick, event_type="FACTION_POWER_VACUUM",
            location=world.location_of(successor["id"])["id"],
            actors=[{"role": "displaced_leader", "entity_id": defeated_captain}],
            targets=[{"role": "successor", "entity_id": successor["id"]}],
            data={"faction_id": faction_id, "former_leader_id": defeated_captain,
                  "new_leader_id": successor["id"], "cause": "set_piece_defeat"},
            caused_by=[caused_by],
            causes_tags=["faction_power_vacuum", "battle_defeat", "succession"],
            magnitude=0.75,
        )
        faction["leader_id"] = successor["id"]
        faction["last_event_id"] = event.id
        return event.id

    def _emit_faction_event(self, world, faction_id: str, event_type: str, *,
                            cause_id: str, data: dict[str, Any],
                            target_id: str | None = None, location: str | None = None,
                            tags: tuple[str, ...] = (), actor_id: str | None = None):
        faction = self.factions[faction_id]
        leader_id = actor_id or faction["leader_id"]
        event = world.event_log.append(
            tick=world.clock.tick, event_type=event_type, location=location,
            actors=[{"role": "faction_leader", "entity_id": leader_id}],
            targets=([{"role": "political_target", "entity_id": target_id}]
                     if target_id else [{"role": "faction", "entity_id": faction_id}]),
            data={"faction_id": faction_id, **data}, caused_by=[cause_id],
            causes_tags=["faction_politics", *tags], magnitude=0.3,
        )
        faction["last_event_id"] = event.id
        return event

    def _successor(self, world, faction: dict[str, Any]) -> str:
        members = [self._character(world, member_id) for member_id in faction["council"]
                   if member_id in world.entities]
        if not members:
            raise ValueError("Cannot select a faction successor without living council members")
        if faction["succession_rule"] == "captain":
            captains = [world.crew_ships.crews[crew_id]["captain_id"]
                        for crew_id in faction.get("member_crew_ids", ())
                        if crew_id in world.crew_ships.crews]
            for captain_id in captains:
                if captain_id in {row["id"] for row in members}:
                    return captain_id
        return min(members, key=lambda row: (
            -(row["stats"]["leadership"] * 2 + row["traits"]["loyalty"]
              - row["traits"]["ambition"] // 2), row["id"]))["id"]

    def _adjacent_unclaimed(self, world, faction: dict[str, Any]) -> list[str]:
        adjacent = set()
        for island_id in faction["territory"]:
            for route in world.geography.adjacency[island_id]:
                candidate = (route.destination if route.origin == island_id else route.origin)
                if candidate not in self.island_rulers:
                    adjacent.add(candidate)
        return sorted(adjacent)

    def _monthly_decision(self, world, faction_id: str) -> None:
        faction = self.factions[faction_id]
        if faction["kind"] == "marine":
            self._monthly_marine_politics(world, faction_id)
            return
        member_crews = [world.crew_ships.crews[crew_id]
                        for crew_id in faction.get("member_crew_ids", ())
                        if crew_id in world.crew_ships.crews
                        and world.crew_ships.crews[crew_id].get("faction_id") == faction_id]
        if not member_crews:
            return
        active_roster = sorted({member for crew in member_crews for member in crew["roster"]
                                if member in world.entities})
        faction["council"] = active_roster
        if not active_roster:
            return
        cause = faction["last_event_id"]
        if faction["leader_id"] not in active_roster:
            old_leader = faction["leader_id"]
            successor = self._successor(world, faction)
            event = self._emit_faction_event(
                world, faction_id, "FACTION_SUCCESSION", cause_id=max(
                    (crew["last_event_id"] for crew in member_crews), default=faction["last_event_id"]),
                data={"former_leader_id": old_leader, "new_leader_id": successor,
                      "rule": faction["succession_rule"]}, target_id=successor,
                tags=("succession",), actor_id=successor)
            faction["leader_id"] = successor
            cause = event.id

        leader = self._character(world, faction["leader_id"])
        stream = world.rng.stream(f"politics:faction:{faction_id}")
        challengers = [self._character(world, member_id) for member_id in active_roster
                       if member_id != faction["leader_id"]]
        challengers.sort(key=lambda row: (
            -(row["traits"]["ambition"] - row["traits"]["loyalty"]
              - leader["traits"]["ambition"] + leader["traits"]["loyalty"]), row["id"]))
        if challengers:
            challenger = challengers[0]
            ambition_margin = max(0, challenger["traits"]["ambition"]
                                  - leader["traits"]["ambition"])
            coup_chance = min(10000, self.data["rules"]["coup_base_basis_points"]
                              + ambition_margin
                              * self.data["rules"]["coup_ambition_basis_points"])
            if stream.randbelow(10000) < coup_chance:
                attempt = self._emit_faction_event(
                    world, faction_id, "FACTION_COUP_ATTEMPTED", cause_id=cause,
                    data={"challenger_id": challenger["id"],
                          "ambition_margin": ambition_margin}, target_id=challenger["id"],
                    tags=("internal_politics", "coup_attempt"))
                success = min(10000, self.data["rules"]["coup_success_base_basis_points"]
                              + (challenger["stats"]["leadership"]
                                 - leader["stats"]["leadership"])
                              * self.data["rules"]["coup_success_leadership_basis_points"]
                              + max(0, challenger["traits"]["loyalty"]
                                    - leader["traits"]["loyalty"]) * 20)
                if stream.randbelow(10000) < max(0, success):
                    event = self._emit_faction_event(
                        world, faction_id, "FACTION_LEADER_CHANGED", cause_id=attempt.id,
                        data={"former_leader_id": leader["id"],
                              "new_leader_id": challenger["id"],
                              "cause": "successful_coup"}, target_id=challenger["id"],
                        tags=("internal_politics", "coup_success"))
                    faction["leader_id"] = challenger["id"]
                    cause = event.id
                else:
                    event = self._emit_faction_event(
                        world, faction_id, "FACTION_COUP_FAILED", cause_id=attempt.id,
                        data={"challenger_id": challenger["id"],
                              "leader_id": leader["id"]}, target_id=challenger["id"],
                        tags=("internal_politics", "coup_failed"))
                    cause = event.id

        faction = self.factions[faction_id]
        leader = self._character(world, faction["leader_id"])
        candidates = self._adjacent_unclaimed(world, faction)
        chance = min(10000, self.data["rules"]["expansion_base_basis_points"]
                     + leader["traits"]["ambition"]
                     * self.data["rules"]["expansion_ambition_basis_points"])
        action = "hold"
        target = None
        if candidates and len(faction["territory"]) < self.data["rules"]["max_faction_territories"]:
            if stream.randbelow(10000) < chance:
                action, target = "claim_unruled", candidates[stream.randbelow(len(candidates))]
        if action == "claim_unruled":
            decision = self._emit_faction_event(
                world, faction_id, "FACTION_MONTHLY_DECISION", cause_id=cause,
                data={"action": action, "target_island": target,
                      "expansion_chance_basis_points": chance}, target_id=target,
                location=target, tags=("monthly_decision",))
            claim = world.event_log.append(
                tick=world.clock.tick, event_type="TERRITORY_CLAIMED", location=target,
                actors=[{"role": "claiming_faction_leader", "entity_id": faction["leader_id"]}],
                targets=[{"role": "island", "entity_id": target}],
                data={"faction_id": faction_id, "previous_ruler": None,
                      "claim_type": "unruled_expansion"}, caused_by=[decision.id],
                causes_tags=["territory_change", "peaceful_claim"], magnitude=0.4,
            )
            self.island_rulers[target] = faction_id
            faction["territory"] = sorted([*faction["territory"], target])
            faction["last_event_id"] = claim.id
        else:
            held = [island_id for island_id in faction["territory"]
                    if self.fortifications.get(island_id, 0) < 3]
            if held:
                island_id = held[stream.randbelow(len(held))]
                self._emit_faction_event(
                    world, faction_id, "FACTION_MONTHLY_DECISION", cause_id=cause,
                    data={"action": "fortify", "target_island": island_id},
                    target_id=island_id, location=island_id, tags=("monthly_decision",))
                level = self.fortifications.get(island_id, 0) + 1
                self.fortifications[island_id] = level
                fortify = world.event_log.append(
                    tick=world.clock.tick, event_type="ISLAND_FORTIFIED", location=island_id,
                    actors=[{"role": "fortifying_leader", "entity_id": faction["leader_id"]}],
                    targets=[{"role": "island", "entity_id": island_id}],
                    data={"faction_id": faction_id, "level": level},
                    caused_by=[faction["last_event_id"]],
                    causes_tags=["territory_defense", "fortification"], magnitude=0.2,
                )
                faction["last_event_id"] = fortify.id
            else:
                self._emit_faction_event(
                    world, faction_id, "FACTION_MONTHLY_DECISION", cause_id=cause,
                    data={"action": "hold", "target_island": None},
                    tags=("monthly_decision",))
        faction["monthly_decisions"] += 1

    def _monthly_marine_politics(self, world, faction_id: str) -> None:
        faction = self.factions[faction_id]
        command = self.marine_command
        if command.get("faction_id") != faction_id:
            raise ValueError("Marine command references the wrong faction")
        active_ids = [member for member in [command["commander_id"], *command["deputy_ids"]]
                      if member in world.entities]
        eligible = [row for row in world.entities.values()
                    if row.get("kind") == "character" and row.get("crew_id") is None
                    and row["id"] not in active_ids]
        eligible.sort(key=lambda row: (
            -(row["stats"]["leadership"] * 2 + row["traits"]["loyalty"]
              - row["traits"]["ambition"] // 2), row["id"]))
        cause = faction["last_event_id"]
        commander_id = command["commander_id"]
        if commander_id not in world.entities:
            if not active_ids:
                if not eligible:
                    return
                active_ids = [eligible.pop(0)["id"]]
            successor = min(active_ids, key=lambda entity_id: (
                -(world.entities[entity_id]["stats"]["leadership"] * 2
                  + world.entities[entity_id]["traits"]["loyalty"]
                  - world.entities[entity_id]["traits"]["ambition"] // 2), entity_id))
            deceased = world.archive.get(commander_id, {})
            death_event = deceased.get("last_event_id", cause)
            event = self._emit_faction_event(
                world, faction_id, "FACTION_SUCCESSION", cause_id=death_event,
                data={"former_leader_id": commander_id, "new_leader_id": successor,
                      "rule": "council_vote", "institution": "marine_command"},
                target_id=successor, tags=("succession", "marine_command"),
                actor_id=successor)
            command["commander_id"] = successor
            command["deputy_ids"] = sorted(member for member in active_ids
                                            if member != successor)
            command["appointment_event_id"] = event.id
            faction["leader_id"] = successor
            commander_id = successor
            cause = event.id

        leader = self._character(world, commander_id)
        officers = [self._character(world, officer_id)
                    for officer_id in command["deputy_ids"] if officer_id in world.entities]
        stream = world.rng.stream("politics:marine-command")
        challenger = max(officers, key=lambda row: (
            row["traits"]["ambition"] - row["traits"]["loyalty"], row["id"]), default=None)
        if challenger is not None:
            margin = max(0, challenger["traits"]["ambition"] - leader["traits"]["ambition"])
            chance = min(10000, self.data["rules"]["marine_challenge_base_basis_points"]
                         + margin * self.data["rules"]["marine_challenge_ambition_basis_points"])
            if stream.randbelow(10000) < chance:
                challenge = self._emit_faction_event(
                    world, faction_id, "MARINE_COMMAND_CHALLENGE", cause_id=cause,
                    data={"challenger_id": challenger["id"], "ambition_margin": margin},
                    target_id=challenger["id"], tags=("internal_politics", "marine_command"))
                success = min(10000,
                              self.data["rules"]["marine_challenge_success_base_basis_points"]
                              + (challenger["stats"]["leadership"]
                                 - leader["stats"]["leadership"]) * 50
                              + max(0, challenger["traits"]["loyalty"]
                                    - leader["traits"]["loyalty"]) * 20)
                if stream.randbelow(10000) < max(0, success):
                    change = self._emit_faction_event(
                        world, faction_id, "MARINE_COMMANDER_CHANGED", cause_id=challenge.id,
                        data={"former_commander_id": commander_id,
                              "new_commander_id": challenger["id"],
                              "reason": "command_challenge"}, target_id=challenger["id"],
                        tags=("promotion_politics", "marine_command"))
                    command["commander_id"] = challenger["id"]
                    command["deputy_ids"] = sorted(
                        [*set(command["deputy_ids"]) - {challenger["id"]}, commander_id])
                    command["appointment_event_id"] = change.id
                    faction["leader_id"] = challenger["id"]
                    cause = change.id
                else:
                    failed = self._emit_faction_event(
                        world, faction_id, "MARINE_COMMAND_CHALLENGE_FAILED",
                        cause_id=challenge.id,
                        data={"challenger_id": challenger["id"],
                              "commander_id": commander_id}, target_id=challenger["id"],
                        tags=("promotion_politics", "marine_command"))
                    cause = failed.id

        deputy_ids = [member for member in command["deputy_ids"]
                      if member in world.entities and member != command["commander_id"]]
        vacancies = max(0, self.data["rules"]["marine_officer_slots"] - 1 - len(deputy_ids))
        for officer in eligible[:vacancies]:
            event = self._emit_faction_event(
                world, faction_id, "MARINE_OFFICER_APPOINTED", cause_id=cause,
                data={"officer_id": officer["id"], "rank": "deputy_commander"},
                target_id=officer["id"], tags=("promotion", "marine_command"))
            officer["home_faction"] = faction_id
            deputy_ids.append(officer["id"])
            cause = event.id
            command["appointment_event_id"] = event.id
        command["deputy_ids"] = sorted(deputy_ids)
        faction["council"] = sorted([command["commander_id"], *command["deputy_ids"]])
        review = self._emit_faction_event(
            world, faction_id, "MARINE_COMMAND_REVIEWED", cause_id=cause,
            data={"commander_id": command["commander_id"],
                  "deputy_ids": command["deputy_ids"], "review_day": self.last_updated_day},
            target_id=command["commander_id"], tags=("monthly_decision", "marine_command"))
        faction["monthly_decisions"] += 1
        command["appointment_event_id"] = command.get("appointment_event_id", review.id)

    def update_day(self, day: int, world) -> None:
        if type(day) is not int or day != self.last_updated_day + 1:
            raise ValueError("Politics days must be consecutive")
        self.last_updated_day = day
        decay = self.data["rules"]["heat_daily_decay"]
        for registry in (self.character_heat, self.crew_heat):
            for entity_id in list(registry):
                registry[entity_id] = max(0, registry[entity_id] - decay)
        self.global_tension = min(10000, sum(self.crew_heat.values()))
        if day % self.data["rules"]["faction_month_days"] != 0:
            return
        for faction_id in sorted(self.factions):
            if faction_id in self.factions:
                if not self.factions[faction_id]["kind"].startswith("actor_"):
                    self._monthly_decision(world, faction_id)
        self._monthly_diplomacy(world)
        self._allocate_government_response(world)

    def _emit_heat(self, world, *, entity_id: str, crew_id: str,
                   amount: int, caused_by: str, reason: str) -> str:
        if type(amount) is not int or amount <= 0:
            raise ValueError("Heat changes must be positive integers")
        before_crew = self.crew_heat.get(crew_id, 0)
        before_person = self.character_heat.get(entity_id, 0)
        after_crew = min(1000, before_crew + amount)
        after_person = min(1000, before_person + amount)
        self.crew_heat[crew_id] = after_crew
        self.character_heat[entity_id] = after_person
        event = world.event_log.append(
            tick=world.clock.tick, event_type="HEAT_CHANGED", location=world.location_of(entity_id)["id"],
            actors=[{"role": "notorious_character", "entity_id": entity_id}],
            targets=[{"role": "crew", "entity_id": crew_id}],
            data={"reason": reason, "delta": after_crew - before_crew,
                  "character_heat": after_person, "crew_heat": after_crew},
            caused_by=[caused_by], causes_tags=["government_heat", reason],
            magnitude=min(1.0, amount / 100),
        )
        thresholds = self.data["rules"]
        threshold_event_ids = []
        crossed = [("bounty", thresholds["heat_bounty_threshold"]),
                   ("hunter", thresholds["heat_hunter_threshold"]),
                   ("fleet", thresholds["heat_fleet_threshold"])]
        for label, threshold in crossed:
            if before_crew < threshold <= after_crew:
                threshold_event = world.event_log.append(
                    tick=world.clock.tick, event_type="HEAT_THRESHOLD_CROSSED",
                    location=world.location_of(entity_id)["id"],
                    actors=[{"role": "government", "entity_id": self.marine_command["faction_id"]}],
                    targets=[{"role": "crew", "entity_id": crew_id}],
                    data={"threshold": label, "heat": after_crew},
                    caused_by=[event.id], causes_tags=["government_heat", "threshold", label],
                    magnitude=0.35,
                )
                threshold_event_ids.append(threshold_event.id)
        bounty_threshold = thresholds["heat_bounty_threshold"]
        if after_crew >= bounty_threshold:
            amount_due = thresholds["bounty_base_silver"] + after_crew * thresholds[
                "bounty_per_heat_silver"]
            current = self.crew_bounties.get(crew_id, {"amount": 0, "event_id": None})
            if amount_due > current["amount"]:
                kind = "BOUNTY_RAISED" if current["amount"] else "BOUNTY_SET"
                bounty_event = world.event_log.append(
                    tick=world.clock.tick, event_type=kind, location=world.location_of(entity_id)["id"],
                    actors=[{"role": "government", "entity_id": self.marine_command["faction_id"]}],
                    targets=[{"role": "bounty_target_crew", "entity_id": crew_id}],
                    data={"amount_silver": amount_due, "previous_amount_silver": current["amount"],
                          "heat": after_crew},
                    caused_by=[threshold_event_ids[-1] if threshold_event_ids else event.id],
                    causes_tags=["government_bounty", "heat_policy"], magnitude=0.4,
                )
                self.crew_bounties[crew_id] = {"amount": amount_due, "event_id": bounty_event.id}
                person = world.entities.get(entity_id)
                if person:
                    person["bounty"] = {"amount": amount_due,
                                        "issuer_faction": self.marine_command["faction_id"],
                                        "tick_set": world.clock.tick}
        if threshold_event_ids and after_crew >= bounty_threshold:
            bounty_event_id = self.crew_bounties[crew_id]["event_id"]
            self._allocate_government_response(
                world, only_crew_id=crew_id, cause_override=bounty_event_id)
        self.global_tension = min(10000, sum(self.crew_heat.values()))
        return event.id

    def on_combat_resolved(self, world, *, winner_crew_id: str | None,
                           loser_crew_id: str | None, outcome: str,
                           winner_captain_id: str | None, loser_captain_id: str | None,
                           caused_by: str) -> str | None:
        if not winner_crew_id or not loser_crew_id or outcome in {
                "mutual_withdrawal", "parley_ended_fight"}:
            return None
        # Only warrants that existed before this encounter can be claimed from
        # it; combat-generated heat may create a future warrant, not its own payout.
        bounty = self.crew_bounties.get(loser_crew_id)
        claimable_bounty = bounty["amount"] if bounty else 0
        amount = self.data["rules"]["heat_per_hostile_combat"]
        if outcome in {"ship_sunk", "ship_captured", "captain_killed"}:
            amount += self.data["rules"]["heat_per_ship_sunk"]
        heat_event = None
        if winner_captain_id in world.entities:
            heat_event = self._emit_heat(world, entity_id=winner_captain_id,
                                         crew_id=winner_crew_id, amount=amount,
                                         caused_by=caused_by, reason="hostile_combat")
        if loser_captain_id in world.entities:
            loser_event = self._emit_heat(
                world, entity_id=loser_captain_id, crew_id=loser_crew_id,
                amount=self.data["rules"]["heat_per_hostile_combat"],
                caused_by=caused_by, reason="hostile_combat")
            heat_event = heat_event or loser_event
        breach_event = self._record_hostile_treaty_breach(
            world, winner_crew_id, loser_crew_id, caused_by=caused_by)
        if breach_event:
            heat_event = breach_event
        winner_faction_id = world.crew_ships.crews.get(winner_crew_id, {}).get("faction_id")
        if (bounty and claimable_bounty > 0 and loser_crew_id in world.crew_ships.crews
                and winner_faction_id in self.factions):
            payment = min(claimable_bounty, world.economy.government_silver)
            if payment:
                paid = world.economy.pay_government_bounty(
                    world, winner_crew_id, payment, caused_by=caused_by,
                    target_id=loser_crew_id)
                bounty["amount"] -= payment
                if bounty["amount"] == 0:
                    bounty["claimed_event_id"] = paid
                for member_id in world.crew_ships.crews[loser_crew_id]["roster"]:
                    person = world.entities.get(member_id)
                    if person and person.get("bounty", {}).get("issuer_faction") == \
                            self.marine_command["faction_id"]:
                        person["bounty"]["amount"] = bounty["amount"]
                world.crew_ships.crews[winner_crew_id]["last_event_id"] = paid
                world.encounters.bounty_silver[winner_crew_id] = (
                    world.encounters.bounty_silver.get(winner_crew_id, 0) + payment)
                return paid
        return heat_event

    def _record_hostile_treaty_breach(self, world, first: str, second: str, *,
                                      caused_by: str) -> str | None:
        first_crew = world.crew_ships.crews.get(first)
        second_crew = world.crew_ships.crews.get(second)
        first_faction = first_crew.get("faction_id") if first_crew else first
        second_faction = second_crew.get("faction_id") if second_crew else second
        if not first_faction or not second_faction or first_faction == second_faction:
            return None
        key = self._pair_key(first_faction, second_faction)
        treaty = self.treaties.get(key)
        if (first_faction not in self.factions or second_faction not in self.factions
                or not treaty or treaty["state"] == "war"
                or "no_hostile_action" not in treaty["terms"]):
            return None
        breach = world.event_log.append(
            tick=world.clock.tick, event_type="TREATY_BREACHED", location=None,
            actors=[{"role": "attacking_faction", "entity_id": first_faction}],
            targets=[{"role": "betrayed_faction", "entity_id": second_faction}],
            data={"treaty_event_id": treaty["event_id"], "reason": "hostile_combat",
                  "factions": sorted((first_faction, second_faction)),
                  "attacking_crew_id": first, "harmed_crew_id": second},
            caused_by=[caused_by, treaty["event_id"]],
            causes_tags=["treaty_breach", "betrayal", "hostile_combat"], magnitude=0.8,
        )
        treaty["breach_count"] += 1
        institutional = self.institutional.setdefault(
            key, {"trust": 50, "fear": 50, "grievance": 50})
        institutional["trust"] = max(0, institutional["trust"] - 35)
        institutional["grievance"] = min(100, institutional["grievance"] + 40)
        first_leader = self.factions.get(first_faction, {}).get("leader_id")
        second_leader = self.factions.get(second_faction, {}).get("leader_id")
        for holder_id, target_id in ((first_leader, second_leader),
                                     (second_leader, first_leader)):
            if holder_id in world.entities and target_id in world.entities:
                world.memories.add_memory(
                    world, holder_id, target_id, "betrayed", event_id=breach.id,
                    intensity_permille=800, valence_permille=-800)
        transition = self.propose_treaty(
            world, first_faction, second_faction, "war", caused_by=breach.id)
        return transition

    def _monthly_diplomacy(self, world) -> None:
        active = sorted(faction_id for faction_id, faction in self.factions.items()
                        if not faction["kind"].startswith("actor_"))
        for index, first in enumerate(active):
            for second in active[index + 1:]:
                key = self._pair_key(first, second)
                current = self.treaties.get(key, {"state": "none"})["state"]
                relation = self.relation(world, first, second)
                if current == "alliance":
                    continue
                if current == "war":
                    if relation["trust"] >= 25 and relation["grievance"] <= 60:
                        target_state = "nonaggression"
                    else:
                        continue
                elif current == "none":
                    target_state = "nonaggression"
                elif current in {"nonaggression", "trade_pact"}:
                    target_state = "alliance" if relation["trust"] >= 70 else None
                elif current == "vassalage":
                    continue
                else:
                    continue
                stream = world.rng.stream(f"politics:treaty:{key.replace('|', '/')}")
                if stream.randbelow(10000) >= self.data["rules"][
                        "monthly_diplomacy_basis_points"]:
                    continue
                if target_state is None and current in {"nonaggression", "trade_pact"}:
                    self._diplomatic_exchange(world, first, second)
                    continue
                if target_state is None:
                    continue
                cause = self.factions[first]["last_event_id"]
                try:
                    self.propose_treaty(world, first, second, target_state, caused_by=cause)
                except ValueError as exc:
                    if "thresholds" not in str(exc):
                        raise

    def _diplomatic_exchange(self, world, first: str, second: str) -> str:
        first_leader = self.factions[first]["leader_id"]
        second_leader = self.factions[second]["leader_id"]
        event = world.event_log.append(
            tick=world.clock.tick, event_type="DIPLOMATIC_EXCHANGE", location=None,
            actors=[{"role": "envoy", "entity_id": first_leader}],
            targets=[{"role": "envoy", "entity_id": second_leader}],
            data={"factions": sorted((first, second)),
                  "treaty_state": self.treaties[self._pair_key(first, second)]["state"]},
            caused_by=[self.factions[first]["last_event_id"],
                       self.factions[second]["last_event_id"]],
            causes_tags=["diplomacy", "confidence_building"], magnitude=0.2,
        )
        state = self.treaties[self._pair_key(first, second)]["state"]
        self._record_treaty_memories(world, first, second, event.id, state)
        self.factions[first]["last_event_id"] = event.id
        self.factions[second]["last_event_id"] = event.id
        return event.id

    def _record_treaty_memories(self, world, first: str, second: str,
                                treaty_event_id: str, state: str) -> None:
        if state not in {"nonaggression", "trade_pact", "alliance", "vassalage"}:
            return
        members_a = [member for member in self.factions[first]["council"]
                     if member in world.entities]
        members_b = [member for member in self.factions[second]["council"]
                     if member in world.entities]
        kind = "traded_fairly" if state == "trade_pact" else "honored"
        for holder_id in members_a:
            for target_id in members_b:
                world.memories.add_memory(world, holder_id, target_id, kind,
                                          event_id=treaty_event_id,
                                          intensity_permille=400,
                                          valence_permille=500)
                world.memories.add_memory(world, target_id, holder_id, kind,
                                          event_id=treaty_event_id,
                                          intensity_permille=400,
                                          valence_permille=500)

    def _allocate_government_response(self, world, *, only_crew_id: str | None = None,
                                      cause_override: str | None = None) -> None:
        rules = self.data["rules"]
        tiers = (("fleet", rules["heat_fleet_threshold"], rules["monthly_fleet_cost_silver"]),
                 ("hunter", rules["heat_hunter_threshold"], rules["monthly_hunter_cost_silver"]),
                 ("patrol", rules["heat_bounty_threshold"], rules["monthly_patrol_cost_silver"]))
        ranked = sorted(self.crew_heat.items(), key=lambda row: (-row[1], row[0]))
        for crew_id, heat in ranked:
            if only_crew_id is not None and crew_id != only_crew_id:
                continue
            tier = next(((label, cost) for label, threshold, cost in tiers if heat >= threshold), None)
            if tier is None or crew_id not in world.crew_ships.crews:
                continue
            label, desired_cost = tier
            crew = world.crew_ships.crews[crew_id]
            faction = self.factions.get(crew.get("faction_id"))
            if (not faction or faction["kind"].startswith("actor_")
                    or not faction["territory"]):
                continue
            if world.economy.government_silver <= 0:
                break
            island_id = faction["territory"][0]
            cost = min(desired_cost, world.economy.government_silver)
            cause = cause_override or self.crew_bounties.get(crew_id, {}).get("event_id") \
                or faction["last_event_id"]
            response_event = world.economy.spend_government_response(
                world, island_id, cost, caused_by=cause,
                target_crew_id=crew_id, response=label)
            self.response_assignments[crew_id] = {
                "level": label, "island_id": island_id, "event_id": response_event,
                "last_day": self.last_updated_day, "silver_spent": cost,
            }

    def relation(self, world, first: str, second: str) -> dict[str, int]:
        a, b = self._faction(world, first), self._faction(world, second)
        key = self._pair_key(first, second)
        institution = self.institutional.get(key, {})
        members_a = [world.entities[cid] for cid in a["council"] if cid in world.entities]
        members_b = [world.entities[cid] for cid in b["council"] if cid in world.entities]
        opinions = [person.get("opinions", {}).get(target["id"], {})
                    for person in members_a for target in members_b]
        reverse = [person.get("opinions", {}).get(target["id"], {})
                   for person in members_b for target in members_a]
        def mean(rows, dimension):
            observed = [row[dimension] for row in rows if dimension in row]
            return sum(observed) // max(1, len(observed))
        values = {}
        for dim, opinion_dim in (("trust", "trust"), ("fear", "fear"),
                                 ("grievance", "grievance")):
            score = (mean(opinions, opinion_dim) + mean(reverse, opinion_dim)) // 2
            base = 50 + score * 50 // 100
            weights = self.data["relation_weights"][dim]
            opinion_weight = weights[f"opinion_{opinion_dim}"]
            institution_weight = weights["institutional"]
            denominator = max(1, opinion_weight + institution_weight)
            values[dim] = max(0, min(100, (
                base * opinion_weight + institution.get(dim, 50) * institution_weight
            ) // denominator))
        houses_a = {world.entities[cid].get("heritage", {}).get("house")
                    for cid in a["council"] if cid in world.entities}
        houses_b = {world.entities[cid].get("heritage", {}).get("house")
                    for cid in b["council"] if cid in world.entities}
        shared_house = bool((houses_a - {None}) & (houses_b - {None}))
        kinship_weight = self.data["relation_weights"]["kinship"]["shared_house"]
        values["kinship"] = kinship_weight // 10 if shared_house else 0
        volume = self.trade_volume.get(key, 0)
        trade_weight = self.data["relation_weights"]["trade_dependence"]["trade_volume"]
        values["trade_dependence"] = min(
            self.data["rules"]["trade_dependence_cap"],
            volume * trade_weight // (1000 * max(1, self.data["rules"]["trade_dependence_per_volume"])),
        )
        return values

    def record_trade(self, first: str, second: str, volume: int, *, world, caused_by: str) -> str:
        self._faction(world, first); self._faction(world, second)
        if type(volume) is not int or volume <= 0:
            raise ValueError("Trade volume must be a positive integer")
        parent = world.event_log.get(caused_by)
        if parent.tick > world.clock.tick:
            raise ValueError("Trade evidence cannot come from a future event")
        key = self._pair_key(first, second)
        total = self.trade_volume.get(key, 0) + volume
        if total > self.data["rules"]["max_trade_volume"]:
            raise ValueError("Faction trade volume exceeds configured bound")
        self.trade_volume[key] = total
        event = world.event_log.append(
            tick=world.clock.tick, event_type="FACTION_TRADE_RECORDED", location=None,
            actors=[{"role": "trading_faction", "entity_id": first}],
            targets=[{"role": "counterparty", "entity_id": second}],
            data={"volume": volume, "cumulative_volume": total}, caused_by=[caused_by],
            causes_tags=["institutional_trade"], magnitude=min(1.0, volume / 1000),
        )
        return event.id

    def propose_treaty(self, world, proposer: str, target: str, state: str, *,
                       caused_by: str, terms: list[str] | None = None) -> str:
        self._faction(world, proposer); self._faction(world, target)
        key = self._pair_key(proposer, target)
        current = self.treaties.get(key, {"state": "none"})["state"]
        rule = self.data["transitions"].get(current, {}).get(state)
        if rule is None:
            raise ValueError(f"Invalid treaty transition: {current}->{state}")
        relation = self.relation(world, proposer, target)
        if (relation["trust"] < rule["min_trust"]
                or relation["grievance"] > rule["max_grievance"]):
            raise ValueError("Faction relation does not meet treaty thresholds")
        required_terms = self.data["terms"][state]
        chosen_terms = list(required_terms if terms is None else terms)
        if len(set(chosen_terms)) != len(chosen_terms) or set(chosen_terms) != set(required_terms):
            raise ValueError("Treaty terms must exactly satisfy the data-defined obligations")
        cause = world.event_log.get(caused_by)
        if cause.tick > world.clock.tick:
            raise ValueError("Treaty proposal cause cannot be from the future")
        event = world.event_log.append(
            tick=world.clock.tick, event_type="TREATY_TRANSITION", location=None,
            actors=[{"role": "proposer", "entity_id": proposer}],
            targets=[{"role": "treaty_partner", "entity_id": target}],
            data={"factions": sorted([proposer, target]), "from_state": current,
                  "to_state": state, "terms": chosen_terms}, caused_by=[caused_by],
            causes_tags=["treaty_transition", f"treaty:{state}"], magnitude=0.4,
        )
        previous = self.treaties.get(key, {})
        self.treaties[key] = {
            "factions": sorted([proposer, target]), "state": state,
            "terms": chosen_terms, "event_id": event.id,
            "obligations": previous.get("obligations", []),
            "breach_count": previous.get("breach_count", 0),
        }
        self._record_treaty_memories(world, proposer, target, event.id, state)
        return event.id

    def request_aid(self, world, requester: str, ally: str, *, caused_by: str) -> str:
        self._faction(world, requester)
        self._faction(world, ally)
        key = self._pair_key(requester, ally)
        treaty = self.treaties.get(key)
        if not treaty or "mutual_defense" not in treaty["terms"]:
            raise ValueError("No mutual-defense obligation exists")
        event = world.event_log.append(
            tick=world.clock.tick, event_type="TREATY_AID_REQUESTED", location=None,
            actors=[{"role": "requester", "entity_id": requester}],
            targets=[{"role": "obligated_ally", "entity_id": ally}],
            data={"treaty_event_id": treaty["event_id"]}, caused_by=[caused_by],
            causes_tags=["treaty_obligation", "mutual_defense"], magnitude=0.3,
        )
        treaty["obligations"].append({"event_id": event.id, "kind": "mutual_defense",
                                      "obligated_faction": ally, "requester": requester,
                                      "status": "open"})
        return event.id

    def resolve_obligation(self, world, faction_id: str, obligation_event_id: str, *,
                           fulfilled: bool, caused_by: str) -> str:
        if type(fulfilled) is not bool:
            raise ValueError("Obligation fulfillment must be boolean")
        obligation = None
        treaty = None
        for row in self.treaties.values():
            found = next((item for item in row["obligations"]
                          if item["event_id"] == obligation_event_id), None)
            if found:
                treaty, obligation = row, found
                break
        if obligation is None or obligation["status"] != "open":
            raise ValueError("Unknown or already resolved treaty obligation")
        if obligation["obligated_faction"] != faction_id:
            raise ValueError("Only the obligated faction may resolve this obligation")
        event_type = "TREATY_OBLIGATION_FULFILLED" if fulfilled else "TREATY_BREACHED"
        event = world.event_log.append(
            tick=world.clock.tick, event_type=event_type, location=None,
            actors=[{"role": "obligated_faction", "entity_id": faction_id}],
            targets=[{"role": "requester", "entity_id": obligation["requester"]}],
            data={"obligation_event_id": obligation_event_id,
                  "treaty_event_id": treaty["event_id"]},
            caused_by=list(dict.fromkeys([caused_by, obligation_event_id])),
            causes_tags=["treaty_obligation", "honored" if fulfilled else "treaty_breach"],
            magnitude=0.3,
        )
        obligation["status"] = "fulfilled" if fulfilled else "breached"
        if not fulfilled:
            treaty["breach_count"] += 1
            key = "|".join(treaty["factions"])
            row = self.institutional.setdefault(key, {"trust": 50, "fear": 50, "grievance": 50})
            row["trust"] = max(0, row["trust"] - 20)
            row["grievance"] = min(100, row["grievance"] + 25)
        return event.id

    def validate_state(self) -> None:
        if not isinstance(self.treaties, dict) or not isinstance(self.institutional, dict):
            raise ValueError("Invalid politics state")
        if not isinstance(self.trade_volume, dict):
            raise ValueError("Invalid faction trade volume")
        for key, volume in self.trade_volume.items():
            parts = key.split("|") if isinstance(key, str) else []
            if (len(parts) != 2 or not all(parts) or not parts[0] < parts[1]
                    or type(volume) is not int
                    or not 0 < volume <= self.data["rules"]["max_trade_volume"]):
                raise ValueError("Invalid faction trade record")
        for key, row in self.treaties.items():
            if (not isinstance(row, dict) or set(row) != {
                    "factions", "state", "terms", "event_id", "obligations", "breach_count"}
                    or not isinstance(row["factions"], list) or len(row["factions"]) != 2
                    or any(not isinstance(faction, str) or not faction
                           for faction in row["factions"])
                    or row["factions"][0] >= row["factions"][1]
                    or not isinstance(key, str) or key != "|".join(row["factions"])
                    or row["state"] not in self.data["treaty_states"]
                    or not isinstance(row["terms"], list)
                    or any(not isinstance(term, str) for term in row["terms"])
                    or set(row["terms"]) != set(self.data["terms"][row["state"]])
                    or not isinstance(row["event_id"], str) or not row["event_id"]
                    or not isinstance(row["obligations"], list)
                    or type(row["breach_count"]) is not int or row["breach_count"] < 0):
                raise ValueError("Invalid treaty record")
            for obligation in row["obligations"]:
                if (not isinstance(obligation, dict) or set(obligation) != {
                        "event_id", "kind", "obligated_faction", "requester", "status"}
                        or any(not isinstance(obligation[field], str) or not obligation[field]
                               for field in ("event_id", "kind", "obligated_faction",
                                             "requester", "status"))
                        or obligation["kind"] not in {
                            term for terms in self.data["terms"].values() for term in terms}
                        or obligation["obligated_faction"] not in row["factions"]
                        or obligation["requester"] not in row["factions"]
                        or obligation["status"] not in {"open", "fulfilled", "breached"}):
                    raise ValueError("Invalid treaty obligation")
        for key, row in self.institutional.items():
            parts = key.split("|") if isinstance(key, str) else []
            if (len(parts) != 2 or not parts[0] < parts[1]
                    or not isinstance(row, dict) or set(row) != {"trust", "fear", "grievance"}
                    or any(type(value) is not int or not 0 <= value <= 100
                           for value in row.values())):
                raise ValueError("Invalid institutional relation evidence")

    def validate_world(self, world) -> None:
        self.validate_state()
        if (self.last_updated_day != world.clock.tick // (
                world.config["time"]["hours_per_day"] // world.config["time"]["hours_per_tick"])):
            raise ValueError("Politics update day does not match world clock")
        if not isinstance(self.factions, dict) or not isinstance(self.archived_factions, dict):
            raise ValueError("Invalid faction registry")
        for faction_id, faction in self.factions.items():
            expected = {"id", "name", "kind", "leader_id", "council", "succession_rule",
                        "doctrine", "territory", "founded_event_id", "last_event_id",
                        "monthly_decisions", "member_crew_ids", "founder_id",
                        "anchorage_island_id", "purpose"}
            if (not isinstance(faction, dict) or set(faction) != expected
                    or faction["id"] != faction_id
                    or not all(isinstance(faction[field], str) and faction[field]
                               for field in ("name", "kind", "leader_id", "succession_rule",
                                             "founded_event_id", "last_event_id"))
                    or faction["succession_rule"] not in {"council_vote", "captain"}
                    or not isinstance(faction["council"], list)
                    or len(set(faction["council"])) != len(faction["council"])
                    or any(not isinstance(member, str) or member not in world.entities | world.archive
                           for member in faction["council"])
                    or not isinstance(faction["doctrine"], dict)
                    or set(faction["doctrine"]) != {"order", "freedom", "profit", "glory", "faith"}
                    or any(type(value) is not int or not 0 <= value <= 100
                           for value in faction["doctrine"].values())
                    or not isinstance(faction["territory"], list)
                    or len(set(faction["territory"])) != len(faction["territory"])
                    or any(island not in world.geography.islands
                           for island in faction["territory"])
                    or type(faction["monthly_decisions"]) is not int
                    or faction["monthly_decisions"] < 0
                    or not isinstance(faction["member_crew_ids"], list)
                    or len(set(faction["member_crew_ids"])) != len(faction["member_crew_ids"])
                    or any(crew_id not in world.crew_ships.crews
                           for crew_id in faction["member_crew_ids"])
                    or not isinstance(faction["founder_id"], str)
                    or faction["founder_id"] not in world.entities | world.archive
                    or (faction["anchorage_island_id"] is not None
                        and faction["anchorage_island_id"] not in world.geography.islands)
                    or not isinstance(faction["purpose"], str) or not faction["purpose"]):
                raise ValueError(f"Invalid faction state: {faction_id}")
            if faction["leader_id"] not in faction["council"]:
                if faction["leader_id"] not in world.archive:
                    raise ValueError("Faction leader is neither a council member nor archived")
            founded = world.event_log.get(faction["founded_event_id"])
            last = world.event_log.get(faction["last_event_id"])
            if founded.type != "FACTION_FOUNDED" or faction_id not in {
                    founded.data.get("faction_id"),
                    founded.targets[0]["entity_id"] if founded.targets else None}:
                raise ValueError("Faction founding event does not match faction record")
            if founded.data.get("member_crew_ids") is not None:
                historical_members = set(founded.data.get("member_crew_ids", ()))
                for transition in world.event_log:
                    if (transition.id <= founded.id
                            or transition.data.get("faction_id") != faction_id):
                        continue
                    if transition.type == "FACTION_MEMBER_DEPARTED":
                        historical_members.discard(transition.data.get("crew_id"))
                    elif transition.type == "FACTION_MEMBER_JOINED":
                        historical_members.add(transition.data.get("crew_id"))
                if (historical_members != set(faction["member_crew_ids"])
                        or founded.data.get("founder_id") != faction["founder_id"]
                        or founded.data.get("purpose") != faction["purpose"]
                        or founded.data.get("anchorage_island_id")
                        != faction["anchorage_island_id"]
                        or founded.data.get("name") != faction["name"]):
                    raise ValueError("Faction coalition state disagrees with its founding event")
                if faction["kind"] == "marine":
                    if faction["member_crew_ids"] or faction["anchorage_island_id"] is not None:
                        raise ValueError("Institutional Marine faction cannot claim crew coalition")
                else:
                    founding_captains = founded.data.get("member_captain_ids")
                    founding_actors = sorted(actor.get("entity_id") for actor in founded.actors
                                             if actor.get("role") in {
                                                 "faction_founder", "coalition_captain"})
                    historical_founder = (world.entities.get(faction["founder_id"])
                                          or world.archive.get(faction["founder_id"]))
                    if (len(faction["member_crew_ids"])
                            < self.data["rules"]["faction_min_member_crews"]
                            or not isinstance(founding_captains, (list, tuple))
                            or any(not isinstance(member_id, str) or not member_id
                                   for member_id in founding_captains)
                            or list(founding_captains) != sorted(set(founding_captains))
                            or list(founding_captains) != founding_actors
                            or any(member_id not in world.entities | world.archive
                                   for member_id in founding_captains)
                            or not isinstance(founded.data.get("founder_display_name"), str)
                            or not isinstance(founded.data.get("name"), str)
                            or founded.data["founder_display_name"].casefold()
                            not in founded.data.get("name", "").casefold()
                            or historical_founder is None
                            or founded.data.get("founder_display_name")
                            != historical_founder.get("display_name")
                            or founded.data.get("founder_id") != faction["founder_id"]
                            or founded.data.get("founder_ambition", -1)
                            < self.data["rules"]["faction_founder_min_ambition"]
                            or founded.data.get("founder_leadership", -1)
                            < self.data["rules"]["faction_founder_min_leadership"]
                            or not any(actor.get("entity_id") == faction["founder_id"]
                                       for actor in founded.actors)
                            or faction["anchorage_island_id"] is None
                            or not faction["purpose"].strip()):
                        raise ValueError("Faction founding lacks coalition prerequisites")
                    for crew_id in faction["member_crew_ids"]:
                        if world.crew_ships.crews[crew_id].get("faction_id") != faction_id:
                            raise ValueError("Faction member crew has inconsistent allegiance")
            if last.data.get("faction_id", faction_id) != faction_id:
                raise ValueError("Faction last event does not identify the faction")
            for island_id in faction["territory"]:
                if self.island_rulers.get(island_id) != faction_id:
                    raise ValueError("Faction territory and island ruler index disagree")
        for faction_id, faction in self.archived_factions.items():
            if (not isinstance(faction, dict)
                    or set(faction) != {"id", "name", "kind", "leader_id", "council",
                                        "succession_rule", "doctrine", "territory",
                                        "founded_event_id", "last_event_id", "monthly_decisions",
                                        "member_crew_ids", "founder_id", "anchorage_island_id",
                                        "purpose", "dissolved_event_id"}
                    or faction.get("id") != faction_id or faction_id in self.factions):
                raise ValueError("Invalid archived faction record")
            dissolution = world.event_log.get(faction["dissolved_event_id"])
            if (dissolution.type != "FACTION_DISSOLVED"
                    or dissolution.data.get("faction_id") != faction_id):
                raise ValueError("Archived faction does not match its dissolution event")
        for island_id, faction_id in self.island_rulers.items():
            if (island_id not in world.geography.islands or faction_id not in self.factions
                    or island_id not in self.factions[faction_id]["territory"]):
                raise ValueError("Invalid island ruler record")
        for island_id, level in self.fortifications.items():
            if (island_id not in self.island_rulers or type(level) is not int
                    or not 1 <= level <= 3):
                raise ValueError("Invalid island fortification")
        command = self.marine_command
        if (not isinstance(command, dict) or set(command) != {
                "faction_id", "commander_id", "deputy_ids", "appointment_event_id",
                "budget_funding_event_id"}
                or command["faction_id"] not in self.factions
                or self.factions[command["faction_id"]]["kind"] != "marine"
                or command["commander_id"] != self.factions[command["faction_id"]]["leader_id"]
                or not isinstance(command["deputy_ids"], list)
                or command["commander_id"] in command["deputy_ids"]
                or len(set(command["deputy_ids"])) != len(command["deputy_ids"])
                or any(not isinstance(member, str) or member not in world.entities | world.archive
                       for member in [command["commander_id"], *command["deputy_ids"]])
                or set(self.factions[command["faction_id"]]["council"])
                != {command["commander_id"], *command["deputy_ids"]}):
            raise ValueError("Invalid Marine command appointments")
        try:
            appointment = world.event_log.get(command["appointment_event_id"])
        except (KeyError, TypeError) as exc:
            raise ValueError("Marine command references an unknown appointment event") from exc
        if appointment.type not in {
                "FACTION_FOUNDED", "FACTION_SUCCESSION", "MARINE_COMMANDER_CHANGED",
                "MARINE_OFFICER_APPOINTED"}:
            raise ValueError("Marine appointment has an invalid source event")
        try:
            funding = world.event_log.get(command["budget_funding_event_id"])
        except (KeyError, TypeError) as exc:
            raise ValueError("Marine budget references an unknown funding event") from exc
        if funding.type != "GOVERNMENT_BUDGET_FUNDED" or funding.data.get("silver") != \
                self.data["rules"]["government_starting_budget_silver"]:
            raise ValueError("Marine response budget does not match its funding event")
        for registry, valid_ids, label in (
                (self.character_heat, set(world.entities) | set(world.archive), "character"),
                (self.crew_heat, set(world.crew_ships.crews) | set(self.archived_factions), "crew")):
            if (not isinstance(registry, dict)
                    or any(entity_id not in valid_ids or type(value) is not int
                           or not 0 <= value <= 1000
                           for entity_id, value in registry.items())):
                raise ValueError(f"Invalid {label} heat registry")
        if type(self.global_tension) is not int or not 0 <= self.global_tension <= 10000:
            raise ValueError("Invalid global government tension")
        if self.global_tension != min(10000, sum(self.crew_heat.values())):
            raise ValueError("Global tension disagrees with faction heat")
        for crew_id, bounty in self.crew_bounties.items():
            if (crew_id not in (set(world.crew_ships.crews) | set(self.archived_factions))
                    or not isinstance(bounty, dict)
                    or not {"amount", "event_id"} <= set(bounty)
                    or set(bounty) - {"amount", "event_id", "claimed_event_id"}
                    or type(bounty["amount"]) is not int or bounty["amount"] < 0
                    or not isinstance(bounty["event_id"], str)):
                raise ValueError("Invalid government bounty registry")
            bounty_event = world.event_log.get(bounty["event_id"])
            if bounty_event.type not in {"BOUNTY_SET", "BOUNTY_RAISED"}:
                raise ValueError("Bounty record does not reference an issue event")
            if "claimed_event_id" in bounty:
                paid_event = world.event_log.get(bounty["claimed_event_id"])
                if paid_event.type != "BOUNTY_PAID":
                    raise ValueError("Bounty claim does not reference a payout")
        for crew_id, response in self.response_assignments.items():
            if (crew_id not in world.crew_ships.crews or not isinstance(response, dict)
                    or set(response) != {"level", "island_id", "event_id", "last_day", "silver_spent"}
                    or response["level"] not in {"patrol", "hunter", "fleet"}
                    or response["island_id"] not in world.geography.islands
                    or type(response["last_day"]) is not int or response["last_day"] < 0
                    or type(response["silver_spent"]) is not int or response["silver_spent"] <= 0
                    or world.event_log.get(response["event_id"]).type
                    != "GOVERNMENT_RESPONSE_FUNDED"):
                raise ValueError("Invalid government response assignment")
        for crew_id, crew in world.crew_ships.crews.items():
            faction_id = crew.get("faction_id")
            if faction_id is None:
                continue
            faction = self.factions.get(faction_id)
            archived = self.archived_factions.get(faction_id)
            if faction is None and archived is None:
                raise ValueError("Crew references an unknown political faction")
            if faction is not None and crew_id not in faction["member_crew_ids"]:
                raise ValueError("Crew allegiance is absent from faction coalition membership")
        for key, row in self.institutional.items():
            first, second = key.split("|")
            if first == second:
                raise ValueError("Institutional relation must join distinct factions")
            for faction_id in (first, second):
                if (faction_id not in self.factions
                        and not any(event.type == "FACTION_FOUNDED"
                                    and event.data.get("faction_id") == faction_id
                                    for event in world.event_log)):
                    raise ValueError("Institutional relation references unknown faction")
        for key, treaty in self.treaties.items():
            try:
                transition = world.event_log.get(treaty["event_id"])
            except KeyError as exc:
                raise ValueError("Treaty references unknown transition event") from exc
            if (transition.type != "TREATY_TRANSITION"
                    or list(transition.data.get("factions", ())) != treaty["factions"]
                    or transition.data.get("to_state") != treaty["state"]):
                raise ValueError("Treaty state disagrees with its transition event")
            for obligation in treaty["obligations"]:
                event = world.event_log.get(obligation["event_id"])
                if event.type != "TREATY_AID_REQUESTED":
                    raise ValueError("Treaty obligation references wrong event type")
