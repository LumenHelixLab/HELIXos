"""HELIXos BABEL dispatcher — validated, audited, allowlist-based command routing.

Fixes audit findings AUD-C3 and AUD-H5 (HELIXos_Handoff_Audit.md):

* AUD-C3 — the original ``manifest()`` flowed raw, unaudited commands into an
  undefined ``BABEL`` global.  Here the dispatcher is an explicit, injectable
  object; every dispatch is validated and recorded on the audit hook before
  execution; only pre-registered verbs route anywhere (allowlist pattern).
* AUD-H5 — governance commands transiting IRC must not smuggle raw protocol
  lines.  Validation rejects ``\\r``, ``\\n`` and ``\\x00`` and caps commands at
  400 UTF-8 bytes (inside the 512-byte IRC line budget).

Contract (SPEC.md §3.5):

* ``BabelDispatcher(audit=None)`` — ``audit`` is a ``Callable[[str], None]``
  invoked with one fully formatted record per dispatch; the default hook is
  stdlib ``logging`` (logger ``helix.babel``).
* ``register(verb, handler)`` — ``handler`` receives ``list[str]`` arguments;
  its return value is passed back to the caller of ``dispatch_direct``.
* ``dispatch_direct(command)`` — validate -> audit -> execute.

``create_default_dispatcher()`` wires a small built-in safe command set
(ECHO, PING, STATUS) demonstrating the allowlist pattern.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

log = logging.getLogger("helix.babel")

MAX_COMMAND_BYTES = 400  # IRC 512-byte line budget, minus framing (AUD-H5 / AUD-L2)
ILLEGAL_CHARS = frozenset("\r\n\x00")


class CommandError(Exception):
    """A command failed validation or could not be executed."""


class UnknownCommand(CommandError):
    """The command verb is not registered with this dispatcher."""


def _default_audit(record: str) -> None:
    log.info("AUDIT %s", record)


class BabelDispatcher:
    """Allowlist command dispatcher with validation and audit.

    ``audit`` receives a single formatted string per dispatch record
    (``"babel.dispatch cmd=%r"``); pass any ``Callable[[str], None]`` to
    redirect records (journal, metrics, test capture, ...).
    """

    def __init__(self, audit: Callable[[str], None] | None = None) -> None:
        self._handlers: dict[str, Callable[[list[str]], object]] = {}
        self._audit: Callable[[str], None] = audit if audit is not None else _default_audit

    def register(self, verb: str, handler: Callable[[list[str]], object]) -> None:
        """Register ``handler`` for ``verb`` (case-insensitive, stored uppercase)."""
        if not isinstance(verb, str) or not verb.strip():
            raise ValueError("verb must be a non-empty string")
        norm = verb.strip().upper()
        if any(c.isspace() or c in ILLEGAL_CHARS for c in norm):
            raise ValueError(f"verb {verb!r} contains whitespace or control characters")
        if not callable(handler):
            raise ValueError("handler must be callable")
        self._handlers[norm] = handler

    @property
    def verbs(self) -> tuple[str, ...]:
        """Registered verbs, sorted (read-only view for introspection/STATUS)."""
        return tuple(sorted(self._handlers))

    def dispatch_direct(self, command: str) -> object:
        """Validate -> audit -> execute ``command``; return the handler's result.

        Raises :class:`CommandError` on any validation failure and
        :class:`UnknownCommand` when the verb is not registered.  Handler
        exceptions propagate unchanged (transparency, no silent failure).
        """
        self._validate(command)
        parts = command.split()
        verb, args = parts[0].upper(), parts[1:]
        handler = self._handlers.get(verb)
        if handler is None:
            raise UnknownCommand(f"unknown command: {verb!r}")
        self._audit(f"babel.dispatch cmd={command!r}")
        return handler(args)

    @staticmethod
    def _validate(command: str) -> None:
        if not isinstance(command, str):
            raise CommandError(f"command must be str, got {type(command).__name__}")
        if not command.strip():
            raise CommandError("empty command")
        if len(command.encode("utf-8")) > MAX_COMMAND_BYTES:
            raise CommandError(
                f"command too long: {len(command.encode('utf-8'))}B > {MAX_COMMAND_BYTES}B"
            )
        if any(c in command for c in ILLEGAL_CHARS):
            raise CommandError("illegal control characters (CR/LF/NUL)")


def create_default_dispatcher(audit: Callable[[str], None] | None = None) -> BabelDispatcher:
    """Return a dispatcher with the built-in safe command set (ECHO, PING, STATUS).

    Demonstrates the allowlist pattern: nothing executes unless registered here.
    """
    dispatcher = BabelDispatcher(audit=audit)
    dispatcher.register("ECHO", lambda args: " ".join(args))
    dispatcher.register("PING", lambda args: "PONG")
    dispatcher.register(
        "STATUS",
        lambda args: {"status": "ok", "registered_verbs": list(dispatcher.verbs)},
    )
    return dispatcher
