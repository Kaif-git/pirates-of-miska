"""P6 weapon identity, lasting injury, and deterministic severe outcomes."""
from copy import deepcopy

import pytest

from pirates.duel_system.engine import Choice, commit_action, legality
from pirates.duel_system.model import (
    ActionKind, ArmorPiece, EventType, GroundWeapon, PermanentInjuries, Severity, Status, Weapon, Wound, Zone,
)
from pirates.duel_system.session import DuelSession
from pirates.duel_system.stats import effective_stats, weapon_edge
from pirates.duel_system.guide import legal_choices
from pirates.duel_system.engine import apply_weapon_stress
from pirates.duel_system.wounds import apply_wound
from test_duel_system_scheduler import advance_to, arena


def test_riposte_disarm_moves_the_exact_weapon_to_an_occupied_ground_tile():
    state = arena()
    player, ronin = state.fighters
    player.strength, player.accuracy, player.riposte = 40, 100, True
    ronin.strength = 1
    original = ronin.equipment.weapon
    original.condition = 73

    commit_action(state, ronin.id, Choice(ActionKind.HEAVY, Zone.HEAD))
    commit_action(state, player.id, Choice(ActionKind.DISARM, Zone.HAND))
    advance_to(state, player.action.strike_tick)

    assert ronin.disarmed and ronin.equipment.weapon is None
    assert len(state.ground_weapons) == 1
    dropped = state.ground_weapons[0]
    assert dropped.weapon.item_id == original.item_id
    assert dropped.weapon.condition == 73
    assert dropped.pos == ronin.pos  # ground items may share an occupied tile
    assert len({item.weapon.item_id for item in state.ground_weapons}) == 1
    assert any(event.type == EventType.WEAPON_DROP and event.deltas["cause"] == "DISARM"
               for event in state.event_log)


def test_deep_and_severed_hand_wounds_drop_weapon_and_record_permanent_loss():
    state = arena()
    attacker, defender = state.fighters
    weapon_id = defender.equipment.weapon.item_id
    apply_wound(state, attacker, defender, Zone.HAND, Severity.DEEP, 30, 26)
    assert defender.disarmed and defender.equipment.weapon is None
    assert defender.permanent_injuries.hand_severed is False
    assert state.ground_weapons[0].weapon.item_id == weapon_id

    apply_wound(state, attacker, defender, Zone.HAND, Severity.SEVERED, 30, 30)
    assert defender.hand_severed
    assert defender.permanent_injuries.hand_severed
    assert "severed hand" in legality(state, defender.id,
                                       Choice(ActionKind.PICKUP, item_id=weapon_id)).lower()


def test_pickup_selects_one_ground_weapon_and_drops_replaced_weapon_without_duplication():
    state = arena()
    player, _ = state.fighters
    first = player.equipment.weapon
    replacement = Weapon(item_id="dropped:ronin", name="Worn cutlass", material="IRON", condition=61)
    state.ground_weapons.append(GroundWeapon(replacement, player.pos))
    assert not legality(state, player.id, Choice(ActionKind.PICKUP, item_id=replacement.item_id))
    assert Choice(ActionKind.PICKUP, item_id=replacement.item_id) in legal_choices(
        state, player.id, affordable=False)

    commit_action(state, player.id, Choice(ActionKind.PICKUP, item_id=replacement.item_id))
    advance_to(state, player.action.end_tick)

    assert player.equipment.weapon.item_id == replacement.item_id
    assert player.equipment.weapon.condition == 61
    assert not player.disarmed
    assert {item.weapon.item_id for item in state.ground_weapons} == {first.item_id}
    assert state.ground_weapons[0].pos == player.pos
    assert any(event.type == EventType.WEAPON_PICKUP
               and event.deltas["item_id"] == replacement.item_id for event in state.event_log)


def test_pickup_rejects_missing_broken_and_out_of_range_items():
    state = arena()
    player, _ = state.fighters
    assert "select" in legality(state, player.id, Choice(ActionKind.PICKUP)).lower()
    broken = Weapon(item_id="broken", name="Broken blade", condition=0)
    far = Weapon(item_id="far", name="Far blade", condition=80)
    state.ground_weapons.extend((GroundWeapon(broken, player.pos), GroundWeapon(far, (8, 3))))
    assert "broken" in legality(state, player.id, Choice(ActionKind.PICKUP, item_id="broken")).lower()
    assert "range" in legality(state, player.id, Choice(ActionKind.PICKUP, item_id="far")).lower()
    player.hand_severed = True
    assert "severed" in legality(state, player.id, Choice(ActionKind.PICKUP, item_id="far")).lower()


def test_simultaneous_pickup_contention_cannot_duplicate_ground_item_identity():
    state = arena()
    item = Weapon(item_id="only-one", name="Shared dropped sword", condition=53)
    state.ground_weapons.append(GroundWeapon(item, (1, 0)))
    choices = [Choice(ActionKind.PICKUP, item_id=item.item_id) for _ in state.fighters]
    assert all(not legality(state, fighter.id, choice)
               for fighter, choice in zip(state.fighters, choices))
    for fighter, choice in zip(state.fighters, choices):
        commit_action(state, fighter.id, choice)
    advance_to(state, max(fighter.action.end_tick for fighter in state.fighters))
    holders = [fighter.id for fighter in state.fighters
               if fighter.equipment.weapon and fighter.equipment.weapon.item_id == item.item_id]
    assert len(holders) == 1
    assert not any(row.weapon.item_id == item.item_id for row in state.ground_weapons)
    assert not any(fighter.equipment.weapon and fighter.equipment.weapon.item_id == item.item_id
                   for fighter in state.fighters if fighter.id != holders[0])


def test_weapon_wear_is_limited_to_hard_contacts_and_material_condition_change_wear():
    state = arena()
    player, ronin = state.fighters
    player.equipment.weapon.material = "WOOD"
    player.equipment.weapon.condition = 1
    state.config["weapon_integrity"]["hard_contact_wear"]["PARRY"] = 18
    ronin.accuracy = 100
    commit_action(state, ronin.id, Choice(ActionKind.GUARD))
    commit_action(state, player.id, Choice(ActionKind.LIGHT, Zone.TORSO))
    advance_to(state, player.action.strike_tick)
    assert player.equipment.weapon is None and player.disarmed
    assert any(event.type == EventType.WEAPON_BREAK for event in state.event_log)
    broken = next(item for item in state.ground_weapons if item.weapon.condition == 0)
    assert broken.weapon.material == "WOOD"

    state = arena()
    player = state.fighters[0]
    before = player.equipment.weapon.condition
    sharp_edge = weapon_edge(player, state.config)
    player.equipment.weapon.condition = 0
    assert weapon_edge(player, state.config) < sharp_edge
    player.equipment.weapon.condition = before
    commit_action(state, player.id, Choice(ActionKind.LIGHT, Zone.TORSO))
    advance_to(state, player.action.strike_tick)
    assert player.equipment.weapon.condition == before


def test_hard_contact_wear_respects_material_and_current_condition():
    state = arena()
    player, _ = state.fighters
    player.equipment.weapon.material = "STEEL"
    apply_weapon_stress(state, player, "HEAVY_BLOCK")
    steel_wear_at_full = 100 - player.equipment.weapon.condition
    player.equipment.weapon.material = "IRON"
    player.equipment.weapon.condition = 100
    apply_weapon_stress(state, player, "HEAVY_BLOCK")
    iron_wear_at_full = 100 - player.equipment.weapon.condition
    assert steel_wear_at_full < iron_wear_at_full

    player.equipment.weapon.material = "STEEL"
    player.equipment.weapon.condition = 25
    apply_weapon_stress(state, player, "HEAVY_BLOCK")
    steel_wear_at_low = 25 - player.equipment.weapon.condition
    assert steel_wear_at_low > steel_wear_at_full


def test_only_clean_heavy_cutting_hand_contact_severs_and_head_incapacity_is_not_death():
    # Seed 2 resolves the clean hand contact to the right wrist severance site.
    state = arena(rng_seed=2)
    player, ronin = state.fighters
    player.strength, player.accuracy = 100, 100
    state.config["accuracy"]["jitter"] = 0
    commit_action(state, ronin.id, Choice(ActionKind.GUARD))
    commit_action(state, player.id, Choice(ActionKind.HEAVY, Zone.HAND))
    advance_to(state, player.action.strike_tick)
    assert ronin.hand_severed
    assert ronin.permanent_injuries.severed_hands == ["RIGHT"]
    assert ronin.equipment.weapon is None
    hit = next(event for event in state.event_log if event.type == EventType.HIT)
    assert hit.deltas["function"] == "weapon hand severed; cannot hold a weapon"
    assert any(event.type == EventType.SEVER for event in state.event_log)

    state = arena()
    player, ronin = state.fighters
    player.strength, player.accuracy = 100, 100
    player.equipment.weapon.damage_type = "PIERCING"
    commit_action(state, ronin.id, Choice(ActionKind.GUARD))
    commit_action(state, player.id, Choice(ActionKind.HEAVY, Zone.HAND))
    advance_to(state, player.action.strike_tick)
    assert not ronin.hand_severed

    state = arena()
    player, ronin = state.fighters
    player.strength, player.accuracy = 14, 100
    state.config["accuracy"]["jitter"] = 0
    commit_action(state, player.id, Choice(ActionKind.HEAVY, Zone.HEAD))
    advance_to(state, player.action.strike_tick)
    assert ronin.unconscious and not ronin.decapitated and ronin.blood > 0
    assert state.end_reason == "unconscious"
    assert not ronin.can_act(state.tick)
    carried_winner = DuelSession(state, None, "off").restart(carry=True)
    assert carried_winner.player.can_act(0)


def test_supporting_hand_severance_persists_without_disabling_weapon_hand():
    state = arena()
    attacker, defender = state.fighters
    weapon = defender.equipment.weapon
    apply_wound(state, attacker, defender, Zone.HAND, Severity.SEVERED,
                30, 30, site="WRIST", side="LEFT")

    assert defender.permanent_injuries.severed_hands == ["LEFT"]
    assert not defender.hand_severed
    assert not defender.disarmed
    assert defender.equipment.weapon is weapon
    assert commit_action(state, defender.id, Choice(ActionKind.LIGHT, Zone.TORSO))
    wound = next(event for event in state.event_log if event.type == EventType.WOUND)
    assert wound.deltas["function"] == "supporting hand severed; one-handed weapon remains usable"
    restored = type(state).from_data(state.to_data())
    carried = restored.fighter(defender.id)
    assert carried.permanent_injuries.severed_hands == ["LEFT"]
    assert carried.weapon_hand == "RIGHT"
    assert not carried.hand_severed


def test_explicit_clean_eye_contact_is_persistent_without_changing_accuracy():
    state = arena()
    state.config["accuracy"]["jitter"] = 0
    player, ronin = state.fighters
    player.strength, player.accuracy = 50, 100
    player.equipment.weapon.damage_type = "PIERCING"
    commit_action(state, player.id, Choice(ActionKind.HEAVY, Zone.EYE))
    advance_to(state, player.action.strike_tick)
    assert ronin.permanent_injuries.eyes_damaged == 1
    assert any(event.type == EventType.EYE_INJURY for event in state.event_log)
    baseline = deepcopy(ronin)
    baseline.permanent_injuries.eyes_damaged = 0
    assert effective_stats(ronin, state.config, state.tick).accuracy == effective_stats(
        baseline, state.config, state.tick).accuracy == ronin.accuracy

    player.action = None
    player.riposte = False
    player.accuracy = 20
    player.idle_since = state.tick
    ronin.action = None
    ronin.pos = (player.pos[0]+1, player.pos[1])
    commit_action(state, player.id, Choice(ActionKind.HEAVY, Zone.EYE))
    advance_to(state, player.action.strike_tick)
    assert ronin.permanent_injuries.eyes_damaged == 1
    hit = [event for event in state.event_log if event.type == EventType.HIT][-1]
    assert hit.zone == Zone.HEAD and hit.deltas["drifted"]


def test_only_clean_heavy_cutting_head_contact_at_threshold_decapitates():
    # Seed 7 resolves the actual contact to the configured neck subsite.
    state = arena(rng_seed=7)
    player, ronin = state.fighters
    player.strength, player.accuracy = 100, 100
    player.equipment.weapon.damage_type = "CUTTING"
    commit_action(state, player.id, Choice(ActionKind.HEAVY, Zone.HEAD))
    advance_to(state, player.action.strike_tick)
    assert ronin.decapitated
    hit = next(event for event in state.event_log if event.type == EventType.HIT)
    assert hit.deltas["site"] == "NECK"
    assert state.end_reason == "death" and state.winner == player.id
    assert not ronin.hand_severed
    assert any(event.type == EventType.DECAPITATION for event in state.event_log)
    assert not ronin.can_act(state.tick)
    from pirates.duel_system.notebook import Notebook
    from pirates.duel_system.render_model import build_render_model
    rendered = build_render_model(state, Notebook(state.config).public_view(), timeline=False)
    labels = [popup.text for popup in rendered.popups]
    assert labels.count("DEATH — decapitation") == 1
    assert not any(label in {"DEEP head · concussion", "concussed", "No reflex"} for label in labels)

    state = arena(rng_seed=0)
    player, ronin = state.fighters
    player.strength, player.accuracy = 100, 100
    player.equipment.weapon.damage_type = "CUTTING"
    commit_action(state, player.id, Choice(ActionKind.HEAVY, Zone.HEAD))
    advance_to(state, player.action.strike_tick)
    hit = next(event for event in state.event_log if event.type == EventType.HIT)
    assert hit.deltas["site"] != "NECK"
    assert not ronin.decapitated

    state = arena()
    player, ronin = state.fighters
    player.strength, player.accuracy = 100, 100
    player.equipment.weapon.damage_type = "PIERCING"
    commit_action(state, player.id, Choice(ActionKind.HEAVY, Zone.HEAD))
    advance_to(state, player.action.strike_tick)
    assert not ronin.decapitated

    state = arena()
    player, ronin = state.fighters
    player.strength, player.accuracy = 100, 20
    player.equipment.weapon.damage_type = "CUTTING"
    state.config["accuracy"]["jitter"] = 0
    commit_action(state, player.id, Choice(ActionKind.HEAVY, Zone.EYE))
    advance_to(state, player.action.strike_tick)
    hit = next(event for event in state.event_log if event.type == EventType.HIT)
    assert hit.zone == Zone.HEAD and hit.deltas["drifted"]
    assert not ronin.decapitated

    state = arena()
    player, ronin = state.fighters
    player.strength, player.accuracy = 100, 100
    player.equipment.weapon.damage_type = "CUTTING"
    ronin.equipment.armor[Zone.HEAD] = ArmorPiece(value=130, weight=0)
    commit_action(state, player.id, Choice(ActionKind.HEAVY, Zone.HEAD))
    advance_to(state, player.action.strike_tick)
    assert not ronin.decapitated
    hit = next(event for event in state.event_log if event.type == EventType.HIT)
    assert hit.deltas["net"] < state.config["wound_net"]["decapitation"]


def test_permanent_injuries_and_dropped_items_survive_carry_and_world_journal_roundtrip(tmp_path):
    session = DuelSession.new(seed=73, guide_mode="off")
    player = session.player
    player.permanent_injuries = PermanentInjuries(hand_severed=False, eyes_damaged=1)
    player.equipment.weapon.condition = 42
    player.statuses.append(Status("concussed", until_tick=50, accuracy_mult=0.6))
    player.blood, player.fatigue, player.stamina = 75, 22, 40
    player.wounds.append(Wound(Zone.ARM, Severity.CUT, 3))
    session.state.tick = 20
    item = Weapon(item_id="player:spare", name="Spare steel sword", material="STEEL", condition=44)
    session.state.ground_weapons.append(GroundWeapon(item, player.pos))
    session.state.end_reason, session.state.winner = "yield", session.enemy.id

    carried = session.restart(carry=True)
    assert carried.player.permanent_injuries.eyes_damaged == 1
    assert carried.player.equipment.weapon.condition == 42
    assert carried.player.statuses[0].until_tick == 30
    assert (carried.player.blood, carried.player.fatigue, carried.player.stamina) == (75, 22, 40)
    assert carried.player.wounds[0].zone == Zone.ARM and carried.player.wounds[0].bleed_per_turn == 3
    assert carried.state.ground_weapons[0].weapon.item_id == item.item_id
    saved = carried.to_data()
    restored = DuelSession.from_data(saved)
    assert restored.state.to_data() == carried.state.to_data()
    from pirates.game import World
    world = World()
    world.duel_journal = saved
    world_path = tmp_path / "p6-world-journal-test.json"
    world.save(world_path)
    world_loaded = World.load(world_path)
    world_session = DuelSession.from_data(world_loaded.duel_journal)
    assert world_session.state.to_data() == carried.state.to_data()


def test_temporary_concussion_expires_without_healing_permanent_eye_injury():
    state = arena()
    player = state.fighters[0]
    player.permanent_injuries.eyes_damaged = 1
    player.statuses.append(Status("concussed", until_tick=2, accuracy_mult=0.6))
    from pirates.duel_system.engine import advance_one_tick
    advance_one_tick(state)
    assert any(status.name == "concussed" for status in player.statuses)
    advance_one_tick(state)
    assert not any(status.name == "concussed" for status in player.statuses)
    assert player.permanent_injuries.eyes_damaged == 1


def test_eye_injury_nervousness_and_status_do_not_change_authored_accuracy():
    state = arena()
    player = state.fighters[0]
    player.accuracy = 1
    player.nervousness = 1
    player.permanent_injuries.eyes_damaged = 2
    player.statuses.append(Status("dizzy", until_tick=20, accuracy_mult=0.01))
    assert effective_stats(player, state.config, state.tick).accuracy == player.accuracy


def test_bilateral_eye_impairment_blocks_precise_targeting_without_accuracy_debuff():
    state = arena()
    attacker, target = state.fighters
    attacker.permanent_injuries.eyes_damaged = 1
    target.permanent_injuries.eyes_damaged = 1
    eye_shot = Choice(ActionKind.LIGHT, Zone.EYE, target_id=target.id)
    assert legality(state, attacker.id, eye_shot) == ""

    target.permanent_injuries.eyes_damaged = 2
    assert legality(state, attacker.id, eye_shot) == "No functioning eye remains to target"
    target.permanent_injuries.eyes_damaged = 0
    attacker.permanent_injuries.eyes_damaged = 2
    assert legality(state, attacker.id, eye_shot) == \
        "Bilateral eye injury prevents acquiring a precise eye target"
    assert effective_stats(attacker, state.config, state.tick).accuracy == attacker.accuracy

    from pirates.duel_system.notebook import Notebook
    from pirates.duel_system.render_model import build_render_model
    model = build_render_model(state, Notebook(state.config).public_view(), timeline=False)
    eye_option = next(option for option in model.zones if option.zone == Zone.EYE)
    assert "Bilateral eye injury" in eye_option.blocked_reason


def test_unconscious_or_dead_fighter_cannot_carry_to_another_duel():
    cases = (("unconscious", True, "unconscious", 100),
             ("blood", 0, "death", 0),
             ("decapitated", True, "death", 0))
    for field, value, reason, blood in cases:
        session = DuelSession.new(guide_mode="off")
        setattr(session.player, field, value)
        session.player.blood = blood
        session.state.end_reason, session.state.winner = reason, session.enemy.id
        with pytest.raises(ValueError):
            session.restart(carry=True)


def test_v5_session_migration_adds_weapon_eye_and_ground_item_defaults():
    session = DuelSession.new(seed=83, guide_mode="off")
    data = session.to_data()
    data["version"] = data["state"]["schema_version"] = data["state"]["config"]["schema_version"] = 5
    config = data["state"]["config"]
    for key in ("weapon_integrity",):
        config.pop(key, None)
    config["schema_version"] = 5
    for fighter in data["state"]["fighters"]:
        fighter["hand_severed"] = True
        fighter.pop("permanent_injuries", None)
        fighter["equipment"]["weapon"].pop("item_id", None)
        fighter["equipment"]["weapon"].pop("name", None)
        fighter["equipment"]["weapon"].pop("material", None)
        fighter["equipment"]["weapon"].pop("condition", None)
    data["state"].pop("ground_weapons", None)

    restored = DuelSession.from_data(data)
    assert restored.state.config["schema_version"] == 18
    assert all(fighter.permanent_injuries.hand_severed for fighter in restored.state.fighters)
    assert all(fighter.equipment.weapon is None for fighter in restored.state.fighters)
    assert {item.weapon.item_id for item in restored.state.ground_weapons} == {"player:weapon", "ronin:weapon"}


def test_v5_migration_preserves_active_action_deadline_and_world_journal_continuation(tmp_path):
    session = DuelSession.new(seed=84, guide_mode="off")
    player = session.player
    action = commit_action(session.state, player.id, Choice(ActionKind.RUN, direction=(0, 1), tiles=2))
    old_deadline = action.end_tick
    data = session.to_data()
    data["version"] = data["state"]["schema_version"] = data["state"]["config"]["schema_version"] = 5
    config = data["state"]["config"]
    config.pop("weapon_integrity", None)
    config["schema_version"] = 5
    for fighter in data["state"]["fighters"]:
        weapon = fighter["equipment"]["weapon"]
        if weapon:
            for key in ("item_id", "name", "material", "condition"):
                weapon.pop(key, None)
        fighter.pop("permanent_injuries", None)

    from pirates.game import World
    world = World()
    world.duel_journal = data
    save_path = tmp_path / "p6-v5-world.json"
    world.save(save_path)
    loaded = World.load(save_path)
    restored = DuelSession.from_data(loaded.duel_journal)
    assert restored.player.action.end_tick == old_deadline
    assert restored.state.config["schema_version"] == 18
    assert restored.state.to_data()["rng_draws"] == session.state.to_data()["rng_draws"]
    replay = DuelSession.from_data(loaded.duel_journal)
    from pirates.duel_system.engine import advance_one_tick
    while restored.state.tick < old_deadline:
        advance_one_tick(restored.state)
        advance_one_tick(replay.state)
    assert restored.state.to_data() == replay.state.to_data()
