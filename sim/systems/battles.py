"""Deterministic fleet commands, legend duels, and set-piece theater battles."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


DEFAULT_BATTLE_PATH = Path(__file__).resolve().parents[1] / "data" / "battles.json"


def _load_data(path: str | Path = DEFAULT_BATTLE_PATH) -> tuple[dict[str, Any], str]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load battle catalog {path}: {exc}") from exc
    _validate_data(data)
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return data, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_data(data: Any) -> None:
    if not isinstance(data, dict) or set(data) != {
            "schema_version", "rules", "doctrines", "legend_traits"} \
            or type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("Invalid battle catalog schema")
    rules = data["rules"]
    required_rules = {"starting_legend_captain_count", "battle_cooldown_days",
                      "max_battles_per_year", "rounds", "reinforcement_round",
                      "round_variance_permille", "morale_swing_per_round",
                      "morale_cascade_threshold", "morale_cascade_loss",
                      "defeat_hull_damage_base", "defeat_rigging_damage_base",
                      "defeat_casualty_chance_basis_points", "power_vacuum_morale_threshold",
                      "minimum_active_ships_per_side"}
    if (not isinstance(rules, dict) or set(rules) != required_rules
            or any(type(value) is not int or value < 0 for value in rules.values())
            or rules["starting_legend_captain_count"] < 2
            or rules["battle_cooldown_days"] < 1
            or rules["max_battles_per_year"] < 1
            or rules["rounds"] < 2
            or not 1 <= rules["reinforcement_round"] <= rules["rounds"]
            or rules["round_variance_permille"] > 500
            or rules["defeat_casualty_chance_basis_points"] > 10000
            or rules["power_vacuum_morale_threshold"] > 100
            or rules["minimum_active_ships_per_side"] < 1):
        raise ValueError("Invalid battle rules")
    doctrines = data["doctrines"]
    if not isinstance(doctrines, dict) or not doctrines:
        raise ValueError("Battle catalog needs doctrines")
    for name, doctrine in doctrines.items():
        if (not isinstance(name, str) or not name
                or not isinstance(doctrine, dict)
                or set(doctrine) != {"power_permille", "morale_permille", "description"}
                or type(doctrine["power_permille"]) is not int or doctrine["power_permille"] < 1
                or type(doctrine["morale_permille"]) is not int or doctrine["morale_permille"] < 1
                or not isinstance(doctrine["description"], str) or not doctrine["description"]):
            raise ValueError("Invalid fleet doctrine")
    traits = data["legend_traits"]
    if not isinstance(traits, list) or len(traits) < rules["starting_legend_captain_count"]:
        raise ValueError("Legend trait catalog must cover starting legend captains")
    ids = set()
    for trait in traits:
        if (not isinstance(trait, dict)
                or set(trait) != {"id", "name", "round_power_permille", "duel_bonus", "description"}
                or not isinstance(trait["id"], str) or not trait["id"] or trait["id"] in ids
                or not isinstance(trait["name"], str) or not trait["name"]
                or type(trait["round_power_permille"]) is not int
                or not 500 <= trait["round_power_permille"] <= 2000
                or type(trait["duel_bonus"]) is not int or trait["duel_bonus"] < 0
                or not isinstance(trait["description"], str) or not trait["description"]):
            raise ValueError("Invalid legend trait")
        ids.add(trait["id"])


class BattleSystem:
    """Model fleet orders and resolve eligible hostile encounters as theater battles."""

    def __init__(self, *, data: dict[str, Any], digest: str,
                 fleet_commands: dict[str, dict[str, Any]] | None = None,
                 pair_last_battle_day: dict[str, int] | None = None,
                 battles_per_year: dict[str, int] | None = None,
                 battle_count: int = 0, last_updated_day: int = 0) -> None:
        self.data = data
        self.digest = digest
        self.fleet_commands = json.loads(json.dumps(fleet_commands or {}, sort_keys=True))
        self.pair_last_battle_day = dict(pair_last_battle_day or {})
        self.battles_per_year = dict(battles_per_year or {})
        self.battle_count = battle_count
        self.last_updated_day = last_updated_day

    @classmethod
    def create(cls, path: str | Path = DEFAULT_BATTLE_PATH) -> "BattleSystem":
        data, digest = _load_data(path)
        return cls(data=data, digest=digest)

    @classmethod
    def restore(cls, snapshot: dict[str, Any]) -> "BattleSystem":
        required = {"digest", "fleet_commands", "pair_last_battle_day", "battles_per_year",
                    "battle_count", "last_updated_day"}
        if (not isinstance(snapshot, dict) or set(snapshot) != required
                or not isinstance(snapshot["fleet_commands"], dict)
                or not isinstance(snapshot["pair_last_battle_day"], dict)
                or not isinstance(snapshot["battles_per_year"], dict)):
            raise ValueError("Invalid battle snapshot")
        data, digest = _load_data()
        if snapshot["digest"] != digest:
            raise ValueError("Battle catalog changed since snapshot creation")
        return cls(data=data, digest=digest, fleet_commands=snapshot["fleet_commands"],
                   pair_last_battle_day=snapshot["pair_last_battle_day"],
                   battles_per_year=snapshot["battles_per_year"],
                   battle_count=snapshot["battle_count"],
                   last_updated_day=snapshot["last_updated_day"])

    def snapshot(self) -> dict[str, Any]:
        return {"digest": self.digest, "fleet_commands": self.fleet_commands,
                "pair_last_battle_day": dict(sorted(self.pair_last_battle_day.items())),
                "battles_per_year": dict(sorted(self.battles_per_year.items())),
                "battle_count": self.battle_count, "last_updated_day": self.last_updated_day}

    def seed_world(self, world) -> None:
        if self.fleet_commands:
            raise ValueError("Fleet command state can only be initialized once")
        captains = []
        for crew_id, crew in sorted(world.crew_ships.crews.items()):
            captain_id = crew["captain_id"]
            captain = world.entities[captain_id]
            captains.append((crew_id, captain_id, captain))
        limit = min(self.data["rules"]["starting_legend_captain_count"], len(captains))
        traits = self.data["legend_traits"]
        for index, (crew_id, captain_id, captain) in enumerate(captains[:limit]):
            trait = traits[index]
            captain["tier"] = "LEGEND"
            captain["legend_traits"] = [trait["id"]]
            captain["legend_since_event_id"] = None
        self.refresh_fleet_commands(world)
        self.validate_world(world)

    def refresh_fleet_commands(self, world) -> None:
        commands = {}
        doctrine_names = sorted(self.data["doctrines"])
        factions = world.politics.factions
        for index, (faction_id, faction) in enumerate(sorted(factions.items())):
            crew = world.crew_ships.crews.get(faction_id)
            commander_id = faction.get("leader_id") or (crew or {}).get("captain_id")
            if commander_id not in world.entities:
                continue
            existing = self.fleet_commands.get(faction_id, {})
            doctrine = existing.get("doctrine")
            if doctrine not in self.data["doctrines"]:
                doctrine = "defensive" if faction.get("kind") == "marine" else doctrine_names[index % len(doctrine_names)]
            last_command_event_id = existing.get("last_command_event_id")
            if existing and (existing.get("commander_id") != commander_id
                    or existing.get("doctrine") != doctrine):
                causes = [faction.get("last_event_id"), (crew or {}).get("last_event_id"),
                          world.entities[commander_id].get("last_event_id")]
                causes = list(dict.fromkeys(event_id for event_id in causes if event_id))
                command_event = world.event_log.append(
                    tick=world.clock.tick, event_type="FLEET_COMMAND_SET",
                    location=world.location_of(commander_id)["id"],
                    actors=[{"role": "fleet_commander", "entity_id": commander_id}],
                    targets=[{"role": "commanded_faction", "entity_id": faction_id}],
                    data={"faction_id": faction_id, "commander_id": commander_id,
                          "doctrine": doctrine,
                          "doctrine_description": self.data["doctrines"][doctrine]["description"]},
                    caused_by=causes, causes_tags=["fleet_command", f"doctrine:{doctrine}"],
                    magnitude=0.35,
                )
                last_command_event_id = command_event.id
            commands[faction_id] = {"faction_id": faction_id, "commander_id": commander_id,
                                    "doctrine": doctrine,
                                    "last_command_event_id": last_command_event_id}
        self.fleet_commands = commands

    def update_day(self, day: int, world) -> list[str]:
        if type(day) is not int or day != self.last_updated_day + 1:
            raise ValueError("Battle days must be updated consecutively")
        self.refresh_fleet_commands(world)
        ticks_per_day = (world.config["time"]["hours_per_day"]
                         // world.config["time"]["hours_per_tick"])
        tick = day * ticks_per_day
        triggered = []
        for event in world.event_log.by_tick(tick):
            if event.type != "ENCOUNTER_RESOLVED":
                continue
            battle_id = self._maybe_resolve_set_piece(world, event, day)
            if battle_id:
                triggered.append(battle_id)
        self.last_updated_day = day
        return triggered

    def _maybe_resolve_set_piece(self, world, encounter, day: int) -> str | None:
        if encounter.data.get("outcome") in {"mutual_withdrawal", "one_side_flees", "parley_ended_fight"}:
            return None
        ship_ids = encounter.data.get("ships", [])
        if len(ship_ids) != 2 or any(ship_id not in world.entities for ship_id in ship_ids):
            return None
        ships = [world.entities[ship_id] for ship_id in ship_ids]
        crews = [world.crew_ships.crews.get(ship.get("crew_id")) for ship in ships]
        if any(crew is None for crew in crews):
            return None
        factions = [crew.get("faction_id") for crew in crews]
        captains = [crew.get("captain_id") for crew in crews]
        if (not factions[0] or not factions[1] or factions[0] == factions[1]
                or any(captain not in world.entities for captain in captains)
                or any(faction_id not in self.fleet_commands for faction_id in factions)
                or any(world.entities[captain].get("tier") != "LEGEND" for captain in captains)):
            return None
        pair_key = "|".join(sorted(factions))
        if day - self.pair_last_battle_day.get(pair_key, -10**9) \
                < self.data["rules"]["battle_cooldown_days"]:
            return None
        year = (day - 1) // world.config["time"]["days_per_year"] + 1
        year_key = str(year)
        if self.battles_per_year.get(year_key, 0) >= self.data["rules"]["max_battles_per_year"]:
            return None
        location = encounter.location
        if location is None:
            return None
        side_ships = [self._nearby_fleet_ships(world, factions[index], ship_ids[index], location)
                      for index in range(2)]
        if any(len(group) < self.data["rules"]["minimum_active_ships_per_side"]
               for group in side_ships):
            return None
        recognition_events = []
        traits_by_id = {trait["id"]: trait for trait in self.data["legend_traits"]}
        for captain_id in captains:
            captain = world.entities[captain_id]
            if captain.get("legend_since_event_id") is not None:
                continue
            trait_id = captain["legend_traits"][0]
            trait = traits_by_id[trait_id]
            recognition = world.event_log.append(
                tick=world.clock.tick, event_type="LEGEND_RECOGNIZED", location=location,
                actors=[{"role": "recognized_legend", "entity_id": captain_id}],
                data={"tier": "LEGEND", "trait_id": trait_id,
                      "trait_name": trait["name"],
                      "reason": "commanding a founding fleet in a set-piece battle"},
                caused_by=[encounter.id],
                causes_tags=["legend_foundation", f"trait:{trait_id}", "set_piece_battle"],
                magnitude=0.65,
            )
            captain["legend_since_event_id"] = recognition.id
            recognition_events.append(recognition.id)
        return self._resolve_battle(world, encounter, day, year, factions, captains,
                                    side_ships, recognition_events)

    def _nearby_fleet_ships(self, world, faction_id: str, main_ship: str,
                            location: str) -> list[str]:
        main_location = world.location_of(main_ship)
        result = []
        for ship_id, ship in sorted(world.entities.items()):
            if ship.get("kind") != "ship" or ship.get("crew_id") not in world.crew_ships.crews:
                continue
            crew = world.crew_ships.crews[ship["crew_id"]]
            if crew.get("faction_id") != faction_id or ship_id in self.engaged_ship_ids:
                continue
            other_location = world.location_of(ship_id)
            overlap = world.encounters._overlap(main_location, other_location)
            if ship_id == main_ship or (overlap and overlap["location"] == location):
                result.append(ship_id)
        return result

    @property
    def engaged_ship_ids(self) -> set[str]:
        # Battles resolve synchronously within a day; this is a clear query hook
        # for the encounter scanner and keeps theater rounds atomic across saves.
        return set()

    def _resolve_battle(self, world, trigger, day: int, year: int,
                        factions: list[str], captains: list[str],
                        side_ships: list[list[str]],
                        recognition_events: list[str]) -> str:
        commands = [self.fleet_commands[faction_id] for faction_id in factions]
        doctrine_names = [command["doctrine"] for command in commands]
        battle_number = self.battle_count + 1
        battle_event = world.event_log.append(
            tick=world.clock.tick, event_type="BATTLE", location=trigger.location,
            actors=[{"role": "fleet_commander", "entity_id": captain} for captain in captains]
                   + [{"role": "fleet_ship", "entity_id": ship_id}
                      for group in side_ships for ship_id in group],
            targets=[{"role": "opposing_faction", "entity_id": factions[1]}],
            data={"battle_number": battle_number, "trigger_event_id": trigger.id,
                  "factions": factions, "commanders": captains,
                  "ships_by_side": side_ships, "doctrines": doctrine_names,
                  "legend_count": sum(world.entities[captain].get("tier") == "LEGEND"
                                      for captain in captains),
                  "rounds_planned": self.data["rules"]["rounds"]},
            caused_by=[trigger.id, *recognition_events],
            causes_tags=["set_piece_battle", "legendary_commanders"],
            magnitude=1.0,
        )
        morale = [self._fleet_morale(world, group) for group in side_ships]
        totals = [0, 0]
        momentum = [0, 0]
        previous_event_id = battle_event.id
        duel_event_id = None
        for round_number in range(1, self.data["rules"]["rounds"] + 1):
            reinforcements = []
            if round_number == self.data["rules"]["reinforcement_round"]:
                for side in range(2):
                    candidate_ships = self._nearby_fleet_ships(
                        world, factions[side], side_ships[side][0], trigger.location)
                    added = [ship_id for ship_id in candidate_ships if ship_id not in side_ships[side]]
                    if added:
                        side_ships[side].extend(added)
                        reinforcements.append({"side": side, "ships": added})
                        reinforcement_event = world.event_log.append(
                            tick=world.clock.tick, event_type="BATTLE_REINFORCEMENT",
                            location=trigger.location,
                            actors=[{"role": "reinforcement_ship", "entity_id": ship_id}
                                    for ship_id in added],
                            targets=[{"role": "reinforced_commander", "entity_id": captains[side]}],
                            data={"battle_event_id": battle_event.id, "round": round_number,
                                  "side": side, "faction_id": factions[side],
                                  "ships": added},
                            caused_by=list(dict.fromkeys([battle_event.id, previous_event_id])),
                            causes_tags=["scheduled_battle_reinforcements"], magnitude=0.55,
                        )
                        previous_event_id = reinforcement_event.id
            power = [self._fleet_power(world, faction_id, captain_id, ship_ids, doctrine_names[side])
                     for side, (faction_id, captain_id, ship_ids) in enumerate(
                         zip(factions, captains, side_ships))]
            variance = self.data["rules"]["round_variance_permille"]
            stream = world.rng.stream(f"battle-round:{battle_event.id}:{round_number}")
            rolls = [1000 - variance + stream.randbelow(2 * variance + 1) for _ in range(2)]
            scores = [power[side] * rolls[side] // 1000 + momentum[side] for side in range(2)]
            winner = 0 if scores[0] > scores[1] else 1 if scores[1] > scores[0] else None
            morale_before = list(morale)
            if winner is not None:
                loser = 1 - winner
                swing = self.data["rules"]["morale_swing_per_round"]
                morale[winner] = min(100, morale[winner] + swing)
                morale[loser] = max(0, morale[loser] - swing)
                if morale[loser] <= self.data["rules"]["morale_cascade_threshold"]:
                    morale[loser] = max(0, morale[loser] - self.data["rules"]["morale_cascade_loss"])
            round_event = world.event_log.append(
                tick=world.clock.tick, event_type="BATTLE_ROUND", location=trigger.location,
                actors=[{"role": "side_a_commander", "entity_id": captains[0]},
                        {"role": "side_b_commander", "entity_id": captains[1]}],
                targets=[{"role": "opposing_fleet_commander", "entity_id": captains[1]}],
                data={"battle_event_id": battle_event.id, "round": round_number,
                      "factions": factions, "ships_by_side": side_ships,
                      "power": power, "rolls_permille": rolls, "scores": scores,
                      "winner_side": winner, "morale_before": morale_before,
                      "morale_after": list(morale), "reinforcements": reinforcements,
                      "legend_traits": [
                          {"captain_id": captain_id,
                           "trait_id": self._trait(captain_id, world).get("id"),
                           "round_power_permille": self._trait(captain_id, world).get(
                               "round_power_permille", 1000),
                           "duel_bonus": self._duel_bonus(world.entities[captain_id])}
                          for captain_id in captains]},
                caused_by=list(dict.fromkeys([battle_event.id, previous_event_id])),
                causes_tags=["theater_round", f"round:{round_number}"], magnitude=0.75,
            )
            previous_event_id = round_event.id
            totals[0] += scores[0]
            totals[1] += scores[1]
            if round_number == 2:
                duel_event, duel_winner = self._resolve_legend_duel(
                    world, battle_event, round_event, captains, factions, trigger.location)
                duel_event_id = duel_event.id
                previous_event_id = duel_event.id
                if duel_winner is not None:
                    momentum[duel_winner] = max(power[duel_winner] // 20, 1)
            for side, captain_id in enumerate(captains):
                world.entities[captain_id]["last_event_id"] = round_event.id
        winner_side = (0 if totals[0] > totals[1] else
                       1 if totals[1] > totals[0] else None)
        if winner_side is None:
            outcome = "stalemate"
        else:
            outcome = "victory"
        result = world.event_log.append(
            tick=world.clock.tick, event_type="BATTLE_CONCLUDED", location=trigger.location,
            actors=([{"role": "victorious_commander", "entity_id": captains[winner_side]}]
                    if winner_side is not None else
                    [{"role": "commander", "entity_id": captain} for captain in captains]),
            targets=[{"role": "defeated_or_opposing_faction",
                      "entity_id": factions[1 - winner_side if winner_side is not None else 1]}],
            data={"battle_event_id": battle_event.id, "outcome": outcome,
                  "winner_side": winner_side, "factions": factions,
                  "commanders": captains, "ships_by_side": side_ships,
                  "round_totals": totals, "final_morale": morale,
                  "legend_duel_event_id": duel_event_id},
            caused_by=[battle_event.id, previous_event_id],
            causes_tags=["battle_outcome", outcome], magnitude=0.9,
        )
        for ship_ids in side_ships:
            for ship_id in ship_ids:
                if ship_id in world.entities:
                    world.entities[ship_id]["history"].append(result.id)
        self._apply_persistent_aftermath(
            world, result, factions, captains, side_ships, morale, winner_side)
        self.battle_count += 1
        pair_key = "|".join(sorted(factions))
        self.pair_last_battle_day[pair_key] = day
        year_key = str(year)
        self.battles_per_year[year_key] = self.battles_per_year.get(year_key, 0) + 1
        return battle_event.id

    def _apply_persistent_aftermath(self, world, result, factions: list[str],
                                    captains: list[str], side_ships: list[list[str]],
                                    morale: list[int], winner_side: int | None) -> None:
        rules = self.data["rules"]
        for side, faction_id in enumerate(factions):
            crew_ids = sorted({world.entities[ship_id].get("crew_id")
                               for ship_id in side_ships[side]
                               if ship_id in world.entities
                               and world.entities[ship_id].get("crew_id")
                               in world.crew_ships.crews})
            crews = [world.crew_ships.crews[crew_id] for crew_id in crew_ids]
            if not crews:
                continue
            for crew_id, crew in zip(crew_ids, crews):
                before = crew["morale"]
                crew["morale"] = morale[side]
                if before == morale[side]:
                    continue
                morale_event = world.event_log.append(
                    tick=world.clock.tick, event_type="BATTLE_MORALE_CHANGED",
                    location=result.location,
                    actors=[{"role": "fleet_commander", "entity_id": captains[side]}],
                    targets=[{"role": "affected_crew", "entity_id": crew_id}],
                    data={"battle_event_id": result.data["battle_event_id"],
                          "before": before, "after": morale[side],
                          "side": side, "victory": winner_side == side,
                          "faction_id": faction_id},
                    caused_by=[result.id],
                    causes_tags=["persistent_battle_morale",
                                 "victory" if winner_side == side else "defeat"],
                    magnitude=min(1.0, abs(before - morale[side]) / 100),
                )
                crew["last_event_id"] = morale_event.id
            if winner_side is None or side == winner_side:
                continue
            for ship_id in side_ships[side]:
                if ship_id not in world.entities:
                    continue
                ship = world.entities[ship_id]
                hull_damage = min(rules["defeat_hull_damage_base"],
                                  max(0, ship["hull"]["current"] - 1))
                rigging_damage = min(rules["defeat_rigging_damage_base"],
                                     max(0, ship["rigging"]["current"] - 1))
                if hull_damage:
                    world.crew_ships.damage_ship(world, ship_id, "hull", hull_damage,
                                                 caused_by=result.id)
                if rigging_damage:
                    world.crew_ships.damage_ship(world, ship_id, "rigging", rigging_damage,
                                                 caused_by=result.id)
            rng = world.rng.stream(f"battle-casualty:{result.data['battle_event_id']}:{side}")
            casualty_roll = rng.randbelow(10000)
            available = sorted({member_id for crew in crews for member_id in crew["roster"]
                                if member_id != captains[side] and member_id in world.entities})
            if available and casualty_roll < rules["defeat_casualty_chance_basis_points"]:
                casualty_id = sorted(available)[rng.randbelow(len(available))]
                injury_id = "broken_arm" if casualty_roll % 2 == 0 else "lost_eye"
                injury_event = world.characters.inflict_injury(
                    world, casualty_id, injury_id, caused_by=result.id)
                world.event_log.append(
                    tick=world.clock.tick, event_type="BATTLE_CASUALTY",
                    location=result.location,
                    actors=[{"role": "wounded_crew_member", "entity_id": casualty_id}],
                    targets=[{"role": "defeated_commander", "entity_id": captains[side]}],
                    data={"battle_event_id": result.data["battle_event_id"],
                          "crew_id": world.entities[casualty_id].get("crew_id"),
                          "faction_id": faction_id, "injury_id": injury_id,
                          "fate": "wounded"},
                    caused_by=[result.id, injury_event],
                    causes_tags=["battle_casualty", "persistent_injury"], magnitude=0.55,
                )
            self._political_aftermath(world, result, factions, captains, morale, side)

    def _political_aftermath(self, world, result, factions: list[str], captains: list[str],
                             morale: list[int], defeated_side: int) -> None:
        winner_side = result.data.get("winner_side")
        if winner_side is None:
            amount = max(1, world.politics.data["rules"]["heat_per_hostile_combat"] // 2)
            for side, captain_id in enumerate(captains):
                crew_id = world.entities.get(captain_id, {}).get("crew_id")
                if captain_id in world.entities and crew_id in world.crew_ships.crews:
                    world.politics._emit_heat(
                        world, entity_id=captain_id, crew_id=crew_id, amount=amount,
                        caused_by=result.id, reason="set_piece_battle_stalemate",
                    )
            return
        loser_captain = captains[defeated_side]
        crew_id = world.entities.get(loser_captain, {}).get("crew_id")
        faction_id = factions[defeated_side]
        if loser_captain in world.entities and crew_id in world.crew_ships.crews:
            amount = world.politics.data["rules"]["heat_per_hostile_combat"]
            world.politics._emit_heat(
                world, entity_id=loser_captain, crew_id=crew_id, amount=amount,
                caused_by=result.id, reason="set_piece_battle_defeat",
            )
        power_vacuum = None
        if morale[defeated_side] <= self.data["rules"]["power_vacuum_morale_threshold"]:
            power_vacuum = world.politics.resolve_battle_power_vacuum(
                world, faction_id, defeated_captain=loser_captain, caused_by=result.id)
        if morale[defeated_side] == 0:
            world.politics.dissolve_faction(
                world, faction_id, caused_by=power_vacuum or result.id,
                reason="battle_morale_collapse",
            )

    def _resolve_legend_duel(self, world, battle, round_event, captains: list[str],
                             factions: list[str], location: str) -> tuple[Any, int | None]:
        stats = [world.characters.effective_stat(world.entities[captain], "combat")
                 for captain in captains]
        bonuses = [self._duel_bonus(world.entities[captain]) for captain in captains]
        stream = world.rng.stream(f"battle-duel:{battle.id}")
        rolls = [stream.randbelow(21) - 10 for _ in range(2)]
        scores = [stats[index] + bonuses[index] + rolls[index] for index in range(2)]
        winner = 0 if scores[0] > scores[1] else 1 if scores[1] > scores[0] else None
        outcome = "draw" if winner is None else f"side_{'a' if winner == 0 else 'b'}_edge"
        event = world.event_log.append(
            tick=world.clock.tick, event_type="LEGEND_DUEL", location=location,
            actors=[{"role": "duelist", "entity_id": captains[0]}],
            targets=[{"role": "challenger", "entity_id": captains[1]}],
            data={"battle_event_id": battle.id, "round_event_id": round_event.id,
                  "factions": factions, "combat_stats": stats,
                  "trait_bonuses": bonuses, "rolls": rolls, "scores": scores,
                  "outcome": outcome, "winner_side": winner},
            caused_by=[battle.id, round_event.id],
            causes_tags=["named_legend_duel", outcome], magnitude=0.9,
        )
        return event, winner

    def _fleet_power(self, world, faction_id: str, captain_id: str,
                     ship_ids: list[str], doctrine: str) -> int:
        active = [ship_id for ship_id in ship_ids if ship_id in world.entities]
        power = sum(world.crew_ships.combat_power(world, ship_id)["total"] for ship_id in active)
        doctrine_data = self.data["doctrines"][doctrine]
        trait_power = self._trait(captain_id, world).get("round_power_permille", 1000)
        morale = self._fleet_morale(world, active)
        morale_factor = max(400, (700 + morale * 3) * doctrine_data["morale_permille"] // 1000)
        return max(1, power * doctrine_data["power_permille"] // 1000
                   * trait_power // 1000 * morale_factor // 1000)

    @staticmethod
    def _fleet_morale(world, ship_ids: list[str]) -> int:
        values = [world.crew_ships.crews[world.entities[ship_id]["crew_id"]]["morale"]
                  for ship_id in ship_ids if ship_id in world.entities
                  and world.entities[ship_id].get("crew_id") in world.crew_ships.crews]
        return sum(values) // len(values) if values else 0

    def _trait(self, captain_id: str, world) -> dict[str, Any]:
        ids = world.entities[captain_id].get("legend_traits", [])
        by_id = {trait["id"]: trait for trait in self.data["legend_traits"]}
        return by_id.get(ids[0], {}) if ids else {}

    def _duel_bonus(self, captain: dict[str, Any]) -> int:
        by_id = {trait["id"]: trait for trait in self.data["legend_traits"]}
        return sum(by_id[trait_id]["duel_bonus"] for trait_id in captain.get("legend_traits", [])
                   if trait_id in by_id)

    def validate_state(self) -> None:
        if (type(self.battle_count) is not int or self.battle_count < 0
                or type(self.last_updated_day) is not int or self.last_updated_day < 0
                or not isinstance(self.pair_last_battle_day, dict)
                or not isinstance(self.battles_per_year, dict)):
            raise ValueError("Invalid battle state")
        for pair, day in self.pair_last_battle_day.items():
            if (not isinstance(pair, str) or pair.count("|") != 1
                    or any(not part for part in pair.split("|"))
                    or type(day) is not int or day < 0):
                raise ValueError("Invalid battle cooldown")
        if any(not isinstance(year, str) or not year.isdigit() or year == "0"
               or type(count) is not int or count < 0
               or count > self.data["rules"]["max_battles_per_year"]
               for year, count in self.battles_per_year.items()):
            raise ValueError("Invalid annual battle count")
        if any(not isinstance(faction_id, str) or not isinstance(command, dict)
               or set(command) != {"faction_id", "commander_id", "doctrine", "last_command_event_id"}
               or command["faction_id"] != faction_id
               or command["doctrine"] not in self.data["doctrines"]
               or command["last_command_event_id"] is not None
                  and not isinstance(command["last_command_event_id"], str)
               for faction_id, command in self.fleet_commands.items()):
            raise ValueError("Invalid fleet command")

    def validate_world(self, world) -> None:
        self.validate_state()
        if self.digest != _load_data()[1]:
            raise ValueError("Battle catalog digest mismatch")
        ticks_per_day = (world.config["time"]["hours_per_day"]
                         // world.config["time"]["hours_per_tick"])
        if self.last_updated_day != world.clock.tick // ticks_per_day:
            raise ValueError("Battle state day does not match world clock")
        valid_traits = {trait["id"] for trait in self.data["legend_traits"]}
        for character in (*world.entities.values(), *world.archive.values()):
            if character.get("kind") != "character" or character.get("tier") != "LEGEND":
                continue
            trait_ids = character.get("legend_traits", [])
            if (not isinstance(trait_ids, list) or not trait_ids
                    or any(trait_id not in valid_traits for trait_id in trait_ids)):
                raise ValueError("Legend character has invalid special traits")
            event_id = character.get("legend_since_event_id")
            if event_id is not None and world.event_log.get(event_id).type != "LEGEND_RECOGNIZED":
                raise ValueError("Legend character references a non-recognition event")
        for faction_id, command in self.fleet_commands.items():
            faction = (world.politics.factions.get(faction_id)
                       or world.politics.archived_factions.get(faction_id))
            if faction is None:
                raise ValueError("Fleet command references an unknown faction")
            commander_id = command["commander_id"]
            if commander_id not in world.entities and commander_id not in world.archive:
                raise ValueError("Fleet command references an unknown commander")
            event_id = command["last_command_event_id"]
            if event_id is not None and world.event_log.get(event_id).type != "FLEET_COMMAND_SET":
                raise ValueError("Fleet command references a non-command event")
