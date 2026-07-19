"""KnotClient — Unix-socket RPC client for the knotcore sidecar (fixes AUD-H6).

Implements the KnotBackend protocol against the sidecar server with the two
resilience mechanisms the audit demanded for the blackbox dependency:

- Per-call deadline: every call gets its own connection with a socket timeout
  that is re-armed against the remaining deadline on every read.
- Circuit breaker: after ``breaker_threshold`` consecutive transport failures
  the breaker opens and calls raise ``Unavailable`` immediately (no socket
  attempt) until ``breaker_reset_s`` has elapsed; the next call is then a
  half-open probe — success closes the breaker, failure re-opens it.

Error taxonomy:

- ``Unavailable`` — transport failure, deadline exceeded, framing violation,
  or breaker open. Counted toward the breaker (except the open-state reject).
- ``SidecarError`` — the server answered with an application-level error
  frame (e.g. StoreFull, KeyError). The transport is healthy, so this is NOT
  counted toward the breaker.

Socket path default: ``$HELIXOS_SIDECAR_SOCKET`` or ``/tmp/helixos-knotcore.sock``.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

try:  # flat import namespace (root conftest.py puts this dir on sys.path)
    from rpc_protocol import (
        MAX_FRAME_BYTES,
        ProtocolError,
        encode_request,
        parse_frame,
    )
except ImportError:  # pragma: no cover - running as a loose script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from rpc_protocol import (
        MAX_FRAME_BYTES,
        ProtocolError,
        encode_request,
        parse_frame,
    )

__all__ = ["KnotClient", "Unavailable", "SidecarError", "DEFAULT_SOCKET_PATH"]

DEFAULT_SOCKET_PATH = "/tmp/helixos-knotcore.sock"


class Unavailable(RuntimeError):
    """The sidecar cannot be reached (transport/deadline) or the breaker is open."""


class SidecarError(RuntimeError):
    """The server returned an application-level error frame."""


class KnotClient:
    """RPC client for the knotcore sidecar; one short-lived connection per call."""

    def __init__(
        self,
        socket_path: str | None = None,
        timeout: float = 2.0,
        breaker_threshold: int = 5,
        breaker_reset_s: float = 30.0,
    ):
        self.socket_path = socket_path or os.environ.get(
            "HELIXOS_SIDECAR_SOCKET", DEFAULT_SOCKET_PATH
        )
        if timeout <= 0:
            raise ValueError(f"timeout must be > 0, got {timeout}")
        if breaker_threshold < 1:
            raise ValueError(f"breaker_threshold must be >= 1, got {breaker_threshold}")
        if breaker_reset_s < 0:
            raise ValueError(f"breaker_reset_s must be >= 0, got {breaker_reset_s}")
        self.timeout = float(timeout)
        self.breaker_threshold = int(breaker_threshold)
        self.breaker_reset_s = float(breaker_reset_s)
        self._lock = threading.Lock()
        self._next_id = 0
        self._consecutive_failures = 0
        self._breaker_opened_at: float | None = None  # monotonic ts; None = closed
        self._half_open_probe = False  # a probe call is currently admitted

    # -- public API (KnotBackend + health) ----------------------------

    def store_instruction(self, payload: str) -> str:
        return self._call("store_instruction", {"payload": payload})

    def fetch_payload(self, ptr: str) -> str | None:
        return self._call("fetch_payload", {"ptr": ptr})

    def verify_cauldron_phase(self, ptr: str, tag: str, phase: int) -> bool:
        return bool(
            self._call(
                "verify_cauldron_phase",
                {"ptr": ptr, "tag": tag, "phase": int(phase)},
            )
        )

    def bump_generation(self, ptr: str) -> int:
        return int(self._call("bump_generation", {"ptr": ptr}))

    def health(self) -> dict:
        return self._call("health", {})

    def abi_version(self) -> int:
        return int(self._call("abi_version", {}))

    # -- breaker state machine ----------------------------------------

    def _breaker_admit(self) -> None:
        """Raise Unavailable if the breaker is open and the reset has not elapsed."""
        with self._lock:
            if self._breaker_opened_at is None:
                return
            elapsed = time.monotonic() - self._breaker_opened_at
            if elapsed >= self.breaker_reset_s:
                self._half_open_probe = True  # half-open: admit one probe call
                return
            raise Unavailable(
                f"circuit breaker open; retry in {self.breaker_reset_s - elapsed:.2f}s"
            )

    def _record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._breaker_opened_at = None
            self._half_open_probe = False

    def _record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if (
                self._half_open_probe
                or self._consecutive_failures >= self.breaker_threshold
            ):
                self._breaker_opened_at = time.monotonic()
                self._half_open_probe = False

    # -- transport -----------------------------------------------------

    def _call(self, method: str, params: dict) -> Any:
        self._breaker_admit()
        try:
            result = self._roundtrip(method, params)
        except (OSError, ProtocolError) as exc:
            # OSError covers connect errors, ConnectionError and TimeoutError
            # (socket.timeout is an alias of TimeoutError since 3.10).
            self._record_failure()
            raise Unavailable(f"sidecar call {method} failed: {exc}") from exc
        self._record_success()
        return result

    def _roundtrip(self, method: str, params: dict) -> Any:
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
        frame = encode_request(request_id, method, params)
        deadline = time.monotonic() + self.timeout
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(self.timeout)
            sock.connect(self.socket_path)
            sock.sendall(frame)
            buf = bytearray()
            while b"\n" not in buf:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"deadline of {self.timeout:.3f}s exceeded waiting for {method}"
                    )
                sock.settimeout(remaining)
                chunk = sock.recv(65536)
                if not chunk:
                    raise ConnectionError("sidecar closed the connection")
                buf += chunk
                if len(buf) > MAX_FRAME_BYTES:
                    raise ProtocolError(
                        f"response exceeds the {MAX_FRAME_BYTES}-byte cap"
                    )
            line, _sep, _rest = bytes(buf).partition(b"\n")
            response = parse_frame(line)
        finally:
            sock.close()
        if response["id"] != request_id:
            raise ProtocolError(
                f"response id {response['id']} does not match request id {request_id}"
            )
        if "error" in response:
            raise SidecarError(response["error"])
        return response.get("result")
