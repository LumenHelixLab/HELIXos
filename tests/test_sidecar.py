"""Integration tests for the knotcore sidecar (SPEC §3.4, AUD-H6).

Covers: full client roundtrip of every method against a real server on a tmp
socket, per-call deadline enforcement against a slow server, circuit-breaker
open/half-open/recover behavior, stale-socket cleanup, socket-file removal on
shutdown, StoreFull propagation, frame-cap enforcement, and the wrapper
running end-to-end over SidecarBackend.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from types import SimpleNamespace

import pytest

import knotcore_sim
from rpc_protocol import (
    MAX_FRAME_BYTES,
    ProtocolError,
    encode_request,
    encode_response,
    parse_frame,
)
from sidecar_client import KnotClient, SidecarError, Unavailable
from sidecar_server import ABI_VERSION, KnotSidecarServer


@pytest.fixture(autouse=True)
def clean_sim():
    knotcore_sim.reset_store()
    yield
    knotcore_sim.reset_store()
    knotcore_sim.configure()  # restore default capacity for other test modules


def _wait_for_socket(path: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(path):
            try:
                probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                probe.settimeout(0.2)
                probe.connect(path)
                probe.close()
                return
            except OSError:
                pass
        time.sleep(0.02)
    raise RuntimeError(f"sidecar did not start listening on {path}")


@pytest.fixture
def server(tmp_path):
    """A real KnotSidecarServer on a tmp-path Unix socket."""
    path = str(tmp_path / "knot.sock")
    srv = KnotSidecarServer(path)
    thread = srv.start_background()
    _wait_for_socket(path)
    yield SimpleNamespace(path=path, server=srv, thread=thread)
    srv.shutdown()
    thread.join(timeout=5.0)


def _slow_server(path: str, delay: float, ready: threading.Event) -> None:
    """Minimal fake server: reads one request, sleeps, then responds correctly."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)
    ready.set()
    conn, _ = srv.accept()
    try:
        line = conn.makefile("rb").readline(MAX_FRAME_BYTES + 2)
        req = parse_frame(line)
        time.sleep(delay)
        conn.sendall(encode_response(req["id"], result={"status": "ok", "abi_version": 1}))
    except OSError:
        pass  # client already gave up and closed
    finally:
        conn.close()
        srv.close()


# ---------------------------------------------------------------------------
# Roundtrip
# ---------------------------------------------------------------------------


def test_roundtrip_all_methods(server):
    client = KnotClient(server.path, timeout=2.0)
    assert client.health() == {"status": "ok", "abi_version": ABI_VERSION}
    assert client.abi_version() == ABI_VERSION

    ptr = client.store_instruction('{"ts":1,"nonce":"n","body":"hello"}')
    assert isinstance(ptr, str) and len(ptr) == 16

    assert client.fetch_payload(ptr) == '{"ts":1,"nonce":"n","body":"hello"}'
    assert client.fetch_payload("ffffffffffffffff") is None

    assert client.verify_cauldron_phase(ptr, "sometag", 0) is True
    assert client.verify_cauldron_phase(ptr, "sometag", 7) is False

    assert client.bump_generation(ptr) == 1
    assert client.verify_cauldron_phase(ptr, "sometag", 0) is False
    assert client.verify_cauldron_phase(ptr, "sometag", 1) is True


def test_server_removes_socket_file_on_shutdown(server):
    path = server.path
    assert os.path.exists(path)
    server.server.shutdown()
    server.thread.join(timeout=5.0)
    assert not os.path.exists(path)


def test_multiple_sequential_calls_one_client(server):
    client = KnotClient(server.path, timeout=2.0)
    ptrs = [client.store_instruction(f"payload-{i}") for i in range(10)]
    assert len(set(ptrs)) == 10
    for i, ptr in enumerate(ptrs):
        assert client.fetch_payload(ptr) == f"payload-{i}"


def test_unknown_method_returns_error_frame(server):
    raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    raw.settimeout(2.0)
    raw.connect(server.path)
    try:
        raw.sendall(encode_request(7, "definitely_not_a_method", {}))
        data = b""
        while b"\n" not in data:
            data += raw.recv(65536)
        frame = parse_frame(data)
    finally:
        raw.close()
    assert frame["id"] == 7
    assert "unknown method" in frame["error"]


def test_storefull_propagates_as_sidecar_error(server):
    knotcore_sim.configure(1)
    client = KnotClient(server.path, timeout=2.0)
    client.store_instruction("only-slot")
    with pytest.raises(SidecarError, match="StoreFull"):
        client.store_instruction("overflow")


def test_frame_cap_enforced():
    with pytest.raises(ProtocolError, match="cap"):
        parse_frame(b"x" * (MAX_FRAME_BYTES + 1))
    with pytest.raises(ProtocolError, match="cap"):
        encode_request(1, "store_instruction", {"payload": "x" * MAX_FRAME_BYTES})
    # a frame just under the cap parses fine
    ok = encode_request(1, "health", {})
    assert parse_frame(ok)["method"] == "health"


# ---------------------------------------------------------------------------
# Deadline enforcement
# ---------------------------------------------------------------------------


def test_deadline_enforced_on_slow_server(tmp_path):
    path = str(tmp_path / "slow.sock")
    ready = threading.Event()
    thread = threading.Thread(target=_slow_server, args=(path, 1.5, ready), daemon=True)
    thread.start()
    assert ready.wait(timeout=5.0)
    client = KnotClient(path, timeout=0.2, breaker_threshold=10)
    started = time.monotonic()
    with pytest.raises(Unavailable):
        client.health()
    elapsed = time.monotonic() - started
    assert elapsed < 1.0, f"deadline not enforced; call took {elapsed:.2f}s"
    thread.join(timeout=5.0)


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


def test_circuit_breaker_opens_after_threshold_and_recovers(tmp_path):
    dead_path = str(tmp_path / "dead.sock")
    client = KnotClient(dead_path, timeout=0.2, breaker_threshold=5, breaker_reset_s=0.3)

    # 5 consecutive transport failures open the breaker.
    for _ in range(5):
        with pytest.raises(Unavailable, match="failed"):
            client.health()
    assert client._breaker_opened_at is not None

    # Open breaker: immediate reject, no socket attempt.
    with pytest.raises(Unavailable, match="circuit breaker open"):
        client.health()

    # After the reset elapses, a half-open probe is admitted; it fails
    # (server still down) and the breaker re-opens.
    time.sleep(0.35)
    with pytest.raises(Unavailable, match="failed"):
        client.health()
    assert client._breaker_opened_at is not None
    with pytest.raises(Unavailable, match="circuit breaker open"):
        client.health()

    # Bring up a real server; after the next reset window the half-open
    # probe succeeds and the breaker closes.
    srv = KnotSidecarServer(dead_path)
    thread = srv.start_background()
    _wait_for_socket(dead_path)
    try:
        time.sleep(0.35)
        assert client.health() == {"status": "ok", "abi_version": ABI_VERSION}
        assert client._breaker_opened_at is None
        assert client.health()["status"] == "ok"  # subsequent calls flow normally
    finally:
        srv.shutdown()
        thread.join(timeout=5.0)


def test_application_errors_do_not_trip_breaker(server):
    client = KnotClient(server.path, timeout=2.0, breaker_threshold=2, breaker_reset_s=60.0)
    for _ in range(4):  # more application errors than the threshold
        with pytest.raises(SidecarError):
            client.bump_generation("ffffffffffffffff")  # KeyError server-side
    # breaker still closed: transport-level calls keep working
    assert client.health()["status"] == "ok"


# ---------------------------------------------------------------------------
# Stale socket cleanup
# ---------------------------------------------------------------------------


def test_stale_socket_file_is_reclaimed(tmp_path):
    path = str(tmp_path / "stale.sock")
    # Simulate a crashed daemon: a bound socket whose process died without unlink.
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(path)
    stale.close()
    assert os.path.exists(path)

    srv = KnotSidecarServer(path)
    thread = srv.start_background()
    _wait_for_socket(path)
    try:
        client = KnotClient(path, timeout=2.0)
        assert client.health()["status"] == "ok"
    finally:
        srv.shutdown()
        thread.join(timeout=5.0)


def test_stale_regular_file_is_reclaimed(tmp_path):
    path = str(tmp_path / "junk.sock")
    with open(path, "w", encoding="ascii") as fh:
        fh.write("junk left behind")

    srv = KnotSidecarServer(path)
    thread = srv.start_background()
    _wait_for_socket(path)
    try:
        client = KnotClient(path, timeout=2.0)
        assert client.health()["status"] == "ok"
    finally:
        srv.shutdown()
        thread.join(timeout=5.0)


# ---------------------------------------------------------------------------
# Wrapper over the sidecar (end-to-end through the RPC boundary)
# ---------------------------------------------------------------------------


def test_wrapper_roundtrip_over_sidecar_backend(server):
    import KNOT_API_WRAPPER as W
    from signers import HMACSigner, HMACVerifier

    key = bytes(range(32))  # TEST-ONLY
    backend = W.SidecarBackend(server.path, timeout=2.0)
    line = W.generate_triangulated_instruction("over the wire", "EXEC", HMACSigner(key), backend=backend)
    assert W.BUS_RE.fullmatch(line)
    assert W.verify_instruction_lock(line, HMACVerifier(key), backend=backend) is True
    assert W.verify_instruction_lock(line, HMACVerifier(key), backend=backend) is False  # replay
