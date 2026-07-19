"""Corrected KNOTstore triangulated-bus wrapper (SPEC §3.3; audit §6.1 adapted).

Fixes audit findings (see ../HELIXos_Handoff_Audit.md):
- AUD-C1 (dead "system firewall"): the verify path actually parses the
  instruction and recomputes the tag; it is fail-closed — returns False on
  ANY exception (logged), never raises into the agent loop.
- AUD-C4 (HMAC bound only the payload): the MAC/signature input is a
  length-prefixed canonical encoding of ptr | verb | stored-envelope, so all
  three axes are authenticated.
- AUD-C5 (deterministic MAC, unlimited replay): a freshness envelope
  {"ts","nonce","body"} rides inside the stored payload (preserving the
  3-axis wire format); the verifier enforces a 300 s clock-skew window and a
  seen-nonce replay cache with expiry sweep.
- AUD-H1 (48-bit tags): HMAC mode uses 128-bit tags (22 base64url chars);
  Ed25519 mode carries the full 64-byte signature (86 chars, ADR-004); the
  BUS_RE tag class is {22,86}.
- AUD-H3 (hardcoded phase=0): ``expected_phase`` is caller-supplied; one
  canonical tag representation (base64url, no padding) at the ABI boundary.
- AUD-H4 (silent failures): the store's return pointer is strictly validated
  at generation; generation raises on invalid inputs (dev-visible) while
  verify never raises.

Backend indirection (AUD-H6): ``KnotBackend`` protocol with ``SimBackend``
(default, wraps knotcore_sim) and ``SidecarBackend`` (wraps
sidecar_client.KnotClient; the import is deferred to the constructor so the
wrapper has no hard sidecar dependency).

TEST-ONLY determinism hooks (never use in production): the ``_now`` /
``_nonce`` keyword parameters on generate_triangulated_instruction, and the
module-level ``_time_now`` / ``_nonce_bytes`` indirections, let the golden
vectors pin fixed timestamps and nonces. Verification always uses the real
wall clock.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time
from typing import Protocol

import knotcore_sim

log = logging.getLogger("helix.knot")

__all__ = [
    "BUS_RE",
    "ALLOWED_VERBS",
    "TAG_BYTES",
    "MAX_CLOCK_SKEW_S",
    "MAX_PAYLOAD_BYTES",
    "KnotBackend",
    "SimBackend",
    "SidecarBackend",
    "generate_triangulated_instruction",
    "verify_instruction_lock",
]

BUS_RE = re.compile(
    r"^\[ (?P<ptr>[0-9a-f]{16}) \| (?P<verb>[A-Z0-9_]{1,16}) \| "
    r"#(?P<tag>[A-Za-z0-9_-]{22,86}) \]$"
)
ALLOWED_VERBS = frozenset({"READ", "WRITE", "EXEC", "ARCHIVE"})
TAG_BYTES = 16               # HMAC mode tag size; Ed25519 mode uses 64 (see signers.py)
MAX_CLOCK_SKEW_S = 300       # freshness window for the ts envelope field
MAX_PAYLOAD_BYTES = 4096     # payload cap against store exhaustion (AUD-L2)
_NONCE_BYTES = 12            # 12 random bytes -> 16 base64url chars in the envelope
_PTR_RE = re.compile(r"[0-9a-f]{16}")


class KnotBackend(Protocol):
    """Storage backend contract shared by the simulator and the sidecar."""

    def store_instruction(self, payload: str) -> str: ...

    def fetch_payload(self, ptr: str) -> str | None: ...

    def verify_cauldron_phase(self, ptr: str, tag: str, phase: int) -> bool: ...


class SimBackend:
    """KnotBackend over the in-process reference simulator (default)."""

    def store_instruction(self, payload: str) -> str:
        return knotcore_sim.store_instruction(payload)

    def fetch_payload(self, ptr: str) -> str | None:
        return knotcore_sim.fetch_payload(ptr)

    def verify_cauldron_phase(self, ptr: str, tag: str, phase: int) -> bool:
        return knotcore_sim.verify_cauldron_phase(ptr, tag, phase)


class SidecarBackend:
    """KnotBackend over the knotcore sidecar process (Unix-socket RPC).

    The sidecar_client import is deferred to this constructor so importing
    the wrapper never requires the sidecar package.
    """

    def __init__(self, socket_path: str | None = None, **client_kwargs):
        from sidecar_client import KnotClient  # deferred: no hard dependency

        self._client = KnotClient(socket_path, **client_kwargs)

    def store_instruction(self, payload: str) -> str:
        return self._client.store_instruction(payload)

    def fetch_payload(self, ptr: str) -> str | None:
        return self._client.fetch_payload(ptr)

    def verify_cauldron_phase(self, ptr: str, tag: str, phase: int) -> bool:
        return self._client.verify_cauldron_phase(ptr, tag, phase)


_default_backend_lock = threading.Lock()
_default_backend: KnotBackend | None = None


def _get_default_backend() -> KnotBackend:
    global _default_backend
    with _default_backend_lock:
        if _default_backend is None:
            _default_backend = SimBackend()
    return _default_backend


def _time_now() -> float:
    """Wall-clock indirection — TEST-ONLY monkeypatch point (production: time.time)."""
    return time.time()


def _nonce_bytes() -> bytes:
    """Randomness indirection — TEST-ONLY monkeypatch point (production: os.urandom)."""
    return os.urandom(_NONCE_BYTES)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _canonical(ptr_hex: str, verb: str, envelope_text: str) -> bytes:
    """Length-prefixed canonical MAC/signature input: binds ALL three axes (AUD-C4)."""
    blob = bytearray(b"HELIX-BUS/2")
    for part in (
        bytes.fromhex(ptr_hex),
        verb.encode("utf-8"),
        envelope_text.encode("utf-8"),
    ):
        blob += len(part).to_bytes(4, "big") + part
    return bytes(blob)


def generate_triangulated_instruction(
    payload_text: str,
    action_verb_unicode: str,
    signer,
    backend: KnotBackend | None = None,
    *,
    _now: float | None = None,
    _nonce: bytes | None = None,
) -> str:
    """Build a ``[ {ptr} | {verb} | #{tag} ]`` bus line for ``payload_text``.

    Validates inputs and raises ValueError (dev-visible) on a bad payload,
    verb, signer or injected test hook; RuntimeError if the backend returns a
    malformed pointer (AUD-H4). ``_now``/``_nonce`` are TEST-ONLY determinism
    hooks for golden vectors.
    """
    if not isinstance(payload_text, str) or not payload_text.strip():
        raise ValueError("payload_text must be a non-empty str")
    payload_size = len(payload_text.encode("utf-8"))
    if payload_size > MAX_PAYLOAD_BYTES:
        raise ValueError(
            f"payload_text is {payload_size} bytes; cap is {MAX_PAYLOAD_BYTES} (AUD-L2)"
        )
    if not isinstance(action_verb_unicode, str):
        raise ValueError(
            f"action_verb_unicode must be str, got {type(action_verb_unicode).__name__}"
        )
    verb = action_verb_unicode.strip().upper()
    if verb not in ALLOWED_VERBS:
        raise ValueError(f"verb {verb!r} not in allowlist {sorted(ALLOWED_VERBS)}")
    if not (
        hasattr(signer, "sign")
        and hasattr(signer, "b64tag")
        and hasattr(signer, "tag_length")
    ):
        raise ValueError(
            "signer must expose sign()/b64tag()/tag_length — see signers.py"
        )
    if _now is None:
        ts = int(_time_now())
    else:
        try:
            ts = int(_now)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"_now must be a numeric timestamp (TEST-ONLY): {exc}") from exc
    if _nonce is None:
        nonce = _nonce_bytes()
        if not (isinstance(nonce, (bytes, bytearray)) and len(nonce) == _NONCE_BYTES):
            raise RuntimeError("_nonce_bytes() hook must return 12 bytes")
        nonce = bytes(nonce)
    else:
        if not isinstance(_nonce, (bytes, bytearray)) or len(_nonce) != _NONCE_BYTES:
            raise ValueError(f"_nonce must be exactly {_NONCE_BYTES} bytes (TEST-ONLY)")
        nonce = bytes(_nonce)
    envelope = json.dumps(
        {"ts": ts, "nonce": _b64url_encode(nonce), "body": payload_text},
        separators=(",", ":"),
    )
    be = backend if backend is not None else _get_default_backend()
    ptr = be.store_instruction(envelope)
    if not (isinstance(ptr, str) and _PTR_RE.fullmatch(ptr)):
        raise RuntimeError(f"KNOTstore returned an invalid pointer {ptr!r}")  # AUD-H4
    tag = signer.b64tag(signer.sign(_canonical(ptr, verb, envelope)))
    return f"[ {ptr} | {verb} | #{tag} ]"


_seen_nonces: dict[str, float] = {}
_seen_lock = threading.Lock()


def verify_instruction_lock(
    instruction: str,
    verifier,
    expected_phase: int = 0,
    backend: KnotBackend | None = None,
) -> bool:
    """Fail-closed verification ("the system firewall"): False on ANY error; never raises.

    Steps: strict BUS_RE fullmatch -> verb allowlist -> fetch envelope ->
    freshness window -> replay cache -> length-checked tag decode ->
    constant-time verify -> cauldron phase check. The nonce is admitted to the
    seen-cache only after every check passes, so a failed attempt never burns
    a legitimate instruction's nonce.
    """
    try:
        if not isinstance(instruction, str):
            return False
        m = BUS_RE.fullmatch(instruction.strip())
        if not m:
            return False
        ptr, verb, tag_text = m.group("ptr"), m.group("verb"), m.group("tag")
        if verb not in ALLOWED_VERBS:
            return False
        be = backend if backend is not None else _get_default_backend()
        envelope = be.fetch_payload(ptr)
        if envelope is None or not isinstance(envelope, str):
            return False
        env = json.loads(envelope)
        ts = int(env["ts"])
        nonce = str(env["nonce"])
        if not isinstance(env.get("body"), str):
            return False
        now = time.time()
        if abs(now - ts) > MAX_CLOCK_SKEW_S:
            return False  # stale, or future-dated beyond the skew window
        with _seen_lock:
            for seen_nonce, seen_at in list(_seen_nonces.items()):
                if now - seen_at > MAX_CLOCK_SKEW_S:
                    del _seen_nonces[seen_nonce]
            if nonce in _seen_nonces:
                return False  # replay
        tag = verifier.tag_from_b64(tag_text)  # validates charset AND length per mode
        if not verifier.verify(_canonical(ptr, verb, envelope), tag):
            return False
        if not be.verify_cauldron_phase(ptr, tag_text, int(expected_phase)):
            return False
        with _seen_lock:
            _seen_nonces[nonce] = now
        return True
    except Exception:
        log.exception("verify_instruction_lock: failing closed")
        return False
