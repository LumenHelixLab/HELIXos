#!/usr/bin/env python3
"""HELIXos M0 smoke demo — end-to-end proof of the hardened pipeline (SPEC §3).

Owner: Stage-2 integrator (SPEC §1). Exercises, in order:

 1. config from env (HELIXOS_HMAC_KEY / HELIXOS_OWNER_TOKEN; ephemeral dev
    values, announced loudly, when unset — never hardcoded);
 2. the knotcore sidecar as a subprocess on a temp Unix socket, with the
    health()/abi_version handshake (ADR-002);
 3. generation of a triangulated bus instruction via KNOT_API_WRAPPER with
    HMACSigner + SidecarBackend (SPEC §3.3);
 4. fail-closed verification of that instruction;
 5. five attacks, each expected to be REJECTED fail-closed: replay, verb-swap
    forgery, wrong key, stale timestamp, malformed line (AUD-C1/C4/C5);
 6. owner possession through KrishnaManifestor (wrong token denied, fenced
    possess, manifest "PING" through BABEL, release) (SPEC §3.6);
 7. the EventJournal: every demo event appended, hash chain verified (ADR-001);
 8. the TEN-SQUARED FSM benchmark — Layer-1 budget p99 < 1000 µs (AUD-C7);
 9. epoch fencing of a stale message after a Projective Collapse increment;
10. clean sidecar shutdown.

Run from the repo root:  python3 scripts/demo_m0.py
Exit code 0 = all checks passed; 1 = a check failed; 2 = unexpected error.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

# --- sys.path bootstrap (mirrors root conftest.py; SPEC §1 flat namespace) ---
_ROOT = Path(__file__).resolve().parent.parent
_KERNEL = _ROOT / "aigent-os-kernel"
_CODE_DIRS = (
    _KERNEL / "src" / "runtime",       # ten_squared_fsm.py
    _KERNEL / "src" / "memory",        # journal.py, epochs.py
    _KERNEL / "src" / "BABEL",         # dispatcher.py
    _KERNEL / "KNOTstore_bin",         # signers.py, knotcore_sim.py, KNOT_API_WRAPPER.py
    _KERNEL / "KNOTstore_bin" / "sidecar",  # rpc_protocol.py, sidecar_server.py, sidecar_client.py
    _KERNEL / "orchestrator",          # possession.py
)
for _dir in reversed(_CODE_DIRS):
    _path = str(_dir)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dispatcher import create_default_dispatcher  # noqa: E402
from epochs import EpochFence  # noqa: E402
from journal import EventJournal  # noqa: E402
from KNOT_API_WRAPPER import (  # noqa: E402
    BUS_RE,
    SidecarBackend,
    generate_triangulated_instruction,
    verify_instruction_lock,
)
from possession import KrishnaManifestor, PossessionDenied  # noqa: E402
from sidecar_client import KnotClient  # noqa: E402
from signers import HMACSigner, HMACVerifier  # noqa: E402
from ten_squared_fsm import benchmark  # noqa: E402

_SIDECAR_SERVER = _KERNEL / "KNOTstore_bin" / "sidecar" / "sidecar_server.py"
_FSM_BUDGET_US = 1000.0  # Layer-1 in-process budget (docs/latency-budgets.md)

_CHECKS = 0
_FAILURES: list[str] = []


def check(ok: bool, label: str) -> bool:
    """Record one demo assertion; print PASS/FAIL."""
    global _CHECKS
    _CHECKS += 1
    print(f"      [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        _FAILURES.append(label)
    return ok


def step(index: int, title: str) -> None:
    print(f"\n[{index:2d}/10] {title}")
    print("      " + "-" * (len(title) + 4))


def main() -> int:
    print("=" * 74)
    print("HELIXos M0 SMOKE DEMO — hardened Triangulated Bus pipeline, end to end")
    print("=" * 74)

    journal: EventJournal | None = None
    sidecar: subprocess.Popen | None = None
    tmp = tempfile.TemporaryDirectory(prefix="helixos-m0-demo-")
    try:
        sock_path = os.path.join(tmp.name, "knotcore.sock")
        journal_path = os.path.join(tmp.name, "demo-journal.jsonl")
        sidecar_log_path = os.path.join(tmp.name, "sidecar.log")

        # ---------------------------------------------------------- 1. config
        step(1, "Config from environment (secrets from env only — SPEC §3.10)")
        hmac_hex = os.environ.get("HELIXOS_HMAC_KEY", "").strip()
        if hmac_hex:
            try:
                key = bytes.fromhex(hmac_hex)
            except ValueError:
                print("      ERROR: HELIXOS_HMAC_KEY is not valid hex")
                return 1
            if len(key) < 32:
                print(f"      ERROR: HELIXOS_HMAC_KEY is {len(key)} bytes; need >= 32")
                return 1
            print("      HELIXOS_HMAC_KEY: loaded from environment "
                  f"({len(key)} bytes)")
        else:
            key = secrets.token_bytes(32)
            print("      !! WARNING: HELIXOS_HMAC_KEY is NOT SET — generated an")
            print("      !! EPHEMERAL 32-byte dev key for this run only. It is")
            print("      !! never stored; nothing signed here can be verified")
            print("      !! after this process exits. Set HELIXOS_HMAC_KEY")
            print("      !! (hex, >= 32 bytes) for a stable dev identity.")
        signer, verifier = HMACSigner(key), HMACVerifier(key)

        owner_token = os.environ.get("HELIXOS_OWNER_TOKEN", "")
        if owner_token:
            print("      HELIXOS_OWNER_TOKEN: loaded from environment")
        else:
            owner_token = f"ephemeral-owner-{secrets.token_hex(16)}"
            print("      !! WARNING: HELIXOS_OWNER_TOKEN is NOT SET — using an")
            print("      !! EPHEMERAL owner token for this run only (never stored).")
        fence = EpochFence(0)
        journal = EventJournal(journal_path)
        journal.append("demo.config", {
            "hmac_key": "env" if hmac_hex else "ephemeral",
            "owner_token": "env" if os.environ.get("HELIXOS_OWNER_TOKEN") else "ephemeral",
        }, epoch=fence.current)
        print(f"      EventJournal (system of record, ADR-001): {journal_path}")
        print(f"      EpochFence: starting at epoch {fence.current}")

        # ------------------------------------------------- 2. sidecar startup
        step(2, "knotcore sidecar subprocess + ABI handshake (ADR-002)")
        sidecar_log = open(sidecar_log_path, "w", encoding="utf-8")
        sidecar = subprocess.Popen(  # noqa: S603
            [sys.executable, str(_SIDECAR_SERVER), sock_path],
            stdout=sidecar_log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "HELIXOS_SIDECAR_SOCKET": sock_path},
        )
        print(f"      sidecar pid={sidecar.pid} socket={sock_path}")
        health, abi = None, None
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if sidecar.poll() is not None:
                break  # died during startup; handled below
            try:
                # Fresh client per attempt: the circuit breaker must not latch
                # open during startup races.
                probe = KnotClient(sock_path, timeout=1.0)
                health, abi = probe.health(), probe.abi_version()
                break
            except Exception:
                time.sleep(0.1)
        if sidecar.poll() is not None:
            sidecar_log.flush()
            print(f"      ERROR: sidecar exited during startup "
                  f"(rc={sidecar.returncode}); log:")
            print(Path(sidecar_log_path).read_text(encoding="utf-8"))
            return 1
        ok = (
            isinstance(health, dict)
            and health.get("status") == "ok"
            and health.get("abi_version") == 1
            and abi == 1
        )
        check(ok, f"health()/abi_version handshake: health={health} abi_version={abi}")
        if not ok:
            return 1
        backend = SidecarBackend(sock_path)

        # ------------------------------------------------- 3. generate line
        step(3, "Generate triangulated instruction (HMACSigner + SidecarBackend)")
        payload = "HELIXos M0 smoke: owner-authorized EXEC over the Triangulated Bus"
        line = generate_triangulated_instruction(payload, "EXEC", signer, backend=backend)
        print(f"      bus line: {line}")
        check(BUS_RE.fullmatch(line) is not None, "bus line matches SPEC §2 wire format")
        journal.append("bus.instruction.generated", {"line": line, "verb": "EXEC"},
                       epoch=fence.current)

        # ---------------------------------------------------- 4. verify line
        step(4, "Verify the instruction (fail-closed system firewall)")
        verified = verify_instruction_lock(line, verifier, backend=backend)
        check(verified is True, "verify_instruction_lock(line) is True")
        journal.append("bus.instruction.verified", {"line": line, "result": verified},
                       epoch=fence.current)
        if not verified:
            return 1

        # ----------------------------------------------------- 5. attack set
        step(5, "Attack demonstrations — every one must fail closed")
        replayed = verify_instruction_lock(line, verifier, backend=backend)
        check(replayed is False, "replay of the same line rejected (seen-nonce cache)")
        journal.append("bus.attack_rejected", {"kind": "replay", "result": replayed},
                       epoch=fence.current)

        exec_line = generate_triangulated_instruction(
            "forgery target", "EXEC", signer, backend=backend)
        forged = exec_line.replace("| EXEC |", "| READ |")
        assert forged != exec_line
        forged_ok = verify_instruction_lock(forged, verifier, backend=backend)
        check(forged_ok is False,
              "verb-swap forgery EXEC->READ rejected (HMAC binds ptr|verb|envelope)")
        journal.append("bus.attack_rejected", {"kind": "verb_swap", "line": forged,
                                               "result": forged_ok}, epoch=fence.current)

        wrong_key_line = generate_triangulated_instruction(
            "wrong-key target", "READ", signer, backend=backend)
        wrong_verifier = HMACVerifier(secrets.token_bytes(32))
        wrong_ok = verify_instruction_lock(wrong_key_line, wrong_verifier, backend=backend)
        check(wrong_ok is False, "wrong-key verification rejected (constant-time compare)")
        journal.append("bus.attack_rejected", {"kind": "wrong_key", "result": wrong_ok},
                       epoch=fence.current)

        stale_line = generate_triangulated_instruction(
            "stale target", "READ", signer, backend=backend,
            _now=time.time() - 3600)  # TEST-ONLY hook (wrapper docstring), 1 h back
        stale_ok = verify_instruction_lock(stale_line, verifier, backend=backend)
        check(stale_ok is False, "stale timestamp (1 h old) rejected (300 s skew window)")
        journal.append("bus.attack_rejected", {"kind": "stale_timestamp",
                                               "result": stale_ok}, epoch=fence.current)

        malformed = "[ 0xDEADBEEF | EXEC | #not-a-real-tag ]"
        malformed_ok = verify_instruction_lock(malformed, verifier, backend=backend)
        check(malformed_ok is False, "malformed line rejected (BUS_RE fullmatch)")
        journal.append("bus.attack_rejected", {"kind": "malformed",
                                               "result": malformed_ok}, epoch=fence.current)

        # ------------------------------------------------------ 6. possession
        step(6, "Owner possession — KrishnaManifestor (KRISHNA-only, fenced)")
        dispatcher = create_default_dispatcher()
        manifestor = KrishnaManifestor(
            "KRISHNA", KrishnaManifestor.hash_token(owner_token.encode("utf-8")),
            dispatcher)
        try:
            manifestor.toggle_possession(f"wrong-token-{secrets.token_hex(8)}")
            check(False, "wrong owner token must be denied")
        except PossessionDenied as exc:
            check(True, f"wrong owner token denied ({exc})")
            journal.append("possession.denied", {"reason": str(exc),
                                                 "fence": manifestor.fencing_token},
                           epoch=fence.current)

        state = manifestor.toggle_possession(owner_token)
        print(f"      possess -> {state}")
        check("MANIFESTOR_MODE: True" in state and manifestor.fencing_token == 1,
              f"possessed with fencing token {manifestor.fencing_token}")
        journal.append("possession.toggled", {"state": True,
                                              "fence": manifestor.fencing_token},
                       epoch=fence.current)

        result = manifestor.manifest("PING")
        print(f"      manifest 'PING' through BABEL -> {result!r}")
        check(result == "PONG", "BABEL dispatch PING -> PONG")
        journal.append("manifest.dispatch", {"cmd": "PING", "result": result,
                                             "fence": manifestor.fencing_token},
                       epoch=fence.current)

        released = manifestor.toggle_possession(owner_token)
        print(f"      release -> {released}")
        check("MANIFESTOR_MODE: False" in released and manifestor.fencing_token == 2,
              f"released; fencing token advanced to {manifestor.fencing_token}")
        journal.append("possession.toggled", {"state": False,
                                              "fence": manifestor.fencing_token},
                       epoch=fence.current)

        # --------------------------------------------------------- 7. journal
        step(7, "EventJournal — hash-chain verification (ADR-001)")
        chain_ok = journal.verify_chain()
        events = journal.read_all()
        check(chain_ok is True, "verify_chain() is True")
        print(f"      journal holds {len(events)} events "
              f"(generation, verification, 5 rejected attacks, possession transitions)")
        check(len(events) >= 12, f"event count {len(events)} covers all demo steps")

        # ------------------------------------------------------------- 8. FSM
        step(8, "TEN-SQUARED FSM benchmark — Layer-1 latency budget (AUD-C7)")
        stats = benchmark(50_000)
        print(f"      50,000 transitions: p50={stats['p50_us']:.2f} µs  "
              f"p95={stats['p95_us']:.2f} µs  p99={stats['p99_us']:.2f} µs  "
              f"p99.9={stats['p999_us']:.2f} µs  max={stats['max_us']:.2f} µs")
        check(stats["p99_us"] < _FSM_BUDGET_US,
              f"p99 {stats['p99_us']:.2f} µs < {_FSM_BUDGET_US:.0f} µs budget")
        journal.append("fsm.benchmark", dict(stats), epoch=fence.current)

        # ---------------------------------------------------- 9. epoch fence
        step(9, "Epoch fencing — Projective Collapse invalidates stale epochs")
        msg_epoch = fence.current
        check(fence.fences(msg_epoch) is False,
              f"epoch-{msg_epoch} message admitted before collapse")
        new_epoch = fence.increment()
        print(f"      Projective Collapse: epoch {msg_epoch} -> {new_epoch}")
        journal.append("epoch.incremented", {"new_epoch": new_epoch}, epoch=new_epoch)
        check(fence.fences(msg_epoch) is True,
              f"stale epoch-{msg_epoch} message FENCED after increment to {new_epoch}")

        # ------------------------------------------------------- 10. shutdown
        step(10, "Clean sidecar shutdown")
        sidecar.terminate()  # SIGTERM -> graceful shutdown path in main()
        rc = sidecar.wait(timeout=10)
        sidecar_log.close()
        check(rc == 0, f"sidecar exited cleanly on SIGTERM (rc={rc})")
        check(not os.path.exists(sock_path), "socket file removed on shutdown")
        final_ok = journal.verify_chain()
        total = len(journal.read_all())
        check(final_ok is True,
              f"final journal verify_chain() True over {total} events")
        journal.close()
        journal = None
    except Exception:
        print("\nUNEXPECTED ERROR — demo fails closed:")
        traceback.print_exc()
        return 2
    finally:
        if journal is not None:
            journal.close()
        if sidecar is not None and sidecar.poll() is None:
            sidecar.kill()
            sidecar.wait(timeout=5)
        tmp.cleanup()

    print()
    if _FAILURES:
        print(f"M0 SMOKE: FAILED — {len(_FAILURES)}/{_CHECKS} checks failed:")
        for label in _FAILURES:
            print(f"  - {label}")
        return 1
    print(f"M0 SMOKE: ALL CHECKS PASSED ({_CHECKS} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
