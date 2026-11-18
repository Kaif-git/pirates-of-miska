"""Deterministic scheduler/perception/render scaling sample for P7 actor counts."""
from pathlib import Path
import argparse
import json
import statistics
import sys
import time
import tracemalloc
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from duel_group_fixture import SUPPORTED_ACTOR_COUNTS, make_group_session
from pirates.duel_system.engine import Choice
from pirates.duel_system.model import ActionKind, EventType


def run_scripted(actor_count, seed, rounds):
    from pirates.duel_system import session as session_module
    session = make_group_session(actor_count, seed, guide_mode="off")
    original = session_module.choose_action
    session_module.choose_action = lambda snapshot, fighter_id: SimpleNamespace(
        category="WAIT", choice=Choice(ActionKind.WAIT))
    timings = []
    try:
        for _ in range(rounds):
            if session.state.end_reason:
                raise AssertionError("WAIT-only deterministic workload unexpectedly ended")
            start = time.perf_counter()
            assert session.resolve_round(Choice(ActionKind.WAIT))
            timings.append((time.perf_counter()-start)*1000)
    finally:
        session_module.choose_action = original
    return session, timings


def run_live_ai(actor_count, seed):
    session = make_group_session(actor_count, seed, guide_mode="off")
    started = time.perf_counter()
    assert session.resolve_round(Choice(ActionKind.WAIT))
    elapsed = (time.perf_counter()-started)*1000
    starts = [event for event in session.state.event_log if event.type == EventType.ACTION_START]
    decisions = [event for event in session.state.event_log if event.type == EventType.DECISION]
    action_counts = {fighter.id: sum(event.actor == fighter.id for event in starts)
                     for fighter in session.state.fighters}
    decision_counts = {fighter.id: sum(event.actor == fighter.id for event in decisions)
                       for fighter in session.state.fighters}
    assert all(count <= 1 for count in action_counts.values()), action_counts
    assert all(count <= 1 for count in decision_counts.values()), decision_counts
    return session, elapsed, action_counts, decision_counts


def measure(actor_count, seed, rounds):
    tracemalloc.start()
    session, _ = run_scripted(actor_count, seed, rounds)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    timing_rows = []
    for _ in range(3):
        replay, timings = run_scripted(actor_count, seed, rounds)
        assert replay.state.to_data() == session.state.to_data()
        timing_rows.extend(timings)
    starts = [event for event in session.state.event_log if event.type == EventType.ACTION_START]
    action_counts = {fighter.id: sum(event.actor == fighter.id for event in starts)
                     for fighter in session.state.fighters}
    assert all(count == rounds for count in action_counts.values()), action_counts
    before = session.state.to_data()
    render_start = time.perf_counter()
    for _ in range(30):
        session.render_model(timeline=False, include_advice=False)
    render_ms = (time.perf_counter()-render_start)*1000/30
    assert before == session.state.to_data(), "Repeated group rendering mutated simulation"
    first_seed = seed + 10000
    tracemalloc.start()
    live_reference_session, _, live_actions, live_decisions = run_live_ai(actor_count, first_seed)
    _, live_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    live_reference = live_reference_session.state.to_data()
    live_ticks, live_events = live_reference_session.state.tick, len(live_reference_session.state.event_log)
    live_traces = []
    for trial in range(3):
        trial_seed = first_seed + trial
        live, elapsed, _, _ = run_live_ai(actor_count, trial_seed)
        replay, _, _, _ = run_live_ai(actor_count, trial_seed)
        assert replay.state.to_data() == live.state.to_data()
        if trial == 0:
            assert live.state.to_data() == live_reference
        live_traces.append(elapsed)
    return {"actors": actor_count,
            "fixed_wait": {"rounds": rounds, "ticks": session.state.tick,
                           "round_ms_median": round(statistics.median(timing_rows), 3),
                           "round_ms_max": round(max(timing_rows), 3),
                           "render_model_ms_mean": round(render_ms, 3),
                           "peak_traced_bytes": peak, "retained_traced_bytes": current,
                           "event_count": len(session.state.event_log),
                           "action_starts": len(starts), "per_actor_action_counts": action_counts},
            "live_ai_one_exchange": {"samples": len(live_traces),
                                     "round_ms_median": round(statistics.median(live_traces), 3),
                                     "round_ms_max": round(max(live_traces), 3),
                                     "peak_traced_bytes": live_peak,
                                     "ticks_first_seed": live_ticks,
                                     "event_count_first_seed": live_events,
                                     "action_counts_first_seed": live_actions,
                                     "decision_counts_first_seed": live_decisions}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--seed", type=int, default=5200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("--rounds must be positive")
    rows = []
    for count in SUPPORTED_ACTOR_COUNTS:
        rows.append(measure(count, args.seed+count, args.rounds))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"workloads": {
                                           "fixed_wait": "all participants commit WAIT; AI choices fixed",
                                           "live_ai_one_exchange": "player commits WAIT; normal deterministic group AI chooses"},
                                       "seed": args.seed, "results": rows}, indent=2), encoding="utf-8")
    for row in rows:
        fixed, live = row["fixed_wait"], row["live_ai_one_exchange"]
        print(f"{row['actors']:>2} actors: fixed-WAIT median {fixed['round_ms_median']}ms, "
              f"live-AI median {live['round_ms_median']}ms (max {live['round_ms_max']}ms); "
              f"render-model {fixed['render_model_ms_mean']}ms; live-AI peak {live['peak_traced_bytes']} bytes")


if __name__ == "__main__":
    main()
