"""Versioned, immutable-by-convention definitions for authored play scenarios."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path


SCENARIO_DIR = Path(__file__).resolve().parents[1] / "content" / "scenarios"
SUPPORTED_SCENARIOS = {"lawful-trader-mvp": "lawful-trader-mvp.json"}
_POLICY_FIELDS = (
    "generated_prehistory", "strategic_society",
    "npc_destination_activity", "hostile_population_trade",
)


def load_scenario(scenario_id: str, *, ports=None, sea_nodes=None):
    """Load a supported definition and optionally validate its port/node links."""
    if not isinstance(scenario_id, str) or scenario_id not in SUPPORTED_SCENARIOS:
        raise ValueError("Unknown authored scenario")
    path = SCENARIO_DIR / SUPPORTED_SCENARIOS[scenario_id]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"Cannot read authored scenario {scenario_id}: {error}") from error
    if not isinstance(data, dict) or set(data) != {
            "id", "version", "name", "entry_port", "ports", "policies"}:
        raise ValueError("Invalid authored scenario definition")
    if (data["id"] != scenario_id or type(data["version"]) is not int or data["version"] != 1
            or not isinstance(data["name"], str) or not data["name"].strip()):
        raise ValueError("Invalid authored scenario identity")
    if (not isinstance(data["ports"], list) or len(data["ports"]) < 3
            or any(not isinstance(port, str) or not port for port in data["ports"])
            or len(set(data["ports"])) != len(data["ports"])
            or data["entry_port"] not in data["ports"]):
        raise ValueError("Invalid authored scenario ports")
    policies = data["policies"]
    if (not isinstance(policies, dict) or set(policies) != set(_POLICY_FIELDS)
            or any(type(policies[field]) is not bool for field in _POLICY_FIELDS)
            or any(policies.values())):
        raise ValueError("Authored MVP must keep generated/strategic world systems disconnected")
    if ports is not None:
        if any(port not in ports for port in data["ports"]):
            raise ValueError("Authored scenario references an unknown trade port")
    if sea_nodes is not None:
        for port in data["ports"]:
            if not any(node.get("port") == port for node in sea_nodes.values()):
                raise ValueError(f"Authored port {port} has no chart node")
    return deepcopy(data)
