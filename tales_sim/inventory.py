"""Body capabilities and pocketed, capacity-aware item stacks."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from tales_sim.temperature import merge_thermal_stack, thermal_stacks_compatible
from tales_sim.freshness import freshness_stacks_compatible
from tales_sim.materials import (item_base_stats, material_stacks_compatible,
                                quality_scaled_stats)


def condition_indicator(condition: int) -> tuple[str, str]:
    """ASCII wear tier, intentionally independent from workmanship quality."""
    value = max(0, min(100, int(condition)))
    if value >= 81:
        return "||", "sound"
    if value >= 61:
        return "|\\", "worn"
    if value >= 41:
        return "\\\\", "worn"
    if value >= 21:
        return "\\-", "critical"
    return "--", "critical"


def damage_item(item: dict, amount: int = 1) -> int:
    """Apply bounded wear and return the resulting item condition."""
    current = max(0, min(100, int(item.get("condition", 100))))
    item["condition"] = max(0, current - max(0, int(amount)))
    return item["condition"]


@dataclass(frozen=True)
class BodyCapabilities:
    physical_hands: int
    dexterous_hands: int
    hands: tuple[dict, ...] = ()

    @classmethod
    def from_definition(cls, definition: dict) -> "BodyCapabilities":
        body = definition.get("body", {})
        return cls(int(body.get("physical_hands", 2)), int(body.get("dexterous_hands", 1)),
                   tuple(dict(hand) for hand in body.get("hands", [])))

    def can_use_hands(self, required: int, supported_hands: int = 0) -> bool:
        return required <= min(self.physical_hands, self.dexterous_hands + supported_hands)

    def can_use_hand_type(self, hand_type: str) -> bool:
        return any(hand.get("type") == hand_type and hand.get("usable", True) for hand in self.hands)


class Inventory:
    """Equipped storage plus ordered stacks assigned to individual pockets."""

    def __init__(self, pockets: list[dict], items: list[dict] | None = None,
                 body: BodyCapabilities | None = None,
                 equipment: list[dict] | None = None):
        self.pockets = {p["id"]: dict(p) for p in pockets}
        self.items: list[dict] = []
        self.body = body
        self.equipment = [dict(item) for item in (equipment or [])]
        for item in items or []:
            if not self.add(item):
                raise ValueError(f"Initial inventory item {item['id']!r} does not fit")

    @classmethod
    def from_character(cls, character: dict, item_catalog: dict[str, dict],
                       body: BodyCapabilities) -> "Inventory":
        equipment = []
        pockets = [{
            "id": "under_arm", "name": "Carried under one arm",
            "max_weight_g": 3000, "max_bulk_ml": 2500,
            "uses_hands": 1, "stow_priority": 90,
        }]
        for equipped in character.get("equipment", []):
            item = item_catalog[equipped["item_id"]]
            slot = equipped["slot"]
            equipment.append({
                "id": item["id"], "name": item["name"], "slot": slot,
                "weight_g": item["weight_g"], "bulk_ml": item["bulk_ml"],
                "wearable": item.get("wearable", False),
                "equipment_slot": item.get("equipment_slot", slot),
                "pockets": list(item.get("pockets", [])),
                "quality": 50,
                "condition": 100,
                **({"stats": quality_scaled_stats(item_base_stats(item), 50)}
                   if item_base_stats(item) else {}),
            })
            for pocket in item.get("pockets", []):
                pocket_id = f"{slot.lower()}:{pocket['id']}"
                pockets.append({
                    **pocket,
                    "id": pocket_id,
                    "name": f"{item['name']} · {pocket['name']}",
                    "equipment_id": item["id"],
                    "equipment_name": item["name"],
                    "equipment_slot": slot,
                })
        return cls(pockets, body=body, equipment=equipment)

    def __iter__(self) -> Iterator[dict]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index):
        return self.items[index]

    def __setitem__(self, index, value) -> None:
        self.items[index] = value

    def pop(self, index: int = -1) -> dict:
        return self.items.pop(index)

    def append(self, item: dict) -> None:
        if not self.add(item):
            raise ValueError(f"Item {item['id']!r} does not fit in any pocket")

    def remove(self, item: dict) -> None:
        self.items.remove(item)

    def __bool__(self) -> bool:
        return bool(self.items)

    def used_weight_g(self, pocket_id: str | None = None) -> int:
        return sum(i["weight_g"] * i["quantity"] for i in self.items
                   if pocket_id is None or i.get("pocket") == pocket_id)

    def used_bulk_ml(self, pocket_id: str | None = None) -> int:
        return sum(i["bulk_ml"] * i["quantity"] for i in self.items
                   if pocket_id is None or i.get("pocket") == pocket_id)

    def carried_weight_g(self) -> int:
        return self.used_weight_g() + sum(item["weight_g"] for item in self.equipment)

    def carried_bulk_ml(self) -> int:
        return self.used_bulk_ml() + sum(item["bulk_ml"] for item in self.equipment)

    def _fits(self, item: dict, pocket_id: str) -> bool:
        pocket = self.pockets[pocket_id]
        weight = self.used_weight_g(pocket_id) + item["weight_g"] * item["quantity"]
        bulk = self.used_bulk_ml(pocket_id) + item["bulk_ml"] * item["quantity"]
        return weight <= pocket["max_weight_g"] and bulk <= pocket["max_bulk_ml"]

    def can_use_hands(self, required: int, supported_hands: int = 0) -> bool:
        if self.body is None:
            return required <= 2 + supported_hands
        occupied = sum(int(self.pockets[stack.get("pocket", {})].get("uses_hands", 0))
                       for stack in self.items if stack.get("pocket") in self.pockets)
        available = max(0, self.body.dexterous_hands - occupied) + supported_hands
        return required <= min(self.body.physical_hands, available)

    def can_add(self, item: dict) -> bool:
        existing = next((stack for stack in self.items if stack["id"] == item["id"]), None)
        for pocket_id in self.pockets:
            if existing is not None and existing.get("pocket") == pocket_id and self._fits(item, pocket_id):
                return True
            if self._fits(item, pocket_id):
                return True
        return False

    def add_to_pocket(self, item: dict, pocket_id: str) -> bool:
        if pocket_id not in self.pockets or not self._fits(item, pocket_id):
            return False
        existing = next((stack for stack in self.items
                         if stack["id"] == item["id"] and stack.get("pocket") == pocket_id
                         and "bucket_state" not in stack and "bucket_state" not in item
                         and "live_fish" not in stack and "live_fish" not in item
                         and thermal_stacks_compatible(stack, item)
                         and freshness_stacks_compatible(stack, item)
                         and material_stacks_compatible(stack, item)
                         and stack.get("stored_in") == item.get("stored_in")), None)
        if existing is not None:
            merge_thermal_stack(existing, item)
            existing["quantity"] += item["quantity"]
        else:
            self.items.append({**item, "pocket": pocket_id})
        return True

    def move_to_pocket(self, index: int, pocket_id: str) -> bool:
        if not 0 <= index < len(self.items) or pocket_id not in self.pockets:
            return False
        item = self.items.pop(index)
        if self.add_to_pocket(item, pocket_id):
            return True
        self.items.insert(index, item)
        return False

    def wear(self, index: int) -> tuple[bool, str]:
        if not 0 <= index < len(self.items):
            return False, "Choose an inventory item first."
        stack = self.items[index]
        if not stack.get("wearable") or not stack.get("equipment_slot"):
            return False, f"{stack['name']} cannot be worn."
        slot = stack["equipment_slot"]
        if any(gear["slot"] == slot for gear in self.equipment):
            return False, f"The {slot} slot is already occupied."
        pocket_ids = [f"{slot.lower()}:{pocket['id']}" for pocket in stack.get("pockets", [])]
        if any(pocket_id in self.pockets for pocket_id in pocket_ids):
            return False, f"A pocket for the {slot} slot already exists."
        quantity = stack["quantity"]
        if quantity > 1:
            stack["quantity"] -= 1
            item = {**stack, "quantity": 1}
        else:
            item = self.items.pop(index)
        self.equipment.append({
            "id": item["id"], "name": item["name"], "slot": slot,
            "weight_g": item["weight_g"], "bulk_ml": item["bulk_ml"], "wearable": True,
            "equipment_slot": slot,
            "pockets": list(item.get("pockets", [])),
            "quality": int(item.get("quality", 50)),
            "condition": int(item.get("condition", 100)),
            **({"stats": dict(item["stats"])} if item.get("stats") else {}),
        })
        for pocket in item.get("pockets", []):
            pocket_id = f"{slot.lower()}:{pocket['id']}"
            self.pockets[pocket_id] = {
                **pocket, "id": pocket_id,
                "name": f"{item['name']} · {pocket['name']}",
                "equipment_id": item["id"], "equipment_name": item["name"],
                "equipment_slot": slot,
            }
        return True, f"You put on {item['name']}."

    def take_off(self, slot: str) -> tuple[bool, str]:
        gear = next((item for item in self.equipment
                     if item["slot"] == slot and item.get("wearable")), None)
        if gear is None:
            return False, f"There is no removable clothing in the {slot} slot."
        pocket_ids = [key for key, pocket in self.pockets.items()
                      if pocket.get("equipment_slot") == slot]
        if any(self.used_weight_g(key) or self.used_bulk_ml(key) for key in pocket_ids):
            return False, f"Empty the {gear['name']}'s pockets before taking it off."
        item = {**gear, "quantity": 1, "actions": [], "tags": ["clothing"],
                "weight_g": gear["weight_g"], "bulk_ml": gear["bulk_ml"],
                "equipment_slot": gear.get("equipment_slot", slot), "wearable": True}
        if not self.add(item):
            return False, "There is not enough pocket capacity to carry that clothing."
        self.equipment.remove(gear)
        for key in pocket_ids:
            self.pockets.pop(key, None)
        return True, f"You take off {gear['name']}."

    def apply_work_wear(self, required_tools: list[dict] | None = None,
                        tool_options: list[list[dict]] | None = None) -> None:
        """Wear one used tool and worn clothing slightly after a work job."""
        tool_ids = [str(row.get("id")) for row in (required_tools or [])
                    if isinstance(row, dict) and row.get("id")]
        for group in tool_options or []:
            candidates = [str(row.get("id")) for row in group
                          if isinstance(row, dict) and row.get("id")]
            chosen = next((item_id for item_id in candidates
                           if any(item["id"] == item_id for item in self.items)
                           or any(item["id"] == item_id for item in self.equipment)), None)
            if chosen:
                tool_ids.append(chosen)
        for item_id in dict.fromkeys(tool_ids):
            tool = next((item for item in self.items if item["id"] == item_id), None)
            if tool is None:
                tool = next((item for item in self.equipment if item["id"] == item_id), None)
            if tool is not None:
                damage_item(tool, 1)
        for gear in self.equipment:
            if gear.get("wearable"):
                damage_item(gear, 1)

    def add(self, item: dict) -> bool:
        """Add a full stack; return False without mutation if no pocket fits."""
        priority = sorted(self.pockets, key=lambda pocket_id: (
            self.pockets[pocket_id].get("stow_priority", 100),
            list(self.pockets).index(pocket_id),
        ))
        for pocket_id in priority:
            if self.add_to_pocket(item, pocket_id):
                return True
        return False

    def capacity_summary(self) -> str:
        weight = self.used_weight_g()
        bulk = self.used_bulk_ml()
        max_weight = sum(p["max_weight_g"] for p in self.pockets.values())
        max_bulk = sum(p["max_bulk_ml"] for p in self.pockets.values())
        return (f"{self.carried_weight_g()} g total incl. {sum(e['weight_g'] for e in self.equipment)} g worn gear · "
                f"{weight}/{max_weight} g pocketed · {bulk}/{max_bulk} ml pocketed")

    def clone(self) -> "Inventory":
        import copy
        clone = Inventory(self.pockets.values(), body=self.body, equipment=self.equipment)
        clone.items = copy.deepcopy(self.items)
        return clone


def movement_cost_ticks(base_ticks: int, carried_weight_g: int, weight_step_g: int,
                        ticks_per_step: int, max_surcharge_ticks: int) -> int:
    """Add a tunable movement surcharge for each carried-load weight step."""
    if min(base_ticks, carried_weight_g, ticks_per_step, max_surcharge_ticks) < 0 or weight_step_g <= 0:
        raise ValueError("movement costs and carried weight must be non-negative; weight_step_g must be positive")
    steps = (carried_weight_g + weight_step_g - 1) // weight_step_g
    return int(base_ticks + min(max_surcharge_ticks, steps * ticks_per_step))
