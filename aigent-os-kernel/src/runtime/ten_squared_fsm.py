"""HELIXos TEN-SQUARED FSM — the deterministic 100-state runtime core.

TEN-SQUARED definition (SPEC.md §3.9, resolves "undefined term" AUD-§8): a
finite-state machine over a 10x10 grid — 100 states ``S00``..``S99`` where
state ``S(r, c)`` is row ``r``, column ``c`` — driven by 10 events
``E0``..``E9`` under the reference transition rule::

    S(r, c) --Ee--> S(r', c')   with  r' = (r + e) % 10
                                      c' = (c * 3 + e + r) % 10

Cites AUD-C7 (HELIXos_Handoff_Audit.md): the <1 ms requirement is split per
layer, and this module carries the **Layer-1 budget: in-process FSM
transition < 1 ms p99**.  To hold that budget the transition table is
materialized at init as nested tuples (no dict lookups on the hot path),
the FSM tracks only an integer index into a precomputed state-name tuple,
and :meth:`TenSquaredFSM.transition` allocates nothing per call — it returns
the existing interned state-name object.  :func:`benchmark` measures with
``time.perf_counter_ns`` while GC is disabled and reports
p50/p95/p99/p99.9/max per transition; the CI gate lives in
``tests/test_fsm.py::test_fsm_latency_budget``.
"""

from __future__ import annotations

import gc
import logging
import time

log = logging.getLogger("helix.runtime.ten_squared")

_GRID = 10
_EVENTS = 10

# Materialized once at import; bound per instance at __init__ (SPEC §3.9).
# Nested tuples of ints — constant memory, cache-friendly, zero dict lookups.
_STATES: tuple[str, ...] = tuple(
    f"S{row}{col}" for row in range(_GRID) for col in range(_GRID)
)
_TRANSITION_TABLE: tuple[tuple[int, ...], ...] = tuple(
    tuple(
        ((row + event) % _GRID) * _GRID + ((col * 3 + event + row) % _GRID)
        for event in range(_EVENTS)
    )
    for row in range(_GRID)
    for col in range(_GRID)
)
_STATE_INDEX: dict[str, int] = {name: i for i, name in enumerate(_STATES)}  # init-time only


class TenSquaredFSM:
    """100-state TEN-SQUARED FSM.  Hot path: two tuple indexings, one int store."""

    STATES: tuple[str, ...] = _STATES  # 100 state names, S00..S99
    EVENTS: tuple[str, ...] = tuple(f"E{e}" for e in range(_EVENTS))

    def __init__(self, start: str = "S00") -> None:
        idx = _STATE_INDEX.get(start)
        if idx is None:
            raise ValueError(f"unknown start state: {start!r}")
        self._idx = idx
        self._table = _TRANSITION_TABLE  # materialized nested tuples (SPEC §3.9)

    def transition(self, event: str) -> str:
        """Apply ``event`` ("E0".."E9"); return the new state name.

        Allocates nothing on the success path: the event is decoded by
        ordinal arithmetic and the state name is an existing tuple element.
        """
        if len(event) != 2 or event[0] != "E":
            raise ValueError(f"unknown event: {event!r}")
        event_idx = ord(event[1]) - 0x30
        if event_idx < 0 or event_idx >= _EVENTS:
            raise ValueError(f"unknown event: {event!r}")
        self._idx = self._table[self._idx][event_idx]
        return _STATES[self._idx]

    @property
    def state(self) -> str:
        """Current state name (existing object, no allocation)."""
        return _STATES[self._idx]


def benchmark(iterations: int = 100_000) -> dict[str, float]:
    """Measure per-transition latency; return percentiles in microseconds.

    Pre-creates the event list, warms up, collects with GC disabled, and
    reports ``{"p50_us", "p95_us", "p99_us", "p999_us", "max_us"}``.
    Layer-1 budget (AUD-C7): ``p99_us < 1000``.
    """
    if iterations < 1:
        raise ValueError("iterations must be >= 1")
    fsm = TenSquaredFSM()
    events = [f"E{i % _EVENTS}" for i in range(iterations)]
    for i in range(min(1_000, iterations)):  # warm-up outside measurement
        fsm.transition(events[i])
    gc.collect()
    gc.disable()
    try:
        samples = [0] * iterations
        for i in range(iterations):
            t0 = time.perf_counter_ns()
            fsm.transition(events[i])
            samples[i] = time.perf_counter_ns() - t0
    finally:
        gc.enable()
    samples.sort()
    last = iterations - 1

    def pct(q: float) -> float:
        return samples[int(q * last)] / 1_000.0  # ns -> µs

    stats = {
        "p50_us": pct(0.50),
        "p95_us": pct(0.95),
        "p99_us": pct(0.99),
        "p999_us": pct(0.999),
        "max_us": samples[last] / 1_000.0,
    }
    log.info("ten_squared.benchmark iterations=%d %s", iterations, stats)
    return stats
