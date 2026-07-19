"""HELIXos epoch fencing — the monotonic clock of the Projective Collapse protocol.

Fixes audit finding AUD-C8 (HELIXos_Handoff_Audit.md): "Projective Collapse"
was a name, not a design — no epoch announcement meant peers could act on
stale state (split-brain).  The protocol is:

    collapse -> increment the epoch -> rebuild state from snapshot + journal
    replay (journal.py is the system of record, ADR-001) -> broadcast the new
    epoch -> peers fence every message carrying an older epoch.

Contract (SPEC.md §3.8): ``EpochFence.current`` is the live epoch;
``increment()`` advances it and returns the new value (the Collapse trigger);
``fences(epoch)`` is True exactly when ``epoch < current`` — i.e. the message
is stale and must be fenced (dropped).  All operations are thread-safe; one
``EpochFence`` per node owns the local epoch.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger("helix.memory.epochs")


class EpochFence:
    """Thread-safe monotonic epoch with stale-message fencing."""

    def __init__(self, epoch: int = 0) -> None:
        if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
            raise ValueError("epoch must be a non-negative int")
        self._epoch = epoch
        self._lock = threading.Lock()

    @property
    def current(self) -> int:
        """The live epoch."""
        with self._lock:
            return self._epoch

    def increment(self) -> int:
        """Advance the epoch (Projective Collapse trigger); return the new value."""
        with self._lock:
            self._epoch += 1
            new = self._epoch
        log.info("epoch.incremented new=%d", new)  # Collapse triggers are rare and auditable
        return new

    def fences(self, epoch: int) -> bool:
        """True if ``epoch`` is stale (< current) and must be fenced."""
        if not isinstance(epoch, int) or isinstance(epoch, bool):
            raise ValueError("epoch must be an int")
        with self._lock:
            return epoch < self._epoch
