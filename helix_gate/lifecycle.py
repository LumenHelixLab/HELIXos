"""Deterministic execution lifecycle (closes reviewer finding 8).

Draft 1 "deleted the sandbox entry" on completion, so COMPLETED, CANCELLED, and
FAILED became indistinguishable from an unknown sandbox afterwards. Here the
lifecycle is an explicit state machine with **retained** records, and the two
dimensions the reviewer asked to separate are kept apart:

* ``State``       — the execution lifecycle (terminates in one named outcome);
* ``CleanupState``— the resource-reclamation dimension (``ACTIVE`` -> ``FROZEN``
  -> ``CLEANED_UP``). A record is never deleted, so re-inspection always
  distinguishes a terminal outcome from "unknown".

The reviewer's ``FROZEN`` holding state maps onto ``CleanupState.FROZEN``: an
abnormal terminal (``TIMED_OUT``/``TRAPPED``/``FAILED_UNSAFE``) freezes resources
for quarantine before they are reclaimed.
"""

from __future__ import annotations

import enum


class State(str, enum.Enum):
    RECEIVED = "RECEIVED"
    VALIDATING = "VALIDATING"
    REJECTED = "REJECTED"            # terminal
    AUTHORIZED = "AUTHORIZED"
    QUEUED = "QUEUED"
    INITIALIZING = "INITIALIZING"
    RUNNING = "RUNNING"
    CANCELLING = "CANCELLING"
    COMPLETED = "COMPLETED"          # terminal
    CANCELLED = "CANCELLED"          # terminal
    TIMED_OUT = "TIMED_OUT"          # terminal
    TRAPPED = "TRAPPED"              # terminal
    FAILED_UNSAFE = "FAILED_UNSAFE"  # terminal


class CleanupState(str, enum.Enum):
    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"
    CLEANED_UP = "CLEANED_UP"


_TERMINAL: frozenset[State] = frozenset({
    State.REJECTED, State.COMPLETED, State.CANCELLED,
    State.TIMED_OUT, State.TRAPPED, State.FAILED_UNSAFE,
})

# Abnormal terminals whose resources must be FROZEN before cleanup.
_ABNORMAL: frozenset[State] = frozenset({
    State.TIMED_OUT, State.TRAPPED, State.FAILED_UNSAFE,
})

_TRANSITIONS: dict[State, frozenset[State]] = {
    State.RECEIVED: frozenset({State.VALIDATING}),
    State.VALIDATING: frozenset({State.AUTHORIZED, State.REJECTED}),
    State.AUTHORIZED: frozenset({State.QUEUED}),
    State.QUEUED: frozenset({State.INITIALIZING, State.CANCELLING}),
    State.INITIALIZING: frozenset({State.RUNNING, State.CANCELLING, State.FAILED_UNSAFE}),
    State.RUNNING: frozenset({
        State.COMPLETED, State.CANCELLING, State.TIMED_OUT,
        State.TRAPPED, State.FAILED_UNSAFE,
    }),
    State.CANCELLING: frozenset({State.CANCELLED, State.FAILED_UNSAFE}),
}


def is_terminal(state: State) -> bool:
    return state in _TERMINAL


def is_abnormal_terminal(state: State) -> bool:
    return state in _ABNORMAL


def can_transition(src: State, dst: State) -> bool:
    return dst in _TRANSITIONS.get(src, frozenset())


class IllegalTransition(Exception):
    def __init__(self, src: State, dst: State) -> None:
        super().__init__(f"illegal transition {src.value} -> {dst.value}")
        self.src, self.dst = src, dst
