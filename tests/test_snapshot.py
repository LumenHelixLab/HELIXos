"""Tests for src/memory/snapshot.py (SPEC-M1 §2 snapshots; ADR-001 §2.3 recovery).

Pins the atomic write, magic/field fail-closed validation, journal_sha256
pinning, the optional HELIX-BRAID/1-domain signature, and chain-verified
journal replay (a tampered journal is never replayed).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest

from journal import EventJournal
from signers import HMACSigner, HMACVerifier
from snapshot import (
    BRAID_SIG_DOMAIN,
    SNAPSHOT_MAGIC,
    SnapshotError,
    load_snapshot,
    replay_events,
    write_snapshot,
)

HMAC_KEY = bytes(range(32))
WRONG_HMAC_KEY = b"\xff" * 32


@pytest.fixture
def journal_path(tmp_path):
    path = tmp_path / "journal.jsonl"
    with EventJournal(path) as j:
        for i in range(5):
            j.append("fsm.transition", {"i": i}, epoch=0)
    return path


@pytest.fixture
def snap_args(journal_path):
    return {
        "seq": 5,
        "epoch": 0,
        "fsm_state": "S42",
        "braid_root": "ab" * 32,
        "strand_tips": {"krishna": "cd" * 32},
        "journal_path": str(journal_path),
    }


def _rewrite(path, mutate) -> None:
    """Load a snapshot file, apply ``mutate`` to the dict, write it back."""
    snap = json.loads(path.read_text(encoding="utf-8"))
    mutate(snap)
    path.write_text(json.dumps(snap, sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------------------
# write/load roundtrip
# ---------------------------------------------------------------------------


class TestWriteLoad:
    def test_roundtrip(self, tmp_path, snap_args):
        path = tmp_path / "snap.json"
        written = write_snapshot(path, **snap_args)
        assert written["magic"] == SNAPSHOT_MAGIC
        assert "sig" not in written  # no signer supplied
        loaded = load_snapshot(path)
        assert loaded == written
        assert loaded["seq"] == 5
        assert loaded["fsm_state"] == "S42"
        assert loaded["strand_tips"] == {"krishna": "cd" * 32}

    def test_atomic_write_leaves_no_tmp_file(self, tmp_path, snap_args):
        path = tmp_path / "snap.json"
        write_snapshot(path, **snap_args)
        assert [p.name for p in tmp_path.iterdir() if p.name != "journal.jsonl"] == [
            "snap.json"
        ]
        # and the on-disk bytes are exactly the canonical JSON of the dict
        written = write_snapshot(path, **snap_args)
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk == written

    def test_journal_sha256_pins_journal_bytes(self, tmp_path, snap_args, journal_path):
        path = tmp_path / "snap.json"
        written = write_snapshot(path, **snap_args)
        expected = hashlib.sha256(journal_path.read_bytes()).hexdigest()
        assert written["journal_sha256"] == expected

    def test_write_validates_inputs(self, tmp_path, snap_args):
        path = tmp_path / "snap.json"
        for bad in (
            {"seq": -1},
            {"seq": "5"},
            {"epoch": True},
            {"fsm_state": ""},
            {"braid_root": "zz" * 32},
            {"braid_root": "ab" * 16},  # too short
            {"strand_tips": {"s": "nothex"}},
            {"strand_tips": ["not", "a", "dict"]},
            {"journal_path": str(tmp_path / "missing.jsonl")},
        ):
            kwargs = {**snap_args, **bad}
            with pytest.raises(ValueError):
                write_snapshot(path, **kwargs)
        assert not path.exists()  # validation happens before any write


# ---------------------------------------------------------------------------
# load validation (fail-closed)
# ---------------------------------------------------------------------------


class TestLoadValidation:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(SnapshotError):
            load_snapshot(tmp_path / "nope.json")

    def test_non_json_raises(self, tmp_path):
        path = tmp_path / "snap.json"
        path.write_text("not json at all", encoding="utf-8")
        with pytest.raises(SnapshotError):
            load_snapshot(path)

    def test_bad_magic_raises(self, tmp_path, snap_args):
        path = tmp_path / "snap.json"
        write_snapshot(path, **snap_args)
        _rewrite(path, lambda s: s.update(magic="HELIXOS-SNAPSHOT/0"))
        with pytest.raises(SnapshotError):
            load_snapshot(path)

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda s: s.update(seq="5"),               # seq wrong type
            lambda s: s.update(seq=-1),                # seq negative
            lambda s: s.update(epoch=-1),              # epoch negative
            lambda s: s.update(ts="now"),              # ts wrong type
            lambda s: s.update(fsm_state=""),          # empty fsm_state
            lambda s: s.update(fsm_state=42),          # fsm_state wrong type
            lambda s: s.update(braid_root="ZZ" * 32),  # non-hex root
            lambda s: s.update(strand_tips={"s": "x"}),  # bad tip
            lambda s: s.update(journal_sha256="ab"),   # short hash
            lambda s: s.update(sig=123),               # sig wrong type
            lambda s: s.pop("epoch"),                  # missing required field
            lambda s: s.update(evil=1),                # unknown field
        ],
        ids=[
            "seq-str", "seq-negative", "epoch-negative", "ts-str", "fsm-state-empty",
            "fsm-state-int", "braid-root-nonhex", "strand-tip-bad", "sha-short",
            "sig-int", "missing-field", "unknown-field",
        ],
    )
    def test_tampered_fields_raise(self, tmp_path, snap_args, mutate):
        path = tmp_path / "snap.json"
        write_snapshot(path, **snap_args)
        _rewrite(path, mutate)
        with pytest.raises(SnapshotError):
            load_snapshot(path)


# ---------------------------------------------------------------------------
# Signed snapshots (HELIX-BRAID/1 domain over braid_root, SPEC-M1 §1/§2)
# ---------------------------------------------------------------------------


class TestSignatures:
    def test_signed_snapshot_verifies(self, tmp_path, snap_args):
        path = tmp_path / "snap.json"
        written = write_snapshot(path, signer=HMACSigner(HMAC_KEY), **snap_args)
        assert isinstance(written["sig"], str) and written["sig"]
        loaded = load_snapshot(path, verifier=HMACVerifier(HMAC_KEY))
        assert loaded == written

    def test_sig_matches_braid_domain(self, tmp_path, snap_args):
        """sig = b64url(HMAC(key, b"HELIX-BRAID/1" || bytes.fromhex(root))[:16])."""
        path = tmp_path / "snap.json"
        written = write_snapshot(path, signer=HMACSigner(HMAC_KEY), **snap_args)
        raw = hmac.new(
            HMAC_KEY,
            BRAID_SIG_DOMAIN + bytes.fromhex(snap_args["braid_root"]),
            hashlib.sha256,
        ).digest()[:16]
        expected = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        assert written["sig"] == expected

    def test_wrong_key_verifier_raises(self, tmp_path, snap_args):
        path = tmp_path / "snap.json"
        write_snapshot(path, signer=HMACSigner(HMAC_KEY), **snap_args)
        with pytest.raises(SnapshotError):
            load_snapshot(path, verifier=HMACVerifier(WRONG_HMAC_KEY))

    def test_tampered_sig_raises(self, tmp_path, snap_args):
        path = tmp_path / "snap.json"
        written = write_snapshot(path, signer=HMACSigner(HMAC_KEY), **snap_args)
        forged = ("A" if written["sig"][0] != "A" else "B") + written["sig"][1:]
        _rewrite(path, lambda s: s.update(sig=forged))
        with pytest.raises(SnapshotError):
            load_snapshot(path, verifier=HMACVerifier(HMAC_KEY))

    def test_tampered_root_breaks_sig(self, tmp_path, snap_args):
        path = tmp_path / "snap.json"
        write_snapshot(path, signer=HMACSigner(HMAC_KEY), **snap_args)
        _rewrite(path, lambda s: s.update(braid_root="ef" * 32))
        with pytest.raises(SnapshotError):
            load_snapshot(path, verifier=HMACVerifier(HMAC_KEY))

    def test_unsigned_snapshot_with_verifier_raises(self, tmp_path, snap_args):
        path = tmp_path / "snap.json"
        write_snapshot(path, **snap_args)  # no signer -> no sig
        with pytest.raises(SnapshotError):
            load_snapshot(path, verifier=HMACVerifier(HMAC_KEY))


# ---------------------------------------------------------------------------
# replay_events (chain-verified, fail-closed)
# ---------------------------------------------------------------------------


class TestReplayEvents:
    def test_since_seq_filters(self, journal_path):
        events = replay_events(journal_path, since_seq=2)
        assert [e["seq"] for e in events] == [3, 4, 5]

    def test_since_seq_zero_returns_all(self, journal_path):
        events = replay_events(journal_path, 0)
        assert [e["seq"] for e in events] == [1, 2, 3, 4, 5]
        assert all(e["type"] == "fsm.transition" for e in events)

    def test_since_seq_beyond_tail_returns_empty(self, journal_path):
        assert replay_events(journal_path, since_seq=99) == []

    def test_tampered_journal_raises(self, journal_path):
        lines = journal_path.read_text(encoding="utf-8").splitlines()
        first = json.loads(lines[0])
        first["payload"]["i"] = 999  # mutate without fixing the hash chain
        lines[0] = json.dumps(first, sort_keys=True, separators=(",", ":"))
        journal_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with pytest.raises(SnapshotError):
            replay_events(journal_path, 0)

    def test_truncated_journal_raises(self, journal_path):
        lines = journal_path.read_text(encoding="utf-8").splitlines()
        journal_path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        # truncating the tail leaves a valid prefix chain; this must NOT raise...
        assert [e["seq"] for e in replay_events(journal_path, 0)] == [1, 2, 3, 4]
        # ...but a mid-chain hole must
        journal_path.write_text(
            "\n".join([lines[0], lines[1], lines[3], lines[4]]) + "\n", encoding="utf-8"
        )
        with pytest.raises(SnapshotError):
            replay_events(journal_path, 0)

    def test_missing_journal_raises(self, tmp_path):
        with pytest.raises(SnapshotError):
            replay_events(tmp_path / "missing.jsonl", 0)

    def test_corrupt_tail_raises(self, journal_path):
        with journal_path.open("a", encoding="utf-8") as fh:
            fh.write("{not valid json\n")
        with pytest.raises(SnapshotError):
            replay_events(journal_path, 0)

    def test_negative_since_seq_rejected(self, journal_path):
        with pytest.raises(ValueError):
            replay_events(journal_path, since_seq=-1)
