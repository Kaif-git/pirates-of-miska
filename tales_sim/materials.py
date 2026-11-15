"""Material identity for wood items (Expansion 05 · The Workshop & Hearth, Slice 1).

A wood stack carries a compact snapshot — ``material: {id, species_id, part,
grade}`` — stamped when the wood is harvested, felled, or crafted. It never
copies the tree: inherent properties (workability, flexibility, durability,
fuel) are derived from the species table (content/trees.json ``wood`` blocks)
with the material class as fallback, exactly as the spec's "smallest safe
metadata bridge" asks.

Legacy stacks without a snapshot normalize to ``wood:generic``: they keep
working in every recipe and merge only with other generic wood.

Stacking rule: wood stacks merge only when their gameplay-relevant properties
match — same material id, same grade band (raw wood) or quality band (crafted
wood). Everything else stays a separate, honestly-labeled stack.
"""
from __future__ import annotations

import random

GENERIC_MATERIAL_ID = "wood:generic"
GENERIC_SPECIES_ID = ""

# Material-class defaults (1..5) — used when a species has no `wood` block
# (or for generic wood). Data-driven species rows override these.
CLASS_WOOD_DEFAULTS: dict[str, dict] = {
    "palm": {"workability": 3, "flexibility": 4, "durability": 2, "fuel": 2},
    "hardwood": {"workability": 2, "flexibility": 2, "durability": 5, "fuel": 4},
    "softwood": {"workability": 4, "flexibility": 3, "durability": 2, "fuel": 3},
    "ironwood": {"workability": 1, "flexibility": 1, "durability": 5, "fuel": 5},
    "generic": {"workability": 3, "flexibility": 3, "durability": 3, "fuel": 3},
}

WOOD_PROPERTIES = ("workability", "flexibility", "durability", "fuel")

# Raw-wood grade folds the harvested piece's thickness/size into three honest
# bands so equivalent pieces stack without spraying near-identical variants:
# grades 1-2 are light, 3 standard, 4-5 dense.
GRADE_BANDS = ((4, "dense"), (3, "standard"), (0, "light"))

# Workmanship bands (Expansion 05 §8): a few understandable labels.
QUALITY_BANDS = ((75, "Excellent"), (50, "Good"), (25, "Serviceable"), (0, "Rough"))


def quality_scaled_stats(base_stats: dict | None, quality: int) -> dict[str, int]:
    """Scale catalog-defined item stats from the item's overall workmanship.

    At 50 quality an item keeps its baseline; rough work remains usable and
    excellent work grants a bounded 20% improvement.
    """
    value = max(0, min(100, int(quality)))
    factor = .8 + value * .004
    return {str(key): max(0, min(100, round(float(base) * factor)))
            for key, base in (base_stats or {}).items()}


def quality_from_grade(grade: int) -> int:
    """Map the existing 1..5 raw-material grade onto the shared 0..100 scale."""
    return max(1, min(5, int(grade))) * 20


def item_base_stats(item_spec: dict) -> dict[str, int]:
    """Return only authored, meaningful stats for the catalog item.

    Crafted gear can declare ``base_stats``. Other items expose their existing
    gameplay data (wearable protection and consumable benefits) rather than
    receiving invented combat/durability numbers.
    """
    stats = {str(key): int(value) for key, value in
             (item_spec.get("base_stats") or {}).items()}
    if item_spec.get("wearable"):
        if isinstance(item_spec.get("warmth"), int):
            stats["warmth"] = int(item_spec["warmth"])
        if isinstance(item_spec.get("wind_resistance"), int):
            stats["wind_resistance"] = int(item_spec["wind_resistance"])
        for part, value in (item_spec.get("coverage") or {}).items():
            if isinstance(value, int):
                stats[f"coverage_{part}"] = int(value)
    for action in item_spec.get("actions", []):
        if action.get("effect") not in {"eat", "drink"}:
            continue
        for source, label in (("hunger_relief", "satiety"),
                              ("thirst_relief", "hydration"),
                              ("nutrition_gain", "nutrition"),
                              ("work_energy_gain", "work_energy")):
            value = action.get(source)
            if isinstance(value, int):
                stats[label] = value
    return stats

WOOD_ITEM_PARTS = {
    "branch": "branch", "small_log": "log", "wood_log": "log",
    "wood_plank": "plank", "firewood_bundle": "split", "kindling_bundle": "split",
    "wood_scraps": "scrap", "rough_handle": "handle", "wooden_rod_body": "rod_body",
}


def wood_profile(species_row: dict | None, material_class: str | None = None) -> dict:
    """Effective 1..5 wood properties for a species (or a bare class)."""
    if species_row:
        block = species_row.get("wood")
        if isinstance(block, dict):
            return {key: max(1, min(5, int(block.get(key, 3))))
                    for key in WOOD_PROPERTIES}
        material_class = material_class or species_row.get("material")
    defaults = CLASS_WOOD_DEFAULTS.get(material_class or "", None) \
        or CLASS_WOOD_DEFAULTS["generic"]
    return dict(defaults)


def make_snapshot(species_row: dict, part: str, *, grade: int = 3) -> dict:
    """Compact JSON-safe material payload for one piece of harvested wood."""
    species_id = str(species_row.get("id") or "")
    material_class = str(species_row.get("material") or "generic")
    return {
        "id": f"wood:{material_class}",
        "species_id": species_id,
        "part": part,
        "grade": max(1, min(5, int(grade))),
    }


def normalize_material(item: dict) -> dict:
    """The stack's material snapshot; legacy stacks read as generic wood."""
    material = item.get("material")
    if isinstance(material, dict) and material.get("id"):
        return material
    return {"id": GENERIC_MATERIAL_ID, "species_id": GENERIC_SPECIES_ID,
            "part": WOOD_ITEM_PARTS.get(str(item.get("id", "")), "wood"),
            "grade": 3}


def is_wood_item(item_id: str) -> bool:
    return item_id in WOOD_ITEM_PARTS


def grade_band(grade: int) -> str:
    value = max(1, min(5, int(grade)))
    for floor, label in GRADE_BANDS:
        if value >= floor:
            return label
    return GRADE_BANDS[-1][1]


def quality_band(quality: int) -> str:
    value = max(0, min(100, int(quality)))
    for floor, label in QUALITY_BANDS:
        if value >= floor:
            return label
    return QUALITY_BANDS[-1][1]


def material_label(material: dict, species_table: dict | None = None) -> str:
    """Readable wood name: species first ('shore willow'), class otherwise."""
    species_id = str(material.get("species_id") or "")
    if species_id and species_table and species_id in species_table:
        return str(species_table[species_id].get("name") or species_id)
    material_id = str(material.get("id") or GENERIC_MATERIAL_ID)
    return material_id.removeprefix("wood:").replace("_", " ") or "wood"


CRAFTED_QUALITY_ITEMS = ("rough_handle", "wooden_rod_body")

# Expansion 05 Slice 2: smithed iron parts are stamped with a workmanship band
# exactly once at the forge, and assembled gear carries its component slots.
SMITHED_QUALITY_ITEMS = ("iron_fitting", "fishing_hook", "iron_knife", "iron_hoe")
EQUIPMENT_ITEMS = ("improved_axe", "basic_fishing_rod")

# Jes's hearth: cooked dishes are stamped once, when the cook takes the pot
# off the heat, with the grade the pot's own story earned (S/A/B/C). Same-band
# stacks merge; a superb stew and a plain one stay distinct on the shelf.
COOKED_QUALITY_ITEMS = ("cooked_beans", "fish_stew", "grilled_shore_fish",
                        "roasted_fruit", "prepared_sea_fish", "cocoa_syrup",
                        "cocobread")

# Items whose gameplay-relevant identity is a quality band (crafted wood) or a
# quality band alone (smithed iron): equivalent-band stacks may merge.
QUALITY_BAND_ITEMS = (CRAFTED_QUALITY_ITEMS + SMITHED_QUALITY_ITEMS
                      + EQUIPMENT_ITEMS + COOKED_QUALITY_ITEMS)


def _raw_compatible(a: dict, b: dict) -> bool:
    return (a.get("id") == b.get("id")
            and grade_band(int(a.get("grade", 3))) == grade_band(int(b.get("grade", 3))))


def material_stacks_compatible(stack: dict, incoming: dict) -> bool:
    """True when two stacks may merge; non-wood items are usually compatible.

    Assembled gear (a finished axe or rod with components) is one of a kind and
    never merges. Crafted parts merge only within the same workmanship band;
    raw wood only within the same grade band; generic (legacy) wood only with
    generic.
    """
    if stack.get("components") or incoming.get("components"):
        return False  # assembled equipment is one of a kind
    if int(stack.get("condition", 100)) != int(incoming.get("condition", 100)):
        return False  # never blend sound items with worn/broken ones
    if (str(stack.get("id", "")) in QUALITY_BAND_ITEMS
            or stack.get("quality") is not None or incoming.get("quality") is not None
            or str(incoming.get("id", "")) in QUALITY_BAND_ITEMS):
        if str(stack.get("id", "")) != str(incoming.get("id", "")):
            return False
        a, b = normalize_material(stack), normalize_material(incoming)
        if a.get("id") != b.get("id"):
            return False
        return quality_band(int(stack.get("quality", 40))) == \
            quality_band(int(incoming.get("quality", 40)))
    if not (is_wood_item(str(stack.get("id", "")))
            or is_wood_item(str(incoming.get("id", "")))):
        return True
    a, b = normalize_material(stack), normalize_material(incoming)
    if a.get("id") != b.get("id"):
        return False
    return _raw_compatible(a, b)


def stack_variant_key(stack: dict) -> tuple:
    """Identity of one stack for input-variant selection and merging.

    Crafted parts key on material + workmanship band, raw wood on material +
    grade band, assembled gear is always unique, and plain items key on their
    id alone.
    """
    item_id = str(stack.get("id", ""))
    if stack.get("components"):
        return ("unique", item_id, len(stack["components"]))
    if item_id in QUALITY_BAND_ITEMS or stack.get("quality") is not None:
        material = normalize_material(stack)
        return (material.get("id"), quality_band(int(stack.get("quality", 40))),
                int(stack.get("condition", 100)))
    if is_wood_item(item_id):
        material = normalize_material(stack)
        return (material.get("id"), grade_band(int(material.get("grade", 3))))
    return (item_id,)


def variant_row_label(stack: dict, species_table: dict | None = None) -> str:
    """One honest chooser row: what the piece is made of and how well."""
    item_id = str(stack.get("id", ""))
    bits = []
    if stack.get("quality") is not None:
        bits.append(f"Q{int(stack['quality'])} · {quality_band(int(stack['quality']))}")
    if item_id in SMITHED_QUALITY_ITEMS:
        bits.append(f"{quality_band(int(stack.get('quality', 50)))} workmanship")
    else:
        material = normalize_material(stack)
        if is_wood_item(item_id):
            bits.append(material_label(material, species_table))
        if item_id in CRAFTED_QUALITY_ITEMS:
            bits.append("workmanship")
        elif material.get("id") != GENERIC_MATERIAL_ID or material.get("species_id"):
            if is_wood_item(item_id):
                bits.append(f"{grade_band(int(material.get('grade', 3)))} grade")
    return " · ".join(bits) if bits else item_id


def suitability_note(material: dict, purpose: str,
                     species_table: dict | None = None) -> str:
    """Honest, plain-language fit for one purpose; never a gate, per spec."""
    species_id = str(material.get("species_id") or "")
    species_row = (species_table or {}).get(species_id) if species_id else None
    profile = wood_profile(species_row, str(material.get("id", "")).removeprefix("wood:"))
    if purpose == "rod_body":
        flexibility = profile["flexibility"]
        if flexibility >= 4:
            return "bends without fighting you — good rod stock"
        if flexibility >= 3:
            return "workable rod stock"
        return "stiff; an awkward rod body, but it can be tried"
    if purpose == "handle":
        durability = profile["durability"]
        workability = profile["workability"]
        if durability >= 4 and workability >= 2:
            return "firm and dependable — good handle wood"
        if durability >= 4:
            return "tough but demanding to shape"
        if durability >= 3:
            return "serviceable handle wood"
        return "soft; a handle that will wear quickly"
    if purpose == "fuel":
        fuel = profile["fuel"]
        if fuel >= 4:
            return "burns long and hot"
        if fuel >= 3:
            return "everyday firewood"
        return "burns quick and light"
    return ""


def shaping_effort_factor(material: dict | None,
                          species_table: dict | None = None) -> float:
    """Shaping-time multiplier from workability: willow quick, ironbark slow.

    Workability 5 → 0.85×, 3 → 1.0×, 1 → 1.35×; generic wood stays 1.0×.
    """
    if not material:
        return 1.0
    if material.get("id") == GENERIC_MATERIAL_ID:
        return 1.0
    species_id = str(material.get("species_id") or "")
    species_row = (species_table or {}).get(species_id) if species_id else None
    profile = wood_profile(species_row, str(material.get("id", "")).removeprefix("wood:"))
    return round(1.35 - 0.10 * profile["workability"], 2)


def workmanship(view, material: dict | None, skill_id: str,
                knowledge_ids: tuple[str, ...] = (),
                 species_table: dict | None = None, salt: str = "") -> int:
    """0..100 workmanship for one shaped or smithed output, stamped once.

    Emerges from the real inputs the spec names — material suitability, the
    character's practical skill, named knowledge, and a bounded seeded roll —
    never from a standalone rarity draw. The optional salt keeps two pieces
    finished in the same tick distinct without breaking determinism.
    """
    base = 30.0
    if material and material.get("id") != GENERIC_MATERIAL_ID:
        species_id = str(material.get("species_id") or "")
        species_row = (species_table or {}).get(species_id) if species_id else None
        profile = wood_profile(species_row,
                               str(material.get("id", "")).removeprefix("wood:"))
        base += (profile["workability"] - 3) * 5.0
    skills = getattr(view, "skill_defs", None) or {}
    definition = skills.get(skill_id, {}) if isinstance(skills, dict) else {}
    xp_per_level = max(1, int(definition.get("xp_per_level", 100)))
    base_level = int(definition.get("base_level", 1))
    levels = max(0, int(getattr(view, "skill_xp", {}).get(skill_id, 0)) // xp_per_level
                 - base_level)
    base += min(30.0, levels * 6.0)
    known = getattr(view, "knowledge", set())
    base += 8.0 * sum(1 for knowledge_id in knowledge_ids if knowledge_id in known)
    seed = f"{getattr(view, 'activity_sequence', 0)}:{getattr(view, 'clock', None) and view.clock.total_ticks}:{skill_id}:{material and material.get('species_id')}:{salt}"
    roll = random.Random(seed).uniform(-12.0, 18.0)
    return max(5, min(95, int(base + roll)))


# --- Expansion 05 Slice 2: assembled gear and what it actually does -----------

# Handle structure for chopping work: the durability of the handle wood drives
# how well the axe holds together under the strike (task-specific — the same
# handle says nothing about fishing). Bounded and honest: generic reads 1.0.
_HANDLE_DURABILITY_FACTOR = {5: 1.10, 4: 1.06, 3: 1.00, 2: 0.94, 1: 0.88}


def axe_handle_factor(handle_component: dict | None,
                      species_table: dict | None = None) -> float:
    """Woodcutting factor from the axe's actual handle, or 1.0 when legacy."""
    if not handle_component:
        return 1.0
    material = handle_component.get("material")
    if not isinstance(material, dict):
        return 1.0
    species_id = str(material.get("species_id") or "")
    species_row = (species_table or {}).get(species_id) if species_id else None
    profile = wood_profile(species_row,
                           str(material.get("id", "")).removeprefix("wood:"))
    return _HANDLE_DURABILITY_FACTOR.get(profile["durability"], 1.0)


# Rod-body flexibility shifts catch odds within bounded limits; so does the
# workmanship of the hook that has to hold the bite. A stiff rod is awkward,
# not forbidden; legacy gear without components reads exactly as before.
_FLEXIBILITY_CATCH_DELTA = {5: 0.08, 4: 0.05, 3: 0.02, 2: -0.04, 1: -0.08}
_HOOK_QUALITY_CATCH_DELTA = {"Excellent": 0.06, "Good": 0.04, "Serviceable": 0.02}


def rod_catch_delta(rod_body_component: dict | None,
                    hook_component: dict | None,
                    species_table: dict | None = None) -> float:
    """Bounded catch-chance delta from the rod's actual body and hook."""
    delta = 0.0
    if rod_body_component and isinstance(rod_body_component.get("material"), dict):
        material = rod_body_component["material"]
        species_id = str(material.get("species_id") or "")
        species_row = (species_table or {}).get(species_id) if species_id else None
        profile = wood_profile(species_row,
                               str(material.get("id", "")).removeprefix("wood:"))
        delta += _FLEXIBILITY_CATCH_DELTA.get(profile["flexibility"], 0.0)
    if hook_component and hook_component.get("quality") is not None:
        delta += _HOOK_QUALITY_CATCH_DELTA.get(
            quality_band(int(hook_component["quality"])), 0.0)
    return max(-0.12, min(0.12, delta))


def component_label(component: dict, species_table: dict | None = None) -> str:
    """One readable component: material first, then its workmanship."""
    item_id = str(component.get("item_id", ""))
    bits = []
    if isinstance(component.get("material"), dict) and component["material"].get("id"):
        bits.append(material_label(component["material"], species_table))
    if component.get("quality") is not None:
        bits.append(f"{quality_band(int(component['quality']))} workmanship")
    return " · ".join(bits) if bits else item_id.replace("_", " ")


def composition_summary(components: list | None,
                        species_table: dict | None = None) -> str:
    """Compact read: 'island hardwood handle + iron fitting'."""
    if not components:
        return ""
    return " + ".join(component_label(component, species_table)
                      for component in components)


def composition_text(item: dict, species_table: dict | None = None) -> str:
    """One examine line describing the finished gear's actual make-up."""
    components = item.get("components")
    if not components:
        return ""
    parts = []
    for component in components:
        slot = str(component.get("slot", "")).replace("_", " ")
        label = component_label(component, species_table)
        parts.append(f"{slot}: {label}" if slot else label)
    return " · ".join(parts)
