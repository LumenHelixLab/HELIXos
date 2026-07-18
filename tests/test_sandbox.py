"""Resource containment in the disposable sandbox (reviewer findings 4, 6)."""

from __future__ import annotations

from helix_gate import testkit as tk
from helix_gate.errors import GateOutcome, ReasonCode
from conftest import submit


def test_fuel_exhaustion_times_out(harness):
    wasm = tk.publish(harness.store, "spin")
    env = harness.issuer.envelope(wasm=wasm, limits=tk.default_limits(fuel=500_000))
    res = submit(harness, env)
    assert res.outcome is GateOutcome.TIMED_OUT
    assert res.reason is ReasonCode.EXEC_TIMED_OUT


def test_wall_clock_deadline_times_out(permissive_harness):
    h = permissive_harness
    wasm = tk.publish(h.store, "spin")
    # plenty of fuel, but a short wall clock -> the epoch watchdog fires first.
    env = h.issuer.envelope(
        wasm=wasm, limits=tk.default_limits(fuel=10 ** 12, wall_ms=200))
    assert submit(h, env).outcome is GateOutcome.TIMED_OUT


def test_guest_trap_is_contained(harness):
    wasm = tk.publish(harness.store, "oob")  # out-of-bounds memory load
    res = submit(harness, harness.issuer.envelope(wasm=wasm))
    assert res.outcome is GateOutcome.TRAPPED
    assert res.reason is ReasonCode.EXEC_TRAPPED


def test_output_overflow_contained(harness):
    wasm = tk.publish(harness.store, "big_output")  # advertises 1000 output bytes
    env = harness.issuer.envelope(wasm=wasm, limits=tk.default_limits(output_bytes=8))
    res = submit(harness, env)
    assert res.reason is ReasonCode.EXEC_OUTPUT_OVERFLOW
    assert res.output is None


def test_undeclared_import_denied_at_runtime(harness):
    """Defense in depth: a module needing an undeclared import cannot instantiate."""
    wasm = tk.publish(harness.store, "needs_import")
    res = submit(harness, harness.issuer.envelope(wasm=wasm))
    # No host authority was granted, so instantiation fails inside the sandbox.
    assert res.outcome is GateOutcome.TRAPPED
