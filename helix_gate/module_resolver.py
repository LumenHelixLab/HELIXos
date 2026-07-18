"""Module resolution + digest binding (closes reviewer finding 3).

The signature binds authorization to *specific executable code* only if the
envelope carries an immutable execution reference and the adapter verifies it.
This module resolves ``module.artifact_ref`` (a ``knot://<digest>`` URI) through
a content-addressed store, then verifies:

* the resolved bytes hash to ``module.sha256`` (else digest mismatch);
* ``interface_version`` is one the runtime profile understands.

``ModuleStore`` is the seam for AKASH / ``knot_api_wrapper``. The bundled
``LocalModuleStore`` is a filesystem content-addressed store used for
tests/demo; the real AKASH native binding is a deferred backend behind the same
interface.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Protocol

from .errors import ReasonCode, ValidationRejection

SUPPORTED_INTERFACE_VERSIONS = frozenset({"helix:execution@1.0.0"})
_KNOT_SCHEME = "knot://"


@dataclass(frozen=True)
class ResolvedModule:
    wasm: bytes
    sha256: str
    entrypoint: str
    interface_version: str


class ModuleStore(Protocol):
    """Content-addressed artifact store. AKASH/knot binding implements this."""

    def fetch(self, digest: str) -> bytes | None: ...


class LocalModuleStore:
    """Filesystem content-addressed store: ``<root>/<sha256>.wasm``."""

    def __init__(self, root: str) -> None:
        self._root = root

    def put(self, wasm: bytes) -> str:
        digest = hashlib.sha256(wasm).hexdigest()
        os.makedirs(self._root, exist_ok=True)
        with open(os.path.join(self._root, digest + ".wasm"), "wb") as fh:
            fh.write(wasm)
        return digest

    def fetch(self, digest: str) -> bytes | None:
        path = os.path.join(self._root, digest + ".wasm")
        if not os.path.exists(path):
            return None
        with open(path, "rb") as fh:
            return fh.read()


def resolve_module(envelope, store: ModuleStore) -> ResolvedModule:
    """Resolve + digest-verify the envelope's module, or raise ValidationRejection."""
    module = envelope.module
    if module["interface_version"] not in SUPPORTED_INTERFACE_VERSIONS:
        raise ValidationRejection(
            ReasonCode.REJECTED_MODULE_INTERFACE_MISMATCH,
            f"interface {module['interface_version']!r} unsupported",
        )

    ref = module["artifact_ref"]
    if not ref.startswith(_KNOT_SCHEME):
        raise ValidationRejection(
            ReasonCode.REJECTED_MODULE_NOT_FOUND, f"bad artifact_ref {ref!r}"
        )
    digest_ref = ref[len(_KNOT_SCHEME):]

    wasm = store.fetch(digest_ref)
    if wasm is None:
        raise ValidationRejection(
            ReasonCode.REJECTED_MODULE_NOT_FOUND, f"artifact {digest_ref} absent"
        )

    actual = hashlib.sha256(wasm).hexdigest()
    # Both the artifact_ref digest and the signed sha256 must match the bytes;
    # this binds the signature to exactly these bytes.
    if actual != module["sha256"] or actual != digest_ref:
        raise ValidationRejection(
            ReasonCode.REJECTED_MODULE_DIGEST_MISMATCH,
            f"sha256 mismatch: bytes={actual} signed={module['sha256']} ref={digest_ref}",
        )
    return ResolvedModule(
        wasm=wasm,
        sha256=actual,
        entrypoint=module["entrypoint"],
        interface_version=module["interface_version"],
    )
