"""P3 localized structure, contact, shock, recoil and anti-chain gates."""
from copy import deepcopy

import pytest

from pirates.duel_system.engine import Choice, advance_one_tick, commit_action
from pirates.duel_system.model import (
    ActionKind, ArmorPiece, CombatState, EventType, Equipment, Personality,
    Severity, Weapon, Wound, Zone,
)
from pirates.duel_system.wounds import classify_wound
from test_duel_system_foundation import bare_fighter
from test_duel_system_scheduler import arena, advance_to


def hit_once(*, profile="recruit", zone=Zone.ARM, strength=12, damage_type="CUTTING",
             position=(1, 0), walkable=None, armor=0):
    state = arena(walkable=walkable)
    attacker, defender = state.fighters
    attacker.strength = strength
    attacker.equipment.weapon = Weapon(edge=4, weight=0, damage_type=damage_type)
    defender.pos = position
    if armor:
        defender.equipment.armor[zone] = ArmorPiece(value=armor, weight=0)
    if profile:
        values = deepcopy(state.config["ronin"])
        values.update(deepcopy(state.config["ai_profiles"][profile]))
        values["archetype"] = profile
        defender.personality = Personality(**values)
    commit_action(state, attacker.id, Choice(ActionKind.LIGHT, zone))
    advance_to(state, attacker.action.strike_tick)
    return state, attacker, defender


def test_legacy_wound_and_weapon_rows_gain_coarse_anatomy_defaults():
    wound = Wound(Zone.ARM, Severity.DEEP, 4)
    assert (wound.depth, wound.structure, wound.damage_type) == ("DEEP", "TENDON", "CUTTING")
    weapon = Weapon()
    assert weapon.damage_type == "CUTTING"


def test_contact_types_change_bleeding_and_structure_without_lethal_lottery():
    cfg = arena().config
    cutting = Wound(Zone.ARM, Severity.CUT, 3, damage_type="CUTTING")
    piercing = Wound(Zone.ARM, Severity.CUT, 3, damage_type="PIERCING")
    blunt = Wound(Zone.ARM, Severity.CUT, 3, damage_type="BLUNT")
    assert cutting.structure == "MUSCLE"
    assert piercing.structure == "PENETRATING_SOFT_TISSUE"
    assert blunt.structure == "MUSCLE_CONTUSION"
    assert cfg["injury"]["damage_types"]["CUTTING"]["bleed_mult"] > cfg["injury"]["damage_types"]["BLUNT"]["bleed_mult"]
    assert cfg["injury"]["damage_types"]["BLUNT"]["shock_mult"] > cfg["injury"]["damage_types"]["CUTTING"]["shock_mult"]
    assert classify_wound(cfg, Zone.HAND, 30, ActionKind.HEAVY, False, "BLUNT") == Severity.DEEP
    assert classify_wound(cfg, Zone.HAND, 30, ActionKind.HEAVY, False, "CUTTING") == Severity.SEVERED


@pytest.mark.parametrize("damage_type,structure", [
    ("CUTTING", "MUSCLE"),
    ("PIERCING", "PENETRATING_SOFT_TISSUE"),
    ("BLUNT", "MUSCLE_CONTUSION"),
])
def test_each_contact_type_resolves_after_armor_with_distinct_tissue_and_bleeding(damage_type, structure):
    state, _, defender = hit_once(profile=None, damage_type=damage_type, strength=12, armor=4)
    hit = next(event for event in state.event_log if event.type == EventType.HIT)
    base_bleed = state.config["wounds"]["ARM"]["CUT"]["bleed"]
    multiplier = state.config["injury"]["damage_types"][damage_type]["bleed_mult"]
    assert hit.deltas["net"] == 12
    assert hit.severity == Severity.CUT
    assert hit.deltas["structure"] == structure
    assert defender.wounds[0].bleed_per_turn == pytest.approx(base_bleed * multiplier)


@pytest.mark.parametrize("profile,expected_response", [("veteran", "FLINCH"), ("recruit", "RECOIL")])
def test_same_deep_injury_has_same_function_but_composure_changes_reflex(profile, expected_response):
    state, _, defender = hit_once(profile=profile, strength=12)
    hit = next(event for event in state.event_log if event.type == EventType.HIT)
    assert hit.severity == Severity.DEEP
    assert hit.deltas["structure"] == "TENDON"
    assert hit.deltas["depth"] == "DEEP"
    assert hit.deltas["shock_response"] == expected_response
    assert any(status.name == "weak_grip" and status.strength_mult == 0.65 for status in defender.statuses)
    if expected_response == "RECOIL":
        assert defender.pos == (2, 0)
    else:
        assert defender.pos == (1, 0)
    saved = state.to_data()
    restored = CombatState.from_data(saved)
    assert restored.to_data() == saved
    advance_one_tick(state)
    advance_one_tick(restored)
    assert restored.to_data() == state.to_data()


def test_after_armor_net_controls_depth_and_injury_description_is_recorded():
    state = arena()
    attacker, defender = state.fighters
    attacker.strength = 10  # power 14
    defender.equipment.armor[Zone.ARM] = ArmorPiece(value=8, weight=0)
    attacker.equipment.weapon = Weapon(edge=4, weight=0, damage_type="PIERCING")
    commit_action(state, attacker.id, Choice(ActionKind.LIGHT, Zone.ARM))
    advance_to(state, attacker.action.strike_tick)
    hit = next(event for event in state.event_log if event.type == EventType.HIT)
    assert hit.deltas["power"] == 14
    assert hit.deltas["armor"] == 8
    assert hit.deltas["net"] == 6
    assert hit.severity == Severity.GRAZE
    assert hit.deltas["depth"] == "SUPERFICIAL"
    assert hit.deltas["structure"] == "SKIN"
    assert defender.wounds[0].bleed_per_turn == pytest.approx(
        state.config["wounds"]["ARM"]["GRAZE"]["bleed"] *
        state.config["injury"]["damage_types"]["PIERCING"]["bleed_mult"])


def test_open_recoil_respects_walls_and_reports_braced_result():
    state, _, defender = hit_once(profile="recruit", strength=12,
                                  walkable={(0, 0), (1, 0)})
    assert defender.pos == (1, 0)
    response = next(event for event in state.event_log if event.type == EventType.INJURY_RESPONSE)
    assert response.deltas["response"] == "BRACED"
    assert response.deltas["blocker"] == "ARENA_EDGE"
    assert "RECOIL_BLOCKED" in response.cause_tags


def test_recoil_respects_another_occupied_tile_without_overlap():
    state, _, defender = hit_once(profile="recruit", strength=12)
    blocker = bare_fighter("blocker", pos=(2, 0))
    state.fighters.append(blocker)
    defender.pos = (1, 0)
    defender.last_recoil_tick = None
    # Resolve the same deterministic injury again while the recoil tile is occupied.
    from pirates.duel_system.wounds import resolve_pending_injury_responses
    state.pending_injury_responses.append({
        "fighter_id": defender.id, "attacker_id": state.fighters[0].id,
        "zone": Zone.ARM.value, "severity": Severity.DEEP.value,
        "depth": "DEEP", "structure": "TENDON", "shock_load": 60,
        "decision_control": 0.2, "response": "RECOIL",
    })
    resolve_pending_injury_responses(state)
    response = next(event for event in reversed(state.event_log) if event.type == EventType.INJURY_RESPONSE)
    assert response.deltas["response"] == "BRACED"
    assert response.deltas["blocker"] == blocker.id
    assert defender.pos == (1, 0) and blocker.pos == (2, 0)


def test_recoil_cooldown_prevents_repeat_displacement_without_suppressing_wound():
    state = arena()
    attacker, defender = state.fighters
    attacker.speed = 20
    attacker.strength = 12
    attacker.equipment.weapon = Weapon(edge=4, reach=2, weight=0)
    values = deepcopy(state.config["ronin"])
    values.update(deepcopy(state.config["ai_profiles"]["recruit"]))
    values["archetype"] = "recruit"
    defender.personality = Personality(**values)
    commit_action(state, attacker.id, Choice(ActionKind.LIGHT, Zone.ARM))
    advance_to(state, attacker.action.end_tick)
    assert defender.pos == (2, 0)
    first_recoil_tick = defender.last_recoil_tick
    commit_action(state, attacker.id, Choice(ActionKind.LIGHT, Zone.ARM))
    advance_to(state, attacker.action.strike_tick)
    assert len(defender.wounds) == 2
    assert defender.last_recoil_tick == first_recoil_tick
    responses = [event for event in state.event_log if event.type == EventType.INJURY_RESPONSE]
    assert [event.deltas["response"] for event in responses] == ["RECOIL", "REFRACTORY"]
    assert defender.pos == (2, 0)


def test_same_tick_strikes_resolve_before_either_shock_recoil():
    state = arena()
    first, second = state.fighters
    for fighter in state.fighters:
        fighter.strength = 12
        fighter.equipment.weapon = Weapon(edge=4, weight=0, damage_type="BLUNT")
        values = deepcopy(state.config["ronin"])
        values.update(deepcopy(state.config["ai_profiles"]["recruit"]))
        values["archetype"] = "recruit"
        fighter.personality = Personality(**values)
        commit_action(state, fighter.id, Choice(ActionKind.LIGHT, Zone.ARM))
    advance_to(state, first.action.strike_tick)
    assert {event.actor for event in state.event_log if event.type == EventType.HIT} == {first.id, second.id}
    assert first.pos == (0, 0) and second.pos == (2, 0)
    assert len([event for event in state.event_log if event.type == EventType.INJURY_RESPONSE]) == 2
    assert all(fighter.action is not None for fighter in state.fighters)
    assert not any(event.type == EventType.INTERRUPT for event in state.event_log)


def test_recoil_during_recovery_does_not_cancel_an_already_landed_commitment():
    state = arena()
    attacker, defender = state.fighters
    attacker.speed = 20
    attacker.strength = 20
    attacker.equipment.weapon = Weapon(edge=4, weight=0, damage_type="BLUNT")
    defender.strength = 12
    values = deepcopy(state.config["ronin"])
    values.update(deepcopy(state.config["ai_profiles"]["recruit"]))
    values["archetype"] = "recruit"
    defender.personality = Personality(**values)
    commit_action(state, defender.id, Choice(ActionKind.LIGHT, Zone.TORSO))
    advance_to(state, defender.action.strike_tick)
    assert defender.action is not None and defender.action.phase_at(state.tick) == "ACTIVE"
    # The attack struck at t4; the opponent commits at t5 and lands during recovery.
    state.tick = state.tick + 1
    attacker.action = None
    commit_action(state, attacker.id, Choice(ActionKind.LIGHT, Zone.ARM))
    advance_to(state, attacker.action.strike_tick)
    assert defender.action is not None and defender.action.phase_at(state.tick) == "RECOVERY"
    assert defender.last_recoil_tick == state.tick
    assert defender.action.kind == ActionKind.LIGHT
    assert not any(event.type == EventType.INTERRUPT and event.actor == defender.id for event in state.event_log)


def test_recoil_interrupts_unresolved_movement_after_same_tick_strikes_without_refund():
    state = arena()
    attacker, defender = state.fighters
    attacker.strength = 12
    values = deepcopy(state.config["ronin"])
    values.update(deepcopy(state.config["ai_profiles"]["recruit"]))
    values["archetype"] = "recruit"
    defender.personality = Personality(**values)
    commit_action(state, defender.id, Choice(ActionKind.WALK, direction=(0, 1)))
    paid_stamina = defender.stamina
    commit_action(state, attacker.id, Choice(ActionKind.LIGHT, Zone.ARM))
    advance_to(state, attacker.action.strike_tick)
    assert defender.action is None
    assert defender.pos == (2, 0)
    assert defender.stamina == pytest.approx(paid_stamina + 0.8)  # the committed cost was not refunded
    assert len([event for event in state.event_log if event.type == EventType.ACTION_START]) == 2
    assert any(event.type == EventType.INTERRUPT and event.actor == defender.id for event in state.event_log)
    advance_to(state, 10)
    assert defender.pos == (2, 0)  # canceled walk path did not execute afterward


def test_head_incapacitation_and_blood_loss_remain_explicit_not_random():
    state = arena()
    attacker, defender = state.fighters
    attacker.strength = 20
    commit_action(state, attacker.id, Choice(ActionKind.LIGHT, Zone.HEAD))
    advance_to(state, attacker.action.strike_tick)
    assert defender.unconscious
    assert next(event for event in state.event_log if event.type == EventType.HIT).severity == Severity.DEEP

    bleeding = arena()
    target = bleeding.fighters[1]
    target.blood = 0.05
    target.wounds = [Wound(Zone.TORSO, Severity.DEEP, 6)]
    advance_one_tick(bleeding)
    assert bleeding.end_reason == "death"
    assert bleeding.winner == bleeding.fighters[0].id


def test_old_weapon_and_wound_json_migrate_and_mid_action_replays():
    state = arena(rng_seed=77)
    state.fighters[0].wounds = [Wound(Zone.LEG, Severity.CUT, 2)]
    commit_action(state, state.fighters[0].id, Choice(ActionKind.DASH, direction=(0, 1), tiles=2))
    data = state.to_data()
    data["schema_version"] = 2
    data["config"]["schema_version"] = 2
    data["config"].pop("injury")
    for fighter in data["fighters"]:
        fighter["equipment"]["weapon"].pop("damage_type", None)
        fighter.pop("last_recoil_tick", None)
        for wound in fighter["wounds"]:
            wound.pop("depth", None)
            wound.pop("structure", None)
            wound.pop("damage_type", None)
    migrated = CombatState.from_data(data)
    assert migrated.config["schema_version"] == 7
    assert migrated.fighters[0].equipment.weapon.damage_type == "CUTTING"
    assert migrated.fighters[0].wounds[0].structure == "MUSCLE"
    expected = migrated.to_data()
    clone = CombatState.from_data(expected)
    while migrated.tick < migrated.fighters[0].action.path[0][0]:
        advance_one_tick(migrated)
        advance_one_tick(clone)
    assert clone.to_data() == migrated.to_data()


def test_v2_config_file_migrates_through_the_public_loader(tmp_path):
    import json
    from pirates.duel_system.config import load_config

    old = deepcopy(load_config())
    old["schema_version"] = 2
    old.pop("injury")
    path = tmp_path / "combat-v2.json"
    path.write_text(json.dumps(old), encoding="utf-8")
    migrated = load_config(path)
    assert migrated["schema_version"] == 7
    assert migrated["actions"] == old["actions"]
    assert migrated["injury"]["recoil_cooldown_ticks"] == 10


def test_v2_session_journal_migrates_config_notebook_and_active_injury_fields():
    from pirates.duel_system.session import DuelSession
    from pirates.duel_system.notebook import Notebook

    session = DuelSession.new(seed=23, guide_mode="off")
    session.select(Choice(ActionKind.DASH, direction=(0, 1), tiles=2))
    assert session.confirm()
    old_path = deepcopy(session.player.action.path)
    data = session.to_data()
    data["version"] = data["state"]["schema_version"] = data["state"]["config"]["schema_version"] = 2
    data["state"]["config"].pop("injury")
    data["notebook"]["version"] = 2
    for fighter in data["state"]["fighters"]:
        fighter.pop("last_recoil_tick", None)
        fighter["equipment"]["weapon"].pop("damage_type", None)
        for wound in fighter["wounds"]:
            wound.pop("depth", None)
            wound.pop("structure", None)
            wound.pop("damage_type", None)
    restored = DuelSession.from_data(data)
    assert restored.to_data()["version"] == 7
    assert restored.player.action.path == old_path
    assert restored.player.equipment.weapon.damage_type == "CUTTING"
    assert restored.notebook.to_data()["version"] == 7


def test_render_details_and_coach_explain_wound_without_private_shock_values():
    from pirates.duel_coach import coach, strategy_rows
    from pirates.duel_system.notebook import Notebook
    from pirates.duel_system.session import DuelSession
    from pirates.duel_ui import DuelUI

    state, _, defender = hit_once(profile=None, strength=12)
    state.fighters[0].wounds = deepcopy(defender.wounds)
    state.fighters[0].statuses = deepcopy(defender.statuses)
    session = DuelSession(state, Notebook(state.config), guide_mode="off")
    model = DuelUI(session, rounds=True).model()
    assert model.enemy_injury_rows[0].startswith("Arm deep tendon: weak grip")
    advice = coach(model)
    rows = strategy_rows(model, advice)
    assert any("Your injury: Arm deep tendon" in row for row in advice.cautions)
    assert any("YOUR CURRENT INJURIES" == row for row in rows)
    assert not any("decision_control" in row or "shock_load" in row for row in rows)
    assert not any("decision_control" in row or "shock_load" in row for row in model.full_log)
    from pirates.duel_system.exchange_log import format_exchange
    recap = format_exchange(state.event_log, {"player": "You", "ronin": "Ronin"})
    assert any("deep tendon" in row and "function: weak grip" in row for row in recap)
    assert not any("decision_control" in row or "shock_load" in row for row in recap)
