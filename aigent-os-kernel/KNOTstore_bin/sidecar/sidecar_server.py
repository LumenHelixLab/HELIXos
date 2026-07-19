"""Unix-socket sidecar server for the KNOTstore (fixes AUD-H6).

Moves the (currently simulated, later proprietary) knotcore out of the agent
process and puts it behind versioned IPC: NDJSON RPC (rpc_protocol.py) over a
Unix socket, one thread per connection, ABI handshake via the ``health`` and
``abi_version`` methods, graceful SIGTERM/SIGINT shutdown. The dispatch table
mirrors the knotcore_sim module ABI exactly, so the real knotcore.so can later
be dropped in behind this server without client changes (ADR-002).

Socket path: ``$HELIXOS_SIDECAR_SOCKET`` or ``/tmp/helixos-knotcore.sock``.
A stale socket file left by a crashed daemon is unlinked before bind, and the
socket file is removed again on shutdown.

Run standalone: ``python3 sidecar_server.py [socket_path]``.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import sys
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("helix.knot.sidecar")

ABI_VERSION = 1
DEFAULT_SOCKET_PATH = "/tmp/helixos-knotcore.sock"

try:  # flat import namespace (root conftest.py puts both dirs on sys.path)
    import knotcore_sim
    from rpc_protocol import (
        MAX_FRAME_BYTES,
        ProtocolError,
        encode_response,
        parse_frame,
    )
except ImportError:  # pragma: no cover - running as a loose script
    _HERE = Path(__file__).resolve().parent
    sys.path.insert(0, str(_HERE))
    sys.path.insert(0, str(_HERE.parent))
    import knotcore_sim
    from rpc_protocol import (
        MAX_FRAME_BYTES,
        ProtocolError,
        encode_response,
        parse_frame,
    )

_METHODS = (
    "store_instruction",
    "fetch_payload",
    "verify_cauldron_phase",
    "bump_generation",
    "health",
    "abi_version",
)


def _need_str(params: dict, key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str):
        raise ProtocolError(f"params.{key} must be str, got {type(value).__name__}")
    return value


def _need_int(params: dict, key: str) -> int:
    value = params.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(f"params.{key} must be int, got {type(value).__name__}")
    return value


class KnotSidecarServer:
    """Thread-per-connection Unix-socket RPC server wrapping knotcore_sim."""

    def __init__(self, socket_path: str | None = None):
        self.socket_path = socket_path or os.environ.get(
            "HELIXOS_SIDECAR_SOCKET", DEFAULT_SOCKET_PATH
        )
        self._shutdown = threading.Event()
        self._listener: socket.socket | None = None
        self._connections: set[socket.socket] = set()
        self._conn_lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------

    def _cleanup_stale_socket(self) -> None:
        """Unlink a leftover socket file from a previous (crashed) instance."""
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            return
        log.warning("removed stale socket file %s", self.socket_path)

    def serve_forever(self) -> None:
        """Bind, listen and serve until ``shutdown`` is requested."""
        self._cleanup_stale_socket()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener = listener
        try:
            listener.bind(self.socket_path)
            os.chmod(self.socket_path, 0o600)
            listener.listen(16)
            listener.settimeout(0.2)  # poll the shutdown event periodically
            log.info(
                "knotcore sidecar listening on %s (abi=%d)", self.socket_path, ABI_VERSION
            )
            while not self._shutdown.is_set():
                try:
                    conn, _ = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break  # listener closed during shutdown
                threading.Thread(
                    target=self._serve_connection,
                    args=(conn,),
                    daemon=True,
                    name="knot-sidecar-conn",
                ).start()
        finally:
            try:
                listener.close()
            finally:
                try:
                    os.unlink(self.socket_path)
                except FileNotFoundError:
                    pass
            log.info("knotcore sidecar stopped (%s)", self.socket_path)

    def start_background(self) -> threading.Thread:
        """Start ``serve_forever`` on a daemon thread; returns the thread."""
        thread = threading.Thread(
            target=self.serve_forever, daemon=True, name="knotcore-sidecar"
        )
        thread.start()
        return thread

    def shutdown(self) -> None:
        """Signal the accept loop to stop and drop in-flight connections."""
        self._shutdown.set()
        with self._conn_lock:
            conns = list(self._connections)
        for conn in conns:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    # -- request handling --------------------------------------------

    def _serve_connection(self, conn: socket.socket) -> None:
        with self._conn_lock:
            self._connections.add(conn)
        try:
            with conn:
                rfile = conn.makefile("rb")
                while not self._shutdown.is_set():
                    # +2: allow the '\n' terminator within the frame budget and
                    # still detect an over-cap line (readline returns a partial
                    # line when the size hint is hit).
                    line = rfile.readline(MAX_FRAME_BYTES + 2)
                    if not line:
                        break  # peer closed
                    response = self._handle_line(line)
                    conn.sendall(response)
        except OSError:
            pass  # peer vanished mid-conversation
        finally:
            with self._conn_lock:
                self._connections.discard(conn)

    def _handle_line(self, line: bytes) -> bytes:
        """Handle one raw NDJSON line; always produces a response frame.

        Application errors (StoreFull, KeyError, bad params, ...) are reported
        to the caller as ``{"error": "ClassName: message"}`` frames; the server
        itself keeps serving (fail-loud, not fail-dead).
        """
        request_id = 0
        try:
            if len(line) > MAX_FRAME_BYTES + 1:  # +1 for the '\n'
                raise ProtocolError(
                    f"frame exceeds the {MAX_FRAME_BYTES}-byte cap"
                )
            if not line.endswith(b"\n"):
                raise ProtocolError("unterminated frame (missing newline)")
            frame = parse_frame(line)
            request_id = frame["id"]
            if "method" not in frame:
                raise ProtocolError("expected a request frame")
            result = self._dispatch(frame["method"], frame.get("params", {}))
            return encode_response(request_id, result=result)
        except Exception as exc:
            if not isinstance(exc, ProtocolError):
                log.exception("sidecar dispatch failed")
            return encode_response(request_id, error=f"{type(exc).__name__}: {exc}")

    def _dispatch(self, method: str, params: dict) -> Any:
        if method == "store_instruction":
            return knotcore_sim.store_instruction(_need_str(params, "payload"))
        if method == "fetch_payload":
            return knotcore_sim.fetch_payload(_need_str(params, "ptr"))
        if method == "verify_cauldron_phase":
            ptr = _need_str(params, "ptr")
            tag = _need_str(params, "tag")
            phase = _need_int(params, "phase")
            return knotcore_sim.verify_cauldron_phase(ptr, tag, phase)
        if method == "bump_generation":
            return knotcore_sim.bump_generation(_need_str(params, "ptr"))
        if method == "health":
            return {"status": "ok", "abi_version": ABI_VERSION}
        if method == "abi_version":
            return ABI_VERSION
        raise ProtocolError(f"unknown method {method!r}; supported: {', '.join(_METHODS)}")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: run the server in the foreground with graceful SIGTERM."""
    logging.basicConfig(
        level=os.environ.get("HELIXOS_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    args = list(sys.argv[1:] if argv is None else argv)
    socket_path = args[0] if args else None
    server = KnotSidecarServer(socket_path)

    def _handle_signal(signum: int, _frame: object) -> None:
        log.info("received signal %d; shutting down", signum)
        server.shutdown()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    try:
        server.serve_forever()
    except OSError as exc:
        log.error("sidecar failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
