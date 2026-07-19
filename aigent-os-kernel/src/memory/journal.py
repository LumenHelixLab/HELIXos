"""HELIXos event journal — append-only, hash-chained, fsync-durable JSONL store.

Fixes audit finding AUD-C8 (HELIXos_Handoff_Audit.md): four overlapping state
stores with no system of record.  ADR-001 declares **this journal is the
system of record**; KNOTstore braid signatures are integrity anchors, ledgers
and the Obsidian vault are regenerable materialized views, and zero-copy
memory is a disposable cache.  After a Projective Collapse, state is rebuilt
from snapshot + journal replay (see ``epochs.py``).

Format (SPEC.md §3.7): newline-delimited JSON, one event per line::

    {"seq":int,"ts":float,"epoch":int,"type":str,"payload":dict,
     "prev":hex64,"hash":hex64}

``hash`` is SHA-256 over the canonical JSON (``sort_keys=True``,
``separators=(",", ":")``) of all fields except ``hash``; ``prev`` chains to
the previous line's hash; the genesis line uses ``prev = "0" * 64``.

Durability & concurrency:

* file opened ``O_APPEND | O_CREAT | O_WRONLY``; every append is a single
  ``os.write`` followed by ``os.fsync``;
* ``fcntl.flock(LOCK_EX)`` is held during each append (advisory, inter-process;
  documented — all writers must cooperate through this class);
* a :class:`threading.Lock` serializes appends from threads in-process
  (``flock`` alone cannot — threads share the open file description);
* reopening an existing journal resumes the chain from the last line.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("helix.memory.journal")

GENESIS_PREV = "0" * 64
_FIELDS = ("seq", "ts", "epoch", "type", "payload", "prev", "hash")
_HEX64 = frozenset("0123456789abcdef")


def _canonical(record: dict) -> bytes:
    """Canonical JSON encoding of all fields except ``hash`` (deterministic)."""
    return json.dumps(
        {k: record[k] for k in _FIELDS if k != "hash"},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _line_hash(record: dict) -> str:
    return hashlib.sha256(_canonical(record)).hexdigest()


class EventJournal:
    """Append-only event-sourced journal with SHA-256 hash-chain integrity."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._fd = os.open(self._path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        self._closed = False
        # Resume the chain from the existing tail (empty file -> genesis).
        self._seq, self._prev = 0, GENESIS_PREV
        last = self._last_line()
        if last is not None:
            try:
                record = json.loads(last)
                self._seq, self._prev = int(record["seq"]), str(record["hash"])
            except (KeyError, TypeError, ValueError) as exc:
                os.close(self._fd)
                self._closed = True
                raise ValueError(f"journal tail corrupt in {self._path}: {exc}") from exc
        log.info("journal.open path=%s resume_seq=%d", self._path, self._seq)

    # ------------------------------------------------------------------ append
    def append(self, event_type: str, payload: dict, epoch: int = 0) -> int:
        """Append one event; fsync before returning the assigned ``seq``."""
        if self._closed:
            raise ValueError("journal is closed")
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("event_type must be a non-empty string")
        if not isinstance(payload, dict):
            raise ValueError("payload must be a dict")
        if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
            raise ValueError("epoch must be a non-negative int")
        with self._lock:
            record = {
                "seq": self._seq + 1,
                "ts": time.time(),
                "epoch": epoch,
                "type": event_type,
                "payload": payload,
                "prev": self._prev,
            }
            record["hash"] = _line_hash(record)
            # Build the full line BEFORE touching the fd: a serialization
            # failure can never leave a partial line in the journal.
            line = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode(
                "utf-8"
            )
            fcntl.flock(self._fd, fcntl.LOCK_EX)  # advisory single-writer protocol
            try:
                os.write(self._fd, line)  # one syscall under O_APPEND
                os.fsync(self._fd)
            finally:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            self._seq, self._prev = record["seq"], record["hash"]
            return self._seq

    # ------------------------------------------------------------------ reads
    def read_all(self) -> list[dict]:
        """Return every event as a dict, in append order. Raises on corruption."""
        records = []
        with open(self._path, "r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"journal line {lineno} corrupt: {exc}") from exc
        return records

    def verify_chain(self) -> bool:
        """Fail-closed integrity check: False on ANY anomaly, never raises.

        Verifies, in order: exact field set, field types, ``seq`` running
        1..N without gaps, genesis ``prev``, hash linkage, and recomputed
        SHA-256 of every line.
        """
        try:
            expected_seq, expected_prev = 1, GENESIS_PREV
            with open(self._path, "r", encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    if not isinstance(record, dict) or set(record) != set(_FIELDS):
                        return False
                    if not (
                        isinstance(record["seq"], int)
                        and not isinstance(record["seq"], bool)
                        and isinstance(record["ts"], (int, float))
                        and not isinstance(record["ts"], bool)
                        and isinstance(record["epoch"], int)
                        and not isinstance(record["epoch"], bool)
                        and isinstance(record["type"], str)
                        and isinstance(record["payload"], dict)
                        and _is_hex64(record["prev"])
                        and _is_hex64(record["hash"])
                    ):
                        return False
                    if record["seq"] != expected_seq or record["prev"] != expected_prev:
                        return False
                    if _line_hash(record) != record["hash"]:
                        return False
                    expected_seq, expected_prev = record["seq"] + 1, record["hash"]
            return True
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return False

    # ------------------------------------------------------------------ misc
    def close(self) -> None:
        """Close the underlying fd (idempotent)."""
        if not self._closed:
            self._closed = True
            os.close(self._fd)

    def __enter__(self) -> "EventJournal":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _last_line(self) -> str | None:
        last = None
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        last = line
        except FileNotFoundError:
            return None
        return last


def _is_hex64(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX64
