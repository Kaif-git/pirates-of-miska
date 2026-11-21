"""Stateful natural events, harvest, disease, monsters, and finite clues."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


DEFAULT_ENVIRONMENT_PATH = Path(__file__).resolve().parents[1] / "data" / "environment.json"


def load_environment_data(path: str | Path = DEFAULT_ENVIRONMENT_PATH) -> tuple[dict[str, Any], str]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load environment data {path}: {exc}") from exc
    validate_environment_data(data)
    digest = hashlib.sha256(json.dumps(
        data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    return data, digest


def validate_environment_data(data: Any) -> None:
    required = {
        "harvest_min_yield_permille", "harvest_max_yield_permille",
        "harvest_failure_threshold_permille", "disease_outbreak_basis_points_per_island_day",
        "disease_spread_basis_points_per_day", "disease_duration_days",
        "disease_productivity_loss_permille", "disease_unrest_increase_per_day",
        "disease_person_exposure_basis_points_per_day",
        "disease_medical_care_reduction_basis_points_per_point",
        "monster_spawn_basis_points_per_day", "monster_max_active", "monster_move_days",
        "monster_hull_damage", "storm_hull_damage", "hurricane_hull_damage",
        "storm_supply_loss_permille", "hurricane_supply_loss_permille",
        "storm_move_days", "storm_duration_days", "storm_region_cooldown_days",
        "treasure_site_count", "rumor_spread_days",
    }
    if (not isinstance(data, dict) or set(data) != {"schema_version", "rules"}
            or type(data["schema_version"]) is not int or data["schema_version"] != 1
            or not isinstance(data["rules"], dict) or set(data["rules"]) != required):
        raise ValueError("Invalid environment catalog schema")
    rules = data["rules"]
    if any(type(value) is not int or value < 0 for value in rules.values()):
        raise ValueError("Environment tuning values must be nonnegative integers")
    if (not 0 < rules["harvest_min_yield_permille"]
            <= rules["harvest_failure_threshold_permille"]
            < rules["harvest_max_yield_permille"]
            or rules["disease_outbreak_basis_points_per_island_day"] > 10_000
            or rules["disease_spread_basis_points_per_day"] > 10_000
            or rules["disease_person_exposure_basis_points_per_day"] > 10_000
            or rules["disease_duration_days"] < 1
            or rules["disease_productivity_loss_permille"] > 1000
            or rules["storm_supply_loss_permille"] > 1000
            or rules["hurricane_supply_loss_permille"] > 1000
            or rules["monster_spawn_basis_points_per_day"] > 10_000
            or rules["monster_max_active"] < 1
            or rules["monster_move_days"] < 1 or rules["storm_move_days"] < 1
            or rules["storm_duration_days"] < 1 or rules["storm_region_cooldown_days"] < 1
            or rules["treasure_site_count"] < 1 or rules["rumor_spread_days"] < 1):
        raise ValueError("Environment tuning is outside supported bounds")


class EnvironmentSystem:
    """Owns persistent, seeded environmental conditions and finite treasure clues."""

    def __init__(self, geography, rng, *, digest: str,
                 last_updated_day: int = 0, harvests: dict[str, dict[str, Any]] | None = None,
                 outbreaks: dict[str, dict[str, Any]] | None = None,
                 monsters: dict[str, dict[str, Any]] | None = None,
                 storms: dict[str, dict[str, Any]] | None = None,
                 last_storm_day_by_region: dict[str, int] | None = None,
                 treasures: dict[str, dict[str, Any]] | None = None,
                 data: dict[str, Any] | None = None) -> None:
        self.geography = geography
        self.rng = rng
        catalog, actual_digest = load_environment_data()
        if digest != actual_digest:
            raise ValueError("Environment catalog changed since snapshot creation")
        self.data = data or catalog
        validate_environment_data(self.data)
        self.rules = self.data["rules"]
        self.digest = digest
        self.last_updated_day = last_updated_day
        self.harvests = self._copy(harvests or {})
        self.outbreaks = self._copy(outbreaks or {})
        self.monsters = self._copy(monsters or {})
        self.storms = self._copy(storms or {})
        self.last_storm_day_by_region = dict(last_storm_day_by_region or {})
        self.treasures = self._copy(treasures or {})
        self.validate_state()

    @staticmethod
    def _copy(value: dict[str, Any]) -> dict[str, Any]:
        return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))

    @classmethod
    def create(cls, geography, rng) -> "EnvironmentSystem":
        _, digest = load_environment_data()
        return cls(geography, rng, digest=digest)

    @classmethod
    def restore(cls, geography, rng, snapshot: dict[str, Any]) -> "EnvironmentSystem":
        required = {"digest", "last_updated_day", "harvests", "outbreaks", "monsters",
                    "storms", "last_storm_day_by_region", "treasures"}
        if not isinstance(snapshot, dict) or set(snapshot) != required:
            raise ValueError("Invalid environment snapshot")
        return cls(geography, rng, **snapshot)

    def seed_world(self, world) -> None:
        if self.treasures:
            raise ValueError("Environment world has already been seeded")
        islands = sorted(self.geography.islands)
        count = min(self.rules["treasure_site_count"], len(islands))
        stream = self.rng.stream("environment:treasure:placement")
        shuffled = list(islands)
        for index in range(len(shuffled) - 1, 0, -1):
            other = stream.randbelow(index + 1)
            shuffled[index], shuffled[other] = shuffled[other], shuffled[index]
        selected = sorted(shuffled[:count])
        for index, island_id in enumerate(selected, 1):
            treasure_id = f"treasure_{index:03d}"
            self.treasures[treasure_id] = {
                "id": treasure_id, "island_id": island_id,
                "rumor_event_id": None, "last_rumor_event_id": None,
                "known_islands": [island_id], "next_spread_day": 0,
                "discovered_event_id": None,
                "discovered_by": None,
            }

    def update_day(self, day: int, world, weather_transitions: list[dict[str, str]] | None = None
                   ) -> list[str]:
        if type(day) is not int or day != self.last_updated_day + 1:
            raise ValueError("Environment days must be consecutive")
        events: list[str] = []
        days_per_year = world.config["time"]["days_per_year"]
        self._seed_treasure_rumors(world, events)
        self._spread_treasure_rumors(world, day, events)
        if (day - 1) % days_per_year == 0:
            year = (day - 1) // days_per_year + 1
            self._roll_harvest(world, day, year, events)
        self._start_storms(world, day, weather_transitions or (), events)
        self._move_storms(world, day, events)
        self._apply_storm_damage(world, day, events)
        self._update_disease(world, day, events)
        self._update_monsters(world, day, events)
        self.last_updated_day = day
        return events

    def _seed_treasure_rumors(self, world, events: list[str]) -> None:
        for treasure_id, treasure in sorted(self.treasures.items()):
            if treasure["rumor_event_id"] is not None:
                continue
            island_id = treasure["island_id"]
            event = world.event_log.append(
                tick=world.clock.tick, event_type="TREASURE_RUMOR", location=island_id,
                data={"treasure_id": treasure_id, "region_id":
                      self.geography.islands[island_id].region_id,
                      "clue": "A marked chart names a nearby island, not the exact cove."},
                causes_tags=["finite_treasure_clue", "regional_rumor"], magnitude=0.25,
            )
            treasure["rumor_event_id"] = event.id
            treasure["last_rumor_event_id"] = event.id
            treasure["next_spread_day"] = self.last_updated_day + self.rules["rumor_spread_days"] + 1
            events.append(event.id)

    def _spread_treasure_rumors(self, world, day: int, events: list[str]) -> None:
        for treasure_id, treasure in sorted(self.treasures.items()):
            if (treasure["rumor_event_id"] is None or treasure["discovered_event_id"] is not None
                    or day < treasure["next_spread_day"]):
                continue
            known = set(treasure["known_islands"])
            frontier = sorted({
                route.destination if route.origin == island_id else route.origin
                for island_id in known for route in self.geography.adjacency[island_id]
            } - known)
            if not frontier:
                continue
            stream = self.rng.stream(f"environment:treasure-rumor:{treasure_id}")
            target = frontier[stream.randbelow(len(frontier))]
            sources = sorted({
                route.origin if route.destination == target else route.destination
                for route in self.geography.adjacency[target]
                if (route.origin if route.destination == target else route.destination) in known
            })
            source = sources[0]
            spread = world.event_log.append(
                tick=world.clock.tick, event_type="TREASURE_CLUE_SPREAD", location=target,
                actors=[{"role": "rumor_source_island", "entity_id": source}],
                targets=[{"role": "rumor_recipient_island", "entity_id": target}],
                data={"treasure_id": treasure_id, "source_island": source,
                      "known_island_count_before": len(known), "finite_clue": True},
                caused_by=[treasure["last_rumor_event_id"]],
                causes_tags=["regional_rumor", "clue_propagation"], magnitude=0.15,
            )
            treasure["known_islands"] = sorted((*known, target))
            treasure["last_rumor_event_id"] = spread.id
            treasure["next_spread_day"] = day + self.rules["rumor_spread_days"]
            events.append(spread.id)

    def _roll_harvest(self, world, day: int, year: int, events: list[str]) -> None:
        rules = self.rules
        span = rules["harvest_max_yield_permille"] - rules["harvest_min_yield_permille"] + 1
        for island_id in sorted(self.geography.islands):
            draw = self.rng.stream(f"environment:harvest:{island_id}").randbelow(span)
            yield_permille = rules["harvest_min_yield_permille"] + draw
            record = {"year": year, "yield_permille": yield_permille, "event_id": None}
            world.economy.islands[island_id]["harvest_yield_permille"] = yield_permille
            if yield_permille <= rules["harvest_failure_threshold_permille"]:
                island = world.economy.islands[island_id]
                event = world.event_log.append(
                    tick=world.clock.tick, event_type="HARVEST_FAILURE", location=island_id,
                    targets=[{"role": "affected_island", "entity_id": island_id}],
                    data={"year": year, "yield_permille": yield_permille,
                          "grain_output_loss_permille": 1000 - yield_permille,
                          "famine_level_before": island["famine_level"]},
                    causes_tags=["yearly_harvest_roll", f"profile:{island['profile']}"],
                    magnitude=min(1.0, (1000 - yield_permille) / 1000),
                )
                record["event_id"] = event.id
                events.append(event.id)
            self.harvests[island_id] = record

    def _start_storms(self, world, day: int, transitions: list[dict[str, str]],
                      events: list[str]) -> None:
        for transition in sorted(transitions, key=lambda row: row["region_id"]):
            state = transition["to"]
            if state not in {"storm", "hurricane"}:
                continue
            region_id = transition["region_id"]
            last_started = self.last_storm_day_by_region.get(region_id, 0)
            if last_started > 0 and day - last_started < self.rules["storm_region_cooldown_days"]:
                continue
            self.last_storm_day_by_region[region_id] = day
            location = min(island.id for island in self.geography.islands.values()
                           if island.region_id == region_id)
            event_type = "HURRICANE" if state == "hurricane" else "STORM"
            storm_id = world.ids.next("storm")
            event = world.event_log.append(
                tick=world.clock.tick, event_type=event_type, location=location,
                data={"storm_id": storm_id, "region_id": region_id,
                      "path_regions": [region_id], "weather_state": state, "day": day,
                      "from_weather": transition["from"]},
                causes_tags=["weather_transition", f"region:{region_id}"], magnitude=0.65,
            )
            self.storms[storm_id] = {
                "storm_id": storm_id, "region_id": region_id, "path_regions": [region_id],
                "weather_state": state, "event_id": event.id, "last_move_day": day,
                "started_day": day,
            }
            events.append(event.id)

    def _move_storms(self, world, day: int, events: list[str]) -> None:
        for storm_id, storm in list(sorted(self.storms.items())):
            region_id = storm["region_id"]
            if day - storm["started_day"] >= self.rules["storm_duration_days"]:
                event = world.event_log.append(
                    tick=world.clock.tick, event_type="STORM_DISSIPATED",
                    location=min(island.id for island in self.geography.islands.values()
                                 if island.region_id == region_id),
                    data={"storm_id": storm_id, "region_id": region_id,
                          "path_regions": list(storm["path_regions"]),
                          "duration_days": day - storm["started_day"]},
                    caused_by=[storm["event_id"]], causes_tags=["weather_cleared"], magnitude=0.25,
                )
                events.append(event.id)
                del self.storms[storm_id]
                continue
            if day - storm["last_move_day"] < self.rules["storm_move_days"]:
                continue
            neighbors = set()
            for route in self.geography.routes.values():
                first_region = self.geography.islands[route.origin].region_id
                second_region = self.geography.islands[route.destination].region_id
                if first_region == region_id and second_region != region_id:
                    neighbors.add(second_region)
                elif second_region == region_id and first_region != region_id:
                    neighbors.add(first_region)
            choices = sorted(neighbors)
            if not choices:
                continue
            next_region = self.rng.stream(f"environment:storm-path:{storm_id}").choice(choices)
            state = storm["weather_state"]
            next_location = min(island.id for island in self.geography.islands.values()
                                if island.region_id == next_region)
            path_regions = [*storm["path_regions"], next_region]
            event = world.event_log.append(
                tick=world.clock.tick, event_type="STORM_PATH_UPDATED", location=next_location,
                data={"storm_id": storm_id, "region_id": next_region,
                      "previous_region": region_id, "path_regions": path_regions,
                      "weather_state": state},
                caused_by=[storm["event_id"]], causes_tags=["storm_track", f"region:{next_region}"],
                magnitude=0.4,
            )
            # Natural weather already drives route speed. Keep storm tracks
            # separate from the daily weather draw so an event does not pin an
            # entire region into severe weather after the storm has moved on.
            storm.update({"region_id": next_region, "path_regions": path_regions,
                          "event_id": event.id, "last_move_day": day})
            events.append(event.id)

    def _apply_storm_damage(self, world, day: int, events: list[str]) -> None:
        for ship_id, ship in sorted(world.entities.items()):
            if ship.get("kind") != "ship" or "hull" not in ship:
                continue
            location = world.location_of(ship_id)
            if location["kind"] == "island":
                region_id = self.geography.islands[location["id"]].region_id
            else:
                route = self.geography.routes[location["id"]]
                region_id = self.geography.islands[route.origin].region_id
            storm = next((record for record in self.storms.values()
                          if record["region_id"] == region_id), None)
            if storm is None or day != storm["last_move_day"]:
                continue
            amount = self.rules["hurricane_hull_damage"] if storm["weather_state"] == "hurricane" \
                else self.rules["storm_hull_damage"]
            if amount <= 0 or ship["hull"]["current"] <= 0:
                continue
            damage_event = world.crew_ships.damage_ship(
                world, ship_id, "hull", amount, caused_by=storm["event_id"])
            events.append(damage_event)
            loss_permille = (self.rules["hurricane_supply_loss_permille"]
                             if storm["weather_state"] == "hurricane"
                             else self.rules["storm_supply_loss_permille"])
            supply_event = world.economy.lose_ship_supplies(
                world, ship_id, loss_permille, caused_by=storm["event_id"],
                weather_state=storm["weather_state"])
            if supply_event:
                events.append(supply_event)

    def _update_disease(self, world, day: int, events: list[str]) -> None:
        rules = self.rules
        for island_id in self.geography.islands:
            world.economy.islands[island_id]["disease_pressure_permille"] = 0
        active_at_start = set(self.outbreaks)
        for island_id in sorted(self.geography.islands):
            outbreak = self.outbreaks.get(island_id)
            if outbreak is None:
                stream = self.rng.stream(f"environment:disease:{island_id}")
                if stream.randbelow(10_000) >= rules[
                        "disease_outbreak_basis_points_per_island_day"]:
                    continue
                event = world.event_log.append(
                    tick=world.clock.tick, event_type="PLAGUE", location=island_id,
                    targets=[{"role": "affected_island", "entity_id": island_id}],
                    data={"strain": "island_fever", "duration_days": rules["disease_duration_days"],
                          "population": world.economy.islands[island_id]["population"]},
                    causes_tags=["endemic_disease_roll", f"region:{self.geography.islands[island_id].region_id}"],
                    magnitude=0.5,
                )
                self.outbreaks[island_id] = {
                    "started_day": day, "remaining_days": rules["disease_duration_days"],
                    "cases": max(1, world.economy.islands[island_id]["population"] // 1000),
                    "event_id": event.id,
                }
                events.append(event.id)
            else:
                if island_id not in active_at_start:
                    continue
                outbreak["remaining_days"] -= 1
                if outbreak["remaining_days"] <= 0:
                    event = world.event_log.append(
                        tick=world.clock.tick, event_type="DISEASE_CONTAINED", location=island_id,
                        data={"strain": "island_fever", "cases": outbreak["cases"],
                              "duration_days": day - outbreak["started_day"]},
                        caused_by=[outbreak["event_id"]], causes_tags=["outbreak_resolved"],
                        magnitude=0.2,
                    )
                    events.append(event.id)
                    del self.outbreaks[island_id]
                    continue
                state = world.economy.islands[island_id]
                state["disease_pressure_permille"] = min(
                    1000, rules["disease_productivity_loss_permille"])
                state["unrest"] = min(
                    100, state["unrest"] + rules["disease_unrest_increase_per_day"])
                self._expose_characters(world, island_id, outbreak, day, events)
                stream = self.rng.stream(f"environment:disease-spread:{island_id}")
                if stream.randbelow(10_000) < rules["disease_spread_basis_points_per_day"]:
                    paths = {
                        route.destination if route.origin == island_id else route.origin: None
                        for route in self.geography.adjacency[island_id]
                    }
                    carrier_causes = {}
                    for ship_id in sorted(world.travel.transits):
                        location = world.location_of(ship_id)
                        if location.get("kind") != "route":
                            continue
                        route = self.geography.routes[location["id"]]
                        if route.origin == island_id:
                            carrier_causes[route.destination] = ship_id
                        elif route.destination == island_id:
                            carrier_causes[route.origin] = ship_id
                    for candidate, ship_id in carrier_causes.items():
                        if ship_id is not None:
                            paths[candidate] = ship_id
                    target = next((candidate for candidate in sorted(paths)
                                   if candidate not in self.outbreaks), None)
                    if target is not None:
                        carrier = paths[target]
                        parents = [outbreak["event_id"]]
                        actors = [{"role": "source_island", "entity_id": island_id}]
                        if carrier:
                            transit = world.travel.transits[carrier]
                            parents.append(transit["voyage_event_id"])
                            actors.append({"role": "carrying_vessel", "entity_id": carrier})
                        spread = world.event_log.append(
                            tick=world.clock.tick, event_type="DISEASE_SPREAD", location=target,
                            actors=actors,
                            targets=[{"role": "newly_affected_island", "entity_id": target}],
                            data={"strain": "island_fever", "source_region":
                                  self.geography.islands[island_id].region_id,
                                  "transmission": "ship" if carrier else "port_contact",
                                  "carrier_ship_id": carrier},
                            caused_by=parents,
                            causes_tags=["contagion", "connected_islands",
                                         *( ["shipborne"] if carrier else ["port_contact"] )],
                            magnitude=0.3,
                        )
                        self.outbreaks[target] = {
                            "started_day": day, "remaining_days": rules["disease_duration_days"],
                            "cases": max(1, world.economy.islands[target]["population"] // 2000),
                            "event_id": spread.id,
                        }
                        events.append(spread.id)

    def _expose_characters(self, world, island_id: str, outbreak: dict[str, Any],
                           day: int, events: list[str]) -> None:
        rules = self.rules
        for character_id, character in sorted(world.entities.items()):
            if character.get("kind") != "character" or character.get("death_tick") is not None:
                continue
            location = character.get("location", {})
            ship_id = character.get("ship_id")
            if ship_id in world.entities:
                location = world.location_of(ship_id)
            if location.get("kind") != "island" or location.get("id") != island_id:
                continue
            if any(injury.get("injury_id") == "chronic_fever" and injury.get("severity", 0) > 0
                   for injury in character.get("health", {}).get("injuries", ())):
                continue
            medicine = 0
            crew_id = character.get("crew_id")
            crew = world.crew_ships.crews.get(crew_id)
            if crew:
                medic_id = crew.get("roles", {}).get("surgeon")
                medic = world.entities.get(medic_id)
                if medic:
                    medicine = world.characters.effective_stat(medic, "medicine")
            exposure = max(
                0, rules["disease_person_exposure_basis_points_per_day"]
                - medicine * rules["disease_medical_care_reduction_basis_points_per_point"])
            stream = world.rng.stream(
                f"environment:disease-exposure:{island_id}:{character_id}:{day}")
            if stream.randbelow(10_000) >= exposure:
                continue
            case = world.event_log.append(
                tick=world.clock.tick, event_type="DISEASE_CASE", location=island_id,
                targets=[{"role": "infected_person", "entity_id": character_id}],
                data={"character_id": character_id, "strain": "island_fever",
                      "outbreak_event_id": outbreak["event_id"],
                      "medical_care": medicine, "exposure_basis_points": exposure},
                caused_by=[outbreak["event_id"]],
                causes_tags=["disease_exposure", "person_level_case"], magnitude=0.4)
            injury_id = world.characters.inflict_injury(
                world, character_id, "chronic_fever", caused_by=case.id)
            character["health"]["injuries"][-1]["disease_outbreak_event_id"] = outbreak["event_id"]
            outbreak["cases"] += 1
            events.extend((case.id, injury_id))

    def _update_monsters(self, world, day: int, events: list[str]) -> None:
        rules = self.rules
        if len(self.monsters) < rules["monster_max_active"]:
            stream = self.rng.stream("environment:monster:spawn")
            if stream.randbelow(10_000) < rules["monster_spawn_basis_points_per_day"]:
                route_id = sorted(self.geography.routes)[stream.randbelow(len(self.geography.routes))]
                monster_id = world.ids.next("monster")
                if monster_id not in self.monsters:
                    route = self.geography.routes[route_id]
                    region_id = self.geography.islands[route.origin].region_id
                    event = world.event_log.append(
                        tick=world.clock.tick, event_type="MONSTER_SIGHTING", location=route_id,
                        data={"monster_id": monster_id, "region_id": region_id,
                              "movement": "along sea lanes"},
                        causes_tags=["regional_hazard_spawn"], magnitude=0.45,
                    )
                    self.monsters[monster_id] = {
                        "route_id": route_id, "region_id": region_id,
                        "last_event_id": event.id, "last_move_day": day,
                    }
                    events.append(event.id)
        for monster_id, monster in sorted(self.monsters.items()):
            if day - monster["last_move_day"] < rules["monster_move_days"]:
                continue
            route = self.geography.routes[monster["route_id"]]
            island_id = route.destination
            adjacent = sorted(self.geography.adjacency[island_id], key=lambda row: row.id)
            if not adjacent:
                continue
            next_route = adjacent[self.rng.stream(
                f"environment:monster-move:{monster_id}").randbelow(len(adjacent))]
            region_id = self.geography.islands[next_route.origin].region_id
            moved = world.event_log.append(
                tick=world.clock.tick, event_type="MONSTER_MOVED", location=next_route.id,
                data={"monster_id": monster_id, "from_route": monster["route_id"],
                      "to_route": next_route.id, "region_id": region_id},
                caused_by=[monster["last_event_id"]], causes_tags=["monster_migration"],
                magnitude=0.2,
            )
            monster.update({"route_id": next_route.id, "region_id": region_id,
                            "last_event_id": moved.id, "last_move_day": day})
            events.append(moved.id)
        for monster_id, monster in sorted(self.monsters.items()):
            for ship_id, ship in sorted(world.entities.items()):
                if (ship.get("kind") != "ship" or "hull" not in ship
                        or world.location_of(ship_id).get("id") != monster["route_id"]):
                    continue
                sighting = world.event_log.append(
                    tick=world.clock.tick, event_type="MONSTER_ENCOUNTER",
                    location=monster["route_id"],
                    actors=[{"role": "vessel", "entity_id": ship_id}],
                    targets=[{"role": "sea_hazard", "entity_id": monster_id}],
                    data={"monster_id": monster_id, "route_id": monster["route_id"],
                          "day": day},
                    caused_by=[monster["last_event_id"]],
                    causes_tags=["regional_hazard", "monster_attack"], magnitude=0.4,
                )
                events.append(sighting.id)
                if self.rules["monster_hull_damage"] > 0:
                    damage = world.crew_ships.damage_ship(
                        world, ship_id, "hull", self.rules["monster_hull_damage"],
                        caused_by=sighting.id)
                    events.append(damage)

    def discover_treasure(self, world, treasure_id: str, character_id: str, *,
                          caused_by: str) -> str:
        treasure = self.treasures.get(treasure_id)
        character = world.entities.get(character_id)
        if (treasure is None or treasure["rumor_event_id"] is None
                or treasure["discovered_event_id"] is not None
                or not character or character.get("kind") != "character"
                or character.get("death_tick") is not None
                or world.location_of(character_id).get("id") != treasure["island_id"]):
            raise ValueError("Treasure is unknown, already found, or not at the searcher's location")
        asset_id = f"treasure_asset:{treasure_id}"
        wealth = character.get("wealth")
        if (not isinstance(wealth, dict) or not isinstance(wealth.get("assets"), list)
                or any(isinstance(asset, dict) and asset.get("id") == asset_id
                       for person in (*world.entities.values(), *world.archive.values())
                       if person.get("kind") == "character"
                       for asset in person.get("wealth", {}).get("assets", []))):
            raise ValueError("Treasure reward ownership is invalid or already transferred")
        world.event_log.get(caused_by)
        event = world.event_log.append(
            tick=world.clock.tick, event_type="TREASURE_FOUND", location=treasure["island_id"],
            actors=[{"role": "discoverer", "entity_id": character_id}],
            targets=[{"role": "treasure", "entity_id": treasure_id}],
            data={"treasure_id": treasure_id, "finite": True,
                  "reward": "charted_relic_cache", "reward_is_monetary": False,
                  "reward_asset_id": asset_id, "reward_transferred_to": character_id},
            caused_by=list(dict.fromkeys((caused_by, treasure["last_rumor_event_id"]))),
            causes_tags=["treasure_discovered", "finite_site"], magnitude=0.65,
        )
        treasure["discovered_event_id"] = event.id
        treasure["discovered_by"] = character_id
        wealth["assets"].append({"id": asset_id, "kind": "charted_relic_cache",
                                  "treasure_id": treasure_id,
                                  "source_event_id": event.id})
        character["last_event_id"] = event.id
        return event.id

    def validate_state(self) -> None:
        if type(self.last_updated_day) is not int or self.last_updated_day < 0:
            raise ValueError("Invalid environment update day")
        for island_id, record in self.harvests.items():
            if (island_id not in self.geography.islands or not isinstance(record, dict)
                    or set(record) != {"year", "yield_permille", "event_id"}
                    or type(record["year"]) is not int or record["year"] < 1
                    or type(record["yield_permille"]) is not int
                    or not self.rules["harvest_min_yield_permille"]
                    <= record["yield_permille"] <= self.rules["harvest_max_yield_permille"]
                    or record["event_id"] is not None
                    and not isinstance(record["event_id"], str)):
                raise ValueError(f"Invalid harvest state: {island_id}")
        for island_id, record in self.outbreaks.items():
            if (island_id not in self.geography.islands or not isinstance(record, dict)
                    or set(record) != {"started_day", "remaining_days", "cases", "event_id"}
                    or any(type(record[key]) is not int or record[key] < minimum
                           for key, minimum in (("started_day", 1), ("remaining_days", 1),
                                                ("cases", 1)))
                    or not isinstance(record["event_id"], str)):
                raise ValueError(f"Invalid disease outbreak: {island_id}")
        for monster_id, record in self.monsters.items():
            if (not isinstance(monster_id, str) or not monster_id.startswith("monster_")
                    or not isinstance(record, dict)
                    or set(record) != {"route_id", "region_id", "last_event_id", "last_move_day"}
                    or record["route_id"] not in self.geography.routes
                    or record["region_id"] not in self.geography.regions
                    or not isinstance(record["last_event_id"], str)
                    or type(record["last_move_day"]) is not int or record["last_move_day"] < 1):
                raise ValueError(f"Invalid monster state: {monster_id}")
        for storm_id, record in self.storms.items():
            if (not isinstance(storm_id, str) or not storm_id.startswith("storm_")
                    or not isinstance(record, dict)
                    or set(record) != {"storm_id", "region_id", "path_regions", "weather_state",
                                       "event_id", "last_move_day", "started_day"}
                    or record["storm_id"] != storm_id
                    or record["region_id"] not in self.geography.regions
                    or not isinstance(record["path_regions"], list) or not record["path_regions"]
                    or any(region not in self.geography.regions for region in record["path_regions"])
                    or record["path_regions"][-1] != record["region_id"]
                    or record["weather_state"] not in {"storm", "hurricane"}
                    or not isinstance(record["event_id"], str)
                    or any(type(record[key]) is not int or record[key] < 1
                           for key in ("last_move_day", "started_day"))):
                raise ValueError(f"Invalid active storm: {storm_id}")
        if (not set(self.last_storm_day_by_region).issubset(self.geography.regions)
                or any(type(day) is not int or day < 1 or day > self.last_updated_day + 1
                       for day in self.last_storm_day_by_region.values())):
            raise ValueError("Invalid regional storm cooldown state")
        if len(self.treasures) > self.rules["treasure_site_count"]:
            raise ValueError("Environment has more finite treasure sites than configured")
        for treasure_id, record in self.treasures.items():
            if (not isinstance(treasure_id, str) or not treasure_id.startswith("treasure_")
                    or not isinstance(record, dict)
                    or set(record) != {"id", "island_id", "rumor_event_id",
                                       "last_rumor_event_id", "known_islands", "next_spread_day",
                                       "discovered_event_id", "discovered_by"}
                    or record["id"] != treasure_id or record["island_id"] not in self.geography.islands
                    or record["rumor_event_id"] is not None
                    and not isinstance(record["rumor_event_id"], str)
                    or record["last_rumor_event_id"] is not None
                    and not isinstance(record["last_rumor_event_id"], str)
                    or not isinstance(record["known_islands"], list)
                    or not record["known_islands"]
                    or any(island not in self.geography.islands for island in record["known_islands"])
                    or record["known_islands"] != sorted(set(record["known_islands"]))
                    or type(record["next_spread_day"]) is not int
                    or record["next_spread_day"] < 0
                    or record["discovered_event_id"] is not None
                    and not isinstance(record["discovered_event_id"], str)
                    or record["discovered_by"] is not None
                    and not isinstance(record["discovered_by"], str)
                    or (record["discovered_event_id"] is None) != (record["discovered_by"] is None)):
                raise ValueError(f"Invalid finite treasure state: {treasure_id}")

    def validate_world(self, world) -> None:
        self.validate_state()
        for island_id, harvest in self.harvests.items():
            if harvest["event_id"] is not None:
                event = world.event_log.get(harvest["event_id"])
                if (event.type != "HARVEST_FAILURE" or event.location != island_id
                        or event.data.get("year") != harvest["year"]):
                    raise ValueError(f"Harvest state lacks its failure event: {island_id}")
        for island_id, outbreak in self.outbreaks.items():
            event = world.event_log.get(outbreak["event_id"])
            if event.type not in {"PLAGUE", "DISEASE_SPREAD"} or event.location != island_id:
                raise ValueError(f"Disease outbreak lacks its source event: {island_id}")
        for monster_id, monster in self.monsters.items():
            event = world.event_log.get(monster["last_event_id"])
            if (event.type not in {"MONSTER_SIGHTING", "MONSTER_MOVED"}
                    or event.data.get("monster_id") != monster_id
                    or event.location != monster["route_id"]):
                raise ValueError(f"Monster lacks its current event: {monster_id}")
        for storm_id, storm in self.storms.items():
            event = world.event_log.get(storm["event_id"])
            if (event.type not in {"STORM", "HURRICANE", "STORM_PATH_UPDATED"}
                    or event.data.get("storm_id") != storm_id
                    or event.data.get("region_id") != storm["region_id"]):
                raise ValueError(f"Storm lacks its current event: {storm_id}")
        for treasure_id, treasure in self.treasures.items():
            if treasure["rumor_event_id"] is not None:
                rumor = world.event_log.get(treasure["rumor_event_id"])
                if rumor.type != "TREASURE_RUMOR" or rumor.data.get("treasure_id") != treasure_id:
                    raise ValueError(f"Treasure lacks its rumor event: {treasure_id}")
            if treasure["last_rumor_event_id"] is not None:
                latest_rumor = world.event_log.get(treasure["last_rumor_event_id"])
                if (latest_rumor.type not in {"TREASURE_RUMOR", "TREASURE_CLUE_SPREAD"}
                        or latest_rumor.data.get("treasure_id") != treasure_id):
                    raise ValueError(f"Treasure rumor chain is invalid: {treasure_id}")
            if treasure["discovered_event_id"] is not None:
                discovery = world.event_log.get(treasure["discovered_event_id"])
                if (discovery.type != "TREASURE_FOUND" or not discovery.caused_by
                        or treasure["last_rumor_event_id"] not in discovery.caused_by
                        or discovery.data.get("treasure_id") != treasure_id):
                    raise ValueError(f"Treasure discovery is not linked to its clue: {treasure_id}")
                asset_id = discovery.data.get("reward_asset_id")
                if asset_id is not None:
                    owner_id = discovery.data.get("reward_transferred_to")
                    owner = world.entities.get(owner_id) or world.archive.get(owner_id)
                    asset = next((item for item in (owner or {}).get("wealth", {}).get("assets", [])
                                  if isinstance(item, dict) and item.get("id") == asset_id), None)
                    if (asset is None or asset.get("treasure_id") != treasure_id
                            or asset.get("source_event_id") != discovery.id
                            or treasure["discovered_by"] != owner_id):
                        raise ValueError(f"Treasure reward transfer is not conserved: {treasure_id}")

    def snapshot(self) -> dict[str, Any]:
        return {"digest": self.digest, "last_updated_day": self.last_updated_day,
                "harvests": self._copy(self.harvests), "outbreaks": self._copy(self.outbreaks),
                "monsters": self._copy(self.monsters), "storms": self._copy(self.storms),
                "last_storm_day_by_region": dict(sorted(self.last_storm_day_by_region.items())),
                "treasures": self._copy(self.treasures)}
