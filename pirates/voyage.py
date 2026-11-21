"""Abstract sea routes, officer consultations, free-time orders and rival fleets.

The overworld is deliberately strategic rather than ship-handling physics. Time
still flows through ShipLife, so ordinary chores, needs and wear continue aboard.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import blake2b
import heapq
import math

from pirates import sea_encounter
from pirates.sea_traffic import (CONTACT_LOSS_DELAY_TICKS, CONTACT_LOSS_RADIUS,
                                 LOOKOUT_RADIUS, NPC_LANE_HOP_TICKS,
                                 relative_observation)
from pirates.sea_conduct import SeaConductLedger
from pirates import sea_transfer
from pirates import sea_trade
from pirates.economy import PORTS, MERCHANTS, CATALOG, build_port
from tales_sim.clock import TICKS_PER_DAY, TICKS_PER_SECOND

SEA_NODES = {
    "verdant": {"name": "Verdant Cay", "xy": (1, 3), "port": "verdant"},
    "shoals": {"name": "The Glass Shoals", "xy": (3, 2)},
    "cinder": {"name": "Cinder Quay", "xy": (6, 4), "port": "cinder"},
    "navy_lane": {"name": "Government Patrol Lane", "xy": (4, 5)},
    "freeport": {"name": "Freeport Exchange", "xy": (8, 2), "port": "freeport"},
    "deepwater": {"name": "The Blue Reach", "xy": (4, 0)},
}
LANES = (("verdant", "shoals"), ("shoals", "cinder"), ("cinder", "navy_lane"),
         ("navy_lane", "verdant"), ("shoals", "navy_lane"), ("shoals", "deepwater"),
         ("deepwater", "freeport"), ("freeport", "cinder"), ("freeport", "navy_lane"))
SEA_ROLES = ("captain", "navigator", "quartermaster", "purser")
CAPTAIN_ACTIONS = ("rest", "study_charts", "inspect_ship", "talk_with_crew")
CREW_ORDERS = ("work_as_needed", "sword_drill", "brawling_drill", "rest")
VOYAGE_STORM_CHANCE_BY_WIND = {
    "Trade wind": 20, "Crosswind": 30, "Headwind": 30, "Light wind": 15,
}
VOYAGE_STORM_DAMAGE = {"moderate": (5, 8), "severe": (10, 16)}


def ticks_to_next_day_boundary(total_ticks):
    """Return exact canonical ticks until GameClock.day advances at midnight."""
    if type(total_ticks) is not int or total_ticks < 0:
        raise ValueError("total_ticks must be a non-negative integer")
    return TICKS_PER_DAY - (total_ticks % TICKS_PER_DAY)


class VoyageSystem:
    def __init__(self):
        self.position = "verdant"
        self.roles = {"captain": None, "navigator": "suri", "quartermaster": "ivo", "purser": "mara"}
        self.voyage = None
        self.captain_action = "rest"
        self.crew_order = {"order": "work_as_needed", "leader": "tavi", "members": ["tavi"]}
        self.last_sim_hour = 0
        self.events = []
        self.arrival_interrupt = False
        self._action_advance_depth = 0
        self.captain_fatigue = 20.0
        self.conduct_ledger = SeaConductLedger()

    def _crew(self, world, sid):
        sailor = world.ship.sailor(sid)
        if not sailor:
            raise ValueError("Choose a current crew member")
        return sailor

    def role_report(self, world):
        crew = world.ship.crew
        nav = world.ship.sailor(self.roles["navigator"])
        captain = world.ship.sailor(self.roles.get("captain"))
        quartermaster = world.ship.sailor(self.roles["quartermaster"])
        purser = world.ship.sailor(self.roles["purser"])
        qname = quartermaster.name if quartermaster else "No quartermaster"
        nav_name = nav.name if nav else "No navigator"
        purser_name = purser.name if purser else "No purser"
        distance = self.route_distance(self.position, "cinder" if self.position != "cinder" else "verdant")
        duration_hours = distance * 0.75
        from pirates.provisioning import (CLEAN_WATER_L_PER_CREW_DAY,
                                          FOOD_KG_PER_CREW_DAY,
                                          forecast_provisions)
        forecast = forecast_provisions(world, target_days=duration_hours / 24)
        water_liters = forecast["water_liters"]
        water_days = forecast["water_days"]
        ready_food_kg = forecast["ready_food_g"] / 1000
        ready_food_days = forecast["ready_food_days"]
        projected_food_kg = forecast["projected_food_g"] / 1000
        food_days = forecast["projected_food_days"]
        warnings = []
        if water_days < duration_hours / 24:
            warnings.append(f"{qname}: clean water is short for this route ({water_days:.1f} days at 2.2 L/crew/day).")
        if ready_food_days < duration_hours / 24:
            if food_days > ready_food_days:
                warnings.append(f"{qname}: ready-to-eat food covers about {ready_food_days:.1f} days; "
                                f"cookable supplies could extend coverage to {food_days:.1f} days.")
            else:
                warnings.append(f"{qname}: food stores cover about {ready_food_days:.1f} days at "
                                f"{FOOD_KG_PER_CREW_DAY:.2f} kg/crew/day.")
        from pirates.provisioning import readiness_rows
        preparation = readiness_rows(world, target_days=duration_hours / 24, forecast=forecast)
        preparation_start = preparation.index("SHIP AND CREW PREPARATION (advisories; not departure gates)")
        supply_readiness = preparation[:preparation_start]
        preparation = preparation[preparation_start:]
        warnings.extend(f"{qname}: {blocker}" for blocker in forecast["blockers"])
        warnings.extend(f"{qname}: {row[len('Next: '):]}"
                        for row in supply_readiness if row.startswith("Next: "))
        if min(water_days, food_days) < 1:
            suggestion = "Verdant Cay" if self.position != "verdant" else "the nearest provision stop"
            warnings.append(f"{qname} suggests a provisioning voyage to {suggestion} before a long run.")
        else:
            suggestion = "Cinder Quay" if self.position != "cinder" else "Verdant Cay"
            warnings.append(f"{qname} suggests {suggestion} for the next stores/trade run.")
        wind = self.wind_at(world.clock.day, self.position)
        if self.voyage and self.voyage.get("status") == "underway":
            next_node = self.voyage["route"][min(self.voyage["leg"] + 1, len(self.voyage["route"]) - 1)]
        else:
            route, _ = self.shortest_route(self.position,
                                           "cinder" if self.position != "cinder" else "verdant")
            next_node = route[1] if len(route) > 1 else self.position
        a, b = SEA_NODES[self.position]["xy"], SEA_NODES[next_node]["xy"]
        heading = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) if next_node != self.position else 0
        from pirates.ship_physics import sailing_forces
        sailing = sailing_forces(world, wind, heading)
        return [f"Appointed crew captain: {captain.name} | leadership {captain.mental['leadership']:.0f}, judgement {captain.mental['intelligence']:.0f}"
                if captain else "Appointed crew captain: unassigned | no command judgement bonus",
                f"Navigator: {nav_name} | Rigging skill {nav.skills['rig']:.1f}" if nav else "Navigator: unassigned | no specialist rigging bonus",
                f"Quartermaster: {qname} | clean water {water_liters:.1f} L ({water_days:.1f}d), "
                f"routine-ready food {ready_food_kg:.2f} kg ({ready_food_days:.1f}d), "
                f"potential food {projected_food_kg:.2f} kg ({food_days:.1f}d)",
                (f"Numbers-skilled purser: {purser.name} | stores-ledger aptitude {purser.personality['diligence']:.0f}/100; repair skill {purser.skills['repair']:.1f}"
                 if purser else "Purser: unassigned | no specialist stores-ledger review"),
                f"Navigator's wind reading: {wind['name']} ({wind['dx']:+d},{wind['dy']:+d}); route estimate {duration_hours:.1f} hours.",
                 f"Provisional sailing estimate: {sailing['sail_force_n']:.0f} N drive, {sailing['target_speed_knots']:.1f} kn target; "
                 f"rigging {sailing['sail_condition']:.0%}, rudder authority {sailing['rudder_authority']:.2f}.",
                 *(warnings or [f"Quartermaster {quartermaster.name}: stores look adequate for the estimate."]),
                 "", *preparation,
                 f"Ration estimate: {CLEAN_WATER_L_PER_CREW_DAY:.1f} L clean water + "
                f"{FOOD_KG_PER_CREW_DAY:.2f} kg food per crew member/day; "
                "cookable estimates protect a one-day drinking reserve; individual needs still simulate."]

    def assign_role(self, world, role, sid):
        if role not in SEA_ROLES:
            raise ValueError("Unknown shipboard officer role")
        if sid is None:
            self.roles[role] = None
            return
        sailor = self._crew(world, sid)
        if sid in (v for k, v in self.roles.items() if k != role):
            raise ValueError("Each officer role needs a different sailor")
        self.roles[role] = sailor.id

    def shortest_route(self, start, end):
        if start not in SEA_NODES or end not in SEA_NODES:
            raise ValueError("Unknown chart location")
        graph = {key: [] for key in SEA_NODES}
        for a, b in LANES:
            x1, y1 = SEA_NODES[a]["xy"]
            x2, y2 = SEA_NODES[b]["xy"]
            distance = math.hypot(x2 - x1, y2 - y1)
            graph[a].append((b, distance))
            graph[b].append((a, distance))
        queue = [(0.0, start, (start,))]
        best = {}
        while queue:
            distance, node, route = heapq.heappop(queue)
            if node in best:
                continue
            best[node] = distance
            if node == end:
                return list(route), distance
            for nxt, cost in graph[node]:
                if nxt not in best:
                    heapq.heappush(queue, (distance + cost, nxt, route + (nxt,)))
        raise ValueError("No charted lane connects those destinations")

    def route_distance(self, start, end):
        return self.shortest_route(start, end)[1]

    def route_options(self, start, end, *, include_detours=False):
        """Return distinct chart paths, including equal-length waypoint routes."""
        shortest, shortest_distance = self.shortest_route(start, end)
        options = {tuple(shortest): {"route": shortest, "distance": shortest_distance,
                                     "waypoint": shortest[1] if len(shortest) > 2 else None}}
        for waypoint in SEA_NODES:
            if waypoint in (start, end):
                continue
            first, first_distance = self.shortest_route(start, waypoint)
            second, second_distance = self.shortest_route(waypoint, end)
            route = first + second[1:]
            if len(set(route)) != len(route):
                continue
            distance = first_distance + second_distance
            if (include_detours or math.isclose(
                    distance, shortest_distance, rel_tol=1e-9, abs_tol=1e-9)):
                options.setdefault(tuple(route), {"route": route, "distance": distance,
                                                   "waypoint": waypoint})
        return sorted(options.values(), key=lambda row: (row["route"] != shortest, row["route"]))

    def map_coordinate(self):
        v = self.voyage
        if not v or v["status"] in ("arrived", "aborted"):
            return SEA_NODES[self.position]["xy"]
        route = v["route"]
        a = SEA_NODES[route[v["leg"]]]["xy"]
        b = SEA_NODES[route[min(v["leg"] + 1, len(route) - 1)]]["xy"]
        p = max(0.0, min(1.0, v["leg_progress"]))
        cross = v.get("sailing", {}).get("cross_track", 0.0)
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = max(0.001, math.hypot(dx, dy))
        return (a[0] + dx * p - dy / length * cross,
                a[1] + dy * p + dx / length * cross)

    @staticmethod
    def wind_at(day, node):
        vectors = (("Trade wind", 1, 0), ("Crosswind", 0, 1), ("Headwind", -1, 0), ("Light wind", 0, 0))
        name, dx, dy = vectors[(day + sum(map(ord, node))) % len(vectors)]
        return {"name": name, "dx": dx, "dy": dy}

    def plan(self, world, destination, preferred_waypoint=None):
        if self.voyage and self.voyage["status"] in ("underway", "storm"):
            raise ValueError("A voyage is already underway")
        if destination not in SEA_NODES or destination == self.position:
            raise ValueError("Choose a different chart location")
        if (getattr(world, "scenario_id", None) == "lawful-trader-mvp"
                and SEA_NODES[destination].get("port") not in PORTS):
            raise ValueError("Choose one of the three trading ports as your destination")
        route, distance = self.shortest_route(self.position, destination)
        if preferred_waypoint and preferred_waypoint not in route:
            # Allow a deliberate detour only when both legs are charted.
            first, d1 = self.shortest_route(self.position, preferred_waypoint)
            second, d2 = self.shortest_route(preferred_waypoint, destination)
            route, distance = first + second[1:], d1 + d2
        nav = world.ship.sailor(self.roles["navigator"])
        captain = world.ship.sailor(self.roles.get("captain"))
        purser = world.ship.sailor(self.roles["purser"])
        qmaster = world.ship.sailor(self.roles["quartermaster"])
        wind = self.wind_at(world.clock.day, self.position)
        chart_notes = self.voyage.get("chart_notes", 0) if self.voyage and self.voyage["status"] == "arrived" else 0
        nav_skill = nav.skills["rig"] if nav else 0
        captain_judgement = captain.mental["intelligence"] * 0.0005 if captain else 0
        duration = max(1800, round(distance * 2700 / (1 + nav_skill * 0.025 + captain_judgement + (0.1 if wind["dx"] else 0)
                                                          + min(0.15, chart_notes * 0.01))))
        from pirates.provisioning import forecast_provisions
        forecast = forecast_provisions(world, target_days=duration / 86400)
        water_liters = forecast["water_liters"]
        food_kg = forecast["projected_food_g"] / 1000
        water_days = forecast["water_days"]
        ration_days = forecast["projected_food_days"]
        storm_forecast = ({"node": route[1],
                           "chance_percent": VOYAGE_STORM_CHANCE_BY_WIND[wind["name"]],
                           "confidence": "low"} if len(route) >= 3 else None)
        return {"route": route, "distance": round(distance, 2), "duration_seconds": duration,
                "duration_hours": round(duration / 3600, 1), "wind": wind,
                "storm_forecast": storm_forecast,
                "navigator": nav.name if nav else "Unassigned", "quartermaster": qmaster.name if qmaster else "Unassigned",
                "purser": purser.name if purser else "Unassigned",
                "water_liters": round(water_liters, 1), "food_kg": round(food_kg, 2),
                "ready_food_kg": round(forecast["ready_food_g"] / 1000, 2),
                "ready_food_days": round(forecast["ready_food_days"], 1),
                "water_days": round(water_days, 1), "food_days": round(ration_days, 1)}

    @staticmethod
    def _make_storm_event(world, plan):
        forecast = plan.get("storm_forecast")
        if not forecast:
            return None
        storm_seed = blake2b(
            f"{world.clock.total_ticks}:{'|'.join(plan['route'])}:{plan['wind']['name']}".encode(),
            digest_size=8,
        ).digest()
        trigger_roll = int.from_bytes(storm_seed, "big") % 100
        chance = forecast["chance_percent"]
        return {
            "event_id": storm_seed.hex(), "node": plan["route"][1],
            "storm_lane": plan["route"][1:3], "trigger_roll": trigger_roll,
            "chance_percent": chance, "triggered": trigger_roll < chance,
            "stage": "forecast", "choice": None,
        }

    def confirm(self, world, destination, preferred_waypoint=None):
        plan = self.plan(world, destination, preferred_waypoint)
        a = SEA_NODES[plan["route"][0]]["xy"]
        b = SEA_NODES[plan["route"][1]]["xy"]
        heading = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 360
        storm_event = self._make_storm_event(world, plan)
        self.voyage = {**plan, "status": "underway", "leg": 0, "leg_progress": 0.0,
                       "elapsed_seconds": 0, "destination": destination, "encounter": None,
                       "deck_visit": None,
                       "initial_estimate_seconds": plan["duration_seconds"],
                       "storm_event": storm_event,
                       "departure_snapshot": self._ledger_checkpoint(world),
                       "arrival_snapshot": None, "outcomes": [],
                       "sailing": {"speed_mps": 0.0, "heading_deg": heading,
                                   "rudder_deg": 0.0, "cross_track": 0.0,
                                   "drive_n": 0.0, "resistance_n": 0.0,
                                   "acceleration_mps2": 0.0, "target_speed_mps": 0.0}}
        self.position = plan["route"][0]
        self._record(f"Captain ordered a voyage to {SEA_NODES[destination]['name']} via {' → '.join(SEA_NODES[n]['name'] for n in plan['route'])}.")
        world.message(f"Route ordered with {plan['navigator']} navigating and {plan['quartermaster']} reviewing stores. {plan['duration_hours']} hour estimate.")
        return plan

    def _record(self, text):
        self.events = (self.events + [text])[-60:]
        if self.voyage and self.voyage.get("status") in (
                "underway", "encounter", "storm", "arrived"):
            self.voyage["outcomes"] = (self.voyage.get("outcomes", []) + [text])[-30:]

    @staticmethod
    def _ledger_checkpoint(world):
        return {
            "ticks": world.clock.total_ticks,
            "money": world.economy.money,
            "cargo": deepcopy(world.economy.cargo),
            "hull": world.ship.hull,
            "rigging": world.ship.rigging,
            "contracts": [
                {"offer_id": row["offer_id"], "destination": row["destination"],
                 "deadline_day": row["deadline_day"], "status": row["status"],
                 "escrow": row["escrow"]}
                for row in world.economy.contracts
            ],
        }

    def arrival_report(self, world):
        """Stable, read-only arrival summary; never settles contracts or advances time."""
        voyage = self.voyage
        if not voyage or voyage.get("status") != "arrived":
            return ["No completed voyage is available to report."]
        departure = voyage.get("departure_snapshot")
        arrival = voyage.get("arrival_snapshot")
        rows = [f"Destination: {SEA_NODES[voyage['destination']]['name']}"]
        if departure and arrival:
            actual_seconds = max(0, (arrival["ticks"] - departure["ticks"]) // 100)
            from tales_sim.clock import TICKS_PER_DAY, format_duration
            rows += [f"Actual elapsed: {format_duration(actual_seconds * 100)} | "
                     f"departure estimate: {format_duration(voyage.get('initial_estimate_seconds', voyage['duration_seconds']) * 100)}",
                     f"Funds: {departure['money']} → {arrival['money']} Marks "
                     f"({arrival['money'] - departure['money']:+d})",
                     f"Hull: {departure['hull']:.0f} → {arrival['hull']:.0f} | "
                     f"rigging: {departure['rigging']:.0f} → {arrival['rigging']:.0f}"]
            item_ids = sorted(set(departure["cargo"]) | set(arrival["cargo"]))
            changes = [f"{item}: {departure['cargo'].get(item, 0)} → {arrival['cargo'].get(item, 0)} "
                       f"({arrival['cargo'].get(item, 0) - departure['cargo'].get(item, 0):+d})"
                       for item in item_ids
                       if departure["cargo"].get(item, 0) != arrival["cargo"].get(item, 0)]
            rows.append("Cargo changes: " + ("; ".join(changes) if changes else "none"))
            departure_contracts = {row["offer_id"]: row for row in departure["contracts"]}
            arrival_contracts = {row["offer_id"]: row for row in arrival["contracts"]}
            objectives = [row for row in departure_contracts.values()
                          if row["destination"] == voyage["destination"]
                          and row["status"] == "active"]
            if objectives:
                rows.append("Delivery objectives (arrival does not settle these):")
                for objective in objectives:
                    state = arrival_contracts.get(objective["offer_id"])
                    if state and state["status"] == "active":
                        arrival_day = arrival["ticks"] // TICKS_PER_DAY + 1
                        result = ("arrived; visit the harbor office to settle"
                                  if arrival_day <= state["deadline_day"]
                                  else "deadline passed; contract expired at arrival")
                        current = next((row for row in world.economy.contracts
                                        if row["offer_id"] == objective["offer_id"]), None)
                        if current and current["status"] != "active":
                            result += f"; current ledger: {current['status']}"
                    else:
                        result = state["status"] if state else "no longer in arrival ledger"
                    rows.append(f"  {objective['offer_id']}: {result}")
            else:
                if world.scenario_id == "lawful-trader-mvp":
                    rows.append("No delivery objective is required for this passage.")
                else:
                    rows.append("Delivery objective: none recorded for this destination.")
        else:
            rows.append("Legacy save: departure/arrival ledger checkpoints are unavailable.")
        undertakings = sorted(
            ((sailor_id, row["undertaking"]) for sailor_id, row in
             world.economy.employment.records.items() if row.get("undertaking")),
            key=lambda pair: pair[0])
        if undertakings:
            rows.append("Crew undertakings:")
            for sailor_id, undertaking in undertakings:
                sailor = world.ship.sailor(sailor_id)
                name = sailor.name if sailor else next(
                    (row["name"] for row in world.ship.crew_history if row["id"] == sailor_id),
                    sailor_id)
                where = PORTS[undertaking["destination"]]["name"]
                if undertaking["status"] == "open":
                    rows.append(f"  {name}: reach {where} by day {undertaking['deadline_day']} "
                                "(open; needs them alive and aboard while ashore there).")
                else:
                    rows.append(f"  {name}: {where} by day {undertaking['deadline_day']} — "
                                f"{undertaking['status']} on day {undertaking['closed_day']}.")
        reviews = [world.ship.sailor(sailor_id) for sailor_id, row in
                   world.economy.employment.records.items()
                   if row["status"] == "active" and row["review"]
                   and row["review"]["status"] == "offered"]
        reviews = [sailor.name for sailor in reviews if sailor is not None]
        if reviews:
            rows.append(f"Follow-up: {', '.join(reviews)} asked to discuss amended terms "
                        "at the crew roster ashore.")
        outcomes = voyage.get("outcomes", [])
        rows.append("Consequential voyage outcomes:")
        rows.extend(f"  {text}" for text in outcomes[-8:])
        if not outcomes:
            rows.append("  No recorded contact, squall, or other voyage outcome.")
        active = [row for row in world.economy.contracts if row["status"] == "active"]
        if world.scenario_id == "lawful-trader-mvp":
            rows.append("No arrival settlement is required; trade, repair, recruit or sail again when you choose.")
        elif active:
            rows.append("Follow-up: review the active delivery ledger at a harbor office before accepting another route.")
        else:
            rows.append("Follow-up: inspect the local harbor office for a new finite delivery opportunity.")
        return rows

    def step_second(self, world, npc_sighting=None):
        if not self.voyage or self.voyage["status"] != "underway":
            return
        v = self.voyage
        v["elapsed_seconds"] += 1
        segment_count = len(v["route"]) - 1
        sailing = v.setdefault("sailing", self._initial_sailing(v))
        course_deg = self._course_heading(v)
        from pirates.ship_physics import sailing_forces
        force = sailing_forces(world, v["wind"], course_deg, sailing["speed_mps"])
        mass_factor = max(0.45, min(1.0, 54682 / max(54682, force["mass_kg"])))
        accel = max(-0.006, min(0.006, force["acceleration_mps2"]))
        speed = max(0.0, sailing["speed_mps"] + accel * mass_factor)
        # Treat the chart schedule as the reference-condition ETA. Actual sail
        # force/load scales progress around it; rudder turns introduce cross-track
        # drift and reduce progress rather than replacing the strategic route.
        sailing.update({"speed_mps": round(speed, 5), "drive_n": force["sail_force_n"],
                       "resistance_n": force["resistance_n"],
                       "acceleration_mps2": force["acceleration_mps2"],
                       "target_speed_mps": force["target_speed_mps"]})
        heading_goal = (course_deg + sailing["rudder_deg"]) % 360
        heading_error = self._angle_delta(heading_goal, sailing["heading_deg"])
        turn_rate = min(abs(heading_error), 0.45 * force["rudder_authority"]
                        * max(0.2, min(1.0, speed / 0.8)))
        sailing["heading_deg"] = (sailing["heading_deg"] + math.copysign(turn_rate, heading_error)) % 360
        sailing["heading_error_deg"] = round(self._angle_delta(course_deg, sailing["heading_deg"]), 2)
        speed_ratio = speed / 0.72
        aligned = max(0.15, math.cos(math.radians(sailing["heading_error_deg"])))
        if v["duration_seconds"] <= 30:  # explicitly accelerated/synthetic test routes
            speed_ratio = 1.0
            aligned = 1.0
        progress = segment_count / v["duration_seconds"] * max(0.0, speed_ratio) * aligned
        if v["duration_seconds"] <= 30 and v["elapsed_seconds"] >= v["duration_seconds"]:
            progress = max(progress, segment_count - (v["leg"] + v["leg_progress"]))
        sailing["cross_track"] = max(-2.0, min(2.0, sailing["cross_track"] +
            speed_ratio * math.sin(math.radians(sailing["heading_error_deg"])) / max(1, segment_count) * 0.02))
        scaled_progress = v["leg"] + v["leg_progress"] + progress
        v["leg_progress"] += progress
        # NPC simulation is coarse and deterministic; sightings interrupt in the
        # second ship life reaches them rather than letting a 10-minute wait pass.
        npc_sighting = npc_sighting if v["elapsed_seconds"] > 1800 else None
        if npc_sighting:
            v["status"] = "encounter"
            v["encounter"] = npc_sighting
            sea_encounter.contact_of(npc_sighting)
            self._record(f"Lookouts sight {npc_sighting['name']} nearby at {SEA_NODES[self.position]['name']}.")
            world.message(f"LOOKOUTS: {npc_sighting['name']} nearby! The voyage pauses; the ship is yours to command.")
            return
        storm = v.get("storm_event")
        if storm and storm["stage"] == "forecast" and self.position == storm["node"] and v["leg"] >= 1:
            if storm["triggered"]:
                storm["stage"] = "active"
                v["status"] = "storm"
                self._record(f"Lookouts report a squall at {SEA_NODES[storm['node']]['name']}; the captain must choose a response.")
                world.message(f"SQUALL: unsettled weather closes in near {SEA_NODES[storm['node']]['name']}. Choose a detour or press on.")
                return
            storm["stage"] = "clear"
            self._record(f"The uncertain squall forecast passed without a storm near {SEA_NODES[storm['node']]['name']}.")
        if v["leg_progress"] >= 1:
            v["leg"] += 1
            if v["leg"] >= len(v["route"]) - 1:
                # Arrival is terminal for this route; clamp the final partial
                # leg even when a coarse/accelerated step overshoots it.
                v["leg_progress"] = 1.0
                self.position = v["destination"]
                v["status"] = "arrived"
                self.arrival_interrupt = True
                port = SEA_NODES[self.position].get("port")
                if port:
                    world.economy.port = port
                    build_port(world)
                    world.restore_harbor(port)
                    world.at_sea = False
                    world.player.x, world.player.y = 76, 25
                self._record(f"Arrived at {SEA_NODES[self.position]['name']} after {v['elapsed_seconds'] // 60} minutes.")
                world.message(f"Arrived: {SEA_NODES[self.position]['name']}. The ship and crew simulation continue in the main game.")
                v["arrival_snapshot"] = self._ledger_checkpoint(world)
            else:
                self.position = v["route"][v["leg"]]
                v["leg_progress"] = max(0.0, scaled_progress - v["leg"])
                sailing["cross_track"] *= 0.5
                sailing["heading_deg"] = self._course_heading(v)
        if v["elapsed_seconds"] % 600 == 0:
            self._free_time_tick(world)

    def _free_time_tick(self, world):
        action = self.captain_action
        # Captain is not a simulated sailor: orders grant small, bounded effects.
        if action == "rest":
            self.captain_fatigue = max(0, self.captain_fatigue - 1.5)
        elif action == "study_charts":
            self.captain_fatigue = min(100, self.captain_fatigue + 0.3)
            self.voyage["chart_notes"] = self.voyage.get("chart_notes", 0) + 1
        elif action == "inspect_ship":
            self.captain_fatigue = min(100, self.captain_fatigue + 0.2)
            self.voyage["inspections"] = self.voyage.get("inspections", 0) + 1
        elif action == "talk_with_crew":
            self.captain_fatigue = min(100, self.captain_fatigue + 0.1)
            for c in world.ship.crew:
                c.loneliness = max(0, c.loneliness - 0.25)

    def crew_activity(self, world, sailor):
        if not self.voyage or self.voyage["status"] != "underway":
            return False
        order = self.crew_order
        if sailor.id not in order["members"] or order["order"] in ("work_as_needed", "rest"):
            if sailor.id in order["members"] and order["order"] == "rest":
                sailor.activity = "Resting between sea watches"
                sailor.energy = min(100, sailor.energy + 0.001)
                sailor.fatigue = max(0, sailor.fatigue - 0.001)
                return True
            return False
        if (sailor.health <= 0 or sailor.job_id is not None or sailor.intent
                or max(sailor.hunger, sailor.thirst, sailor.sleep_debt) >= 65):
            return False
        drill = order["order"].replace("_", " ")
        leader = world.ship.sailor(order["leader"])
        sailor.activity = f"{drill} with {order['leader']}"
        sailor.energy = max(0, sailor.energy - 0.0008 * (1 - leader.mental["discipline"] * 0.002))
        sailor.boredom = max(0, sailor.boredom - 0.0013)
        if self.voyage["elapsed_seconds"] % 60 == 0:
            sailor.think(f"Practised {drill} under {order['leader']}.", 0.01)
            if order["order"] == "sword_drill":
                training = 0.1 * (0.5 + leader.mental["leadership"] / 100)
                world.economy.weapon_xp[sailor.id] = world.economy.weapon_xp.get(sailor.id, 0) + training
        return True

    def set_captain_action(self, action):
        if action not in CAPTAIN_ACTIONS:
            raise ValueError("Unknown captain free-time action")
        self.captain_action = action

    def set_rudder(self, degrees_from_course):
        if type(degrees_from_course) not in (int, float) or not math.isfinite(degrees_from_course) \
                or not -30 <= degrees_from_course <= 30:
            raise ValueError("Rudder order must be between -30 and +30 degrees")
        if not self.voyage or self.voyage["status"] != "underway":
            raise ValueError("Rudder orders require an underway voyage")
        self.voyage.setdefault("sailing", self._initial_sailing(self.voyage))["rudder_deg"] = float(degrees_from_course)

    @staticmethod
    def _angle_delta(target, current):
        return (target - current + 180) % 360 - 180

    @staticmethod
    def _course_heading(voyage):
        route, leg = voyage["route"], voyage["leg"]
        if leg >= len(route) - 1:
            return voyage.get("sailing", {}).get("heading_deg", 0.0)
        a, b = SEA_NODES[route[leg]]["xy"], SEA_NODES[route[leg + 1]]["xy"]
        return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 360

    @classmethod
    def _initial_sailing(cls, voyage):
        return {"speed_mps": 0.0, "heading_deg": cls._course_heading(voyage),
                "rudder_deg": 0.0, "cross_track": 0.0, "drive_n": 0.0,
                "resistance_n": 0.0, "acceleration_mps2": 0.0, "target_speed_mps": 0.0}

    def set_crew_order(self, world, order, leader, members):
        if order not in CREW_ORDERS or not isinstance(members, list):
            raise ValueError("Invalid crew free-time order")
        self._crew(world, leader)
        if len(set(members)) != len(members) or any(not world.ship.sailor(sid) for sid in members):
            raise ValueError("Choose unique current crew members")
        if leader not in members:
            raise ValueError("The team leader must be in the selected team")
        if order == "sword_drill" and any("weapon" not in world.economy.gear.get(sid, {}) for sid in members):
            raise ValueError("Every sword-drill participant needs an issued weapon")
        if len(members) < 2 and order in ("sword_drill", "brawling_drill"):
            raise ValueError("A team drill needs at least two sailors")
        self.crew_order = {"order": order, "leader": leader, "members": list(members)}

    def stop_after_encounter(self):
        return bool(self.voyage and self.voyage["status"] in ("encounter", "storm"))

    def interrupt_current_advance(self):
        if self.arrival_interrupt:
            return True
        if not self.voyage:
            return False
        status = self.voyage.get("status")
        return status == "storm" or (status == "encounter" and self._action_advance_depth == 0)

    def advance_encounter_action_time(self, world, ticks):
        """Charge a deliberate encounter action through canonical world time."""
        if not self.voyage or self.voyage.get("status") != "encounter":
            raise ValueError("Encounter action time requires a pending ship sighting")
        if world.combat.active:
            raise ValueError("Encounter actions that advance time are unavailable during combat")
        self._action_advance_depth += 1
        try:
            world.advance(ticks)
        finally:
            self._action_advance_depth -= 1

    def resolve_storm(self, world, choice, alternate_route=None):
        voyage = self.voyage
        storm = voyage.get("storm_event") if voyage else None
        if not voyage or voyage["status"] != "storm" or not storm or storm["stage"] != "active":
            raise ValueError("There is no active squall decision")
        if choice == "press_on":
            severity = "severe" if storm["trigger_roll"] < 8 else "moderate"
            hull_damage, rigging_damage = VOYAGE_STORM_DAMAGE[severity]
            world.ship.hull = max(0, world.ship.hull - hull_damage)
            world.ship.rigging = max(0, world.ship.rigging - rigging_damage)
            storm["stage"] = "resolved"
            storm["choice"] = "press_on"
            self._record(f"The captain pressed through a {severity} squall: hull -{hull_damage}, rigging -{rigging_damage}.")
            world.message(f"Pressed through the {severity} squall: hull -{hull_damage}, rigging -{rigging_damage}.")
        elif choice == "detour":
            if not isinstance(alternate_route, list) or len(alternate_route) < 2:
                raise ValueError("Choose a charted route around the storm lane")
            routes = self.route_options(self.position, voyage["destination"], include_detours=True)
            valid = next((row for row in routes if row["route"] == alternate_route), None)
            if (valid is None or alternate_route[0] != self.position
                    or any(node in voyage["route"][:voyage["leg"]]
                           for node in alternate_route[1:])
                    or alternate_route[:2] == storm["storm_lane"]):
                raise ValueError("The selected route is unavailable or still crosses the forecast storm lane")
            old_distance = max(voyage["distance"], 0.01)
            new_route = voyage["route"][:voyage["leg"]] + alternate_route
            new_distance = sum(
                math.hypot(SEA_NODES[b]["xy"][0] - SEA_NODES[a]["xy"][0],
                           SEA_NODES[b]["xy"][1] - SEA_NODES[a]["xy"][1])
                for a, b in zip(new_route, new_route[1:])
            )
            voyage["route"] = new_route
            voyage["distance"] = round(new_distance, 2)
            voyage["duration_seconds"] = max(
                voyage["elapsed_seconds"] + 1,
                round(voyage["duration_seconds"] * new_distance / old_distance),
            )
            voyage["leg_progress"] = 0.0
            voyage["sailing"]["heading_deg"] = self._course_heading(voyage)
            storm["stage"] = "avoided"
            storm["choice"] = "detour"
            self._record(f"The captain altered course around the squall via {' → '.join(SEA_NODES[n]['name'] for n in alternate_route)}.")
            world.message("The navigator laid in the charted detour. It adds real sailing distance; no storm damage was taken.")
        else:
            raise ValueError("Choose to press on or take a charted detour")
        voyage["status"] = "underway"

    def continue_after_sighting(self):
        if not self.voyage or self.voyage["status"] != "encounter":
            raise ValueError("There is no active ship sighting")
        if sea_encounter.request_pending(self.voyage):
            raise ValueError("Resolve the pending encounter request before resuming the voyage")
        if ((self.voyage["encounter"].get("contact") or {}).get("trade_pending")
                is not None):
            raise ValueError("Commit or cancel the pending sea-trade quote before resuming")
        if self.voyage.get("deck_visit") is not None:
            raise ValueError("Return everyone from the other vessel before resuming the voyage")
        self._record(f"Captain holds course after sighting {self.voyage['encounter']['name']}.")
        self.voyage["encounter"] = None
        self.voyage["status"] = "underway"

    def turn_back(self, world):
        if not self.voyage or self.voyage["status"] != "encounter":
            raise ValueError("Turn back is available only during a sighting")
        if sea_encounter.request_pending(self.voyage):
            raise ValueError("Resolve the pending encounter request before turning back")
        if ((self.voyage["encounter"].get("contact") or {}).get("trade_pending")
                is not None):
            raise ValueError("Commit or cancel the pending sea-trade quote before turning back")
        if self.voyage.get("deck_visit") is not None:
            raise ValueError("Return everyone from the other vessel before turning back")
        current = self.position
        ports = [node for node, row in SEA_NODES.items() if row.get("port")]
        target = min(ports, key=lambda p: self.route_distance(current, p))
        if target == current:
            port = SEA_NODES[target]["port"]
            self.position = target
            self.voyage["destination"] = target
            self.voyage["route"] = [target]
            self.voyage["leg"] = 0
            self.voyage["leg_progress"] = 1.0
            self.voyage["duration_seconds"] = 1
            self.voyage["elapsed_seconds"] = 1
            self.voyage["distance"] = 0.0
            self.voyage["status"] = "arrived"
            self.voyage["encounter"] = None
            self.voyage["storm_event"] = None
            self.voyage["arrival_snapshot"] = self._ledger_checkpoint(world)
            self.arrival_interrupt = True
            world.economy.port = port
            build_port(world)
            world.restore_harbor(port)
            world.at_sea = False
            world.player.x, world.player.y = 76, 25
            return
        plan = self.plan(world, target)
        storm_event = self._make_storm_event(world, plan)
        prior = self.voyage
        self.voyage = {**plan, "status": "underway", "leg": 0, "leg_progress": 0.0,
                       "elapsed_seconds": 0, "destination": target, "encounter": None,
                       "deck_visit": None,
                       "initial_estimate_seconds": prior.get("initial_estimate_seconds", prior["duration_seconds"]),
                       "storm_event": storm_event,
                       "departure_snapshot": prior.get("departure_snapshot") or self._ledger_checkpoint(world),
                       "arrival_snapshot": None, "outcomes": list(prior.get("outcomes", []))}
        self.position = current
        self.voyage["route"], self.voyage["distance"] = self.shortest_route(current, target)
        self._record(f"Captain ordered a return to {SEA_NODES[target]['name']}.")
        if storm_event:
            world.message(
                f"Return-route squall watch: {storm_event['chance_percent']}% eligibility near "
                f"{SEA_NODES[storm_event['node']]['name']} (low confidence)."
            )

    def _encounter_state(self, world, *, allow_pending=False):
        """Return the live sighting and contact, rejecting other encounter actions."""
        if not self.voyage or self.voyage.get("status") != "encounter":
            raise ValueError("There is no active ship sighting")
        encounter = self.voyage.get("encounter")
        contact = sea_encounter.contact_of(encounter, create=False)
        if contact is None:
            contact = sea_encounter.new_contact()
        if self.voyage.get("deck_visit") is not None:
            raise ValueError("Return the visiting party to the Wayward before another encounter action")
        if isinstance(encounter.get("id"), str):
            world.npcs._encountered_ship(world, encounter["id"])
        if encounter.get("combat", {}).get("status") == "active":
            raise ValueError("A saved legacy artillery exchange is active; use its combat menu")
        if not allow_pending and (contact.get("crossing") is not None
                                  or contact.get("combat_pending") is not None
                                  or contact.get("trade_pending") is not None):
            raise ValueError("A pending encounter request blocks further encounter actions")
        return encounter, contact

    def request_sea_trade_offer(self, world):
        """Ask a close merchant to disclose a bounded, explicit commodity offer."""
        encounter, contact = self._encounter_state(world)
        contact = sea_encounter.contact_of(encounter)
        instance_id = encounter.get("contact_instance_id", encounter["id"])
        sea_encounter.validate_contact(
            contact, encounter["id"], contact_instance_id=instance_id,
            current_tick=world.clock.total_ticks)
        if contact["range"] != "close":
            raise ValueError("At-sea trade requires a close-range vessel")
        if sea_encounter.threat_of(encounter) != "merchant":
            raise ValueError("Only a publicly assessed merchant contact may offer peaceful trade")
        if contact["crossing_completed"]:
            raise ValueError("Return from the other vessel before requesting trade")
        if contact["trade_offer"] is not None:
            return deepcopy(contact["trade_offer"])
        target = world.npcs._encountered_ship(world, encounter["id"])
        offer = sea_trade.create_offer(instance_id, target, world.clock.total_ticks)
        contact["trade_offer"] = offer
        encounter["contact"] = contact
        sea_encounter.validate_contact(
            contact, encounter["id"], contact_instance_id=(
                encounter.get("contact_instance_id", encounter["id"])),
            current_tick=world.clock.total_ticks)
        self.advance_encounter_action_time(world, 6000)
        return deepcopy(offer)

    def quote_sea_trade(self, world, item_id, quantity, buying):
        """Freeze a fixed-price preview; balances, cargo and time stay untouched."""
        encounter, contact = self._encounter_state(world)
        contact = sea_encounter.contact_of(encounter)
        instance_id = encounter.get("contact_instance_id", encounter["id"])
        sea_encounter.validate_contact(
            contact, encounter["id"], contact_instance_id=instance_id,
            current_tick=world.clock.total_ticks)
        if contact["range"] != "close" or sea_encounter.threat_of(encounter) != "merchant":
            raise ValueError("At-sea trade requires a close-range merchant")
        offer = contact.get("trade_offer")
        if offer is None:
            raise ValueError("Request the merchant's explicit finite offer first")
        target = world.npcs._encountered_ship(world, encounter["id"])
        next_quote = sea_trade.quote(
            instance_id, offer,
            contact["trade_pending"], contact["trade_receipts"],
            contact["trade_sequence"], item_id, quantity, buying)
        from pirates.economy import CATALOG, TRADE_BULK_ML, TRADE_MASS_G
        item = CATALOG[item_id]
        economy = world.economy
        if buying:
            if target["cargo"].get(item_id, 0) < quantity:
                raise ValueError("The merchant's offered stock is no longer available")
            if economy.money < next_quote["total"]:
                raise ValueError("Not enough Marks for this fixed-price purchase")
            mass, bulk = economy.load()
            if (mass + item["weight_g"] * quantity > TRADE_MASS_G
                    or bulk + item["bulk_ml"] * quantity > TRADE_BULK_ML):
                raise ValueError("At-sea purchase exceeds available cargo capacity")
        else:
            if economy.available_cargo(item_id) < quantity:
                raise ValueError("Not enough unreserved cargo to sell")
            if target["silver"] < next_quote["total"]:
                raise ValueError("The merchant no longer has enough silver for this sale")
        next_quote["quoted_tick"] = world.clock.total_ticks
        contact["trade_sequence"] = next_quote["sequence"]
        contact["trade_pending"] = next_quote
        encounter["contact"] = contact
        sea_encounter.validate_contact(
            contact, encounter["id"], contact_instance_id=(
                encounter.get("contact_instance_id", encounter["id"])),
            current_tick=world.clock.total_ticks)
        return deepcopy(next_quote)

    def cancel_sea_trade(self, world, transaction_id=None):
        encounter, contact = self._encounter_state(world, allow_pending=True)
        contact = sea_encounter.contact_of(encounter)
        sea_encounter.validate_contact(
            contact, encounter["id"], contact_instance_id=(
                encounter.get("contact_instance_id", encounter["id"])),
            current_tick=world.clock.total_ticks)
        if contact.get("crossing") is not None or contact.get("combat_pending") is not None:
            raise ValueError("A different pending encounter request blocks trade cancellation")
        pending = contact.get("trade_pending")
        if pending is None:
            raise ValueError("There is no sea-trade quote to cancel")
        if transaction_id is not None and pending["transaction_id"] != transaction_id:
            raise ValueError("That transaction is not the pending sea-trade quote")
        contact["trade_pending"] = None
        encounter["contact"] = contact
        sea_encounter.validate_contact(
            contact, encounter["id"], contact_instance_id=(
                encounter.get("contact_instance_id", encounter["id"])),
            current_tick=world.clock.total_ticks)
        world.message("Sea-trade quote cancelled; no Marks, cargo or time changed.")
        return True

    def commit_sea_trade(self, world, transaction_id):
        """Revalidate and atomically commit a quote once; receipts make retries idempotent."""
        encounter, contact = self._encounter_state(world, allow_pending=True)
        contact = sea_encounter.contact_of(encounter)
        sea_encounter.validate_contact(
            contact, encounter["id"], contact_instance_id=(
                encounter.get("contact_instance_id", encounter["id"])),
            current_tick=world.clock.total_ticks)
        if contact.get("crossing") is not None or contact.get("combat_pending") is not None:
            raise ValueError("A different pending encounter request blocks sea trade")
        if not isinstance(transaction_id, str):
            raise ValueError("Choose a valid sea-trade transaction")
        receipt = next((row for row in contact["trade_receipts"]
                        if row["transaction_id"] == transaction_id), None)
        if receipt is not None:
            return sea_trade.copy_receipt(receipt)
        pending = contact.get("trade_pending")
        if pending is None or pending["transaction_id"] != transaction_id:
            raise ValueError("That transaction is not the pending sea-trade quote")
        if (contact["range"] != "close" or contact["crossing_completed"]
                or sea_encounter.threat_of(encounter) != "merchant"):
            raise ValueError("Trade consent or close-range eligibility no longer holds")
        target = world.npcs._encountered_ship(world, encounter["id"])
        economy = world.economy
        item_id, quantity, total = pending["item_id"], pending["quantity"], pending["total"]
        if pending["buying"]:
            if target["cargo"].get(item_id, 0) < quantity:
                raise ValueError("Merchant stock changed; cancel and request a fresh quote")
            if economy.money < total:
                raise ValueError("Player purse changed; cancel and request a fresh quote")
            mass, bulk = economy.load()
            from pirates.economy import CATALOG, TRADE_BULK_ML, TRADE_MASS_G
            item = CATALOG[item_id]
            if (mass + item["weight_g"] * quantity > TRADE_MASS_G
                    or bulk + item["bulk_ml"] * quantity > TRADE_BULK_ML):
                raise ValueError("Cargo capacity changed; cancel and request a fresh quote")
            offer_row = next(row for row in contact["trade_offer"]["items"]
                             if row["item_id"] == item_id)
            if offer_row["sell_quantity"] < quantity:
                raise ValueError("The explicit offer no longer covers this quantity")
            # No fallible checks follow the first assignment: both ledgers move
            # together under the single-threaded world command boundary.
            economy.money -= total
            economy.cargo[item_id] = economy.cargo.get(item_id, 0) + quantity
            target["cargo"][item_id] -= quantity
            target["silver"] += total
            offer_row["sell_quantity"] -= quantity
        else:
            if economy.available_cargo(item_id) < quantity:
                raise ValueError("Cargo changed or is reserved; cancel and request a fresh quote")
            if target["silver"] < total:
                raise ValueError("Merchant purse changed; cancel and request a fresh quote")
            offer_row = next(row for row in contact["trade_offer"]["items"]
                             if row["item_id"] == item_id)
            if offer_row["buy_quantity"] < quantity:
                raise ValueError("The explicit offer no longer covers this quantity")
            economy.remove(item_id, quantity)
            economy.money += total
            target["cargo"][item_id] = target["cargo"].get(item_id, 0) + quantity
            target["silver"] -= total
            offer_row["buy_quantity"] -= quantity

        committed_tick = world.clock.total_ticks
        receipt = {key: deepcopy(pending[key]) for key in (
            "transaction_id", "sequence", "offer_id", "item_id", "quantity",
            "buying", "unit_price", "total", "quoted_tick")}
        receipt["committed_tick"] = committed_tick
        contact["trade_receipts"].append(receipt)
        contact["trade_pending"] = None
        encounter["contact"] = contact
        sea_encounter.validate_contact(
            contact, encounter["id"], contact_instance_id=(
                encounter.get("contact_instance_id", encounter["id"])),
            current_tick=committed_tick)
        direction = "Bought" if pending["buying"] else "Sold"
        text = (f"{direction} {quantity} {item_id} from {encounter['name']} for "
                f"{total} Marks; vessel stock and purse updated.")
        economy.record(text)
        world.message(text + " One minute of ship time passed.")
        self.advance_encounter_action_time(world, 6000)
        return sea_trade.copy_receipt(receipt)

    def _record_witnessed_hostile_fire(self, world):
        """Record the legacy artillery's actual NPC return shot, if witnessed."""
        if not self.voyage or self.voyage.get("status") != "encounter":
            raise ValueError("A witnessed hostile shot requires the active encounter")
        encounter = self.voyage.get("encounter")
        if not isinstance(encounter, dict) or not encounter.get("id"):
            raise ValueError("A witnessed hostile shot requires an identified vessel")
        instance_id = encounter.get("contact_instance_id", encounter["id"])
        return self.conduct_ledger.record(
            tick=world.clock.total_ticks, cause="committed_fire",
            aggressor_id=encounter["id"], target_id=world.player.id,
            encounter_id=instance_id, witnesses=[world.player.id])

    def hostile_commitment_basis(self, world, encounter):
        if not isinstance(encounter, dict):
            return None
        instance_id = encounter.get("contact_instance_id", encounter.get("id"))
        return self.conduct_ledger.authorization(
            target_id=encounter.get("id"), actor_id=world.player.id,
            encounter_id=instance_id, current_tick=world.clock.total_ticks)

    def signal_sighting(self, world, intent):
        """Exchange one bounded signal subject at a real one-minute cost."""
        encounter, _contact = self._encounter_state(world)
        if world.combat.active:
            raise ValueError("Signal exchanges are unavailable during active combat")
        record = sea_encounter.signal(encounter, world.clock.total_ticks, intent)
        self.advance_encounter_action_time(world, 6000)
        self._record(f"Signals exchanged with {encounter['name']} "
                     f"({intent.replace('_', ' ')}); no private intent disclosed.")
        world.message(record["reply"])
        return record

    def approach_sighting(self, world):
        """Close the encounter range when the other captain permits it."""
        encounter, _contact = self._encounter_state(world)
        if world.combat.active:
            raise ValueError("Approach is unavailable during active combat")
        result = sea_encounter.approach(encounter, world.clock.total_ticks)
        self.advance_encounter_action_time(world, 30000)
        self._record(result["text"])
        world.message(result["text"])
        return result

    def evade_sighting(self, world):
        """Attempt to break contact; a failure worsens the contact range."""
        encounter, _contact = self._encounter_state(world)
        if world.combat.active:
            raise ValueError("Evasion is unavailable during active combat")
        result = sea_encounter.evade(world, encounter, world.clock.total_ticks)
        self.advance_encounter_action_time(world, 30000)
        self._record(result["text"])
        if result["escaped"]:
            self.voyage["encounter"] = None
            self.voyage["status"] = "underway"
        world.message(result["text"])
        return result

    def parley_sighting(self, world, topic):
        """Raise one encounter-local parley subject at close range."""
        encounter, _contact = self._encounter_state(world)
        if world.combat.active:
            raise ValueError("Parley is unavailable during active combat")
        result = sea_encounter.parley(encounter, topic, world=world)
        self.advance_encounter_action_time(world, 6000)
        self._record(result["text"])
        world.message(result["text"])
        return result

    def request_crossing(self, world, mode, escorts=()):
        """Record a peaceful transfer or hand off a refused crossing to combat."""
        encounter, contact = self._encounter_state(world, allow_pending=True)
        if world.combat.active:
            raise ValueError("A crossing request is unavailable during active combat")
        resolved = sea_encounter.manifest(world, mode, escorts)
        already_pending = bool(contact.get("combat_pending") or contact.get("crossing"))
        request = sea_encounter.request_crossing(
            world, encounter, world.clock.total_ticks, mode, resolved)
        if not already_pending:
            self.advance_encounter_action_time(world, 6000)
        if request.get("cause") == "forced_boarding":
            text = (f"{encounter['name']} has not clearly welcomed the crossing. A durable boarding "
                    "request is pending for the combat system; nobody moved and no clash was resolved.")
        else:
            text = (f"{encounter['name']} welcomes a peaceful personnel transfer. "
                    "The crossing is recorded as a pending request; nobody has moved yet.")
        if not already_pending:
            self._record(text)
            world.message(text)
        return request

    def withdraw_crossing_request(self, world):
        """Cancel a pending crossing request without time or side effects."""
        if not self.voyage or self.voyage.get("status") != "encounter":
            raise ValueError("There is no active ship sighting")
        encounter = self.voyage.get("encounter")
        request = sea_encounter.withdraw_crossing(encounter)
        self._record("Captain withdrew the pending peaceful crossing request.")
        world.message("The peaceful crossing request is withdrawn. No person moved.")
        return request

    def complete_peaceful_crossing(self, world):
        """Commit one consented visit over a verified walkable gangplank path."""
        encounter, contact = self._encounter_state(world, allow_pending=True)
        known_people = {sailor.id for sailor in world.ship.crew}
        living_people = [sailor.id for sailor in world.ship.crew if sailor.health > 0]
        sea_encounter.validate_sighting(
            encounter, valid_nodes=SEA_NODES,
            valid_people=known_people | {world.player.id},
            living_people=living_people, captain_id=world.player.id,
            current_tick=world.clock.total_ticks)
        request = contact.get("crossing")
        if not request:
            raise ValueError("There is no welcomed crossing request to complete")
        if (not self.voyage or self.voyage.get("status") != "encounter"
                or not world.at_sea or world.combat.active):
            raise ValueError("A peaceful crossing requires an active sea encounter")
        if contact["range"] != "close":
            raise ValueError("A peaceful crossing requires close range")
        parley = contact.get("parley") or {}
        if not parley.get("transfer_permission") or parley.get("warned_off"):
            raise ValueError("The other captain has not granted this contact's crossing permission")
        if contact.get("crossing_completed") or self.voyage.get("deck_visit") is not None:
            raise ValueError("This contact already has a completed or active crossing")
        if request["participants"][0] != world.player.id:
            raise ValueError("The crossing manifest must begin with the captain")
        if len(request["participants"]) > sea_transfer.DECK_CAPACITY:
            raise ValueError("The other vessel's visit deck has insufficient capacity")
        target = next((ship for ship in world.npcs.ships
                       if ship["id"] == encounter["id"]), None)
        if target is None or target.get("condition", 0) <= 0:
            raise ValueError("The welcomed vessel is no longer available for a crossing")

        actors = sea_transfer.actor_map(world)
        participants = request["participants"]
        if any(sid not in actors or sid != world.player.id
               and actors[sid].health <= 0 for sid in participants):
            raise ValueError("Every crossing participant must be present and living")
        moving_ids = set(participants)
        if any(not sea_transfer.in_home_ship(
                actors[sid].x, actors[sid].y, getattr(actors[sid], "z", 0))
               for sid in participants):
            raise ValueError("Every participant must start aboard the Wayward's main deck")

        sea_transfer.apply_deck(world)
        try:
            for sid, destination in zip(participants, sea_transfer.LANDING_CELLS):
                if not sea_transfer.reachable(world, actors[sid], destination, moving_ids):
                    raise ValueError("No clear reachable path from the Wayward to the other vessel's deck")
        except Exception:
            sea_transfer.clear_deck(world)
            raise

        deck_visit = {
            "version": 1, "target_id": encounter["id"],
            "contact_instance_id": encounter.get("contact_instance_id", encounter["id"]),
            "participants": list(participants), "started_tick": world.clock.total_ticks,
        }
        for sid, destination in zip(participants, sea_transfer.LANDING_CELLS):
            actors[sid].x, actors[sid].y, actors[sid].z = destination[0], destination[1], 0
        contact["crossing"] = None
        contact["crossing_completed"] = True
        self.voyage["deck_visit"] = deck_visit
        sea_encounter.validate_contact(
            contact, encounter["id"], contact_instance_id=(
                encounter.get("contact_instance_id", encounter["id"])),
            current_tick=world.clock.total_ticks)
        world.refresh()
        self._record(f"The welcomed party crossed to {encounter['name']}'s deck by gangplank.")
        world.message(
            f"Crossing completed: {len(participants)} named person(s) are on {encounter['name']}'s deck. "
            "Walk back across the gangplank or order the party home; 1 minute passes.")
        self.advance_encounter_action_time(world, 6000)
        return deepcopy(deck_visit)

    def return_crossing_party(self, world):
        """Return the recorded visitors along a clear path to the Wayward."""
        visit = self.voyage.get("deck_visit") if self.voyage else None
        if not isinstance(visit, dict):
            raise ValueError("No peaceful crossing party is away from the Wayward")
        if (set(visit) != {"version", "target_id", "contact_instance_id",
                          "participants", "started_tick"}
                or type(visit.get("version")) is not int or visit["version"] != 1
                or not isinstance(visit.get("target_id"), str)
                or not isinstance(visit.get("contact_instance_id"), str)
                or not isinstance(visit.get("participants"), list)
                or not visit["participants"]
                or len(visit["participants"]) > sea_transfer.DECK_CAPACITY
                or any(not isinstance(sid, str) or not sid for sid in visit["participants"])
                or len(set(visit["participants"])) != len(visit["participants"])
                or visit["participants"][0] != world.player.id
                or type(visit.get("started_tick")) is not int
                or not 0 <= visit["started_tick"] <= world.clock.total_ticks):
            raise ValueError("Invalid saved peaceful deck visit")
        encounter = self.voyage.get("encounter")
        instance_id = (encounter.get("contact_instance_id", encounter.get("id"))
                       if isinstance(encounter, dict) else None)
        if (not encounter or encounter.get("id") != visit["target_id"]
                or instance_id != visit["contact_instance_id"]):
            raise ValueError("The saved crossing belongs to another contact instance")
        if not world.at_sea or world.combat.active:
            raise ValueError("The crossing party can return only during the active sea encounter")
        actors = sea_transfer.actor_map(world)
        participants = visit["participants"]
        if any(sid not in actors for sid in participants):
            raise ValueError("A crossing participant is no longer available to return")
        moving_ids = set(participants)
        destinations = {}
        reserved = set()
        for sid in participants:
            actor = actors[sid]
            current = (actor.x, actor.y, getattr(actor, "z", 0))
            if sea_transfer.in_home_ship(*current):
                if current[:2] in reserved:
                    raise ValueError("Crossing participants overlap on the Wayward")
                destinations[sid] = current[:2]
                reserved.add(current[:2])
        for sid in participants:
            if sid in destinations:
                continue
            actor = actors[sid]
            destination = next((cell for cell in sea_transfer.RETURN_CELLS
                                if cell not in reserved
                                and sea_transfer.reachable(world, actor, cell, moving_ids)), None)
            if destination is None:
                raise ValueError("No clear reachable path back across the gangplank")
            destinations[sid] = destination
            reserved.add(destination)

        for sid, (x, y) in destinations.items():
            actors[sid].x, actors[sid].y, actors[sid].z = x, y, 0
        self.voyage["deck_visit"] = None
        sea_transfer.clear_deck(world)
        self._record(f"The peaceful crossing party returned from {encounter['name']}.")
        world.message("The crossing party is back aboard the Wayward; 1 minute passes.")
        self.advance_encounter_action_time(world, 6000)
        return list(participants)

    def rebuild_transfer_deck(self, world):
        """Recreate the ephemeral NPC deck after the saved sea map is restored."""
        if self.voyage and self.voyage.get("deck_visit") is not None:
            sea_transfer.apply_deck(world)
            self.refresh_transfer_state(world)

    def refresh_transfer_state(self, world):
        """Drop the temporary deck only after every visitor has returned home."""
        visit = self.voyage.get("deck_visit") if self.voyage else None
        if not isinstance(visit, dict):
            return False
        actors = sea_transfer.actor_map(world)
        if any(sid not in actors for sid in visit["participants"]):
            return False
        if all(sea_transfer.in_home_ship(
                actors[sid].x, actors[sid].y, getattr(actors[sid], "z", 0))
               for sid in visit["participants"]):
            self.voyage["deck_visit"] = None
            sea_transfer.clear_deck(world)
            return True
        return False

    def request_naval_combat(self, world, cannon):
        """Create the cannon-fire handoff; never fire or consume ammunition here."""
        encounter, contact = self._encounter_state(world, allow_pending=True)
        if world.combat.active:
            raise ValueError("Cannon intent is unavailable during active combat")
        already_pending = contact.get("combat_pending") is not None
        request = sea_encounter.request_cannon(world, encounter, world.clock.total_ticks, cannon)
        if not already_pending:
            text = (f"Cannon-fire intent is recorded for {encounter['name']}. "
                    "The combat system owns firing, ammunition, damage and outcome resolution.")
            self._record(text)
            world.message(text)
        return request

    def begin_naval_combat(self, world, cannon=None):
        """Compatibility entry point now creates a handoff instead of a battle."""
        if cannon is None:
            raise ValueError("Choose a loaded, installed cannon before creating the combat handoff")
        return self.request_naval_combat(world, cannon)

    @staticmethod
    def _combat_roll(combat, exchange, side, gun):
        payload = f"{combat['seed']}:{exchange}:{side}:{gun}".encode()
        return int.from_bytes(blake2b(payload, digest_size=4).digest(), "big") / 2**32

    def cannon_exchange(self, world, cannon):
        sighting = self.voyage.get("encounter") if self.voyage else None
        if sighting and sea_encounter.request_pending({"encounter": sighting}):
            raise ValueError("Cannon exchange belongs to the pending combat handoff")
        combat = sighting.get("combat") if sighting else None
        if not combat or combat.get("status") != "active":
            raise ValueError("There is no active cannon engagement")
        if cannon not in ("port_cannon", "starboard_cannon"):
            raise ValueError("Choose an installed ship cannon")
        target = next((ship for ship in world.npcs.ships if ship["id"] == sighting.get("id")), None)
        if target is None:
            raise ValueError("The target vessel is no longer in the fleet")
        if (world.ship.ammo[cannon] < 1 or not world.ship.layout.available(cannon)):
            raise ValueError("That cannon needs an installed station and a loaded charge")

        crew = [member for member in world.ship.crew
                if member.health > 0 and member.labors.get("load", 0) > 0]
        gunnery = (sum(member.skills["load"] for member in crew) / len(crew)) if crew else 0
        captain = world.ship.sailor(world.voyages.roles.get("captain"))
        awareness = captain.mental["tactical_awareness"] if captain else 0
        command = captain.mental["leadership"] if captain else 0
        hit_chance = max(0.2, min(0.85, 0.3 + gunnery * 0.04 + awareness * 0.0025 + command * 0.001))
        exchange = combat["exchange"]
        world.ship.ammo[cannon] = 0
        roll = self._combat_roll(combat, exchange, "player", cannon)
        messages = [f"Your {cannon.replace('_', ' ')} fires; shot consumed; hit chance {hit_chance:.0%}."]
        if roll < hit_chance:
            damage = 7 + round(gunnery * 0.5 + awareness * 0.04)
            target["condition"] = max(0, target["condition"] - damage)
            messages.append(f"Direct hit: {target['name']} loses {damage} hull condition ({target['condition']} remains).")
        else:
            messages.append(f"The shot misses {target['name']}.")

        if target["condition"] > 20 and target.get("cargo", {}).get("shot", 0) > 0:
            # The NPC has committed a witnessed return shot. Record scoped
            # evidence before mutating its finite shot stock; this remains
            # legacy combat behavior.
            self._record_witnessed_hostile_fire(world)
            target["cargo"]["shot"] -= 1
            society = getattr(world, "society", None)
            score = (society._strength_for_ship(target)["score"]
                     if society and society.mode == "generated" else
                     min(100, round(target["crew"] * 5 + target["condition"] * 0.35)))
            enemy_chance = max(0.2, min(0.75, 0.25 + score * 0.0035))
            return_roll = self._combat_roll(combat, exchange, "enemy", "broadside")
            messages.append(f"{target['name']} returns fire; shot consumed; hit chance {enemy_chance:.0%}.")
            if return_roll < enemy_chance:
                hull_damage = 5 + round(score * 0.06)
                world.ship.hull = max(0, world.ship.hull - hull_damage)
                messages.append(f"Incoming ball strikes the hull: Wayward loses {hull_damage} condition ({world.ship.hull:.0f} remains).")
                if self._combat_roll(combat, exchange, "enemy", "rigging") < 0.35:
                    rigging_damage = 3 + round(score * 0.04)
                    world.ship.rigging = max(0, world.ship.rigging - rigging_damage)
                    messages.append(f"A second impact tears rigging; condition falls by {rigging_damage}.")
            else:
                messages.append("The enemy broadside splashes wide.")
        else:
            messages.append(f"{target['name']} has no loaded ship shot ready to return fire.")

        combat["exchange"] += 1
        sighting["condition"] = target["condition"]
        if target["condition"] <= 20:
            ports = [node for node, row in SEA_NODES.items() if row.get("port")]
            target["destination"] = min(ports, key=lambda port: self.route_distance(target["node"], port))
            combat["status"] = "enemy_withdrew"
            messages.append(f"{target['name']} breaks away for port.")
        elif world.ship.hull <= 15:
            combat["status"] = "player_retreat"
            messages.append("The Wayward is critically damaged; disengage and turn back for repairs.")
        combat["log"] = (combat["log"] + messages)[-24:]
        for message in messages:
            self._record(message)
        world.advance(15 * 100)
        return combat

    def snapshot(self):
        return {"position": self.position, "roles": dict(self.roles), "voyage": deepcopy(self.voyage),
                "captain_action": self.captain_action, "crew_order": deepcopy(self.crew_order),
                "last_sim_hour": self.last_sim_hour, "events": list(self.events),
                "captain_fatigue": self.captain_fatigue,
                "conduct_ledger": self.conduct_ledger.snapshot()}

    @classmethod
    def restore(cls, data, world):
        system = cls()
        if data is None:
            return system
        if data["position"] not in SEA_NODES or not isinstance(data.get("roles"), dict):
            raise ValueError("Invalid voyage location or officer roster")
        ids = {c.id for c in world.ship.crew}
        roles = dict(data["roles"])
        legacy_roles = set(SEA_ROLES) - {"captain"}
        if set(roles) == legacy_roles:
            available = sorted(ids - {sid for sid in roles.values() if sid is not None})
            roles["captain"] = "tavi" if "tavi" in available else (available[0] if available else None)
        if set(roles) != set(SEA_ROLES):
            raise ValueError("Invalid voyage location or officer roster")
        assigned_roles = [sid for sid in roles.values() if sid is not None]
        if (any(sid not in ids for sid in assigned_roles)
                or len(set(assigned_roles)) != len(assigned_roles)):
            raise ValueError("Invalid voyage officer assignment")
        if data["captain_action"] not in CAPTAIN_ACTIONS:
            raise ValueError("Invalid captain free-time action")
        order = data["crew_order"]
        # Saves produced by the earlier voyage prototype omitted its selected
        # leader from the member list; normalize that default without changing
        # any non-empty explicit roster.
        if (isinstance(order, dict) and isinstance(order.get("members"), list)
                and not order["members"] and order.get("leader") in ids):
            order = {**order, "members": [order["leader"]]}
        empty_roster_order = (not ids and isinstance(order, dict)
                              and order.get("leader") is None and order.get("members") == [])
        if (not isinstance(order, dict) or order.get("order") not in CREW_ORDERS
                or (not empty_roster_order and order.get("leader") not in ids)
                or not isinstance(order.get("members"), list)
                or (not empty_roster_order and order["leader"] not in order["members"])
                or len(set(order["members"])) != len(order["members"])
                or any(sid not in ids for sid in order["members"])):
            raise ValueError("Invalid crew order")
        if order["order"] == "sword_drill" and any("weapon" not in world.economy.gear.get(sid, {}) for sid in order["members"]):
            raise ValueError("Saved sword team is not equipped")
        voyage = deepcopy(data["voyage"])
        if voyage is not None:
            voyage.setdefault("storm_event", None)
            voyage.setdefault("deck_visit", None)
            voyage.setdefault("initial_estimate_seconds", voyage.get("duration_seconds", 0))
            voyage.setdefault("departure_snapshot", None)
            voyage.setdefault("arrival_snapshot", None)
            voyage.setdefault("outcomes", [])
            if (voyage.get("status") not in ("underway", "encounter", "storm", "arrived", "aborted")
                    or voyage.get("destination") not in SEA_NODES
                    or (voyage.get("status") in ("underway", "encounter", "storm") and voyage.get("destination") == data["position"])):
                raise ValueError("Invalid voyage state")
            route = voyage.get("route")
            min_route_length = 1 if voyage["status"] == "arrived" else 2
            if (not isinstance(route, list) or len(route) < min_route_length or route[0] not in SEA_NODES
                    or route[-1] != voyage["destination"]):
                raise ValueError("Invalid voyage route")
            valid_edges = {frozenset(edge) for edge in LANES}
            if any(frozenset((a, b)) not in valid_edges for a, b in zip(route, route[1:])):
                raise ValueError("Voyage route uses an uncharted lane")
            _, distance = system.shortest_route(route[0], route[-1])
            for field, lo, hi in (("duration_seconds", 1, 10**8), ("elapsed_seconds", 0, 10**8), ("leg", 0, len(route)), ("leg_progress", 0, 1)):
                value = voyage[field]
                if type(value) not in (int, float) or not math.isfinite(value) or not lo <= value <= hi:
                    raise ValueError("Invalid voyage progress")
            if (type(voyage["initial_estimate_seconds"]) is not int
                    or not 1 <= voyage["initial_estimate_seconds"] <= 10**8):
                raise ValueError("Invalid original voyage estimate")
            if (not math.isfinite(voyage.get("distance", distance))
                    or (voyage["distance"] <= 0 and voyage["status"] != "arrived")):
                raise ValueError("Invalid voyage distance")
            if voyage["status"] == "encounter" and not isinstance(voyage.get("encounter"), dict):
                raise ValueError("Encounter requires a sighting record")
            if voyage["status"] == "encounter":
                living_ids = [crew.id for crew in world.ship.crew if crew.health > 0]
                sea_encounter.validate_sighting(
                    voyage["encounter"], valid_nodes=SEA_NODES,
                    valid_people=set(living_ids) | {world.player.id},
                    living_people=living_ids, captain_id=world.player.id,
                    current_tick=world.clock.total_ticks)
            visit = voyage.get("deck_visit")
            if visit is not None:
                required_visit = {"version", "target_id", "contact_instance_id",
                                  "participants", "started_tick"}
                encounter = voyage.get("encounter")
                contact = encounter.get("contact", {}) if isinstance(encounter, dict) else {}
                instance_id = (encounter.get("contact_instance_id", encounter.get("id"))
                               if isinstance(encounter, dict) else None)
                valid_people = {crew.id for crew in world.ship.crew}
                valid_people.add(world.player.id)
                if (voyage["status"] != "encounter"
                        or not isinstance(visit, dict) or set(visit) != required_visit
                        or type(visit["version"]) is not int or visit["version"] != 1
                        or not isinstance(visit["target_id"], str)
                        or visit["target_id"] != encounter.get("id")
                        or not isinstance(visit["contact_instance_id"], str)
                        or visit["contact_instance_id"] != instance_id
                        or not isinstance(visit["participants"], list)
                        or not visit["participants"]
                        or len(visit["participants"]) > sea_transfer.DECK_CAPACITY
                        or len(set(visit["participants"])) != len(visit["participants"])
                        or visit["participants"][0] != world.player.id
                        or any(sid not in valid_people for sid in visit["participants"])
                        or type(visit["started_tick"]) is not int
                        or not 0 <= visit["started_tick"] <= world.clock.total_ticks
                        or not contact.get("crossing_completed")
                        or contact.get("crossing") is not None):
                    raise ValueError("Invalid saved peaceful deck visit")
                actors = sea_transfer.actor_map(world)
                if any(not (sea_transfer.in_deck(actors[sid].x, actors[sid].y)
                            or sea_transfer.in_home_ship(
                                actors[sid].x, actors[sid].y,
                                getattr(actors[sid], "z", 0)))
                       or getattr(actors[sid], "z", 0) != 0
                       for sid in visit["participants"]):
                    raise ValueError("Saved crossing participant is outside the two reachable decks")
            storm = voyage.get("storm_event")
            if storm is not None:
                if (not isinstance(storm, dict)
                        or set(storm) != {"event_id", "node", "storm_lane", "trigger_roll",
                                         "chance_percent", "triggered", "stage", "choice"}
                        or not isinstance(storm["event_id"], str) or not storm["event_id"]
                        or storm["node"] not in SEA_NODES
                        or not isinstance(storm["storm_lane"], list) or len(storm["storm_lane"]) != 2
                        or any(node not in SEA_NODES for node in storm["storm_lane"])
                        or storm["node"] != storm["storm_lane"][0]
                        or frozenset(storm["storm_lane"]) not in valid_edges
                        or type(storm["trigger_roll"]) is not int or not 0 <= storm["trigger_roll"] < 100
                        or type(storm["chance_percent"]) is not int
                        or not 0 <= storm["chance_percent"] <= 100
                        or type(storm["triggered"]) is not bool
                        or storm["triggered"] != (storm["trigger_roll"] < storm["chance_percent"])
                        or storm["stage"] not in ("forecast", "active", "clear", "avoided", "resolved")
                        or storm["choice"] not in (None, "press_on", "detour")
                        or (storm["stage"] == "active") != (voyage["status"] == "storm")
                        or storm["stage"] == "active" and not storm["triggered"]
                        or storm["stage"] == "clear" and storm["triggered"]
                        or storm["stage"] in ("avoided", "resolved") and not storm["triggered"]
                        or storm["stage"] == "avoided" and storm["choice"] != "detour"
                        or storm["stage"] == "resolved" and storm["choice"] != "press_on"):
                    raise ValueError("Invalid persisted voyage storm event")
            if voyage["status"] == "storm" and (not storm or storm["stage"] != "active"):
                raise ValueError("Storm decision requires a persisted active event")
            if voyage["status"] == "storm" and (
                    data["position"] != storm["node"] or voyage["leg"] < 1):
                raise ValueError("Active storm decision is not at its saved chart node")
            for checkpoint_name in ("departure_snapshot", "arrival_snapshot"):
                checkpoint = voyage[checkpoint_name]
                if checkpoint is None:
                    continue
                required = {"ticks", "money", "cargo", "hull", "rigging", "contracts"}
                if (not isinstance(checkpoint, dict) or set(checkpoint) != required
                        or type(checkpoint["ticks"]) is not int or checkpoint["ticks"] < 0
                        or type(checkpoint["money"]) is not int or checkpoint["money"] < 0
                        or not isinstance(checkpoint["cargo"], dict)
                        or any(not isinstance(item, str) or type(quantity) is not int or quantity < 0
                               for item, quantity in checkpoint["cargo"].items())
                        or any(type(checkpoint[key]) not in (int, float)
                               or not math.isfinite(checkpoint[key]) or not 0 <= checkpoint[key] <= 100
                               for key in ("hull", "rigging"))
                        or not isinstance(checkpoint["contracts"], list)):
                    raise ValueError("Invalid voyage ledger checkpoint")
                contract_ids = set()
                for contract in checkpoint["contracts"]:
                    if (not isinstance(contract, dict)
                            or set(contract) != {"offer_id", "destination", "deadline_day", "status", "escrow"}
                            or not isinstance(contract["offer_id"], str)
                            or contract["offer_id"] in contract_ids
                            or contract["destination"] not in SEA_NODES
                            or type(contract["deadline_day"]) is not int or contract["deadline_day"] < 1
                            or contract["status"] not in ("active", "completed", "expired")
                            or type(contract["escrow"]) is not int or contract["escrow"] < 0):
                        raise ValueError("Invalid voyage contract checkpoint")
                    contract_ids.add(contract["offer_id"])
            if (voyage["arrival_snapshot"] is not None and voyage["status"] != "arrived"):
                raise ValueError("Only an arrived voyage may have an arrival checkpoint")
            if (not isinstance(voyage["outcomes"], list) or len(voyage["outcomes"]) > 30
                    or any(not isinstance(text, str) for text in voyage["outcomes"])):
                raise ValueError("Invalid voyage outcome history")
            encounter = voyage.get("encounter") or {}
            if "combat" in encounter:
                combat = encounter["combat"]
                if (not isinstance(encounter.get("id"), str) or not isinstance(combat, dict)
                        or set(combat) != {"status", "seed", "exchange", "log"}
                        or combat["status"] not in ("active", "enemy_withdrew", "player_retreat")
                        or type(combat["seed"]) is not int or not 0 <= combat["seed"] < 2**64
                        or type(combat["exchange"]) is not int or not 0 <= combat["exchange"] <= 1_000_000
                        or not isinstance(combat["log"], list) or len(combat["log"]) > 24
                        or any(not isinstance(line, str) for line in combat["log"])):
                    raise ValueError("Invalid saved naval combat state")
            sailing = voyage.get("sailing")
            if sailing is not None:
                numeric = ("speed_mps", "heading_deg", "rudder_deg", "cross_track", "drive_n",
                           "resistance_n", "acceleration_mps2", "target_speed_mps")
                if (not isinstance(sailing, dict) or any(
                        type(sailing.get(key)) not in (int, float)
                        or not math.isfinite(sailing[key]) for key in numeric)
                        or not 0 <= sailing["speed_mps"] <= 30
                        or not 0 <= sailing["heading_deg"] < 360
                        or not -30 <= sailing["rudder_deg"] <= 30
                        or not -2 <= sailing["cross_track"] <= 2
                        or any(sailing[key] < 0 for key in ("drive_n", "resistance_n", "target_speed_mps"))):
                    raise ValueError("Invalid persisted sailing state")
        system.position = data["position"]
        system.roles = roles
        system.voyage = deepcopy(voyage)
        system.captain_action = data["captain_action"]
        system.crew_order = deepcopy(order)
        system.last_sim_hour = data["last_sim_hour"]
        system.events = list(data["events"])
        system.captain_fatigue = data.get("captain_fatigue", 20.0)
        if type(system.captain_fatigue) not in (int, float) or not math.isfinite(system.captain_fatigue) or not 0 <= system.captain_fatigue <= 100:
            raise ValueError("Invalid captain fatigue")
        if type(system.last_sim_hour) is not int or system.last_sim_hour < 0 or not isinstance(system.events, list) or len(system.events) > 60 or any(not isinstance(e, str) for e in system.events):
            raise ValueError("Invalid voyage history")
        system.conduct_ledger = SeaConductLedger.restore(
            data.get("conduct_ledger"), current_tick=world.clock.total_ticks)
        if system.voyage and system.voyage.get("encounter"):
            encounter = system.voyage["encounter"]
            contact = encounter.get("contact", {})
            handoff = contact.get("combat_pending") if isinstance(contact, dict) else None
            basis_id = handoff.get("lawful_basis_id") if isinstance(handoff, dict) else None
            if handoff is not None:
                if basis_id is None:
                    # Pre-policy saves could contain hostile requests created
                    # from proximity/refusal alone. They are uncommitted, so
                    # cancel them rather than upgrading unsupported conduct.
                    contact["combat_pending"] = None
                    system.events = (system.events + [
                        "Cancelled an unsupported legacy hostile handoff; no witnessed attack or verified warrant was saved."])[-60:]
                else:
                    basis = system.conduct_ledger.authorization(
                        target_id=encounter.get("id"), actor_id=world.player.id,
                        encounter_id=encounter.get("contact_instance_id", encounter.get("id")),
                        current_tick=world.clock.total_ticks)
                    if basis is None or basis["event_id"] != basis_id:
                        raise ValueError("Combat handoff lacks matching saved lawful evidence")
        return system


class NpcFleetSystem:
    """Three persistent rival ships with bounded autonomous economic activity."""
    def __init__(self):
        self.hour = 0
        self.sighted = set()
        self.sighting_reports = {}
        self.player_contacts = {}
        self.contact_tracks = {}
        self.roster_mode = "authored"
        self.population_total = 40
        self.generation_manifest = None
        self.ships = [
            {"id": "syndicate_1", "name": "Free Syndicate cutter", "faction": "Free Syndicate", "node": "verdant", "destination": "cinder", "mission": "trade", "cargo": {"grain": 12}, "silver": 240, "crew": 7, "captives": 0, "threat": "merchant", "condition": 92},
            {"id": "government_1", "name": "Government marine patrol", "faction": "World Government", "node": "navy_lane", "destination": "shoals", "mission": "patrol", "cargo": {"shot": 8}, "silver": 400, "crew": 10, "captives": 0, "threat": "marines", "condition": 96},
            {"id": "raider_1", "name": "Rogue pirate brig", "faction": "Free Syndicate", "node": "freeport", "destination": "navy_lane", "mission": "hunt", "cargo": {"beans": 5}, "silver": 90, "crew": 6, "captives": 1, "threat": "pirates", "condition": 88},
            {"id": "blackwake_escort", "name": "Blackwake's Ashen Gull", "faction": "Free Syndicate", "node": "shoals", "destination": "navy_lane", "mission": "hunt", "cargo": {"shot": 3}, "silver": 130, "crew": 6, "captives": 0, "threat": "pirates", "condition": 90},
            {"id": "jackal_sloop", "name": "Salt Jackals' Swift", "faction": "Free Syndicate", "node": "deepwater", "destination": "freeport", "mission": "trade", "cargo": {"beans": 4}, "silver": 110, "crew": 5, "captives": 0, "threat": "merchant", "condition": 84},
        ]
        self._seed_motion(0)
        self._last_player_sample = None
        self.market = {"enslaved_people": 5, "silver": 500}
        self.events = []

    @classmethod
    def from_generated(cls, generated):
        system = cls()
        if not isinstance(generated, dict) or not isinstance(generated.get("manifest"), dict):
            raise ValueError("Invalid generated NPC fleet source")
        ships = generated.get("vessels")
        ledger = generated.get("population_ledger")
        if not isinstance(ships, dict) or not isinstance(ledger, dict):
            raise ValueError("Generated NPC fleet lacks vessels or population ledger")
        system.roster_mode = "generated"
        system.generation_manifest = deepcopy(generated["manifest"])
        system.ships = deepcopy(list(ships.values()))
        system._seed_motion(0)
        system.market = {"enslaved_people": int(ledger.get("unassigned", 0)), "silver": 500}
        system.population_total = int(ledger.get("total", -1))
        headcount = system.market["enslaved_people"] + sum(
            ship["crew"] + ship["captives"] for ship in system.ships)
        if system.population_total != headcount:
            raise ValueError("Generated NPC opening population is not conserved")
        return system

    def _seed_motion(self, start_tick):
        """Start authored/generated opening traffic on its first legal route edge."""
        for ship in self.ships:
            ship["motion"] = None
            if ship["node"] == ship["destination"]:
                continue
            route, _ = VoyageSystem.shortest_route(self, ship["node"], ship["destination"])
            if len(route) > 1:
                ship["motion"] = {"from": route[0], "to": route[1],
                                  "start_tick": start_tick, "paused_at_tick": None}

    @staticmethod
    def _chart_position(ship, total_ticks):
        motion = ship.get("motion")
        if motion is None:
            return SEA_NODES[ship["node"]]["xy"]
        effective_tick = (min(total_ticks, motion["paused_at_tick"])
                          if motion["paused_at_tick"] is not None else total_ticks)
        fraction = max(0.0, min(1.0, (effective_tick - motion["start_tick"])
                                / NPC_LANE_HOP_TICKS))
        start = SEA_NODES[motion["from"]]["xy"]
        end = SEA_NODES[motion["to"]]["xy"]
        return (start[0] + (end[0] - start[0]) * fraction,
                start[1] + (end[1] - start[1]) * fraction)

    @staticmethod
    def _set_motion_paused(ship, paused, total_ticks):
        motion = ship.get("motion")
        if motion is None:
            return
        if paused:
            if motion["paused_at_tick"] is None:
                motion["paused_at_tick"] = total_ticks
        elif motion["paused_at_tick"] is not None:
            motion["start_tick"] += max(0, total_ticks - motion["paused_at_tick"])
            motion["paused_at_tick"] = None

    def _start_next_motion(self, ship, start_tick):
        if ship.get("motion") is not None or ship["node"] == ship["destination"]:
            return
        route, _ = VoyageSystem.shortest_route(self, ship["node"], ship["destination"])
        if len(route) > 1:
            ship["motion"] = {"from": route[0], "to": route[1],
                              "start_tick": start_tick, "paused_at_tick": None}

    def _event(self, text):
        self.events = (self.events + [text])[-80:]

    def clear_active_contacts(self, total_ticks):
        """End lookout continuity when the player is no longer at sea."""
        for track in self.contact_tracks.values():
            if track["active"]:
                track.update({"active": False, "separation_since_tick": None,
                              "lost_tick": total_ticks})

    def contact_record_for_encounter(self, ship_id, encounter):
        record = self.player_contacts.get(ship_id)
        if record is None:
            return None
        instance_id = encounter.get("contact_instance_id", ship_id)
        if record.get("contact_instance_id", ship_id) != instance_id:
            return None
        return record

    def _update_sighting_report(self, world, ship, current_hour):
        society = getattr(world, "society", None)
        estimated_strength = (society._strength_for_ship(ship)["score"]
                              if society and society.mode == "generated" else None)
        self.sighting_reports[ship["id"]] = {
            "node": ship["node"], "observed_hour": current_hour,
            "crew": ship["crew"], "condition": ship["condition"],
            **({"estimated_strength": estimated_strength}
               if estimated_strength is not None else {}),
        }

    def _observe_contact(self, world, ship, distance, total_ticks, current_hour, *, enabled):
        """Track visibility hysteretically and return only a new instance."""
        if not enabled:
            return None
        ship_id = ship["id"]
        track = self.contact_tracks.get(ship_id)
        if track and track["active"]:
            if distance <= CONTACT_LOSS_RADIUS:
                track["last_seen_tick"] = total_ticks
                track["separation_since_tick"] = None
                self._update_sighting_report(world, ship, current_hour)
            else:
                if track["separation_since_tick"] is None:
                    track["separation_since_tick"] = total_ticks
                elif total_ticks - track["separation_since_tick"] >= CONTACT_LOSS_DELAY_TICKS:
                    track.update({"active": False, "separation_since_tick": None,
                                  "lost_tick": total_ticks})
            return None
        if distance > LOOKOUT_RADIUS:
            return None
        sequence = (track["sequence"] if track else 0) + 1
        instance_id = f"{ship_id}:{sequence}"
        self.contact_tracks[ship_id] = {
            "sequence": sequence, "instance_id": instance_id, "active": True,
            "first_seen_tick": total_ticks, "last_seen_tick": total_ticks,
            "separation_since_tick": None, "lost_tick": None,
        }
        self.sighted.add(ship_id)
        self._update_sighting_report(world, ship, current_hour)
        return instance_id

    def _encountered_ship(self, world, ship_id):
        voyage = world.voyages.voyage
        encounter = voyage.get("encounter") if voyage else None
        if (not voyage or voyage["status"] != "encounter" or not encounter
                or encounter.get("id") != ship_id):
            raise ValueError("That vessel is no longer the active lookout contact")
        ship = next((row for row in self.ships if row["id"] == ship_id), None)
        if ship is None:
            raise ValueError("The sighted vessel is no longer in the fleet")
        instance_id = encounter.get("contact_instance_id")
        if instance_id is not None:
            track = self.contact_tracks.get(ship_id)
            if not track or not track["active"] or track["instance_id"] != instance_id:
                raise ValueError("That lookout contact instance has ended")
        if encounter.get("node") != ship.get("node"):
            raise ValueError("The vessel has moved on from its reported encounter position")
        return ship

    def hail_ship(self, world, ship_id):
        ship = self._encountered_ship(world, ship_id)
        if sea_encounter.request_pending(world.voyages.voyage):
            raise ValueError("A pending encounter request blocks further signals")
        if world.combat.active:
            raise ValueError("Cannot exchange signals during active combat")
        if ship["threat"] == "pirates":
            raise ValueError("The hostile pirate vessel will not answer a peaceful hail")
        encounter = world.voyages.voyage["encounter"]
        record = self.contact_record_for_encounter(ship_id, encounter)
        if record and record["hailed"]:
            raise ValueError("A signal exchange with this vessel is already recorded")
        hour = world.clock.total_ticks // 360000
        if record is None:
            record = {"first_hour": hour, "last_hour": hour, "hailed": True, "aid_count": 0}
            if "contact_instance_id" in encounter:
                record["contact_instance_id"] = encounter["contact_instance_id"]
            self.player_contacts[ship_id] = record
        else:
            record.update({"last_hour": hour, "hailed": True})
        self._event(f"Lookouts exchanged signals with {ship['name']} ({ship['faction']}); no cargo or private intent was disclosed.")
        world.voyages._record(f"The captain exchanged signals with {ship['name']}.")
        world.message(f"{ship['name']} answers the hail. Their flag and observed condition are logged; no cargo or private intent is known.")
        world.voyages.advance_encounter_action_time(world, 6000)
        return record

    def assist_ship(self, world, ship_id):
        ship = self._encountered_ship(world, ship_id)
        if sea_encounter.request_pending(world.voyages.voyage):
            raise ValueError("A pending encounter request blocks repair aid")
        if world.combat.active:
            raise ValueError("Cannot offer repair aid during active combat")
        encounter = world.voyages.voyage.get("encounter")
        record = self.contact_record_for_encounter(ship_id, encounter)
        if ship["threat"] == "pirates":
            raise ValueError("Do not transfer repair stores to a hostile pirate vessel")
        contact = sea_encounter.contact_of(encounter, create=False)
        signaled = any(row["outcome"] == "answered" for row in contact["signals"])
        if not signaled and (not record or not record["hailed"]):
            raise ValueError("Exchange signals with the vessel before offering assistance")
        if record and record["aid_count"]:
            raise ValueError("Repair assistance to this vessel has already been given")
        if ship["condition"] > 60:
            raise ValueError("The vessel is not visibly distressed enough to warrant repair aid")
        world.economy.require_hold(world)
        if world.economy.available_cargo("iron") < 1:
            raise ValueError("Repair aid requires one uncommitted iron cargo unit at the hold")
        # The generated-NPC harbor repair authority already prices one iron for
        # a bounded 20-point condition repair; use the same cap for this aid.
        world.economy.remove("iron", 1)
        repair = min(20, 100 - ship["condition"])
        ship["condition"] += repair
        hour = world.clock.total_ticks // 360000
        if record is None:
            record = {"first_hour": hour, "last_hour": hour, "hailed": False, "aid_count": 0}
            if "contact_instance_id" in encounter:
                record["contact_instance_id"] = encounter["contact_instance_id"]
            self.player_contacts[ship_id] = record
        record["last_hour"] = hour
        record["aid_count"] = 1
        self._event(f"The Wayward gave {ship['name']} one iron cargo unit; their observed vessel condition improved by {repair}.")
        world.voyages._record(f"The crew gave one iron cargo unit to repair {ship['name']} by {repair} condition.")
        world.message(f"Repair aid delivered: one iron cargo unit; {ship['name']} condition +{repair}. No payment or favor is promised.")
        world.voyages.advance_encounter_action_time(world, 6000)
        return repair

    def advance_second(self, world, player_node, sighting_enabled=False, reserved_ship_ids=()):
        # Strategy/activity remains hourly, but each legal lane hop is observed
        # continuously across that hour on the shared chart coordinate system.
        total_ticks = world.clock.total_ticks
        current_hour = total_ticks // NPC_LANE_HOP_TICKS
        reserved = set(reserved_ship_ids)
        for ship in self.ships:
            self._set_motion_paused(ship, ship["id"] in reserved, total_ticks)

        # A paused edge may finish between global hour boundaries after its
        # clock is shifted on release. Complete it on its actual chart-time end.
        for ship in self.ships:
            motion = ship.get("motion")
            if (ship["id"] not in reserved and motion is not None
                    and motion["paused_at_tick"] is None
                    and total_ticks >= motion["start_tick"] + NPC_LANE_HOP_TICKS):
                ship["node"] = motion["to"]
                ship["motion"] = None

        # Catch up one boundary at a time. Normal play reaches each boundary
        # through ShipLife's per-second hook; generated prehistory may call this
        # once per hour. Both paths must end at the same strategic state.
        while self.hour < current_hour:
            step_hour = self.hour + 1
            boundary_tick = step_hour * NPC_LANE_HOP_TICKS
            for ship in self.ships:
                if ship["id"] in reserved:
                    continue
                motion = ship.get("motion")
                if (motion is not None and motion["paused_at_tick"] is None
                        and boundary_tick >= motion["start_tick"] + NPC_LANE_HOP_TICKS):
                    ship["node"] = motion["to"]
                    ship["motion"] = None
            for ship in self.ships:
                if ship["id"] in reserved or ship.get("motion") is not None:
                    continue
                if ship["node"] == ship["destination"]:
                    if getattr(world, "scenario_id", None) != "lawful-trader-mvp":
                        self._activity(world, ship)
                    choices = [b if a == ship["node"] else a
                               for a, b in LANES if a == ship["node"] or b == ship["node"]]
                    if choices:
                        # Deterministic faction-specific route preference.
                        ship["destination"] = sorted(choices)[(step_hour + len(ship["id"])) % len(choices)]
                else:
                    self._start_next_motion(ship, boundary_tick)
            self.hour = step_hour

        player_xy = (SEA_NODES[player_node]["xy"] if isinstance(player_node, str)
                     else (float(player_node[0]), float(player_node[1])))
        prior_sample = self._last_player_sample
        sighting = None
        for ship in self.ships:
            ship_xy = self._chart_position(ship, total_ticks)
            distance = math.hypot(ship_xy[0] - player_xy[0], ship_xy[1] - player_xy[1])
            instance_id = self._observe_contact(
                world, ship, distance, total_ticks, current_hour,
                enabled=sighting_enabled)
            if instance_id is not None:
                vessel_before = (self._chart_position(ship, prior_sample["tick"])
                                 if prior_sample else None)
                prior_player = prior_sample["xy"] if prior_sample else None
                elapsed = ((total_ticks - prior_sample["tick"]) / TICKS_PER_SECOND
                           if prior_sample else 0)
                prior_nearby = (vessel_before is not None and prior_player is not None
                                and math.hypot(vessel_before[0] - prior_player[0],
                                               vessel_before[1] - prior_player[1]) <= LOOKOUT_RADIUS)
                observation = relative_observation(
                    player_xy, ship_xy,
                    player_before=prior_player if prior_nearby else None,
                    vessel_before=vessel_before if prior_nearby else None,
                    elapsed_seconds=elapsed if prior_nearby else 0)
                sighting = {
                    key: deepcopy(ship[key])
                    for key in ("id", "name", "faction", "node", "crew",
                                "condition", "threat")
                }
                if isinstance(ship.get("organization_id"), str):
                    sighting["organization_id"] = ship["organization_id"]
                sighting["contact_instance_id"] = instance_id
                sighting["chart_position"] = [round(ship_xy[0], 5), round(ship_xy[1], 5)]
                sighting["relative_observation"] = observation
                break
        self._last_player_sample = {"tick": total_ticks, "xy": player_xy}
        if sighting is not None:
            return sighting
        return None

    def _activity(self, world, ship):
        self._repair_generated_vessel(world, ship)
        if ship["mission"] == "trade":
            port = SEA_NODES[ship["node"]].get("port")
            # Freeport's new player-facing merchant is part of the authored
            # lawful MVP. Preserve the pre-existing legacy NPC anchorage loop:
            # its cargo/captive ledger must not become a different market model.
            if port == "freeport" and getattr(world, "scenario_id", None) != "lawful-trader-mvp":
                port = None
            if port:
                local_merchants = [mid for mid, merchant in MERCHANTS.items() if merchant["port"] == port]
                for item_id, carried in list(ship["cargo"].items()):
                    if carried <= 0:
                        continue
                    merchant_id = next((mid for mid in local_merchants
                                        if CATALOG[item_id]["category"] in MERCHANTS[mid]["accepts"]), None)
                    if not merchant_id:
                        continue
                    amount = min(3, carried)
                    total = world.economy.quote(merchant_id, item_id, amount, False)
                    market = world.economy.markets[merchant_id]
                    if market["money"] < total:
                        continue
                    market["money"] -= total
                    market["stock"][item_id] = market["stock"].get(item_id, 0) + amount
                    ship["cargo"][item_id] -= amount
                    ship["silver"] += total
                    self._event(f"{ship['name']} sold {amount} {item_id} to {MERCHANTS[merchant_id]['name']} at {SEA_NODES[ship['node']]['name']}.")
                # Buy the island's surplus for the next leg through the same
                # finite stock/purse ledgers used by the player's market.
                preferred = "grain" if port == "verdant" else "iron"
                buyer_id = next((mid for mid in local_merchants
                                 if CATALOG[preferred]["category"] in MERCHANTS[mid]["accepts"]
                                 and world.economy.markets[mid]["stock"].get(preferred, 0) > 0), None)
                if buyer_id:
                    market = world.economy.markets[buyer_id]
                    amount = min(2, market["stock"][preferred])
                    total = sum(world.economy.price(buyer_id, preferred, True, market["stock"][preferred] - i)
                                for i in range(amount))
                    if ship["silver"] >= total:
                        market["money"] += total
                        market["stock"][preferred] -= amount
                        ship["cargo"][preferred] = ship["cargo"].get(preferred, 0) + amount
                        ship["silver"] -= total
                        self._event(f"{ship['name']} bought {amount} {preferred} from {MERCHANTS[buyer_id]['name']} for the next market.")
        elif ship["mission"] == "hunt" and self.roster_mode != "generated":
            # Generated societies resolve pursuit and plunder in NpcSocietySystem,
            # which owns knowledge, motive, outcome, and event-linked state. Keep
            # this legacy shortcut for the authored fleet only; otherwise a
            # generated hunter steals the same merchant provisions every pulse
            # without debiting the target ledger.
            target = next((s for s in self.ships if s["id"] != ship["id"] and s["node"] == ship["node"] and s["threat"] != "pirates"), None)
            if target:
                if target["threat"] == "marines":
                    if target["crew"] > 1:
                        target["crew"] -= 1
                        ship["captives"] += 1
                        self._event(f"{ship['name']} raided a government tender; one surviving crew member was taken captive.")
                else:
                    ship["cargo"]["grain"] = ship["cargo"].get("grain", 0) + 2
                    self._event(f"{ship['name']} robbed a merchant vessel for provisions.")
        if (ship["node"] == "freeport" and ship["captives"]
                and self.market["silver"] >= ship["captives"] * 30):
            people = ship["captives"]
            # Explicit NPC-only coercive slave market loop: people are transferred
            # between holder and market, never spawned/deleted by a sale.
            self.market["enslaved_people"] += people
            ship["captives"] = 0
            ship["silver"] += people * 30
            self.market["silver"] -= people * 30
            self._event(f"{ship['name']} sold {people} captive(s) into enslavement at Free Syndicate Anchorage.")
        if ship["node"] == "freeport" and self.market["enslaved_people"]:
            if ship["mission"] == "trade" and ship["silver"] >= 30:
                people = 1
                self.market["enslaved_people"] -= people
                ship["captives"] += people
                ship["silver"] -= 30
                self.market["silver"] += 30
                self._event(f"{ship['name']} purchased {people} enslaved person(s) at Free Syndicate Anchorage.")

    def _repair_generated_vessel(self, world, ship):
        """Buy finite iron-based hull repairs only for generated NPC vessels."""
        if self.roster_mode != "generated" or ship.get("condition", 100) >= 100:
            return False
        port = SEA_NODES.get(ship.get("node"), {}).get("port")
        if not port or not hasattr(world, "economy"):
            return False
        merchant_id = next((mid for mid, merchant in MERCHANTS.items()
                            if merchant["port"] == port and "metal" in merchant["accepts"]
                            and world.economy.markets[mid]["stock"].get("iron", 0) > 0), None)
        if merchant_id is None:
            return False
        market = world.economy.markets[merchant_id]
        cost = world.economy.quote(merchant_id, "iron", 1, True)
        if ship.get("silver", 0) < cost:
            return False
        market["stock"]["iron"] -= 1
        market["money"] += cost
        ship["silver"] -= cost
        repair = min(20, 100 - ship["condition"])
        ship["condition"] += repair
        self._event(f"{ship['name']} spent {cost} silver and one iron at {port} for "
                    f"{repair} hull-condition repair.")
        return True

    def snapshot(self):
        return {"hour": self.hour, "ships": deepcopy(self.ships), "market": dict(self.market),
                "events": list(self.events), "sighted": sorted(self.sighted),
                "sighting_reports": deepcopy(self.sighting_reports),
                "player_contacts": deepcopy(self.player_contacts),
                "contact_tracks": deepcopy(self.contact_tracks),
                "roster_mode": self.roster_mode, "population_total": self.population_total,
                "generation_manifest": deepcopy(self.generation_manifest)}

    @classmethod
    def restore(cls, data, generation_manifest=None, *, scenario_id=None, current_tick=None):
        system = cls()
        if data is None:
            if scenario_id is not None:
                raise ValueError("Authored scenario save is missing its NPC fleet")
            if generation_manifest is not None:
                from pirates.npc_generation import generate_society, validate_manifest
                system = cls.from_generated(generate_society(validate_manifest(generation_manifest)))
            return system
        if type(data.get("hour")) is not int or data["hour"] < 0 or not isinstance(data.get("ships"), list):
            raise ValueError("Invalid NPC fleet state")
        mode = data.get("roster_mode", "authored")
        if mode not in ("authored", "generated"):
            raise ValueError("Invalid NPC roster mode")
        if generation_manifest is not None:
            from pirates.npc_generation import validate_manifest
            generation_manifest = validate_manifest(generation_manifest)
            if mode != "generated" or data.get("generation_manifest") != generation_manifest:
                raise ValueError("NPC roster and generation manifest disagree")
        elif mode == "generated":
            raise ValueError("Generated NPC roster requires a generation manifest")
        canonical = {s["id"]: s for s in system.ships}
        rows = deepcopy(data["ships"])
        for row in rows:
            row.setdefault("motion", None)
        legacy_ids = {"syndicate_1", "government_1", "raider_1"}
        if mode == "authored" and len(rows) == 3 and {row.get("id") for row in rows} == legacy_ids:
            # Older v3 saves had three NPC ships and no condition field. Preserve
            # their ledgers, then seed the new named society vessels once.
            for row in rows:
                row.setdefault("condition", canonical[row["id"]]["condition"])
            added = [deepcopy(canonical[sid])
                     for sid in ("blackwake_escort", "jackal_sloop")]
            for row in added:
                row["motion"] = None  # New compatibility hulls have no travel history.
            rows.extend(added)
        if mode == "authored" and len(rows) != len(system.ships):
            raise ValueError("Invalid NPC fleet count")
        seen = set()
        for row in rows:
            if not isinstance(row.get("id"), str) or not row["id"] or row["id"] in seen:
                raise ValueError("Invalid NPC ship identity")
            seen.add(row["id"])
            base = canonical.get(row["id"])
            if mode == "authored" and (base is None or any(
                    row.get(k) != base[k] for k in ("name", "faction", "mission", "threat"))):
                raise ValueError("NPC ship identity changed")
            if any(not isinstance(row.get(key), str) or not row[key]
                   for key in ("name", "faction", "mission", "threat")):
                raise ValueError("Invalid NPC ship description")
            if mode == "generated" and (row["mission"] not in ("trade", "hunt", "patrol")
                    or row["threat"] not in ("merchant", "pirates", "marines")):
                raise ValueError("Invalid generated NPC ship activity")
            if row.get("node") not in SEA_NODES or row.get("destination") not in SEA_NODES:
                raise ValueError("Invalid NPC ship route")
            motion = row.get("motion")
            if motion is not None:
                limit_tick = (current_tick if type(current_tick) is int
                              else (data["hour"] + 1) * NPC_LANE_HOP_TICKS)
                if (not isinstance(motion, dict)
                        or set(motion) != {"from", "to", "start_tick", "paused_at_tick"}
                        or motion["from"] != row["node"]
                        or motion["from"] not in SEA_NODES or motion["to"] not in SEA_NODES
                        or not any({motion["from"], motion["to"]} == set(lane) for lane in LANES)
                        or type(motion["start_tick"]) is not int
                        or not 0 <= motion["start_tick"] <= limit_tick
                        or motion["paused_at_tick"] is not None
                        and (type(motion["paused_at_tick"]) is not int
                             or not motion["start_tick"] <= motion["paused_at_tick"] <= limit_tick)):
                    raise ValueError("Invalid NPC lane motion state")
            for key in ("silver", "crew", "captives"):
                if type(row.get(key)) is not int or row[key] < 0:
                    raise ValueError("Invalid NPC cargo ledger")
            if type(row.get("condition")) is not int or not 0 <= row["condition"] <= 100:
                raise ValueError("Invalid NPC vessel condition")
            if not isinstance(row.get("cargo"), dict) or any(k not in ("grain", "iron", "beans", "shot") or type(v) is not int or v < 0 for k, v in row["cargo"].items()):
                raise ValueError("Invalid NPC trade cargo")
            if mode == "generated":
                if (not isinstance(row.get("organization_id"), str)
                        or not isinstance(row.get("captain_id"), str)
                        or not isinstance(row.get("member_ids"), list)
                        or len(set(row["member_ids"])) != len(row["member_ids"])
                        or row["captain_id"] not in row["member_ids"]):
                    raise ValueError("Invalid generated NPC vessel hierarchy")
        market = data["market"]
        if set(market) != {"enslaved_people", "silver"} or any(type(v) is not int or v < 0 for v in market.values()):
            raise ValueError("Invalid Freeport market")
        headcount = market["enslaved_people"] + sum(row["crew"] + row["captives"] for row in rows)
        population_total = data.get("population_total", 40)
        if type(population_total) is not int or population_total < 0 or headcount != population_total:
            raise ValueError("NPC captive/crew headcount was created or lost")
        if scenario_id == "lawful-trader-mvp":
            if (mode != "authored" or population_total != 34
                    or market["enslaved_people"] != 0
                    or any(row["captives"] for row in rows)):
                raise ValueError("Invalid lawful-trader sample population baseline")
        elif scenario_id is not None:
            raise ValueError("Unsupported scenario NPC roster")
        elif mode == "authored" and population_total != 40:
            raise ValueError("Invalid authored NPC headcount baseline")
        events = data["events"]
        if not isinstance(events, list) or len(events) > 80 or any(not isinstance(x, str) for x in events):
            raise ValueError("Invalid NPC event history")
        system.hour, system.ships, system.market, system.events = data["hour"], rows, dict(market), list(events)
        system._last_player_sample = None
        system.sighted = set(data.get("sighted", []))
        if any(sid not in seen for sid in system.sighted):
            raise ValueError("Invalid NPC sighting ledger")
        tracks = deepcopy(data.get("contact_tracks", {}))
        limit_tick = (current_tick if type(current_tick) is int
                      else (system.hour + 1) * NPC_LANE_HOP_TICKS)
        if not isinstance(tracks, dict) or not set(tracks).issubset(system.sighted):
            raise ValueError("Invalid NPC lookout contact tracks")
        for ship_id, track in tracks.items():
            fields = {"sequence", "instance_id", "active", "first_seen_tick",
                      "last_seen_tick", "separation_since_tick", "lost_tick"}
            if (not isinstance(track, dict) or set(track) != fields
                    or type(track["sequence"]) is not int
                    or not 1 <= track["sequence"] <= 1_000_000
                    or track["instance_id"] != f"{ship_id}:{track['sequence']}"
                    or type(track["active"]) is not bool
                    or type(track["first_seen_tick"]) is not int
                    or type(track["last_seen_tick"]) is not int
                    or not 0 <= track["first_seen_tick"] <= track["last_seen_tick"] <= limit_tick):
                raise ValueError("Invalid NPC lookout contact track")
            separated = track["separation_since_tick"]
            lost = track["lost_tick"]
            if track["active"]:
                if (lost is not None or separated is not None and
                        (type(separated) is not int
                         or not track["last_seen_tick"] <= separated <= limit_tick)):
                    raise ValueError("Invalid active NPC lookout contact track")
            elif (separated is not None or type(lost) is not int
                  or not track["last_seen_tick"] <= lost <= limit_tick):
                raise ValueError("Invalid lost NPC lookout contact track")
        system.contact_tracks = tracks
        reports = deepcopy(data.get("sighting_reports", {}))
        if (not isinstance(reports, dict) or not set(reports).issubset(system.sighted)
                or any(not isinstance(report, dict) or report.get("node") not in SEA_NODES
                       or type(report.get("observed_hour")) is not int
                       or not 0 <= report["observed_hour"] <= system.hour
                       or type(report.get("crew")) is not int or report["crew"] < 0
                       or type(report.get("condition")) is not int
                       or not 0 <= report["condition"] <= 100
                       or "estimated_strength" in report
                       and (type(report["estimated_strength"]) is not int
                            or not 0 <= report["estimated_strength"] <= 100)
                       for report in reports.values())):
            raise ValueError("Invalid NPC player sighting reports")
        system.sighting_reports = reports
        contacts = deepcopy(data.get("player_contacts", {}))
        if not isinstance(contacts, dict) or not set(contacts).issubset(system.sighted):
            raise ValueError("Invalid persisted player ship contacts")
        for ship_id, row in contacts.items():
            required = {"first_hour", "last_hour", "hailed", "aid_count"}
            if (not isinstance(row, dict) or set(row) not in
                    (required, required | {"contact_instance_id"})
                    or type(row["first_hour"]) is not int
                    or type(row["last_hour"]) is not int
                    or not 0 <= row["first_hour"] <= row["last_hour"] <= system.hour
                    or type(row["hailed"]) is not bool
                    or type(row["aid_count"]) is not int or row["aid_count"] not in (0, 1)):
                raise ValueError("Invalid persisted player ship contact")
            instance_id = row.get("contact_instance_id")
            if instance_id is not None:
                prefix = f"{ship_id}:"
                suffix = (instance_id[len(prefix):] if isinstance(instance_id, str)
                          and instance_id.startswith(prefix) else "")
                if (not suffix.isdigit() or str(int(suffix)) != suffix
                        or not 1 <= int(suffix) <= 1_000_000
                        or ship_id in tracks and int(suffix) > tracks[ship_id]["sequence"]):
                    raise ValueError("Invalid persisted player contact instance")
        system.player_contacts = contacts
        system.roster_mode = mode
        system.population_total = population_total
        system.generation_manifest = deepcopy(generation_manifest)
        return system
