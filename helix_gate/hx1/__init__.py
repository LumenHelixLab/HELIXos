"""HX1 — the signed envelope format ingested by Gate 2.

An HX1 envelope authorizes exactly one execution. Its signed fields are
canonicalized (:mod:`helix_gate.hx1.canonical`) before an Ed25519 signature is
computed/verified (:mod:`helix_gate.hx1.signature`), so logically identical
envelopes cannot present multiple serializations to the trust boundary.
"""

from __future__ import annotations

from .canonical import canonical_bytes
from .schema import HX1_SCHEMA_VERSION, Envelope, decode_envelope
from .signature import Key, KeyRing, KeyStatus, verify_signature

__all__ = [
    "Envelope",
    "decode_envelope",
    "HX1_SCHEMA_VERSION",
    "canonical_bytes",
    "KeyRing",
    "Key",
    "KeyStatus",
    "verify_signature",
]
