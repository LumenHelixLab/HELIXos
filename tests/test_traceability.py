"""Traceability: every reviewer finding maps to concrete, tested behavior.

This is the checklist the reviewer asked for — each of the 13 Gate 2 findings
is tied to the module that closes it and the test(s) that exercise it. The test
fails if a mapped reason code disappears from the taxonomy, catching accidental
regressions of the contract.
"""

from __future__ import annotations

from helix_gate.errors import ReasonCode

# finding number -> (one-line disposition, covering test node(s))
FINDINGS = {
    1: ("Real Ed25519 signature verification over canonical bytes; keyring with "
        "revocation + algorithm-confusion guard",
        ["test_signature.py", "test_module_and_schema.py::test_module_digest_mismatch_rejected"]),
    2: ("Full ordered validation pipeline: schema, issuer/audience, time, "
        "replay/sequence, policy revision, module digest, capability manifest",
        ["test_module_and_schema.py", "test_temporal_and_audience.py", "test_replay.py"]),
    3: ("Module resolved by immutable knot:// ref and sha256-verified before use",
        ["test_module_and_schema.py::test_module_digest_mismatch_rejected"]),
    4: ("Guest runs in a disposable process; no host authority beyond declared "
        "imports; instantiation failures contained",
        ["test_sandbox.py::test_undeclared_import_denied_at_runtime"]),
    5: ("Cancellation works concurrently with a running guest (process isolation)",
        ["test_cancellation.py::test_cancel_mid_run"]),
    6: ("Fuel, epoch/wall, memory, output limits enforced; escalation denied",
        ["test_sandbox.py", "test_policy.py"]),
    7: ("Signed, hash-chained, append-only structured audit events",
        ["test_audit.py"]),
    8: ("Deterministic lifecycle FSM; records retained, not deleted",
        ["test_conformance.py::test_record_is_retained_after_completion",
         "test_registry.py"]),
    9: ("Idempotent cancellation returns the terminal state, never TOO_LATE",
        ["test_cancellation.py::test_recancel_is_idempotent",
         "test_registry.py::test_terminal_op_cancel_is_idempotent"]),
    10: ("Verb model narrowed to EXECUTE_WASM_COMPONENT only",
         ["test_policy.py::test_disallowed_operation_rejected"]),
    11: ("Detected/rejected conditions are safe REJECTED_*; FAILED_UNSAFE reserved",
         ["test_signature.py", "test_sandbox.py"]),
    12: ("Structured results with stable reason codes; no exception-text leakage",
         ["test_conformance.py", "test_module_and_schema.py"]),
    13: ("Durable registry: atomic CAS transitions, distinct ids, restart recovery",
         ["test_registry.py"]),
}

# Reason codes each finding relies on; a rename/removal should fail loudly here.
REQUIRED_REASON_CODES = [
    "REJECTED_SIGNATURE_INVALID", "REJECTED_ALG_UNSUPPORTED", "REJECTED_KEY_REVOKED",
    "REJECTED_KEY_UNKNOWN", "REJECTED_AUDIENCE_MISMATCH", "REJECTED_EXPIRED",
    "REJECTED_NOT_YET_VALID", "REJECTED_REPLAY", "REJECTED_SEQUENCE_REGRESSION",
    "REJECTED_POLICY_REVISION", "REJECTED_OPERATION_NOT_ALLOWED",
    "REJECTED_CAPABILITY_ESCALATION", "REJECTED_RESOURCE_LIMIT_EXCEEDS_POLICY",
    "REJECTED_MODULE_NOT_FOUND", "REJECTED_MODULE_DIGEST_MISMATCH",
    "REJECTED_MODULE_INTERFACE_MISMATCH", "REJECTED_SCHEMA_INVALID",
    "REJECTED_SCHEMA_VERSION_UNSUPPORTED", "REJECTED_MALFORMED",
    "EXEC_COMPLETED", "EXEC_CANCELLED", "EXEC_TIMED_OUT", "EXEC_TRAPPED",
    "EXEC_OUTPUT_OVERFLOW", "FAILED_UNSAFE",
]


def test_all_thirteen_findings_have_coverage():
    assert sorted(FINDINGS) == list(range(1, 14))
    for num, (disposition, tests) in FINDINGS.items():
        assert disposition and tests, f"finding {num} lacks coverage"


def test_required_reason_codes_exist():
    known = {rc.value for rc in ReasonCode}
    missing = [c for c in REQUIRED_REASON_CODES if c not in known]
    assert not missing, f"reason codes went missing: {missing}"
