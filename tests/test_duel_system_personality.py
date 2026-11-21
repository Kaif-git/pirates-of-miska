"""P2 profile, emotion, seeded-choice and resolved-adaptation acceptance gates."""
from copy import deepcopy

import pytest

from pirates.duel_system.ai import (
    choose_action, decision_control, score_actions,
)
from pirates.duel_system.config import load_config, upgrade_config, validate_config
from pirates.duel_system.engine import Choice, commit_action, legality
from pirates.duel_system.model import ActionKind, EventType, Personality, Zone
from pirates.duel_system.session import DuelSession
from pirates.duel_system.stats import legacy_fear_pressure
from test_duel_system_scheduler import arena


def neutral_ai_state(seed=1):
    state = arena(rng_seed=seed)
    fighter = state.fighters[1]
    fighter.personality = Personality(**deepcopy(state.config["ronin"]))
    return state, fighter


def legacy_policy_config(version):
    """Build a pre-schema-29 config carrying equivalent legacy profile inputs."""
    config = deepcopy(load_config())
    config["schema_version"] = version
    config["defaults"]["confidence"] = 1 - config["defaults"].pop("starting_fear") / 100
    for profile in [config["ronin"], *config["ai_profiles"].values()]:
        confidence = 1 - profile.pop("starting_fear") / 100
        stress = profile.pop("stress_composure")
        morale = profile.pop("morale_bias")
        profile["traits"] = {"aggression": 0.5, "discipline": stress,
                             "confidence": confidence, "patience": 0.5,
                             "adaptability": 0.5}
        profile["courage"] = morale
        profile.update(weights={}, base={}, reaction_delay=4, zone_pref=["TORSO"], score_bias={})
    config["personality"].update({
        "variation": {"margin": 0, "amplitude": 0},
        "adaptation": {"history": 12, "minimum_counters": 3,
                       "counter_penalty": 1, "success_decay": 1, "max_penalty": 10},
    })
    return config


def test_numeric_response_profiles_are_bounded_and_identity_is_selectable():
    config = DuelSession.new(guide_mode="off").state.config
    assert set(config["ai_profiles"]) == {
        "brute", "aggressor", "veteran", "counterfighter", "recruit", "bully",
    }
    assert DuelSession.new(seed=1, guide_mode="off").enemy.personality.archetype == "brute"
    assert DuelSession.new(seed=2, guide_mode="off").enemy.personality.archetype != "brute"
    assert DuelSession.new(archetype="recruit", guide_mode="off").enemy.personality.archetype == "recruit"
    personality = DuelSession.new(archetype="aggressor", guide_mode="off").enemy.personality
    assert 0 <= personality.starting_fear <= 100
    with pytest.raises(ValueError, match="susceptibility"):
        Personality(archetype="bad", fear_gain_susceptibility=1.6)
    invalid_config = load_config()
    invalid_config["ai_profiles"]["brute"]["stress_composure"] = 1.1
    with pytest.raises(ValueError, match="bounded response"):
        validate_config(invalid_config)
    invalid_config = load_config()
    invalid_config["defaults"]["starting_fear"] = True
    with pytest.raises(ValueError, match="starting_fear: expected finite number"):
        validate_config(invalid_config)
    invalid_susceptibility = load_config()
    invalid_susceptibility["ai_profiles"]["brute"]["fear_gain_susceptibility"] = 1.51
    with pytest.raises(ValueError, match="fear_gain_susceptibility"):
        validate_config(invalid_susceptibility)


def test_schema25_config_and_fighter_migration_maps_confidence_to_fear_susceptibility():
    old_config = legacy_policy_config(25)
    old_config["defaults"]["confidence"] = 0.3
    old_config["ai_profiles"]["recruit"]["traits"]["confidence"] = 0.1
    old_config["ai_profiles"]["recruit"].pop("fear_gain_susceptibility")
    old_config["ronin"].pop("fear_gain_susceptibility")
    for profile in old_config["ai_profiles"].values():
        profile.pop("fear_gain_susceptibility", None)
    old_config["ai_profiles"]["brute"]["score_bias"]["LIGHT"] = 77

    migrated = upgrade_config(old_config)
    assert migrated["schema_version"] == 30
    assert migrated["ai_profiles"]["recruit"]["fear_gain_susceptibility"] == 1.2
    assert migrated["ai_profiles"]["brute"]["fear_gain_susceptibility"] == 0.9
    assert migrated["ai_profiles"]["recruit"]["starting_fear"] == 90
    assert migrated["defaults"]["starting_fear"] == 70
    assert "score_bias" not in migrated["ai_profiles"]["brute"]
    assert migrated["ronin"]["fear_gain_susceptibility"] == 0.9

    session = DuelSession.new(archetype="recruit", guide_mode="off")
    data = session.to_data()
    data["version"] = data["state"]["schema_version"] = 25
    data["state"]["config"] = deepcopy(old_config)
    data["notebook"]["version"] = 25
    data["state"]["fighters"] = [row for row in data["state"]["fighters"]]
    for fighter in data["state"]["fighters"]:
        if fighter["personality"]:
            fighter["personality"] = {
                "archetype": "recruit",
                **deepcopy(old_config["ai_profiles"]["recruit"]),
            }
    restored = DuelSession.from_data(data)
    assert restored.state.config["schema_version"] == 30
    assert restored.enemy.personality.fear_gain_susceptibility == 1.2

    old_v26 = legacy_policy_config(26)
    old_v26.pop("behavior_thresholds")
    migrated_v27 = upgrade_config(old_v26)
    assert migrated_v27["schema_version"] == 30
    assert migrated_v27["behavior_thresholds"] == {
        "mode_enter": 50, "mode_exit": 40, "critical_enter": 100,
        "critical_exit": 90, "yield_morale_max": 25,
    }
    old_v27 = legacy_policy_config(27)
    old_v27.pop("universal_ai")
    old_v27["ai_profiles"]["veteran"]["traits"]["confidence"] = 0.4
    migrated_v28 = upgrade_config(old_v27)
    assert migrated_v28["schema_version"] == 30
    assert migrated_v28["universal_ai"]["base"]["LIGHT"] == 20
    assert migrated_v28["ai_profiles"]["veteran"]["starting_fear"] == 60


def test_schema_23_adds_dash_slash_without_rewriting_dash_light():
    prior = legacy_policy_config(23)
    dash_light = deepcopy(prior["actions"]["DASH_LIGHT"])
    prior["actions"].pop("DASH_SLASH")

    migrated = upgrade_config(prior)

    assert migrated["schema_version"] == 30
    assert migrated["actions"]["DASH_LIGHT"] == dash_light
    assert migrated["actions"]["DASH_SLASH"]["max_tiles"] == 1


def test_numeric_stress_response_does_not_select_actions_but_composure_remains_bounded():
    state, fighter = neutral_ai_state()
    fighter.personality.stress_composure = 0.5
    aggressive = {row.category: row for row in score_actions(state, fighter.id)}
    fighter.personality.stress_composure = 0.1
    low_composure = {row.category: row for row in score_actions(state, fighter.id)}
    assert {key: row.score for key, row in aggressive.items()} == {key: row.score for key, row in low_composure.items()}
    assert not any(name.startswith("P2_") for row in aggressive.values() for name, _ in row.reasons)

    calm = decision_control(state, fighter.id)
    fighter.morale, fighter.pain, fighter.nervousness = 0, 80, 1
    pressured = decision_control(state, fighter.id)
    assert 0 <= pressured < calm <= 1
    weights = state.config["personality"]["pressure_weights"]
    expected_pressure = (weights["fear"] * legacy_fear_pressure(fighter, state.config) / 100
                         + weights["pain"] * fighter.pain / 100
                         + weights["nervousness"] * fighter.nervousness)
    assert pressured == pytest.approx(
        fighter.personality.composure * (1-expected_pressure*(1-fighter.personality.stress_composure)))


def test_authored_profile_identity_does_not_change_universal_action_scores():
    aggressor = DuelSession.new(archetype="aggressor", guide_mode="off")
    recruit = DuelSession.new(archetype="recruit", guide_mode="off")
    for session in (aggressor, recruit):
        session.player.pos, session.enemy.pos = (4, 3), (5, 3)
        session.enemy.morale = 70
        session.enemy.fear_meter = 0
    aggressive_scores = {row.category: row.score for row in score_actions(aggressor.state, "ronin")}
    recruit_scores = {row.category: row.score for row in score_actions(recruit.state, "ronin")}
    assert aggressor.enemy.anger == recruit.enemy.anger
    assert aggressor.enemy.morale == recruit.enemy.morale
    assert aggressive_scores == recruit_scores


def test_seeded_action_ties_do_not_depend_on_npc_identity_label():
    state, fighter = neutral_ai_state(seed=725)
    state.fighters[0].pos, fighter.pos = (4, 3), (5, 3)
    before = choose_action(state, fighter.id)
    original_id = fighter.id
    fighter.id = "renamed-npc"
    after = choose_action(state, fighter.id)
    assert before.choice == after.choice
    fighter.id = original_id


def test_shared_modes_adjust_preferences_without_personality_specific_policy():
    state, fighter = neutral_ai_state()
    calm = {row.category: row for row in score_actions(state, fighter.id)}
    fighter.behavior_modes["fear"] = "CAUTIOUS"
    cautious = {row.category: row for row in score_actions(state, fighter.id)}
    assert dict(cautious["DASH_OUT"].reasons)["MODE_CAUTIOUS_SPACE"] > 0
    assert cautious["DASH_OUT"].score > calm["DASH_OUT"].score
    fighter.behavior_modes["anger"] = "HOT"
    hot = {row.category: row for row in score_actions(state, fighter.id)}
    assert dict(hot["LIGHT"].reasons)["MODE_ANGER_PRESSURE"] == state.config[
        "universal_ai"]["mode_bonus"]["hot_attack"]


def test_universal_seeded_tiebreak_is_repeatable_bounded_and_rng_free():
    variations = set()
    for seed in range(24):
        state, fighter = neutral_ai_state(seed)
        before = state.to_data()
        rows = score_actions(state, fighter.id)
        first = choose_action(state, fighter.id)
        assert choose_action(state, fighter.id) == first
        assert state.to_data() == before
        values = [dict(row.reasons)["UNIVERSAL_SEEDED_TIE"] for row in rows
                  if "UNIVERSAL_SEEDED_TIE" in dict(row.reasons)]
        assert values
        assert all(abs(value) <= state.config["universal_ai"]["tie_break"]["amplitude"]
                   for value in values)
        variations.update(values)
    assert len(variations) > 1


def test_matched_seed_openings_are_profile_invariant_and_legal():
    names = ("brute", "aggressor", "veteran", "counterfighter", "recruit", "bully")
    for seed in range(1, 41):
        decisions = []
        for name in names:
            session = DuelSession.new(seed=seed, archetype=name, guide_mode="off")
            session.enemy.fear_meter = 0
            decision = choose_action(session.state, session.enemy.id)
            assert not legality(session.state, session.enemy.id, decision.choice)
            decisions.append(decision)
        assert len({decision.category for decision in decisions}) == 1
        assert all(decision == decisions[0] for decision in decisions)


def test_retry_keeps_the_selected_npc_profile():
    session = DuelSession.new(seed=8, archetype="counterfighter", guide_mode="off")
    retry = session.restart()
    assert retry.enemy.personality.archetype == "counterfighter"


def test_schema19_config_migrates_shove_timing_tuning_and_measured_profile_biases():
    old = legacy_policy_config(19)
    old["tactics"].pop("shove_committed_heavy_bonus")
    old["ai_profiles"]["brute"]["score_bias"]["DASH_LIGHT"] = 0
    old["ai_profiles"]["bully"]["score_bias"]["DASH_LIGHT"] = 2
    migrated = upgrade_config(old)
    assert migrated["schema_version"] == 30
    assert migrated["tactics"]["shove_committed_heavy_bonus"] == 5
    assert "score_bias" not in migrated["ai_profiles"]["brute"]


def test_schema21_config_migration_adds_tolerance_defaults_without_losing_traits():
    old = legacy_policy_config(21)
    old["stats"]["fear_accuracy"] = 0
    old["anger"]["acc_penalty"] = 0
    for key in ("confidence", "anger_tolerance", "pain_tolerance"):
        old["defaults"].pop(key)
    for profile in old["ai_profiles"].values():
        profile.pop("anger_tolerance")
        profile.pop("pain_tolerance")
    old["ronin"].pop("anger_tolerance")
    old["ronin"].pop("pain_tolerance")

    migrated = upgrade_config(old)

    assert migrated["schema_version"] == 30
    assert migrated["ai_profiles"]["veteran"]["stress_composure"] == 0.9
    assert migrated["ai_profiles"]["veteran"]["pain_tolerance"] == 0.9
    assert migrated["ai_profiles"]["recruit"]["anger_tolerance"] == 0.25
    assert migrated["defaults"]["starting_fear"] == load_config()["defaults"]["starting_fear"]
    assert migrated["stats"]["fear_accuracy"] == load_config()["stats"]["fear_accuracy"]
    assert migrated["anger"]["acc_penalty"] == load_config()["anger"]["acc_penalty"]


def test_schema22_bleed_migration_updates_shipped_defaults_but_preserves_custom_tuning():
    old = legacy_policy_config(22)
    old["wounds"]["TORSO"]["CUT"]["bleed"] = 3
    old["wounds"]["TORSO"]["DEEP"]["bleed"] = 17
    old["wounds"]["HAND"]["SEVERED"]["bleed"] = 8

    migrated = upgrade_config(old)

    assert migrated["schema_version"] == 30
    assert migrated["wounds"]["TORSO"]["CUT"]["bleed"] == 0.5
    assert migrated["wounds"]["TORSO"]["DEEP"]["bleed"] == 17
    assert migrated["wounds"]["HAND"]["SEVERED"]["bleed"] == 12


def test_old_adaptation_history_and_deadlock_fields_are_discarded_on_restore():
    session = DuelSession.new(archetype="brute", guide_mode="off")
    data = session.to_data()
    for row in data["state"]["fighters"]:
        row["ai_experience"] = [{"category": "LIGHT", "outcome": "COUNTERED"}]
        row["ai_stagnant_decisions"] = 12
        row["ai_current_category"] = "LIGHT"
    data["version"] = data["state"]["schema_version"] = 28
    data["state"]["config"]["schema_version"] = 28
    data["notebook"]["version"] = 28
    restored = DuelSession.from_data(data)
    assert not hasattr(restored.enemy, "ai_experience")
    assert "ai_experience" not in restored.to_data()["state"]["fighters"][1]


def test_session_schema_v1_migration_preserves_active_path_and_defaults_memory(tmp_path):
    import json

    session = DuelSession.new(archetype="brute", guide_mode="off")
    session.select(Choice(ActionKind.DASH, direction=(1, 0), tiles=2))
    assert session.confirm()
    data = json.loads(json.dumps(session.to_data()))
    state = data["state"]
    legacy_config = state["config"]
    legacy_config["schema_version"] = 1
    legacy_config.pop("ai_profiles")
    legacy_config.pop("personality")
    legacy_config.pop("injury")
    legacy_config["ronin"].pop("stress_composure", None)
    state.pop("pending_injury_responses", None)
    for fighter_row in state["fighters"]:
        for key in ("ai_experience", "ai_stagnant_decisions", "ai_current_category", "last_recoil_tick"):
            fighter_row.pop(key, None)
        fighter_row["equipment"]["weapon"].pop("damage_type", None)
        for wound in fighter_row["wounds"]:
            wound.pop("depth", None)
            wound.pop("structure", None)
            wound.pop("damage_type", None)
        if fighter_row["personality"]:
            fighter_row["personality"].pop("traits", None)
            fighter_row["personality"].pop("score_bias", None)
    state["schema_version"] = 1
    data["version"] = 1
    data["notebook"]["version"] = 1

    old_path = deepcopy(state["fighters"][0]["action"]["path"])
    old_deadline = state["fighters"][0]["action"]["end_tick"]
    restored = DuelSession.from_data(data)
    assert restored.to_data()["version"] == 30
    assert restored.player.action.path == [(tick, tuple(pos)) for tick, pos in old_path]
    assert restored.player.action.end_tick == old_deadline
    assert not hasattr(restored.enemy, "ai_experience")
    assert 0 <= restored.enemy.personality.stress_composure <= 1

    old_config = deepcopy(load_config())
    old_config["schema_version"] = 1
    old_config.pop("ai_profiles")
    old_config.pop("personality")
    old_config.pop("injury")
    old_config["ronin"].pop("stress_composure", None)
    old_config_path = tmp_path / "duel-config-v1.json"
    old_config_path.write_text(json.dumps(old_config), encoding="utf-8")
    migrated_config = load_config(old_config_path)
    assert migrated_config["schema_version"] == 30
    assert migrated_config["actions"]["DASH"]["per_tile"] == old_config["actions"]["DASH"]["per_tile"]
    assert "base" not in migrated_config["ronin"]
    assert "traits" not in migrated_config["ai_profiles"]["brute"]

    from pirates.game import World
    world = World()
    world.duel_journal = data
    save_path = tmp_path / "legacy-duel-world.json"
    world.save(save_path)
    loaded = World.load(save_path)
    resumed = DuelSession.from_data(loaded.duel_journal)
    assert loaded.duel_journal["version"] == 30
    restored.run_to_player()
    resumed.run_to_player()
    assert resumed.state.to_data() == restored.state.to_data()
