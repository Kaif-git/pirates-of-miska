"""M6 E1–E10 and perception/legality/purity gates, written before the AI."""
from copy import deepcopy

import pytest

from pirates.duel_system.ai import choose_action, movement_choice, observe, score_actions
from pirates.duel_system.engine import Choice, advance_one_tick, commit_action, movement_path
from pirates.duel_system.model import (ActionKind, ActiveAction, CombatState, EventType,
                                       Phase, Personality, Severity, Wound, Zone)
from pirates.duel_system.stats import effective_stats
from test_duel_system_scheduler import arena


def scenario(*, anger=0, gap=0, stamina=100, fatigue=0, morale=80, enemy_speed=14,
             player_speed=20, player_strength=10, own_strength=18, phase="IDLE",
             remaining=0, whiff=False, seen=True):
    state = arena()
    player, ronin = state.fighters
    player.speed, player.strength = player_speed, player_strength
    player.pos = (0, 0)
    ronin.pos = (gap + 1, 0)
    ronin.personality = Personality(**deepcopy(state.config["ronin"]))
    ronin.strength, ronin.speed = own_strength, enemy_speed
    ronin.accuracy = 16
    ronin.anger, ronin.stamina, ronin.fatigue, ronin.morale = anger, stamina, fatigue, morale
    state.tick = 20
    if phase != "IDLE":
        kind = ActionKind.HEAVY if phase == "WINDUP_H" else ActionKind.LIGHT
        start = 0 if seen else state.tick
        end = state.tick + remaining
        strike = end if phase.startswith("WINDUP") else state.tick - 1
        phase_name = "WINDUP" if phase.startswith("WINDUP") else phase
        player.action = ActiveAction(kind, Zone.TORSO, None, start,
                                    [Phase(phase_name, state.tick, end)], strike, 0,
                                    effective_stats(player, state.config), end,
                                    windup_len=12 if kind == ActionKind.HEAVY else 4,
                                    power_mult=1.6 if kind == ActionKind.HEAVY else 1)
    player.last_strike_whiffed = whiff
    return state


@pytest.mark.parametrize("kwargs,expected", [
    ({"anger": 10}, "LIGHT"),                                           # E1
    ({"anger": 70, "phase": "RECOVERY", "remaining": 2, "whiff": True}, "HEAVY"), # E2
    ({"anger": 70, "phase": "RECOVERY", "remaining": 12}, "HEAVY"),  # E3
    ({"phase": "WINDUP_H", "remaining": 9}, "LIGHT"),                 # E4
    ({"phase": "WINDUP_H", "remaining": 1, "enemy_speed": 5,
      "own_strength": 5, "player_strength": 20}, "DASH_OUT"),           # E5
    ({"phase": "GUARD_HOLD", "remaining": 30, "anger": 50}, "HEAVY"),# E6
    ({"stamina": 20, "gap": 4, "fatigue": 50}, "WAIT"),               # E7
    ({"gap": 2}, "DASH_LIGHT"),                                        # E9
])
def test_ronin_expected_behavior_e1_e7_e9(kwargs, expected):
    decision = choose_action(scenario(**kwargs), "ronin")
    assert decision.category == expected


def test_e8_fear90_uses_run_or_walk_escape_not_attack():
    decision = choose_action(scenario(morale=5), "ronin")
    assert decision.category in {"RUN_OUT", "WALK_OUT"}


def test_e10_same_full_state20_times_identical_and_read_only_no_rng():
    state = scenario(anger=70, phase="RECOVERY", remaining=12)
    before = state.to_data()
    results = [choose_action(state, "ronin") for _ in range(20)]
    assert all(item == results[0] for item in results)
    assert state.to_data() == before


def test_reaction_delay_hides_new_commitment_and_uses_previous_category():
    state = scenario(phase="WINDUP_H", remaining=9, seen=False)
    player = state.fighters[0]
    player.last_action_cat = "HEAVY"
    player.previous_action_cat = "WAIT"
    view = observe(state, "ronin")
    assert view.public_key.opp_state == "UNSEEN"
    assert view.public_key.opp_last_cat == "WAIT"
    assert view.opponent_action is None
    state.tick += state.fighters[1].personality.reaction_delay
    view = observe(state, "ronin")
    assert view.opponent_action == ActionKind.HEAVY
    assert view.public_key.opp_state == "WINDUP_H"


def test_two_unseen_action_kinds_do_not_change_ai_observation_or_scores():
    first = scenario(phase="WINDUP_H", remaining=9, seen=False)
    second = first.clone()
    second.fighters[0].action.kind = ActionKind.LIGHT
    assert observe(first, "ronin") == observe(second, "ronin")
    assert score_actions(first, "ronin") == score_actions(second, "ronin")


def test_ai_scores_bleeding_treatment_and_waiting_to_recover_stability():
    state = scenario(gap=3)
    ronin = state.fighter("ronin")
    ronin.wounds.append(Wound(Zone.TORSO, Severity.DEEP, 6))
    ronin.bandages = 1
    bandage = {item.category: item for item in score_actions(state, ronin.id)}["BANDAGE"]
    assert any(name == "I5_BANDAGE_BLEED" and value > 0 for name, value in bandage.reasons)
    ronin.blood = 25
    assert choose_action(state, ronin.id).category != "BANDAGE"  # opponent is free to act before treatment completes
    state.fighter("player").stagger_until = state.tick + 100
    assert choose_action(state, ronin.id).category == "BANDAGE"
    ronin.wounds.clear()
    ronin.stability = 40
    waiting = {item.category: item for item in score_actions(state, ronin.id)}["WAIT"]
    assert any(name == "I5_WAIT_STABILITY" and value > 0 for name, value in waiting.reasons)
    state.fighter("player").stagger_until = 0
    ronin.wounds = [Wound(Zone.TORSO, Severity.DEEP, 20)]
    ronin.blood = 1
    assert choose_action(state, ronin.id).category != "BANDAGE"


def test_ai_prioritizes_shove_of_witnessed_unsteady_opponent_into_open_tile():
    from test_duel_system_foundation import bare_fighter
    player = bare_fighter("player", pos=(1, 1), team_id="players")
    ronin = bare_fighter("ronin", pos=(2, 1), team_id="raiders")
    ally = bare_fighter("ally", pos=(5, 1), team_id="raiders")
    ronin.personality = Personality(**deepcopy(scenario().config["ronin"]))
    state = CombatState([player, ronin, ally], walkable={(x, y) for x in range(6) for y in range(4)})
    state.emit(EventType.STABILITY, player.id,
               deltas={"source": "HIT_IMPACT", "loss": 12, "band": "OFF_BALANCE"})
    shove = {item.category: item for item in score_actions(state, ronin.id)}["SHOVE"]
    assert any(name == "I5_SHOVE_UNSTEADY" and value > 0 for name, value in shove.reasons)
    assert any(name == "I5_SHOVE_SPACE" and value > 0 for name, value in shove.reasons)


def test_opposed_closing_walkers_choose_distinct_flanks_and_both_advance():
    state = arena()
    player, ronin = state.fighters
    player.pos, ronin.pos = (5, 3), (7, 3)
    player_walk = movement_choice(state, player, ActionKind.WALK, toward=True)
    ronin_walk = movement_choice(state, ronin, ActionKind.WALK, toward=True)
    assert player_walk is not None and ronin_walk is not None
    player_destination = movement_path(state, player, player_walk)[-1]
    ronin_destination = movement_path(state, ronin, ronin_walk)[-1]
    assert player_destination != ronin_destination
    assert max(abs(player_destination[0] - ronin_destination[0]),
               abs(player_destination[1] - ronin_destination[1])) == 1

    commit_action(state, player.id, player_walk)
    commit_action(state, ronin.id, ronin_walk)
    for _ in range(10):
        advance_one_tick(state)
    assert player.pos == player_destination
    assert ronin.pos == ronin_destination


def test_accuracy_does_not_gate_ai_zone_choice_on_a_witnessed_opening():
    low = scenario(gap=0)
    high = low.clone()
    low.fighter("player").stagger_until = low.tick + 20
    high.fighter("player").stagger_until = high.tick + 20
    low.fighter("ronin").accuracy = 10
    high.fighter("ronin").accuracy = 16

    low_light = next(item for item in score_actions(low, "ronin") if item.category == "LIGHT")
    high_light = next(item for item in score_actions(high, "ronin") if item.category == "LIGHT")
    assert low_light.choice.aim == high_light.choice.aim == Zone.HEAD
    assert effective_stats(low.fighter("ronin"), low.config, low.tick).accuracy == 10
    assert effective_stats(high.fighter("ronin"), high.config, high.tick).accuracy == 16


def test_zone_preference_uses_exposure_and_phase_not_accuracy_thresholds():
    recovering = scenario(phase="RECOVERY", remaining=12)
    recovering.fighters[1].accuracy = 25
    assert choose_action(recovering, "ronin").choice.aim == Zone.HAND
    guarding = scenario(phase="GUARD_HOLD", remaining=30, anger=50)
    guarding.fighters[1].accuracy = 20  # Accuracy does not turn the covered hand into a legal target.
    assert choose_action(guarding, "ronin").choice.aim == Zone.LEG
    idle = scenario()
    assert choose_action(idle, "ronin").choice.aim == Zone.TORSO


def test_authored_profiles_choose_distinct_responses_to_a_visible_light_commitment():
    state = scenario(phase="WINDUP_L", remaining=8)
    fighter = state.fighter("ronin")
    base = deepcopy(state.config["ronin"])
    observed = {}

    for profile_name in ("brute", "aggressor", "veteran", "counterfighter", "recruit", "bully"):
        profile = deepcopy(base)
        profile.update(deepcopy(state.config["ai_profiles"][profile_name]))
        fighter.personality = Personality(**profile)
        observed[profile_name] = choose_action(state, fighter.id).category

    assert observed["veteran"] == "GUARD"
    assert observed["counterfighter"] == "GUARD"
    assert observed["recruit"] == "GUARD"
    assert observed["aggressor"] == "LIGHT"
    assert observed["brute"] == "LIGHT"
    assert observed["bully"] == "HEAVY"


def test_candidates_never_dash_when_exhausted_or_plan_into_walls():
    state = scenario(gap=2)
    state.fighters[1].exhausted = True
    state.walkable = {f.pos for f in state.fighters}
    decision = choose_action(state, "ronin")
    assert decision.choice.kind not in {ActionKind.DASH, ActionKind.DASH_LIGHT, ActionKind.HEAVY}


def test_fixed_tie_order_not_dict_order_decides_equal_scores():
    state = scenario()
    ronin = state.fighters[1]
    ronin.personality.base = dict.fromkeys(ronin.personality.base, 0)
    ronin.personality.weights = dict.fromkeys(ronin.personality.weights, 0)
    ronin.personality.traits = dict.fromkeys(ronin.personality.traits, 0.5)
    state.config["personality"]["variation"]["amplitude"] = 0
    # Disable non-weighted positional/fatigue/pain rules in this neutral scenario.
    assert choose_action(state, "ronin").category == "GUARD"


def test_literal_starting_constants_explain_documented_e5_e7_e8_conflicts():
    samples = [(scenario(phase="WINDUP_H", remaining=1, enemy_speed=5,
                         own_strength=5, player_strength=20), "LIGHT"),
               (scenario(stamina=20, gap=4, fatigue=50), "WALK_IN"),
               (scenario(morale=5), "DASH_OUT")]
    for state, original_result in samples:
        state.config["ai"]["counter_heavy_dash"] = 40
        state.fighters[1].personality.base["WAIT"] = -50
        state.fighters[1].personality.base["RUN_OUT"] = 0
        assert choose_action(state, "ronin").category == original_result
