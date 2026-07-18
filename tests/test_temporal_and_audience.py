"""Temporal window + audience faults (reviewer finding 2)."""

from __future__ import annotations

from helix_gate import testkit as tk
from helix_gate.errors import ReasonCode
from conftest import submit


def test_expired_rejected(harness):
    wasm = tk.publish(harness.store, "answer")
    env = harness.issuer.envelope(wasm=wasm, now=tk.CLOCK_NOW - 10_000, lifetime=1)
    assert submit(harness, env).reason is ReasonCode.REJECTED_EXPIRED


def test_not_yet_valid_rejected(harness):
    wasm = tk.publish(harness.store, "answer")
    env = harness.issuer.envelope(wasm=wasm, now=tk.CLOCK_NOW + 10_000)
    assert submit(harness, env).reason is ReasonCode.REJECTED_NOT_YET_VALID


def test_audience_mismatch_rejected(harness):
    wasm = tk.publish(harness.store, "answer")
    env = harness.issuer.envelope(wasm=wasm, audience="gate://someone-else")
    assert submit(harness, env).reason is ReasonCode.REJECTED_AUDIENCE_MISMATCH
