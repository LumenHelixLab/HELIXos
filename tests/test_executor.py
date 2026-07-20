"""Tests for src/runtime/executor.py (SPEC-M1 §3 executor, §4 event types, §5 phase policy).

Covers the six-step apply pipeline (fence -> verify -> event_for -> transition
-> journal -> braid), the fail-closed rejection taxonomy
(invalid|stale|replay|phase), backend-Unavailable chaos behavior (no raise,
no state change, ``backend.unavailable`` journaled), Projective Collapse
(ADR-001 §2.3) and snapshot+journal recovery.

Braid is exercised through a recording fake only — the executor duck-types
the SPEC-M1 §1 interface and never imports akash.braid.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from types import SimpleNamespace

import pytest

import knotcore_sim
import KNOT_API_WRAPPER as W
from KNOT_API_WRAPPER import SimBackend, generate_triangulated_instruction
from epochs import EpochFence
from executor import FSMExecutor, event_for
from journal import EventJournal
from sidecar_client import Unavailable
from signers import HMACSigner, HMACVerifier
from snapshot import SnapshotError, write_snapshot
from ten_squared_fsm import TenSquaredFSM

# TEST-ONLY dev key material (never production; secrets come from env per SPEC §3.10).
HMAC_KEY = bytes(range(32))
WRONG_HMAC_KEY = b"\xff" * 32

PTR_OF = re.compile(r"^\[ (?P<ptr>[0-9a-f]{16})")


def _ptr_of(line: str) -> str:
    return PTR_OF.match(line).group("ptr")  # type: ignore[union-attr]


def _tag_first_byte_event(line: str) -> str:
    """Independent re-derivation of the deterministic event mapping."""
    tag = W.BUS_RE.fullmatch(line).group("tag")  # type: ignore[union-attr]
    raw = base64.urlsafe_b64decode(tag + "=" * (-len(tag) % 4))
    return f"E{raw[0] % 10}"


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


@pytest.fixture
def fsm():
    return TenSquaredFSM()


@pytest.fixture
def executor(fsm, journal, fence, verifier, backend):
    return FSMExecutor(fsm, journal, fence, verifier, backend)


def make_line(signer, backend, payload: str = "hello", verb: str = "EXEC", **hooks) -> str:
    return generate_triangulated_instruction(payload, verb, signer, backend, **hooks)


class ExplodingBackend:
    """KnotBackend stub that is always down (chaos: sidecar killed)."""

    def store_instruction(self, payload: str) -> str:
        raise Unavailable("sidecar down")

    def fetch_payload(self, ptr: str):
        raise Unavailable("sidecar down")

    def verify_cauldron_phase(self, ptr: str, tag: str, phase: int) -> bool:
        raise Unavailable("sidecar down")


class FlakyBackend:
    """SimBackend wrapper whose outage a test can switch on and off."""

    def __init__(self, inner: SimBackend) -> None:
        self._inner = inner
        self.down = True

    def store_instruction(self, payload: str) -> str:
        if self.down:
            raise Unavailable("sidecar down")
        return self._inner.store_instruction(payload)

    def fetch_payload(self, ptr: str):
        if self.down:
            raise Unavailable("sidecar down")
        return self._inner.fetch_payload(ptr)

    def verify_cauldron_phase(self, ptr: str, tag: str, phase: int) -> bool:
        if self.down:
            raise Unavailable("sidecar down")
        return self._inner.verify_cauldron_phase(ptr, tag, phase)


class FakeBraid:
    """Recording stand-in for akash.braid.Braid (SPEC-M1 §1 duck type)."""

    def __init__(self) -> None:
        self.commits: list[tuple[int, str, str, dict]] = []
        self.commitments: list[SimpleNamespace] = []

    def commit(self, seq: int, strand: str, bus_line: str, crossings=None):
        self.commits.append((seq, strand, bus_line, dict(crossings or {})))
        commitment = SimpleNamespace(
            seq=seq,
            strand=strand,
            bus_line=bus_line,
            crossings=dict(crossings or {}),
            hash=hashlib.sha256(f"{seq}:{strand}:{bus_line}".encode()).hexdigest(),
        )
        self.commitments.append(commitment)
        return commitment

    def root(self) -> str:
        return hashlib.sha256(repr(self.commits).encode()).hexdigest()


# ---------------------------------------------------------------------------
# apply: happy path
# ---------------------------------------------------------------------------


class TestApplyHappyPath:
    def test_apply_valid_instruction_returns_true_and_advances_fsm(
        self, executor, fsm, signer, backend
    ):
        line = make_line(signer, backend)
        assert executor.apply(line) is True
        assert fsm.state == TenSquaredFSM().transition(_tag_first_byte_event(line))

    def test_apply_journals_fsm_transition_with_epoch_and_fields(
        self, executor, journal, fence, signer, backend
    ):
        line = make_line(signer, backend)
        assert executor.apply(line, strand="krishna") is True
        events = journal.read_all()
        assert len(events) == 1
        rec = events[0]
        assert rec["seq"] == 1
        assert rec["type"] == "fsm.transition"
        assert rec["epoch"] == fence.current == 0
        payload = rec["payload"]
        assert payload["bus_line"] == line
        assert payload["event"] == _tag_first_byte_event(line)
        assert payload["from"] == "S00"
        assert payload["to"] == fsm_state_of(events)
        assert payload["strand"] == "krishna"

    def test_apply_multiple_instructions_chain_in_order(
        self, executor, fsm, journal, signer, backend
    ):
        reference = TenSquaredFSM()
        for i in range(5):
            line = make_line(signer, backend, payload=f"payload-{i}")
            assert executor.apply(line) is True
            reference.transition(_tag_first_byte_event(line))
        assert fsm.state == reference.state
        assert [e["seq"] for e in journal.read_all()] == [1, 2, 3, 4, 5]
        assert journal.verify_chain() is True


def fsm_state_of(events) -> str:
    return events[-1]["payload"]["to"]


# ---------------------------------------------------------------------------
# event_for
# ---------------------------------------------------------------------------


class TestEventFor:
    def test_deterministic_derivation_from_tag(self, signer, backend):
        line = make_line(signer, backend)
        assert event_for(line) == _tag_first_byte_event(line)

    def test_envelope_body_event_override_wins(self, executor, fsm, journal, signer, backend):
        line = make_line(signer, backend, payload=json.dumps({"event": "E5"}))
        assert executor.apply(line) is True
        assert fsm.state == "S55"  # S00 --E5--> S(5, (0*3+5+0)%10)
        assert journal.read_all()[0]["payload"]["event"] == "E5"

    def test_override_pure_function(self, signer, backend):
        line = make_line(signer, backend)
        assert event_for(line, '{"event":"E3"}') == "E3"
        assert event_for(line, '{"event":"E0"}') == "E0"

    @pytest.mark.parametrize(
        "body",
        [
            '{"event":"E12"}',      # two digits: not E[0-9]
            '{"event":"e5"}',       # lowercase
            '{"event":"X5"}',       # wrong prefix
            '{"event":5}',          # not a str
            '{"other":"E5"}',       # no event member
            "not json at all",
            "",
        ],
    )
    def test_invalid_overrides_fall_back_to_deterministic(self, signer, backend, body):
        line = make_line(signer, backend)
        assert event_for(line, body) == _tag_first_byte_event(line)

    def test_override_none_body_is_deterministic(self, signer, backend):
        line = make_line(signer, backend)
        assert event_for(line, None) == _tag_first_byte_event(line)

    def test_rejects_non_bus_lines(self):
        for bad in ("garbage", "", None, 123, "[ 00 | EXEC | #abc ]"):
            with pytest.raises(ValueError):
                event_for(bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# apply: rejection paths (fail-closed, never raises, no state change)
# ---------------------------------------------------------------------------


class TestRejections:
    def test_invalid_bus_line_rejected(self, executor, fsm, journal):
        assert executor.apply("this is not a bus line") is False
        assert fsm.state == "S00"
        events = journal.read_all()
        assert len(events) == 1
        assert events[0]["type"] == "instruction.rejected"
        assert events[0]["payload"]["reason"] == "invalid"

    def test_apply_never_raises_on_garbage_types(self, executor, fsm, journal):
        for bad in (None, 123, b"bytes", "[ 00 | EXEC | #abc ]"):
            assert executor.apply(bad) is False  # type: ignore[arg-type]
        assert fsm.state == "S00"
        assert all(e["type"] == "instruction.rejected" for e in journal.read_all())

    def test_tampered_tag_rejected_invalid(self, executor, fsm, journal, signer, backend):
        line = make_line(signer, backend)
        tag = W.BUS_RE.fullmatch(line).group("tag")  # type: ignore[union-attr]
        forged_tag = ("A" if tag[0] != "A" else "B") + tag[1:]
        forged = line.replace(f"#{tag}", f"#{forged_tag}")
        assert executor.apply(forged) is False
        assert fsm.state == "S00"
        assert journal.read_all()[0]["payload"]["reason"] == "invalid"

    def test_replay_attack_rejected(self, executor, fsm, journal, signer, backend):
        line = make_line(signer, backend)
        assert executor.apply(line) is True
        state_after_first = fsm.state
        assert executor.apply(line) is False  # same nonce re-presented
        assert fsm.state == state_after_first  # exactly one transition happened
        rejected = [e for e in journal.read_all() if e["type"] == "instruction.rejected"]
        assert len(rejected) == 1
        assert rejected[0]["payload"]["reason"] == "replay"

    def test_wrong_key_verifier_rejected(self, fsm, journal, fence, backend, signer):
        wrong_verifier = HMACVerifier(WRONG_HMAC_KEY)
        executor = FSMExecutor(fsm, journal, fence, wrong_verifier, backend)
        line = make_line(signer, backend)  # signed with HMAC_KEY
        assert executor.apply(line) is False
        assert fsm.state == "S00"
        assert journal.read_all()[0]["payload"]["reason"] == "invalid"

    def test_stale_instruction_rejected(self, executor, fsm, journal, signer, backend):
        stale_ts = time.time() - (W.MAX_CLOCK_SKEW_S + 60)
        line = make_line(signer, backend, _now=stale_ts)  # TEST-ONLY hook
        assert executor.apply(line) is False
        assert fsm.state == "S00"
        assert journal.read_all()[0]["payload"]["reason"] == "stale"

    def test_unknown_ptr_rejected_invalid(self, executor, fsm, journal, signer, backend):
        line = make_line(signer, backend)
        knotcore_sim.reset_store()  # ptr no longer exists; tag now unverifiable too
        forged = line  # well-formed line pointing at a vanished slot
        assert executor.apply(forged) is False
        assert fsm.state == "S00"
        assert journal.read_all()[0]["payload"]["reason"] == "invalid"


# ---------------------------------------------------------------------------
# Backend Unavailable (chaos: sidecar down) — SPEC-M1 §3 final paragraph
# ---------------------------------------------------------------------------


class TestBackendUnavailable:
    def test_down_backend_returns_false_no_raise_no_state_change(
        self, fsm, journal, fence, verifier, signer, backend
    ):
        line = make_line(signer, backend)  # minted while the store was up
        exploding = ExplodingBackend()
        executor = FSMExecutor(fsm, journal, fence, verifier, exploding)
        assert executor.apply(line) is False  # NO exception
        assert fsm.state == "S00"  # NO state change
        events = journal.read_all()
        assert len(events) == 1
        assert events[0]["type"] == "backend.unavailable"
        assert "down" in events[0]["payload"]["error"]

    def test_resume_after_outage(self, fsm, journal, fence, verifier, signer, backend):
        line = make_line(signer, backend)
        flaky = FlakyBackend(backend)
        executor = FSMExecutor(fsm, journal, fence, verifier, flaky)
        assert executor.apply(line) is False  # down: unavailable, nonce NOT burned
        flaky.down = False  # sidecar restarted
        assert executor.apply(line) is True  # same instruction now verifies
        assert fsm.state != "S00"
        types = [e["type"] for e in journal.read_all()]
        assert types == ["backend.unavailable", "fsm.transition"]
        assert journal.verify_chain() is True


# ---------------------------------------------------------------------------
# Epoch fence + Projective Collapse (ADR-001 §2.3, SPEC-M1 §3/§5)
# ---------------------------------------------------------------------------


class TestFenceAndCollapse:
    def test_stale_executor_refused_after_external_collapse(
        self, fsm, journal, fence, verifier, backend, signer
    ):
        audits: list[str] = []
        executor = FSMExecutor(fsm, journal, fence, verifier, backend, audit=audits.append)
        assert executor.apply(make_line(signer, backend)) is True
        state_before = fsm.state
        fence.increment()  # another node's collapse broadcast moved the live epoch
        events_before = len(journal.read_all())
        assert executor.apply(make_line(signer, backend)) is False
        assert fsm.state == state_before  # unchanged
        assert len(journal.read_all()) == events_before  # nothing journaled
        assert any("stale_epoch" in a for a in audits)

    def test_collapse_increments_epoch_and_journals(self, executor, journal, fence):
        new_epoch = executor.collapse("tampered verdict")
        assert new_epoch == 1 == fence.current
        assert executor.epoch == 1
        rec = journal.read_all()[-1]
        assert rec["type"] == "epoch.collapse"
        assert rec["epoch"] == 1  # journaled under the NEW epoch (ADR-001 §2.3 order)
        assert rec["payload"] == {"reason": "tampered verdict", "new_epoch": 1}

    def test_apply_works_after_own_collapse(self, executor, fsm, journal, signer, backend):
        executor.collapse("routine")
        assert executor.apply(make_line(signer, backend)) is True
        assert fsm.state != "S00"
        last = journal.read_all()[-1]
        assert last["type"] == "fsm.transition"
        assert last["epoch"] == 1

    def test_collapse_requires_reason(self, executor):
        with pytest.raises(ValueError):
            executor.collapse("")

    def test_phase_policy_old_instruction_rejected_after_bump(
        self, executor, fsm, journal, signer, backend
    ):
        """SPEC-M1 §5: bump_generation (anchor cadence) invalidates old-phase
        instructions; re-presenting them is logged instruction.rejected/phase."""
        line = make_line(signer, backend)
        assert executor.apply(line, expected_phase=0) is True
        knotcore_sim.bump_generation(_ptr_of(line))  # anchor/collapse cadence duty
        assert executor.apply(line, expected_phase=0) is False
        rejected = [e for e in journal.read_all() if e["type"] == "instruction.rejected"]
        assert rejected[-1]["payload"]["reason"] == "phase"
        # ...while a freshly minted (phase-current) instruction still applies.
        assert executor.apply(make_line(signer, backend, payload="fresh"), expected_phase=0) is True


# ---------------------------------------------------------------------------
# Braid wiring (fake braid; no akash.braid import)
# ---------------------------------------------------------------------------


class TestBraidWiring:
    def test_braid_commit_and_journal_event(self, fsm, journal, fence, verifier, backend, signer):
        braid = FakeBraid()
        executor = FSMExecutor(fsm, journal, fence, verifier, backend, braid=braid)
        line = make_line(signer, backend)
        assert executor.apply(line, strand="krishna") is True
        assert braid.commits == [(1, "krishna", line, {})]  # seq of the fsm.transition
        events = journal.read_all()
        assert [e["type"] for e in events] == ["fsm.transition", "braid.commit"]
        commit_event = events[1]["payload"]
        assert commit_event["seq"] == 1
        assert commit_event["strand"] == "krishna"
        assert commit_event["bus_line"] == line
        assert commit_event["crossings"] == {}
        assert commit_event["hash"] == braid.commitments[0].hash
        assert commit_event["root"] == braid.root()

    def test_braid_commit_seqs_track_transition_seqs(
        self, fsm, journal, fence, verifier, backend, signer
    ):
        braid = FakeBraid()
        executor = FSMExecutor(fsm, journal, fence, verifier, backend, braid=braid)
        lines = [make_line(signer, backend, payload=f"p{i}") for i in range(3)]
        for line in lines:
            assert executor.apply(line) is True
        # events interleave: fsm.transition@1, braid.commit@2, fsm.transition@3, ...
        assert [c[:3] for c in braid.commits] == [
            (1, "kernel", lines[0]),
            (3, "kernel", lines[1]),
            (5, "kernel", lines[2]),
        ]

    def test_no_braid_no_braid_events(self, executor, journal, signer, backend):
        assert executor.apply(make_line(signer, backend)) is True
        assert [e["type"] for e in journal.read_all()] == ["fsm.transition"]


# ---------------------------------------------------------------------------
# Recovery (ADR-001 §2.3 step 2; SPEC-M1 §3 recover, §6 acceptance)
# ---------------------------------------------------------------------------


class TestRecover:
    def test_recover_reproduces_state_from_journal_alone(
        self, executor, fsm, journal, journal_path, fence, verifier, backend, signer
    ):
        for i in range(5):
            assert executor.apply(make_line(signer, backend, payload=f"p{i}")) is True
        state_before = fsm.state
        journal_before = journal.read_all()
        last_seq = journal_before[-1]["seq"]

        recovered = FSMExecutor.recover(journal_path, EpochFence(), verifier, backend)

        assert recovered.fsm.state == state_before
        assert recovered.journal.read_all() == journal_before  # recover wrote nothing
        # journal seq continuity: the next append resumes the chain exactly
        assert recovered.apply(make_line(signer, backend, payload="post-recovery")) is True
        assert recovered.journal.read_all()[-1]["seq"] == last_seq + 1

    def test_recover_skips_rejected_events(
        self, executor, fsm, journal, journal_path, verifier, backend, signer
    ):
        assert executor.apply(make_line(signer, backend)) is True
        assert executor.apply("garbage") is False  # instruction.rejected: no FSM effect
        assert executor.apply(make_line(signer, backend)) is True
        recovered = FSMExecutor.recover(journal_path, EpochFence(), verifier, backend)
        assert recovered.fsm.state == fsm.state

    def test_recover_from_snapshot_plus_tail(
        self, executor, fsm, journal, journal_path, tmp_path, verifier, backend, signer
    ):
        for i in range(3):
            assert executor.apply(make_line(signer, backend, payload=f"pre-{i}")) is True
        snap_path = tmp_path / "snap.json"
        write_snapshot(
            snap_path,
            seq=len(journal.read_all()),
            epoch=0,
            fsm_state=fsm.state,
            braid_root="0" * 64,
            strand_tips={},
            journal_path=str(journal_path),
        )
        for i in range(2):
            assert executor.apply(make_line(signer, backend, payload=f"post-{i}")) is True

        recovered = FSMExecutor.recover(
            journal_path, EpochFence(), verifier, backend, snapshot_path=snap_path
        )
        assert recovered.fsm.state == fsm.state

    def test_recover_tampered_snapshot_raises(
        self, executor, fsm, journal, journal_path, tmp_path, verifier, backend, signer
    ):
        assert executor.apply(make_line(signer, backend)) is True
        snap_path = tmp_path / "snap.json"
        write_snapshot(
            snap_path,
            seq=1,
            epoch=0,
            fsm_state=fsm.state,
            braid_root="0" * 64,
            strand_tips={},
            journal_path=str(journal_path),
        )
        snap = json.loads(snap_path.read_text())
        snap["fsm_state"] = ""  # tamper: invalid field
        snap_path.write_text(json.dumps(snap))
        with pytest.raises(SnapshotError):
            FSMExecutor.recover(
                journal_path, EpochFence(), verifier, backend, snapshot_path=snap_path
            )

    def test_recover_tampered_journal_raises_fail_closed(
        self, executor, journal, journal_path, verifier, backend, signer
    ):
        assert executor.apply(make_line(signer, backend)) is True
        assert executor.apply(make_line(signer, backend)) is True
        _tamper_journal_payload(journal_path)
        with pytest.raises(SnapshotError):
            FSMExecutor.recover(journal_path, EpochFence(), verifier, backend)

    def test_recover_rebuilds_braid_from_journal(
        self, fsm, journal, journal_path, fence, verifier, backend, signer
    ):
        braid = FakeBraid()
        executor = FSMExecutor(fsm, journal, fence, verifier, backend, braid=braid)
        for i in range(3):
            assert executor.apply(make_line(signer, backend, payload=f"b{i}")) is True

        rebuilt = FakeBraid()
        recovered = FSMExecutor.recover(
            journal_path, EpochFence(), verifier, backend, braid=rebuilt
        )
        assert rebuilt.commits == braid.commits
        assert rebuilt.root() == braid.root()
        assert recovered.fsm.state == fsm.state

    def test_recover_rebuilds_braid_with_snapshot_uses_full_history(
        self, fsm, journal, journal_path, fence, verifier, backend, signer, tmp_path
    ):
        """A snapshot stores only the braid root; the root must still be
        recomputable from the journal alone (SPEC-M1 §6a), so recovery replays
        ALL braid.commit events, not just the post-snapshot tail."""
        braid = FakeBraid()
        executor = FSMExecutor(fsm, journal, fence, verifier, backend, braid=braid)
        for i in range(2):
            assert executor.apply(make_line(signer, backend, payload=f"pre-{i}")) is True
        snap_path = tmp_path / "snap.json"
        write_snapshot(
            snap_path,
            seq=len(journal.read_all()),
            epoch=0,
            fsm_state=fsm.state,
            braid_root=braid.root(),
            strand_tips={},
            journal_path=str(journal_path),
        )
        assert executor.apply(make_line(signer, backend, payload="post")) is True

        rebuilt = FakeBraid()
        FSMExecutor.recover(
            journal_path, EpochFence(), verifier, backend,
            snapshot_path=snap_path, braid=rebuilt,
        )
        assert rebuilt.commits == braid.commits  # full history, not just the tail
        assert rebuilt.root() == braid.root()


def _tamper_journal_payload(journal_path) -> None:
    """Rewrite the first journal line with a mutated payload (breaks the chain)."""
    lines = journal_path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["payload"]["event"] = "E9" if first["payload"].get("event") != "E9" else "E8"
    lines[0] = json.dumps(first, sort_keys=True, separators=(",", ":"))
    journal_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
