"""Newline-delimited JSON (NDJSON) RPC framing for the knotcore sidecar (AUD-H6).

One JSON object per line, UTF-8, ``\\n``-terminated, at most 1 MiB per frame:

- request:  ``{"id": int, "method": str, "params": dict}``
- response: ``{"id": int, "result": <any JSON>}`` or ``{"id": int, "error": str}``

``encode_request`` / ``encode_response`` build frames; ``parse_frame`` parses
and structurally validates one frame (request or response). All framing and
validation violations raise ``ProtocolError``. Encoders emit ASCII-only
compact JSON so frames are safe to log verbatim.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["MAX_FRAME_BYTES", "ProtocolError", "encode_request", "encode_response", "parse_frame"]

MAX_FRAME_BYTES = 1024 * 1024  # 1 MiB hard cap on any single frame


class ProtocolError(RuntimeError):
    """Raised on any framing or structural protocol violation."""


def _check_id(request_id: Any) -> int:
    if isinstance(request_id, bool) or not isinstance(request_id, int) or request_id < 0:
        raise ProtocolError(f"frame 'id' must be a non-negative int, got {request_id!r}")
    return request_id


def _encode(frame: dict) -> bytes:
    try:
        raw = json.dumps(frame, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"frame is not JSON-serializable: {exc}") from exc
    if len(raw) > MAX_FRAME_BYTES:
        raise ProtocolError(
            f"encoded frame is {len(raw)} bytes, over the {MAX_FRAME_BYTES}-byte cap"
        )
    return raw


def encode_request(request_id: int, method: str, params: dict | None = None) -> bytes:
    """Encode ``{"id":id,"method":method,"params":params}`` as one NDJSON frame."""
    _check_id(request_id)
    if not isinstance(method, str) or not method:
        raise ProtocolError(f"method must be a non-empty str, got {method!r}")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ProtocolError(f"params must be a dict, got {type(params).__name__}")
    return _encode({"id": request_id, "method": method, "params": params})


def encode_response(request_id: int, result: Any = None, error: str | None = None) -> bytes:
    """Encode a success (``result``) or failure (``error``) response frame.

    ``error`` wins when both are given; a response carries exactly one of the
    two fields.
    """
    _check_id(request_id)
    if error is not None:
        if not isinstance(error, str):
            error = str(error)
        return _encode({"id": request_id, "error": error})
    return _encode({"id": request_id, "result": result})


def parse_frame(raw: bytes | str) -> dict:
    """Parse and structurally validate one NDJSON frame; returns the dict.

    Accepts the frame with or without its trailing newline. Raises
    ProtocolError if the frame exceeds the 1 MiB cap, is not valid JSON, is
    not an object, has an invalid id, or is neither a well-formed request
    (``method`` str + optional ``params`` dict) nor a well-formed response
    (exactly one of ``result`` / ``error`` str).
    """
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, (bytes, bytearray)):
        raise ProtocolError(f"frame must be bytes, got {type(raw).__name__}")
    raw = bytes(raw)
    if len(raw) > MAX_FRAME_BYTES:
        raise ProtocolError(
            f"frame is {len(raw)} bytes, over the {MAX_FRAME_BYTES}-byte cap"
        )
    stripped = raw.strip()
    if not stripped:
        raise ProtocolError("empty frame")
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"invalid JSON frame: {exc}") from exc
    if not isinstance(obj, dict):
        raise ProtocolError("frame must be a JSON object")
    if "id" not in obj:
        raise ProtocolError("frame is missing 'id'")
    _check_id(obj["id"])
    if "method" in obj:
        if not isinstance(obj["method"], str) or not obj["method"]:
            raise ProtocolError("request 'method' must be a non-empty str")
        if "params" in obj and not isinstance(obj["params"], dict):
            raise ProtocolError("request 'params' must be a dict when present")
    elif "error" in obj:
        if "result" in obj:
            raise ProtocolError("response must carry exactly one of 'result'/'error'")
        if not isinstance(obj["error"], str):
            raise ProtocolError("response 'error' must be a str")
    elif "result" not in obj:
        raise ProtocolError("frame is neither a request nor a response")
    return obj
