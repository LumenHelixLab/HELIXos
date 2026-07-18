"""ExecutionGate — the Gate 2 orchestrator (replaces Draft 1's receive_envelope).

Drives one HX1 submission through the full boundary: validate -> register ->
execute in a disposable sandbox -> record a deterministic terminal state -> emit
signed audit events at every transition. Every exit is a structured
:class:`~helix_gate.results.GateResult` with a stable reason code; no exception
text ever reaches the caller (diagnostic ``detail`` goes to the audit log only).

Cancellation is concurrent: pass ``on_operation_id`` to learn the operation id
the instant it exists, then call :meth:`cancel` from another thread while
:meth:`submit` is still running.
"""

from __future__ import annotations

import hashlib
import threading
import time
import uuid
from typing import Callable

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .audit import AuditLog
from .errors import GateError, GateOutcome, ReasonCode, ValidationRejection
from .hx1.canonical import canonical_bytes
from .lifecycle import CleanupState, State
from .policy import Policy
from .registry import ConcurrentTransition, Registry
from .results import GateResult
from .sandbox import ExecSpec, SandboxController
from .validation import validate

_OUTCOME_TO_STATE = {
    GateOutcome.COMPLETED: State.COMPLETED,
    GateOutcome.TIMED_OUT: State.TIMED_OUT,
    GateOutcome.TRAPPED: State.TRAPPED,
    GateOutcome.FAILED_UNSAFE: State.FAILED_UNSAFE,
}


class ExecutionGate:
    def __init__(self, *, keyring, policy: Policy, module_store, audience: str,
                 replay=None, registry: Registry | None = None,
                 audit: AuditLog | None = None,
                 controller: SandboxController | None = None,
                 clock: Callable[[], int] | None = None) -> None:
        from .replay import ReplayStore  # local import avoids an unused hard dep

        self._keyring = keyring
        self._policy = policy
        self._module_store = module_store
        self._audience = audience
        self._replay = replay if replay is not None else ReplayStore(":memory:")
        self._registry = registry if registry is not None else Registry(":memory:")
        if audit is None:
            audit = AuditLog(":memory:", Ed25519PrivateKey.generate(),
                             "audit-ephemeral-" + uuid.uuid4().hex[:8])
        self._audit = audit
        self._controller = controller if controller is not None else SandboxController()
        self._clock = clock or (lambda: int(time.time()))

        self._inflight: dict[str, threading.Event] = {}
        self._inflight_lock = threading.Lock()

    # -- public API --------------------------------------------------------
    def submit(self, raw_envelope, owner: str = "unknown",
               on_operation_id: Callable[[str], None] | None = None) -> GateResult:
        op_id = self._registry.create(owner)
        cancel_ev = threading.Event()
        with self._inflight_lock:
            self._inflight[op_id] = cancel_ev
        if on_operation_id is not None:
            on_operation_id(op_id)

        try:
            return self._run(op_id, raw_envelope, cancel_ev)
        except Exception as exc:  # orchestration bug: fail closed, never leak text
            self._safe_terminal(op_id, f"orchestration: {type(exc).__name__}")
            return GateResult(GateOutcome.FAILED_UNSAFE, ReasonCode.FAILED_UNSAFE,
                              operation_id=op_id, terminal_state=State.FAILED_UNSAFE.value,
                              cleanup_state=CleanupState.FROZEN.value)
        finally:
            with self._inflight_lock:
                self._inflight.pop(op_id, None)

    def cancel(self, operation_id: str) -> dict:
        """Idempotently request cancellation. Returns a structured status.

        ``{'actionable': bool, 'state': <State value or None>}``. Re-cancelling a
        finished operation returns its terminal state, never ``TOO_LATE``.
        """
        actionable, state = self._registry.request_cancel(operation_id)
        with self._inflight_lock:
            ev = self._inflight.get(operation_id)
        if ev is not None:
            ev.set()
        return {"actionable": actionable, "state": state.value if state else None}

    def operation(self, operation_id: str):
        return self._registry.get(operation_id)

    @property
    def audit(self) -> AuditLog:
        return self._audit

    # -- internals ---------------------------------------------------------
    def _run(self, op_id: str, raw_envelope, cancel_ev: threading.Event) -> GateResult:
        self._emit(op_id, None, None, State.RECEIVED, ReasonCode.OK)
        self._registry.transition(op_id, State.RECEIVED, State.VALIDATING)
        self._emit(op_id, None, None, State.VALIDATING, ReasonCode.OK)

        try:
            vreq = validate(
                raw_envelope, keyring=self._keyring, policy=self._policy,
                replay=self._replay, module_store=self._module_store,
                audience=self._audience, now=self._clock(),
            )
        except ValidationRejection as rej:
            self._registry.transition(op_id, State.VALIDATING, State.REJECTED,
                                      terminal_reason=rej.reason.value)
            ev = self._emit(op_id, None, None, State.REJECTED, rej.reason,
                            message=rej.detail)
            self._registry.mark_cleaned_up(op_id)
            return GateResult(GateOutcome.REJECTED, rej.reason, operation_id=op_id,
                              terminal_state=State.REJECTED.value,
                              cleanup_state=CleanupState.CLEANED_UP.value,
                              audit_seq=ev.seq)

        env = vreq.envelope
        mod_digest = vreq.module.sha256
        env_digest = hashlib.sha256(canonical_bytes(env.signed_content)).hexdigest()

        self._registry.transition(op_id, State.VALIDATING, State.AUTHORIZED)
        self._emit(op_id, None, env_digest, State.AUTHORIZED, ReasonCode.OK,
                   module_digest=mod_digest)
        self._registry.transition(op_id, State.AUTHORIZED, State.QUEUED)
        sandbox_id = self._registry.assign_sandbox(op_id)
        self._emit(op_id, sandbox_id, env_digest, State.QUEUED, ReasonCode.OK,
                   module_digest=mod_digest)

        self._registry.transition(op_id, State.QUEUED, State.INITIALIZING)
        self._emit(op_id, sandbox_id, env_digest, State.INITIALIZING, ReasonCode.OK,
                   module_digest=mod_digest)

        # Cancellation requested before we start the guest: short-circuit.
        rec = self._registry.get(op_id)
        if (rec and rec.cancel_requested) or cancel_ev.is_set():
            return self._finish_cancelled(op_id, sandbox_id, env_digest, mod_digest,
                                          State.INITIALIZING)

        self._registry.transition(op_id, State.INITIALIZING, State.RUNNING)
        self._emit(op_id, sandbox_id, env_digest, State.RUNNING, ReasonCode.OK,
                   module_digest=mod_digest)

        spec = ExecSpec(wasm=vreq.module.wasm, entrypoint=vreq.module.entrypoint,
                        limits=dict(env.resource_limits), args=[])
        report = self._controller.run(spec, cancel_ev)

        if report.outcome is GateOutcome.CANCELLED:
            return self._finish_cancelled(op_id, sandbox_id, env_digest, mod_digest,
                                          State.RUNNING, report_detail=report.detail)

        terminal = _OUTCOME_TO_STATE[report.outcome]
        result_digest = (hashlib.sha256(report.output).hexdigest()
                         if report.output is not None else None)
        self._registry.transition(op_id, State.RUNNING, terminal,
                                  terminal_reason=report.reason.value)
        ev = self._emit(op_id, sandbox_id, env_digest, terminal, report.reason,
                        module_digest=mod_digest, result_digest=result_digest,
                        message=report.detail)
        self._registry.mark_cleaned_up(op_id)
        rec = self._registry.get(op_id)
        return GateResult(report.outcome, report.reason, operation_id=op_id,
                          sandbox_id=sandbox_id, terminal_state=terminal.value,
                          cleanup_state=rec.cleanup_state.value,
                          result_digest=result_digest, output=report.output,
                          audit_seq=ev.seq)

    def _finish_cancelled(self, op_id, sandbox_id, env_digest, mod_digest,
                          from_state: State, report_detail: str = "") -> GateResult:
        self._registry.transition(op_id, from_state, State.CANCELLING)
        self._emit(op_id, sandbox_id, env_digest, State.CANCELLING, ReasonCode.OK,
                   module_digest=mod_digest)
        self._registry.transition(op_id, State.CANCELLING, State.CANCELLED,
                                  terminal_reason=ReasonCode.EXEC_CANCELLED.value)
        ev = self._emit(op_id, sandbox_id, env_digest, State.CANCELLED,
                        ReasonCode.EXEC_CANCELLED, module_digest=mod_digest,
                        message=report_detail)
        self._registry.mark_cleaned_up(op_id)
        return GateResult(GateOutcome.CANCELLED, ReasonCode.EXEC_CANCELLED,
                          operation_id=op_id, sandbox_id=sandbox_id,
                          terminal_state=State.CANCELLED.value,
                          cleanup_state=CleanupState.CLEANED_UP.value, audit_seq=ev.seq)

    def _safe_terminal(self, op_id: str, detail: str) -> None:
        """Best-effort move to FAILED_UNSAFE after an orchestration error."""
        rec = self._registry.get(op_id)
        if rec is None or rec.is_terminal:
            return
        for src in (State.RUNNING, State.INITIALIZING, State.CANCELLING):
            try:
                self._registry.transition(op_id, rec.state, State.FAILED_UNSAFE,
                                          terminal_reason="orchestration failure")
                break
            except (ConcurrentTransition, Exception):
                continue
        try:
            self._emit(op_id, rec.sandbox_id, None, State.FAILED_UNSAFE,
                       ReasonCode.FAILED_UNSAFE, message=detail)
        except GateError:
            pass

    def _emit(self, op_id, sandbox_id, env_digest, state: State,
              reason: ReasonCode, *, module_digest=None, result_digest=None,
              message: str = ""):
        return self._audit.append(
            operation_id=op_id, sandbox_id=sandbox_id, state=state.value,
            previous_state=None, reason_code=reason.value,
            module_digest=module_digest, envelope_digest=env_digest,
            result_digest=result_digest, message=message,
        )
