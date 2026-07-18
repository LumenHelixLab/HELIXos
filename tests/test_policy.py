"""Policy revision, operation, capability, and resource-ceiling faults.

Reviewer findings 2, 6, 10.
"""

from __future__ import annotations

from helix_gate import testkit as tk
from helix_gate.errors import ReasonCode
from conftest import submit


def test_wrong_policy_revision_rejected(harness):
    wasm = tk.publish(harness.store, "answer")
    env = harness.issuer.envelope(wasm=wasm, policy_revision="policy-ancient")
    assert submit(harness, env).reason is ReasonCode.REJECTED_POLICY_REVISION


def test_disallowed_operation_rejected(harness):
    """Broad verbs never reach execution; only EXECUTE_WASM_COMPONENT is allowed."""
    wasm = tk.publish(harness.store, "answer")
    env = harness.issuer.envelope(wasm=wasm, operation="REFACTOR")  # signed in
    assert submit(harness, env).reason is ReasonCode.REJECTED_OPERATION_NOT_ALLOWED


def test_capability_escalation_import_denied(harness):
    wasm = tk.publish(harness.store, "answer")
    env = harness.issuer.envelope(wasm=wasm, manifest_imports=["env.secret_syscall"])
    assert submit(harness, env).reason is ReasonCode.REJECTED_CAPABILITY_ESCALATION


def test_output_destination_escalation_denied(harness):
    wasm = tk.publish(harness.store, "answer")
    env = harness.issuer.envelope(wasm=wasm, output_destinations=["file:///etc/passwd"])
    assert submit(harness, env).reason is ReasonCode.REJECTED_CAPABILITY_ESCALATION


def test_resource_limit_exceeds_policy_rejected(harness):
    wasm = tk.publish(harness.store, "answer")
    # policy fuel ceiling is 100_000_000; ask for more.
    env = harness.issuer.envelope(
        wasm=wasm, limits=tk.default_limits(fuel=999_999_999))
    assert submit(harness, env).reason is ReasonCode.REJECTED_RESOURCE_LIMIT_EXCEEDS_POLICY
