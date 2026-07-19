"""Tests for src/runtime/ten_squared_fsm.py (SPEC.md §3.9; AUD-C7 Layer-1 budget)."""

import pytest

from ten_squared_fsm import TenSquaredFSM, benchmark


def expected_next(state: str, event: str) -> str:
    """Independent re-derivation of the SPEC §3.9 reference rule."""
    row, col = int(state[1]), int(state[2])
    e = int(event[1])
    return f"S{(row + e) % 10}{(col * 3 + e + row) % 10}"


class TestStateSpace:
    def test_hundred_states(self):
        assert len(TenSquaredFSM.STATES) == 100
        assert len(set(TenSquaredFSM.STATES)) == 100
        assert TenSquaredFSM.STATES[0] == "S00"
        assert TenSquaredFSM.STATES[99] == "S99"
        for name in TenSquaredFSM.STATES:
            assert len(name) == 3 and name[0] == "S" and name[1:].isdigit()

    def test_events(self):
        assert TenSquaredFSM.EVENTS == tuple(f"E{e}" for e in range(10))

    def test_default_start_and_state_property(self):
        fsm = TenSquaredFSM()
        assert fsm.state == "S00"
        assert TenSquaredFSM("S57").state == "S57"

    def test_invalid_start_rejected(self):
        for bad in ("S0", "S100", "s00", "XX", "", None):
            with pytest.raises(ValueError, match="unknown start state"):
                TenSquaredFSM(bad)


class TestTransitionRule:
    @pytest.mark.parametrize(
        ("start", "event", "expected"),
        [
            ("S00", "E0", "S00"),   # identity
            ("S00", "E1", "S11"),   # r'=1, c'=0*3+1+0
            ("S00", "E5", "S55"),   # r'=5, c'=5
            ("S11", "E2", "S36"),   # r'=3, c'=1*3+2+1=6
            ("S99", "E9", "S85"),   # r'=(9+9)%10=8, c'=(27+9+9)%10=5
            ("S57", "E4", "S90"),   # r'=9, c'=(21+4+5)%10=0
            ("S34", "E7", "S02"),   # r'=(3+7)%10=0, c'=(12+7+3)%10=2
            ("S90", "E3", "S22"),   # r'=(9+3)%10=2, c'=(0+3+9)%10=2
        ],
    )
    def test_hand_computed_cases(self, start, event, expected):
        assert TenSquaredFSM(start).transition(event) == expected

    def test_full_table_deterministic(self):
        """All 100 states x 10 events match the reference rule, twice (determinism)."""
        for state in TenSquaredFSM.STATES:
            for event in TenSquaredFSM.EVENTS:
                assert TenSquaredFSM(state).transition(event) == expected_next(state, event)
                assert TenSquaredFSM(state).transition(event) == expected_next(state, event)

    def test_chained_transitions_update_state(self):
        fsm = TenSquaredFSM("S00")
        assert fsm.transition("E1") == "S11"
        assert fsm.state == "S11"
        assert fsm.transition("E2") == "S36"
        assert fsm.state == "S36"

    def test_returns_precomputed_state_object_no_allocation(self):
        fsm = TenSquaredFSM("S00")
        result = fsm.transition("E1")
        assert result is TenSquaredFSM.STATES[11]  # identical object, not a fresh str

    def test_invalid_events_rejected(self):
        fsm = TenSquaredFSM()
        for bad in ("E10", "E", "", "X1", "Ee", "e1", "E-", "EE"):
            with pytest.raises(ValueError, match="unknown event"):
                fsm.transition(bad)
        assert fsm.state == "S00"  # failed transitions never move the FSM


class TestBenchmark:
    def test_benchmark_shape(self):
        stats = benchmark(1_000)
        assert set(stats) == {"p50_us", "p95_us", "p99_us", "p999_us", "max_us"}
        assert all(v >= 0.0 for v in stats.values())
        assert stats["p50_us"] <= stats["p99_us"] <= stats["max_us"]
        with pytest.raises(ValueError):
            benchmark(0)

    def test_fsm_latency_budget(self):
        """Layer-1 budget CI gate (AUD-C7): p99 < 1000 µs per transition."""
        stats = benchmark(100_000)
        assert stats["p99_us"] < 1000.0, f"Layer-1 budget exceeded: {stats}"
