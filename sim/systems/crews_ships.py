"""Persistent crew rosters, ship components, provisions, and supply morale."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sim.core.names import living_name_in_use, validate_living_names
from sim.core.ownership import ship_is_commandable
from sim.core.actor_bands import classify_ship_actor_band


CREWS_SHIPS_PATH = Path(__file__).resolve().parents[1] / "data" / "crews_ships.json"
_SUPPLY_GOODS = {"grain", "water", "rum", "powder", "timber", "sailcloth"}
_RAIDER_INTEL_MAX_AGE_DAYS = 60  # allow one missed monthly observation; then expire it
_RAIDER_PLAN_TERMINAL = {"contact", "intercepted", "aborted", "escaped", "invalidated", "expired"}


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
                 last_updated_day: int = 0,
                 actor_deadlines: dict[str, int] | None = None) -> None:
        self.data, actual_digest = load_crews_ships_data()
        if digest != actual_digest:
            raise ValueError("Crew/ship catalog changed since snapshot creation")
        if type(last_updated_day) is not int or last_updated_day < 0:
            raise ValueError("Invalid crew/ship update day")
        self.digest = digest
        self.crews = {key: json.loads(json.dumps(row, sort_keys=True))
                      for key, row in (crews or {}).items()}
        self.last_updated_day = last_updated_day
        self.actor_deadlines = dict(actor_deadlines or {})
        # Audit-only counters; excluded from snapshots and never consulted by simulation rules.
        self.actor_recruitment_diagnostics: dict[str, dict[str, Any]] = {}

    @classmethod
    def create(cls) -> "CrewShipSystem":
        _, digest = load_crews_ships_data()
        return cls(digest=digest)

    @classmethod
    def restore(cls, snapshot: dict[str, Any]) -> "CrewShipSystem":
        if (not isinstance(snapshot, dict)
                or set(snapshot) not in (
                    {"digest", "crews", "last_updated_day"},
                    {"digest", "crews", "last_updated_day", "actor_deadlines"})
                or not isinstance(snapshot.get("crews"), dict)):
            raise ValueError("Invalid crew/ship snapshot")
        return cls(digest=snapshot["digest"], crews=snapshot["crews"],
                   last_updated_day=snapshot["last_updated_day"],
                   actor_deadlines=snapshot.get("actor_deadlines"))

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
                      "ship_id": ship_id, "roles": role_map,
                      "legal_owner_kind": "merchant", "legal_owner_id": merchant_id,
                      "controller_crew_id": crew_id},
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
                "ideology_tags": ["merchant", "trade"],
                "founded_tick": world.clock.tick,
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
                "owner_char": captain_id,
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
            world.assign_ship_title(
                ship_id, owner_kind="merchant", owner_id=merchant_id,
                controller_crew_id=crew_id, event_id=event.id)
            desired = dict(ship_profile["starting_supplies"])
            island_stock = world.economy.islands[home]["stockpile_milli"]
            actual = {goods_id: min(quantity, island_stock[goods_id])
                      for goods_id, quantity in desired.items()}
            supply_event = world.economy.record_ship_supply_transfer(
                world, ship_id, home, actual, caused_by=event.id)
            ship["supplies"] = dict(world.economy.ship_supplies[ship_id])
            ship["history"].append(supply_event)
            crew["last_event_id"] = supply_event
        self.actor_deadlines = {
            "merchant": 6, "pirate": 13, "privateer": 20, "marine": 27,
            "promotion": 9, "legend": 31,
        }
        self.validate_world(world)

    def seed_opening_actor_bands(self, world) -> None:
        """Create a small, explicitly world-generated opening force for early agency."""
        if any(event.type == "ACTOR_FORCE_SEEDED" for event in world.event_log):
            raise ValueError("Opening actor force can only be seeded once")
        cause = world.event_log.by_type("WORLD_GENERATED")[0].id
        # These are opening actors, not a band quota. Their initial stores are
        # transferred from the named island's finite stockpile by provisioning.
        for band, island_id in (("pirate", "isle_002"),
                                ("privateer", "isle_006"),
                                ("marine", "isle_012")):
            name = self._regional_actor_vessel_name(world, island_id, band)
            ship_id, ship_event_id = world.create_vessel(name, island_id)
            world.event_log.append(
                tick=world.clock.tick, event_type="ACTOR_FORCE_SEEDED", location=island_id,
                targets=[{"role": "seeded_vessel", "entity_id": ship_id}],
                data={"band": band, "origin": "world_generation", "island_id": island_id},
                caused_by=[cause, ship_event_id],
                causes_tags=["world_generation", "opening_actor_force", band], magnitude=0.1)
            self.provision_recruited_vessel(world, ship_id, band, caused_by=ship_event_id)
            world.politics.crew_heat.setdefault(
                self.crews[world.entities[ship_id]["crew_id"]]["id"], 0)
        world.politics.global_tension = min(10000, sum(world.politics.crew_heat.values()))
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
                  "roles": {"captain": captain_id}, "recruitment": "actor_budget",
                  "legal_owner_kind": "merchant", "legal_owner_id": merchant_id,
                  "controller_crew_id": crew_id},
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
            "owner_char": captain_id, "owner_type": "merchant",
            "hull": {"max": profile["hull_max"], "current": profile["hull_max"]},
            "rigging": {"max": profile["rigging_max"], "current": profile["rigging_max"]},
            "cannons": [dict(row) for row in profile["cannons"]],
            "ammunition": profile["ammunition"], "ammunition_max": profile["ammunition"],
            "maneuverability": profile["maneuverability"],
            "cargo_capacity_milli": merchant["capacity_milli"],
            "cargo": dict(merchant["cargo_milli"]), "upgrades": [],
            "condition_flags": [], "history": [founded.id], "crew_id": crew_id,
        })
        world.assign_ship_title(
            ship_id, owner_kind="merchant", owner_id=merchant_id,
            controller_crew_id=crew_id, event_id=founded.id)
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
                  "roles": {"captain": captain_id}, "owner_type": owner_type,
                  "legal_owner_kind": "crew", "legal_owner_id": crew_id,
                  "controller_crew_id": crew_id},
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
            "owner_char": captain_id, "owner_type": owner_type,
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
                      "crew_id": crew_id, "captain_appointed_event_id": appointed.id,
                      "legal_owner_kind": "crew", "legal_owner_id": crew_id,
                      "controller_crew_id": crew_id},
            caused_by=[founded.id, appointed.id],
            causes_tags=["ship_acquisition", "actor_budget_recruitment", owner_type],
            magnitude=0.3)
        crew["last_event_id"] = acquired.id
        world.assign_ship_title(
            ship_id, owner_kind="crew", owner_id=crew_id,
            controller_crew_id=crew_id, event_id=acquired.id)
        return crew_id

    @staticmethod
    def crew_location(world, crew: dict[str, Any]) -> dict[str, str]:
        if crew.get("ship_ids"):
            ship_id = crew["ship_ids"][0]
            if ship_id in world.entities:
                return world.location_of(ship_id)
        location = crew.get("stranded_location")
        if isinstance(location, dict) and location.get("kind") == "island":
            return dict(location)
        raise ValueError(f"Hull-less crew lacks a valid stranded location: {crew.get('id')}")

    def recruit(self, world, crew_id: str, character_id: str, *, caused_by: str) -> str:
        crew = self._crew(crew_id)
        if not crew["ship_ids"] or not ship_is_commandable(world, crew["ship_ids"][0]):
            raise ValueError("Crew cannot recruit without valid vessel command authority")
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

    def on_character_captured(self, world, character_id: str, *, captor_ship_id: str,
                              captor_crew_id: str, caused_by: str) -> str:
        """Move a captured character into custody and keep their former crew operational."""
        character = world.entities.get(character_id)
        if not character or character.get("kind") != "character":
            raise ValueError("Captured character must remain an active character")
        captor_ship = world.entities.get(captor_ship_id)
        if (not captor_ship or captor_ship.get("kind") != "ship"
                or captor_ship.get("crew_id") != captor_crew_id):
            raise ValueError("Prisoner captor must have a valid commanded vessel")
        world.event_log.get(caused_by)
        former_crew_id = character.get("crew_id")
        former_crew = self.crews.get(former_crew_id)
        if former_crew is not None and character_id not in former_crew["roster"]:
            raise ValueError("Captured character's crew link is absent from its roster")
        location = world.location_of(captor_ship_id)
        capture = world.event_log.append(
            tick=world.clock.tick, event_type="PRISONER_CAPTURED", location=location["id"],
            actors=[{"role": "captor_crew", "entity_id": captor_crew_id}],
            targets=[{"role": "prisoner", "entity_id": character_id},
                     *([{"role": "former_crew", "entity_id": former_crew_id}]
                       if former_crew_id else [])],
            data={"character_id": character_id, "captor_ship_id": captor_ship_id,
                  "from_crew_id": former_crew_id, "to_crew_id": None},
            caused_by=[caused_by], causes_tags=["prisoner_capture", "crew_departure"],
            magnitude=0.65)
        if former_crew is not None:
            former_crew["roster"].remove(character_id)
            for role, assigned in list(former_crew["roles"].items()):
                if assigned == character_id:
                    del former_crew["roles"][role]
            former_crew["last_event_id"] = capture.id
            if former_crew["captain_id"] == character_id:
                if not former_crew["roster"]:
                    stranded_or_ship_location = self.crew_location(world, former_crew)
                    arrival_island = (stranded_or_ship_location["id"]
                                      if stranded_or_ship_location["kind"] == "island"
                                      else world.geography.routes[
                                          stranded_or_ship_location["id"]].origin)
                    replacement_id = world.characters.create_character(
                        world, tier="MINOR", cause_event_id=capture.id, role="sailor",
                        island_id=arrival_island)
                    generated_id = world.entities[replacement_id]["last_event_id"]
                    arrival = world.event_log.append(
                        tick=world.clock.tick, event_type="CHARACTER_ARRIVED",
                        location=stranded_or_ship_location["id"],
                        actors=[{"role": "new_crew_member", "entity_id": replacement_id}],
                        data={"crew_id": former_crew_id, "role": "sailor",
                              "replacement_for": character_id,
                              "reason": "captain_captured"},
                        caused_by=[capture.id, generated_id],
                        causes_tags=["crew_replacement", "character_arrival"], magnitude=0.25)
                    former_crew["roster"].append(replacement_id)
                    replacement = world.entities[replacement_id]
                    replacement.update({
                        "crew_id": former_crew_id,
                        "ship_id": former_crew["ship_ids"][0] if former_crew["ship_ids"] else None,
                        "location": stranded_or_ship_location,
                        "last_event_id": arrival.id,
                    })
                    world.economy.crew_accounts[former_crew_id]["member_wallets"][replacement_id] = 0
                former_crew["roster"].sort()
                successor = min(former_crew["roster"])
                successor_ship_id = (former_crew["ship_ids"][0]
                                     if former_crew["ship_ids"] else None)
                succession_location = (world.location_of(successor_ship_id)["id"]
                                       if successor_ship_id
                                       else self.crew_location(world, former_crew)["id"])
                succession = world.event_log.append(
                    tick=world.clock.tick, event_type="CAPTAIN_CHANGED",
                    location=succession_location,
                    actors=[{"role": "new_captain", "entity_id": successor}],
                    targets=[{"role": "crew", "entity_id": former_crew_id}],
                    data={"crew_id": former_crew_id, "former_captain_id": character_id,
                          "new_captain_id": successor, "reason": "captured"},
                    caused_by=[capture.id], causes_tags=["crew_succession", "captain_captured"],
                    magnitude=0.45)
                former_crew["captain_id"] = successor
                former_crew["roles"]["captain"] = successor
                former_crew["last_event_id"] = succession.id
                if successor_ship_id:
                    ship = world.entities[successor_ship_id]
                    ship["owner_char"] = successor
                    ship["history"].append(succession.id)
                world.entities[successor]["last_event_id"] = succession.id
        character.update({"crew_id": None, "ship_id": captor_ship_id,
                          "location": location, "last_event_id": capture.id})
        prisoner = world.encounters.prisoners[character_id]
        prisoner["captor_ship_id"] = captor_ship_id
        prisoner["capture_event_id"] = capture.id
        prisoner["former_crew_id"] = former_crew_id
        prisoner["last_custody_event_id"] = capture.id
        prisoner["capture_day"] = world.clock.tick // (
            world.config["time"]["hours_per_day"]
            // world.config["time"]["hours_per_tick"])
        prisoner["next_review_day"] = prisoner["capture_day"] + 7
        return capture.id

    def employ_stranded_crew(self, world, stranded_crew_id: str, employer_crew_id: str, *,
                             caused_by: str) -> str:
        """Merge an opted-in hull-less crew into a co-located, commandable employer."""
        stranded = self._crew(stranded_crew_id)
        employer = self._crew(employer_crew_id)
        if stranded_crew_id == employer_crew_id or stranded["ship_ids"]:
            raise ValueError("Employment source must be a distinct hull-less crew")
        if not employer["ship_ids"] or not ship_is_commandable(world, employer["ship_ids"][0]):
            raise ValueError("Employment requires a commandable employer vessel")
        if len(stranded["roster"]) + len(employer["roster"]) > self.data["rules"]["crew_roster_max"]:
            raise ValueError("Employer vessel lacks crew capacity for the stranded crew")
        location = self.crew_location(world, stranded)
        employer_ship_id = employer["ship_ids"][0]
        employer_location = world.location_of(employer_ship_id)
        if location.get("kind") != "island" or employer_location != location:
            raise ValueError("Employer vessel must be at the stranded crew's island")
        if any(member_id not in world.entities
               or world.entities[member_id].get("missing_at_sea") is not None
               for member_id in stranded["roster"]):
            raise ValueError("Employment cannot resolve a crew with missing or inactive members")
        if any(prisoner.get("status") == "held"
               and prisoner.get("former_crew_id") == stranded_crew_id
               for prisoner in world.encounters.prisoners.values()):
            raise ValueError("Employment cannot dissolve a crew with an unresolved held prisoner")
        source_faction = world.politics.factions.get(stranded.get("faction_id"))
        if ((source_faction is not None and source_faction.get("kind") == "marine")
                or "marine" in stranded.get("ideology_tags", ())
                or any(world.entities.get(ship_id, {}).get("owner_type") == "marine"
                       for ship_id in stranded["ship_ids"])):
            raise ValueError("Marine organizations cannot dissolve through civilian employment")
        source_account = world.economy.crew_accounts[stranded_crew_id]
        target_account = world.economy.crew_accounts[employer_crew_id]
        if set(source_account["member_wallets"]) & set(target_account["member_wallets"]):
            raise ValueError("Employment would duplicate a personal crew wallet")
        world.event_log.get(caused_by)
        accepted = world.event_log.append(
            tick=world.clock.tick, event_type="CREW_EMPLOYMENT_ACCEPTED", location=location["id"],
            actors=[{"role": "employer_captain", "entity_id": employer["captain_id"]}],
            targets=[{"role": "employed_crew", "entity_id": stranded_crew_id}],
            data={"from_crew_id": stranded_crew_id, "to_crew_id": employer_crew_id,
                  "ship_id": employer_ship_id, "member_count": len(stranded["roster"])},
            caused_by=list(dict.fromkeys([caused_by, employer["last_event_id"]])),
            causes_tags=["stranded_crew", "employment", "crew_consolidation"], magnitude=0.45)
        for member_id in sorted(stranded["roster"]):
            wallet_silver = source_account["member_wallets"].get(member_id, 0)
            transfer = world.event_log.append(
                tick=world.clock.tick, event_type="CREW_MEMBER_TRANSFERRED",
                location=location["id"],
                actors=[{"role": "employer_captain", "entity_id": employer["captain_id"]}],
                targets=[{"role": "employed_member", "entity_id": member_id}],
                data={"character_id": member_id, "from_crew_id": stranded_crew_id,
                      "to_crew_id": employer_crew_id, "wallet_silver": wallet_silver,
                      "reason": "stranded_employment"},
                caused_by=[accepted.id],
                causes_tags=["crew_membership_transfer", "employment", "wallet_transfer"],
                magnitude=0.25)
            source_account["member_wallets"].pop(member_id, None)
            target_account["member_wallets"][member_id] = wallet_silver
            person = world.entities[member_id]
            person.update({"crew_id": employer_crew_id, "ship_id": employer_ship_id,
                           "location": employer_location, "last_event_id": transfer.id})
            role = next((name for name, assigned in stranded["roles"].items()
                         if assigned == member_id and name != "captain"), "sailor")
            while role in employer["roles"]:
                role = "sailor_" + str(len(employer["roles"]) + 1)
            employer["roles"][role] = member_id
            employer["roster"].append(member_id)
            employer["last_event_id"] = transfer.id
        employer["roster"].sort()

        # Historical/dead/captive members can still own wallet balances in the
        # old account; transfer that ledger explicitly without recreating roster membership.
        for member_id, wallet_silver in sorted(source_account["member_wallets"].items()):
            transfer = world.event_log.append(
                tick=world.clock.tick, event_type="CREW_WALLET_TRANSFERRED",
                location=location["id"],
                actors=[{"role": "source_crew", "entity_id": stranded_crew_id}],
                targets=[{"role": "wallet_beneficiary", "entity_id": member_id}],
                data={"character_id": member_id, "from_crew_id": stranded_crew_id,
                      "to_crew_id": employer_crew_id, "wallet_silver": wallet_silver},
                caused_by=[accepted.id],
                causes_tags=["wallet_transfer", "crew_consolidation"], magnitude=0.1)
            target_account["member_wallets"][member_id] = wallet_silver
            source_account["member_wallets"].pop(member_id)
            employer["last_event_id"] = transfer.id

        source_treasury = source_account["treasury_silver"]
        source_arrears = stranded["arrears_silver"]
        source_owned_ship_ids = sorted(
            ship_id for ship_id, ship in world.entities.items()
            if ship.get("kind") == "ship"
            and ship.get("legal_owner") == {"kind": "crew", "id": stranded_crew_id})
        assets = world.event_log.append(
            tick=world.clock.tick, event_type="CREW_ASSETS_TRANSFERRED", location=location["id"],
            actors=[{"role": "source_crew", "entity_id": stranded_crew_id}],
            targets=[{"role": "employer_crew", "entity_id": employer_crew_id}],
            data={"from_crew_id": stranded_crew_id, "to_crew_id": employer_crew_id,
                  "treasury_silver": source_treasury, "arrears_silver": source_arrears,
                  "ship_ids": source_owned_ship_ids},
            caused_by=[accepted.id],
            causes_tags=["crew_assets", "finite_transfer", "employment"], magnitude=0.2)
        target_account["treasury_silver"] += source_treasury
        employer["treasury_silver"] += source_treasury
        employer["arrears_silver"] += source_arrears
        source_account["treasury_silver"] = 0
        stranded["treasury_silver"] = 0
        stranded["arrears_silver"] = 0
        employer["last_event_id"] = assets.id

        # A crew can hold legal title to captured hulls it does not list as its
        # onboard vessel. Dissolving that owner must not leave references to a
        # terminal organization. Preserve control only when the hull already
        # has a valid, living onboard crew/captain; otherwise clear it.
        for asset_ship_id in source_owned_ship_ids:
            asset_ship = world.entities[asset_ship_id]
            former_controller = asset_ship.get("controller_crew_id")
            onboard_crew_id = asset_ship.get("crew_id")
            onboard_crew = self.crews.get(onboard_crew_id)
            onboard_captain_id = onboard_crew.get("captain_id") if onboard_crew else None
            onboard_captain = world.entities.get(onboard_captain_id, {})
            onboard_control_valid = (
                onboard_crew is not None
                and asset_ship_id in onboard_crew.get("ship_ids", ())
                and onboard_captain_id in onboard_crew.get("roster", ())
                and onboard_captain.get("kind") == "character"
                and onboard_captain.get("death_tick") is None
                and onboard_captain.get("crew_id") == onboard_crew_id
                and onboard_captain.get("ship_id") == asset_ship_id)
            new_controller = onboard_crew_id if onboard_control_valid else None
            title_transfer = world.event_log.append(
                tick=world.clock.tick, event_type="SHIP_ACQUIRED",
                location=world.location_of(asset_ship_id)["id"],
                actors=[{"role": "acquiring_crew", "entity_id": employer_crew_id}],
                targets=[{"role": "acquired_ship", "entity_id": asset_ship_id}],
                data={"method": "crew_employment_asset_transfer",
                      "previous_owner": {"kind": "crew", "id": stranded_crew_id},
                      "previous_controller_crew_id": former_controller,
                      "new_crew_id": employer_crew_id,
                      "legal_owner_kind": "crew", "legal_owner_id": employer_crew_id,
                      "controller_crew_id": new_controller,
                      "controller_assignment": ("existing_onboard_crew"
                                                if onboard_control_valid
                                                else "unassigned_no_valid_onboard_crew")},
                caused_by=[accepted.id, assets.id],
                causes_tags=["ship_acquisition", "crew_dissolution", "employment"],
                magnitude=0.35)
            world.assign_ship_title(
                asset_ship_id, owner_kind="crew", owner_id=employer_crew_id,
                controller_crew_id=new_controller, event_id=title_transfer.id)
            asset_ship["last_event_id"] = title_transfer.id
            asset_ship.setdefault("history", []).append(title_transfer.id)

        dissolved = world.event_log.append(
            tick=world.clock.tick, event_type="CREW_DISSOLVED", location=location["id"],
            actors=[{"role": "former_captain", "entity_id": stranded["captain_id"]}],
            targets=[{"role": "dissolved_crew", "entity_id": stranded_crew_id}],
            data={"crew_id": stranded_crew_id, "employed_by_crew_id": employer_crew_id,
                  "member_count": len(stranded["roster"]),
                  "assets_event_id": assets.id},
            caused_by=[assets.id], causes_tags=["crew_dissolution", "employment"],
            magnitude=0.45)
        faction_event = world.politics.on_crew_dissolved(
            world, stranded_crew_id, caused_by=dissolved.id, reason="employment")
        stranded["last_event_id"] = faction_event or dissolved.id
        self.crews.pop(stranded_crew_id)
        world.economy.crew_accounts.pop(stranded_crew_id)
        return faction_event or dissolved.id

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
            location = self.crew_location(world, crew)
            replacement_id = world.characters.create_character(
                world, tier="MINOR", cause_event_id=death_event_id, role="sailor")
            generated_id = world.entities[replacement_id]["last_event_id"]
            arrival = world.event_log.append(
                tick=world.clock.tick, event_type="CHARACTER_ARRIVED",
                location=location["id"],
                actors=[{"role": "new_crew_member", "entity_id": replacement_id}],
                data={"crew_id": crew["id"], "role": "sailor",
                      "replacement_for": character_id},
                caused_by=[death_event_id, generated_id],
                causes_tags=["crew_replacement", "character_arrival"], magnitude=0.25)
            crew["roster"].append(replacement_id)
            replacement = world.entities[replacement_id]
            replacement["last_event_id"] = arrival.id
            replacement["crew_id"] = crew["id"]
            replacement["ship_id"] = crew["ship_ids"][0] if crew["ship_ids"] else None
            replacement["location"] = location
            world.economy.crew_accounts[crew["id"]]["member_wallets"][replacement_id] = 0
        if crew["captain_id"] == character_id:
            successor = min(crew["roster"])
            ship_id = crew["ship_ids"][0] if crew["ship_ids"] else None
            location = (world.location_of(ship_id)["id"] if ship_id
                        else self.crew_location(world, crew)["id"])
            succession = world.event_log.append(
                tick=world.clock.tick, event_type="CAPTAIN_CHANGED", location=location,
                actors=[{"role": "new_captain", "entity_id": successor}],
                targets=[{"role": "crew", "entity_id": crew["id"]}],
                data={"crew_id": crew["id"], "former_captain_id": character_id,
                      "new_captain_id": successor, "reason": "captain_death"},
                caused_by=[death_event_id], causes_tags=["crew_succession", "captain_death"],
                magnitude=0.45,
            )
            world.event_log.append(
                tick=world.clock.tick, event_type="CAPTAIN_APPOINTED",
                location=location,
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
            if ship_id is not None:
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
            if not crew["ship_ids"]:
                self._pay_daily_wages(world, crew_id, day)
                crew["distress_days"] += 1
                if crew["distress_days"] % 30 == 0:
                    self._seek_stranded_employment(world, crew_id)
                continue
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

    def _seek_stranded_employment(self, world, stranded_crew_id: str, *,
                                  opportunity_event_id: str | None = None,
                                  preferred_employer_crew_id: str | None = None) -> str | None:
        stranded = self.crews.get(stranded_crew_id)
        if stranded is None or stranded["ship_ids"]:
            return None
        location = self.crew_location(world, stranded)
        captain_id = stranded["captain_id"]
        eligible_employers = []
        blocked_reasons: dict[str, int] = {}
        stranded_members_active = all(
            member_id in world.entities
            and world.entities[member_id].get("missing_at_sea") is None
            for member_id in stranded["roster"])
        held_prisoner = any(
            prisoner.get("status") == "held"
            and prisoner.get("former_crew_id") == stranded_crew_id
            for prisoner in world.encounters.prisoners.values())
        source_faction = world.politics.factions.get(stranded.get("faction_id"))
        marine_crew = (
            (source_faction is not None and source_faction.get("kind") == "marine")
            or "marine" in stranded.get("ideology_tags", ())
            or any(world.entities.get(ship_id, {}).get("owner_type") == "marine"
                   for ship_id in stranded.get("ship_ids", ())))
        source_wallets = set(world.economy.crew_accounts[stranded_crew_id]["member_wallets"])

        def block(reason: str) -> None:
            blocked_reasons[reason] = blocked_reasons.get(reason, 0) + 1

        for employer_id, employer in sorted(self.crews.items()):
            if employer_id == stranded_crew_id:
                continue
            if not employer["ship_ids"]:
                block("employer_has_no_hull")
                continue
            ship_id = employer["ship_ids"][0]
            if ship_id not in world.entities:
                block("employer_hull_inactive")
                continue
            if not ship_is_commandable(world, ship_id):
                block("employer_hull_not_commandable")
                continue
            if world.location_of(ship_id) != location:
                block("employer_not_at_stranded_island")
                continue
            if len(employer["roster"]) + len(stranded["roster"]) > self.data["rules"]["crew_roster_max"]:
                block("employer_lacks_roster_capacity")
                continue
            if not stranded_members_active:
                block("stranded_crew_has_missing_or_inactive_member")
                continue
            if held_prisoner:
                block("stranded_crew_has_held_prisoner")
                continue
            if marine_crew:
                block("marine_organization_cannot_use_civilian_employment")
                continue
            employer_wallets = set(world.economy.crew_accounts[employer_id]["member_wallets"])
            if source_wallets & employer_wallets:
                block("overlapping_personal_wallet_accounts")
                continue
            eligible_employers.append(employer_id)

        request_causes = [stranded["last_event_id"]]
        if opportunity_event_id is not None:
            world.event_log.get(opportunity_event_id)
            request_causes.append(opportunity_event_id)
        request = world.event_log.append(
            tick=world.clock.tick, event_type="CREW_EMPLOYMENT_SOUGHT",
            location=location["id"], actors=[{"role": "requesting_captain", "entity_id": captain_id}],
            targets=[{"role": "stranded_crew", "entity_id": stranded_crew_id}],
            data={"crew_id": stranded_crew_id, "captain_id": captain_id,
                  "stranded_days": stranded["distress_days"],
                  "member_count": len(stranded["roster"]), "location": location,
                  "eligible_employer_crew_ids": eligible_employers,
                  "blocked_candidate_reasons": dict(sorted(blocked_reasons.items()))},
            caused_by=list(dict.fromkeys(request_causes)),
            causes_tags=["stranded_crew", "employment_search", "crew_distress"],
            magnitude=0.3)
        stranded["last_event_id"] = request.id
        if eligible_employers:
            employer_id = (preferred_employer_crew_id
                           if preferred_employer_crew_id in eligible_employers
                           else eligible_employers[0])
            return self.employ_stranded_crew(
                world, stranded_crew_id, employer_id, caused_by=request.id)
        return request.id

    def on_commanded_ship_arrival(self, world, ship_id: str, arrival_event_id: str) -> list[str]:
        """Offer co-located stranded crews an immediate, causal employment/rescue opportunity."""
        if not ship_is_commandable(world, ship_id):
            return []
        location = world.location_of(ship_id)
        if location.get("kind") != "island":
            return []
        arriving_crew_id = world.entities[ship_id]["controller_crew_id"]
        resolved = world.encounters.install_pending_prize_crews(
            world, ship_id, arrival_event_id)
        for stranded_crew_id in sorted(self.crews):
            stranded = self.crews[stranded_crew_id]
            if (stranded_crew_id == arriving_crew_id or stranded["ship_ids"]
                    or self.crew_location(world, stranded) != location):
                continue
            # A commanded arrival at the recorded search island is a concrete
            # rescue opportunity for this stranded crew. Resolve only people
            # whose persisted missing-person state points to this location;
            # successful rescue restores the same identity and does not invent
            # a population gain.
            for person_id in sorted(stranded["roster"]):
                person = world.entities.get(person_id)
                missing = person.get("missing_at_sea") if person else None
                if (not isinstance(missing, dict)
                        or missing.get("search_location") != location):
                    continue
                rescue_id = world.encounters.resolve_missing_person(
                    world, person_id, "found_alive", caused_by=arrival_event_id,
                    rescuer_ship_id=ship_id)
                resolved.append(rescue_id)
            result_id = self._seek_stranded_employment(
                world, stranded_crew_id, opportunity_event_id=arrival_event_id,
                preferred_employer_crew_id=arriving_crew_id)
            if result_id is not None:
                resolved.append(result_id)
        return resolved

    def update_actor_budgets(self, day: int, world) -> None:
        """Process independently staggered opportunities and retain regular intel review."""
        self._ensure_actor_deadlines(day)
        self._recruit_actor_bands(world, day)
        if day % 30 == 0:
            self._dispatch_raiders(world, day)

    def _recruit_actor_bands(self, world, day: int) -> None:
        """Recruit one actor only at its persisted deadline and when local resources permit."""
        self._ensure_actor_deadlines(day)
        actors = world.config.get("world", {}).get("actors", {})
        ship_rules = actors.get("ship_bands", {})
        if not ship_rules:
            return
        island_count = len(world.geography.islands)
        minimums = {
            band: (rules["minimum_per_island_milli"] * island_count + 999) // 1000
            for band, rules in ship_rules.items()
        }
        # Merchant minimum is 32 hulls for the 40-island map; the prior 23-day
        # interval could add at most ~16 ships/year from a three-ship opening,
        # below the measured floor even with no losses. Keep each recruitment
        # resource/deadline-gated, but allow enough independent opportunities.
        intervals = {"merchant": 12, "pirate": 29, "privateer": 37, "marine": 31}
        for band in ("merchant", "pirate", "privateer", "marine"):
            if day < self.actor_deadlines[band]:
                continue
            count = self._active_actor_ship_count(world, band)
            if count < minimums[band]:
                recruitment_diag = self.actor_recruitment_diagnostics.setdefault(
                    band, {"opportunities": 0, "recruited": 0, "blocked": {}})
                recruitment_diag["opportunities"] += 1
                profile_supplies = self.data["classes"]["merchant_sloop"]["starting_supplies"]
                required = {good: profile_supplies[good]
                            for good in ("grain", "water", "rum")}
                island_id = self._least_populated_ship_island(
                    world, band, required_supplies=required)
                if island_id is not None:
                    cause = world.event_log.by_type("WORLD_GENERATED")[0].id
                    if band == "merchant":
                        island_name = world.geography.islands[island_id].name
                        regional_count = sum(
                            merchant.get("home_island") == island_id
                            for merchant in world.economy.merchants.values())
                        merchant_name = f"{island_name} Trading Company {regional_count + 1}"
                        merchant_id, merchant_event_id = world.economy.create_merchant(
                            world, merchant_name, island_id,
                            silver=self.data["rules"]["starting_silver"],
                            capacity_milli=100_000)
                        crew_id = self.provision_recruited_merchant(
                            world, merchant_id, caused_by=merchant_event_id)
                        ship_id = world.economy.merchants[merchant_id]["ship_id"]
                        captain_id = self.crews[crew_id]["captain_id"]
                        acquired = world.event_log.append(
                            tick=world.clock.tick, event_type="SHIP_ACQUIRED", location=island_id,
                            actors=[{"role": "captain", "entity_id": captain_id}],
                            targets=[{"role": "acquired_ship", "entity_id": ship_id}],
                            data={"method": "build", "owner_type": band,
                                  "merchant_id": merchant_id, "crew_id": crew_id,
                                  "legal_owner_kind": "merchant", "legal_owner_id": merchant_id,
                                  "controller_crew_id": crew_id},
                            caused_by=[merchant_event_id, self.crews[crew_id]["last_event_id"]],
                            causes_tags=["ship_acquisition", "actor_budget_recruitment", band],
                            magnitude=0.3)
                        self.crews[crew_id]["last_event_id"] = acquired.id
                        world.assign_ship_title(
                            ship_id, owner_kind="merchant", owner_id=merchant_id,
                            controller_crew_id=crew_id, event_id=acquired.id)
                    else:
                        name = self._regional_actor_vessel_name(world, island_id, band)
                        ship_id, ship_event_id = world.create_vessel(name, island_id)
                        self.provision_recruited_vessel(
                            world, ship_id, band, caused_by=ship_event_id)
                    recruitment_diag["recruited"] += 1
                    self.actor_deadlines[band] = day + intervals[band]
                    continue
                reason = ("no_viable_island" if not world.geography.islands
                          else "insufficient_local_provisioning_stock")
                blocked = recruitment_diag["blocked"]
                blocked[reason] = blocked.get(reason, 0) + 1
            else:
                self.actor_deadlines[band] = day + intervals[band]
            # Failed capability/resource checks are retried on a shorter,
            # separately staggered opportunity window, never in a shared quota batch.
            self.actor_deadlines[band] = day + 7 + (sum(map(ord, band)) % 5)
        if day >= self.actor_deadlines["promotion"]:
            self._promote_captain_rookie(world, day)
            self.actor_deadlines["promotion"] = day + 13
        if day >= self.actor_deadlines["legend"]:
            self._promote_major_legend(world, day)
            self.actor_deadlines["legend"] = day + 31

    def _ensure_actor_deadlines(self, day: int) -> None:
        offsets = {"merchant": 6, "pirate": 13, "privateer": 20,
                   "marine": 27, "promotion": 9, "legend": 31}
        for key, offset in offsets.items():
            self.actor_deadlines.setdefault(key, day + offset)

    @staticmethod
    def _regional_actor_vessel_name(world, island_id: str, band: str) -> str:
        labels = {"pirate": "Corsair", "marine": "Patrol", "privateer": "Privateer"}
        if band not in labels or island_id not in world.geography.islands:
            raise ValueError("Actor vessel naming requires a known region and actor band")
        base = f"{world.geography.islands[island_id].name} {labels[band]} Sloop"
        known_names = {ship.get("name", "").casefold()
                       for ship in (*world.entities.values(), *world.archive.values())
                       if ship.get("kind") == "ship"}
        ordinal = 1
        while f"{base} {ordinal}".casefold() in known_names:
            ordinal += 1
        return f"{base} {ordinal}"

    @staticmethod
    def _active_actor_ship_count(world, band: str) -> int:
        total = 0
        for ship in world.entities.values():
            if (ship.get("kind") != "ship" or ship.get("sunk")
                    or ship.get("state") == "sunk"
                    or not ship_is_commandable(world, ship["id"])):
                continue
            crew = world.crew_ships.crews.get(ship.get("crew_id"), {})
            faction_id = crew.get("faction_id")
            faction = world.politics.factions.get(faction_id, {}) if faction_id else {}
            actor_band = classify_ship_actor_band(
                ship, faction_id=faction_id, faction_kind=faction.get("kind", ""),
                merchant_registered=any(merchant.get("ship_id") == ship.get("id")
                                        for merchant in world.economy.merchants.values()))
            total += actor_band == band
        return total

    @staticmethod
    def _least_populated_ship_island(world, band: str,
                                     required_supplies: dict[str, int] | None = None) -> str | None:
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
        if required_supplies is not None:
            counts = {island_id: count for island_id, count in counts.items()
                      if all(world.economy.islands[island_id]["stockpile_milli"].get(good, 0)
                             >= quantity for good, quantity in required_supplies.items())}
            if not counts:
                return None
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
        candidate = next((character for character in sorted(
            world.entities.values(), key=lambda row: row["id"])
            if character.get("kind") == "character" and character.get("tier") == "MINOR"
            and character.get("role") == "captain" and character.get("death_tick") is None
            and character.get("crew_id") in self.crews
            and self.crews[character["crew_id"]]["captain_id"] == character["id"]
            and len(self._captain_qualifying_deeds(world, character["id"])) >= 2), None)
        if candidate is None:
            return
        deed_event_ids = self._captain_qualifying_deeds(world, candidate["id"])[-2:]
        promotion = world.event_log.append(
            tick=world.clock.tick, event_type="ROOKIE_PROMOTED",
            location=world.location_of(candidate["id"])["id"],
            actors=[{"role": "rising_captain", "entity_id": candidate["id"]}],
            targets=[{"role": "crew", "entity_id": candidate["crew_id"]}],
            data={"from_tier": "MINOR", "to_tier": "MAJOR",
                  "deed_event_ids": deed_event_ids, "day": day},
            caused_by=deed_event_ids,
            causes_tags=["captain_rising", "logged_deeds", "actor_budget"], magnitude=0.5)
        candidate["tier"] = "MAJOR"
        candidate["last_event_id"] = promotion.id
        self.crews[candidate["crew_id"]]["last_event_id"] = promotion.id

    def _captain_qualifying_deeds(self, world, captain_id: str) -> list[str]:
        captain = world.entities.get(captain_id, {})
        crew = self.crews.get(captain.get("crew_id"), {})
        ship_ids = set(crew.get("ship_ids", ()))
        merchant_ids = {merchant_id for merchant_id, merchant
                        in world.economy.merchants.items()
                        if merchant.get("ship_id") in ship_ids}
        deeds = []
        for event in world.event_log:
            if event.tick > world.clock.tick:
                continue
            if event.type == "MARKET_TRADE":
                if (event.data.get("merchant_id") in merchant_ids
                        and event.data.get("side") == "sell"
                        and type(event.data.get("profit_silver")) is int
                        and event.data["profit_silver"] > 0):
                    deeds.append(event.id)
            elif event.type == "ENCOUNTER_RESOLVED":
                details = event.data.get("outcome_details", {})
                winner_ship = details.get("winner_ship_id")
                ships = event.data.get("ships", ())
                captains = event.data.get("captain_ids_before", ())
                winner_index = next((index for index, ship_id in enumerate(ships)
                                     if ship_id == winner_ship), None)
                if (winner_index is not None and winner_index < len(captains)
                        and captains[winner_index] == captain_id
                        and event.data.get("outcome") not in {
                            "mutual_withdrawal", "parley_ended_fight", "one_side_flees"}):
                    deeds.append(event.id)
            elif event.type == "VOYAGE_ARRIVED":
                ship_id = next((actor.get("entity_id") for actor in event.actors
                                if actor.get("entity_id") in ship_ids), None)
                if ship_id is not None:
                    deeds.append(event.id)
            elif event.type == "BATTLE_CONCLUDED":
                commanders = event.data.get("commanders", ())
                winner_side = event.data.get("winner_side")
                if (type(winner_side) is int and 0 <= winner_side < len(commanders)
                        and commanders[winner_side] == captain_id):
                    deeds.append(event.id)
            elif event.type == "SHIP_CAPTURED":
                if (event.data.get("captor_captain_id") == captain_id
                        or any(actor.get("entity_id") == captain_id for actor in event.actors)):
                    deeds.append(event.id)
        return list(dict.fromkeys(deeds))

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
        used_traits = {trait_id for character in (*world.entities.values(), *world.archive.values())
                       if character.get("kind") == "character"
                       for trait_id in character.get("legend_traits", ())}
        available_traits = sorted(
            trait["id"] for trait in world.battles.data["legend_traits"]
            if trait["id"] not in used_traits)
        if not available_traits:
            return
        candidate = next((character for character in sorted(
            world.entities.values(), key=lambda row: row["id"])
            if character.get("kind") == "character" and character.get("tier") == "MAJOR"
            and character.get("role") == "captain" and character.get("death_tick") is None
            and character.get("crew_id") in self.crews
            and self.crews[character["crew_id"]]["captain_id"] == character["id"]
            and len(self._captain_qualifying_deeds(world, character["id"])) >= 5), None)
        if candidate is None:
            return
        deeds = self._captain_qualifying_deeds(world, candidate["id"])[-3:]
        event = world.event_log.append(
            tick=world.clock.tick, event_type="LEGEND_PROMOTED",
            location=world.location_of(candidate["id"])["id"],
            actors=[{"role": "legendary_captain", "entity_id": candidate["id"]}],
            targets=[{"role": "crew", "entity_id": candidate["crew_id"]}],
            data={"from_tier": "MAJOR", "to_tier": "LEGEND",
                  "deed_event_ids": deeds, "legend_trait_id": available_traits[0],
                  "day": day},
            caused_by=deeds, causes_tags=["captain_rising", "logged_deeds", "legend_band"],
            magnitude=0.6)
        candidate["tier"] = "LEGEND"
        candidate["legend_traits"] = [available_traits[0]]
        candidate["legend_since_event_id"] = None
        candidate["last_event_id"] = event.id
        self.crews[candidate["crew_id"]]["last_event_id"] = event.id

    def _dispatch_raiders(self, world, day: int) -> None:
        """Observe local merchant traffic, then act only on fresh attributed beliefs."""
        ticks_per_day = (world.config["time"]["hours_per_day"]
                         // world.config["time"]["hours_per_tick"])
        max_age = _RAIDER_INTEL_MAX_AGE_DAYS * ticks_per_day
        pirates = sorted(
            ship_id for ship_id, ship in world.entities.items()
            if ship.get("kind") == "ship" and ship.get("owner_type") == "pirate"
            and ship_id not in world.travel.transits
            and ship_is_commandable(world, ship_id))
        for pirate_id in pirates:
            pirate = world.entities[pirate_id]
            location = world.location_of(pirate_id)
            if location["kind"] != "island":
                continue
            crew = self.crews[pirate["crew_id"]]
            crew.setdefault("raider_plan", None)
            if crew["raider_plan"] and crew["raider_plan"]["status"] == "underway":
                continue
            captain_id = crew["captain_id"]
            adjacent_routes = world.geography.adjacency[location["id"]]
            for route in adjacent_routes:
                self._record_raider_chart_intel(world, captain_id, location["id"], route)
            for merchant_id, merchant in sorted(world.economy.merchants.items()):
                merchant_ship_id = merchant["ship_id"]
                ship = world.entities.get(merchant_ship_id)
                if (not ship or not world.ship_is_commandable(merchant_ship_id)
                        or ship.get("legal_owner") != {"kind": "merchant", "id": merchant_id}):
                    continue
                if world.location_of(merchant_ship_id) == location:
                    self._record_raider_vessel_intel(
                        world, captain_id, merchant_id, merchant_ship_id, merchant)

            believed_targets = []
            for merchant_id, merchant in sorted(world.economy.merchants.items()):
                merchant_ship_id = merchant["ship_id"]
                ship = world.entities.get(merchant_ship_id)
                if (not ship or not world.ship_is_commandable(merchant_ship_id)
                        or ship.get("legal_owner") != {"kind": "merchant", "id": merchant_id}):
                    continue
                cargo_belief = world.decisions.belief(
                    captain_id, merchant_ship_id, "estimated_cargo_value_milli")
                defense_belief = world.decisions.belief(
                    captain_id, merchant_ship_id, "estimated_vessel_strength")
                location_belief = world.decisions.belief(
                    captain_id, merchant_ship_id, "last_known_location")
                if (not self._fresh_raider_belief(cargo_belief, world.clock.tick, max_age)
                        or not self._fresh_raider_belief(
                            defense_belief, world.clock.tick, max_age)
                        or not self._fresh_raider_belief(
                            location_belief, world.clock.tick, max_age)):
                    continue
                previous_plan = crew.get("raider_plan")
                if (isinstance(previous_plan, dict)
                        and previous_plan.get("target_ship_id") == merchant_ship_id
                        and previous_plan.get("status") in {"escaped", "expired", "invalidated"}):
                    previous_result = world.event_log.get(
                        previous_plan["resolution_event_id"])
                    if max(cargo_belief["observed_tick"],
                           defense_belief["observed_tick"],
                           location_belief["observed_tick"]) <= previous_result.tick:
                        continue
                last_location = location_belief["value"]
                if not isinstance(last_location, dict):
                    continue
                kind = last_location.get("kind")
                if kind == "island" and last_location.get("id") in world.geography.islands:
                    route_options = [(route, last_location["id"])
                                     for route in world.geography.adjacency[last_location["id"]]]
                elif kind == "route" and last_location.get("id") in world.geography.routes:
                    route = world.geography.routes[last_location["id"]]
                    destination = last_location.get("to")
                    route_options = ([(route, destination)]
                                     if destination in world.geography.islands else [])
                else:
                    route_options = []
                intel_age = max(0, world.clock.tick - cargo_belief["observed_tick"])
                for route, destination in route_options:
                    patrol_belief = world.decisions.belief(
                        captain_id, route.id, "estimated_patrol_density_milli")
                    if not self._fresh_raider_belief(patrol_belief, world.clock.tick, max_age):
                        continue
                    estimated_cargo = max(0, cargo_belief["value"])
                    estimated_patrol = max(0, patrol_belief["value"])
                    confidence = min(cargo_belief["confidence_permille"],
                                     patrol_belief["confidence_permille"])
                    try:
                        path = world.geography.shortest_path(
                            location["id"], destination,
                            edge_cost=lambda edge: world.travel._leg_hours(
                                edge, pirate, pirate["condition"]),
                            blocked_routes=world.travel.closed_routes)
                    except ValueError as exc:
                        if "No route" not in str(exc) and "blocked" not in str(exc).lower():
                            raise
                        continue
                    expected_hours = sum(world.travel._leg_hours(
                        edge, pirate, pirate["condition"]) for edge in path)
                    expected_days = (expected_hours + world.config["time"]["hours_per_day"] - 1) \
                        // world.config["time"]["hours_per_day"]
                    roster_size = len(crew["roster"])
                    rules = self.data["rules"]
                    expected_supplies = {
                        "grain": expected_days * rules["daily_food_milli_per_member"] * roster_size,
                        "water": expected_days * rules["daily_water_milli_per_member"] * roster_size,
                        "rum": expected_days * rules["daily_rum_milli_per_member"] * roster_size,
                    }
                    supply_budget = world.economy.ship_supplies[pirate_id]
                    provision_cost = sum(
                        amount * world.economy.goods[good]["base_price_silver"]
                        for good, amount in expected_supplies.items())
                    provision_factor = min((1000 if amount == 0 else
                                             supply_budget.get(good, 0) * 1000 // amount)
                                            for good, amount in expected_supplies.items())
                    raider_power = max(1, world.crew_ships.combat_power(
                        world, pirate_id)["total"])
                    estimated_defense = max(1, defense_belief["value"])
                    capability_factor = max(500, min(
                        1500, raider_power * 1000 // estimated_defense))
                    competition = sum(
                        1 for other_crew in self.crews.values()
                        if isinstance(other_crew.get("raider_plan"), dict)
                        and other_crew["raider_plan"].get("status") == "underway"
                        and other_crew["raider_plan"].get("target_ship_id") == merchant_ship_id)
                    distance_factor = 72000 // (72 + expected_hours)
                    belief_score = (estimated_cargo * confidence // 1000
                                    * 1000 // (1000 + estimated_patrol)
                                    * capability_factor // 1000
                                    * distance_factor // 1000
                                    * provision_factor // 1000
                                    * 1000 // (1000 + competition * 500))
                    if belief_score <= 0:
                        continue
                    believed_targets.append((belief_score, -confidence, intel_age,
                                             merchant_id, route.id, merchant_ship_id,
                                             destination, route, estimated_patrol,
                                             estimated_cargo, last_location,
                                             cargo_belief, location_belief,
                                             patrol_belief, expected_hours,
                                             expected_supplies, provision_cost,
                                             provision_factor, competition,
                                             defense_belief, capability_factor))
            if not believed_targets:
                continue
            (belief_score, negative_confidence, intel_age, merchant_id, route_id,
             merchant_ship_id, destination, route, estimated_patrol, estimated_cargo,
             last_location, cargo_belief, location_belief, patrol_belief,
             expected_hours, expected_supplies, provision_cost, provision_factor,
             competition, defense_belief, capability_factor) = min(
                 believed_targets, key=lambda row: (-row[0], row[1], row[2], row[3], row[4]))
            observed = world.event_log.get(pirate.get("created_event_id")
                                            or pirate.get("last_event_id"))
            merchant = world.economy.merchants[merchant_id]
            causes = list(dict.fromkeys((
                observed.id, cargo_belief["source"]["event_id"],
                location_belief["source"]["event_id"],
                patrol_belief["source"]["event_id"])))
            target_event = world.event_log.append(
                tick=world.clock.tick, event_type="RAIDER_TARGET_SELECTED",
                location=route.id,
                actors=[{"role": "raider_captain", "entity_id": captain_id}],
                targets=[{"role": "merchant_vessel", "entity_id": merchant_ship_id}],
                data={"merchant_id": merchant_id, "route_id": route_id,
                      "last_known_location": last_location,
                      "intel_age_ticks": intel_age,
                      "cargo_confidence_permille": cargo_belief["confidence_permille"],
                      "patrol_confidence_permille": patrol_belief["confidence_permille"],
                      "defense_confidence_permille": defense_belief["confidence_permille"],
                      "estimated_patrol_density_milli": estimated_patrol,
                      "estimated_cargo_value_milli": estimated_cargo,
                      "estimated_vessel_strength": defense_belief["value"],
                      "belief_score": belief_score,
                      "capability_factor_permille": capability_factor,
                      "expected_travel_hours": expected_hours,
                      "expected_supplies_milli": expected_supplies,
                      "estimated_provision_cost_milli": provision_cost,
                      "provision_availability_permille": provision_factor,
                      "active_competing_commitments": competition,
                      "decision_day": day},
                caused_by=causes,
                causes_tags=["piracy_targeting", "belief_driven", "low_patrol_high_cargo"],
                magnitude=0.3)
            self._commit_raider_pursuit(
                world, crew, pirate_id, merchant_id, merchant_ship_id,
                route_id, destination, last_location, target_event.id)

    def _commit_raider_pursuit(self, world, crew: dict[str, Any], raider_ship_id: str,
                               merchant_id: str, target_ship_id: str, route_id: str,
                               destination: str, last_location: dict[str, Any],
                               selected_event_id: str) -> None:
        """Persist a bounded pursuit before sailing or attempting same-port contact."""
        ship = world.entities[raider_ship_id]
        location = world.location_of(raider_ship_id)
        origin = location.get("id") if location.get("kind") == "island" else None
        expected_routes = []
        expected_hours = 0
        failure_reason = None
        if origin is None:
            failure_reason = "raider_not_at_island"
        elif origin != destination:
            try:
                path = world.geography.shortest_path(
                    origin, destination,
                    edge_cost=lambda route: world.travel._leg_hours(
                        route, ship, ship["condition"]),
                    blocked_routes=world.travel.closed_routes,
                )
                expected_routes = [route.id for route in path]
                expected_hours = sum(world.travel._leg_hours(
                    route, ship, ship["condition"]) for route in path)
            except ValueError as exc:
                if "No path" not in str(exc) and "blocked" not in str(exc).lower():
                    raise
                failure_reason = "route_unavailable"
        hours_per_day = world.config["time"]["hours_per_day"]
        hours_per_tick = world.config["time"]["hours_per_tick"]
        expected_days = ((expected_hours + hours_per_day - 1) // hours_per_day)
        roster_size = len(crew["roster"])
        rules = self.data["rules"]
        expected_supplies = {
            "grain": expected_days * rules["daily_food_milli_per_member"] * roster_size,
            "water": expected_days * rules["daily_water_milli_per_member"] * roster_size,
            "rum": expected_days * rules["daily_rum_milli_per_member"] * roster_size,
        }
        budget = {good: world.economy.ship_supplies[raider_ship_id].get(good, 0)
                  for good in expected_supplies}
        if (failure_reason is None
                and any(budget[good] < quantity
                        for good, quantity in expected_supplies.items())):
            failure_reason = "insufficient_pursuit_provisions"
        deadline_tick = (world.clock.tick
                         + (expected_hours + 30 * hours_per_day) // hours_per_tick)
        commitment = world.event_log.append(
            tick=world.clock.tick, event_type="RAIDER_PURSUIT_COMMITTED",
            location=location["id"],
            actors=[{"role": "raider_vessel", "entity_id": raider_ship_id}],
            targets=[{"role": "merchant_vessel", "entity_id": target_ship_id}],
            data={"merchant_id": merchant_id, "route_id": route_id,
                  "destination": destination, "last_known_location": last_location,
                  "expected_route_ids": expected_routes,
                  "expected_duration_hours": expected_hours,
                  "expected_supplies_milli": expected_supplies,
                  "pursuit_budget_milli": budget, "deadline_tick": deadline_tick},
            caused_by=[selected_event_id],
            causes_tags=["raider_pursuit", "bounded_commitment"], magnitude=0.2)
        plan = {
            "merchant_id": merchant_id, "target_ship_id": target_ship_id,
            "raider_ship_id": raider_ship_id, "route_id": route_id,
            "destination": destination, "last_known_location": last_location,
            "selected_event_id": selected_event_id,
            "commitment_event_id": commitment.id, "voyage_event_id": None,
            "expected_duration_hours": expected_hours,
            "expected_supplies_milli": expected_supplies,
            "pursuit_budget_milli": budget, "deadline_tick": deadline_tick,
            "status": "underway", "resolution_event_id": None,
        }
        crew["raider_plan"] = plan
        crew["last_event_id"] = commitment.id
        if failure_reason:
            self._resolve_raider_plan(
                world, crew, "aborted", caused_by=[commitment.id], reason=failure_reason)
            return
        if origin == destination:
            # Do not force combat from a strategic selection. The ordinary
            # encounter detector decides whether co-located ships make contact.
            return
        try:
            voyage_id = world.travel.start_voyage(
                world, raider_ship_id, destination, caused_by=commitment.id)
        except ValueError as exc:
            if "No path" not in str(exc) and "blocked" not in str(exc).lower():
                raise
            self._resolve_raider_plan(
                world, crew, "aborted", caused_by=[commitment.id],
                reason="route_became_unavailable")
            return
        plan["voyage_event_id"] = voyage_id
        transit = world.travel.transits[raider_ship_id]
        plan["deadline_tick"] = (transit["arrival_tick"]
                                 + 30 * hours_per_day // hours_per_tick)
        world.entities[raider_ship_id]["last_event_id"] = voyage_id

    def _resolve_raider_contact(self, world, crew: dict[str, Any], *,
                                caused_by: list[str]) -> None:
        plan = crew.get("raider_plan")
        if not plan or plan.get("status") != "underway":
            return
        raider_ship_id = plan["raider_ship_id"]
        target_ship_id = plan["target_ship_id"]
        if (raider_ship_id not in world.entities
                or not world.ship_is_commandable(raider_ship_id)
                or target_ship_id not in world.entities
                or not world.ship_is_commandable(target_ship_id)):
            self._resolve_raider_plan(
                world, crew, "invalidated", caused_by=caused_by,
                reason="participant_no_longer_commandable")
            return
        raider_location = world.location_of(raider_ship_id)
        target_location = world.location_of(target_ship_id)
        overlap = world.encounters._overlap(raider_location, target_location)
        if overlap is None:
            self._resolve_raider_plan(
                world, crew, "escaped", caused_by=caused_by,
                reason="target_not_at_last_known_location")
            return
        encounter_id = world.encounters.resolve(
            world, raider_ship_id, target_ship_id,
            caused_by=list(dict.fromkeys(caused_by)), location=overlap["location"])
        encounter = world.event_log.get(encounter_id)
        self._resolve_raider_plan(
            world, crew, self._raider_encounter_outcome(
                world, raider_ship_id, target_ship_id, encounter),
            caused_by=[encounter_id], reason="encounter_resolved")
        pair = world.encounters._pair_key(raider_ship_id, target_ship_id)
        day = world.clock.tick // (world.config["time"]["hours_per_day"]
                                  // world.config["time"]["hours_per_tick"])
        world.encounters.pair_last_encounter_day[pair] = day

    def resolve_raider_plans(self, world, day: int,
                             encounter_event_ids: list[str] | tuple[str, ...] = ()) -> None:
        """Resolve committed plans only from actual contact, invalidation or timeout."""
        events = [world.event_log.get(event_id) for event_id in encounter_event_ids]
        for crew_id in sorted(self.crews):
            crew = self.crews[crew_id]
            plan = crew.get("raider_plan")
            if not isinstance(plan, dict) or plan.get("status") != "underway":
                continue
            raider_id = plan["raider_ship_id"]
            target_id = plan["target_ship_id"]
            if (raider_id not in world.entities
                    or not world.ship_is_commandable(raider_id)
                    or target_id not in world.entities
                    or not world.ship_is_commandable(target_id)):
                self._resolve_raider_plan(
                    world, crew, "invalidated", caused_by=[],
                    reason="pursuit_participant_lost_command")
                continue
            encounter = next((event for event in events
                              if event.type == "ENCOUNTER_RESOLVED"
                              and {actor.get("entity_id") for actor in event.actors
                                   if actor.get("role") == "combatant"}
                              == {raider_id, target_id}
                              and event.tick >= world.event_log.get(
                                  plan["commitment_event_id"]).tick), None)
            if encounter is not None:
                outcome = self._raider_encounter_outcome(
                    world, raider_id, target_id, encounter)
                self._resolve_raider_plan(
                    world, crew, outcome, caused_by=[encounter.id],
                    reason="causally_linked_encounter")
                continue
            if world.clock.tick >= plan["deadline_tick"]:
                self._resolve_raider_plan(
                    world, crew, "expired", caused_by=[],
                    reason="pursuit_deadline_reached")
                continue
            if raider_id in world.travel.transits:
                continue
            if not plan.get("voyage_event_id"):
                # A same-island commitment waits for the normal encounter system.
                continue
            raider_location = world.location_of(raider_id)
            target_location = world.location_of(target_id)
            if world.encounters._overlap(raider_location, target_location) is None:
                self._resolve_raider_plan(
                    world, crew, "escaped", caused_by=[plan["voyage_event_id"]],
                    reason="target_left_last_known_destination")

    @staticmethod
    def _raider_encounter_outcome(world, raider_ship_id: str, target_ship_id: str,
                                  encounter) -> str:
        if (target_ship_id not in world.entities
                or world.entities[target_ship_id].get("sunk_tick") is not None
                or not world.ship_is_commandable(target_ship_id)):
            return "intercepted"
        if encounter.data.get("outcome") in {"ship_captured", "ship_sunk", "surrender"}:
            return "intercepted"
        if encounter.data.get("outcome") == "one_side_flees":
            return ("escaped" if encounter.data.get("stances", {}).get(
                target_ship_id) == "flee" else "aborted")
        return "contact"

    def _resolve_raider_plan(self, world, crew: dict[str, Any], outcome: str, *,
                             caused_by: list[str], reason: str) -> str | None:
        plan = crew.get("raider_plan")
        if (not isinstance(plan, dict) or plan.get("status") != "underway"
                or outcome not in _RAIDER_PLAN_TERMINAL):
            return None
        cause_ids = list(dict.fromkeys((plan["selected_event_id"],
                                        plan["commitment_event_id"], *caused_by)))
        event = world.event_log.append(
            tick=world.clock.tick, event_type="RAIDER_PURSUIT_RESOLVED",
            location=world.location_of(plan["raider_ship_id"])["id"]
            if plan["raider_ship_id"] in world.entities else None,
            actors=[{"role": "raider_vessel", "entity_id": plan["raider_ship_id"]}],
            targets=[{"role": "merchant_vessel", "entity_id": plan["target_ship_id"]}],
            data={"outcome": outcome, "reason": reason,
                  "merchant_id": plan["merchant_id"],
                  "selected_event_id": plan["selected_event_id"],
                  "commitment_event_id": plan["commitment_event_id"],
                  "voyage_event_id": plan["voyage_event_id"]},
            caused_by=cause_ids, causes_tags=["raider_pursuit", outcome], magnitude=0.2)
        plan["status"] = outcome
        plan["resolution_event_id"] = event.id
        crew["last_event_id"] = event.id
        if plan["raider_ship_id"] in world.entities:
            world.entities[plan["raider_ship_id"]]["last_event_id"] = event.id
        return event.id

    @staticmethod
    def _fresh_raider_belief(record: dict[str, Any] | None, tick: int,
                             max_age_ticks: int) -> bool:
        return bool(record and type(record.get("observed_tick")) is int
                    and 0 <= tick - record["observed_tick"] <= max_age_ticks
                    and type(record.get("confidence_permille")) is int
                    and record["confidence_permille"] > 0)

    @staticmethod
    def _raider_estimate(world, observer_id: str, subject_id: str, attribute: str,
                         actual: int) -> tuple[int, int]:
        """Use bounded seeded estimation, with a stream unique to observer and subject."""
        maximum = world.decisions.ai_data["belief_rules"]["max_distortion_permille"]
        rng = world.rng.stream(
            f"raider-estimate:{observer_id}:{subject_id}:{attribute}:{world.clock.tick}")
        error = rng.randint(-maximum, maximum) if maximum else 0
        return max(0, actual * (1000 + error) // 1000), error

    def _record_raider_chart_intel(self, world, captain_id: str, island_id: str,
                                   route) -> None:
        current = world.decisions.belief(
            captain_id, route.id, "estimated_patrol_density_milli")
        max_age = _RAIDER_INTEL_MAX_AGE_DAYS * (
            world.config["time"]["hours_per_day"]
            // world.config["time"]["hours_per_tick"])
        if self._fresh_raider_belief(current, world.clock.tick, max_age):
            return
        estimated, error = self._raider_estimate(
            world, captain_id, route.id, "patrol_density_milli",
            int(route.patrol_density * 1000))
        cause = world.event_log.by_type("WORLD_GENERATED")[0]
        chart = world.event_log.append(
            tick=world.clock.tick, event_type="RAIDER_CHART_REVIEWED", location=island_id,
            actors=[{"role": "chart_reader", "entity_id": captain_id}],
            data={"route_id": route.id, "estimated_patrol_density_milli": estimated,
                  "estimation_error_permille": error,
                  "confidence_permille": 1000, "observation_tick": world.clock.tick},
            caused_by=[cause.id], causes_tags=["raider_intelligence", "charted_route"],
            magnitude=0.05)
        world.decisions.set_belief(
            world, captain_id, route.id, "estimated_patrol_density_milli", estimated,
            source_event_id=chart.id, confidence_permille=1000, source_kind="direct")

    def _record_raider_vessel_intel(self, world, captain_id: str, merchant_id: str,
                                    merchant_ship_id: str,
                                    merchant: dict[str, Any]) -> None:
        observed_location = world.location_of(merchant_ship_id)
        cargo_value = sum(
            quantity * world.economy.goods[goods_id]["base_price_silver"]
            for goods_id, quantity in merchant["cargo_milli"].items())
        estimated, error = self._raider_estimate(
            world, captain_id, merchant_ship_id, "cargo_value_milli", cargo_value)
        defense_power = world.crew_ships.combat_power(world, merchant_ship_id)["total"]
        estimated_defense, defense_error = self._raider_estimate(
            world, captain_id, merchant_ship_id, "vessel_strength", defense_power)
        cause_ids = [merchant.get("last_event_id"),
                     world.entities[merchant_ship_id].get("last_event_id")]
        causes = list(dict.fromkeys(event_id for event_id in cause_ids if event_id))
        event = world.event_log.append(
            tick=world.clock.tick, event_type="RAIDER_INTEL_OBSERVED",
            location=observed_location["id"],
            actors=[{"role": "observer", "entity_id": captain_id}],
            targets=[{"role": "observed_merchant_vessel", "entity_id": merchant_ship_id}],
            data={"merchant_id": merchant_id, "estimated_cargo_value_milli": estimated,
                  "estimation_error_permille": error,
                  "estimated_vessel_strength": estimated_defense,
                  "defense_estimation_error_permille": defense_error,
                  "last_known_location": observed_location,
                  "confidence_permille": 1000, "observation_tick": world.clock.tick},
            caused_by=causes, causes_tags=["raider_intelligence", "direct_observation"],
            magnitude=0.08)
        world.decisions.set_belief(
            world, captain_id, merchant_ship_id, "estimated_cargo_value_milli", estimated,
            source_event_id=event.id, confidence_permille=1000, source_kind="direct")
        world.decisions.set_belief(
            world, captain_id, merchant_ship_id, "estimated_vessel_strength",
            estimated_defense, source_event_id=event.id,
            confidence_permille=900, source_kind="direct")
        world.decisions.set_belief(
            world, captain_id, merchant_ship_id, "last_known_location", observed_location,
            source_event_id=event.id, confidence_permille=1000, source_kind="direct")
        world.entities[captain_id]["last_event_id"] = event.id

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
                location=self.crew_location(world, crew)["id"],
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
                location=self.crew_location(world, crew)["id"],
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
            ship_id = crew["ship_ids"][0] if crew["ship_ids"] else None
            event = world.event_log.append(
                tick=world.clock.tick, event_type="DESERTION",
                location=self.crew_location(world, crew)["id"],
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
            person["location"] = self.crew_location(world, crew)
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
            if crew["id"] != crew_id:
                raise ValueError(f"Invalid crew identity: {crew_id}")
            plan = crew.get("raider_plan")
            if plan is not None:
                plan_keys = {
                    "merchant_id", "target_ship_id", "raider_ship_id", "route_id",
                    "destination", "last_known_location", "selected_event_id",
                    "commitment_event_id", "voyage_event_id",
                    "expected_duration_hours", "expected_supplies_milli",
                    "pursuit_budget_milli", "deadline_tick", "status",
                    "resolution_event_id",
                }
                invalid_plan = (
                    not isinstance(plan, dict) or set(plan) != plan_keys
                    or plan.get("status") not in _RAIDER_PLAN_TERMINAL | {"underway"}
                )
                if not invalid_plan:
                    invalid_plan = (
                        any(not isinstance(plan.get(key), str) or not plan[key]
                            for key in ("merchant_id", "target_ship_id", "raider_ship_id",
                                        "route_id", "destination", "selected_event_id",
                                        "commitment_event_id"))
                        or (plan["voyage_event_id"] is not None
                            and (not isinstance(plan["voyage_event_id"], str)
                                 or not plan["voyage_event_id"]))
                        or type(plan["expected_duration_hours"]) is not int
                        or plan["expected_duration_hours"] < 0
                        or type(plan["deadline_tick"]) is not int or plan["deadline_tick"] < 0
                        or not isinstance(plan["last_known_location"], dict)
                        or not isinstance(plan["expected_supplies_milli"], dict)
                        or not isinstance(plan["pursuit_budget_milli"], dict)
                        or set(plan["expected_supplies_milli"]) != {"grain", "water", "rum"}
                        or set(plan["pursuit_budget_milli"]) != {"grain", "water", "rum"}
                        or any(type(value) is not int or value < 0
                               for budget in (plan["expected_supplies_milli"],
                                              plan["pursuit_budget_milli"])
                               for value in budget.values())
                        or (plan["status"] == "underway"
                            and plan["resolution_event_id"] is not None)
                        or (plan["status"] != "underway"
                            and (not isinstance(plan["resolution_event_id"], str)
                                 or not plan["resolution_event_id"]))
                    )
                if invalid_plan:
                    raise ValueError(f"Invalid persisted raider pursuit plan: {crew_id}")
            if crew["ship_ids"]:
                ship_id = crew["ship_ids"][0]
                if ship_id not in world.entities:
                    raise ValueError(f"Crew vessel is not active: {crew_id}")
                ship = world.entities[ship_id]
                if (ship.get("crew_id") != crew_id
                        or ship.get("supplies") != world.economy.ship_supplies[ship["id"]]):
                    raise ValueError(f"Ship and crew supply state disagree: {crew_id}")
                merchant = next((row for row in world.economy.merchants.values()
                                 if row["ship_id"] == ship["id"]), None)
                if merchant is not None and ship.get("cargo") != merchant["cargo_milli"]:
                    raise ValueError(f"Ship and merchant cargo state disagree: {ship['id']}")
                stranded_location = None
            else:
                stranded_location = self.crew_location(world, crew)
            if len(set(crew["roster"])) != len(crew["roster"]):
                raise ValueError(f"Duplicate crew roster member: {crew_id}")
            for character_id in crew["roster"]:
                character = world.entities.get(character_id)
                expected_ship_id = crew["ship_ids"][0] if crew["ship_ids"] else None
                missing_at_sea = character.get("missing_at_sea") if character else None
                stranded_location_matches = (
                    stranded_location is None
                    or character.get("location") == stranded_location
                    or (isinstance(missing_at_sea, dict)
                        and character.get("location")
                        == missing_at_sea.get("last_known_location")))
                if (not character or character.get("kind") != "character"
                        or character.get("crew_id") != crew_id
                        or character.get("ship_id") != expected_ship_id
                        or not stranded_location_matches):
                    raise ValueError(f"Crew roster member has inconsistent links: {character_id}")
            if crew["captain_id"] not in crew["roster"] or crew["roles"].get("captain") != crew["captain_id"]:
                raise ValueError(f"Crew captain is not an active roster member: {crew_id}")
            plan = crew.get("raider_plan")
            if plan is not None:
                for event_key in ("selected_event_id", "commitment_event_id",
                                  "voyage_event_id", "resolution_event_id"):
                    event_id = plan.get(event_key)
                    if event_id is not None:
                        world.event_log.get(event_id)
                if (plan["status"] == "underway"
                        and (plan["raider_ship_id"] not in world.entities
                             or plan["raider_ship_id"] not in crew["ship_ids"])):
                    raise ValueError(f"Active raider plan is detached from its vessel: {crew_id}")
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
                elif event.type == "CREW_ASSETS_TRANSFERRED":
                    if event.data.get("to_crew_id") == crew_id:
                        treasury_adjustment += event.data.get("treasury_silver", 0)
                    if event.data.get("from_crew_id") == crew_id:
                        treasury_adjustment -= event.data.get("treasury_silver", 0)
                elif event.type == "RANSOM_PAID":
                    if any(actor.get("entity_id") == crew_id for actor in event.actors):
                        treasury_adjustment -= event.data.get("silver", 0)
                    if any(target.get("entity_id") == crew_id for target in event.targets):
                        treasury_adjustment += event.data.get("silver", 0)
                elif (event.type == "CONTRABAND_SEIZED"
                      and event.data.get("crew_id") == crew_id):
                    fine = event.data.get("fine_silver", -1)
                    if type(fine) is not int or fine < 0:
                        raise ValueError(f"Invalid contraband enforcement fine: {event.id}")
                    treasury_adjustment -= fine
                elif (event.type == "AGENT_ACTION"
                      and event.data.get("action_id") == "hunt_rival"
                      and event.data.get("crew_id") == crew_id):
                    cost = event.data.get("silver_cost", 0)
                    if type(cost) is not int or cost < 0:
                        raise ValueError(f"Invalid crew action expense: {event.id}")
                    treasury_adjustment -= cost
            if len(fund_events) != 1:
                raise ValueError(f"Crew funding history is missing or duplicated: {crew_id}")
            funded_silver = fund_events[0].data.get("funded_silver")
            paid_silver = sum(event.data.get("silver", -1) for event in wage_events)
            if (type(funded_silver) is not int or funded_silver < 0
                    or paid_silver != crew["wages_paid_silver"]
                    or funded_silver - paid_silver + treasury_adjustment != crew["treasury_silver"]):
                raise ValueError(f"Crew treasury disagrees with its funding/payroll events: {crew_id}")
            expected_wallets = {}
            for event in world.event_log:
                if event.type == "WAGES_PAID" and event.data.get("crew_id") == crew_id:
                    shares = event.data.get("member_shares")
                    if (not isinstance(shares, Mapping)
                            or sum(shares.values()) != event.data.get("silver")):
                        raise ValueError(f"Invalid crew wage distribution event: {event.id}")
                    for member_id, amount in shares.items():
                        expected_wallets[member_id] = expected_wallets.get(member_id, 0) + amount
                elif event.type in {"CREW_MEMBER_DEFECTED", "CREW_MEMBER_TRANSFERRED",
                                    "CREW_WALLET_TRANSFERRED"}:
                    member_id = event.data.get("character_id")
                    if member_id is None and event.actors:
                        member_id = event.actors[0].get("entity_id")
                    if event.data.get("from_crew_id") == crew_id:
                        expected_wallets.pop(member_id, None)
                    elif event.data.get("to_crew_id") == crew_id:
                        wallet_silver = event.data.get("wallet_silver", 0)
                        if event.type == "CREW_MEMBER_DEFECTED":
                            expected_wallets[member_id] = (
                                expected_wallets.get(member_id, 0) + wallet_silver)
                        else:
                            expected_wallets[member_id] = wallet_silver
            if any(expected_wallets.get(member_id, 0) != balance
                   for member_id, balance in account["member_wallets"].items()):
                raise ValueError(f"Crew wallets disagree with payroll history: {crew_id}")
            arrears = sum(event.data.get("unpaid_silver", -1) for event in unpaid_events)
            for event in world.event_log.by_type("CREW_ASSETS_TRANSFERRED"):
                if event.data.get("to_crew_id") == crew_id:
                    arrears += event.data.get("arrears_silver", 0)
                if event.data.get("from_crew_id") == crew_id:
                    arrears -= event.data.get("arrears_silver", 0)
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
        deadline_keys = {"merchant", "pirate", "privateer", "marine", "promotion", "legend"}
        if (set(self.actor_deadlines) - deadline_keys
                or any(type(day) is not int or day < 0
                       for day in self.actor_deadlines.values())):
            raise ValueError("Invalid persisted actor opportunity deadlines")
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
                    or not isinstance(crew.get("ship_ids"), list)
                    or len(crew["ship_ids"]) not in {0, 1}
                    or (not crew["ship_ids"] and "stranded_location" not in crew)
                    or (crew["ship_ids"] and "stranded_location" in crew)
                    or not isinstance(crew.get("last_event_id"), str)):
                raise ValueError(f"Invalid crew record: {crew_id}")

    def snapshot(self) -> dict[str, Any]:
        return {"digest": self.digest,
                "crews": {key: self.crews[key] for key in sorted(self.crews)},
                "last_updated_day": self.last_updated_day,
                "actor_deadlines": dict(sorted(self.actor_deadlines.items()))}

    def _crew(self, crew_id: str) -> dict[str, Any]:
        try:
            return self.crews[crew_id]
        except KeyError as exc:
            raise ValueError(f"Unknown crew: {crew_id}") from exc
