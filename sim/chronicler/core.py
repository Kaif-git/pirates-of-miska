"""Deterministic, read-only prose and historical artifacts for simulation worlds."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable


DEFAULT_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "data" / "templates" / "chronicler.json"
_SLOT = re.compile(r"\{([a-z_]+)(?:![rsa])?(?::[^{}]+)?\}")
_RAW_ID = re.compile(r"\b(?:event|character|ship|crew|faction|isle|route|region)_\d+\b", re.I)
_FIRST_PERSON = re.compile(r"\b(?:I|me|my|mine|we|us|our|ours)\b", re.I)
_SLOTS = {"actor", "target", "place", "cause", "detail"}
_EVENT_LABELS = {
    "CHARACTER_DIED": "the death",
    "SHIP_SUNK": "the sinking",
    "ENCOUNTER_RESOLVED": "the encounter",
    "WAR_DECLARED": "the declaration of war",
    "TREATY_BREACHED": "the treaty breach",
    "FACTION_FOUNDED": "the founding",
    "FACTION_DISSOLVED": "the dissolution",
    "TREASURE_FOUND": "the discovery",
    "HARVEST_FAILURE": "the failed harvest",
    "PLAGUE": "the outbreak",
    "TERRITORY_CLAIMED": "the territorial claim",
    "BOUNTY_SET": "the bounty",
    "BOUNTY_RAISED": "the raised bounty",
    "DIRECTOR_INJECTION": "the vendetta",
    "CHARACTER_AGED": "the passing years",
    "AGENT_ACTION": "an earlier choice",
    "ENCOUNTER_DETECTED": "a sighting at sea",
    "ROUTE_HAZARD": "danger along a route",
    "SHIP_DAMAGED": "damage to a vessel",
    "MEMORY_CREATED": "a grievance",
    "DREAM_PROGRESS": "a long-held ambition",
    "TREATY_TRANSITION": "a change in relations",
    "GRUDGE_PURSUED": "the pursuit of a rival",
    "FACTION_MONTHLY_DECISION": "a faction council decision",
    "VOYAGE_STARTED": "a voyage beginning",
    "VOYAGE_ARRIVED": "a voyage's arrival",
    "MARKET_TRADE": "a market exchange",
    "CREW_RESTED": "a crew taking rest",
    "PRISONER_FATE": "a prisoner's fate",
    "MARINE_COMMAND_CHANGED": "a change in Marine command",
    "MARINE_COMMAND_CHALLENGE_FAILED": "a failed challenge for Marine command",
    "GOVERNMENT_RESPONSE_FUNDED": "a funded government response",
    "CAPTAIN_CHANGED": "a change of captain",
    "SHIP_CAPTURED": "the capture of a ship",
    "SHIP_RENAMED": "a change of a ship's name",
    "BATTLE": "the fleet battle",
    "BATTLE_CONCLUDED": "the battle's conclusion",
    "LEGEND_DUEL": "the legendary duel",
    "LEGEND_RECOGNIZED": "the recognition",
    "FLEET_COMMAND_SET": "a fleet command order",
    "BATTLE_MORALE_CHANGED": "the battle's toll on morale",
    "BATTLE_CASUALTY": "a battle casualty",
    "FACTION_POWER_VACUUM": "a faction's crisis of command",
    "LOD_PROMOTED": "a rise from the background",
    "PUBLIC_RENOWN_EARNED": "recognition for a verified deed",
    "PROFITABLE_TRADE_RIVALRY": "a rivalry over profitable trade",
    "SHORTAGE_RELIEF_NEGOTIATED": "the easing of a recorded shortage",
    "CONTRABAND_SEIZED": "a lawful contraband seizure",
    "DREAM_PLAN_COMPLETED": "the completion of a proven ambition",
    "DREAM_PLAN_FAILED": "an ambition's verified failure",
    "DREAM_PLAN_ABANDONED": "an abandoned ambition",
    "TREASURE_CLUE_LEARNED": "the learning of a treasure clue",
    "TREASURE_CLUE_SPREAD": "a circulating treasure rumor",
    "TREASURE_RUMOR": "a circulating treasure rumor",
    "RUMOR_SHARED": "the passing of an uncertain report",
    "FEUD_RAID_ATTEMPTED": "a grudge-driven raid attempt",
    "CREW_EMPLOYMENT_ACCEPTED": "the employment of a stranded crew",
}


def load_templates(path: str | Path = DEFAULT_TEMPLATE_PATH) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load chronicler templates {path}: {exc}") from exc
    validate_templates(data)
    return data


def validate_templates(data: Any) -> None:
    if not isinstance(data, dict) or set(data) != {
            "schema_version", "templates", "significance", "reputation"} \
            or type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("Invalid chronicler template catalog schema")
    templates = data["templates"]
    if not isinstance(templates, dict) or not templates:
        raise ValueError("Chronicler templates must be a nonempty event mapping")
    for event_type, entry in templates.items():
        if (not isinstance(event_type, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", event_type)
                or not isinstance(entry, dict) or set(entry) != {"required_slots", "variants"}):
            raise ValueError("Invalid chronicler event template entry")
        required = entry["required_slots"]
        if (not isinstance(required, list) or len(set(required)) != len(required)
                or not set(required).issubset(_SLOTS) or not required):
            raise ValueError(f"Invalid required slots for {event_type}")
        variants = entry["variants"]
        if not isinstance(variants, list) or not variants:
            raise ValueError(f"Templates need variants: {event_type}")
        for variant in variants:
            if (not isinstance(variant, dict) or set(variant) != {"tone", "context_tags", "text"}
                    or not isinstance(variant["tone"], str) or not variant["tone"]
                    or not isinstance(variant["context_tags"], list)
                    or any(not isinstance(tag, str) or not tag for tag in variant["context_tags"])
                    or not isinstance(variant["text"], str) or not variant["text"]):
                raise ValueError(f"Invalid template variant for {event_type}")
            text = variant["text"]
            slots = set(_SLOT.findall(text))
            if slots != set(required):
                raise ValueError(f"Template {event_type} slots differ from declared required slots")
            if _RAW_ID.search(text) or _FIRST_PERSON.search(text) or "{" in _SLOT.sub("", text) \
                    or "}" in _SLOT.sub("", text):
                raise ValueError(f"Unsafe or malformed template text for {event_type}")
            try:
                text.format(**{slot: "sample" for slot in required})
            except (KeyError, ValueError, IndexError) as exc:
                raise ValueError(f"Unfilled or malformed template slot for {event_type}") from exc
    rules = data["significance"]
    required_rules = {"threshold", "rarity_floor", "rarity_ceiling", "reach_per_child",
                      "reach_cap", "novelty_floor", "recent_window_events", "collapse_types"}
    if (not isinstance(rules, dict) or set(rules) != required_rules
            or any(type(rules[key]) not in (int, float) or not math.isfinite(rules[key])
                   or rules[key] < 0 for key in required_rules - {"collapse_types", "recent_window_events"})
            or type(rules["recent_window_events"]) is not int or rules["recent_window_events"] < 1
            or not isinstance(rules["collapse_types"], list)
            or any(not isinstance(kind, str) for kind in rules["collapse_types"])
            or len(set(rules["collapse_types"])) != len(rules["collapse_types"])
            or rules["rarity_floor"] > rules["rarity_ceiling"]
            or rules["novelty_floor"] > 1 or rules["reach_cap"] < 1):
        raise ValueError("Invalid chronicler significance settings")
    reputation = data["reputation"]
    if (not isinstance(reputation, dict)
            or set(reputation) != {"fame_weight_by_tier", "epithet_rules"}
            or not isinstance(reputation["fame_weight_by_tier"], dict)
            or set(reputation["fame_weight_by_tier"])
            != {"BACKGROUND", "MINOR", "MAJOR", "LEGEND"}
            or any(type(weight) not in (int, float) or not math.isfinite(weight) or weight < 0
                   for weight in reputation["fame_weight_by_tier"].values())
            or not isinstance(reputation["epithet_rules"], list)):
        raise ValueError("Invalid chronicler reputation settings")
    for rule in reputation["epithet_rules"]:
        if (not isinstance(rule, dict)
                or set(rule) != {"event_types", "minimum_count", "epithet", "reason"}
                or not isinstance(rule["event_types"], list) or not rule["event_types"]
                or any(not isinstance(kind, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", kind)
                       for kind in rule["event_types"])
                or type(rule["minimum_count"]) is not int or rule["minimum_count"] < 1
                or not isinstance(rule["epithet"], str) or not rule["epithet"]
                or not isinstance(rule["reason"], str) or not rule["reason"]):
            raise ValueError("Invalid earned epithet rule")


def _ticks_per_year(world) -> int:
    time = world.config["time"]
    return (time["days_per_year"] * time["hours_per_day"]
            // time["hours_per_tick"])


def _year_for_tick(world, tick: int) -> int:
    year_ticks = _ticks_per_year(world)
    return 1 if tick <= 0 else (tick - 1) // year_ticks + 1


def _character_rows(world) -> dict[str, dict[str, Any]]:
    return {**world.archive, **world.entities,
            **{entity_id: world.lod._materialize(record)
               for entity_id, record in world.lod.named_records.items()}}


@dataclass(frozen=True)
class EpithetAward:
    entity_id: str
    name: str
    epithet: str
    deed_event_id: str
    reason: str

    def to_record(self) -> dict[str, str]:
        return {"name": self.name, "epithet": self.epithet,
                "deed_event_id": self.deed_event_id, "reason": self.reason}


class NameRegistry:
    """Read-only display labels for world entities; stable IDs never become prose."""

    def __init__(self, world) -> None:
        self.world = world
        self.labels: dict[str, str] = {}
        living_names: dict[str, str] = {}
        character_rows = {**world.archive, **world.entities,
                          **{entity_id: world.lod._materialize(record)
                             for entity_id, record in world.lod.named_records.items()}}
        for entity_id, row in sorted(character_rows.items()):
            kind = row.get("kind")
            if kind == "character":
                label = row.get("display_name")
                heritage = row.get("heritage", {})
                lineage_titles = heritage.get("titles", []) if isinstance(heritage, dict) else []
                suffix = next((title for title in lineage_titles
                               if title in {"Jr.", "II", "III"}), None)
                if suffix and heritage.get("parents", [None, None]) != [None, None]:
                    label = f"{label} {suffix}" if isinstance(label, str) else label
            elif kind == "ship":
                label = row.get("name")
            else:
                continue
            if not isinstance(label, str) or not label.strip():
                continue
            base_label = row.get("display_name") if kind == "character" else row.get("name")
            key = " ".join(base_label.split()).casefold()
            if entity_id in world.entities or entity_id in world.lod.named_records:
                previous = living_names.get(key)
                if previous is not None and previous != entity_id:
                    raise ValueError("Chronicler encountered duplicate living character/ship names")
                living_names[key] = entity_id
            epithet = row.get("epithet")
            self.labels[entity_id] = f"{label.strip()}, {epithet}" if epithet else label.strip()
        for faction_id, faction in sorted(world.politics.factions.items()):
            faction_name = faction.get("name")
            if isinstance(faction_name, str) and faction_name.strip():
                self.labels[faction_id] = faction_name.strip()
        for faction_id, faction in sorted(world.politics.archived_factions.items()):
            faction_name = faction.get("name")
            if isinstance(faction_name, str) and faction_name.strip():
                self.labels[faction_id] = faction_name.strip()
        for merchant_id, merchant in sorted(world.economy.merchants.items()):
            merchant_name = merchant.get("name")
            if isinstance(merchant_name, str) and merchant_name.strip():
                self.labels[merchant_id] = merchant_name.strip()
        for island in world.geography.islands.values():
            self.labels[island.id] = island.name
        for route in world.geography.routes.values():
            self.labels[route.id] = route.id.replace("_", " ").title()
        for region in world.geography.regions.values():
            self.labels[region.id] = region.name

    def entity(self, entity_id: str | None, fallback: str = "someone") -> str:
        if not entity_id:
            return fallback
        label = self.labels.get(entity_id)
        if label:
            return label
        # Event-only display names are allowed; internal identifiers are not.
        if not _RAW_ID.search(entity_id):
            return entity_id
        return fallback

    def location(self, location_id: str | None) -> str:
        return self.entity(location_id, "the open sea")


class Chronicler:
    """Ranks and narrates immutable events, returning artifacts without mutating world."""

    _THREAD_TERMINALS = {
        "DREAM_PLAN_COMPLETED", "DREAM_PLAN_FAILED", "DREAM_PLAN_ABANDONED",
        "PLAN_STEP_BLOCKED", "PLAN_STEP_FAILED",
        "TREASURE_FOUND", "PROFITABLE_TRADE_RIVALRY", "SHORTAGE_RELIEF_NEGOTIATED",
        "CONTRABAND_SEIZED", "TREATY_BREACHED", "TREATY_OBLIGATION_FULFILLED",
        "ENCOUNTER_RESOLVED", "BATTLE_CONCLUDED", "LEGEND_RECOGNIZED",
        "PUBLIC_RENOWN_EARNED", "FEUD_RAID_ATTEMPTED", "CREW_EMPLOYMENT_ACCEPTED",
    }

    def __init__(self, templates: dict[str, Any] | None = None) -> None:
        self.catalog = load_templates() if templates is None else templates
        validate_templates(self.catalog)
        self._prepared_key: tuple[int, int] | None = None
        self._type_counts: Counter[str] = Counter()
        self._outcome_counts: Counter[tuple[str, str]] = Counter()
        self._novelty_counts: dict[str, int] = {}
        self._reach_cache: dict[str, int] = {}
        self._emotion_cache: dict[str, float] = {}
        self._registry_key: int | None = None
        self._name_registry: NameRegistry | None = None
        self._significance_cache: dict[str, float] = {}

    def _prepare(self, world) -> None:
        key = (id(world.event_log), len(world.event_log))
        if key == self._prepared_key:
            return
        events = list(world.event_log)
        self._type_counts = Counter(event.type for event in events)
        self._outcome_counts = Counter(
            (event.type, str(event.data.get("outcome", event.data.get("fate", ""))))
            for event in events
            if event.data.get("outcome", event.data.get("fate")) is not None
        )
        window = self.catalog["significance"]["recent_window_events"]
        recent: deque[str] = deque()
        recent_counts: Counter[str] = Counter()
        self._novelty_counts = {}
        for event in events:
            self._novelty_counts[event.id] = recent_counts[event.type]
            recent.append(event.type)
            recent_counts[event.type] += 1
            if len(recent) > window:
                expired = recent.popleft()
                recent_counts[expired] -= 1
        self._prepared_key = key
        self._reach_cache.clear()
        self._emotion_cache.clear()
        self._significance_cache.clear()
        self._registry_key = None

    def _registry(self, world) -> NameRegistry:
        key = id(world)
        if self._registry_key != key or self._name_registry is None:
            self._name_registry = NameRegistry(world)
            self._registry_key = key
        return self._name_registry

    def significance(self, world, event) -> float:
        self._prepare(world)
        cached = self._significance_cache.get(event.id)
        if cached is not None:
            return cached
        outcome = event.data.get("outcome", event.data.get("fate"))
        rarity_count = (self._outcome_counts[(event.type, str(outcome))]
                        if outcome is not None else self._type_counts[event.type])
        rarity = max(self.catalog["significance"]["rarity_floor"],
                     min(self.catalog["significance"]["rarity_ceiling"],
                         2.0 / math.sqrt(max(1, rarity_count))))
        fame = max((self._fame(world, item["entity_id"]) for item in event.actors), default=1.0)
        reach = self._causal_reach(world, event.id)
        reach_factor = min(self.catalog["significance"]["reach_cap"],
                           1.0 + reach * self.catalog["significance"]["reach_per_child"])
        emotional = self._emotional_weight(world, event.id)
        novelty = self._novelty(world, event)
        score = event.magnitude * fame * rarity * reach_factor * emotional * novelty
        self._significance_cache[event.id] = score
        return score

    def _fame(self, world, entity_id: str) -> float:
        row = world.entities.get(entity_id) or world.archive.get(entity_id)
        if not row:
            return 1.0
        return self.catalog["reputation"]["fame_weight_by_tier"].get(row.get("tier"), 1.0)

    def _causal_reach(self, world, event_id: str) -> int:
        if event_id in self._reach_cache:
            return self._reach_cache[event_id]
        todo = deque([event_id])
        seen = {event_id}
        while todo and len(seen) <= 256:
            current = todo.popleft()
            for child in world.event_log.consequences(current):
                if child.id not in seen:
                    seen.add(child.id)
                    todo.append(child.id)
        value = len(seen) - 1
        self._reach_cache[event_id] = value
        return value

    def _emotional_weight(self, world, event_id: str) -> float:
        if event_id in self._emotion_cache:
            return self._emotion_cache[event_id]
        todo = deque([event_id])
        seen = {event_id}
        memories = 0
        while todo and len(seen) <= 256:
            current = todo.popleft()
            for child in world.event_log.consequences(current):
                if child.id in seen:
                    continue
                seen.add(child.id)
                memories += child.type in {"MEMORY_CREATED", "GRUDGE_INHERITED", "VENGEANCE_DREAM"}
                todo.append(child.id)
        value = 1.0 + min(1.0, memories * 0.15)
        self._emotion_cache[event_id] = value
        return value

    def _novelty(self, world, event) -> float:
        similar = self._novelty_counts.get(event.id, 0)
        return max(self.catalog["significance"]["novelty_floor"], 1.0 - similar * 0.08)

    def epithet_awards(self, world) -> list[EpithetAward]:
        labels = self._registry(world)
        credited: dict[str, list[Any]] = defaultdict(list)
        faction_records = {**world.politics.archived_factions, **world.politics.factions}
        for event in world.event_log:
            if event.type == "TREATY_BREACHED":
                for participant in event.actors:
                    faction_id = participant["entity_id"]
                    faction = faction_records.get(faction_id)
                    crew = world.crew_ships.crews.get(faction_id)
                    leader_id = (faction or {}).get("leader_id") or (crew or {}).get("captain_id")
                    if leader_id:
                        credited[leader_id].append(event)
            if event.type == "SHIP_SUNK":
                sunk_ship = next((item["entity_id"] for item in event.targets
                                  if item["role"] == "sunken_ship"), None)
                pending = list(event.caused_by)
                visited = set()
                while pending:
                    parent_id = pending.pop(0)
                    if parent_id in visited:
                        continue
                    visited.add(parent_id)
                    parent = world.event_log.get(parent_id)
                    if parent.type == "ENCOUNTER_RESOLVED":
                        for opponent_ship in parent.data.get("ships", []):
                            if opponent_ship == sunk_ship:
                                continue
                            ship = world.entities.get(opponent_ship) or world.archive.get(opponent_ship, {})
                            crew = world.crew_ships.crews.get(ship.get("crew_id"))
                            if crew and crew.get("captain_id"):
                                credited[crew["captain_id"]].append(event)
                        break
                    pending.extend(parent.caused_by)
            if event.type == "SHIP_CAPTURED":
                for participant in event.actors:
                    crew = world.crew_ships.crews.get(participant["entity_id"])
                    if crew and crew.get("captain_id"):
                        credited[crew["captain_id"]].append(event)
        awards = []
        for entity_id, character in sorted(_character_rows(world).items()):
            if character.get("kind") != "character":
                continue
            deeds = sorted({event.id: event for event in (
                *world.event_log.by_entity(entity_id), *credited.get(entity_id, ()))
                            }.values(), key=lambda event: event.id)
            for rule in self.catalog["reputation"]["epithet_rules"]:
                deeds_for_rule = [event for event in deeds if event.type in rule["event_types"]]
                if len(deeds_for_rule) >= rule["minimum_count"] \
                        and character.get("epithet") != rule["epithet"]:
                    deed = deeds_for_rule[rule["minimum_count"] - 1]
                    awards.append(EpithetAward(entity_id, labels.entity(entity_id),
                                               rule["epithet"], deed.id, rule["reason"]))
        return awards

    def _slots(self, world, event, awards: dict[str, EpithetAward] | None = None) -> dict[str, str]:
        labels = self._registry(world)
        awards = awards or {}
        actor_ids = [row["entity_id"] for row in event.actors]
        target_ids = [row["entity_id"] for row in event.targets]
        if event.type == "ENCOUNTER_RESOLVED" and len(actor_ids) > 1:
            target_ids = [*actor_ids[1:], *target_ids]
            actor_ids = actor_ids[:1]
        if event.type == "MARKET_TRADE" and event.data.get("merchant_id"):
            actor_ids = [event.data["merchant_id"]]
        if not actor_ids and target_ids:
            actor_ids, target_ids = target_ids, []
        actor = self._participant_name(labels, actor_ids, awards, "someone")
        target = self._participant_name(labels, target_ids, awards, "their rivals")
        if actor == target:
            target = "their own company"
        detail = self._detail(world, event)
        cause = self._cause(world, event, labels, awards)
        return {"actor": actor, "target": target,
                "place": labels.location(event.location), "detail": detail, "cause": cause}

    @staticmethod
    def _participant_name(labels: NameRegistry, ids: list[str], awards: dict[str, EpithetAward],
                          fallback: str) -> str:
        for entity_id in ids:
            award = awards.get(entity_id)
            if award:
                return f"{award.name}, {award.epithet}"
            found = labels.entity(entity_id, "")
            if found:
                return found
        return fallback

    def _detail(self, world, event) -> str:
        data = event.data
        if event.type == "BATTLE":
            legend_count = data.get("legend_count", 0)
            rounds = data.get("rounds_planned", 0)
            return f"{legend_count} legendary commanders fought through {rounds} rounds"
        if event.type == "LEGEND_DUEL":
            winner = data.get("winner_side")
            if winner is None:
                return "neither duelist gained an edge"
            participant = (*event.actors, *event.targets)[winner]
            name = self._registry(world).entity(participant["entity_id"], "a duelist")
            return f"{name} won the duel"
        if event.type == "BATTLE_CONCLUDED":
            winner = data.get("winner_side")
            if winner is None:
                return "neither fleet broke the other's line"
            return "the fleet carried the day"
        if event.type == "FLEET_COMMAND_SET":
            return f"the commander adopted a {data.get('doctrine', 'balanced')} doctrine"
        if event.type == "BATTLE_REINFORCEMENT":
            return f"{len(data.get('ships', []))} ships joined the line"
        if event.type == "BATTLE_MORALE_CHANGED":
            change = data.get("after", 0) - data.get("before", 0)
            return f"morale {'rose' if change >= 0 else 'fell'} by {abs(change)} points"
        if event.type == "BATTLE_CASUALTY":
            return str(data.get("injury_id", "wounds")).replace("_", " ")
        if event.type == "LOD_PROMOTED":
            return f"{data.get('name', 'a resident')} drew notice after a notable event"
        if event.type == "FACTION_POWER_VACUUM":
            return "a succession crisis after defeat"
        if event.type == "MARKET_TRADE":
            quantity = data.get("quantity_milli", 0)
            good = data.get("goods_id", "goods")
            if type(quantity) is int and quantity > 0 and isinstance(good, str) \
                    and not _RAW_ID.search(good):
                return f"{quantity / 1000:g} units of {good.replace('_', ' ')}"
        if event.type == "PLAGUE":
            strain = data.get("strain", "illness")
            population = data.get("population")
            if isinstance(strain, str) and type(population) is int:
                return f"{strain.replace('_', ' ')} affected {population:,} residents"
        if event.type == "HARVEST_FAILURE":
            loss = data.get("grain_output_loss_permille")
            if type(loss) is int:
                return f"grain output fell by {loss / 10:g}%"
        claim_type = data.get("claim_type")
        if event.type == "TERRITORY_CLAIMED" and claim_type == "unruled_expansion":
            return "the island had no ruler"
        preferred = ("outcome", "fate", "reason", "condition", "claim_type", "action",
                     "name", "good_id", "cause")
        for key in preferred:
            value = data.get(key)
            if isinstance(value, str) and value and not _RAW_ID.search(value):
                return {"mutual_withdrawal": "the crews chose mutual withdrawal and avoided a fight",
                        "parley_ended_fight": "talks failed and the crews fought",
                        "combat_resolved": "the ships disengaged after combat",
                        "both_sunk": "both vessels were lost",
                        "both_sunk_rare": "both vessels were lost in an unusual fight",
                        "surrender": "the opposing crew surrendered",
                        "unruled_expansion": "the island had no ruler"}.get(
                            value, value.replace("_", " "))
        for key in ("amount_silver", "bounty_silver", "bounty_amount", "amount",
                    "quantity_milli", "season"):
            value = data.get(key)
            if type(value) is int and value:
                if key.endswith("silver") or key in {"bounty_amount", "amount"}:
                    return f"a {value}-silver sum was at stake"
                if key == "quantity_milli":
                    return f"{value / 1000:g} units changed hands"
                return f"the {value}th season brought hardship"
        return event.type.replace("_", " ").lower()

    def _cause(self, world, event, labels: NameRegistry,
               awards: dict[str, EpithetAward]) -> str:
        if event.caused_by:
            parent = world.event_log.get(event.caused_by[0])
            return _EVENT_LABELS.get(parent.type, parent.type.replace("_", " ").lower())
        tags = [tag.replace("_", " ") for tag in event.causes_tags
                if not _RAW_ID.search(tag) and not re.search(r"\d{3,}", tag)]
        return tags[0] if tags else "events beyond their control"

    def narrate(self, world, event, *, tone: str = "plain",
                awards: dict[str, EpithetAward] | None = None) -> str:
        template = self.catalog["templates"].get(event.type)
        if template is None:
            slots = self._slots(world, event, awards)
            label = _EVENT_LABELS.get(event.type)
            if label is None:
                label = f"an event concerning {event.type.replace('_', ' ').lower()}"
            where = f" near {slots['place']}" if event.location else ""
            text = f"The records place {slots['actor']} at {label}{where}."
            if _RAW_ID.search(text):
                raise ValueError(f"Unsafe rendered chronicler text for {event.type}")
            return text
        candidates = [row for row in template["variants"] if row["tone"] == tone]
        if not candidates:
            candidates = template["variants"]
        context = {event.type.lower(), *(tag.lower() for tag in event.causes_tags)}
        for value in event.data.values():
            if isinstance(value, str):
                context.add(value.lower().replace("_", " "))
                context.update(part for part in value.lower().split("_") if part)
        overlap = [sum(tag.lower() in context for tag in row["context_tags"])
                   for row in candidates]
        best = max(overlap)
        selected = [row for row, score in zip(candidates, overlap) if score == best]
        import hashlib
        index = int.from_bytes(hashlib.sha256(event.id.encode()).digest()[:4], "big") % len(selected)
        variant = selected[index]
        slots = self._slots(world, event, awards)
        text = variant["text"].format(**slots)
        if _RAW_ID.search(text) or "{" in text or "}" in text:
            raise ValueError(f"Unsafe rendered chronicler text for {event.type}")
        return text

    def _is_significant(self, world, event) -> bool:
        return (event.type in self.catalog["templates"]
                and self.significance(world, event) >= self.catalog["significance"]["threshold"])

    def running_chronicle(self, world, *, tone: str = "plain") -> list[dict[str, Any]]:
        awards_list = self.epithet_awards(world)
        awards = {row.entity_id: row for row in awards_list}
        prose = []
        collapsed: dict[tuple, list[Any]] = defaultdict(list)
        low_types = set(self.catalog["significance"]["collapse_types"])
        annual_residual_counts: Counter[int] = Counter()
        for event in world.event_log:
            if self._is_significant(world, event):
                prose.append({"tick": event.tick, "text": self.narrate(
                    world, event, tone=tone, awards=awards), "event_ids": [event.id],
                    "significance": round(self.significance(world, event), 4)})
            elif event.type in low_types:
                actor = event.data.get("merchant_id", "") if event.type == "MARKET_TRADE" else ""
                location = event.location if event.type == "MARKET_TRADE" else None
                collapsed[(event.type, actor, location,
                           _year_for_tick(world, event.tick))].append(event)
            elif event.type not in {"WORLD_GENERATED", "ISLAND_ECONOMY_INITIALIZED",
                                    "CHARACTER_GENERATED", "SHIP_CREATED", "CREW_FOUNDED"}:
                year = _year_for_tick(world, event.tick)
                annual_residual_counts[year] += 1
        repeated_text: dict[str, dict[str, Any]] = {}
        unique_prose = []
        for entry in prose:
            if not entry.get("collapsed"):
                prior = repeated_text.get(entry["text"])
                if prior is not None:
                    prior["event_ids"].extend(entry["event_ids"])
                    prior["repeat_count"] = len(prior["event_ids"])
                    continue
                repeated_text[entry["text"]] = entry
            unique_prose.append(entry)
        for entry in unique_prose:
            if entry.get("repeat_count", 1) > 1:
                entry["text"] += f" It was recorded {entry['repeat_count']} times."
        prose = unique_prose
        for (event_type, actor_id, location_id, year), rows in sorted(
                collapsed.items(), key=lambda item: (item[0][0], item[0][1],
                                                     item[0][2] or "", item[0][3])):
            if len(rows) < 2:
                continue
            labels = self._registry(world)
            actor = labels.entity(actor_id, "local crews")
            place = labels.location(location_id)
            if event_type == "MARKET_TRADE":
                quantity = sum(row.data.get("quantity_milli", 0) for row in rows)
                actor = labels.entity(actor_id, "local merchants")
                text = (f"In year {year}, {actor} completed {len(rows)} trade runs near {place}, "
                        f"moving {quantity / 1000:g} units of goods.")
            elif event_type == "ISLAND_ECONOMY_UPDATED":
                text = f"In year {year}, island markets recorded {len(rows)} daily economic updates."
            elif event_type == "SHIP_SUPPLIES_CONSUMED":
                text = f"In year {year}, crews logged {len(rows)} shipboard supply draws."
            elif event_type == "DISEASE_CONTAINED":
                text = f"In year {year}, healers contained {len(rows)} local disease outbreaks."
            elif event_type == "STORM_PATH_UPDATED":
                text = f"In year {year}, storm tracks shifted {len(rows)} times across the chart."
            elif event_type == "BATTLE_ROUND":
                text = f"In year {year}, fleets fought {len(rows)} theater-combat rounds near {place}."
            elif event_type == "BATTLE_REINFORCEMENT":
                text = f"In year {year}, {len(rows)} scheduled reinforcements joined fleets near {place}."
            elif event_type == "FLEET_COMMAND_SET":
                text = f"In year {year}, fleet commanders issued {len(rows)} doctrine orders near {place}."
            else:
                title = event_type.replace("_", " ").lower()
                text = f"In year {year}, {len(rows)} {title} events were recorded near {place}."
            prose.append({"tick": rows[0].tick, "text": text,
                          "event_ids": [row.id for row in rows], "collapsed": True})
        for year, count in sorted(annual_residual_counts.items()):
            if count:
                year_ticks = world.config["time"]["days_per_year"] * world.config["time"]["hours_per_day"]
                prose.append({"tick": (year - 1) * year_ticks,
                              "text": f"The year {year} harbor books condensed {count} lesser "
                                      "event records into annual summaries.",
                              "event_ids": [], "collapsed": True, "event_count": count})
        return sorted(prose, key=lambda row: (row["tick"],
                                              row["event_ids"][0] if row["event_ids"] else ""))

    def story_threads(self, world, *, limit: int = 100, tone: str = "plain") -> list[dict[str, Any]]:
        """Return compact outcome-centered threads with explicit immutable event provenance."""
        if type(limit) is not int or limit < 1:
            raise ValueError("Story-thread limit must be a positive integer")
        threads = []
        for terminal in world.event_log:
            if terminal.type not in self._THREAD_TERMINALS:
                continue
            pending = [(parent_id, 1) for parent_id in terminal.caused_by]
            ancestors = {}
            while pending and len(ancestors) < 48:
                parent_id, depth = pending.pop(0)
                if parent_id in ancestors or depth > 12:
                    continue
                parent = world.event_log.get(parent_id)
                ancestors[parent_id] = parent
                pending.extend((ancestor_id, depth + 1) for ancestor_id in parent.caused_by)
            chain = sorted((*ancestors.values(), terminal), key=lambda event: (event.tick, event.id))
            steps = []
            for event in chain:
                if event.type in {"WORLD_GENERATED", "ISLAND_ECONOMY_INITIALIZED",
                                  "CHARACTER_GENERATED", "SHIP_CREATED", "CREW_FOUNDED"}:
                    continue
                if event.event_class == "routine" and event.type not in {
                        "AGENT_ACTION", "MARKET_TRADE", "DREAM_PROGRESS",
                        "RUMOR_SHARED", "TREASURE_RUMOR", "TREASURE_CLUE_SPREAD"}:
                    continue
                row = {"event_id": event.id, "tick": event.tick,
                       "event_type": event.type, "text": self.narrate(world, event, tone=tone),
                       "caused_by_event_ids": list(event.caused_by), "certainty": "recorded_fact"}
                if event.type in {"RUMOR_SHARED", "TREASURE_RUMOR", "TREASURE_CLUE_SPREAD"}:
                    row["certainty"] = "rumor"
                    if "confidence_permille" in event.data:
                        row["confidence_permille"] = event.data["confidence_permille"]
                    if "distorted" in event.data:
                        row["distorted"] = event.data["distorted"]
                steps.append(row)
            threads.append({"thread_id": terminal.id, "terminal_event_id": terminal.id,
                            "terminal_type": terminal.type,
                            "title": terminal.type.replace("_", " ").title(),
                            "steps": steps,
                            "evidence_event_ids": [row["event_id"] for row in steps]})
        return threads[-limit:]

    def rumor(self, world, event, *, rumor_seed: int = 0) -> dict[str, Any]:
        if type(rumor_seed) is not int:
            raise ValueError("rumor_seed must be an integer")
        # Hash-derived choice is local to this artifact and cannot perturb world RNG.
        import hashlib
        digest = hashlib.sha256(f"{rumor_seed}:{event.id}:rumor".encode()).digest()
        tone = "tavern" if digest[0] & 1 else "plain"
        text = self.narrate(world, event, tone=tone)
        distortions = ["the details may have grown in the telling", "so the dockside version goes"]
        if digest[1] & 1:
            text = text.rstrip(".") + ", though " + distortions[0] + "."
        else:
            text = "Rumor has it that " + text[0].lower() + text[1:]
        return {"source_event_id": event.id, "text": text, "tone": "tavern",
                "distorted": True}

    def bounty_posters(self, world) -> list[dict[str, Any]]:
        labels = self._registry(world)
        posters = []
        for entity_id, character in sorted(world.entities.items()):
            bounty = character.get("bounty", {})
            amount = bounty.get("amount", 0) if isinstance(bounty, dict) else 0
            if character.get("kind") != "character" or type(amount) is not int or amount <= 0:
                continue
            crimes = [event.type.replace("_", " ").lower() for event in world.event_log.by_entity(entity_id)
                      if event.type in {"TREATY_BREACHED", "SHIP_SUNK", "ENCOUNTER_RESOLVED"}]
            posters.append({"name": labels.entity(entity_id), "epithet": character.get("epithet"),
                            "amount_silver": amount, "crimes": sorted(set(crimes)),
                            "sketch_tags": sorted(set(character.get("flags", [])))})
        return sorted(posters, key=lambda row: (-row["amount_silver"], row["name"]))

    def biography(self, world, entity_id: str) -> dict[str, Any]:
        character = world.entities.get(entity_id) or world.archive.get(entity_id)
        if character is None and entity_id in world.lod.named_records:
            character = world.lod._materialize(world.lod.named_records[entity_id])
        if not character or character.get("kind") != "character":
            raise ValueError(f"Unknown character: {entity_id}")
        labels = self._registry(world)
        awards = {row.entity_id: row for row in self.epithet_awards(world)}
        events = [event for event in world.event_log.by_entity(entity_id)
                  if self._is_significant(world, event)]
        return {"name": self._participant_name(labels, [entity_id], awards, "unknown"),
                "role": character.get("role"), "birthplace": labels.location(character.get("birthplace")),
                 "alive": entity_id in world.entities or entity_id in world.lod.named_records,
                "epithet_awards": [row.to_record() for row in awards.values()
                                   if row.entity_id == entity_id],
                "events": [{"tick": event.tick, "text": self.narrate(
                    world, event, awards=awards), "event_id": event.id} for event in events]}

    def faction_history(self, world, faction_id: str) -> dict[str, Any]:
        faction = (world.politics.factions.get(faction_id)
                   or world.politics.archived_factions.get(faction_id))
        if not faction:
            raise ValueError(f"Unknown active faction: {faction_id}")
        name = faction.get("name", "a faction")
        events = [event for event in world.event_log if faction_id in
                  {row["entity_id"] for row in (*event.actors, *event.targets)}
                  or faction_id in event.data.values()]
        return {"name": name, "events": [{"tick": event.tick,
                "text": self.narrate(world, event), "event_id": event.id}
                for event in events if self._is_significant(world, event)]}

    def legends(self, world, *, limit: int = 20) -> list[dict[str, Any]]:
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        candidates = []
        for entity_id, row in _character_rows(world).items():
            if row.get("kind") != "character":
                continue
            deeds = world.event_log.by_entity(entity_id)
            fame = sum(self.significance(world, event) for event in deeds)
            if fame <= 0:
                continue
            candidates.append({"name": row.get("display_name"), "epithet": row.get("epithet"),
                                "fame": round(fame, 4),
                                "alive": entity_id in world.entities or entity_id in world.lod.named_records})
        return sorted(candidates, key=lambda row: (-row["fame"], row["name"]))[:limit]

    def almanac(self, world) -> list[dict[str, Any]]:
        per_year: dict[int, list[Any]] = defaultdict(list)
        year_ticks = (world.config["time"]["days_per_year"]
                      * world.config["time"]["hours_per_day"]
                      // world.config["time"]["hours_per_tick"])
        for event in world.event_log:
            per_year[_year_for_tick(world, event.tick)].append(event)
        return [{"year": year, "events": len(events),
                 "deaths": sum(row.type == "CHARACTER_DIED" for row in events),
                 "encounters": sum(row.type == "ENCOUNTER_RESOLVED" for row in events),
                 "wars": sum(row.type == "WAR_DECLARED" for row in events),
                 "discoveries": sum(row.type == "TREASURE_FOUND" for row in events),
                 "headline": self.narrate(world, max(events, key=lambda row: self.significance(world, row)))
                 if events else "No events were recorded."}
                for year, events in sorted(per_year.items())]

    def obituaries(self, world) -> list[dict[str, Any]]:
        labels = self._registry(world)
        results = []
        for event in world.event_log.by_type("CHARACTER_DIED"):
            actor_id = event.actors[0]["entity_id"] if event.actors else ""
            row = world.archive.get(actor_id, {})
            results.append({"name": labels.entity(actor_id), "tick": event.tick,
                            "cause": self.narrate(world, event),
                            "age_years": row.get("age_years"), "event_id": event.id})
        return results

    def weekly_digest(self, world, *, end_tick: int | None = None) -> dict[str, Any]:
        end = world.clock.tick if end_tick is None else end_tick
        ticks_per_day = (world.config["time"]["hours_per_day"]
                         // world.config["time"]["hours_per_tick"])
        if type(end) is not int or not 0 <= end <= world.clock.tick:
            raise ValueError("Digest end tick must lie within the simulated timeline")
        start = max(0, end - 7 * ticks_per_day + 1)
        events = [event for event in world.event_log if start <= event.tick <= end]
        ranked = sorted(events, key=lambda event: (-self.significance(world, event), event.tick, event.id))
        return {"start_tick": start, "end_tick": end, "event_count": len(events),
                "headlines": [{"event_id": event.id, "text": self.narrate(world, event)}
                              for event in ranked[:5]]}

    def simulation_report(self, world) -> dict[str, Any]:
        """Summarize observed records/state; absent events remain zero, never quota-filled."""
        events = list(world.event_log)
        by_type = Counter(event.type for event in events)
        by_class = Counter(event.event_class for event in events)
        plans = world.decisions.agents.values()
        plan_rows = [state["plan"] for state in plans if state.get("plan")]
        plan_status = Counter(plan["status"] for plan in plan_rows)
        plan_status["blocked_steps"] = sum(
            step.get("status") == "blocked" for plan in plan_rows for step in plan["steps"])
        plan_status["failed_steps"] = sum(
            step.get("status") == "failed" for plan in plan_rows for step in plan["steps"])
        treasure_chains = []
        for found in world.event_log.by_type("TREASURE_FOUND"):
            pending = list(found.caused_by)
            seen = set()
            chain = []
            while pending and len(seen) < 64:
                event_id = pending.pop(0)
                if event_id in seen:
                    continue
                seen.add(event_id)
                event = world.event_log.get(event_id)
                chain.append(event)
                pending.extend(event.caused_by)
            chain.sort(key=lambda event: (event.tick, event.id))
            treasure_chains.append({"discovery_event_id": found.id,
                                    "treasure_id": found.data.get("treasure_id"),
                                    "clue_event_ids": [event.id for event in chain
                                                       if event.type in {
                                                           "TREASURE_RUMOR", "TREASURE_CLUE_SPREAD",
                                                           "TREASURE_CLUE_LEARNED"}],
                                    "evidence_event_ids": [event.id for event in chain] + [found.id]})
        conflict_types = {
            "ROUTE_BLOCKADED", "SHORTAGE_RELIEF_NEGOTIATED", "PROFITABLE_TRADE_RIVALRY",
            "CONTRABAND_SEIZED", "TREATY_BREACHED", "TREATY_OBLIGATION_FULFILLED",
            "TREATY_TRANSITION",
        }
        conflict_counts = {event_type: by_type[event_type] for event_type in sorted(conflict_types)}
        encounter_outcomes = Counter(str(event.data.get("outcome", "unknown"))
                                     for event in world.event_log.by_type("ENCOUNTER_RESOLVED"))
        battle_outcomes = Counter(str(event.data.get("outcome", "unknown"))
                                  for event in world.event_log.by_type("BATTLE_CONCLUDED"))
        exchanges = list(world.event_log.by_type("PERSONAL_EXCHANGE"))
        gear_morale = Counter()
        for event in exchanges:
            morale = event.data.get("attacker_morale")
            if type(morale) is not int:
                morale_band = "unknown"
            elif morale < 34:
                morale_band = "low"
            elif morale < 67:
                morale_band = "mid"
            else:
                morale_band = "high"
            key = (event.data.get("weapon_id", "unknown"),
                   event.data.get("armor_id", "unknown"), morale_band)
            gear_morale[key] += 1
        relative_attributes = Counter()
        relative_stats: dict[str, Counter[str]] = defaultdict(Counter)
        for event in world.event_log.by_type("PERSONAL_INITIATIVE"):
            speeds = event.data.get("speed", ())
            if isinstance(speeds, (list, tuple)) and len(speeds) == 2 \
                    and all(type(value) is int for value in speeds):
                delta = speeds[0] - speeds[1]
                relative_attributes["first_faster" if delta > 0 else
                                    "second_faster" if delta < 0 else "equal_speed"] += 1
        for event in world.event_log.by_type("PERSONAL_COMBAT_STARTED"):
            profiles = event.data.get("profiles_before", ())
            if not isinstance(profiles, (list, tuple)) or len(profiles) != 2:
                continue
            for stat in ("combat", "speed", "strength", "endurance", "toughness", "willpower"):
                values = [profile.get("stats", {}).get(stat, {}).get("effective")
                          for profile in profiles if isinstance(profile, Mapping)]
                if len(values) != 2 or any(type(value) is not int for value in values):
                    continue
                relative_stats[stat]["first_higher" if values[0] > values[1] else
                                     "second_higher" if values[0] < values[1] else "equal"] += 1
        personal_duels = []
        for duel in world.event_log.by_type("LEGEND_DUEL"):
            start_id = duel.data.get("personal_combat_started_event_id")
            if not isinstance(start_id, str):
                continue
            start = world.event_log.get(start_id)
            profiles = start.data.get("profiles_before", ())
            stat_advantages = {}
            if isinstance(profiles, (list, tuple)) and len(profiles) == 2:
                for stat in ("combat", "speed", "strength", "endurance", "toughness", "willpower"):
                    values = [profile.get("stats", {}).get(stat, {}).get("effective")
                              for profile in profiles if isinstance(profile, Mapping)]
                    if len(values) == 2 and all(type(value) is int for value in values):
                        stat_advantages[stat] = ("first" if values[0] > values[1] else
                                                 "second" if values[0] < values[1] else "even")
            exchange_bands = []
            for exchange_id in duel.data.get("personal_exchange_event_ids", ()):
                exchange = world.event_log.get(exchange_id)
                morale = exchange.data.get("attacker_morale")
                morale_band = ("unknown" if type(morale) is not int else
                               "low" if morale < 34 else "mid" if morale < 67 else "high")
                exchange_bands.append({"weapon_id": exchange.data.get("weapon_id", "unknown"),
                                       "armor_id": exchange.data.get("armor_id", "unknown"),
                                       "attacker_morale_band": morale_band,
                                       "hit": exchange.data.get("hit")})
            personal_duels.append({"duel_event_id": duel.id,
                                   "outcome": duel.data.get("outcome", "unknown"),
                                   "winner_side": duel.data.get("winner_side"),
                                   "relative_attributes": stat_advantages,
                                   "exchange_bands": exchange_bands})
        rival_pairs = Counter()
        for event in world.event_log.by_type("FEUD_RAID_ATTEMPTED"):
            raider = next((row.get("entity_id") for row in event.actors
                           if row.get("role") == "raider"), None)
            rival = next((row.get("entity_id") for row in event.targets
                          if row.get("role") == "raided_rival"), None)
            if raider and rival:
                rival_pairs[tuple(sorted((raider, rival)))] += 1
        open_obligations = [
            {"treaty_key": key, "event_id": row["event_id"],
             "obligated_faction": row["obligated_faction"]}
            for key, treaty in sorted(world.politics.treaties.items())
            for row in treaty["obligations"] if row["status"] == "open"]
        stranded = [crew_id for crew_id, crew in world.crew_ships.crews.items()
                    if not crew["ship_ids"] and crew.get("stranded_location")]
        recovered = [event.id for event in events
                     if event.type == "CREW_EMPLOYMENT_ACCEPTED"
                     and "stranded_crew" in event.causes_tags]
        return {
            "event_counts": {"by_type": dict(sorted(by_type.items())),
                             "by_class": dict(sorted(by_class.items())),
                             "total": len(events)},
            "dream_plans": {"observed_transitions": {
                event_type: by_type[event_type] for event_type in (
                    "DREAM_PLAN_COMPLETED", "DREAM_PLAN_FAILED", "DREAM_PLAN_ABANDONED")},
                "current_state_counts": dict(sorted(plan_status.items()))},
            "treasure": {"clue_events": (by_type["TREASURE_CLUE_LEARNED"]
                                            + by_type["TREASURE_CLUE_SPREAD"]),
                         "rumor_events": by_type["TREASURE_RUMOR"],
                         "discovery_events": by_type["TREASURE_FOUND"],
                         "undiscovered_sites": sum(row["discovered_event_id"] is None
                                                   for row in world.environment.treasures.values()),
                         "discovery_chains": treasure_chains},
            "trade_conflicts": {"event_counts": conflict_counts,
                                "blockades_open": len(world.economy.blockades),
                                "treaty_obligations_open": len(open_obligations)},
            "combat": {"encounter_outcomes": dict(sorted(encounter_outcomes.items())),
                       "fleet_battle_outcomes": dict(sorted(battle_outcomes.items())),
                       "personal_duels": personal_duels,
                       "relative_attribute_bands": {
                           stat: dict(sorted(counts.items()))
                           for stat, counts in sorted(relative_stats.items())},
                       "personal_exchange_bands": [
                           {"weapon_id": weapon, "armor_id": armor,
                            "attacker_morale_band": morale_band, "exchange_count": count}
                           for (weapon, armor, morale_band), count
                           in sorted(gear_morale.items())],
                       "relative_speed_bands": dict(sorted(relative_attributes.items())),
                       "limitation": "Personal-combat relative stat bands, attacker morale, weapon, armor, and initiative speed are descriptive event counts; the current event model does not join every band to a validated duel winner, so these are not causal win-rate estimates."},
            "public_deeds": {"renown_awards": by_type["PUBLIC_RENOWN_EARNED"],
                             "legend_recognitions": by_type["LEGEND_RECOGNIZED"],
                             "rookie_promotions": by_type["ROOKIE_PROMOTED"],
                             "deed_event_ids": [event.id for event in world.event_log.by_type(
                                 "PUBLIC_RENOWN_EARNED")],
                             "legend_recognition_event_ids": [event.id for event in
                                                               world.event_log.by_type(
                                                                   "LEGEND_RECOGNIZED")],
                             "deeds_by_kind": dict(sorted(Counter(
                                 event.data.get("deed_kind", "unknown")
                                 for event in world.event_log.by_type("PUBLIC_RENOWN_EARNED")
                             ).items()))},
            "rivalry": {"attempts": by_type["FEUD_RAID_ATTEMPTED"],
                        "repeat_pairs": [{"character_ids": list(pair), "attempts": count}
                                         for pair, count in sorted(rival_pairs.items()) if count > 1]},
            "open_obligations": open_obligations,
            "stranded_crew_recovery": {"currently_stranded_crew_ids": sorted(stranded),
                                        "recovery_events": recovered},
        }

    def generate_all(self, world, *, tone: str = "plain") -> dict[str, Any]:
        before = (world.clock.tick, len(world.event_log), world.rng.snapshot(), world.ids.snapshot())
        awards = self.epithet_awards(world)
        artifacts = {
            "chronicle": self.running_chronicle(world, tone=tone),
            "story_threads": self.story_threads(world, tone=tone),
            "simulation_report": self.simulation_report(world),
            "rumors": [self.rumor(world, event, rumor_seed=world.seed if type(world.seed) is int else 0)
                       for event in world.event_log if self._is_significant(world, event)],
            "bounty_posters": self.bounty_posters(world),
                 "biographies": [self.biography(world, entity_id) for entity_id, row in sorted(
                 _character_rows(world).items()) if row.get("kind") == "character"],
            "faction_histories": [self.faction_history(world, faction_id)
                                  for faction_id in sorted(world.politics.factions
                                                           | world.politics.archived_factions)],
            "epithet_awards": [row.to_record() for row in awards],
            "legends": self.legends(world),
            "almanac": self.almanac(world),
            "obituaries": self.obituaries(world),
            "weekly_digest": self.weekly_digest(world),
        }
        for value in _prose_strings(artifacts):
            if _RAW_ID.search(value):
                raise ValueError("Chronicler artifact leaked an internal ID")
        after = (world.clock.tick, len(world.event_log), world.rng.snapshot(), world.ids.snapshot())
        if after != before:
            raise RuntimeError("Chronicler must not mutate simulation state")
        return artifacts


def _prose_strings(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"text", "headline", "cause"} and isinstance(item, str):
                yield item
            else:
                yield from _prose_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _prose_strings(item)
