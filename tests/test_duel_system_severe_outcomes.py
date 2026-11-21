"""P6 weapon identity, lasting injury, and deterministic severe outcomes."""
from copy import deepcopy

import pytest

from pirates.duel_system.engine import Choice, commit_action, legality
from pirates.duel_system.model import (
    ActionKind, ArmorPiece, CombatState, EventType, GroundWeapon, PermanentInjuries, Severity, Status, Weapon, Wound, Zone,
)
from pirates.duel_system.session import DuelSession
from pirates.duel_system.stats import effective_stats, weapon_edge
from pirates.duel_system.guide import legal_choices
from pirates.duel_system.engine import apply_weapon_stress
from pirates.duel_system.wounds import apply_wound
from test_duel_system_scheduler import advance_to, arena
from duel_test_helpers import force_clean_aims


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
    assert dropped.pos == (1, 0)  # the one-tile forced reflex moved the struck fighter
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
    force_clean_aims(state.config)
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
    force_clean_aims(state.config)
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


def test_eye_trauma_has_no_direct_accuracy_penalty_beyond_fear_and_anger():
    state = arena()
    force_clean_aims(state.config)
    state.config["anatomy_sites"]["EYE"] = ["LEFT_EYE", "RIGHT_EYE"]
    player, ronin = state.fighters
    player.strength, player.accuracy = 50, 100
    player.equipment.weapon.damage_type = "PIERCING"
    commit_action(state, player.id, Choice(ActionKind.HEAVY, Zone.EYE))
    advance_to(state, player.action.strike_tick)
    assert ronin.permanent_injuries.eyes_damaged == 1
    assert any(event.type == EventType.EYE_INJURY for event in state.event_log)
    from pirates.duel_system.notebook import Notebook
    from pirates.duel_system.render_model import build_render_model
    eye_rows = " ".join(build_render_model(
        state, Notebook(state.config).public_view(), timeline=False).enemy_injury_rows)
    assert "threshold already banked" in eye_rows
    assert "next eligible clean eye wound permanently impairs eye 2/2" in eye_rows
    assert "/24" not in eye_rows
    baseline = deepcopy(ronin)
    baseline.permanent_injuries.eyes_damaged = 0
    assert effective_stats(ronin, state.config, state.tick).accuracy == effective_stats(
        baseline, state.config, state.tick).accuracy
    assert effective_stats(ronin, state.config, state.tick).accuracy < ronin.accuracy

    player.action = None
    player.riposte = False
    player.accuracy = 20
    player.idle_since = state.tick
    ronin.action = None
    ronin.pos = (player.pos[0]+1, player.pos[1])
    commit_action(state, player.id, Choice(ActionKind.HEAVY, Zone.EYE))
    advance_to(state, player.action.strike_tick)
    assert ronin.permanent_injuries.eyes_damaged == 2
    hit = [event for event in state.event_log if event.type == EventType.HIT][-1]
    assert hit.zone == Zone.EYE and not hit.deltas["drifted"]


def test_repeated_clean_eye_cuts_accumulate_to_a_persistent_vision_injury():
    from pirates.duel_system.wounds import apply_wound

    state = arena()
    attacker, defender = state.fighters
    attacker.equipment.weapon.damage_type = "CUTTING"
    apply_wound(state, attacker, defender, Zone.EYE, Severity.CUT,
                power=14, net=14, damage_type="CUTTING", site="EYE", side="RIGHT")
    assert defender.permanent_injuries.eyes_damaged == 0
    assert defender.permanent_injuries.eye_trauma == 14
    assert not any(event.type == EventType.EYE_INJURY for event in state.event_log)

    apply_wound(state, attacker, defender, Zone.EYE, Severity.CUT,
                power=14, net=14, damage_type="CUTTING", site="EYE", side="RIGHT")
    injury = next(event for event in state.event_log if event.type == EventType.EYE_INJURY)
    assert defender.permanent_injuries.eyes_damaged == 1
    assert injury.deltas["cumulative_contact"] is True
    assert injury.deltas["eye_trauma_before"] == 14
    assert injury.deltas["eye_trauma_threshold"] == 24
    restored = CombatState.from_data(state.to_data())
    assert restored.fighter(defender.id).permanent_injuries.eyes_damaged == 1
    assert restored.fighter(defender.id).permanent_injuries.eye_trauma == 4

    legacy_state = arena()
    commit_action(legacy_state, legacy_state.fighters[0].id, Choice(ActionKind.SHOVE))
    legacy = legacy_state.to_data()
    for fighter in legacy["fighters"]:
        fighter["permanent_injuries"].pop("eye_trauma", None)
        if fighter.get("action"):
            fighter["action"].pop("tactical_resolved", None)
    migrated = CombatState.from_data(legacy)
    assert all(fighter.permanent_injuries.eye_trauma == 0 for fighter in migrated.fighters)
    assert migrated.fighters[0].action.tactical_resolved is False


def test_only_clean_heavy_cutting_head_contact_at_threshold_decapitates():
    # Seed 7 resolves the actual contact to the configured neck subsite.
    state = arena(rng_seed=7)
    force_clean_aims(state.config)
    state.config["anatomy_sites"]["HEAD"] = ["NECK"]
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
    force_clean_aims(state.config)
    state.config["anatomy_sites"]["HEAD"] = ["SKULL"]
    player, ronin = state.fighters
    player.strength, player.accuracy = 100, 100
    player.equipment.weapon.damage_type = "CUTTING"
    commit_action(state, player.id, Choice(ActionKind.HEAVY, Zone.HEAD))
    advance_to(state, player.action.strike_tick)
    hit = next(event for event in state.event_log if event.type == EventType.HIT)
    assert hit.deltas["site"] != "NECK"
    assert not ronin.decapitated

    state = arena()
    force_clean_aims(state.config)
    state.config["anatomy_sites"]["HEAD"] = ["SKULL"]
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
    dispersion = state.config["accuracy"]["aim_dispersion"]
    dispersion.update(clean_base=0, clean_min=0, clean_max=0,
                      per_accuracy=0, difficulty_penalty=0, drift_share=1)
    dispersion["nearby_zones"]["EYE"] = ["HEAD"]
    commit_action(state, player.id, Choice(ActionKind.HEAVY, Zone.EYE))
    advance_to(state, player.action.strike_tick)
    hit = next(event for event in state.event_log if event.type == EventType.HIT)
    assert hit.zone == Zone.HEAD and hit.deltas["drifted"]
    assert not ronin.decapitated

    state = arena()
    force_clean_aims(state.config)
    state.config["anatomy_sites"]["HEAD"] = ["SKULL"]
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
    player.permanent_injuries = PermanentInjuries(hand_severed=False, eyes_damaged=1,
                                                   severed_ears=["LEFT"])
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
    assert carried.player.permanent_injuries.severed_ears == ["LEFT"]
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


def test_bilateral_eye_impairment_limits_sight_range_without_changing_accuracy():
    state = DuelSession.new(seed=92, guide_mode="off").state
    observer, target = state.fighters
    observer.accuracy = 17
    observer.permanent_injuries.eyes_damaged = 1
    assert state.can_see(observer, target)
    observer.permanent_injuries.eyes_damaged = 2
    assert not state.can_see(observer, target)
    assert effective_stats(observer, state.config, state.tick).accuracy == observer.accuracy
    target.pos = (observer.pos[0] + 1, observer.pos[1])
    assert state.can_see(observer, target)


@pytest.mark.parametrize("net,expected_outcome,expected_bleed", [
    (23.9, None, 6.4), (24, "LUNG_BLEED", 12), (30, "LIVER_BLEED", 12),
])
def test_vital_organ_outcomes_require_post_armor_force_and_qualified_contact(
        net, expected_outcome, expected_bleed):
    state = arena()
    attacker, defender = state.fighters
    apply_wound(state, attacker, defender, Zone.TORSO, Severity.DEEP, 50, net,
                damage_type="PIERCING", site="LUNG" if expected_outcome != "LIVER_BLEED" else "LIVER",
                side="LEFT" if expected_outcome != "LIVER_BLEED" else None)
    wound = defender.wounds[-1]
    assert wound.vital_outcome == expected_outcome
    assert wound.bleed_per_turn == expected_bleed
    assert wound.structure == ("LUNG" if expected_outcome == "LUNG_BLEED" else
                               "LIVER" if expected_outcome == "LIVER_BLEED" else "TORSO_WALL")
    assert defender.blood == 100
    restored = CombatState.from_data(state.to_data())
    restored_wound = restored.fighter(defender.id).wounds[-1]
    assert (restored_wound.vital_outcome, restored_wound.structure,
            restored_wound.bleed_per_turn, restored_wound.side) == (
                expected_outcome, wound.structure, expected_bleed, wound.side)


def test_repeated_below_gate_torso_contacts_do_not_accumulate_into_vital_death():
    state = arena()
    attacker, defender = state.fighters
    for _ in range(3):
        apply_wound(state, attacker, defender, Zone.TORSO, Severity.DEEP, 50, 23.9,
                    damage_type="PIERCING", site="HEART")
    assert len(defender.wounds) == 3
    assert all(wound.vital_outcome is None for wound in defender.wounds)
    assert defender.blood == 100
    assert not defender.unconscious


def test_seeded_torso_subsite_resolution_replays_and_nose_keeps_supported_effects_only():
    from pirates.duel_system.wounds import choose_anatomical_site, injury_profile

    first = arena(rng_seed=887)
    replay = CombatState.from_data(first.to_data())
    first_site = choose_anatomical_site(first, Zone.TORSO)
    replay_site = choose_anatomical_site(replay, Zone.TORSO)
    assert first_site == replay_site
    nose = injury_profile(first.config, Zone.HEAD, Severity.DEEP, ActionKind.HEAVY,
                          "CUTTING", 30, site="NOSE")
    assert nose["structure"] == "FACIAL_BONE"
    assert nose["function"] == "unconscious"
    assert nose["vital_outcome"] is None


@pytest.mark.parametrize("damage_type,severity,net,expected", [
    ("PIERCING", Severity.DEEP, 41.9, "HEART_BLEED"),
    ("PIERCING", Severity.DEEP, 42, "HEART_FATAL"),
    ("CUTTING", Severity.DEEP, 50, None),
    ("BLUNT", Severity.DEEP, 50, None),
    ("PIERCING", Severity.CUT, 50, None),
])
def test_heart_outcome_has_deterministic_severe_gate_and_no_random_death(
        damage_type, severity, net, expected):
    from pirates.duel_system.engine import check_end_conditions

    state = arena(rng_seed=731)
    attacker, defender = state.fighters
    apply_wound(state, attacker, defender, Zone.TORSO, severity, 60, net,
                damage_type=damage_type, site="HEART")
    wound = defender.wounds[-1]
    assert wound.vital_outcome == expected
    if expected == "HEART_FATAL":
        assert defender.blood == 0
        check_end_conditions(state)
        assert state.end_reason == "death"
        assert any(event.type == EventType.POOL_CHANGE and "HEART_FATAL" in event.cause_tags
                   for event in state.event_log)
        assert any(event.type == EventType.FIGHT_END
                   and event.deltas.get("reason") == "death" for event in state.event_log)
    else:
        assert defender.blood == 100
        if expected == "HEART_BLEED":
            assert wound.bleed_per_turn == 12
        else:
            assert wound.bleed_per_turn != 12
        if expected is None:
            assert wound.structure not in {"HEART", "LUNG", "LIVER"}


def test_right_lung_and_qualified_liver_have_distinct_site_function_and_bleed():
    state = arena()
    attacker, defender = state.fighters
    for site, side, outcome, structure in (
            ("LUNG", "RIGHT", "LUNG_BLEED", "LUNG"),
            ("LIVER", None, "LIVER_BLEED", "LIVER")):
        apply_wound(state, attacker, defender, Zone.TORSO, Severity.DEEP, 50, 24,
                    damage_type="PIERCING", site=site, side=side)
        wound = defender.wounds[-1]
        assert (wound.vital_outcome, wound.structure, wound.site, wound.side) == (
            outcome, structure, site, side)
        assert wound.bleed_per_turn == 12


def test_actual_heart_strike_uses_post_armor_net_force_and_records_cause():
    from pirates.duel_system.engine import advance_one_tick

    def strike(armor):
        session = DuelSession.new(seed=4, guide_mode="off")
        attacker, defender = session.state.fighters
        defender.pos = (attacker.pos[0] + 1, attacker.pos[1])
        session.state.config["anatomy_sites"]["TORSO"] = ["HEART"]
        dispersion = session.state.config["accuracy"]["aim_dispersion"]
        dispersion.update(clean_base=1, clean_min=1, clean_max=1,
                          per_accuracy=0, difficulty_penalty=0)
        attacker.strength = 30
        attacker.equipment.weapon.damage_type = "PIERCING"
        if armor:
            defender.equipment.armor[Zone.TORSO] = ArmorPiece(value=armor, weight=0)
        commit_action(session.state, attacker.id, Choice(ActionKind.HEAVY, Zone.TORSO))
        for _ in range(100):
            if any(event.type == EventType.HIT for event in session.state.event_log):
                break
            advance_one_tick(session.state)
        hit = next(event for event in session.state.event_log if event.type == EventType.HIT)
        wound = next(event for event in session.state.event_log if event.type == EventType.WOUND)
        return session, defender, hit, wound

    unarmored, fatal_target, fatal_hit, fatal_wound = strike(0)
    armored, bleeding_target, bleeding_hit, bleeding_wound = strike(10)
    assert fatal_hit.deltas["net"] >= unarmored.state.config["injury"]["vital_sites"]["heart_fatal_net"]
    assert fatal_hit.deltas["vital_outcome"] == "HEART_FATAL"
    assert fatal_wound.deltas["vital_outcome"] == "HEART_FATAL"
    assert fatal_target.blood == 0 and unarmored.state.end_reason == "death"
    from pirates.duel_system.notebook import Notebook
    from pirates.duel_system.render_model import build_render_model
    rendered = build_render_model(unarmored.state, Notebook(unarmored.state.config).public_view(),
                                  timeline=False)
    assert any("fatal qualified heart injury" in row for row in rendered.enemy_injury_rows)
    assert bleeding_hit.deltas["net"] < armored.state.config["injury"]["vital_sites"]["heart_fatal_net"]
    assert bleeding_hit.deltas["vital_outcome"] == "HEART_BLEED"
    assert bleeding_wound.deltas["bleed_per_turn"] == 12
    assert bleeding_target.blood == 100 and armored.state.end_reason is None


def test_qualified_clean_head_cut_can_permanently_sever_an_ear_and_roundtrip():
    from pirates.duel_system.engine import advance_one_tick

    session = DuelSession.new(seed=1, guide_mode="off")
    attacker, defender = session.state.fighters
    defender.pos = (attacker.pos[0] + 1, attacker.pos[1])
    dispersion = session.state.config["accuracy"]["aim_dispersion"]
    dispersion.update(clean_base=1, clean_min=1, clean_max=1,
                      per_accuracy=0, difficulty_penalty=0)
    attacker.strength = 20
    attacker.accuracy = 100
    attacker.equipment.weapon.damage_type = "CUTTING"
    replay = DuelSession.from_data(session.to_data())
    commit_action(session.state, attacker.id, Choice(ActionKind.HEAVY, Zone.HEAD))
    commit_action(replay.state, attacker.id, Choice(ActionKind.HEAVY, Zone.HEAD))
    for _ in range(100):
        if any(event.type == EventType.HIT for event in session.state.event_log):
            break
        advance_one_tick(session.state)
        advance_one_tick(replay.state)
    assert replay.state.to_data() == session.state.to_data()
    hit = next(event for event in session.state.event_log if event.type == EventType.HIT)
    sever = next(event for event in session.state.event_log if event.type == EventType.SEVER)
    assert hit.deltas["site"] == "EAR" and hit.deltas["side"] == "RIGHT"
    assert hit.deltas["net"] >= session.state.config["wound_net"]["sever"]
    assert defender.permanent_injuries.severed_ears == ["RIGHT"]
    assert sever.deltas["ear_lost"] is True
    assert defender.wounds[-1].structure == "EAR"
    assert defender.wounds[-1].bleed_per_turn == 12
    from pirates.duel_system.wounds import choose_anatomical_site
    assert all(choose_anatomical_site(session.state, Zone.HEAD, defender=defender)
               != ("EAR", "RIGHT") for _ in range(12))
    restored = CombatState.from_data(session.state.to_data())
    restored_defender = restored.fighter(defender.id)
    assert restored_defender.permanent_injuries.severed_ears == ["RIGHT"]
    assert restored_defender.wounds[-1].structure == "EAR"
    from pirates.duel_system.notebook import Notebook
    from pirates.duel_system.render_model import build_render_model
    model = build_render_model(session.state, Notebook(session.state.config).public_view(), timeline=False)
    assert any("right ear severed" in row for row in model.enemy_injury_rows)
    from pirates.duel_system.exchange_log import summarize_exchange
    summary = summarize_exchange(session.state.event_log, {attacker.id: "You", defender.id: "Ronin"})
    assert "lost the right ear" in summary[1]


def test_schema24_duel_journal_migrates_new_vital_sites_and_wound_fields():
    session = DuelSession.new(seed=94, guide_mode="off")
    data = session.to_data()
    data["version"] = data["state"]["schema_version"] = 24
    data["state"]["config"]["schema_version"] = 24
    injury = data["state"]["config"]["injury"]
    injury.pop("vital_sites")
    injury.pop("bilateral_vision_range")
    data["state"]["config"]["wounds"]["HEAD"].pop("SEVERED")
    data["state"]["config"]["anatomy_sites"]["TORSO"] = [
        site for site in data["state"]["config"]["anatomy_sites"]["TORSO"]
        if site not in {"HEART", "LEFT_LUNG", "RIGHT_LUNG", "LIVER"}]
    data["notebook"]["version"] = 24
    for fighter in data["state"]["fighters"]:
        fighter["permanent_injuries"].pop("severed_ears", None)
    restored = DuelSession.from_data(data)
    assert restored.state.config["schema_version"] == 27
    assert {"HEART", "LEFT_LUNG", "RIGHT_LUNG", "LIVER"} <= set(
        restored.state.config["anatomy_sites"]["TORSO"])
    assert restored.state.config["injury"]["bilateral_vision_range"] == 1


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
    assert restored.state.config["schema_version"] == 27
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
    assert restored.state.config["schema_version"] == 27
    assert restored.state.to_data()["rng_draws"] == session.state.to_data()["rng_draws"]
    replay = DuelSession.from_data(loaded.duel_journal)
    from pirates.duel_system.engine import advance_one_tick
    while restored.state.tick < old_deadline:
        advance_one_tick(restored.state)
        advance_one_tick(replay.state)
    assert restored.state.to_data() == replay.state.to_data()
