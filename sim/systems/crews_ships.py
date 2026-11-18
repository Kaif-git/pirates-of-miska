"""Persistent crew rosters, ship components, provisions, and supply morale."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sim.core.names import living_name_in_use, validate_living_names


CREWS_SHIPS_PATH = Path(__file__).resolve().parents[1] / "data" / "crews_ships.json"
_SUPPLY_GOODS = {"grain", "water", "rum", "powder", "timber", "sailcloth"}


def load_crews_ships_data(path: str | Path = CREWS_SHIPS_PATH) -> tuple[dict[str, Any], str]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load crew/ship data {path}: {exc}") from exc
    validate_crews_ships_data(data)
    digest = hashlib.sha256(json.dumps(
        data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    return data, digest


def validate_crews_ships_data(data: Any) -> None:
    if (not isinstance(data, dict) or set(data) != {
            "schema_version", "rules", "classes", "strength_weights",
            "starting_roster_roles", "crew_name_suffixes"}
            or type(data["schema_version"]) is not int or data["schema_version"] != 1):
        raise ValueError("Invalid crew/ship catalog schema")
    rules = data["rules"]
    required_rules = {
        "starting_crew_size", "crew_roster_min", "crew_roster_max", "starting_morale",
        "starting_cohesion", "starting_discipline", "daily_food_milli_per_member",
        "daily_water_milli_per_member", "daily_rum_milli_per_member",
        "morale_loss_per_shortage_day", "morale_recovery_per_supplied_day",
        "desertion_morale_threshold", "desertion_basis_points_per_day",
        "wage_per_member_per_day_silver", "initial_supply_days", "starting_silver",
    }
    if not isinstance(rules, dict) or set(rules) != required_rules:
        raise ValueError("Crew/ship rules have invalid fields")
    if any(type(value) is not int or value < 0 for value in rules.values()):
        raise ValueError("Crew/ship numeric rules must be nonnegative integers")
    if (rules["crew_roster_min"] < 1
            or not rules["crew_roster_min"] <= rules["starting_crew_size"] <= rules["crew_roster_max"]
            or rules["starting_morale"] > 100 or rules["starting_cohesion"] > 100
            or rules["starting_discipline"] > 100
            or rules["desertion_morale_threshold"] > 100
            or rules["desertion_basis_points_per_day"] > 10000
            or rules["initial_supply_days"] < 1):
        raise ValueError("Crew/ship rules are outside supported bounds")
    classes = data["classes"]
    if not isinstance(classes, dict) or not classes:
        raise ValueError("Ship classes must be a nonempty object")
    for class_id, profile in classes.items():
        if (not isinstance(class_id, str) or not class_id or not isinstance(profile, dict)
                or set(profile) != {"hull_max", "rigging_max", "cannons", "ammunition",
                                    "speed_permille", "maneuverability", "cargo_capacity_milli",
                                    "starting_supplies"}
                or any(type(profile[key]) is not int or profile[key] <= 0
                       for key in ("hull_max", "rigging_max", "speed_permille", "cargo_capacity_milli"))
                or any(type(profile[key]) is not int or profile[key] < 0
                       for key in ("ammunition", "maneuverability"))
                or profile["maneuverability"] > 100
                or not isinstance(profile["cannons"], list)
                or any(not isinstance(cannon, dict)
                       or set(cannon) != {"caliber", "count", "condition"}
                       or any(type(cannon[key]) is not int or cannon[key] < 0
                              for key in ("caliber", "count", "condition"))
                       or cannon["condition"] > 100 for cannon in profile["cannons"])
                or not isinstance(profile["starting_supplies"], dict)
                or set(profile["starting_supplies"]) - _SUPPLY_GOODS
                or any(type(quantity) is not int or quantity < 0
                       for quantity in profile["starting_supplies"].values())):
            raise ValueError(f"Invalid ship class profile: {class_id}")
    if (not isinstance(data["starting_roster_roles"], list)
            or len(data["starting_roster_roles"]) != rules["starting_crew_size"] - 1
            or any(not isinstance(role, str) or not role for role in data["starting_roster_roles"])
            or not isinstance(data["crew_name_suffixes"], list) or not data["crew_name_suffixes"]
            or any(not isinstance(suffix, str) or not suffix for suffix in data["crew_name_suffixes"])):
        raise ValueError("Invalid starting crew roles or banner names")
    weights = data["strength_weights"]
    weight_keys = {
        "cannon_caliber_weight", "gunner_skill_weight", "boarding_member_weight",
        "durability_component_weight", "speed_weight", "maneuverability_weight",
        "seamanship_weight", "leadership_weight", "cohesion_weight",
        "morale_penalty_per_missing", "empty_essential_supply_penalty",
        "damage_penalty_per_lost_point", "noise_max_permille",
        "noise_staleness_permille_per_day", "noise_skill_dampening_per_point",
    }
    if (not isinstance(weights, dict) or set(weights) != weight_keys
            or any(type(value) is not int or value < 0 for value in weights.values())
            or weights["noise_max_permille"] > 1000):
        raise ValueError("Invalid ship strength weights")


class CrewShipSystem:
    """Manages ship component stores and finite crew rosters for the headless world."""

    def __init__(self, *, digest: str, crews: dict[str, dict[str, Any]] | None = None,
                 last_updated_day: int = 0) -> None:
        self.data, actual_digest = load_crews_ships_data()
        if digest != actual_digest:
            raise ValueError("Crew/ship catalog changed since snapshot creation")
        if type(last_updated_day) is not int or last_updated_day < 0:
            raise ValueError("Invalid crew/ship update day")
        self.digest = digest
        self.crews = {key: json.loads(json.dumps(row, sort_keys=True))
                      for key, row in (crews or {}).items()}
        self.last_updated_day = last_updated_day

    @classmethod
    def create(cls) -> "CrewShipSystem":
        _, digest = load_crews_ships_data()
        return cls(digest=digest)

    @classmethod
    def restore(cls, snapshot: dict[str, Any]) -> "CrewShipSystem":
        if (not isinstance(snapshot, dict)
                or set(snapshot) != {"digest", "crews", "last_updated_day"}
                or not isinstance(snapshot.get("crews"), dict)):
            raise ValueError("Invalid crew/ship snapshot")
        return cls(digest=snapshot["digest"], crews=snapshot["crews"],
                   last_updated_day=snapshot["last_updated_day"])

    def seed_world(self, world) -> None:
        if self.crews:
            raise ValueError("Starting crews can only be seeded once")
        character_rows = {key: row for key, row in world.entities.items()
                          if row.get("kind") == "character"}
        available_majors = sorted(
            (key for key, row in character_rows.items()
             if row["tier"] in {"MAJOR", "LEGEND"} and row["role"] == "captain"))
        available_members = sorted(
            key for key, row in character_rows.items() if row["tier"] == "MINOR")
        merchants = sorted(world.economy.merchants.items())
        if len(available_majors) < len(merchants):
            raise ValueError("Not enough generated captains to found starting merchant crews")
        suffixes = self.data["crew_name_suffixes"]
        roles = self.data["starting_roster_roles"]
        for index, (merchant_id, merchant) in enumerate(merchants):
            ship_id = merchant["ship_id"]
            ship = world.entities[ship_id]
            captain_id = available_majors[index]
            roster = [captain_id]
            for character_id in available_members:
                if len(roster) >= self.data["rules"]["starting_crew_size"]:
                    break
                roster.append(character_id)
            del available_members[:len(roster) - 1]
            if len(roster) < self.data["rules"]["crew_roster_min"]:
                raise ValueError("Starting crew roster cannot meet its minimum size")
            crew_id = world.ids.next("crew")
            name = f"{merchant['name'].split()[0]}'s {suffixes[index % len(suffixes)]}"
            role_map = {"captain": captain_id}
            for role, character_id in zip(roles, roster[1:]):
                role_map[role] = character_id
            home = ship["location"]["id"]
            cause = character_rows[captain_id]["last_event_id"]
            event = world.event_log.append(
                tick=world.clock.tick, event_type="CREW_FOUNDED", location=home,
                actors=[{"role": "captain", "entity_id": captain_id}],
                targets=[{"role": "ship", "entity_id": ship_id}],
                data={"crew_id": crew_id, "name": name, "roster": roster,
                      "ship_id": ship_id, "roles": role_map},
                caused_by=[cause, merchant["last_event_id"]],
                causes_tags=["crew_formation", "merchant_company"], magnitude=0.25,
            )
            world.event_log.append(
                tick=world.clock.tick, event_type="CAPTAIN_APPOINTED", location=home,
                actors=[{"role": "captain", "entity_id": captain_id}],
                targets=[{"role": "crew", "entity_id": crew_id}],
                data={"crew_id": crew_id, "ship_id": ship_id,
                      "appointment": "founding_captain"},
                caused_by=[event.id], causes_tags=["captain_appointment", "crew_formation"],
                magnitude=0.2)
            crew = {
                "id": crew_id, "name": name, "captain_id": captain_id,
                "ship_ids": [ship_id], "roster": roster, "roles": role_map,
                "morale": self.data["rules"]["starting_morale"],
                "cohesion": self.data["rules"]["starting_cohesion"],
                "discipline": self.data["rules"]["starting_discipline"],
                "pay_policy": {"share_rule": "equal",
                               "wage_per_day": self.data["rules"]["wage_per_member_per_day_silver"]},
                "arrears_silver": 0, "treasury_silver": self.data["rules"]["starting_silver"],
                "wages_paid_silver": 0,
                "ideology_tags": [], "founded_tick": world.clock.tick,
                "faction_id": None, "flag_symbol": "skull", "last_event_id": event.id,
                "distress_days": 0,
            }
            self.crews[crew_id] = crew
            funded_event, funded = world.economy.fund_crew_account(
                world, crew_id, merchant_id, roster,
                self.data["rules"]["starting_silver"], caused_by=event.id)
            crew["treasury_silver"] = funded
            crew["last_event_id"] = funded_event
            character_rows[captain_id]["crew_id"] = crew_id
            character_rows[captain_id]["ship_id"] = ship_id
            character_rows[captain_id]["last_event_id"] = event.id
            for character_id in roster[1:]:
                character_rows[character_id]["crew_id"] = crew_id
                character_rows[character_id]["ship_id"] = ship_id
                character_rows[character_id]["last_event_id"] = event.id
            ship_profile = self.data["classes"]["merchant_sloop"]
            ship.update({
                "class": "merchant_sloop", "built_tick": world.clock.tick,
                "owner_char": captain_id, "owner_faction": None,
                "hull": {"max": ship_profile["hull_max"], "current": ship_profile["hull_max"]},
                "rigging": {"max": ship_profile["rigging_max"],
                            "current": ship_profile["rigging_max"]},
                "cannons": [dict(row) for row in ship_profile["cannons"]],
                "ammunition": ship_profile["ammunition"],
                "ammunition_max": ship_profile["ammunition"],
                "maneuverability": ship_profile["maneuverability"],
                "cargo_capacity_milli": merchant["capacity_milli"],
                "cargo": {goods_id: 0 for goods_id in world.economy.goods},
                "upgrades": [], "condition_flags": [], "history": [event.id],
                "crew_id": crew_id,
            })
            desired = dict(ship_profile["starting_supplies"])
            island_stock = world.economy.islands[home]["stockpile_milli"]
            actual = {goods_id: min(quantity, island_stock[goods_id])
                      for goods_id, quantity in desired.items()}
            supply_event = world.economy.record_ship_supply_transfer(
                world, ship_id, home, actual, caused_by=event.id)
            ship["supplies"] = dict(world.economy.ship_supplies[ship_id])
            ship["history"].append(supply_event)
            crew["last_event_id"] = supply_event
        self.validate_world(world)

    def provision_recruited_merchant(self, world, merchant_id: str, *,
                                     caused_by: str) -> str:
        """Attach a generated, captain-led crew to a merchant added after bootstrap."""
        merchant = world.economy.merchants.get(merchant_id)
        if not merchant:
            raise ValueError("Unknown merchant for recruited crew")
        ship_id = merchant["ship_id"]
        ship = world.entities.get(ship_id)
        if (not ship or ship.get("kind") != "ship" or ship.get("crew_id")
                or ship_id in world.travel.transits):
            raise ValueError("Recruited merchant vessel must be an active, uncrewed ship")
        world.event_log.get(caused_by)
        location = ship["location"]["id"]
        captain_id = world.characters.create_character(
            world, tier="MINOR", role="captain", cause_event_id=caused_by,
            island_id=location)
        captain = world.entities[captain_id]
        crew_id = world.ids.next("crew")
        crew_name = f"{merchant['name']}'s Company"
        founded = world.event_log.append(
            tick=world.clock.tick, event_type="CREW_FOUNDED", location=location,
            actors=[{"role": "captain", "entity_id": captain_id}],
            targets=[{"role": "ship", "entity_id": ship_id}],
            data={"crew_id": crew_id, "name": crew_name,
                  "roster": [captain_id], "ship_id": ship_id,
                  "roles": {"captain": captain_id}, "recruitment": "actor_budget"},
            caused_by=[caused_by, captain["last_event_id"]],
            causes_tags=["crew_formation", "actor_budget_recruitment"], magnitude=0.2)
        self.crews[crew_id] = {
            "id": crew_id, "name": crew_name, "captain_id": captain_id,
            "ship_ids": [ship_id], "roster": [captain_id],
            "roles": {"captain": captain_id},
            "morale": self.data["rules"]["starting_morale"],
            "cohesion": self.data["rules"]["starting_cohesion"],
            "discipline": self.data["rules"]["starting_discipline"],
            "pay_policy": {"share_rule": "equal", "wage_per_day": 0},
            "arrears_silver": 0, "treasury_silver": 0, "wages_paid_silver": 0,
            "ideology_tags": [], "founded_tick": world.clock.tick,
            "faction_id": None, "flag_symbol": "skull", "last_event_id": founded.id,
            "distress_days": 0,
        }
        funding, funded = world.economy.fund_crew_account(
            world, crew_id, merchant_id, [captain_id], 0, caused_by=founded.id)
        crew = self.crews[crew_id]
        crew["treasury_silver"] = funded
        crew["last_event_id"] = funding
        captain["crew_id"] = crew_id
        captain["ship_id"] = ship_id
        captain["last_event_id"] = founded.id
        profile = self.data["classes"]["merchant_sloop"]
        ship.update({
            "class": "merchant_sloop", "built_tick": world.clock.tick,
            "owner_char": captain_id, "owner_faction": None, "owner_type": "merchant",
            "hull": {"max": profile["hull_max"], "current": profile["hull_max"]},
            "rigging": {"max": profile["rigging_max"], "current": profile["rigging_max"]},
            "cannons": [dict(row) for row in profile["cannons"]],
            "ammunition": profile["ammunition"], "ammunition_max": profile["ammunition"],
            "maneuverability": profile["maneuverability"],
            "cargo_capacity_milli": merchant["capacity_milli"],
            "cargo": dict(merchant["cargo_milli"]), "upgrades": [],
            "condition_flags": [], "history": [founded.id], "crew_id": crew_id,
        })
        desired = dict(profile["starting_supplies"])
        stock = world.economy.islands[location]["stockpile_milli"]
        actual = {goods_id: min(quantity, stock[goods_id])
                  for goods_id, quantity in desired.items()}
        supply_event = world.economy.record_ship_supply_transfer(
            world, ship_id, location, actual, caused_by=founded.id)
        ship["supplies"] = dict(world.economy.ship_supplies[ship_id])
        ship["history"].append(supply_event)
        crew["last_event_id"] = supply_event
        world.event_log.append(
            tick=world.clock.tick, event_type="CAPTAIN_APPOINTED", location=location,
            actors=[{"role": "captain", "entity_id": captain_id}],
            targets=[{"role": "crew", "entity_id": crew_id}],
            data={"crew_id": crew_id, "ship_id": ship_id,
                  "appointment": "recruited_merchant_captain"},
            caused_by=[founded.id], causes_tags=["captain_appointment", "crew_formation"],
            magnitude=0.2)
        if world.politics.factions:
            faction_event = world.politics.register_recruited_crew(
                world, crew_id, owner_type="merchant", caused_by=founded.id)
            crew["last_event_id"] = faction_event
        return crew_id

    def provision_recruited_vessel(self, world, ship_id: str, owner_type: str, *,
                                   caused_by: str) -> str:
        """Create a finite one-captain crew for non-merchant budget vessels."""
        ship = world.entities.get(ship_id)
        if (owner_type not in {"pirate", "marine", "privateer"} or not ship
                or ship.get("kind") != "ship" or ship.get("crew_id")):
            raise ValueError("Actor-budget vessel must be a new uncrewed non-merchant ship")
        world.event_log.get(caused_by)
        location = ship["location"]["id"]
        captain_id = world.characters.create_character(
            world, tier="MINOR", role="captain", cause_event_id=caused_by,
            island_id=location)
        captain = world.entities[captain_id]
        crew_id = world.ids.next("crew")
        crew_name = f"{ship['name']}'s Company"
        founded = world.event_log.append(
            tick=world.clock.tick, event_type="CREW_FOUNDED", location=location,
            actors=[{"role": "captain", "entity_id": captain_id}],
            targets=[{"role": "ship", "entity_id": ship_id}],
            data={"crew_id": crew_id, "name": crew_name,
                  "roster": [captain_id], "ship_id": ship_id,
                  "roles": {"captain": captain_id}, "owner_type": owner_type},
            caused_by=[caused_by, captain["last_event_id"]],
            causes_tags=["crew_formation", "actor_budget_recruitment", owner_type],
            magnitude=0.25)
        self.crews[crew_id] = {
            "id": crew_id, "name": crew_name, "captain_id": captain_id,
            "ship_ids": [ship_id], "roster": [captain_id],
            "roles": {"captain": captain_id},
            "morale": self.data["rules"]["starting_morale"],
            "cohesion": self.data["rules"]["starting_cohesion"],
            "discipline": self.data["rules"]["starting_discipline"],
            "pay_policy": {"share_rule": "equal", "wage_per_day": 0},
            "arrears_silver": 0, "treasury_silver": 0, "wages_paid_silver": 0,
            "ideology_tags": [owner_type], "founded_tick": world.clock.tick,
            "faction_id": None, "flag_symbol": "skull", "last_event_id": founded.id,
            "distress_days": 0,
        }
        world.economy.crew_accounts[crew_id] = {
            "treasury_silver": 0, "member_wallets": {captain_id: 0}}
        funded = world.event_log.append(
            tick=world.clock.tick, event_type="CREW_FUNDED", location=location,
            targets=[{"role": "crew", "entity_id": crew_id}],
            data={"crew_id": crew_id, "merchant_id": None,
                  "requested_silver": 0, "funded_silver": 0},
            caused_by=[founded.id], causes_tags=["crew_treasury", "finite_transfer"],
            magnitude=0.0)
        crew = self.crews[crew_id]
        crew["last_event_id"] = funded.id
        captain.update({"crew_id": crew_id, "ship_id": ship_id,
                        "last_event_id": founded.id})
        profile = self.data["classes"]["merchant_sloop"]
        ship.update({
            "class": "merchant_sloop", "built_tick": world.clock.tick,
            "owner_char": captain_id, "owner_faction": None, "owner_type": owner_type,
            "hull": {"max": profile["hull_max"], "current": profile["hull_max"]},
            "rigging": {"max": profile["rigging_max"], "current": profile["rigging_max"]},
            "cannons": [dict(row) for row in profile["cannons"]],
            "ammunition": profile["ammunition"], "ammunition_max": profile["ammunition"],
            "maneuverability": profile["maneuverability"], "cargo_capacity_milli": 50_000,
            "cargo": {goods_id: 0 for goods_id in world.economy.goods},
            "upgrades": [], "condition_flags": [], "history": [founded.id],
            "crew_id": crew_id,
        })
        desired = dict(profile["starting_supplies"])
        stock = world.economy.islands[location]["stockpile_milli"]
        actual = {goods_id: min(quantity, stock[goods_id])
                  for goods_id, quantity in desired.items()}
        supply_event = world.economy.record_ship_supply_transfer(
            world, ship_id, location, actual, caused_by=founded.id)
        ship["supplies"] = dict(world.economy.ship_supplies[ship_id])
        ship["history"].append(supply_event)
        crew["last_event_id"] = supply_event
        appointed = world.event_log.append(
            tick=world.clock.tick, event_type="CAPTAIN_APPOINTED", location=location,
            actors=[{"role": "captain", "entity_id": captain_id}],
            targets=[{"role": "crew", "entity_id": crew_id}],
            data={"crew_id": crew_id, "ship_id": ship_id,
                  "appointment": f"recruited_{owner_type}_captain"},
            caused_by=[founded.id], causes_tags=["captain_appointment", owner_type],
            magnitude=0.2)
        acquired = world.event_log.append(
            tick=world.clock.tick, event_type="SHIP_ACQUIRED", location=location,
            actors=[{"role": "captain", "entity_id": captain_id}],
            targets=[{"role": "acquired_ship", "entity_id": ship_id}],
            data={"method": "build", "owner_type": owner_type,
                  "crew_id": crew_id, "captain_appointed_event_id": appointed.id},
            caused_by=[founded.id, appointed.id],
            causes_tags=["ship_acquisition", "actor_budget_recruitment", owner_type],
            magnitude=0.3)
        crew["last_event_id"] = acquired.id
        if world.politics.factions:
            faction_event = world.politics.register_recruited_crew(
                world, crew_id, owner_type=owner_type, caused_by=acquired.id)
            crew["last_event_id"] = faction_event
        return crew_id

    def recruit(self, world, crew_id: str, character_id: str, *, caused_by: str) -> str:
        crew = self._crew(crew_id)
        character = world.entities.get(character_id)
        if not character or character.get("kind") != "character" or character.get("death_tick") is not None:
            raise ValueError("Only a living active character may be recruited")
        if character.get("crew_id") is not None:
            raise ValueError("Character already belongs to a crew")
        if len(crew["roster"]) >= self.data["rules"]["crew_roster_max"]:
            raise ValueError("Crew roster is full")
        world.event_log.get(caused_by)
        ship_id = crew["ship_ids"][0]
        event = world.event_log.append(
            tick=world.clock.tick, event_type="RECRUITED", location=world.location_of(ship_id)["id"],
            actors=[{"role": "recruiter", "entity_id": crew["captain_id"]}],
            targets=[{"role": "recruit", "entity_id": character_id}],
            data={"crew_id": crew_id, "character_id": character_id,
                  "roster_size_before": len(crew["roster"])},
            caused_by=[caused_by], causes_tags=["crew_recruitment"], magnitude=0.15,
        )
        crew["roster"].append(character_id)
        crew["last_event_id"] = event.id
        character["crew_id"] = crew_id
        character["ship_id"] = ship_id
        character["last_event_id"] = event.id
        world.economy.crew_accounts[crew_id]["member_wallets"][character_id] = character["wealth"]["silver"]
        return event.id

    def on_character_death(self, world, character_id: str, death_event_id: str) -> None:
        character = world.archive.get(character_id)
        if not character or character.get("crew_id") is None:
            return
        crew = self.crews[character["crew_id"]]
        if character_id in crew["roster"]:
            crew["roster"].remove(character_id)
        for role, assigned in list(crew["roles"].items()):
            if assigned == character_id:
                del crew["roles"][role]
        crew["last_event_id"] = death_event_id
        if not crew["roster"]:
            replacement_id = world.characters.create_character(
                world, tier="MINOR", cause_event_id=death_event_id, role="sailor")
            generated_id = world.entities[replacement_id]["last_event_id"]
            arrival = world.event_log.append(
                tick=world.clock.tick, event_type="CHARACTER_ARRIVED",
                location=world.location_of(crew["ship_ids"][0])["id"],
                actors=[{"role": "new_crew_member", "entity_id": replacement_id}],
                data={"crew_id": crew["id"], "role": "sailor",
                      "replacement_for": character_id},
                caused_by=[death_event_id, generated_id],
                causes_tags=["crew_replacement", "character_arrival"], magnitude=0.25)
            crew["roster"].append(replacement_id)
            replacement = world.entities[replacement_id]
            replacement["last_event_id"] = arrival.id
            replacement["crew_id"] = crew["id"]
            replacement["ship_id"] = crew["ship_ids"][0]
            world.economy.crew_accounts[crew["id"]]["member_wallets"][replacement_id] = 0
        if crew["captain_id"] == character_id:
            successor = min(crew["roster"])
            ship_id = crew["ship_ids"][0]
            succession = world.event_log.append(
                tick=world.clock.tick, event_type="CAPTAIN_CHANGED",
                location=world.location_of(ship_id)["id"],
                actors=[{"role": "new_captain", "entity_id": successor}],
                targets=[{"role": "crew", "entity_id": crew["id"]}],
                data={"crew_id": crew["id"], "former_captain_id": character_id,
                      "new_captain_id": successor, "reason": "captain_death"},
                caused_by=[death_event_id], causes_tags=["crew_succession", "captain_death"],
                magnitude=0.45,
            )
            world.event_log.append(
                tick=world.clock.tick, event_type="CAPTAIN_APPOINTED",
                location=world.location_of(ship_id)["id"],
                actors=[{"role": "captain", "entity_id": successor}],
                targets=[{"role": "crew", "entity_id": crew["id"]}],
                data={"crew_id": crew["id"], "ship_id": ship_id,
                      "former_captain_id": character_id,
                      "appointment": "succession"},
                caused_by=[succession.id],
                causes_tags=["captain_appointment", "crew_succession"], magnitude=0.2)
            crew["captain_id"] = successor
            crew["roles"]["captain"] = successor
            crew["last_event_id"] = succession.id
            ship = world.entities[ship_id]
            ship["owner_char"] = successor
            ship["history"].append(succession.id)
            world.entities[successor]["last_event_id"] = succession.id
        character["crew_id"] = None
        character["ship_id"] = None

    def damage_ship(self, world, ship_id: str, component: str, amount: int, *,
                    caused_by: str) -> str:
        ship = world.entities.get(ship_id)
        if not ship or ship.get("kind") != "ship":
            raise ValueError(f"Unknown active ship: {ship_id}")
        if type(amount) is not int or amount <= 0:
            raise ValueError("Ship damage must be a positive integer")
        world.event_log.get(caused_by)
        if component in {"hull", "rigging"}:
            state = ship[component]
            before = state["current"]
            after = max(0, before - amount)
            state["current"] = after
        elif component == "ammunition":
            before = ship["ammunition"]
            after = max(0, before - amount)
            ship["ammunition"] = after
        elif component.startswith("cannon:"):
            try:
                index = int(component.partition(":")[2])
                cannon = ship["cannons"][index]
            except (ValueError, IndexError) as exc:
                raise ValueError("Unknown ship cannon component") from exc
            before = cannon["condition"]
            after = max(0, before - amount)
            cannon["condition"] = after
        else:
            raise ValueError(f"Unknown ship component: {component}")
        if component in {"hull", "rigging"}:
            hull_ratio = ship["hull"]["current"] * 100 // ship["hull"]["max"]
            rigging_ratio = ship["rigging"]["current"] * 100 // ship["rigging"]["max"]
            ship["condition"] = (hull_ratio + rigging_ratio) // 2
        location = world.location_of(ship_id)
        event = world.event_log.append(
            tick=world.clock.tick, event_type="SHIP_DAMAGED", location=location["id"],
            targets=[{"role": "damaged_ship", "entity_id": ship_id}],
            data={"component": component, "before": before, "after": after,
                  "damage": before - after},
            caused_by=[caused_by], causes_tags=["ship_damage", f"component:{component}"],
            magnitude=min(1.0, (before - after) / 100),
        )
        ship["history"].append(event.id)
        return event.id

    def rename_ship(self, world, ship_id: str, name: str, *, caused_by: str) -> str:
        ship = world.entities.get(ship_id)
        if not ship or ship.get("kind") != "ship":
            raise ValueError(f"Unknown active ship: {ship_id}")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Ship display name must be nonempty")
        name = " ".join(name.split())
        if living_name_in_use(world, name, exclude_id=ship_id):
            raise ValueError(f"Living character or ship name is already in use: {name}")
        world.event_log.get(caused_by)
        old_name = ship["name"]
        location = world.location_of(ship_id)
        event = world.event_log.append(
            tick=world.clock.tick, event_type="SHIP_RENAMED", location=location["id"],
            targets=[{"role": "renamed_ship", "entity_id": ship_id}],
            data={"old_name": old_name, "new_name": name}, caused_by=[caused_by],
            causes_tags=["ship_renaming", "name_registry"], magnitude=0.05,
        )
        ship["name"] = name
        ship["history"].append(event.id)
        ship["last_event_id"] = event.id
        return event.id

    def combat_power(self, world, ship_id: str) -> dict[str, int]:
        ship = world.entities.get(ship_id)
        if not ship or ship.get("kind") != "ship":
            raise ValueError(f"Unknown active ship: {ship_id}")
        crew_id = ship.get("crew_id")
        crew = self.crews.get(crew_id)
        if not crew:
            raise ValueError(f"Ship has no computed-strength crew: {ship_id}")
        weights = self.data["strength_weights"]
        roster = [world.entities[key] for key in crew["roster"] if key in world.entities]
        if not roster:
            raise ValueError("A ship needs at least one living crew member for strength")
        captain = world.entities[crew["captain_id"]]
        gunner_id = crew["roles"].get("gunner")
        gunner = world.entities.get(gunner_id) if gunner_id else None
        firepower = sum(
            cannon["caliber"] * cannon["count"] * cannon["condition"] // 100
            for cannon in ship["cannons"]
        ) * weights["cannon_caliber_weight"]
        if gunner is not None and ship["ammunition"] > 0:
            firepower += world.characters.effective_stat(gunner, "gunnery") * weights["gunner_skill_weight"]
        elif ship["ammunition"] <= 0:
            firepower = 0
        combat_average = sum(world.characters.effective_stat(row, "combat") for row in roster) // len(roster)
        seamanship_average = sum(world.characters.effective_stat(row, "seamanship")
                                 for row in roster) // len(roster)
        boarding = (combat_average * len(roster) * weights["boarding_member_weight"]
                    + world.characters.effective_stat(captain, "combat"))
        durability = (ship["hull"]["current"] + ship["rigging"]["current"])
        durability *= weights["durability_component_weight"]
        mobility = (
            ship["speed_permille"] // 10 * weights["speed_weight"]
            + ship["maneuverability"] * weights["maneuverability_weight"]
            + seamanship_average * weights["seamanship_weight"]
        )
        command = (
            world.characters.effective_stat(captain, "leadership") * weights["leadership_weight"]
            + crew["cohesion"] * weights["cohesion_weight"]
        )
        morale_penalty = ((100 - crew["morale"])
                          * weights["morale_penalty_per_missing"])
        supplies = world.economy.ship_supplies.get(ship_id, {})
        supply_penalty = sum(
            weights["empty_essential_supply_penalty"]
            for goods_id in ("grain", "water") if supplies.get(goods_id, 0) <= 0
        )
        damage_penalty = (
            (ship["hull"]["max"] - ship["hull"]["current"])
            + (ship["rigging"]["max"] - ship["rigging"]["current"])
        ) * weights["damage_penalty_per_lost_point"]
        positive = firepower + boarding + durability + mobility + command
        total = max(0, positive - morale_penalty - supply_penalty - damage_penalty)
        return {
            "firepower": firepower, "boarding": boarding, "durability": durability,
            "mobility": mobility, "command": command,
            "morale_penalty": morale_penalty, "supply_penalty": supply_penalty,
            "damage_penalty": damage_penalty, "total": total,
        }

    def estimated_strength(self, world, observer_id: str, ship_id: str,
                           staleness_days: int) -> dict[str, int]:
        observer = world.entities.get(observer_id)
        if not observer or observer.get("kind") != "character":
            raise ValueError(f"Unknown observer character: {observer_id}")
        if type(staleness_days) is not int or staleness_days < 0:
            raise ValueError("Strength observation staleness must be nonnegative")
        true = self.combat_power(world, ship_id)["total"]
        weights = self.data["strength_weights"]
        skill = (world.characters.effective_stat(observer, "cunning")
                 + world.characters.effective_stat(observer, "navigation")) // 2
        uncertainty = min(
            weights["noise_max_permille"],
            staleness_days * weights["noise_staleness_permille_per_day"]
            + max(0, 100 - skill) * weights["noise_skill_dampening_per_point"],
        )
        rng = world.rng.stream(f"strength-estimate:{observer_id}:{ship_id}")
        error = rng.randint(-uncertainty, uncertainty)
        estimate = max(0, true * (1000 + error) // 1000)
        return {"estimate": estimate, "uncertainty_permille": uncertainty,
                "error_permille": error}

    def update_day(self, day: int, world) -> None:
        if type(day) is not int or day != self.last_updated_day + 1:
            raise ValueError("Crew/ship days must be updated consecutively")
        rules = self.data["rules"]
        for crew_id in sorted(self.crews):
            crew = self.crews[crew_id]
            ship_id = crew["ship_ids"][0]
            if ship_id not in world.entities:
                continue
            self._pay_daily_wages(world, crew_id, day)
            crew_size = len(crew["roster"])
            demand = {
                "grain": rules["daily_food_milli_per_member"] * crew_size,
                "water": rules["daily_water_milli_per_member"] * crew_size,
                "rum": rules["daily_rum_milli_per_member"] * crew_size,
            }
            event_id = world.economy.consume_ship_supplies(
                world, ship_id, crew_id, demand, caused_by=crew["last_event_id"], day=day)
            ship = world.entities[ship_id]
            ship["supplies"] = dict(world.economy.ship_supplies[ship_id])
            ship["history"].append(event_id)
            crew["last_event_id"] = event_id
            shortage = any(world.event_log.get(event_id).data["shortage_milli"].values())
            if shortage:
                crew["distress_days"] += 1
                previous_morale = crew["morale"]
                crew["morale"] = max(0, crew["morale"] - rules["morale_loss_per_shortage_day"])
                if previous_morale > rules["desertion_morale_threshold"] >= crew["morale"]:
                    distress = world.event_log.append(
                        tick=world.clock.tick, event_type="CREW_SUPPLIES_EXHAUSTED",
                        location=world.location_of(ship_id)["id"],
                        targets=[{"role": "crew", "entity_id": crew_id}],
                        data={"ship_id": ship_id, "crew_id": crew_id,
                              "morale_before": previous_morale, "morale_after": crew["morale"],
                              "distress_days": crew["distress_days"]},
                        caused_by=[event_id], causes_tags=["supply_exhaustion", "crew_distress"],
                        magnitude=0.6,
                    )
                    crew["last_event_id"] = distress.id
            else:
                crew["distress_days"] = 0
                crew["morale"] = min(100, crew["morale"] + rules["morale_recovery_per_supplied_day"])
            self._try_desertion(world, crew_id, day)
        self.last_updated_day = day

    def update_actor_budgets(self, day: int, world) -> None:
        """Recruit after settlement monthly and before annual budget samples."""
        days_per_year = world.config["time"]["days_per_year"]
        if day % 30 == 0 or day % days_per_year == 0:
            self._recruit_actor_bands(world, day)
            self._dispatch_raiders(world, day)

    def _recruit_actor_bands(self, world, day: int) -> None:
        """Monthly deterministic top-up through vessel, character, and crew systems."""
        actors = world.config.get("world", {}).get("actors", {})
        ship_rules = actors.get("ship_bands", {})
        if not ship_rules:
            return
        island_count = len(world.geography.islands)
        minimums = {
            band: (rules["minimum_per_island_milli"] * island_count + 999) // 1000
            for band, rules in ship_rules.items()
        }
        max_monthly_recruits = (
            10 if day % world.config["time"]["days_per_year"] == 0 else 5)
        recruited = 0
        for band in ("merchant", "pirate", "privateer", "marine"):
            count = self._active_actor_ship_count(world, band)
            while count < minimums[band] and recruited < max_monthly_recruits:
                island_id = self._least_populated_ship_island(world, band)
                cause = world.event_log.by_type("WORLD_GENERATED")[0].id
                if band == "merchant":
                    merchant_name = f"Budget Merchant {len(world.economy.merchants) + 1:03d}"
                    merchant_id, merchant_event_id = world.economy.create_merchant(
                        world, merchant_name, island_id, silver=0, capacity_milli=100_000)
                    crew_id = self.provision_recruited_merchant(
                        world, merchant_id, caused_by=merchant_event_id)
                    ship_id = world.economy.merchants[merchant_id]["ship_id"]
                    captain_id = self.crews[crew_id]["captain_id"]
                    acquired = world.event_log.append(
                        tick=world.clock.tick, event_type="SHIP_ACQUIRED", location=island_id,
                        actors=[{"role": "captain", "entity_id": captain_id}],
                        targets=[{"role": "acquired_ship", "entity_id": ship_id}],
                        data={"method": "build", "owner_type": band,
                              "merchant_id": merchant_id, "crew_id": crew_id},
                        caused_by=[merchant_event_id, self.crews[crew_id]["last_event_id"]],
                        causes_tags=["ship_acquisition", "actor_budget_recruitment", band],
                        magnitude=0.3)
                    self.crews[crew_id]["last_event_id"] = acquired.id
                else:
                    historical_ships = sum(
                        entity.get("kind") == "ship"
                        for entity in (*world.entities.values(), *world.archive.values()))
                    name = f"{band.title()} Vessel {historical_ships + 1:06d}"
                    ship_id, ship_event_id = world.create_vessel(name, island_id)
                    crew_id = self.provision_recruited_vessel(
                        world, ship_id, band, caused_by=ship_event_id)
                count += 1
                recruited += 1
                if recruited >= max_monthly_recruits:
                    break
        for _ in range(2):
            self._promote_captain_rookie(world, day)
        self._promote_major_legend(world, day)

    @staticmethod
    def _active_actor_ship_count(world, band: str) -> int:
        total = 0
        for ship in world.entities.values():
            if (ship.get("kind") != "ship" or ship.get("sunk")
                    or ship.get("state") == "sunk"
                    or ship.get("captured_by_crew_id") is not None):
                continue
            owner_type = ship.get("owner_type")
            if band == "merchant" and any(
                    merchant.get("ship_id") == ship.get("id")
                    for merchant in world.economy.merchants.values()):
                owner_type = "merchant"
            crew = world.crew_ships.crews.get(ship.get("crew_id"), {})
            faction = world.politics.factions.get(crew.get("faction_id"), {})
            faction_kind = str(faction.get("kind", "")).lower()
            if faction_kind in {"merchant", "merchant_league"}:
                owner_type = "merchant"
            elif faction_kind in {"pirate", "raider"}:
                owner_type = "pirate"
            elif faction_kind == "marine":
                owner_type = "marine"
            elif faction_kind == "privateer":
                owner_type = "privateer"
            total += owner_type == band
        return total

    @staticmethod
    def _least_populated_ship_island(world, band: str) -> str:
        counts = {island_id: 0 for island_id in world.geography.islands}
        for ship_id, ship in world.entities.items():
            if ship.get("kind") != "ship":
                continue
            location = world.location_of(ship_id)
            if location["kind"] == "island":
                counts[location["id"]] += 1
        patrol = {
            island_id: (sum(route.patrol_density
                            for route in world.geography.adjacency[island_id])
                        / max(1, len(world.geography.adjacency[island_id])))
            for island_id in world.geography.islands
        }
        if band == "pirate":
            return min(counts, key=lambda island_id: (patrol[island_id], counts[island_id], island_id))
        if band == "marine":
            return min(counts, key=lambda island_id: (-patrol[island_id], counts[island_id], island_id))
        if band == "privateer":
            midpoint = sum(patrol.values()) / len(patrol)
            return min(counts, key=lambda island_id: (
                abs(patrol[island_id] - midpoint), counts[island_id], island_id))
        return min(counts, key=lambda island_id: (counts[island_id], island_id))

    def _promote_captain_rookie(self, world, day: int) -> None:
        rules = world.config.get("world", {}).get("actors", {}).get("captain_bands", {})
        major_target = rules.get("major", {}).get("target", 0)
        major_maximum = rules.get("major", {}).get("maximum", 0)
        majors = sum(1 for character in world.entities.values()
                     if character.get("kind") == "character"
                     and character.get("tier") == "MAJOR"
                     and character.get("role") == "captain"
                     and character.get("death_tick") is None)
        if majors >= major_target or majors >= major_maximum:
            return
        appointed = {
            event.actors[0]["entity_id"]: event.id
            for event in world.event_log.by_type("CAPTAIN_APPOINTED")
            if event.actors and event.actors[0].get("role") == "captain"
        }
        candidate = next((character for character in sorted(
            world.entities.values(), key=lambda row: row["id"])
            if character.get("kind") == "character" and character.get("tier") == "MINOR"
            and character.get("role") == "captain" and character.get("death_tick") is None
            and character["id"] in appointed
            and character.get("crew_id") in self.crews
            and self.crews[character["crew_id"]]["captain_id"] == character["id"]), None)
        if candidate is None:
            return
        deed_event_id = appointed[candidate["id"]]
        acquired_event = next((event.id for event in reversed(list(world.event_log))
                               if event.type == "SHIP_ACQUIRED"
                               and event.actors and event.actors[0]["entity_id"] == candidate["id"]),
                              deed_event_id)
        promotion = world.event_log.append(
            tick=world.clock.tick, event_type="ROOKIE_PROMOTED",
            location=world.location_of(candidate["id"])["id"],
            actors=[{"role": "rising_captain", "entity_id": candidate["id"]}],
            targets=[{"role": "crew", "entity_id": candidate["crew_id"]}],
            data={"from_tier": "MINOR", "to_tier": "MAJOR",
                  "deed_event_ids": [deed_event_id, acquired_event], "day": day},
            caused_by=[deed_event_id, acquired_event],
            causes_tags=["captain_rising", "logged_deeds", "actor_budget"], magnitude=0.5)
        candidate["tier"] = "MAJOR"
        candidate["last_event_id"] = promotion.id
        self.crews[candidate["crew_id"]]["last_event_id"] = promotion.id

    def _promote_major_legend(self, world, day: int) -> None:
        bands = world.config.get("world", {}).get("actors", {}).get("captain_bands", {})
        target = bands.get("legend", {}).get("target", 0)
        legends = sum(character.get("kind") == "character"
                      and character.get("tier") == "LEGEND"
                      and character.get("role") == "captain"
                      and character.get("death_tick") is None
                      for character in world.entities.values())
        if legends >= target:
            return
        acquired_by_captain = {
            event.actors[0]["entity_id"]: event.id
            for event in world.event_log.by_type("SHIP_ACQUIRED")
            if event.actors and event.actors[0].get("role") == "captain"
        }
        candidate = next((character for character in sorted(
            world.entities.values(), key=lambda row: row["id"])
            if character.get("kind") == "character" and character.get("tier") == "MAJOR"
            and character.get("role") == "captain" and character.get("death_tick") is None
            and character.get("crew_id") in self.crews
            and self.crews[character["crew_id"]]["captain_id"] == character["id"]
            and character["id"] in acquired_by_captain), None)
        if candidate is None:
            return
        deed = acquired_by_captain[candidate["id"]]
        event = world.event_log.append(
            tick=world.clock.tick, event_type="LEGEND_PROMOTED",
            location=world.location_of(candidate["id"])["id"],
            actors=[{"role": "legendary_captain", "entity_id": candidate["id"]}],
            targets=[{"role": "crew", "entity_id": candidate["crew_id"]}],
            data={"from_tier": "MAJOR", "to_tier": "LEGEND",
                  "deed_event_ids": [deed], "day": day},
            caused_by=[deed], causes_tags=["captain_rising", "logged_deed", "legend_band"],
            magnitude=0.6)
        candidate["tier"] = "LEGEND"
        candidate["last_event_id"] = event.id
        self.crews[candidate["crew_id"]]["last_event_id"] = event.id

    def _dispatch_raiders(self, world, day: int) -> None:
        """Send idle raiders toward observed high-value, weakly patrolled traffic."""
        candidates = []
        for merchant_id, merchant in sorted(world.economy.merchants.items()):
            ship_id = merchant["ship_id"]
            ship = world.entities.get(ship_id)
            if not ship:
                continue
            location = world.location_of(ship_id)
            cargo_value = sum(
                quantity * world.economy.goods[goods_id]["base_price_silver"]
                for goods_id, quantity in merchant["cargo_milli"].items())
            route_options = []
            if location["kind"] == "route":
                route = world.geography.routes[location["id"]]
                route_options.append((route, location["to"]))
            elif location["kind"] == "island":
                route_options.extend((route, location["id"])
                                     for route in world.geography.adjacency[location["id"]])
            for route, destination in route_options:
                score = cargo_value * 1000 // (1000 + int(route.patrol_density * 1000))
                candidates.append((score, route.patrol_density, merchant_id, ship_id,
                                   route, destination))
        if not candidates:
            return
        targets = sorted(candidates, key=lambda row: (-row[0], row[1], row[2], row[4].id))[:12]
        pirates = sorted(
            ship_id for ship_id, ship in world.entities.items()
            if ship.get("kind") == "ship" and ship.get("owner_type") == "pirate"
            and ship.get("captured_by_crew_id") is None
            and ship_id not in world.travel.transits and ship.get("crew_id") in self.crews)
        for pirate_id in pirates:
            pirate = world.entities[pirate_id]
            location = world.location_of(pirate_id)
            if location["kind"] != "island":
                continue
            crew = self.crews[pirate["crew_id"]]
            captain_id = crew["captain_id"]
            believed_targets = []
            for _, _, merchant_id, merchant_ship_id, route, destination in targets:
                merchant = world.economy.merchants[merchant_id]
                cause_id = merchant["last_event_id"]
                cargo_value = sum(
                    quantity * world.economy.goods[goods_id]["base_price_silver"]
                    for goods_id, quantity in merchant["cargo_milli"].items())
                patrol_value = int(route.patrol_density * 1000)
                if world.decisions.belief(
                        captain_id, route.id, "estimated_patrol_density_milli") is None:
                    world.decisions.set_belief(
                        world, captain_id, route.id, "estimated_patrol_density_milli",
                        patrol_value, source_event_id=cause_id, confidence_permille=850,
                        source_kind="rumor")
                if world.decisions.belief(
                        captain_id, merchant_ship_id, "estimated_cargo_value_milli") is None:
                    world.decisions.set_belief(
                        world, captain_id, merchant_ship_id, "estimated_cargo_value_milli",
                        cargo_value, source_event_id=cause_id, confidence_permille=700,
                        source_kind="rumor")
                patrol_belief = world.decisions.belief(
                    captain_id, route.id, "estimated_patrol_density_milli")
                cargo_belief = world.decisions.belief(
                    captain_id, merchant_ship_id, "estimated_cargo_value_milli")
                believed_score = max(0, cargo_belief["value"]) * 1000 // (
                    1000 + max(0, patrol_belief["value"]))
                believed_targets.append((believed_score, patrol_value, merchant_id,
                                         route.id, merchant_ship_id, destination, route,
                                         patrol_belief["value"], cargo_belief["value"]))
            (belief_score, _, merchant_id, _, merchant_ship_id, destination, route,
             estimated_patrol, estimated_cargo) = sorted(
                believed_targets, key=lambda row: (-row[0], row[1], row[2], row[3]))[0]
            observed = world.event_log.get(pirate.get("created_event_id")
                                           or pirate.get("last_event_id"))
            merchant = world.economy.merchants[merchant_id]
            target_event = world.event_log.append(
                tick=world.clock.tick, event_type="RAIDER_TARGET_SELECTED",
                location=route.id,
                actors=[{"role": "raider_captain", "entity_id": captain_id}],
                targets=[{"role": "merchant_vessel", "entity_id": merchant_ship_id}],
                data={"merchant_id": merchant_id, "route_id": route.id,
                      "patrol_density_milli": int(route.patrol_density * 1000),
                      "estimated_patrol_density_milli": estimated_patrol,
                      "estimated_cargo_value_milli": estimated_cargo,
                      "belief_score": belief_score,
                      "observed_cargo_value_milli": sum(
                          quantity * world.economy.goods[goods_id]["base_price_silver"]
                          for goods_id, quantity in merchant["cargo_milli"].items()),
                      "decision_day": day},
                caused_by=[observed.id, merchant["last_event_id"]],
                causes_tags=["piracy_targeting", "belief_driven", "low_patrol_high_cargo"],
                magnitude=0.3)
            if location["id"] == destination:
                continue
            try:
                world.travel.start_voyage(
                    world, pirate_id, destination, caused_by=target_event.id)
            except ValueError as exc:
                if "No path" not in str(exc) and "blocked" not in str(exc).lower():
                    raise

    def _pay_daily_wages(self, world, crew_id: str, day: int) -> None:
        crew = self.crews[crew_id]
        account = world.economy.crew_accounts[crew_id]
        due = crew["pay_policy"]["wage_per_day"] * len(crew["roster"])
        paid = min(account["treasury_silver"], due)
        if paid:
            account["treasury_silver"] -= paid
            crew["treasury_silver"] = account["treasury_silver"]
            quotient, remainder = divmod(paid, max(1, len(crew["roster"])))
            shares = {}
            for index, character_id in enumerate(sorted(crew["roster"])):
                wage = quotient + (1 if index < remainder else 0)
                if wage:
                    wallets = account["member_wallets"]
                    wallets.setdefault(character_id, 0)
                    wallets[character_id] += wage
                    world.entities[character_id]["wealth"]["silver"] = wallets[character_id]
                    shares[character_id] = wage
            crew["wages_paid_silver"] += paid
            wage_event = world.event_log.append(
                tick=world.clock.tick, event_type="WAGES_PAID",
                location=world.location_of(crew["ship_ids"][0])["id"],
                targets=[{"role": "crew", "entity_id": crew_id}],
                data={"crew_id": crew_id, "day": day, "silver": paid,
                      "member_shares": shares,
                      "roster_size": len(crew["roster"]),
                      "treasury_after": account["treasury_silver"]},
                caused_by=[crew["last_event_id"]],
                causes_tags=["crew_payroll", "silver_transfer"], magnitude=0.05,
            )
            crew["last_event_id"] = wage_event.id
        unpaid = due - paid
        if unpaid:
            crew["arrears_silver"] += unpaid
            event = world.event_log.append(
                tick=world.clock.tick, event_type="WAGES_UNPAID",
                location=world.location_of(crew["ship_ids"][0])["id"],
                targets=[{"role": "crew", "entity_id": crew_id}],
                data={"crew_id": crew_id, "day": day, "unpaid_silver": unpaid,
                      "arrears_silver": crew["arrears_silver"]},
                caused_by=[crew["last_event_id"]],
                causes_tags=["crew_payroll", "wage_arrears"], magnitude=0.15,
            )
            crew["last_event_id"] = event.id

    def _try_desertion(self, world, crew_id: str, day: int) -> None:
        crew = self.crews[crew_id]
        rules = self.data["rules"]
        if (crew["morale"] > rules["desertion_morale_threshold"]
                or len(crew["roster"]) <= rules["crew_roster_min"]):
            return
        for character_id in sorted(crew["roster"]):
            if character_id == crew["captain_id"]:
                continue
            rng = world.rng.stream(f"crew:{crew_id}:desertion:{character_id}")
            if rng.randbelow(10000) >= rules["desertion_basis_points_per_day"]:
                continue
            ship_id = crew["ship_ids"][0]
            event = world.event_log.append(
                tick=world.clock.tick, event_type="DESERTION",
                location=world.location_of(ship_id)["id"],
                actors=[{"role": "deserter", "entity_id": character_id}],
                data={"crew_id": crew_id, "ship_id": ship_id, "day": day,
                      "morale": crew["morale"]},
                caused_by=[crew["last_event_id"]], causes_tags=["crew_desertion", "low_morale"],
                magnitude=0.3,
            )
            crew["roster"].remove(character_id)
            for role, assigned in list(crew["roles"].items()):
                if assigned == character_id:
                    del crew["roles"][role]
            person = world.entities[character_id]
            person["crew_id"] = None
            person["ship_id"] = None
            person["location"] = world.location_of(ship_id)
            person["last_event_id"] = event.id
            crew["last_event_id"] = event.id
            crew["morale"] = max(0, crew["morale"] - 2)
            break

    def validate_world(self, world) -> None:
        self.validate_state()
        validate_living_names(world)
        if set(world.economy.crew_accounts) != set(self.crews):
            raise ValueError("Crew silver accounts must exactly match active crews")
        expected_ship_ids = set(world.economy.ship_supplies)
        if any((ship_id not in world.entities and ship_id not in world.archive)
               or (world.entities.get(ship_id) or world.archive.get(ship_id)).get("kind") != "ship"
               for ship_id in expected_ship_ids):
            raise ValueError("Ship supply account references an unknown active ship")
        for crew_id, crew in self.crews.items():
            if crew["id"] != crew_id or crew["ship_ids"][0] not in world.entities:
                raise ValueError(f"Invalid crew identity or ship link: {crew_id}")
            ship = world.entities[crew["ship_ids"][0]]
            if ship.get("crew_id") != crew_id or ship.get("supplies") != world.economy.ship_supplies[ship["id"]]:
                raise ValueError(f"Ship and crew supply state disagree: {crew_id}")
            merchant = next((row for row in world.economy.merchants.values()
                             if row["ship_id"] == ship["id"]), None)
            if merchant is not None and ship.get("cargo") != merchant["cargo_milli"]:
                raise ValueError(f"Ship and merchant cargo state disagree: {ship['id']}")
            if len(set(crew["roster"])) != len(crew["roster"]):
                raise ValueError(f"Duplicate crew roster member: {crew_id}")
            for character_id in crew["roster"]:
                character = world.entities.get(character_id)
                if (not character or character.get("kind") != "character"
                        or character.get("crew_id") != crew_id or character.get("ship_id") != ship["id"]):
                    raise ValueError(f"Crew roster member has inconsistent links: {character_id}")
            if crew["captain_id"] not in crew["roster"] or crew["roles"].get("captain") != crew["captain_id"]:
                raise ValueError(f"Crew captain is not an active roster member: {crew_id}")
            if any(character_id not in crew["roster"] for character_id in crew["roles"].values()):
                raise ValueError(f"Crew role is assigned outside its roster: {crew_id}")
            account = world.economy.crew_accounts.get(crew_id)
            if (not account or account["treasury_silver"] != crew["treasury_silver"]
                    or set(account["member_wallets"]) - set(world.entities) - set(world.archive)):
                raise ValueError(f"Crew and silver account disagree: {crew_id}")
            for member_id, balance in account["member_wallets"].items():
                person = world.entities.get(member_id) or world.archive.get(member_id)
                if person.get("wealth", {}).get("silver") != balance:
                    raise ValueError(f"Crew wage wallet disagrees with character wealth: {member_id}")
            fund_events = [event for event in world.event_log.by_type("CREW_FUNDED")
                           if event.data.get("crew_id") == crew_id]
            wage_events = [event for event in world.event_log.by_type("WAGES_PAID")
                           if event.data.get("crew_id") == crew_id]
            unpaid_events = [event for event in world.event_log.by_type("WAGES_UNPAID")
                             if event.data.get("crew_id") == crew_id]
            treasury_adjustment = 0
            for event in world.event_log:
                if event.type == "BOUNTY_PAID" and any(
                        target.get("role") == "beneficiary_crew"
                        and target.get("entity_id") == crew_id for target in event.targets):
                    treasury_adjustment += event.data.get("silver", 0)
                elif event.type == "COMBAT_LOOT_TRANSFERRED":
                    if event.data.get("winner_crew_id") == crew_id:
                        treasury_adjustment += event.data.get("silver", 0)
                    if event.data.get("loser_crew_id") == crew_id:
                        treasury_adjustment -= event.data.get("silver", 0)
                elif event.type == "RANSOM_PAID":
                    if any(actor.get("entity_id") == crew_id for actor in event.actors):
                        treasury_adjustment -= event.data.get("silver", 0)
                    if any(target.get("entity_id") == crew_id for target in event.targets):
                        treasury_adjustment += event.data.get("silver", 0)
            if len(fund_events) != 1:
                raise ValueError(f"Crew funding history is missing or duplicated: {crew_id}")
            funded_silver = fund_events[0].data.get("funded_silver")
            paid_silver = sum(event.data.get("silver", -1) for event in wage_events)
            if (type(funded_silver) is not int or funded_silver < 0
                    or paid_silver != crew["wages_paid_silver"]
                    or funded_silver - paid_silver + treasury_adjustment != crew["treasury_silver"]):
                raise ValueError(f"Crew treasury disagrees with its funding/payroll events: {crew_id}")
            expected_wallets = {}
            for event in wage_events:
                shares = event.data.get("member_shares")
                if (not isinstance(shares, Mapping)
                        or sum(shares.values()) != event.data.get("silver")):
                    raise ValueError(f"Invalid crew wage distribution event: {event.id}")
                for member_id, amount in shares.items():
                    expected_wallets[member_id] = expected_wallets.get(member_id, 0) + amount
            for event in world.event_log.by_type("CREW_MEMBER_DEFECTED"):
                member_id = event.actors[0]["entity_id"]
                if event.data.get("from_crew_id") == crew_id:
                    expected_wallets.pop(member_id, None)
                elif event.data.get("to_crew_id") == crew_id:
                    expected_wallets[member_id] = (expected_wallets.get(member_id, 0)
                                                   + event.data.get("wallet_silver", 0))
            if any(expected_wallets.get(member_id, 0) != balance
                   for member_id, balance in account["member_wallets"].items()):
                raise ValueError(f"Crew wallets disagree with payroll history: {crew_id}")
            arrears = sum(event.data.get("unpaid_silver", -1) for event in unpaid_events)
            if arrears != crew["arrears_silver"]:
                raise ValueError(f"Crew wage arrears disagree with payroll history: {crew_id}")
            try:
                world.event_log.get(crew["last_event_id"])
            except KeyError as exc:
                raise ValueError(f"Crew references an unknown event: {crew_id}") from exc
        for ship_id in expected_ship_ids:
            ship = world.entities.get(ship_id) or world.archive.get(ship_id)
            if ship is not None and ship.get("sunk_tick") is None \
                    and ship.get("crew_id") not in self.crews:
                raise ValueError(f"Provisioned ship lacks a crew: {ship_id}")

    def validate_state(self) -> None:
        if type(self.last_updated_day) is not int or self.last_updated_day < 0:
            raise ValueError("Invalid crew/ship lifecycle day")
        for crew_id, crew in self.crews.items():
            if (not isinstance(crew, dict) or crew.get("id") != crew_id
                    or not isinstance(crew.get("name"), str) or not crew["name"]
                    or not isinstance(crew.get("roster"), list)
                    or not isinstance(crew.get("roles"), dict)
                    or any(type(crew.get(key)) is not int or not 0 <= crew[key] <= 100
                           for key in ("morale", "cohesion", "discipline"))
                    or type(crew.get("arrears_silver")) is not int or crew["arrears_silver"] < 0
                    or type(crew.get("treasury_silver")) is not int or crew["treasury_silver"] < 0
                    or type(crew.get("wages_paid_silver")) is not int or crew["wages_paid_silver"] < 0
                    or not isinstance(crew.get("ship_ids"), list) or len(crew["ship_ids"]) != 1
                    or not isinstance(crew.get("last_event_id"), str)):
                raise ValueError(f"Invalid crew record: {crew_id}")

    def snapshot(self) -> dict[str, Any]:
        return {"digest": self.digest,
                "crews": {key: self.crews[key] for key in sorted(self.crews)},
                "last_updated_day": self.last_updated_day}

    def _crew(self, crew_id: str) -> dict[str, Any]:
        try:
            return self.crews[crew_id]
        except KeyError as exc:
            raise ValueError(f"Unknown crew: {crew_id}") from exc
