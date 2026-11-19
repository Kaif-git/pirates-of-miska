"""Data-driven utility action scoring and bounded per-agent choice state."""

from __future__ import annotations

import hashlib
import heapq
import json
from collections import Counter, deque
from pathlib import Path
from typing import Any

from sim.core.ownership import ship_is_commandable


ACTIONS_PATH = Path(__file__).resolve().parents[1] / "data" / "actions.json"
_TRAITS = {"ambition", "honor", "greed", "loyalty", "cruelty", "curiosity",
           "pride", "paranoia", "boldness", "sociability"}
_PRECONDITIONS = {"living_character_with_dream", "crew_at_island_below_full_morale",
                   "captain_with_profitable_trade_route", "living_character_with_grudge"}
_HANDLERS = {"advance_dream", "rest_crew", "dispatch_trade", "pursue_grudge"}
_DREAM_KINDS = {
    "find_treasure", "become_title", "destroy_entity", "restore_house", "chart_region",
    "free_island", "become_wealthiest", "defeat_character", "find_person",
    "protect_entity", "reach_legend_tier",
}
_LOOP_RULES = {"loop_max_sequence_length", "loop_repeat_limit", "loop_penalty",
               "grudge_target_cooldown_days"}


def load_action_data(path: str | Path = ACTIONS_PATH) -> tuple[dict[str, Any], str]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load action catalog {path}: {exc}") from exc
    validate_action_data(data)
    digest = _catalog_digest(data, load_ai_rules_data()[0])
    return data, digest


AI_RULES_PATH = Path(__file__).resolve().parents[1] / "data" / "ai_rules.json"


def _catalog_digest(actions: dict[str, Any], ai_rules: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        {"actions": actions, "ai_rules": ai_rules}, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def load_ai_rules_data(path: str | Path = AI_RULES_PATH) -> tuple[dict[str, Any], str]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load AI rules {path}: {exc}") from exc
    validate_ai_rules_data(data)
    digest = hashlib.sha256(json.dumps(
        data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    return data, digest


def validate_ai_rules_data(data: Any) -> None:
    if (not isinstance(data, dict) or set(data) != {
            "schema_version", "belief_rules", "plan_templates", "wake_event_types"}
            or type(data["schema_version"]) is not int or data["schema_version"] != 1):
        raise ValueError("Invalid AI rules catalog schema")
    rules = data["belief_rules"]
    rule_keys = {
        "daily_confidence_decay_permille", "share_base_basis_points",
        "share_sociability_basis_points_per_point", "share_confidence_loss_permille",
        "distortion_basis_points", "max_distortion_permille",
        "belief_error_threshold_permille", "minimum_confidence_permille",
        "max_beliefs_per_agent",
    }
    if (not isinstance(rules, dict) or set(rules) != rule_keys
            or any(type(value) is not int or value < 0 for value in rules.values())
            or rules["daily_confidence_decay_permille"] > 1000
            or rules["share_base_basis_points"] > 10000
            or rules["share_sociability_basis_points_per_point"] > 100
            or rules["share_confidence_loss_permille"] > 1000
            or rules["distortion_basis_points"] > 10000
            or rules["max_distortion_permille"] > 1000
            or rules["belief_error_threshold_permille"] > 1000
            or rules["minimum_confidence_permille"] > 1000
            or rules["max_beliefs_per_agent"] < 1):
        raise ValueError("Invalid belief propagation rules")
    plans = data["plan_templates"]
    if (not isinstance(plans, dict) or set(plans) != _DREAM_KINDS
            or any(not isinstance(steps, list) or not steps
                   or any(not isinstance(step, str) or not step for step in steps)
                   for steps in plans.values())):
        raise ValueError("Dream plan templates must cover all supported dreams")
    wake_types = data["wake_event_types"]
    if (not isinstance(wake_types, list) or not wake_types
            or any(not isinstance(kind, str) or not kind
                   or kind.upper() != kind or not kind.replace("_", "").isalnum()
                   for kind in wake_types)
            or len(set(wake_types)) != len(wake_types)):
        raise ValueError("Invalid decision wake event types")


def validate_action_data(data: Any) -> None:
    if (not isinstance(data, dict) or set(data) != {"schema_version", "rules", "actions"}
            or type(data["schema_version"]) is not int or data["schema_version"] != 1):
        raise ValueError("Invalid action catalog schema")
    rules = data["rules"]
    rule_keys = {"top_k", "recency_window_ticks", "recency_penalty_per_repeat",
                 "noise_max_points", "selection_temperature_base",
                 "selection_temperature_boldness", *_LOOP_RULES, "debug_history_limit"}
    if (not isinstance(rules, dict) or set(rules) != rule_keys
            or any(type(value) is not int or value < 0 for value in rules.values())
            or rules["top_k"] < 1 or rules["top_k"] > 10
            or rules["recency_window_ticks"] < 1
            or rules["selection_temperature_base"] < 1
            or rules["grudge_target_cooldown_days"] < 1
            or rules["debug_history_limit"] < 1):
        raise ValueError("Invalid action scoring rules")
    actions = data["actions"]
    action_keys = {"precondition", "handler", "time_cost_ticks", "silver_cost",
                   "risk_permille", "expected_gain", "expected_loss", "want_alignment",
                   "dream_kinds", "plan_steps", "trait_weights", "grudge_weight",
                   "recency_penalty"}
    if not isinstance(actions, dict) or not actions:
        raise ValueError("Action definitions must be a nonempty object")
    for action_id, action in actions.items():
        if (not isinstance(action_id, str) or not action_id or not isinstance(action, dict)
                or set(action) != action_keys
                or not isinstance(action["precondition"], str) or not action["precondition"]
                or not isinstance(action["handler"], str) or not action["handler"]
                or action["precondition"] not in _PRECONDITIONS
                or action["handler"] not in _HANDLERS
                 or any(type(action[key]) is not int or action[key] < 0
                        for key in ("time_cost_ticks", "silver_cost", "risk_permille",
                                    "expected_gain", "expected_loss", "grudge_weight",
                                    "recency_penalty"))
                or action["time_cost_ticks"] < 1 or action["risk_permille"] > 1000
                or not isinstance(action["want_alignment"], dict)
                or any(not isinstance(key, str) or not key
                       or type(value) is not int or not 0 <= value <= 1000
                       for key, value in action["want_alignment"].items())
                or not isinstance(action["dream_kinds"], list)
                or any(not isinstance(value, str) or not value for value in action["dream_kinds"])
                or set(action["dream_kinds"]) - _DREAM_KINDS
                or not isinstance(action["plan_steps"], list)
                or any(not isinstance(value, str) or not value for value in action["plan_steps"])
                or not isinstance(action["trait_weights"], dict)
                or set(action["trait_weights"]) - _TRAITS
                or any(type(value) is not int or not -100 <= value <= 100
                       for value in action["trait_weights"].values())):
            raise ValueError(f"Invalid action definition: {action_id}")


class DecisionSystem:
    """Scores feasible actions, samples among top candidates, and records private traces."""

    def __init__(self, *, digest: str, agents: dict[str, dict[str, Any]] | None = None,
                 debug_history: list[dict[str, Any]] | None = None,
                 pending_wakes: list[dict[str, Any]] | None = None,
                 event_cursor: int = 0, last_updated_day: int = 0) -> None:
        self.data, _ = load_action_data()
        self.ai_data, ai_digest = load_ai_rules_data()
        actual_digest = _catalog_digest(self.data, self.ai_data)
        if digest != actual_digest:
            raise ValueError("Action catalog changed since snapshot creation")
        self.digest = digest
        self.agents = json.loads(json.dumps(agents or {}, sort_keys=True))
        self.debug_history = json.loads(json.dumps(debug_history or [], sort_keys=True))
        self._pending_wakes_by_agent: dict[str, deque[dict[str, Any]]] = {}
        self._pending_wake_ids_by_agent: dict[str, set[str]] = {}
        self._pending_wake_heap: list[tuple[int, str]] = []
        self._pending_wake_scheduled: set[str] = set()
        self.pending_wakes = json.loads(json.dumps(pending_wakes or [], sort_keys=True))
        if (type(event_cursor) is not int or event_cursor < 0
                or type(last_updated_day) is not int or last_updated_day < 0):
            raise ValueError("Invalid decision event cursor or update day")
        self.event_cursor = event_cursor
        self.last_updated_day = last_updated_day
        self.validate_state()

    @property
    def pending_wakes(self) -> list[dict[str, Any]]:
        """Flatten the runtime per-agent queues into the compatible snapshot shape."""
        return [wake for agent_id in sorted(self._pending_wakes_by_agent)
                for wake in self._pending_wakes_by_agent[agent_id]]

    @pending_wakes.setter
    def pending_wakes(self, rows: list[dict[str, Any]]) -> None:
        if not isinstance(rows, list):
            raise ValueError("Pending wakes must be a list")
        queues: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            if (not isinstance(row, dict) or set(row) != {"agent_id", "event_id", "reason"}
                    or not isinstance(row["agent_id"], str)
                    or not isinstance(row["event_id"], str)):
                raise ValueError("Invalid pending agent decision wake")
            queues.setdefault(row["agent_id"], []).append(row)
        self._pending_wakes_by_agent = {}
        self._pending_wake_ids_by_agent = {}
        self._pending_wake_heap = []
        self._pending_wake_scheduled = set()
        for agent_id, wakes in queues.items():
            wakes.sort(key=lambda wake: wake["event_id"])
            self._pending_wakes_by_agent[agent_id] = deque(wakes)
            self._pending_wake_ids_by_agent[agent_id] = {
                wake["event_id"] for wake in wakes
            }
            state = self.agents.get(agent_id, {})
            due_tick = state.get("next_decision_tick", 0)
            if type(due_tick) is not int:
                due_tick = 0
            self._pending_wake_heap.append((due_tick, agent_id))
            self._pending_wake_scheduled.add(agent_id)
        heapq.heapify(self._pending_wake_heap)

    def _schedule_pending_wake_agent(self, agent_id: str, world) -> None:
        """Index the next decision tick for a queued agent without coalescing wakes."""
        if agent_id in self._pending_wake_scheduled:
            return
        state = self.agents.get(agent_id)
        due_tick = state.get("next_decision_tick") if state else world.clock.tick
        if type(due_tick) is not int:
            due_tick = world.clock.tick
        heapq.heappush(self._pending_wake_heap, (due_tick, agent_id))
        self._pending_wake_scheduled.add(agent_id)

    def on_agent_terminal(self, world, agent_id: str) -> None:
        """Drop queued wakes at the terminal transition without polling every tick."""
        agent = world.entities.get(agent_id) or world.archive.get(agent_id)
        if not self._terminal_agent(world, agent_id, agent):
            return
        queue = self._pending_wakes_by_agent.pop(agent_id, None)
        self._pending_wake_ids_by_agent.pop(agent_id, None)
        self._pending_wake_scheduled.discard(agent_id)
        if queue:
            for wake in queue:
                self._record_stale_wakeup(world, agent_id, wake["event_id"], agent)

    @classmethod
    def create(cls) -> "DecisionSystem":
        action_data, _ = load_action_data()
        ai_data, _ = load_ai_rules_data()
        digest = _catalog_digest(action_data, ai_data)
        return cls(digest=digest)

    @classmethod
    def restore(cls, snapshot: dict[str, Any]) -> "DecisionSystem":
        if (not isinstance(snapshot, dict)
                or set(snapshot) != {"digest", "agents", "debug_history", "pending_wakes",
                                    "event_cursor", "last_updated_day"}
                or not isinstance(snapshot.get("agents"), dict)
                or not isinstance(snapshot.get("debug_history"), list)
                or not isinstance(snapshot.get("pending_wakes"), list)):
            raise ValueError("Invalid decision system snapshot")
        return cls(digest=snapshot["digest"], agents=snapshot["agents"],
                   debug_history=snapshot["debug_history"],
                   pending_wakes=snapshot["pending_wakes"],
                   event_cursor=snapshot["event_cursor"],
                   last_updated_day=snapshot["last_updated_day"])

    def candidates(self, world, agent_id: str) -> list[str]:
        agent = world.entities.get(agent_id)
        if not agent or agent.get("kind") != "character" or agent.get("death_tick") is not None:
            return []
        result = []
        for action_id, definition in sorted(self.data["actions"].items()):
            if (self._eligible(world, agent, definition["precondition"])
                    and self._can_afford_action(world, agent, action_id, definition)):
                result.append(action_id)
        return result

    def score_actions(self, world, agent_id: str, *, caused_by: str) -> list[dict[str, Any]]:
        agent = world.entities.get(agent_id)
        if not agent or agent.get("kind") != "character" or agent.get("death_tick") is not None:
            raise ValueError(f"Decision maker is not a living character: {agent_id}")
        world.event_log.get(caused_by)
        state = self.agents.get(agent_id, self._new_agent_state())
        history = state["history"]
        cutoff = world.clock.tick - self.data["rules"]["recency_window_ticks"]
        recent = Counter(row["action_id"] for row in history if row["tick"] >= cutoff)
        rng = world.rng.stream(f"decision:{agent_id}")
        rules = self.data["rules"]
        current_plan_step = None
        plan = state.get("plan")
        if plan and plan["status"] == "active" and plan["current_step"] < len(plan["steps"]):
            current_plan_step = plan["steps"][plan["current_step"]]
        scores = []
        for action_id in self.candidates(world, agent_id):
            definition = self.data["actions"][action_id]
            traits = agent["traits"]
            greed = traits["greed"]
            boldness = traits["boldness"]
            curiosity = traits["curiosity"]
            want_score = sum(
                want.get("urgency_permille", 0)
                * definition["want_alignment"].get(want.get("kind"), 0) // 1000
                for want in agent.get("wants", [])
            )
            dream = agent.get("dream", {})
            dream_score = (definition["expected_gain"] // 2
                           if dream.get("kind") in definition["dream_kinds"] else 0)
            plan_score = 18 if current_plan_step in definition["plan_steps"] else 0
            gain_score = definition["expected_gain"] * (500 + greed * 10) // 1000
            loss_score = definition["expected_loss"] * (1500 - boldness * 10) // 1000
            personality_score = sum(
                (traits[trait] - 50) * weight // 10
                for trait, weight in definition["trait_weights"].items()
            )
            grudge_target = self._grudge_target_id(world, agent)
            grudge_bonus = (agent["opinions"][grudge_target]["grievance"]
                            * definition["grudge_weight"] // 100
                            if grudge_target is not None else 0)
            costs = (definition["time_cost_ticks"] // 24 + definition["silver_cost"]
                     + definition["risk_permille"] // 10)
            repeat_count = recent[action_id]
            loop_repetitions = self._loop_repetitions(
                [row["action_id"] for row in history], action_id, rules["loop_max_sequence_length"])
            loop_penalty = (rules["loop_penalty"]
                            if loop_repetitions >= rules["loop_repeat_limit"] else 0)
            recency_penalty = (
                definition["recency_penalty"] * repeat_count * (500 + curiosity * 10) // 1000
            )
            noise_bound = (rules["noise_max_points"] * (500 + curiosity * 10) // 1000)
            noise = rng.randint(-noise_bound, noise_bound) if noise_bound else 0
            components = {
                "wants": want_score, "dream": dream_score, "plan": plan_score,
                "expected_gain": gain_score,
                "expected_loss": -loss_score, "personality": personality_score,
                "grudge": grudge_bonus,
                "costs": -costs, "recency": -recency_penalty,
                "repeated_sequence": -loop_penalty, "noise": noise,
            }
            scores.append({"action_id": action_id, "score": sum(components.values()),
                           "components": components, "recent_repeats": repeat_count,
                           "loop_repetitions": loop_repetitions,
                           "noise_bound": noise_bound,
                           "cost": {key: definition[key] for key in
                                    ("time_cost_ticks", "silver_cost", "risk_permille")},
                           "outcome": {"expected_gain": definition["expected_gain"],
                                       "expected_loss": definition["expected_loss"]}})
        return sorted(scores, key=lambda row: (-row["score"], row["action_id"]))

    def _loop_repetitions(self, history: list[str], candidate: str, maximum: int) -> int:
        """Count repeats of the cycle that candidate would continue, up to its configured length."""
        for length in range(1, min(maximum, len(history) // 2) + 1):
            prior = history[-2 * length:-length]
            latest = history[-length:]
            if prior == latest and candidate == latest[0]:
                repetitions = 2
                cursor = len(history) - 3 * length
                while cursor >= 0 and history[cursor:cursor + length] == latest:
                    repetitions += 1
                    cursor -= length
                return repetitions
        return 0

    def decide(self, world, agent_id: str, *, caused_by: str) -> dict[str, Any] | None:
        world.event_log.get(caused_by)
        if agent_id in self.agents and world.clock.tick < self.agents[agent_id]["next_decision_tick"]:
            return None
        self.ensure_dream_plan(world, agent_id, caused_by=caused_by)
        scored = self.score_actions(world, agent_id, caused_by=caused_by)
        diagnostics = getattr(world, "decision_score_diagnostics", None)
        if diagnostics is None:
            diagnostics = {"by_tier": {}, "by_action": {}, "scored_decisions": 0}
            world.decision_score_diagnostics = diagnostics
        agent_tier = world.entities[agent_id].get("tier", "unknown")
        diagnostics["scored_decisions"] += 1
        diagnostics["by_tier"][agent_tier] = diagnostics["by_tier"].get(agent_tier, 0) + 1
        for row in scored:
            action_id = row["action_id"]
            by_action = diagnostics["by_action"].setdefault(
                action_id, {"candidates_scored": 0, "decisions_chosen": 0})
            by_action["candidates_scored"] += 1
        if not scored:
            return None
        top = scored[:self.data["rules"]["top_k"]]
        temperature = (self.data["rules"]["selection_temperature_base"]
                       + world.entities[agent_id]["traits"]["boldness"]
                       * self.data["rules"]["selection_temperature_boldness"])
        best_score = top[0]["score"]
        weights = [max(1, 1_000_000 // (
            1 + max(0, best_score - row["score"]) * 1000 // max(1, temperature)
        )) for row in top]
        rng = world.rng.stream(f"decision:{agent_id}")
        pick = rng.randbelow(sum(weights))
        chosen = top[-1]
        for row, weight in zip(top, weights):
            if pick < weight:
                chosen = row
                break
            pick -= weight
        state = self.agents.setdefault(agent_id, self._new_agent_state())
        decision_number = state["decision_count"] + 1
        choice = {
            "agent_id": agent_id, "action_id": chosen["action_id"],
            "decision_number": decision_number, "tick": world.clock.tick,
            "caused_by": caused_by, "score": chosen["score"],
            "top_candidates": [{**row, "selection_weight": weight}
                               for row, weight in zip(top, weights)],
        }
        state["decision_count"] = decision_number
        state["last_action_id"] = chosen["action_id"]
        state["last_decision_tick"] = world.clock.tick
        state["next_decision_tick"] = world.clock.tick + max(1, chosen["cost"]["time_cost_ticks"])
        state["history"].append({"action_id": chosen["action_id"], "tick": world.clock.tick})
        diagnostics["by_action"][chosen["action_id"]]["decisions_chosen"] += 1
        state["history"] = state["history"][-50:]
        trace = {**choice, "score_components": chosen["components"]}
        self.debug_history.append(trace)
        self.debug_history = self.debug_history[-self.data["rules"]["debug_history_limit"]:]
        return choice

    def execute(self, world, choice: dict[str, Any]) -> str:
        if (not isinstance(choice, dict) or set(choice) != {
                "agent_id", "action_id", "decision_number", "tick", "caused_by",
                "score", "top_candidates"}):
            raise ValueError("Invalid agent action choice")
        agent_id = choice["agent_id"]
        agent = world.entities.get(agent_id)
        if not agent or agent.get("kind") != "character" or agent.get("death_tick") is not None:
            raise ValueError("Cannot execute an action for a nonliving character")
        state = self.agents.get(agent_id)
        if (not state or choice["decision_number"] != state["decision_count"]
                or choice["action_id"] != state["last_action_id"]
                or state["last_executed_decision_number"] == choice["decision_number"]):
            raise ValueError("Action choice is stale or has already executed")
        definition = self.data["actions"].get(choice["action_id"])
        if (definition is None or not self._eligible(world, agent, definition["precondition"])
                or not self._can_afford_action(world, agent, choice["action_id"], definition)):
            raise ValueError("Action preconditions no longer hold")
        world.event_log.get(choice["caused_by"])
        location = world.location_of(agent_id)
        grudge_target_id = (self._grudge_target_id(world, agent)
                            if definition["handler"] == "pursue_grudge" else None)
        parents = [choice["caused_by"]]
        if grudge_target_id is not None:
            parents.extend(world.memories.grudge_evidence(world, agent_id, grudge_target_id))
        parents = list(dict.fromkeys(parents))
        tags = ["agent_decision", f"action:{choice['action_id']}"]
        if grudge_target_id is not None:
            tags.append("grudge")
        raid_ship_id = None
        crew_id = agent.get("crew_id")
        cost = definition["silver_cost"]
        if definition["handler"] == "pursue_grudge":
            if grudge_target_id is None:
                raise ValueError("No living reachable grievance target remains for this action")
            raid_ship_id = self._grudge_ship_for_target(world, agent, grudge_target_id)
            if raid_ship_id is None:
                raise ValueError("Grudge target is no longer reachable by the agent's crew")
            crew = world.crew_ships.crews.get(crew_id)
            account = world.economy.crew_accounts.get(crew_id)
            if (not crew or not account or crew["treasury_silver"] != account["treasury_silver"]
                    or account["treasury_silver"] < cost):
                raise ValueError("Crew treasury cannot afford this grudge action")
        action_data = {"action_id": choice["action_id"],
                       "decision_number": choice["decision_number"],
                       "score": choice["score"], "cost": definition["time_cost_ticks"],
                       "expected_gain": definition["expected_gain"],
                       "expected_loss": definition["expected_loss"],
                       "target_id": grudge_target_id}
        if definition["handler"] == "pursue_grudge" and cost:
            action_data.update({"crew_id": crew_id, "silver_cost": cost,
                                "ledger_sink": {"silver": cost, "goods": {}}})
        action_event = world.event_log.append(
            tick=world.clock.tick, event_type="AGENT_ACTION", location=location["id"],
            actors=[{"role": "agent", "entity_id": agent_id}],
            data=action_data,
            caused_by=parents, causes_tags=tags, magnitude=0.05,
            event_class=("narrative" if definition["handler"] in {
                "advance_dream", "pursue_grudge"} else "routine"),
        )
        if definition["handler"] == "pursue_grudge" and cost:
            crew["treasury_silver"] -= cost
            world.economy.crew_accounts[crew_id]["treasury_silver"] -= cost
            world.economy.ledger.record_sink(action_event.id, silver=cost)
        handler = definition["handler"]
        if handler == "advance_dream":
            dream = agent["dream"]
            before = dream["progress"]
            dream["progress"] += 1
            result = world.event_log.append(
                tick=world.clock.tick, event_type="DREAM_PROGRESS", location=location["id"],
                actors=[{"role": "dreamer", "entity_id": agent_id}],
                data={"dream_kind": dream["kind"], "before": before,
                      "after": dream["progress"]}, caused_by=[action_event.id],
                causes_tags=["dream_pursuit"], magnitude=0.1,
            )
            agent["last_event_id"] = result.id
        elif handler == "rest_crew":
            crew = world.crew_ships.crews[agent["crew_id"]]
            before = crew["morale"]
            crew["morale"] = min(100, before + 8)
            result = world.event_log.append(
                tick=world.clock.tick, event_type="CREW_RESTED", location=location["id"],
                actors=[{"role": "crew_member", "entity_id": agent_id}],
                targets=[{"role": "crew", "entity_id": crew["id"]}],
                data={"crew_id": crew["id"], "morale_before": before,
                      "morale_after": crew["morale"]}, caused_by=[action_event.id],
                causes_tags=["crew_rest", "morale_recovery"], magnitude=0.1,
            )
            crew["last_event_id"] = result.id
            agent["last_event_id"] = result.id
        elif handler == "dispatch_trade":
            ship_id = agent["ship_id"]
            merchant_id = next((key for key, merchant in world.economy.merchants.items()
                                if merchant["ship_id"] == ship_id), None)
            plan = world.economy.best_trade_route(world, merchant_id)
            if plan is None:
                raise ValueError("Profitable trade route disappeared before action execution")
            route_event_id = world.economy.dispatch_trade_route(
                world, merchant_id, plan, caused_by=action_event.id)
            result_id = route_event_id
        elif handler == "pursue_grudge":
            grievance = agent["opinions"][grudge_target_id]["grievance"]
            target = world.entities[grudge_target_id]
            actor_location = world.location_of(agent_id)
            target_location = world.location_of(grudge_target_id)
            pursuit = world.event_log.append(
                tick=world.clock.tick, event_type="GRUDGE_PURSUED", location=location["id"],
                actors=[{"role": "avenger", "entity_id": agent_id}],
                targets=[{"role": "rival", "entity_id": grudge_target_id}],
                data={"grievance": grievance, "action_id": choice["action_id"],
                      "ship_id": raid_ship_id,
                      "actor_location_id": actor_location["id"],
                      "target_location_id": target_location["id"]},
                caused_by=[action_event.id], causes_tags=["grudge", "revenge_pursuit"],
                magnitude=min(1.0, grievance / 100),
            )
            raid_event = world.event_log.append(
                tick=world.clock.tick, event_type="FEUD_RAID_ATTEMPTED",
                location=target_location["id"],
                actors=[{"role": "raider", "entity_id": agent_id}],
                targets=[{"role": "raided_rival", "entity_id": grudge_target_id}],
                data={"outcome": "repelled", "material_transfer": False,
                      "grievance": grievance, "ship_id": raid_ship_id,
                      "actor_location_id": actor_location["id"],
                      "target_location_id": target_location["id"]},
                caused_by=[pursuit.id], causes_tags=["grudge", "retaliation", "feud_escalation"],
                magnitude=0.35,
            )
            world.memories.add_memory(
                world, grudge_target_id, agent_id, "humiliated", event_id=raid_event.id,
                intensity_permille=800, valence_permille=-1000,
            )
            agent["last_event_id"] = raid_event.id
            result = raid_event
        else:
            raise ValueError(f"No execution handler is registered: {handler}")
        state["last_executed_decision_number"] = choice["decision_number"]
        return result_id if handler == "dispatch_trade" else result.id

    def decide_and_execute(self, world, agent_id: str, *, caused_by: str) -> tuple[dict[str, Any], str] | None:
        choice = self.decide(world, agent_id, caused_by=caused_by)
        if choice is None:
            return None
        return choice, self.execute(world, choice)

    def set_belief(self, world, agent_id: str, subject_id: str, attribute: str,
                   value: Any, *, source_event_id: str, confidence_permille: int = 1000,
                   source_kind: str = "direct", source_agent_id: str | None = None) -> None:
        agent = world.entities.get(agent_id)
        if (not agent or agent.get("kind") != "character" or agent.get("death_tick") is not None
                or not isinstance(subject_id, str) or not subject_id
                or not isinstance(attribute, str) or not attribute
                or type(confidence_permille) is not int or not 1 <= confidence_permille <= 1000
                or source_kind not in {"direct", "rumor"}
                or source_agent_id is not None and source_agent_id not in world.entities):
            raise ValueError("Invalid belief observer, subject, source, or confidence")
        world.event_log.get(source_event_id)
        try:
            json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("Belief values must be finite JSON values") from exc
        state = self.agents.setdefault(agent_id, self._new_agent_state())
        self._store_belief(state, {
            "subject_id": subject_id, "attribute": attribute,
            "value": value,
            "confidence_permille": confidence_permille,
            "source": {"kind": source_kind, "agent_id": source_agent_id,
                       "event_id": source_event_id},
            "observed_tick": world.clock.tick,
        })

    def _store_belief(self, state: dict[str, Any], record: dict[str, Any]) -> None:
        beliefs = state["beliefs"]
        for index, previous in enumerate(beliefs):
            if (previous["subject_id"] == record["subject_id"]
                    and previous["attribute"] == record["attribute"]):
                beliefs[index] = record
                break
        else:
            beliefs.append(record)
        maximum = self.ai_data["belief_rules"]["max_beliefs_per_agent"]
        if len(beliefs) > maximum:
            beliefs.sort(key=lambda row: (row["confidence_permille"], row["observed_tick"],
                                          row["subject_id"], row["attribute"]))
            del beliefs[:len(beliefs) - maximum]
        beliefs.sort(key=lambda row: (row["subject_id"], row["attribute"]))

    def belief(self, agent_id: str, subject_id: str, attribute: str) -> dict[str, Any] | None:
        state = self.agents.get(agent_id)
        if not state:
            return None
        for record in state["beliefs"]:
            if record["subject_id"] == subject_id and record["attribute"] == attribute:
                return json.loads(json.dumps(record, sort_keys=True))
        return None

    def audit_belief(self, world, agent_id: str, subject_id: str, attribute: str, *,
                     triggered_by: str) -> str | None:
        """Compare an agent's estimate with truth and causally record material errors."""
        record = self.belief(agent_id, subject_id, attribute)
        if record is None:
            return None
        world.event_log.get(triggered_by)
        subject = world.entities.get(subject_id)
        if not subject:
            raise ValueError("Belief audit requires an active subject")
        if subject.get("kind") == "ship" and attribute == "estimated_strength":
            actual = world.crew_ships.combat_power(world, subject_id)["total"]
        elif subject.get("kind") == "island" and attribute == "prosperity":
            actual = world.economy.islands[subject_id]["prosperity"]
        else:
            raise ValueError(f"Unsupported belief audit attribute: {attribute}")
        believed = record["value"]
        if type(believed) is not int:
            raise ValueError("Audited belief and actual value must be integers")
        threshold = self.ai_data["belief_rules"]["belief_error_threshold_permille"]
        if abs(believed - actual) * 1000 <= max(1, abs(actual)) * threshold:
            return None
        observer = world.entities[agent_id]
        causes = list(dict.fromkeys((record["source"]["event_id"], triggered_by)))
        event = world.event_log.append(
            tick=world.clock.tick, event_type="BELIEF_ERROR",
            location=world.location_of(agent_id)["id"],
            actors=[{"role": "observer", "entity_id": agent_id}],
            targets=[{"role": "misjudged_subject", "entity_id": subject_id}],
            data={"attribute": attribute, "believed_value": believed,
                  "actual_value": actual, "confidence_permille": record["confidence_permille"],
                  "source_event_id": record["source"]["event_id"],
                  "trigger_event_id": triggered_by},
            caused_by=causes, causes_tags=["belief_error", f"belief:{attribute}"],
            magnitude=min(1.0, abs(believed - actual) / max(1, abs(actual))),
        )
        observer["last_event_id"] = event.id
        return event.id

    def ensure_dream_plan(self, world, agent_id: str, *, caused_by: str) -> dict[str, Any]:
        agent = world.entities.get(agent_id)
        if not agent or agent.get("kind") != "character" or agent.get("death_tick") is not None:
            raise ValueError("Dream plans require a living character")
        world.event_log.get(caused_by)
        dream = agent.get("dream", {})
        dream_kind = dream.get("kind")
        template = self.ai_data["plan_templates"].get(dream_kind)
        if template is None:
            raise ValueError(f"No dream plan template exists for: {dream_kind}")
        state = self.agents.setdefault(agent_id, self._new_agent_state())
        current = state["plan"]
        target = dream.get("target_ref")
        if (current and current["status"] == "active"
                and current["dream_kind"] == dream_kind and current["target_ref"] == target):
            return json.loads(json.dumps(current, sort_keys=True))
        event = world.event_log.append(
            tick=world.clock.tick, event_type="DREAM_PLAN_CREATED",
            location=world.location_of(agent_id)["id"],
            actors=[{"role": "dreamer", "entity_id": agent_id}],
            data={"dream_kind": dream_kind, "target_ref": target,
                  "steps": list(template), "replaced_plan": current is not None},
            caused_by=[caused_by], causes_tags=["dream_decomposition", f"dream:{dream_kind}"],
            magnitude=0.1,
        )
        state["plan"] = {
            "dream_kind": dream_kind, "target_ref": target,
            "steps": list(template), "current_step": 0, "status": "active",
            "created_tick": world.clock.tick, "source_event_id": event.id,
        }
        agent["last_event_id"] = event.id
        return json.loads(json.dumps(state["plan"], sort_keys=True))

    def advance_dream_plan_step(self, world, agent_id: str, *, caused_by: str) -> str:
        state = self.agents.get(agent_id)
        plan = state.get("plan") if state else None
        if (not plan or plan["status"] != "active"
                or plan["current_step"] >= len(plan["steps"])):
            raise ValueError("Character has no active dream plan")
        world.event_log.get(caused_by)
        index = plan["current_step"]
        step = plan["steps"][index]
        event = world.event_log.append(
            tick=world.clock.tick, event_type="PLAN_STEP_COMPLETED",
            location=world.location_of(agent_id)["id"],
            actors=[{"role": "planner", "entity_id": agent_id}],
            data={"dream_kind": plan["dream_kind"], "step": step,
                  "step_index": index, "steps_total": len(plan["steps"])},
            caused_by=[caused_by], causes_tags=["plan_progress", f"plan_step:{step}"],
            magnitude=0.1,
        )
        plan["current_step"] += 1
        if plan["current_step"] == len(plan["steps"]):
            plan["status"] = "completed"
            completed = world.event_log.append(
                tick=world.clock.tick, event_type="DREAM_PLAN_COMPLETED",
                location=world.location_of(agent_id)["id"],
                actors=[{"role": "dreamer", "entity_id": agent_id}],
                data={"dream_kind": plan["dream_kind"], "steps_total": len(plan["steps"])},
                caused_by=[event.id], causes_tags=["dream_plan_completed"], magnitude=0.2,
            )
            event_id = completed.id
        else:
            event_id = event.id
        world.entities[agent_id]["last_event_id"] = event_id
        return event_id

    def set_voyage_plan(self, world, agent_id: str, steps: list[dict[str, Any]], *,
                        caused_by: str) -> str:
        agent = world.entities.get(agent_id)
        if (not agent or agent.get("kind") != "character" or agent.get("death_tick") is not None
                or agent.get("ship_id") is None or not isinstance(steps, list)
                or not 1 <= len(steps) <= 8):
            raise ValueError("Voyage plans require a living ship agent and one to eight steps")
        world.event_log.get(caused_by)
        normalized = []
        for step in steps:
            if (not isinstance(step, dict) or set(step) != {"action", "target_island_id"}
                    or step["action"] not in {"travel", "trade", "repair"}
                    or (step["target_island_id"] is not None
                        and step["target_island_id"] not in world.geography.islands)
                    or (step["action"] == "travel" and step["target_island_id"] is None)):
                raise ValueError("Invalid voyage plan step")
            normalized.append({"action": step["action"],
                               "target_island_id": step["target_island_id"]})
        state = self.agents.setdefault(agent_id, self._new_agent_state())
        event = world.event_log.append(
            tick=world.clock.tick, event_type="VOYAGE_PLAN_CREATED",
            location=world.location_of(agent_id)["id"],
            actors=[{"role": "planner", "entity_id": agent_id}],
            data={"steps": normalized, "step_count": len(normalized)},
            caused_by=[caused_by], causes_tags=["voyage_plan"], magnitude=0.1,
        )
        state["voyage_plan"] = {
            "steps": normalized, "current_step": 0, "status": "active",
            "created_tick": world.clock.tick, "source_event_id": event.id,
        }
        agent["last_event_id"] = event.id
        return event.id

    def advance_voyage_plan(self, world, agent_id: str, *, caused_by: str,
                            completed_action: str, island_id: str | None = None) -> str:
        state = self.agents.get(agent_id)
        plan = state.get("voyage_plan") if state else None
        if not plan or plan["status"] != "active":
            raise ValueError("Character has no active voyage plan")
        if completed_action not in {"travel", "trade", "repair"}:
            raise ValueError("Unknown completed voyage-plan action")
        expected = plan["steps"][plan["current_step"]]
        if (expected["action"] != completed_action
                or (expected["target_island_id"] is not None
                    and expected["target_island_id"] != island_id)):
            raise ValueError("Completed action does not match the next voyage-plan step")
        world.event_log.get(caused_by)
        event = world.event_log.append(
            tick=world.clock.tick, event_type="VOYAGE_PLAN_STEP_COMPLETED",
            location=island_id or world.location_of(agent_id)["id"],
            actors=[{"role": "planner", "entity_id": agent_id}],
            data={"action": completed_action, "island_id": island_id,
                  "step_index": plan["current_step"]},
            caused_by=[caused_by], causes_tags=["voyage_plan_progress"], magnitude=0.1,
        )
        plan["current_step"] += 1
        if plan["current_step"] == len(plan["steps"]):
            plan["status"] = "completed"
            final_event = world.event_log.append(
                tick=world.clock.tick, event_type="VOYAGE_PLAN_COMPLETED",
                location=island_id or world.location_of(agent_id)["id"],
                actors=[{"role": "planner", "entity_id": agent_id}],
                data={"step_count": len(plan["steps"])}, caused_by=[event.id],
                causes_tags=["voyage_plan_completed"], magnitude=0.1,
            )
            event_id = final_event.id
        else:
            event_id = event.id
        world.entities[agent_id]["last_event_id"] = event_id
        return event_id

    def update_day(self, day: int, world) -> None:
        if type(day) is not int or day != self.last_updated_day + 1:
            raise ValueError("Decision/belief days must be updated consecutively")
        rules = self.ai_data["belief_rules"]
        decay = rules["daily_confidence_decay_permille"]
        for agent_id in sorted(self.agents):
            state = self.agents[agent_id]
            survivors = []
            for belief in state["beliefs"]:
                loss = max(1, belief["confidence_permille"] * decay // 1000)
                belief["confidence_permille"] = max(0, belief["confidence_permille"] - loss)
                if belief["confidence_permille"] >= rules["minimum_confidence_permille"]:
                    survivors.append(belief)
            state["beliefs"] = survivors
        self._spread_beliefs(day, world)
        self.last_updated_day = day

    def _spread_beliefs(self, day: int, world) -> None:
        cohorts: dict[tuple[Any, ...], list[str]] = {}
        for agent_id, agent in sorted(world.entities.items()):
            if agent.get("kind") != "character" or agent.get("death_tick") is not None:
                continue
            location = world.location_of(agent_id)
            key = (location.get("kind"), location.get("id"), location.get("from"), location.get("to"))
            cohorts.setdefault(key, []).append(agent_id)
        rules = self.ai_data["belief_rules"]
        # Snapshot each list so same-day transfers cannot cascade, but retain the
        # immutable-by-convention belief records instead of JSON-round-tripping them.
        source_beliefs = {
            agent_id: tuple(self.agents.get(agent_id, {}).get("beliefs", ()))
            for agent_id in sorted(world.entities)
        }
        belief_observed_ticks = {}
        for agent_id, beliefs in source_beliefs.items():
            observed = {}
            for belief in beliefs:
                observed.setdefault(
                    (belief["subject_id"], belief["attribute"]), belief["observed_tick"])
            belief_observed_ticks[agent_id] = observed
        source_last_events = {
            agent_id: world.entities[agent_id]["last_event_id"]
            for agent_id in sorted(source_beliefs)
            if world.entities[agent_id].get("kind") == "character"
        }
        for cohort in cohorts.values():
            if len(cohort) < 2:
                continue
            for sender_id in cohort:
                sender = world.entities[sender_id]
                beliefs = source_beliefs.get(sender_id, [])
                if not beliefs:
                    continue
                for belief in beliefs:
                    belief_key = (belief["subject_id"], belief["attribute"])
                    for recipient_id in cohort:
                        if recipient_id == sender_id:
                            continue
                        observed_tick = belief_observed_ticks.get(recipient_id, {}).get(
                            belief_key)
                        if observed_tick is not None and observed_tick >= belief["observed_tick"]:
                            continue
                        recipient = world.entities[recipient_id]
                        sociability = (sender["traits"]["sociability"]
                                       + recipient["traits"]["sociability"]) // 2
                        probability = min(10000, rules["share_base_basis_points"]
                                          + sociability
                                          * rules["share_sociability_basis_points_per_point"])
                        belief_digest = hashlib.sha256(
                            f"{belief['subject_id']}\u001f{belief['attribute']}".encode("utf-8")
                        ).hexdigest()
                        stream_name = (
                            f"belief-share:{day}:{sender_id}:{recipient_id}:{belief_digest}")
                        rng = world.rng.stream(stream_name)
                        if rng.randbelow(10000) >= probability:
                            continue
                        distorted = False
                        value = belief["value"]
                        original_value = value
                        if (type(value) is int
                                and rng.randbelow(10000) < rules["distortion_basis_points"]):
                            delta = rng.randint(-rules["max_distortion_permille"],
                                                rules["max_distortion_permille"])
                            value = value * (1000 + delta) // 1000
                            distorted = delta != 0
                        confidence = (belief["confidence_permille"]
                                      * (1000 - rules["share_confidence_loss_permille"]) // 1000)
                        if confidence < rules["minimum_confidence_permille"]:
                            continue
                        source_event_id = belief["source"]["event_id"]
                        causes = list(dict.fromkeys((source_event_id,
                                                     source_last_events[sender_id])))
                        location = world.location_of(sender_id)
                        event = world.event_log.append(
                            tick=world.clock.tick, event_type="RUMOR_SHARED",
                            location=location["id"],
                            actors=[{"role": "source", "entity_id": sender_id}],
                            targets=[{"role": "recipient", "entity_id": recipient_id}],
                            data={"subject_id": belief["subject_id"],
                                  "attribute": belief["attribute"], "value": value,
                                  "original_value": original_value,
                                  "confidence_permille": confidence, "distorted": distorted,
                                  "original_source_event": source_event_id},
                            caused_by=causes,
                            causes_tags=["information_propagation", "rumor",
                                         *( ["belief_distortion"] if distorted else [])],
                            magnitude=0.15 if not distorted else 0.3,
                        )
                        self.set_belief(
                            world, recipient_id, belief["subject_id"], belief["attribute"],
                            value, source_event_id=event.id, confidence_permille=confidence,
                            source_kind="rumor", source_agent_id=sender_id)
                        recipient_state = self.agents[recipient_id]
                        stored = next((row for row in recipient_state["beliefs"]
                                       if (row["subject_id"], row["attribute"]) == belief_key),
                                      None)
                        recipient_index = belief_observed_ticks.setdefault(recipient_id, {})
                        if stored is None:
                            recipient_index.pop(belief_key, None)
                        else:
                            recipient_index[belief_key] = stored["observed_tick"]
                        recipient["last_event_id"] = event.id

    def set_event_cursor(self, cursor: int) -> None:
        if type(cursor) is not int or cursor < 0:
            raise ValueError("Decision event cursor must be nonnegative")
        self.event_cursor = cursor

    def process_event_wakes(self, world) -> list[tuple[str, str]]:
        event_count = len(world.event_log)
        if self.event_cursor > event_count:
            raise ValueError("Decision event cursor is beyond the event log")
        wake_types = self.ai_data["wake_event_types"]
        new_events = world.event_log.since_types(self.event_cursor, wake_types)
        self.event_cursor = event_count
        for event in new_events:
            participants = [item["entity_id"] for item in (*event.actors, *event.targets)]
            crew_id = event.data.get("crew_id")
            if crew_id in world.crew_ships.crews:
                participants.append(world.crew_ships.crews[crew_id]["captain_id"])
            for entity_id in participants:
                entity = world.entities.get(entity_id)
                if entity and entity.get("kind") == "ship":
                    crew = world.crew_ships.crews.get(entity.get("crew_id"))
                    if crew:
                        participants.append(crew["captain_id"])
            for agent_id in sorted(set(participants)):
                agent = world.entities.get(agent_id)
                if self._terminal_agent(world, agent_id, agent):
                    self._record_stale_wakeup(world, agent_id, event.id, agent)
                    self.on_agent_terminal(world, agent_id)
                    continue
                if not agent or agent.get("kind") != "character":
                    continue
                event_ids = self._pending_wake_ids_by_agent.setdefault(agent_id, set())
                if event.id not in event_ids:
                    self._pending_wakes_by_agent.setdefault(agent_id, deque()).append({
                        "agent_id": agent_id, "event_id": event.id, "reason": event.type,
                    })
                    event_ids.add(event.id)
                    self._schedule_pending_wake_agent(agent_id, world)
        completed = []
        ready_agents = []
        while (self._pending_wake_heap
               and self._pending_wake_heap[0][0] <= world.clock.tick):
            due_tick, agent_id = heapq.heappop(self._pending_wake_heap)
            self._pending_wake_scheduled.discard(agent_id)
            if (self._pending_wakes_by_agent.get(agent_id)
                    and (not self.agents.get(agent_id)
                         or self.agents[agent_id]["next_decision_tick"] <= world.clock.tick)):
                ready_agents.append(agent_id)
            elif self._pending_wakes_by_agent.get(agent_id):
                # A scheduled action may have moved the deadline since this heap entry
                # was created. Re-index it at the authoritative deadline.
                self._schedule_pending_wake_agent(agent_id, world)
        for agent_id in sorted(ready_agents):
            queue = self._pending_wakes_by_agent.get(agent_id)
            if not queue:
                continue
            agent = world.entities.get(agent_id)
            if self._terminal_agent(world, agent_id, agent):
                self.on_agent_terminal(world, agent_id)
                continue
            if not agent or agent.get("kind") != "character":
                self._pending_wakes_by_agent.pop(agent_id, None)
                self._pending_wake_ids_by_agent.pop(agent_id, None)
                continue
            while queue:
                state = self.agents.get(agent_id)
                if state and world.clock.tick < state["next_decision_tick"]:
                    break
                wake = queue.popleft()
                self._pending_wake_ids_by_agent[agent_id].discard(wake["event_id"])
                choice_result = self.decide_and_execute(
                    world, agent_id, caused_by=wake["event_id"])
                if choice_result is not None:
                    completed.append((agent_id, choice_result[0]["action_id"]))
            if not queue:
                self._pending_wakes_by_agent.pop(agent_id, None)
                self._pending_wake_ids_by_agent.pop(agent_id, None)
            else:
                self._schedule_pending_wake_agent(agent_id, world)
        return completed

    @staticmethod
    def _terminal_agent(world, agent_id: str, agent: dict[str, Any] | None) -> bool:
        archived = agent_id in world.archive
        if not agent:
            return True
        return (archived or agent.get("death_tick") is not None
                or agent.get("sunk_tick") is not None
                or agent.get("state") in ("sunk", "dead", "archived", "captured"))

    @staticmethod
    def _record_stale_wakeup(world, agent_id: str, event_id: str,
                             agent: dict[str, Any] | None) -> None:
        terminal_record = agent or world.archive.get(agent_id)
        state = terminal_record.get("state") if terminal_record else "missing"
        if agent_id in world.archive and not state:
            state = "archived"
        if terminal_record and terminal_record.get("death_tick") is not None:
            state = "dead"
        elif terminal_record and terminal_record.get("sunk_tick") is not None:
            state = "sunk"
        world._record_lifecycle_diagnostic(
            "STALE_WAKEUP", agent_id, event_id, f"terminal_state:{state}")

    def process_scheduled_agents(self, world) -> list[tuple[str, str]]:
        """Give due capable characters their persisted scheduled decision review."""
        completed = []
        for agent_id, agent in sorted(world.entities.items()):
            if self._terminal_agent(world, agent_id, agent):
                state = agent.get("state") or (
                    "dead" if agent.get("death_tick") is not None else "sunk")
                cause = agent.get("last_event_id") or "event_000001"
                world._record_lifecycle_diagnostic(
                    "STALE_WAKEUP", agent_id, cause, f"terminal_state:{state}")
                continue
            if (agent.get("kind") != "character" or agent.get("death_tick") is not None
                    or agent.get("tier") not in {"MAJOR", "LEGEND"}):
                continue
            state = self.agents.get(agent_id)
            if state and world.clock.tick < state["next_decision_tick"]:
                continue
            cause = agent.get("last_event_id")
            try:
                world.event_log.get(cause)
            except (KeyError, TypeError):
                cause = world.event_log.by_type("WORLD_GENERATED")[0].id
            result = self.decide_and_execute(world, agent_id, caused_by=cause)
            if result is not None:
                completed.append((agent_id, result[0]["action_id"]))
        return completed

    def _eligible(self, world, agent: dict[str, Any], precondition: str) -> bool:
        if agent.get("kind") != "character" or agent.get("death_tick") is not None:
            return False
        if agent.get("missing_at_sea") is not None:
            return False
        prisoner = world.encounters.prisoners.get(agent["id"])
        if prisoner is not None and prisoner.get("status") == "held":
            return False
        if precondition == "living_character_with_dream":
            return isinstance(agent.get("dream"), dict) and bool(agent["dream"].get("kind"))
        if precondition == "living_character_with_grudge":
            return self._grudge_target_id(world, agent) is not None
        crew_id = agent.get("crew_id")
        crew = world.crew_ships.crews.get(crew_id)
        if precondition == "crew_at_island_below_full_morale":
            if not crew or crew["morale"] >= 100:
                return False
            if crew["ship_ids"]:
                return bool(ship_is_commandable(world, crew["ship_ids"][0])
                            and world.location_of(crew["ship_ids"][0]).get("kind") == "island")
            return crew.get("stranded_location", {}).get("kind") == "island"
        if precondition == "captain_with_profitable_trade_route":
            if (not crew or not crew["ship_ids"]
                    or crew.get("captain_id") != agent["id"]):
                return False
            ship_id = crew["ship_ids"][0]
            merchant_id = next((key for key, row in world.economy.merchants.items()
                                if row["ship_id"] == ship_id), None)
            return merchant_id is not None and world.economy.best_trade_route(world, merchant_id) is not None
        raise ValueError(f"Unknown action precondition: {precondition}")

    @staticmethod
    def _can_afford_action(world, agent: dict[str, Any], action_id: str,
                           definition: dict[str, Any]) -> bool:
        cost = definition["silver_cost"]
        if cost <= 0:
            return True
        if definition["handler"] != "pursue_grudge":
            return False
        crew_id = agent.get("crew_id")
        crew = world.crew_ships.crews.get(crew_id)
        account = world.economy.crew_accounts.get(crew_id)
        return bool(crew and account
                    and crew["treasury_silver"] == account["treasury_silver"]
                    and account["treasury_silver"] >= cost)

    def _grudge_target_id(self, world, agent: dict[str, Any]) -> str | None:
        threshold = world.memories.rules_data["rules"]["revenge_want_threshold"]
        time = world.config["time"]
        ticks_per_day = time["hours_per_day"] // time["hours_per_tick"]
        cooldown = self.data["rules"]["grudge_target_cooldown_days"] * ticks_per_day
        recently_pursued = set()
        for event in reversed(world.event_log.by_entity_type(
                agent["id"], "FEUD_RAID_ATTEMPTED")):
            if world.clock.tick - event.tick >= cooldown:
                break
            if not any(actor.get("role") == "raider"
                       and actor.get("entity_id") == agent["id"]
                       for actor in event.actors):
                continue
            if any(target.get("role") == "raided_rival"
                   for target in event.targets):
                target_id = next((target.get("entity_id") for target in event.targets
                                  if target.get("role") == "raided_rival"), None)
                if target_id:
                    recently_pursued.add(target_id)
        eligible = [
            (record["grievance"], target_id)
            for target_id, record in agent.get("opinions", {}).items()
            if target_id not in recently_pursued
            and record.get("grievance", 0) >= threshold
            and (target := world.entities.get(target_id)) is not None
            and target.get("kind") == "character" and target.get("death_tick") is None
            and self._grudge_ship_for_target(world, agent, target_id) is not None
        ]
        if not eligible:
            return None
        return min(eligible, key=lambda row: (-row[0], row[1]))[1]

    @staticmethod
    def _grudge_ship_for_target(world, agent: dict[str, Any], target_id: str) -> str | None:
        """Return an active crew vessel only when the agent and rival share its location."""
        crew_id = agent.get("crew_id")
        crew = world.crew_ships.crews.get(crew_id)
        if not crew or agent["id"] not in crew["roster"]:
            return None
        actor_location = world.location_of(agent["id"])
        target_location = world.location_of(target_id)
        if (actor_location.get("kind") not in {"island", "route"}
                or actor_location != target_location):
            return None
        for ship_id in sorted(crew["ship_ids"]):
            ship = world.entities.get(ship_id)
            if (ship and world.ship_is_commandable(ship_id)
                    and world.location_of(ship_id) == actor_location):
                return ship_id
        return None

    @staticmethod
    def _new_agent_state() -> dict[str, Any]:
        return {"decision_count": 0, "last_action_id": None, "last_decision_tick": -1,
                "next_decision_tick": 0, "last_executed_decision_number": 0, "history": [],
                "beliefs": [], "plan": None, "voyage_plan": None}

    def validate_state(self) -> None:
        if (not isinstance(self.agents, dict) or not isinstance(self.debug_history, list)
                or not isinstance(self.pending_wakes, list)
                or type(self.event_cursor) is not int or self.event_cursor < 0
                or type(self.last_updated_day) is not int or self.last_updated_day < 0):
            raise ValueError("Invalid decision state containers")
        if len(self.debug_history) > self.data["rules"]["debug_history_limit"]:
            raise ValueError("Decision debug history exceeds its configured bound")
        for agent_id, state in self.agents.items():
            if (not isinstance(agent_id, str) or not agent_id or not isinstance(state, dict)
                    or set(state) != {"decision_count", "last_action_id", "last_decision_tick",
                                      "next_decision_tick", "last_executed_decision_number", "history",
                                      "beliefs", "plan", "voyage_plan"}
                    or any(type(state[key]) is not int or state[key] < minimum for key, minimum in (
                        ("decision_count", 0), ("last_decision_tick", -1),
                        ("next_decision_tick", 0), ("last_executed_decision_number", 0)))
                    or state["last_executed_decision_number"] > state["decision_count"]
                    or state["last_action_id"] is not None
                    and state["last_action_id"] not in self.data["actions"]
                    or not isinstance(state["history"], list) or len(state["history"]) > 50
                    or any(not isinstance(row, dict) or set(row) != {"action_id", "tick"}
                           or row["action_id"] not in self.data["actions"]
                           or type(row["tick"]) is not int or row["tick"] < 0
                           for row in state["history"])
                    or not isinstance(state["beliefs"], list)
                    or len(state["beliefs"]) > self.ai_data["belief_rules"]["max_beliefs_per_agent"]
                    or any(not self._valid_belief_record(row) for row in state["beliefs"])
                    or not self._valid_plan_record(state["plan"])
                    or not self._valid_voyage_plan_record(state["voyage_plan"])):
                raise ValueError(f"Invalid agent decision state: {agent_id}")

        for wake in self.pending_wakes:
            if (not isinstance(wake, dict) or set(wake) != {"agent_id", "event_id", "reason"}
                    or not isinstance(wake["agent_id"], str) or not wake["agent_id"]
                    or not isinstance(wake["event_id"], str) or not wake["event_id"]
                    or wake["reason"] not in self.ai_data["wake_event_types"]):
                raise ValueError("Invalid pending agent decision wake")

    def _valid_belief_record(self, row: Any) -> bool:
        return (isinstance(row, dict)
                and set(row) == {"subject_id", "attribute", "value", "confidence_permille",
                                 "source", "observed_tick"}
                and isinstance(row["subject_id"], str) and bool(row["subject_id"])
                and isinstance(row["attribute"], str) and bool(row["attribute"])
                and type(row["confidence_permille"]) is int
                and 1 <= row["confidence_permille"] <= 1000
                and isinstance(row["source"], dict)
                and set(row["source"]) == {"kind", "agent_id", "event_id"}
                and row["source"]["kind"] in {"direct", "rumor"}
                and (row["source"]["agent_id"] is None
                     or isinstance(row["source"]["agent_id"], str))
                and isinstance(row["source"]["event_id"], str)
                and type(row["observed_tick"]) is int and row["observed_tick"] >= 0)

    def _valid_plan_record(self, plan: Any) -> bool:
        if plan is None:
            return True
        return (isinstance(plan, dict)
                and set(plan) == {"dream_kind", "target_ref", "steps", "current_step",
                                  "status", "created_tick", "source_event_id"}
                and plan["dream_kind"] in self.ai_data["plan_templates"]
                and (plan["target_ref"] is None or isinstance(plan["target_ref"], str))
                and isinstance(plan["steps"], list) and plan["steps"]
                and all(isinstance(step, str) and step for step in plan["steps"])
                and type(plan["current_step"]) is int
                and 0 <= plan["current_step"] <= len(plan["steps"])
                and plan["status"] in {"active", "completed"}
                and (plan["status"] != "active" or plan["current_step"] < len(plan["steps"])
                     )
                and (plan["status"] != "completed" or plan["current_step"] == len(plan["steps"])
                     )
                and type(plan["created_tick"]) is int and plan["created_tick"] >= 0
                and isinstance(plan["source_event_id"], str))

    def _valid_voyage_plan_record(self, plan: Any) -> bool:
        if plan is None:
            return True
        if not isinstance(plan, dict) or set(plan) != {
                "steps", "current_step", "status", "created_tick", "source_event_id"}:
            return False
        steps = plan["steps"]
        if (not isinstance(steps, list) or not 1 <= len(steps) <= 8
                or any(not isinstance(step, dict)
                       or set(step) != {"action", "target_island_id"}
                       or step["action"] not in {"travel", "trade", "repair"}
                       or step["target_island_id"] is not None
                       and not isinstance(step["target_island_id"], str)
                       or step["action"] == "travel" and step["target_island_id"] is None
                       for step in steps)
                or type(plan["current_step"]) is not int
                or not 0 <= plan["current_step"] <= len(steps)
                or plan["status"] not in {"active", "completed"}
                or plan["status"] == "active" and plan["current_step"] >= len(steps)
                or plan["status"] == "completed" and plan["current_step"] != len(steps)
                or type(plan["created_tick"]) is not int or plan["created_tick"] < 0
                or not isinstance(plan["source_event_id"], str)):
            return False
        return True

    def validate_world(self, world) -> None:
        self.validate_state()
        if self.event_cursor > len(world.event_log):
            raise ValueError("Decision event cursor exceeds the event log")
        for agent_id in self.agents:
            agent = (world.entities.get(agent_id) or world.archive.get(agent_id)
                     or world.lod.named_records.get(agent_id))
            if not agent or agent.get("kind") != "character":
                raise ValueError(f"Decision state references an unknown character: {agent_id}")
            state = self.agents[agent_id]
            if (state["last_decision_tick"] > world.clock.tick
                    or state["next_decision_tick"] < 0
                    or any(row["tick"] > world.clock.tick for row in state["history"])):
                raise ValueError(f"Agent decision state is beyond the world clock: {agent_id}")
            for belief in state["beliefs"]:
                try:
                    json.dumps(belief["value"], sort_keys=True, ensure_ascii=False, allow_nan=False)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"Belief contains a non-JSON value: {agent_id}") from exc
                try:
                    world.event_log.get(belief["source"]["event_id"])
                except KeyError as exc:
                    raise ValueError(f"Belief references an unknown source event: {agent_id}") from exc
                source_agent = belief["source"]["agent_id"]
                if source_agent is not None and source_agent not in world.entities \
                        and source_agent not in world.archive \
                        and source_agent not in world.lod.named_records:
                    raise ValueError(f"Belief references an unknown source agent: {agent_id}")
                if belief["observed_tick"] > world.clock.tick:
                    raise ValueError(f"Belief is observed in the future: {agent_id}")
            plan = state["plan"]
            if plan is not None:
                try:
                    world.event_log.get(plan["source_event_id"])
                except KeyError as exc:
                    raise ValueError(f"Dream plan references an unknown event: {agent_id}") from exc
                if plan["created_tick"] > world.clock.tick:
                    raise ValueError(f"Dream plan was created in the future: {agent_id}")
            voyage_plan = state["voyage_plan"]
            if voyage_plan is not None:
                try:
                    world.event_log.get(voyage_plan["source_event_id"])
                except KeyError as exc:
                    raise ValueError(f"Voyage plan references an unknown event: {agent_id}") from exc
                if voyage_plan["created_tick"] > world.clock.tick:
                    raise ValueError(f"Voyage plan was created in the future: {agent_id}")
                if any(step["target_island_id"] is not None
                       and step["target_island_id"] not in world.geography.islands
                       for step in voyage_plan["steps"]):
                    raise ValueError(f"Voyage plan targets an unknown island: {agent_id}")
        for wake in self.pending_wakes:
            if wake["agent_id"] not in world.entities:
                raise ValueError("Pending decision wake references no active character")
            try:
                event = world.event_log.get(wake["event_id"])
            except KeyError as exc:
                raise ValueError("Pending decision wake references an unknown event") from exc
            if event.type != wake["reason"] or event.type not in self.ai_data["wake_event_types"]:
                raise ValueError("Pending decision wake reason disagrees with its event")
        for row in self.debug_history:
            if (not isinstance(row, dict)
                    or not {"agent_id", "action_id", "decision_number", "tick", "caused_by",
                            "score", "top_candidates", "score_components"} <= set(row)
                    or row["agent_id"] not in self.agents
                    or row["action_id"] not in self.data["actions"]
                    or type(row["tick"]) is not int or row["tick"] < 0
                    or row["tick"] > world.clock.tick
                    or type(row["decision_number"]) is not int or row["decision_number"] < 1
                    or not isinstance(row["top_candidates"], list)
                    or not isinstance(row["score_components"], dict)):
                raise ValueError("Invalid decision debug trace")
            try:
                event = world.event_log.get(row["caused_by"])
            except (KeyError, TypeError) as exc:
                raise ValueError("Decision debug trace references an unknown cause event") from exc
            if event.tick > row["tick"]:
                raise ValueError("Decision debug trace predates its cause event")

    def snapshot(self) -> dict[str, Any]:
        return {"digest": self.digest, "agents": self.agents,
                "debug_history": self.debug_history, "pending_wakes": self.pending_wakes,
                "event_cursor": self.event_cursor, "last_updated_day": self.last_updated_day}
