"""M7: counted public habits, private-break redaction and durable ownership."""
from dataclasses import asdict, replace
import json

import pytest

from pirates.duel_system.ai import FullKey, PublicKey
from pirates.duel_system.config import load_config
from pirates.duel_system.notebook import Notebook, PatternState


KEY = PublicKey("ADJ", "ANGRY", "HIGH", "LIGHT", "WHIFFED")
FULL = FullKey(KEY, "OK", "BOLD", "LOW")


def record(book, action="HEAVY", key=KEY, full=FULL, **kwargs):
    book.record("brute", "ronin", key, action, tick=10, full_key=full, **kwargs)


def test_unknown_tentative_tendency_habit_and_public_prediction():
    book = Notebook(load_config())
    assert book.entry("brute", KEY).state == PatternState.UNKNOWN
    record(book)
    assert book.entry("brute", KEY).state == PatternState.TENTATIVE
    record(book)
    assert book.entry("brute", KEY).state == PatternState.TENTATIVE
    record(book)
    assert book.entry("brute", KEY).state == PatternState.TENDENCY
    record(book)
    record(book)
    public = book.entry("brute", KEY)
    assert public.state == PatternState.HABIT
    assert public.top_action == "HEAVY" and public.total == 5
    assert public.ghost and public.timeline_prediction


def test_mixed_state_requires_sufficient_samples_and_tendency_share():
    book = Notebook(load_config())
    for action in ["LIGHT", "HEAVY", "WAIT", "GUARD"]:
        record(book, action)
    assert book.entry("brute", KEY).state == PatternState.MIXED
    assert not book.entry("brute", KEY).ghost


def test_habit_break_compares_recent_fullkeys_but_redacts_invisible_dimension():
    book = Notebook(load_config())
    for _ in range(5):
        record(book)
    record(book, "GUARD", full=replace(FULL, blood_band="CRITICAL"), visible_dimensions=())
    entry = book.entry("brute", KEY)
    assert entry.state == PatternState.MIXED
    assert entry.habit_break_hint == "Something else changed."
    assert "CRITICAL" not in str(asdict(entry))
    assert "full_key" not in asdict(entry)
    assert "hidden_diffs" not in asdict(entry)


def test_visible_habit_break_can_name_observable_wounds():
    book = Notebook(load_config())
    for _ in range(5):
        record(book)
    record(book, "GUARD", full=replace(FULL, blood_band="CRITICAL"),
           visible_dimensions=("blood_band",))
    assert book.entry("brute", KEY).habit_break_hint == "Something else changed: he is badly hurt."


def test_unobserved_decisions_do_not_learn_or_leak():
    book = Notebook(load_config())
    before = book.to_data()
    record(book, observed=False)
    assert book.to_data() == before


def test_named_npc_entries_are_separate_while_archetype_learning_transfers():
    book = Notebook(load_config())
    for _ in range(5):
        record(book)
    assert book.entry("brute", KEY, npc_id="ronin").state == PatternState.HABIT
    assert book.entry("brute", KEY, npc_id="other-ronin").state == PatternState.UNKNOWN
    assert book.entry("brute", KEY).state == PatternState.HABIT


def test_trait_inference_requires_six_established_angry_entries_and_transfers():
    book = Notebook(load_config())
    for index in range(6):
        key = replace(KEY, opp_last_cat=f"EXAMPLE_{index}")
        full = replace(FULL, public=key)
        for _ in range(3):
            record(book, key=key, full=full)
    assert "Aggressive: rarely guards when angry" in book.traits("brute")
    assert book.traits("unseen") == ()


def test_exact_stats_only_unlocked_after_three_observed_exchanges():
    book = Notebook(load_config())
    assert not book.exact_stats_known("ronin")
    book.record_exchange("ronin")
    book.record_exchange("ronin")
    assert not book.exact_stats_known("ronin")
    book.record_exchange("ronin", observed=False)
    assert not book.exact_stats_known("ronin")
    book.record_exchange("ronin")
    assert book.exact_stats_known("ronin")


def test_notebook_json_roundtrip_and_immutable_public_snapshot():
    book = Notebook(load_config())
    for _ in range(7):
        record(book)
    book.record_exchange("ronin")
    data = json.loads(json.dumps(book.to_data()))
    restored = Notebook.from_data(data, load_config())
    assert restored.to_data() == data
    assert restored.entry("brute", KEY) == book.entry("brute", KEY)
    public = restored.public_view()
    with pytest.raises((AttributeError, TypeError)):
        public.entry("brute", KEY, npc_id="ronin").total = 99
    record(restored, "LIGHT")
    assert public.entry("brute", KEY, npc_id="ronin").total == 7
    assert public.entry("brute", KEY).total == 0  # archetype priors are never public
    assert book.entry("brute", KEY).total == 7
    data["version"] += 1
    with pytest.raises(ValueError, match="version"):
        Notebook.from_data(data, load_config())


def test_v1_notebook_migrates_to_v7_without_losing_observed_counts():
    book = Notebook(load_config())
    for _ in range(3):
        record(book)
    data = book.to_data()
    data["version"] = 1
    restored = Notebook.from_data(data, load_config())
    assert restored.to_data()["version"] == 18
    assert restored.entry("brute", KEY).counts == book.entry("brute", KEY).counts


def test_entry_retains_only_last_five_private_keys_and_validates_public_full_join():
    book = Notebook(load_config())
    for _ in range(20):
        record(book)
    data = book.to_data()
    assert len(data["archetypes"]["brute"][0]["full_keys"]) == 5
    with pytest.raises(ValueError, match="public"):
        record(book, full=replace(FULL, public=replace(KEY, gap_band="FAR")))
