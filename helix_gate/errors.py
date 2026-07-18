"""Stable reason-code taxonomy for Gate 2 (closes reviewer findings 11 & 12).

Two hard rules encoded here:

* External results carry a **stable machine-readable reason code**, never a raw
  exception string. ``str(e)`` may leak filesystem paths, host config, or module
  internals; diagnostic detail is routed to the protected audit channel instead
  (see :mod:`helix_gate.audit`).
* A cleanly *detected and rejected* condition (bad signature, replay, digest
  mismatch, capability escalation, resource-limit trip) is a **safe rejection**
  (``REJECTED_*``). ``FAILED_UNSAFE`` is reserved for the narrow case where the
  adapter cannot establish or preserve a safe state — e.g. it cannot terminate a
  worker after an isolation failure.
"""

from __future__ import annotations

import enum


class GateOutcome(str, enum.Enum):
    """Top-level disposition of a submission."""

    COMPLETED = "COMPLETED"          # module ran to a clean terminal result
    REJECTED = "REJECTED"            # safely refused before/at the boundary
    CANCELLED = "CANCELLED"          # terminated on request
    TIMED_OUT = "TIMED_OUT"          # exceeded fuel/epoch/wall budget
    TRAPPED = "TRAPPED"              # guest trapped (e.g. memory ceiling)
    FAILED_UNSAFE = "FAILED_UNSAFE"  # could not guarantee a safe state


class ReasonCode(str, enum.Enum):
    """Machine-readable reason codes. Returned to callers; safe to log/branch on."""

    # --- accepted / success ------------------------------------------------
    OK = "OK"

    # --- decode / schema ---------------------------------------------------
    REJECTED_MALFORMED = "REJECTED_MALFORMED"
    REJECTED_SCHEMA_INVALID = "REJECTED_SCHEMA_INVALID"
    REJECTED_SCHEMA_VERSION_UNSUPPORTED = "REJECTED_SCHEMA_VERSION_UNSUPPORTED"

    # --- signature / key ---------------------------------------------------
    REJECTED_ALG_UNSUPPORTED = "REJECTED_ALG_UNSUPPORTED"   # algorithm-confusion guard
    REJECTED_KEY_UNKNOWN = "REJECTED_KEY_UNKNOWN"
    REJECTED_KEY_REVOKED = "REJECTED_KEY_REVOKED"
    REJECTED_SIGNATURE_INVALID = "REJECTED_SIGNATURE_INVALID"

    # --- issuer / audience -------------------------------------------------
    REJECTED_ISSUER_UNTRUSTED = "REJECTED_ISSUER_UNTRUSTED"
    REJECTED_AUDIENCE_MISMATCH = "REJECTED_AUDIENCE_MISMATCH"

    # --- temporal ----------------------------------------------------------
    REJECTED_EXPIRED = "REJECTED_EXPIRED"
    REJECTED_NOT_YET_VALID = "REJECTED_NOT_YET_VALID"
    REJECTED_TIME_WINDOW_INVALID = "REJECTED_TIME_WINDOW_INVALID"

    # --- replay ------------------------------------------------------------
    REJECTED_REPLAY = "REJECTED_REPLAY"
    REJECTED_SEQUENCE_REGRESSION = "REJECTED_SEQUENCE_REGRESSION"

    # --- policy / capability ----------------------------------------------
    REJECTED_POLICY_REVISION = "REJECTED_POLICY_REVISION"
    REJECTED_OPERATION_NOT_ALLOWED = "REJECTED_OPERATION_NOT_ALLOWED"
    REJECTED_CAPABILITY_ESCALATION = "REJECTED_CAPABILITY_ESCALATION"
    REJECTED_RESOURCE_LIMIT_EXCEEDS_POLICY = "REJECTED_RESOURCE_LIMIT_EXCEEDS_POLICY"

    # --- module resolution -------------------------------------------------
    REJECTED_MODULE_NOT_FOUND = "REJECTED_MODULE_NOT_FOUND"
    REJECTED_MODULE_DIGEST_MISMATCH = "REJECTED_MODULE_DIGEST_MISMATCH"
    REJECTED_MODULE_INTERFACE_MISMATCH = "REJECTED_MODULE_INTERFACE_MISMATCH"

    # --- execution terminal states ----------------------------------------
    EXEC_COMPLETED = "EXEC_COMPLETED"
    EXEC_CANCELLED = "EXEC_CANCELLED"
    EXEC_TIMED_OUT = "EXEC_TIMED_OUT"
    EXEC_TRAPPED = "EXEC_TRAPPED"
    EXEC_OUTPUT_OVERFLOW = "EXEC_OUTPUT_OVERFLOW"

    # --- unsafe ------------------------------------------------------------
    FAILED_UNSAFE = "FAILED_UNSAFE"


class GateError(Exception):
    """Base for internal gate errors. Never surfaced verbatim to callers."""


class ValidationRejection(GateError):
    """A safe rejection at a validation stage. Carries a stable reason code.

    ``detail`` is diagnostic and goes only to the audit channel, never to the
    returned :class:`~helix_gate.results.GateResult`.
    """

    def __init__(self, reason: ReasonCode, detail: str = "") -> None:
        super().__init__(reason.value)
        self.reason = reason
        self.detail = detail


class UnsafeStateError(GateError):
    """The adapter could not establish or preserve a safe state (FAILED_UNSAFE)."""

    def __init__(self, detail: str = "") -> None:
        super().__init__(ReasonCode.FAILED_UNSAFE.value)
        self.reason = ReasonCode.FAILED_UNSAFE
        self.detail = detail
