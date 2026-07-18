"""Child-process Wasm execution (closes reviewer findings 4 & 6).

Runs in a **separate, disposable process** (spawned fresh, no inherited state)
so the parent stays responsive during execution and can hard-terminate a
runaway guest. Core WebAssembly grants the guest no host authority beyond the
imports the embedder supplies; this worker supplies **none** by default — the
capability manifest's ``imports`` allowlist is enforced by policy upstream, and a
module that requires an undeclared import fails to instantiate here.

Enforced ceilings (all from the signed manifest's ``resource_limits``):

* ``fuel``        — Wasmtime fuel budget; exhaustion traps -> TIMED_OUT.
* ``wall_ms``     — watchdog thread bumps the epoch -> interrupt -> TIMED_OUT.
* ``memory_bytes``/``table_elems`` — store limits.
* ``stack_bytes`` — max wasm stack.
* ``output_bytes``— cap on bytes read back from guest memory; over -> OUTPUT_OVERFLOW.

The interface contract ``helix:execution@1.0.0``:

* the module exports the ``entrypoint`` function ``() -> i32`` (a status code);
* optionally exports ``memory`` plus ``output_ptr () -> i32`` and
  ``output_len () -> i32`` to return result bytes.

This module must stay importable without side effects: it is re-imported in the
spawned child.
"""

from __future__ import annotations

import threading
import time
from typing import Any


# Reason strings the parent maps to ReasonCode/GateOutcome. Kept as plain
# constants so this module has no import cycle with the gate's error taxonomy.
R_COMPLETED = "EXEC_COMPLETED"
R_TIMED_OUT = "EXEC_TIMED_OUT"
R_TRAPPED = "EXEC_TRAPPED"
R_OUTPUT_OVERFLOW = "EXEC_OUTPUT_OVERFLOW"
R_FAILED = "FAILED_UNSAFE"


def _classify_trap(message: str) -> str:
    m = message.lower()
    if "fuel" in m or "epoch" in m or "interrupt" in m or "deadline" in m:
        return R_TIMED_OUT
    return R_TRAPPED


def execute(spec: dict[str, Any], result_q) -> None:
    """Entry point for the child process. Puts one result dict on ``result_q``.

    ``spec`` keys: ``wasm`` (bytes), ``entrypoint`` (str), ``args`` (list[int]),
    ``limits`` (dict). Never raises out of the process; every failure is a
    structured dict.
    """
    try:
        result_q.put(_run(spec))
    except BaseException as exc:  # last-resort: report, never crash silently
        result_q.put({
            "reason": R_FAILED,
            "detail": f"worker crashed: {type(exc).__name__}",
            "output": None,
            "result_value": None,
        })


def _run(spec: dict[str, Any]) -> dict[str, Any]:
    import wasmtime  # imported in the child

    limits = spec["limits"]
    cfg = wasmtime.Config()
    cfg.consume_fuel = True
    cfg.epoch_interruption = True
    if limits.get("stack_bytes"):
        # max_wasm_stack must be < the host thread stack; clamp defensively.
        cfg.max_wasm_stack = max(8192, int(limits["stack_bytes"]))

    engine = wasmtime.Engine(cfg)
    store = wasmtime.Store(engine)
    store.set_fuel(int(limits["fuel"]))
    store.set_limits(
        memory_size=int(limits["memory_bytes"]),
        table_elements=int(limits["table_elems"]) if limits.get("table_elems") else -1,
    )

    # Wall-clock watchdog: one epoch tick past the deadline interrupts the guest.
    store.set_epoch_deadline(1)
    deadline_ms = int(limits["wall_ms"])
    stop = threading.Event()

    def watchdog() -> None:
        if stop.wait(deadline_ms / 1000.0):
            return
        engine.increment_epoch()

    wd = threading.Thread(target=watchdog, daemon=True)
    wd.start()

    try:
        module = wasmtime.Module(engine, spec["wasm"])
    except Exception as exc:
        stop.set()
        return {"reason": R_TRAPPED, "detail": f"compile: {type(exc).__name__}",
                "output": None, "result_value": None}

    try:
        # No imports supplied: capability-denied by default.
        instance = wasmtime.Instance(store, module, [])
    except wasmtime.Trap as t:
        stop.set()
        return {"reason": _classify_trap(str(t)), "detail": "instantiate trap",
                "output": None, "result_value": None}
    except Exception as exc:
        stop.set()
        # Instantiation failing under resource limits is an execution-time trap,
        # not a validation reject (the module was already digest-authorized).
        return {"reason": R_TRAPPED, "detail": f"instantiate: {type(exc).__name__}",
                "output": None, "result_value": None}

    exports = instance.exports(store)
    entry = exports.get(spec["entrypoint"])
    if entry is None:
        stop.set()
        return {"reason": R_TRAPPED, "detail": "entrypoint missing",
                "output": None, "result_value": None}

    try:
        args = list(spec.get("args") or [])
        result_value = entry(store, *args)
    except wasmtime.Trap as t:
        stop.set()
        return {"reason": _classify_trap(str(t)), "detail": "guest trap",
                "output": None, "result_value": None}
    except Exception as exc:
        stop.set()
        return {"reason": R_FAILED, "detail": f"host error: {type(exc).__name__}",
                "output": None, "result_value": None}
    finally:
        stop.set()

    # Optional structured output from guest linear memory.
    output, over = _read_output(store, exports, int(limits["output_bytes"]))
    if over:
        return {"reason": R_OUTPUT_OVERFLOW, "detail": "output exceeds cap",
                "output": None, "result_value": _as_int(result_value)}

    return {"reason": R_COMPLETED, "detail": "", "output": output,
            "result_value": _as_int(result_value)}


def _as_int(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _read_output(store, exports, cap: int):
    mem = exports.get("memory")
    fptr = exports.get("output_ptr")
    flen = exports.get("output_len")
    if mem is None or fptr is None or flen is None:
        return None, False
    try:
        ptr = int(fptr(store))
        length = int(flen(store))
    except Exception:
        return None, False
    if length < 0:
        return None, False
    if length > cap:
        return None, True
    data = mem.read(store, ptr, ptr + length)
    return bytes(data), False
