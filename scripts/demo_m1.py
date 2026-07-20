#!/usr/bin/env python3
"""HELIXos M1 smoke demo — "M1 SMOKE: Core Foundation" (SPEC-M1, end to end).

Owner: Stage-2 integrator. Builds on demo_m0.py's hardened bus pipeline and
exercises every M1 deliverable against the REAL sidecar subprocess, in order:

 1. config from env (HELIXOS_HMAC_KEY / HELIXOS_ED25519_SK; ephemeral dev
    values, announced loudly, when unset — never hardcoded);
 2. the knotcore sidecar as a subprocess on a temp Unix socket, with the
    health()/abi_version handshake (ADR-002);
 3. three agent strands (krishna, natasha, charlotte) generating triangulated
    instructions (HMACSigner + SidecarBackend) and applying them through
    FSMExecutor.apply() — FSM transitions journaled with the epoch (SPEC-M1 §3);
 4. weaving the AKASH braid: one braid.commit per applied instruction, with
    crossings between strands (natasha's 3rd crosses krishna's tip; charlotte
    crosses both); root printed and recomputed from the journal alone (§6a);
 5. the AKASH anchor: anchor_braid onto the bus, journaled "braid.anchor",
    verify_anchor against the recomputed root; then the SPEC-M1 §5 phase
    policy — bump_generation on all live ptrs (anchor cadence duty) and a
    held pre-bump instruction re-presented -> instruction.rejected/phase;
 6. the braid-root signature: sign_root (Ed25519) + verify_root_signature;
    a wrong root is rejected;
 7. CHAOS: the sidecar is SIGKILLed mid-stream — further applies journal
    backend.unavailable, change no FSM state and raise nothing; the sidecar
    is restarted on the same socket and applies resume;
 8. SNAPSHOT + RECOVERY: write_snapshot (seq, epoch, FSM state, braid root,
    strand tips) and a FRESH executor via FSMExecutor.recover(journal,
    snapshot) reproducing the exact FSM state and braid root (§6c);
 9. PROJECTIVE COLLAPSE: executor.collapse("demo") increments the epoch
    (journaled epoch.collapse), §5 bump of the live ptrs, and a stale-epoch
    executor's apply is refused;
10. final journal verify_chain + canonical event-type tally, then a clean
    sidecar shutdown.

Run from the repo root:  python3 scripts/demo_m1.py
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
from collections import Counter
from dataclasses import asdict
from pathlib import Path

# --- sys.path bootstrap (mirrors root conftest.py; SPEC §1 flat namespace) ---
_ROOT = Path(__file__).resolve().parent.parent
_KERNEL = _ROOT / "aigent-os-kernel"
_CODE_DIRS = (
    _KERNEL / "src" / "runtime",       # ten_squared_fsm.py, executor.py
    _KERNEL / "src" / "memory",        # journal.py, epochs.py, snapshot.py
    _KERNEL / "src" / "BABEL",         # dispatcher.py
    _KERNEL / "src" / "akash",         # braid.py (SPEC-M1 §1)
    _KERNEL / "KNOTstore_bin",         # signers.py, knotcore_sim.py, KNOT_API_WRAPPER.py
    _KERNEL / "KNOTstore_bin" / "sidecar",  # rpc_protocol.py, sidecar_server.py, sidecar_client.py
    _KERNEL / "orchestrator",          # possession.py
)
for _dir in reversed(_CODE_DIRS):
    _path = str(_dir)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from braid import (  # noqa: E402
    Braid,
    anchor_braid,
    sign_root,
    verify_anchor,
    verify_root_signature,
)
from epochs import EpochFence  # noqa: E402
from executor import FSMExecutor  # noqa: E402
from journal import EventJournal  # noqa: E402
from KNOT_API_WRAPPER import (  # noqa: E402
    BUS_RE,
    SidecarBackend,
    generate_triangulated_instruction,
)
from sidecar_client import KnotClient  # noqa: E402
from signers import (  # noqa: E402
    Ed25519Signer,
    Ed25519Verifier,
    HMACSigner,
    HMACVerifier,
)
from snapshot import load_snapshot, write_snapshot  # noqa: E402
from ten_squared_fsm import TenSquaredFSM  # noqa: E402

_SIDECAR_SERVER = _KERNEL / "KNOTstore_bin" / "sidecar" / "sidecar_server.py"
_STEPS = 10

# The canonical M1 journal event taxonomy (SPEC-M1 §4) — the demo journals
# ONLY these types, and the final tally asserts full coverage.
_M1_EVENT_TYPES = frozenset(
    {
        "fsm.transition",
        "instruction.rejected",
        "backend.unavailable",
        "braid.commit",
        "braid.anchor",
        "epoch.collapse",
        "snapshot.written",
    }
)

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
    print(f"\n[{index:2d}/{_STEPS}] {title}")
    print("      " + "-" * (len(title) + 4))


def ptr_of(line: str) -> str:
    return BUS_RE.fullmatch(line).group("ptr")  # type: ignore[union-attr]


def start_sidecar(sock_path: str, log_path: str) -> subprocess.Popen:
    """Spawn the knotcore sidecar on ``sock_path``; return the Popen handle."""
    log = open(log_path, "a", encoding="utf-8")
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, str(_SIDECAR_SERVER), sock_path],
        stdout=log,
        stderr=subprocess.STDOUT,
        env={**os.environ, "HELIXOS_SIDECAR_SOCKET": sock_path},
    )
    return proc


def await_handshake(proc: subprocess.Popen, sock_path: str, timeout: float = 15.0):
    """Probe health()/abi_version with throwaway clients until the server is up.

    Fresh client per attempt (demo_m0 pattern): startup-race failures must not
    latch the circuit breaker of the backend the demo actually uses.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return None, None  # died during startup
        try:
            probe = KnotClient(sock_path, timeout=1.0)
            return probe.health(), probe.abi_version()
        except Exception:
            time.sleep(0.1)
    return None, None


def weave(journal, braid, *, seq, strand, line, crossings, epoch):
    """Commit one applied instruction into the braid and journal the event.

    The journal payload carries the full commitment record (including ``prev``)
    so Braid.from_events can recompute the root from the journal ALONE with
    every hash re-validated (SPEC-M1 §6a); FSMExecutor.recover needs only
    seq/strand/bus_line/crossings for its re-commit replay.
    """
    commitment = braid.commit(seq, strand, line, crossings=crossings or None)
    journal.append(
        "braid.commit",
        {**asdict(commitment), "root": braid.root()},
        epoch=epoch,
    )
    return commitment


def main() -> int:
    print("=" * 74)
    print("HELIXos M1 SMOKE: Core Foundation — TEN-SQUARED FSM executor over the")
    print("hardened bus with verifiable AKASH braid signatures (SPEC-M1)")
    print("=" * 74)

    journal: EventJournal | None = None
    sidecar: subprocess.Popen | None = None
    tmp = tempfile.TemporaryDirectory(prefix="helixos-m1-demo-")
    try:
        sock_path = os.path.join(tmp.name, "knotcore.sock")
        journal_path = os.path.join(tmp.name, "demo-journal.jsonl")
        snapshot_path = os.path.join(tmp.name, "demo-snapshot.json")
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
            print(f"      HELIXOS_HMAC_KEY: loaded from environment ({len(key)} bytes)")
        else:
            key = secrets.token_bytes(32)
            print("      !! WARNING: HELIXOS_HMAC_KEY is NOT SET — generated an")
            print("      !! EPHEMERAL 32-byte dev key for this run only (never stored).")
        signer, verifier = HMACSigner(key), HMACVerifier(key)

        ed_hex = os.environ.get("HELIXOS_ED25519_SK", "").strip()
        if ed_hex:
            try:
                ed_seed = bytes.fromhex(ed_hex)
                ed_signer = Ed25519Signer(ed_seed)
            except ValueError as exc:
                print(f"      ERROR: HELIXOS_ED25519_SK invalid: {exc}")
                return 1
            print("      HELIXOS_ED25519_SK: loaded from environment (32-byte seed)")
        else:
            ed_signer = Ed25519Signer.generate()
            print("      !! WARNING: HELIXOS_ED25519_SK is NOT SET — generated an")
            print("      !! EPHEMERAL Ed25519 root-signing identity for this run only;")
            print("      !! root signatures below cannot be verified after exit.")
        ed_verifier = Ed25519Verifier(ed_signer.public_key_bytes())

        fence = EpochFence(0)
        journal = EventJournal(journal_path)
        fsm = TenSquaredFSM()
        braid = Braid()
        print(f"      EventJournal (system of record, ADR-001): {journal_path}")
        print(f"      EpochFence at epoch {fence.current}; TEN-SQUARED FSM at {fsm.state}")

        # ------------------------------------------------- 2. sidecar startup
        step(2, "knotcore sidecar subprocess + ABI handshake (ADR-002)")
        sidecar = start_sidecar(sock_path, sidecar_log_path)
        print(f"      sidecar pid={sidecar.pid} socket={sock_path}")
        health, abi = await_handshake(sidecar, sock_path)
        ok = (
            isinstance(health, dict)
            and health.get("status") == "ok"
            and health.get("abi_version") == 1
            and abi == 1
        )
        check(ok, f"health()/abi_version handshake: health={health} abi_version={abi}")
        if not ok:
            print(Path(sidecar_log_path).read_text(encoding="utf-8"))
            return 1
        # Short breaker reset so the chaos resume (step 7) exercises the
        # half-open probe instead of waiting out the 30 s default.
        backend = SidecarBackend(sock_path, timeout=1.0, breaker_reset_s=1.0)
        executor = FSMExecutor(fsm, journal, fence, verifier, backend)
        # Separate admin client for the SPEC-M1 §5 cadence duty (bump_generation
        # is deliberately NOT on the KnotBackend protocol the executor sees).
        admin = KnotClient(sock_path, timeout=1.0)

        # ------------------------------- 3. agent strands generate + apply
        step(3, "Agent strands krishna/natasha/charlotte — generate + apply (FSMExecutor)")
        plan = [  # (strand, payload) — application order; crossings chosen in step 4
            ("krishna", "krishna: compile the morning ledger"),
            ("krishna", "krishna: attest vault inbox sweep"),
            ("krishna", "krishna: bless the braid anchor cadence"),
            ("natasha", "natasha: spin web partition #t-7"),
            ("natasha", "natasha: index ledger deltas"),
            ("natasha", "natasha: weave cross-ledger summary"),
            ("charlotte", "charlotte: reconcile spider caches"),
            ("charlotte", "charlotte: pin weekly archive set"),
        ]
        applied: list[tuple[str, str, str]] = []  # (strand, bus_line, event)
        for strand, payload in plan:
            line = generate_triangulated_instruction(payload, "EXEC", signer, backend)
            applied_ok = executor.apply(line, strand=strand)
            if not check(applied_ok, f"apply({strand}) -> True: {payload!r}"):
                return 1
            transition = journal.read_all()[-1]
            applied.append((strand, line, transition["payload"]["event"]))
        check(
            fsm.state != "S00",
            f"TEN-SQUARED FSM advanced to {fsm.state} after {len(applied)} applies",
        )
        transitions = [e for e in journal.read_all() if e["type"] == "fsm.transition"]
        check(
            len(transitions) == len(applied)
            and all(t["epoch"] == fence.current for t in transitions),
            f"{len(transitions)} fsm.transition events journaled at epoch {fence.current}",
        )
        seq_of_line = {e["payload"]["bus_line"]: e["seq"] for e in transitions}

        # ------------------------------------- 4. weave the braid (crossings)
        step(4, "Weave the AKASH braid — crossings between strands (SPEC-M1 §1)")
        crossing_plan = {  # applied-index -> other strands to cross at commit time
            5: ["krishna"],                    # natasha's 3rd commit crosses krishna's tip
            6: ["krishna", "natasha"],         # charlotte crosses BOTH
            7: ["krishna", "natasha"],         # charlotte's 2nd crosses both again
        }
        for i, (strand, line, _event) in enumerate(applied):
            others = crossing_plan.get(i, [])
            crossings = {name: braid.tip(name) for name in others}
            commitment = weave(
                journal, braid,
                seq=seq_of_line[line], strand=strand, line=line,
                crossings=crossings, epoch=fence.current,
            )
            note = f" crosses {sorted(crossings)}" if crossings else ""
            print(f"      {strand:9s} seq={commitment.seq:2d} tip={commitment.hash[:12]}…{note}")
        root = braid.root()
        print(f"      strands: {braid.strands()}")
        print(f"      braid root: {root}")
        check(len(braid.to_list()) == len(applied), f"{len(applied)} commitments woven")
        natasha_third = braid.to_list()[5]
        check(
            natasha_third.strand == "natasha"
            and set(natasha_third.crossings) == {"krishna"},
            "natasha's 3rd commit crosses krishna's tip",
        )
        check(
            all(set(braid.to_list()[i].crossings) == {"krishna", "natasha"} for i in (6, 7)),
            "charlotte's commits cross both krishna and natasha",
        )
        # §6(a): the root is recomputable from the journal ALONE, every hash
        # re-validated (from_events raises BraidError on any tamper).
        rebuilt = Braid.from_events(journal.read_all())
        check(
            rebuilt.root() == root,
            "braid root recomputed from journal alone == live root (§6a)",
        )

        # ------------------------------------ 5. AKASH anchor + §5 phase bump
        step(5, "AKASH braid signature anchor on the bus + SPEC-M1 §5 phase policy")
        held_line = generate_triangulated_instruction(
            "held: pre-anchor instruction (phase-0)", "EXEC", signer, backend
        )  # generated pre-bump, deliberately NOT applied — the §5 exhibit
        seq_hi = journal.read_all()[-1]["seq"]
        anchor_line = anchor_braid(braid, signer, backend, seq_lo=1, seq_hi=seq_hi)
        print(f"      anchor: {anchor_line}")
        check(BUS_RE.fullmatch(anchor_line) is not None, "anchor is a triangulated ARCHIVE bus line")
        check(
            verify_anchor(anchor_line, verifier, rebuilt.root(), backend),
            "verify_anchor(anchor, recomputed root) is True (§6b)",
        )
        check(
            verify_anchor(anchor_line, verifier, "0" * 64, backend) is False,
            "verify_anchor against a WRONG root fails closed",
        )
        live_ptrs = [ptr_of(line) for _s, line, _e in applied]
        live_ptrs += [ptr_of(held_line), ptr_of(anchor_line)]
        bumped = {ptr: admin.bump_generation(ptr) for ptr in live_ptrs}  # §5 anchor cadence
        journal.append(
            "braid.anchor",
            {
                "line": anchor_line,
                "root": root,
                "seq_lo": 1,
                "seq_hi": seq_hi,
                "strands": len(braid.strands()),
                "bumped_ptrs": len(bumped),  # SPEC-M1 §5: anchor cadence bumped all live ptrs
            },
            epoch=fence.current,
        )
        check(
            len(bumped) == len(live_ptrs) and all(g == 1 for g in bumped.values()),
            f"SPEC-M1 §5: bump_generation on all {len(live_ptrs)} live ptrs (anchor cadence)",
        )
        fsm_before_held = fsm.state
        check(
            executor.apply(held_line, strand="krishna") is False,
            "pre-bump instruction re-presented post-anchor is refused (forward security)",
        )
        rejected = [e for e in journal.read_all() if e["type"] == "instruction.rejected"]
        check(
            len(rejected) == 1 and rejected[0]["payload"]["reason"] == "phase",
            "rejection journaled as instruction.rejected with reason 'phase'",
        )
        check(fsm.state == fsm_before_held, "FSM state unchanged by the rejected instruction")

        # --------------------------------------------- 6. braid-root signature
        step(6, "Braid-root signature — Ed25519 over HELIX-BRAID/1 || root (SPEC-M1 §1)")
        root_sig = sign_root(root, ed_signer)
        print(f"      root signature: {root_sig[:32]}… ({len(root_sig)} b64url chars)")
        check(
            verify_root_signature(root, root_sig, ed_verifier) is True,
            "verify_root_signature(root, sig) is True",
        )
        check(
            verify_root_signature("0" * 64, root_sig, ed_verifier) is False,
            "signature over a WRONG root is rejected",
        )
        check(
            verify_root_signature(root, root_sig[:-1] + ("A" if root_sig[-1] != "A" else "B"), ed_verifier)
            is False,
            "bit-flipped signature is rejected",
        )

        # --------------------------------------- 7. CHAOS: kill the sidecar
        step(7, "CHAOS — SIGKILL the sidecar mid-stream; restart; applies resume")
        flow = [
            ("krishna", "krishna: pre-chaos heartbeat"),
            ("natasha", "natasha: pre-chaos snapshot note"),
            ("charlotte", "charlotte: pre-chaos cache flush"),
            ("krishna", "krishna: mid-stream victim"),
            ("natasha", "natasha: mid-stream victim"),
            ("charlotte", "charlotte: mid-stream victim"),
        ]
        flow_lines = [
            (strand, generate_triangulated_instruction(payload, "EXEC", signer, backend))
            for strand, payload in flow
        ]
        woven_pre_kill = 0
        for strand, line in flow_lines[:3]:  # stream is flowing...
            if not check(executor.apply(line, strand=strand), f"pre-kill apply({strand}) -> True"):
                return 1
            weave(
                journal, braid,
                seq=journal.read_all()[-1]["seq"], strand=strand, line=line,
                crossings={}, epoch=fence.current,
            )
            woven_pre_kill += 1
        state_before_kill = fsm.state
        events_before_kill = len(journal.read_all())

        sidecar.kill()  # SIGKILL — no graceful shutdown, socket file left behind
        rc = sidecar.wait(timeout=10)
        print(f"      sidecar SIGKILLed (rc={rc}); attempting 3 more applies...")
        chaos_ok = True
        for strand, line in flow_lines[3:]:
            try:
                result = executor.apply(line, strand=strand)  # must NOT raise
            except Exception as exc:  # noqa: BLE001 - the chaos contract is "no exception"
                print(f"      !! apply RAISED during outage: {exc!r}")
                result = None
            chaos_ok = chaos_ok and (result is False)
        check(chaos_ok, "3 mid-stream applies during outage -> False, NO exception")
        down_events = [
            e for e in journal.read_all() if e["type"] == "backend.unavailable"
        ]
        check(
            len(down_events) == 3,
            "3 backend.unavailable events journaled (SPEC-M1 §3 chaos contract)",
        )
        check(
            fsm.state == state_before_kill,
            "FSM state UNCHANGED through the outage (no corruption)",
        )
        check(
            len(journal.read_all()) == events_before_kill + 3,
            "nothing but backend.unavailable was journaled during the outage",
        )

        print("      restarting sidecar on the SAME socket path...")
        sidecar = start_sidecar(sock_path, sidecar_log_path)
        health, abi = await_handshake(sidecar, sock_path)
        check(
            isinstance(health, dict) and health.get("status") == "ok" and abi == 1,
            f"sidecar restarted (pid={sidecar.pid}), handshake ok (stale socket reclaimed)",
        )
        time.sleep(1.2)  # let the circuit breaker's half-open window open (reset 1.0 s)
        resumed_ok = True
        post_restart_ptrs: list[str] = []
        for strand, payload in (
            ("krishna", "krishna: post-restart heartbeat"),
            ("charlotte", "charlotte: post-restart resume note"),
        ):
            line = generate_triangulated_instruction(payload, "EXEC", signer, backend)
            post_restart_ptrs.append(ptr_of(line))
            resumed_ok = resumed_ok and executor.apply(line, strand=strand) is True
            weave(
                journal, braid,
                seq=journal.read_all()[-1]["seq"], strand=strand, line=line,
                crossings={}, epoch=fence.current,
            )
        check(resumed_ok, "applies RESUME after restart (breaker half-open probe closed)")
        check(
            len(braid.to_list()) == len(applied) + woven_pre_kill + 2,
            "braid grew across the outage: pre-kill + post-restart commitments intact",
        )

        # ------------------------------------------ 8. snapshot + recovery
        step(8, "SNAPSHOT + RECOVERY — fresh executor from snapshot + journal (§6c)")
        snap_seq = journal.read_all()[-1]["seq"]
        snap = write_snapshot(
            snapshot_path,
            seq=snap_seq,
            epoch=fence.current,
            fsm_state=fsm.state,
            braid_root=braid.root(),
            strand_tips={s: braid.tip(s) for s in braid.strands()},
            journal_path=journal_path,
            signer=ed_signer,
        )
        print(f"      snapshot: seq={snap['seq']} epoch={snap['epoch']} "
              f"fsm={snap['fsm_state']} root={snap['braid_root'][:12]}…")
        check(snap.get("sig"), "snapshot carries an Ed25519 braid-root signature")
        try:
            loaded = load_snapshot(snapshot_path, verifier=ed_verifier)
            snap_ok = loaded["braid_root"] == braid.root()
        except Exception as exc:  # noqa: BLE001
            print(f"      !! load_snapshot raised: {exc!r}")
            snap_ok = False
        check(snap_ok, "load_snapshot validates and the root signature verifies")
        journal.append(
            "snapshot.written",
            {
                "path": snapshot_path,
                "seq": snap_seq,
                "epoch": fence.current,
                "fsm_state": fsm.state,
                "braid_root": braid.root(),
            },
            epoch=fence.current,
        )
        original_state, original_root, original_epoch = fsm.state, braid.root(), fence.current
        # Close the original journal object: recovery opens its own handle on
        # the same path, and only ONE writer may append from here on (the
        # recovered executor), else the chain would fork.
        journal.close()
        journal = None
        fresh_braid = Braid()
        recovered = FSMExecutor.recover(
            journal_path, fence, verifier, backend,
            snapshot_path=snapshot_path, braid=fresh_braid,
        )
        check(
            recovered.fsm.state == original_state,
            f"recovered FSM state == original ({original_state})",
        )
        check(
            fresh_braid.root() == original_root,
            "recovered braid root == original root (rebuilt from journal commits)",
        )
        check(
            recovered.epoch == original_epoch,
            f"recovered executor epochs at {original_epoch} (fence carried over)",
        )
        check(
            Braid.from_events(recovered.journal.read_all()).root() == original_root,
            "journal-alone root recompute still matches after recovery (§6a)",
        )

        # ------------------------------------------ 9. projective collapse
        step(9, "PROJECTIVE COLLAPSE — epoch increments; stale-epoch apply refused")
        new_epoch = recovered.collapse("demo")
        check(
            new_epoch == original_epoch + 1 == fence.current,
            f"collapse -> epoch {original_epoch} -> {new_epoch} (fence shared)",
        )
        collapse_events = [
            e for e in recovered.journal.read_all() if e["type"] == "epoch.collapse"
        ]
        check(
            len(collapse_events) == 1
            and collapse_events[0]["epoch"] == new_epoch
            and collapse_events[0]["payload"]["reason"] == "demo",
            "epoch.collapse journaled under the NEW epoch (ADR-001 §2.3 order)",
        )
        # SPEC-M1 §5 collapse duty: bump every live ptr (post-restart store).
        bumped_live = {p: admin.bump_generation(p) for p in post_restart_ptrs}
        check(
            len(bumped_live) == len(post_restart_ptrs),
            f"SPEC-M1 §5: collapse bumped all {len(bumped_live)} live ptrs",
        )
        stale_line = generate_triangulated_instruction(
            "stale-epoch attempt", "EXEC", signer, backend
        )
        events_before_stale = len(recovered.journal.read_all())
        check(
            executor.apply(stale_line, strand="krishna") is False,
            "stale-epoch executor apply REFUSED after collapse (split-brain fence)",
        )
        check(
            len(recovered.journal.read_all()) == events_before_stale
            and fsm.state == original_state,
            "refused apply journaled NOTHING and changed no FSM state",
        )

        # --------------------------------- 10. final chain verify + shutdown
        step(10, "Final journal verify + canonical event tally + clean shutdown")
        final_chain = recovered.journal.verify_chain()
        check(final_chain is True, "final journal verify_chain() is True")
        events = recovered.journal.read_all()
        tally = Counter(e["type"] for e in events)
        print("      event-type tally (canonical M1 set, SPEC-M1 §4):")
        for etype in sorted(tally):
            print(f"        {etype:22s} {tally[etype]}")
        check(
            set(tally) == set(_M1_EVENT_TYPES),
            f"all {len(_M1_EVENT_TYPES)} canonical M1 event types present, no others",
        )
        check(
            Braid.from_events(events).root() == braid.root(),
            "final braid root recompute from the full journal == live root",
        )
        recovered.journal.close()
        sidecar.terminate()  # SIGTERM -> graceful shutdown path
        rc = sidecar.wait(timeout=10)
        check(rc == 0, f"sidecar exited cleanly on SIGTERM (rc={rc})")
        check(not os.path.exists(sock_path), "socket file removed on shutdown")
        sidecar = None
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
        print(f"M1 SMOKE: FAILED — {len(_FAILURES)}/{_CHECKS} checks failed:")
        for label in _FAILURES:
            print(f"  - {label}")
        return 1
    print(f"M1 SMOKE: ALL CHECKS PASSED ({_CHECKS} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
