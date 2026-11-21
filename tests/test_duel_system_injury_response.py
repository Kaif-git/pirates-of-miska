"""P3 localized structure, contact, shock, recoil and anti-chain gates."""
from copy import deepcopy

import pytest

from pirates.duel_system.engine import Choice, advance_one_tick, commit_action
from pirates.duel_system.model import (
    ActionKind, ArmorPiece, CombatState, EventType, Equipment, Personality,
    Severity, Weapon, Wound, Zone,
)
from pirates.duel_system.stats import effective_stats
from pirates.duel_system.wounds import classify_wound, impact_stability_request
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
    assert defender.pos == (2, 0)
    moved = next(event for event in state.event_log
                 if event.type == EventType.MOVE and event.actor == defender.id)
    assert moved.deltas["to"] == (2, 0)
    saved = state.to_data()
    restored = CombatState.from_data(saved)
    assert restored.to_data() == saved
    advance_one_tick(state)
    advance_one_tick(restored)
    assert restored.to_data() == state.to_data()


def test_resolved_hit_transfers_bounded_anatomical_impact_without_reusing_shock():
    impacts = {}
    for zone in (Zone.HEAD, Zone.TORSO, Zone.LEG, Zone.ARM, Zone.HAND):
        state, _, defender = hit_once(profile="veteran", zone=zone, strength=12)
        hit = next(event for event in state.event_log if event.type == EventType.HIT)
        impacts[hit.zone] = hit.deltas["impact_applied"]
        assert hit.severity == Severity.DEEP
        assert hit.deltas["shock_response"] == "FLINCH"
        assert hit.deltas["impact_requested"] > 0
        assert hit.deltas["impact_applied"] == pytest.approx(
            100 - defender.stability, abs=1e-6)
        stability_event = next(event for event in state.event_log
                               if event.type == EventType.STABILITY
                               and event.deltas.get("source") == "HIT_IMPACT")
        assert stability_event.deltas["attacker"] == state.fighters[0].id
        assert stability_event.deltas["zone"] == hit.zone.value
    assert impacts[Zone.HEAD] > impacts[Zone.TORSO] > impacts[Zone.ARM]
    hand_impact = impact_stability_request(state.config, Zone.HAND, Severity.DEEP,
                                          "CUTTING", 16, "OPEN")
    assert impacts[Zone.LEG] > hand_impact


@pytest.mark.parametrize("damage_type,minimum", [("CUTTING", 0.9), ("PIERCING", 1.0), ("BLUNT", 1.2)])
def test_contact_material_modulates_post_armor_impact(damage_type, minimum):
    state, _, _ = hit_once(profile=None, zone=Zone.TORSO, strength=12, damage_type=damage_type)
    hit = next(event for event in state.event_log if event.type == EventType.HIT)
    cfg = state.config["injury"]
    assert hit.deltas["impact_requested"] == pytest.approx(
        (hit.deltas["net"] - cfg["impact_net_threshold"])
        * cfg["impact_per_net"]
        * cfg["impact_severity_mult"][hit.severity.value]
        * cfg["impact_zone_mult"][Zone.TORSO.value]
        * minimum
        * cfg["impact_defense_mult"]["OPEN"])


def test_armor_and_posture_reduce_or_gate_hit_impact_after_armor():
    weak, _, weak_target = hit_once(profile=None, zone=Zone.TORSO, strength=1)
    weak_hit = next(event for event in weak.event_log if event.type == EventType.HIT)
    assert weak_hit.severity == Severity.GRAZE
    assert weak_hit.deltas["impact_requested"] == 0
    assert weak_target.stability == 100

    armored, _, armored_target = hit_once(profile=None, zone=Zone.TORSO, strength=12, armor=10)
    armored_hit = next(event for event in armored.event_log if event.type == EventType.HIT)
    assert armored_hit.deltas["net"] == 6
    assert armored_hit.deltas["impact_requested"] == 0
    assert armored_target.stability == 100

    open_state, _, open_target = hit_once(profile=None, zone=Zone.LEG, strength=12)
    open_hit = next(event for event in open_state.event_log if event.type == EventType.HIT)
    guarded_state = arena()
    attacker, target = guarded_state.fighters
    attacker.strength = 12
    attacker.equipment.weapon = Weapon(edge=4, weight=0, damage_type="CUTTING")
    target.pos = (1, 0)
    target.action = None
    # A guard is held while the attacker lands on an uncovered leg.
    commit_action(guarded_state, target.id, Choice(ActionKind.GUARD))
    commit_action(guarded_state, attacker.id, Choice(ActionKind.LIGHT, Zone.LEG))
    advance_to(guarded_state, attacker.action.strike_tick)
    guarded_hit = next(event for event in guarded_state.event_log if event.type == EventType.HIT)
    assert guarded_hit.deltas["net"] == open_hit.deltas["net"]
    assert guarded_hit.deltas["impact_requested"] < open_hit.deltas["impact_requested"]
    assert open_target.stability < 100 and target.stability < 100
    assert impact_stability_request(open_state.config, Zone.HEAD, Severity.SEVERED,
                                    "BLUNT", 100, "DEFENSELESS") == 8


def test_head_graze_and_deep_impact_have_distinct_stability_and_function_effects():
    graze_state, _, graze_target = hit_once(profile=None, zone=Zone.HEAD, strength=1)
    deep_state, _, deep_target = hit_once(profile=None, zone=Zone.HEAD, strength=12)
    graze = next(event for event in graze_state.event_log if event.type == EventType.HIT)
    deep = next(event for event in deep_state.event_log if event.type == EventType.HIT)
    assert graze.severity == Severity.GRAZE
    assert deep.severity == Severity.DEEP
    assert graze.deltas["impact_applied"] == 0
    assert deep.deltas["impact_applied"] > 0
    assert graze_target.statuses[0].name == "dizzy"
    assert any(status.name == "concussed" for status in deep_target.statuses)
    assert deep_target.stability < graze_target.stability


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
    wound = defender.wounds[0]
    expected_loss = (state.config["blood"]["graze_initial_loss"]
                     * state.config["injury"]["damage_types"]["PIERCING"]["bleed_mult"])
    assert wound.bleed_per_turn == 0
    assert wound.initial_blood_loss == pytest.approx(expected_loss)
    assert defender.blood == pytest.approx(state.config["blood"]["start"] - expected_loss)
    for _ in range(state.config["time"]["ticks_per_turn"] * 3):
        advance_one_tick(state)
    assert defender.blood == pytest.approx(state.config["blood"]["start"] - expected_loss)
    restored = CombatState.from_data(state.to_data())
    restored_defender = next(actor for actor in restored.fighters if actor.id == defender.id)
    blood_at_save = restored_defender.blood
    for _ in range(state.config["time"]["ticks_per_turn"]):
        advance_one_tick(restored)
    assert restored_defender.blood == pytest.approx(blood_at_save)
    assert restored_defender.wounds[0].initial_blood_loss == pytest.approx(expected_loss)


def test_v17_active_graze_migrates_from_ongoing_bleed_to_nonbleeding_wound():
    state = arena()
    defender = state.fighters[1]
    defender.wounds.append(Wound(Zone.ARM, Severity.GRAZE, 0.5))
    data = state.to_data()
    data["schema_version"] = data["config"]["schema_version"] = 17
    data["config"]["blood"].pop("graze_initial_loss", None)
    data["config"]["wounds"]["ARM"]["GRAZE"]["bleed"] = 0.5
    data["fighters"][1]["wounds"][0].pop("initial_blood_loss", None)
    data["fighters"][1]["wounds"][0]["bleed_per_turn"] = 0.5

    restored = CombatState.from_data(data)
    migrated = restored.fighters[1].wounds[0]
    assert restored.config["schema_version"] == 27
    assert migrated.bleed_per_turn == 0
    assert migrated.initial_blood_loss == 0


def test_cumulative_blood_loss_progressively_reduces_physical_stats_not_accuracy():
    state = arena()
    fighter = state.fighters[0]
    samples = []
    for blood in (100, 50, 25, 0):
        fighter.blood = blood
        live = effective_stats(fighter, state.config, state.tick)
        samples.append((live.speed, live.strength, live.accuracy))
    assert [row[0] for row in samples] == sorted((row[0] for row in samples), reverse=True)
    assert [row[1] for row in samples] == sorted((row[1] for row in samples), reverse=True)
    assert {row[2] for row in samples} == {fighter.accuracy}
    assert samples[0][0] == samples[1][0]  # ordinary blood loss does not debuff immediately
    assert samples[-1][0] < samples[1][0]

    healthy, injured = arena(), arena()
    for comparison, blood in ((healthy, 100), (injured, 25)):
        comparison.tick = 3  # both fighters are past the idle-recovery delay
        for actor in comparison.fighters:
            actor.idle_since = 0
            actor.stamina = 50
            actor.blood = blood
        advance_one_tick(comparison)
    assert injured.fighters[0].stamina - 50 < healthy.fighters[0].stamina - 50


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
    attacker.equipment.weapon = Weapon(edge=4, reach=1, weight=0)
    values = deepcopy(state.config["ronin"])
    values.update(deepcopy(state.config["ai_profiles"]["recruit"]))
    values["archetype"] = "recruit"
    defender.personality = Personality(**values)
    commit_action(state, attacker.id, Choice(ActionKind.LIGHT, Zone.ARM))
    advance_to(state, attacker.action.end_tick)
    assert defender.pos == (2, 0)
    first_recoil_tick = defender.last_recoil_tick
    attacker.pos = (1, 0)
    commit_action(state, attacker.id, Choice(ActionKind.LIGHT, Zone.ARM))
    advance_to(state, attacker.action.strike_tick)
    assert len(defender.wounds) == 2
    assert defender.last_recoil_tick == first_recoil_tick
    responses = [event for event in state.event_log if event.type == EventType.INJURY_RESPONSE]
    assert [event.deltas["response"] for event in responses] == ["RECOIL", "REFRACTORY"]
    assert defender.pos == (2, 0)


def test_flinch_forces_safe_step_even_when_head_hit_also_staggers_target():
    state, attacker, defender = hit_once(profile="veteran", zone=Zone.HEAD, strength=12)
    assert defender.stagger_until > state.tick
    response = next(event for event in state.event_log
                    if event.type == EventType.INJURY_RESPONSE)
    assert response.deltas["response"] == "FLINCH"
    assert response.deltas["from_pos"] == (1, 0)
    assert response.deltas["to_pos"] == (2, 0)
    assert defender.pos == (2, 0)


@pytest.mark.parametrize("profile", ["veteran", "recruit"])
def test_flinch_and_recoil_share_the_same_visible_one_tile_outcome(profile):
    from pirates.duel_system.exchange_log import summarize_exchange
    from pirates.duel_system.notebook import Notebook
    from pirates.duel_system.render_model import event_line
    from pirates.duel_system.render_model import build_render_model

    state, attacker, defender = hit_once(profile=profile, zone=Zone.HEAD, strength=12)
    response = next(event for event in state.event_log if event.type == EventType.INJURY_RESPONSE)

    assert response.deltas["response"] in {"FLINCH", "RECOIL"}
    assert response.deltas["to_pos"] == (2, 0)
    assert event_line(response, {attacker.id: "You", defender.id: "Enemy"}) == \
        "Enemy: forced back one tile by injury reflex"
    assert "forced back one tile by the injury reflex" in \
        " ".join(summarize_exchange(state.event_log, {attacker.id: "You", defender.id: "Enemy"}))
    model = build_render_model(state, Notebook(state.config).public_view(), timeline=False)
    reflex_popup = next(popup for popup in model.popups if popup.text == "Back 1 tile")
    assert reflex_popup.big is True


def test_repeated_head_hits_do_not_refresh_stagger_without_bound():
    state = arena()
    attacker, defender = state.fighters
    attacker.strength = 12
    first = commit_action(state, attacker.id, Choice(ActionKind.LIGHT, Zone.HEAD))
    advance_to(state, first.end_tick)
    initial_until = defender.stagger_until
    assert initial_until > state.tick
    second = commit_action(state, attacker.id, Choice(ActionKind.LIGHT, Zone.HEAD))
    # The first hit's shared FLINCH/RECOIL response moved the defender one tile;
    # reset geometry to test repeated contact rather than range.
    attacker.pos, defender.pos = (0, 0), (1, 0)
    advance_to(state, second.strike_tick)
    stagger_events = [event for event in state.event_log if event.type == EventType.STAGGER]
    assert len(stagger_events) == 2
    assert stagger_events[0].deltas["repeated"] is False
    assert stagger_events[1].deltas["repeated"] is True
    assert defender.stagger_until == initial_until
    advance_to(state, initial_until)
    assert defender.can_act(state.tick)


def test_hit_impact_rules_are_role_symmetric_for_player_and_npc():
    outcomes = []
    for reverse in (False, True):
        state = arena()
        attacker, defender = state.fighters[::-1] if reverse else state.fighters
        attacker.strength = 12
        attacker.equipment.weapon = Weapon(edge=4, weight=0, damage_type="CUTTING")
        commit_action(state, attacker.id, Choice(ActionKind.LIGHT, Zone.ARM))
        advance_to(state, attacker.action.strike_tick)
        hit = next(event for event in state.event_log if event.type == EventType.HIT)
        assert hit.actor == attacker.id and hit.target == defender.id
        outcomes.append((hit.deltas["impact_requested"], hit.deltas["impact_applied"],
                         100 - defender.stability))
    assert outcomes[0] == pytest.approx(outcomes[1])


def test_identical_seeded_impact_exchange_replays_identically():
    first, _, _ = hit_once(profile="veteran", zone=Zone.HEAD, strength=12)
    second, _, _ = hit_once(profile="veteran", zone=Zone.HEAD, strength=12)
    assert first.to_data() == second.to_data()


def test_same_tick_intersecting_strikes_clash_before_any_wound_or_shock_recoil():
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
    assert not any(event.type == EventType.HIT for event in state.event_log)
    assert len([event for event in state.event_log if event.type == EventType.CLASH]) == 1
    assert first.pos == (0, 0) and second.pos == (1, 0)
    assert not any(event.type == EventType.INJURY_RESPONSE for event in state.event_log)
    assert first.stability == second.stability == 100
    assert all(fighter.action is not None for fighter in state.fighters)
    assert not any(event.type == EventType.INTERRUPT for event in state.event_log)


def test_same_tick_clash_does_not_fabricate_hit_or_knock_down_low_stability_target():
    state = arena()
    player, enemy = state.fighters
    for fighter in state.fighters:
        fighter.strength = 12
        fighter.equipment.weapon = Weapon(edge=4, weight=0, damage_type="BLUNT")
        values = deepcopy(state.config["ronin"])
        values.update(deepcopy(state.config["ai_profiles"]["recruit"]))
        values["archetype"] = "recruit"
        fighter.personality = Personality(**values)
        commit_action(state, fighter.id, Choice(ActionKind.LIGHT, Zone.ARM))
    enemy.stability = 0.5
    origin = enemy.pos
    advance_to(state, player.action.strike_tick)
    assert not any(event.type == EventType.HIT for event in state.event_log)
    assert len([event for event in state.event_log if event.type == EventType.CLASH]) == 1
    assert not enemy.grounded and enemy.pos == origin
    assert not any(event.type == EventType.INJURY_RESPONSE for event in state.event_log)


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
    assert defender.action is not None and defender.action.phase_at(state.tick) == "RECOVERY"
    # The attack struck at t3; the faster opponent commits immediately and lands at t5 in recovery.
    attacker.action = None
    attacker.pos, defender.pos = (0, 0), (1, 0)
    commit_action(state, attacker.id, Choice(ActionKind.LIGHT, Zone.ARM))
    advance_to(state, attacker.action.strike_tick)
    assert defender.action is not None and defender.action.phase_at(state.tick) == "RECOVERY"
    response = [event for event in state.event_log if event.type == EventType.INJURY_RESPONSE][-1]
    assert response.deltas["response"] == "RECOIL"
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
    assert paid_stamina < defender.stamina < 100  # passive recovery occurred, but committed movement cost was not refunded
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
    assert migrated.config["schema_version"] == 27
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
    old["actions"]["THRUST"].pop("damage_type")
    old["guard"].pop("riposte_disarm_windup_mult")
    old["tactics"].pop("feint_disrupt_ticks")
    path = tmp_path / "combat-v2.json"
    path.write_text(json.dumps(old), encoding="utf-8")
    migrated = load_config(path)
    assert migrated["schema_version"] == 27
    assert migrated["actions"]["THRUST"]["damage_type"] == "PIERCING"
    assert migrated["guard"]["riposte_disarm_windup_mult"] == 0.3
    assert migrated["tactics"]["feint_disrupt_ticks"] == 6
    assert {key: value for key, value in migrated["actions"]["THRUST"].items()
            if key != "damage_type"} == old["actions"]["THRUST"]
    assert migrated["injury"]["recoil_cooldown_ticks"] == 10
    assert migrated["injury"]["impact_max_stability_loss"] == 8


def test_schema15_config_and_wound_save_migrate_anatomical_sites(tmp_path):
    from copy import deepcopy
    import json
    from pirates.duel_system.config import load_config
    from pirates.duel_system.model import CombatState
    from test_duel_system_scheduler import arena

    old_config = deepcopy(load_config())
    old_config["schema_version"] = 15
    old_config.pop("anatomy_sites")
    path = tmp_path / "duel-v15.json"
    path.write_text(json.dumps(old_config), encoding="utf-8")
    migrated_config = load_config(path)
    assert migrated_config["schema_version"] == 27
    assert migrated_config["anatomy_sites"]["HAND"]

    state = arena()
    state.fighters[1].wounds.append(Wound(Zone.HAND, Severity.CUT, 1.5))
    data = state.to_data()
    data["schema_version"] = 15
    data["config"]["schema_version"] = 15
    data["fighters"][1]["wounds"][0].pop("site")
    data["fighters"][1]["wounds"][0].pop("side")
    restored = CombatState.from_data(data)
    assert restored.config["schema_version"] == 27
    assert restored.fighters[1].wounds[0].site == "HAND"
    assert restored.fighters[1].wounds[0].side is None


def test_v8_active_session_migrates_new_impact_tuning_and_preserves_action():
    from pirates.duel_system.session import DuelSession

    session = DuelSession.new(seed=98, guide_mode="off")
    session.select(Choice(ActionKind.RUN, direction=(0, 1), tiles=2))
    assert session.confirm()
    data = session.to_data()
    expected_path = deepcopy(session.player.action.path)
    data["version"] = data["state"]["schema_version"] = data["state"]["config"]["schema_version"] = 8
    data["notebook"]["version"] = 8
    for key in ("impact_net_threshold", "impact_per_net", "impact_max_stability_loss",
                "impact_severity_mult", "impact_zone_mult", "impact_contact_mult",
                "impact_defense_mult"):
        data["state"]["config"]["injury"].pop(key)
    restored = DuelSession.from_data(data)
    assert restored.to_data()["version"] == 27
    assert restored.player.action.path == expected_path
    assert restored.state.config["injury"]["impact_net_threshold"] == 8


def test_v9_session_migration_adds_bounded_stagger_refresh_rule():
    from pirates.duel_system.session import DuelSession

    session = DuelSession.new(seed=99, guide_mode="off")
    data = session.to_data()
    data["version"] = data["state"]["schema_version"] = data["state"]["config"]["schema_version"] = 9
    data["notebook"]["version"] = 9
    data["state"]["config"]["injury"].pop("stagger_repeat_duration_mult")
    restored = DuelSession.from_data(data)
    assert restored.to_data()["version"] == 27
    assert restored.state.config["injury"]["stagger_repeat_duration_mult"] == 0


def test_v10_session_migration_adds_reduced_motion_preference_and_preserves_state():
    from pirates.duel_system.session import DuelSession

    session = DuelSession.new(seed=101, guide_mode="off")
    data = session.to_data()
    data["version"] = data["state"]["schema_version"] = data["state"]["config"]["schema_version"] = 10
    data["notebook"]["version"] = 10
    data["state"]["config"]["ui"].pop("reduced_motion")
    player_blood = session.player.blood
    restored = DuelSession.from_data(data)
    assert restored.to_data()["version"] == 27
    assert restored.state.config["ui"]["reduced_motion"] is False
    assert restored.player.blood == player_blood


def test_v11_session_migration_adds_consequence_ai_tuning_and_preserves_active_state():
    from pirates.duel_system.session import DuelSession

    session = DuelSession.new(seed=102, guide_mode="off")
    commit_action(session.state, session.player.id, Choice(ActionKind.WALK, direction=(0, 1)))
    data = session.to_data()
    data["version"] = data["state"]["schema_version"] = data["state"]["config"]["schema_version"] = 11
    data["notebook"]["version"] = 11
    old_config = data["state"]["config"]
    for key in ("SHOVE_UNSTEADY", "SHOVE_OPEN_SPACE", "BANDAGE_BLEEDING",
                "BANDAGE_UNSAFE_PENALTY", "WAIT_STABILITY", "BLEED_PATIENCE"):
        old_config["tactics"]["ai_scores"].pop(key)
    path_before = deepcopy(data["state"]["fighters"][0]["action"])
    restored = DuelSession.from_data(data)
    assert restored.to_data()["version"] == 27
    assert restored.state.config["tactics"]["ai_scores"]["BANDAGE_BLEEDING"] == 120
    assert restored.to_data()["state"]["fighters"][0]["action"] == path_before


def test_v11_world_journal_migration_replays_active_action_deterministically(tmp_path):
    from pirates.duel_system.session import DuelSession
    from pirates.game import World

    session = DuelSession.new(seed=103, guide_mode="off")
    commit_action(session.state, session.player.id, Choice(ActionKind.WALK, direction=(0, 1)))
    data = session.to_data()
    data["version"] = data["state"]["schema_version"] = data["state"]["config"]["schema_version"] = 11
    data["notebook"]["version"] = 11
    for key in ("SHOVE_UNSTEADY", "SHOVE_OPEN_SPACE", "BANDAGE_BLEEDING",
                "BANDAGE_UNSAFE_PENALTY", "WAIT_STABILITY", "BLEED_PATIENCE"):
        data["state"]["config"]["tactics"]["ai_scores"].pop(key, None)
    expected = DuelSession.from_data(data)
    world = World()
    world.duel_journal = deepcopy(data)
    save_path = tmp_path / "duel-v11-world.json"
    world.save(save_path)
    restored_world = World.load(save_path)
    restored = DuelSession.from_data(restored_world.duel_journal)
    assert restored_world.duel_journal["version"] == 27
    expected.run_to_player()
    restored.run_to_player()
    assert restored.state.to_data() == expected.state.to_data()


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
    assert restored.to_data()["version"] == 27
    assert restored.player.action.path == old_path
    assert restored.player.equipment.weapon.damage_type == "CUTTING"
    assert restored.notebook.to_data()["version"] == 27


def test_render_details_and_coach_explain_wound_without_private_shock_values():
    from pirates.duel_coach import coach, strategy_rows
    from pirates.duel_system.notebook import Notebook
    from pirates.duel_system.session import DuelSession
    from pirates.duel_ui import DuelUI

    state = arena()
    state.config["anatomy_sites"]["ARM"] = ["LEFT_WRIST"]
    attacker, defender = state.fighters
    attacker.strength = 12
    attacker.equipment.weapon = Weapon(edge=4, weight=0, damage_type="CUTTING")
    commit_action(state, attacker.id, Choice(ActionKind.LIGHT, Zone.ARM))
    advance_to(state, attacker.action.strike_tick)
    state.fighters[0].wounds = deepcopy(defender.wounds)
    state.fighters[0].statuses = deepcopy(defender.statuses)
    session = DuelSession(state, Notebook(state.config), guide_mode="off")
    model = DuelUI(session, rounds=True).model()
    assert model.enemy_injury_rows[0].startswith(
        "Arm — left wrist: deep, deep tissue (tendon) cutting; weak grip")
    advice = coach(model)
    rows = strategy_rows(model, advice)
    assert any("Your injury: Arm — left wrist: deep, deep tissue (tendon) cutting" in row
               for row in advice.cautions)
    assert any("YOUR CURRENT INJURIES" == row for row in rows)
    assert not any("decision_control" in row or "shock_load" in row for row in rows)
    assert not any("decision_control" in row or "shock_load" in row for row in model.full_log)
    from pirates.duel_system.exchange_log import format_exchange
    recap = format_exchange(state.event_log, {"player": "You", "ronin": "Ronin"})
    assert any("reached deep tissue (tendon)" in row and "function: weak grip" in row
               for row in recap)
    assert not any("decision_control" in row or "shock_load" in row for row in recap)
