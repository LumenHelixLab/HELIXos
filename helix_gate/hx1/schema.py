"""HX1 v1 envelope schema + strict decode (closes reviewer finding 2).

A correctly *signed* envelope can still be *invalid* for this adapter, so the
schema is validated before anything trusts the payload. Decode is strict: every
required field must be present with the right type; unknown top-level fields are
rejected so a signature cannot silently cover unexpected content.

The signed content is every field **except** ``signature``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..errors import ReasonCode, ValidationRejection

HX1_SCHEMA_VERSION = "HX1/1.0"
ALLOWED_OPERATION = "EXECUTE_WASM_COMPONENT"

_TOP_FIELDS = {
    "schema_version": str,
    "issuer": str,
    "audience": str,
    "issued_at": int,
    "not_before": int,
    "expires_at": int,
    "nonce": str,
    "sequence": int,
    "policy_revision": str,
    "operation": str,
    "target": str,
    "module": dict,
    "capability_manifest": dict,
    "signature": dict,
}
_MODULE_FIELDS = {
    "artifact_ref": str,
    "sha256": str,
    "runtime_profile": str,
    "entrypoint": str,
    "interface_version": str,
}
_MANIFEST_FIELDS = {
    "imports": list,
    "input_objects": list,
    "output_destinations": list,
    "resource_limits": dict,
}
_LIMIT_FIELDS = (
    "fuel",
    "wall_ms",
    "memory_bytes",
    "table_elems",
    "stack_bytes",
    "output_bytes",
    "host_calls",
)
_SIGNATURE_FIELDS = {"alg": str, "key_id": str, "bytes": str}


def _require(obj: dict, spec: dict, where: str) -> None:
    for name, typ in spec.items():
        if name not in obj:
            raise ValidationRejection(
                ReasonCode.REJECTED_SCHEMA_INVALID, f"missing {where}.{name}"
            )
        val = obj[name]
        # bool is a subclass of int — reject it where an int is required.
        if typ is int and isinstance(val, bool):
            raise ValidationRejection(
                ReasonCode.REJECTED_SCHEMA_INVALID, f"{where}.{name} must be int"
            )
        if not isinstance(val, typ):
            raise ValidationRejection(
                ReasonCode.REJECTED_SCHEMA_INVALID,
                f"{where}.{name} must be {typ.__name__}",
            )


@dataclass(frozen=True)
class Envelope:
    """A decoded, schema-valid HX1 envelope (not yet signature/policy verified)."""

    signed_content: dict[str, Any]   # every field except ``signature``
    signature: dict[str, Any]        # {alg, key_id, bytes}

    # convenience typed accessors ------------------------------------------
    @property
    def issuer(self) -> str: return self.signed_content["issuer"]
    @property
    def audience(self) -> str: return self.signed_content["audience"]
    @property
    def issued_at(self) -> int: return self.signed_content["issued_at"]
    @property
    def not_before(self) -> int: return self.signed_content["not_before"]
    @property
    def expires_at(self) -> int: return self.signed_content["expires_at"]
    @property
    def nonce(self) -> str: return self.signed_content["nonce"]
    @property
    def sequence(self) -> int: return self.signed_content["sequence"]
    @property
    def policy_revision(self) -> str: return self.signed_content["policy_revision"]
    @property
    def operation(self) -> str: return self.signed_content["operation"]
    @property
    def target(self) -> str: return self.signed_content["target"]
    @property
    def module(self) -> dict: return self.signed_content["module"]
    @property
    def capability_manifest(self) -> dict:
        return self.signed_content["capability_manifest"]
    @property
    def resource_limits(self) -> dict:
        return self.capability_manifest["resource_limits"]


def decode_envelope(raw: bytes | str) -> Envelope:
    """Parse and strictly schema-validate an HX1 envelope.

    Raises :class:`ValidationRejection` with a stable reason code on any defect.
    """
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ValidationRejection(
            ReasonCode.REJECTED_MALFORMED, f"json: {type(exc).__name__}"
        ) from None
    if not isinstance(obj, dict):
        raise ValidationRejection(ReasonCode.REJECTED_MALFORMED, "not an object")

    # Schema version is checked first and specifically.
    ver = obj.get("schema_version")
    if ver != HX1_SCHEMA_VERSION:
        raise ValidationRejection(
            ReasonCode.REJECTED_SCHEMA_VERSION_UNSUPPORTED, f"version={ver!r}"
        )

    unknown = set(obj) - set(_TOP_FIELDS)
    if unknown:
        raise ValidationRejection(
            ReasonCode.REJECTED_SCHEMA_INVALID, f"unknown fields {sorted(unknown)}"
        )
    _require(obj, _TOP_FIELDS, "envelope")
    _require(obj["module"], _MODULE_FIELDS, "module")
    _require(obj["capability_manifest"], _MANIFEST_FIELDS, "capability_manifest")
    _require(obj["signature"], _SIGNATURE_FIELDS, "signature")

    limits = obj["capability_manifest"]["resource_limits"]
    for name in _LIMIT_FIELDS:
        val = limits.get(name)
        if not isinstance(val, int) or isinstance(val, bool) or val < 0:
            raise ValidationRejection(
                ReasonCode.REJECTED_SCHEMA_INVALID,
                f"resource_limits.{name} must be a non-negative int",
            )

    signed = {k: v for k, v in obj.items() if k != "signature"}
    return Envelope(signed_content=signed, signature=obj["signature"])
