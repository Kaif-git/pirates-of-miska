"""Weather-aware multi-route voyages with explicit transit locations."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import math
from typing import Any

from sim.core.ownership import ship_is_commandable

from sim.core.names import living_name_in_use


PPM = 1_000_000
HOURS_PER_DAY = 24


class TravelSystem:
    def __init__(self, geography, weather, transits: dict[str, dict[str, Any]] | None = None,
                 closed_routes: set[str] | list[str] | tuple[str, ...] | None = None) -> None:
        self.geography = geography
        self.weather = weather
        self.transits = {ship_id: dict(state) for ship_id, state in (transits or {}).items()}
        self.closed_routes = set(closed_routes or ())
        if not self.closed_routes.issubset(self.geography.routes):
            raise ValueError("Closed routes must reference known map edges")

    def create_vessel(self, world, name: str, island_id: str, *,
                      speed_permille: int = 1000, navigation: int = 50,
                      condition: int = 100, successor_of: str | None = None,
                      renamed_from: str | None = None) -> tuple[str, str]:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Vessel name must be nonempty")
        name = " ".join(name.split())
        if living_name_in_use(world, name):
            raise ValueError(f"Living character or ship name is already in use: {name}")
        if successor_of is not None and renamed_from is not None:
            raise ValueError("A vessel may specify only one name-continuity reference")
        continuity_ref = successor_of or renamed_from
        archived_name_match = next((entity_id for entity_id, entity in world.archive.items()
                                    if entity.get("kind") == "ship"
                                    and entity.get("name", "").casefold() == name.casefold()), None)
        if archived_name_match is not None and archived_name_match != continuity_ref:
            raise ValueError("A sunk ship name requires successor_of or renamed_from continuity")
        if continuity_ref is not None:
            prior = world.archive.get(continuity_ref)
            if (not prior or prior.get("kind") != "ship"
                    or prior.get("sunk_tick") is None):
                raise ValueError("Vessel name continuity must reference a sunk ship")
        if island_id not in self.geography.islands:
            raise ValueError(f"Unknown starting island: {island_id}")
        if type(speed_permille) is not int or speed_permille <= 0:
            raise ValueError("Vessel speed must be a positive permille integer")
        if type(navigation) is not int or not 0 <= navigation <= 100:
            raise ValueError("Navigation skill must be in [0, 100]")
        if type(condition) is not int or not 0 <= condition <= 100:
            raise ValueError("Vessel condition must be in [0, 100]")
        ship_id = world.ids.next("ship")
        vessel = {
            "id": ship_id,
            "kind": "ship",
            "name": name,
            "speed_permille": speed_permille,
            "navigation": navigation,
            "condition": condition,
            "location": {"kind": "island", "id": island_id},
            "legal_owner": None,
            "controller_crew_id": None,
        }
        world.entities[ship_id] = vessel
        created = world.event_log.append(
            tick=world.clock.tick,
            event_type="SHIP_CREATED",
            location=island_id,
            targets=[{"role": "created_vessel", "entity_id": ship_id}],
            data={"name": vessel["name"], "speed_permille": speed_permille,
                  "navigation": navigation, "condition": condition,
                  **({"successor_of": successor_of} if successor_of else {}),
                  **({"renamed_from": renamed_from} if renamed_from else {})},
            caused_by=["event_000001"],
            causes_tags=["world_population"],
            magnitude=0.1,
        )
        vessel["created_event_id"] = created.id
        vessel["ownership_event_id"] = created.id
        vessel["captured_by_crew_id"] = None
        return ship_id, created.id

    def start_voyage(self, world, ship_id: str, destination: str, *,
                     caused_by: str | list[str] | tuple[str, ...]) -> str:
        if ship_id not in world.entities or world.entities[ship_id].get("kind") != "ship":
            raise ValueError(f"Unknown active vessel: {ship_id}")
        if ship_id in self.transits:
            raise ValueError(f"Vessel is already in transit: {ship_id}")
        vessel = world.entities[ship_id]
        if not ship_is_commandable(world, ship_id):
            raise ValueError("Vessel lacks a valid onboard controller crew")
        location = vessel.get("location")
        if not isinstance(location, dict) or location.get("kind") != "island":
            raise ValueError("Vessel must be berthed at an island before departure")
        origin = location.get("id")
        if destination == origin:
            raise ValueError("Voyage destination must differ from origin")
        parent_ids = [caused_by] if isinstance(caused_by, str) else list(caused_by)
        if not parent_ids:
            raise ValueError("Voyage must cite at least one causal event")
        # Validate parents before drawing RNG or mutating the vessel.
        for parent_id in parent_ids:
            try:
                world.event_log.get(parent_id)
            except KeyError as exc:
                raise ValueError(f"Unknown cause event: {parent_id}") from exc

        path = self.geography.shortest_path(
            origin, destination,
            edge_cost=lambda route: self._leg_hours(route, vessel, vessel["condition"]),
            blocked_routes=self.closed_routes,
        )
        hazard_plan, route_conditions = self._plan_hazards(world, ship_id, path, vessel)
        route_durations = []
        for route in path:
            delay = sum(item["delay_hours"] for item in hazard_plan if item["route_id"] == route.id)
            route_durations.append(self._leg_hours(route, vessel, route_conditions[route.id]) + delay)
        total_hours = sum(route_durations)
        if total_hours <= 0:
            raise ValueError("Voyage duration must be positive")
        arrival_tick = world.clock.tick + total_hours * world.config["time"]["hours_per_tick"]
        voyage = world.event_log.append(
            tick=world.clock.tick,
            event_type="VOYAGE_STARTED",
            location=path[0].id,
            actors=[{"role": "vessel", "entity_id": ship_id}],
            data={"origin": origin, "destination": destination,
                  "route_ids": [route.id for route in path],
                  "planned_duration_hours": total_hours,
                  "arrival_tick": arrival_tick},
            caused_by=parent_ids,
            causes_tags=["voyage_plan"],
            magnitude=0.25,
        )
        hazard_event_ids = []
        for hazard in hazard_plan:
            if not hazard["triggered"]:
                continue
            event = world.event_log.append(
                tick=world.clock.tick,
                event_type="ROUTE_HAZARD",
                location=hazard["route_id"],
                actors=[{"role": "vessel", "entity_id": ship_id}],
                data={key: value for key, value in hazard.items()
                      if key not in ("triggered", "hazard_id")}
                     | {"hazard_id": hazard["hazard_id"], "triggered": True},
                caused_by=[voyage.id],
                causes_tags=[f"route_hazard:{hazard['hazard_id']}"],
                magnitude=0.3,
            )
            hazard_event_ids.append(event.id)
            vessel["condition"] = max(0, vessel["condition"] - hazard["condition_damage"])

        transit = {
            "ship_id": ship_id,
            "origin": origin,
            "destination": destination,
            "route_ids": [route.id for route in path],
            "route_durations_hours": route_durations,
            "start_tick": world.clock.tick,
            "arrival_tick": arrival_tick,
            "voyage_event_id": voyage.id,
            "hazard_event_ids": hazard_event_ids,
        }
        self.transits[ship_id] = transit
        vessel.pop("location", None)
        world.schedule_event(
            arrival_tick, "VOYAGE_ARRIVED", location=destination,
            actors=[{"role": "vessel", "entity_id": ship_id}],
            data={"origin": origin, "destination": destination,
                  "route_ids": transit["route_ids"]},
            caused_by=[voyage.id, *hazard_event_ids],
            causes_tags=["completed_route"], magnitude=0.2,
        )
        return voyage.id

    def location_at(self, world, ship_id: str, tick: int | None = None) -> dict[str, Any]:
        if ship_id not in world.entities:
            raise ValueError(f"Unknown vessel: {ship_id}")
        transit = self.transits.get(ship_id)
        if transit is None:
            return dict(world.entities[ship_id]["location"])
        at_tick = world.clock.tick if tick is None else tick
        if type(at_tick) is not int or at_tick < transit["start_tick"]:
            raise ValueError("Location query tick precedes voyage departure")
        elapsed_hours = min(
            max(0, (at_tick - transit["start_tick"]) // world.config["time"]["hours_per_tick"]),
            transit["arrival_tick"] - transit["start_tick"],
        )
        elapsed = 0
        route_ids = transit["route_ids"]
        for index, (route_id, duration) in enumerate(zip(route_ids, transit["route_durations_hours"])):
            route = self.geography.routes[route_id]
            if elapsed_hours < elapsed + duration:
                progress = (elapsed_hours - elapsed) * 1000 // duration
                segment_origin = self._path_island(transit, index)
                return {
                    "kind": "route", "id": route_id,
                    "from": segment_origin,
                    "to": self.geography.other_end(route_id, segment_origin),
                    "progress_permille": progress,
                }
            elapsed += duration
        if elapsed_hours >= sum(transit["route_durations_hours"]):
            return {"kind": "island", "id": transit["destination"]}
        raise ValueError("Transit route durations do not cover the requested tick")

    def _path_island(self, transit: dict[str, Any], route_index: int) -> str:
        island_id = transit["origin"]
        for route_id in transit["route_ids"][:route_index]:
            island_id = self.geography.other_end(route_id, island_id)
        return island_id

    def handle_scheduled_event(self, world, scheduled, event) -> None:
        if scheduled.event_type != "VOYAGE_ARRIVED":
            return
        ship_id = scheduled.payload["actors"][0]["entity_id"]
        transit = self.transits.get(ship_id)
        if transit is None or transit["arrival_tick"] != scheduled.tick:
            raise ValueError("Scheduled voyage arrival has no matching transit")
        world.entities[ship_id]["location"] = {
            "kind": "island", "id": transit["destination"],
        }
        del self.transits[ship_id]
        world.crew_ships.on_commanded_ship_arrival(world, ship_id, event.id)

    def snapshot(self) -> dict[str, Any]:
        return {"transits": {ship_id: self.transits[ship_id]
                             for ship_id in sorted(self.transits)},
                "closed_routes": sorted(self.closed_routes)}

    def validate_state(self, world) -> None:
        expected_arrivals = set()
        for ship_id, transit in self.transits.items():
            if (ship_id not in world.entities or world.entities[ship_id].get("kind") != "ship"
                    or "location" in world.entities[ship_id]
                    or transit.get("ship_id") != ship_id
                    or transit.get("origin") not in self.geography.islands
                    or transit.get("destination") not in self.geography.islands
                    or not transit.get("route_ids")
                    or len(transit["route_ids"]) != len(transit.get("route_durations_hours", []))
                    or any(route_id not in self.geography.routes for route_id in transit["route_ids"])
                    or any(type(duration) is not int or duration <= 0
                           for duration in transit["route_durations_hours"])
                    or type(transit.get("start_tick")) is not int
                    or type(transit.get("arrival_tick")) is not int
                    or transit["arrival_tick"] <= transit["start_tick"]):
                raise ValueError(f"Invalid transit state for vessel {ship_id}")
            route_origin = transit["origin"]
            for route_id in transit["route_ids"]:
                route_origin = self.geography.other_end(route_id, route_origin)
            if route_origin != transit["destination"]:
                raise ValueError(f"Transit route does not reach its destination: {ship_id}")
            if transit["arrival_tick"] != (
                    transit["start_tick"]
                    + sum(transit["route_durations_hours"])
                    * world.config["time"]["hours_per_tick"]):
                raise ValueError(f"Transit arrival tick disagrees with route durations: {ship_id}")
            voyage_event = world.event_log.get(transit["voyage_event_id"])
            if voyage_event.type != "VOYAGE_STARTED" or ship_id not in {
                    actor["entity_id"] for actor in voyage_event.actors}:
                raise ValueError(f"Transit has no matching departure event: {ship_id}")
            if any(world.event_log.get(event_id).type != "ROUTE_HAZARD"
                   for event_id in transit["hazard_event_ids"]):
                raise ValueError(f"Transit has an invalid hazard cause: {ship_id}")
            expected_arrivals.add(ship_id)
        scheduled_arrivals = {}
        for event in world.scheduler.events():
            if event.event_type != "VOYAGE_ARRIVED":
                continue
            actors = event.payload.get("actors", ())
            if len(actors) != 1:
                raise ValueError("Voyage arrival event must identify exactly one vessel")
            ship_id = actors[0].get("entity_id")
            if ship_id in scheduled_arrivals:
                raise ValueError(f"Duplicate scheduled arrival for vessel {ship_id}")
            scheduled_arrivals[ship_id] = event
        if set(scheduled_arrivals) != expected_arrivals:
            raise ValueError("Scheduled voyage arrivals do not match active transit records")
        if any(scheduled_arrivals[ship_id].tick != self.transits[ship_id]["arrival_tick"]
               for ship_id in expected_arrivals):
            raise ValueError("Scheduled arrival tick disagrees with transit state")
        for ship_id, vessel in world.entities.items():
            if vessel.get("kind") == "ship" and ship_id not in self.transits:
                location = vessel.get("location")
                if (not isinstance(location, dict) or location.get("kind") != "island"
                        or location.get("id") not in self.geography.islands):
                    raise ValueError(f"Berthed vessel has an invalid island location: {ship_id}")

    def _leg_hours(self, route, vessel, condition: int) -> int:
        first_region = self.geography.regions[
            self.geography.islands[route.origin].region_id
        ]
        second_region = self.geography.regions[
            self.geography.islands[route.destination].region_id
        ]
        weather_permille = (
            self.weather.speed_permille(first_region.id)
            + self.weather.speed_permille(second_region.id)
        ) // 2
        skill_permille = 500 + 5 * vessel["navigation"]
        condition_permille = 500 + 5 * condition
        speed_permille = max(
            1,
            vessel["speed_permille"] * weather_permille * skill_permille
            * condition_permille // 1_000_000_000,
        )
        base_hours = (route.distance_days * HOURS_PER_DAY * 1000 + speed_permille - 1) // speed_permille
        modifiers = dict(route.season_modifiers)
        seasonal_modifiers = [modifiers[region.season]
                              for region in (first_region, second_region)
                              if region.season in modifiers]
        seasonal_permille = (sum(seasonal_modifiers) // len(seasonal_modifiers)
                             if seasonal_modifiers else 1000)
        return max(1, (base_hours * seasonal_permille + 999) // 1000)

    def _plan_hazards(self, world, ship_id: str, path, vessel):
        stream = world.rng.stream(f"hazards:{ship_id}")
        plan = []
        condition = vessel["condition"]
        route_conditions = {}
        for route in path:
            for hazard_id in sorted(route.hazards):
                hazard = self.geography.hazards[hazard_id]
                chance = self._probability_ppm(hazard["trigger_probability"])
                triggered = stream.randbelow(PPM) < chance
                avoided = False
                delay = 0
                damage = 0
                if triggered:
                    difficulty = hazard["avoidance"]["difficulty"]
                    roll = stream.randbelow(100)
                    avoided = roll + vessel["navigation"] >= difficulty
                    if not avoided:
                        effect = hazard["effect"]
                        delay = int(effect.get("travel_delay_hours", 0))
                        damage_chance = self._probability_ppm(effect.get("hull_damage_chance", 0))
                        if stream.randbelow(PPM) < damage_chance:
                            damage = int(effect.get("hull_damage_points", 0))
                            condition = max(0, condition - damage)
                plan.append({
                    "route_id": route.id,
                    "hazard_id": hazard_id,
                    "triggered": triggered,
                    "avoided": avoided,
                    "delay_hours": delay,
                    "condition_damage": damage,
                })
            route_conditions[route.id] = condition
        return plan, route_conditions

    @staticmethod
    def _probability_ppm(value: Any) -> int:
        try:
            probability = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError("Hazard probability must be numeric") from exc
        if not probability.is_finite() or not 0 <= probability <= 1:
            raise ValueError("Hazard probability must be in [0, 1]")
        return int(probability * PPM)
