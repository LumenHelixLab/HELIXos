"""HELIXos possession gate — authenticated owner override for the KRISHNA agent.

Adopted from the corrected reference in HELIXos_Handoff_Audit.md §6.2 and
adapted per SPEC.md §3.6.  Fixes audit findings AUD-C2, AUD-C3 and AUD-M4:

* AUD-C2 — ``owner_token`` is no longer accepted-and-ignored: candidates are
  SHA-256 hashed and compared with :func:`hmac.compare_digest` against the
  stored hash (the raw token is never stored), bad tokens are audited, and
  brute force is throttled to 5 failures per 300 s window.
* AUD-C3 — the dispatcher is injected (duck-typed: any object with
  ``.dispatch_direct(str)``), commands are validated (<=400 UTF-8 bytes, no
  CR/LF/NUL), every transition and dispatch is audited, and refusal is an
  explicit :class:`PossessionDenied` — never a silent ``None``.
* AUD-M4 — the toggle runs under :class:`threading.Lock`, construction is
  KRISHNA-only, and every successful toggle increments a monotonic
  ``fencing_token`` (split-brain fencing per AUD-C8's epoch model) which is
  included in audit records and in the returned state string.

The owner token enters only via the environment: use
:meth:`KrishnaManifestor.from_env` (reads ``HELIXOS_OWNER_TOKEN``) or hash it
yourself with :meth:`KrishnaManifestor.hash_token`.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import threading
import time
from collections.abc import Callable
from typing import Protocol

log = logging.getLogger("helix.possession")

RATE_LIMIT, RATE_WINDOW = 5, 300.0  # SPEC defaults: 5 failures / 300 s
MAX_COMMAND_BYTES = 400             # IRC 512-byte line budget
OWNER_TOKEN_ENV = "HELIXOS_OWNER_TOKEN"


class PossessionError(Exception):
    """Base error for the possession gate."""


class PossessionDenied(PossessionError):
    """Explicit refusal: bad token, rate limited, or not possessed."""


class _DirectDispatcher(Protocol):
    """Duck-typed dispatcher contract (SPEC §3.6)."""

    def dispatch_direct(self, command: str) -> object: ...


def _default_audit(record: str) -> None:
    log.info("AUDIT %s", record)


class KrishnaManifestor:
    """Authenticated, rate-limited, fencing-token possession gate (KRISHNA only)."""

    ALLOWED_AGENT = "KRISHNA"

    def __init__(
        self,
        agent_id: str,
        owner_token_hash: bytes,
        dispatcher: _DirectDispatcher,
        audit: Callable[[str], None] | None = None,
        rate_limit: int = RATE_LIMIT,
        rate_window: float = RATE_WINDOW,
    ) -> None:
        if agent_id != self.ALLOWED_AGENT:  # enforces the L89 advisory (AUD-M3)
            raise ValueError("KrishnaManifestor may only wrap the KRISHNA agent")
        if not isinstance(owner_token_hash, (bytes, bytearray)) or len(owner_token_hash) != 32:
            raise ValueError("owner_token_hash must be a SHA-256 digest")
        if rate_limit < 1 or rate_window <= 0:
            raise ValueError("rate_limit must be >= 1 and rate_window > 0")
        self.agent_id = agent_id
        self._owner_token_hash = bytes(owner_token_hash)  # hash only; token never stored
        self._dispatcher = dispatcher                     # injected; no undefined BABEL global
        self._audit: Callable[[str], None] = audit if audit is not None else _default_audit
        self._rate_limit = rate_limit
        self._rate_window = rate_window
        self.possessed_by_owner = False
        self.fencing_token: int = 0                       # monotonic fence (AUD-M4/C8)
        self._lock = threading.Lock()
        self._failures: list[float] = []

    @classmethod
    def from_env(
        cls,
        agent_id: str,
        dispatcher: _DirectDispatcher,
        audit: Callable[[str], None] | None = None,
    ) -> "KrishnaManifestor":
        """Construct with the owner token read from ``HELIXOS_OWNER_TOKEN``."""
        token = os.environ.get(OWNER_TOKEN_ENV)
        if not token:
            raise ValueError(f"{OWNER_TOKEN_ENV} is not set")
        return cls(agent_id, cls.hash_token(token.encode("utf-8")), dispatcher, audit=audit)

    @staticmethod
    def hash_token(token: bytes) -> bytes:
        """SHA-256 hash of an owner token — the only form ever stored."""
        return hashlib.sha256(token).digest()

    def _throttle(self) -> None:
        now = time.monotonic()
        self._failures = [t for t in self._failures if now - t < self._rate_window]
        if len(self._failures) >= self._rate_limit:
            self._audit(
                f"possession.rate_limited agent={self.agent_id} fence={self.fencing_token}"
            )
            raise PossessionDenied("rate limited")

    def toggle_possession(self, owner_token: bytes) -> str:
        """Flip possession if ``owner_token`` matches; return the fenced state string."""
        self._throttle()  # brute-force surface (AUD-H5)
        candidate = hashlib.sha256(
            owner_token if isinstance(owner_token, bytes) else str(owner_token).encode()
        ).digest()
        if not hmac.compare_digest(candidate, self._owner_token_hash):  # AUD-C2
            self._failures.append(time.monotonic())
            self._audit(f"possession.denied agent={self.agent_id} fence={self.fencing_token}")
            raise PossessionDenied("invalid owner token")
        with self._lock:  # AUD-M4 race; flip and fence advance atomically
            self.possessed_by_owner = not self.possessed_by_owner
            self.fencing_token += 1
            state, fence = self.possessed_by_owner, self.fencing_token
        self._audit(f"possession.toggled agent={self.agent_id} state={state} fence={fence}")
        return f"MANIFESTOR_MODE: {state} FENCE: {fence}"

    def manifest(self, command: str) -> object:
        """Dispatch ``command`` while possessed; explicit refusal otherwise."""
        if not self.possessed_by_owner:
            self._audit(
                f"manifest.refused agent={self.agent_id} reason=not_possessed "
                f"fence={self.fencing_token}"
            )
            raise PossessionDenied("not possessed")  # explicit refusal, never silent None
        self._validate_command(command)
        self._audit(
            f"manifest.dispatch agent={self.agent_id} cmd={command!r} fence={self.fencing_token}"
        )
        return self._dispatcher.dispatch_direct(command)

    @staticmethod
    def _validate_command(command: str) -> None:
        if not isinstance(command, str) or not command.strip():
            raise PossessionError("empty command")
        if len(command.encode("utf-8")) > MAX_COMMAND_BYTES:  # IRC 512-byte line budget
            raise PossessionError("command too long")
        if any(c in command for c in "\r\n\x00"):  # blocks IRC line smuggling (AUD-H5)
            raise PossessionError("illegal control characters")
