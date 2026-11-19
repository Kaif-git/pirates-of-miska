"""P2 profile, emotion, seeded-choice and resolved-adaptation acceptance gates."""
from copy import deepcopy

import pytest

from pirates.duel_system.ai import (
    adaptation_penalty, choose_action, decision_control, record_ai_decision, score_actions,
)
from pirates.duel_system.config import load_config, validate_config
from pirates.duel_system.engine import Choice, commit_action, legality
from pirates.duel_system.model import ActionKind, EventType, Personality, Zone
from pirates.duel_system.session import DuelSession
from test_duel_system_scheduler import arena


def neutral_ai_state(seed=1):
    state = arena(rng_seed=seed)
    fighter = state.fighters[1]
    fighter.personality = Personality(**deepcopy(state.config["ronin"]))
    fighter.personality.base = dict.fromkeys(fighter.personality.base, 0)
    fighter.personality.weights = dict.fromkeys(fighter.personality.weights, 0)
    fighter.personality.traits = dict.fromkeys(fighter.personality.traits, 0.5)
    return state, fighter


def test_personality_traits_are_bounded_and_profiles_are_authored_and_selectable():
    config = DuelSession.new(guide_mode="off").state.config
    assert set(config["ai_profiles"]) == {
        "brute", "aggressor", "veteran", "counterfighter", "recruit", "bully",
    }
    assert DuelSession.new(seed=1, guide_mode="off").enemy.personality.archetype == "brute"
    assert DuelSession.new(seed=2, guide_mode="off").enemy.personality.archetype != "brute"
    assert DuelSession.new(archetype="recruit", guide_mode="off").enemy.personality.archetype == "recruit"
    personality = DuelSession.new(archetype="aggressor", guide_mode="off").enemy.personality
    assert all(0 <= value <= 1 for value in personality.traits.values())
    invalid = dict(personality.traits, aggression=1.01)
    with pytest.raises(ValueError, match="trait"):
        Personality(**{**personality.__dict__, "traits": invalid})
    invalid_config = load_config()
    invalid_config["ai_profiles"]["brute"]["traits"]["discipline"] = 1.1
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        validate_config(invalid_config)


def test_traits_condition_scores_and_derived_composure_tracks_fear_pain_and_nerves():
    state, fighter = neutral_ai_state()
    fighter.personality.traits = {"aggression": 1, "discipline": 0.5, "confidence": 0.5,
                                  "patience": 0.5, "adaptability": 0.5}
    aggressive = {row.category: row for row in score_actions(state, fighter.id)}
    state.config["personality"]["trait_score"] = 0
    no_trait_effect = {row.category: row for row in score_actions(state, fighter.id)}
    assert aggressive["LIGHT"].score > no_trait_effect["LIGHT"].score
    assert any(name.startswith("P2_") for name, _ in aggressive["LIGHT"].reasons)

    calm = decision_control(state, fighter.id)
    fighter.morale, fighter.pain, fighter.nervousness = 0, 80, 1
    pressured = decision_control(state, fighter.id)
    assert 0 <= pressured < calm <= 1


def test_authored_profiles_score_differently_at_the_same_emotion_and_geometry():
    aggressor = DuelSession.new(archetype="aggressor", guide_mode="off")
    recruit = DuelSession.new(archetype="recruit", guide_mode="off")
    for session in (aggressor, recruit):
        session.player.pos, session.enemy.pos = (4, 3), (5, 3)
        session.enemy.morale = 70
    aggressive_scores = {row.category: row.score for row in score_actions(aggressor.state, "ronin")}
    recruit_scores = {row.category: row.score for row in score_actions(recruit.state, "ronin")}
    assert aggressor.enemy.anger == recruit.enemy.anger
    assert aggressor.enemy.morale == recruit.enemy.morale
    assert aggressive_scores["LIGHT"] > recruit_scores["LIGHT"]
    assert recruit_scores["GUARD"] > aggressive_scores["GUARD"]


def test_panic_score_increases_when_the_same_personality_is_under_emotional_pressure():
    state, fighter = neutral_ai_state()
    fighter.personality.composure = 0.5
    calm = {row.category: dict(row.reasons) for row in score_actions(state, fighter.id)}
    fighter.morale, fighter.pain, fighter.nervousness = 0, 70, 0.8
    pressured = {row.category: dict(row.reasons) for row in score_actions(state, fighter.id)}
    assert "P2_PANIC" not in calm["DASH_OUT"]
    assert pressured["DASH_OUT"]["P2_PANIC"] > 0


def test_seeded_variation_is_repeatable_plausible_and_does_not_consume_combat_rng():
    chosen = set()
    for seed in range(24):
        state, fighter = neutral_ai_state(seed)
        before = state.to_data()
        first = choose_action(state, fighter.id)
        assert choose_action(state, fighter.id) == first
        assert state.to_data() == before
        chosen.add(first.category)
    assert len(chosen) >= 2


def test_paired_opening_distributions_are_legal_repeatable_and_profile_distinct():
    calm = {name: [] for name in ("brute", "aggressor", "veteran", "counterfighter", "recruit", "bully")}
    pressured = {name: [] for name in calm}
    for seed in range(1, 41):
        for name in calm:
            for target in (calm, pressured):
                session = DuelSession.new(seed=seed, archetype=name, guide_mode="off")
                enemy = session.enemy
                enemy.anger, enemy.morale, enemy.pain, enemy.nervousness, enemy.fatigue = (
                    0, 70, 0, 0, 0) if target is calm else (0, 10, 60, 0.6, 0)
                decision = choose_action(session.state, enemy.id)
                assert not legality(session.state, enemy.id, decision.choice)
                target[name].append(decision.category)

    assert all(set(pressured[name]) == {"DASH_OUT"} for name in pressured)
    assert calm["veteran"].count("WALK_IN") >= 36
    assert calm["counterfighter"].count("GUARD") >= 30
    assert len(set(calm["recruit"])) >= 2
    assert calm["brute"].count("DASH_LIGHT") >= 36
    assert calm["aggressor"].count("DASH_LIGHT") >= 36


def test_only_resolved_countered_outcomes_adapt_and_success_decays_penalty():
    session = DuelSession.new(archetype="brute", guide_mode="off")
    fighter = session.enemy
    session.player.pos, fighter.pos = (7, 3), (8, 3)
    fighter.ai_current_category = "LIGHT"
    assert adaptation_penalty(fighter, "LIGHT", session.state.config) == 0
    baseline = next(row.score for row in score_actions(session.state, fighter.id) if row.category == "LIGHT")

    # A queued commitment is not evidence; only resolved public outcomes teach.
    commit_action(session.state, fighter.id, Choice(ActionKind.LIGHT, Zone.TORSO))
    assert fighter.ai_experience == []

    for _ in range(2):
        session.state.emit(EventType.PARRY, "player", fighter.id, Zone.TORSO)
        session._record_ai_outcomes()
    fighter.action = None
    first_penalty = adaptation_penalty(fighter, "LIGHT", session.state.config)
    assert first_penalty > 0
    countered_score = next(row.score for row in score_actions(session.state, fighter.id) if row.category == "LIGHT")
    assert countered_score < baseline

    session.state.emit(EventType.HIT, fighter.id, "player", Zone.TORSO)
    session._record_ai_outcomes()
    assert adaptation_penalty(fighter, "LIGHT", session.state.config) < first_penalty


def test_adaptation_ring_is_bounded_and_anti_deadlock_only_scores_legal_choices():
    session = DuelSession.new(archetype="brute", guide_mode="off")
    fighter = session.enemy
    fighter.ai_current_category = "LIGHT"
    for _ in range(20):
        session.state.emit(EventType.WHIFF, fighter.id, "player")
        session._record_ai_outcomes()
    assert len(fighter.ai_experience) == 12

    fighter.ai_stagnant_decisions = 3
    rows = score_actions(session.state, fighter.id)
    assert rows
    assert all(row.category in (set(session.state.config["ai"]["tie_order"])
                                | set(session.state.config["tactics"]["ai_scores"])) for row in rows)
    assert any(name == "P2_ANTI_DEADLOCK" for row in rows for name, _ in row.reasons)
    fighter.ai_stagnant_decisions = 0
    legal_before = {row.category for row in score_actions(session.state, fighter.id)}
    fighter.ai_stagnant_decisions = 3
    legal_after = {row.category for row in score_actions(session.state, fighter.id)}
    assert legal_after == legal_before
    record_ai_decision(fighter, "DASH_IN")
    assert fighter.ai_stagnant_decisions == 0


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
    legacy_config["ronin"].pop("traits")
    legacy_config["ronin"].pop("score_bias", None)
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
    assert restored.to_data()["version"] == 18
    assert restored.player.action.path == [(tick, tuple(pos)) for tick, pos in old_path]
    assert restored.player.action.end_tick == old_deadline
    assert restored.enemy.ai_experience == []
    assert set(restored.enemy.personality.traits) == {
        "aggression", "discipline", "confidence", "patience", "adaptability",
    }

    old_config = deepcopy(load_config())
    old_config["schema_version"] = 1
    old_config.pop("ai_profiles")
    old_config.pop("personality")
    old_config.pop("injury")
    old_config["ronin"].pop("traits")
    old_config["ronin"].pop("score_bias")
    old_config_path = tmp_path / "duel-config-v1.json"
    old_config_path.write_text(json.dumps(old_config), encoding="utf-8")
    migrated_config = load_config(old_config_path)
    assert migrated_config["schema_version"] == 18
    assert migrated_config["actions"]["DASH"]["per_tile"] == old_config["actions"]["DASH"]["per_tile"]
    assert migrated_config["ronin"]["base"] == old_config["ronin"]["base"]
    assert migrated_config["ronin"]["traits"] == migrated_config["ai_profiles"]["brute"]["traits"]

    from pirates.game import World
    world = World()
    world.duel_journal = data
    save_path = tmp_path / "legacy-duel-world.json"
    world.save(save_path)
    loaded = World.load(save_path)
    resumed = DuelSession.from_data(loaded.duel_journal)
    assert loaded.duel_journal["version"] == 18
    restored.run_to_player()
    resumed.run_to_player()
    assert resumed.state.to_data() == restored.state.to_data()
