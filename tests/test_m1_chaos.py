"""M1 acceptance suite — soak, chaos, phase policy, recovery equivalence (SPEC-M1 §6).

The Stage-2 integrator's acceptance core for Milestone 1. Each test maps to a
SPEC-M1 §6 acceptance criterion (docs/m1-acceptance.md carries the mapping):

- ``test_soak``                        §6e — 10k instructions, zero chain/root mismatches
- ``test_sidecar_kill_resume``         §6d — SIGKILL mid-stream: no corruption, resume works
- ``test_anchor_after_bump``           §6b + §5 — anchor verifies; bumped generation
                                        invalidates pre-bump instructions (reason "phase")
- ``test_recover_equivalence``         §6a/§6c — journal-only recover reproduces exact
                                        FSM state, braid root and epoch
- ``test_tampered_journal_blocks_recover``  §6c — hash-chain tamper => recover raises
- ``test_braid_root_tamper_evidence``  §6a — a tampered braid.commit => BraidError
"""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

import pytest

import knotcore_sim
import KNOT_API_WRAPPER as W
from braid import Braid, BraidError, anchor_braid, verify_anchor
from epochs import EpochFence
from executor import FSMExecutor, event_for
from journal import EventJournal
from KNOT_API_WRAPPER import SimBackend, generate_triangulated_instruction
from sidecar_client import KnotClient
from signers import HMACSigner, HMACVerifier
from snapshot import SnapshotError
from ten_squared_fsm import TenSquaredFSM

# TEST-ONLY dev key material (never production; secrets come from env per SPEC §3.10).
HMAC_KEY = bytes(range(32))

_PTR_OF = re.compile(r"^\[ (?P<ptr>[0-9a-f]{16})")
_SIDECAR_SERVER = (
    Path(__file__).resolve().parent.parent
    / "aigent-os-kernel" / "KNOTstore_bin" / "sidecar" / "sidecar_server.py"
)
_STRANDS = ("krishna", "natasha", "charlotte")

# §6e soak size: 10k instructions (measured ~17 s on the dev box, inside the
# 20 s budget); override with HELIXOS_SOAK_N for slower/faster environments.
SOAK_N = int(os.environ.get("HELIXOS_SOAK_N", "10000"))


def _ptr_of(line: str) -> str:
    return _PTR_OF.match(line).group("ptr")  # type: ignore[union-attr]


@pytest.fixture(autouse=True)
def clean_state():
    """Isolate the simulator store and the wrapper replay cache between tests."""
    knotcore_sim.reset_store()
    with W._seen_lock:
        W._seen_nonces.clear()
    yield
    knotcore_sim.reset_store()
    with W._seen_lock:
        W._seen_nonces.clear()


@pytest.fixture
def signer():
    return HMACSigner(HMAC_KEY)


@pytest.fixture
def verifier():
    return HMACVerifier(HMAC_KEY)


@pytest.fixture
def backend():
    return SimBackend()


@pytest.fixture
def journal_path(tmp_path):
    return tmp_path / "journal.jsonl"


@pytest.fixture
def journal(journal_path):
    j = EventJournal(journal_path)
    yield j
    j.close()


@pytest.fixture
def fence():
    return EpochFence()


def make_line(signer, backend, payload: str = "hello", verb: str = "EXEC", **hooks) -> str:
    return generate_triangulated_instruction(payload, verb, signer, backend, **hooks)


def _rebuild_braid(events) -> tuple[Braid, int]:
    """Re-commit every journaled braid.commit payload into a fresh braid.

    Returns (braid, mismatches) where mismatches counts journaled commitment
    hashes that do not equal the recomputed commitment hash. This is the
    journal-alone root recompute behind SPEC-M1 §6a/§6e; unlike
    Braid.from_events it also accepts the executor's braid.commit payload
    shape (which omits ``prev``), so it can check executor-woven journals.
    """
    rebuilt = Braid()
    mismatches = 0
    for record in events:
        if record["type"] != "braid.commit":
            continue
        payload = record["payload"]
        commitment = rebuilt.commit(
            int(payload["seq"]),
            str(payload["strand"]),
            str(payload["bus_line"]),
            payload.get("crossings") or None,
        )
        if commitment.hash != payload["hash"]:
            mismatches += 1
    return rebuilt, mismatches


def _await_sidecar(proc: subprocess.Popen, sock_path: str, timeout: float = 15.0) -> None:
    """Wait until the sidecar answers health()/abi_version (fresh probe clients)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"sidecar exited during startup (rc={proc.returncode})")
        try:
            probe = KnotClient(sock_path, timeout=1.0)
            health, abi = probe.health(), probe.abi_version()
            if isinstance(health, dict) and health.get("status") == "ok" and abi == 1:
                return
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"sidecar did not come up on {sock_path}")


def _start_sidecar(sock_path: str, log_path: str) -> subprocess.Popen:
    log = open(log_path, "a", encoding="utf-8")
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, str(_SIDECAR_SERVER), sock_path],
        stdout=log,
        stderr=subprocess.STDOUT,
        env={**os.environ, "HELIXOS_SIDECAR_SOCKET": sock_path},
    )
    _await_sidecar(proc, sock_path)
    return proc


# ---------------------------------------------------------------------------
# §6e — soak: N instructions, zero chain/root mismatches
# ---------------------------------------------------------------------------


def test_soak(signer, verifier, backend, journal, journal_path, fence):
    """§6e: SOAK_N instructions across 3 strands through SimBackend.

    Final state: verify_chain True, the braid root recomputed from the journal
    alone equals the live Braid.root, and ZERO commitment-hash mismatches; a
    reference FSM driven by event_for reproduces the executor's FSM state.
    """
    fsm = TenSquaredFSM()
    braid = Braid()
    executor = FSMExecutor(fsm, journal, fence, verifier, backend, braid=braid)
    reference = TenSquaredFSM()

    started = time.perf_counter()
    for i in range(SOAK_N):
        line = make_line(signer, backend, payload=f"soak-{i}")
        assert executor.apply(line, strand=_STRANDS[i % 3]) is True
        reference.transition(event_for(line))
    elapsed = time.perf_counter() - started

    events = journal.read_all()
    assert sum(1 for e in events if e["type"] == "fsm.transition") == SOAK_N
    assert sum(1 for e in events if e["type"] == "braid.commit") == SOAK_N
    assert fsm.state == reference.state  # zero FSM drift across the soak
    assert journal.verify_chain() is True

    rebuilt, mismatches = _rebuild_braid(events)
    assert mismatches == 0
    assert rebuilt.root() == braid.root()
    print(f"\nsoak: {SOAK_N} instructions in {elapsed:.1f}s "
          f"({SOAK_N / elapsed:.0f}/s), root={braid.root()[:16]}…")


# ---------------------------------------------------------------------------
# §6d — sidecar SIGKILL mid-stream: fail-closed outage, clean resume
# ---------------------------------------------------------------------------


def test_sidecar_kill_resume(tmp_path, signer, verifier, journal, journal_path, fence):
    """§6d: real sidecar subprocess; 50 applies, SIGKILL, 10 more all fail
    closed (backend.unavailable journaled, no state change, no exception);
    restart on the same socket; 50 fresh applies succeed; chain valid."""
    sock_path = str(tmp_path / "knotcore.sock")
    log_path = str(tmp_path / "sidecar.log")
    sidecar = _start_sidecar(sock_path, log_path)
    try:
        backend = W.SidecarBackend(sock_path, timeout=1.0, breaker_reset_s=1.0)
        fsm = TenSquaredFSM()
        executor = FSMExecutor(fsm, journal, fence, verifier, backend)

        pre_kill = [make_line(signer, backend, payload=f"pre-{i}") for i in range(50)]
        held = [make_line(signer, backend, payload=f"held-{i}") for i in range(10)]
        for line in pre_kill:
            assert executor.apply(line, strand="krishna") is True
        state_before_kill = fsm.state

        sidecar.kill()  # SIGKILL — the mid-stream crash
        sidecar.wait(timeout=10)

        outage_results = []
        for line in held:
            try:
                outage_results.append(executor.apply(line, strand="krishna"))
            except Exception as exc:  # noqa: BLE001 - the contract is "no exception"
                pytest.fail(f"apply raised during outage: {exc!r}")
        assert outage_results == [False] * 10
        assert fsm.state == state_before_kill  # NO state corruption
        down = [e for e in journal.read_all() if e["type"] == "backend.unavailable"]
        assert len(down) == 10
        # nothing but backend.unavailable was journaled during the outage
        assert [e["type"] for e in journal.read_all()[-10:]] == ["backend.unavailable"] * 10

        sidecar = _start_sidecar(sock_path, log_path)  # same socket path
        time.sleep(1.2)  # breaker half-open window (reset 1.0 s)

        post = [make_line(signer, backend, payload=f"post-{i}") for i in range(50)]
        for line in post:
            assert executor.apply(line, strand="krishna") is True

        assert journal.verify_chain() is True
        types = [e["type"] for e in journal.read_all()]
        assert types.count("fsm.transition") == 100
        assert types.count("backend.unavailable") == 10
        assert "instruction.rejected" not in types  # outage is NOT a rejection
    finally:
        if sidecar.poll() is None:
            sidecar.kill()
            sidecar.wait(timeout=5)


# ---------------------------------------------------------------------------
# §6b + §5 — anchor verifies; generation bump invalidates pre-bump instructions
# ---------------------------------------------------------------------------


def test_anchor_after_bump(signer, verifier, backend, journal, fence):
    """§5/§6b: anchor -> verify_anchor True -> bump_generation on all live
    instruction ptrs (anchor cadence, via knotcore_sim directly) -> a held
    pre-bump instruction is now rejected with reason "phase" while a freshly
    minted instruction still applies (intended forward security)."""
    fsm = TenSquaredFSM()
    braid = Braid()
    executor = FSMExecutor(fsm, journal, fence, verifier, backend, braid=braid)

    ptrs: list[str] = []
    for i in range(5):
        line = make_line(signer, backend, payload=f"anchor-run-{i}")
        ptrs.append(_ptr_of(line))
        assert executor.apply(line, strand=_STRANDS[i % 3]) is True
    held_line = make_line(signer, backend, payload="held pre-bump instruction")
    ptrs.append(_ptr_of(held_line))

    seq_hi = journal.read_all()[-1]["seq"]
    anchor_line = anchor_braid(braid, signer, backend, seq_lo=1, seq_hi=seq_hi)
    journal.append(
        "braid.anchor",
        {"line": anchor_line, "root": braid.root(), "seq_lo": 1, "seq_hi": seq_hi},
        epoch=fence.current,
    )
    assert verify_anchor(anchor_line, verifier, braid.root(), backend) is True
    assert verify_anchor(anchor_line, verifier, "0" * 64, backend) is False

    # SPEC-M1 §5 anchor cadence: bump the generation of every live ptr.
    for ptr in ptrs + [_ptr_of(anchor_line)]:
        assert knotcore_sim.bump_generation(ptr) == 1

    # Re-presenting the pre-bump (never-applied, nonce-fresh) instruction now
    # fails the cauldron phase check: treated as Tampered, journaled
    # instruction.rejected with reason "phase", no state change.
    state_before = fsm.state
    assert executor.apply(held_line, strand="krishna") is False
    assert fsm.state == state_before
    rejected = [e for e in journal.read_all() if e["type"] == "instruction.rejected"]
    assert len(rejected) == 1
    assert rejected[0]["payload"]["reason"] == "phase"

    # ...while a freshly minted (phase-current) instruction still applies.
    assert executor.apply(make_line(signer, backend, payload="post-bump fresh")) is True
    # ...and the old anchor itself no longer verifies (phase moved on).
    assert verify_anchor(anchor_line, verifier, braid.root(), backend) is False
    assert journal.verify_chain() is True


# ---------------------------------------------------------------------------
# §6a/§6c — randomized run: journal-only recovery reproduces everything
# ---------------------------------------------------------------------------


def test_recover_equivalence(signer, verifier, backend, journal, journal_path, tmp_path):
    """§6a/§6c: randomized 200-instruction run (3 strands, rejections, one
    collapse with the §5 bump); FSMExecutor.recover from the JOURNAL ONLY
    reproduces identical FSM state, braid root and epoch."""
    rng = random.Random(0x1E1F)  # fixed seed: deterministic acceptance run
    fence = EpochFence()
    fsm = TenSquaredFSM()
    braid = Braid()
    executor = FSMExecutor(fsm, journal, fence, verifier, backend, braid=braid)
    reference = TenSquaredFSM()
    ptrs: list[str] = []

    for i in range(200):
        if i % 17 == 16:  # intersperse malformed instructions (rejected, no FSM effect)
            assert executor.apply(f"not-a-bus-line-{i}", strand="natasha") is False
            continue
        line = make_line(signer, backend, payload=f"run-{rng.randrange(1 << 30)}")
        ptrs.append(_ptr_of(line))
        assert executor.apply(line, strand=rng.choice(_STRANDS)) is True
        reference.transition(event_for(line))
        if i == 99:  # mid-run Projective Collapse (+ SPEC-M1 §5 live-ptr bump)
            assert executor.collapse("mid-run collapse") == 1
            for ptr in ptrs:
                knotcore_sim.bump_generation(ptr)
    assert fence.current == 1
    journal_before = journal.read_all()

    fresh_braid = Braid()
    recovered = FSMExecutor.recover(
        journal_path, EpochFence(fence.current), verifier, backend, braid=fresh_braid
    )

    assert recovered.fsm.state == fsm.state == reference.state
    assert fresh_braid.root() == braid.root()
    assert recovered.epoch == fence.current == 1
    assert recovered.journal.read_all() == journal_before  # recovery wrote nothing
    # and the journal-alone recompute (from_events, full hash re-validation)
    # agrees — executor payloads omit prev, so use the re-commit recompute.
    rebuilt, mismatches = _rebuild_braid(journal_before)
    assert mismatches == 0
    assert rebuilt.root() == braid.root()


# ---------------------------------------------------------------------------
# §6c — a tampered journal blocks recovery (fail-closed)
# ---------------------------------------------------------------------------


def test_tampered_journal_blocks_recover(
    signer, verifier, backend, journal, journal_path, fence
):
    """§6c: flipping one byte in a middle journal line breaks the hash chain;
    recover() must raise (SnapshotError) rather than replay partial state."""
    fsm = TenSquaredFSM()
    braid = Braid()
    executor = FSMExecutor(fsm, journal, fence, verifier, backend, braid=braid)
    for i in range(10):
        assert executor.apply(make_line(signer, backend, payload=f"t-{i}")) is True
    journal.close()

    lines = journal_path.read_text(encoding="utf-8").splitlines(keepends=True)
    middle = json.loads(lines[len(lines) // 2])
    old_hash = middle["hash"]
    flipped = ("0" if old_hash[0] != "0" else "1") + old_hash[1:]
    lines[len(lines) // 2] = lines[len(lines) // 2].replace(
        f'"hash":"{old_hash}"', f'"hash":"{flipped}"'
    )
    assert flipped != old_hash  # the flip actually changed the line
    journal_path.write_text("".join(lines), encoding="utf-8")

    probe = EventJournal(journal_path)
    assert probe.verify_chain() is False
    probe.close()
    with pytest.raises(SnapshotError):
        FSMExecutor.recover(journal_path, EpochFence(), verifier, backend, braid=Braid())


# ---------------------------------------------------------------------------
# §6a — a tampered journaled braid.commit is cryptographic evidence
# ---------------------------------------------------------------------------


def test_braid_root_tamper_evidence(signer, verifier, backend, journal, fence):
    """§6a: braid.commit events journaled with full commitment records are
    tamper-evident INDEPENDENT of the journal chain: Braid.from_events
    re-computes and re-validates every hash, so a flipped bus_line, declared
    hash, crossing or prev raises BraidError."""
    braid = Braid()
    lines = [make_line(signer, backend, payload=f"weave-{i}") for i in range(6)]
    seq = 0

    def weave(strand: str, line: str, crossings: dict[str, str] | None = None):
        nonlocal seq
        seq += 1
        commitment = braid.commit(seq, strand, line, crossings=crossings)
        journal.append(
            "braid.commit", {**asdict(commitment), "root": braid.root()}, epoch=fence.current
        )
        return commitment

    weave("krishna", lines[0])
    weave("natasha", lines[1])
    weave("krishna", lines[2])
    # crossings always reference the CURRENT tip of the other strand
    weave("natasha", lines[3], crossings={"krishna": braid.tip("krishna")})
    weave(
        "charlotte",
        lines[4],
        crossings={"krishna": braid.tip("krishna"), "natasha": braid.tip("natasha")},
    )
    weave("charlotte", lines[5])

    events = journal.read_all()
    assert Braid.from_events(events).root() == braid.root()  # untampered baseline

    def tampered(index: int, mutate) -> list[dict]:
        copied = [dict(e, payload=dict(e["payload"])) for e in events]
        mutate(copied[index]["payload"])
        return copied

    commit_idx = [i for i, e in enumerate(events) if e["type"] == "braid.commit"]

    with pytest.raises(BraidError):  # bus_line swapped: recomputed hash != declared
        Braid.from_events(
            tampered(commit_idx[2], lambda p: p.update(bus_line=lines[5]))
        )
    with pytest.raises(BraidError):  # declared hash flipped: != recomputed
        Braid.from_events(
            tampered(commit_idx[1], lambda p: p.update(hash="f" * 64))
        )
    with pytest.raises(BraidError):  # crossing tip flipped: stale causal reference
        Braid.from_events(
            tampered(commit_idx[3], lambda p: p.update(crossings={"krishna": "e" * 64}))
        )
    with pytest.raises(BraidError):  # prev re-linked: broken chain linkage
        # (commit_idx[5] = charlotte's 2nd commit; its real prev is her 1st tip,
        # so re-linking it to genesis changes the recomputed commitment hash)
        Braid.from_events(
            tampered(commit_idx[5], lambda p: p.update(prev="0" * 64))
        )
