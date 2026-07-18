"""Sandbox controller — process supervision + interruptible cancel (finding 5).

The Draft 1 controller called execution *synchronously*, so ``cancel`` could
never run while a guest was executing. Here the guest runs in a **child
process** and the controller (parent) stays fully responsive: it polls for a
result, watches a cancellation signal, and enforces a hard wall-clock backstop.
Cancellation is a real interrupt — cooperative epoch interrupt inside the worker,
then hard process termination if the worker does not stop.

There is no in-process "kill a running Wasm call" primitive for embedded
Wasmtime; process isolation is what makes hard termination possible.
"""

from __future__ import annotations

import multiprocessing as mp
import queue as _queue
import time
from dataclasses import dataclass, field
from typing import Any

from ..errors import GateOutcome, ReasonCode
from . import worker

_CTX = mp.get_context("spawn")  # clean interpreter per execution; true isolation

_REASON_MAP = {
    worker.R_COMPLETED: (GateOutcome.COMPLETED, ReasonCode.EXEC_COMPLETED),
    worker.R_TIMED_OUT: (GateOutcome.TIMED_OUT, ReasonCode.EXEC_TIMED_OUT),
    worker.R_TRAPPED: (GateOutcome.TRAPPED, ReasonCode.EXEC_TRAPPED),
    worker.R_OUTPUT_OVERFLOW: (GateOutcome.TRAPPED, ReasonCode.EXEC_OUTPUT_OVERFLOW),
    worker.R_FAILED: (GateOutcome.FAILED_UNSAFE, ReasonCode.FAILED_UNSAFE),
}

_GRACE_MS = 2000       # backstop beyond wall_ms before hard kill
_POLL_S = 0.02
_KILL_JOIN_S = 2.0


@dataclass(frozen=True)
class ExecSpec:
    wasm: bytes
    entrypoint: str
    limits: dict[str, int]
    args: list[int] = field(default_factory=list)

    def as_child_dict(self) -> dict[str, Any]:
        return {
            "wasm": self.wasm,
            "entrypoint": self.entrypoint,
            "limits": self.limits,
            "args": list(self.args),
        }


@dataclass(frozen=True)
class ExecReport:
    outcome: GateOutcome
    reason: ReasonCode
    output: bytes | None = None
    result_value: int | None = None
    detail: str = ""              # internal; routed to audit, never to callers


class SandboxController:
    """Runs one :class:`ExecSpec` in a disposable child process."""

    def run(self, spec: ExecSpec, cancel_event=None) -> ExecReport:
        q = _CTX.Queue()
        proc = _CTX.Process(target=worker.execute, args=(spec.as_child_dict(), q))
        proc.start()

        backstop = time.monotonic() + spec.limits["wall_ms"] / 1000.0 + _GRACE_MS / 1000.0
        report: ExecReport | None = None
        try:
            while report is None:
                if cancel_event is not None and cancel_event.is_set():
                    report = ExecReport(GateOutcome.CANCELLED, ReasonCode.EXEC_CANCELLED,
                                        detail="cancelled by request")
                    break
                if time.monotonic() > backstop:
                    report = ExecReport(GateOutcome.TIMED_OUT, ReasonCode.EXEC_TIMED_OUT,
                                        detail="wall-clock backstop; worker hard-killed")
                    break
                try:
                    payload = q.get(timeout=_POLL_S)
                except _queue.Empty:
                    if not proc.is_alive():
                        # Died without delivering a result: cannot attest safety.
                        report = ExecReport(GateOutcome.FAILED_UNSAFE,
                                            ReasonCode.FAILED_UNSAFE,
                                            detail=f"worker exited code={proc.exitcode}")
                        break
                    continue
                report = self._map(payload)
        finally:
            self._terminate(proc)
            q.close()
        return report

    @staticmethod
    def _map(payload: dict[str, Any]) -> ExecReport:
        outcome, reason = _REASON_MAP.get(
            payload.get("reason", worker.R_FAILED),
            (GateOutcome.FAILED_UNSAFE, ReasonCode.FAILED_UNSAFE),
        )
        return ExecReport(
            outcome=outcome,
            reason=reason,
            output=payload.get("output"),
            result_value=payload.get("result_value"),
            detail=payload.get("detail", ""),
        )

    @staticmethod
    def _terminate(proc) -> None:
        """Idempotent teardown: safe whether the child exited on its own or not.

        Never calls ``proc.close()`` — keeping the handle open lets the caller
        read ``exitcode`` and avoids ValueError from later ``is_alive`` checks.
        """
        if proc.is_alive():
            proc.terminate()                 # SIGTERM
            proc.join(_KILL_JOIN_S)
        if proc.is_alive():
            proc.kill()                      # SIGKILL
            proc.join(_KILL_JOIN_S)
        else:
            proc.join(_KILL_JOIN_S)
