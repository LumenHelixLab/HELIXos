"""Signing and verification primitives for the HELIXos triangulated bus (SPEC §3.1).

Fixes audit findings (see ../HELIXos_Handoff_Audit.md):
- AUD-C6 (symmetric-key distribution: every verifier is also a forger):
  Ed25519 mode lets agents verify with a public key while only the sequencer
  holds the 32-byte signing seed.
- AUD-H1 (tag truncated to 48 bits): HMAC mode uses a 128-bit tag (16 bytes,
  22 base64url chars); Ed25519 mode carries the FULL 64-byte signature
  (86 base64url chars) because a truncated Ed25519 signature cannot be
  independently verified (SPEC §3.1 note / ADR-004).

Wire-tag encoding is base64url without padding in both modes. Every class
exposes ``tag_length`` (bytes) plus ``b64tag`` / ``tag_from_b64`` helpers so
the wrapper can format and validate tags per mode; the wrapper's BUS_RE tag
class is ``{22,86}`` to admit both encodings. All constructors and helpers
validate inputs and raise ``ValueError`` with a clear message on misuse.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

__all__ = ["HMACSigner", "HMACVerifier", "Ed25519Signer", "Ed25519Verifier"]

_B64URL_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)

HMAC_TAG_BYTES = 16      # 128-bit truncated HMAC-SHA-256 -> 22 base64url chars
ED25519_TAG_BYTES = 64   # full Ed25519 signature -> 86 base64url chars
_ED25519_SEED_BYTES = 32
_ED25519_PUBKEY_BYTES = 32
_HMAC_MIN_KEY_BYTES = 32


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _require_bytes(value: bytes, what: str) -> bytes:
    if not isinstance(value, (bytes, bytearray)):
        raise ValueError(f"{what} must be bytes, got {type(value).__name__}")
    return bytes(value)


class _TagCodec:
    """Shared base64url-no-padding tag codec, length-checked per mode."""

    tag_length: int = 0  # subclasses override (HMAC_TAG_BYTES / ED25519_TAG_BYTES)

    def b64tag(self, raw: bytes) -> str:
        """Encode a raw tag/signature to its base64url (unpadded) wire form."""
        raw = _require_bytes(raw, "raw tag")
        if len(raw) != self.tag_length:
            raise ValueError(
                f"raw tag must be exactly {self.tag_length} bytes, got {len(raw)}"
            )
        return _b64url_encode(raw)

    def tag_from_b64(self, text: str) -> bytes:
        """Decode a wire tag back to raw bytes; ValueError on charset/length mismatch."""
        if not isinstance(text, str):
            raise ValueError(f"encoded tag must be str, got {type(text).__name__}")
        if not text or any(ch not in _B64URL_ALPHABET for ch in text):
            raise ValueError("encoded tag contains non-base64url characters")
        try:
            raw = _b64url_decode(text)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"encoded tag is not valid base64url: {exc}") from exc
        if len(raw) != self.tag_length:
            raise ValueError(
                f"encoded tag decodes to {len(raw)} bytes, expected {self.tag_length}"
            )
        return raw


def _check_hmac_key(key: bytes) -> bytes:
    key = _require_bytes(key, "HMAC key")
    if len(key) < _HMAC_MIN_KEY_BYTES:
        raise ValueError(
            f"HMAC key must be >= {_HMAC_MIN_KEY_BYTES} bytes, got {len(key)}"
        )
    return key


class HMACSigner(_TagCodec):
    """Dev-mode signer: HMAC-SHA-256 truncated to 16 bytes (AUD-C6 dev keying)."""

    tag_length = HMAC_TAG_BYTES

    def __init__(self, key: bytes):
        self._key = _check_hmac_key(key)

    def sign(self, msg: bytes) -> bytes:
        """Return the 16-byte tag (HMAC-SHA-256 truncated to 128 bits)."""
        msg = _require_bytes(msg, "msg")
        return hmac.new(self._key, msg, hashlib.sha256).digest()[: self.tag_length]


class HMACVerifier(_TagCodec):
    """Dev-mode verifier for HMAC tags; constant-time comparison."""

    tag_length = HMAC_TAG_BYTES

    def __init__(self, key: bytes):
        self._key = _check_hmac_key(key)

    def verify(self, msg: bytes, tag: bytes) -> bool:
        msg = _require_bytes(msg, "msg")
        tag = _require_bytes(tag, "tag")
        if len(tag) != self.tag_length:
            return False
        expected = hmac.new(self._key, msg, hashlib.sha256).digest()[: self.tag_length]
        return hmac.compare_digest(expected, tag)


class Ed25519Signer(_TagCodec):
    """Production signer (AUD-C6): Ed25519 over a raw 32-byte seed.

    ``sign`` returns the FULL 64-byte signature; truncation would make the
    signature unverifiable without the private key (SPEC §3.1 note / ADR-004),
    so the wire tag is the full signature base64url'd (86 chars).
    """

    tag_length = ED25519_TAG_BYTES

    def __init__(self, private_key_bytes: bytes):
        seed = _require_bytes(private_key_bytes, "Ed25519 private key seed")
        if len(seed) != _ED25519_SEED_BYTES:
            raise ValueError(
                f"Ed25519 private key must be a raw {_ED25519_SEED_BYTES}-byte seed, "
                f"got {len(seed)} bytes"
            )
        self._sk = Ed25519PrivateKey.from_private_bytes(seed)

    @classmethod
    def generate(cls) -> "Ed25519Signer":
        """Create a signer from a freshly generated random key."""
        sk = Ed25519PrivateKey.generate()
        seed = sk.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        return cls(seed)

    def public_key_bytes(self) -> bytes:
        """Raw 32-byte public key, suitable for ``Ed25519Verifier``."""
        return self._sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    def sign(self, msg: bytes) -> bytes:
        """Return the full deterministic 64-byte Ed25519 signature."""
        msg = _require_bytes(msg, "msg")
        return self._sk.sign(msg)


class Ed25519Verifier(_TagCodec):
    """Production verifier: library Ed25519 verify over the full 64-byte signature."""

    tag_length = ED25519_TAG_BYTES

    def __init__(self, public_key_bytes: bytes):
        raw = _require_bytes(public_key_bytes, "Ed25519 public key")
        if len(raw) != _ED25519_PUBKEY_BYTES:
            raise ValueError(
                f"Ed25519 public key must be {_ED25519_PUBKEY_BYTES} raw bytes, "
                f"got {len(raw)}"
            )
        try:
            self._pk = Ed25519PublicKey.from_public_bytes(raw)
        except ValueError as exc:
            raise ValueError(f"invalid Ed25519 public key: {exc}") from exc

    def verify(self, msg: bytes, tag: bytes) -> bool:
        msg = _require_bytes(msg, "msg")
        tag = _require_bytes(tag, "tag")
        if len(tag) != self.tag_length:
            return False
        try:
            self._pk.verify(tag, msg)
        except InvalidSignature:
            return False
        return True
