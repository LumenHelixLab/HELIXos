"""Durable registry: atomic transitions, recovery, idempotent cancel.

Reviewer findings 8, 9, 13.
"""

from __future__ import annotations

import os
import threading

import pytest

from helix_gate.lifecycle import CleanupState, State
from helix_gate.registry import ConcurrentTransition, Registry


def test_atomic_cas_transition_has_single_winner():
    reg = Registry(":memory:")
    op = reg.create("owner")
    reg.transition(op, State.RECEIVED, State.VALIDATING)

    results = []
    barrier = threading.Barrier(6)

    def racer():
        barrier.wait()
        try:
            reg.transition(op, State.VALIDATING, State.AUTHORIZED)
            results.append("won")
        except ConcurrentTransition:
            results.append("lost")

    threads = [threading.Thread(target=racer) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("won") == 1
    assert results.count("lost") == 5


def test_illegal_transition_rejected():
    reg = Registry(":memory:")
    op = reg.create("owner")
    from helix_gate.lifecycle import IllegalTransition
    with pytest.raises(IllegalTransition):
        reg.transition(op, State.RECEIVED, State.COMPLETED)


def test_restart_recovery_sweeps_orphans(tmp_path):
    path = os.path.join(tmp_path, "reg.db")
    reg = Registry(path)
    op = reg.create("owner")
    reg.transition(op, State.RECEIVED, State.VALIDATING)
    reg.transition(op, State.VALIDATING, State.AUTHORIZED)
    reg.transition(op, State.AUTHORIZED, State.QUEUED)
    reg.transition(op, State.QUEUED, State.INITIALIZING)
    reg.transition(op, State.INITIALIZING, State.RUNNING)
    reg.close()

    # A new controller process opens the same durable registry.
    reopened = Registry(path)
    swept = reopened.recover_orphans()
    assert op in swept
    rec = reopened.get(op)
    assert rec.state is State.FAILED_UNSAFE
    assert rec.cleanup_state is CleanupState.FROZEN


def test_terminal_op_cancel_is_idempotent():
    reg = Registry(":memory:")
    op = reg.create("owner")
    reg.transition(op, State.RECEIVED, State.VALIDATING)
    reg.transition(op, State.VALIDATING, State.REJECTED, terminal_reason="x")
    # Re-cancelling a terminal op reports its terminal state, never TOO_LATE.
    assert reg.request_cancel(op) == (False, State.REJECTED)


def test_abnormal_terminal_freezes_before_cleanup():
    reg = Registry(":memory:")
    op = reg.create("owner")
    reg.transition(op, State.RECEIVED, State.VALIDATING)
    reg.transition(op, State.VALIDATING, State.AUTHORIZED)
    reg.transition(op, State.AUTHORIZED, State.QUEUED)
    reg.transition(op, State.QUEUED, State.INITIALIZING)
    reg.transition(op, State.INITIALIZING, State.RUNNING)
    reg.transition(op, State.RUNNING, State.TRAPPED, terminal_reason="trap")
    rec = reg.get(op)
    assert rec.state is State.TRAPPED
    assert rec.cleanup_state is CleanupState.FROZEN  # resources quarantined
