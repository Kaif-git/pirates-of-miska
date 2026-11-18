"""M3 zone/exposure/wound/passive acceptance gates."""
import pytest

from pirates.duel_system.model import ActionKind, ArmorPiece, EventType, Severity, Wound, Zone
from pirates.duel_system.engine import Choice, advance_one_tick, commit_action
from pirates.duel_system.wounds import classify_wound, defense_snapshot, resolve_zone
from test_duel_system_scheduler import arena, advance_to


def test_hand_requires_exposure_and_accuracy_drift_follows_chain():
    state = arena()
    attacker, defender = state.fighters
    attacker.accuracy = 20
    assert resolve_zone(state, attacker, defender, Zone.HAND, defense_snapshot(state, defender)) == (Zone.ARM, True)
    commit_action(state, defender.id, Choice(ActionKind.HEAVY, Zone.TORSO))
    attacker.accuracy = 17
    assert resolve_zone(state, attacker, defender, Zone.HAND, defense_snapshot(state, defender)) == (Zone.ARM, True)
    attacker.accuracy = 13
    assert resolve_zone(state, attacker, defender, Zone.ARM, defense_snapshot(state, defender)) == (Zone.TORSO, True)
    attacker.accuracy = 20
    assert resolve_zone(state, attacker, defender, Zone.HAND, defense_snapshot(state, defender)) == (Zone.HAND, False)


@pytest.mark.parametrize("power,severity", [(0, Severity.BRUISE), (1, Severity.GRAZE),
                                           (7, Severity.GRAZE), (8, Severity.CUT),
                                           (15, Severity.CUT), (16, Severity.DEEP)])
def test_wound_thresholds(power, severity):
    state = arena()
    assert classify_wound(state.config, Zone.TORSO, power, ActionKind.LIGHT, False) == severity


def test_hand_sever_requires_clean_heavy_and_threshold():
    cfg = arena().config
    assert classify_wound(cfg, Zone.HAND, 22, ActionKind.HEAVY, False) == Severity.SEVERED
    assert classify_wound(cfg, Zone.HAND, 22, ActionKind.LIGHT, False) == Severity.DEEP
    assert classify_wound(cfg, Zone.HAND, 22, ActionKind.HEAVY, True) == Severity.DEEP


def test_torso_cut_bleeding_at_walk_exertion_is_point_three_per_tick():
    state = arena()
    fighter = state.fighters[0]
    state.fighters[1].pos = (5, 0)
    fighter.wounds.append(Wound(Zone.TORSO, Severity.CUT, 3))
    commit_action(state, fighter.id, Choice(ActionKind.WALK, direction=(0, 1)))
    advance_one_tick(state)
    assert fighter.blood == pytest.approx(99.7)
    fighter.wounds[0].bandaged = True
    advance_one_tick(state)
    assert fighter.blood == pytest.approx(99.7 - .3 * .4)


def test_walk_is_defenseless_and_damage_interrupts_windup():
    state = arena()
    commit_action(state, "player", Choice(ActionKind.LIGHT, Zone.LEG))
    commit_action(state, "ronin", Choice(ActionKind.WALK, direction=(0, 1)))
    assert defense_snapshot(state, state.fighters[1]).defense == "DEFENSELESS"
    advance_to(state, 4)
    hit = next(e for e in state.event_log if e.type == EventType.HIT)
    assert "WALK_DEFENSELESS" in hit.cause_tags


def test_sword_range_is_distance_one_not_double_counted_reach():
    state = arena()
    state.fighters[1].pos = (2, 0)
    commit_action(state, "player", Choice(ActionKind.LIGHT, Zone.TORSO))
    advance_to(state, 4)
    assert any(e.type == EventType.WHIFF for e in state.event_log)
    assert not state.fighters[1].wounds


def test_bandage_inventory_only_consumed_on_completion_and_hit_cancels():
    state = arena()
    fighter = state.fighters[1]
    fighter.bandages = 1
    fighter.wounds.append(Wound(Zone.TORSO, Severity.CUT, 3))
    commit_action(state, fighter.id, Choice(ActionKind.BANDAGE))
    commit_action(state, "player", Choice(ActionKind.LIGHT, Zone.TORSO))
    advance_to(state, 4)
    assert fighter.bandages == 1
    assert not fighter.wounds[0].bandaged
    assert fighter.action is None


def test_bleed_stack_cap_and_wait_exertion_are_config_driven():
    state = arena()
    fighter = state.fighters[0]
    fighter.wounds = [Wound(Zone.TORSO, Severity.DEEP, 6) for _ in range(6)]
    commit_action(state, fighter.id, Choice(ActionKind.WAIT))
    advance_one_tick(state)
    assert fighter.blood == pytest.approx(100 - 20 / 10 * .7)


@pytest.mark.parametrize("zone,stat,status", [
    (Zone.LEG, "speed_mult", "slowed"),
    (Zone.ARM, "strength_mult", "weak_grip"),
    (Zone.HAND, "strength_mult", "weak_grip"),
    (Zone.HEAD, "accuracy_mult", "dizzy"),
])
def test_zone_cut_effects_are_applied_from_table(zone, stat, status):
    from pirates.duel_system.wounds import apply_wound
    state = arena()
    attacker, defender = state.fighters
    apply_wound(state, attacker, defender, zone, Severity.CUT, 14, 14)
    effect = next(item for item in defender.statuses if item.name == status)
    assert getattr(effect, stat) < 1
    assert effect.until_tick is not None
    assert defender.wounds[0].bleed_per_turn == state.config["wounds"][zone.value]["CUT"]["bleed"]
    if zone == Zone.HAND:
        assert effect.parry_penalty == 2
        assert defender.pain == 21


def test_armor_bruise_causes_pain_but_no_bleeding_or_interrupt():
    state = arena()
    defender = state.fighters[1]
    defender.equipment.armor[Zone.TORSO] = ArmorPiece(value=100, weight=0)
    commit_action(state, "player", Choice(ActionKind.LIGHT, Zone.TORSO))
    commit_action(state, "ronin", Choice(ActionKind.HEAVY, Zone.TORSO))
    advance_to(state, 4)
    assert not defender.wounds
    assert defender.pain > 0
    assert defender.action is not None
    assert not any(e.type == EventType.INTERRUPT for e in state.event_log)


def test_completed_bandage_changes_only_one_wound_and_food_is_finite():
    state = arena()
    fighter = state.fighters[0]
    state.fighters[1].pos = (5, 0)
    fighter.bandages = 1
    fighter.wounds = [Wound(Zone.TORSO, Severity.CUT, 3), Wound(Zone.ARM, Severity.CUT, 2)]
    commit_action(state, fighter.id, Choice(ActionKind.BANDAGE))
    deadline = fighter.action.end_tick
    advance_to(state, deadline)
    assert fighter.bandages == 0
    assert [w.bandaged for w in fighter.wounds] == [True, False]
    fighter.food = 1
    fighter.fatigue = 50
    commit_action(state, fighter.id, Choice(ActionKind.EAT))
    advance_to(state, fighter.action.end_tick)
    assert fighter.food == 0
    assert fighter.fatigue == 30


def test_deep_hand_disarms_and_clean_heavy_can_sever_permanently():
    from pirates.duel_system.wounds import apply_wound
    state = arena()
    attacker, defender = state.fighters
    apply_wound(state, attacker, defender, Zone.HAND, Severity.DEEP, 18, 18)
    assert defender.disarmed
    assert not defender.hand_severed
    apply_wound(state, attacker, defender, Zone.HAND, Severity.SEVERED, 24, 24)
    assert defender.hand_severed
    assert defender.wounds[-1].bleed_per_turn == 8


def test_dash_light_recovery_is_not_cancelled_by_a_non_staggering_hit():
    from pirates.duel_system.wounds import defense_snapshot, resolve_strike
    state = arena()
    defender, attacker = state.fighters
    commit_action(state, defender.id, Choice(ActionKind.DASH_LIGHT, Zone.TORSO, (0, 1), 1))
    state.tick = 8  # dash3 + windup4 + active1, now recovery
    assert defender.action.phase_at(state.tick) == "RECOVERY"
    commit_action(state, attacker.id, Choice(ActionKind.LIGHT, Zone.TORSO))
    resolve_strike(state, attacker, defender, defense_snapshot(state, defender))
    assert defender.id not in state.pending_interrupts
