"""Deterministic spatial encounter detection and multi-phase ship combat."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sim.core.ownership import ship_is_commandable


ENCOUNTERS_PATH = Path(__file__).resolve().parents[1] / "data" / "encounters.json"


def load_encounter_data(path: str | Path = ENCOUNTERS_PATH) -> tuple[dict[str, Any], str]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load encounter data {path}: {exc}") from exc
    validate_encounter_data(data)
    digest = hashlib.sha256(json.dumps(
        data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    return data, digest


def validate_encounter_data(data: Any) -> None:
    if (not isinstance(data, dict) or set(data) != {"schema_version", "rules"}
            or type(data["schema_version"]) is not int or data["schema_version"] != 1):
        raise ValueError("Invalid encounter catalog schema")
    keys = {
        "base_detection_basis_points", "lookout_basis_points_per_point",
        "weather_detection_modifier_permille", "route_signal_range_permille",
        "island_detection_bonus_basis_points", "encounter_cooldown_days",
        "max_gunnery_rounds", "gunnery_damage_base", "gunnery_damage_per_100_firepower",
        "gunnery_variance_percent", "boarding_threshold_basis_points",
        "boarding_damage_base", "escape_base_basis_points",
        "escape_mobility_basis_points_per_point",
        "surrender_morale_threshold", "surrender_basis_points",
        "ship_capture_basis_points", "defection_morale_threshold",
        "defection_basis_points", "captain_wound_basis_points",
        "captain_death_basis_points", "captain_capture_basis_points",
        "loot_basis_points", "reputation_shift",
        "ransom_base_silver",
        "failed_flee_rigging_damage", "failed_flee_morale_loss",
    }
    rules = data["rules"]
    if (not isinstance(rules, dict) or set(rules) != keys
            or any(type(value) is not int or value < 0 for value in rules.values())
            or rules["base_detection_basis_points"] > 10000
            or rules["weather_detection_modifier_permille"] > 1000
            or rules["route_signal_range_permille"] > 1000
            or rules["island_detection_bonus_basis_points"] > 10000
            or rules["encounter_cooldown_days"] < 1
            or not 1 <= rules["max_gunnery_rounds"] <= 10
            or rules["gunnery_variance_percent"] > 100
            or rules["boarding_threshold_basis_points"] > 10000
            or rules["escape_base_basis_points"] > 10000
            or rules["surrender_morale_threshold"] > 100
            or rules["surrender_basis_points"] > 10000
            or rules["ship_capture_basis_points"] > 10000
            or rules["defection_morale_threshold"] > 100
            or rules["defection_basis_points"] > 10000
            or rules["captain_wound_basis_points"] > 10000
            or rules["captain_death_basis_points"] > 10000
            or rules["captain_capture_basis_points"] > 10000
            or rules["loot_basis_points"] > 10000
            or rules["failed_flee_rigging_damage"] < 1
            or rules["failed_flee_morale_loss"] > 100):
        raise ValueError("Invalid encounter rules")


class EncounterSystem:
    def __init__(self, *, digest: str, last_updated_day: int = 0,
                 pair_last_encounter_day: dict[str, int] | None = None,
                 encounter_count: int = 0,
                 prisoners: dict[str, dict[str, Any]] | None = None,
                 reputation: dict[str, int] | None = None,
                 bounty_silver: dict[str, int] | None = None) -> None:
        self.data, actual = load_encounter_data()
        if digest != actual:
            raise ValueError("Encounter catalog changed since snapshot creation")
        self.digest = digest
        self.last_updated_day = last_updated_day
        self.pair_last_encounter_day = dict(pair_last_encounter_day or {})
        self.encounter_count = encounter_count
        self.prisoners = json.loads(json.dumps(prisoners or {}, sort_keys=True))
        self.reputation = dict(reputation or {})
        self.bounty_silver = dict(bounty_silver or {})
        self.validate_state()

    @classmethod
    def create(cls) -> "EncounterSystem":
        _, digest = load_encounter_data()
        return cls(digest=digest)

    @classmethod
    def restore(cls, snapshot: dict[str, Any]) -> "EncounterSystem":
        required = {"digest", "last_updated_day", "pair_last_encounter_day", "encounter_count",
                    "prisoners", "reputation", "bounty_silver"}
        if (not isinstance(snapshot, dict) or set(snapshot) != required
                or not isinstance(snapshot["pair_last_encounter_day"], dict)
                or not isinstance(snapshot["prisoners"], dict)):
            raise ValueError("Invalid encounter snapshot")
        return cls(**snapshot)

    def update_day(self, day: int, world) -> list[str]:
        if type(day) is not int or day != self.last_updated_day + 1:
            raise ValueError("Encounter days must be updated consecutively")
        ships = sorted(entity_id for entity_id, row in world.entities.items()
                       if row.get("kind") == "ship"
                       and ship_is_commandable(world, entity_id))
        locations = {ship_id: world.location_of(ship_id) for ship_id in ships}
        emitted = []
        for index, first_id in enumerate(ships):
            if first_id not in world.entities:
                continue
            for second_id in ships[index + 1:]:
                if first_id not in world.entities or second_id not in world.entities:
                    continue
                if (not ship_is_commandable(world, first_id)
                        or not ship_is_commandable(world, second_id)):
                    continue
                first_crew = world.crew_ships.crews[world.entities[first_id]["crew_id"]]
                second_crew = world.crew_ships.crews[world.entities[second_id]["crew_id"]]
                if (first_crew["captain_id"] not in world.entities
                        or second_crew["captain_id"] not in world.entities
                        or not any(member_id in world.entities for member_id in first_crew["roster"])
                        or not any(member_id in world.entities for member_id in second_crew["roster"])):
                    continue
                key = self._pair_key(first_id, second_id)
                if day - self.pair_last_encounter_day.get(key, -10**9) < \
                        self.data["rules"]["encounter_cooldown_days"]:
                    continue
                first_location = locations[first_id]
                second_location = locations[second_id]
                overlap = self._overlap(first_location, second_location)
                if overlap is None:
                    continue
                first_chance = self._detection_chance(world, first_id, overlap)
                second_chance = self._detection_chance(world, second_id, overlap)
                rng = world.rng.stream(f"encounter-detect:{day}:{first_id}:{second_id}")
                first_detects = rng.randbelow(10000) < first_chance
                second_detects = rng.randbelow(10000) < second_chance
                if not first_detects and not second_detects:
                    continue
                event_id = self.resolve(
                    world, first_id, second_id, caused_by=self._last_cause(world, first_id, second_id),
                    location=overlap["location"], surprise_by=(
                        first_id if first_detects and not second_detects else
                        second_id if second_detects and not first_detects else None),
                )
                self.pair_last_encounter_day[key] = day
                emitted.append(event_id)
                for participant_id in (first_id, second_id):
                    if participant_id in world.entities:
                        locations[participant_id] = world.location_of(participant_id)
                    else:
                        locations.pop(participant_id, None)
        self.last_updated_day = day
        world.crew_ships.resolve_raider_plans(world, day, emitted)
        self._review_prisoners(day, world)
        return emitted

    def update_prisoner_provisions(self, day: int, world) -> list[str]:
        """Consume a captive's ration before the economy closes the day's flow ledger."""
        events = []
        per_person = world.crew_ships.data["rules"]
        for character_id, prisoner in sorted(self.prisoners.items()):
            if prisoner["status"] != "held" or character_id not in world.entities:
                continue
            capture_day = prisoner.get("capture_day", day)
            if day <= capture_day:
                continue
            ship_id = prisoner["captor_ship_id"]
            ship = world.entities.get(ship_id)
            if not ship or ship.get("kind") != "ship" or ship_id not in world.economy.ship_supplies:
                continue
            crew_id = ship.get("crew_id")
            if crew_id not in world.crew_ships.crews:
                continue
            event_id = world.economy.consume_prisoner_supplies(
                world, ship_id, crew_id, [character_id], {
                    "grain": per_person["daily_food_milli_per_member"],
                    "water": per_person["daily_water_milli_per_member"],
                    "rum": per_person["daily_rum_milli_per_member"],
                }, caused_by=prisoner.get("last_custody_event_id",
                                          prisoner["capture_event_id"]), day=day)
            prisoner["last_custody_event_id"] = event_id
            shortage = world.event_log.get(event_id).data["shortage_milli"]
            prisoner["provision_shortage_days"] += bool(any(shortage.values()))
            events.append(event_id)
        return events

    @staticmethod
    def _ticks_per_day(world) -> int:
        return world.config["time"]["hours_per_day"] // world.config["time"]["hours_per_tick"]

    def _new_prisoner_record(self, world, character_id: str, captor_ship_id: str,
                             capture_event_id: str, former_crew_id: str | None) -> dict[str, Any]:
        capture_day = world.clock.tick // self._ticks_per_day(world)
        return {
            "character_id": character_id, "captor_ship_id": captor_ship_id,
            "capture_event_id": capture_event_id, "status": "held", "fate_event_id": None,
            "former_crew_id": former_crew_id, "capture_day": capture_day,
            "next_review_day": capture_day + 7,
            "last_custody_event_id": capture_event_id,
            "provision_shortage_days": 0, "ransom_demand_event_id": None,
            "ransom_demand_silver": None, "escape_attempts": 0,
        }

    def attempt_prisoner_escape(self, world, character_id: str, *, caused_by: str) -> str:
        """Resolve one explicit escape attempt; failure preserves custody, success changes location."""
        prisoner = self.prisoners.get(character_id)
        character = world.entities.get(character_id)
        if (not prisoner or prisoner["status"] != "held" or not character
                or character.get("kind") != "character"):
            raise ValueError("Character is not an active held prisoner")
        world.event_log.get(caused_by)
        ship_id = prisoner["captor_ship_id"]
        ship = world.entities.get(ship_id)
        if not ship or ship.get("kind") != "ship":
            raise ValueError("Prisoner escape requires an active captor vessel")
        location = world.location_of(ship_id)
        mobility = (world.characters.effective_stat(character, "cunning")
                    + world.characters.effective_stat(character, "willpower")) // 2
        chance = min(10_000, max(0, self.data["rules"]["escape_base_basis_points"]
                                 + mobility * self.data["rules"][
                                     "escape_mobility_basis_points_per_point"]))
        attempt = prisoner["escape_attempts"] + 1
        rng = world.rng.stream(f"prisoner-escape:{character_id}:{attempt}")
        escaped = rng.randbelow(10_000) < chance
        event = world.event_log.append(
            tick=world.clock.tick, event_type="PRISONER_ESCAPE_ATTEMPT", location=location["id"],
            actors=[{"role": "prisoner", "entity_id": character_id}],
            targets=[{"role": "captor_ship", "entity_id": ship_id}],
            data={"character_id": character_id, "captor_ship_id": ship_id,
                  "attempt": attempt, "escape_basis_points": chance,
                  "escaped": escaped, "location": location},
            caused_by=list(dict.fromkeys((prisoner.get("last_custody_event_id",
                                                        prisoner["capture_event_id"]),
                                          caused_by))),
            causes_tags=["prisoner_custody", "escape_attempt",
                         "escape_succeeded" if escaped else "escape_failed"],
            magnitude=0.5 if escaped else 0.1)
        prisoner["escape_attempts"] = attempt
        prisoner["last_custody_event_id"] = event.id
        if escaped:
            character.update({"crew_id": None, "ship_id": None, "location": location,
                              "last_event_id": event.id})
            prisoner["status"] = "escaped"
            prisoner["fate_event_id"] = event.id
        return event.id

    def _review_prisoners(self, day: int, world) -> None:
        for character_id, prisoner in sorted(self.prisoners.items()):
            if (prisoner["status"] != "held" or character_id not in world.entities
                    or day < prisoner.get("next_review_day", day + 7)):
                continue
            ship_id = prisoner["captor_ship_id"]
            ship = world.entities.get(ship_id)
            if not ship:
                continue
            captor_crew_id = ship.get("crew_id")
            payer_crew_id = prisoner.get("former_crew_id")
            demand_silver = self.data["rules"]["ransom_base_silver"]
            review = world.event_log.append(
                tick=world.clock.tick, event_type="PRISONER_REVIEWED",
                location=world.location_of(ship_id)["id"],
                actors=[{"role": "captor_crew", "entity_id": captor_crew_id}],
                targets=[{"role": "prisoner", "entity_id": character_id}],
                data={"character_id": character_id, "capture_event_id": prisoner["capture_event_id"],
                      "provision_shortage_days": prisoner["provision_shortage_days"],
                      "payer_crew_id": payer_crew_id,
                      "available_silver": (world.economy.crew_accounts.get(
                          payer_crew_id, {}).get("treasury_silver", 0)),
                      "demand_silver": demand_silver,
                      "next_review_day": day + 30},
                caused_by=[prisoner.get("last_custody_event_id",
                                        prisoner["capture_event_id"])],
                causes_tags=["prisoner_custody", "bounded_review"], magnitude=0.2)
            if prisoner.get("ransom_demand_event_id") is None:
                demand = world.event_log.append(
                    tick=world.clock.tick, event_type="RANSOM_DEMANDED",
                    location=world.location_of(ship_id)["id"],
                    actors=[{"role": "captor_crew", "entity_id": captor_crew_id}],
                    targets=[{"role": "prisoner", "entity_id": character_id},
                             *([{"role": "payer_crew", "entity_id": payer_crew_id}]
                               if payer_crew_id else [])],
                    data={"character_id": character_id, "silver": demand_silver,
                          "capture_event_id": prisoner["capture_event_id"],
                          "review_event_id": review.id},
                    caused_by=[review.id], causes_tags=["ransom", "demand"], magnitude=0.3)
                prisoner["ransom_demand_event_id"] = demand.id
                prisoner["ransom_demand_silver"] = demand_silver
                prisoner["last_custody_event_id"] = demand.id
            else:
                prisoner["last_custody_event_id"] = review.id
            prisoner["next_review_day"] = day + 30

    def resolve(self, world, first_ship_id: str, second_ship_id: str, *, caused_by: list[str],
                location: str | None = None, stances: dict[str, str] | None = None,
                force_combat: bool = False, surprise_by: str | None = None) -> str:
        if first_ship_id == second_ship_id:
            raise ValueError("A ship cannot encounter itself")
        ships = [world.entities.get(first_ship_id), world.entities.get(second_ship_id)]
        if any(not ship or ship.get("kind") != "ship" for ship in ships):
            raise ValueError("Encounter participants must be active ships")
        if any(not ship_is_commandable(world, ship_id)
               for ship_id in (first_ship_id, second_ship_id)):
            raise ValueError("Encounter participants must have valid onboard command authority")
        for event_id in caused_by:
            world.event_log.get(event_id)
        first_crew_id, second_crew_id = (ship["crew_id"] for ship in ships)
        crews = [world.crew_ships.crews[first_crew_id], world.crew_ships.crews[second_crew_id]]
        captains = [world.entities[crew["captain_id"]] for crew in crews]
        first_location = world.location_of(first_ship_id)
        second_location = world.location_of(second_ship_id)
        if location is None:
            overlap = self._overlap(first_location, second_location)
            if overlap is None:
                raise ValueError("Ships are not co-located for an encounter")
            location = overlap["location"]
        strengths_before = [world.crew_ships.combat_power(world, ship_id)
                            for ship_id in (first_ship_id, second_ship_id)]
        beliefs_before = [json.loads(json.dumps(
            world.decisions.agents.get(crew["captain_id"], {}).get("beliefs", []),
            sort_keys=True)) for crew in crews]
        if stances is None:
            ship_ids = (first_ship_id, second_ship_id)
            stances = {ship_id: self._choose_stance(world, captain, ship_id)
                       for ship_id, captain in zip(ship_ids, captains)}
            bands = [self._actor_band(world, ship_id) for ship_id in ship_ids]
            for index, ship_id in enumerate(ship_ids):
                if bands[index] == "pirate" and bands[1 - index] == "merchant":
                    stances[ship_id] = "attack"
        if (not isinstance(stances, dict) or set(stances) != {first_ship_id, second_ship_id}
                or any(value not in {"attack", "avoid", "parley", "ignore", "flee"}
                       for value in stances.values())):
            raise ValueError("Encounter stances must be valid for both ship participants")
        parent_ids = list(dict.fromkeys(caused_by))
        encounter = world.event_log.append(
            tick=world.clock.tick, event_type="ENCOUNTER_DETECTED", location=location,
            actors=[{"role": "first_party", "entity_id": first_ship_id},
                    {"role": "second_party", "entity_id": second_ship_id}],
            data={"ships": [first_ship_id, second_ship_id], "stances": stances,
                  "surprise_by": surprise_by, "strengths_before": strengths_before,
                  "beliefs_before": beliefs_before,
                  "captain_ids_before": [captain["id"] for captain in captains]},
            caused_by=parent_ids, causes_tags=["encounter_detection"], magnitude=0.2,
        )
        self.encounter_count += 1
        if not force_combat and all(stances[ship_id] in {"avoid", "ignore", "flee"}
                                    for ship_id in (first_ship_id, second_ship_id)):
            return self._finish(world, encounter, "mutual_withdrawal", strengths_before,
                                stances, [], [], ["both_parties_avoided_combat"])
        attackers = [ship_id for ship_id in (first_ship_id, second_ship_id)
                     if stances[ship_id] == "attack"]
        if not force_combat and not attackers:
            outcome = "parley_ended_fight" if "parley" in stances.values() else "mutual_withdrawal"
            return self._finish(world, encounter, outcome, strengths_before,
                                stances, [], [], ["parley_or_nonhostile_stance"])
        failed_fleeing_ship = None
        if attackers and not force_combat:
            fleeing = second_ship_id if attackers[0] == first_ship_id else first_ship_id
            if stances[fleeing] == "flee":
                if self._escape_succeeds(world, fleeing, attackers[0], location):
                    return self._finish(world, encounter, "one_side_flees", strengths_before,
                                        stances, [], [], ["successful_escape"])
                failed_fleeing_ship = fleeing
        return self._combat(world, encounter, first_ship_id, second_ship_id,
                            strengths_before, beliefs_before, stances, location,
                            failed_fleeing_ship=failed_fleeing_ship)

    def _combat(self, world, encounter, first_id: str, second_id: str,
                strengths_before: list[dict[str, int]], beliefs_before: list[list[dict[str, Any]]],
                stances: dict[str, str], location: str,
                failed_fleeing_ship: str | None = None) -> str:
        rules = self.data["rules"]
        rng = world.rng.stream(f"encounter-combat:{encounter.id}")
        ship_ids = [first_id, second_id]
        rounds = 1 + rng.randbelow(rules["max_gunnery_rounds"])
        phases = []
        damage_events = []
        gunnery_damage = [0, 0]
        failed_escape_event_id = None
        if failed_fleeing_ship is not None:
            failed_event = world.event_log.append(
                tick=world.clock.tick, event_type="FLEE_FAILED", location=location,
                actors=[{"role": "fleeing_party", "entity_id": failed_fleeing_ship}],
                data={"weakened_rigging": rules["failed_flee_rigging_damage"],
                      "morale_loss": rules["failed_flee_morale_loss"]},
                caused_by=[encounter.id], causes_tags=["failed_escape", "weakened_before_combat"],
                magnitude=0.35)
            failed_escape_event_id = failed_event.id
            ship = world.entities[failed_fleeing_ship]
            crew = world.crew_ships.crews[ship["crew_id"]]
            crew["morale"] = max(0, crew["morale"] - rules["failed_flee_morale_loss"])
            if ship["rigging"]["current"] > 0:
                damage_events.append(world.crew_ships.damage_ship(
                    world, failed_fleeing_ship, "rigging",
                    rules["failed_flee_rigging_damage"], caused_by=failed_event.id))
            phases.append({"phase": "pursuit_escape", "outcome": "failed_flee",
                           "ship_id": failed_fleeing_ship, "event_id": failed_event.id})
        surprise_by = encounter.data.get("surprise_by")
        attacker_order = [0, 1]
        if surprise_by == second_id:
            attacker_order = [1, 0]
        for round_number in range(1, rounds + 1):
            for attacker_index in attacker_order:
                defender_index = 1 - attacker_index
                attacker = world.entities[ship_ids[attacker_index]]
                if attacker["ammunition"] <= 0:
                    continue
                strength = strengths_before[attacker_index]["firepower"]
                nominal = rules["gunnery_damage_base"] + (
                    strength // 100 * rules["gunnery_damage_per_100_firepower"])
                if round_number == 1 and ship_ids[attacker_index] == surprise_by:
                    nominal = nominal * 125 // 100
                variance = nominal * rules["gunnery_variance_percent"] // 100
                damage = max(1, nominal + (rng.randint(-variance, variance) if variance else 0))
                damage = min(damage, world.entities[ship_ids[defender_index]]["hull"]["current"])
                if damage:
                    damage_events.append(world.crew_ships.damage_ship(
                        world, ship_ids[defender_index], "hull", damage,
                        caused_by=encounter.id))
                    gunnery_damage[defender_index] += damage
                    damage_events.append(world.crew_ships.damage_ship(
                        world, ship_ids[attacker_index], "ammunition", 1,
                        caused_by=encounter.id))
            phases.append({"phase": "gunnery", "round": round_number,
                           "damage_by_ship": list(gunnery_damage),
                           "surprise_by": surprise_by if round_number == 1 else None})
            if any(world.entities[ship_id]["hull"]["current"] <= 0 for ship_id in ship_ids):
                break
        boarding = False
        if all(world.entities[ship_id]["hull"]["current"] > 0 for ship_id in ship_ids):
            boarding = (rng.randbelow(10000) < rules["boarding_threshold_basis_points"]
                        and not any(world.entities[ship_id]["rigging"]["current"] <= 0
                                    for ship_id in ship_ids))
        if boarding:
            for defender_index, ship_id in enumerate(ship_ids):
                amount = min(rules["boarding_damage_base"],
                             world.entities[ship_id]["rigging"]["current"])
                if amount:
                    damage_events.append(world.crew_ships.damage_ship(
                        world, ship_id, "rigging", amount, caused_by=encounter.id))
            loser_index = min(range(2), key=lambda index: (
                world.crew_ships.combat_power(world, ship_ids[index])["total"], ship_ids[index]))
            for index, ship_id in enumerate(ship_ids):
                crew = world.crew_ships.crews[world.entities[ship_id]["crew_id"]]
                crew["morale"] = max(0, crew["morale"] - (10 if index == loser_index else 3))
            phases.append({"phase": "boarding", "outcome": "engaged",
                           "rigging_damage": rules["boarding_damage_base"]})
        boarding_personal = None
        if boarding:
            captains = [world.crew_ships.crews[world.entities[ship_id]["crew_id"]]["captain_id"]
                        for ship_id in ship_ids]
            boarding_personal = world.battles.resolve_personal_combat(
                world, captains, caused_by=[encounter.id], location=location,
                context="boarding", context_id=encounter.id)
            winner_side = boarding_personal["winner_side"]
            if winner_side is not None:
                shift = world.characters.data["combat_equipment"]["rules"][
                    "boarding_morale_shift"]
                morale_before = []
                morale_after = []
                for side, ship_id in enumerate(ship_ids):
                    crew = world.crew_ships.crews[world.entities[ship_id]["crew_id"]]
                    morale_before.append(crew["morale"])
                    delta = shift if side == winner_side else -shift
                    crew["morale"] = max(0, min(100, crew["morale"] + delta))
                    morale_after.append(crew["morale"])
                edge = world.event_log.append(
                    tick=world.clock.tick, event_type="BOARDING_PERSONAL_EDGE",
                    location=location,
                    actors=[{"role": "winning_combatant", "entity_id": captains[winner_side]}],
                    targets=[{"role": "opposed_combatant", "entity_id": captains[1 - winner_side]}],
                    data={"encounter_event_id": encounter.id,
                          "personal_combat_started_event_id":
                              boarding_personal["started_event_id"],
                          "impact_scores": boarding_personal["impact_scores"],
                          "winner_side": winner_side,
                          "morale_before": morale_before, "morale_after": morale_after},
                    caused_by=list(dict.fromkeys([
                        boarding_personal["started_event_id"],
                        *boarding_personal["child_event_ids"],
                    ])),
                    causes_tags=["boarding", "personal_combat_edge", "morale_shift"],
                    magnitude=min(1.0, shift / 100),
                )
                for ship_id in ship_ids:
                    world.crew_ships.crews[world.entities[ship_id]["crew_id"]][
                        "last_event_id"] = edge.id
                phases.append({"phase": "boarding_personal_combat",
                               "outcome": "personal_edge", "winner_side": winner_side,
                               "event_id": edge.id})
        strengths_after = [world.crew_ships.combat_power(world, ship_id) for ship_id in ship_ids]
        decisive = ["gunnery_exchange"]
        if failed_fleeing_ship is not None:
            decisive.append("failed_flee")
        if boarding:
            decisive.append("boarding_engagement")
        if surprise_by is not None:
            decisive.append("surprise")
        outcome, details, outcome_event_ids = self._combat_outcome(
            world, encounter, ship_ids, strengths_after, boarding, gunnery_damage, rng)
        upset = self._belief_upset(ship_ids, strengths_after, beliefs_before)
        if upset and upset["upset"]:
            decisive.append("upset")
        if outcome != "combat_resolved":
            decisive.append(f"outcome:{outcome}")
        resolved_id = self._finish(
            world, encounter, outcome, strengths_before, stances,
            phases, damage_events, decisive, strengths_after=strengths_after,
            beliefs_before=beliefs_before, gunnery_damage=gunnery_damage, boarding=boarding,
            outcome_details={**details, "belief_upset": upset,
                             "boarding_personal_combat": (
                                 {"started_event_id": boarding_personal["started_event_id"],
                                  "exchange_event_ids": boarding_personal["exchange_event_ids"],
                                  "impact_scores": boarding_personal["impact_scores"],
                                  "winner_side": boarding_personal["winner_side"]}
                                 if boarding_personal else None)},
            outcome_event_ids=[*(outcome_event_ids or []),
                               *([failed_escape_event_id] if failed_escape_event_id else [])])
        self._apply_combat_consequences(world, world.event_log.get(resolved_id))
        return resolved_id

    @staticmethod
    def _belief_upset(ship_ids: list[str], strengths_after: list[dict[str, int]],
                      beliefs_before: list[list[dict[str, Any]]]) -> dict[str, Any] | None:
        estimates = [None, None]
        for subject_index, ship_id in enumerate(ship_ids):
            observer_beliefs = beliefs_before[1 - subject_index]
            belief = next((record for record in observer_beliefs
                           if record.get("subject_id") == ship_id
                           and record.get("attribute") == "estimated_strength"), None)
            if belief and type(belief.get("value")) is int:
                estimates[subject_index] = belief["value"]
        if any(value is None for value in estimates) or estimates[0] == estimates[1]:
            return None
        estimated_weaker = min(range(2), key=lambda index: (estimates[index], ship_ids[index]))
        actual_winner = max(range(2), key=lambda index: (
            strengths_after[index]["total"], ship_ids[index]))
        return {"estimated_strengths": estimates,
                "weaker_estimated_ship_id": ship_ids[estimated_weaker],
                "actual_winner_ship_id": ship_ids[actual_winner],
                "upset": estimated_weaker == actual_winner}

    def _combat_outcome(self, world, encounter, ship_ids: list[str],
                        strengths_after: list[dict[str, int]], boarding: bool,
                        gunnery_damage: list[int], rng) -> tuple[str, dict[str, Any], list[str]]:
        ships = [world.entities[ship_id] for ship_id in ship_ids]
        sunk = [index for index, ship in enumerate(ships) if ship["hull"]["current"] <= 0]
        if len(sunk) == 2:
            return "both_sunk_rare", {"sunk_ship_ids": ship_ids}, []
        if sunk:
            winner_index = 1 - sunk[0]
            return "ship_sunk", {"sunk_ship_id": ship_ids[sunk[0]],
                                 "winner_ship_id": ship_ids[winner_index]}, []
        loser_index = min(range(2), key=lambda index: (strengths_after[index]["total"], ship_ids[index]))
        winner_index = 1 - loser_index
        loser_crew = world.crew_ships.crews[ships[loser_index]["crew_id"]]
        winner_crew = world.crew_ships.crews[ships[winner_index]["crew_id"]]
        rules = self.data["rules"]
        if boarding and loser_crew["morale"] <= rules["surrender_morale_threshold"]:
            if rng.randbelow(10000) < rules["surrender_basis_points"]:
                if rng.randbelow(10000) < rules["ship_capture_basis_points"]:
                    return "ship_captured", {
                        "captured_ship_id": ship_ids[loser_index],
                        "winner_ship_id": ship_ids[winner_index],
                        "surrendering_crew_id": loser_crew["id"],
                    }, []
                return "surrender", {"surrendering_crew_id": loser_crew["id"],
                                     "winner_ship_id": ship_ids[winner_index]}, []
        if boarding and loser_crew["morale"] <= rules["defection_morale_threshold"] \
                and rng.randbelow(10000) < rules["defection_basis_points"]:
            candidates = [member_id for member_id in loser_crew["roster"]
                          if member_id != loser_crew["captain_id"]]
            if candidates:
                defector_id = min(candidates)
                return "crew_defects", {"defector_ids": [defector_id],
                                         "from_crew_id": loser_crew["id"],
                                         "to_crew_id": winner_crew["id"]}, []
        if boarding and rng.randbelow(10000) < rules["captain_capture_basis_points"]:
            captain_id = loser_crew["captain_id"]
            self.prisoners[captain_id] = self._new_prisoner_record(
                world, captain_id, ship_ids[winner_index], encounter.id, loser_crew["id"])
            world.decisions.on_agent_inaccessible(
                world, captain_id, cause_id=encounter.id,
                reason="captured_before_objective_completion")
            return "captain_captured", {"captured_character_id": captain_id,
                                         "captor_ship_id": ship_ids[winner_index]}, []
        casualty_candidates = [index for index, damage in enumerate(gunnery_damage) if damage > 0]
        if casualty_candidates:
            victim_index = casualty_candidates[rng.randbelow(len(casualty_candidates))]
            victim_crew = world.crew_ships.crews[ships[victim_index]["crew_id"]]
            captain_id = victim_crew["captain_id"]
            casualty_roll = rng.randbelow(10000)
            if casualty_roll < rules["captain_death_basis_points"]:
                event_id = world.characters.kill_character(
                    world, captain_id, cause="battle", caused_by=encounter.id,
                    tags=["combat_casualty", "captain_killed"])
                return "captain_killed", {"captain_id": captain_id,
                                          "victim_ship_id": ship_ids[victim_index]}, [event_id]
            if casualty_roll < (rules["captain_death_basis_points"]
                                + rules["captain_wound_basis_points"]):
                event_id = world.characters.inflict_injury(
                    world, captain_id, "broken_arm", caused_by=encounter.id)
                return "captain_wounded", {"captain_id": captain_id,
                                            "victim_ship_id": ship_ids[victim_index]}, [event_id]
        return "combat_resolved", {"winner_ship_id": ship_ids[winner_index],
                                   "loser_ship_id": ship_ids[loser_index]}, []

    def resolve_prisoner_fate(self, world, character_id: str, fate: str, *,
                              caused_by: str) -> str:
        prisoner = self.prisoners.get(character_id)
        if not prisoner or prisoner["status"] != "held":
            raise ValueError("Character is not a held prisoner")
        if fate not in {"ransom", "execute", "release"}:
            raise ValueError("Unknown prisoner fate")
        world.event_log.get(caused_by)
        character = world.entities.get(character_id)
        if not character or character.get("kind") != "character":
            raise ValueError("Held prisoner is not an active character")
        ransom_accounts = None
        if fate == "ransom":
            payer_crew_id = prisoner.get("former_crew_id") or character.get("crew_id")
            captor_ship = world.entities.get(prisoner["captor_ship_id"])
            captor_crew_id = captor_ship.get("crew_id") if captor_ship else None
            if (payer_crew_id not in world.economy.crew_accounts
                    or captor_crew_id not in world.economy.crew_accounts):
                raise ValueError("Ransom requires active payer and captor crew accounts")
            ransom_amount = prisoner.get("ransom_demand_silver")
            if ransom_amount is None:
                ransom_amount = self.data["rules"]["ransom_base_silver"]
            if world.economy.crew_accounts[payer_crew_id]["treasury_silver"] < ransom_amount:
                raise ValueError("Payer cannot meet the demanded ransom")
            ransom_accounts = (payer_crew_id, captor_crew_id, ransom_amount)
        event = world.event_log.append(
            tick=world.clock.tick, event_type="PRISONER_FATE",
            location=world.location_of(prisoner["captor_ship_id"])["id"],
            actors=[{"role": "captor", "entity_id": prisoner["captor_ship_id"]}],
            targets=[{"role": "prisoner", "entity_id": character_id}],
            data={"fate": fate, "capture_event_id": prisoner["capture_event_id"]},
            caused_by=list(dict.fromkeys((prisoner["capture_event_id"], caused_by))),
            causes_tags=["prisoner_fate", f"fate:{fate}"], magnitude=0.75,
        )
        if fate == "execute":
            death_event = world.characters.kill_character(
                world, character_id, cause="execution", caused_by=event.id,
                tags=["execution", "combat_casualty"])
            prisoner["status"] = "executed"
            prisoner["fate_event_id"] = death_event
            return death_event
        if fate == "ransom":
            character_crew_id, captor_crew_id, amount = ransom_accounts
            payer = world.economy.crew_accounts[character_crew_id]
            receiver = world.economy.crew_accounts[captor_crew_id]
            payer["treasury_silver"] -= amount
            receiver["treasury_silver"] += amount
            world.crew_ships.crews[character_crew_id]["treasury_silver"] = payer["treasury_silver"]
            world.crew_ships.crews[captor_crew_id]["treasury_silver"] = receiver["treasury_silver"]
            transfer = world.event_log.append(
                tick=world.clock.tick, event_type="RANSOM_PAID",
                location=world.location_of(prisoner["captor_ship_id"])["id"],
                actors=[{"role": "payer_crew", "entity_id": character_crew_id}],
                targets=[{"role": "captor_crew", "entity_id": captor_crew_id}],
                data={"silver": amount, "prisoner_id": character_id,
                      "demand_event_id": prisoner.get("ransom_demand_event_id")},
                caused_by=[event.id, *([prisoner["ransom_demand_event_id"]]
                                       if prisoner.get("ransom_demand_event_id") else [])],
                causes_tags=["ransom", "silver_transfer"], magnitude=0.4)
            ransom_event_id = transfer.id
        else:
            ransom_event_id = None
        location = world.location_of(prisoner["captor_ship_id"])
        released = world.event_log.append(
            tick=world.clock.tick, event_type="PRISONER_RELEASED", location=location["id"],
            actors=[{"role": "captor", "entity_id": prisoner["captor_ship_id"]}],
            targets=[{"role": "released_prisoner", "entity_id": character_id}],
            data={"character_id": character_id, "fate": fate,
                  "captor_ship_id": prisoner["captor_ship_id"],
                  "former_crew_id": prisoner.get("former_crew_id"),
                  "ransom_event_id": ransom_event_id,
                  "location": location},
            caused_by=list(dict.fromkeys([event.id, *([ransom_event_id]
                                                       if ransom_event_id else [])])),
            causes_tags=["prisoner_custody", "release", fate], magnitude=0.45)
        character.update({"crew_id": None, "ship_id": None, "location": location,
                          "last_event_id": released.id})
        prisoner["status"] = "ransomed" if fate == "ransom" else "released"
        prisoner["fate_event_id"] = released.id
        return released.id

    def resolve_missing_person(self, world, character_id: str, resolution: str, *,
                               caused_by: str, rescuer_ship_id: str | None = None) -> str:
        """Resolve a shipwreck missing-person record without creating a new identity."""
        person = world.entities.get(character_id)
        missing = person.get("missing_at_sea") if person else None
        if not person or person.get("kind") != "character" or not isinstance(missing, dict):
            raise ValueError("Character does not have an active missing-person record")
        if resolution not in {"found_alive", "confirmed_dead"}:
            raise ValueError("Unknown missing-person resolution")
        world.event_log.get(caused_by)
        search_location = missing.get("search_location")
        if not isinstance(search_location, dict) or search_location.get("kind") != "island":
            raise ValueError("Missing-person record has no valid search island")
        if resolution == "found_alive":
            if not rescuer_ship_id or not ship_is_commandable(world, rescuer_ship_id):
                raise ValueError("Finding a missing person requires a commanded rescue vessel")
            rescue_location = world.location_of(rescuer_ship_id)
            if (rescue_location.get("kind") != "island"
                    or rescue_location.get("id") != search_location["id"]):
                raise ValueError("Rescue vessel must be at the missing person's search island")
            crew_id = person.get("crew_id")
            crew = world.crew_ships.crews.get(crew_id)
            if crew is not None:
                if character_id not in crew["roster"]:
                    raise ValueError("Missing crew member is absent from their active roster")
                if crew.get("ship_ids"):
                    raise ValueError("Missing crew member's former crew must be hull-less to resolve here")
                resolved_location = dict(crew["stranded_location"])
                if resolved_location != search_location:
                    raise ValueError("Missing crew member's stranded location changed since ship loss")
            else:
                resolved_location = dict(search_location)
            event = world.event_log.append(
                tick=world.clock.tick, event_type="CHARACTER_RESCUED",
                location=resolved_location["id"],
                actors=[{"role": "rescue_vessel", "entity_id": rescuer_ship_id}],
                targets=[{"role": "rescued_person", "entity_id": character_id}],
                data={"character_id": character_id, "ship_id": missing["ship_id"],
                      "missing_event_id": person["last_event_id"],
                      "location": resolved_location, "crew_id": crew_id},
                caused_by=list(dict.fromkeys([missing["cause_event_id"], caused_by])),
                causes_tags=["shipwreck", "rescue", "missing_person_resolved"], magnitude=0.5)
            person.pop("missing_at_sea")
            person.update({"ship_id": None, "location": resolved_location,
                           "last_event_id": event.id})
            return event.id

        resolution_event = world.event_log.append(
            tick=world.clock.tick, event_type="MISSING_PERSON_RESOLVED",
            location=missing["last_known_location"]["id"],
            targets=[{"role": "missing_person", "entity_id": character_id}],
            data={"character_id": character_id, "resolution": "confirmed_dead",
                  "ship_id": missing["ship_id"],
                  "missing_event_id": person["last_event_id"]},
            caused_by=list(dict.fromkeys([missing["cause_event_id"], caused_by])),
            causes_tags=["shipwreck", "missing_person_resolved", "death_confirmed"],
            magnitude=0.6)
        person.pop("missing_at_sea")
        return world.characters.kill_character(
            world, character_id, cause="presumed_drowned", caused_by=resolution_event.id,
            tags=["shipwreck", "missing_person", "presumed_drowned"],
            replace_majors=False)

    @staticmethod
    def _prize_captain_candidate(world, crew: dict[str, Any], *,
                                 exclude: set[str] | None = None) -> str | None:
        former_captain_id = crew.get("captain_id")
        candidates = [world.entities[member_id] for member_id in crew.get("roster", [])
                     if member_id != former_captain_id and member_id not in (exclude or set())
                     and member_id in world.entities
                     and world.entities[member_id].get("death_tick") is None]
        if not candidates:
            return None
        return min(candidates, key=lambda row: (
            -(row["stats"]["leadership"] * 2 + row["traits"]["loyalty"]
              - row["traits"]["ambition"] // 2), row["id"]))["id"]

    @staticmethod
    def _local_prize_captain_candidate(world, ship_id: str) -> str | None:
        location = world.location_of(ship_id)
        if location.get("kind") != "island":
            return None
        candidates = [row for row in world.entities.values()
                      if row.get("kind") == "character" and row.get("death_tick") is None
                      and row.get("crew_id") is None and row.get("ship_id") is None
                      and row.get("wealth", {}).get("silver") == 0
                      and row.get("location", {}).get("kind") == "island"
                      and row.get("location", {}).get("id") == location["id"]]
        if not candidates:
            return None
        return min(candidates, key=lambda row: (
            -(row["stats"]["leadership"] * 2 + row["traits"]["loyalty"]
              - row["traits"]["ambition"] // 2), row["id"]))["id"]

    def _install_prize_crew(self, world, captured_ship_id: str, captor_ship_id: str,
                            crew_id: str, former_captain_id: str,
                            prize_captain_id: str, caused_by: str,
                            source_crew_id: str | None = None,
                            local_recruit: bool = False) -> str:
        """Reorganize a surrendered onboard crew into an operational prize crew."""
        crew = world.crew_ships.crews[crew_id]
        if (crew["captain_id"] != former_captain_id
                or captured_ship_id not in crew["ship_ids"]
                or captured_ship_id not in world.entities
                or captor_ship_id not in world.entities):
            raise ValueError("Prize-crew installation no longer matches the captured state")
        source_crew = (world.crew_ships.crews.get(source_crew_id)
                       if source_crew_id is not None else None)
        if source_crew_id is None and not local_recruit:
            candidate_available = prize_captain_id in crew["roster"]
        elif source_crew is not None:
            candidate_available = (source_crew is not None
                                   and prize_captain_id in source_crew["roster"]
                                   and len(source_crew["roster"]) > 1
                                   and source_crew["ship_ids"][0] == captor_ship_id)
        else:
            location = world.location_of(captured_ship_id)
            candidate_available = (new_captain_placeholder := world.entities.get(prize_captain_id)) is not None
            candidate_available = (candidate_available
                                   and location.get("kind") == "island"
                                   and new_captain_placeholder.get("kind") == "character"
                                   and new_captain_placeholder.get("death_tick") is None
                                   and new_captain_placeholder.get("crew_id") is None
                                   and new_captain_placeholder.get("ship_id") is None
                                   and new_captain_placeholder.get("wealth", {}).get("silver") == 0
                                   and new_captain_placeholder.get("location", {}).get("kind") == "island"
                                   and new_captain_placeholder.get("location", {}).get("id") == location["id"])
        if not candidate_available:
            raise ValueError("Prize captain is no longer available from its source crew")
        old_captain = world.entities.get(former_captain_id)
        new_captain = world.entities.get(prize_captain_id)
        if (not old_captain or old_captain.get("death_tick") is not None
                or not new_captain or new_captain.get("death_tick") is not None):
            raise ValueError("Prize-crew captains must remain living active characters")
        prisoner = self.prisoners.get(former_captain_id)
        if prisoner and prisoner["status"] == "held":
            raise ValueError("A prize-crew captain is already held by another captor")
        previous_role = next((role for role, member_id in (source_crew or crew)["roles"].items()
                              if member_id == prize_captain_id and role != "captain"), None)
        transfer_event = None
        recruitment_event = None
        wallet_silver = 0
        if source_crew is not None:
            source_account = world.economy.crew_accounts[source_crew_id]
            wallet_silver = source_account["member_wallets"].get(prize_captain_id, 0)
            transfer_event = world.event_log.append(
                tick=world.clock.tick, event_type="CREW_MEMBER_TRANSFERRED",
                location=world.location_of(captured_ship_id)["id"],
                actors=[{"role": "transferring_person", "entity_id": prize_captain_id}],
                targets=[{"role": "former_crew", "entity_id": source_crew_id},
                         {"role": "new_crew", "entity_id": crew_id},
                         {"role": "transferred_person", "entity_id": prize_captain_id}],
                data={"from_crew_id": source_crew_id, "to_crew_id": crew_id,
                      "crew_id": source_crew_id, "character_id": prize_captain_id,
                      "wallet_silver": wallet_silver,
                      "reason": "prize_crew_assignment"},
                caused_by=list(dict.fromkeys((caused_by,
                                              world.entities[prize_captain_id]["last_event_id"],
                                              source_crew["last_event_id"]))),
                causes_tags=["crew_membership_transfer", "prize_crew"], magnitude=0.55)
        elif local_recruit:
            recruiter = world.crew_ships.crews[world.entities[captor_ship_id]["crew_id"]]["captain_id"]
            recruitment_event = world.event_log.append(
                tick=world.clock.tick, event_type="RECRUITED",
                location=world.location_of(captured_ship_id)["id"],
                actors=[{"role": "prize_crew_recruiter", "entity_id": recruiter}],
                targets=[{"role": "recruit", "entity_id": prize_captain_id}],
                data={"crew_id": crew_id, "character_id": prize_captain_id,
                      "reason": "prize_crew_assignment",
                      "roster_size_before": len(crew["roster"])},
                caused_by=list(dict.fromkeys((caused_by,
                                              world.entities[prize_captain_id]["last_event_id"]))),
                causes_tags=["crew_recruitment", "prize_crew"], magnitude=0.4)
        event = world.event_log.append(
            tick=world.clock.tick, event_type="PRIZE_CREW_INSTALLED",
            location=world.location_of(captured_ship_id)["id"],
            actors=[{"role": "appointing_crew", "entity_id":
                     world.entities[captor_ship_id]["crew_id"]},
                    {"role": "prize_captain", "entity_id": prize_captain_id}],
            targets=[{"role": "captured_ship", "entity_id": captured_ship_id},
                     {"role": "former_captain", "entity_id": former_captain_id}],
            data={"crew_id": crew_id, "former_captain_id": former_captain_id,
                  "prize_captain_id": prize_captain_id,
                  "source_crew_id": source_crew_id,
                  "local_recruit": local_recruit,
                  "wallet_silver": wallet_silver,
                  "previous_role": previous_role, "captor_ship_id": captor_ship_id},
            caused_by=list(dict.fromkeys((caused_by,
                                          *( [transfer_event.id] if transfer_event else []),
                                          *( [recruitment_event.id] if recruitment_event else []),
                                          old_captain["last_event_id"],
                                          new_captain["last_event_id"]))),
            causes_tags=["prize_crew", "captain_succession", "captured_crew_disposition"],
            magnitude=0.65)
        crew["roster"].remove(former_captain_id)
        if source_crew is not None:
            source_crew["roster"].remove(prize_captain_id)
            for role, member_id in list(source_crew["roles"].items()):
                if member_id == prize_captain_id:
                    del source_crew["roles"][role]
            source_crew["last_event_id"] = transfer_event.id
            source_account["member_wallets"].pop(prize_captain_id, None)
            world.economy.crew_accounts[crew_id]["member_wallets"][prize_captain_id] = wallet_silver
            crew["roster"].append(prize_captain_id)
            crew["roster"].sort()
            world.entities[prize_captain_id].update({
                "crew_id": crew_id, "ship_id": captured_ship_id,
                "last_event_id": transfer_event.id,
            })
        elif local_recruit:
            crew["roster"].append(prize_captain_id)
            crew["roster"].sort()
            world.economy.crew_accounts[crew_id]["member_wallets"][prize_captain_id] = (
                new_captain["wealth"]["silver"])
            new_captain.update({"crew_id": crew_id, "ship_id": captured_ship_id,
                                "last_event_id": recruitment_event.id})
        for role, member_id in list(crew["roles"].items()):
            if member_id in {former_captain_id, prize_captain_id}:
                del crew["roles"][role]
        crew["captain_id"] = prize_captain_id
        crew["roles"]["captain"] = prize_captain_id
        crew["last_event_id"] = event.id
        captor_location = world.location_of(captor_ship_id)
        old_captain.update({"crew_id": None, "ship_id": captor_ship_id,
                            "location": captor_location, "last_event_id": event.id})
        new_captain["last_event_id"] = event.id
        self.prisoners[former_captain_id] = self._new_prisoner_record(
            world, former_captain_id, captor_ship_id, event.id, crew_id)
        captured_ship = world.entities[captured_ship_id]
        captured_ship["last_event_id"] = event.id
        captured_ship.setdefault("history", []).append(event.id)
        faction_id = crew.get("faction_id")
        faction = world.politics.factions.get(faction_id)
        if faction is not None and faction.get("leader_id") == former_captain_id:
            faction_event = world.event_log.append(
                tick=world.clock.tick, event_type="FACTION_SUCCESSION",
                location=world.location_of(captured_ship_id)["id"],
                actors=[{"role": "former_faction_leader", "entity_id": former_captain_id}],
                targets=[{"role": "successor", "entity_id": prize_captain_id}],
                data={"faction_id": faction_id, "former_leader_id": former_captain_id,
                      "new_leader_id": prize_captain_id, "affected_crew_id": crew_id,
                      "cause": "prize_crew_installation"},
                caused_by=[event.id], causes_tags=["faction_succession", "prize_crew"],
                magnitude=0.55)
            faction["leader_id"] = prize_captain_id
            faction["council"] = sorted({member_id for member_crew_id
                                          in faction["member_crew_ids"]
                                          if member_crew_id in world.crew_ships.crews
                                          for member_id in world.crew_ships.crews[
                                              member_crew_id]["roster"]
                                          if member_id in world.entities})
            faction["council"] = [member_id for member_id in faction["council"]
                                  if member_id != former_captain_id]
            if prize_captain_id not in faction["council"]:
                faction["council"].append(prize_captain_id)
                faction["council"].sort()
            faction["last_event_id"] = faction_event.id
        return event.id

    def install_pending_prize_crews(self, world, arriving_ship_id: str,
                                    arrival_event_id: str) -> list[str]:
        """Use a real co-location arrival to staff a captured hull if a lawful candidate exists."""
        if not ship_is_commandable(world, arriving_ship_id):
            return []
        world.event_log.get(arrival_event_id)
        arrival_location = world.location_of(arriving_ship_id)
        if arrival_location.get("kind") != "island":
            return []
        controller_crew_id = world.entities[arriving_ship_id]["crew_id"]
        installed = []
        for captured_ship_id, captured_ship in sorted(world.entities.items()):
            if (captured_ship_id == arriving_ship_id or captured_ship.get("kind") != "ship"
                    or captured_ship.get("captured_by_crew_id") != controller_crew_id
                    or captured_ship.get("controller_crew_id") != controller_crew_id
                    or captured_ship.get("location") != arrival_location
                    or captured_ship_id in world.travel.transits):
                continue
            legal_owner = captured_ship.get("legal_owner")
            if (not isinstance(legal_owner, dict)
                    or legal_owner.get("kind") not in {"crew", "merchant"}
                    or not isinstance(legal_owner.get("id"), str)):
                continue
            prize_crew_id = captured_ship.get("crew_id")
            prize_crew = world.crew_ships.crews.get(prize_crew_id)
            if (prize_crew is None or captured_ship_id not in prize_crew.get("ship_ids", ())
                    or prize_crew.get("captain_id") not in world.entities):
                continue
            former_captain_id = prize_crew["captain_id"]
            source_crew_id = None
            prize_captain_id = self._prize_captain_candidate(world, prize_crew)
            if prize_captain_id is None:
                controller_crew = world.crew_ships.crews[controller_crew_id]
                # _install_prize_crew transfers a controller-crew member only
                # from that crew's primary vessel. A secondary vessel's arrival
                # is still a real opportunity for captured-crew/local candidates,
                # but must not select a source candidate that installation will
                # reject as stale authority.
                primary_controller_ship = next(iter(controller_crew.get("ship_ids", ())), None)
                if primary_controller_ship == arriving_ship_id:
                    prize_captain_id = self._prize_captain_candidate(
                        world, controller_crew)
                if prize_captain_id is not None:
                    source_crew_id = controller_crew_id
            local_recruit = False
            if prize_captain_id is None:
                prize_captain_id = self._local_prize_captain_candidate(world, captured_ship_id)
                local_recruit = prize_captain_id is not None
            if prize_captain_id is None:
                continue

            prize_event_id = self._install_prize_crew(
                world, captured_ship_id, arriving_ship_id, prize_crew_id,
                former_captain_id, prize_captain_id, arrival_event_id,
                source_crew_id=source_crew_id, local_recruit=local_recruit)
            legal_owner = dict(legal_owner)
            previous_controller = captured_ship.get("controller_crew_id")
            control_event = world.event_log.append(
                tick=world.clock.tick, event_type="SHIP_ACQUIRED",
                location=arrival_location["id"],
                actors=[{"role": "controlling_crew", "entity_id": prize_crew_id}],
                targets=[{"role": "controlled_ship", "entity_id": captured_ship_id}],
                data={"method": "pending_prize_crew_installed",
                      "previous_owner": legal_owner, "new_owner": legal_owner,
                      "previous_controller_crew_id": previous_controller,
                      "controller_crew_id": prize_crew_id,
                      "legal_owner_kind": legal_owner.get("kind"),
                      "legal_owner_id": legal_owner.get("id")},
                caused_by=[arrival_event_id, prize_event_id],
                causes_tags=["ship_control_assignment", "prize_crew_recovery"],
                magnitude=0.45)
            world.assign_ship_title(
                captured_ship_id, owner_kind=legal_owner["kind"], owner_id=legal_owner["id"],
                controller_crew_id=prize_crew_id, event_id=control_event.id)
            captured_ship["last_event_id"] = control_event.id
            captured_ship.setdefault("history", []).append(control_event.id)
            installed.append(control_event.id)
        return installed

    def _apply_combat_consequences(self, world, resolved) -> None:
        outcome = resolved.data["outcome"]
        details = resolved.data["outcome_details"]
        ships = resolved.data["ships"] if "ships" in resolved.data else [
            actor["entity_id"] for actor in resolved.actors]
        winner_ship_id = details.get("winner_ship_id")
        loser_ship_id = details.get("captured_ship_id") or details.get("sunk_ship_id")
        if not loser_ship_id and details.get("surrendering_crew_id"):
            loser_ship_id = next((ship_id for ship_id in ships
                                  if world.entities.get(ship_id, {}).get("crew_id")
                                  == details["surrendering_crew_id"]), None)
        if not winner_ship_id and outcome in {"captain_captured", "captain_killed", "captain_wounded",
                                              "crew_defects", "combat_resolved"}:
            strength_rows = resolved.data["strengths_after"]
            winner_index = max(range(len(strength_rows)),
                               key=lambda index: (strength_rows[index]["total"], -index))
            winner_ship_id = ships[winner_index]
        if not loser_ship_id and winner_ship_id and outcome in {
                "captain_killed", "captain_wounded", "crew_defects", "combat_resolved"}:
            loser_ship_id = next(ship_id for ship_id in ships if ship_id != winner_ship_id)
        winner_crew_id = (world.entities.get(winner_ship_id, {}).get("crew_id")
                          if winner_ship_id else None)
        loser_crew_id = (world.entities.get(loser_ship_id, {}).get("crew_id")
                         if loser_ship_id else None)
        consequence_causes = [resolved.id]
        loot_event_id = None
        if outcome == "captain_captured":
            character_id = details["captured_character_id"]
            capture_id = world.crew_ships.on_character_captured(
                world, character_id, captor_ship_id=details["captor_ship_id"],
                captor_crew_id=world.entities[details["captor_ship_id"]]["crew_id"],
                caused_by=resolved.id)
            consequence_causes.append(capture_id)
        if winner_ship_id and winner_crew_id and outcome in {
                "ship_captured", "surrender", "ship_sunk", "captain_captured",
                "captain_killed", "captain_wounded", "crew_defects", "combat_resolved"}:
            loot_event_id = self._transfer_combat_loot(
                world, winner_ship_id, winner_crew_id, loser_ship_id, resolved.id)
            if loot_event_id:
                consequence_causes.append(loot_event_id)
        if outcome in {"ship_captured", "surrender"} and loser_ship_id and winner_crew_id:
            captured = world.entities[loser_ship_id]
            surrendered_crew = world.crew_ships.crews[loser_crew_id]
            former_captain_id = surrendered_crew["captain_id"]
            prize_source_crew_id = None
            prize_captain_id = self._prize_captain_candidate(world, surrendered_crew)
            if prize_captain_id is None:
                prize_captain_id = self._prize_captain_candidate(
                    world, world.crew_ships.crews[winner_crew_id])
                if prize_captain_id is not None:
                    prize_source_crew_id = winner_crew_id
            prize_local_recruit = False
            if prize_captain_id is None:
                prize_captain_id = self._local_prize_captain_candidate(world, loser_ship_id)
                prize_local_recruit = prize_captain_id is not None
            prize_crew_pending = prize_captain_id is None
            controller_crew_id = winner_crew_id if prize_crew_pending else loser_crew_id
            previous_owner = dict(captured.get("legal_owner", {}))
            previous_controller = captured.get("controller_crew_id", loser_crew_id)
            if loser_ship_id in world.travel.transits:
                transit = world.travel.transits.pop(loser_ship_id)
                world.scheduler.cancel_for_entity(loser_ship_id)
                captured["location"] = {"kind": "island", "id": transit["origin"]}
            ownership = world.event_log.append(
                tick=world.clock.tick, event_type="SHIP_CAPTURED", location=resolved.location,
                actors=[{"role": "captor_crew", "entity_id": winner_crew_id}],
                targets=[{"role": "captured_ship", "entity_id": loser_ship_id}],
                data={"previous_crew_id": captured["crew_id"], "new_crew_id": winner_crew_id,
                      "previous_owner": previous_owner,
                      "new_owner": {"kind": "crew", "id": winner_crew_id},
                      "previous_controller_crew_id": previous_controller,
                       "new_controller_crew_id": controller_crew_id,
                       "onboard_crew_id": captured.get("crew_id"),
                       "prize_crew_pending": prize_crew_pending,
                       "prize_crew_id": None if prize_crew_pending else loser_crew_id,
                       "former_captain_id": former_captain_id,
                       "prize_captain_id": prize_captain_id},
                caused_by=consequence_causes, causes_tags=["ship_capture", "ownership_change"],
                magnitude=0.8)
            acquired = world.event_log.append(
                tick=world.clock.tick, event_type="SHIP_ACQUIRED", location=resolved.location,
                actors=[{"role": "acquiring_crew", "entity_id": winner_crew_id}],
                targets=[{"role": "acquired_ship", "entity_id": loser_ship_id}],
                data={"method": "capture", "previous_crew_id": loser_crew_id,
                      "previous_owner": previous_owner,
                      "previous_controller_crew_id": previous_controller,
                       "new_crew_id": winner_crew_id,
                       "legal_owner_kind": "crew", "legal_owner_id": winner_crew_id,
                       "controller_crew_id": controller_crew_id},
                caused_by=[ownership.id], causes_tags=["ship_acquisition", "capture"],
                magnitude=0.45)
            world.assign_ship_title(
                loser_ship_id, owner_kind="crew", owner_id=winner_crew_id,
                controller_crew_id=controller_crew_id, event_id=acquired.id)
            captured["last_event_id"] = acquired.id
            if acquired.id not in captured.get("history", []):
                captured.setdefault("history", []).append(acquired.id)
            consequence_causes.extend((ownership.id, acquired.id))
            custody_transfers = []
            for prisoner_id, prisoner in sorted(self.prisoners.items()):
                if (prisoner.get("captor_ship_id") != loser_ship_id
                        or prisoner.get("status") != "held"
                        or prisoner_id not in world.entities):
                    continue
                custody_transfers.append(prisoner_id)
            if custody_transfers:
                custody = world.event_log.append(
                    tick=world.clock.tick, event_type="PRISONER_CUSTODY_TRANSFERRED",
                    location=world.location_of(winner_ship_id)["id"],
                    actors=[{"role": "new_captor_crew", "entity_id": winner_crew_id}],
                    targets=([{"role": "prisoner", "entity_id": prisoner_id}
                              for prisoner_id in custody_transfers]
                             + [{"role": "former_captor_crew", "entity_id": loser_crew_id}]),
                    data={"prisoner_ids": custody_transfers,
                          "from_ship_id": loser_ship_id, "to_ship_id": winner_ship_id,
                          "from_crew_id": loser_crew_id, "to_crew_id": winner_crew_id,
                          "reason": "captor_vessel_captured"},
                    caused_by=[acquired.id],
                    causes_tags=["prisoner_custody", "custody_transfer", "ship_capture"],
                    magnitude=0.45)
                new_captor_location = world.location_of(winner_ship_id)
                for prisoner_id in custody_transfers:
                    self.prisoners[prisoner_id]["captor_ship_id"] = winner_ship_id
                    self.prisoners[prisoner_id]["last_custody_event_id"] = custody.id
                    world.entities[prisoner_id].update({
                        "ship_id": winner_ship_id,
                        "location": dict(new_captor_location),
                        "last_event_id": custody.id,
                    })
                consequence_causes.append(custody.id)
            if not prize_crew_pending:
                prize_event = self._install_prize_crew(
                    world, loser_ship_id, winner_ship_id, loser_crew_id,
                    former_captain_id, prize_captain_id, acquired.id,
                    source_crew_id=prize_source_crew_id,
                    local_recruit=prize_local_recruit)
                consequence_causes.append(prize_event)
        if outcome == "crew_defects":
            to_crew_id = details["to_crew_id"]
            from_crew_id = details["from_crew_id"]
            target_ship_id = next(ship_id for ship_id in ships
                                  if world.entities[ship_id]["crew_id"] == to_crew_id)
            for character_id in details["defector_ids"]:
                source = world.crew_ships.crews[from_crew_id]
                target = world.crew_ships.crews[to_crew_id]
                if character_id not in source["roster"]:
                    continue
                source["roster"].remove(character_id)
                for role, assigned in list(source["roles"].items()):
                    if assigned == character_id:
                        del source["roles"][role]
                target["roster"].append(character_id)
                target["roster"].sort()
                target["roles"].setdefault("sailor", character_id)
                person = world.entities[character_id]
                old_account = world.economy.crew_accounts[from_crew_id]
                new_account = world.economy.crew_accounts[to_crew_id]
                wallet_silver = old_account["member_wallets"].pop(character_id, 0)
                new_account["member_wallets"][character_id] = wallet_silver
                person["crew_id"] = to_crew_id
                person["ship_id"] = target_ship_id
                defection = world.event_log.append(
                    tick=world.clock.tick, event_type="CREW_MEMBER_DEFECTED",
                    location=world.location_of(target_ship_id)["id"],
                    actors=[{"role": "defector", "entity_id": character_id}],
                    targets=[{"role": "former_crew", "entity_id": from_crew_id},
                             {"role": "new_crew", "entity_id": to_crew_id}],
                    data={"from_crew_id": from_crew_id, "to_crew_id": to_crew_id,
                          "wallet_silver": wallet_silver},
                    caused_by=[resolved.id], causes_tags=["crew_defection", "loyalty_change"],
                    magnitude=0.65)
                person["last_event_id"] = defection.id
        sunk_ids = details.get("sunk_ship_ids", [])
        if outcome == "ship_sunk" and loser_ship_id:
            sunk_ids = [loser_ship_id]
        if outcome == "both_sunk_rare":
            sunk_ids = details.get("sunk_ship_ids", ships)
        for ship_id in sunk_ids:
            sink_event = self._sink_ship(world, ship_id, resolved.id)
            consequence_causes.append(sink_event)
        if winner_crew_id:
            increment = self.data["rules"]["reputation_shift"]
            self.reputation[winner_crew_id] = self.reputation.get(winner_crew_id, 0) + increment
            rep_event = world.event_log.append(
                tick=world.clock.tick, event_type="CREW_REPUTATION_CHANGED",
                location=resolved.location,
                actors=[{"role": "crew", "entity_id": winner_crew_id}],
                data={"delta": increment, "reputation": self.reputation[winner_crew_id],
                      "bounty_awarded_silver": 0,
                      "bounty_total_silver": self.bounty_silver.get(winner_crew_id, 0)},
                caused_by=list(dict.fromkeys(consequence_causes)),
                causes_tags=["reputation", "combat_reputation"], magnitude=0.35)
            winner_captain = world.crew_ships.crews.get(winner_crew_id, {}).get("captain_id")
            if winner_captain:
                world.politics.record_public_deed(
                    world, winner_captain, resolved.id, deed_kind="combat_victory")
            if winner_crew_id and loser_crew_id:
                loser_captain = world.crew_ships.crews.get(loser_crew_id, {}).get("captain_id")
                response_event = world.politics.on_combat_resolved(
                    world, winner_crew_id=winner_crew_id, loser_crew_id=loser_crew_id,
                    outcome=outcome, winner_captain_id=winner_captain,
                    loser_captain_id=loser_captain, caused_by=rep_event.id)
                if response_event:
                    consequence_causes.append(response_event)
        for character_id in resolved.data.get("captain_ids_before", []):
            person = world.entities.get(character_id)
            if person is None:
                # The outcome/death chain already records the deceased captain.
                # Never append reflective events under that person's actor ID.
                continue
            if outcome in {"ship_captured", "ship_sunk", "captain_killed", "captain_captured"}:
                want = {"kind": "avenge_crew", "target_ref": winner_crew_id,
                        "urgency_permille": 650, "created_tick": world.clock.tick,
                        "source_event": resolved.id}
                if not any(row.get("kind") == want["kind"] and row.get("source_event") == resolved.id
                           for row in person.get("wants", [])):
                    person.setdefault("wants", []).append(want)
                    person["wants"] = person["wants"][-8:]
            index = resolved.data["captain_ids_before"].index(character_id)
            rival_id = resolved.data["captain_ids_before"][1 - index]
            memory_hook = world.event_log.append(
                tick=world.clock.tick, event_type="COMBAT_MEMORY_HOOK",
                location=resolved.location,
                actors=[{"role": "witness_captain", "entity_id": character_id}],
                targets=[{"role": "rival_captain", "entity_id": rival_id}],
                data={"outcome": outcome, "ship_id": ships[index],
                      "rival_ship_id": ships[1 - index],
                      "decisive_factors": list(resolved.data["decisive_factors"])},
                caused_by=[resolved.id],
                causes_tags=["combat_memory_hook", f"encounter:{outcome}"], magnitude=0.3)
            person["last_event_id"] = memory_hook.id
            winner_ship_id = resolved.data.get("outcome_details", {}).get("winner_ship_id")
            if outcome in {"mutual_withdrawal", "parley_ended_fight"}:
                memory_kind, valence = "honored", 500
            elif winner_ship_id == ships[index]:
                memory_kind, valence = "defeated", 400
            else:
                memory_kind, valence = "defeated_by", -700
            world.memories.add_memory(
                world, character_id, rival_id, memory_kind,
                event_id=memory_hook.id,
                intensity_permille=700 if outcome in {
                    "ship_sunk", "captain_killed", "captain_captured"} else 550,
                valence_permille=valence,
            )

    def _transfer_combat_loot(self, world, winner_ship_id: str, winner_crew_id: str,
                              loser_ship_id: str | None, cause_id: str) -> str | None:
        winner_merchant = next((row for row in world.economy.merchants.values()
                                if row["ship_id"] == winner_ship_id), None)
        loser_merchant = next((row for row in world.economy.merchants.values()
                               if row["ship_id"] == loser_ship_id), None)
        winner_goods = (winner_merchant["cargo_milli"] if winner_merchant else
                        world.economy.ship_supplies.get(winner_ship_id, {}))
        loser_goods = (loser_merchant["cargo_milli"] if loser_merchant else
                       world.economy.ship_supplies.get(loser_ship_id, {})) if loser_ship_id else {}
        transfer = {}
        for good in sorted(winner_goods):
            requested = loser_goods.get(good, 0) * self.data["rules"]["loot_basis_points"] // 10000
            amount = min(requested, max(0, world.entities[winner_ship_id].get("cargo_capacity_milli", 10**9)
                                       - sum(winner_goods.values())))
            if amount:
                loser_goods[good] -= amount
                winner_goods[good] += amount
                transfer[good] = amount
        loser_crew_id = (world.entities.get(loser_ship_id, {}).get("crew_id")
                         if loser_ship_id else None)
        silver = 0
        if loser_crew_id in world.economy.crew_accounts:
            loser_account = world.economy.crew_accounts[loser_crew_id]
            winner_account = world.economy.crew_accounts[winner_crew_id]
            silver = loser_account["treasury_silver"] * self.data["rules"]["loot_basis_points"] // 10000
            loser_account["treasury_silver"] -= silver
            winner_account["treasury_silver"] += silver
            world.crew_ships.crews[loser_crew_id]["treasury_silver"] = loser_account["treasury_silver"]
            world.crew_ships.crews[winner_crew_id]["treasury_silver"] = winner_account["treasury_silver"]
        if not transfer and not silver:
            return None
        if winner_merchant:
            world.entities[winner_ship_id]["cargo"] = dict(winner_goods)
        if loser_merchant:
            world.entities[loser_ship_id]["cargo"] = dict(loser_goods)
        for ship_id in (winner_ship_id, loser_ship_id):
            if ship_id in world.entities and ship_id in world.economy.ship_supplies:
                world.entities[ship_id]["supplies"] = dict(
                    world.economy.ship_supplies[ship_id])
        event = world.event_log.append(
            tick=world.clock.tick, event_type="COMBAT_LOOT_TRANSFERRED",
            location=world.location_of(winner_ship_id)["id"],
            actors=[{"role": "winner_crew", "entity_id": winner_crew_id}],
            targets=([{"role": "loser_crew", "entity_id": loser_crew_id}]
                     if loser_crew_id else []),
            data={"goods_milli": transfer, "silver": silver,
                  "winner_ship_id": winner_ship_id, "loser_ship_id": loser_ship_id,
                  "winner_crew_id": winner_crew_id, "loser_crew_id": loser_crew_id},
            caused_by=[cause_id], causes_tags=["combat_loot", "conserved_transfer"], magnitude=0.5)
        return event.id

    def _sink_ship(self, world, ship_id: str, cause_id: str) -> str:
        ship = world.entities.get(ship_id)
        if not ship:
            raise ValueError("Cannot sink an inactive ship")
        crew_id = ship.get("crew_id")
        crew = world.crew_ships.crews.get(crew_id) if crew_id is not None else None
        if crew_id is not None and crew is None:
            raise ValueError(f"Cannot disposition ship with inactive crew: {crew_id}")
        if crew is not None:
            roster = crew.get("roster")
            if (not isinstance(roster, list) or len(roster) != len(set(roster))
                    or ship_id not in crew.get("ship_ids", ())):
                raise ValueError(f"Cannot disposition ship with inconsistent crew roster: {crew_id}")
            for member_id in roster:
                person = world.entities.get(member_id)
                if (person is None or person.get("kind") != "character"
                        or person.get("death_tick") is not None
                        or person.get("departure_tick") is not None
                        or person.get("crew_id") != crew_id
                        or person.get("ship_id") != ship_id):
                    raise ValueError(
                        "Cannot disposition ship with an unresolved crew roster member: "
                        f"{member_id}")
        location = world.location_of(ship_id)
        if ship_id in world.travel.transits:
            del world.travel.transits[ship_id]
            world.scheduler.cancel_for_entity(ship_id)
        event = world.event_log.append(
            tick=world.clock.tick, event_type="SHIP_SUNK", location=location["id"],
            targets=[{"role": "sunken_ship", "entity_id": ship_id}],
            data={"crew_id": ship.get("crew_id"), "location": location}, caused_by=[cause_id],
            causes_tags=["ship_sinking", "asset_removed"], magnitude=1.0)
        if crew:
            account = world.economy.crew_accounts[crew_id]
            salvage_island = (location["id"] if location["kind"] == "island" else
                              world.geography.routes[location["id"]].origin)
            crew_treasury = account["treasury_silver"]
            wallet_transfers = dict(account["member_wallets"])
            survivor_ids = []
            missing_ids = []
            deceased_ids = []
            hull = ship.get("hull", {})
            hull_max = max(1, int(hull.get("max", 1)))
            hull_current = max(0, min(hull_max, int(hull.get("current", hull_max))))
            damage_permille = (hull_max - hull_current) * 1000 // hull_max
            # People have independent, keyed fate rolls. Risk rises with severe
            # hull damage and being underway; there is no casualty quota.
            peril_bp = (0 if location["kind"] == "island" else
                        min(9500, 700 + damage_permille * 8))
            drowned_bp = peril_bp * 65 // 100
            missing_bp = peril_bp * 25 // 100
            roster_before = sorted(crew["roster"])
            for member_id in roster_before:
                person = world.entities.get(member_id)
                fate_rng = world.rng.stream(
                    f"ship-loss:{ship_id}:{cause_id}:{member_id}")
                roll = fate_rng.randbelow(10000)
                if roll < drowned_bp:
                    deceased_ids.append(member_id)
                elif roll < drowned_bp + missing_bp:
                    missing_ids.append(member_id)
                else:
                    survivor_ids.append(member_id)
                person["ship_id"] = None
                if member_id in missing_ids:
                    person["missing_at_sea"] = {
                        "since_tick": world.clock.tick,
                        "last_known_location": dict(location),
                        "search_location": {"kind": "island", "id": salvage_island},
                        "ship_id": ship_id,
                        "cause_event_id": cause_id,
                    }
                    # Keep the last known valid route location until an explicit
                    # rescue/reappearance transition resolves the missing state.
                    person["location"] = dict(location)
                else:
                    person.pop("missing_at_sea", None)
                    person["location"] = {"kind": "island", "id": salvage_island}
                person["crew_id"] = crew_id
            # Crew assets and personal wallets remain with the surviving
            # organization. Do not also transfer them into the island treasury.
            disposition_silver = 0
            crew["ship_ids"] = []
            crew["stranded_location"] = {"kind": "island", "id": salvage_island}
            crew["distress_days"] = 0
            crew["last_event_id"] = event.id
            held_prisoner_ids = sorted(
                prisoner_id for prisoner_id, prisoner in self.prisoners.items()
                if prisoner.get("captor_ship_id") == ship_id
                and prisoner.get("status") == "held"
                and prisoner_id in world.entities)
            disposition = world.event_log.append(
                tick=world.clock.tick, event_type="SHIP_LOSS_DISPOSITION",
                location=salvage_island,
                actors=[{"role": "shipwrecked_crew", "entity_id": crew_id}],
                targets=([{"role": "survivor", "entity_id": person_id}
                          for person_id in sorted(survivor_ids)]
                         + [{"role": "sunken_ship", "entity_id": ship_id}]),
                data={"ship_id": ship_id, "crew_id": crew_id,
                      "salvage_location": {"kind": "island", "id": salvage_island},
                      "crew_roster_before": roster_before,
                      "survivors": sorted(survivor_ids), "missing": sorted(missing_ids),
                      "deceased": sorted(deceased_ids),
                      "circumstances": {"location": dict(location),
                                        "hull_damage_permille": damage_permille,
                                        "peril_basis_points": peril_bp,
                                        "drowned_basis_points": drowned_bp,
                                        "missing_basis_points": missing_bp,
                                        "casualty_model": "independent_keyed_rolls_no_quota"},
                      "prisoners_released": held_prisoner_ids,
                      "silver_transferred": disposition_silver,
                      "crew_treasury_silver": crew_treasury,
                      "personal_wallets_silver": wallet_transfers,
                      "silver_retained_by_crew": True,
                      "crew_continues_hull_less": True},
                caused_by=[event.id],
                causes_tags=["ship_loss", "population_disposition", "silver_transfer"],
                magnitude=0.8)
            for member_id in survivor_ids + missing_ids:
                world.entities[member_id]["last_event_id"] = disposition.id
            for member_id in missing_ids:
                missing_event = world.event_log.append(
                    tick=world.clock.tick, event_type="CHARACTER_MISSING",
                    location=location["id"],
                    actors=[{"role": "missing_person", "entity_id": member_id}],
                    data={"character_id": member_id, "ship_id": ship_id,
                          "last_known_location": dict(location),
                          "search_location": {"kind": "island", "id": salvage_island}},
                    caused_by=[disposition.id],
                    causes_tags=["shipwreck", "missing_person", "population_unresolved"],
                    magnitude=0.55)
                world.entities[member_id]["last_event_id"] = missing_event.id
            for member_id in deceased_ids:
                world.characters.kill_character(
                    world, member_id, cause="drowned", caused_by=disposition.id,
                    tags=["shipwreck", "drowned", "combat_casualty"],
                    replace_majors=False)
            # A held prisoner aboard this hull is rescued with the survivors;
            # their identity is retained and custody closes causally.
            released_prisoners = []
            for prisoner_id, prisoner in sorted(self.prisoners.items()):
                if (prisoner.get("captor_ship_id") != ship_id
                        or prisoner.get("status") != "held"):
                    continue
                person = world.entities.get(prisoner_id)
                if person is None:
                    continue
                released_prisoners.append(prisoner_id)
                memberships = [candidate for candidate, row in world.crew_ships.crews.items()
                               if prisoner_id in row["roster"]]
                if len(memberships) > 1:
                    raise ValueError(
                        f"Released prisoner has multiple active crew memberships: {prisoner_id}")
                if memberships:
                    membership_crew = world.crew_ships.crews[memberships[0]]
                    if membership_crew["ship_ids"]:
                        membership_ship = membership_crew["ship_ids"][0]
                        person.update({"crew_id": memberships[0],
                                       "ship_id": membership_ship,
                                       "location": {"kind": "island", "id": salvage_island},
                                       "last_event_id": disposition.id})
                    else:
                        person.update({"crew_id": memberships[0], "ship_id": None,
                                       "location": dict(membership_crew["stranded_location"]),
                                       "last_event_id": disposition.id})
                else:
                    person.update({"crew_id": None, "ship_id": None,
                                   "location": {"kind": "island", "id": salvage_island},
                                   "last_event_id": disposition.id})
                prisoner["status"] = "released"
                prisoner["fate_event_id"] = disposition.id
            if sorted(released_prisoners) != held_prisoner_ids:
                raise ValueError("Shipwreck custody disposition changed during resolution")
            if released_prisoners:
                for prisoner_id in released_prisoners:
                    rescue = world.event_log.append(
                        tick=world.clock.tick, event_type="PRISONER_RELEASED",
                        location=salvage_island,
                        actors=[{"role": "shipwreck_survivor", "entity_id": prisoner_id}],
                        targets=[{"role": "released_prisoner", "entity_id": prisoner_id}],
                        data={"character_id": prisoner_id, "captor_ship_id": ship_id,
                              "reason": "captor_ship_sunk"},
                        caused_by=[disposition.id],
                        causes_tags=["prisoner_custody", "rescue", "ship_loss"],
                        magnitude=0.4)
                    self.prisoners[prisoner_id]["fate_event_id"] = rescue.id
                    world.entities[prisoner_id]["last_event_id"] = rescue.id
            crew["last_event_id"] = disposition.id
        ship["sunk_tick"] = world.clock.tick
        ship["last_event_id"] = event.id
        world.archive[ship_id] = ship
        del world.entities[ship_id]
        world.economy.close_merchant_for_vessel_loss(
            world, ship_id, event.id, location=location)
        return event.id

    def _finish(self, world, encounter, outcome: str,
                strengths_before: list[dict[str, int]], stances: dict[str, str],
                phases: list[dict[str, Any]], damage_events: list[str], decisive: list[str],
                *, strengths_after: list[dict[str, int]] | None = None,
                beliefs_before: list[list[dict[str, Any]]] | None = None,
                gunnery_damage: list[int] | None = None, boarding: bool = False,
                outcome_details: dict[str, Any] | None = None,
                outcome_event_ids: list[str] | None = None) -> str:
        ship_ids = [actor["entity_id"] for actor in encounter.actors]
        event = world.event_log.append(
            tick=world.clock.tick, event_type="ENCOUNTER_RESOLVED", location=encounter.location,
            actors=[{"role": "combatant", "entity_id": ship_id} for ship_id in ship_ids],
            data={"outcome": outcome, "stances": stances,
                  "ships": ship_ids,
                  "strengths_before": strengths_before,
                  "strengths_after": strengths_after or strengths_before,
                  "phases": phases, "damage_event_ids": damage_events,
                  "gunnery_damage": gunnery_damage or [0, 0], "boarding": boarding,
                  "outcome_details": outcome_details or {},
                  "outcome_event_ids": outcome_event_ids or [],
                  "captain_ids_before": encounter.data.get("captain_ids_before", []),
                  "decisive_factors": decisive,
                  "beliefs_before": beliefs_before or []},
            caused_by=list(dict.fromkeys([encounter.id, *damage_events,
                                          *(outcome_event_ids or [])])),
            causes_tags=["encounter_resolution", *decisive], magnitude=0.6,
        )
        for ship_id in ship_ids:
            world.entities[ship_id]["history"].append(event.id)
        return event.id

    def _choose_stance(self, world, captain: dict[str, Any], ship_id: str) -> str:
        traits = captain["traits"]
        stream = world.rng.stream(f"encounter-stance:{captain['id']}:{ship_id}")
        roll = stream.randbelow(100)
        aggression = max(0, min(80, (traits["boldness"] + traits["greed"]
                                    + traits["cruelty"]) // 4))
        if roll < aggression:
            return "attack"
        if roll < aggression + 12:
            return "parley"
        return "avoid"

    @staticmethod
    def _actor_band(world, ship_id: str) -> str | None:
        ship = world.entities.get(ship_id, {})
        if ship.get("captured_by_crew_id") is not None:
            return None
        owner_type = ship.get("owner_type")
        crew = world.crew_ships.crews.get(ship.get("crew_id"), {})
        faction = world.politics.factions.get(crew.get("faction_id"), {})
        kind = faction.get("kind", "")
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

    def _escape_succeeds(self, world, fleeing_id: str, pursuer_id: str, location: str) -> bool:
        rules = self.data["rules"]
        fleeing = world.entities[fleeing_id]
        pursuer = world.entities[pursuer_id]
        flee_crew = world.crew_ships.crews[fleeing["crew_id"]]
        pursuit_crew = world.crew_ships.crews[pursuer["crew_id"]]
        flee_captain = world.entities[flee_crew["captain_id"]]
        pursue_captain = world.entities[pursuit_crew["captain_id"]]
        flee_mobility = world.crew_ships.combat_power(world, fleeing_id)["mobility"]
        pursue_mobility = world.crew_ships.combat_power(world, pursuer_id)["mobility"]
        seamanship_delta = (world.characters.effective_stat(flee_captain, "seamanship")
                            - world.characters.effective_stat(pursue_captain, "seamanship"))
        chance = (rules["escape_base_basis_points"]
                  + (flee_mobility - pursue_mobility)
                  * rules["escape_mobility_basis_points_per_point"]
                  + seamanship_delta * 30)
        if location in world.geography.islands:
            region_id = world.geography.islands[location].region_id
        elif location in world.geography.routes:
            route = world.geography.routes[location]
            region_id = world.geography.islands[route.origin].region_id
        else:
            region_id = None
        if region_id is not None:
            weather_state = world.weather.state(region_id)
            speed = world.weather.weather_data["states"][weather_state]["speed_permille"]
            chance = chance * speed // 1000
        chance = max(500, min(9500, chance))
        stream = world.rng.stream(f"encounter-escape:{fleeing_id}:{pursuer_id}:{world.clock.tick}")
        return stream.randbelow(10000) < chance

    def _detection_chance(self, world, ship_id: str, overlap: dict[str, Any]) -> int:
        rules = self.data["rules"]
        ship = world.entities[ship_id]
        crew = world.crew_ships.crews[ship["crew_id"]]
        lookout_id = crew["roles"].get("navigator", crew["captain_id"])
        lookout = world.entities[lookout_id]
        skill = world.characters.effective_stat(lookout, "navigation")
        chance = rules["base_detection_basis_points"] + skill * rules[
            "lookout_basis_points_per_point"]
        if overlap["kind"] == "island":
            chance += rules["island_detection_bonus_basis_points"]
        region_id = (world.geography.islands[overlap["location"]].region_id
                     if overlap["kind"] == "island" else
                     world.geography.islands[world.geography.routes[overlap["location"]].origin].region_id)
        weather = world.weather.state(region_id)
        visibility = world.weather.weather_data["states"][weather]["visibility_permille"]
        weather_scale = max(0, 1000 + (visibility - 1000)
                            * rules["weather_detection_modifier_permille"] // 1000)
        chance = chance * weather_scale // 1000
        return max(0, min(9500, chance))

    def _overlap(self, first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any] | None:
        if first["kind"] == second["kind"] == "island" and first["id"] == second["id"]:
            return {"kind": "island", "location": first["id"]}
        if first["kind"] == second["kind"] == "route" and first["id"] == second["id"]:
            if abs(first["progress_permille"] - second["progress_permille"]) <= \
                    self.data["rules"]["route_signal_range_permille"]:
                return {"kind": "route", "location": first["id"]}
        return None

    @staticmethod
    def _pair_key(first_id: str, second_id: str) -> str:
        return "|".join(sorted((first_id, second_id)))

    @staticmethod
    def _last_cause(world, first_id: str, second_id: str) -> list[str]:
        causes = []
        for ship_id in (first_id, second_id):
            ship = world.entities[ship_id]
            crew = world.crew_ships.crews[ship["crew_id"]]
            ship_cause = ship.get("last_event_id") or ship.get("history", [crew["last_event_id"]])[-1]
            causes.extend((ship_cause, crew["last_event_id"]))
        return list(dict.fromkeys(causes))

    def validate_state(self) -> None:
        if (type(self.last_updated_day) is not int or self.last_updated_day < 0
                or type(self.encounter_count) is not int or self.encounter_count < 0
                or not isinstance(self.pair_last_encounter_day, dict)):
            raise ValueError("Invalid encounter state")
        for key, day in self.pair_last_encounter_day.items():
            if (not isinstance(key, str) or key.count("|") != 1
                    or type(day) is not int or day < 0 or day > self.last_updated_day):
                raise ValueError("Invalid encounter cooldown state")
        if not isinstance(self.prisoners, dict):
            raise ValueError("Invalid prisoner registry")
        for registry in (self.reputation, self.bounty_silver):
            if (not isinstance(registry, dict)
                    or any(not isinstance(key, str) or not key
                           or type(value) is not int or value < 0
                           for key, value in registry.items())):
                raise ValueError("Invalid crew reputation or bounty registry")
        for character_id, prisoner in self.prisoners.items():
            if (not isinstance(character_id, str) or not character_id
                    or not isinstance(prisoner, dict)
                    or set(prisoner) - {"character_id", "captor_ship_id", "capture_event_id",
                                        "status", "fate_event_id", "former_crew_id",
                                        "capture_day", "next_review_day", "last_custody_event_id",
                                        "provision_shortage_days", "ransom_demand_event_id",
                                        "ransom_demand_silver", "escape_attempts"}
                    or not {"character_id", "captor_ship_id", "capture_event_id",
                            "status", "fate_event_id"}.issubset(prisoner)
                    or prisoner["character_id"] != character_id
                    or not isinstance(prisoner["captor_ship_id"], str)
                    or not isinstance(prisoner["capture_event_id"], str)
                    or prisoner.get("former_crew_id") is not None
                    and not isinstance(prisoner["former_crew_id"], str)
                    or prisoner["status"] not in {"held", "ransomed", "executed", "deceased", "recruited",
                                                    "released", "traded", "escaped"}
                    or prisoner["fate_event_id"] is not None
                    and not isinstance(prisoner["fate_event_id"], str)
                    or any(key in prisoner and (type(prisoner[key]) is not int
                                                or prisoner[key] < 0)
                           for key in ("capture_day", "next_review_day",
                                       "provision_shortage_days", "escape_attempts"))
                    or any(key in prisoner and prisoner[key] is not None
                           and not isinstance(prisoner[key], str)
                           for key in ("last_custody_event_id", "ransom_demand_event_id"))):
                raise ValueError(f"Invalid prisoner record: {character_id}")
            if (prisoner.get("ransom_demand_silver") is not None
                    and (type(prisoner["ransom_demand_silver"]) is not int
                         or prisoner["ransom_demand_silver"] < 0)):
                raise ValueError(f"Invalid prisoner ransom demand: {character_id}")

    def validate_world(self, world) -> None:
        self.validate_state()
        ticks_per_day = (world.config["time"]["hours_per_day"]
                         // world.config["time"]["hours_per_tick"])
        if self.last_updated_day != world.clock.tick // ticks_per_day:
            raise ValueError("Encounter snapshot day does not match world clock")
        for key in self.pair_last_encounter_day:
            first, second = key.split("|")
            if first not in world.entities and first not in world.archive \
                    or second not in world.entities and second not in world.archive:
                raise ValueError("Encounter cooldown references an unknown ship")
        for character_id, prisoner in self.prisoners.items():
            if character_id not in world.entities and character_id not in world.archive:
                raise ValueError("Prisoner registry references an unknown character")
            if prisoner["captor_ship_id"] not in world.entities \
                    and prisoner["captor_ship_id"] not in world.archive:
                raise ValueError("Prisoner registry references an unknown captor ship")
            for event_id in (prisoner["capture_event_id"], prisoner["fate_event_id"],
                             prisoner.get("last_custody_event_id"),
                             prisoner.get("ransom_demand_event_id")):
                if event_id is not None:
                    world.event_log.get(event_id)
            archived = character_id in world.archive
            terminal_status = prisoner["status"] in {"executed", "deceased"}
            if terminal_status != archived:
                raise ValueError("Prisoner terminal status disagrees with character archive")
            if (prisoner["status"] == "held" and "former_crew_id" in prisoner
                    and character_id in world.entities):
                person = world.entities[character_id]
                if (person.get("crew_id") is not None
                        or person.get("ship_id") != prisoner["captor_ship_id"]):
                    raise ValueError("Held prisoner is not physically assigned to its captor vessel")

    def snapshot(self) -> dict[str, Any]:
        return {"digest": self.digest, "last_updated_day": self.last_updated_day,
                "pair_last_encounter_day": dict(sorted(self.pair_last_encounter_day.items())),
                "encounter_count": self.encounter_count, "prisoners": self.prisoners,
            "reputation": dict(sorted(self.reputation.items())),
                "bounty_silver": dict(sorted(self.bounty_silver.items()))}
