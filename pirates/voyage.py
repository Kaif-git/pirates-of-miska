"""Abstract sea routes, officer consultations, free-time orders and rival fleets.

The overworld is deliberately strategic rather than ship-handling physics. Time
still flows through ShipLife, so ordinary chores, needs and wear continue aboard.
"""
from __future__ import annotations

from copy import deepcopy
import heapq
import math

from pirates.economy import PORTS, MERCHANTS, CATALOG, build_port

SEA_NODES = {
    "verdant": {"name": "Verdant Cay", "xy": (1, 3), "port": "verdant"},
    "shoals": {"name": "The Glass Shoals", "xy": (3, 2)},
    "cinder": {"name": "Cinder Quay", "xy": (6, 4), "port": "cinder"},
    "navy_lane": {"name": "Government Patrol Lane", "xy": (4, 5)},
    "freeport": {"name": "Free Syndicate Anchorage", "xy": (8, 2)},
    "deepwater": {"name": "The Blue Reach", "xy": (4, 0)},
}
LANES = (("verdant", "shoals"), ("shoals", "cinder"), ("cinder", "navy_lane"),
         ("navy_lane", "verdant"), ("shoals", "navy_lane"), ("shoals", "deepwater"),
         ("deepwater", "freeport"), ("freeport", "cinder"), ("freeport", "navy_lane"))
SEA_ROLES = ("navigator", "quartermaster", "purser")
CAPTAIN_ACTIONS = ("rest", "study_charts", "inspect_ship", "talk_with_crew")
CREW_ORDERS = ("work_as_needed", "sword_drill", "brawling_drill", "rest")


class VoyageSystem:
    def __init__(self):
        self.position = "verdant"
        self.roles = {"navigator": "suri", "quartermaster": "ivo", "purser": "mara"}
        self.voyage = None
        self.captain_action = "rest"
        self.crew_order = {"order": "work_as_needed", "leader": "tavi", "members": ["tavi"]}
        self.last_sim_hour = 0
        self.events = []
        self.arrival_interrupt = False
        self.captain_fatigue = 20.0

    def _crew(self, world, sid):
        sailor = world.ship.sailor(sid)
        if not sailor:
            raise ValueError("Choose a current crew member")
        return sailor

    def role_report(self, world):
        crew = world.ship.crew
        nav = world.ship.sailor(self.roles["navigator"])
        quartermaster = world.ship.sailor(self.roles["quartermaster"])
        purser = world.ship.sailor(self.roles["purser"])
        distance = self.route_distance(self.position, "cinder" if self.position != "cinder" else "verdant")
        duration_hours = distance * 0.75
        # Current simulation's hunger/thirst meters rise in the hundreds per day;
        # these are watchful warnings, not claims that stores alone prevent hunger.
        water_days = world.ship.stores["water"] / max(1, len(crew) * 2.2)
        meals_days = world.ship.meals / max(1, len(crew) * 2.0)
        raw_food_days = world.ship.stores["raw_food"] / max(1, len(crew) * 0.45)
        warnings = []
        if water_days < duration_hours / 24:
            warnings.append(f"Quartermaster {quartermaster.name}: water is short for this route ({water_days:.1f} days).")
        if meals_days < duration_hours / 24:
            warnings.append(f"Quartermaster {quartermaster.name}: prepared meals cover about {meals_days:.1f} days.")
        if raw_food_days < duration_hours / 24:
            warnings.append(f"Quartermaster {quartermaster.name}: raw galley ingredients cover about {raw_food_days:.1f} days.")
        if world.ship.hull < 50:
            warnings.append(f"Quartermaster {quartermaster.name}: hull condition is low; consider a safer route.")
        if min(water_days, meals_days, raw_food_days) < 1:
            suggestion = "Verdant Cay" if self.position != "verdant" else "the nearest provision stop"
            warnings.append(f"Quartermaster {quartermaster.name} suggests a provisioning voyage to {suggestion} before a long run.")
        else:
            suggestion = "Cinder Quay" if self.position != "cinder" else "Verdant Cay"
            warnings.append(f"Quartermaster {quartermaster.name} suggests {suggestion} for the next stores/trade run.")
        wind = self.wind_at(world.clock.day, self.position)
        return [f"Navigator: {nav.name} | Rigging skill {nav.skills['rig']:.1f}",
                f"Quartermaster: {quartermaster.name} | estimated water {water_days:.1f}d, meals {meals_days:.1f}d, raw food {raw_food_days:.1f}d",
                f"Numbers-skilled purser: {purser.name} | stores-ledger aptitude {purser.personality['diligence']:.0f}/100; repair skill {purser.skills['repair']:.1f}",
                f"Navigator's wind reading: {wind['name']} ({wind['dx']:+d},{wind['dy']:+d}); route estimate {duration_hours:.1f} hours.",
                *(warnings or [f"Quartermaster {quartermaster.name}: stores look adequate for the estimate."]),
                "Rations are estimates from current stores; crew needs still simulate individually."]

    def assign_role(self, world, role, sid):
        if role not in SEA_ROLES:
            raise ValueError("Unknown shipboard officer role")
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

    def map_coordinate(self):
        v = self.voyage
        if not v or v["status"] in ("arrived", "aborted"):
            return SEA_NODES[self.position]["xy"]
        route = v["route"]
        a = SEA_NODES[route[v["leg"]]]["xy"]
        b = SEA_NODES[route[min(v["leg"] + 1, len(route) - 1)]]["xy"]
        p = max(0.0, min(1.0, v["leg_progress"]))
        return (a[0] + (b[0] - a[0]) * p, a[1] + (b[1] - a[1]) * p)

    @staticmethod
    def wind_at(day, node):
        vectors = (("Trade wind", 1, 0), ("Crosswind", 0, 1), ("Headwind", -1, 0), ("Light wind", 0, 0))
        name, dx, dy = vectors[(day + sum(map(ord, node))) % len(vectors)]
        return {"name": name, "dx": dx, "dy": dy}

    def plan(self, world, destination, preferred_waypoint=None):
        if self.voyage and self.voyage["status"] == "underway":
            raise ValueError("A voyage is already underway")
        if destination not in SEA_NODES or destination == self.position:
            raise ValueError("Choose a different chart location")
        route, distance = self.shortest_route(self.position, destination)
        if preferred_waypoint and preferred_waypoint not in route:
            # Allow a deliberate detour only when both legs are charted.
            first, d1 = self.shortest_route(self.position, preferred_waypoint)
            second, d2 = self.shortest_route(preferred_waypoint, destination)
            route, distance = first + second[1:], d1 + d2
        nav = world.ship.sailor(self.roles["navigator"])
        purser = world.ship.sailor(self.roles["purser"])
        qmaster = world.ship.sailor(self.roles["quartermaster"])
        wind = self.wind_at(world.clock.day, self.position)
        chart_notes = self.voyage.get("chart_notes", 0) if self.voyage and self.voyage["status"] == "arrived" else 0
        duration = max(1800, round(distance * 2700 / (1 + nav.skills["rig"] * 0.025 + (0.1 if wind["dx"] else 0)
                                                         + min(0.15, chart_notes * 0.01))))
        water_days = world.ship.stores["water"] / max(1, len(world.ship.crew) * 2.2)
        ration_days = world.ship.meals / max(1, len(world.ship.crew) * 2)
        return {"route": route, "distance": round(distance, 2), "duration_seconds": duration,
                "duration_hours": round(duration / 3600, 1), "wind": wind,
                "navigator": nav.name, "quartermaster": qmaster.name, "purser": purser.name,
                "water_days": round(water_days, 1), "meal_days": round(ration_days, 1)}

    def confirm(self, world, destination, preferred_waypoint=None):
        plan = self.plan(world, destination, preferred_waypoint)
        self.voyage = {**plan, "status": "underway", "leg": 0, "leg_progress": 0.0,
                       "elapsed_seconds": 0, "destination": destination, "encounter": None}
        self.position = plan["route"][0]
        self._record(f"Captain ordered a voyage to {SEA_NODES[destination]['name']} via {' → '.join(SEA_NODES[n]['name'] for n in plan['route'])}.")
        world.message(f"The navigator lays in the route; {self.roles['quartermaster']} warns of stores. {plan['duration_hours']} hour estimate.")
        return plan

    def _record(self, text):
        self.events = (self.events + [text])[-60:]

    def step_second(self, world, npc_sighting=None):
        if not self.voyage or self.voyage["status"] != "underway":
            return
        v = self.voyage
        v["elapsed_seconds"] += 1
        segment_count = len(v["route"]) - 1
        scaled_progress = min(float(segment_count), v["elapsed_seconds"] * segment_count / v["duration_seconds"])
        v["leg_progress"] = scaled_progress - v["leg"]
        # NPC simulation is coarse and deterministic; sightings interrupt in the
        # second ship life reaches them rather than letting a 10-minute wait pass.
        npc_sighting = npc_sighting if v["elapsed_seconds"] > 1800 else None
        if npc_sighting:
            v["status"] = "encounter"
            v["encounter"] = npc_sighting
            self._record(f"Lookouts sight {npc_sighting['name']} nearby at {SEA_NODES[self.position]['name']}.")
            world.message(f"LOOKOUTS: {npc_sighting['name']} nearby! The voyage pauses; the ship is yours to command.")
            return
        if v["leg_progress"] >= 1:
            v["leg"] += 1
            if v["leg"] >= len(v["route"]) - 1:
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
            else:
                self.position = v["route"][v["leg"]]
                v["leg_progress"] = max(0.0, scaled_progress - v["leg"])
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
        sailor.activity = f"{drill} with {order['leader']}"
        sailor.energy = max(0, sailor.energy - 0.0008)
        sailor.boredom = max(0, sailor.boredom - 0.0013)
        if self.voyage["elapsed_seconds"] % 60 == 0:
            sailor.think(f"Practised {drill} under {order['leader']}.", 0.01)
            if order["order"] == "sword_drill":
                world.economy.weapon_xp[sailor.id] = world.economy.weapon_xp.get(sailor.id, 0) + 0.1
        return True

    def set_captain_action(self, action):
        if action not in CAPTAIN_ACTIONS:
            raise ValueError("Unknown captain free-time action")
        self.captain_action = action

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
        return bool(self.voyage and self.voyage["status"] == "encounter")

    def interrupt_current_advance(self):
        return self.stop_after_encounter() or self.arrival_interrupt

    def continue_after_sighting(self):
        if not self.voyage or self.voyage["status"] != "encounter":
            raise ValueError("There is no active ship sighting")
        self._record(f"Captain holds course after sighting {self.voyage['encounter']['name']}.")
        self.voyage["encounter"] = None
        self.voyage["status"] = "underway"

    def turn_back(self, world):
        if not self.voyage or self.voyage["status"] != "encounter":
            raise ValueError("Turn back is available only during a sighting")
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
            self.arrival_interrupt = True
            world.economy.port = port
            build_port(world)
            world.restore_harbor(port)
            world.at_sea = False
            world.player.x, world.player.y = 76, 25
            return
        plan = self.plan(world, target)
        self.voyage = {**plan, "status": "underway", "leg": 0, "leg_progress": 0.0,
                       "elapsed_seconds": 0, "destination": target, "encounter": None}
        self.position = current
        self.voyage["route"], self.voyage["distance"] = self.shortest_route(current, target)
        self._record(f"Captain ordered a return to {SEA_NODES[target]['name']}.")

    def snapshot(self):
        return {"position": self.position, "roles": dict(self.roles), "voyage": deepcopy(self.voyage),
                "captain_action": self.captain_action, "crew_order": deepcopy(self.crew_order),
                "last_sim_hour": self.last_sim_hour, "events": list(self.events),
                "captain_fatigue": self.captain_fatigue}

    @classmethod
    def restore(cls, data, world):
        system = cls()
        if data is None:
            return system
        if data["position"] not in SEA_NODES or set(data["roles"]) != set(SEA_ROLES):
            raise ValueError("Invalid voyage location or officer roster")
        ids = {c.id for c in world.ship.crew}
        if any(sid not in ids for sid in data["roles"].values()) or len(set(data["roles"].values())) != len(SEA_ROLES):
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
        if (not isinstance(order, dict) or order.get("order") not in CREW_ORDERS
                or order.get("leader") not in ids or not isinstance(order.get("members"), list)
                or order["leader"] not in order["members"] or len(set(order["members"])) != len(order["members"])
                or any(sid not in ids for sid in order["members"])):
            raise ValueError("Invalid crew order")
        if order["order"] == "sword_drill" and any("weapon" not in world.economy.gear.get(sid, {}) for sid in order["members"]):
            raise ValueError("Saved sword team is not equipped")
        voyage = data["voyage"]
        if voyage is not None:
            if (voyage.get("status") not in ("underway", "encounter", "arrived", "aborted")
                    or voyage.get("destination") not in SEA_NODES
                    or (voyage.get("status") in ("underway", "encounter") and voyage.get("destination") == data["position"])):
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
            if (not math.isfinite(voyage.get("distance", distance))
                    or (voyage["distance"] <= 0 and voyage["status"] != "arrived")):
                raise ValueError("Invalid voyage distance")
            if voyage["status"] == "encounter" and not isinstance(voyage.get("encounter"), dict):
                raise ValueError("Encounter requires a sighting record")
        system.position = data["position"]
        system.roles = dict(data["roles"])
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
        return system


class NpcFleetSystem:
    """Three persistent rival ships with bounded autonomous economic activity."""
    def __init__(self):
        self.hour = 0
        self.sighted = set()
        self.ships = [
            {"id": "syndicate_1", "name": "Free Syndicate cutter", "faction": "Free Syndicate", "node": "verdant", "destination": "cinder", "mission": "trade", "cargo": {"grain": 12}, "silver": 240, "crew": 7, "captives": 0, "threat": "merchant"},
            {"id": "government_1", "name": "Government marine patrol", "faction": "World Government", "node": "navy_lane", "destination": "shoals", "mission": "patrol", "cargo": {"shot": 8}, "silver": 400, "crew": 10, "captives": 0, "threat": "marines"},
            {"id": "raider_1", "name": "Rogue pirate brig", "faction": "Free Syndicate", "node": "freeport", "destination": "navy_lane", "mission": "hunt", "cargo": {"beans": 5}, "silver": 90, "crew": 6, "captives": 1, "threat": "pirates"},
        ]
        self.market = {"enslaved_people": 5, "silver": 500}
        self.events = []

    def _event(self, text):
        self.events = (self.events + [text])[-80:]

    def advance_second(self, world, player_node, sighting_enabled=False):
        # One fleet activity pulse per simulated hour. Ships move only on charted
        # lanes; their cargo, purses and captive headcounts remain conserved.
        if world.clock.total_ticks // 360000 <= self.hour:
            return None
        self.hour = world.clock.total_ticks // 360000
        for ship in self.ships:
            if ship["node"] == ship["destination"]:
                self._activity(world, ship)
                choices = [b if a == ship["node"] else a for a, b in LANES if a == ship["node"] or b == ship["node"]]
                if choices:
                    # Deterministic faction-specific route preference.
                    ship["destination"] = sorted(choices)[(self.hour + len(ship["id"])) % len(choices)]
            else:
                route, _ = VoyageSystem.shortest_route(self, ship["node"], ship["destination"])
                if len(route) > 1:
                    ship["node"] = route[1]
            player_xy = SEA_NODES[player_node]["xy"] if isinstance(player_node, str) else player_node
            ship_xy = SEA_NODES[ship["node"]]["xy"]
            nearby = math.hypot(ship_xy[0] - player_xy[0], ship_xy[1] - player_xy[1]) <= 1.25
            if sighting_enabled and nearby and ship["id"] not in self.sighted:
                self.sighted.add(ship["id"])
                return deepcopy(ship)
        return None

    def _activity(self, world, ship):
        if ship["mission"] == "trade":
            port = SEA_NODES[ship["node"]].get("port")
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
        elif ship["mission"] == "hunt":
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

    def snapshot(self):
        return {"hour": self.hour, "ships": deepcopy(self.ships), "market": dict(self.market), "events": list(self.events), "sighted": sorted(self.sighted)}

    @classmethod
    def restore(cls, data):
        system = cls()
        if data is None:
            return system
        if type(data.get("hour")) is not int or data["hour"] < 0 or len(data.get("ships", [])) != 3:
            raise ValueError("Invalid NPC fleet state")
        canonical = {s["id"]: s for s in system.ships}
        seen = set()
        for row in data["ships"]:
            if row.get("id") not in canonical or row["id"] in seen:
                raise ValueError("Invalid NPC ship identity")
            seen.add(row["id"])
            base = canonical[row["id"]]
            if any(row.get(k) != base[k] for k in ("name", "faction", "mission", "threat")):
                raise ValueError("NPC ship identity changed")
            if row.get("node") not in SEA_NODES or row.get("destination") not in SEA_NODES:
                raise ValueError("Invalid NPC ship route")
            for key in ("silver", "crew", "captives"):
                if type(row.get(key)) is not int or row[key] < 0:
                    raise ValueError("Invalid NPC cargo ledger")
            if not isinstance(row.get("cargo"), dict) or any(k not in ("grain", "iron", "beans", "shot") or type(v) is not int or v < 0 for k, v in row["cargo"].items()):
                raise ValueError("Invalid NPC trade cargo")
        market = data["market"]
        if set(market) != {"enslaved_people", "silver"} or any(type(v) is not int or v < 0 for v in market.values()):
            raise ValueError("Invalid Freeport market")
        headcount = market["enslaved_people"] + sum(row["crew"] + row["captives"] for row in data["ships"])
        if headcount != 29:
            raise ValueError("NPC captive/crew headcount was created or lost")
        events = data["events"]
        if not isinstance(events, list) or len(events) > 80 or any(not isinstance(x, str) for x in events):
            raise ValueError("Invalid NPC event history")
        system.hour, system.ships, system.market, system.events = data["hour"], deepcopy(data["ships"]), dict(market), list(events)
        system.sighted = set(data.get("sighted", []))
        if any(sid not in canonical for sid in system.sighted):
            raise ValueError("Invalid NPC sighting ledger")
        return system
