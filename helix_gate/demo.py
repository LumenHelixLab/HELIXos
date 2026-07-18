"""End-to-end Gate 2 demonstration.

Run with ``python -m helix_gate.demo``. Signs real HX1 envelopes, runs them
through the full boundary against real Wasm modules, and prints the structured
result plus the verified signed audit chain — then a fault-injection tour
showing each rejection with its stable reason code.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time

from . import testkit as tk


def _line(label: str, res) -> None:
    print(f"  {label:24} -> outcome={res.outcome.value:13} reason={res.reason.value:34} "
          f"state={res.terminal_state}")


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="helix-gate-demo-")
    h = tk.build_harness(tmp)

    print("=" * 78)
    print("HELIXos Gate 2 — hardened Wasm execution adapter — live demo")
    print("=" * 78)

    print("\n[1] Happy path — a signed, policy-conformant module executes")
    answer = tk.publish(h.store, "answer")
    res = h.gate.submit(json.dumps(h.issuer.envelope(wasm=answer)), owner="krishna")
    _line("compute (returns 42)", res)

    echo = tk.publish(h.store, "echo")
    res = h.gate.submit(json.dumps(h.issuer.envelope(wasm=echo)), owner="krishna")
    _line("structured output", res)
    print(f"       output={res.output!r} result_digest={res.result_digest[:16]}...")

    print("\n[2] Fault injection — every defect is a safe, coded rejection")

    # tampered signature
    env = h.issuer.envelope(wasm=answer)
    env["target"] = "#t-evil"
    _line("tampered payload", h.gate.submit(json.dumps(env), owner="natasha"))

    # algorithm confusion
    env = h.issuer.envelope(wasm=answer)
    env["signature"]["alg"] = "rsa"
    _line("algorithm confusion", h.gate.submit(json.dumps(env), owner="natasha"))

    # expired
    env = h.issuer.envelope(wasm=answer, now=tk.CLOCK_NOW - 10_000, lifetime=1)
    _line("expired envelope", h.gate.submit(json.dumps(env), owner="natasha"))

    # replayed nonce: same nonce, bumped sequence, validly re-signed -> the
    # nonce store rejects it (isolates the replay path from sequence checks)
    first = h.issuer.envelope(wasm=answer, nonce="reused-nonce", sequence=100)
    h.gate.submit(json.dumps(first), owner="krishna")
    again = h.issuer.envelope(wasm=answer, nonce="reused-nonce", sequence=101)
    _line("replayed nonce", h.gate.submit(json.dumps(again), owner="natasha"))

    # sequence regression: older sequence from the same issuer
    older = h.issuer.envelope(wasm=answer, nonce="fresh-nonce", sequence=50)
    _line("sequence regression", h.gate.submit(json.dumps(older), owner="natasha"))

    # capability escalation (manifest declares a disallowed import)
    env = h.issuer.envelope(wasm=answer, manifest_imports=["env.secret_syscall"])
    _line("capability escalation", h.gate.submit(json.dumps(env), owner="natasha"))

    # module digest mismatch: validly signed for digest D, but the stored bytes
    # at D are tampered so they no longer hash to D (isolates the resolver)
    dh = tk.build_harness(tempfile.mkdtemp(prefix="helix-gate-dig-"))
    good = tk.publish(dh.store, "answer")
    digest = hashlib.sha256(good).hexdigest()
    env = dh.issuer.envelope(wasm=good)
    with open(os.path.join(dh.store._root, digest + ".wasm"), "wb") as fh:
        fh.write(good + b"\x00")  # corrupt the artifact in the store
    _line("module digest mismatch", dh.gate.submit(json.dumps(env), owner="natasha"))

    # revoked key
    h.keyring.revoke(h.issuer.key_id)
    env = h.issuer.envelope(wasm=answer)
    _line("revoked key", h.gate.submit(json.dumps(env), owner="natasha"))

    print("\n[3] Resource containment")
    h2 = tk.build_harness(tempfile.mkdtemp(prefix="helix-gate-demo2-"))
    fuel_mod = tk.publish(h2.store, "spin")
    env = h2.issuer.envelope(wasm=fuel_mod, limits=tk.default_limits(fuel=500_000))
    _line("fuel exhaustion", h2.gate.submit(json.dumps(env), owner="krishna"))

    oob = tk.publish(h2.store, "oob")
    env = h2.issuer.envelope(wasm=oob)
    _line("guest trap (OOB)", h2.gate.submit(json.dumps(env), owner="krishna"))

    print("\n[3b] Interruptible cancellation (concurrent, then idempotent)")
    permissive = tk.default_policy(limit_ceilings={
        "fuel": 10 ** 13, "wall_ms": 120_000, "memory_bytes": 16 * 65536,
        "table_elems": 1024, "stack_bytes": 1_048_576, "output_bytes": 4096,
        "host_calls": 0,
    })
    h3 = tk.build_harness(tempfile.mkdtemp(prefix="helix-gate-cancel-"), policy=permissive)
    spin = tk.publish(h3.store, "spin")
    env = h3.issuer.envelope(
        wasm=spin, limits=tk.default_limits(fuel=10 ** 12, wall_ms=60_000))
    holder: dict = {}

    def _worker() -> None:
        holder["res"] = h3.gate.submit(
            json.dumps(env), owner="krishna",
            on_operation_id=lambda oid: holder.__setitem__("op", oid))

    t = threading.Thread(target=_worker)
    t.start()
    while "op" not in holder:
        time.sleep(0.01)
    time.sleep(0.2)  # let it get well into the guest loop
    h3.gate.cancel(holder["op"])   # from the main thread, mid-run
    t.join()
    _line("cancel mid-run", holder["res"])
    print(f"       re-cancel (idempotent): {h3.gate.cancel(holder['op'])}")

    print("\n[4] Audit chain")
    print(f"       {len(h.audit.events())} signed events; chain verifies: "
          f"{h.audit.verify_chain()}")
    print(f"       {len(h2.audit.events())} signed events; chain verifies: "
          f"{h2.audit.verify_chain()}")
    print(f"       {len(h3.audit.events())} signed events; chain verifies: "
          f"{h3.audit.verify_chain()}")
    print("\nDone.")


if __name__ == "__main__":
    main()
