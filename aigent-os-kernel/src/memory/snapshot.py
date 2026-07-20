"""HELIXos state snapshots — atomic, hash-pinned checkpoints of derived state.

Fixes audit finding AUD-C8 (HELIXos_Handoff_Audit.md) together with
``journal.py`` and ``epochs.py``: ADR-001 §2.3 defines Projective Collapse
recovery as *restore the latest snapshot, then replay the journal forward
from it*.  This module is the snapshot half of that protocol (SPEC-M1 §2):

* :func:`write_snapshot` serializes the derived-state checkpoint (journal
  seq, epoch, TEN-SQUARED FSM state, AKASH braid root + strand tips) as one
  canonical-JSON document, pins the journal it was taken against via
  ``journal_sha256`` (SHA-256 over the journal file bytes), and writes it
  **atomically** (tmp file + ``os.replace`` + directory fsync) so a crash
  mid-checkpoint never leaves a half-written snapshot.
* If a ``signer`` is given, the snapshot carries ``sig`` — a
  ``sign_root``-style signature (SPEC-M1 §1) over
  ``b"HELIX-BRAID/1" || bytes.fromhex(braid_root)``, base64url-no-padding —
  binding the checkpoint to the braid root it claims.
* :func:`load_snapshot` is fail-closed: magic, exact field set, field types
  and hex formats are all validated; any anomaly raises
  :class:`SnapshotError`.  An optional ``verifier`` additionally enforces a
  present-and-valid ``sig``.
* :func:`replay_events` is the replay half of ADR-001 §2.3 step 2: it
  verifies the journal hash chain FIRST (``EventJournal.verify_chain``) and
  raises :class:`SnapshotError` on a bad chain — replay from a tampered
  journal is refused, never partially applied.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path

from journal import EventJournal

log = logging.getLogger("helix.memory.snapshot")

__all__ = [
    "SNAPSHOT_MAGIC",
    "BRAID_SIG_DOMAIN",
    "SnapshotError",
    "write_snapshot",
    "load_snapshot",
    "replay_events",
]

SNAPSHOT_MAGIC = "HELIXOS-SNAPSHOT/1"

# Domain separator for the optional braid-root signature.  Identical to
# SPEC-M1 §1 sign_root: sig = b64url( sign( BRAID_SIG_DOMAIN || bytes.fromhex(root) ) ).
BRAID_SIG_DOMAIN = b"HELIX-BRAID/1"

_HEX64 = frozenset("0123456789abcdef")
_REQUIRED_FIELDS = (
    "magic",
    "seq",
    "ts",
    "epoch",
    "fsm_state",
    "braid_root",
    "strand_tips",
    "journal_sha256",
)
_OPTIONAL_FIELDS = ("sig",)


class SnapshotError(ValueError):
    """A snapshot or the journal it replays from failed validation (fail-closed)."""


def _is_hex64(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX64


def _require_int(name: str, value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative int, got {value!r}")
    return value


def _require_hex64(name: str, value: object) -> str:
    if not _is_hex64(value):
        raise ValueError(f"{name} must be 64 lowercase hex chars, got {value!r}")
    return value  # type: ignore[return-value]


def _require_strand_tips(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f"strand_tips must be a dict, got {type(value).__name__}")
    for strand, tip in value.items():
        if not isinstance(strand, str) or not strand:
            raise ValueError(f"strand_tips keys must be non-empty str, got {strand!r}")
        _require_hex64(f"strand_tips[{strand!r}]", tip)
    return dict(value)


def sign_braid_root(braid_root: str, signer) -> str:
    """b64url signature over ``BRAID_SIG_DOMAIN || bytes.fromhex(braid_root)``.

    Same domain and encoding as SPEC-M1 §1 ``sign_root`` so snapshots and
    braid anchors verify against the same signature scheme.
    """
    _require_hex64("braid_root", braid_root)
    if not (hasattr(signer, "sign") and hasattr(signer, "b64tag")):
        raise ValueError("signer must expose sign()/b64tag() — see signers.py")
    return signer.b64tag(signer.sign(BRAID_SIG_DOMAIN + bytes.fromhex(braid_root)))


def verify_braid_root(braid_root: str, sig_b64: str, verifier) -> bool:
    """Fail-closed counterpart of :func:`sign_braid_root`; False on ANY error."""
    try:
        if not _is_hex64(braid_root) or not isinstance(sig_b64, str):
            return False
        tag = verifier.tag_from_b64(sig_b64)
        return bool(verifier.verify(BRAID_SIG_DOMAIN + bytes.fromhex(braid_root), tag))
    except Exception:
        log.exception("verify_braid_root: failing closed")
        return False


def write_snapshot(
    path: str | Path,
    *,
    seq: int,
    epoch: int,
    fsm_state: str,
    braid_root: str,
    strand_tips: dict[str, str],
    journal_path: str | Path,
    signer=None,
) -> dict:
    """Write a checkpoint atomically (tmp + rename) and return the snapshot dict.

    ``journal_sha256`` pins the exact journal bytes the snapshot was taken
    against.  Invalid inputs raise ValueError (dev-visible); OS-level write
    failures propagate after the tmp file is cleaned up.
    """
    _require_int("seq", seq)
    _require_int("epoch", epoch)
    if not isinstance(fsm_state, str) or not fsm_state:
        raise ValueError(f"fsm_state must be a non-empty str, got {fsm_state!r}")
    _require_hex64("braid_root", braid_root)
    tips = _require_strand_tips(strand_tips)
    journal_file = Path(journal_path)
    if not journal_file.is_file():
        raise ValueError(f"journal_path does not exist or is not a file: {journal_file}")

    snapshot: dict = {
        "magic": SNAPSHOT_MAGIC,
        "seq": seq,
        "ts": time.time(),
        "epoch": epoch,
        "fsm_state": fsm_state,
        "braid_root": braid_root,
        "strand_tips": tips,
        "journal_sha256": hashlib.sha256(journal_file.read_bytes()).hexdigest(),
    }
    if signer is not None:
        snapshot["sig"] = sign_braid_root(braid_root, signer)

    target = Path(path)
    data = (json.dumps(snapshot, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    tmp = target.with_name(target.name + ".tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, target)  # atomic on POSIX: readers see old or new, never partial
        dir_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)  # durable rename
        finally:
            os.close(dir_fd)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            log.warning("snapshot tmp cleanup failed: %s", tmp)
        raise
    log.info(
        "snapshot.written path=%s seq=%d epoch=%d fsm_state=%s braid_root=%s",
        target,
        seq,
        epoch,
        fsm_state,
        braid_root[:12],
    )
    return snapshot


def load_snapshot(path: str | Path, verifier=None) -> dict:
    """Load and validate a snapshot; raise SnapshotError on ANY anomaly.

    With ``verifier`` given, the snapshot must carry a ``sig`` that verifies
    against ``braid_root`` (fail-closed).  Without one, a present ``sig`` is
    structurally validated but not cryptographically checked (documented —
    callers that care pass a verifier).
    """
    target = Path(path)
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise SnapshotError(f"snapshot unreadable: {target}: {exc}") from exc
    try:
        snap = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SnapshotError(f"snapshot is not valid JSON: {target}: {exc}") from exc
    if not isinstance(snap, dict):
        raise SnapshotError(f"snapshot root must be a JSON object: {target}")

    if snap.get("magic") != SNAPSHOT_MAGIC:
        raise SnapshotError(
            f"bad snapshot magic: {snap.get('magic')!r} (expected {SNAPSHOT_MAGIC!r})"
        )
    allowed = set(_REQUIRED_FIELDS) | set(_OPTIONAL_FIELDS)
    missing = [f for f in _REQUIRED_FIELDS if f not in snap]
    unknown = [f for f in snap if f not in allowed]
    if missing or unknown:
        raise SnapshotError(f"snapshot fields invalid: missing={missing} unknown={unknown}")

    def fail(what: str) -> SnapshotError:
        return SnapshotError(f"snapshot field invalid: {what}")

    if not isinstance(snap["seq"], int) or isinstance(snap["seq"], bool) or snap["seq"] < 0:
        raise fail(f"seq must be a non-negative int, got {snap['seq']!r}")
    if isinstance(snap["ts"], bool) or not isinstance(snap["ts"], (int, float)):
        raise fail(f"ts must be a number, got {snap['ts']!r}")
    if (
        not isinstance(snap["epoch"], int)
        or isinstance(snap["epoch"], bool)
        or snap["epoch"] < 0
    ):
        raise fail(f"epoch must be a non-negative int, got {snap['epoch']!r}")
    if not isinstance(snap["fsm_state"], str) or not snap["fsm_state"]:
        raise fail(f"fsm_state must be a non-empty str, got {snap['fsm_state']!r}")
    if not _is_hex64(snap["braid_root"]):
        raise fail(f"braid_root must be 64 lowercase hex chars, got {snap['braid_root']!r}")
    if not isinstance(snap["strand_tips"], dict) or any(
        not isinstance(k, str) or not k or not _is_hex64(v)
        for k, v in snap["strand_tips"].items()
    ):
        raise fail("strand_tips must map non-empty str -> 64 lowercase hex chars")
    if not _is_hex64(snap["journal_sha256"]):
        raise fail(
            f"journal_sha256 must be 64 lowercase hex chars, got {snap['journal_sha256']!r}"
        )
    if "sig" in snap and not isinstance(snap["sig"], str):
        raise fail(f"sig must be a str, got {type(snap['sig']).__name__}")

    if verifier is not None:
        sig = snap.get("sig")
        if sig is None:
            raise SnapshotError("snapshot is unsigned but a verifier was supplied")
        if not verify_braid_root(snap["braid_root"], sig, verifier):
            raise SnapshotError("snapshot braid-root signature does not verify")
    return snap


def replay_events(journal_path: str | Path, since_seq: int = 0) -> list[dict]:
    """Return journal events with ``seq > since_seq`` after chain verification.

    Fail-closed (ADR-001 §2.3 step 2): the hash chain is verified FIRST via
    ``EventJournal.verify_chain()``; a broken chain, a corrupt tail or a
    missing journal raises SnapshotError and nothing is replayed.
    """
    _require_int("since_seq", since_seq)
    journal_file = Path(journal_path)
    if not journal_file.is_file():
        raise SnapshotError(f"journal not found: {journal_file}")
    try:
        journal = EventJournal(journal_file)
    except ValueError as exc:
        raise SnapshotError(f"journal tail corrupt in {journal_file}: {exc}") from exc
    try:
        if not journal.verify_chain():
            raise SnapshotError(
                f"journal hash-chain verification failed (fail-closed): {journal_file}"
            )
        try:
            events = journal.read_all()
        except ValueError as exc:
            raise SnapshotError(f"journal unreadable: {journal_file}: {exc}") from exc
    finally:
        journal.close()
    selected = [e for e in events if int(e["seq"]) > since_seq]
    log.info(
        "snapshot.replay journal=%s since_seq=%d selected=%d",
        journal_file,
        since_seq,
        len(selected),
    )
    return selected
