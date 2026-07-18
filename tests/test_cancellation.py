"""Interruptible + idempotent cancellation (reviewer findings 5, 9)."""

from __future__ import annotations

import json
import threading
import time

from helix_gate import testkit as tk
from helix_gate.errors import GateOutcome, ReasonCode
from conftest import submit


def _run_spin_and_cancel(h):
    wasm = tk.publish(h.store, "spin")
    env = h.issuer.envelope(
        wasm=wasm, limits=tk.default_limits(fuel=10 ** 12, wall_ms=60_000))
    holder: dict = {}

    def worker():
        holder["res"] = h.gate.submit(
            json.dumps(env), owner="krishna",
            on_operation_id=lambda oid: holder.__setitem__("op", oid))

    t = threading.Thread(target=worker)
    t.start()
    while "op" not in holder:
        time.sleep(0.005)
    time.sleep(0.15)  # ensure the guest is well into its loop
    cancel_status = h.gate.cancel(holder["op"])
    t.join(timeout=10)
    assert not t.is_alive(), "submit did not return after cancel"
    return holder, cancel_status


def test_cancel_mid_run(permissive_harness):
    holder, status = _run_spin_and_cancel(permissive_harness)
    assert status["actionable"] is True
    res = holder["res"]
    assert res.outcome is GateOutcome.CANCELLED
    assert res.reason is ReasonCode.EXEC_CANCELLED


def test_recancel_is_idempotent(permissive_harness):
    holder, _ = _run_spin_and_cancel(permissive_harness)
    # After the op has terminated, re-cancelling returns its terminal state,
    # not a TOO_LATE-style error.
    again = permissive_harness.gate.cancel(holder["op"])
    assert again == {"actionable": False, "state": "CANCELLED"}


def test_cancel_unknown_operation(harness):
    assert harness.gate.cancel("op-nonexistent") == {"actionable": False, "state": None}
