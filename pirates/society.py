"""Persistent named NPC society, strategic diplomacy, clashes and sea news."""
from __future__ import annotations

from copy import deepcopy
import math

from pirates.voyage import LANES, SEA_NODES, VoyageSystem
from pirates.npc_decisions import choose_action
from pirates.npc_generation import derive_command_power, random_stream
from pirates.npc_strength import vessel_strength_breakdown

MAX_NEWS = 120
MAX_PAIR_TIMERS = 100
MAX_MEMORIES = 80
ORGANIZATION_CLASH_COOLDOWN_HOURS = 96
PRIORITY_NEWS_KINDS = frozenset({
    "npc_clash", "raid", "rivalry", "petty_feud", "agreement_breach",
    "alliance", "diplomacy", "agreement_expiry",
})
PETTY_SLIGHTS = (
    ("lucky_hat", "called {target}'s lucky hat an upside-down bait bucket"),
    ("borrowed_shanty", "claimed {initiator}'s crew had invented the rival's favourite shanty"),
    ("parrot_laugh", "laughed when a parrot copied {target}'s battle cry"),
    ("reef_naming", "argued over who had the right to give a reef its ridiculous name"),
    ("stew_insult", "called {target}'s celebrated stew boiled rope with onions"),
    ("flag_mixup", "mistook {target}'s proud flag for a freshly hung laundry sheet"),
)

PEOPLE = {
    "mara_vey": {"name": "Mara Vey", "title": "Captain", "organization": "blackwake_fleet",
                 "ship": "raider_1", "ambition": "Make Blackwake the most feared fleet in Miska",
                 "target": "world_government", "reputation": 82},
    "tamsin_rill": {"name": "Tamsin Rill", "title": "Captain", "organization": "blackwake_fleet",
                    "ship": "blackwake_escort", "ambition": "Earn a name independent of the flagship",
                    "target": "blackwake_fleet", "reputation": 57},
    "oren_pike": {"name": "Oren Pike", "title": "Captain", "organization": "cinder_free_company",
                  "ship": "syndicate_1", "ambition": "Grow wealthy without taking sides",
                  "target": "cinder_free_company", "reputation": 34},
    "sella_quill": {"name": "Sella Quill", "title": "Captain", "organization": "salt_jackals",
                    "ship": "jackal_sloop", "ambition": "Keep the Jackals independent",
                    "target": "blackwake_fleet", "reputation": 43},
    "commander_rusk": {"name": "Ilyan Rusk", "title": "Commander", "organization": "world_government",
                       "ship": "government_1", "ambition": "Break Blackwake's reach across the lanes",
                       "target": "blackwake_fleet", "reputation": 68},
}

ORGANIZATIONS = {
    "blackwake_fleet": {"name": "Blackwake Fleet", "kind": "pirate_fleet", "leader": "mara_vey",
                        "ships": ["raider_1", "blackwake_escort"], "ambition": "Expand influence",
                        "target": "salt_jackals", "reputation": 76},
    "cinder_free_company": {"name": "Cinder Free Company", "kind": "independent_crew", "leader": "oren_pike",
                            "ships": ["syndicate_1"], "ambition": "Trade for profit and avoid entanglements",
                            "target": "cinder", "reputation": 38},
    "salt_jackals": {"name": "Salt Jackals", "kind": "independent_crew", "leader": "sella_quill",
                     "ships": ["jackal_sloop"], "ambition": "Protect independence and reputation",
                     "target": "blackwake_fleet", "reputation": 45},
    "world_government": {"name": "World Government Marines", "kind": "naval_command", "leader": "commander_rusk",
                         "ships": ["government_1"], "ambition": "Contain Blackwake",
                         "target": "blackwake_fleet", "reputation": 70},
}

# Signed affinity: -100 is a blood feud, +100 is a dependable alliance.
INITIAL_RELATIONSHIPS = {
    "blackwake_fleet|salt_jackals": -38,
    "blackwake_fleet|world_government": -82,
    "blackwake_fleet|cinder_free_company": -8,
    "cinder_free_company|salt_jackals": 12,
    "cinder_free_company|world_government": 24,
    "salt_jackals|world_government": -12,
}


def relation_key(first, second):
    return "|".join(sorted((first, second)))


def relation_status(score):
    if score >= 65:
        return "allied"
    if score <= -70:
        return "hostile"
    if score <= -35:
        return "rivals"
    if score >= 25:
        return "friendly"
    return "wary"


class NpcSocietySystem:
    """Strategic social layer over the persistent ships in NpcFleetSystem."""

    def __init__(self, npc_fleets=None, hour=0):
        self.hour = int(hour)
        self.mode = "authored"
        self.generation_manifest = None
        self.people = {person_id: {**deepcopy(row), "person_id": person_id,
                                   "activity": "At sea"}
                       for person_id, row in PEOPLE.items()}
        self.organizations = deepcopy(ORGANIZATIONS)
        self.relationships = dict(INITIAL_RELATIONSHIPS)
        self.news = []
        self.news_sequence = 0
        self.pair_timers = {}
        self.knowledge = {}
        self.decision_state = {}
        self.memories = {}
        self.last_positions = {ship["id"]: ship["node"] for ship in (npc_fleets.ships if npc_fleets else [])}
        self.last_legacy_events = list(npc_fleets.events[-80:]) if npc_fleets else []

    @classmethod
    def from_generated(cls, generated, npc_fleets=None, hour=0):
        system = cls(npc_fleets, hour)
        if not isinstance(generated, dict) or not isinstance(generated.get("manifest"), dict):
            raise ValueError("Invalid generated NPC society")
        system.mode = "generated"
        system.generation_manifest = deepcopy(generated["manifest"])
        system.people = deepcopy(generated["people"])
        system.organizations = deepcopy(generated["organizations"])
        for person in system.people.values():
            person["organization"] = person["organization_id"]
            person["ship"] = person.get("vessel_id")
            person["title"] = person.get("rank", "Crew")
            person["activity"] = "At sea"
        for organization in system.organizations.values():
            organization["leader"] = organization["leader_id"]
            organization["ships"] = list(organization["vessel_ids"])
            organization["target"] = organization["active_goal"].get("target_id")
        system.social_relationships = deepcopy(generated["relationships"])
        system.agreements = deepcopy(generated["agreements"])
        system.memories = {entity_id: [] for entity_id in (*system.people, *system.organizations)}
        system.knowledge = {}
        for org_id, organization in system.organizations.items():
            system.knowledge[org_id] = {
                ship["id"]: {"node": ship["node"], "observed_hour": int(hour),
                             "source": "own fleet",
                             "estimated_strength": system._strength_for_ship(ship)["score"]}
                for ship in (npc_fleets.ships if npc_fleets else [])
                if ship["organization_id"] == org_id
            }
        system.decision_state = {}
        system.relationships = {}
        by_pair = {(row["source_id"], row["target_id"]): row
                   for row in system.social_relationships if row["entity_type"] == "organization"}
        for first in system.organizations:
            for second in system.organizations:
                if first >= second:
                    continue
                left = by_pair[(first, second)]["dimensions"]
                right = by_pair[(second, first)]["dimensions"]
                score = round(((left["trust"] - left["grievance"])
                               + (right["trust"] - right["grievance"])) / 2)
                system.relationships[relation_key(first, second)] = max(-100, min(100, score))
        system.last_positions = {ship["id"]: ship["node"] for ship in (npc_fleets.ships if npc_fleets else [])}
        system.last_legacy_events = list(npc_fleets.events[-80:]) if npc_fleets else []
        return system

    def relationship(self, first, second):
        if first == second:
            return 100
        return self.relationships.get(relation_key(first, second), 0)

    def _set_relationship(self, first, second, score):
        score = max(-100, min(100, int(score)))
        self.relationships[relation_key(first, second)] = score
        if self.mode == "generated":
            for row in self.social_relationships:
                if row["entity_type"] == "organization" and (
                        (row["source_id"], row["target_id"]) in ((first, second), (second, first))):
                    row["dimensions"]["trust"] = max(0, min(100, 50 + score // 2))
                    row["dimensions"]["grievance"] = max(0, min(100, 50 - score // 2))

    def _append_news(self, hour, kind, participants, location, cause, outcome, headline, body, details=None):
        if details is not None and not isinstance(details, dict):
            raise ValueError("NPC news details must be a mapping")
        self.news_sequence += 1
        row = {"id": f"news-{self.news_sequence:06d}", "hour": int(hour), "kind": kind,
               "participants": list(dict.fromkeys(participants)), "location": location,
               "cause": cause, "outcome": outcome, "headline": headline, "body": body,
               "details": deepcopy(details) if isinstance(details, dict) else {}}
        self.news.append(row)
        if len(self.news) > MAX_NEWS:
            priority = [item for item in self.news if item["kind"] in PRIORITY_NEWS_KINDS]
            routine = [item for item in self.news if item["kind"] not in PRIORITY_NEWS_KINDS]
            priority = priority[-MAX_NEWS:]
            routine_limit = MAX_NEWS - len(priority)
            routine = routine[-routine_limit:] if routine_limit else []
            self.news = sorted(priority + routine, key=lambda item: item["id"])
        if self.mode == "generated":
            for entity_id in row["participants"]:
                if entity_id not in self.memories:
                    continue
                memory = {"event_id": row["id"], "hour": int(hour), "kind": kind,
                          "summary": headline, "valence": "adverse" if kind in
                          ("npc_clash", "rivalry", "petty_feud", "agreement_breach") else "notable",
                          "cause": cause, "outcome": outcome,
                          "participants": list(row["participants"])}
                self.memories[entity_id].append(memory)
                self.memories[entity_id] = self.memories[entity_id][-MAX_MEMORIES:]
        if self.mode == "generated":
            self._advance_goals_from_event(row)
        return row

    def _advance_goals_from_event(self, event):
        """Advance only goals whose promised outcome actually occurred."""
        participant_orgs = {entity_id for entity_id in event["participants"]
                            if entity_id in self.organizations}
        if not participant_orgs:
            return
        for owner_id in event["participants"]:
            owner = self.people.get(owner_id) or self.organizations.get(owner_id)
            if not owner:
                continue
            goal = owner.get("active_goal")
            if not isinstance(goal, dict) or goal.get("status") != "active":
                continue
            goal_type, target_id = goal.get("type"), goal.get("target_id")
            own_org = owner.get("organization_id", owner_id)
            matched = (
                goal_type == "trade" and event["kind"] == "npc_market_event"
                and event["location"] == target_id
                or goal_type == "hunt" and event["kind"] in ("npc_clash", "raid")
                and target_id in participant_orgs and target_id != own_org
                or goal_type == "negotiate" and event["kind"] == "alliance"
                and target_id in participant_orgs and target_id != own_org
                or goal_type == "patrol" and event["kind"] in ("npc_clash", "raid")
                and target_id in participant_orgs and target_id != own_org
                and self.organizations.get(own_org, {}).get("kind") == "naval_command"
            )
            if not matched:
                continue
            goal["progress"] = min(100, goal.get("progress", 0) + 50)
            goal["last_progress_hour"] = event["hour"]
            if goal["progress"] >= 100:
                goal["status"] = "completed"
                goal["completed_hour"] = event["hour"]

    def recent_headlines(self, count=8, public_only=False):
        return [item["headline"] for item in reversed(self.chronicle_rows(count, public_only=public_only))]

    def chronicle_rows(self, count=20, participant_id=None, public_only=False):
        """Choose a bounded public chronicle that keeps feud origins with outcomes."""
        limit = max(0, int(count))
        if not limit:
            return []
        rows = [row for row in self.news
                if (participant_id is None or participant_id in row.get("participants", []))
                and not (public_only and self.mode == "generated"
                         and (row["kind"] in ("npc_clash", "raid")
                              or row["kind"] in ("rivalry", "diplomacy", "alliance")
                              and "clash between" in row.get("cause", "").lower())
                         and row.get("details", {}).get("public_report") is not True)]
        selected = {}

        def include(row):
            selected[row["id"]] = row

        anchors = [row for row in rows if row["kind"] in ("petty_feud", "rivalry", "agreement_breach")]
        anchors = anchors[-limit:]
        for anchor in anchors:
            include(anchor)
            org_pair = {value for value in anchor.get("participants", [])
                        if value in self.organizations}
            if len(org_pair) < 2:
                continue
            followups = [row for row in rows if row["hour"] > anchor["hour"]
                         and row["kind"] in ("npc_clash", "rivalry", "agreement_breach")
                         and org_pair.issubset(set(row.get("participants", [])))]
            if followups:
                include(followups[0])
                include(followups[-1])

        priority = [row for row in rows if row["kind"] in PRIORITY_NEWS_KINDS]
        for row in reversed(priority):
            if len(selected) >= limit:
                break
            include(row)
        for row in reversed(rows):
            if len(selected) >= limit:
                break
            include(row)
        return sorted(selected.values(), key=lambda row: row["id"])[-limit:]

    @staticmethod
    def _new_events(previous, current):
        """Return newly appended entries, handling the legacy capped string log."""
        limit = min(len(previous), len(current))
        overlap = 0
        for size in range(limit, -1, -1):
            if size == 0 or previous[-size:] == current[:size]:
                overlap = size
                break
        return current[overlap:]

    def _news_from_legacy(self, world, previous_events, hour):
        current = world.npcs.events[-80:]
        for text in self._new_events(previous_events, current):
            ship = next((row for row in world.npcs.ships if row["name"] in text), None)
            if not ship:
                continue
            person = next((row for row in self.people.values() if row["ship"] == ship["id"]), None)
            organization = self.organizations.get(person["organization"]) if person else None
            participants = [ship["id"]]
            if person:
                person_id = next(pid for pid, row in self.people.items() if row is person)
                participants += [person_id, person["organization"]]
            kind = "npc_market_event"
            headline = f"{ship['name']} stirs the sea lanes"
            if "sold" in text or "bought" in text or "purchased" in text:
                headline = f"{ship['name']} trades at {SEA_NODES[ship['node']]['name']}"
                if "captive" in text or "enslaved" in text:
                    headline = f"Reports of captive trading at {SEA_NODES[ship['node']]['name']}"
            elif "raided" in text or "robbed" in text:
                kind = "raid"
                headline = f"Raid reported near {SEA_NODES[ship['node']]['name']}"
            self._append_news(hour, kind, participants, ship["node"],
                              "observable trade or raid report",
                              text, headline, text)

    def _ship_org(self, ship_id):
        return next((org_id for org_id, org in self.organizations.items() if ship_id in org["ships"]), None)

    def _ship_person(self, ship_id):
        return next((person_id for person_id, person in self.people.items() if person["ship"] == ship_id), None)

    def _ship(self, world, ship_id):
        return next((ship for ship in world.npcs.ships if ship["id"] == ship_id), None)

    def _strength_for_ship(self, ship):
        person_id = ship.get("captain_id") or self._ship_person(ship["id"])
        person = self.people.get(person_id)
        organization = self.organizations.get(self._ship_org(ship["id"]))
        return vessel_strength_breakdown(ship, person, organization)

    def _may_engage(self, first, second, score):
        """Resolve hostility plus the scenario-specific meaning of a hunt order."""
        if self.mode == "generated" and min(first.get("condition", 100),
                                              second.get("condition", 100)) <= 20:
            # A badly damaged vessel is not a willing combatant. It should be
            # allowed to regroup rather than enter a repetitive losing loop.
            return False
        if score <= -60:
            return True
        hunting = first.get("mission") == "hunt" or second.get("mission") == "hunt"
        first_org = self._ship_org(first["id"])
        second_org = self._ship_org(second["id"])
        explicitly_targeted = (
            self.decision_state.get(first["id"], {}).get("target_org_id") == second_org
            or self.decision_state.get(second["id"], {}).get("target_org_id") == first_org)
        if not hunting and not explicitly_targeted:
            return False
        if self.mode == "generated" and explicitly_targeted:
            return True
        if score <= -25:
            return True
        if self.mode == "generated":
            first_org = self.organizations.get(self._ship_org(first["id"]), {})
            second_org = self.organizations.get(self._ship_org(second["id"]), {})
            kinds = {first_org.get("kind"), second_org.get("kind")}
            return "naval_command" in kinds and bool(kinds & {"pirate_fleet", "independent_crew"})
        return ((first.get("mission") == "hunt" and second.get("threat") != "pirates")
                or (second.get("mission") == "hunt" and first.get("threat") != "pirates"))

    def _nearest_retreat_lane(self, node):
        neighbors = [b if a == node else a for a, b in LANES if a == node or b == node]
        ports = [key for key, row in SEA_NODES.items() if row.get("port")]
        if not neighbors:
            return node
        target = min(ports, key=lambda port: self._distance(node, port))
        return min(neighbors, key=lambda neighbor: (self._distance(neighbor, target), neighbor))

    @staticmethod
    def _distance(first, second):
        ax, ay = SEA_NODES[first]["xy"]
        bx, by = SEA_NODES[second]["xy"]
        return math.hypot(ax - bx, ay - by)

    @staticmethod
    def _apply_condition_damage(ship, requested, floor=1):
        """Apply bounded hull wear without healing already-damaged vessels."""
        current = max(0, min(100, int(ship.get("condition", 100))))
        actual = min(max(0, int(requested)), max(0, current - floor))
        ship["condition"] = current - actual
        return actual

    def _award_clash_experience(self, person_id, outcome):
        """Advance a named generated captain from a resolved strategic encounter."""
        person = self.people.get(person_id)
        if self.mode != "generated" or not isinstance(person, dict):
            return None
        xp = person.get("skill_xp")
        skills = person.get("skills")
        if not isinstance(xp, dict) or not isinstance(skills, dict):
            return None
        fame_drive = person.get("values", {}).get("fame", 50) // 25
        if outcome == "victory":
            gains = {"tactics": 14 + fame_drive, "seamanship": 8 + fame_drive // 2}
        elif outcome == "disengagement":
            gains = {"tactics": 6, "seamanship": 4}
        else:
            gains = {"tactics": 5, "seamanship": 2}
        before = {"career_level": person["career_level"],
                  "command_power": person["command_power"],
                  "personal_capability": person["personal_capability"],
                  "skills": {skill: skills[skill] for skill in gains}}
        actual_gains = {}
        for skill, amount in gains.items():
            actual = min(amount, max(0, 10000 - xp[skill]))
            xp[skill] += actual
            actual_gains[skill] = actual
            skills[skill] = min(100, int(math.sqrt(xp[skill])))
        person["career_level"] = max(1, min(20, 1 + sum(skills.values()) // 35))
        attrs = person["attributes"]
        person["personal_capability"] = max(0, min(100, round(
            attrs["strength"] * 0.12 + attrs["endurance"] * 0.12
            + attrs["perception"] * 0.14 + attrs["intellect"] * 0.16
            + attrs["presence"] * 0.10 + skills["leadership"] * 0.12
            + skills["tactics"] * 0.12 + skills["navigation"] * 0.12)))
        person["command_power"] = derive_command_power(person)
        return {"xp_gained": actual_gains,
                "skills_before": before["skills"],
                "skills_after": {skill: skills[skill] for skill in gains},
                "career_level_before": before["career_level"],
                "career_level_after": person["career_level"],
                "command_power_before": before["command_power"],
                "command_power_after": person["command_power"],
                "personal_capability_before": before["personal_capability"],
                "personal_capability_after": person["personal_capability"]}

    def _adjust_cohesion(self, organization_id, change):
        organization = self.organizations.get(organization_id)
        if not organization or type(organization.get("cohesion")) is not int:
            return 0
        before = organization["cohesion"]
        organization["cohesion"] = max(0, min(100, before + change))
        return organization["cohesion"] - before

    def _record_relation_change(self, hour, first, second, before, after, node, reason,
                                public_report=None):
        old_status, new_status = relation_status(before), relation_status(after)
        if old_status == new_status:
            return
        org_a, org_b = self.organizations[first], self.organizations[second]
        if new_status == "allied":
            headline = f"{org_a['name']} and {org_b['name']} seal an alliance"
            kind = "alliance"
        elif new_status in ("rivals", "hostile"):
            headline = f"Tensions rise between {org_a['name']} and {org_b['name']}"
            kind = "rivalry"
        else:
            headline = f"Relations shift between {org_a['name']} and {org_b['name']}"
            kind = "diplomacy"
        self._append_news(hour, kind, [first, second, org_a["leader"], org_b["leader"]], node,
                          reason, f"relationship shifted from {old_status} to {new_status}", headline,
                          f"After {reason}, {org_a['name']} and {org_b['name']} are now {new_status}.",
                          {"public_report": public_report} if public_report is not None else None)

    def _resolve_clash(self, world, first_ship, second_ship, hour):
        first_org = self._ship_org(first_ship["id"])
        second_org = self._ship_org(second_ship["id"])
        if not first_org or not second_org or first_org == second_org:
            return
        score = self.relationship(first_org, second_org)
        if not self._may_engage(first_ship, second_ship, score):
            return
        pair = "|".join(sorted((first_ship["id"], second_ship["id"])))
        organization_pair = f"conflict:{relation_key(first_org, second_org)}"
        if (self.pair_timers.get(pair, -1) > hour
                or self.pair_timers.get(organization_pair, -1) > hour):
            return
        first_person_id, second_person_id = self._ship_person(first_ship["id"]), self._ship_person(second_ship["id"])
        first_person = self.people.get(first_person_id)
        second_person = self.people.get(second_person_id)
        first_strength = vessel_strength_breakdown(
            first_ship, first_person, self.organizations.get(first_org))
        second_strength = vessel_strength_breakdown(
            second_ship, second_person, self.organizations.get(second_org))
        first_power, second_power = first_strength["score"], second_strength["score"]
        node = first_ship["node"]
        sightings = getattr(world.npcs, "sighting_reports", {})
        public_report = (self.mode != "generated"
                         or any(ship_id in sightings
                                and sightings[ship_id].get("node") == node
                                and 0 <= hour - sightings[ship_id].get("observed_hour", -1000) <= 1
                                for ship_id in (first_ship["id"], second_ship["id"])))
        draw = False
        if self.mode == "generated":
            rng = random_stream(self.generation_manifest, "clash_outcome",
                                f"{hour}:{pair}")
            difference = first_power - second_power
            # A narrow strength gap can end in a costly disengagement; otherwise
            # capability sets the odds, not certainty. Event-keyed randomness
            # keeps this independent of call order and UI activity.
            draw = abs(difference) <= 10 and rng.random() < 0.28
            if not draw:
                first_win_chance = max(0.18, min(0.82, 0.5 + difference * 0.006))
                winner, loser = ((first_ship, second_ship) if rng.random() < first_win_chance
                                 else (second_ship, first_ship))
        else:
            if first_power == second_power:
                winner, loser = ((first_ship, second_ship) if (hour + sum(map(ord, pair))) % 2 == 0
                                 else (second_ship, first_ship))
            else:
                winner, loser = ((first_ship, second_ship) if first_power > second_power
                                 else (second_ship, first_ship))

        if draw:
            first_condition_loss = self._apply_condition_damage(first_ship, 12)
            second_condition_loss = self._apply_condition_damage(second_ship, 12)
            first_ship["destination"] = self._nearest_retreat_lane(node)
            second_ship["destination"] = self._nearest_retreat_lane(node)
            cohesion_change = {
                first_org: self._adjust_cohesion(first_org, -2),
                second_org: self._adjust_cohesion(second_org, -2),
            }
            self._set_relationship(first_org, second_org, max(-100, score - 8))
            first_name, second_name = first_ship["name"], second_ship["name"]
            outcome = (f"{first_name} and {second_name} broke contact after both vessels took damage; "
                       f"estimated strategic strength was {first_power} to {second_power}.")
            headline = f"{first_name} and {second_name} trade damage near {SEA_NODES[node]['name']}"
            body = (f"Neither captain could secure an advantage near {SEA_NODES[node]['name']}. "
                    f"Both vessels withdrew after a costly encounter (estimated strength "
                    f"{first_power} to {second_power}).")
            event_details = {
                "outcome_type": "disengagement",
                "public_report": public_report,
                "strength": {first_ship["id"]: first_power, second_ship["id"]: second_power},
                "condition_loss": {first_ship["id"]: first_condition_loss,
                                   second_ship["id"]: second_condition_loss},
                "cohesion_change": cohesion_change,
                "transfers": {"silver": 0, "cargo": {}},
            }
            captain_progression = {}
            for person_id in (first_person_id, second_person_id):
                result = self._award_clash_experience(person_id, "disengagement")
                if result:
                    captain_progression[person_id] = result
            event_details["captain_progression"] = captain_progression
            participants = [first_ship["id"], second_ship["id"], first_org, second_org,
                            *(p for p in (first_person_id, second_person_id) if p)]
            for vessel, person_id in ((first_ship, first_person_id), (second_ship, second_person_id)):
                if person_id:
                    self.people[person_id]["activity"] = "Withdrawing to regroup after a costly clash"
                if self.mode == "generated":
                    self.decision_state[vessel["id"]] = {
                        "kind": "regroup", "target_node": vessel["destination"],
                        "score": 0.0, "decided_hour": hour,
                        "goal_id": self.people.get(person_id, {}).get("active_goal", {}).get("goal_id"),
                        "explanation": "regroup after a costly recorded clash",
                    }
        else:
            winner_org, loser_org = self._ship_org(winner["id"]), self._ship_org(loser["id"])
            winner_person_id, loser_person_id = self._ship_person(winner["id"]), self._ship_person(loser["id"])
            rng = (random_stream(self.generation_manifest, "clash_damage", f"{hour}:{pair}")
                   if self.mode == "generated" else None)
            winner_damage = rng.randint(4, 12) if rng else 8
            loser_damage = rng.randint(18, 28) if rng else 24
            winner_condition_loss = self._apply_condition_damage(winner, winner_damage)
            loser_condition_loss = self._apply_condition_damage(loser, loser_damage)
            loser["destination"] = self._nearest_retreat_lane(node)
            cohesion_change = {
                winner_org: self._adjust_cohesion(winner_org, 3),
                loser_org: self._adjust_cohesion(loser_org, -5),
            }
            silver_loot = 0
            cargo_loot = {}
            if self.mode == "generated":
                silver_loot = min(loser.get("silver", 0), rng.randint(10, 10 + winner.get("crew", 0) * 5))
                loser["silver"] -= silver_loot
                winner["silver"] = winner.get("silver", 0) + silver_loot
                remaining_capacity = min(4, 1 + winner.get("crew", 0) // 3)
                for item_id in sorted(loser.get("cargo", {})):
                    available = loser["cargo"].get(item_id, 0)
                    if available <= 0 or remaining_capacity <= 0:
                        continue
                    amount = min(available, remaining_capacity, rng.randint(1, min(2, available)))
                    loser["cargo"][item_id] -= amount
                    winner.setdefault("cargo", {})[item_id] = winner.get("cargo", {}).get(item_id, 0) + amount
                    cargo_loot[item_id] = amount
                    remaining_capacity -= amount
            self.organizations[winner_org]["reputation"] = min(100, self.organizations[winner_org]["reputation"] + 4)
            self.organizations[loser_org]["reputation"] = max(0, self.organizations[loser_org]["reputation"] - 3)
            if winner_person_id:
                self.people[winner_person_id]["reputation"] = min(100, self.people[winner_person_id]["reputation"] + 3)
                self.people[winner_person_id]["activity"] = f"Won an offscreen clash at {SEA_NODES[node]['name']}"
            if loser_person_id:
                self.people[loser_person_id]["reputation"] = max(0, self.people[loser_person_id]["reputation"] - 2)
                self.people[loser_person_id]["activity"] = "Retreating to regroup after a clash"
            if self.mode == "generated":
                self.decision_state[loser["id"]] = {
                    "kind": "regroup", "target_node": loser["destination"],
                    "score": 0.0, "decided_hour": hour,
                    "goal_id": self.people.get(loser_person_id, {}).get("active_goal", {}).get("goal_id"),
                    "explanation": "regroup after a recorded strategic clash",
                }
            winner_name = self.people[winner_person_id]["name"] if winner_person_id else winner["name"]
            loser_name = self.people[loser_person_id]["name"] if loser_person_id else loser["name"]
            outcome = (f"{loser['name']} was forced to retreat; its vessel suffered {loser_condition_loss} condition "
                       f"damage after an estimated strength comparison of {first_power} to {second_power}.")
            if silver_loot or cargo_loot:
                cargo_text = ", ".join(f"{amount} {item_id}" for item_id, amount in sorted(cargo_loot.items()))
                loot_text = f" Seized {silver_loot} silver" + (f" and {cargo_text}" if cargo_text else "") + "."
                outcome += loot_text
            else:
                loot_text = ""
            headline = f"{winner['name']} defeats {loser['name']} near {SEA_NODES[node]['name']}"
            body = (f"{winner_name} prevailed over {loser_name} near {SEA_NODES[node]['name']}. "
                    f"{loser['name']} withdrew with its crew aboard (estimated strategic strength "
                    f"{first_power} to {second_power}).{loot_text}")
            event_details = {
                "outcome_type": "victory",
                "public_report": public_report,
                "winner_ship_id": winner["id"],
                "loser_ship_id": loser["id"],
                "strength": {first_ship["id"]: first_power, second_ship["id"]: second_power},
                "condition_loss": {winner["id"]: winner_condition_loss,
                                   loser["id"]: loser_condition_loss},
                "cohesion_change": cohesion_change,
                "transfers": {"silver": silver_loot, "cargo": cargo_loot},
            }
            captain_progression = {}
            for person_id, captain_outcome in ((winner_person_id, "victory"),
                                               (loser_person_id, "defeat")):
                result = self._award_clash_experience(person_id, captain_outcome)
                if result:
                    captain_progression[person_id] = result
            event_details["captain_progression"] = captain_progression
            participants = [winner["id"], loser["id"], winner_org, loser_org,
                            *(p for p in (winner_person_id, loser_person_id) if p)]
            self._set_relationship(first_org, second_org, max(-100, score - 12))

        after = self.relationship(first_org, second_org)
        self._record_relation_change(hour, first_org, second_org, score, after, node,
                                     f"the clash between {first_ship['name']} and {second_ship['name']}",
                                     public_report=public_report)
        self.pair_timers[pair] = hour + 24
        self.pair_timers[organization_pair] = hour + ORGANIZATION_CLASH_COOLDOWN_HOURS
        self._append_news(hour, "npc_clash", participants, node,
                          "hostile ships met; "
                          f"estimated strategic strength was {first_power} to {second_power}",
                          outcome, headline, body, event_details)
        if self.mode == "generated":
            for agreement in self.agreements:
                if (agreement["status"] != "active"
                        or set(agreement["participants"]) != {first_org, second_org}):
                    continue
                agreement["status"] = "breached"
                agreement["breach_hour"] = hour
                agreement["breach_cause"] = f"strategic clash between {first_ship['name']} and {second_ship['name']}"
                breach = self._append_news(
                    hour, "agreement_breach", [first_org, second_org, first_ship["id"], second_ship["id"],
                                                *agreement["signatories"]], node,
                    agreement["purpose"], "signatories engaged in a hostile clash",
                    f"{self.organizations[first_org]['name']} and {self.organizations[second_org]['name']} "
                    "break their mutual-aid agreement",
                    f"The clash between {first_ship['name']} and {second_ship['name']} violated the signed "
                    "mutual-aid terms, ending the agreement.")
                agreement["breach_event_id"] = breach["id"]

    def _maybe_create_petty_feud(self, first_org_id, second_org_id, first_ship, second_ship, hour):
        """A rare, character-grounded slight can turn a quiet meeting sour."""
        first_org, second_org = self.organizations[first_org_id], self.organizations[second_org_id]
        pirate_kinds = {"pirate_fleet", "independent_crew"}
        if (self.mode != "generated" or first_org.get("kind") not in pirate_kinds
                or second_org.get("kind") not in pirate_kinds):
            return False
        if any(agreement["status"] == "active"
               and set(agreement["participants"]) == {first_org_id, second_org_id}
               for agreement in self.agreements):
            return False
        first_leader = self.people.get(first_org.get("leader_id"), {})
        second_leader = self.people.get(second_org.get("leader_id"), {})
        first_personality = first_leader.get("personality", {})
        second_personality = second_leader.get("personality", {})
        pride = max(first_personality.get("pride", 50), second_personality.get("pride", 50))
        vindictive = int("vindictive" in first_leader.get("traits", [])) \
            + int("vindictive" in second_leader.get("traits", []))
        empathy = (first_personality.get("empathy", 50) + second_personality.get("empathy", 50)) / 2
        chance = max(0.01, min(0.08, 0.018 + pride * 0.00035 + vindictive * 0.012 - empathy * 0.0001))
        pair = relation_key(first_org_id, second_org_id)
        rng = random_stream(self.generation_manifest, "petty_slight", f"{hour}:{pair}")
        if rng.random() >= chance:
            return False

        leaders = [(first_org_id, first_leader), (second_org_id, second_leader)]
        if first_personality.get("pride", 50) == second_personality.get("pride", 50):
            initiator_org_id, initiator = leaders[rng.randrange(2)]
        else:
            initiator_org_id, initiator = max(
                leaders, key=lambda row: row[1].get("personality", {}).get("pride", 50))
        target_org_id = second_org_id if initiator_org_id == first_org_id else first_org_id
        target = self.people.get(self.organizations[target_org_id].get("leader_id"), {})
        incident_id, template = PETTY_SLIGHTS[rng.randrange(len(PETTY_SLIGHTS))]
        incident = template.format(initiator=initiator.get("name", "A captain"),
                                   target=target.get("name", "a rival captain"))
        before = self.relationship(first_org_id, second_org_id)
        # Pride and vindictiveness decide whether a petty slight crosses into
        # rivalry; an empathetic target can soften its impact. Bound the event
        # so one joke never creates an instant, irreversible blood feud.
        penalty = max(4, 20 + round(pride * 0.75) + vindictive * 20 - round(empathy * 0.20))
        rivalry_floor = -35 if pride >= 55 or vindictive else -15
        after = min(rivalry_floor, max(-55, before - penalty))
        self._set_relationship(first_org_id, second_org_id, after)
        initiator["activity"] = f"Took a petty slight personally: {incident}"
        self.pair_timers[f"meeting:{pair}"] = hour + 24
        participants = [first_org_id, second_org_id, initiator.get("person_id"),
                        target.get("person_id"), first_ship["id"], second_ship["id"]]
        self._append_news(
            hour, "petty_feud", participants, first_ship["node"],
            f"{initiator.get('name')} took a petty slight personally: {incident}",
            f"Organization relations shifted from {relation_status(before)} to {relation_status(after)} "
            f"after a pride-driven grievance (affinity {before} to {after}).",
            f"A ridiculous slight sours relations between {first_org['name']} and {second_org['name']}",
            f"During a meeting near {SEA_NODES[first_ship['node']]['name']}, {initiator.get('name')} "
            f"{incident}. The other captain refused to laugh it off. Their relationship fell to "
            f"{relation_status(after)}; a silly insult may now have serious consequences.",
            {"incident_id": incident_id, "initiator_id": initiator.get("person_id"),
             "target_id": target.get("person_id"), "relationship_before": before,
             "relationship_after": after, "pride": pride, "target_empathy": target.get("personality", {}).get("empathy", 50),
             "vindictive_captains": vindictive})
        return True

    def _resolve_meetings(self, world, hour):
        ships = sorted(world.npcs.ships, key=lambda row: row["id"])
        for index, first in enumerate(ships):
            for second in ships[index + 1:]:
                if first["node"] != second["node"]:
                    continue
                first_org, second_org = self._ship_org(first["id"]), self._ship_org(second["id"])
                if not first_org or not second_org or first_org == second_org:
                    continue
                score = self.relationship(first_org, second_org)
                should_clash = self._may_engage(first, second, score)
                if should_clash or self.pair_timers.get("|".join(sorted((first["id"], second["id"]))), -1) > hour:
                    self._resolve_clash(world, first, second, hour)
                    continue
                if score <= -35:
                    continue
                relation_pair = relation_key(first_org, second_org)
                cooldown = f"meeting:{relation_pair}"
                if self.pair_timers.get(cooldown, -1) > hour:
                    continue
                if self._maybe_create_petty_feud(first_org, second_org, first, second, hour):
                    continue
                # Repeated peaceful meetings improve trust; shared enemies make
                # cooperation especially likely. Alliances emerge from history.
                before = score
                shared_enemy = any(other not in (first_org, second_org)
                                   and self.relationship(first_org, other) <= -35
                                   and self.relationship(second_org, other) <= -35
                                   for other in self.organizations)
                self._set_relationship(first_org, second_org, score + (18 if shared_enemy else 8))
                after = self.relationship(first_org, second_org)
                self.pair_timers[cooldown] = hour + 24
                self._record_relation_change(hour, first_org, second_org, before, after, first["node"],
                                             "cooperation against a shared enemy" if shared_enemy else "repeated peaceful contact")

    def _update_generated_knowledge(self, world, hour):
        """Record only owned positions and ships directly observed at the same node."""
        for org_id in self.organizations:
            self.knowledge.setdefault(org_id, {})
            for ship in world.npcs.ships:
                if self._ship_org(ship["id"]) == org_id:
                    self.knowledge[org_id][ship["id"]] = {
                        "node": ship["node"], "observed_hour": hour, "source": "own fleet",
                        "estimated_strength": self._strength_for_ship(ship)["score"]}
        for first in world.npcs.ships:
            first_org = self._ship_org(first["id"])
            for second in world.npcs.ships:
                second_org = self._ship_org(second["id"])
                if first_org != second_org and first["node"] == second["node"]:
                    self.knowledge.setdefault(first_org, {})[second["id"]] = {
                        "node": second["node"], "observed_hour": hour, "source": first["id"],
                        "estimated_strength": self._strength_for_ship(second)["score"]}
        for agreement in self.agreements:
            if agreement["status"] != "active" or agreement["kind"] != "mutual_aid":
                continue
            first_org, second_org = agreement["participants"]
            for source_org, target_org in ((first_org, second_org), (second_org, first_org)):
                shared = set(agreement["shared_reports"])
                for ship_id, report in self.knowledge[source_org].items():
                    if report["source"] != "own fleet" or ship_id in shared:
                        continue
                    self.knowledge[target_org][ship_id] = {
                        "node": report["node"], "observed_hour": report["observed_hour"],
                        "source": f"agreement:{agreement['agreement_id']}",
                        "estimated_strength": report.get("estimated_strength", 50)}
                    agreement["shared_reports"].append(ship_id)
                    shared.add(ship_id)
                    event = self._append_news(
                        hour, "agreement_fulfillment", [first_org, second_org, ship_id], report["node"],
                        agreement["purpose"], "route information shared under the signed terms",
                        f"{self.organizations[source_org]['name']} shares a route report with "
                        f"{self.organizations[target_org]['name']}",
                        f"{self.organizations[source_org]['name']} fulfilled its mutual-aid agreement "
                        f"by sharing a report about {ship_id}.")
                    agreement["fulfillments"].append(event["id"])
        for contacts in self.knowledge.values():
            for ship_id, report in list(contacts.items()):
                if hour - report["observed_hour"] > 168:
                    contacts.pop(ship_id)

    def _expire_generated_agreements(self, world, hour):
        for agreement in self.agreements:
            if agreement["status"] != "active" or hour < agreement["expires_hour"]:
                continue
            agreement["status"] = "expired"
            agreement["expired_hour"] = hour
            first_org, second_org = agreement["participants"]
            ship = self._ship(world, self.organizations[first_org]["ships"][0])
            event = self._append_news(
                hour, "agreement_expiry", [first_org, second_org, *agreement["signatories"]],
                ship["node"] if ship else next(iter(SEA_NODES)),
                agreement["purpose"], "mutual-aid terms expired", "A mutual-aid agreement expires",
                f"The mutual-aid agreement between {self.organizations[first_org]['name']} and "
                f"{self.organizations[second_org]['name']} expired without renewal.")
            agreement["expiry_event_id"] = event["id"]

    @staticmethod
    def _route_length(world, start, destination):
        route, _ = VoyageSystem.shortest_route(world.npcs, start, destination)
        return max(0, len(route) - 1) if route else 100

    def _nearest_port(self, world, node):
        ports = [key for key, record in SEA_NODES.items() if record.get("port")]
        return min(ports, key=lambda target: (self._route_length(world, node, target), target))

    def _generated_candidates(self, world, ship, captain, organization, hour):
        goal = captain.get("active_goal", {})
        if goal.get("status") == "completed":
            goal = organization.get("active_goal", {})
        target_id = goal.get("target_id")
        org_id = organization["organization_id"]
        contacts = self.knowledge.get(org_id, {})
        contacts_by_id = {row["id"]: row for row in world.npcs.ships}
        known_contacts = []
        for ship_id, report in contacts.items():
            target_ship = contacts_by_id.get(ship_id)
            target_org_id = self._ship_org(ship_id)
            target_org = self.organizations.get(target_org_id)
            if (hour - report["observed_hour"] > 48 or target_ship is None
                    or target_org_id == org_id or target_org is None):
                continue
            known_contacts.append({"ship": target_ship, "report": report,
                                   "organization": target_org, "organization_id": target_org_id,
                                   "relationship": self.relationship(org_id, target_org_id)})
        candidates = []

        def add(kind, destination, base, risk, cost, relationship_bonus=0,
                target_ship_id=None, target_org_id=None):
            if destination not in SEA_NODES:
                return
            matches = goal.get("type") == kind
            action = {"kind": kind, "base": base, "risk": min(100, max(0, risk)),
                      "cost": min(100, max(0, cost)),
                      "goal_progress": goal.get("priority", 50) * 0.5 if matches else 0,
                      "goal_match": goal.get("type") if matches else None}
            candidates.append({"action": action, "goal": goal,
                               "relationship_bonus": relationship_bonus,
                               "target_node": destination,
                               "target_ship_id": target_ship_id,
                               "target_org_id": target_org_id})

        if organization["kind"] != "naval_command":
            trade_target = target_id if goal.get("type") == "trade" and target_id in SEA_NODES else self._nearest_port(world, ship["node"])
            route_risk = max(0, self._route_length(world, ship["node"], trade_target) * 5
                             + (80 - ship.get("condition", 80)) // 3)
            add("trade", trade_target, 30, route_risk, 12 if ship.get("cargo") else 20)
        else:
            hostile_contacts = [contact for contact in known_contacts
                                if contact["organization"]["kind"] in ("pirate_fleet", "independent_crew")]
            hostile_contacts.sort(key=lambda row: (row["relationship"],
                                                   -row["report"]["observed_hour"],
                                                   row["ship"]["id"]))
            target_contact = hostile_contacts[0] if hostile_contacts else None
            patrol_target = target_contact["report"]["node"] if target_contact else None
            if patrol_target is None:
                neighbors = sorted(b if a == ship["node"] else a for a, b in LANES
                                   if a == ship["node"] or b == ship["node"])
                rng = random_stream(self.generation_manifest, "patrol_route",
                                    f"{ship['id']}:{hour // 6}")
                patrol_target = neighbors[rng.randrange(len(neighbors))] if neighbors else ship["node"]
            add("patrol", patrol_target, 28, 18, 8,
                (20 if target_contact else 0))

        if organization["kind"] != "naval_command":
            own_strength = self._strength_for_ship(ship)["score"]
            leader = organization.get("leader_id")
            leader_person = self.people.get(leader, captain)
            personality = leader_person.get("personality", {})
            values = leader_person.get("values", {})
            for contact in known_contacts:
                target_org = contact["organization"]
                target_org_id = contact["organization_id"]
                target_kind = target_org["kind"]
                relation = contact["relationship"]
                is_marine = target_kind == "naval_command"
                is_pirate = target_kind in ("pirate_fleet", "independent_crew")
                targeted_goal = goal.get("type") == "hunt" and target_id == target_org_id
                active_treaty = any(
                    agreement["status"] == "active"
                    and set(agreement["participants"]) == {org_id, target_org_id}
                    for agreement in getattr(self, "agreements", []))
                conflict_cooldown = self.pair_timers.get(
                    f"conflict:{relation_key(org_id, target_org_id)}", -1) > hour
                remembered_grievance = any(
                    memory.get("kind") in ("petty_feud", "npc_clash", "agreement_breach")
                    and memory.get("valence") == "adverse"
                    and org_id in memory.get("participants", [])
                    and target_org_id in memory.get("participants", [])
                    and hour - memory["hour"] <= 240
                    for memory in self.memories.get(leader_person.get("person_id"), []))
                pirate_fleet = organization["kind"] == "pirate_fleet"
                bold_independent = (personality.get("boldness", 50) >= 62
                                    and personality.get("greed", 50) >= 58)
                willing = (targeted_goal or is_marine and (pirate_fleet or bold_independent)
                           or is_pirate and (relation <= -25 or pirate_fleet and relation < -10))
                if (not willing or active_treaty and relation > -60
                        or conflict_cooldown):
                    continue

                estimated_target = contact["report"].get("estimated_strength", 50)
                relative_advantage = own_strength - estimated_target
                risk = max(8, min(92, round(42 - relative_advantage * 0.55)))
                route_cost = self._route_length(world, ship["node"], contact["report"]["node"]) * 5
                ambition_bonus = (values.get("fame", 50) + values.get("wealth", 50)) // 10
                reward_bonus = 12 if is_marine else 4
                relationship_bonus = max(-25, min(25, round(-relation * 0.30)))
                if remembered_grievance:
                    relationship_bonus = min(40, relationship_bonus + 15)
                add("hunt", contact["report"]["node"], 25 + reward_bonus + ambition_bonus,
                    risk, min(100, route_cost + 10), relationship_bonus,
                    contact["ship"]["id"], target_org_id)

        if ship.get("condition", 100) < 70 or ship.get("crew", 0) <= 5:
            add("regroup", self._nearest_port(world, ship["node"]), 42,
                max(0, 80 - ship.get("condition", 80)), 8)
        return goal, candidates

    def _apply_generated_decisions(self, world, hour):
        for ship in sorted(world.npcs.ships, key=lambda row: row["id"]):
            captain_id = ship.get("captain_id")
            captain = self.people.get(captain_id)
            organization = self.organizations.get(ship.get("organization_id"))
            if not captain or not organization:
                continue
            previous = self.decision_state.get(ship["id"], {})
            reached_target = previous.get("target_node") == ship["node"]
            if hour < previous.get("decided_hour", -1000) + 6 and not reached_target:
                continue
            goal, candidates = self._generated_candidates(world, ship, captain, organization, hour)
            if not candidates:
                continue
            decision = choose_action(captain, candidates, self.generation_manifest,
                                     captain_id, hour, previous.get("kind"),
                                     goal.get("commitment", 50))
            selected = decision["candidate"]
            kind = decision["kind"]
            if kind in ("trade", "hunt", "patrol"):
                ship["mission"] = kind
            elif kind in ("regroup", "negotiate"):
                # These are strategic orders, not new fleet-system mission types.
                ship["mission"] = "patrol" if organization["kind"] == "naval_command" else "trade"
            ship["destination"] = selected["target_node"]
            captain["activity"] = (f"{kind.title()} toward {SEA_NODES[selected['target_node']]['name']} "
                                    f"for {goal.get('reason', organization['ambition'])}")
            self.decision_state[ship["id"]] = {
                "kind": kind, "target_node": selected["target_node"],
                "target_ship_id": selected.get("target_ship_id"),
                "target_org_id": selected.get("target_org_id"),
                "score": decision["score"], "decided_hour": hour,
                "goal_id": goal.get("goal_id"),
                "explanation": f"{kind} favored by {goal.get('reason', organization['ambition'])}",
            }

    def _apply_ambition_orders(self, world):
        # Famous rival leaders actively try to find each other; independent
        # traders keep their existing commerce routes and the Marines pursue.
        if self.mode == "authored":
            for org_id in ("blackwake_fleet", "world_government"):
                org = self.organizations[org_id]
                target_org = org["target"]
                target = self.organizations.get(target_org)
                own_ship = self._ship(world, org["ships"][0])
                target_ship = self._ship(world, target["ships"][0]) if target else None
                if own_ship and target_ship and own_ship["node"] != target_ship["node"]:
                    own_ship["destination"] = target_ship["node"]
        for person in self.people.values():
            ship = self._ship(world, person["ship"])
            if ship:
                if ship["mission"] == "hunt":
                    person["activity"] = f"Hunting rivals along the {SEA_NODES[ship['node']]['name']} route"
                elif ship["mission"] == "patrol":
                    person["activity"] = f"Patrolling near {SEA_NODES[ship['node']]['name']}"
                elif ship["mission"] == "trade":
                    person["activity"] = f"Trading through {SEA_NODES[ship['node']]['name']}"

    def advance(self, world, previous_npc_events=None):
        current_hour = world.clock.total_ticks // 360000
        if current_hour <= self.hour:
            return
        # World advances gameplay in individual seconds, but keep catch-up bounded
        # for explicit time jumps or loading an old state.
        first_hour = max(self.hour + 1, current_hour - 168)
        for hour in range(first_hour, current_hour + 1):
            previous_events = list(previous_npc_events or []) if hour == current_hour else []
            if hour == current_hour:
                self._news_from_legacy(world, previous_events, hour)
            if self.mode == "generated":
                self._expire_generated_agreements(world, hour)
                self._update_generated_knowledge(world, hour)
                self._apply_generated_decisions(world, hour)
            else:
                self._apply_ambition_orders(world)
            self._resolve_meetings(world, hour)
            for ship in world.npcs.ships:
                old_node = self.last_positions.get(ship["id"], ship["node"])
                if old_node != ship["node"] and SEA_NODES[ship["node"]].get("port"):
                    captain_id = self._ship_person(ship["id"])
                    org_id = self._ship_org(ship["id"])
                    captain = self.people.get(captain_id) if captain_id else None
                    org = self.organizations.get(org_id) if org_id else None
                    if captain and org:
                        self._append_news(hour, "port_arrival", [ship["id"], captain_id, org_id], ship["node"],
                                          f"visible {ship['mission']} voyage activity",
                                          f"{captain['name']} arrived at {SEA_NODES[ship['node']]['name']}",
                                          f"{captain['name']} brings {ship['name']} into {SEA_NODES[ship['node']]['name']}",
                                          f"The {org['name']} vessel {ship['name']} arrived at "
                                          f"{SEA_NODES[ship['node']]['name']} while on a {ship['mission']} voyage.")
                self.last_positions[ship["id"]] = ship["node"]
            self.hour = hour
        if previous_npc_events is not None:
            self.last_legacy_events = list(world.npcs.events[-80:])
        if len(self.pair_timers) > MAX_PAIR_TIMERS:
            self.pair_timers = {key: until for key, until in self.pair_timers.items() if until > current_hour}

    def snapshot(self):
        return {"mode": self.mode, "generation_manifest": deepcopy(self.generation_manifest),
                "hour": self.hour, "people": deepcopy(self.people), "organizations": deepcopy(self.organizations),
                "relationships": dict(self.relationships), "news": deepcopy(self.news),
                "social_relationships": deepcopy(getattr(self, "social_relationships", [])),
                "agreements": deepcopy(getattr(self, "agreements", [])),
                "memories": deepcopy(self.memories),
                "knowledge": deepcopy(self.knowledge), "decision_state": deepcopy(self.decision_state),
                "news_sequence": self.news_sequence, "pair_timers": dict(self.pair_timers),
                "last_positions": dict(self.last_positions), "last_legacy_events": list(self.last_legacy_events)}

    @classmethod
    def restore(cls, data, npc_fleets, current_hour=0, generation_manifest=None):
        system = cls(npc_fleets, current_hour)
        if data is None:
            return system
        mode = data.get("mode", "authored")
        if mode == "generated":
            return cls._restore_generated(data, npc_fleets, current_hour, generation_manifest)
        if mode != "authored":
            raise ValueError("Invalid NPC society mode")
        if type(data.get("hour")) is not int or data["hour"] < 0 or data["hour"] > current_hour:
            raise ValueError("Invalid NPC society clock")
        if set(data.get("people", {})) != set(PEOPLE) or set(data.get("organizations", {})) != set(ORGANIZATIONS):
            raise ValueError("NPC society cast or organizations changed")
        ship_ids = {row["id"] for row in npc_fleets.ships}
        for person_id, canonical in PEOPLE.items():
            row = data["people"][person_id]
            if any(row.get(key) != value for key, value in canonical.items() if key != "reputation"):
                raise ValueError("NPC identity or ambition changed")
            if row.get("ship") not in ship_ids or type(row.get("reputation")) is not int or not 0 <= row["reputation"] <= 100:
                raise ValueError("Invalid named NPC status")
            if row.get("person_id") != person_id or not isinstance(row.get("activity"), str) or len(row["activity"]) > 180:
                raise ValueError("Invalid NPC activity")
        for org_id, canonical in ORGANIZATIONS.items():
            row = data["organizations"][org_id]
            for key, value in canonical.items():
                if key != "reputation" and row.get(key) != value:
                    raise ValueError("NPC organization identity changed")
            if type(row.get("reputation")) is not int or not 0 <= row["reputation"] <= 100:
                raise ValueError("Invalid organization reputation")
        if set(data.get("relationships", {})) != set(INITIAL_RELATIONSHIPS):
            raise ValueError("Invalid NPC relationship graph")
        for score in data["relationships"].values():
            if type(score) is not int or not -100 <= score <= 100:
                raise ValueError("Invalid NPC relationship score")
        known = set(PEOPLE) | set(ORGANIZATIONS) | ship_ids
        news = data.get("news")
        if (not isinstance(news, list) or len(news) > MAX_NEWS
                or any(not isinstance(row, dict) or row.get("kind") not in
                       ("npc_market_event", "raid", "npc_clash", "alliance", "rivalry", "petty_feud",
                        "diplomacy", "port_arrival")
                       or type(row.get("hour")) is not int or not 0 <= row["hour"] <= data["hour"]
                       or row.get("location") not in SEA_NODES
                       or not isinstance(row.get("participants"), list)
                       or any(participant not in known for participant in row["participants"])
                       or any(not isinstance(row.get(field), str) or not row[field]
                              for field in ("id", "cause", "outcome", "headline", "body")) for row in news)):
            raise ValueError("Invalid NPC news history")
        sequence = data.get("news_sequence")
        if type(sequence) is not int or sequence < len(news):
            raise ValueError("Invalid NPC news sequence")
        news_ids = [row["id"] for row in news]
        if len(set(news_ids)) != len(news_ids) or any(
                not event_id.startswith("news-") or not event_id[5:].isdigit()
                or int(event_id[5:]) > sequence
                for event_id in news_ids):
            raise ValueError("Invalid NPC news identifiers")
        pair_timers = data.get("pair_timers")
        if not isinstance(pair_timers, dict) or len(pair_timers) > MAX_PAIR_TIMERS or any(
                not isinstance(key, str) or type(value) is not int or value < 0 for key, value in pair_timers.items()):
            raise ValueError("Invalid NPC meeting ledger")
        positions = data.get("last_positions")
        if not isinstance(positions, dict) or set(positions) != ship_ids or any(node not in SEA_NODES for node in positions.values()):
            raise ValueError("Invalid NPC chart history")
        legacy_events = data.get("last_legacy_events")
        if not isinstance(legacy_events, list) or len(legacy_events) > 80 or any(not isinstance(text, str) for text in legacy_events):
            raise ValueError("Invalid legacy NPC event cursor")
        system.hour = data["hour"]
        system.people = deepcopy(data["people"])
        system.organizations = deepcopy(data["organizations"])
        system.relationships = dict(data["relationships"])
        system.news = deepcopy(news)
        system.news_sequence = sequence
        system.pair_timers = dict(pair_timers)
        system.last_positions = dict(positions)
        system.last_legacy_events = list(legacy_events)
        return system

    @classmethod
    def _restore_generated(cls, data, npc_fleets, current_hour, generation_manifest):
        from pirates.npc_generation import (validate_person, validate_manifest, _validate_relationship,
                                            derive_command_power, _goal_type_for)

        if generation_manifest is None:
            raise ValueError("Generated society requires a generation manifest")
        manifest = validate_manifest(generation_manifest)
        if data.get("generation_manifest") != manifest:
            raise ValueError("NPC society and generation manifest disagree")
        hour = data.get("hour")
        if type(hour) is not int or hour < 0 or hour > current_hour:
            raise ValueError("Invalid generated society clock")
        people, organizations = deepcopy(data.get("people")), deepcopy(data.get("organizations"))
        if not isinstance(people, dict) or not 1 <= len(people) <= 200:
            raise ValueError("Invalid generated NPC people")
        if not isinstance(organizations, dict) or not 2 <= len(organizations) <= 20:
            raise ValueError("Invalid generated NPC organizations")
        ship_ids = {ship["id"] for ship in npc_fleets.ships}
        person_ids, organization_ids = set(people), set(organizations)
        ships_by_id = {ship["id"]: ship for ship in npc_fleets.ships}

        def migrate_goal(goal, reason, ship_id):
            if not isinstance(goal, dict):
                return
            if goal.get("type") in ("pursue_ambition", "advance_organization_ambition"):
                goal["type"] = _goal_type_for(str(goal.get("reason", reason)))
                if goal["type"] == "trade" and goal.get("target_id") not in SEA_NODES:
                    goal["target_id"] = ships_by_id.get(ship_id, {}).get("node")
            goal.setdefault("status", "active")
            goal.setdefault("completed_hour", None)

        for person in people.values():
            if not isinstance(person, dict):
                raise ValueError("Invalid generated NPC person record")
            if "command_power" not in person:
                # v1 generated saves predate this strategic-only score. Derive
                # it from persisted skills/role; never reroll the person.
                person["command_power"] = derive_command_power(person)
            migrate_goal(person.get("active_goal"), person.get("aspiration", ""), person.get("ship"))
        for organization in organizations.values():
            if isinstance(organization, dict):
                vessel_ids = organization.get("vessel_ids", organization.get("ships", []))
                ship_id = next((value for value in vessel_ids if value in ships_by_id), None)
                migrate_goal(organization.get("active_goal"), organization.get("ambition", ""), ship_id)
        agreement_rows = deepcopy(data.get("agreements", []))
        if isinstance(agreement_rows, list):
            for agreement in agreement_rows:
                if isinstance(agreement, dict):
                    agreement.setdefault("fulfillments", [])
                    agreement.setdefault("shared_reports", [])
        def valid_goal_target(goal):
            if not isinstance(goal, dict):
                return False
            if (not isinstance(goal.get("goal_id"), str) or not goal["goal_id"]
                    or goal.get("status") not in ("active", "completed")
                    or type(goal.get("progress")) is not int or not 0 <= goal["progress"] <= 100
                    or type(goal.get("commitment")) is not int or not 0 <= goal["commitment"] <= 100
                    or type(goal.get("priority")) is not int or not 0 <= goal["priority"] <= 100
                    or type(goal.get("created_hour")) is not int or goal["created_hour"] < 0
                    or type(goal.get("review_hour")) is not int or goal["review_hour"] < goal["created_hour"]
                    or (goal.get("status") == "completed") != (type(goal.get("completed_hour")) is int)
                    or (goal.get("completed_hour") is not None
                        and not goal["created_hour"] <= goal["completed_hour"] <= hour)
                    or type(goal.get("last_progress_hour", 0)) is not int
                    or not goal["created_hour"] <= goal.get("last_progress_hour", 0) <= hour):
                return False
            target = goal.get("target_id")
            if goal.get("type") == "trade":
                return target in SEA_NODES
            if goal.get("type") in ("hunt", "patrol", "negotiate"):
                return target in organization_ids
            return False
        for person_id, person in people.items():
            validate_person(person)
            if person.get("person_id") != person_id:
                raise ValueError("Generated NPC person key mismatch")
            organization = organizations.get(person.get("organization_id"), {})
            leader_id = organization.get("leader_id") if isinstance(organization, dict) else None
            expected_superior = None if person_id == leader_id else leader_id
            if (person.get("organization_id") not in organization_ids
                    or person.get("organization") != person.get("organization_id")
                    or person.get("ship") not in ship_ids
                    or person.get("vessel_id") != person.get("ship")
                    or person.get("superior_id") != expected_superior
                    or not valid_goal_target(person.get("active_goal"))
                    or not isinstance(person.get("activity"), str)):
                raise ValueError("Invalid generated NPC person links or state")
        for org_id, organization in organizations.items():
            if (organization.get("organization_id") != org_id
                    or not isinstance(organization.get("name"), str)
                    or organization.get("kind") not in
                    ("pirate_fleet", "independent_crew", "naval_command", "trading_company")
                    or organization.get("leader_id") not in person_ids
                    or organization.get("leader") != organization.get("leader_id")
                    or not isinstance(organization.get("member_ids"), list)
                    or any(not isinstance(person_id, str) for person_id in organization["member_ids"])
                    or len(set(organization["member_ids"])) != len(organization["member_ids"])
                    or organization["leader_id"] not in organization["member_ids"]
                    or any(person_id not in person_ids or people[person_id]["organization_id"] != org_id
                           for person_id in organization["member_ids"])
                    or not isinstance(organization.get("vessel_ids"), list)
                    or any(not isinstance(ship_id, str) for ship_id in organization["vessel_ids"])
                    or len(set(organization["vessel_ids"])) != len(organization["vessel_ids"])
                    or organization.get("ships") != organization.get("vessel_ids")
                    or any(ship_id not in ship_ids for ship_id in organization["vessel_ids"])
                    or not valid_goal_target(organization.get("active_goal"))
                    or type(organization.get("reputation")) is not int or not 0 <= organization["reputation"] <= 100
                    or type(organization.get("cohesion")) is not int or not 0 <= organization["cohesion"] <= 100):
                raise ValueError("Invalid generated NPC organization hierarchy")
        for ship in npc_fleets.ships:
            if (ship.get("organization_id") not in organization_ids
                    or ship.get("captain_id") not in person_ids
                    or people[ship["captain_id"]].get("ship") != ship["id"]
                    or not isinstance(ship.get("member_ids"), list)
                    or any(person_id not in person_ids or people[person_id].get("ship") != ship["id"]
                           for person_id in ship["member_ids"])):
                raise ValueError("Generated NPC fleet and society hierarchy disagree")

        legacy_relationships = data.get("relationships")
        if not isinstance(legacy_relationships, dict):
            raise ValueError("Invalid generated organization affinity map")
        expected_pairs = {relation_key(first, second) for first in organization_ids for second in organization_ids
                          if first != second}
        if set(legacy_relationships) != expected_pairs or any(
                type(score) is not int or not -100 <= score <= 100 for score in legacy_relationships.values()):
            raise ValueError("Invalid generated organization affinity map")
        social_relationships = data.get("social_relationships")
        if not isinstance(social_relationships, list) or len(social_relationships) > 200 * 6 + 20 * 19:
            raise ValueError("Invalid generated social relationships")
        relationship_keys = set()
        for relationship in social_relationships:
            allowed = person_ids if relationship.get("entity_type") == "person" else organization_ids
            _validate_relationship(relationship, allowed)
            key = (relationship["entity_type"], relationship["source_id"], relationship["target_id"])
            if key in relationship_keys:
                raise ValueError("Duplicate generated social relationship")
            relationship_keys.add(key)
        agreements = agreement_rows
        if not isinstance(agreements, list) or len(agreements) > 190:
            raise ValueError("Invalid generated agreements")
        agreement_ids = set()
        for agreement in agreements:
            if (not isinstance(agreement, dict) or not isinstance(agreement.get("agreement_id"), str)
                    or agreement["agreement_id"] in agreement_ids or agreement.get("kind") != "mutual_aid"
                    or not isinstance(agreement.get("participants"), list)
                    or len(agreement["participants"]) != 2
                    or agreement["participants"][0] == agreement["participants"][1]
                    or any(org_id not in organization_ids for org_id in agreement["participants"])
                    or not isinstance(agreement.get("signatories"), list)
                    or len(agreement["signatories"]) != 2
                    or agreement["signatories"][0] == agreement["signatories"][1]
                    or any(organizations[org_id]["leader_id"] != signatory
                           for org_id, signatory in zip(agreement["participants"], agreement["signatories"]))
                    or agreement.get("status") not in ("active", "expired", "breached")
                    or type(agreement.get("start_hour")) is not int
                    or type(agreement.get("expires_hour")) is not int
                    or not 0 <= agreement["start_hour"] <= hour
                    or agreement["expires_hour"] <= agreement["start_hour"]
                    or not isinstance(agreement.get("purpose"), str) or not agreement["purpose"]
                    or not isinstance(agreement.get("basis"), str) or not agreement["basis"]
                    or not isinstance(agreement.get("terms"), list) or not agreement["terms"]
                    or any(not isinstance(term, str) or not term for term in agreement["terms"])
                    or not isinstance(agreement.get("fulfillments"), list)
                    or len(agreement["fulfillments"]) > 20000
                    or any(not isinstance(event_id, str) for event_id in agreement["fulfillments"])
                    or not isinstance(agreement.get("shared_reports"), list)
                    or len(set(agreement["shared_reports"])) != len(agreement["shared_reports"])
                    or any(ship_id not in ship_ids for ship_id in agreement["shared_reports"])
                    or agreement["status"] == "expired" and type(agreement.get("expiry_event_id")) is not str
                    or agreement["status"] == "expired" and (
                        type(agreement.get("expired_hour")) is not int
                        or not agreement["expires_hour"] <= agreement["expired_hour"] <= hour)
                    or agreement["status"] == "breached" and (
                        type(agreement.get("breach_hour")) is not int
                        or not agreement["start_hour"] <= agreement["breach_hour"] <= hour
                        or type(agreement.get("breach_event_id")) is not str)):
                raise ValueError("Invalid generated formal agreement")
            agreement_ids.add(agreement["agreement_id"])

        system = cls(npc_fleets, hour)
        system.mode = "generated"
        system.generation_manifest = deepcopy(manifest)
        system.people = deepcopy(people)
        system.organizations = deepcopy(organizations)
        system.relationships = dict(legacy_relationships)
        system.social_relationships = deepcopy(social_relationships)
        system.agreements = deepcopy(agreements)
        memories = data.get("memories")
        expected_memory_owners = person_ids | organization_ids
        if memories is None:
            memories = {owner_id: [] for owner_id in expected_memory_owners}
        if not isinstance(memories, dict) or set(memories) != expected_memory_owners:
            raise ValueError("Invalid generated NPC memory ledger")
        for owner_id, rows in memories.items():
            if not isinstance(rows, list) or len(rows) > MAX_MEMORIES:
                raise ValueError("Invalid generated NPC memory list")
            if any(not isinstance(memory, dict)
                   or not isinstance(memory.get("event_id"), str)
                   or not memory["event_id"].startswith("news-")
                   or type(memory.get("hour")) is not int or not 0 <= memory["hour"] <= hour
                   or not isinstance(memory.get("kind"), str)
                   or not isinstance(memory.get("summary"), str)
                   or memory.get("valence") not in ("adverse", "notable")
                   or "cause" in memory and (not isinstance(memory["cause"], str)
                                               or len(memory["cause"]) > 1000)
                   or "outcome" in memory and (not isinstance(memory["outcome"], str)
                                                 or len(memory["outcome"]) > 1000)
                   or "participants" in memory and (
                       not isinstance(memory["participants"], list)
                       or any(value not in person_ids | organization_ids | ship_ids
                              for value in memory["participants"])) for memory in rows):
                raise ValueError("Invalid generated NPC memory record")
        system.memories = deepcopy(memories)
        system.news = deepcopy(data.get("news", []))
        if (len(system.news) > MAX_NEWS or any(not isinstance(row, dict)
                or not isinstance(row.get("id"), str) or not row["id"].startswith("news-")
                 or row.get("kind") not in ("npc_market_event", "raid", "npc_clash", "alliance",
                                              "rivalry", "petty_feud", "diplomacy", "port_arrival",
                                             "agreement_fulfillment", "agreement_expiry", "agreement_breach")
                or row.get("location") not in SEA_NODES
                or type(row.get("hour")) is not int or not 0 <= row["hour"] <= hour
                or not isinstance(row.get("cause"), str) or not isinstance(row.get("outcome"), str)
                or not isinstance(row.get("headline"), str) or not isinstance(row.get("body"), str)
                or not isinstance(row.get("participants"), list)
                or any(participant not in person_ids | organization_ids | ship_ids
                       for participant in row.get("participants", []))
                or not isinstance(row.get("details", {}), dict)
                for row in system.news)):
            raise ValueError("Invalid generated NPC news history")
        for row in system.news:
            details = row.get("details", {})
            if row["kind"] != "npc_clash" or "outcome_type" not in details:
                continue
            if (details.get("outcome_type") not in ("victory", "disengagement")
                    or "public_report" in details and type(details["public_report"]) is not bool
                    or not isinstance(details.get("strength"), dict)
                    or any(ship_id not in ship_ids or type(value) is not int or not 0 <= value <= 100
                           for ship_id, value in details["strength"].items())
                    or not isinstance(details.get("condition_loss"), dict)
                    or any(ship_id not in ship_ids or type(value) is not int or not 0 <= value <= 100
                           for ship_id, value in details["condition_loss"].items())):
                raise ValueError("Invalid generated NPC clash details")
            transfers = details.get("transfers")
            if (not isinstance(transfers, dict) or type(transfers.get("silver")) is not int
                    or transfers["silver"] < 0 or not isinstance(transfers.get("cargo"), dict)
                    or any(item_id not in ("grain", "iron", "beans", "shot")
                           or type(amount) is not int or amount < 0
                           for item_id, amount in transfers["cargo"].items())):
                raise ValueError("Invalid generated NPC clash transfers")
            progression = details.get("captain_progression", {})
            if (not isinstance(progression, dict)
                    or any(person_id not in person_ids or not isinstance(result, dict)
                           or not isinstance(result.get("xp_gained"), dict)
                           or set(result["xp_gained"]) != {"tactics", "seamanship"}
                           or any(type(amount) is not int or not 0 <= amount <= 100
                                  for amount in result["xp_gained"].values())
                           or type(result.get("career_level_before")) is not int
                           or type(result.get("career_level_after")) is not int
                           or type(result.get("command_power_before")) is not int
                           or type(result.get("command_power_after")) is not int
                           for person_id, result in progression.items())):
                raise ValueError("Invalid generated NPC clash captain progression")
            cohesion_change = details.get("cohesion_change", {})
            if (not isinstance(cohesion_change, dict)
                    or any(organization_id not in organization_ids or type(change) is not int
                           or not -100 <= change <= 100
                           for organization_id, change in cohesion_change.items())):
                raise ValueError("Invalid generated NPC clash cohesion changes")
            if details["outcome_type"] == "victory" and (
                    details.get("winner_ship_id") not in ship_ids
                    or details.get("loser_ship_id") not in ship_ids):
                raise ValueError("Invalid generated NPC clash participants")
        system.news_sequence = data.get("news_sequence")
        if type(system.news_sequence) is not int or system.news_sequence < len(system.news):
            raise ValueError("Invalid generated NPC news sequence")
        pair_timers = data.get("pair_timers")
        if not isinstance(pair_timers, dict) or len(pair_timers) > MAX_PAIR_TIMERS or any(
                not isinstance(key, str) or type(value) is not int or value < 0 for key, value in pair_timers.items()):
            raise ValueError("Invalid generated NPC meeting ledger")
        positions = data.get("last_positions")
        if not isinstance(positions, dict) or set(positions) != ship_ids or any(
                node not in SEA_NODES for node in positions.values()):
            raise ValueError("Invalid generated NPC chart history")
        knowledge = deepcopy(data.get("knowledge"))
        if knowledge is None:
            knowledge = {
                org_id: {ship["id"]: {"node": ship["node"], "observed_hour": hour,
                                      "source": "own fleet"}
                         for ship in npc_fleets.ships if ship["organization_id"] == org_id}
                for org_id in organization_ids
            }
        if not isinstance(knowledge, dict) or set(knowledge) != organization_ids:
            raise ValueError("Invalid generated NPC knowledge map")
        for observer_id, contacts in knowledge.items():
            if not isinstance(contacts, dict) or len(contacts) > len(ship_ids):
                raise ValueError("Invalid generated NPC contact list")
            for ship_id, report in contacts.items():
                if (ship_id not in ship_ids or not isinstance(report, dict)
                        or report.get("node") not in SEA_NODES
                        or type(report.get("observed_hour")) is not int
                        or not 0 <= report["observed_hour"] <= hour
                        or not isinstance(report.get("source"), str) or not report["source"]
                        or "estimated_strength" in report and (
                            type(report["estimated_strength"]) is not int
                            or not 0 <= report["estimated_strength"] <= 100)):
                    raise ValueError("Invalid generated NPC sighting report")
                if report["source"] == "own fleet" and ship_id not in organizations[observer_id]["vessel_ids"]:
                    raise ValueError("NPC claims an unowned vessel as its own")
        decision_state = data.get("decision_state", {})
        if not isinstance(decision_state, dict) or not set(decision_state) <= ship_ids:
            raise ValueError("Invalid generated NPC decision state")
        for ship_id, decision in decision_state.items():
            if (not isinstance(decision, dict)
                    or decision.get("kind") not in ("trade", "hunt", "patrol", "regroup", "negotiate")
                    or decision.get("target_node") not in SEA_NODES
                    or decision.get("target_ship_id") is not None
                    and decision.get("target_ship_id") not in ship_ids
                    or decision.get("target_org_id") is not None
                    and decision.get("target_org_id") not in organization_ids
                    or type(decision.get("decided_hour")) is not int
                    or not 0 <= decision["decided_hour"] <= hour
                    or type(decision.get("score")) not in (int, float)
                    or not isinstance(decision.get("explanation"), str)):
                raise ValueError("Invalid generated NPC decision record")
        legacy_events = data.get("last_legacy_events")
        if not isinstance(legacy_events, list) or len(legacy_events) > 80 or any(
                not isinstance(text, str) for text in legacy_events):
            raise ValueError("Invalid generated NPC event cursor")
        system.pair_timers = dict(pair_timers)
        system.last_positions = dict(positions)
        system.last_legacy_events = list(legacy_events)
        system.knowledge = deepcopy(knowledge)
        system.decision_state = deepcopy(decision_state)
        return system
