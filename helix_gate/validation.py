"""The ordered HX1 validation pipeline (closes reviewer finding 2).

Runs the reviewer's mandated sequence and returns an authorized, execution-ready
request, or raises :class:`ValidationRejection` with a stable reason code:

    DECODE -> SCHEMA -> (CANONICALIZE + VERIFY SIGNATURE) -> ISSUER/AUDIENCE
      -> TIME WINDOW -> POLICY REVISION + CAPABILITY -> MODULE RESOLVE + DIGEST
      -> NONCE/SEQUENCE (replay commit) -> AUTHORIZE

Canonicalization is performed inside signature verification (the signature binds
canonical bytes). The replay *commit* is the last step before authorization, so
a later-rejected envelope does not burn a nonce/sequence — and by then the
issuer is cryptographically authenticated, so the commit is attributable.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import ReasonCode, ValidationRejection
from .hx1.schema import Envelope, decode_envelope
from .hx1.signature import Key, KeyRing, verify_signature
from .module_resolver import ModuleStore, ResolvedModule, resolve_module
from .policy import Policy
from .replay import ReplayStore


@dataclass(frozen=True)
class ValidatedRequest:
    envelope: Envelope
    module: ResolvedModule
    key: Key


def validate(
    raw: bytes | str,
    *,
    keyring: KeyRing,
    policy: Policy,
    replay: ReplayStore,
    module_store: ModuleStore,
    audience: str,
    now: int,
) -> ValidatedRequest:
    # 1. DECODE + 2. SCHEMA
    envelope = decode_envelope(raw)

    # 3. CANONICALIZE + VERIFY SIGNATURE (canonicalization happens inside)
    key = verify_signature(envelope, keyring)

    # 4. ISSUER / AUDIENCE (issuer<->key binding already checked in verify)
    if envelope.audience != audience:
        raise ValidationRejection(
            ReasonCode.REJECTED_AUDIENCE_MISMATCH,
            f"audience={envelope.audience!r} expected={audience!r}",
        )

    # 5. TIME WINDOW
    if not (envelope.not_before <= envelope.expires_at
            and envelope.issued_at <= envelope.expires_at):
        raise ValidationRejection(
            ReasonCode.REJECTED_TIME_WINDOW_INVALID,
            f"nbf={envelope.not_before} iat={envelope.issued_at} exp={envelope.expires_at}",
        )
    if now < envelope.not_before:
        raise ValidationRejection(
            ReasonCode.REJECTED_NOT_YET_VALID, f"now={now} nbf={envelope.not_before}"
        )
    if now > envelope.expires_at:
        raise ValidationRejection(
            ReasonCode.REJECTED_EXPIRED, f"now={now} exp={envelope.expires_at}"
        )

    # 6. POLICY REVISION + OPERATION + CAPABILITY MANIFEST
    policy.evaluate(envelope)

    # 7. MODULE RESOLVE + DIGEST BINDING
    module = resolve_module(envelope, module_store)

    # 8. NONCE / SEQUENCE (durable replay commit) — last, and attributable
    replay.commit_use(envelope.nonce, envelope.issuer, envelope.sequence, now)

    # 9. AUTHORIZED
    return ValidatedRequest(envelope=envelope, module=module, key=key)
