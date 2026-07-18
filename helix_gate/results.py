"""Structured results returned to callers (closes reviewer findings 11 & 12).

Results are structured objects with a stable :class:`~helix_gate.errors.ReasonCode`
and machine-readable state — never a bare string and never a raw exception
message. Diagnostic ``detail`` is deliberately *not* included here; it goes only
to the protected audit channel.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .errors import GateOutcome, ReasonCode


@dataclass(frozen=True)
class GateResult:
    """The single value returned from :meth:`helix_gate.adapter.ExecutionGate.submit`."""

    outcome: GateOutcome
    reason: ReasonCode
    operation_id: str | None = None
    sandbox_id: str | None = None
    terminal_state: str | None = None
    cleanup_state: str | None = None
    result_digest: str | None = None
    output: bytes | None = None          # bounded; may be None on rejection
    audit_seq: int | None = None         # index of the terminal audit event

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["outcome"] = self.outcome.value
        d["reason"] = self.reason.value
        if self.output is not None:
            d["output"] = self.output.decode("utf-8", "replace")
        return d

    @property
    def accepted(self) -> bool:
        return self.outcome is GateOutcome.COMPLETED
