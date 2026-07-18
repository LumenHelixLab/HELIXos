"""Conformance: a valid, policy-conformant envelope executes cleanly."""

from __future__ import annotations

from helix_gate import testkit as tk
from helix_gate.errors import GateOutcome, ReasonCode
from helix_gate.lifecycle import CleanupState, State
from conftest import submit


def test_compute_module_completes(harness):
    wasm = tk.publish(harness.store, "answer")
    res = submit(harness, harness.issuer.envelope(wasm=wasm), owner="krishna")
    assert res.outcome is GateOutcome.COMPLETED
    assert res.reason is ReasonCode.EXEC_COMPLETED
    assert res.terminal_state == State.COMPLETED.value
    assert res.cleanup_state == CleanupState.CLEANED_UP.value
    assert res.operation_id and res.sandbox_id
    # operation_id and sandbox_id are distinct identifiers (not the nonce).
    assert res.operation_id != res.sandbox_id


def test_structured_output_and_digest(harness):
    wasm = tk.publish(harness.store, "echo")
    res = submit(harness, harness.issuer.envelope(wasm=wasm))
    assert res.outcome is GateOutcome.COMPLETED
    assert res.output == b"HELIX-OK"
    assert res.result_digest is not None and len(res.result_digest) == 64


def test_record_is_retained_after_completion(harness):
    """Draft 1 deleted the record; a terminal op must remain inspectable."""
    wasm = tk.publish(harness.store, "answer")
    res = submit(harness, harness.issuer.envelope(wasm=wasm))
    rec = harness.gate.operation(res.operation_id)
    assert rec is not None
    assert rec.state is State.COMPLETED
    assert rec.is_terminal


def test_full_lifecycle_recorded_in_audit(harness):
    wasm = tk.publish(harness.store, "answer")
    submit(harness, harness.issuer.envelope(wasm=wasm))
    states = [e.fields["state"] for e in harness.audit.events()]
    assert states == [
        "RECEIVED", "VALIDATING", "AUTHORIZED", "QUEUED",
        "INITIALIZING", "RUNNING", "COMPLETED",
    ]
    assert harness.audit.verify_chain()
