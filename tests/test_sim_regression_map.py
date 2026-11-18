import json
from pathlib import Path

import pytest

from sim.core.events import EventLog
from sim.core.world import WorldState
from sim.tools.batch_test import _director_metrics


ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "sim" / "data" / "legacy_baseline.json"
TARGETS_PATH = ROOT / "sim" / "data" / "regression_targets.json"
BASELINE = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
TARGETS = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))


def _pending_contract(finding):
    def contract():
        pytest.fail(
            f"{finding['id']} awaits {finding['milestone']}: {finding['target']}"
        )
    contract.__name__ = finding["test"]
    return pytest.mark.xfail(
        strict=True,
        reason=f"Behavioral contract activates with {finding['milestone']}",
    )(contract)


def test_F13_every_cause_id_resolves_to_a_parent_event():
    log = EventLog({"WORLD_GENERATED"})
    root = log.append(tick=0, event_type="WORLD_GENERATED",
                      causes_tags=["baseline_world"])
    child = log.append(tick=1, event_type="CHARACTER_MOVED",
                       caused_by=[root.id], causes_tags=["planned_action"])
    log.validate_causality()
    assert log.causes(child.id) == (root,)


for _finding in TARGETS["findings"]:
    if _finding["id"] not in {"F1", "F2", "F13"}:
        globals()[_finding["test"]] = _pending_contract(_finding)


def test_F01_liveness_no_gap_over_three_days():
    world = WorldState.create("regression-F1", director_enabled=False)
    world.run_until(365 * 24)
    assert _director_metrics(world, 1)["liveness_pass"]


def test_F02_event_density_is_stationary():
    world = WorldState.create("regression-F2", director_enabled=False)
    world.run_until(365 * 24)
    metrics = _director_metrics(world, 1)
    assert metrics["density_pass"]
    assert not metrics["density_failing_months"]


def test_legacy_baseline_fixture_has_the_captured_one_year_metrics():
    assert BASELINE["schema_version"] == 1
    assert BASELINE["seed"] == "m1-baseline-2026"
    assert BASELINE["days"] == 365
    assert BASELINE["total_society_news"] == sum(BASELINE["society_news_by_kind"].values())
    assert BASELINE["total_society_news"] == 324
    assert BASELINE["retained_society_news"] == 120
    assert BASELINE["max_gap_between_nontrivial_event_days"] == 34
    assert BASELINE["nontrivial_first_two_day_share"] < 0.5
    assert BASELINE["npc_clash_outcomes"] == {"disengagement": 9, "victory": 29}
    assert BASELINE["events_with_structured_caused_by_ids"] == 0
    assert BASELINE["population_ledger_total"] == 72


def test_F01_liveness_no_gap_over_three_days_legacy_failure_is_recorded():
    assert BASELINE["max_gap_between_nontrivial_event_days"] > 3


def test_F03_legacy_encounters_lack_death_capture_and_sinking_outcomes():
    assert set(BASELINE["npc_clash_outcomes"]) == {"victory", "disengagement"}
    assert BASELINE["death_events"] == 0
    assert BASELINE["ship_sinking_events"] == 0


def test_F06_F13_legacy_event_causes_are_not_structured_parent_ids():
    assert BASELINE["events_with_structured_caused_by_ids"] == 0


def test_F14_legacy_generated_world_is_below_required_scale():
    assert BASELINE["organizations"] == 4
    assert BASELINE["sea_nodes"] == 6
    assert BASELINE["notable_people"] == 24
    assert BASELINE["vessels"] == 8


@pytest.mark.parametrize("finding", TARGETS["findings"], ids=lambda row: row["id"])
def test_each_F01_to_F14_has_a_named_behavioral_regression_contract(finding):
    assert finding["id"] in {f"F{index}" for index in range(1, 15)}
    padded_id = f"F{int(finding['id'][1:]):02d}"
    assert finding["test"].startswith(f"test_{padded_id}")
    assert finding["test"] in globals()
    assert finding["milestone"] in {
        "M0", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M2_M4",
    }
    assert finding["target"]
    assert finding["baseline_status"]


def test_regression_contract_matrix_has_no_missing_or_duplicate_findings():
    findings = TARGETS["findings"]
    assert [row["id"] for row in findings] == [f"F{index}" for index in range(1, 15)]
    assert len({row["test"] for row in findings}) == 14
