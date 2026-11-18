"""M8 forward-simulation and hidden-information boundary tests."""
from copy import deepcopy
from dataclasses import replace

import pytest

from pirates.duel_system.ai import FullKey, PublicKey
from pirates.duel_system.engine import Choice, commit_action
from pirates.duel_system.guide import (
    Confidence, evaluate, guide, make_guide_view, rollout_scores,
)
from pirates.duel_system.model import ActionKind, Personality, Zone
from pirates.duel_system.notebook import Notebook
from test_duel_system_scheduler import arena


def scene():
    state = arena(walkable={(x, y) for x in range(-1, 4) for y in range(-1, 2)})
    state.fighters[1].personality = Personality(**deepcopy(state.config["ronin"]))
    return state, Notebook(state.config)


def learn(book, view, action, samples):
    full = FullKey(view.public_key, "OK", "BOLD", "LOW")
    for index in range(samples):
        book.record("brute", view.enemy_id, view.public_key, action,
                    tick=index, full_key=full)


def test_empty_notebook_is_unknown_and_forward_simulation_is_read_only():
    state, book = scene()
    before, memory = state.to_data(), book.to_data()
    view = make_guide_view(state, "player", book.public_view())
    advice = guide(view, book.public_view())
    assert advice.confidence == Confidence.UNKNOWN
    assert "Unknown. Keep distance and watch." in advice.why
    assert advice.action is not None
    assert advice.simulations > 1
    assert state.to_data() == before and book.to_data() == memory


def test_visible_commitment_is_certain_even_with_empty_notebook():
    state, book = scene()
    commit_action(state, "ronin", Choice(ActionKind.HEAVY, Zone.TORSO))
    advice = guide(make_guide_view(state, "player", book.public_view()), book.public_view())
    assert advice.confidence == Confidence.CERTAIN
    assert "visible" in " ".join(advice.why).lower()
    evaluated = rollout_scores(make_guide_view(state, "player", book.public_view()), book.public_view())
    assert any("LIGHT_INTERRUPTS_HEAVY" in outcome.cause_tags for candidate in evaluated
               if candidate.action.kind == ActionKind.LIGHT for outcome in candidate.outcomes)
    assert advice.score == max(candidate.score for candidate in evaluated)


@pytest.mark.parametrize("samples,confidence", [(1, Confidence.GUESSING),
                                               (3, Confidence.LIKELY), (5, Confidence.LIKELY)])
def test_prediction_confidence_comes_from_notebook_not_ai_weights(samples, confidence):
    state, book = scene()
    view = make_guide_view(state, "player", book.public_view())
    learn(book, view, "HEAVY", samples)
    advice = guide(view, book.public_view())
    assert advice.confidence == confidence
    assert advice.predicted == "HEAVY"


def test_ai_weights_hidden_emotions_and_rng_seed_never_change_unlearned_advice():
    state, book = scene()
    original = guide(make_guide_view(state, "player", book.public_view()), book.public_view())
    changed = state.clone()
    enemy = changed.fighters[1]
    enemy.personality.weights = {key: 999 for key in enemy.personality.weights}
    enemy.personality.base = {key: -999 for key in enemy.personality.base}
    enemy.personality.reaction_delay = 500
    enemy.pain, enemy.morale = 90, 2  # private emotional dimensions
    enemy.anger = 24  # stays in the same public anger bucket
    enemy.personality.traits = dict.fromkeys(enemy.personality.traits, 0)
    enemy.personality.score_bias = dict.fromkeys(enemy.personality.score_bias, 999)
    enemy.strength, enemy.speed, enemy.accuracy = 20, 20, 20
    changed.config["ronin"]["weights"] = {key: -999 for key in changed.config["ronin"]["weights"]}
    changed.rng_seed = 54321
    for _ in range(10):
        changed.uniform(-1, 1)
    result = guide(make_guide_view(changed, "player", book.public_view()), book.public_view())
    assert result == original


def test_uncommitted_ai_category_and_adaptation_memory_do_not_enter_guide_projection():
    state, book = scene()
    original = guide(make_guide_view(state, "player", book.public_view()), book.public_view())
    changed = state.clone()
    enemy = changed.fighters[1]
    enemy.ai_current_category = "HEAVY"
    enemy.ai_experience = [{"category": "LIGHT", "outcome": "COUNTERED"}] * 12
    enemy.ai_stagnant_decisions = 99
    result = guide(make_guide_view(changed, "player", book.public_view()), book.public_view())
    assert result == original


def test_archetype_history_is_not_a_substitute_for_named_npc_observations():
    state, book = scene()
    view = make_guide_view(state, "player", book.public_view())
    learn(book, view, "HEAVY", 8)
    book.named.clear()
    advice = guide(make_guide_view(state, "player", book.public_view()), book.public_view())
    assert advice.confidence == Confidence.UNKNOWN
    assert advice.predicted is None


def test_guide_rejects_live_state_and_private_notebook_objects():
    state, book = scene()
    view = make_guide_view(state, "player", book.public_view())
    with pytest.raises(TypeError):
        guide(state, book.public_view())
    with pytest.raises(TypeError):
        guide(view, book)
    with pytest.raises(TypeError):
        make_guide_view(state, "player", book)


def test_projection_never_accesses_personality_weights_or_fullkeys():
    class NoSecrets:
        archetype = "brute"
        def __getattr__(self, name):
            raise AssertionError(f"Hidden personality access: {name}")
    state, book = scene()
    state.fighters[1].personality = NoSecrets()
    view = make_guide_view(state, "player", book.public_view())
    assert not hasattr(view, "archetype")
    assert view.simulation.fighters[1].personality is None
    assert not hasattr(book.public_view().entry("brute", view.public_key), "full_keys")


def test_unknown_safe_play_uses_worst_case_not_best_enemy_response():
    state, book = scene()
    view = make_guide_view(state, "player", book.public_view())
    scores = rollout_scores(view, book.public_view())
    assert len(scores) > 1
    for candidate in scores:
        assert candidate.score == min(outcome.score for outcome in candidate.outcomes)
    advice = guide(view, book.public_view())
    assert advice.score == max(candidate.score for candidate in scores)


def test_stat_shortfall_is_disclosed_and_exhausted_actions_are_not_offered():
    state, book = scene()
    player = state.fighters[0]
    player.accuracy = 17
    commit_action(state, "ronin", Choice(ActionKind.HEAVY, Zone.TORSO))
    player.exhausted = True
    player.stamina = 0
    advice = guide(make_guide_view(state, "player", book.public_view()), book.public_view())
    assert advice.action.kind not in {ActionKind.HEAVY, ActionKind.DASH, ActionKind.DASH_LIGHT}
    assert any("20" in risk and "hand" in risk.lower() for risk in advice.risk)


def test_hints_off_and_near_equal_alternatives():
    state, book = scene()
    view = make_guide_view(state, "player", book.public_view())
    full = guide(view, book.public_view())
    hints = guide(view, book.public_view(), mode="hints")
    assert hints.action is None and hints.zone is None and hints.why
    assert guide(view, book.public_view(), mode="off") is None
    for alternative in full.alternatives:
        tolerance = abs(full.score) * state.config["guide"]["alternative_fraction"]
        assert alternative.score >= full.score - tolerance


def test_evaluation_uses_spec_resource_weights():
    state, book = scene()
    before = make_guide_view(state, "player", book.public_view()).simulation
    after = before.clone()
    player, enemy = after.fighters
    enemy.blood -= 10
    enemy.pain += 10
    enemy.morale -= 10
    enemy.fatigue += 10
    player.blood -= 5
    player.fatigue += 5
    player.stamina -= 10
    assert evaluate(before, after, "player") == pytest.approx(10+3+3+3-6-1.5-2)
