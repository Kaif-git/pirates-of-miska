"""Deterministic spatial encounter detection and multi-phase ship combat."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


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
                       and row.get("crew_id") in world.crew_ships.crews)
        locations = {ship_id: world.location_of(ship_id) for ship_id in ships}
        emitted = []
        for index, first_id in enumerate(ships):
            if first_id not in world.entities:
                continue
            for second_id in ships[index + 1:]:
                if first_id not in world.entities or second_id not in world.entities:
                    continue
                if (world.entities[first_id].get("crew_id") not in world.crew_ships.crews
                        or world.entities[second_id].get("crew_id") not in world.crew_ships.crews):
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
        return emitted

    def resolve(self, world, first_ship_id: str, second_ship_id: str, *, caused_by: list[str],
                location: str | None = None, stances: dict[str, str] | None = None,
                force_combat: bool = False, surprise_by: str | None = None) -> str:
        if first_ship_id == second_ship_id:
            raise ValueError("A ship cannot encounter itself")
        ships = [world.entities.get(first_ship_id), world.entities.get(second_ship_id)]
        if any(not ship or ship.get("kind") != "ship" for ship in ships):
            raise ValueError("Encounter participants must be active ships")
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
            outcome_details={**details, "belief_upset": upset},
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
            self.prisoners[captain_id] = {
                "character_id": captain_id, "captor_ship_id": ship_ids[winner_index],
                "capture_event_id": encounter.id, "status": "held", "fate_event_id": None,
            }
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
        if fate not in {"ransom", "execute", "recruit", "release", "trade_to_marines"}:
            raise ValueError("Unknown prisoner fate")
        world.event_log.get(caused_by)
        character = world.entities.get(character_id)
        if not character or character.get("kind") != "character":
            raise ValueError("Held prisoner is not an active character")
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
            character_crew_id = character.get("crew_id")
            captor_crew_id = world.entities[prisoner["captor_ship_id"]].get("crew_id")
            if character_crew_id in world.economy.crew_accounts and captor_crew_id in world.economy.crew_accounts:
                payer = world.economy.crew_accounts[character_crew_id]
                receiver = world.economy.crew_accounts[captor_crew_id]
                amount = min(self.data["rules"]["ransom_base_silver"], payer["treasury_silver"])
                payer["treasury_silver"] -= amount
                receiver["treasury_silver"] += amount
                world.crew_ships.crews[character_crew_id]["treasury_silver"] = payer["treasury_silver"]
                world.crew_ships.crews[captor_crew_id]["treasury_silver"] = receiver["treasury_silver"]
                transfer = world.event_log.append(
                    tick=world.clock.tick, event_type="RANSOM_PAID",
                    location=world.location_of(prisoner["captor_ship_id"])["id"],
                    actors=[{"role": "payer_crew", "entity_id": character_crew_id}],
                    targets=[{"role": "captor_crew", "entity_id": captor_crew_id}],
                    data={"silver": amount, "prisoner_id": character_id},
                    caused_by=[event.id], causes_tags=["ransom", "silver_transfer"], magnitude=0.4)
                prisoner["fate_event_id"] = transfer.id
            else:
                prisoner["fate_event_id"] = event.id
            prisoner["status"] = "ransomed"
            return prisoner["fate_event_id"]
        if fate in {"recruit", "release", "trade_to_marines"}:
            # Keep roster/account links intact until explicit transfer mechanics exist.
            pass
        prisoner["status"] = {"ransom": "ransomed", "recruit": "recruited",
                               "release": "released", "trade_to_marines": "traded"}[fate]
        prisoner["fate_event_id"] = event.id
        return event.id

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
        if winner_ship_id and winner_crew_id and outcome in {
                "ship_captured", "surrender", "ship_sunk", "captain_captured",
                "captain_killed", "captain_wounded", "crew_defects", "combat_resolved"}:
            loot_event_id = self._transfer_combat_loot(
                world, winner_ship_id, winner_crew_id, loser_ship_id, resolved.id)
            if loot_event_id:
                consequence_causes.append(loot_event_id)
        if outcome in {"ship_captured", "surrender"} and loser_ship_id and winner_crew_id:
            captured = world.entities[loser_ship_id]
            if loser_ship_id in world.travel.transits:
                transit = world.travel.transits.pop(loser_ship_id)
                world.scheduler.cancel_for_entity(loser_ship_id)
                captured["location"] = {"kind": "island", "id": transit["origin"]}
            captured["captured_by_crew_id"] = winner_crew_id
            captured["last_event_id"] = resolved.id
            ownership = world.event_log.append(
                tick=world.clock.tick, event_type="SHIP_CAPTURED", location=resolved.location,
                actors=[{"role": "captor_crew", "entity_id": winner_crew_id}],
                targets=[{"role": "captured_ship", "entity_id": loser_ship_id}],
                data={"previous_crew_id": captured["crew_id"], "new_crew_id": winner_crew_id},
                caused_by=consequence_causes, causes_tags=["ship_capture", "ownership_change"],
                magnitude=0.8)
            acquired = world.event_log.append(
                tick=world.clock.tick, event_type="SHIP_ACQUIRED", location=resolved.location,
                actors=[{"role": "acquiring_crew", "entity_id": winner_crew_id}],
                targets=[{"role": "acquired_ship", "entity_id": loser_ship_id}],
                data={"method": "capture", "previous_crew_id": loser_crew_id,
                      "new_crew_id": winner_crew_id},
                caused_by=[ownership.id], causes_tags=["ship_acquisition", "capture"],
                magnitude=0.45)
            consequence_causes.extend((ownership.id, acquired.id))
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
            if winner_crew_id and loser_crew_id:
                winner_captain = world.crew_ships.crews.get(winner_crew_id, {}).get("captain_id")
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
        location = world.location_of(ship_id)
        if ship_id in world.travel.transits:
            del world.travel.transits[ship_id]
            world.scheduler.cancel_for_entity(ship_id)
        event = world.event_log.append(
            tick=world.clock.tick, event_type="SHIP_SUNK", location=location["id"],
            targets=[{"role": "sunken_ship", "entity_id": ship_id}],
            data={"crew_id": ship.get("crew_id"), "location": location}, caused_by=[cause_id],
            causes_tags=["ship_sinking", "asset_removed"], magnitude=1.0)
        crew_id = ship.get("crew_id")
        crew = world.crew_ships.crews.get(crew_id)
        if crew:
            account = world.economy.crew_accounts[crew_id]
            salvage_island = (location["id"] if location["kind"] == "island" else
                              world.geography.routes[location["id"]].origin)
            world.economy.islands[salvage_island]["treasury_silver"] += (
                account["treasury_silver"] + sum(account["member_wallets"].values()))
            for member_id in list(crew["roster"]):
                person = world.entities.get(member_id)
                if person:
                    person["crew_id"] = None
                    person["ship_id"] = None
                    person["location"] = (location if location["kind"] == "island"
                                           else {"kind": "island", "id": world.geography.islands[
                                               world.geography.routes[location["id"]].origin].id})
                    person["last_event_id"] = event.id
            world.politics.on_crew_dissolved(
                world, crew_id, caused_by=event.id, reason="crew_ship_lost")
            del world.crew_ships.crews[crew_id]
            del world.economy.crew_accounts[crew_id]
        ship["sunk_tick"] = world.clock.tick
        ship["last_event_id"] = event.id
        world.archive[ship_id] = ship
        del world.entities[ship_id]
        merchant = next((row for row in world.economy.merchants.values()
                         if row["ship_id"] == ship_id), None)
        if merchant:
            salvage_island = (location["id"] if location["kind"] == "island" else
                              world.geography.routes[location["id"]].origin)
            replacement_id, replacement_created = world.create_vessel(
                merchant["name"] + "'s ship", salvage_island,
                navigation=ship.get("navigation", 55),
                successor_of=ship_id,
            )
            replacement = world.entities[replacement_id]
            replacement["cargo"] = dict(merchant["cargo_milli"])
            replacement["cargo_capacity_milli"] = merchant["capacity_milli"]
            acquired = world.event_log.append(
                tick=world.clock.tick, event_type="SHIP_ACQUIRED", location=salvage_island,
                actors=[{"role": "merchant", "entity_id": merchant["id"]}],
                targets=[{"role": "acquired_ship", "entity_id": replacement_id}],
                data={"method": "replacement", "successor_of": ship_id,
                      "ship_created_event_id": replacement_created},
                caused_by=[event.id, replacement_created],
                causes_tags=["ship_acquisition", "merchant_replacement"], magnitude=0.35,
            )
            replaced = world.event_log.append(
                tick=world.clock.tick, event_type="MERCHANT_VESSEL_REPLACED",
                location=salvage_island,
                actors=[{"role": "merchant", "entity_id": merchant["id"]}],
                targets=[{"role": "lost_vessel", "entity_id": ship_id},
                         {"role": "replacement_vessel", "entity_id": replacement_id}],
                data={"merchant_id": merchant["id"], "old_ship_id": ship_id,
                      "new_ship_id": replacement_id,
                      "silver_carried": merchant["silver"],
                      "cargo_milli": dict(merchant["cargo_milli"])},
                caused_by=[event.id, replacement_created, acquired.id],
                causes_tags=["merchant_continuity", "replacement_vessel", "finite_assets_retained"],
                magnitude=0.5,
            )
            merchant["ship_id"] = replacement_id
            merchant["last_event_id"] = replaced.id
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
                    or set(prisoner) != {"character_id", "captor_ship_id", "capture_event_id",
                                         "status", "fate_event_id"}
                    or prisoner["character_id"] != character_id
                    or not isinstance(prisoner["captor_ship_id"], str)
                    or not isinstance(prisoner["capture_event_id"], str)
                    or prisoner["status"] not in {"held", "ransomed", "executed", "deceased", "recruited",
                                                   "released", "traded"}
                    or prisoner["fate_event_id"] is not None
                    and not isinstance(prisoner["fate_event_id"], str)):
                raise ValueError(f"Invalid prisoner record: {character_id}")

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
            for event_id in (prisoner["capture_event_id"], prisoner["fate_event_id"]):
                if event_id is not None:
                    world.event_log.get(event_id)
            archived = character_id in world.archive
            terminal_status = prisoner["status"] in {"executed", "deceased"}
            if terminal_status != archived:
                raise ValueError("Prisoner terminal status disagrees with character archive")

    def snapshot(self) -> dict[str, Any]:
        return {"digest": self.digest, "last_updated_day": self.last_updated_day,
                "pair_last_encounter_day": dict(sorted(self.pair_last_encounter_day.items())),
                "encounter_count": self.encounter_count, "prisoners": self.prisoners,
            "reputation": dict(sorted(self.reputation.items())),
                "bounty_silver": dict(sorted(self.bounty_silver.items()))}
