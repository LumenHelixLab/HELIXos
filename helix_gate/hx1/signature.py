"""Ed25519 signature verification + keyring (closes reviewer finding 1).

A single ``authorized_public_key`` is insufficient for rotation, revocation,
staging, and multiple approved issuers, so verification runs against a
:class:`KeyRing` of keys addressed by ``key_id``, each bound to an issuer and a
status. Verification:

1. **Algorithm-confusion guard** — only ``ed25519`` is accepted; any other
   ``alg`` is rejected outright (never inferred from the key).
2. **Key lookup** — ``key_id`` must resolve to a known key.
3. **Revocation** — a revoked key is rejected.
4. **Issuer binding** — the key's bound issuer must match the envelope issuer.
5. **Signature** — Ed25519 verify over the *canonical* signed-content bytes.

Signing (:func:`sign_envelope`) is provided for test/demo issuers only; in
production the private key lives in a KMS/HSM (a deferred seam).
"""

from __future__ import annotations

import base64
import enum
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from ..errors import ReasonCode, ValidationRejection
from .canonical import canonical_bytes

ALG_ED25519 = "ed25519"


class KeyStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


@dataclass(frozen=True)
class Key:
    key_id: str
    issuer: str
    public_key: Ed25519PublicKey
    status: KeyStatus = KeyStatus.ACTIVE


class KeyRing:
    """A set of trusted verification keys addressed by ``key_id``."""

    def __init__(self, keys: list[Key] | None = None) -> None:
        self._keys: dict[str, Key] = {}
        for k in keys or []:
            self.add(k)

    def add(self, key: Key) -> None:
        self._keys[key.key_id] = key

    def revoke(self, key_id: str) -> None:
        k = self._keys.get(key_id)
        if k is not None:
            self._keys[key_id] = Key(k.key_id, k.issuer, k.public_key, KeyStatus.REVOKED)

    def get(self, key_id: str) -> Key | None:
        return self._keys.get(key_id)


def verify_signature(envelope, keyring: KeyRing) -> Key:
    """Verify ``envelope``'s signature against ``keyring``; return the used key.

    Raises :class:`ValidationRejection` with a stable reason code on any failure.
    ``envelope`` is a :class:`~helix_gate.hx1.schema.Envelope`.
    """
    sig = envelope.signature
    alg = sig.get("alg")
    if alg != ALG_ED25519:
        # Algorithm-confusion guard: never fall through to a key-implied alg.
        raise ValidationRejection(ReasonCode.REJECTED_ALG_UNSUPPORTED, f"alg={alg!r}")

    key = keyring.get(sig["key_id"])
    if key is None:
        raise ValidationRejection(
            ReasonCode.REJECTED_KEY_UNKNOWN, f"key_id={sig['key_id']!r}"
        )
    if key.status is not KeyStatus.ACTIVE:
        raise ValidationRejection(
            ReasonCode.REJECTED_KEY_REVOKED, f"key_id={key.key_id}"
        )
    if key.issuer != envelope.issuer:
        # The key is not authorized to speak for this issuer.
        raise ValidationRejection(
            ReasonCode.REJECTED_ISSUER_UNTRUSTED,
            f"key issuer={key.issuer} envelope issuer={envelope.issuer}",
        )

    try:
        raw_sig = base64.b64decode(sig["bytes"], validate=True)
    except (ValueError, base64.binascii.Error):
        raise ValidationRejection(
            ReasonCode.REJECTED_SIGNATURE_INVALID, "signature not base64"
        ) from None

    message = canonical_bytes(envelope.signed_content)
    try:
        key.public_key.verify(raw_sig, message)
    except InvalidSignature:
        raise ValidationRejection(
            ReasonCode.REJECTED_SIGNATURE_INVALID, "ed25519 verify failed"
        ) from None
    return key


# --- test/demo signing helper (NOT for production key custody) -------------

def sign_envelope(signed_content: dict, private_key: Ed25519PrivateKey,
                  key_id: str) -> dict:
    """Return a complete envelope dict = ``signed_content`` + a signature block.

    For issuing test/demo envelopes. Production issuers sign inside a KMS/HSM.
    """
    raw_sig = private_key.sign(canonical_bytes(signed_content))
    return {
        **signed_content,
        "signature": {
            "alg": ALG_ED25519,
            "key_id": key_id,
            "bytes": base64.b64encode(raw_sig).decode("ascii"),
        },
    }
