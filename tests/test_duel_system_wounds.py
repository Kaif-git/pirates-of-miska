"""M3 zone/exposure/wound/passive acceptance gates."""
import pytest

from pirates.duel_system.model import ActionKind, ArmorPiece, CombatState, EventType, Severity, Wound, Zone
from pirates.duel_system.engine import Choice, advance_one_tick, commit_action, legality
from pirates.duel_system.config import load_config
from pirates.duel_system.stats import effective_stats
from pirates.duel_system.wounds import apply_wound, classify_wound, defense_snapshot, resolve_zone
from test_duel_system_scheduler import arena, advance_to


def test_hand_requires_exposure_and_accuracy_drift_follows_chain():
    state = arena()
    attacker, defender = state.fighters
    attacker.accuracy = 0
    result = resolve_zone(state, attacker, defender, Zone.HAND,
                          defense_snapshot(state, defender))
    assert result[0] != Zone.HAND and result[1]
    commit_action(state, defender.id, Choice(ActionKind.HEAVY, Zone.TORSO))
    attacker.accuracy = 0  # Accuracy never gates this visible, exposed target.
    assert resolve_zone(state, attacker, defender, Zone.HAND, defense_snapshot(state, defender)) == (Zone.HAND, False)


def test_aim_dispersion_is_seeded_bounded_and_accuracy_improves_clean_site_chance():
    def resolve(seed, accuracy, aim=Zone.HEAD):
        state = arena(rng_seed=seed)
        state.config["accuracy"]["aim_dispersion"] = load_config()["accuracy"]["aim_dispersion"]
        attacker, defender = state.fighters
        attacker.accuracy = accuracy
        return resolve_zone(state, attacker, defender, aim,
                            defense_snapshot(state, defender))

    assert resolve(104, 10) == resolve(104, 10)
    allowed = {Zone.HEAD, Zone.EYE, Zone.TORSO, None}
    low = [resolve(seed, 0) for seed in range(160)]
    high = [resolve(seed, 100) for seed in range(160)]
    assert set(low) <= {(zone, drifted) for zone in allowed for drifted in (False, True)}
    assert sum(zone == Zone.HEAD and not drifted for zone, drifted in high) > \
        sum(zone == Zone.HEAD and not drifted for zone, drifted in low)
    assert any(zone is None for zone, _ in low)


def test_aim_dispersion_never_selects_absent_or_covered_sites():
    state = arena(rng_seed=39)
    state.config["accuracy"]["aim_dispersion"] = load_config()["accuracy"]["aim_dispersion"]
    attacker, defender = state.fighters
    defender.permanent_injuries.eyes_damaged = 2
    head_results = [resolve_zone(state, attacker, defender, Zone.HEAD,
                                 defense_snapshot(state, defender))
                    for _ in range(120)]
    assert all(zone != Zone.EYE for zone, _ in head_results)

    snap = defense_snapshot(state, defender)
    closed_hand_results = [resolve_zone(state, attacker, defender, Zone.HAND, snap)
                           for _ in range(40)]
    assert all(zone != Zone.HAND for zone, _ in closed_hand_results)
    assert resolve_zone(state, attacker, defender, Zone.EYE, snap) == (Zone.HEAD, True)

    defender.permanent_injuries.severed_hands = ["LEFT", "RIGHT"]
    defender.hand_severed = True
    assert resolve_zone(state, attacker, defender, Zone.HAND, snap) == (Zone.ARM, True)


@pytest.mark.parametrize("accuracy", [0, 100])
def test_accuracy_does_not_change_target_legality_or_physical_reach(accuracy):
    state = arena(rng_seed=81)
    attacker, defender = state.fighters
    attacker.accuracy = accuracy
    defender.pos = (2, 0)
    choice = Choice(ActionKind.LIGHT, Zone.HEAD, target_id=defender.id)
    assert legality(state, attacker.id, choice) == ""

    action = commit_action(state, attacker.id, choice)
    advance_to(state, action.strike_tick)

    miss = next(event for event in state.event_log if event.type == EventType.WHIFF)
    assert "OUT_OF_REACH" in miss.cause_tags
    assert not any(event.type == EventType.HIT for event in state.event_log)


def test_dispersion_miss_is_replayable_and_recap_names_the_actual_cause():
    from pirates.duel_system.exchange_log import format_exchange

    def resolve(seed):
        state = arena(rng_seed=seed)
        rule = state.config["accuracy"]["aim_dispersion"]
        rule.update(clean_base=0, per_accuracy=0, difficulty_penalty=0,
                    clean_min=0, clean_max=0, drift_share=0)
        attacker, defender = state.fighters
        commit_action(state, attacker.id,
                      Choice(ActionKind.LIGHT, Zone.HEAD, target_id=defender.id))
        advance_to(state, attacker.action.strike_tick)
        miss = next(event for event in state.event_log
                    if event.type == EventType.WHIFF and "AIM_DISPERSION_MISS" in event.cause_tags)
        recap = "\n".join(format_exchange(
            state.event_log, {attacker.id: "You", defender.id: "Ronin"}))
        return miss.deltas, state.rng_draws, recap

    first, replay = resolve(551), resolve(551)
    assert first == replay
    assert first[0]["aimed_zone"] == "HEAD" and first[0]["resolved_zone"] is None
    assert "seeded dispersion left no clean or nearby-site contact" in first[2]
    assert "not a reach failure" in first[2]


@pytest.mark.parametrize("power,severity", [(0, Severity.BRUISE), (1, Severity.GRAZE),
                                           (7, Severity.GRAZE), (8, Severity.CUT),
                                           (15, Severity.CUT), (16, Severity.DEEP)])
def test_wound_thresholds(power, severity):
    state = arena()
    assert classify_wound(state.config, Zone.TORSO, power, ActionKind.LIGHT, False) == severity


def test_full_hand_severance_requires_clean_wrist_site_not_finger_or_palm_contact():
    state = arena()
    cfg = state.config
    assert classify_wound(cfg, Zone.HAND, 30, ActionKind.HEAVY, False,
                          "CUTTING", site="WRIST") == Severity.SEVERED
    assert classify_wound(cfg, Zone.HAND, 30, ActionKind.HEAVY, False,
                          "CUTTING", site="FINGERS") == Severity.DEEP
    assert classify_wound(cfg, Zone.HAND, 30, ActionKind.HEAVY, True,
                          "CUTTING", site="WRIST") == Severity.DEEP


def test_resolved_anatomical_site_is_seeded_public_and_persisted():
    def resolve():
        state = arena()
        attacker, defender = state.fighters
        attacker.accuracy = 100
        commit_action(state, attacker.id, Choice(ActionKind.LIGHT, Zone.TORSO,
                                                  target_id=defender.id))
        advance_to(state, attacker.action.strike_tick)
        hit = next(event for event in state.event_log if event.type == EventType.HIT)
        wound = next(event for event in state.event_log if event.type == EventType.WOUND)
        restored = CombatState.from_data(state.to_data())
        return hit, wound, restored.fighter(defender.id).wounds[-1], state.config["anatomy_sites"]["TORSO"]

    first, first_wound, saved_wound, allowed_sites = resolve()
    second, second_wound, _, _ = resolve()
    assert first.deltas["site"] == second.deltas["site"] == first_wound.deltas["site"]
    assert first.deltas["side"] == second.deltas["side"] == first_wound.deltas["side"]
    assert saved_wound.site == first_wound.deltas["site"]
    assert saved_wound.side == first_wound.deltas["side"]
    assert first.deltas["site"] in {site.removeprefix("LEFT_").removeprefix("RIGHT_")
                                     for site in allowed_sites}


def test_deep_foot_site_records_function_limits_and_mental_accuracy_effects():
    state = arena()
    attacker, defender = state.fighters
    before = effective_stats(defender, state.config, state.tick)
    pain = apply_wound(state, attacker, defender, Zone.LEG, Severity.DEEP,
                       20, 16, site="FOOT", side="LEFT")
    after = effective_stats(defender, state.config, state.tick)
    wound_event = next(event for event in state.event_log if event.type == EventType.WOUND)

    assert pain > 0
    assert defender.wounds[-1].site == "FOOT"
    assert defender.wounds[-1].side == "LEFT"
    assert defender.wounds[-1].structure == "FOOT_TENDON"
    assert wound_event.deltas["function"] == "impaired footing; reduced speed and no dash"
    assert any(status.no_dash for status in defender.statuses)
    assert legality(state, defender.id, Choice(ActionKind.DASH, direction=(1, 0))) == \
        "Leg injury prevents dashing"
    assert after.speed < before.speed
    assert after.accuracy < before.accuracy == defender.accuracy


def test_hand_sever_requires_clean_heavy_and_threshold():
    cfg = arena().config
    assert classify_wound(cfg, Zone.HAND, 22, ActionKind.HEAVY, False) == Severity.SEVERED
    assert classify_wound(cfg, Zone.HAND, 22, ActionKind.LIGHT, False) == Severity.DEEP
    assert classify_wound(cfg, Zone.HAND, 22, ActionKind.HEAVY, True) == Severity.DEEP


def test_torso_cut_bleeding_at_walk_exertion_is_point_zero_five_per_tick():
    state = arena()
    fighter = state.fighters[0]
    state.fighters[1].pos = (5, 0)
    fighter.wounds.append(Wound(Zone.TORSO, Severity.CUT,
                                state.config["wounds"]["TORSO"]["CUT"]["bleed"]))
    commit_action(state, fighter.id, Choice(ActionKind.WALK, direction=(0, 1)))
    advance_one_tick(state)
    assert fighter.blood == pytest.approx(99.95)
    fighter.wounds[0].bandaged = True
    advance_one_tick(state)
    assert fighter.blood == pytest.approx(99.95 - .05 * .4)


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


def test_bleed_pool_events_report_actual_tick_loss_rate_and_exertion():
    state = arena()
    fighter = state.fighters[0]
    fighter.wounds = [Wound(Zone.TORSO, Severity.DEEP, 6) for _ in range(6)]
    commit_action(state, fighter.id, Choice(ActionKind.WAIT))
    cursor, before = len(state.event_log), fighter.blood
    for _ in range(10):
        advance_one_tick(state)
    bleed_events = [event for event in state.event_log[cursor:]
                    if event.type == EventType.POOL_CHANGE and event.actor == fighter.id
                    and "BLEEDING" in event.cause_tags]
    assert len(bleed_events) == 10
    assert sum(event.deltas["bleed_loss"] for event in bleed_events) == pytest.approx(before-fighter.blood)
    assert sum(event.deltas["bleed_loss"] for event in bleed_events) == pytest.approx(14)
    assert all(event.deltas["bleed_rate_per_turn"] == pytest.approx(14)
               and event.deltas["bleed_exertion"] == "idle" for event in bleed_events)


def test_equal_horizon_physiology_audit_distinguishes_graze_cut_deep_stack_and_treatment():
    def ten_idle_ticks(wounds=(), *, initial_loss=0):
        state = arena(rng_seed=1818)
        fighter = state.fighters[0]
        fighter.wounds = list(wounds)
        before = fighter.blood
        fighter.blood -= initial_loss
        commit_action(state, fighter.id, Choice(ActionKind.WAIT))
        for _ in range(state.config["time"]["ticks_per_turn"]):
            advance_one_tick(state)
        return before - fighter.blood

    cfg = arena(rng_seed=1818).config
    graze = Wound(Zone.TORSO, Severity.GRAZE, 0, initial_blood_loss=0.25)
    cut_rate = cfg["wounds"]["TORSO"]["CUT"]["bleed"]
    deep_rate = cfg["wounds"]["TORSO"]["DEEP"]["bleed"]
    cut = Wound(Zone.TORSO, Severity.CUT, cut_rate)
    deep = Wound(Zone.TORSO, Severity.DEEP, deep_rate)
    treated_deep = Wound(Zone.TORSO, Severity.DEEP, deep_rate, bandaged=True)
    stacked = [Wound(Zone.TORSO, Severity.DEEP, deep_rate) for _ in range(6)]

    losses = {
        "none": ten_idle_ticks(),
        "graze": ten_idle_ticks([graze], initial_loss=0.25),
        "cut": ten_idle_ticks([cut]),
        "deep": ten_idle_ticks([deep]),
        "treated_deep": ten_idle_ticks([treated_deep]),
        "stacked": ten_idle_ticks(stacked),
    }
    assert losses["none"] == pytest.approx(0)
    assert losses["graze"] == pytest.approx(0.25)
    assert losses["cut"] == pytest.approx(cut_rate * cfg["blood"]["exertion"]["idle"])
    assert losses["deep"] == pytest.approx(deep_rate * cfg["blood"]["exertion"]["idle"])
    assert losses["treated_deep"] == pytest.approx(
        deep_rate * (1 - cfg["blood"]["bandage_reduction"]) * cfg["blood"]["exertion"]["idle"])
    assert losses["stacked"] == pytest.approx(
        cfg["blood"]["total_bleed_cap_per_turn"] * cfg["blood"]["exertion"]["idle"])


def test_unbandaged_serious_bleeding_resumes_deterministically_from_active_save():
    uninterrupted, resumed = arena(rng_seed=2818), arena(rng_seed=2818)
    for state in (uninterrupted, resumed):
        fighter = state.fighters[0]
        fighter.wounds = [Wound(Zone.TORSO, Severity.DEEP,
                                state.config["wounds"]["TORSO"]["DEEP"]["bleed"])]
        commit_action(state, fighter.id, Choice(ActionKind.WAIT))
    for _ in range(4):
        advance_one_tick(uninterrupted)
        advance_one_tick(resumed)
    resumed = CombatState.from_data(resumed.to_data())
    for _ in range(6):
        advance_one_tick(uninterrupted)
        advance_one_tick(resumed)
    assert resumed.fighters[0].blood == pytest.approx(uninterrupted.fighters[0].blood)
    assert resumed.fighters[0].wounds[0].bleed_per_turn == 8
    assert resumed.fighters[0].wounds[0].bandaged is False


def test_schema22_active_save_migrates_default_wound_rates_but_keeps_custom_rate():
    state = arena(rng_seed=2822)
    fighter = state.fighters[0]
    fighter.wounds = [
        Wound(Zone.TORSO, Severity.CUT, 3),
        Wound(Zone.TORSO, Severity.DEEP, 17),
        Wound(Zone.HAND, Severity.SEVERED, 8),
    ]
    data = state.to_data()
    data["schema_version"] = data["config"]["schema_version"] = 22
    for zone, severity, rate in (("TORSO", "CUT", 3), ("TORSO", "DEEP", 6),
                                 ("HAND", "SEVERED", 8)):
        data["config"]["wounds"][zone][severity]["bleed"] = rate

    restored = CombatState.from_data(data)

    assert [wound.bleed_per_turn for wound in restored.fighters[0].wounds] == [0.5, 17, 12]
    assert restored.config["wounds"]["TORSO"]["CUT"]["bleed"] == 0.5


def test_completed_bandage_event_names_treated_wound_and_remaining_rate():
    from pirates.duel_system.exchange_log import format_exchange

    state = arena()
    fighter = state.fighters[0]
    fighter.bandages = 2
    fighter.wounds = [Wound(Zone.ARM, Severity.CUT, 3),
                      Wound(Zone.TORSO, Severity.DEEP, 6)]
    before = sum(wound.bleed_per_turn for wound in fighter.wounds)
    commit_action(state, fighter.id, Choice(ActionKind.BANDAGE))
    advance_to(state, fighter.action.end_tick)
    event = next(event for event in state.event_log
                 if "BANDAGE_COMPLETED" in event.cause_tags)
    assert event.deltas["zone"] == "TORSO"
    assert event.deltas["rate_before"] == before
    assert event.deltas["rate_after"] == pytest.approx(before-6*state.config["blood"]["bandage_reduction"])
    assert event.deltas["bandages_remaining"] == 1
    lines = format_exchange(state.event_log, {fighter.id: "You", state.fighters[1].id: "Enemy"})
    assert any("bandaged the torso deep wound" in line and "bleed rate 9 → 5.4" in line
               for line in lines)


@pytest.mark.parametrize("zone,stat,status", [
    (Zone.LEG, "speed_mult", "slowed"),
    (Zone.ARM, "strength_mult", "weak_grip"),
    (Zone.HAND, "strength_mult", "weak_grip"),
    (Zone.HEAD, "speed_mult", "dizzy"),
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
    assert defender.wounds[-1].bleed_per_turn == 12


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


def test_stale_bilateral_eye_resolution_falls_back_before_armor_and_does_not_crash(monkeypatch):
    from pirates.duel_system import wounds

    state = arena()
    attacker, defender = state.fighters
    defender.permanent_injuries.eyes_damaged = 2
    commit_action(state, attacker.id, Choice(ActionKind.LIGHT, Zone.HEAD))
    monkeypatch.setattr(wounds, "resolve_zone", lambda *_args, **_kwargs: (Zone.EYE, False))

    wounds.resolve_strike(state, attacker, defender, defense_snapshot(state, defender))

    hit = next(event for event in reversed(state.event_log) if event.type == EventType.HIT)
    assert hit.zone == Zone.HEAD
    assert hit.deltas["armor_zone"] == "HEAD"
    assert hit.deltas["drifted"] is True


def test_strike_with_no_eligible_site_becomes_logged_whiff_not_runtime_crash(monkeypatch):
    from pirates.duel_system import wounds

    state = arena()
    attacker, defender = state.fighters
    commit_action(state, attacker.id, Choice(ActionKind.LIGHT, Zone.TORSO))
    monkeypatch.setattr(wounds, "choose_anatomical_site",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            ValueError("No anatomical site is eligible for this injury")))

    wounds.resolve_strike(state, attacker, defender, defense_snapshot(state, defender))

    whiff = next(event for event in reversed(state.event_log) if event.type == EventType.WHIFF)
    assert whiff.deltas["reason"] == "NO_ELIGIBLE_ANATOMICAL_SITE"
    assert "NO_ELIGIBLE_ANATOMICAL_SITE" in whiff.cause_tags
