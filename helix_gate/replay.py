"""Durable replay + monotonic-sequence store (part of reviewer finding 2).

An envelope may be perfectly signed yet replayed. This store durably records
seen nonces and the highest accepted sequence per issuer, in a single SQLite
transaction so concurrent submissions cannot both win.

* A repeated ``nonce`` -> ``REJECTED_REPLAY``.
* A ``sequence`` not strictly greater than the issuer's last accepted sequence
  -> ``REJECTED_SEQUENCE_REGRESSION``.

The commit is performed *after* all cheaper validation passes so a rejected
envelope does not burn a nonce/sequence.
"""

from __future__ import annotations

import sqlite3
import threading

from .errors import ReasonCode, ValidationRejection

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_nonce (
    nonce TEXT PRIMARY KEY,
    issuer TEXT NOT NULL,
    seen_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS issuer_sequence (
    issuer TEXT PRIMARY KEY,
    last_sequence INTEGER NOT NULL
);
"""


class ReplayStore:
    def __init__(self, path: str = ":memory:") -> None:
        # check_same_thread=False + a lock: the controller may touch this from
        # more than one thread; SQLite serializes writes and the lock keeps the
        # nonce/sequence check-and-commit atomic across threads.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._lock = threading.Lock()

    def commit_use(self, nonce: str, issuer: str, sequence: int, now: int) -> None:
        """Atomically record ``nonce`` and advance the issuer sequence.

        Raises :class:`ValidationRejection` on replay or sequence regression;
        leaves the store unchanged in that case.
        """
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("BEGIN IMMEDIATE")
                row = cur.execute(
                    "SELECT last_sequence FROM issuer_sequence WHERE issuer=?",
                    (issuer,),
                ).fetchone()
                if row is not None and sequence <= row[0]:
                    raise ValidationRejection(
                        ReasonCode.REJECTED_SEQUENCE_REGRESSION,
                        f"sequence {sequence} <= last {row[0]}",
                    )
                try:
                    cur.execute(
                        "INSERT INTO seen_nonce(nonce, issuer, seen_at) VALUES(?,?,?)",
                        (nonce, issuer, now),
                    )
                except sqlite3.IntegrityError:
                    raise ValidationRejection(
                        ReasonCode.REJECTED_REPLAY, f"nonce {nonce!r} already seen"
                    ) from None
                cur.execute(
                    "INSERT INTO issuer_sequence(issuer, last_sequence) VALUES(?,?) "
                    "ON CONFLICT(issuer) DO UPDATE SET last_sequence=excluded.last_sequence",
                    (issuer, sequence),
                )
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    def close(self) -> None:
        self._conn.close()
