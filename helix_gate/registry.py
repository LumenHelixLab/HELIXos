"""Durable operation registry (closes reviewer findings 8, 9, 13).

Draft 1's ``active_sandboxes`` dict had no concurrency protection, no durability,
no recovery, no atomic transitions, and doubled the nonce as the sandbox id.
This registry is a SQLite store with:

* **atomic compare-and-set** transitions (``UPDATE ... WHERE state = expected``),
  validated against the :mod:`helix_gate.lifecycle` FSM;
* distinct ``operation_id`` and ``sandbox_id`` (the nonce is neither);
* **retained** records with a separate cleanup dimension;
* **idempotent cancellation** — re-cancelling a terminal op returns its existing
  outcome, never ``TOO_LATE``;
* **restart recovery** — operations left non-terminal by a crashed controller are
  swept to ``FAILED_UNSAFE`` (their safety cannot be attested).
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass

from .lifecycle import (
    CleanupState,
    IllegalTransition,
    State,
    can_transition,
    is_abnormal_terminal,
    is_terminal,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS operations (
    operation_id   TEXT PRIMARY KEY,
    sandbox_id     TEXT,
    owner          TEXT NOT NULL,
    state          TEXT NOT NULL,
    previous_state TEXT,
    cleanup_state  TEXT NOT NULL,
    terminal_reason TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class OperationRecord:
    operation_id: str
    sandbox_id: str | None
    owner: str
    state: State
    previous_state: State | None
    cleanup_state: CleanupState
    terminal_reason: str | None
    cancel_requested: bool

    @property
    def is_terminal(self) -> bool:
        return is_terminal(self.state)


class ConcurrentTransition(Exception):
    """A CAS transition lost a race (the row was not in the expected state)."""


class Registry:
    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._lock = threading.Lock()

    # -- creation ----------------------------------------------------------
    def create(self, owner: str) -> str:
        operation_id = "op-" + uuid.uuid4().hex
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                "INSERT INTO operations(operation_id, owner, state, cleanup_state, "
                "created_at, updated_at) VALUES(?,?,?,?,?,?)",
                (operation_id, owner, State.RECEIVED.value,
                 CleanupState.ACTIVE.value, now, now),
            )
            self._conn.commit()
        return operation_id

    def assign_sandbox(self, operation_id: str) -> str:
        sandbox_id = "sbx-" + uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                "UPDATE operations SET sandbox_id=?, updated_at=? WHERE operation_id=?",
                (sandbox_id, int(time.time()), operation_id),
            )
            self._conn.commit()
        return sandbox_id

    # -- transitions -------------------------------------------------------
    def transition(self, operation_id: str, expected: State, new: State,
                   terminal_reason: str | None = None) -> None:
        """Atomically move ``expected`` -> ``new`` or raise.

        Raises :class:`IllegalTransition` if the FSM forbids it, or
        :class:`ConcurrentTransition` if the row was not in ``expected``.
        """
        if not can_transition(expected, new):
            raise IllegalTransition(expected, new)
        cleanup = None
        if is_abnormal_terminal(new):
            cleanup = CleanupState.FROZEN.value
        now = int(time.time())
        with self._lock:
            cur = self._conn.cursor()
            if cleanup is None:
                cur.execute(
                    "UPDATE operations SET state=?, previous_state=?, "
                    "terminal_reason=COALESCE(?, terminal_reason), updated_at=? "
                    "WHERE operation_id=? AND state=?",
                    (new.value, expected.value, terminal_reason, now,
                     operation_id, expected.value),
                )
            else:
                cur.execute(
                    "UPDATE operations SET state=?, previous_state=?, cleanup_state=?, "
                    "terminal_reason=COALESCE(?, terminal_reason), updated_at=? "
                    "WHERE operation_id=? AND state=?",
                    (new.value, expected.value, cleanup, terminal_reason, now,
                     operation_id, expected.value),
                )
            if cur.rowcount != 1:
                self._conn.rollback()
                raise ConcurrentTransition(
                    f"{operation_id}: not in expected state {expected.value}"
                )
            self._conn.commit()

    def mark_cleaned_up(self, operation_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE operations SET cleanup_state=?, updated_at=? WHERE operation_id=?",
                (CleanupState.CLEANED_UP.value, int(time.time()), operation_id),
            )
            self._conn.commit()

    # -- cancellation (idempotent) ----------------------------------------
    def request_cancel(self, operation_id: str) -> tuple[bool, State | None]:
        """Record a cancel request idempotently.

        Returns ``(actionable, state)``:
        * terminal op -> ``(False, terminal_state)`` (idempotent, never TOO_LATE);
        * live op     -> ``(True, current_state)`` and sets the cancel flag;
        * unknown op  -> ``(False, None)``.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT state FROM operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if row is None:
                return (False, None)
            state = State(row[0])
            if is_terminal(state):
                return (False, state)
            self._conn.execute(
                "UPDATE operations SET cancel_requested=1, updated_at=? WHERE operation_id=?",
                (int(time.time()), operation_id),
            )
            self._conn.commit()
            return (True, state)

    # -- reads -------------------------------------------------------------
    def get(self, operation_id: str) -> OperationRecord | None:
        row = self._conn.execute(
            "SELECT operation_id, sandbox_id, owner, state, previous_state, "
            "cleanup_state, terminal_reason, cancel_requested "
            "FROM operations WHERE operation_id=?", (operation_id,)
        ).fetchone()
        if row is None:
            return None
        return OperationRecord(
            operation_id=row[0],
            sandbox_id=row[1],
            owner=row[2],
            state=State(row[3]),
            previous_state=State(row[4]) if row[4] else None,
            cleanup_state=CleanupState(row[5]),
            terminal_reason=row[6],
            cancel_requested=bool(row[7]),
        )

    # -- recovery ----------------------------------------------------------
    def recover_orphans(self) -> list[str]:
        """Sweep operations left non-terminal by a crash to FAILED_UNSAFE."""
        live = [s.value for s in State if not is_terminal(s)]
        now = int(time.time())
        swept: list[str] = []
        with self._lock:
            rows = self._conn.execute(
                f"SELECT operation_id, state FROM operations "
                f"WHERE state IN ({','.join('?' * len(live))})", live
            ).fetchall()
            for op_id, prev in rows:
                self._conn.execute(
                    "UPDATE operations SET state=?, previous_state=?, cleanup_state=?, "
                    "terminal_reason=?, updated_at=? WHERE operation_id=?",
                    (State.FAILED_UNSAFE.value, prev, CleanupState.FROZEN.value,
                     "orphan recovered after restart", now, op_id),
                )
                swept.append(op_id)
            self._conn.commit()
        return swept

    def close(self) -> None:
        self._conn.close()
