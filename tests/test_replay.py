"""Replay / sequence faults + store atomicity (reviewer findings 2, 13)."""

from __future__ import annotations

import threading

from helix_gate import testkit as tk
from helix_gate.errors import GateOutcome, ReasonCode
from helix_gate.replay import ReplayStore
from helix_gate.errors import ValidationRejection
from conftest import submit


def test_nonce_replay_rejected(harness):
    wasm = tk.publish(harness.store, "answer")
    first = harness.issuer.envelope(wasm=wasm, nonce="N", sequence=100)
    assert submit(harness, first).outcome is GateOutcome.COMPLETED
    # same nonce, higher sequence -> passes sequence check, fails nonce check
    again = harness.issuer.envelope(wasm=wasm, nonce="N", sequence=101)
    assert submit(harness, again).reason is ReasonCode.REJECTED_REPLAY


def test_sequence_regression_rejected(harness):
    wasm = tk.publish(harness.store, "answer")
    hi = harness.issuer.envelope(wasm=wasm, nonce="a", sequence=100)
    assert submit(harness, hi).outcome is GateOutcome.COMPLETED
    lo = harness.issuer.envelope(wasm=wasm, nonce="b", sequence=50)
    assert submit(harness, lo).reason is ReasonCode.REJECTED_SEQUENCE_REGRESSION


def test_replay_store_is_atomic_under_concurrency():
    """Only one of many concurrent commits of the same nonce may succeed."""
    store = ReplayStore(":memory:")
    outcomes = []
    barrier = threading.Barrier(8)

    def worker(seq):
        barrier.wait()
        try:
            store.commit_use("dup", "issuer://x", seq, now=1)
            outcomes.append("ok")
        except ValidationRejection:
            outcomes.append("rejected")

    threads = [threading.Thread(target=worker, args=(i + 1,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("ok") == 1
    assert outcomes.count("rejected") == 7
