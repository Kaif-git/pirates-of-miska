"""COMBAT SPEC gates M0/M1. No world, SDL, or legacy combat imports."""
from dataclasses import replace
import json

import pytest

from pirates.duel_system.config import load_config, validate_config
from pirates.duel_system.model import (
    ActionKind, CombatState, Equipment, EventType, Fighter, PreFightState,
    Status, Weapon, Wound, Zone, Severity,
)
from pirates.duel_system.stats import (
    effective_stats, fatigue_penalty, fear, max_stamina, pay_stamina,
    phase_ticks, stamina_cost, fatigue_gain, refresh_exhaustion,
)


def bare_fighter(identifier="player", **overrides):
    values = dict(id=identifier, name=identifier.title(), level=1, strength=10,
                  speed=10, accuracy=20, stamina_stat=20, endurance=11,
                  equipment=Equipment(weapon=Weapon(weight=0)), fear_meter=0)
    values.update(overrides)
    return Fighter(**values)


def test_config_is_complete_isolated_and_json_serializable():
    cfg = load_config()
    assert cfg["time"]["ticks_per_turn"] == 10
    assert cfg["actions"]["LIGHT"]["windup"] == 2.9
    assert cfg["actions"]["DASH_LIGHT"]["stamina"] == 12.26
    assert cfg["zones"]["difficulty"]["HAND"] == 20
    assert cfg["wounds"]["TORSO"]["CUT"]["bleed"] == 0.5
    assert cfg["ronin"]["reaction_delay"] == 4
    assert json.loads(json.dumps(cfg)) == cfg
    cfg["actions"]["LIGHT"]["windup"] = 999
    assert load_config()["actions"]["LIGHT"]["windup"] == 2.9


@pytest.mark.parametrize("change", [
    lambda cfg: cfg["stats"].__setitem__("fatigue_accuracy", 0.1),
    lambda cfg: cfg["actions"]["HEAVY"].__setitem__("acc_mod", 1),
    lambda cfg: cfg["wounds"]["HEAD"]["DEEP"].__setitem__("accuracy_mult", 0.5),
])
def test_current_config_rejects_accuracy_debuff_tuning(change):
    cfg = load_config()
    change(cfg)
    with pytest.raises(ValueError, match="Only authored fear/anger|unknown configuration keys"):
        validate_config(cfg)


def test_invalid_config_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"time": {"ticks_per_turn": 0}}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(path)


def test_seeded_rng_and_typed_events_are_deterministic_100_times():
    def run():
        state = CombatState([bare_fighter(), bare_fighter("ronin")], rng_seed=137)
        for _ in range(20):
            state.emit(EventType.ACTION_START, "player", "ronin", Zone.HAND,
                       deltas={"jitter": state.uniform(-1, 1)},
                       cause_tags=("TEST",))
            state.tick += 1
        return state.to_data()
    expected = run()
    for _ in range(100):
        assert run() == expected


def test_clone_isolates_fighters_wounds_equipment_config_rng_and_event_deltas():
    state = CombatState([bare_fighter(), bare_fighter("ronin")], rng_seed=44)
    state.fighters[0].wounds.append(Wound(Zone.TORSO, Severity.CUT, 3))
    state.emit(EventType.WOUND, "player", deltas={"blood": -2})
    before = state.to_data()
    clone = state.clone()
    clone.fighters[0].wounds[0].bandaged = True
    clone.fighters[0].equipment.weapon = Weapon(edge=99)
    clone.fighters[1].pain = 70
    clone.event_log[0].deltas["blood"] = -99
    clone.config["blood"]["collapse"] = 0
    clone.uniform(-1, 1)
    assert state.to_data() == before
    assert state.uniform(-1, 1) == CombatState.from_data(before).uniform(-1, 1)


def test_snapshot_roundtrip_preserves_rng_and_enums_and_rejects_version():
    state = CombatState([bare_fighter(), bare_fighter("ronin")], rng_seed=141)
    state.uniform(-1, 1)
    state.fighters[0].statuses.append(Status("dizzy", until_tick=40, accuracy_mult=0.85))
    state.fighters[0].wounds.append(Wound(Zone.HAND, Severity.CUT, 2))
    state.emit(EventType.HIT, "ronin", "player", Zone.HAND, Severity.CUT)
    data = json.loads(json.dumps(state.to_data()))
    restored = CombatState.from_data(data)
    assert restored.to_data() == data
    assert restored.uniform(-1, 1) == state.uniform(-1, 1)
    data["schema_version"] += 1
    with pytest.raises(ValueError, match="version"):
        CombatState.from_data(data)


@pytest.mark.parametrize("stats", [{"speed": -1}, {"blood": 101}, {"level": 0},
                                  {"speed": float("nan")}, {"stamina_stat": 0}])
def test_invalid_fighter_rejected(stats):
    with pytest.raises(ValueError):
        bare_fighter(**stats)


def test_prefight_values_are_carried_without_aliasing():
    wound = Wound(Zone.LEG, Severity.CUT, 2)
    prefight = PreFightState(fatigue=35, stamina=40, blood=65, injuries=[wound], nervousness=0.2)
    fighter = bare_fighter(prefight=prefight)
    assert (fighter.fatigue, fighter.stamina, fighter.blood) == (35, 40, 65)
    fighter.wounds[0].bandaged = True
    assert not wound.bandaged
    assert fighter.nervousness == 0
    assert fighter.fear_meter == 20


def test_m1_fresh_stats_and_spec_timings_without_weight():
    cfg = load_config()
    fast = bare_fighter(speed=20)
    assert effective_stats(fast, cfg).speed == 20
    assert phase_ticks(4, effective_stats(fast, cfg).speed, cfg) == 2
    assert phase_ticks(5, effective_stats(fast, cfg).speed, cfg) == 3
    assert phase_ticks(12, 14, cfg) == 9
    assert max_stamina(fast, cfg) == 100
    assert max_stamina(replace(fast, level=20), cfg) == pytest.approx(157)


def test_m1_fatigue_gain_endurance_and_weight_formulas():
    cfg = load_config()
    dorime = bare_fighter(endurance=11)
    ronin = bare_fighter(endurance=20)
    assert fatigue_gain(dorime, 14, cfg) == pytest.approx(13.3)
    assert fatigue_gain(ronin, 16, cfg) == pytest.approx(8)
    dorime.equipment.weapon = Weapon(weight=3)
    assert stamina_cost(dorime, 14, cfg) == pytest.approx(14 * 1.06)
    assert fatigue_gain(dorime, 14 * 1.06, cfg) == pytest.approx(14 * 1.06 * .95 * 1.09)
    assert effective_stats(dorime, cfg).speed == pytest.approx(9.7)


def test_m1_exhaustion_pays_once_converts_shortfall_and_recovers():
    cfg = load_config()
    fighter = bare_fighter(stamina=2)
    state = CombatState([fighter, bare_fighter("ronin")], config=cfg)
    paid = pay_stamina(state, fighter, 6)
    assert paid == 2
    assert fighter.stamina == 0
    assert fighter.fatigue == pytest.approx(2 * .95 + 4)
    assert fighter.exhausted
    assert effective_stats(fighter, cfg).speed == 6
    assert effective_stats(fighter, cfg).strength == 6
    assert state.event_log[-1].type == EventType.EXHAUST
    fighter.stamina = 24.99
    refresh_exhaustion(fighter, cfg)
    assert fighter.exhausted
    fighter.stamina = 25
    refresh_exhaustion(fighter, cfg)
    assert not fighter.exhausted


def test_physical_effective_stats_preserve_permanent_accuracy():
    cfg = load_config()
    fighter = bare_fighter(level=4, speed=18, strength=16, accuracy=19,
                          endurance=11, fatigue=60, pain=40, blood=35, fear_meter=25,
                          anger=50, morale=25, nervousness=.1)
    eff = effective_stats(fighter, cfg)
    lvl = 1.09
    fat = (60 - 15) / 85 * (1.6 - 11 * lvl / 20) * .7
    pain = (40 * (1 - .5 * cfg["stats"]["pain_tolerance_suppression"])) / 100 * max(.1, 1.2 - 11 * lvl / 20)
    blood = (50 - 35) / 25 * .25
    assert fatigue_penalty(fighter, cfg) == pytest.approx(fat)
    assert fear(fighter, cfg) == 25
    assert eff.speed == pytest.approx(18 * lvl * (1-fat) * (1-.5*pain) * (1-blood)
                                      * (1-.30*.25) * 1.075)
    assert eff.strength == pytest.approx(16 * lvl * (1-.8*fat) * (1-.5*pain) * (1-blood) * 1.125)
    assert eff.accuracy == pytest.approx(19 * (1 - cfg["stats"]["fear_accuracy"] * .25)
                                        * (1 - cfg["anger"]["acc_penalty"] * .5))
    fighter.fatigue = fighter.pain = 100
    fighter.blood = 1
    fighter.exhausted = True
    fighter.statuses = [Status("test", speed_mult=.01, strength_mult=.01, accuracy_mult=.01)]
    eff = effective_stats(fighter, cfg)
    assert eff.speed == 18 * .25
    assert eff.strength == 16 * .25
    assert eff.accuracy == pytest.approx(19 * (1 - cfg["stats"]["fear_accuracy"] * .25)
                                         * (1 - cfg["anger"]["acc_penalty"] * .5))


def test_status_modifiers_expire_at_their_deadline():
    cfg = load_config()
    fighter = bare_fighter()
    fighter.statuses = [Status("slowed", until_tick=5, speed_mult=.75)]
    assert effective_stats(fighter, cfg, tick=4).speed == 7.5
    assert effective_stats(fighter, cfg, tick=5).speed == 10
