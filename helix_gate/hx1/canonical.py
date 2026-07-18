"""Canonical serialization for HX1 signed fields (closes reviewer finding 1).

The signature must bind a *canonical byte representation*, not a mutable Python
dict — logically identical dicts otherwise have many serializations, which
defeats the trust boundary.

This is a JCS-profile (RFC 8785) canonicalization restricted to the value types
HX1 uses: objects (keys sorted, UTF-8), arrays, strings, booleans, ``null``, and
**integers only**. Floats are rejected on purpose: IEEE-754 canonical number
formatting is a well-known footgun, and no HX1 signed field needs a fractional
value (timestamps and limits are integers). Rejecting them keeps signing
unambiguous.
"""

from __future__ import annotations

import json
from typing import Any


class CanonicalizationError(ValueError):
    """A value cannot be canonicalized (e.g. a float or non-string key)."""


def _check(value: Any, path: str = "$") -> None:
    if isinstance(value, bool):
        return
    if value is None or isinstance(value, (str, int)):
        return
    if isinstance(value, float):
        raise CanonicalizationError(
            f"float not permitted in HX1 signed content at {path}"
        )
    if isinstance(value, list):
        for i, item in enumerate(value):
            _check(item, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError(f"non-string key at {path}")
            _check(item, f"{path}.{key}")
        return
    raise CanonicalizationError(
        f"unsupported type {type(value).__name__} at {path}"
    )


def canonical_bytes(value: Any) -> bytes:
    """Return the canonical UTF-8 bytes for ``value``.

    ``bool`` is checked before ``int`` because ``bool`` is a subclass of ``int``
    in Python; ``json.dumps`` already emits ``true``/``false`` correctly, but the
    type guard keeps the contract explicit.
    """
    _check(value)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
