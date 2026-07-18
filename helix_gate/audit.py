"""Signed, hash-chained, append-only audit log (closes reviewer finding 7).

Draft 1 emitted ``f"[{state}] {message}"`` — unstructured and unsigned. Here each
lifecycle transition emits a structured event with machine-readable ``state`` and
``reason_code`` fields, an Ed25519 signature, and a ``previous_event_digest`` that
chains it to the prior event. Tampering with or dropping any event breaks the
chain, which :func:`AuditLog.verify_chain` detects.

The log is append-only: no ``UPDATE``/``DELETE`` path exists. Human-readable
messages are supplemental; state and reason codes carry the meaning.
"""

from __future__ import annotations

import base64
import hashlib
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .hx1.canonical import canonical_bytes

EVENT_VERSION = "HX-AUDIT-1"
_GENESIS = "0" * 64  # previous_event_digest for the first event

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    seq INTEGER PRIMARY KEY,
    event_json TEXT NOT NULL,
    digest TEXT NOT NULL,
    signature TEXT NOT NULL
);
"""


def _digest(event: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(event)).hexdigest()


@dataclass(frozen=True)
class AuditEvent:
    seq: int
    fields: dict[str, Any]
    digest: str
    signature: str


class AuditLog:
    def __init__(self, path: str, signing_key: Ed25519PrivateKey,
                 signing_key_id: str) -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._key = signing_key
        self._key_id = signing_key_id
        self._lock = threading.Lock()

    def append(self, *, operation_id: str, sandbox_id: str | None, state: str,
               previous_state: str | None, reason_code: str,
               module_digest: str | None = None, envelope_digest: str | None = None,
               result_digest: str | None = None, message: str = "") -> AuditEvent:
        with self._lock:
            row = self._conn.execute(
                "SELECT seq, digest FROM audit_events ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            seq = (row[0] + 1) if row else 0
            prev_digest = row[1] if row else _GENESIS

            fields = {
                "event_version": EVENT_VERSION,
                "seq": seq,
                "operation_id": operation_id,
                "sandbox_id": sandbox_id,
                "state": state,
                "previous_state": previous_state,
                "reason_code": reason_code,
                "module_digest": module_digest,
                "envelope_digest": envelope_digest,
                "result_digest": result_digest,
                "previous_event_digest": prev_digest,
                "signing_key_id": self._key_id,
                "timestamp": int(time.time()),
                "message": message,
            }
            digest = _digest(fields)
            signature = base64.b64encode(
                self._key.sign(canonical_bytes(fields))
            ).decode("ascii")

            self._conn.execute(
                "INSERT INTO audit_events(seq, event_json, digest, signature) "
                "VALUES(?,?,?,?)",
                (seq, canonical_bytes(fields).decode("utf-8"), digest, signature),
            )
            self._conn.commit()
            return AuditEvent(seq=seq, fields=fields, digest=digest, signature=signature)

    def events(self) -> list[AuditEvent]:
        import json
        rows = self._conn.execute(
            "SELECT seq, event_json, digest, signature FROM audit_events ORDER BY seq"
        ).fetchall()
        return [AuditEvent(seq=r[0], fields=json.loads(r[1]), digest=r[2],
                           signature=r[3]) for r in rows]

    def verify_chain(self) -> bool:
        """Recompute digests + signatures and confirm the chain is intact."""
        prev = _GENESIS
        for ev in self.events():
            if ev.fields.get("previous_event_digest") != prev:
                return False
            if _digest(ev.fields) != ev.digest:
                return False
            try:
                self._key.public_key().verify(
                    base64.b64decode(ev.signature), canonical_bytes(ev.fields)
                )
            except Exception:
                return False
            prev = ev.digest
        return True

    def close(self) -> None:
        self._conn.close()
