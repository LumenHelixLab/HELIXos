"""Babel Dispatcher — the traffic cop (spec §1 Layer 4, §3, §4.3).

Responsibilities:

* Parse the **Triangulated Bus** message ``[ Ptr | Verb | Hash ]`` — a
  zero-trust array of (natural pointer, Unicode verb, cryptographic HexHash).
* Enforce the ``0x18`` CANCEL failsafe.
* On an invalid HexHash or an unrecoverable error-correction failure, fire the
  Projective Collapse: dump to the zero-divisor "Fusion Hole," collapse to
  ``COLLAPSE_NODE`` (7), and request an AKASH restore.

Not implemented yet. The hash verification contract must be fixed before this is
safe to run (an unverified dispatcher would defeat the zero-trust guarantee).
"""

from __future__ import annotations

CANCEL_BYTE = 0x18  # spec §3 "Lower ASCII (Assy)" and §4.3


def parse_bus(frame: bytes) -> tuple[int, str, str]:
    """Parse a Triangulated Bus frame into ``(ptr, verb, hexhash)``."""
    raise NotImplementedError(
        "Triangulated Bus wire format and HexHash algorithm are unspecified; "
        "define both before parsing untrusted frames."
    )
